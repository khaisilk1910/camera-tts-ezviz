FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# HCNetSDK is intentionally embedded in the image so the Docker host does not
# need an SDK installation under /opt.
COPY hcnetsdk/ /opt/hcnetsdk/
COPY src/ /build/src/

RUN test -f /opt/hcnetsdk/incEn/HCNetSDK.h \
    && test -f /opt/hcnetsdk/lib/libhcnetsdk.so \
    && g++ -std=c++17 -O2 \
       /build/src/send_aac.cpp \
       -I/opt/hcnetsdk/incEn \
       -I/build/src \
       -L/opt/hcnetsdk/lib \
       -Wl,-rpath,/opt/hcnetsdk/lib \
       -lhcnetsdk \
       -o /build/send_aac

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HCNETSDK_ROOT=/opt/hcnetsdk \
    LD_LIBRARY_PATH=/opt/hcnetsdk/lib:/opt/hcnetsdk/lib/HCNetSDKCom

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip ffmpeg ca-certificates tzdata \
       libstdc++6 libgcc-s1 libgomp1 libuuid1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir \
       "Flask>=3.0,<4" \
       "PyYAML>=6,<7" \
       "waitress>=3,<4" \
       "edge-tts>=7,<8"

COPY hcnetsdk/ /opt/hcnetsdk/
COPY --from=builder /build/send_aac /usr/local/bin/send_aac
COPY app/ /app/

RUN chmod +x /usr/local/bin/send_aac \
    && mkdir -p /config /cache \
    && test -x /usr/local/bin/send_aac \
    && test -f /opt/hcnetsdk/lib/HCNetSDKCom/libHCVoiceTalk.so

WORKDIR /app
EXPOSE 8124

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${CAMERA_TTS_PORT:-8124}/health >/dev/null || exit 1

CMD ["sh", "-c", "exec waitress-serve --listen=0.0.0.0:${CAMERA_TTS_PORT:-8124} --threads=8 server:app"]
