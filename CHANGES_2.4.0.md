# Camera TTS EZVIZ Docker 2.4.0 — reviewed

## Thay đổi chính

- Hàng đợi mới hỗ trợ đúng bốn chế độ `add`, `next`, `play`, `replace` thay vì chỉ nối cuối/xóa toàn bộ.
- Stop/queue/enqueue được serialize bằng lock **theo từng camera**, loại bỏ race giữa các lệnh `PLAY/REPLACE` đồng thời nhưng không khóa camera khác.
- Thêm `VOICE_REOPEN_GUARD_MS` (mặc định 1250 ms) để tránh mở VoiceTalk quá sớm sau khi vừa đóng hoặc cưỡng bức dừng HCNetSDK.
- Trạng thái sender có thêm `connected` để phân biệt process HCNetSDK còn sống với phiên camera đã đăng nhập thành công.
- `GET /cameras` trả thêm `version` và `features` để HACS tự nhận biết capability của backend.
- Thêm `PATCH /cameras/<camera_id>/settings` cho `gain_db`; giá trị được lưu nguyên tử vào `/cache/runtime-settings.json` và tồn tại qua restart container.
- Có thể reset gain về giá trị Stack bằng `{"gain_db":"default"}` hoặc `{"gain_db":null}`.
- Giữ nguyên two-level cache, single-flight, persistent HCNetSDK worker, media conversion và API cũ.

## Tính năng từ dự án Assist Camera/Imou được áp dụng theo kiến trúc này

- Ý tưởng điều khiển gain từ entity Home Assistant được chuyển thành **speaker output gain** vì dự án này không lấy microphone vào HA.
- Cách xử lý HCNetSDK phát liên tiếp được bổ sung khoảng bảo vệ reopen ở cả C++ worker và lớp quản lý process Python.
- Queue được chuẩn hóa theo đúng `ADD/NEXT/PLAY/REPLACE`; announce chỉ là fallback ngắt-phát, không được HACS quảng bá như capability đầy đủ vì chưa resume được vị trí cũ.

## Không đưa vào Docker/HACS này

- Assist Satellite, wake word, VAD, microphone RTSP, go2rtc intercom và server RTSP hai chiều.
- Lý do: các phần đó cần luồng audio vào, ffmpeg/process dài hạn và lifecycle riêng trong Home Assistant; đưa vào integration TTS hiện tại sẽ tăng đáng kể độ phức tạp, tải và rủi ro startup/event loop.

## QA

- 19/19 unit/regression tests pass.
- Python compile pass.
- `send_aac.cpp` compile pass với HCNetSDK bundled (`-Wall -Wextra`).
- Stack YAML parse pass.
