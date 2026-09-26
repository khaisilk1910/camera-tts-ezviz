#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

set -a
# shellcheck disable=SC1091
source ./.env.stack
set +a

# Pull first to make errors obvious, then force Swarm to re-resolve :latest.
docker pull "$CAMERA_TTS_IMAGE"
./deploy-stack.sh
