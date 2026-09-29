#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <string>

#include "hcnetsdk_common.h"

static DWORD command_for(const std::string &direction) {
    if (direction == "left") return PAN_LEFT;
    if (direction == "right") return PAN_RIGHT;
    if (direction == "up") return TILT_UP;
    if (direction == "down") return TILT_DOWN;
    if (direction == "up_left") return UP_LEFT;
    if (direction == "up_right") return UP_RIGHT;
    if (direction == "down_left") return DOWN_LEFT;
    if (direction == "down_right") return DOWN_RIGHT;
    if (direction == "zoom_in") return ZOOM_IN;
    if (direction == "zoom_out") return ZOOM_OUT;
    return 0;
}

class PTZSession {
public:
    PTZSession() : user_id_(-1), channel_(env_int("PTZ_CHANNEL", 0)) {}
    ~PTZSession() { logout(); }

    bool login(std::string &error) {
        if (user_id_ >= 0) return true;
        NET_DVR_DEVICEINFO_V40 device;
        LONG uid = login_camera(&device);
        if (uid < 0) {
            error = "login failed error=" + std::to_string((unsigned int)NET_DVR_GetLastError());
            return false;
        }
        user_id_ = uid;
        if (channel_ <= 0) {
            const int start = (int)device.struDeviceV30.byStartChan;
            channel_ = start > 0 ? start : 1;
        }
        return true;
    }

    void logout() {
        if (user_id_ >= 0) {
            NET_DVR_Logout(user_id_);
            user_id_ = -1;
        }
    }

    LONG user_id() const { return user_id_; }
    int channel() const { return channel_; }

    bool move(const std::string &direction, int speed_percent, int duration_ms, std::string &error) {
        const DWORD command = command_for(direction);
        if (command == 0) {
            error = "unsupported direction";
            return false;
        }
        speed_percent = std::max(1, std::min(100, speed_percent));
        duration_ms = std::max(50, std::min(10000, duration_ms));
        const DWORD speed7 = (DWORD)std::max(1, std::min(7, (speed_percent * 7 + 99) / 100));

        for (int attempt = 0; attempt < 2; ++attempt) {
            if (!login(error)) {
                logout();
                usleep(80000);
                continue;
            }
            if (!NET_DVR_PTZControlWithSpeed_Other(user_id_, channel_, command, 0, speed7)) {
                error = "PTZ start failed error=" + std::to_string((unsigned int)NET_DVR_GetLastError());
                logout();
                usleep(80000);
                continue;
            }

            usleep((useconds_t)duration_ms * 1000U);
            if (!NET_DVR_PTZControlWithSpeed_Other(user_id_, channel_, command, 1, speed7)) {
                // Do not replay the whole movement after a stop failure; doing
                // so could move the camera twice. Drop the stale login instead.
                error = "PTZ stop failed error=" + std::to_string((unsigned int)NET_DVR_GetLastError());
                logout();
                return false;
            }
            return true;
        }
        return false;
    }

private:
    LONG user_id_;
    LONG channel_;
};

static int run_worker() {
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);

    if (!init_hcnetsdk()) {
        printf("ERR_READY\tSDK_INIT_FAILED\n");
        return 1;
    }

    PTZSession session;
    std::string login_error;
    session.login(login_error);  // Best-effort warm login only after first PTZ request starts this worker.
    printf("READY\t%d\t%d\n", (int)session.user_id(), session.channel());

    char *line = NULL;
    size_t cap = 0;
    ssize_t len;
    while ((len = getline(&line, &cap, stdin)) >= 0) {
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) line[--len] = '\0';
        if (strcmp(line, "PING") == 0) {
            printf("PONG\t%d\t%d\n", (int)session.user_id(), session.channel());
            continue;
        }
        if (strcmp(line, "QUIT") == 0) {
            printf("BYE\n");
            break;
        }
        if (strncmp(line, "MOVE\t", 5) != 0) {
            printf("ERR\tBAD_COMMAND\tunsupported command\n");
            continue;
        }

        char direction[32] = {0};
        int speed = 50;
        int duration_ms = 350;
        if (sscanf(line + 5, "%31[^\t]\t%d\t%d", direction, &speed, &duration_ms) != 3) {
            printf("ERR\tBAD_COMMAND\tMOVE requires direction, speed and duration_ms\n");
            continue;
        }
        std::string error;
        const auto started = std::chrono::steady_clock::now();
        if (session.move(direction, speed, duration_ms, error)) {
            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - started).count();
            printf("OK\t%lld\t%d\t%d\n", (long long)elapsed, (int)session.user_id(), session.channel());
        } else {
            printf("ERR\tPTZ_FAILED\t%s\n", error.c_str());
        }
    }

    free(line);
    session.logout();
    NET_DVR_Cleanup();
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--worker") == 0) return run_worker();
    fprintf(stderr, "Usage: %s --worker\n", argv[0]);
    return 1;
}
