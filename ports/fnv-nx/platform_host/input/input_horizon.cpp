/* input_horizon.cpp - libnx hid sources: Npad -> pad source (players 1..4), vibration, USB keyboard and mouse,
 * touch screen -> touch-as-mouse. Built only against libnx (host builds and the guest link leave it out; they define
 * fnvnx_input_pump themselves). */
#include "../../platform/input/input.h"
#include "../../platform/os/os.h"
#include "fnvnx_clock.h"

#include <switch.h>
#include <string.h>

static os_lock_t g_lock;
static int g_init;
static PadState g_pads[4];
static uint64_t g_last_pump;
static HidKeyboardState g_prev_kbd;
static uint32_t g_prev_mouse_buttons;

struct vib {
    uint32_t style;
    int n;
    HidVibrationDeviceHandle h[2];
};
static vib g_vib[4];

static void init_locked(void)
{
    if (g_init) return;
    padConfigureInput(4, HidNpadStyleSet_NpadStandard);
    padInitialize(&g_pads[0], HidNpadIdType_No1, HidNpadIdType_Handheld);
    padInitialize(&g_pads[1], HidNpadIdType_No2);
    padInitialize(&g_pads[2], HidNpadIdType_No3);
    padInitialize(&g_pads[3], HidNpadIdType_No4);
    hidInitializeKeyboard();
    hidInitializeMouse();
    hidInitializeTouchScreen();
    memset(&g_prev_kbd, 0, sizeof g_prev_kbd);
    g_init = 1;
}

static void pump_locked(void)
{
    init_locked();
    for (int i = 0; i < 4; i++)
        padUpdate(&g_pads[i]);

    HidKeyboardState k;
    if (hidGetKeyboardStates(&k, 1)) {
        for (int u = 4; u < 232; u++) {
            bool now = hidKeyboardStateGetKey(&k, (HidKeyboardKey)u), was = hidKeyboardStateGetKey(&g_prev_kbd, (HidKeyboardKey)u);
            if (now != was) {
                uint8_t dik = fnvnx_hid_usage_to_dik((uint8_t)u);
                if (dik) fnvnx_input_key(dik, now);
            }
        }
        g_prev_kbd = k;
    }

    HidMouseState m;
    if (hidGetMouseStates(&m, 1) && (m.attributes & HidMouseAttribute_IsConnected)) {
        if (m.delta_x || m.delta_y || m.wheel_delta_y) fnvnx_input_mouse_move(m.delta_x, m.delta_y, m.wheel_delta_y);
        for (unsigned b = 0; b < 5; b++) {
            bool now = (m.buttons >> b) & 1, was = (g_prev_mouse_buttons >> b) & 1;
            if (now != was) fnvnx_input_mouse_button(b, now);
        }
        g_prev_mouse_buttons = m.buttons;
    }

    HidTouchScreenState ts;
    if (hidGetTouchScreenStates(&ts, 1)) {
        fnvnx_touch_point pts[16];
        unsigned n = ts.count < 0 ? 0 : (unsigned)ts.count > 16 ? 16 : (unsigned)ts.count;
        for (unsigned i = 0; i < n; i++) {
            pts[i].x = (int32_t)ts.touches[i].x;
            pts[i].y = (int32_t)ts.touches[i].y;
            pts[i].id = ts.touches[i].finger_id;
        }
        fnvnx_touch_frame(pts, n, clk_rt_ns() / 1000000u);
    }
}

extern "C" void fnvnx_input_pump(void)
{
    uint64_t now = clk_rt_ns();
    os_lock(&g_lock);
    if (!g_init || now - g_last_pump >= 1000000u) {   /* at most once per ms */
        g_last_pump = now;
        pump_locked();
    }
    os_unlock(&g_lock);
    fnvnx_swkbd_pump();   /* the strokes of an entered text, one edge per pump while the game keeps up */
}

static int hz_read(unsigned i, fnvnx_pad_raw *o)
{
    os_lock(&g_lock);
    init_locked();
    PadState *p = &g_pads[i];
    memset(o, 0, sizeof *o);
    o->connected = padIsConnected(p);
    if (o->connected) {
        o->buttons = padGetButtons(p);
        HidAnalogStickState l = padGetStickPos(p, 0), r = padGetStickPos(p, 1);
        o->lx = l.x; o->ly = l.y; o->rx = r.x; o->ry = r.y;
    }
    os_unlock(&g_lock);
    return o->connected != 0;
}

static void hz_vibrate(unsigned i, float lo, float hi)
{
    os_lock(&g_lock);
    init_locked();
    PadState *p = &g_pads[i];
    uint32_t style = padGetStyleSet(p);
    vib &v = g_vib[i];
    if (v.style != style) {
        v.style = style;
        v.n = 0;
        HidNpadIdType id = (i == 0 && padIsHandheld(p)) ? HidNpadIdType_Handheld : (HidNpadIdType)(HidNpadIdType_No1 + i);
        HidNpadStyleTag tag = (style & HidNpadStyleTag_NpadHandheld) ? HidNpadStyleTag_NpadHandheld
                            : (style & HidNpadStyleTag_NpadJoyDual) ? HidNpadStyleTag_NpadJoyDual
                            : HidNpadStyleTag_NpadFullKey;
        int n = tag == HidNpadStyleTag_NpadFullKey ? 1 : 2;
        if (style && R_SUCCEEDED(hidInitializeVibrationDevices(v.h, n, id, tag))) v.n = n;
    }
    if (v.n) {
        HidVibrationValue val[2];
        for (int k = 0; k < 2; k++) {
            val[k].amp_low = lo;   val[k].freq_low = 160.0f;
            val[k].amp_high = hi;  val[k].freq_high = 320.0f;
        }
        hidSendVibrationValues(v.h, val, v.n);
    }
    os_unlock(&g_lock);
}

static const fnvnx_pad_source g_src = {hz_read, hz_vibrate};
extern "C" const fnvnx_pad_source *fnvnx_pad_horizon_source(void) { return &g_src; }
