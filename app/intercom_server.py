"""Dedicated low-latency HTTP backchannel for go2rtc intercom audio.

The server is deliberately separate from Waitress so a long-lived microphone
POST never consumes a normal API worker. go2rtc sends G.711 A-law/8 kHz. Audio
is decoded locally and voice-gated: a camera talk session opens only while a
person is speaking and closes after a short silence, allowing the camera's own
microphone to become audible again for real two-way conversation.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import struct
import threading
from array import array
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

VOICE_THRESHOLD_DB = -45.0
SILENCE_SECONDS = 1.5
PREROLL_SECONDS = 0.3
INPUT_RATE = 8000


def intercom_token(api_key: str, camera_id: str, configured: str = "") -> str:
    if configured:
        return configured
    return hmac.new(api_key.encode(), f"intercom:{camera_id}".encode(), hashlib.sha256).hexdigest()[:40]


def _decode_alaw_byte(value: int) -> int:
    a = value ^ 0x55
    t = ((a & 0x0F) << 4) + 8
    seg = (a & 0x70) >> 4
    if seg >= 1:
        t += 0x100
    if seg > 1:
        t <<= seg - 1
    return t if (a & 0x80) else -t


_ALAW = tuple(struct.pack("<h", _decode_alaw_byte(i)) for i in range(256))


def alaw_to_pcm16(data: bytes) -> bytes:
    return b"".join(_ALAW[b] for b in data)


def pcm_dbfs(pcm: bytes) -> float:
    """Return RMS dBFS for PCM16 mono without the deprecated audioop module."""
    pcm = pcm[: len(pcm) // 2 * 2]
    if not pcm:
        return -120.0
    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return -120.0
    mean_square = sum(int(value) * int(value) for value in samples) / len(samples)
    if mean_square <= 0:
        return -120.0
    rms = math.sqrt(mean_square)
    return 20.0 * math.log10(rms / 32768.0)


def _read_exact(stream, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            break
        out.extend(chunk)
    return bytes(out)


def iter_request_body(handler: BaseHTTPRequestHandler, block: int = 1600) -> Iterable[bytes]:
    transfer = handler.headers.get("Transfer-Encoding", "").lower()
    if "chunked" in transfer:
        while True:
            line = handler.rfile.readline(128)
            if not line:
                return
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                return
            if size == 0:
                while True:
                    trailer = handler.rfile.readline(4096)
                    if trailer in {b"\r\n", b"\n", b""}:
                        break
                return
            chunk = _read_exact(handler.rfile, size)
            _read_exact(handler.rfile, 2)
            if not chunk:
                return
            yield chunk
        return

    raw_length = handler.headers.get("Content-Length")
    if raw_length:
        remaining = max(0, int(raw_length))
        while remaining:
            chunk = handler.rfile.read(min(block, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk
        return

    # ffmpeg normally uses chunked POST for a live pipe. This fallback supports
    # clients that close the connection to delimit the body.
    while True:
        chunk = handler.rfile.read(block)
        if not chunk:
            return
        yield chunk


def _pcm_chunks(alaw_chunks: Iterable[bytes]) -> Iterator[bytes]:
    for chunk in alaw_chunks:
        if chunk:
            yield alaw_to_pcm16(chunk)


def iter_voice_bursts(pcm_chunks: Iterable[bytes]) -> Iterator[Iterator[bytes]]:
    """Split a live PCM stream into speech bursts with a short pre-roll.

    Each yielded iterator shares the same source iterator and must be consumed
    fully before asking for the next burst. This lets the camera talk transport
    close during silence instead of keeping the camera microphone muted for the
    entire WebRTC session.
    """
    source = iter(pcm_chunks)
    preroll: deque[bytes] = deque()
    preroll_seconds = 0.0

    while True:
        try:
            chunk = next(source)
        except StopIteration:
            return
        seconds = len(chunk) / (2.0 * INPUT_RATE)
        if pcm_dbfs(chunk) <= VOICE_THRESHOLD_DB:
            preroll.append(chunk)
            preroll_seconds += seconds
            while preroll and preroll_seconds > PREROLL_SECONDS:
                removed = preroll.popleft()
                preroll_seconds -= len(removed) / (2.0 * INPUT_RATE)
            continue

        leading = list(preroll)
        preroll.clear()
        preroll_seconds = 0.0

        def burst(first: bytes = chunk, before: list[bytes] = leading) -> Iterator[bytes]:
            for buffered in before:
                yield buffered
            yield first
            silence = 0.0
            for item in source:
                yield item
                if pcm_dbfs(item) > VOICE_THRESHOLD_DB:
                    silence = 0.0
                else:
                    silence += len(item) / (2.0 * INPUT_RATE)
                    if silence >= SILENCE_SECONDS:
                        return

        yield burst()


class IntercomServer:
    def __init__(self, workers: dict[str, Any], cameras: dict[str, dict[str, Any]],
                 api_key: str, port: int, log) -> None:
        self.workers, self.cameras, self.api_key, self.port, self.log = workers, cameras, api_key, port, log
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> bool:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _fmt: str, *_args) -> None:
                return

            def _reply(self, status: int, body: bytes = b"") -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    self._reply(200, b"ok")
                else:
                    self._reply(404, b"not found")

            def do_POST(self) -> None:  # noqa: N802
                parts = [p for p in urlsplit(self.path).path.split("/") if p]
                if len(parts) != 3 or parts[0] != "intercom":
                    self._reply(404, b"not found")
                    return
                camera_id, token = parts[1], parts[2]
                cfg = outer.cameras.get(camera_id)
                worker = outer.workers.get(camera_id)
                if cfg is None or worker is None or not cfg.get("intercom", True):
                    self._reply(404, b"camera not available")
                    return
                expected = intercom_token(outer.api_key, camera_id, str(cfg.get("intercom_key") or ""))
                if not hmac.compare_digest(expected, token):
                    self._reply(403, b"forbidden")
                    return

                try:
                    pcm = _pcm_chunks(iter_request_body(self))
                    for burst in iter_voice_bursts(pcm):
                        worker.sender.play_stream_pcm(burst, input_rate=INPUT_RATE)
                except (BrokenPipeError, ConnectionError, OSError):
                    return
                except Exception as exc:  # one intercom session must not affect API/server
                    outer.log(f"[{camera_id}] intercom error: {exc}")
                    try:
                        self._reply(502, str(exc).encode()[:512])
                    except OSError:
                        pass
                    return
                try:
                    self._reply(200, b"ok")
                except OSError:
                    pass

        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except OSError as exc:
            self.log(f"intercom server disabled: cannot listen on {self.port}: {exc}")
            return False
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="intercom-http")
        self.thread.start()
        self.log(f"intercom server listening on 0.0.0.0:{self.port}")
        return True
