#!/usr/bin/env bash
set -euo pipefail
STACK_NAME="${STACK_NAME:-camera-tts}"
docker stack rm "$STACK_NAME"
