# Camera TTS EZVIZ

Docker service phát TTS tiếng Việt ra loa camera EZVIZ/Hikvision qua HCNetSDK.

## Tính năng

- HCNetSDK được tích hợp sẵn trong Docker image.
- Phát TTS qua API, phù hợp với Home Assistant.
- Hỗ trợ nhiều camera.
- Mỗi camera có queue riêng, các câu TTS cùng camera sẽ chờ nhau và không phát chồng tiếng.
- Các camera khác nhau có thể phát đồng thời.
- API `/say` trả phản hồi nhanh, không chờ phát TTS xong.
- Giữ phiên đăng nhập HCNetSDK theo từng camera để giảm thời gian khởi động mỗi lần phát.
- Cache 2 tầng: TTS gốc + AAC cuối cùng; câu lặp lại bỏ qua Edge TTS/FFmpeg khi có thể.
- Single-flight: nhiều request cùng câu chỉ tạo audio một lần.
- Chuẩn bị TTS song song nhưng vẫn phát tuần tự theo queue từng camera.
- Điều chỉnh âm lượng chung hoặc riêng cho từng camera.
- Cấu hình camera bằng `CAMERAS_JSON`, không cần sửa file trong container.

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
      # ===== Các biến cần chỉnh trong Portainer =====
      PORT: "${PORT:-8124}"
      API_KEY: "${API_KEY:-change-me-now}"
      DEFAULT_CAMERA: "${DEFAULT_CAMERA:-}"
      TTS_GAIN_DB: "${TTS_GAIN_DB:-4}"
      TTS_VOICE: "${TTS_VOICE:-vi-VN-HoaiMyNeural}"
      CAMERAS_JSON: "${CAMERAS_JSON:-}"

      # ===== Cấu hình mặc định =====
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

      VOICE_START_DELAY_MS: "120"
      VOICE_END_DELAY_MS: "80"
      SENDER_START_TIMEOUT: "8"

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


### Pre-cache câu thường dùng (tùy chọn)

Nếu có các câu cố định thường xuyên phát, có thể thêm vào Stack:

```yaml
environment:
  PRECACHE_TEXTS_JSON: '["Có người trước cổng","Có khách đến","Vui lòng đóng cửa"]'
```

Container sẽ tạo cache sau khi khởi động. Khi câu đã có trong cache, request tiếp theo bỏ qua bước gọi Edge TTS và chuyển đổi không cần thiết.

Kiểm tra cache:

```bash
curl -H "X-API-Key: YOUR_API_KEY" http://192.168.31.100:8124/cache/stats
```

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

### 1. Tạo `rest_command`

Thêm API key vào `secrets.yaml`:

```yaml
camera_tts_api_key: "CHANGE_THIS_TO_A_LONG_RANDOM_KEY"
```

Nếu dùng file riêng, thêm vào `configuration.yaml`:

```yaml
rest_command: !include rest_command.yaml
```

Tạo hoặc thêm vào file `rest_command.yaml`:

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

Thay `192.168.31.100` bằng IP máy chạy Docker/Portainer.

Nếu `camera` để trống, request chỉ gửi nội dung `text` và container tự sử dụng `DEFAULT_CAMERA`.

Ví dụ gọi trực tiếp:

```yaml
action: rest_command.camera_ezviz_tts
data:
  camera: gate
  message: "Có người đang đứng trước cổng"
```

Dùng camera mặc định của container:

```yaml
action: rest_command.camera_ezviz_tts
data:
  camera: ""
  message: "Có người đang đứng trước cổng"
```

### 2. Tạo Script có giao diện nhập Camera + Tin nhắn

Để Home Assistant hiện trực tiếp ô nhập **Camera** và **Tin nhắn** trong giao diện Actions, tạo script sau trong `scripts.yaml`:

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

Nếu `configuration.yaml` chưa khai báo file script riêng, thêm:

```yaml
script: !include scripts.yaml
```

Sau đó **Reload Scripts** hoặc khởi động lại Home Assistant.

Trong **Developer Tools → Actions**, chọn:

```text
script.camera_ezviz_tts
```

Home Assistant sẽ hiển thị 2 ô:

- **Camera**: dropdown `Mặc định`, `gate`, `yard`, `all`; vẫn cho phép nhập giá trị tùy chỉnh. Chọn `Mặc định` để dùng `DEFAULT_CAMERA` của container.
- **Tin nhắn**: nội dung TTS cần phát.

Ví dụ dùng trong Automation:

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
  message: "Có người đang đứng trước cổng"
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
