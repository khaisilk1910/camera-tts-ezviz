#pragma once

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include "HCNetSDK.h"

static inline const char *env_or(const char *name, const char *fallback) {
    const char *v = getenv(name);
    return (v && *v) ? v : fallback;
}

static inline int env_int(const char *name, int fallback) {
    const char *v = getenv(name);
    return (v && *v) ? atoi(v) : fallback;
}

static inline std::string sdk_root() {
    return std::string(env_or("HCNETSDK_ROOT", "/opt/hcnetsdk"));
}

static inline bool init_hcnetsdk() {
    std::string root = sdk_root();
    std::string libdir = root + "/lib/";
    std::string crypto = root + "/lib/libcrypto.so.1.1";
    std::string ssl = root + "/lib/libssl.so.1.1";

    NET_DVR_LOCAL_SDK_PATH sdk_path;
    memset(&sdk_path, 0, sizeof(sdk_path));
    strncpy(sdk_path.sPath, libdir.c_str(), sizeof(sdk_path.sPath) - 1);

    NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, &sdk_path);
    NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH, (void *)crypto.c_str());
    NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH, (void *)ssl.c_str());

    if (!NET_DVR_Init()) {
        printf("NET_DVR_Init failed, error=%u\n", NET_DVR_GetLastError());
        return false;
    }

    NET_DVR_SetConnectTime(3000, 1);
    NET_DVR_SetReconnect(10000, TRUE);
    return true;
}

static inline LONG login_camera(NET_DVR_DEVICEINFO_V40 *device) {
    NET_DVR_USER_LOGIN_INFO login;
    memset(&login, 0, sizeof(login));
    memset(device, 0, sizeof(*device));

    const char *ip = env_or("CAMERA_IP", "192.168.31.59");
    const char *user = env_or("CAMERA_USER", "admin");
    const char *password = env_or("CAMERA_PASSWORD", "");
    int port = env_int("CAMERA_PORT", 8000);

    strncpy(login.sDeviceAddress, ip, sizeof(login.sDeviceAddress) - 1);
    login.wPort = (WORD)port;
    strncpy(login.sUserName, user, sizeof(login.sUserName) - 1);
    strncpy(login.sPassword, password, sizeof(login.sPassword) - 1);
    login.bUseAsynLogin = FALSE;

    return NET_DVR_Login_V40(&login, device);
}
