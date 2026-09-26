#!/usr/bin/env python3
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from flask import Flask, jsonify, request

app = Flask(__name__)

def read_text_secret(path):
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"Secret file not found: {p}")
    return p.read_text(encoding="utf-8").strip()


def env_or_file(name, file_name, default=""):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    file_path = os.environ.get(file_name, "").strip()
    if file_path:
        return read_text_secret(file_path)
    return default


API_KEY = env_or_file("CAMERA_TTS_API_KEY", "CAMERA_TTS_API_KEY_FILE", "change-me")
PORT = int(os.environ.get("CAMERA_TTS_PORT", "8124"))
MAX_TEXT = int(os.environ.get("CAMERA_TTS_MAX_TEXT", "500"))
CONFIG_PATH = Path(os.environ.get("CAMERA_TTS_CONFIG", "/config/cameras.yaml"))
CACHE_DIR = Path(os.environ.get("CAMERA_TTS_CACHE_DIR", "/cache"))
SEND_AAC = os.environ.get("CAMERA_TTS_SEND_AAC", "/usr/local/bin/send_aac")
EDGE_TTS = os.environ.get("CAMERA_TTS_EDGE_TTS", "/usr/local/bin/edge-tts")
FFMPEG = os.environ.get("CAMERA_TTS_FFMPEG", "/usr/bin/ffmpeg")
HCNETSDK_ROOT = os.environ.get("HCNETSDK_ROOT", "/opt/hcnetsdk")
PREP_WORKERS = max(1, int(os.environ.get("CAMERA_TTS_PREP_WORKERS", "4")))
JOB_HISTORY = max(20, int(os.environ.get("CAMERA_TTS_JOB_HISTORY", "300")))
SEND_TIMEOUT = max(30, int(os.environ.get("CAMERA_TTS_SEND_TIMEOUT", "180")))
PREP_TIMEOUT = max(60, int(os.environ.get("CAMERA_TTS_PREP_TIMEOUT", "300")))

CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULTS = {
    "voice": "vi-VN-HoaiMyNeural",
    "rate": "+0%",
    "edge_volume": "+0%",
    "gain_db": 4.0,
    "sample_rate": 16000,
    "bitrate": "32k",
    "voice_chan": 1,
    "queue_size": 20,
}


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def expand_env(value):
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def load_config():
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"Missing config file: {CONFIG_PATH}")
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    data = expand_env(data)
    defaults = dict(DEFAULTS)
    defaults.update(data.get("defaults") or {})
    cameras = data.get("cameras") or {}
    if not isinstance(cameras, dict) or not cameras:
        raise RuntimeError("No cameras configured")
    normalized = {}
    for camera_id, cfg in cameras.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(camera_id)):
            raise RuntimeError(f"Invalid camera id: {camera_id}")
        merged = dict(defaults)
        merged.update(cfg or {})
        password_file = str(merged.get("password_file", "")).strip()
        if password_file:
            merged["password"] = read_text_secret(password_file)
        required = ["ip", "username", "password"]
        missing = [k for k in required if not str(merged.get(k, "")).strip()]
        if missing:
            raise RuntimeError(f"Camera {camera_id}: missing {', '.join(missing)}")
        merged["port"] = int(merged.get("port", 8000))
        merged["voice_chan"] = int(merged.get("voice_chan", 1))
        merged["queue_size"] = int(merged.get("queue_size", 20))
        merged["sample_rate"] = int(merged.get("sample_rate", 16000))
        merged["gain_db"] = float(merged.get("gain_db", 4.0))
        normalized[str(camera_id)] = merged
    default_camera = str(data.get("default_camera") or "")
    if not default_camera:
        default_camera = next(iter(normalized)) if len(normalized) == 1 else ""
    if default_camera and default_camera not in normalized:
        raise RuntimeError(f"default_camera '{default_camera}' is not defined")
    return normalized, default_camera


CAMERAS, DEFAULT_CAMERA = load_config()

job_lock = threading.Lock()
jobs = {}
job_order = []
prep_pool = ThreadPoolExecutor(max_workers=PREP_WORKERS, thread_name_prefix="tts-prep")
prep_locks = {}
prep_locks_guard = threading.Lock()
enqueue_lock = threading.Lock()


def update_job(job_id, **fields):
    with job_lock:
        job = jobs.get(job_id)
        if job:
            job.update(fields)
            job["updated_at"] = time.time()


def new_job(camera_id, text):
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
        while len(job_order) > JOB_HISTORY:
            old = job_order.pop(0)
            jobs.pop(old, None)
    return job


def job_snapshot(job_id):
    with job_lock:
        job = jobs.get(job_id)
        return dict(job) if job else None


def get_prep_lock(key):
    with prep_locks_guard:
        lock = prep_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            prep_locks[key] = lock
        return lock


def audio_spec(camera_cfg, text, overrides):
    voice = str(overrides.get("voice") or camera_cfg.get("voice") or DEFAULTS["voice"])
    rate = str(overrides.get("rate") or camera_cfg.get("rate") or "+0%")
    edge_volume = str(overrides.get("edge_volume") or camera_cfg.get("edge_volume") or "+0%")
    gain_db = float(overrides.get("gain_db") if overrides.get("gain_db") is not None else camera_cfg.get("gain_db", 4.0))
    sample_rate = int(camera_cfg.get("sample_rate", 16000))
    bitrate = str(camera_cfg.get("bitrate", "32k"))
    return {
        "text": text,
        "voice": voice,
        "rate": rate,
        "edge_volume": edge_volume,
        "gain_db": gain_db,
        "sample_rate": sample_rate,
        "bitrate": bitrate,
    }


def prepare_audio(spec):
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    final_aac = CACHE_DIR / f"{key}.aac"
    if final_aac.exists() and final_aac.stat().st_size > 64:
        return str(final_aac)

    lock = get_prep_lock(key)
    with lock:
        if final_aac.exists() and final_aac.stat().st_size > 64:
            return str(final_aac)

        mp3 = CACHE_DIR / f".{key}.{uuid.uuid4().hex}.mp3"
        tmp_aac = CACHE_DIR / f".{key}.{uuid.uuid4().hex}.aac"
        try:
            subprocess.run(
                [
                    EDGE_TTS,
                    "--voice", spec["voice"],
                    "--rate", spec["rate"],
                    "--volume", spec["edge_volume"],
                    "--text", spec["text"],
                    "--write-media", str(mp3),
                ],
                check=True,
                timeout=90,
            )

            gain_db = max(-12.0, min(float(spec["gain_db"]), 12.0))
            audio_filter = f"volume={gain_db}dB,alimiter=limit=0.97"
            subprocess.run(
                [
                    FFMPEG,
                    "-y",
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
                timeout=90,
            )
            if not tmp_aac.exists() or tmp_aac.stat().st_size <= 64:
                raise RuntimeError("ffmpeg produced an empty AAC file")
            os.replace(tmp_aac, final_aac)
            return str(final_aac)
        finally:
            for path in (mp3, tmp_aac):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def send_to_camera(camera_id, cfg, aac_file):
    env = os.environ.copy()
    env.update(
        {
            "HCNETSDK_ROOT": HCNETSDK_ROOT,
            "CAMERA_IP": str(cfg["ip"]),
            "CAMERA_PORT": str(cfg.get("port", 8000)),
            "CAMERA_USER": str(cfg["username"]),
            "CAMERA_PASSWORD": str(cfg["password"]),
            "VOICE_CHAN": str(cfg.get("voice_chan", 1)),
            "AUDIO_SAMPLE_RATE": str(cfg.get("sample_rate", 16000)),
            "LD_LIBRARY_PATH": f"{HCNETSDK_ROOT}/lib:{HCNETSDK_ROOT}/lib/HCNetSDKCom:" + env.get("LD_LIBRARY_PATH", ""),
        }
    )
    result = subprocess.run(
        [SEND_AAC, aac_file],
        env=env,
        capture_output=True,
        text=True,
        timeout=SEND_TIMEOUT,
    )
    if result.stdout:
        log(f"[{camera_id}] send_aac: {result.stdout.strip()}")
    if result.stderr:
        log(f"[{camera_id}] send_aac stderr: {result.stderr.strip()}")
    if result.returncode != 0:
        raise RuntimeError(f"send_aac exited with code {result.returncode}")


class CameraWorker:
    def __init__(self, camera_id, cfg):
        self.camera_id = camera_id
        self.cfg = cfg
        self.queue = queue.Queue(maxsize=int(cfg.get("queue_size", 20)))
        self.current_job = None
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"camera-{camera_id}")
        self.thread.start()

    def enqueue(self, item):
        self.queue.put_nowait(item)

    def run(self):
        while True:
            item = self.queue.get()
            job_id = item["job_id"]
            self.current_job = job_id
            try:
                update_job(job_id, status="preparing")
                aac_file = item["future"].result(timeout=PREP_TIMEOUT)
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


def require_auth():
    return request.headers.get("X-API-Key", "") == API_KEY


def parse_targets(value):
    if value is None or value == "":
        if not DEFAULT_CAMERA:
            raise ValueError("camera is required because no default_camera is configured")
        return [DEFAULT_CAMERA]
    if value == "all":
        return list(CAMERAS.keys())
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value:
        return [str(x) for x in value]
    raise ValueError("camera must be a camera id, a list, or 'all'")


def enqueue_request(camera_value, data):
    text = str(data.get("text", "")).strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text too long; max={MAX_TEXT}")

    targets = parse_targets(camera_value)
    unknown = [x for x in targets if x not in CAMERAS]
    if unknown:
        raise ValueError(f"unknown camera(s): {', '.join(unknown)}")

    overrides = {
        "voice": data.get("voice"),
        "rate": data.get("rate"),
        "edge_volume": data.get("edge_volume"),
        "gain_db": data.get("gain_db"),
    }

    staged = []
    futures = {}
    # Serialize producers so concurrent Home Assistant requests cannot race
    # between queue capacity checks and put_nowait(). Camera workers continue
    # consuming normally while this short critical section runs.
    with enqueue_lock:
        for camera_id in targets:
            worker = WORKERS[camera_id]
            if worker.queue.full():
                raise OverflowError(f"queue full for camera: {camera_id}")

        for camera_id in targets:
            worker = WORKERS[camera_id]
            cfg = CAMERAS[camera_id]
            spec = audio_spec(cfg, text, overrides)
            spec_key = json.dumps(spec, ensure_ascii=False, sort_keys=True)
            future = futures.get(spec_key)
            if future is None:
                future = prep_pool.submit(prepare_audio, spec)
                futures[spec_key] = future
            job = new_job(camera_id, text)
            worker.enqueue({"job_id": job["id"], "future": future})
            staged.append((worker, job, future))

    return [job_snapshot(job["id"]) for _, job, _ in staged]


@app.get("/health")
def health():
    camera_status = {}
    for camera_id, worker in WORKERS.items():
        camera_status[camera_id] = {
            "queued": worker.queue.qsize(),
            "current_job": worker.current_job,
        }
    return jsonify({"status": "ok", "default_camera": DEFAULT_CAMERA or None, "cameras": camera_status})


@app.get("/cameras")
def cameras():
    if not require_auth():
        return jsonify({"error": "unauthorized"}), 401
    result = []
    for camera_id, cfg in CAMERAS.items():
        result.append(
            {
                "id": camera_id,
                "ip": cfg["ip"],
                "port": cfg.get("port", 8000),
                "voice": cfg.get("voice"),
                "gain_db": cfg.get("gain_db"),
                "queued": WORKERS[camera_id].queue.qsize(),
            }
        )
    return jsonify({"cameras": result})


@app.get("/jobs/<job_id>")
def job_status(job_id):
    if not require_auth():
        return jsonify({"error": "unauthorized"}), 401
    job = job_snapshot(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.post("/say")
def say():
    if not require_auth():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    try:
        result = enqueue_request(data.get("camera"), data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OverflowError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"status": "queued", "jobs": result}), 202


@app.post("/say/<camera_id>")
def say_camera(camera_id):
    if not require_auth():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    try:
        result = enqueue_request(camera_id, data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OverflowError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"status": "queued", "jobs": result}), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
