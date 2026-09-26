# Camera TTS EZVIZ - self-contained HCNetSDK Docker Swarm image

This project builds one `linux/amd64` Docker image containing:

- HCNetSDK supplied with this project (`hcnetsdk/`)
- `send_aac` compiled against HCNetSDK
- Edge TTS
- FFmpeg AAC conversion
- Flask/Waitress API
- per-camera FIFO queues so messages for the same camera never overlap
- support for multiple cameras
- Docker Swarm `stack deploy`
- GitHub Actions -> automatic build and push to GHCR on every push to `main`

The Ubuntu host does **not** need HCNetSDK installed. The SDK is copied into `/opt/hcnetsdk` inside the image.

> Important: the supplied HCNetSDK is x86-64 Linux, so this project intentionally builds only `linux/amd64`.
>
> Also check the HCNetSDK redistribution license before making the repository or GHCR package public. A private GitHub repository/private GHCR package is the recommended default.

## 1. Repository structure

```text
.
├── .github/workflows/docker-publish.yml
├── app/server.py
├── config/cameras.yaml.example
├── hcnetsdk/
│   ├── incEn/
│   └── lib/
├── homeassistant/
├── secrets/
├── src/
│   ├── hcnetsdk_common.h
│   └── send_aac.cpp
├── Dockerfile
├── stack.yml
├── create-secrets.sh
├── deploy-stack.sh
├── update-stack.sh
└── test-api.sh
```

## 2. Push to GitHub with Git CMD on Windows

Create a **private** empty repository on GitHub first, for example:

```text
camera-tts-ezviz
```

Do not create a README or `.gitignore` from GitHub if this folder already contains them.

Open Git CMD in the extracted project folder:

```bat
cd C:\camera-tts-ezviz

git init
git branch -M main
git add .
git commit -m "Initial Camera TTS Docker Swarm build"
git remote add origin https://github.com/YOUR_GITHUB_USER/camera-tts-ezviz.git
git push -u origin main
```

For every later update:

```bat
git add .
git commit -m "Update camera TTS"
git push
```

A push to `main` automatically starts `.github/workflows/docker-publish.yml` and publishes:

```text
ghcr.io/your_github_user/camera-tts-ezviz:latest
```

It also publishes an immutable SHA tag such as:

```text
ghcr.io/your_github_user/camera-tts-ezviz:sha-abc1234
```

If you push a Git tag:

```bat
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions also creates version tags such as `1.0.0` and `1.0`.

## 3. GitHub Actions

No registry password is stored in the repository. The workflow uses GitHub's built-in `GITHUB_TOKEN` with `packages: write` to publish to GHCR.

The workflow currently uses:

```yaml
uses: actions/checkout@v7
uses: docker/setup-buildx-action@v4
uses: docker/login-action@v4
uses: docker/metadata-action@v6
uses: docker/build-push-action@v7
```

Only `linux/amd64` is built because this HCNetSDK binary is x86-64.

After the first push, open GitHub -> Actions -> `Build and publish Docker image` and confirm the build is green.

## 4. Prepare the Ubuntu Docker Swarm host

Clone the private repository:

```bash
git clone https://github.com/YOUR_GITHUB_USER/camera-tts-ezviz.git
cd camera-tts-ezviz
```

Initialize Swarm once:

```bash
docker swarm init
```

If it already says the node is part of a swarm, do not initialize it again.

### Login to private GHCR

For a private GHCR package, create a GitHub Personal Access Token **classic** with at least `read:packages`, then:

```bash
export CR_PAT='YOUR_GITHUB_PAT_CLASSIC'
echo "$CR_PAT" | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
unset CR_PAT
```

The stack deploy script uses `--with-registry-auth`, so Swarm can pull the private image. It also labels the current node `camera_tts=true` and pins the service to that node.

## 5. Create runtime configuration

Copy templates:

```bash
cp .env.stack.example .env.stack
cp config/cameras.yaml.example config/cameras.yaml
```

Edit `.env.stack`:

```bash
nano .env.stack
```

Example:

```env
CAMERA_TTS_IMAGE=ghcr.io/your_github_user/camera-tts-ezviz:latest
CAMERA_TTS_PORT=8124
CAMERA_TTS_MAX_TEXT=500
CAMERA_TTS_PREP_WORKERS=4
CAMERA_TTS_SEND_TIMEOUT=180
CAMERA_TTS_PREP_TIMEOUT=300
TZ=Asia/Ho_Chi_Minh
```

Edit camera configuration:

```bash
nano config/cameras.yaml
```

Example:

```yaml
default_camera: gate

defaults:
  port: 8000
  voice_chan: 1
  voice: vi-VN-HoaiMyNeural
  rate: "+0%"
  edge_volume: "+0%"
  gain_db: 4
  sample_rate: 16000
  bitrate: 32k
  queue_size: 20

cameras:
  gate:
    ip: "192.168.31.59"
    username: "admin"
    password_file: "/run/secrets/cam_gate_password"
```

## 6. Create Docker secrets

Create local secret files. These files are ignored by Git.

```bash
mkdir -p secrets
printf '%s' 'YOUR_LONG_API_KEY' > secrets/camera_tts_api_key.txt
printf '%s' 'YOUR_CAMERA_PASSWORD' > secrets/cam_gate_password.txt
chmod 600 secrets/*.txt
```

Create the Swarm secrets:

```bash
./create-secrets.sh
```

Verify:

```bash
docker secret ls
```

Expected names:

```text
camera_tts_api_key
cam_gate_password
```

## 7. Deploy with Docker Stack

```bash
./deploy-stack.sh
```

Equivalent core command:

```bash
set -a
source ./.env.stack
set +a
export CAMERA_TTS_CONFIG_PATH="$(pwd)/config/cameras.yaml"
docker stack deploy --with-registry-auth --resolve-image always -c stack.yml camera-tts
```

The service uses the Linux host network directly. Therefore no `ports:` mapping is needed; the API listens on the Docker host itself at port `8124`.

Check status:

```bash
docker stack services camera-tts
docker service ps camera-tts_camera-tts
```

Logs:

```bash
docker service logs -f camera-tts_camera-tts
```

Health:

```bash
curl http://127.0.0.1:8124/health
```

## 8. Test TTS

Use the included helper:

```bash
./test-api.sh 127.0.0.1 gate
```

Or call directly:

```bash
curl -X POST http://127.0.0.1:8124/say \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_LONG_API_KEY' \
  -d '{"camera":"gate","text":"Xin chào, đây là thử nghiệm loa camera"}'
```

The API returns HTTP `202` immediately and the camera worker plays the message asynchronously.

## 9. Queue behavior

Each camera has an independent queue.

If Home Assistant sends three messages to `gate` quickly:

```text
1. Có người trước cổng
2. Cửa đang mở
3. Vui lòng đóng cửa
```

`gate` plays them sequentially in that order. They do not overlap.

A different camera can play at the same time because it has a different worker/queue.

## 10. Add a second camera

Create another secret:

```bash
printf '%s' 'YARD_CAMERA_PASSWORD' > secrets/cam_yard_password.txt
chmod 600 secrets/cam_yard_password.txt
docker secret create cam_yard_password secrets/cam_yard_password.txt
```

Add to `config/cameras.yaml`:

```yaml
  yard:
    ip: "192.168.31.60"
    username: "admin"
    password_file: "/run/secrets/cam_yard_password"
    gain_db: 5
```

Then add the secret to the `camera-tts` service in `stack.yml`:

```yaml
    secrets:
      - camera_tts_api_key
      - cam_gate_password
      - cam_yard_password
```

And declare it at the bottom:

```yaml
secrets:
  camera_tts_api_key:
    external: true
  cam_gate_password:
    external: true
  cam_yard_password:
    external: true
```

Redeploy:

```bash
./deploy-stack.sh
```

Test:

```bash
./test-api.sh 127.0.0.1 yard
```

Send one message to multiple cameras:

```json
{
  "camera": ["gate", "yard"],
  "text": "Thông báo thử nghiệm"
}
```

Send to all configured cameras:

```json
{
  "camera": "all",
  "text": "Thông báo toàn bộ camera"
}
```

## 11. Update after a new Git push

Workflow:

```text
Git CMD -> git push
        -> GitHub Actions
        -> build Docker image
        -> push ghcr.io/...:latest
        -> Ubuntu pulls the new image
        -> stack redeploys
```

On Ubuntu after the GitHub Action finishes:

```bash
cd camera-tts-ezviz
git pull
./update-stack.sh
```

`update-stack.sh` runs `docker pull` and redeploys the stack with `--resolve-image always`.

For production, a versioned tag is safer than relying only on `latest`. Example:

```env
CAMERA_TTS_IMAGE=ghcr.io/your_github_user/camera-tts-ezviz:1.0.0
```

## 12. Home Assistant

Example `configuration.yaml`/included REST command:

```yaml
rest_command:
  camera_tts:
    url: "http://192.168.31.100:8124/say"
    method: POST
    headers:
      X-API-Key: !secret camera_tts_api_key
    content_type: "application/json"
    timeout: 10
    payload: >
      {
        "camera": {{ camera | default('gate') | to_json }},
        "text": {{ message | to_json }}
      }
```

Home Assistant action:

```yaml
- action: rest_command.camera_tts
  data:
    camera: gate
    message: "Có người đang đứng trước cổng"
```

The API responds quickly with `202`, so Home Assistant does not wait for the spoken message to finish.

## 13. Volume

Default:

```yaml
gain_db: 4
```

Try `5` or `6` if necessary. The FFmpeg pipeline applies a limiter after digital gain to reduce clipping.

Per request:

```json
{
  "camera": "gate",
  "text": "Cảnh báo có người trước cổng",
  "gain_db": 6
}
```

The API clamps requested digital gain to `-12..+12 dB`.

## 14. Useful commands

```bash
# Services
docker stack services camera-tts

# Task details
docker service ps camera-tts_camera-tts

# Logs
docker service logs -f camera-tts_camera-tts

# Force restart
docker service update --force camera-tts_camera-tts

# Remove stack
./remove-stack.sh

# Images
docker images | grep camera-tts

# Secrets
docker secret ls
```

## 15. Security notes

- Keep the GitHub repository private unless your HCNetSDK license explicitly permits redistribution.
- Keep the GHCR package private unless redistribution is permitted.
- Do not commit `config/cameras.yaml`, `.env.stack`, or `secrets/*.txt`; the included `.gitignore` excludes them.
- The stack uses Docker secrets for camera passwords and the API key.
- The deploy script labels the current Swarm node with `camera_tts=true`, and the service is pinned to that node because `config/cameras.yaml` is bind-mounted from the host and HCNetSDK voice playback should have one active service instance.
