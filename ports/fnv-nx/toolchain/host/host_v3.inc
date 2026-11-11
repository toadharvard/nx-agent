/* host_v3.inc - FnvnxHostApi version 3 on libnx: audio output (audren) and input (hid) for the guest's
 * platform/audio and platform/input. The Horizon parts of S7 (backend_audren.c, input_horizon.cpp) cannot run
 * in the ILP32 guest; their libnx calls live here, behind fixed-width calls, and the guest glue
 * (toolchain/guest/platform_glue.cpp) feeds the S7 layer. Included by host.c. */

/* ---- audio: one stereo PCM16 voice, a ring of NBUF wave buffers; the guest audio thread renders a period and
 * calls audio_submit, which blocks until a buffer is free (that is the pacing). */
#define HOST_AUDIO_NBUF 4
static AudioDriver g_adrv;
static AudioDriverWaveBuf g_awb[HOST_AUDIO_NBUF];
static void *g_apool;
static u32 g_aperiod;
static int g_aren_up, g_adrv_up;
static Mutex g_alock;

static u64 h_audio_close(void) {
    mutexLock(&g_alock);
    if (g_adrv_up) { audrvClose(&g_adrv); g_adrv_up = 0; }
    if (g_aren_up) { audrenExit(); g_aren_up = 0; }
    free(g_apool);
    g_apool = NULL;
    mutexUnlock(&g_alock);
    return 0;
}

static u64 h_audio_open(u64 rate, u64 period) {
    static const AudioRendererConfig cfg = {
        .output_rate = AudioRendererOutputRate_48kHz, .num_voices = 4, .num_effects = 0, .num_sinks = 1,
        .num_mix_objs = 1, .num_mix_buffers = 2,
    };
    static const u8 sink_channels[2] = {0, 1};
    if (rate != 48000 || !period || period > 8192) return (u64)-1;
    if (g_aren_up) return 0;
    g_aperiod = (u32)period;
    if (R_FAILED(audrenInitialize(&cfg))) return (u64)-2;
    g_aren_up = 1;
    if (R_FAILED(audrvCreate(&g_adrv, &cfg, 2))) { h_audio_close(); return (u64)-3; }
    g_adrv_up = 1;
    size_t pool = ((size_t)HOST_AUDIO_NBUF * period * 4 + AUDREN_MEMPOOL_ALIGNMENT - 1) &
                  ~(size_t)(AUDREN_MEMPOOL_ALIGNMENT - 1);
    if (!(g_apool = memalign(AUDREN_MEMPOOL_ALIGNMENT, pool))) { h_audio_close(); return (u64)-4; }
    memset(g_apool, 0, pool);
    armDCacheFlush(g_apool, pool);
    int id = audrvMemPoolAdd(&g_adrv, g_apool, pool);
    audrvMemPoolAttach(&g_adrv, id);
    for (int i = 0; i < HOST_AUDIO_NBUF; i++) {
        memset(&g_awb[i], 0, sizeof g_awb[i]);
        g_awb[i].data_raw = g_apool;
        g_awb[i].size = pool;
        g_awb[i].start_sample_offset = i * (s32)period;
        g_awb[i].end_sample_offset = (i + 1) * (s32)period;
    }
    audrvDeviceSinkAdd(&g_adrv, AUDREN_DEFAULT_DEVICE_NAME, 2, sink_channels);
    audrvUpdate(&g_adrv);
    if (R_FAILED(audrenStartAudioRenderer())) { h_audio_close(); return (u64)-5; }
    audrvVoiceInit(&g_adrv, 0, 2, PcmFormat_Int16, 48000);
    audrvVoiceSetDestinationMix(&g_adrv, 0, AUDREN_FINAL_MIX_ID);
    audrvVoiceSetMixFactor(&g_adrv, 0, 1.0f, 0, 0);
    audrvVoiceSetMixFactor(&g_adrv, 0, 0.0f, 0, 1);
    audrvVoiceSetMixFactor(&g_adrv, 0, 0.0f, 1, 0);
    audrvVoiceSetMixFactor(&g_adrv, 0, 1.0f, 1, 1);
    audrvVoiceStart(&g_adrv, 0);
    return 0;
}

static u64 h_audio_submit(u64 pcm, u64 frames) {   /* frames == period; interleaved stereo s16 */
    if (!g_adrv_up || frames != g_aperiod) return (u64)-1;
    for (;;) {
        mutexLock(&g_alock);
        for (int i = 0; i < HOST_AUDIO_NBUF; i++) {
            if (g_awb[i].state == AudioDriverWaveBufState_Free || g_awb[i].state == AudioDriverWaveBufState_Done) {
                void *dst = (u8 *)g_apool + (size_t)i * g_aperiod * 4;
                memcpy(dst, (const void *)pcm, (size_t)frames * 4);
                armDCacheFlush(dst, (size_t)frames * 4);
                audrvVoiceAddWaveBuf(&g_adrv, 0, &g_awb[i]);
                if (!audrvVoiceIsPlaying(&g_adrv, 0)) audrvVoiceStart(&g_adrv, 0);
                audrvUpdate(&g_adrv);
                mutexUnlock(&g_alock);
                return 0;
            }
        }
        audrvUpdate(&g_adrv);
        mutexUnlock(&g_alock);
        audrenWaitFrame();
    }
}

/* ---- input */
static Mutex g_ilock;
static int g_iinit;
static PadState g_ipads[4];
static HidKeyboardState g_prev_kbd;
static u32 g_prev_mouse_buttons;
static struct { u32 style; int n; HidVibrationDeviceHandle h[2]; } g_vib[4];

static void input_init_locked(void) {
    if (g_iinit) return;
    padConfigureInput(4, HidNpadStyleSet_NpadStandard);
    padInitialize(&g_ipads[0], HidNpadIdType_No1, HidNpadIdType_Handheld);
    padInitialize(&g_ipads[1], HidNpadIdType_No2);
    padInitialize(&g_ipads[2], HidNpadIdType_No3);
    padInitialize(&g_ipads[3], HidNpadIdType_No4);
    hidInitializeKeyboard();
    hidInitializeMouse();
    hidInitializeTouchScreen();
    memset(&g_prev_kbd, 0, sizeof g_prev_kbd);
    g_iinit = 1;
}

/* fnvnx_pad_raw layout (platform/input/input.h): u32 connected, u64 buttons, s32 lx ly rx ry (32 bytes in
 * both data models). */
typedef struct { u32 connected, pad_; u64 buttons; s32 lx, ly, rx, ry; } HostPadRaw;
_Static_assert(sizeof(HostPadRaw) == 32, "pad raw layout");

static u64 h_pad_read(u64 index, u64 out) {
    if (index > 3) return 0;
    HostPadRaw *o = (HostPadRaw *)out;
    mutexLock(&g_ilock);
    input_init_locked();
    PadState *p = &g_ipads[index];
    padUpdate(p);
    memset(o, 0, sizeof *o);
    o->connected = padIsConnected(p);
    if (o->connected) {
        o->buttons = padGetButtons(p);
        HidAnalogStickState l = padGetStickPos(p, 0), r = padGetStickPos(p, 1);
        o->lx = l.x; o->ly = l.y; o->rx = r.x; o->ry = r.y;
    }
    mutexUnlock(&g_ilock);
    return o->connected != 0;
}

static u64 h_pad_vibrate(u64 index, u64 lo_bits, u64 hi_bits) {
    if (index > 3) return 0;
    float lo, hi;
    u32 lb = (u32)lo_bits, hb = (u32)hi_bits;
    memcpy(&lo, &lb, 4);
    memcpy(&hi, &hb, 4);
    mutexLock(&g_ilock);
    input_init_locked();
    PadState *p = &g_ipads[index];
    u32 style = padGetStyleSet(p);
    if (g_vib[index].style != style) {
        g_vib[index].style = style;
        g_vib[index].n = 0;
        HidNpadIdType id = (index == 0 && padIsHandheld(p)) ? HidNpadIdType_Handheld : (HidNpadIdType)(HidNpadIdType_No1 + index);
        HidNpadStyleTag tag = (style & HidNpadStyleTag_NpadHandheld) ? HidNpadStyleTag_NpadHandheld
                            : (style & HidNpadStyleTag_NpadJoyDual) ? HidNpadStyleTag_NpadJoyDual
                            : HidNpadStyleTag_NpadFullKey;
        int n = tag == HidNpadStyleTag_NpadFullKey ? 1 : 2;
        if (style && R_SUCCEEDED(hidInitializeVibrationDevices(g_vib[index].h, n, id, tag))) g_vib[index].n = n;
    }
    if (g_vib[index].n) {
        HidVibrationValue v[2];
        for (int k = 0; k < 2; k++) {
            v[k].amp_low = lo; v[k].freq_low = 160.0f;
            v[k].amp_high = hi; v[k].freq_high = 320.0f;
        }
        hidSendVibrationValues(g_vib[index].h, v, g_vib[index].n);
    }
    mutexUnlock(&g_ilock);
    return 0;
}

static u64 h_input_poll(u64 out, u64 cap) {
    FnvnxInputEvent *e = (FnvnxInputEvent *)out;
    u64 n = 0;
#define PUSH(t, c, x, y, z) do { if (n < cap) { e[n] = (FnvnxInputEvent){(t), (c), (x), (y), (z), 0}; n++; } } while (0)
    mutexLock(&g_ilock);
    input_init_locked();
    HidKeyboardState k;
    if (hidGetKeyboardStates(&k, 1)) {
        for (int u = 4; u < 232; u++) {
            bool now = hidKeyboardStateGetKey(&k, (HidKeyboardKey)u), was = hidKeyboardStateGetKey(&g_prev_kbd, (HidKeyboardKey)u);
            if (now != was) PUSH(FNVNX_EV_KEY, (u32)u, now, 0, 0);
        }
        g_prev_kbd = k;
    }
    HidMouseState m;
    if (hidGetMouseStates(&m, 1) && (m.attributes & HidMouseAttribute_IsConnected)) {
        if (m.delta_x || m.delta_y || m.wheel_delta_y) PUSH(FNVNX_EV_MOUSE_MOVE, 0, m.delta_x, m.delta_y, m.wheel_delta_y);
        for (u32 b = 0; b < 5; b++) {
            bool now = (m.buttons >> b) & 1, was = (g_prev_mouse_buttons >> b) & 1;
            if (now != was) PUSH(FNVNX_EV_MOUSE_BUTTON, b, now, 0, 0);
        }
        g_prev_mouse_buttons = m.buttons;
    }
    HidTouchScreenState ts;
    if (hidGetTouchScreenStates(&ts, 1)) {
        u32 c = ts.count < 0 ? 0 : (u32)ts.count > 16 ? 16 : (u32)ts.count;
        for (u32 i = 0; i < c; i++) PUSH(FNVNX_EV_TOUCH, ts.touches[i].finger_id, (s32)ts.touches[i].x, (s32)ts.touches[i].y, 0);
        PUSH(FNVNX_EV_TOUCH_END, c, 0, 0, 0);
    }
    mutexUnlock(&g_ilock);
#undef PUSH
    return n;
}

static u64 h_map_fixed(u64 addr, u64 size, u64 src) {
    /* inside a range S10 claimed at startup (the data window): zero it, nothing to map */
    if (fnvnx_reserve_map_fixed && fnvnx_reserve_contains && fnvnx_reserve_contains(addr, size))
        return fnvnx_reserve_map_fixed(addr, size) == 0 ? 0 : MAKERESULT(Module_Libnx, LibnxError_BadInput);
    if (!src || ((addr | size | src) & 0xFFF)) return MAKERESULT(Module_Libnx, LibnxError_BadInput);
    MemoryInfo mi; u32 pi;
    if (R_FAILED(svcQueryMemory(&mi, &pi, addr)) || mi.type != MemType_Unmapped || mi.addr + mi.size < addr + size)
        return MAKERESULT(Module_Libnx, LibnxError_AlreadyInitialized);   /* occupied (random ASLR placement) */
    logf_("fnvnx host: map_fixed %#010lx..%#010lx from %#010lx\n", addr, addr + size, src);
    return svcMapMemory((void *)addr, (void *)src, size);
}

/* FNVNX_PLAT_UNMAP_FIXED (addr, size, src): the undo of h_map_fixed, see hostapi.h. The kernel checks that [addr, addr+size)
 * is the mapping of exactly [src, src+size) (svcUnmapMemory fails otherwise and changes nothing), so the host keeps no table. */
static u64 h_unmap_fixed(u64 addr, u64 size, u64 src) {
    if ((addr | src) & 0xFFF) return 0xCC01;
    if (!size || (size & 0xFFF)) return 0xCA01;
    if (fnvnx_reserve_map_fixed && fnvnx_reserve_contains && fnvnx_reserve_contains(addr, size))   /* map_fixed only zeroed it */
        return fnvnx_reserve_map_fixed(addr, size) == 0 ? 0 : MAKERESULT(Module_Libnx, LibnxError_BadInput);
    logf_("fnvnx host: unmap_fixed %#010lx..%#010lx to %#010lx\n", addr, addr + size, src);
    return svcUnmapMemory((void *)addr, (void *)src, size);
}

static fnvnx_ext_handler g_ext[64];
int fnvnx_host_register_ext(uint32_t module, fnvnx_ext_handler handler) {
    if (module >= 64 || g_ext[module]) return -1;
    g_ext[module] = handler;
    return 0;
}
static u64 h_ext_call(u64 module, u64 fn, u64 args, u64 nargs) {
    if (module >= 64 || !g_ext[module]) return FNVNX_EXT_NO_MODULE;
    return g_ext[module]((uint32_t)fn, (const uint64_t *)args, (uint32_t)nargs);
}

#define FNVNX_HOSTAPI_V3_SLOTS                                                                             \
    .audio_open = (u64)h_audio_open, .audio_submit = (u64)h_audio_submit, .audio_close = (u64)h_audio_close, \
    .pad_read = (u64)h_pad_read, .pad_vibrate = (u64)h_pad_vibrate, .input_poll = (u64)h_input_poll,      \
    .map_fixed = (u64)h_map_fixed, .ext_call = (u64)h_ext_call,
