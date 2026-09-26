#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

create_secret() {
  local name="$1"
  local file="$2"
  if docker secret inspect "$name" >/dev/null 2>&1; then
    echo "Secret already exists: $name"
    return
  fi
  if [[ ! -s "$file" ]]; then
    echo "Missing or empty secret file: $file" >&2
    exit 1
  fi
  docker secret create "$name" "$file" >/dev/null
  echo "Created secret: $name"
}

create_secret camera_tts_api_key secrets/camera_tts_api_key.txt
create_secret cam_gate_password secrets/cam_gate_password.txt
