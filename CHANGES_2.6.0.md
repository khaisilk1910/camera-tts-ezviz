# Camera TTS Multi-Vendor Docker v2.6.0

## Thay đổi chính

- Media URL mặc định phát bằng ffmpeg streaming thay vì tải/chuyển mã toàn bộ file trước khi phát.
- Stop có thể hủy ngay job đang `preparing/buffering`, terminate ffmpeg nguồn và đóng sender khi VoiceTalk đã mở.
- Thêm `volume_level` 0.0–1.0 theo từng camera, persist trong `/cache/runtime-settings.json`.
- EZVIZ/Hikvision ưu tiên volume phần cứng HCNetSDK `NET_DVR_GET/SET_AUDIOOUT_VOLUME`; firmware không hỗ trợ tự fallback software DSP.
- Software-volume của media streaming được đọc theo từng PCM chunk nên đổi slider có hiệu lực trong bài đang phát.
- C++ worker hỗ trợ `GET_VOLUME`, `SET_VOLUME`, `--get-volume`, `--set-volume`.
- `/cameras` công bố `media_volume`, `streaming_media`, `instant_stop` và `volume_backend`.
- Giữ chế độ cũ qua `MEDIA_STREAMING=false`; `MEDIA_PREP_TIMEOUT` chỉ còn cần cho chế độ đó.

## Giới hạn audio recording

HCNetSDK VoiceTalk có thể làm một số firmware camera ngắt/mute mic của recording nội bộ. v2.6.0 giảm thời gian talk channel bị giữ và giải phóng ngay khi Stop, nhưng không giả định mọi model có thể vừa VoiceTalk vừa ghi ambient mic. Audio mixing/broadcast phải được xử lý theo capability thật của model/firmware.

## Kiểm thử

- `python3 -m unittest discover -s tests -v`: 37/37 pass.
- `python3 -m py_compile app/*.py`: pass.
- `send_aac.cpp`: compile bằng HCNetSDK headers/libs đi kèm với `g++ -std=c++17 -O2 -Wall -Wextra`.
