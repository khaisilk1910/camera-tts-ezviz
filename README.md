# Camera TTS EZVIZ

Docker service phát TTS tiếng Việt ra loa camera EZVIZ/Hikvision qua HCNetSDK, tối ưu để gọi nhanh từ Home Assistant và hỗ trợ nhiều camera.

## Tính năng

- HCNetSDK được tích hợp sẵn trong Docker image.
- Gọi TTS qua REST API, phù hợp Home Assistant.
- Hỗ trợ nhiều camera bằng `CAMERAS_JSON`.
- Mỗi camera có queue riêng: các câu cùng camera phát tuần tự, không chồng tiếng.
- Các camera khác nhau có thể phát đồng thời.
- API `/say` trả `202` ngay, không chờ phát xong.
- Giữ HCNetSDK worker/session theo từng camera để giảm thời gian khởi động mỗi lần phát.
- Cache 2 tầng: TTS gốc MP3 + AAC cuối cùng gửi camera.
- Câu đã có AAC cache sẽ bỏ qua Edge TTS và FFmpeg.
- Single-flight: nhiều request cùng một nội dung/audio chỉ tạo file một lần.
- Chuẩn bị TTS song song bằng worker pool nhưng vẫn giữ đúng thứ tự phát của từng camera.
- Có thể pre-cache các câu thường dùng khi container khởi động.
- Có API kiểm tra cache, camera, trạng thái job và thời gian từng công đoạn.
- Điều chỉnh âm lượng chung, riêng từng camera hoặc theo từng request.

---

## 1. Stack Portainer

Vào **Portainer → Stacks → Add stack → Web editor** và dùng:

```yaml
services:
  camera-tts:
    image: "ghcr.io/khaisilk1910/camera-tts-ezviz:latest"
    hostname: "camera-tts-ezviz"

    network_mode: host

    environment:
      # ===== Các biến thường chỉnh trong Portainer =====
      PORT: "${PORT:-8124}"
      API_KEY: "${API_KEY:-change-me-now}"
      DEFAULT_CAMERA: "${DEFAULT_CAMERA:-}"
      TTS_GAIN_DB: "${TTS_GAIN_DB:-4}"
      TTS_VOICE: "${TTS_VOICE:-vi-VN-HoaiMyNeural}"
      CAMERAS_JSON: "${CAMERAS_JSON:-}"

      # ===== Cấu hình chung tối ưu sẵn =====
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

      # Giảm độ trễ VoiceTalk nhưng vẫn giữ khoảng đệm an toàn.
      VOICE_START_DELAY_MS: "120"
      VOICE_END_DELAY_MS: "80"
      SENDER_START_TIMEOUT: "8"

      ALLOW_REQUEST_OVERRIDES: "true"
      ALLOW_DUPLICATE_CAMERA_TARGETS: "false"

      # Tùy chọn: pre-cache các câu thường dùng khi container khởi động.
      # PRECACHE_TEXTS_JSON: '["Có người trước cổng","Có khách đến","Vui lòng đóng cửa"]'

    restart: unless-stopped

    volumes:
      - camera_tts_cache:/cache

    stop_grace_period: 15s

volumes:
  camera_tts_cache:
    name: "camera-tts-ezviz-cache"
```

> Stack dùng `network_mode: host`, vì vậy **không cần khai báo `ports:`**. API sẽ lắng nghe trực tiếp trên `PORT` của máy Docker.

---

## 2. Environment variables

Cấu hình tối thiểu trong Portainer:

```env
PORT=8124
API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"CHANGE_ME"}}
```

Ý nghĩa:

- `PORT`: cổng API Camera TTS.
- `API_KEY`: khóa bảo vệ API.
- `DEFAULT_CAMERA`: camera dùng khi request không truyền `camera`.
- `TTS_GAIN_DB`: gain mặc định cho tất cả camera.
- `TTS_VOICE`: giọng Edge TTS mặc định.
- `CAMERAS_JSON`: danh sách camera.

Sau khi thay đổi Environment, bấm **Update the stack**.

Nếu muốn dùng port `8125`:

```env
PORT=8125
```

Sau đó Home Assistant gọi `http://IP_DOCKER:8125/say`.

---

## 3. Khai báo camera

### Một camera

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"}}
```

### Nhiều camera

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"},"kitchen":{"ip":"192.168.31.61","user":"admin","password":"PASS_KITCHEN"}}
```

Mỗi camera mặc định dùng:

- Port HCNetSDK: `8000`
- Voice channel: `1`
- Queue: `30`
- AAC-LC mono `16 kHz / 32 kbps`
- Gain từ `TTS_GAIN_DB`

---

## 4. Điều chỉnh âm lượng

Âm lượng chung:

```env
TTS_GAIN_DB=4
```

Có thể thử lần lượt `4`, `5`, `6` dB. Nên tăng từng bước để tránh méo tiếng. Pipeline dùng limiter sau gain để hạn chế clipping.

### Âm lượng riêng từng camera

Ví dụ `gate` dùng `7 dB`, `yard` dùng mức chung từ `TTS_GAIN_DB`:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE","gain_db":7},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

### Âm lượng riêng theo từng request

Khi `ALLOW_REQUEST_OVERRIDES=true`, API có thể nhận:

```json
{
  "camera": "gate",
  "text": "Cảnh báo có người trước cổng",
  "gain_db": 6
}
```

---

## 5. Override nâng cao riêng từng camera

Khi cần, từng camera có thể ghi đè cấu hình chung:

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

Có thể override thêm giọng và audio:

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

Nếu không khai báo override, camera tự dùng cấu hình chung.

---

## 6. Tối ưu tốc độ và cache

### Persistent HCNetSDK worker

Mỗi camera có một native worker HCNetSDK riêng. Worker được warm-start khi container chạy và giữ phiên làm việc để tránh phải khởi tạo/login lại toàn bộ cho mỗi câu TTS.

Nếu worker hoặc session lỗi, hệ thống có cơ chế khởi động/reconnect lại khi phát.

### Cache 2 tầng

Cache được lưu trong volume `camera_tts_cache`:

```text
/cache/
├── base/   # MP3 gốc từ Edge TTS
└── aac/    # AAC cuối cùng gửi camera
```

Luồng khi chưa có cache:

```text
Text → Edge TTS → MP3 cache → FFmpeg → AAC cache → Camera
```

Luồng khi AAC đã có cache:

```text
Text → AAC cache HIT → Camera
```

Nếu chỉ thay `gain_db`, `sample_rate` hoặc `bitrate`, hệ thống có thể dùng MP3 base cache và chỉ tạo lại AAC, không phải gọi Edge TTS lại.

### Single-flight

Nếu nhiều request cùng lúc cần cùng một file audio, chỉ một task tạo audio; các request còn lại dùng chung kết quả đó.

### Chuẩn bị TTS song song

`PREP_WORKERS=4` cho phép chuẩn bị nhiều audio song song. Việc phát trên cùng một camera vẫn tuân theo queue FIFO nên không bị chồng tiếng.

### Chuẩn hóa text

Khoảng trắng đầu/cuối và khoảng trắng lặp được chuẩn hóa trước khi tạo cache key, giúp các nội dung tương đương tái sử dụng cache tốt hơn.

### Pre-cache câu thường dùng

Bỏ dấu `#` trong Stack và khai báo:

```yaml
PRECACHE_TEXTS_JSON: '["Có người trước cổng","Có khách đến","Vui lòng đóng cửa"]'
```

Container sẽ chuẩn bị các câu này sau khi khởi động. Khi cần phát, nếu audio đã sẵn sàng trong cache thì không cần chờ Edge TTS/FFmpeg.

### Dọn cache tự động

Mặc định:

```yaml
CACHE_MAX_MB: "512"
CACHE_TTL_DAYS: "30"
```

Hệ thống tự dọn cache cũ và giới hạn dung lượng cache.

---

## 7. Kiểm tra API và hiệu năng

Thay `192.168.31.100` bằng IP máy Docker/Portainer.

### Health

```bash
curl http://192.168.31.100:8124/health
```

Health trả trạng thái camera, queue, persistent sender và thông tin cache.

### Danh sách camera

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  http://192.168.31.100:8124/cameras
```

### Kiểm tra cache

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  http://192.168.31.100:8124/cache/stats
```

Các số quan trọng:

- `aac_hits`: dùng trực tiếp AAC cache.
- `aac_misses`: chưa có AAC phù hợp.
- `base_hits`: tái sử dụng MP3 gốc.
- `singleflight_joins`: request đã dùng chung một tác vụ chuẩn bị audio.
- `generated`: số file AAC đã tạo.

### Phát TTS

```bash
curl -X POST http://192.168.31.100:8124/say \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"camera":"gate","text":"Xin chào các bạn"}'
```

API trả `202` cùng `job id` ngay sau khi đưa request vào queue.

Nếu bỏ `camera`:

```bash
curl -X POST http://192.168.31.100:8124/say \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"text":"Xin chào các bạn"}'
```

container dùng `DEFAULT_CAMERA`.

### Nhiều camera

```json
{
  "camera": ["gate", "yard"],
  "text": "Thông báo thử nghiệm"
}
```

### Tất cả camera

```json
{
  "camera": "all",
  "text": "Thông báo toàn bộ camera"
}
```

### Kiểm tra thời gian của một job

Sau khi `/say` trả về `id`, gọi:

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  http://192.168.31.100:8124/jobs/JOB_ID
```

Kết quả có thể chứa:

- `cache`: `aac-hit`, `generated`, ...
- `prepare_ms`: thời gian chuẩn bị audio.
- `playback.sdk_ms`: thời gian native HCNetSDK xử lý phát.
- `timings.audio_ready_ms`: thời điểm audio sẵn sàng.
- `timings.play_start_ms`: thời điểm bắt đầu phát.
- `timings.done_ms`: thời điểm hoàn tất.

Dùng endpoint này để so sánh **lần đầu** và **lần thứ hai cùng một câu** và xác định phần còn gây độ trễ.

---

## 8. Home Assistant

### 8.1. Thêm API key

Trong `secrets.yaml`:

```yaml
camera_tts_api_key: "CHANGE_THIS_TO_A_LONG_RANDOM_KEY"
```

Giá trị phải giống `API_KEY` trong Portainer.

### 8.2. REST command

Nếu dùng file riêng, thêm vào `configuration.yaml`:

```yaml
rest_command: !include rest_command.yaml
```

Tạo `rest_command.yaml`:

```yaml
camera_ezviz_tts:
  url: "http://192.168.31.100:8124/say"
  method: POST
  headers:
    X-API-Key: !secret camera_tts_api_key
    Content-Type: application/json
  content_type: "application/json"
  timeout: 5
  payload: >-
    {
      {% if camera | default('') | trim != '' %}
      "camera": {{ camera | trim | to_json }},
      {% endif %}
      "text": {{ message | to_json }}
    }
```

Nếu `camera` để trống, REST command không gửi trường `camera`; container tự dùng `DEFAULT_CAMERA`.

Sau khi thêm cấu hình, restart Home Assistant hoặc reload cấu hình phù hợp.

### 8.3. Script có dropdown Camera + ô Tin nhắn

Thêm vào `scripts.yaml`:

```yaml
camera_ezviz_tts:
  alias: Camera EZVIZ TTS
  description: Phát TTS ra loa camera EZVIZ

  fields:
    camera:
      name: Camera
      description: Chọn camera; Mặc định sẽ dùng DEFAULT_CAMERA của container
      required: false
      default: "Mặc định"
      selector:
        select:
          options:
            - "Mặc định"
            - "gate"
            - "yard"
            - "all"
          custom_value: true

    message:
      name: Tin nhắn
      description: Nội dung cần phát ra loa camera
      required: true
      selector:
        text:
          multiline: true

  sequence:
    - action: rest_command.camera_ezviz_tts
      data:
        camera: >-
          {% if camera | default('Mặc định') == 'Mặc định' %}
            {{ '' }}
          {% else %}
            {{ camera }}
          {% endif %}
        message: "{{ message }}"

  mode: queued
  max: 30
```

Sửa các lựa chọn `gate`, `yard` theo ID camera trong `CAMERAS_JSON`. `custom_value: true` vẫn cho phép nhập ID camera khác.

Sau đó dùng trong UI hoặc Automation:

```yaml
action: script.camera_ezviz_tts
data:
  camera: gate
  message: "Có người đang đứng trước cổng"
```

Dùng camera mặc định:

```yaml
action: script.camera_ezviz_tts
data:
  camera: "Mặc định"
  message: "Xin chào các bạn"
```

Phát tất cả camera:

```yaml
action: script.camera_ezviz_tts
data:
  camera: all
  message: "Đây là thông báo toàn bộ camera"
```

---

## 9. Cấu hình khuyến nghị

```env
PORT=8124
API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

Nếu có các câu cố định thường xuyên sử dụng, bật thêm pre-cache trong Stack:

```yaml
PRECACHE_TEXTS_JSON: '["Có người trước cổng","Có khách đến","Vui lòng đóng cửa"]'
```

Sau khi thay camera, API key, âm lượng hoặc pre-cache trong Portainer, bấm **Update the stack**.
