"""On-demand local PTZ adapters.

EZVIZ/Hikvision defaults to a lazy persistent HCNetSDK control worker. The
worker is not started during Docker/Home Assistant startup; it is created only
when PTZ is used. Dahua/Imou keep the local Dahua CGI adapter. ISAPI remains an
explicit fallback for compatible Hikvision/EZVIZ firmware.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from typing import Any

import requests
from requests.auth import HTTPDigestAuth


class PTZError(RuntimeError):
    pass


_DAHUA_CODES = {
    "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "up_left": "LeftUp", "up_right": "RightUp", "down_left": "LeftDown", "down_right": "RightDown",
    "zoom_in": "ZoomTele", "zoom_out": "ZoomWide",
}


def _session(cfg: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    session.auth = HTTPDigestAuth(str(cfg["username"]), str(cfg["password"]))
    return session


def _request(session: requests.Session, method: str, url: str, *, timeout: float, **kwargs) -> requests.Response:
    response = session.request(method, url, timeout=timeout, **kwargs)
    if response.status_code in {401, 403}:
        raise PTZError(f"camera authentication rejected (HTTP {response.status_code})")
    if response.status_code >= 400:
        raise PTZError(f"camera PTZ HTTP {response.status_code}: {response.text[:200]}")
    return response


def _dahua(cfg: dict[str, Any], direction: str, speed: int, duration: float) -> None:
    code = _DAHUA_CODES.get(direction)
    if not code:
        raise PTZError(f"unsupported PTZ direction: {direction}")
    channel = int(cfg.get("ptz_channel", 0))
    speed8 = max(1, min(8, round(speed * 8 / 100)))
    base = f"http://{cfg['ip']}:{int(cfg.get('ptz_port', 80))}/cgi-bin/ptz.cgi"
    session = _session(cfg)
    params = {"action": "start", "channel": channel, "code": code, "arg1": 0, "arg2": speed8, "arg3": 0}
    _request(session, "GET", base, params=params, timeout=3.0)
    if duration > 0:
        time.sleep(duration)
        stop_params = {**params, "action": "stop"}
        _request(session, "GET", base, params=stop_params, timeout=3.0)


def _isapi(cfg: dict[str, Any], direction: str, speed: int, duration: float) -> None:
    channel = int(cfg.get("ptz_channel", 1)) or 1
    pan = tilt = zoom = 0
    magnitude = max(1, min(100, speed))
    if direction == "left": pan = -magnitude
    elif direction == "right": pan = magnitude
    elif direction == "up": tilt = magnitude
    elif direction == "down": tilt = -magnitude
    elif direction == "up_left": pan, tilt = -magnitude, magnitude
    elif direction == "up_right": pan, tilt = magnitude, magnitude
    elif direction == "down_left": pan, tilt = -magnitude, -magnitude
    elif direction == "down_right": pan, tilt = magnitude, -magnitude
    elif direction == "zoom_in": zoom = magnitude
    elif direction == "zoom_out": zoom = -magnitude
    else: raise PTZError(f"unsupported PTZ direction: {direction}")
    url = f"http://{cfg['ip']}:{int(cfg.get('ptz_port', 80))}/ISAPI/PTZCtrl/channels/{channel}/continuous"
    session = _session(cfg)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><PTZData xmlns="http://www.hikvision.com/ver20/XMLSchema">'
           f'<pan>{pan}</pan><tilt>{tilt}</tilt><zoom>{zoom}</zoom></PTZData>')
    _request(session, "PUT", url, data=xml.encode(), headers={"Content-Type": "application/xml"}, timeout=3.0)
    if duration > 0:
        time.sleep(duration)
        stop = ('<?xml version="1.0" encoding="UTF-8"?><PTZData xmlns="http://www.hikvision.com/ver20/XMLSchema">'
                '<pan>0</pan><tilt>0</tilt><zoom>0</zoom></PTZData>')
        _request(session, "PUT", url, data=stop.encode(), headers={"Content-Type": "application/xml"}, timeout=3.0)


class _HCNetSDKPTZWorker:
    """Lazy persistent HCNetSDK worker scoped to one camera."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[tuple[int, str]] = queue.Queue()
        self.lock = threading.Lock()

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        root = env.get("HCNETSDK_ROOT", "/opt/hcnetsdk")
        env.update({
            "HCNETSDK_ROOT": root,
            "CAMERA_IP": str(self.cfg["ip"]),
            "CAMERA_PORT": str(self.cfg.get("port", 8000)),
            "CAMERA_USER": str(self.cfg["username"]),
            "CAMERA_PASSWORD": str(self.cfg["password"]),
            "PTZ_CHANNEL": str(self.cfg.get("ptz_channel", 0)),
            "CAMERA_CONNECT_TIMEOUT_MS": env.get("CAMERA_CONNECT_TIMEOUT_MS", "3000"),
            "CAMERA_RECONNECT_INTERVAL_MS": env.get("CAMERA_RECONNECT_INTERVAL_MS", "10000"),
            "LD_LIBRARY_PATH": f"{root}/lib:{root}/lib/HCNetSDKCom:" + env.get("LD_LIBRARY_PATH", ""),
        })
        return env

    def _reader(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if line:
                    self.responses.put((proc.pid, line))
        finally:
            rc = proc.poll()
            self.responses.put((proc.pid, f"__EXIT__\t{rc if rc is not None else 'unknown'}"))

    def _next(self, proc: subprocess.Popen[str], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise queue.Empty
            pid, line = self.responses.get(timeout=remaining)
            if pid != proc.pid:
                continue
            if line.startswith("__EXIT__\t"):
                raise PTZError(f"HCNetSDK PTZ worker exited ({line.split(chr(9), 1)[1]})")
            return line

    def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write("QUIT\n")
                proc.stdin.flush()
                proc.wait(timeout=0.5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try: proc.kill()
                except Exception: pass

    def _start(self) -> subprocess.Popen[str]:
        if self.proc is not None and self.proc.poll() is None:
            return self.proc
        self._stop()
        binary = os.environ.get("PTZ_HCNETSDK_BIN", "/usr/local/bin/ptz_hcnetsdk")
        try:
            proc = subprocess.Popen(
                [binary, "--worker"], env=self._env(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
        except OSError as exc:
            raise PTZError(f"cannot start HCNetSDK PTZ worker: {exc}") from exc
        self.proc = proc
        threading.Thread(target=self._reader, args=(proc,), daemon=True,
                         name=f"ptz-sdk-{self.cfg.get('id', 'camera')}").start()
        try:
            line = self._next(proc, float(os.environ.get("PTZ_SENDER_START_TIMEOUT", "5")))
        except queue.Empty as exc:
            self._stop()
            raise PTZError("HCNetSDK PTZ worker startup timed out") from exc
        if not line.startswith("READY\t"):
            self._stop()
            raise PTZError(f"HCNetSDK PTZ worker startup failed: {line}")
        return proc

    def move(self, direction: str, speed: int, duration: float) -> None:
        with self.lock:
            proc = self._start()
            if proc.stdin is None:
                raise PTZError("HCNetSDK PTZ worker stdin unavailable")
            duration_ms = max(50, min(10000, int(round(duration * 1000))))
            try:
                proc.stdin.write(f"MOVE\t{direction}\t{int(speed)}\t{duration_ms}\n")
                proc.stdin.flush()
                line = self._next(proc, max(4.0, duration + 4.0))
            except (BrokenPipeError, OSError, queue.Empty) as exc:
                self._stop()
                raise PTZError(f"HCNetSDK PTZ command failed: {exc}") from exc
            if not line.startswith("OK\t"):
                raise PTZError(f"HCNetSDK PTZ failed: {line}")


_HCNET_WORKERS: dict[str, _HCNetSDKPTZWorker] = {}
_HCNET_WORKERS_LOCK = threading.Lock()


def _hcnetsdk(cfg: dict[str, Any], direction: str, speed: int, duration: float) -> None:
    key = str(cfg.get("id") or f"{cfg.get('ip')}:{cfg.get('port', 8000)}")
    with _HCNET_WORKERS_LOCK:
        worker = _HCNET_WORKERS.get(key)
        if worker is None:
            worker = _HCNetSDKPTZWorker(cfg)
            _HCNET_WORKERS[key] = worker
    worker.move(direction, speed, duration)


def ptz_move(cfg: dict[str, Any], direction: str, *, speed: int | None = None, duration: float = 0.35) -> None:
    if not cfg.get("ptz_enabled") or cfg.get("ptz_protocol") == "none":
        raise PTZError("PTZ is disabled for this camera")
    speed = int(speed or cfg.get("ptz_speed", 50))
    speed = max(1, min(speed, 100))
    duration = max(0.05, min(float(duration), 10.0))
    protocol = str(cfg.get("ptz_protocol") or "none").lower()
    if protocol == "dahua":
        _dahua(cfg, direction, speed, duration)
    elif protocol == "hcnetsdk":
        _hcnetsdk(cfg, direction, speed, duration)
    elif protocol == "isapi":
        _isapi(cfg, direction, speed, duration)
    else:
        raise PTZError(f"unsupported PTZ protocol: {protocol}")
