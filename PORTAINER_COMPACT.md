# Portainer Compact

Các biến thường cần chỉnh:

```env
PORT=8124
API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"CHANGE_ME"}}
```

Stack dùng `network_mode: host`. Thêm/bớt camera bằng `CAMERAS_JSON`, sau đó bấm **Update the stack**.
