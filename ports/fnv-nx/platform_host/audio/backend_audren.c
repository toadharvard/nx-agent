/* backend_audren.c - Horizon output through the libnx audio renderer (audren + audrv).
 *
 * One stereo PCM16 voice at 48 kHz fed with a ring of NBUF wave buffers of `period` frames from mixer_render_s16;
 * the thread refills every buffer the renderer has finished and waits for the next renderer frame (5 ms).
 * Latency ~ NBUF * period. Unbuilt so far (S5 owns the toolchain); syntax-checked against libnx headers.
 */
#ifdef __SWITCH__
#include <switch.h>
#include <malloc.h>
#include <string.h>

#include "../../platform/audio/backend.h"
#include "../../platform/audio/mixer.h"
#include "../../platform/os/os.h"

#define NBUF 4

static AudioDriver g_drv;
static AudioDriverWaveBuf g_wb[NBUF];
static void *g_pool;
static int g_pool_id = -1;
static uint32_t g_period;
static os_thread *g_thread;
static volatile int g_run;
static int g_audren_up, g_drv_up;

static void pump(void *arg)
{
    (void)arg;
    while (g_run) {
        for (int i = 0; i < NBUF; i++) {
            if (g_wb[i].state == AudioDriverWaveBufState_Free || g_wb[i].state == AudioDriverWaveBufState_Done) {
                int16_t *pcm = (int16_t *)((uint8_t *)g_pool + (size_t)i * g_period * 4);
                mixer_render_s16(pcm, g_period);
                armDCacheFlush(pcm, (size_t)g_period * 4);
                audrvVoiceAddWaveBuf(&g_drv, 0, &g_wb[i]);
            }
        }
        if (!audrvVoiceIsPlaying(&g_drv, 0))
            audrvVoiceStart(&g_drv, 0);
        audrvUpdate(&g_drv);
        audrenWaitFrame();
    }
}

static void audren_stop(void);

static int audren_start(const char *arg, uint32_t rate, uint32_t period)
{
    (void)arg;
    static const AudioRendererConfig cfg = {
        .output_rate = AudioRendererOutputRate_48kHz, .num_voices = 4, .num_effects = 0, .num_sinks = 1,
        .num_mix_objs = 1, .num_mix_buffers = 2,
    };
    static const uint8_t sink_channels[2] = {0, 1};
    if (rate != 48000)
        return -1;
    g_period = period;
    if (R_FAILED(audrenInitialize(&cfg)))
        return -2;
    g_audren_up = 1;
    if (R_FAILED(audrvCreate(&g_drv, &cfg, 2))) {
        audren_stop();
        return -3;
    }
    g_drv_up = 1;
    size_t pool_size = ((size_t)NBUF * period * 4 + AUDREN_MEMPOOL_ALIGNMENT - 1) & ~(size_t)(AUDREN_MEMPOOL_ALIGNMENT - 1);
    g_pool = memalign(AUDREN_MEMPOOL_ALIGNMENT, pool_size);
    if (!g_pool) {
        audren_stop();
        return -4;
    }
    memset(g_pool, 0, pool_size);
    armDCacheFlush(g_pool, pool_size);
    g_pool_id = audrvMemPoolAdd(&g_drv, g_pool, pool_size);
    audrvMemPoolAttach(&g_drv, g_pool_id);
    for (int i = 0; i < NBUF; i++) {
        memset(&g_wb[i], 0, sizeof g_wb[i]);
        g_wb[i].data_raw = g_pool;
        g_wb[i].size = pool_size;
        g_wb[i].start_sample_offset = i * (int32_t)period;
        g_wb[i].end_sample_offset = (i + 1) * (int32_t)period;
    }
    audrvDeviceSinkAdd(&g_drv, AUDREN_DEFAULT_DEVICE_NAME, 2, sink_channels);
    audrvUpdate(&g_drv);
    if (R_FAILED(audrenStartAudioRenderer())) {
        audren_stop();
        return -5;
    }
    audrvVoiceInit(&g_drv, 0, 2, PcmFormat_Int16, 48000);
    audrvVoiceSetDestinationMix(&g_drv, 0, AUDREN_FINAL_MIX_ID);
    audrvVoiceSetMixFactor(&g_drv, 0, 1.0f, 0, 0);
    audrvVoiceSetMixFactor(&g_drv, 0, 0.0f, 0, 1);
    audrvVoiceSetMixFactor(&g_drv, 0, 0.0f, 1, 0);
    audrvVoiceSetMixFactor(&g_drv, 0, 1.0f, 1, 1);
    audrvVoiceStart(&g_drv, 0);
    g_run = 1;
    g_thread = os_thread_create(pump, NULL, 64 * 1024, 2, OS_THREAD_ROLE(OS_ROLE_AUDIO));  /* above normal: audio must not starve */
    if (!g_thread || os_thread_start(g_thread)) {
        g_run = 0;
        audren_stop();
        return -6;
    }
    return 0;
}

static void audren_stop(void)
{
    g_run = 0;
    if (g_thread) {
        os_thread_join(g_thread);
        os_thread_free(g_thread);
        g_thread = NULL;
    }
    if (g_drv_up) {
        audrvClose(&g_drv);
        g_drv_up = 0;
    }
    if (g_audren_up) {
        audrenExit();
        g_audren_up = 0;
    }
    free(g_pool);
    g_pool = NULL;
    g_pool_id = -1;
}

const fnvnx_audio_backend fnvnx_audio_backend_audren = {"audren", audren_start, audren_stop};
#endif
