FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY hcnetsdk/ /opt/hcnetsdk/
COPY src/ /build/src/

RUN test -f /opt/hcnetsdk/incEn/HCNetSDK.h \
    && test -f /opt/hcnetsdk/lib/libhcnetsdk.so \
    && test -f /opt/hcnetsdk/lib/HCNetSDKCom/libHCVoiceTalk.so \
    && g++ -std=c++17 -O2 -Wall -Wextra \
       /build/src/send_aac.cpp \
       -I/opt/hcnetsdk/incEn \
       -I/build/src \
       -L/opt/hcnetsdk/lib \
       -Wl,-rpath,/opt/hcnetsdk/lib \
       -lhcnetsdk \
       -o /build/send_aac

FROM ubuntu:22.04

ARG APP_VERSION=2.3.3
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_VERSION=${APP_VERSION} \
    HCNETSDK_ROOT=/opt/hcnetsdk \
    LD_LIBRARY_PATH=/opt/hcnetsdk/lib:/opt/hcnetsdk/lib/HCNetSDKCom

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip ffmpeg ca-certificates tzdata \
       libstdc++6 libgcc-s1 libgomp1 libuuid1 curl tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY hcnetsdk/ /opt/hcnetsdk/
COPY --from=builder /build/send_aac /usr/local/bin/send_aac
COPY app/ /app/

RUN chmod 0755 /usr/local/bin/send_aac \
    && mkdir -p /cache \
    && test -x /usr/local/bin/send_aac \
    && test -f /opt/hcnetsdk/lib/libhcnetsdk.so \
    && test -f /opt/hcnetsdk/lib/HCNetSDKCom/libHCVoiceTalk.so \
    && python3 -m py_compile /app/config.py /app/server.py

WORKDIR /app
EXPOSE 8124

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8124}/health" >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/app/server.py"]
