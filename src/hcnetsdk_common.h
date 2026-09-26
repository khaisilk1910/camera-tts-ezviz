#pragma once

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include "HCNetSDK.h"

static inline const char *env_or(const char *name, const char *fallback) {
    const char *value = getenv(name);
    return (value && *value) ? value : fallback;
}

static inline int env_int(const char *name, int fallback) {
    const char *value = getenv(name);
    if (!value || !*value) return fallback;
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (!end || *end != '\0') return fallback;
    return (int)parsed;
}

static inline std::string sdk_root() {
    return std::string(env_or("HCNETSDK_ROOT", "/opt/hcnetsdk"));
}

static inline bool init_hcnetsdk() {
    const std::string root = sdk_root();
    const std::string libdir = root + "/lib/";
    const std::string crypto = root + "/lib/libcrypto.so.1.1";
    const std::string ssl = root + "/lib/libssl.so.1.1";

    NET_DVR_LOCAL_SDK_PATH sdk_path;
    memset(&sdk_path, 0, sizeof(sdk_path));
    strncpy(sdk_path.sPath, libdir.c_str(), sizeof(sdk_path.sPath) - 1);

    if (!NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, &sdk_path)) {
        fprintf(stderr, "NET_DVR_SetSDKInitCfg SDK path failed\n");
    }
    if (!NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH, (void *)crypto.c_str())) {
        fprintf(stderr, "NET_DVR_SetSDKInitCfg crypto path failed\n");
    }
    if (!NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH, (void *)ssl.c_str())) {
        fprintf(stderr, "NET_DVR_SetSDKInitCfg ssl path failed\n");
    }

    if (!NET_DVR_Init()) {
        fprintf(stderr, "NET_DVR_Init failed, error=%u\n", NET_DVR_GetLastError());
        return false;
    }

    int connect_timeout_ms = env_int("CAMERA_CONNECT_TIMEOUT_MS", 3000);
    if (connect_timeout_ms < 500) connect_timeout_ms = 500;
    if (connect_timeout_ms > 30000) connect_timeout_ms = 30000;

    int reconnect_interval_ms = env_int("CAMERA_RECONNECT_INTERVAL_MS", 10000);
    if (reconnect_interval_ms < 1000) reconnect_interval_ms = 1000;
    if (reconnect_interval_ms > 60000) reconnect_interval_ms = 60000;

    NET_DVR_SetConnectTime((DWORD)connect_timeout_ms, 1);
    NET_DVR_SetReconnect((DWORD)reconnect_interval_ms, TRUE);
    return true;
}

static inline LONG login_camera(NET_DVR_DEVICEINFO_V40 *device) {
    NET_DVR_USER_LOGIN_INFO login;
    memset(&login, 0, sizeof(login));
    memset(device, 0, sizeof(*device));

    const char *ip = env_or("CAMERA_IP", "");
    const char *user = env_or("CAMERA_USER", "admin");
    const char *password = env_or("CAMERA_PASSWORD", "");
    int port = env_int("CAMERA_PORT", 8000);

    if (!ip || !*ip || !password || !*password) {
        fprintf(stderr, "CAMERA_IP and CAMERA_PASSWORD are required\n");
        return -1;
    }
    if (port <= 0 || port > 65535) {
        fprintf(stderr, "Invalid CAMERA_PORT=%d\n", port);
        return -1;
    }

    strncpy(login.sDeviceAddress, ip, sizeof(login.sDeviceAddress) - 1);
    login.wPort = (WORD)port;
    strncpy(login.sUserName, user, sizeof(login.sUserName) - 1);
    strncpy(login.sPassword, password, sizeof(login.sPassword) - 1);
    login.bUseAsynLogin = FALSE;

    return NET_DVR_Login_V40(&login, device);
}
