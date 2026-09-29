# Camera TTS Multi-Vendor Docker v2.5.1

Backend local cho **EZVIZ/Hikvision, Imou và Dahua**, dùng chung một API cho TTS, media, PTZ, Assist audio và đàm thoại hai chiều.

## Kiến trúc v2.5.1

`vendors` (hoặc `vendor`) trong từng camera quyết định adapter phát loa:

- `ezviz`: HCNetSDK cổng 8000, worker đăng nhập lâu dài để giảm độ trễ.
- `imou`: ưu tiên giao thức local visualtalk TCP/HTTP cổng 8086, AAC 16 kHz; nếu không dùng được sẽ fallback giao thức Dahua local cổng 37777, PCM 8 kHz và ghi nhớ fallback 1 giờ.
- `dahua`: cùng adapter local Imou/Dahua, ưu tiên 8086 rồi fallback 37777.

Docker giữ toàn bộ camera I/O. Home Assistant chỉ gọi HTTP API bất đồng bộ; không nạp HCNetSDK hoặc mở RTSP trong lúc HA khởi động.

## Tính năng

- TTS và audio nhiều hãng, queue riêng từng camera: `add`, `next`, `play`, `replace`.
- Cache + single-flight, convert audio bằng ffmpeg ngoài request thread.
- EZVIZ dùng persistent HCNetSDK worker; Imou/Dahua chỉ mở talk session khi thật sự phát.
- PTZ on-demand:
  - EZVIZ/Hikvision: ISAPI `continuous`.
  - Imou/Dahua: Dahua CGI PTZ (bật riêng bằng `ptz:true`).
- Assist: endpoint upload WAV nội bộ cho câu trả lời TTS của Home Assistant.
- `mic_url`: HA có thể đọc mic camera/go2rtc bằng ffmpeg và chạy native Assist pipeline.
- Intercom: cổng riêng `8125`, không chiếm worker API; nhận PCMA 8 kHz từ go2rtc, voice-gate để chỉ mở talk channel khi có người nói và đóng sau khoảng 1,5 giây im lặng.
- API key cho API chính; intercom dùng token riêng theo camera/HMAC.
- Không probe PTZ/mic/camera phụ trong startup Docker ngoài EZVIZ warm worker hiện hữu.

## Portainer stack

Dùng `network_mode: host` để Docker truy cập camera LAN trực tiếp và để go2rtc gọi cổng intercom với độ trễ thấp.

```yaml
services:
  camera-tts:
    image: "ghcr.io/khaisilk1910/camera-tts-ezviz:latest"
    hostname: "camera-tts-ezviz"
    network_mode: host

    environment:
      PORT: "${PORT:-8124}"
      INTERCOM_PORT: "${INTERCOM_PORT:-8125}"
      API_KEY: "${API_KEY:-change-me-now}"
      DEFAULT_CAMERA: "${DEFAULT_CAMERA:-}"
      TTS_GAIN_DB: "${TTS_GAIN_DB:-4}"
      TTS_VOICE: "${TTS_VOICE:-vi-VN-HoaiMyNeural}"
      CAMERAS_JSON: "${CAMERAS_JSON:-}"

      TZ: "Asia/Ho_Chi_Minh"
      ALLOW_NO_AUTH: "false"
      TTS_RATE: "+0%"
      TTS_EDGE_VOLUME: "+0%"
      TTS_SAMPLE_RATE: "16000"
      TTS_BITRATE: "32k"
      QUEUE_SIZE: "30"
      MAX_TEXT: "700"
      PREP_WORKERS: "4"
      HTTP_THREADS: "8"
      LOG_SUCCESSFUL_JOBS: "false"
      SEND_TIMEOUT: "180"
      PREP_TIMEOUT: "300"
      TTS_TIMEOUT: "120"
      JOB_HISTORY: "500"
      CACHE_DIR: "/cache"
      CACHE_MAX_MB: "512"
      CACHE_TTL_DAYS: "30"
      CAMERA_DEFAULT_PORT: "8000"
      CAMERA_DEFAULT_VOICE_CHAN: "1"
      CAMERA_CONNECT_TIMEOUT_MS: "3000"
      CAMERA_RECONNECT_INTERVAL_MS: "10000"
      VOICE_START_DELAY_MS: "120"
      VOICE_END_DELAY_MS: "80"
      VOICE_REOPEN_GUARD_MS: "1250"
      SENDER_START_TIMEOUT: "60"
      MEDIA_PREP_TIMEOUT: "900"
      MEDIA_SEND_TIMEOUT: "7200"
      MEDIA_MAX_URL_LENGTH: "4096"
      ALLOW_REQUEST_OVERRIDES: "true"
      ALLOW_DUPLICATE_CAMERA_TARGETS: "false"

    restart: unless-stopped
    volumes:
      - camera_tts_cache:/cache
    stop_grace_period: 15s

volumes:
  camera_tts_cache:
    name: "camera-tts-ezviz-cache"
```

Vì dùng host network nên **không khai báo `ports:`**. API chính mặc định `8124`; intercom mặc định `8125`.

## `CAMERAS_JSON`

### EZVIZ / Hikvision

```env
CAMERAS_JSON={"gate":{"vendors":"ezviz","ip":"192.168.31.59","user":"admin","password":"PASS","port":8000}}
```

Có PTZ và mic Assist:

```env
CAMERAS_JSON={"gate":{"vendors":"ezviz","ip":"192.168.31.59","user":"admin","password":"PASS","port":8000,"ptz":true,"ptz_protocol":"auto","ptz_speed":50,"mic_url":"rtsp://admin:PASS@192.168.31.59:554/Streaming/Channels/102"}}
```

### Imou

```env
CAMERAS_JSON={"yard":{"vendors":"imou","ip":"192.168.31.60","user":"admin","password":"PASS","ptz":true,"mic_url":"rtsp://admin:PASS@192.168.31.60:554/cam/realmonitor?channel=1&subtype=1"}}
```

`port` mặc định của Imou/Dahua là `37777`. Adapter 8086 luôn được thử trước; `37777` là fallback.

### Dahua

```env
CAMERAS_JSON={"garage":{"vendors":"dahua","ip":"192.168.31.61","user":"admin","password":"PASS","ptz":true,"ptz_protocol":"dahua","ptz_port":80,"ptz_channel":0}}
```

### Nhiều hãng cùng lúc

```env
CAMERAS_JSON={"gate":{"vendors":"ezviz","ip":"192.168.31.59","user":"admin","password":"PASS1"},"yard":{"vendors":"imou","ip":"192.168.31.60","user":"admin","password":"PASS2"},"garage":{"vendors":"dahua","ip":"192.168.31.61","user":"admin","password":"PASS3"}}
```

Các khóa mới:

| Khóa | Ý nghĩa |
|---|---|
| `vendors` / `vendor` | `ezviz`, `imou`, `dahua`; `hikvision` được alias về `ezviz` |
| `mic_url` | RTSP/HTTP(S) audio source để Home Assistant dùng Assist |
| `ptz` | bật/tắt PTZ; mặc định `false` |
| `ptz_protocol` | `auto`, `hcnetsdk`, `isapi`, `dahua`, `none`. `auto`: EZVIZ/Hikvision → HCNetSDK local; Imou/Dahua → Dahua CGI local. |
| `ptz_port` | HTTP PTZ port, mặc định 80; chỉ dùng cho `isapi`/`dahua`, không dùng cho HCNetSDK |
| `ptz_channel` | HCNetSDK mặc định `0` = tự lấy channel bắt đầu từ thiết bị; ISAPI mặc định 1; Dahua mặc định 0 |
| `ptz_speed` | tốc độ chuẩn hóa 1–100, mặc định 50 |
| `intercom` | bật/tắt backchannel, mặc định `true` |
| `intercom_key` | tùy chọn token cố định; bỏ trống sẽ dùng HMAC từ API key |

## API chính

```bash
# Camera/capabilities
curl -H "X-API-Key: YOUR_KEY" http://HOST:8124/cameras

# TTS
curl -X POST http://HOST:8124/say/gate \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"text":"Xin chào","queue_mode":"add"}'

# Media URL
curl -X POST http://HOST:8124/media/gate \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/audio.mp3","queue_mode":"replace"}'

# PTZ
curl -X POST http://HOST:8124/ptz/gate \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"direction":"left","speed":50,"duration":0.35}'

# Lấy source go2rtc cho intercom
curl -H "X-API-Key: YOUR_KEY" \
  "http://HOST:8124/cameras/gate/intercom-source?host=192.168.31.100"
```

PTZ direction: `left`, `right`, `up`, `down`, `up_left`, `up_right`, `down_left`, `down_right`, `zoom_in`, `zoom_out`.

## Đàm thoại hai chiều qua go2rtc

Gọi service `camera_tts_ezviz.get_intercom_source` trong Home Assistant để nhận dòng `exec:...#backchannel=1#audio=alaw/8000`, sau đó thêm dòng đó vào cuối danh sách source của stream camera trong `go2rtc.yaml`.

Backchannel dùng server riêng `INTERCOM_PORT` nên một phiên micro mở lâu **không giữ thread Waitress của API**. Voice activity gate chỉ mở camera talk khi có tiếng nói; khi im ~1,5 giây session đóng để camera trả mic về chiều nghe.

## Local

- **Docker → camera:** local LAN cho cả 3 vendor.
- **PTZ:** local HTTP/ISAPI/CGI.
- **Intercom:** local go2rtc → Docker → camera.
- **Assist mic:** camera/go2rtc → ffmpeg trong HA → native Assist pipeline.
- `/say` vẫn dùng `edge-tts`, vì vậy cần Internet cho khâu tổng hợp giọng.
- Muốn TTS local end-to-end: dùng Piper/Home Assistant Assist; HA upload WAV/Media Source qua Docker rồi Docker chỉ convert/phát trong LAN.

## Lưu ý PTZ

ISAPI của Hikvision/EZVIZ là giao diện chính thức. Với Imou/Dahua, CGI PTZ là giao diện được dùng rộng rãi trên firmware Dahua-compatible nhưng khả năng có thể khác theo model/firmware. Vì vậy PTZ **không tự probe**, mặc định tắt và chỉ chạy khi đặt `ptz:true`.

## Kiểm thử v2.5.1

- Python syntax compile cho toàn bộ backend.
- C++ `send_aac.cpp` compile bằng `g++ -std=c++17 -O2 -Wall -Wextra` với HCNetSDK headers/libs đi kèm.
- 31 unit/regression tests: config, queue race, single-flight, vendor mapping, PTZ start/stop, A-law, resample và intercom voice gate.

Xem chi tiết tại `CHANGES_2.5.1.md`.


### PTZ EZVIZ/Hikvision

Từ v2.5.1, `ptz_protocol:auto` dùng **HCNetSDK local** và lazy-start worker khi có lệnh PTZ đầu tiên. Điều này tránh phụ thuộc endpoint ISAPI mà một số firmware EZVIZ không cung cấp. Chỉ đặt `ptz_protocol:isapi` nếu bạn đã xác nhận firmware hỗ trợ `/ISAPI/PTZCtrl/...`.
