/* host_v3_test.c - the device side of audio and input (toolchain/host/host_v3.inc) executed under qemu-aarch64 with
 * libnx's audren / audrv / hid / pad calls stubbed. The guest's DirectSound mixer, DirectShow music and XInput /
 * DirectInput layers reach the console only through these calls (platform/guest/platform_glue.cpp), so this checks
 * what reaches libnx:
 *   audio  48 kHz stereo PCM16 voice with an identity mix to the default sink; each submitted period lands in its
 *          own wave buffer in submission order, and a full ring blocks in audrenWaitFrame until a buffer is done;
 *          close tears audrv and audren down
 *   pads   players 1..4 (No1 + Handheld, No2..No4), buttons and both sticks as libnx reports them, disconnected
 *          pads zeroed, vibration amplitudes as float bits to the right vibration devices (two motors for handheld
 *          and dual Joy-Con, one for a Pro Controller, re-initialised when the style changes)
 *   events keyboard edges as HID usages, mouse motion and buttons, touch frames, and the caller's capacity
 *   ext    the FNVNX_EXT_* registry and dispatch (host_ext.inc). map_fixed / UNMAP_FIXED are in host_vm.inc: host_prot_test.c. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>
#include <switch.h>
#include "fnvnx/hostapi.h"

/* ---- host.c's surroundings */
static void logf_(const char *fmt, ...) { (void)fmt; }
void mutexLock(Mutex *m) { (void)m; }
void mutexUnlock(Mutex *m) { (void)m; }
void armDCacheFlush(void *addr, size_t size) { (void)addr; (void)size; }

/* ---- audren / audrv */
static int g_aren_init, g_aren_exit, g_aren_started, g_drv_closed, g_sinks, g_waits, g_voice_rate, g_voice_ch;
static PcmFormat g_voice_fmt;
static AudioDriverWaveBuf *g_queue[64];
static int g_nqueue;
static int16_t g_played[64][2 * 256];   /* snapshot of each buffer's samples when it was queued */
static int g_nplayed;

Result audrenInitialize(const AudioRendererConfig *c) { (void)c; g_aren_init++; return 0; }
void audrenExit(void) { g_aren_exit++; }
Result audrenStartAudioRenderer(void) { g_aren_started++; return 0; }
void audrenWaitFrame(void)   /* the renderer finishes the oldest queued buffer */
{
    g_waits++;
    if (g_nqueue) {
        g_queue[0]->state = AudioDriverWaveBufState_Done;
        memmove(g_queue, g_queue + 1, (size_t)--g_nqueue * sizeof g_queue[0]);
    }
}
Result audrvCreate(AudioDriver *d, const AudioRendererConfig *c, int n)
{
    (void)n;
    memset(d, 0, sizeof *d);
    d->config = *c;
    d->in_voices = calloc(c->num_voices, sizeof *d->in_voices);
    d->in_channels = calloc(8, sizeof *d->in_channels);
    return 0;
}
void audrvClose(AudioDriver *d) { free(d->in_voices); free(d->in_channels); g_drv_closed++; }
Result audrvUpdate(AudioDriver *d) { (void)d; return 0; }
int audrvMemPoolAdd(AudioDriver *d, void *b, size_t s) { (void)d; (void)b; (void)s; return 0; }
bool audrvMemPoolAttach(AudioDriver *d, int id) { (void)d; (void)id; return true; }
int audrvDeviceSinkAdd(AudioDriver *d, const char *n, int ch, const u8 *ids)
{
    (void)d; (void)ids;
    if (!strcmp(n, AUDREN_DEFAULT_DEVICE_NAME) && ch == 2) g_sinks++;
    return 0;
}
bool audrvVoiceInit(AudioDriver *d, int id, int ch, PcmFormat f, int rate)
{
    d->in_voices[id].channel_ids[0] = 0;
    d->in_voices[id].channel_ids[1] = 1;
    g_voice_ch = ch; g_voice_fmt = f; g_voice_rate = rate;
    return true;
}
bool audrvVoiceIsPlaying(AudioDriver *d, int id) { return d->in_voices[id].state == AudioRendererVoicePlayState_Started; }
bool audrvVoiceAddWaveBuf(AudioDriver *d, int id, AudioDriverWaveBuf *w)
{
    (void)d; (void)id;
    w->state = AudioDriverWaveBufState_Queued;
    g_queue[g_nqueue++] = w;
    const int16_t *s = (const int16_t *)w->data_raw + 2 * w->start_sample_offset;
    memcpy(g_played[g_nplayed++], s, (size_t)(w->end_sample_offset - w->start_sample_offset) * 4);
    return true;
}

/* ---- hid / pad */
static u64 g_masks[4];
static int g_npads;
static struct { u32 style, attr; u64 buttons; HidAnalogStickState l, r; bool handheld; } g_pad_now[4];
static HidKeyboardState g_kbd;
static int g_kbd_ok;
static HidMouseState g_mouse;
static HidTouchScreenState g_touch;
static int g_touch_ok;
static HidVibrationDeviceHandle g_next_handle;
static int g_vib_inits;
static HidNpadIdType g_vib_id;
static HidNpadStyleTag g_vib_tag;
static HidVibrationValue g_vib_sent[2];
static int g_vib_count, g_vib_sends;

void padConfigureInput(u32 max, u32 styles) { (void)max; (void)styles; }
void padInitializeWithMask(PadState *p, u64 mask)
{
    memset(p, 0, sizeof *p);
    p->id_mask = (u8)mask;
    p->read_handheld = (mask >> HidNpadIdType_Handheld) & 1;
    if (g_npads < 4) g_masks[g_npads++] = mask;
}
void padUpdate(PadState *p)
{
    int i = p->id_mask & 1 ? 0 : p->id_mask & 2 ? 1 : p->id_mask & 4 ? 2 : 3;
    p->style_set = g_pad_now[i].style;
    p->attributes = g_pad_now[i].attr;
    p->buttons_old = p->buttons_cur;
    p->buttons_cur = g_pad_now[i].buttons;
    p->sticks[0] = g_pad_now[i].l;
    p->sticks[1] = g_pad_now[i].r;
    p->active_handheld = g_pad_now[i].handheld;
    p->active_id_mask = g_pad_now[i].handheld ? 0 : p->id_mask & 0xF;
}
void hidInitializeKeyboard(void) {}
void hidInitializeMouse(void) {}
void hidInitializeTouchScreen(void) {}
size_t hidGetKeyboardStates(HidKeyboardState *s, size_t n) { (void)n; *s = g_kbd; return g_kbd_ok; }
size_t hidGetMouseStates(HidMouseState *s, size_t n) { (void)n; *s = g_mouse; return 1; }
size_t hidGetTouchScreenStates(HidTouchScreenState *s, size_t n) { (void)n; *s = g_touch; return g_touch_ok; }
Result hidInitializeVibrationDevices(HidVibrationDeviceHandle *h, s32 n, HidNpadIdType id, HidNpadStyleTag tag)
{
    for (s32 i = 0; i < n; i++) { h[i] = g_next_handle; h[i].type_value += 1 + i; }
    g_vib_inits++; g_vib_id = id; g_vib_tag = tag;
    return 0;
}
Result hidSendVibrationValues(const HidVibrationDeviceHandle *h, const HidVibrationValue *v, s32 n)
{
    (void)h;
    g_vib_count = n;
    memcpy(g_vib_sent, v, (size_t)n * sizeof *v);
    g_vib_sends++;
    return 0;
}

#include "../../../toolchain/host/host_ext.inc"
#include "../../../toolchain/host/host_v3.inc"

static int g_fail, g_checks;
#define T(c) do { g_checks++; if (!(c)) { g_fail++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)

static u64 fbits(float f) { u32 b; memcpy(&b, &f, 4); return b; }

static void test_audio(void)
{
    enum { P = 256 };
    T(h_audio_open(44100, P) == (u64)-1);   /* the guest mixer resamples to 48 kHz */
    T(h_audio_open(48000, 9000) == (u64)-1);
    T(h_audio_open(48000, P) == 0);
    T(g_aren_init == 1 && g_aren_started == 1 && g_sinks == 1);
    T(g_voice_ch == 2 && g_voice_fmt == PcmFormat_Int16 && g_voice_rate == 48000);
    T(g_adrv.in_voices[0].dest_mix_id == AUDREN_FINAL_MIX_ID);
    T(g_adrv.in_channels[0].mix[0] == 1.0f && g_adrv.in_channels[0].mix[1] == 0.0f);   /* L -> L, R -> R */
    T(g_adrv.in_channels[1].mix[0] == 0.0f && g_adrv.in_channels[1].mix[1] == 1.0f);
    T(audrvVoiceIsPlaying(&g_adrv, 0));
    T(h_audio_open(48000, P) == 0 && g_aren_init == 1);   /* already open */

    static int16_t pcm[2 * P];
    T(h_audio_submit((u64)(uintptr_t)pcm, P - 1) == (u64)-1);
    for (int k = 0; k < 7; k++) {   /* 4 buffers in the ring: periods 5..7 wait for the renderer */
        for (int i = 0; i < 2 * P; i++) pcm[i] = (int16_t)(k * 1000 + i);
        T(h_audio_submit((u64)(uintptr_t)pcm, P) == 0);
    }
    T(g_nplayed == 7 && g_waits == 3);
    for (int k = 0; k < 7; k++)
        T(g_played[k][0] == k * 1000 && g_played[k][2 * P - 1] == k * 1000 + 2 * P - 1);
    T(h_audio_close() == 0 && g_drv_closed == 1 && g_aren_exit == 1);
    T(h_audio_submit((u64)(uintptr_t)pcm, P) == (u64)-1);
}

static void test_pads(void)
{
    HostPadRaw raw;
    T(h_pad_read(4, (u64)(uintptr_t)&raw) == 0);
    g_pad_now[0].style = HidNpadStyleTag_NpadHandheld;
    g_pad_now[0].attr = HidNpadAttribute_IsConnected;
    g_pad_now[0].handheld = true;
    g_pad_now[0].buttons = HidNpadButton_A | HidNpadButton_ZR | HidNpadButton_StickL;
    g_pad_now[0].l = (HidAnalogStickState){-32767, 12000};
    g_pad_now[0].r = (HidAnalogStickState){500, -32767};
    T(h_pad_read(0, (u64)(uintptr_t)&raw) == 1);
    T(g_npads == 4);
    T(g_masks[0] == (1ull << HidNpadIdType_No1 | 1ull << HidNpadIdType_Handheld));
    T(g_masks[1] == 1ull << HidNpadIdType_No2 && g_masks[3] == 1ull << HidNpadIdType_No4);
    T(raw.connected == 1 && raw.buttons == (HidNpadButton_A | HidNpadButton_ZR | HidNpadButton_StickL));
    T(raw.lx == -32767 && raw.ly == 12000 && raw.rx == 500 && raw.ry == -32767);
    memset(&raw, 0x55, sizeof raw);
    T(h_pad_read(1, (u64)(uintptr_t)&raw) == 0);   /* nothing on No2 */
    T(raw.connected == 0 && raw.buttons == 0 && raw.lx == 0 && raw.ry == 0);

    /* rumble: handheld = two motors (left and right Joy-Con) on the Handheld id */
    T(h_pad_vibrate(0, fbits(0.25f), fbits(0.75f)) == 0);
    T(g_vib_inits == 1 && g_vib_id == HidNpadIdType_Handheld && g_vib_tag == HidNpadStyleTag_NpadHandheld);
    T(g_vib_sends == 1 && g_vib_count == 2);
    T(g_vib_sent[0].amp_low == 0.25f && g_vib_sent[0].amp_high == 0.75f && g_vib_sent[1].amp_high == 0.75f);
    T(g_vib_sent[0].freq_low == 160.0f && g_vib_sent[0].freq_high == 320.0f);
    T(h_pad_vibrate(0, fbits(0.0f), fbits(0.0f)) == 0);   /* XInputSetState(0, 0): stop, same devices */
    T(g_vib_inits == 1 && g_vib_sends == 2 && g_vib_sent[0].amp_low == 0.0f && g_vib_sent[1].amp_high == 0.0f);

    /* a Pro Controller on player 2: one device on No2 */
    g_pad_now[1].style = HidNpadStyleTag_NpadFullKey;
    g_pad_now[1].attr = HidNpadAttribute_IsConnected;
    T(h_pad_read(1, (u64)(uintptr_t)&raw) == 1);
    T(h_pad_vibrate(1, fbits(1.0f), fbits(0.5f)) == 0);
    T(g_vib_inits == 2 && g_vib_id == HidNpadIdType_No2 && g_vib_tag == HidNpadStyleTag_NpadFullKey && g_vib_count == 1);
    T(g_vib_sent[0].amp_low == 1.0f && g_vib_sent[0].amp_high == 0.5f);
    /* player 2 switches to dual Joy-Con: the devices are set up again */
    g_pad_now[1].style = HidNpadStyleTag_NpadJoyDual;
    T(h_pad_read(1, (u64)(uintptr_t)&raw) == 1);
    T(h_pad_vibrate(1, fbits(0.5f), fbits(0.5f)) == 0);
    T(g_vib_inits == 3 && g_vib_tag == HidNpadStyleTag_NpadJoyDual && g_vib_count == 2);
    /* nothing on player 3: no device, nothing sent */
    int sends = g_vib_sends;
    T(h_pad_vibrate(2, fbits(1.0f), fbits(1.0f)) == 0 && g_vib_sends == sends);
    T(h_pad_vibrate(7, fbits(1.0f), fbits(1.0f)) == 0 && g_vib_sends == sends);
}

static void test_events(void)
{
    FnvnxInputEvent ev[64];
    g_kbd_ok = 1;
    g_kbd.keys[HidKeyboardKey_W / 64] |= 1ull << (HidKeyboardKey_W % 64);
    g_kbd.keys[HidKeyboardKey_Escape / 64] |= 1ull << (HidKeyboardKey_Escape % 64);
    g_mouse.attributes = HidMouseAttribute_IsConnected;
    g_mouse.delta_x = 7; g_mouse.delta_y = -3; g_mouse.wheel_delta_y = 120;
    g_mouse.buttons = HidMouseButton_Left;
    g_touch_ok = 1;
    g_touch.count = 2;
    g_touch.touches[0].finger_id = 0; g_touch.touches[0].x = 100; g_touch.touches[0].y = 200;
    g_touch.touches[1].finger_id = 1; g_touch.touches[1].x = 1279; g_touch.touches[1].y = 719;
    u64 n = h_input_poll((u64)(uintptr_t)ev, 64);
    T(n == 7);
    int keys = 0, w = 0, esc = 0;
    for (u64 i = 0; i < n; i++)
        if (ev[i].type == FNVNX_EV_KEY) { keys++; w |= ev[i].code == HidKeyboardKey_W && ev[i].a == 1; esc |= ev[i].code == HidKeyboardKey_Escape && ev[i].a == 1; }
    T(keys == 2 && w && esc);
    T(ev[2].type == FNVNX_EV_MOUSE_MOVE && ev[2].a == 7 && ev[2].b == -3 && ev[2].c == 120);
    T(ev[3].type == FNVNX_EV_MOUSE_BUTTON && ev[3].code == 0 && ev[3].a == 1);
    T(ev[4].type == FNVNX_EV_TOUCH && ev[4].code == 0 && ev[4].a == 100 && ev[4].b == 200);
    T(ev[5].type == FNVNX_EV_TOUCH && ev[5].code == 1 && ev[5].a == 1279 && ev[5].b == 719);
    T(ev[6].type == FNVNX_EV_TOUCH_END && ev[6].code == 2);

    /* next poll: W released, mouse still, button released, no touch; only the edges come */
    g_kbd.keys[HidKeyboardKey_W / 64] &= ~(1ull << (HidKeyboardKey_W % 64));
    g_mouse.delta_x = g_mouse.delta_y = g_mouse.wheel_delta_y = 0;
    g_mouse.buttons = 0;
    g_touch.count = 0;
    n = h_input_poll((u64)(uintptr_t)ev, 64);
    T(n == 3);
    T(ev[0].type == FNVNX_EV_KEY && ev[0].code == HidKeyboardKey_W && ev[0].a == 0);
    T(ev[1].type == FNVNX_EV_MOUSE_BUTTON && ev[1].a == 0);
    T(ev[2].type == FNVNX_EV_TOUCH_END && ev[2].code == 0);

    /* the capacity bounds what is written */
    g_kbd.keys[HidKeyboardKey_A / 64] |= 1ull << (HidKeyboardKey_A % 64);
    g_kbd.keys[HidKeyboardKey_S / 64] |= 1ull << (HidKeyboardKey_S % 64);
    memset(ev, 0xAB, sizeof ev);
    n = h_input_poll((u64)(uintptr_t)ev, 1);
    T(n == 1 && ev[0].type == FNVNX_EV_KEY && ev[1].type == 0xABABABABu);
}

static uint64_t ext_echo(uint32_t fn, const uint64_t *a, uint32_t n) { return fn == 3 && n >= 2 ? a[0] + a[1] : 0; }

/* the ext_call dispatch the guest's FNVNX_EXT_PLATFORM calls go through, and map_fixed's argument checks */
static void test_ext_and_map(void)
{
    uint64_t args[2] = {40, 2};
    T(h_ext_call(5, 3, (u64)(uintptr_t)args, 2) == FNVNX_EXT_NO_MODULE);
    T(fnvnx_host_register_ext(5, ext_echo) == 0 && fnvnx_host_register_ext(5, ext_echo) == -1);
    T(fnvnx_host_register_ext(64, ext_echo) == -1);
    T(h_ext_call(5, 3, (u64)(uintptr_t)args, 2) == 42 && h_ext_call(64, 3, 0, 0) == FNVNX_EXT_NO_MODULE);
    /* ext_call: every refusal has its own value; a handler is never called for a fn it cannot be given */
    T(fnvnx_host_register_ext(6, NULL) == -1 && h_ext_call(6, 3, (u64)(uintptr_t)args, 2) == FNVNX_EXT_NO_MODULE);   /* a null handler registers nothing */
    T(h_ext_call(5, 0x100000003ull, (u64)(uintptr_t)args, 2) == FNVNX_EXT_NO_SERVICE);   /* fn above 32 bits does not wrap to 3 */
    T(h_ext_call(5, 3, (u64)(uintptr_t)args, 0x100000002ull) == 42);                      /* nargs above 32 bits is saturated, not wrapped to 2 */
    T(h_ext_call(63, 1, 0, 0) == FNVNX_EXT_NO_MODULE && h_ext_call(~0ull, 1, 0, 0) == FNVNX_EXT_NO_MODULE);
}

int main(void)
{
    test_audio();
    test_pads();
    test_events();
    test_ext_and_map();
    printf("host_v3_test: %d checks, %d failures\n", g_checks, g_fail);
    return g_fail != 0;
}
