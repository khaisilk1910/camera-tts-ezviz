from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
PERCENT_RE = re.compile(r"^([+-])(\d{1,3})%$")
BITRATE_RE = re.compile(r"^(\d{1,3})([kK]?)$")
SLOT_RE = re.compile(r"^CAMERA_(\d{1,3})_")
VENDORS = {"ezviz", "imou", "dahua"}
VENDOR_ALIASES = {"hikvision": "ezviz", "hik": "ezviz"}


class ConfigError(ValueError):
    pass


def _first(env: Mapping[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _read_secret_file(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"secret file not found: {p}")
    return p.read_text(encoding="utf-8").strip()


def _secret_or_env(env: Mapping[str, str], value_name: str, file_name: str, default: str = "") -> str:
    value = str(env.get(value_name, "")).strip()
    if value:
        return value
    file_path = str(env.get(file_name, "")).strip()
    if file_path:
        return _read_secret_file(file_path)
    return default


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disable", "disabled"}:
        return False
    raise ConfigError(f"invalid boolean value: {value!r}")


def parse_int(value: Any, name: str, default: int, minimum: int, maximum: int) -> int:
    if value is None or str(value).strip() == "":
        result = default
    else:
        try:
            result = int(str(value).strip())
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return result


def parse_float(value: Any, name: str, default: float, minimum: float, maximum: float) -> float:
    if value is None or str(value).strip() == "":
        result = default
    else:
        try:
            result = float(str(value).strip())
        except ValueError as exc:
            raise ConfigError(f"{name} must be a number") from exc
    if not minimum <= result <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return result


def validate_percent(value: str, name: str) -> str:
    value = str(value).strip()
    match = PERCENT_RE.fullmatch(value)
    if not match:
        raise ConfigError(f"{name} must look like +0%, +10% or -10%")
    amount = int(match.group(2))
    if amount > 100:
        raise ConfigError(f"{name} absolute percentage must be <= 100%")
    return value


def validate_bitrate(value: str, name: str = "TTS_BITRATE") -> str:
    value = str(value).strip()
    match = BITRATE_RE.fullmatch(value)
    if not match:
        raise ConfigError(f"{name} must look like 32k or 64k")
    numeric = int(match.group(1))
    kbps = numeric if match.group(2) else max(1, numeric // 1000)
    if not 8 <= kbps <= 256:
        raise ConfigError(f"{name} must be between 8k and 256k")
    return value.lower()


@dataclass(frozen=True)
class Settings:
    port: int
    api_key: str
    allow_no_auth: bool
    max_text: int
    prep_workers: int
    http_threads: int
    send_timeout: int
    prep_timeout: int
    tts_timeout: int
    job_history: int
    cache_dir: str
    cache_max_mb: int
    cache_ttl_days: int
    hcnetsdk_root: str
    send_aac: str
    edge_tts: str
    ffmpeg: str
    allow_request_overrides: bool
    allow_duplicate_camera_targets: bool
    default_camera: str
    camera_default_port: int
    camera_default_voice_chan: int
    queue_size: int
    tts_voice: str
    tts_rate: str
    tts_edge_volume: str
    tts_gain_db: float
    tts_sample_rate: int
    tts_bitrate: str


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    api_key = _secret_or_env(env, "API_KEY", "API_KEY_FILE", "")
    if not api_key:
        api_key = _secret_or_env(env, "CAMERA_TTS_API_KEY", "CAMERA_TTS_API_KEY_FILE", "change-me-now")

    return Settings(
        port=parse_int(_first(env, "PORT", "CAMERA_TTS_PORT", default="8124"), "PORT", 8124, 1024, 65535),
        api_key=api_key,
        allow_no_auth=parse_bool(env.get("ALLOW_NO_AUTH"), False),
        max_text=parse_int(_first(env, "MAX_TEXT", "CAMERA_TTS_MAX_TEXT", default="700"), "MAX_TEXT", 700, 1, 5000),
        prep_workers=parse_int(_first(env, "PREP_WORKERS", "CAMERA_TTS_PREP_WORKERS", default="4"), "PREP_WORKERS", 4, 1, 32),
        http_threads=parse_int(_first(env, "HTTP_THREADS", default="8"), "HTTP_THREADS", 8, 2, 64),
        send_timeout=parse_int(_first(env, "SEND_TIMEOUT", "CAMERA_TTS_SEND_TIMEOUT", default="180"), "SEND_TIMEOUT", 180, 10, 1800),
        prep_timeout=parse_int(_first(env, "PREP_TIMEOUT", "CAMERA_TTS_PREP_TIMEOUT", default="300"), "PREP_TIMEOUT", 300, 30, 1800),
        tts_timeout=parse_int(_first(env, "TTS_TIMEOUT", default="120"), "TTS_TIMEOUT", 120, 10, 600),
        job_history=parse_int(_first(env, "JOB_HISTORY", "CAMERA_TTS_JOB_HISTORY", default="500"), "JOB_HISTORY", 500, 20, 5000),
        cache_dir=_first(env, "CACHE_DIR", "CAMERA_TTS_CACHE_DIR", default="/cache"),
        cache_max_mb=parse_int(_first(env, "CACHE_MAX_MB", default="512"), "CACHE_MAX_MB", 512, 32, 32768),
        cache_ttl_days=parse_int(_first(env, "CACHE_TTL_DAYS", default="30"), "CACHE_TTL_DAYS", 30, 0, 3650),
        hcnetsdk_root=_first(env, "HCNETSDK_ROOT", default="/opt/hcnetsdk"),
        send_aac=_first(env, "SEND_AAC", "CAMERA_TTS_SEND_AAC", default="/usr/local/bin/send_aac"),
        edge_tts=_first(env, "EDGE_TTS", "CAMERA_TTS_EDGE_TTS", default="/usr/local/bin/edge-tts"),
        ffmpeg=_first(env, "FFMPEG", "CAMERA_TTS_FFMPEG", default="/usr/bin/ffmpeg"),
        allow_request_overrides=parse_bool(env.get("ALLOW_REQUEST_OVERRIDES"), True),
        allow_duplicate_camera_targets=parse_bool(env.get("ALLOW_DUPLICATE_CAMERA_TARGETS"), False),
        default_camera=str(env.get("DEFAULT_CAMERA", "")).strip(),
        camera_default_port=parse_int(env.get("CAMERA_DEFAULT_PORT"), "CAMERA_DEFAULT_PORT", 8000, 1, 65535),
        camera_default_voice_chan=parse_int(env.get("CAMERA_DEFAULT_VOICE_CHAN"), "CAMERA_DEFAULT_VOICE_CHAN", 1, 1, 64),
        queue_size=parse_int(_first(env, "QUEUE_SIZE", default="30"), "QUEUE_SIZE", 30, 1, 500),
        tts_voice=_first(env, "TTS_VOICE", default="vi-VN-HoaiMyNeural"),
        tts_rate=validate_percent(_first(env, "TTS_RATE", default="+0%"), "TTS_RATE"),
        tts_edge_volume=validate_percent(_first(env, "TTS_EDGE_VOLUME", default="+0%"), "TTS_EDGE_VOLUME"),
        tts_gain_db=parse_float(_first(env, "TTS_GAIN_DB", default="4"), "TTS_GAIN_DB", 4.0, -20.0, 12.0),
        tts_sample_rate=parse_int(_first(env, "TTS_SAMPLE_RATE", default="16000"), "TTS_SAMPLE_RATE", 16000, 8000, 48000),
        tts_bitrate=validate_bitrate(_first(env, "TTS_BITRATE", default="32k")),
    )


def _normalize_camera(raw: Mapping[str, Any], camera_id: str, settings: Settings) -> dict[str, Any]:
    camera_id = str(camera_id).strip()
    if not CAMERA_ID_RE.fullmatch(camera_id):
        raise ConfigError(f"invalid camera id {camera_id!r}; use letters, numbers, dot, dash or underscore")

    # `vendors` is accepted because it is the spelling used by the Docker JSON
    # configuration requested by users. `vendor` is the canonical internal key.
    vendor_raw = str(raw.get("vendor") or raw.get("vendors") or "ezviz").strip().lower()
    vendor = VENDOR_ALIASES.get(vendor_raw, vendor_raw)
    if vendor not in VENDORS:
        raise ConfigError(f"camera {camera_id}: unsupported vendor {vendor_raw!r}; use ezviz, imou or dahua")

    ip = str(raw.get("ip", raw.get("host", ""))).strip()
    username = str(raw.get("username", raw.get("user", "admin"))).strip()
    password = str(raw.get("password", ""))
    password_file = str(raw.get("password_file", "")).strip()
    if not password and password_file:
        password = _read_secret_file(password_file)

    missing = []
    if not ip:
        missing.append("ip")
    if not username:
        missing.append("user")
    if not password:
        missing.append("password")
    if missing:
        raise ConfigError(f"camera {camera_id}: missing {', '.join(missing)}")

    rate = validate_percent(str(raw.get("rate", settings.tts_rate)), f"camera {camera_id} rate")
    edge_volume = validate_percent(str(raw.get("edge_volume", settings.tts_edge_volume)), f"camera {camera_id} edge_volume")
    default_talk_port = settings.camera_default_port if vendor == "ezviz" else 37777
    default_sample_rate = settings.tts_sample_rate if vendor == "ezviz" else 16000
    ptz_enabled = parse_bool(raw.get("ptz", raw.get("ptz_enabled")), False)
    ptz_protocol = str(raw.get("ptz_protocol", "auto")).strip().lower() or "auto"
    if ptz_protocol not in {"auto", "dahua", "hcnetsdk", "isapi", "none"}:
        raise ConfigError(
            f"camera {camera_id}: ptz_protocol must be auto, dahua, hcnetsdk, isapi or none"
        )
    if ptz_protocol == "auto":
        # EZVIZ/Hikvision firmware frequently exposes HCNetSDK even when the
        # optional ISAPI web endpoint is absent. Use the local SDK by default.
        ptz_protocol = "dahua" if vendor in {"imou", "dahua"} else "hcnetsdk"
    if not ptz_enabled:
        ptz_protocol = "none"

    mic_url = str(raw.get("mic_url", "")).strip()
    if mic_url and not mic_url.lower().startswith(("rtsp://", "http://", "https://")):
        raise ConfigError(f"camera {camera_id}: mic_url must start with rtsp://, http:// or https://")

    return {
        "id": camera_id,
        "vendor": vendor,
        "ip": ip,
        "port": parse_int(raw.get("port"), f"camera {camera_id} port", default_talk_port, 1, 65535),
        "username": username,
        "password": password,
        "voice_chan": parse_int(raw.get("voice_chan"), f"camera {camera_id} voice_chan", settings.camera_default_voice_chan, 1, 64),
        "queue_size": parse_int(raw.get("queue_size"), f"camera {camera_id} queue_size", settings.queue_size, 1, 500),
        "voice": str(raw.get("voice", settings.tts_voice)).strip() or settings.tts_voice,
        "rate": rate,
        "edge_volume": edge_volume,
        "gain_db": parse_float(raw.get("gain_db"), f"camera {camera_id} gain_db", settings.tts_gain_db, -20.0, 12.0),
        "sample_rate": parse_int(raw.get("sample_rate"), f"camera {camera_id} sample_rate", default_sample_rate, 8000, 48000),
        "bitrate": validate_bitrate(str(raw.get("bitrate", settings.tts_bitrate)), f"camera {camera_id} bitrate"),
        "mic_url": mic_url,
        "ptz_enabled": ptz_enabled,
        "ptz_protocol": ptz_protocol,
        "ptz_port": parse_int(raw.get("ptz_port"), f"camera {camera_id} ptz_port", 80, 1, 65535),
        "ptz_channel": parse_int(
            raw.get("ptz_channel"), f"camera {camera_id} ptz_channel",
            1 if ptz_protocol == "isapi" else 0, 0, 64
        ),
        "ptz_speed": parse_int(raw.get("ptz_speed"), f"camera {camera_id} ptz_speed", 50, 1, 100),
        "intercom": parse_bool(raw.get("intercom"), True),
        "intercom_key": str(raw.get("intercom_key", "")).strip(),
    }

def _json_camera_entries(env: Mapping[str, str]) -> list[tuple[str, Mapping[str, Any]]]:
    raw_json = str(env.get("CAMERAS_JSON", "")).strip()
    if not raw_json:
        return []
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"CAMERAS_JSON is invalid JSON: {exc.msg}") from exc

    result: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(parsed, dict):
        for camera_id, cfg in parsed.items():
            if not isinstance(cfg, dict):
                raise ConfigError(f"CAMERAS_JSON camera {camera_id!r} must be an object")
            result.append((str(camera_id), cfg))
        return result

    if isinstance(parsed, list):
        for index, cfg in enumerate(parsed, 1):
            if not isinstance(cfg, dict):
                raise ConfigError(f"CAMERAS_JSON item {index} must be an object")
            camera_id = str(cfg.get("id", "")).strip()
            if not camera_id:
                raise ConfigError(f"CAMERAS_JSON item {index} is missing id")
            result.append((camera_id, cfg))
        return result

    raise ConfigError("CAMERAS_JSON must be a JSON object or array")


def _slot_numbers(env: Mapping[str, str]) -> list[int]:
    slots = set()
    for key in env.keys():
        match = SLOT_RE.match(str(key))
        if match:
            slots.add(int(match.group(1)))
    return sorted(slots)


def _slot_value(env: Mapping[str, str], slot: int, suffix: str, default: str = "", strip: bool = True) -> str:
    padded = f"CAMERA_{slot:02d}_{suffix}"
    plain = f"CAMERA_{slot}_{suffix}"
    if padded in env:
        value = str(env.get(padded, default))
    elif plain in env:
        value = str(env.get(plain, default))
    else:
        value = default
    return value.strip() if strip else value


def _indexed_camera_entries(env: Mapping[str, str]) -> list[tuple[str, Mapping[str, Any]]]:
    result: list[tuple[str, Mapping[str, Any]]] = []
    for slot in _slot_numbers(env):
        enabled_raw = _slot_value(env, slot, "ENABLED", "")
        ip = _slot_value(env, slot, "IP", "")
        if enabled_raw:
            enabled = parse_bool(enabled_raw, False)
        else:
            enabled = bool(ip)
        if not enabled:
            continue

        camera_id = _slot_value(env, slot, "ID", f"cam{slot:02d}") or f"cam{slot:02d}"
        password = _slot_value(env, slot, "PASSWORD", "", strip=False)
        password_file = _slot_value(env, slot, "PASSWORD_FILE", "")
        cfg: dict[str, Any] = {
            "ip": ip,
            "port": _slot_value(env, slot, "PORT", ""),
            "username": _slot_value(env, slot, "USER", "admin") or "admin",
            "password": password,
            "password_file": password_file,
            "voice_chan": _slot_value(env, slot, "VOICE_CHAN", ""),
            "gain_db": _slot_value(env, slot, "GAIN_DB", ""),
            "queue_size": _slot_value(env, slot, "QUEUE_SIZE", ""),
            "voice": _slot_value(env, slot, "VOICE", ""),
            "rate": _slot_value(env, slot, "RATE", ""),
            "edge_volume": _slot_value(env, slot, "EDGE_VOLUME", ""),
            "sample_rate": _slot_value(env, slot, "SAMPLE_RATE", ""),
            "bitrate": _slot_value(env, slot, "BITRATE", ""),
            "vendors": (_slot_value(env, slot, "VENDOR", "")
                        or _slot_value(env, slot, "VENDORS", "ezviz")
                        or "ezviz"),
            "mic_url": _slot_value(env, slot, "MIC_URL", ""),
            "ptz": _slot_value(env, slot, "PTZ", ""),
            "ptz_protocol": _slot_value(env, slot, "PTZ_PROTOCOL", ""),
            "ptz_port": _slot_value(env, slot, "PTZ_PORT", ""),
            "ptz_channel": _slot_value(env, slot, "PTZ_CHANNEL", ""),
            "ptz_speed": _slot_value(env, slot, "PTZ_SPEED", ""),
            "intercom": _slot_value(env, slot, "INTERCOM", ""),
            "intercom_key": _slot_value(env, slot, "INTERCOM_KEY", ""),
        }
        # Empty optional values should fall back to global defaults rather than
        # being interpreted as explicit empty strings.
        cfg = {key: value for key, value in cfg.items() if value != ""}
        result.append((camera_id, cfg))
    return result


def load_cameras(settings: Settings, env: Mapping[str, str] | None = None) -> tuple[dict[str, dict[str, Any]], str]:
    env = os.environ if env is None else env
    cameras: dict[str, dict[str, Any]] = {}

    entries = _json_camera_entries(env) + _indexed_camera_entries(env)
    for camera_id, raw_cfg in entries:
        camera = _normalize_camera(raw_cfg, camera_id, settings)
        if camera_id in cameras:
            raise ConfigError(f"duplicate camera id: {camera_id}")
        cameras[camera_id] = camera

    if not settings.allow_duplicate_camera_targets:
        seen_targets: dict[tuple[str, str, int, int], str] = {}
        for camera_id, camera in cameras.items():
            target = (camera["vendor"], camera["ip"].lower(), int(camera["port"]), int(camera["voice_chan"]))
            existing = seen_targets.get(target)
            if existing:
                raise ConfigError(
                    f"camera {camera_id} duplicates target of {existing}; "
                    "use one id per physical voice channel or set ALLOW_DUPLICATE_CAMERA_TARGETS=true"
                )
            seen_targets[target] = camera_id

    default_camera = settings.default_camera
    if default_camera:
        if default_camera not in cameras:
            raise ConfigError(f"DEFAULT_CAMERA {default_camera!r} is not configured")
    elif len(cameras) == 1:
        default_camera = next(iter(cameras))

    return cameras, default_camera


def public_settings(settings: Settings) -> dict[str, Any]:
    return {
        "port": settings.port,
        "max_text": settings.max_text,
        "prep_workers": settings.prep_workers,
        "http_threads": settings.http_threads,
        "queue_size": settings.queue_size,
        "cache_max_mb": settings.cache_max_mb,
        "cache_ttl_days": settings.cache_ttl_days,
        "allow_request_overrides": settings.allow_request_overrides,
        "tts_voice": settings.tts_voice,
        "tts_rate": settings.tts_rate,
        "tts_edge_volume": settings.tts_edge_volume,
        "tts_gain_db": settings.tts_gain_db,
        "tts_sample_rate": settings.tts_sample_rate,
        "tts_bitrate": settings.tts_bitrate,
    }
