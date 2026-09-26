#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
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
    unsigned char h[7];
    int c;

    while ((c = fgetc(fp)) != EOF) {
        if ((unsigned char)c != 0xFF) continue;

        int c2 = fgetc(fp);
        if (c2 == EOF) return false;

        if ((((unsigned char)c2) & 0xF6) != 0xF0) {
            ungetc(c2, fp);
            continue;
        }

        h[0] = 0xFF;
        h[1] = (unsigned char)c2;

        if (fread(&h[2], 1, 5, fp) != 5) return false;
        break;
    }

    if (c == EOF) return false;

    int frame_length =
        ((h[3] & 0x03) << 11) |
        (h[4] << 3) |
        ((h[5] & 0xE0) >> 5);

    if (frame_length < 7 || frame_length > 8192) {
        fprintf(stderr, "Invalid ADTS frame length: %d\n", frame_length);
        return false;
    }

    frame.resize(frame_length);
    memcpy(frame.data(), h, 7);

    int remain = frame_length - 7;
    if (remain > 0) {
        if ((int)fread(frame.data() + 7, 1, remain, fp) != remain) {
            return false;
        }
    }

    return true;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s /path/file.aac\n", argv[0]);
        return 1;
    }

    const char *aac_file = argv[1];
    if (!init_hcnetsdk()) return 1;

    NET_DVR_DEVICEINFO_V40 device;
    LONG user_id = login_camera(&device);

    if (user_id < 0) {
        fprintf(stderr, "LOGIN FAILED: %u\n", NET_DVR_GetLastError());
        NET_DVR_Cleanup();
        return 2;
    }

    printf("LOGIN SUCCESS userID=%ld\n", user_id);

    NET_DVR_COMPRESSION_AUDIO audio;
    memset(&audio, 0, sizeof(audio));
    if (NET_DVR_GetCurrentAudioCompress(user_id, &audio)) {
        printf("Audio codec=%u rate=%u bitrate=%u\n",
               audio.byAudioEncType,
               audio.byAudioSamplingRate,
               audio.byAudioBitRate);
        if (audio.byAudioEncType != 7) {
            fprintf(stderr, "WARNING: camera codec is not AAC (type 7).\n");
        }
    }

    FILE *fp = fopen(aac_file, "rb");
    if (!fp) {
        perror("fopen");
        NET_DVR_Logout(user_id);
        NET_DVR_Cleanup();
        return 3;
    }

    DWORD voice_chan = (DWORD)env_int("VOICE_CHAN", 1);
    LONG voice_handle = NET_DVR_StartVoiceCom_MR_V30(
        user_id,
        voice_chan,
        VoiceCallback,
        NULL
    );

    if (voice_handle < 0) {
        fprintf(stderr, "VOICE START FAILED: %u\n", NET_DVR_GetLastError());
        fclose(fp);
        NET_DVR_Logout(user_id);
        NET_DVR_Cleanup();
        return 4;
    }

    printf("VOICE START SUCCESS handle=%ld\n", voice_handle);
    usleep(300000);

    std::vector<unsigned char> frame;
    unsigned long frame_number = 0;
    bool send_failed = false;

    while (read_adts_frame(fp, frame)) {
        frame_number++;
        BOOL ok = NET_DVR_VoiceComSendData(
            voice_handle,
            (char *)frame.data(),
            (DWORD)frame.size()
        );

        if (!ok) {
            fprintf(stderr, "SEND FAILED frame=%lu size=%zu error=%u\n",
                    frame_number,
                    frame.size(),
                    NET_DVR_GetLastError());
            send_failed = true;
            break;
        }

        // AAC-LC uses 1024 samples/frame. Pace frames according to
        // the actual sample rate supplied by the bridge.
        int sample_rate = env_int("AUDIO_SAMPLE_RATE", 16000);
        if (sample_rate <= 0) sample_rate = 16000;
        useconds_t frame_delay_us = (useconds_t)((1024LL * 1000000LL) / sample_rate);
        usleep(frame_delay_us);
    }

    printf("Finished. Frames sent: %lu\n", frame_number);
    usleep(250000);

    NET_DVR_StopVoiceCom(voice_handle);
    fclose(fp);
    NET_DVR_Logout(user_id);
    NET_DVR_Cleanup();

    if (send_failed || frame_number == 0) {
        fprintf(stderr, "FAILED\n");
        return 5;
    }

    printf("DONE\n");
    return 0;
}
