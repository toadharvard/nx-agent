/* platform_glue.cpp - S7's Horizon glue for the ILP32 guest build of platform/audio and platform/input.
 *
 * The libnx parts of S7 (backend_audren.c, input_horizon.cpp) run on the LP64 host (toolchain/host/
 * host_v3.inc). In the guest build those two files are left out and this file provides what they provided:
 *   - fnvnx_audio_backend_audren: a guest thread renders mixer periods and hands them to the host's audren
 *     voice (FnvnxHostApi audio_submit blocks, which paces the thread). platform/audio/backend.cpp is compiled
 *     with -D__SWITCH__ so it selects this backend by default, as on the Switch.
 *   - fnvnx_pad_horizon_source(): pads read through the host; fnvnx_input_pump(): keyboard/mouse/touch events
 *     polled from the host and fed to the S7 input layer.
 *   - fnvnx_guest_platform_init(): installs the pad source (xinput.cpp is compiled without __SWITCH__, so tests
 *     can still inject pads).
 */
#include <stdint.h>
#include <string.h>

#include "../audio/backend.h"
#include "../audio/mixer.h"
#include "../input/input.h"
#include "fnvnx_render_hooks.h"
#include "../os/os.h"
#include "../include/fnvnx_clock.h"
#include "fnvnx/hostapi.h"

extern "C" const fnvnx_audio_backend fnvnx_audio_backend_audren;

/* ---- audio */
static os_thread *g_athread;
static volatile int g_arun;
static uint32_t g_aperiod;

static void audio_pump(void *) {
    static int16_t buf[2 * 8192];
    while (g_arun) {
        mixer_render_s16(buf, g_aperiod);
        fnvnx_hcall2(fnvnx_host->audio_submit, FNVNX_ADDR(buf), g_aperiod);
    }
}

static int host_audio_start(const char *, uint32_t rate, uint32_t period) {
    if (period > 8192) return -1;
    if ((int64_t)fnvnx_hcall2(fnvnx_host->audio_open, rate, period) != 0) return -2;
    g_aperiod = period;
    g_arun = 1;
    g_athread = os_thread_create(audio_pump, nullptr, 64 * 1024, 2, -1);   /* above normal, as S7's */
    if (!g_athread || os_thread_start(g_athread)) {
        g_arun = 0;
        fnvnx_hcall0(fnvnx_host->audio_close);
        return -3;
    }
    return 0;
}

static void host_audio_stop(void) {
    g_arun = 0;
    if (g_athread) {
        os_thread_join(g_athread);
        os_thread_free(g_athread);
        g_athread = nullptr;
    }
    fnvnx_hcall0(fnvnx_host->audio_close);
}

extern "C" const fnvnx_audio_backend fnvnx_audio_backend_audren = {"audren", host_audio_start, host_audio_stop};

/* ---- pads */
static_assert(sizeof(fnvnx_pad_raw) == 32, "fnvnx_pad_raw must match the host's layout");

static int host_pad_read(unsigned i, fnvnx_pad_raw *o) {
    return (int)fnvnx_hcall2(fnvnx_host->pad_read, i, FNVNX_ADDR(o));
}
static void host_pad_vibrate(unsigned i, float lo, float hi) {
    uint32_t a, b;
    memcpy(&a, &lo, 4);
    memcpy(&b, &hi, 4);
    fnvnx_hcall3(fnvnx_host->pad_vibrate, i, a, b);
}
static const fnvnx_pad_source g_pad_src = {host_pad_read, host_pad_vibrate};
extern "C" const fnvnx_pad_source *fnvnx_pad_horizon_source(void) { return &g_pad_src; }

/* ---- keyboard / mouse / touch */
static os_lock_t g_ilock;
static uint64_t g_last_pump;

extern "C" void fnvnx_input_pump(void) {
    uint64_t now = clk_rt_ns();
    os_lock(&g_ilock);
    if (g_last_pump && now - g_last_pump < 1000000u) { os_unlock(&g_ilock); return; }   /* <= once per ms */
    g_last_pump = now;
    FnvnxInputEvent ev[64];
    fnvnx_touch_point pts[16];
    unsigned npts = 0;
    uint64_t n = fnvnx_hcall2(fnvnx_host->input_poll, FNVNX_ADDR(ev), 64);
    for (uint64_t i = 0; i < n && i < 64; i++) {
        const FnvnxInputEvent &e = ev[i];
        switch (e.type) {
            case FNVNX_EV_KEY:
                if (uint8_t dik = fnvnx_hid_usage_to_dik((uint8_t)e.code)) fnvnx_input_key(dik, e.a);
                break;
            case FNVNX_EV_MOUSE_MOVE: fnvnx_input_mouse_move(e.a, e.b, e.c); break;
            case FNVNX_EV_MOUSE_BUTTON: fnvnx_input_mouse_button(e.code, e.a); break;
            case FNVNX_EV_TOUCH:
                if (npts < 16) pts[npts++] = fnvnx_touch_point{e.a, e.b, e.code};
                break;
            case FNVNX_EV_TOUCH_END:
                fnvnx_touch_frame(pts, npts, now / 1000000u);
                npts = 0;
                break;
        }
    }
    os_unlock(&g_ilock);
}

extern "C" void fnvnx_guest_platform_init(void) {
    fnvnx_pad_set_source(fnvnx_pad_horizon_source());
}

/* ---- GPU backend: the product side of nxd3d_gpu_call (shim/include/fnvnx_render_hooks.h). The call is the host
 * extension call of module FNVNX_EXT_GPU with the guest address of the caller's argument words; the result is the
 * host's uint64_t unchanged. A host without the ext_call slot has no module: FNVNX_EXT_NO_MODULE, as the host's own
 * answer for an unregistered module. */
uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t *args, uint32_t nargs) {
    if (!fnvnx_host->ext_call) return FNVNX_EXT_NO_MODULE;
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_GPU, fn, FNVNX_ADDR(args), nargs);
}
