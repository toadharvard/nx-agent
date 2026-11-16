/* xinput.cpp - XInput 1.3 over the pad source (input.h) and the Switch -> Xbox mapping.
 *
 * Mapping (docs/AUDIO_INPUT.md "XInput"): face buttons by label (default) or position; L/R -> shoulders,
 * ZL/ZR -> triggers 0/255 (digital on Switch pads), Plus -> START, Minus -> BACK, stick clicks -> thumbs,
 * D-pad -> D-pad, sticks as is (libnx range +-32767, y up like XInput). Joy-Con SL/SR are unmapped.
 * Exactly one controller (user 0, always connected; users 1..3 not connected); the pad source reads index 0 only.
 * Exports: ordinals 2..8 (GetState, SetState, GetCapabilities, Enable, GetDSoundAudioDeviceGuids, GetBatteryInformation,
 * GetKeystroke); the undocumented 100..103 are outside the contract.
 */
#include <xinput.h>
#include "input.h"
#include "../os/os.h"
#include "../include/fnvnx_clock.h"

#include <stdlib.h>
#include <string.h>

static int g_layout = -1;

extern "C" void fnvnx_pad_set_layout(int l) { g_layout = l ? FNVNX_PAD_LAYOUT_POSITIONAL : FNVNX_PAD_LAYOUT_LABEL; }
extern "C" int fnvnx_pad_get_layout(void)
{
    if (g_layout < 0) {
        const char *e = getenv("FNVNX_PAD_LAYOUT");
        g_layout = (e && !strcmp(e, "positional")) ? FNVNX_PAD_LAYOUT_POSITIONAL : FNVNX_PAD_LAYOUT_LABEL;
    }
    return g_layout;
}

static int16_t clamp16(int32_t v) { return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }

extern "C" void fnvnx_pad_map(const fnvnx_pad_raw *r, int layout, fnvnx_xpad *o)
{
    memset(o, 0, sizeof *o);
    if (!r->connected)
        return;
    uint64_t b = r->buttons;
    uint16_t w = 0;
    struct { uint64_t n; uint16_t x; } face_label[] = {
        {FNVNX_NPAD_A, XINPUT_GAMEPAD_A}, {FNVNX_NPAD_B, XINPUT_GAMEPAD_B},
        {FNVNX_NPAD_X, XINPUT_GAMEPAD_X}, {FNVNX_NPAD_Y, XINPUT_GAMEPAD_Y}};
    struct { uint64_t n; uint16_t x; } face_pos[] = {
        {FNVNX_NPAD_B, XINPUT_GAMEPAD_A}, {FNVNX_NPAD_A, XINPUT_GAMEPAD_B},
        {FNVNX_NPAD_Y, XINPUT_GAMEPAD_X}, {FNVNX_NPAD_X, XINPUT_GAMEPAD_Y}};
    for (int i = 0; i < 4; i++) {
        if (layout == FNVNX_PAD_LAYOUT_POSITIONAL ? (b & face_pos[i].n) : (b & face_label[i].n))
            w |= layout == FNVNX_PAD_LAYOUT_POSITIONAL ? face_pos[i].x : face_label[i].x;
    }
    if (b & FNVNX_NPAD_UP) w |= XINPUT_GAMEPAD_DPAD_UP;
    if (b & FNVNX_NPAD_DOWN) w |= XINPUT_GAMEPAD_DPAD_DOWN;
    if (b & FNVNX_NPAD_LEFT) w |= XINPUT_GAMEPAD_DPAD_LEFT;
    if (b & FNVNX_NPAD_RIGHT) w |= XINPUT_GAMEPAD_DPAD_RIGHT;
    if (b & FNVNX_NPAD_PLUS) w |= XINPUT_GAMEPAD_START;
    if (b & FNVNX_NPAD_MINUS) w |= XINPUT_GAMEPAD_BACK;
    if (b & FNVNX_NPAD_STICKL) w |= XINPUT_GAMEPAD_LEFT_THUMB;
    if (b & FNVNX_NPAD_STICKR) w |= XINPUT_GAMEPAD_RIGHT_THUMB;
    if (b & FNVNX_NPAD_L) w |= XINPUT_GAMEPAD_LEFT_SHOULDER;
    if (b & FNVNX_NPAD_R) w |= XINPUT_GAMEPAD_RIGHT_SHOULDER;
    o->buttons = w;
    o->lt = (b & FNVNX_NPAD_ZL) ? 255 : 0;
    o->rt = (b & FNVNX_NPAD_ZR) ? 255 : 0;
    o->lx = clamp16(r->lx);
    o->ly = clamp16(r->ly);
    o->rx = clamp16(r->rx);
    o->ry = clamp16(r->ry);
}

/* ---- host source: injected states */
static os_lock_t g_lock;
static fnvnx_pad_raw g_inject[XUSER_MAX_COUNT];
static float g_vib[XUSER_MAX_COUNT][2];

static int host_read(unsigned i, fnvnx_pad_raw *o)
{
    os_lock(&g_lock);
    *o = g_inject[i];
    os_unlock(&g_lock);
    return o->connected != 0;
}
static void host_vibrate(unsigned i, float lo, float hi)
{
    os_lock(&g_lock);
    g_vib[i][0] = lo;
    g_vib[i][1] = hi;
    os_unlock(&g_lock);
}
static const fnvnx_pad_source g_host_source = {host_read, host_vibrate};

#ifdef __SWITCH__
extern "C" const fnvnx_pad_source *fnvnx_pad_horizon_source(void);
#endif

static const fnvnx_pad_source *g_source;
static const fnvnx_pad_source *source(void)
{
    if (!g_source) {
#ifdef __SWITCH__
        g_source = fnvnx_pad_horizon_source();
#else
        g_source = &g_host_source;
#endif
    }
    return g_source;
}

extern "C" void fnvnx_pad_set_source(const fnvnx_pad_source *s) { g_source = s; }
extern "C" void fnvnx_pad_inject(unsigned i, const fnvnx_pad_raw *r)
{
    if (i >= XUSER_MAX_COUNT) return;
    os_lock(&g_lock);
    g_inject[i] = *r;
    os_unlock(&g_lock);
}
extern "C" void fnvnx_pad_last_vibration(unsigned i, float *lo, float *hi)
{
    if (i >= XUSER_MAX_COUNT) return;
    os_lock(&g_lock);
    *lo = g_vib[i][0];
    *hi = g_vib[i][1];
    os_unlock(&g_lock);
}

/* ---- XInput.  Environment decision: exactly one controller, user 0, always connected (its state is neutral
 * when the pad source reports it absent); users 1..3 -> ERROR_DEVICE_NOT_CONNECTED, > 3 -> ERROR_BAD_ARGUMENTS.
 * All shared state is under g_xlock; source calls are made outside it. */
#ifndef ERROR_EMPTY
#define ERROR_EMPTY 4306L
#endif
static os_lock_t g_xlock;
static int g_enabled = 1;
static fnvnx_xpad g_last;
static uint32_t g_packet;
static float g_rumble[2];                 /* last requested motor amplitudes (restored by XInputEnable(TRUE)) */

/* keystroke tracker: the 32 VK_PAD_* keys derived from the state (SDK thresholds), as a bit set */
static const uint16_t g_vk[32] = {
    VK_PAD_A, VK_PAD_B, VK_PAD_X, VK_PAD_Y, VK_PAD_RSHOULDER, VK_PAD_LSHOULDER, VK_PAD_LTRIGGER, VK_PAD_RTRIGGER,
    VK_PAD_DPAD_UP, VK_PAD_DPAD_DOWN, VK_PAD_DPAD_LEFT, VK_PAD_DPAD_RIGHT, VK_PAD_START, VK_PAD_BACK,
    VK_PAD_LTHUMB_PRESS, VK_PAD_RTHUMB_PRESS,
    0x5820, 0x5821, 0x5822, 0x5823, 0x5824, 0x5825, 0x5826, 0x5827,    /* LTHUMB up down right left ul ur dr dl */
    0x5830, 0x5831, 0x5832, 0x5833, 0x5834, 0x5835, 0x5836, 0x5837};
static const uint16_t g_btn_bits[16] = {
    XINPUT_GAMEPAD_A, XINPUT_GAMEPAD_B, XINPUT_GAMEPAD_X, XINPUT_GAMEPAD_Y, XINPUT_GAMEPAD_RIGHT_SHOULDER,
    XINPUT_GAMEPAD_LEFT_SHOULDER, 0, 0, XINPUT_GAMEPAD_DPAD_UP, XINPUT_GAMEPAD_DPAD_DOWN, XINPUT_GAMEPAD_DPAD_LEFT,
    XINPUT_GAMEPAD_DPAD_RIGHT, XINPUT_GAMEPAD_START, XINPUT_GAMEPAD_BACK, XINPUT_GAMEPAD_LEFT_THUMB,
    XINPUT_GAMEPAD_RIGHT_THUMB};
struct kev { uint16_t vk, flags; };
static kev g_q[128];
static unsigned g_qhead, g_qcount;
static uint32_t g_active;
static uint64_t g_next_repeat[32];
/* ENVIRONMENT CHOICE, not a documented value: XInputGetKeystroke's documentation states no repeat timing. 400 ms
 * before the first repeat and 100 ms between repeats are this layer's own choice (needs an owner decision). */
#define XK_REPEAT_DELAY_NS 400000000ull
#define XK_REPEAT_RATE_NS 100000000ull

static uint32_t stick_keys(int x, int y, int dz, int base)   /* 8-way direction key of a stick, as a bit */
{
    int r = x > dz, l = x < -dz, u = y > dz, d = y < -dz;
    int k = u && l ? 4 : u && r ? 5 : d && r ? 6 : d && l ? 7 : u ? 0 : d ? 1 : r ? 2 : l ? 3 : -1;
    return k < 0 ? 0 : 1u << (base + k);
}
static void kq_push(uint16_t vk, uint16_t fl)   /* g_xlock held */
{
    if (g_qcount == 128) { g_qhead = (g_qhead + 1) % 128; g_qcount--; }
    g_q[(g_qhead + g_qcount++) % 128] = kev{vk, fl};
}
static void track_keys(const fnvnx_xpad &x)   /* g_xlock held */
{
    uint32_t a = 0;
    for (int i = 0; i < 16; i++)
        if (g_btn_bits[i] && (x.buttons & g_btn_bits[i])) a |= 1u << i;
    if (x.lt > XINPUT_GAMEPAD_TRIGGER_THRESHOLD) a |= 1u << 6;
    if (x.rt > XINPUT_GAMEPAD_TRIGGER_THRESHOLD) a |= 1u << 7;
    a |= stick_keys(x.lx, x.ly, XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE, 16);
    a |= stick_keys(x.rx, x.ry, XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE, 24);
    uint64_t now = clk_peek_ns();
    for (int i = 0; i < 32; i++) {
        uint32_t m = 1u << i;
        if ((a & m) && !(g_active & m)) { kq_push(g_vk[i], XINPUT_KEYSTROKE_KEYDOWN); g_next_repeat[i] = now + XK_REPEAT_DELAY_NS; }
        else if (!(a & m) && (g_active & m)) kq_push(g_vk[i], XINPUT_KEYSTROKE_KEYUP);
        else if ((a & m) && now >= g_next_repeat[i]) {
            kq_push(g_vk[i], XINPUT_KEYSTROKE_KEYDOWN | XINPUT_KEYSTROKE_REPEAT);
            g_next_repeat[i] = now + XK_REPEAT_RATE_NS;
        }
    }
    g_active = a;
}

/* reads the pad, applies XInputEnable, advances packet number and keystroke tracker; returns the state */
static fnvnx_xpad xpoll(uint32_t *packet)
{
    fnvnx_input_pump();
    fnvnx_pad_raw raw;
    memset(&raw, 0, sizeof raw);
    source()->read(0, &raw);
    fnvnx_swkbd_filter(&raw.buttons);   /* L3+R3 opens the system keyboard and never reaches the game */
    os_lock(&g_xlock);
    fnvnx_xpad x;
    if (g_enabled)
        fnvnx_pad_map(&raw, fnvnx_pad_get_layout(), &x);
    else
        memset(&x, 0, sizeof x);   /* XInputEnable(FALSE): neutral data */
    if (memcmp(&x, &g_last, sizeof x)) {
        g_last = x;
        g_packet++;
    }
    track_keys(x);
    if (packet) *packet = g_packet;
    os_unlock(&g_xlock);
    return x;
}

/* 0 = user 0; else the Win32 error. any: XUSER_INDEX_ANY allowed (resolves to the one controller) */
static DWORD check_user(DWORD user, int any)
{
    if (any && user == XUSER_INDEX_ANY) return ERROR_SUCCESS;
    if (user >= XUSER_MAX_COUNT) return ERROR_BAD_ARGUMENTS;
    return user ? ERROR_DEVICE_NOT_CONNECTED : ERROR_SUCCESS;
}

extern "C" DWORD WINAPI XInputGetState(DWORD user, XINPUT_STATE *st)
{
    if (user >= XUSER_MAX_COUNT || !st) return ERROR_BAD_ARGUMENTS;
    if (user) return ERROR_DEVICE_NOT_CONNECTED;
    uint32_t pkt;
    fnvnx_xpad x = xpoll(&pkt);
    st->dwPacketNumber = pkt;
    st->Gamepad.wButtons = x.buttons;
    st->Gamepad.bLeftTrigger = x.lt;      /* digital source (ZL/ZR): 0 or 255 */
    st->Gamepad.bRightTrigger = x.rt;
    st->Gamepad.sThumbLX = x.lx;
    st->Gamepad.sThumbLY = x.ly;
    st->Gamepad.sThumbRX = x.rx;
    st->Gamepad.sThumbRY = x.ry;
    return ERROR_SUCCESS;
}

static void apply_rumble(void)   /* g_xlock held by nobody: reads under it, drives outside */
{
    os_lock(&g_xlock);
    float lo = g_enabled ? g_rumble[0] : 0.0f, hi = g_enabled ? g_rumble[1] : 0.0f;
    os_unlock(&g_xlock);
    if (source()->vibrate) source()->vibrate(0, lo, hi);
}

extern "C" DWORD WINAPI XInputSetState(DWORD user, XINPUT_VIBRATION *v)
{
    if (user >= XUSER_MAX_COUNT || !v) return ERROR_BAD_ARGUMENTS;
    if (user) return ERROR_DEVICE_NOT_CONNECTED;
    os_lock(&g_xlock);
    g_rumble[0] = v->wLeftMotorSpeed / 65535.0f;
    g_rumble[1] = v->wRightMotorSpeed / 65535.0f;
    os_unlock(&g_xlock);
    apply_rumble();   /* XInputEnable(FALSE) keeps the motors at 0 but remembers the request */
    return ERROR_SUCCESS;
}

extern "C" DWORD WINAPI XInputGetCapabilities(DWORD user, DWORD flags, XINPUT_CAPABILITIES *c)
{
    if (user >= XUSER_MAX_COUNT && user != XUSER_INDEX_ANY) return ERROR_BAD_ARGUMENTS;
    if (!c || ((uint32_t)flags & ~(uint32_t)XINPUT_FLAG_GAMEPAD)) return ERROR_BAD_ARGUMENTS;
    DWORD e = check_user(user, 1);
    if (e) return e;
    memset(c, 0, sizeof *c);
    c->Type = XINPUT_DEVTYPE_GAMEPAD;
    c->SubType = XINPUT_DEVSUBTYPE_GAMEPAD;
    c->Gamepad.wButtons = 0xF3FF;
    c->Gamepad.bLeftTrigger = c->Gamepad.bRightTrigger = 0xFF;
    c->Gamepad.sThumbLX = c->Gamepad.sThumbLY = c->Gamepad.sThumbRX = c->Gamepad.sThumbRY = (SHORT)0xFFC0;
    c->Vibration.wLeftMotorSpeed = c->Vibration.wRightMotorSpeed = 0xFFFF;
    return ERROR_SUCCESS;
}

extern "C" void WINAPI XInputEnable(BOOL enable)
{
    os_lock(&g_xlock);
    g_enabled = enable ? 1 : 0;
    os_unlock(&g_xlock);
    apply_rumble();
}

extern "C" DWORD WINAPI XInputGetDSoundAudioDeviceGuids(DWORD user, GUID *render, GUID *capture)
{
    if (user >= XUSER_MAX_COUNT || !render || !capture) return ERROR_BAD_ARGUMENTS;
    if (user) return ERROR_DEVICE_NOT_CONNECTED;
    memset(render, 0, sizeof(GUID));   /* no headset: GUID_NULL both */
    memset(capture, 0, sizeof(GUID));
    return ERROR_SUCCESS;
}

extern "C" DWORD WINAPI XInputGetBatteryInformation(DWORD user, BYTE devtype, XINPUT_BATTERY_INFORMATION *b)
{
    if (user >= XUSER_MAX_COUNT || !b || devtype > BATTERY_DEVTYPE_HEADSET) return ERROR_BAD_ARGUMENTS;
    if (user) return ERROR_DEVICE_NOT_CONNECTED;
    b->BatteryType = devtype == BATTERY_DEVTYPE_GAMEPAD ? BATTERY_TYPE_WIRED : BATTERY_TYPE_DISCONNECTED;
    b->BatteryLevel = devtype == BATTERY_DEVTYPE_GAMEPAD ? BATTERY_LEVEL_FULL : BATTERY_LEVEL_EMPTY;
    return ERROR_SUCCESS;
}

extern "C" DWORD WINAPI XInputGetKeystroke(DWORD user, DWORD reserved, XINPUT_KEYSTROKE *ks)
{
    if (!ks || (user >= XUSER_MAX_COUNT && user != XUSER_INDEX_ANY)) return ERROR_BAD_ARGUMENTS;
    if (user != XUSER_INDEX_ANY && user) return ERROR_DEVICE_NOT_CONNECTED;
    xpoll(nullptr);
    os_lock(&g_xlock);
    DWORD r = ERROR_EMPTY;
    if (g_qcount) {
        kev e = g_q[g_qhead];
        g_qhead = (g_qhead + 1) % 128;
        g_qcount--;
        memset(ks, 0, sizeof *ks);
        ks->VirtualKey = e.vk;
        ks->Flags = e.flags;
        r = ERROR_SUCCESS;
    }
    os_unlock(&g_xlock);
    return r;
}
