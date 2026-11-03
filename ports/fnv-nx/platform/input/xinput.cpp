/* xinput.cpp - XInput 1.3 over the pad source (input.h) and the Switch -> Xbox mapping.
 *
 * Mapping (docs/AUDIO_INPUT.md "XInput"): face buttons by label (default) or position; L/R -> shoulders,
 * ZL/ZR -> triggers 0/255 (digital on Switch pads), Plus -> START, Minus -> BACK, stick clicks -> thumbs,
 * D-pad -> D-pad, sticks as is (libnx range +-32767, y up like XInput). Joy-Con SL/SR are unmapped.
 * Player 1 is Handheld or Npad No1, players 2..4 are No2..No4 (input_horizon.cpp).
 */
#include <xinput.h>
#include "input.h"
#include "../os/os.h"

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

/* ---- XInput */
static int g_enabled = 1;
static fnvnx_xpad g_last[XUSER_MAX_COUNT];
static uint32_t g_packet[XUSER_MAX_COUNT];

extern "C" DWORD WINAPI XInputGetState(DWORD user, XINPUT_STATE *st)
{
    uint32_t i = (uint32_t)user;
    if (i >= XUSER_MAX_COUNT || !st) return ERROR_BAD_ARGUMENTS;
    fnvnx_input_pump();
    fnvnx_pad_raw raw;
    memset(&raw, 0, sizeof raw);
    if (!source()->read(i, &raw)) return ERROR_DEVICE_NOT_CONNECTED;
    fnvnx_xpad x;
    if (g_enabled)
        fnvnx_pad_map(&raw, fnvnx_pad_get_layout(), &x);
    else
        memset(&x, 0, sizeof x);   /* XInputEnable(FALSE): neutral data */
    if (memcmp(&x, &g_last[i], sizeof x)) {
        g_last[i] = x;
        g_packet[i]++;
    }
    st->dwPacketNumber = g_packet[i];
    st->Gamepad.wButtons = x.buttons;
    st->Gamepad.bLeftTrigger = x.lt;
    st->Gamepad.bRightTrigger = x.rt;
    st->Gamepad.sThumbLX = x.lx;
    st->Gamepad.sThumbLY = x.ly;
    st->Gamepad.sThumbRX = x.rx;
    st->Gamepad.sThumbRY = x.ry;
    return ERROR_SUCCESS;
}

extern "C" DWORD WINAPI XInputSetState(DWORD user, XINPUT_VIBRATION *v)
{
    uint32_t i = (uint32_t)user;
    if (i >= XUSER_MAX_COUNT || !v) return ERROR_BAD_ARGUMENTS;
    fnvnx_pad_raw raw;
    memset(&raw, 0, sizeof raw);
    if (!source()->read(i, &raw)) return ERROR_DEVICE_NOT_CONNECTED;
    if (g_enabled && source()->vibrate)
        source()->vibrate(i, v->wLeftMotorSpeed / 65535.0f, v->wRightMotorSpeed / 65535.0f);
    return ERROR_SUCCESS;
}

extern "C" DWORD WINAPI XInputGetCapabilities(DWORD user, DWORD flags, XINPUT_CAPABILITIES *c)
{
    uint32_t i = (uint32_t)user;
    if (i >= XUSER_MAX_COUNT || !c || ((uint32_t)flags & ~(uint32_t)XINPUT_FLAG_GAMEPAD)) return ERROR_BAD_ARGUMENTS;
    fnvnx_pad_raw raw;
    memset(&raw, 0, sizeof raw);
    if (!source()->read(i, &raw)) return ERROR_DEVICE_NOT_CONNECTED;
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
    g_enabled = enable ? 1 : 0;
    if (!g_enabled && source()->vibrate)
        for (unsigned i = 0; i < XUSER_MAX_COUNT; i++)
            source()->vibrate(i, 0.0f, 0.0f);
}
