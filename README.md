# Camera TTS EZVIZ v2 - Portainer Environment Stack



## v2.1.0 - Portainer compact configuration

The recommended Portainer stack now exposes only six variables: `PORT`, `API_KEY`, `DEFAULT_CAMERA`, `TTS_GAIN_DB`, `TTS_VOICE`, and `CAMERAS_JSON`. All stable audio, queue, timeout, cache and HCNetSDK defaults remain configured in the stack/application and do not clutter Portainer's Environment variables screen.

Recommended camera configuration uses one JSON object:

```env
CAMERAS_JSON={"gate":{"ip":"192.168.31.59","user":"admin","password":"PASS_GATE"},"yard":{"ip":"192.168.31.60","user":"admin","password":"PASS_YARD"}}
```

All cameras inherit port `8000`, voice channel `1`, queue size `30`, AAC `16 kHz / mono / 32 kbps`, and the global `TTS_GAIN_DB`. A camera can override a setting when needed, for example `"gain_db":6` or `"port":8000`. The older `CAMERA_01_*` parser remains supported by the application for backward compatibility, but those variables are no longer predeclared in the recommended Portainer stack.

Docker/Swarm service for sending Vietnamese TTS to EZVIZ/Hikvision-compatible camera speakers through HCNetSDK.

This version is designed for Portainer Stack use. Camera configuration is read from environment variables, so adding/removing cameras, changing the API port, voice, gain, queue size, or passwords does **not** require editing files over SSH.

## Main characteristics

- HCNetSDK is embedded in the Docker image.
- `send_aac` is compiled into the image during GitHub Actions build.
- Edge TTS -> FFmpeg -> AAC-LC/ADTS -> HCNetSDK voice talk.
- Default audio: mono, 16 kHz, AAC-LC, 32 kbps.
- One FIFO worker per camera: messages for the same camera never overlap.
- Different cameras can play concurrently.
- `/say` returns HTTP 202 immediately after the job is accepted, which is suitable for Home Assistant REST calls.
- TTS/AAC cache reduces latency for repeated messages.
- Global volume through `TTS_GAIN_DB` and optional per-camera volume through `CAMERA_XX_GAIN_DB`.
- 16 camera slots are exposed directly in the Portainer environment UI.
- `CAMERAS_JSON` supports an arbitrary number of cameras if more than 16 are needed.
- GitHub Actions automatically validates, builds, and pushes `linux/amd64` images to GHCR on every push to `main`.
- Optional `PORTAINER_WEBHOOK_URL` GitHub secret can trigger a Portainer redeploy after a successful image push.

> The supplied HCNetSDK binary is x86-64 Linux, so the image is intentionally built only for `linux/amd64`.

## 1. Portainer Stack - no SSH camera editing

Open Portainer -> Stacks -> Add stack -> Web editor, then paste `stack.yml` from this repository.

In **Environment variables**, the minimum useful configuration is:

```env
IMAGE=ghcr.io/khaisilk1910/camera-tts-ezviz:latest
PORT=8124
API_KEY=replace-with-a-long-random-key
TZ=Asia/Ho_Chi_Minh
CACHE_VOLUME_NAME=camera-tts-ezviz-cache

TTS_VOICE=vi-VN-HoaiMyNeural
TTS_RATE=+0%
TTS_EDGE_VOLUME=+0%
TTS_GAIN_DB=4
TTS_SAMPLE_RATE=16000
TTS_BITRATE=32k

QUEUE_SIZE=30
PREP_WORKERS=4
HTTP_THREADS=8

DEFAULT_CAMERA=gate

CAMERA_01_ENABLED=true
CAMERA_01_ID=gate
CAMERA_01_IP=192.168.31.59
CAMERA_01_PORT=8000
CAMERA_01_USER=admin
CAMERA_01_PASSWORD=your-camera-password
CAMERA_01_VOICE_CHAN=1
CAMERA_01_GAIN_DB=4
CAMERA_01_QUEUE_SIZE=30
```

Deploy the stack. The service listens directly on the Swarm node through the host network, so `PORT=8124` means the API is available on:

```text
http://DOCKER_HOST_IP:8124
```

### Why there is no `/opt/...` install path

A Swarm container image is managed by Docker's image storage; it is not installed into a normal application directory. To avoid any SSH preparation of host directories, this stack uses a Docker named volume for the TTS cache. Change its name with:

```env
CACHE_VOLUME_NAME=camera-tts-ezviz-cache
```

Portainer/Docker creates the volume. No `mkdir`, `chmod`, or bind-mount preparation is required.

## 2. Add, remove, or disable cameras from Portainer

To add camera 02, add/edit environment variables and then click **Update the stack**:

```env
CAMERA_02_ENABLED=true
CAMERA_02_ID=yard
CAMERA_02_IP=192.168.31.60
CAMERA_02_PORT=8000
CAMERA_02_USER=admin
CAMERA_02_PASSWORD=your-second-password
CAMERA_02_VOICE_CHAN=1
CAMERA_02_GAIN_DB=5
CAMERA_02_QUEUE_SIZE=30
```

To disable it without deleting the other values:

```env
CAMERA_02_ENABLED=false
```

Slots `CAMERA_01_*` through `CAMERA_16_*` are already present in `stack.yml`.

The application rejects duplicate `(IP, port, voice channel)` targets by default. This prevents accidentally defining the same physical camera under two IDs and defeating the per-camera queue. Only set `ALLOW_DUPLICATE_CAMERA_TARGETS=true` if that behavior is intentional.

## 3. More than 16 cameras

Leave the indexed slots disabled and set `CAMERAS_JSON`:

```env
CAMERAS_JSON=[{"id":"gate","ip":"192.168.31.59","port":8000,"user":"admin","password":"pass1","gain_db":4},{"id":"yard","ip":"192.168.31.60","port":8000,"user":"admin","password":"pass2","gain_db":5}]
```

`CAMERAS_JSON` and enabled indexed slots can also be combined, but all camera IDs and physical targets must remain unique unless duplicate-target protection is explicitly disabled.

## 4. Volume / loudness

Global digital gain:

```env
TTS_GAIN_DB=4
```

Recommended starting range for normal speech is approximately `3` to `6` dB. The application accepts `-20` to `+12` dB and applies an FFmpeg limiter after gain to reduce clipping.

Per-camera override:

```env
CAMERA_01_GAIN_DB=6
CAMERA_02_GAIN_DB=3
```

If `CAMERA_XX_GAIN_DB` is blank, that camera inherits `TTS_GAIN_DB`.

Edge TTS synthesis volume can also be changed separately:

```env
TTS_EDGE_VOLUME=+0%
```

Normally keep `TTS_EDGE_VOLUME=+0%` and tune `TTS_GAIN_DB` first.

## 5. Performance and queue settings

Useful variables:

```env
QUEUE_SIZE=30
PREP_WORKERS=4
HTTP_THREADS=8
TTS_TIMEOUT=120
PREP_TIMEOUT=300
SEND_TIMEOUT=180
CACHE_MAX_MB=512
CACHE_TTL_DAYS=30
```

- `QUEUE_SIZE`: maximum pending jobs for each camera.
- `PREP_WORKERS`: number of parallel Edge TTS/FFmpeg preparation workers.
- `HTTP_THREADS`: Waitress API worker threads.
- `CACHE_MAX_MB`: soft cache limit. Old AAC files are pruned in the background.
- `CACHE_TTL_DAYS`: cached AAC age limit; set `0` to disable age-based expiry.

A repeated sentence with identical TTS parameters reuses its cached AAC file.

## 6. API

Health does not require authentication:

```bash
curl http://192.168.31.100:8124/health
```

List configured cameras:

```bash
curl http://192.168.31.100:8124/cameras \
  -H "X-API-Key: YOUR_API_KEY"
```

Speak on one camera:

```bash
curl -X POST http://192.168.31.100:8124/say \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"camera":"gate","text":"Có người đang đứng trước cổng"}'
```

Speak on multiple cameras:

```json
{
  "camera": ["gate", "yard"],
  "text": "Thông báo thử nghiệm"
}
```

Speak on all cameras:

```json
{
  "camera": "all",
  "text": "Thông báo toàn bộ camera"
}
```

Optional per-request gain (enabled by default with `ALLOW_REQUEST_OVERRIDES=true`):

```json
{
  "camera": "gate",
  "text": "Cảnh báo có người trước cổng",
  "gain_db": 6
}
```

The server also accepts `message` as an alias for `text`.

## 7. Home Assistant

Copy the examples in `homeassistant/` or add the equivalent configuration.

Example action:

```yaml
- action: rest_command.camera_tts
  data:
    camera: gate
    message: "Có người đang đứng trước cổng"
```

The API accepts the request and returns `202` before playback completes. Actual playback is then serialized by the camera's queue.

## 8. GitHub Actions / GHCR

The workflow is in:

```text
.github/workflows/docker-publish.yml
```

On every push to `main` it:

1. compiles Python files;
2. runs configuration parser unit tests;
3. validates `stack.yml` with Docker Compose;
4. builds the self-contained `linux/amd64` Docker image;
5. publishes `latest` and an immutable `sha-*` tag to GHCR.

Default image for this repository:

```text
ghcr.io/khaisilk1910/camera-tts-ezviz:latest
```

## 9. Optional automatic Portainer redeploy after Git push

If Portainer gives you a stack/service webhook URL, add it in GitHub repository settings as an Actions secret named:

```text
PORTAINER_WEBHOOK_URL
```

The workflow will POST to it after a successful `main` image build. If the secret is not configured, the workflow simply skips that step.

Do not commit the webhook URL into the repository.

## 10. Update the existing GitHub repository with Git CMD

Clone once if you do not already have a local clone:

```bat
git clone https://github.com/khaisilk1910/camera-tts-ezviz.git
cd camera-tts-ezviz
```

Update the local repository first:

```bat
git remote set-url origin https://github.com/khaisilk1910/camera-tts-ezviz.git
git pull --rebase origin main
```

Then copy the extracted v2 package files over the repository folder and overwrite the old files. After that run:

```bat
git add -A
git status
git commit -m "Portainer environment stack v2"
git push origin main
```

`git add -A` is important because this version removes the old required `config/cameras.yaml` and Docker-secret workflow.

After the push, open GitHub -> Actions and verify that **Build and publish Camera TTS image** completes successfully.

## 11. Security notes

- Change `API_KEY`; do not keep `change-me-now`.
- Camera passwords stored as Portainer environment values are convenient, but users with sufficient Docker/Portainer permissions can inspect container configuration. If you later require stronger secret isolation, the parser still supports password files and the API supports `API_KEY_FILE` for Docker-secret style deployment.
- Do not expose the API port directly to the public Internet. Keep it on your LAN/VPN and firewall it appropriately.
- Check the vendor's HCNetSDK redistribution terms before publishing SDK binaries publicly.
