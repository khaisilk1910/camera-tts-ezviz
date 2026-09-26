#!/usr/bin/env bash
set -euo pipefail
HOST="${1:-127.0.0.1}"
CAMERA="${2:-gate}"
API_KEY="${CAMERA_TTS_API_KEY:-}"
if [[ -z "$API_KEY" && -f secrets/camera_tts_api_key.txt ]]; then
  API_KEY="$(tr -d '\r\n' < secrets/camera_tts_api_key.txt)"
fi
: "${API_KEY:?Set CAMERA_TTS_API_KEY or create secrets/camera_tts_api_key.txt}"
curl -fsS -X POST "http://${HOST}:8124/say" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "{\"camera\":\"${CAMERA}\",\"text\":\"Xin chào, đây là thử nghiệm TTS camera từ Docker Swarm\"}"
echo
