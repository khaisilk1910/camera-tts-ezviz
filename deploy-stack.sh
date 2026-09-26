#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env.stack ]]; then
  echo "Missing .env.stack. Copy .env.stack.example to .env.stack and edit CAMERA_TTS_IMAGE." >&2
  exit 1
fi
if [[ ! -f config/cameras.yaml ]]; then
  echo "Missing config/cameras.yaml. Copy config/cameras.yaml.example and edit it." >&2
  exit 1
fi

if ! docker info --format '{{.Swarm.LocalNodeState}}' | grep -q '^active$'; then
  echo "Docker Swarm is not active. Run: docker swarm init" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source ./.env.stack
set +a

: "${CAMERA_TTS_IMAGE:?CAMERA_TTS_IMAGE is required}"
export CAMERA_TTS_CONFIG_PATH="$(pwd)/config/cameras.yaml"

NODE_ID="$(docker info --format '{{.Swarm.NodeID}}')"
if [[ -z "$NODE_ID" ]]; then
  echo "Cannot determine local Swarm node ID." >&2
  exit 1
fi
docker node update --label-add camera_tts=true "$NODE_ID" >/dev/null

STACK_NAME="${STACK_NAME:-camera-tts}"
docker stack deploy \
  --with-registry-auth \
  --resolve-image always \
  -c stack.yml \
  "$STACK_NAME"

echo
echo "Stack: $STACK_NAME"
docker stack services "$STACK_NAME"

# Force one task refresh so edits to the bind-mounted cameras.yaml are loaded.
docker service update --force "${STACK_NAME}_camera-tts" >/dev/null
echo "Reloaded ${STACK_NAME}_camera-tts"
