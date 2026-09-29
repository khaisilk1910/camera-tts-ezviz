# Camera TTS Multi-Vendor v2.5.0

## Mới

- Thêm `vendors`/`vendor` cho từng camera: `ezviz`, `imou`, `dahua`; alias `hikvision` → `ezviz`.
- Thêm adapter local Imou/Dahua từ dự án tham chiếu `imou-homeassistant`: visualtalk 8086/AAC 16 kHz, fallback Dahua TCP 37777/PCM 8 kHz.
- TTS/media preparation tự chọn định dạng theo vendor; vẫn dùng chung queue/cache/single-flight.
- Thêm PTZ API on-demand: Hikvision ISAPI và Dahua CGI.
- Thêm `/audio/<camera>` để Home Assistant gửi WAV Assist/TTS trực tiếp xuống Docker.
- Thêm intercom server riêng (mặc định 8125) và `/cameras/<camera>/intercom-source` cho go2rtc.
- Intercom có A-law decoder, speech gate, pre-roll 0,3 s và tự đóng talk session sau ~1,5 s im lặng.
- HCNetSDK worker thêm stream protocol (`STREAM_BEGIN`, `FRAME`, `STREAM_END`) để nhận intercom live cho EZVIZ.
- `/cameras` trả vendor, capabilities, mic/PTZ metadata và transport state.

## Hiệu năng / độ ổn định

- Không thêm PTZ probe trong startup.
- Imou/Dahua không giữ session camera khi idle.
- Intercom chạy HTTP server riêng nên long-lived POST không chiếm Waitress API threads.
- EZVIZ giữ persistent worker như v2.4.0 để không tăng latency TTS.
- Các camera vẫn có lock/queue độc lập; một camera chậm không serialize camera khác.

## Kiểm thử

- 31/31 Python tests pass.
- `send_aac.cpp` compile sạch với `-Wall -Wextra` trên HCNetSDK bundle hiện tại.

- Hardened intercom host validation before generating `go2rtc exec:` commands.
