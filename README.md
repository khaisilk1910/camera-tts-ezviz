# Camera TTS EZVIZ

Docker service phát TTS tiếng Việt ra loa camera EZVIZ/Hikvision qua HCNetSDK.

## Tính năng

- HCNetSDK được tích hợp sẵn trong Docker image.
- Phát TTS qua API, phù hợp với Home Assistant.
- Hỗ trợ nhiều camera.
- Mỗi camera có queue riêng, các câu TTS cùng camera sẽ chờ nhau và không phát chồng tiếng.
- Các camera khác nhau có thể phát đồng thời.
- API `/say` trả phản hồi nhanh, không chờ phát TTS xong.
- Hỗ trợ cache TTS để câu lặp lại phát nhanh hơn.
- Điều chỉnh âm lượng chung hoặc riêng cho từng camera.
- Cấu hình camera bằng `CAMERAS_JSON`, không cần sửa file trong container.

---

## 1. Stack Portainer

Vào **Portainer → Stacks → Add stack → Web editor** và dùng:

```yaml
version: "3.8"

services:
  camera-tts:
    image: "ghcr.io/khaisilk1910/camera-tts-ezviz:latest"
    hostname: "camera-tts-ezviz"

    environment:
      # Các biến cấu hình từ Portainer
      PORT: "${PORT:-8124}"
      API_KEY: "${API_KEY:-change-me-now}"
      DEFAULT_CAMERA: "${DEFAULT_CAMERA:-}"
      TTS_GAIN_DB: "${TTS_GAIN_DB:-4}"
      TTS_VOICE: "${TTS_VOICE:-vi-VN-HoaiMyNeural}"
      CAMERAS_JSON: "${CAMERAS_JSON:-}"

      # Cấu hình chung
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
      ALLOW_REQUEST_OVERRIDES: "true"
      ALLOW_DUPLICATE_CAMERA_TARGETS: "false"

    volumes:
      - camera_tts_cache:/cache

    networks:
      - host

    stop_grace_period: 15s

    deploy:
      mode: replicated
      replicas: 1
      restart_policy:
        condition: on-failure
        delay: 3s
        max_attempts: 10
        window: 60s
      update_config:
        parallelism: 1
        order: stop-first
        failure_action: rollback
        monitor: 20s

volumes:
  camera_tts_cache:
    name: "camera-tts-ezviz-cache"

networks:
  host:
    external: true
```

Sau đó khai báo các biến trong **Environment variables** của Stack.

---

## 2. Environment variables

Cấu hình tối thiểu:

```env
PORT=8124
API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"CHANGE_ME"}}
```

Ý nghĩa:

- `PORT`: cổng API của Camera TTS.
- `API_KEY`: khóa bảo vệ API.
- `DEFAULT_CAMERA`: camera mặc định.
- `TTS_GAIN_DB`: âm lượng chung.
- `TTS_VOICE`: giọng Edge TTS.
- `CAMERAS_JSON`: danh sách camera.

Sau khi thay đổi Environment, bấm **Update the stack**.

---

## 3. Khai báo nhiều camera

Một camera:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"}}
```

Hai camera:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

Ba camera:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"},"livingroom":{"ip":"192.168.31.61","user":"admin","password":"PASS_LIVINGROOM"}}
```

Mỗi camera mặc định sử dụng:

- Port camera: `8000`
- Voice channel: `1`
- Queue: `30`
- AAC mono 16 kHz / 32 kbps
- Âm lượng từ `TTS_GAIN_DB`

---

## 4. Điều chỉnh âm lượng

Âm lượng chung cho tất cả camera:

```env
TTS_GAIN_DB=4
```

Có thể thử lần lượt:

```env
TTS_GAIN_DB=4
```

```env
TTS_GAIN_DB=5
```

```env
TTS_GAIN_DB=6
```

Nên bắt đầu từ `4` và tăng dần. Hệ thống có limiter để giảm nguy cơ clipping khi tăng gain.

### Âm lượng riêng từng camera

Ví dụ camera `gate` dùng `7 dB`, còn `yard` dùng mức chung từ `TTS_GAIN_DB`:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE","gain_db":7},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

---

## 5. Override nâng cao riêng từng camera

Khi thật sự cần, JSON hỗ trợ các tham số riêng cho từng camera:

```json
{
  "gate": {
    "ip": "192.168.31.59",
    "user": "admin",
    "password": "PASS",
    "port": 8000,
    "voice_chan": 1,
    "gain_db": 6,
    "queue_size": 40
  }
}
```

Khai báo trong Portainer dưới dạng một dòng:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS","port":8000,"voice_chan":1,"gain_db":6,"queue_size":40}}
```

Có thể override thêm giọng và audio cho một camera nếu cần:

```json
{
  "gate": {
    "ip": "192.168.31.59",
    "user": "admin",
    "password": "PASS",
    "voice": "vi-VN-HoaiMyNeural",
    "rate": "+0%",
    "edge_volume": "+0%",
    "gain_db": 6,
    "sample_rate": 16000,
    "bitrate": "32k"
  }
}
```

Nếu không khai báo các giá trị override, camera tự sử dụng cấu hình chung.

---

## 6. Kiểm tra API

Health check:

```bash
curl http://192.168.31.100:8124/health
```

Phát TTS ra camera `gate`:

```bash
curl -X POST http://192.168.31.100:8124/say \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"camera":"gate","text":"Có người đang đứng trước cổng"}'
```

Phát ra nhiều camera:

```json
{
  "camera": ["gate", "yard"],
  "text": "Thông báo thử nghiệm"
}
```

Phát ra tất cả camera:

```json
{
  "camera": "all",
  "text": "Thông báo toàn bộ camera"
}
```

---

## 7. Home Assistant

### Bước 1 - thêm API key vào `secrets.yaml`

```yaml
camera_tts_api_key: "CHANGE_THIS_TO_A_LONG_RANDOM_KEY"
```

### Bước 2 - thêm REST command

Nếu dùng file riêng, thêm vào `configuration.yaml`:

```yaml
rest_command: !include rest_command.yaml
```

Tạo file `rest_command.yaml`:

```yaml
camera_tts:
  url: "http://192.168.31.100:8124/say"
  method: POST
  headers:
    X-API-Key: !secret camera_tts_api_key
  content_type: "application/json"
  timeout: 5
  payload: >
    {
      "camera": {{ camera | to_json }},
      "text": {{ message | to_json }}
    }
```

Thay `192.168.31.100` bằng IP máy chạy Docker/Portainer.

Khởi động lại Home Assistant sau khi thêm cấu hình.

### Bước 3 - gọi từ Automation / Script

```yaml
action: rest_command.camera_tts
data:
  camera: gate
  message: "Có người đang đứng trước cổng"
```

Camera khác:

```yaml
action: rest_command.camera_tts
data:
  camera: yard
  message: "Có chuyển động ngoài sân"
```

Phát ra tất cả camera:

```yaml
action: rest_command.camera_tts
data:
  camera: all
  message: "Đây là thông báo toàn bộ camera"
```

### Điều chỉnh gain ngay từ Home Assistant

Nếu muốn truyền âm lượng riêng theo từng lần gọi, thêm REST command thứ hai:

```yaml
camera_tts_gain:
  url: "http://192.168.31.100:8124/say"
  method: POST
  headers:
    X-API-Key: !secret camera_tts_api_key
  content_type: "application/json"
  timeout: 5
  payload: >
    {
      "camera": {{ camera | to_json }},
      "text": {{ message | to_json }},
      "gain_db": {{ gain_db | float }}
    }
```

Gọi:

```yaml
action: rest_command.camera_tts_gain
data:
  camera: gate
  message: "Cảnh báo có người trước cổng"
  gain_db: 6
```

---

## Cấu hình khuyến nghị

```env
PORT=8124
API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

Sau khi sửa camera, âm lượng hoặc API key trong Portainer, chỉ cần bấm **Update the stack**.
