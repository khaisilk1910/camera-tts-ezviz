# Camera TTS Multi‑Vendor v2.5.0

Backend Docker + custom integration Home Assistant để phát TTS/audio ra loa camera, điều khiển PTZ, dùng microphone camera cho Assist và tạo backchannel đàm thoại hai chiều.

Hỗ trợ:

- **EZVIZ / Hikvision**: phát loa local qua HCNetSDK; PTZ qua ISAPI khi camera hỗ trợ.
- **Imou**: phát loa local bằng adapter Imou/Dahua; ưu tiên local talk 8086 và tự fallback 37777 khi cần; PTZ qua Dahua-compatible CGI khi firmware hỗ trợ.
- **Dahua**: phát loa local bằng adapter Dahua; PTZ qua Dahua CGI khi firmware hỗ trợ.
- **Home Assistant**: mỗi camera là một `media_player`; có PTZ, Assist satellite, mic gain, VAD, mute mic và intercom khi camera khai báo capability tương ứng.

> Dự án ưu tiên xử lý trong LAN. Riêng `/say` của Docker dùng **Edge TTS**, vì vậy cần Internet để tổng hợp giọng. Nếu muốn TTS local end-to-end, dùng Piper/Assist của Home Assistant rồi gửi audio sang Docker.

---

## 1. Yêu cầu

- Máy Docker chạy **Linux x86-64 / amd64**.
- Docker Engine hoặc Portainer chạy trên Linux.
- Docker host và camera truy cập được lẫn nhau trong LAN.
- Home Assistant **2026.9.0 trở lên** cho integration v2.5.0.
- Camera phải bật các chức năng cần dùng: local/LAN access, RTSP, PTZ, microphone/two-way audio tùy model.
- Khuyến nghị đặt IP tĩnh/DHCP reservation cho Docker host và từng camera.

Image Docker:

```text
ghcr.io/khaisilk1910/camera-tts-ezviz:latest
```

Hai cổng mặc định của backend:

| Cổng | Chức năng |
|---|---|
| `8124/tcp` | API Docker cho Home Assistant/TTS/media/PTZ |
| `8125/tcp` | intercom backchannel từ go2rtc |

Không public hai cổng này ra Internet. Chỉ cho phép LAN/Home Assistant/go2rtc truy cập.

---

## 2. Tạo API key

Không dùng giá trị mặc định `change-me-now`.

Trên Linux có thể tạo key bằng:

```bash
openssl rand -hex 32
```

Ví dụ:

```text
5d85b8d9c9f4d1d0c60e87f4c880................................
```

Dùng cùng một `API_KEY` ở Docker và khi thêm integration vào Home Assistant.

---

## 3. Cấu hình camera bằng `CAMERAS_JSON`

Mỗi camera là một object; tên object là ID camera.

### EZVIZ / Hikvision

TTS cơ bản:

```json
{
  "gate": {
    "vendors": "ezviz",
    "ip": "192.168.31.59",
    "user": "admin",
    "password": "PASSWORD",
    "port": 8000
  }
}
```

Có PTZ và microphone cho Assist:

```json
{
  "gate": {
    "vendors": "ezviz",
    "ip": "192.168.31.59",
    "user": "admin",
    "password": "PASSWORD",
    "port": 8000,
    "ptz": true,
    "ptz_protocol": "isapi",
    "ptz_port": 80,
    "ptz_channel": 1,
    "ptz_speed": 50,
    "mic_url": "rtsp://admin:PASSWORD@192.168.31.59:554/Streaming/Channels/102"
  }
}
```

### Imou

```json
{
  "yard": {
    "vendors": "imou",
    "ip": "192.168.31.60",
    "user": "admin",
    "password": "PASSWORD",
    "ptz": true,
    "mic_url": "rtsp://admin:PASSWORD@192.168.31.60:554/cam/realmonitor?channel=1&subtype=1"
  }
}
```

### Dahua

```json
{
  "garage": {
    "vendors": "dahua",
    "ip": "192.168.31.61",
    "user": "admin",
    "password": "PASSWORD",
    "ptz": true,
    "ptz_protocol": "dahua",
    "ptz_port": 80,
    "ptz_channel": 0
  }
}
```

### Nhiều hãng cùng lúc

Dùng chuỗi JSON một dòng khi nhập vào Portainer:

```text
{"gate":{"vendors":"ezviz","ip":"192.168.31.59","user":"admin","password":"PASS1","ptz":true},"yard":{"vendors":"imou","ip":"192.168.31.60","user":"admin","password":"PASS2","ptz":true},"garage":{"vendors":"dahua","ip":"192.168.31.61","user":"admin","password":"PASS3","ptz":true}}
```

### Tham số camera

| Tham số | Mặc định | Ý nghĩa |
|---|---:|---|
| `vendors` / `vendor` | `ezviz` | `ezviz`, `imou`, `dahua`; `hikvision` được map về `ezviz` |
| `ip` | bắt buộc | IP/hostname camera |
| `user` / `username` | `admin` | tài khoản camera |
| `password` | bắt buộc | mật khẩu camera |
| `password_file` | trống | đọc mật khẩu từ file secret thay vì JSON |
| `port` | EZVIZ `8000`; Imou/Dahua `37777` | cổng talk chính của adapter |
| `voice_chan` | `1` | voice channel, chủ yếu dùng cho HCNetSDK |
| `voice` | giá trị global | giọng Edge TTS riêng camera |
| `rate` | giá trị global | tốc độ TTS, ví dụ `+0%`, `-5%` |
| `edge_volume` | giá trị global | volume do Edge TTS áp dụng |
| `gain_db` | giá trị global | tăng/giảm mức audio trước khi phát |
| `sample_rate` | thường `16000` | sample rate profile audio |
| `bitrate` | `32k` | bitrate audio |
| `queue_size` | `30` | độ dài hàng đợi riêng camera |
| `ptz` | `false` | bật PTZ cho camera |
| `ptz_protocol` | `auto` | `auto`, `isapi`, `dahua`, `none` |
| `ptz_port` | `80` | HTTP port cho PTZ |
| `ptz_channel` | ISAPI `1`, Dahua `0` | channel PTZ |
| `ptz_speed` | `50` | tốc độ PTZ 1–100 |
| `mic_url` | trống | RTSP/HTTP(S) audio source cho Assist |
| `intercom` | `true` | bật/tắt backchannel hai chiều |
| `intercom_key` | tự sinh | token riêng camera; thường để trống |

> URL RTSP phụ thuộc model/firmware. Ví dụ trong README chỉ là mẫu phổ biến, không phải mọi camera đều dùng đúng đường dẫn đó.

---

## 4. Cài bằng Docker container

Tạo volume cache:

```bash
docker volume create camera-tts-ezviz-cache
```

Chạy container:

```bash
docker run -d \
  --name camera-tts-ezviz \
  --network host \
  --restart unless-stopped \
  -e TZ="Asia/Ho_Chi_Minh" \
  -e PORT="8124" \
  -e INTERCOM_PORT="8125" \
  -e API_KEY="YOUR_LONG_RANDOM_API_KEY" \
  -e DEFAULT_CAMERA="gate" \
  -e TTS_GAIN_DB="4" \
  -e TTS_VOICE="vi-VN-HoaiMyNeural" \
  -e CAMERAS_JSON='{"gate":{"vendors":"ezviz","ip":"192.168.31.59","user":"admin","password":"PASSWORD"}}' \
  -v camera-tts-ezviz-cache:/cache \
  ghcr.io/khaisilk1910/camera-tts-ezviz:latest
```

Kiểm tra:

```bash
docker ps
```

```bash
docker logs --tail 100 camera-tts-ezviz
```

Health check:

```bash
curl http://127.0.0.1:8124/health
```

Danh sách camera:

```bash
curl -H "X-API-Key: YOUR_LONG_RANDOM_API_KEY" \
  http://127.0.0.1:8124/cameras
```

Nếu `/health` trả `degraded`, xem `config_error` trong response và log container trước khi cấu hình Home Assistant.

---

## 5. Deploy Portainer Stack

Khuyến nghị dùng **Portainer Standalone / Docker Compose stack** trên Linux.

Không dùng nguyên cấu hình này cho Docker Swarm vì `network_mode: host` và chính sách restart của Swarm có cách khai báo khác.

Trong Portainer:

1. `Stacks` → `Add stack`.
2. Đặt tên, ví dụ `camera-tts`.
3. Chọn `Web editor`.
4. Dán YAML bên dưới.
5. Ở phần `Environment variables`, nhập các biến được liệt kê sau YAML.
6. `Deploy the stack`.

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
      SENDER_START_TIMEOUT: "120"

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

Environment variables cần nhập trong Portainer:

```text
PORT=8124
INTERCOM_PORT=8125
API_KEY=YOUR_LONG_RANDOM_API_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={...JSON CAMERA MỘT DÒNG...}
```

Vì dùng `network_mode: host`, **không thêm `ports:`**.

Sau khi thay đổi `CAMERAS_JSON`, redeploy/recreate container để Docker đọc cấu hình mới. Home Assistant sẽ nhận danh sách camera ở lần coordinator refresh tiếp theo; bình thường không cần xóa rồi thêm lại integration.

---

## 6. Các thông số Docker nên giữ mặc định

| Biến | Mặc định | Khuyến nghị |
|---|---:|---|
| `PORT` | `8124` | chỉ đổi nếu trùng cổng |
| `INTERCOM_PORT` | `8125` | chỉ đổi nếu trùng cổng |
| `ALLOW_NO_AUTH` | `false` | luôn giữ `false` |
| `TTS_RATE` | `+0%` | bắt đầu từ `+0%`; chỉ chỉnh nhẹ |
| `TTS_EDGE_VOLUME` | `+0%` | để `+0%`, ưu tiên chỉnh `TTS_GAIN_DB` |
| `TTS_GAIN_DB` | `4` | điểm bắt đầu tốt; giảm nếu rè/vỡ tiếng |
| `TTS_SAMPLE_RATE` | `16000` | nên giữ `16000` |
| `TTS_BITRATE` | `32k` | nên giữ `32k` nếu không có lý do đặc biệt |
| `QUEUE_SIZE` | `30` | đủ cho automation gia đình |
| `MAX_TEXT` | `700` | tránh câu quá dài |
| `PREP_WORKERS` | `4` | giữ `4`; tăng quá cao làm tăng CPU/RAM |
| `HTTP_THREADS` | `8` | giữ `8` |
| `SEND_TIMEOUT` | `180` | chỉ tăng khi camera rất chậm |
| `PREP_TIMEOUT` | `300` | giữ mặc định |
| `TTS_TIMEOUT` | `120` | giữ mặc định |
| `CACHE_MAX_MB` | `512` | 256–1024 MB tùy dung lượng máy |
| `CACHE_TTL_DAYS` | `30` | giảm nếu ổ đĩa nhỏ |
| `VOICE_START_DELAY_MS` | `120` | chỉ chỉnh khi đầu câu bị mất |
| `VOICE_END_DELAY_MS` | `80` | chỉ chỉnh khi cuối câu bị cắt |
| `VOICE_REOPEN_GUARD_MS` | `1250` | tăng khi câu thứ hai liên tiếp không phát |
| `MEDIA_PREP_TIMEOUT` | `900` | dành cho file/URL media dài |
| `MEDIA_SEND_TIMEOUT` | `7200` | cho phép media dài; không cần giảm nếu LAN an toàn |

Không nên tối ưu bằng cách tăng mạnh worker/thread trước khi xác định nút thắt. Với camera LAN, độ trễ thường đến từ handshake/talk channel, codec và firmware nhiều hơn số thread HTTP.

---

## 7. Cài integration Home Assistant bằng HACS

Repository:

```text
https://github.com/khaisilk1910/camera_tts_ezviz_hacs
```

Các bước:

1. Mở **HACS**.
2. Vào `Integrations`.
3. Mở menu `Custom repositories`.
4. Nhập repository ở trên.
5. Chọn category `Integration`.
6. Cài **Camera TTS Multi-Vendor**.
7. Restart Home Assistant.
8. Vào `Settings` → `Devices & services`.
9. Chọn `Add integration`.
10. Tìm **Camera TTS Multi-Vendor**.
11. Nhập Docker API URL và API key.

Ví dụ Docker chạy trên máy `192.168.31.100`:

```text
Base URL: http://192.168.31.100:8124
API key:  YOUR_LONG_RANDOM_API_KEY
```

Chỉ dùng:

```text
http://127.0.0.1:8124
```

khi Home Assistant thực sự nhìn thấy backend trên loopback của chính nó, ví dụ HA Container và backend cùng host network trên cùng Linux host. Nếu Home Assistant nằm trong container bridge, `127.0.0.1` là chính container Home Assistant, không phải Docker host; hãy dùng IP LAN của Docker host.

Domain integration vẫn là:

```text
camera_tts_ezviz
```

Nếu nâng cấp từ bản cũ, thông thường không cần xóa config entry cũ.

---

## 8. Kiểm tra TTS trong Home Assistant

Nếu dùng Piper/local TTS của Home Assistant:

```yaml
action: tts.speak
target:
  entity_id: tts.piper
data:
  media_player_entity_id: media_player.camera_gate
  message: "Có người trước cổng"
```

Đây là đường được khuyến nghị nếu muốn xử lý TTS local:

```text
Piper/Home Assistant -> audio -> Docker -> adapter vendor -> loa camera
```

Nếu gửi text trực tiếp cho backend để backend dùng Edge TTS thì máy Docker phải truy cập được Internet.

---

## 9. PTZ

Camera chỉ tạo control PTZ khi cấu hình:

```json
"ptz": true
```

Các hướng hỗ trợ:

```text
left
right
up
down
up_left
up_right
down_left
down_right
zoom_in
zoom_out
```

Ví dụ action:

```yaml
action: camera_tts_ezviz.ptz
data:
  entity_id: media_player.camera_gate
  direction: left
  speed: 50
  duration: 0.35
```

Khuyến nghị hiệu chỉnh ban đầu:

- `speed`: 30–50.
- `duration`: 0.20–0.40 giây.
- Nếu camera chạy quá xa: giảm `duration` trước, sau đó giảm `speed`.
- Nếu camera gần như không di chuyển: tăng `duration` từng 0.1 giây.
- Không đặt `ptz:true` cho camera không có PTZ.

PTZ Imou/Dahua phụ thuộc model/firmware và giao diện Dahua-compatible. Một số model OEM có thể khóa hoặc thay đổi endpoint PTZ dù xem video vẫn hoạt động.

---

## 10. Microphone camera và Home Assistant Assist

Để dùng mic camera, thêm `mic_url`:

```json
"mic_url": "rtsp://USER:PASSWORD@CAMERA_IP:554/...."
```

Integration sẽ tạo các entity liên quan khi backend báo camera có mic source:

- Assist satellite.
- Assist pipeline selector.
- VAD sensitivity.
- Microphone gain.
- Mute microphone.
- Wake acknowledgement sound.

Đường audio:

```text
Camera RTSP/go2rtc
    -> ffmpeg của Home Assistant
    -> PCM 16 kHz
    -> Assist pipeline
    -> TTS response
    -> Docker
    -> loa camera
```

Khuyến nghị:

- Bắt đầu mic gain ở `0 dB`.
- Chỉ tăng 2–3 dB mỗi lần nếu giọng nói quá nhỏ.
- Gain cao làm tăng tiếng quạt/gió/nhiễu và có thể kích hoạt VAD sai.
- Nếu Assist tự kích hoạt do môi trường ồn, giảm mic gain trước rồi mới chỉnh VAD.
- Dùng substream audio có bitrate vừa phải để giảm CPU và tải mạng.

---

## 11. Đàm thoại hai chiều / intercom qua go2rtc

Docker mở intercom server mặc định ở:

```text
TCP 8125
```

Trong Home Assistant gọi service:

```yaml
action: camera_tts_ezviz.get_intercom_source
data:
  entity_id: media_player.camera_gate
  docker_host: 192.168.31.100
response_variable: intercom
```

Giá trị `intercom.source` trả về là source `exec:` để thêm vào stream camera của go2rtc.

`docker_host` phải là IP/hostname mà tiến trình go2rtc truy cập được đến Docker backend.

Backchannel dùng PCMA 8 kHz và voice gate. Docker chỉ mở talk session khi phát hiện giọng nói, sau đó đóng khi im lặng để hạn chế việc camera giữ talk channel và làm mất chiều nghe.

Nếu không dùng intercom, có thể tắt riêng camera:

```json
"intercom": false
```

---

## 12. Hiệu chỉnh âm lượng TTS

Biến chính:

```text
TTS_GAIN_DB=4
```

Khuyến nghị:

| Hiện tượng | Cách chỉnh |
|---|---|
| tiếng nhỏ | tăng `TTS_GAIN_DB` +1 đến +2 dB mỗi lần |
| tiếng rè/vỡ | giảm `TTS_GAIN_DB` |
| đầu câu bị mất | tăng `VOICE_START_DELAY_MS`, ví dụ 150–250 ms |
| cuối câu bị mất | tăng `VOICE_END_DELAY_MS`, ví dụ 100–200 ms |
| câu thứ hai phát quá nhanh bị lỗi | tăng `VOICE_REOPEN_GUARD_MS`, ví dụ 1500–2000 ms |
| giọng quá nhanh/chậm | chỉnh `TTS_RATE`, ví dụ `-5%` hoặc `+5%` |

Giới hạn code của `gain_db` là khoảng `-20` đến `+12 dB`. Không nên dùng mức gain cao chỉ để bù cho loa camera vốn nhỏ vì dễ clipping và méo tiếng.

---

## 13. Kiểm tra kết nối mạng camera

Từ Docker host nên kiểm tra các cổng cần thiết.

EZVIZ/Hikvision thường cần:

```text
8000/tcp  HCNetSDK
80/tcp    PTZ ISAPI nếu dùng
554/tcp   RTSP nếu dùng mic/stream
```

Imou/Dahua có thể cần:

```text
8086/tcp  local visual talk khi model hỗ trợ
37777/tcp Dahua local protocol/fallback
80/tcp    PTZ CGI nếu dùng
554/tcp   RTSP nếu dùng mic/stream
```

Ví dụ:

```bash
nc -vz 192.168.31.59 8000
nc -vz 192.168.31.60 37777
nc -vz 192.168.31.60 554
```

Không cần mở các cổng camera ra Internet; chỉ cần kết nối LAN giữa Docker host và camera.

---

## 14. Cập nhật Docker

Nếu dùng container thủ công:

```bash
docker pull ghcr.io/khaisilk1910/camera-tts-ezviz:latest
```

Sau đó recreate container với cùng environment/volume.

Nếu dùng Portainer Stack:

1. Mở Stack.
2. `Pull latest image` nếu giao diện có tùy chọn này.
3. `Update the stack` / redeploy.
4. Kiểm tra `/health`, `/cameras` và log.

Khuyến nghị cập nhật **Docker backend và HACS integration cùng phiên bản chính** khi bản mới thay đổi capability/API.

---

## 15. Các rủi ro và cảnh báo quan trọng

### Không public API ra Internet

`8124` có quyền phát audio, điều khiển PTZ và tạo intercom source. `8125` là backchannel microphone. Chỉ cho phép trong LAN/VPN đáng tin cậy.

Giữ:

```text
ALLOW_NO_AUTH=false
```

và sử dụng API key dài, ngẫu nhiên.

### Tài khoản camera

`CAMERAS_JSON` có thể chứa user/password camera. Người có quyền quản trị Docker/Portainer có thể đọc environment của container.

- Không chia sẻ screenshot Stack có password/API key.
- Không commit file `.env` có secret lên Git.
- Nếu triển khai môi trường nhạy cảm, ưu tiên `password_file`/secret file và `API_KEY_FILE` thay vì environment plaintext.
- Nếu camera cho phép tạo tài khoản riêng đủ quyền talk/PTZ/RTSP thì nên dùng tài khoản đó thay vì tài khoản quản trị chính.

### PTZ có chuyển động vật lý

Lệnh PTZ có thể làm camera quay ngoài vùng mong muốn, va giới hạn cơ khí hoặc mất góc giám sát. Hiệu chỉnh speed/duration ở mức thấp trước.

### Firmware và model khác nhau

Không phải mọi EZVIZ/Imou/Dahua đều hỗ trợ cùng talk codec, RTSP path, PTZ endpoint hoặc quyền local API. Firmware mới có thể thay đổi hành vi mà không báo trước.

### Intercom có thể ảnh hưởng chiều nghe

Một số camera chỉ hỗ trợ half-duplex hoặc khóa microphone camera khi talk channel đang mở. Voice gate giúp giảm vấn đề này nhưng không thể biến camera half-duplex thành full-duplex thật sự.

### Edge TTS và quyền riêng tư

Docker `/say` dùng Edge TTS nên nội dung text cần được gửi tới dịch vụ TTS bên ngoài để tổng hợp. Nếu không muốn nội dung rời mạng nội bộ, dùng Piper/local Assist của Home Assistant.

### RTSP chứa thông tin đăng nhập

`mic_url` thường chứa user/password trong URL. Không đăng URL đó lên issue/log công khai. Integration đã hạn chế lộ `mic_url` trong diagnostics, nhưng vẫn cần bảo vệ cấu hình Docker và Home Assistant.

### Kiến trúc CPU

Docker image hiện phụ thuộc HCNetSDK Linux x86-64, vì vậy mục tiêu triển khai là **linux/amd64**. Không coi image hiện tại là ARM64-compatible nếu chưa thay SDK tương ứng.

### Sao lưu trước khi nâng cấp

Trước khi đổi version:

- lưu Stack/environment;
- lưu `CAMERAS_JSON`;
- lưu API key;
- sao lưu Home Assistant;
- ghi lại entity ID đang dùng trong automation.

---

## 16. Bản quyền và giấy phép

### Mã nguồn integration Home Assistant

Phần HACS `camera_tts_ezviz` được phát hành theo **MIT License**:

```text
Copyright (c) 2026 khaisilk1910
```

Khi sao chép/phân phối phần mềm hoặc phần đáng kể của phần mềm, phải giữ lại copyright notice và permission notice của MIT License.

### Thành phần tham khảo Imou/Dahua

Một phần thiết kế local talk/intercom được chuyển thể từ dự án `imou-homeassistant` do người dùng cung cấp:

```text
MIT License
Copyright (c) 2026 TriTue2011
```

Khi phân phối lại phần mã chuyển thể đáng kể, phải giữ attribution và thông báo MIT tương ứng.

### HCNetSDK

HCNetSDK là SDK/binary của bên thứ ba và **không được coi là mã MIT của dự án này**.

Trước khi public repository hoặc public Docker image có nhúng HCNetSDK, người phát hành phải tự kiểm tra điều khoản giấy phép của Hikvision/EZVIZ và quyền redistributing binary SDK. Nếu chưa xác định rõ quyền phân phối, lựa chọn an toàn hơn là giữ repository/image ở chế độ private.

### Nhãn hiệu

EZVIZ, Hikvision, Imou, Dahua và Home Assistant là tên/nhãn hiệu của các chủ sở hữu tương ứng. Dự án này là integration không chính thức và không mặc nhiên thể hiện sự chứng thực, bảo trợ hoặc liên kết thương mại với các hãng đó.

### Miễn trừ bảo hành

Phần mềm được cung cấp theo hiện trạng. Người dùng tự chịu trách nhiệm kiểm tra khả năng tương thích của camera, firmware, mạng, quyền truy cập và điều khoản SDK trước khi sử dụng trong môi trường thực tế hoặc hệ thống an ninh quan trọng.
