"""Local PTZ adapters for Dahua CGI and Hikvision/EZVIZ ISAPI.

PTZ is command-driven only: no network probe runs during Docker or Home Assistant startup.
"""
from __future__ import annotations

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
    channel = int(cfg.get("ptz_channel", 1))
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


def ptz_move(cfg: dict[str, Any], direction: str, *, speed: int | None = None, duration: float = 0.35) -> None:
    if not cfg.get("ptz_enabled") or cfg.get("ptz_protocol") == "none":
        raise PTZError("PTZ is disabled for this camera")
    speed = int(speed or cfg.get("ptz_speed", 4))
    duration = max(0.05, min(float(duration), 10.0))
    protocol = cfg.get("ptz_protocol")
    if protocol == "dahua":
        _dahua(cfg, direction, speed, duration)
    elif protocol == "isapi":
        _isapi(cfg, direction, speed, duration)
    else:
        raise PTZError(f"unsupported PTZ protocol: {protocol}")
