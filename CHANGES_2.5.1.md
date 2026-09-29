# Camera TTS Multi-Vendor Docker v2.5.1

- Sửa PTZ EZVIZ/Hikvision: `auto` nay dùng HCNetSDK local thay vì giả định firmware có ISAPI.
- Thêm `ptz_hcnetsdk` native worker, lazy-start ở lần PTZ đầu tiên và giữ login để các lệnh sau nhanh hơn.
- Giữ `isapi` làm fallback thủ công, giữ Dahua/Imou qua local Dahua CGI.
- Giữ nguyên `PRECACHE_TEXTS_JSON` trong `stack.yml` và `portainer-stack.yml` dưới dạng tùy chọn comment.
- Không probe PTZ trong startup; không thêm network I/O vào Home Assistant startup.
- Bổ sung regression tests cho mapping vendor/PTZ.
