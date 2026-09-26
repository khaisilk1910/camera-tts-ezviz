# Portainer compact variables

Use `stack.yml` and create only these variables in Portainer:

```env
PORT=8124
API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_KEY
DEFAULT_CAMERA=gate
TTS_GAIN_DB=4
TTS_VOICE=vi-VN-HoaiMyNeural
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"CHANGE_ME"}}
```

For two cameras:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

To remove a camera, remove its object from `CAMERAS_JSON` and redeploy the stack.
To add one, add another named object and redeploy.

Per-camera optional overrides supported by the existing application include:
`port`, `voice_chan`, `queue_size`, `gain_db`, `voice`, `rate`, `edge_volume`, `sample_rate`, and `bitrate`.
