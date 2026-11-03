/* backend.cpp - backend selection and the host backends (null, wav). See backend.h. */
#include "backend.h"
#include "mixer.h"
#include "../os/os.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static os_lock_t g_lock;
static char g_spec[512];
static const fnvnx_audio_backend *g_active;
static int g_started;

void fnvnx_audio_configure(const char *spec)
{
    os_lock(&g_lock);
    snprintf(g_spec, sizeof g_spec, "%s", spec ? spec : "");
    os_unlock(&g_lock);
}

static const fnvnx_audio_backend *pick(const char *spec, const char **arg)
{
    *arg = "";
    if (!strncmp(spec, "wav:", 4)) {
        *arg = spec + 4;
        return &fnvnx_audio_backend_wav;
    }
    if (!strcmp(spec, "null"))
        return &fnvnx_audio_backend_null;
#ifdef __SWITCH__
    if (!strcmp(spec, "audren") || !*spec)
        return &fnvnx_audio_backend_audren;
#else
    if (!*spec)
        return &fnvnx_audio_backend_null;
#endif
    return NULL; /* "manual" or unknown: no thread */
}

int fnvnx_audio_start(void)
{
    os_lock(&g_lock);
    if (g_started) {
        os_unlock(&g_lock);
        return 0;
    }
    if (!g_spec[0]) {
        const char *e = getenv("FNVNX_AUDIO");
        if (e)
            snprintf(g_spec, sizeof g_spec, "%s", e);
    }
    mixer_init(FNVNX_AUDIO_RATE, FNVNX_AUDIO_PERIOD);
    const char *arg;
    const fnvnx_audio_backend *b = pick(g_spec, &arg);
    int rc = 0;
    if (b) {
        rc = b->start(arg, FNVNX_AUDIO_RATE, FNVNX_AUDIO_PERIOD);
        if (rc) {
            char msg[600];
            snprintf(msg, sizeof msg, "fnvnx audio: backend %s failed (%d); falling back to null", b->name, rc);
            os_debug_print(msg);
            b = &fnvnx_audio_backend_null;
            rc = b->start("", FNVNX_AUDIO_RATE, FNVNX_AUDIO_PERIOD);
        }
    }
    g_active = rc ? NULL : b;
    g_started = 1;
    os_unlock(&g_lock);
    return rc;
}

void fnvnx_audio_stop(void)
{
    os_lock(&g_lock);
    if (g_active)
        g_active->stop();
    g_active = NULL;
    g_started = 0;
    os_unlock(&g_lock);
}

/* ---- paced host thread shared by null and wav */
static os_thread *g_thread;
static volatile int g_run;
static uint32_t g_rate, g_period;
static FILE *g_wav;
static uint32_t g_wav_bytes;

static void put32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void put16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }

static void wav_header(FILE *f, uint32_t rate, uint32_t data_bytes)
{
    uint8_t h[44];
    memcpy(h, "RIFF", 4); put32(h + 4, 36 + data_bytes); memcpy(h + 8, "WAVEfmt ", 8);
    put32(h + 16, 16); put16(h + 20, 1); put16(h + 22, 2); put32(h + 24, rate); put32(h + 28, rate * 4);
    put16(h + 32, 4); put16(h + 34, 16); memcpy(h + 36, "data", 4); put32(h + 40, data_bytes);
    fseek(f, 0, SEEK_SET);
    fwrite(h, 1, sizeof h, f);
}

static void pump(void *arg)
{
    (void)arg;
    int16_t *buf = (int16_t *)malloc((size_t)g_period * 4);
    uint64_t next = os_monotonic_ns();
    const uint64_t period_ns = (uint64_t)g_period * 1000000000ull / g_rate;
    while (g_run && buf) {
        mixer_render_s16(buf, g_period);
        if (g_wav) {
            fwrite(buf, 4, g_period, g_wav);
            g_wav_bytes += g_period * 4;
        }
        next += period_ns;
        uint64_t now = os_monotonic_ns();
        if (next > now)
            os_sleep_ns(next - now);
        else
            next = now;
    }
    free(buf);
}

static int paced_start(uint32_t rate, uint32_t period)
{
    g_rate = rate;
    g_period = period;
    g_run = 1;
    g_thread = os_thread_create(pump, NULL, 64 * 1024, 2, -1);
    if (!g_thread || os_thread_start(g_thread)) {
        g_run = 0;
        return -1;
    }
    return 0;
}

static void paced_stop(void)
{
    g_run = 0;
    if (g_thread) {
        os_thread_join(g_thread);
        os_thread_free(g_thread);
        g_thread = NULL;
    }
    if (g_wav) {
        wav_header(g_wav, g_rate, g_wav_bytes);
        fclose(g_wav);
        g_wav = NULL;
    }
}

static int null_start(const char *arg, uint32_t rate, uint32_t period)
{
    (void)arg;
    return paced_start(rate, period);
}

static int wav_start(const char *path, uint32_t rate, uint32_t period)
{
    g_wav = fopen(path, "wb");
    if (!g_wav)
        return -1;
    g_wav_bytes = 0;
    wav_header(g_wav, rate, 0);
    return paced_start(rate, period);
}

const fnvnx_audio_backend fnvnx_audio_backend_null = {"null", null_start, paced_stop};
const fnvnx_audio_backend fnvnx_audio_backend_wav = {"wav", wav_start, paced_stop};
