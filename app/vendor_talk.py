"""Local vendor talk transports.

Imou/Dahua implementation is adapted from the user-provided imou-homeassistant project:
- port 8086 HTTP visualtalk/AAC 16 kHz first
- port 37777 Dahua NetSDK-compatible PCM 8 kHz fallback

This module is deliberately Home-Assistant independent so Docker owns camera I/O.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import struct
import subprocess
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import Any


class TalkError(RuntimeError):
    pass


class AuthError(TalkError):
    pass


# ---- Dahua 37777 -----------------------------------------------------------
_DH_PORT = 37777
_DH_RATE = 8000
_DH_BLOCK = 640
_HDR = 32
_A0, _B0, _A1, _F4, _TALK = 0xA0, 0xB0, 0xA1, 0xF4, 0x1D
_CHALLENGE = bytes([0x05, 0x02, 0x00, 0x01, 0x00, 0x00, 0xA1, 0xAA])
_LOGIN = bytes([0x05, 0x02, 0x00, 0x08, 0x00, 0x00, 0xA1, 0xAA])
_REASON = {1: "wrong password", 2: "unknown user", 4: "user already logged in elsewhere",
           5: "account locked", 6: "blocked after too many failed logins", 7: "device busy",
           8: "no free connection", 9: "no free channel"}


def _frame(cmd: int, body: bytes = b"", tail: bytes = b"") -> bytes:
    h = bytearray(_HDR)
    h[0] = cmd
    if cmd == _A0:
        h[1:4] = b"\x05\x00\x60"
    h[4:8] = struct.pack("<I", len(body))
    if len(tail) == 8:
        h[24:32] = tail
    return bytes(h) + body


def _audio_frame(pcm: bytes) -> bytes:
    h = bytearray(_HDR)
    h[0] = _TALK
    h[4:8] = struct.pack("<I", 8 + len(pcm))
    h[8] = 0x02
    h[9:21] = struct.pack("<III", 16, 1, _DH_RATE)
    return bytes(h) + b"\x00\x00\x01\xF0" + bytes([0x0C, 2]) + struct.pack("<H", len(pcm)) + pcm


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("camera closed the connection")
        out += chunk
    return out


def _read_frame(sock: socket.socket) -> tuple[bytes, bytes]:
    h = _recv_exact(sock, _HDR)
    n = struct.unpack("<I", h[4:8])[0]
    if n > 65536:
        raise ConnectionError(f"bogus frame length {n}")
    return h, _recv_exact(sock, n) if n else b""


def _kv(body: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body.decode(errors="replace").rstrip("\x00\r\n").split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _text(sock: socket.socket, lines: list[str]) -> None:
    sock.sendall(_frame(_F4, ("\r\n".join(lines) + "\r\n\r\n").encode()))


def _wait_text(sock: socket.socket, wanted: str) -> dict[str, str]:
    for _ in range(32):
        h, b = _read_frame(sock)
        if h[0] == _F4 and wanted.encode() in b:
            return _kv(b)
    raise ConnectionError(f"no {wanted} reply")


def _md5(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest().upper()


def _gen1(password: str) -> str:
    d = hashlib.md5(password.encode(), usedforsecurity=False).digest()
    out = ""
    for i in range(8):
        v = (d[i * 2] + d[i * 2 + 1]) % 62
        out += chr(v + 48 if v < 10 else v + 55 if v < 36 else v + 61)
    return out


class DahuaTalkSession:
    tan_so = _DH_RATE

    def __init__(self, host: str, username: str, password: str, *, port: int = _DH_PORT,
                 timeout: float = 5.0) -> None:
        self.host, self.username, self.password = host, username, password
        self.port, self.timeout = port, timeout
        self.ctrl: socket.socket | None = None
        self.sub: socket.socket | None = None
        self.session, self.connection_id = 0, ""
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def __enter__(self):
        try:
            self._open()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_exc):
        self.close()

    def login(self) -> None:
        self.ctrl = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.ctrl.sendall(_frame(_A0, tail=_CHALLENGE))
        h, body = _read_frame(self.ctrl)
        data = _kv(body)
        if h[0] != _B0 or not data.get("Realm") or not data.get("Random"):
            raise TalkError("device does not speak Dahua talk protocol on configured port")
        u, pw = self.username, self.password
        g2 = _md5(u + ":" + data["Random"] + ":" + _md5(u + ":" + data["Realm"] + ":" + pw))
        g1 = _md5(u + ":" + data["Random"] + ":" + _gen1(pw))
        self.ctrl.sendall(_frame(_A0, f"{u}&&{g2}{g1}".encode(), _LOGIN))
        h, _ = _read_frame(self.ctrl)
        self.session = struct.unpack("<I", h[16:20])[0]
        if not self.session:
            raise AuthError(f"login refused: {_REASON.get(h[8], f'code {h[8]}')}")

    def _open(self) -> None:
        self.login()
        assert self.ctrl is not None
        _text(self.ctrl, ["TransactionID:6", "Method:AddObject",
                          "ParameterName:Dahua.Device.Network.ControlConnection.Passive",
                          "ConnectProtocol:0"])
        data = _wait_text(self.ctrl, "AddObjectResponse")
        if data.get("FaultCode") not in ("OK", "", None) or not data.get("ConnectionID"):
            raise TalkError(f"AddObject failed ({data.get('FaultCode')})")
        self.connection_id = data["ConnectionID"]
        self.sub = socket.create_connection((self.host, self.port), timeout=self.timeout)
        _text(self.sub, ["TransactionID:0", "Method:GetParameterNames",
                         "ParameterName:Dahua.Device.Network.ControlConnection.AckSubChannel",
                         f"SessionID:{self.session}", f"ConnectionID:{self.connection_id}", "Encrypt:0"])
        if _wait_text(self.sub, "AckSubChannel").get("FaultCode") != "OK":
            raise TalkError("sub channel not acknowledged")
        self.sub.sendall(_frame(_A1))
        self._state(True)
        for sock in (self.ctrl, self.sub):
            sock.settimeout(None)
            self._threads.append(threading.Thread(target=self._drain, args=(sock,), daemon=True))
        self._threads.append(threading.Thread(target=self._keepalive, daemon=True))
        for thread in self._threads:
            thread.start()

    def _state(self, enabled: bool) -> None:
        assert self.ctrl is not None
        _text(self.ctrl, ["TransactionID:" + ("7" if enabled else "8"), "Method:GetParameterNames",
                          "ParameterName:Dahua.Device.Network.Talk.General", "Channel:0",
                          "EncodeFormat:1", "Depth:" + ("16" if enabled else "0"),
                          "Frequency:" + (str(_DH_RATE) if enabled else "0"),
                          "State:" + ("1" if enabled else "0"),
                          f"ConnectionID:{self.connection_id}", "TalkMode:0"])

    def _drain(self, sock: socket.socket) -> None:
        try:
            while not self._stop.is_set():
                _read_frame(sock)
        except (OSError, ConnectionError):
            pass

    def _keepalive(self) -> None:
        while not self._stop.wait(1.0):
            try:
                assert self.ctrl is not None
                self.ctrl.sendall(_frame(_A1))
            except OSError:
                return

    def send_pcm(self, pcm: bytes) -> None:
        assert self.sub is not None
        t0 = time.monotonic()
        for i in range(0, len(pcm), _DH_BLOCK):
            self.sub.sendall(_audio_frame(pcm[i:i + _DH_BLOCK]))
            remaining = t0 + (i + _DH_BLOCK) / (2 * _DH_RATE) - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

    def close(self) -> None:
        self._stop.set()
        try:
            if self.ctrl is not None and self.connection_id:
                self._state(False)
                _text(self.ctrl, ["TransactionID:9", "Method:DeleteObject",
                                  "ParameterName:Dahua.Device.Network.ControlConnection.Passive",
                                  f"ConnectionID:{self.connection_id}"])
        except OSError:
            pass
        for sock in (self.sub, self.ctrl):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        self.sub = self.ctrl = None
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(1)


# ---- Imou/Dahua 8086 -------------------------------------------------------
_HTTP_PORT = 8086
_HTTP_RATE = 16000
_HTTP_FRAME_SECONDS = 1024 / _HTTP_RATE
_AHEAD = 0.15
_TAIL = 0.4
_TALK_TRACK = 5
_PATH = ("/live/visualtalk.xav?channel=1&subtype=0&encrypt=3&imagesize=18&audioType=1"
         "&trackID={track}&method=0")
_SDP = ("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Talk\r\nc=IN IP4 0.0.0.0\r\nt=0 0\r\n"
        "m=video 0 RTP/AVP 96\r\na=control:trackID=31\r\n"
        "m=audio 0 RTP/AVP 8 96\r\na=rtpmap:8 PCMA/8000\r\n"
        "a=rtpmap:96 MPEG4-GENERIC/16000/1\r\na=control:trackID=5\r\na=sendrecv\r\n").encode()
_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_FALLBACK_SECONDS = 3600.0


def _wsse(user: str, secret: str, nonce: str, created: str) -> str:
    digest = base64.b64encode(hashlib.sha1(f"{nonce}{created}{secret}".encode(), usedforsecurity=False).digest()).decode()
    return f'UsernameToken Username="{user}", PasswordDigest="{digest}", Nonce="{nonce}", Created="{created}"'


def _dhav(aac: bytes, seq: int, tick_ms: int, seconds: int) -> bytes:
    size = len(aac) + 36
    h = bytearray(28)
    struct.pack_into("<4sB3xII", h, 0, b"DHAV", 0xF0, seq & 0xFFFFFFFF, size)
    struct.pack_into("<IH", h, 0x10, seconds & 0xFFFFFFFF, tick_ms & 0xFFFF)
    h[0x16] = 0x04
    h[0x17] = sum(h[:0x17]) & 0xFF
    h[0x18:0x1C] = b"\x83\x01\x1a\x04"
    frame = bytes(h) + aac + b"dhav" + struct.pack("<I", size)
    return b"$" + bytes([_TALK_TRACK * 2]) + struct.pack(">I", len(frame)) + frame


def split_adts(data: bytes) -> tuple[list[bytes], bytes]:
    out: list[bytes] = []
    while len(data) >= 7:
        if data[0] != 0xFF or data[1] & 0xF0 != 0xF0:
            i = data.find(b"\xff", 1)
            data = data[i:] if i > 0 else b""
            continue
        size = ((data[3] & 0x03) << 11) | (data[4] << 3) | (data[5] >> 5)
        if size < 7 or len(data) < size:
            break
        out.append(data[:size])
        data = data[size:]
    return out, data


class HttpTalkSession:
    tan_so = _HTTP_RATE

    def __init__(self, host: str, username: str, password: str, *, ffmpeg: str = "ffmpeg",
                 port: int = _HTTP_PORT, timeout: float = 5.0) -> None:
        self.host, self.username, self.password = host, username, password
        self.ffmpeg, self.port, self.timeout = ffmpeg, port, timeout
        self.sock: socket.socket | None = None
        self.ff: subprocess.Popen | None = None
        self.cseq, self.realm = 0, ""
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.started: float | None = None
        self.written = 0.0

    def __enter__(self):
        try:
            self._open()
            return self
        except BaseException:
            self.close(wait=False)
            raise

    def __exit__(self, *_exc):
        self.close()

    def _play(self, track: int, *, sdp: bytes = b"", extra: str = "") -> int:
        assert self.sock is not None
        nonce = "".join(_CHARS[b % len(_CHARS)] for b in os.urandom(32))
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        secret = self.password
        if self.realm:
            secret = hashlib.md5(f"{self.username}:{self.realm}:{self.password}".encode(), usedforsecurity=False).hexdigest().upper()
        lines = [f"PLAY {_PATH.format(track=track)}{extra} HTTP/1.1", f"Host: {self.host}:{self.port}",
                 "Connect-Type: P2P", "Connection: keep-alive", f"Cseq: {self.cseq}",
                 "Speed: 1.000000", "User-Agent: Http Stream Client/1.0",
                 'Authorization: WSSE profile="UsernameToken"', "WSSE: " + _wsse(self.username, secret, nonce, created)]
        if sdp:
            lines += ["Accpet-Sdp: Private", "Private-Type: application/sdp", f"Private-Length: {len(sdp)}"]
        self.cseq += 1
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + sdp)
        return self._read_reply()

    def _read_reply(self) -> int:
        assert self.sock is not None
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise TalkError("camera closed port 8086 connection")
            data += chunk
            while data.startswith(b"$") and len(data) >= 6:
                size = 6 + struct.unpack_from(">I", data, 2)[0]
                while len(data) < size:
                    data += self.sock.recv(size - len(data))
                data = data[size:]
        head, _, rest = data.partition(b"\r\n\r\n")
        text = head.decode("latin1", "replace")
        match = re.match(r"\S+ (\d+)", text)
        status = int(match.group(1)) if match else 0
        headers = {k.lower(): v for k, _, v in (x.partition(": ") for x in text.split("\r\n")[1:])}
        size = int(headers.get("private-length") or headers.get("content-length") or 0)
        while len(rest) < size:
            chunk = self.sock.recv(size - len(rest))
            if not chunk:
                break
            rest += chunk
        if status == 401 and not self.realm:
            match = re.search(r'realm="([^"]+)"', headers.get("www-authenticate", ""), re.I)
            self.realm = match.group(1) if match else ""
        return status

    def _open(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        status = self._play(31, sdp=_SDP)
        if status == 401 and self.realm:
            status = self._play(31, sdp=_SDP)
        for track, extra in ((None, ""), (6, ""), (64, "&talktype=talk")):
            if track is not None:
                status = self._play(track, extra=extra)
            if status != 200:
                raise TalkError(f"camera refused talk on port 8086 (code {status})")
        self.ff = subprocess.Popen([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-probesize", "32",
                                    "-analyzeduration", "0", "-fflags", "nobuffer", "-f", "s16le",
                                    "-ar", str(_HTTP_RATE), "-ac", "1", "-i", "pipe:0", "-c:a", "aac",
                                    "-b:a", "48k", "-f", "adts", "-flush_packets", "1", "pipe:1"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.sock.settimeout(1.0)
        self.threads = [threading.Thread(target=self._drain, daemon=True, name="imou-http-drain"),
                        threading.Thread(target=self._send_encoded, args=(self.ff,), daemon=True, name="imou-http-encode")]
        for thread in self.threads:
            thread.start()

    def _drain(self) -> None:
        assert self.sock is not None
        while not self.stop_event.is_set():
            try:
                if not self.sock.recv(65536):
                    return
            except TimeoutError:
                continue
            except OSError:
                return

    def _send_encoded(self, ff: subprocess.Popen) -> None:
        assert ff.stdout is not None and self.sock is not None
        data, seq, t0 = b"", 0, None
        tick, seconds = int(time.monotonic() * 1000), int(time.time())
        try:
            while True:
                chunk = ff.stdout.read1(4096)
                if not chunk:
                    break
                frames, data = split_adts(data + chunk)
                for frame in frames:
                    if t0 is None:
                        t0 = time.monotonic()
                    wait = t0 + seq * _HTTP_FRAME_SECONDS - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    self.sock.sendall(_dhav(frame, seq, tick + int(seq * 64), seconds))
                    seq += 1
        except (OSError, ValueError, AttributeError):
            pass

    def send_pcm(self, pcm: bytes) -> None:
        if self.started is None:
            self.started = time.monotonic()
        try:
            assert self.ff is not None and self.ff.stdin is not None
            self.ff.stdin.write(pcm)
            self.ff.stdin.flush()
        except (BrokenPipeError, ValueError, AttributeError) as exc:
            raise TalkError(f"audio encoder stopped ({exc})") from exc
        self.written += len(pcm) / (2 * _HTTP_RATE)
        wait = self.started + self.written - _AHEAD - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def close(self, wait: bool = True) -> None:
        ff, self.ff = self.ff, None
        if ff is not None:
            try:
                assert ff.stdin is not None
                ff.stdin.close()
            except OSError:
                pass
            if wait:
                for thread in self.threads:
                    if thread is not threading.current_thread() and thread.name == "imou-http-encode":
                        thread.join(300)
                time.sleep(_TAIL)
            if ff.poll() is None:
                ff.kill()
            ff.wait()
        self.stop_event.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.sock = None
        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(1)


class ImouTalkFactory:
    """Open 8086 first; remember 37777 fallback for an hour after an 8086 failure."""
    def __init__(self, host: str, username: str, password: str, *, port: int = _DH_PORT,
                 ffmpeg: str = "ffmpeg") -> None:
        self.host, self.username, self.password, self.port, self.ffmpeg = host, username, password, port, ffmpeg
        self.fallback_until = 0.0

    @property
    def tan_so(self) -> int:
        return _HTTP_RATE if self.fallback_until <= time.monotonic() else _DH_RATE

    def open(self):
        if self.fallback_until <= time.monotonic():
            try:
                return HttpTalkSession(self.host, self.username, self.password, ffmpeg=self.ffmpeg).__enter__()
            except (TalkError, OSError):
                self.fallback_until = time.monotonic() + _FALLBACK_SECONDS
        return DahuaTalkSession(self.host, self.username, self.password, port=self.port).__enter__()


# ---- helpers/sender ---------------------------------------------------------
def resample_pcm16_mono(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not pcm:
        return pcm
    src = array("h")
    src.frombytes(pcm[: len(pcm) // 2 * 2])
    n = len(src)
    if not n:
        return b""
    m = max(1, n * dst_rate // src_rate)
    out = array("h", [0]) * m
    for j in range(m):
        x = j * src_rate / dst_rate
        i = int(x)
        s0 = src[min(i, n - 1)]
        s1 = src[min(i + 1, n - 1)]
        out[j] = int(s0 + (s1 - s0) * (x - i))
    return out.tobytes()


def read_pcm_wav(path: str) -> tuple[int, bytes]:
    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise TalkError("prepared WAV must be PCM16 mono")
        return wav.getframerate(), wav.readframes(wav.getnframes())


class ImouDahuaSender:
    def __init__(self, camera_id: str, cfg: dict[str, Any], *, ffmpeg: str) -> None:
        self.camera_id, self.cfg = camera_id, cfg
        self.factory = ImouTalkFactory(cfg["ip"], cfg["username"], cfg["password"],
                                       port=int(cfg.get("port", _DH_PORT)), ffmpeg=ffmpeg)
        self.command_lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.last_error: str | None = None
        self.last_transport: str | None = None
        self.active_session: Any = None

    def play(self, wav_file: str, timeout: float | None = None) -> dict[str, Any]:
        del timeout
        started = time.monotonic()
        with self.command_lock:
            if self.stop_requested.is_set():
                raise TalkError("playback stopped")
            src_rate, pcm = read_pcm_wav(wav_file)
            session = self.factory.open()
            self.active_session = session
            try:
                dst_rate = int(getattr(session, "tan_so", src_rate))
                if src_rate != dst_rate:
                    pcm = resample_pcm16_mono(pcm, src_rate, dst_rate)
                session.send_pcm(pcm)
                self.last_transport = "imou-http-8086" if dst_rate == 16000 else "dahua-37777"
                self.last_error = None
                return {"transport": self.last_transport, "sender_ms": round((time.monotonic() - started) * 1000, 1)}
            except Exception as exc:
                self.last_error = str(exc)
                raise
            finally:
                try:
                    session.close()
                finally:
                    self.active_session = None

    def play_stream_pcm(self, chunks, *, input_rate: int = 8000, clear_stop: bool = True) -> dict[str, Any]:
        """Play a live PCM16 mono stream, serialized with TTS/media for this camera."""
        started = time.monotonic()
        with self.command_lock:
            if clear_stop:
                self.stop_requested.clear()
            session = self.factory.open()
            self.active_session = session
            try:
                dst_rate = int(getattr(session, "tan_so", input_rate))
                total = 0
                for chunk in chunks:
                    if not chunk:
                        continue
                    if self.stop_requested.is_set():
                        break
                    pcm = resample_pcm16_mono(chunk, input_rate, dst_rate) if input_rate != dst_rate else chunk
                    session.send_pcm(pcm)
                    total += len(chunk)
                self.last_transport = "imou-http-8086-live" if dst_rate == 16000 else "dahua-37777-live"
                self.last_error = None
                return {"transport": self.last_transport, "input_bytes": total,
                        "sender_ms": round((time.monotonic() - started) * 1000, 1)}
            except Exception as exc:
                self.last_error = str(exc)
                raise
            finally:
                try:
                    session.close()
                finally:
                    self.active_session = None

    def stop_current(self) -> None:
        self.stop_requested.set()
        session = self.active_session
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        return {"alive": True, "connected": self.active_session is not None,
                "last_error": self.last_error, "transport": self.last_transport or "imou-auto"}
