#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify, request
from waitress import serve

from config import ConfigError, load_cameras, load_settings, parse_float, public_settings, validate_percent


APP_VERSION = os.environ.get("APP_VERSION", "2.3.3")
app = Flask(__name__)

SETTINGS = load_settings()
CACHE_DIR = Path(SETTINGS.cache_dir)
BASE_CACHE_DIR = CACHE_DIR / "base"
AAC_CACHE_DIR = CACHE_DIR / "aac"
MEDIA_CACHE_DIR = CACHE_DIR / "media"
BASE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
AAC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

SENDER_START_TIMEOUT = max(1.0, float(os.environ.get("SENDER_START_TIMEOUT", "8")))
VOICE_START_DELAY_MS = max(0, min(int(os.environ.get("VOICE_START_DELAY_MS", "120")), 2000))
VOICE_END_DELAY_MS = max(0, min(int(os.environ.get("VOICE_END_DELAY_MS", "80")), 2000))
PRECACHE_TEXTS_JSON = os.environ.get("PRECACHE_TEXTS_JSON", "").strip()
MEDIA_PREP_TIMEOUT = max(30, min(int(os.environ.get("MEDIA_PREP_TIMEOUT", "900")), 7200))
MEDIA_SEND_TIMEOUT = max(60, min(int(os.environ.get("MEDIA_SEND_TIMEOUT", "7200")), 21600))
MEDIA_MAX_URL_LENGTH = max(256, min(int(os.environ.get("MEDIA_MAX_URL_LENGTH", "4096")), 16384))
LOG_SUCCESSFUL_JOBS = os.environ.get("LOG_SUCCESSFUL_JOBS", "false").strip().lower() in {"1", "true", "yes", "on"}

CONFIG_ERROR = ""
try:
    CAMERAS, DEFAULT_CAMERA = load_cameras(SETTINGS)
except ConfigError as exc:
    CAMERAS, DEFAULT_CAMERA = {}, ""
    CONFIG_ERROR = str(exc)


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


if SETTINGS.api_key in {"change-me", "change-me-now"} and not SETTINGS.allow_no_auth:
    log("WARNING: API_KEY is using the default value; change it in the stack environment")
if CONFIG_ERROR:
    log(f"CONFIG ERROR: {CONFIG_ERROR}")
elif not CAMERAS:
    log("WARNING: no camera is configured; set CAMERAS_JSON and redeploy the stack")
else:
    log(f"Configured cameras: {', '.join(CAMERAS.keys())}")


job_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}
job_order: list[str] = []
prep_pool = ThreadPoolExecutor(max_workers=SETTINGS.prep_workers, thread_name_prefix="tts-prep")
base_locks = [threading.Lock() for _ in range(64)]
aac_locks = [threading.Lock() for _ in range(64)]
enqueue_lock = threading.Lock()
cache_lock = threading.Lock()
# RLock is intentional: Future.add_done_callback() may execute synchronously
# when a very fast cache-hit future is already complete. A plain Lock here can
# deadlock the HTTP request while registering the callback.
flight_lock = threading.RLock()
inflight_audio: dict[str, Future] = {}

cache_stats_lock = threading.Lock()
cache_stats: dict[str, int] = {
    "aac_hits": 0,
    "aac_misses": 0,
    "base_hits": 0,
    "base_misses": 0,
    "singleflight_joins": 0,
    "generated": 0,
    "media_hits": 0,
    "media_misses": 0,
    "media_generated": 0,
}


def stat_inc(name: str, value: int = 1) -> None:
    with cache_stats_lock:
        cache_stats[name] = cache_stats.get(name, 0) + value


def normalize_text(text: str) -> str:
    # Collapse accidental leading/trailing/repeated whitespace so equivalent
    # Home Assistant messages reuse the same cache entry. Preserve case,
    # punctuation and Vietnamese Unicode because they can affect speech.
    return " ".join(text.strip().split())


def update_job(job_id: str, **fields: Any) -> None:
    with job_lock:
        job = jobs.get(job_id)
        if job:
            job.update(fields)
            job["updated_at"] = time.time()


def mark_job_stage(job_id: str, stage: str) -> None:
    now = time.time()
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return
        timings = job.setdefault("timings", {})
        timings[stage] = round((now - job["created_at"]) * 1000.0, 1)
        job["updated_at"] = now


def new_job(camera_id: str, text: str = "", *, kind: str = "tts", title: str | None = None) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    now = time.time()
    job = {
        "id": job_id,
        "camera": camera_id,
        "kind": kind,
        "text": text if kind == "tts" else None,
        "media_title": title if kind == "media" else None,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "timings": {"accepted_ms": 0.0},
    }
    with job_lock:
        jobs[job_id] = job
        job_order.append(job_id)
        while len(job_order) > SETTINGS.job_history:
            old = job_order.pop(0)
            jobs.pop(old, None)
    return job


def job_snapshot(job_id: str) -> dict[str, Any] | None:
    with job_lock:
        job = jobs.get(job_id)
        return dict(job) if job else None


def valid_cache_file(path: Path, min_size: int = 64) -> bool:
    try:
        return path.is_file() and path.stat().st_size > min_size
    except FileNotFoundError:
        return False


def touch_cache(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def prune_cache(min_age_seconds: int = 600) -> None:
    now = time.time()
    with cache_lock:
        files: list[tuple[float, int, Path]] = []
        total = 0
        for pattern in ("base/*.mp3", "aac/*.aac", "media/*.aac", "*.aac"):
            for path in CACHE_DIR.glob(pattern):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                if SETTINGS.cache_ttl_days > 0 and now - stat.st_mtime > SETTINGS.cache_ttl_days * 86400:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                files.append((stat.st_mtime, stat.st_size, path))
                total += stat.st_size

        max_bytes = SETTINGS.cache_max_mb * 1024 * 1024
        if total <= max_bytes:
            return

        files.sort(key=lambda item: item[0])
        target_bytes = int(max_bytes * 0.90)
        for mtime, size, path in files:
            if total <= target_bytes:
                break
            if now - mtime < min_age_seconds:
                continue
            try:
                path.unlink()
                total -= size
            except FileNotFoundError:
                pass


def cache_disk_stats() -> dict[str, Any]:
    result = {"base_files": 0, "aac_files": 0, "media_files": 0, "legacy_aac_files": 0, "bytes": 0}
    for kind, pattern in (
        ("base_files", "base/*.mp3"),
        ("aac_files", "aac/*.aac"),
        ("media_files", "media/*.aac"),
        ("legacy_aac_files", "*.aac"),
    ):
        for path in CACHE_DIR.glob(pattern):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            result[kind] += 1
            result["bytes"] += stat.st_size
    result["mb"] = round(result["bytes"] / (1024 * 1024), 2)
    return result


def cache_maintenance_loop() -> None:
    while True:
        try:
            prune_cache()
        except Exception as exc:
            log(f"cache maintenance error: {exc}")
        time.sleep(1800)


try:
    prune_cache(min_age_seconds=0)
except Exception as exc:
    log(f"initial cache maintenance error: {exc}")
threading.Thread(target=cache_maintenance_loop, daemon=True, name="cache-maintenance").start()


def audio_spec(camera_cfg: dict[str, Any], text: str, data: dict[str, Any]) -> dict[str, Any]:
    voice = str(camera_cfg.get("voice") or SETTINGS.tts_voice)
    rate = str(camera_cfg.get("rate") or SETTINGS.tts_rate)
    edge_volume = str(camera_cfg.get("edge_volume") or SETTINGS.tts_edge_volume)
    gain_db = float(camera_cfg.get("gain_db", SETTINGS.tts_gain_db))

    if SETTINGS.allow_request_overrides:
        if data.get("voice") not in (None, ""):
            voice = str(data["voice"]).strip()
        if data.get("rate") not in (None, ""):
            rate = validate_percent(str(data["rate"]), "rate")
        if data.get("edge_volume") not in (None, ""):
            edge_volume = validate_percent(str(data["edge_volume"]), "edge_volume")
        if data.get("gain_db") not in (None, ""):
            gain_db = parse_float(data["gain_db"], "gain_db", gain_db, -20.0, 12.0)

    return {
        "text": normalize_text(text),
        "voice": voice,
        "rate": rate,
        "edge_volume": edge_volume,
        "gain_db": gain_db,
        "sample_rate": int(camera_cfg.get("sample_rate", SETTINGS.tts_sample_rate)),
        "bitrate": str(camera_cfg.get("bitrate", SETTINGS.tts_bitrate)),
    }


def audio_keys(spec: dict[str, Any]) -> tuple[str, str]:
    base_spec = {
        "text": spec["text"],
        "voice": spec["voice"],
        "rate": spec["rate"],
        "edge_volume": spec["edge_volume"],
    }
    base_raw = json.dumps(base_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    base_key = hashlib.sha256(base_raw.encode("utf-8")).hexdigest()

    aac_spec = {
        "base": base_key,
        "gain_db": float(spec["gain_db"]),
        "sample_rate": int(spec["sample_rate"]),
        "bitrate": str(spec["bitrate"]),
    }
    aac_raw = json.dumps(aac_spec, sort_keys=True, separators=(",", ":"))
    aac_key = hashlib.sha256(aac_raw.encode("utf-8")).hexdigest()
    return base_key, aac_key


def prepare_audio(spec: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    base_key, aac_key = audio_keys(spec)
    base_mp3 = BASE_CACHE_DIR / f"{base_key}.mp3"
    final_aac = AAC_CACHE_DIR / f"{aac_key}.aac"

    if valid_cache_file(final_aac):
        stat_inc("aac_hits")
        touch_cache(final_aac)
        return {
            "path": str(final_aac),
            "cache": "aac-hit",
            "base_cache": "n/a",
            "prepare_ms": round((time.monotonic() - started) * 1000.0, 1),
        }

    stat_inc("aac_misses")
    aac_lock = aac_locks[int(aac_key[:8], 16) % len(aac_locks)]
    with aac_lock:
        if valid_cache_file(final_aac):
            stat_inc("aac_hits")
            touch_cache(final_aac)
            return {
                "path": str(final_aac),
                "cache": "aac-hit-after-wait",
                "base_cache": "n/a",
                "prepare_ms": round((time.monotonic() - started) * 1000.0, 1),
            }

        base_cache_state = "hit"
        if valid_cache_file(base_mp3):
            stat_inc("base_hits")
            touch_cache(base_mp3)
        else:
            stat_inc("base_misses")
            base_cache_state = "miss"
            base_lock = base_locks[int(base_key[:8], 16) % len(base_locks)]
            with base_lock:
                if valid_cache_file(base_mp3):
                    stat_inc("base_hits")
                    touch_cache(base_mp3)
                    base_cache_state = "hit-after-wait"
                else:
                    tmp_mp3 = BASE_CACHE_DIR / f".{base_key}.{uuid.uuid4().hex}.mp3"
                    try:
                        subprocess.run(
                            [
                                SETTINGS.edge_tts,
                                "--voice", spec["voice"],
                                "--rate", spec["rate"],
                                "--volume", spec["edge_volume"],
                                "--text", spec["text"],
                                "--write-media", str(tmp_mp3),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=SETTINGS.tts_timeout,
                        )
                        if not valid_cache_file(tmp_mp3):
                            raise RuntimeError("edge-tts produced an empty media file")
                        os.replace(tmp_mp3, base_mp3)
                    except subprocess.CalledProcessError as exc:
                        detail = (exc.stderr or exc.stdout or str(exc)).strip()
                        raise RuntimeError(detail[-1200:] if detail else "edge-tts failed") from exc
                    finally:
                        try:
                            tmp_mp3.unlink()
                        except FileNotFoundError:
                            pass

        tmp_aac = AAC_CACHE_DIR / f".{aac_key}.{uuid.uuid4().hex}.aac"
        try:
            gain_db = max(-20.0, min(float(spec["gain_db"]), 12.0))
            audio_filter = f"volume={gain_db}dB,alimiter=limit=0.97"
            subprocess.run(
                [
                    SETTINGS.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-i", str(base_mp3),
                    "-vn",
                    "-ac", "1",
                    "-ar", str(spec["sample_rate"]),
                    "-af", audio_filter,
                    "-c:a", "aac",
                    "-profile:a", "aac_low",
                    "-b:a", spec["bitrate"],
                    "-f", "adts",
                    str(tmp_aac),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=SETTINGS.tts_timeout,
            )
            if not valid_cache_file(tmp_aac):
                raise RuntimeError("ffmpeg produced an empty AAC file")
            os.replace(tmp_aac, final_aac)
            stat_inc("generated")
            return {
                "path": str(final_aac),
                "cache": "generated",
                "base_cache": base_cache_state,
                "prepare_ms": round((time.monotonic() - started) * 1000.0, 1),
            }
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(detail[-1200:] if detail else "ffmpeg failed") from exc
        finally:
            try:
                tmp_aac.unlink()
            except FileNotFoundError:
                pass


def _submit_singleflight(key: str, prepare_fn, spec: dict[str, Any]) -> Future:
    """Submit one preparation job per cache key without blocking HTTP threads.

    add_done_callback() is deliberately called after the critical section.
    Python may invoke callbacks immediately when a Future is already finished
    (common on cache hits). Keeping callback registration outside the lock avoids
    the request-thread deadlock that previously surfaced in Home Assistant as
    "Timeout on reading data from socket".
    """
    with flight_lock:
        existing = inflight_audio.get(key)
        if existing is not None and not existing.done():
            stat_inc("singleflight_joins")
            return existing
        future = prep_pool.submit(prepare_fn, spec)
        inflight_audio[key] = future

    def clear_flight(done_future: Future, flight_key: str = key) -> None:
        with flight_lock:
            if inflight_audio.get(flight_key) is done_future:
                inflight_audio.pop(flight_key, None)

    future.add_done_callback(clear_flight)
    return future


def get_prepare_future(spec: dict[str, Any]) -> Future:
    _, aac_key = audio_keys(spec)
    return _submit_singleflight(aac_key, prepare_audio, spec)


def media_spec(camera_cfg: dict[str, Any], media_url: str, data: dict[str, Any]) -> dict[str, Any]:
    media_url = str(media_url).strip()
    if not media_url:
        raise ValueError("media URL is empty")
    if len(media_url) > MEDIA_MAX_URL_LENGTH:
        raise ValueError(f"media URL too long; max={MEDIA_MAX_URL_LENGTH}")
    parsed = urlparse(media_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("media URL must be an absolute http:// or https:// URL")

    gain_db = float(camera_cfg.get("gain_db", SETTINGS.tts_gain_db))
    if SETTINGS.allow_request_overrides and data.get("gain_db") not in (None, ""):
        gain_db = parse_float(data["gain_db"], "gain_db", gain_db, -20.0, 12.0)

    cache_identity = str(data.get("cache_key") or media_url).strip()
    title = str(data.get("title") or data.get("media_title") or "Media").strip() or "Media"
    return {
        "url": media_url,
        "cache_identity": cache_identity,
        "title": title[:240],
        "gain_db": gain_db,
        "sample_rate": int(camera_cfg.get("sample_rate", SETTINGS.tts_sample_rate)),
        "bitrate": str(camera_cfg.get("bitrate", SETTINGS.tts_bitrate)),
    }


def media_key(spec: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "source": spec["cache_identity"],
            "gain_db": float(spec["gain_db"]),
            "sample_rate": int(spec["sample_rate"]),
            "bitrate": str(spec["bitrate"]),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prepare_media(spec: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    key = media_key(spec)
    final_aac = MEDIA_CACHE_DIR / f"{key}.aac"

    if valid_cache_file(final_aac):
        stat_inc("media_hits")
        touch_cache(final_aac)
        return {
            "path": str(final_aac),
            "cache": "media-hit",
            "prepare_ms": round((time.monotonic() - started) * 1000.0, 1),
        }

    stat_inc("media_misses")
    lock = aac_locks[int(key[:8], 16) % len(aac_locks)]
    with lock:
        if valid_cache_file(final_aac):
            stat_inc("media_hits")
            touch_cache(final_aac)
            return {
                "path": str(final_aac),
                "cache": "media-hit-after-wait",
                "prepare_ms": round((time.monotonic() - started) * 1000.0, 1),
            }

        tmp_aac = MEDIA_CACHE_DIR / f".{key}.{uuid.uuid4().hex}.aac"
        try:
            gain_db = max(-20.0, min(float(spec["gain_db"]), 12.0))
            audio_filter = f"volume={gain_db}dB,alimiter=limit=0.97"
            subprocess.run(
                [
                    SETTINGS.ffmpeg,
                    "-nostdin",
                    "-y",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-i", spec["url"],
                    "-vn",
                    "-ac", "1",
                    "-ar", str(spec["sample_rate"]),
                    "-af", audio_filter,
                    "-c:a", "aac",
                    "-profile:a", "aac_low",
                    "-b:a", spec["bitrate"],
                    "-f", "adts",
                    str(tmp_aac),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=MEDIA_PREP_TIMEOUT,
            )
            if not valid_cache_file(tmp_aac):
                raise RuntimeError("ffmpeg produced an empty media AAC file")
            os.replace(tmp_aac, final_aac)
            stat_inc("media_generated")
            return {
                "path": str(final_aac),
                "cache": "media-generated",
                "prepare_ms": round((time.monotonic() - started) * 1000.0, 1),
            }
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"media preparation timed out after {MEDIA_PREP_TIMEOUT}s") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(detail[-1200:] if detail else "ffmpeg media conversion failed") from exc
        finally:
            try:
                tmp_aac.unlink()
            except FileNotFoundError:
                pass


def get_media_future(spec: dict[str, Any]) -> Future:
    key = "media:" + media_key(spec)
    return _submit_singleflight(key, prepare_media, spec)


class PlaybackStopped(RuntimeError):
    pass


class PersistentCameraSender:
    def __init__(self, camera_id: str, cfg: dict[str, Any]):
        self.camera_id = camera_id
        self.cfg = cfg
        self.proc: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[tuple[int, str]] = queue.Queue()
        self.command_lock = threading.Lock()
        self.last_error: str | None = None
        self.last_ready_at: float | None = None
        self.stop_requested = threading.Event()
        threading.Thread(target=self._warm_start, daemon=True, name=f"sdk-warm-{camera_id}").start()

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HCNETSDK_ROOT": SETTINGS.hcnetsdk_root,
                "CAMERA_IP": str(self.cfg["ip"]),
                "CAMERA_PORT": str(self.cfg.get("port", SETTINGS.camera_default_port)),
                "CAMERA_USER": str(self.cfg["username"]),
                "CAMERA_PASSWORD": str(self.cfg["password"]),
                "VOICE_CHAN": str(self.cfg.get("voice_chan", SETTINGS.camera_default_voice_chan)),
                "AUDIO_SAMPLE_RATE": str(self.cfg.get("sample_rate", SETTINGS.tts_sample_rate)),
                "CAMERA_CONNECT_TIMEOUT_MS": os.environ.get("CAMERA_CONNECT_TIMEOUT_MS", "3000"),
                "CAMERA_RECONNECT_INTERVAL_MS": os.environ.get("CAMERA_RECONNECT_INTERVAL_MS", "10000"),
                "VOICE_START_DELAY_MS": str(VOICE_START_DELAY_MS),
                "VOICE_END_DELAY_MS": str(VOICE_END_DELAY_MS),
                "LD_LIBRARY_PATH": (
                    f"{SETTINGS.hcnetsdk_root}/lib:{SETTINGS.hcnetsdk_root}/lib/HCNetSDKCom:"
                    + env.get("LD_LIBRARY_PATH", "")
                ),
            }
        )
        return env

    def _warm_start(self) -> None:
        try:
            with self.command_lock:
                self._ensure_started()
        except Exception as exc:
            self.last_error = str(exc)
            log(f"[{self.camera_id}] SDK warm start deferred: {exc}")

    def _stdout_reader(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if line:
                    self.responses.put((proc.pid, line))
        finally:
            rc = proc.poll()
            self.responses.put((proc.pid, f"__EXIT__\t{rc if rc is not None else 'unknown'}"))

    def _stderr_reader(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip("\r\n")
            if line:
                log(f"[{self.camera_id}] SDK: {line}")


    def _next_response(self, proc: subprocess.Popen[str], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise queue.Empty
            pid, line = self.responses.get(timeout=remaining)
            if pid != proc.pid:
                continue
            if line.startswith("__EXIT__\t"):
                raise RuntimeError(f"HCNetSDK worker exited unexpectedly ({line.split(chr(9), 1)[1]})")
            return line

    def _clear_responses(self) -> None:
        while True:
            try:
                self.responses.get_nowait()
            except queue.Empty:
                break

    def _stop_process(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write("QUIT\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _ensure_started(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return

        self._stop_process()
        self._clear_responses()
        proc = subprocess.Popen(
            [SETTINGS.send_aac, "--worker"],
            env=self._env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.proc = proc
        threading.Thread(target=self._stdout_reader, args=(proc,), daemon=True, name=f"sdk-out-{self.camera_id}").start()
        threading.Thread(target=self._stderr_reader, args=(proc,), daemon=True, name=f"sdk-err-{self.camera_id}").start()

        try:
            line = self._next_response(proc, SENDER_START_TIMEOUT)
        except queue.Empty as exc:
            self._stop_process()
            raise RuntimeError(f"HCNetSDK worker did not become ready within {SENDER_START_TIMEOUT:g}s") from exc

        if not line.startswith("READY\t"):
            self._stop_process()
            raise RuntimeError(f"HCNetSDK worker startup failed: {line}")

        self.last_error = None
        self.last_ready_at = time.time()
        log(f"[{self.camera_id}] persistent HCNetSDK worker ready ({line})")

    def play(self, aac_file: str, timeout: float | None = None) -> dict[str, Any]:
        started = time.monotonic()
        play_timeout = float(timeout or SETTINGS.send_timeout)
        self.stop_requested.clear()
        with self.command_lock:
            for attempt in range(2):
                if self.stop_requested.is_set():
                    raise PlaybackStopped("playback stopped")
                self._ensure_started()
                proc = self.proc
                if proc is None or proc.stdin is None:
                    raise RuntimeError("HCNetSDK worker is unavailable")

                try:
                    proc.stdin.write(f"PLAY\t{aac_file}\n")
                    proc.stdin.flush()
                    line = self._next_response(proc, play_timeout)
                except (BrokenPipeError, OSError) as exc:
                    if self.stop_requested.is_set():
                        raise PlaybackStopped("playback stopped") from exc
                    self.last_error = str(exc)
                    self._stop_process()
                    if attempt == 0:
                        continue
                    raise RuntimeError(f"HCNetSDK worker pipe failed: {exc}") from exc
                except queue.Empty as exc:
                    if self.stop_requested.is_set():
                        raise PlaybackStopped("playback stopped") from exc
                    self.last_error = f"playback timed out after {play_timeout:g}s"
                    self._stop_process()
                    raise RuntimeError(self.last_error) from exc
                except RuntimeError as exc:
                    if self.stop_requested.is_set():
                        raise PlaybackStopped("playback stopped") from exc
                    self.last_error = str(exc)
                    self._stop_process()
                    if attempt == 0:
                        continue
                    raise

                if self.stop_requested.is_set():
                    raise PlaybackStopped("playback stopped")

                if line.startswith("OK\t"):
                    parts = line.split("\t")
                    frames = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                    sdk_ms = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
                    self.last_error = None
                    return {
                        "frames": frames,
                        "sdk_ms": sdk_ms,
                        "sender_ms": round((time.monotonic() - started) * 1000.0, 1),
                    }

                self.last_error = line
                if line.startswith("ERR\t") and attempt == 0:
                    self._stop_process()
                    continue
                raise RuntimeError(f"HCNetSDK playback failed: {line}")

        raise RuntimeError(self.last_error or "HCNetSDK playback failed")

    def stop_current(self) -> None:
        self.stop_requested.set()
        proc = self.proc
        self.proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def status(self) -> dict[str, Any]:
        proc = self.proc
        return {
            "alive": bool(proc is not None and proc.poll() is None),
            "pid": proc.pid if proc is not None and proc.poll() is None else None,
            "last_error": self.last_error,
            "ready_at": self.last_ready_at,
        }


class CameraWorker:
    def __init__(self, camera_id: str, cfg: dict[str, Any]):
        self.camera_id = camera_id
        self.cfg = cfg
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=int(cfg.get("queue_size", SETTINGS.queue_size)))
        self.current_job: str | None = None
        self.cancelled_jobs: set[str] = set()
        self.cancel_lock = threading.Lock()
        self.sender = PersistentCameraSender(camera_id, cfg)
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"camera-{camera_id}")
        self.thread.start()

    def enqueue(self, item: dict[str, Any]) -> None:
        self.queue.put_nowait(item)

    def stop(self, clear_queue: bool = True) -> int:
        stopped = 0
        if clear_queue:
            while True:
                try:
                    item = self.queue.get_nowait()
                except queue.Empty:
                    break
                update_job(item["job_id"], status="stopped")
                self.queue.task_done()
                stopped += 1
        if self.current_job:
            with self.cancel_lock:
                self.cancelled_jobs.add(self.current_job)
            update_job(self.current_job, status="stopping")
            self.sender.stop_current()
            stopped += 1
        return stopped

    def _is_cancelled(self, job_id: str) -> bool:
        with self.cancel_lock:
            return job_id in self.cancelled_jobs

    def _clear_cancelled(self, job_id: str) -> None:
        with self.cancel_lock:
            self.cancelled_jobs.discard(job_id)

    def run(self) -> None:
        while True:
            item = self.queue.get()
            job_id = item["job_id"]
            kind = str(item.get("kind") or "unknown")
            stage = "prepare"
            self.current_job = job_id
            try:
                update_job(job_id, status="preparing")
                mark_job_stage(job_id, "prepare_wait_start_ms")
                prepared = item["future"].result(
                    timeout=float(item.get("prep_timeout") or SETTINGS.prep_timeout)
                )
                if self._is_cancelled(job_id):
                    raise PlaybackStopped("playback stopped")
                mark_job_stage(job_id, "audio_ready_ms")
                update_job(
                    job_id,
                    cache=prepared.get("cache"),
                    base_cache=prepared.get("base_cache"),
                    prepare_ms=prepared.get("prepare_ms"),
                    status="playing",
                )
                if LOG_SUCCESSFUL_JOBS:
                    log(
                        f"[{job_id}] camera={self.camera_id} kind={kind} stage=play "
                        f"cache={prepared.get('cache')} prepare_ms={prepared.get('prepare_ms')}"
                    )
                stage = "hcnetsdk_playback"
                mark_job_stage(job_id, "play_start_ms")
                play_result = self.sender.play(prepared["path"], timeout=item.get("send_timeout"))
                mark_job_stage(job_id, "done_ms")
                update_job(job_id, status="done", playback=play_result)
                if LOG_SUCCESSFUL_JOBS:
                    log(
                        f"[{job_id}] camera={self.camera_id} kind={kind} stage=done "
                        f"sdk_ms={play_result.get('sdk_ms')} sender_ms={play_result.get('sender_ms')}"
                    )
            except PlaybackStopped:
                mark_job_stage(job_id, "stopped_ms")
                update_job(job_id, status="stopped", error_stage=stage)
                if LOG_SUCCESSFUL_JOBS:
                    log(f"[{job_id}] camera={self.camera_id} kind={kind} stage={stage} STOPPED")
            except Exception as exc:
                mark_job_stage(job_id, "error_ms")
                error_type = exc.__class__.__name__
                error_text = str(exc) or error_type
                update_job(
                    job_id,
                    status="error",
                    error=error_text,
                    error_type=error_type,
                    error_stage=stage,
                )
                log(
                    f"ERROR job={job_id} camera={self.camera_id} kind={kind} "
                    f"stage={stage} type={error_type} detail={error_text}"
                )
            finally:
                self._clear_cancelled(job_id)
                self.current_job = None
                self.queue.task_done()


WORKERS = {camera_id: CameraWorker(camera_id, cfg) for camera_id, cfg in CAMERAS.items()}


def require_auth() -> bool:
    if SETTINGS.allow_no_auth:
        return True
    supplied = request.headers.get("X-API-Key", "").strip()
    if not supplied:
        auth = request.headers.get("Authorization", "").strip()
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, SETTINGS.api_key)


def auth_error():
    return jsonify({"error": "unauthorized"}), 401


def parse_targets(value: Any) -> list[str]:
    if not CAMERAS:
        raise RuntimeError(CONFIG_ERROR or "no cameras configured")
    if value is None or value == "":
        if not DEFAULT_CAMERA:
            raise ValueError("camera is required because DEFAULT_CAMERA is not configured")
        targets = [DEFAULT_CAMERA]
    elif value == "all":
        targets = list(CAMERAS.keys())
    elif isinstance(value, str):
        targets = [value]
    elif isinstance(value, list) and value:
        targets = [str(item) for item in value]
    else:
        raise ValueError("camera must be a camera id, a list, or 'all'")
    return list(dict.fromkeys(targets))


def enqueue_request(camera_value: Any, data: dict[str, Any]) -> list[dict[str, Any]]:
    text = normalize_text(str(data.get("text", data.get("message", ""))))
    if not text:
        raise ValueError("text is empty")
    if len(text) > SETTINGS.max_text:
        raise ValueError(f"text too long; max={SETTINGS.max_text}")

    targets = parse_targets(camera_value)
    unknown = [camera_id for camera_id in targets if camera_id not in CAMERAS]
    if unknown:
        raise ValueError(f"unknown camera(s): {', '.join(unknown)}")

    staged: list[tuple[CameraWorker, dict[str, Any], Future]] = []
    per_request_futures: dict[str, Future] = {}

    with enqueue_lock:
        for camera_id in targets:
            if WORKERS[camera_id].queue.full():
                raise OverflowError(f"queue full for camera: {camera_id}")

        for camera_id in targets:
            worker = WORKERS[camera_id]
            cfg = CAMERAS[camera_id]
            spec = audio_spec(cfg, text, data)
            _, aac_key = audio_keys(spec)
            future = per_request_futures.get(aac_key)
            if future is None:
                future = get_prepare_future(spec)
                per_request_futures[aac_key] = future
            job = new_job(camera_id, text)
            worker.enqueue({"job_id": job["id"], "future": future, "kind": "tts", "prep_timeout": SETTINGS.prep_timeout, "send_timeout": SETTINGS.send_timeout})
            staged.append((worker, job, future))

    snapshots = []
    for _, job, _ in staged:
        snapshot = job_snapshot(job["id"])
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def enqueue_media_request(camera_value: Any, data: dict[str, Any]) -> list[dict[str, Any]]:
    media_url = str(data.get("url", data.get("media_url", data.get("media_content_id", "")))).strip()
    if not media_url:
        raise ValueError("media URL is empty")

    targets = parse_targets(camera_value)
    unknown = [camera_id for camera_id in targets if camera_id not in CAMERAS]
    if unknown:
        raise ValueError(f"unknown camera(s): {', '.join(unknown)}")

    replace = bool(data.get("replace", True))
    staged: list[tuple[CameraWorker, dict[str, Any], Future]] = []
    per_request_futures: dict[str, Future] = {}

    # Stopping a camera can take up to ~1s while the HCNetSDK worker exits.
    # Do this outside the global enqueue lock so one camera cannot stall API
    # requests for every other camera.
    if replace:
        for camera_id in targets:
            WORKERS[camera_id].stop(clear_queue=True)

    with enqueue_lock:
        for camera_id in targets:
            if WORKERS[camera_id].queue.full():
                raise OverflowError(f"queue full for camera: {camera_id}")

        for camera_id in targets:
            worker = WORKERS[camera_id]
            cfg = CAMERAS[camera_id]
            spec = media_spec(cfg, media_url, data)
            key = media_key(spec)
            future = per_request_futures.get(key)
            if future is None:
                future = get_media_future(spec)
                per_request_futures[key] = future
            job = new_job(camera_id, kind="media", title=spec["title"])
            worker.enqueue({
                "job_id": job["id"],
                "future": future,
                "kind": "media",
                "prep_timeout": MEDIA_PREP_TIMEOUT,
                "send_timeout": MEDIA_SEND_TIMEOUT,
            })
            staged.append((worker, job, future))

    snapshots = []
    for _, job, _ in staged:
        snapshot = job_snapshot(job["id"])
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def precache_loop() -> None:
    if not PRECACHE_TEXTS_JSON or not CAMERAS:
        return
    try:
        texts = json.loads(PRECACHE_TEXTS_JSON)
        if not isinstance(texts, list):
            raise ValueError("PRECACHE_TEXTS_JSON must be a JSON array")
        texts = [normalize_text(str(item)) for item in texts if normalize_text(str(item))]
    except Exception as exc:
        log(f"precache disabled: {exc}")
        return

    if not texts:
        return

    log(f"precache starting: {len(texts)} phrase(s) x {len(CAMERAS)} camera profile(s)")
    futures: list[Future] = []
    seen: set[str] = set()
    for text in texts:
        for cfg in CAMERAS.values():
            spec = audio_spec(cfg, text, {})
            _, aac_key = audio_keys(spec)
            if aac_key in seen:
                continue
            seen.add(aac_key)
            futures.append(get_prepare_future(spec))

    for future in futures:
        try:
            future.result(timeout=SETTINGS.prep_timeout)
        except Exception as exc:
            log(f"precache item failed: {exc}")
    log("precache finished")


threading.Thread(target=precache_loop, daemon=True, name="precache").start()


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "request body too large"}), 413


@app.get("/")
def root():
    return jsonify(
        {
            "service": "camera-tts-ezviz",
            "version": APP_VERSION,
            "status": "ready" if CAMERAS and not CONFIG_ERROR else "needs-configuration",
            "optimizations": ["persistent-hcnetsdk-login", "two-level-cache", "singleflight", "parallel-prepare", "media-player"],
            "endpoints": ["/health", "/cameras", "/cache/stats", "/config", "/say", "/say/<camera_id>", "/media", "/media/<camera_id>", "/stop/<camera_id>", "/jobs/<job_id>"],
        }
    )


@app.get("/health")
def health():
    camera_status = {
        camera_id: {
            "queued": worker.queue.qsize(),
            "current_job": worker.current_job,
            "sender": worker.sender.status(),
        }
        for camera_id, worker in WORKERS.items()
    }
    status = "ok" if CAMERAS and not CONFIG_ERROR else "degraded"
    result: dict[str, Any] = {
        "status": status,
        "version": APP_VERSION,
        "default_camera": DEFAULT_CAMERA or None,
        "camera_count": len(CAMERAS),
        "cache": {**cache_disk_stats(), **dict(cache_stats)},
        "cameras": camera_status,
    }
    if CONFIG_ERROR:
        result["config_error"] = CONFIG_ERROR
    elif not CAMERAS:
        result["config_error"] = "no cameras configured"
    return jsonify(result)


@app.get("/cache/stats")
def cache_stats_view():
    if not require_auth():
        return auth_error()
    with cache_stats_lock:
        runtime = dict(cache_stats)
    with flight_lock:
        inflight_count = sum(1 for future in inflight_audio.values() if not future.done())
    return jsonify({"runtime": runtime, "disk": cache_disk_stats(), "inflight": inflight_count})


@app.get("/config")
def config_view():
    if not require_auth():
        return auth_error()
    return jsonify(
        {
            "version": APP_VERSION,
            "settings": public_settings(SETTINGS),
            "default_camera": DEFAULT_CAMERA or None,
            "camera_count": len(CAMERAS),
            "config_error": CONFIG_ERROR or None,
            "voice_start_delay_ms": VOICE_START_DELAY_MS,
            "voice_end_delay_ms": VOICE_END_DELAY_MS,
        }
    )


@app.get("/cameras")
def cameras_view():
    if not require_auth():
        return auth_error()
    result = []
    for camera_id, cfg in CAMERAS.items():
        worker = WORKERS[camera_id]
        current = job_snapshot(worker.current_job) if worker.current_job else None
        current_status = current.get("status") if current else None
        if current_status == "playing":
            media_state = "playing"
        elif current_status in {"queued", "preparing", "stopping"}:
            media_state = "buffering"
        else:
            media_state = "idle"
        result.append(
            {
                "id": camera_id,
                "ip": cfg["ip"],
                "port": cfg["port"],
                "voice_chan": cfg["voice_chan"],
                "voice": cfg["voice"],
                "gain_db": cfg["gain_db"],
                "sample_rate": cfg["sample_rate"],
                "bitrate": cfg["bitrate"],
                "state": media_state,
                "queued": worker.queue.qsize(),
                "current_job": worker.current_job,
                "current_kind": current.get("kind") if current else None,
                "media_title": (current.get("media_title") or current.get("text")) if current else None,
                "sender": worker.sender.status(),
            }
        )
    return jsonify({"cameras": result})


@app.get("/jobs/<job_id>")
def job_status(job_id: str):
    if not require_auth():
        return auth_error()
    job = job_snapshot(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


def handle_say(camera_value: Any, data: dict[str, Any]):
    if not require_auth():
        return auth_error()
    try:
        result = enqueue_request(camera_value, data)
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OverflowError as exc:
        response = jsonify({"error": str(exc)})
        response.status_code = 429
        response.headers["Retry-After"] = "2"
        return response
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"status": "queued", "jobs": result}), 202


@app.post("/say")
def say():
    data = request.get_json(silent=True) or {}
    return handle_say(data.get("camera"), data)


@app.post("/say/<camera_id>")
def say_camera(camera_id: str):
    data = request.get_json(silent=True) or {}
    return handle_say(camera_id, data)


def handle_media(camera_value: Any, data: dict[str, Any]):
    if not require_auth():
        return auth_error()
    try:
        result = enqueue_media_request(camera_value, data)
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OverflowError as exc:
        response = jsonify({"error": str(exc)})
        response.status_code = 429
        response.headers["Retry-After"] = "2"
        return response
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"status": "queued", "jobs": result}), 202


@app.post("/media")
def media():
    data = request.get_json(silent=True) or {}
    return handle_media(data.get("camera"), data)


@app.post("/media/<camera_id>")
def media_camera(camera_id: str):
    data = request.get_json(silent=True) or {}
    return handle_media(camera_id, data)


@app.post("/stop/<camera_id>")
def stop_camera(camera_id: str):
    if not require_auth():
        return auth_error()
    if camera_id not in WORKERS:
        return jsonify({"error": f"unknown camera: {camera_id}"}), 404
    stopped = WORKERS[camera_id].stop(clear_queue=True)
    return jsonify({"status": "stopped", "camera": camera_id, "jobs_stopped": stopped})


if __name__ == "__main__":
    log(f"Starting HTTP API on 0.0.0.0:{SETTINGS.port} with {SETTINGS.http_threads} threads")
    serve(app, host="0.0.0.0", port=SETTINGS.port, threads=SETTINGS.http_threads)
