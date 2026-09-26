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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from waitress import serve

from config import ConfigError, load_cameras, load_settings, parse_float, public_settings, validate_percent


APP_VERSION = os.environ.get("APP_VERSION", "2.1.0")
app = Flask(__name__)

SETTINGS = load_settings()
CACHE_DIR = Path(SETTINGS.cache_dir)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

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
    log("WARNING: no camera is enabled; configure CAMERA_01_* variables and redeploy the stack")
else:
    log(f"Configured cameras: {', '.join(CAMERAS.keys())}")


job_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}
job_order: list[str] = []
prep_pool = ThreadPoolExecutor(max_workers=SETTINGS.prep_workers, thread_name_prefix="tts-prep")
prep_locks = [threading.Lock() for _ in range(64)]
enqueue_lock = threading.Lock()
cache_lock = threading.Lock()


def update_job(job_id: str, **fields: Any) -> None:
    with job_lock:
        job = jobs.get(job_id)
        if job:
            job.update(fields)
            job["updated_at"] = time.time()


def new_job(camera_id: str, text: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    now = time.time()
    job = {
        "id": job_id,
        "camera": camera_id,
        "text": text,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
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


def prune_cache(min_age_seconds: int = 600) -> None:
    now = time.time()
    with cache_lock:
        files = []
        total = 0
        for path in CACHE_DIR.glob("*.aac"):
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

        # Keep recently generated/used files to avoid deleting a file between
        # TTS preparation and HCNetSDK opening it for playback.
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
        "text": text,
        "voice": voice,
        "rate": rate,
        "edge_volume": edge_volume,
        "gain_db": gain_db,
        "sample_rate": int(camera_cfg.get("sample_rate", SETTINGS.tts_sample_rate)),
        "bitrate": str(camera_cfg.get("bitrate", SETTINGS.tts_bitrate)),
    }


def prepare_audio(spec: dict[str, Any]) -> str:
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    final_aac = CACHE_DIR / f"{key}.aac"

    if final_aac.exists() and final_aac.stat().st_size > 64:
        try:
            os.utime(final_aac, None)
        except OSError:
            pass
        return str(final_aac)

    lock = prep_locks[int(key[:8], 16) % len(prep_locks)]
    with lock:
        if final_aac.exists() and final_aac.stat().st_size > 64:
            try:
                os.utime(final_aac, None)
            except OSError:
                pass
            return str(final_aac)

        mp3 = CACHE_DIR / f".{key}.{uuid.uuid4().hex}.mp3"
        tmp_aac = CACHE_DIR / f".{key}.{uuid.uuid4().hex}.aac"
        try:
            subprocess.run(
                [
                    SETTINGS.edge_tts,
                    "--voice", spec["voice"],
                    "--rate", spec["rate"],
                    "--volume", spec["edge_volume"],
                    "--text", spec["text"],
                    "--write-media", str(mp3),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=SETTINGS.tts_timeout,
            )

            gain_db = max(-20.0, min(float(spec["gain_db"]), 12.0))
            audio_filter = f"volume={gain_db}dB,alimiter=limit=0.97"
            subprocess.run(
                [
                    SETTINGS.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-i", str(mp3),
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
            if not tmp_aac.exists() or tmp_aac.stat().st_size <= 64:
                raise RuntimeError("ffmpeg produced an empty AAC file")
            os.replace(tmp_aac, final_aac)
            return str(final_aac)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(detail[-1200:] if detail else "audio preparation failed") from exc
        finally:
            for path in (mp3, tmp_aac):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def send_to_camera(camera_id: str, cfg: dict[str, Any], aac_file: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "HCNETSDK_ROOT": SETTINGS.hcnetsdk_root,
            "CAMERA_IP": str(cfg["ip"]),
            "CAMERA_PORT": str(cfg.get("port", SETTINGS.camera_default_port)),
            "CAMERA_USER": str(cfg["username"]),
            "CAMERA_PASSWORD": str(cfg["password"]),
            "VOICE_CHAN": str(cfg.get("voice_chan", SETTINGS.camera_default_voice_chan)),
            "AUDIO_SAMPLE_RATE": str(cfg.get("sample_rate", SETTINGS.tts_sample_rate)),
            "LD_LIBRARY_PATH": (
                f"{SETTINGS.hcnetsdk_root}/lib:{SETTINGS.hcnetsdk_root}/lib/HCNetSDKCom:"
                + env.get("LD_LIBRARY_PATH", "")
            ),
        }
    )
    try:
        result = subprocess.run(
            [SETTINGS.send_aac, aac_file],
            env=env,
            capture_output=True,
            text=True,
            timeout=SETTINGS.send_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"camera playback timed out after {SETTINGS.send_timeout}s") from exc

    if result.stdout:
        log(f"[{camera_id}] {result.stdout.strip()}")
    if result.stderr:
        log(f"[{camera_id}] stderr: {result.stderr.strip()}")
    if result.returncode != 0:
        raise RuntimeError(f"send_aac exited with code {result.returncode}")


class CameraWorker:
    def __init__(self, camera_id: str, cfg: dict[str, Any]):
        self.camera_id = camera_id
        self.cfg = cfg
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=int(cfg.get("queue_size", SETTINGS.queue_size)))
        self.current_job: str | None = None
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"camera-{camera_id}")
        self.thread.start()

    def enqueue(self, item: dict[str, Any]) -> None:
        self.queue.put_nowait(item)

    def run(self) -> None:
        while True:
            item = self.queue.get()
            job_id = item["job_id"]
            self.current_job = job_id
            try:
                update_job(job_id, status="preparing")
                aac_file = item["future"].result(timeout=SETTINGS.prep_timeout)
                update_job(job_id, status="playing")
                log(f"[{job_id}] camera={self.camera_id} PLAY")
                send_to_camera(self.camera_id, self.cfg, aac_file)
                update_job(job_id, status="done")
                log(f"[{job_id}] camera={self.camera_id} DONE")
            except Exception as exc:
                update_job(job_id, status="error", error=str(exc))
                log(f"[{job_id}] camera={self.camera_id} ERROR: {exc}")
            finally:
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

    # Preserve order while preventing the same camera from being enqueued twice.
    return list(dict.fromkeys(targets))


def enqueue_request(camera_value: Any, data: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(data.get("text", data.get("message", ""))).strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > SETTINGS.max_text:
        raise ValueError(f"text too long; max={SETTINGS.max_text}")

    targets = parse_targets(camera_value)
    unknown = [camera_id for camera_id in targets if camera_id not in CAMERAS]
    if unknown:
        raise ValueError(f"unknown camera(s): {', '.join(unknown)}")

    staged: list[tuple[CameraWorker, dict[str, Any], Any]] = []
    futures: dict[str, Any] = {}

    with enqueue_lock:
        for camera_id in targets:
            if WORKERS[camera_id].queue.full():
                raise OverflowError(f"queue full for camera: {camera_id}")

        for camera_id in targets:
            worker = WORKERS[camera_id]
            cfg = CAMERAS[camera_id]
            spec = audio_spec(cfg, text, data)
            spec_key = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            future = futures.get(spec_key)
            if future is None:
                future = prep_pool.submit(prepare_audio, spec)
                futures[spec_key] = future
            job = new_job(camera_id, text)
            worker.enqueue({"job_id": job["id"], "future": future})
            staged.append((worker, job, future))

    snapshots = []
    for _, job, _ in staged:
        snapshot = job_snapshot(job["id"])
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


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
            "endpoints": ["/health", "/cameras", "/config", "/say", "/say/<camera_id>", "/jobs/<job_id>"],
        }
    )


@app.get("/health")
def health():
    camera_status = {
        camera_id: {"queued": worker.queue.qsize(), "current_job": worker.current_job}
        for camera_id, worker in WORKERS.items()
    }
    status = "ok" if CAMERAS and not CONFIG_ERROR else "degraded"
    result: dict[str, Any] = {
        "status": status,
        "version": APP_VERSION,
        "default_camera": DEFAULT_CAMERA or None,
        "camera_count": len(CAMERAS),
        "cameras": camera_status,
    }
    if CONFIG_ERROR:
        result["config_error"] = CONFIG_ERROR
    elif not CAMERAS:
        result["config_error"] = "no cameras configured"
    return jsonify(result)


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
        }
    )


@app.get("/cameras")
def cameras_view():
    if not require_auth():
        return auth_error()
    result = []
    for camera_id, cfg in CAMERAS.items():
        worker = WORKERS[camera_id]
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
                "queued": worker.queue.qsize(),
                "current_job": worker.current_job,
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


if __name__ == "__main__":
    log(f"Starting HTTP API on 0.0.0.0:{SETTINGS.port} with {SETTINGS.http_threads} threads")
    serve(app, host="0.0.0.0", port=SETTINGS.port, threads=SETTINGS.http_threads)
