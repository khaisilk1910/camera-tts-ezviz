# Camera TTS EZVIZ Docker v2.4.0 (reviewed)

Docker backend phát TTS và audio/nhạc ra loa camera EZVIZ/Hikvision qua HCNetSDK.

Home Assistant custom integration đã được tách sang repository riêng: `https://github.com/khaisilk1910/camera_tts_ezviz_hacs`.

## Tính năng

- HCNetSDK tích hợp sẵn trong Docker image, host không cần cài SDK.
- Nhiều camera qua `CAMERAS_JSON`.
- Queue riêng từng camera, hỗ trợ `add`, `next`, `play`, `replace`; không phát chồng audio trên cùng camera.
- Persistent HCNetSDK login/worker để giảm độ trễ.
- Guard mở lại VoiceTalk sau khi dừng/phát liên tiếp để giảm lỗi HCNetSDK do camera chưa nhả kênh.
- Edge TTS + cache hai tầng: MP3 gốc và AAC cuối.
- Single-flight: cùng nội dung đồng thời chỉ tạo audio một lần.
- Cache riêng cho file/URL nhạc sau khi convert AAC.
- Phát MP3/M4A/WAV/AAC/URL audio qua `POST /media`.
- Dừng media và xóa queue camera qua `POST /stop/<camera>`.
- Giữ nguyên `POST /say` để tương thích `rest_command` và automation cũ.
- API `/cameras` cung cấp trạng thái, backend capabilities và kết nối HCNetSDK cho Home Assistant.
- Gain loa có thể chỉnh từ Home Assistant và lưu trong volume `/cache`.

> VoiceTalk của camera dùng AAC-LC mono 16 kHz / 32 kbps. Nhạc phát được nhưng chất lượng phụ thuộc loa thoại của camera.

## Stack Portainer

```yaml
services:
  camera-tts:
    image: "ghcr.io/khaisilk1910/camera-tts-ezviz:latest"
    hostname: "camera-tts-ezviz"
    network_mode: host

    environment:
      PORT: "${PORT:-8124}"
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
      SENDER_START_TIMEOUT: "8"

      MEDIA_PREP_TIMEOUT: "900"
      MEDIA_SEND_TIMEOUT: "7200"
      MEDIA_MAX_URL_LENGTH: "4096"

      ALLOW_REQUEST_OVERRIDES: "true"
      ALLOW_DUPLICATE_CAMERA_TARGETS: "false"

      # Optional:
      # PRECACHE_TEXTS_JSON: '["Có người trước cổng","Có khách đến"]'

    restart: unless-stopped
    volumes:
      - camera_tts_cache:/cache
    stop_grace_period: 15s

volumes:
  camera_tts_cache:
    name: "camera-tts-ezviz-cache"
```

Do dùng `network_mode: host`, không khai báo `ports:`.

## Environment tối thiểu

```env
PORT=8124
API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"CHANGE_ME"}}
```

Nhiều camera:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

### Override riêng camera

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS","port":8000,"voice_chan":1,"gain_db":6,"queue_size":40}}
```

## Điều chỉnh âm lượng

Mức chung:

```env
TTS_GAIN_DB=4
```

Có thể thử `5` hoặc `6`. Camera riêng có thể đặt `gain_db` trong `CAMERAS_JSON`.

## API

Health:

```bash
curl http://192.168.31.100:8124/health
```

Danh sách camera:

```bash
curl -H "X-API-Key: YOUR_API_KEY" http://192.168.31.100:8124/cameras
```

TTS (mặc định nối cuối queue):

```bash
curl -X POST http://192.168.31.100:8124/say/gate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"text":"Xin chào các bạn","queue_mode":"add"}'
```

`queue_mode`: `add` = cuối hàng đợi, `next` = phát kế tiếp, `play` = ngắt item hiện tại nhưng giữ queue, `replace` = ngắt và xóa queue.

Phát media URL:

```bash
curl -X POST http://192.168.31.100:8124/media/gate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"url":"https://example.com/music.mp3","title":"Music","replace":true}'
```

Dừng camera:

```bash
curl -X POST http://192.168.31.100:8124/stop/gate \
  -H "X-API-Key: YOUR_API_KEY"
```

Cache stats:

```bash
curl -H "X-API-Key: YOUR_API_KEY" http://192.168.31.100:8124/cache/stats
```

### Chỉnh gain loa runtime

```bash
curl -X PATCH http://192.168.31.100:8124/cameras/gate/settings \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"gain_db":6}'
```

Giá trị được lưu tại `/cache/runtime-settings.json`. Reset về giá trị Stack bằng `{"gain_db":"default"}`.

## Chế độ local thực sự

Đường **Docker → camera** luôn đi LAN trực tiếp qua HCNetSDK; không dùng EZVIZ cloud. Tuy nhiên endpoint `/say` vẫn dùng `edge-tts`, nên bản thân cách này cần Internet để tổng hợp giọng.

Để TTS end-to-end local, dùng Home Assistant **Piper** với action `tts.speak` nhắm vào `media_player` của camera. Home Assistant tạo audio cục bộ, integration đưa URL Media Source nội bộ cho Docker, Docker chỉ convert AAC rồi gửi HCNetSDK trong LAN. Nếu yêu cầu hệ thống không được ra Internet, không dùng `/say`/`media_content_type: tts` trực tiếp.

## Home Assistant

Cài repository HACS riêng `https://github.com/khaisilk1910/camera_tts_ezviz_hacs`. Integration sẽ đọc `/cameras` và tự tạo một `media_player` cho từng camera.

REST API `/say` vẫn được giữ để các automation cũ tiếp tục chạy.


## Thay đổi v2.4.0

Xem `CHANGES_2.4.0.md`. Bản này bổ sung queue chuẩn Home Assistant, runtime speaker gain, capability negotiation và guard mở lại HCNetSDK; đồng thời giữ các sửa lỗi deadlock/single-flight của v2.3.3.
