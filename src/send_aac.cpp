#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <chrono>
#include <string>
#include <vector>

#include "hcnetsdk_common.h"

static void CALLBACK VoiceCallback(
    LONG lVoiceComHandle,
    char *pRecvDataBuffer,
    DWORD dwBufSize,
    BYTE byAudioFlag,
    void *pUser)
{
    (void)lVoiceComHandle;
    (void)pRecvDataBuffer;
    (void)dwBufSize;
    (void)byAudioFlag;
    (void)pUser;
}

static bool read_adts_frame(FILE *fp, std::vector<unsigned char> &frame) {
    unsigned char header[7];
    int first;

    while ((first = fgetc(fp)) != EOF) {
        if ((unsigned char)first != 0xFF) continue;

        int second = fgetc(fp);
        if (second == EOF) return false;

        if ((((unsigned char)second) & 0xF6) != 0xF0) {
            ungetc(second, fp);
            continue;
        }

        header[0] = 0xFF;
        header[1] = (unsigned char)second;
        if (fread(&header[2], 1, 5, fp) != 5) return false;
        break;
    }

    if (first == EOF) return false;

    const int frame_length =
        ((header[3] & 0x03) << 11) |
        (header[4] << 3) |
        ((header[5] & 0xE0) >> 5);

    if (frame_length < 7 || frame_length > 8192) {
        fprintf(stderr, "Invalid ADTS frame length: %d\n", frame_length);
        return false;
    }

    frame.resize((size_t)frame_length);
    memcpy(frame.data(), header, 7);

    const int remaining = frame_length - 7;
    if (remaining > 0) {
        if ((int)fread(frame.data() + 7, 1, (size_t)remaining, fp) != remaining) {
            return false;
        }
    }
    return true;
}

class CameraSession {
public:
    CameraSession()
        : user_id_(-1), voice_chan_((DWORD)env_int("VOICE_CHAN", 1)),
          sample_rate_(env_int("AUDIO_SAMPLE_RATE", 16000)),
          start_delay_ms_(env_int("VOICE_START_DELAY_MS", 120)),
          end_delay_ms_(env_int("VOICE_END_DELAY_MS", 80)),
          reopen_guard_ms_(env_int("VOICE_REOPEN_GUARD_MS", 1250)),
          has_last_stop_(false) {
        if (sample_rate_ < 8000 || sample_rate_ > 48000) sample_rate_ = 16000;
        if (start_delay_ms_ < 0) start_delay_ms_ = 0;
        if (start_delay_ms_ > 2000) start_delay_ms_ = 2000;
        if (end_delay_ms_ < 0) end_delay_ms_ = 0;
        if (end_delay_ms_ > 2000) end_delay_ms_ = 2000;
        if (reopen_guard_ms_ < 0) reopen_guard_ms_ = 0;
        if (reopen_guard_ms_ > 5000) reopen_guard_ms_ = 5000;
    }

    ~CameraSession() { logout(); }

    bool login(bool verbose = true) {
        if (user_id_ >= 0) return true;

        NET_DVR_DEVICEINFO_V40 device;
        LONG uid = login_camera(&device);
        if (uid < 0) {
            if (verbose) {
                fprintf(stderr, "LOGIN FAILED: %u\n", NET_DVR_GetLastError());
                fflush(stderr);
            }
            return false;
        }

        user_id_ = uid;
        if (verbose) {
            fprintf(stderr, "LOGIN SUCCESS userID=%d\n", (int)user_id_);
            fflush(stderr);
        }

        NET_DVR_COMPRESSION_AUDIO audio;
        memset(&audio, 0, sizeof(audio));
        if (NET_DVR_GetCurrentAudioCompress(user_id_, &audio)) {
            fprintf(stderr, "AUDIO codec=%u rate=%u bitrate=%u\n",
                    audio.byAudioEncType,
                    audio.byAudioSamplingRate,
                    audio.byAudioBitRate);
            if (audio.byAudioEncType != 7) {
                fprintf(stderr, "WARNING: camera reports non-AAC codec type=%u\n", audio.byAudioEncType);
            }
            fflush(stderr);
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

    bool play(const std::string &aac_file, unsigned long &frames_sent, long long &elapsed_ms, std::string &error) {
        // Retry once with a fresh login if the SDK session became stale.
        for (int attempt = 0; attempt < 2; ++attempt) {
            if (!login(true)) {
                error = "login failed error=" + std::to_string((unsigned int)NET_DVR_GetLastError());
                logout();
                continue;
            }

            if (play_once(aac_file, frames_sent, elapsed_ms, error)) {
                return true;
            }

            fprintf(stderr, "PLAY attempt %d failed: %s; reconnecting\n", attempt + 1, error.c_str());
            fflush(stderr);
            logout();
            usleep(100000);
        }
        return false;
    }

private:
    bool play_once(const std::string &aac_file, unsigned long &frames_sent, long long &elapsed_ms, std::string &error) {
        FILE *fp = fopen(aac_file.c_str(), "rb");
        if (!fp) {
            error = "unable to open AAC file";
            return false;
        }

        if (has_last_stop_ && reopen_guard_ms_ > 0) {
            const auto now = std::chrono::steady_clock::now();
            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - last_stop_).count();
            const long long remaining = (long long)reopen_guard_ms_ - elapsed;
            if (remaining > 0) usleep((useconds_t)remaining * 1000U);
        }

        const auto started = std::chrono::steady_clock::now();
        LONG voice_handle = NET_DVR_StartVoiceCom_MR_V30(user_id_, voice_chan_, VoiceCallback, NULL);
        if (voice_handle < 0) {
            error = "voice start failed error=" + std::to_string((unsigned int)NET_DVR_GetLastError());
            fclose(fp);
            return false;
        }

        if (start_delay_ms_ > 0) usleep((useconds_t)start_delay_ms_ * 1000U);

        const useconds_t frame_delay_us = (useconds_t)((1024LL * 1000000LL) / sample_rate_);
        std::vector<unsigned char> frame;
        frames_sent = 0;
        bool send_failed = false;

        while (read_adts_frame(fp, frame)) {
            ++frames_sent;
            BOOL ok = NET_DVR_VoiceComSendData(
                voice_handle,
                (char *)frame.data(),
                (DWORD)frame.size()
            );
            if (!ok) {
                error = "send failed frame=" + std::to_string(frames_sent) +
                        " error=" + std::to_string((unsigned int)NET_DVR_GetLastError());
                send_failed = true;
                break;
            }
            usleep(frame_delay_us);
        }

        if (end_delay_ms_ > 0) usleep((useconds_t)end_delay_ms_ * 1000U);
        NET_DVR_StopVoiceCom(voice_handle);
        last_stop_ = std::chrono::steady_clock::now();
        has_last_stop_ = true;
        fclose(fp);

        const auto ended = std::chrono::steady_clock::now();
        elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(ended - started).count();

        if (send_failed || frames_sent == 0) {
            if (frames_sent == 0 && !send_failed) error = "AAC file contains no ADTS frames";
            return false;
        }
        return true;
    }

    LONG user_id_;
    DWORD voice_chan_;
    int sample_rate_;
    int start_delay_ms_;
    int end_delay_ms_;
    int reopen_guard_ms_;
    std::chrono::steady_clock::time_point last_stop_;
    bool has_last_stop_;
};

static int run_worker() {
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);

    if (!init_hcnetsdk()) {
        printf("ERR_READY\tSDK_INIT_FAILED\n");
        return 1;
    }

    CameraSession session;
    // Warm login. A temporary camera outage should not kill the worker; PLAY
    // will retry login when a request arrives.
    session.login(true);
    printf("READY\t%d\n", (int)session.user_id());

    char *line = NULL;
    size_t cap = 0;
    ssize_t len;

    while ((len = getline(&line, &cap, stdin)) >= 0) {
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
            line[--len] = '\0';
        }

        if (strcmp(line, "PING") == 0) {
            printf("PONG\t%d\n", (int)session.user_id());
            continue;
        }
        if (strcmp(line, "QUIT") == 0) {
            printf("BYE\n");
            break;
        }

        const char prefix[] = "PLAY\t";
        if (strncmp(line, prefix, sizeof(prefix) - 1) != 0) {
            printf("ERR\tBAD_COMMAND\tunsupported command\n");
            continue;
        }

        const char *path = line + sizeof(prefix) - 1;
        if (!*path) {
            printf("ERR\tBAD_PATH\tempty AAC path\n");
            continue;
        }

        unsigned long frames = 0;
        long long elapsed_ms = 0;
        std::string error;
        if (session.play(path, frames, elapsed_ms, error)) {
            printf("OK\t%lu\t%lld\t%d\n", frames, elapsed_ms, (int)session.user_id());
        } else {
            printf("ERR\tPLAY_FAILED\t%s\n", error.c_str());
        }
    }

    free(line);
    session.logout();
    NET_DVR_Cleanup();
    return 0;
}

static int run_once(const char *aac_file) {
    if (!init_hcnetsdk()) return 1;

    CameraSession session;
    unsigned long frames = 0;
    long long elapsed_ms = 0;
    std::string error;
    bool ok = session.play(aac_file, frames, elapsed_ms, error);

    if (ok) {
        printf("DONE frames=%lu elapsed_ms=%lld\n", frames, elapsed_ms);
    } else {
        fprintf(stderr, "FAILED: %s\n", error.c_str());
    }

    session.logout();
    NET_DVR_Cleanup();
    return ok ? 0 : 5;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--worker") == 0) {
        return run_worker();
    }
    if (argc == 2) {
        return run_once(argv[1]);
    }

    fprintf(stderr, "Usage: %s /path/file.aac | --worker\n", argv[0]);
    return 1;
}
