/* test_input.cpp - XInput mapping table, XInput API, DirectInput keyboard/mouse (generic data formats,
 * immediate and buffered), touch-as-mouse, HID usage -> DIK. */
#include "s7_test.h"

#include <dinput.h>
#include <xinput.h>
#include "fnvnx_win32_ext.h"
#include "../../platform/input/input.h"

#include <stdint.h>

TEST(input_xinput_mapping_table)
{
    struct row { uint64_t npad; uint16_t label, positional; };
    static const row rows[] = {
        {FNVNX_NPAD_A, XINPUT_GAMEPAD_A, XINPUT_GAMEPAD_B},
        {FNVNX_NPAD_B, XINPUT_GAMEPAD_B, XINPUT_GAMEPAD_A},
        {FNVNX_NPAD_X, XINPUT_GAMEPAD_X, XINPUT_GAMEPAD_Y},
        {FNVNX_NPAD_Y, XINPUT_GAMEPAD_Y, XINPUT_GAMEPAD_X},
        {FNVNX_NPAD_L, XINPUT_GAMEPAD_LEFT_SHOULDER, XINPUT_GAMEPAD_LEFT_SHOULDER},
        {FNVNX_NPAD_R, XINPUT_GAMEPAD_RIGHT_SHOULDER, XINPUT_GAMEPAD_RIGHT_SHOULDER},
        {FNVNX_NPAD_PLUS, XINPUT_GAMEPAD_START, XINPUT_GAMEPAD_START},
        {FNVNX_NPAD_MINUS, XINPUT_GAMEPAD_BACK, XINPUT_GAMEPAD_BACK},
        {FNVNX_NPAD_STICKL, XINPUT_GAMEPAD_LEFT_THUMB, XINPUT_GAMEPAD_LEFT_THUMB},
        {FNVNX_NPAD_STICKR, XINPUT_GAMEPAD_RIGHT_THUMB, XINPUT_GAMEPAD_RIGHT_THUMB},
        {FNVNX_NPAD_UP, XINPUT_GAMEPAD_DPAD_UP, XINPUT_GAMEPAD_DPAD_UP},
        {FNVNX_NPAD_DOWN, XINPUT_GAMEPAD_DPAD_DOWN, XINPUT_GAMEPAD_DPAD_DOWN},
        {FNVNX_NPAD_LEFT, XINPUT_GAMEPAD_DPAD_LEFT, XINPUT_GAMEPAD_DPAD_LEFT},
        {FNVNX_NPAD_RIGHT, XINPUT_GAMEPAD_DPAD_RIGHT, XINPUT_GAMEPAD_DPAD_RIGHT},
        {FNVNX_NPAD_ZL, 0, 0}, {FNVNX_NPAD_ZR, 0, 0},
        {FNVNX_NPAD_LEFT_SL, 0, 0}, {FNVNX_NPAD_RIGHT_SR, 0, 0},
    };
    for (const row &r : rows) {
        fnvnx_pad_raw raw = {1, r.npad, 0, 0, 0, 0};
        fnvnx_xpad x;
        fnvnx_pad_map(&raw, FNVNX_PAD_LAYOUT_LABEL, &x);
        CHECK_EQ(x.buttons, r.label);
        fnvnx_pad_map(&raw, FNVNX_PAD_LAYOUT_POSITIONAL, &x);
        CHECK_EQ(x.buttons, r.positional);
    }
    fnvnx_pad_raw raw = {1, FNVNX_NPAD_ZL | FNVNX_NPAD_ZR, 32767, -32767, 40000, -40000};
    fnvnx_xpad x;
    fnvnx_pad_map(&raw, 0, &x);
    CHECK_EQ(x.lt, 255); CHECK_EQ(x.rt, 255);
    CHECK_EQ(x.lx, 32767); CHECK_EQ(x.ly, -32767); CHECK_EQ(x.rx, 32767); CHECK_EQ(x.ry, -32768);
    raw.connected = 0;
    fnvnx_pad_map(&raw, 0, &x);
    CHECK_EQ(x.buttons, 0); CHECK_EQ(x.lt, 0);
}

TEST(input_xinput_api)
{
    fnvnx_pad_set_layout(FNVNX_PAD_LAYOUT_LABEL);
    XINPUT_STATE st;
    fnvnx_pad_raw off = {0, 0, 0, 0, 0, 0};
    fnvnx_pad_inject(1, &off);
    CHECK_EQ(XInputGetState(1, &st), ERROR_DEVICE_NOT_CONNECTED);
    CHECK_EQ(XInputGetState(4, &st), ERROR_BAD_ARGUMENTS);
    fnvnx_pad_raw p = {1, FNVNX_NPAD_A, 100, 200, -300, -400};
    fnvnx_pad_inject(0, &p);
    CHECK_EQ(XInputGetState(0, &st), ERROR_SUCCESS);
    CHECK_EQ(st.Gamepad.wButtons, XINPUT_GAMEPAD_A);
    CHECK_EQ(st.Gamepad.sThumbLY, 200); CHECK_EQ(st.Gamepad.sThumbRX, -300);
    DWORD pkt = st.dwPacketNumber;
    XInputGetState(0, &st);
    CHECK_EQ(st.dwPacketNumber, pkt);          /* unchanged state: same packet */
    p.buttons |= FNVNX_NPAD_ZR;
    fnvnx_pad_inject(0, &p);
    XInputGetState(0, &st);
    CHECK_EQ(st.dwPacketNumber, pkt + 1);
    CHECK_EQ(st.Gamepad.bRightTrigger, 255);
    XINPUT_VIBRATION v = {65535, 32768};
    CHECK_EQ(XInputSetState(0, &v), ERROR_SUCCESS);
    float lo, hi;
    fnvnx_pad_last_vibration(0, &lo, &hi);
    CHECK_NEAR(lo, 1.0, 1e-6); CHECK_NEAR(hi, 0.5, 1e-4);
    XINPUT_CAPABILITIES c;
    CHECK_EQ(XInputGetCapabilities(0, XINPUT_FLAG_GAMEPAD, &c), ERROR_SUCCESS);
    CHECK_EQ(c.Type, XINPUT_DEVTYPE_GAMEPAD);
    XInputEnable(FALSE);
    XInputGetState(0, &st);
    CHECK_EQ(st.Gamepad.wButtons, 0);
    fnvnx_pad_last_vibration(0, &lo, &hi);
    CHECK_EQ(lo, 0);
    XInputEnable(TRUE);
    /* through the module, as LoadLibrary/GetProcAddress users see it */
    typedef DWORD (*get_fn)(DWORD, XINPUT_STATE *);
    get_fn g = (get_fn)(void *)GetProcAddress(LoadLibraryA("XINPUT1_3.DLL"), "XInputGetState");
    CHECK(g != nullptr);
    if (g) CHECK_EQ(g(0, &st), ERROR_SUCCESS);
    fnvnx_pad_inject(0, &off);
}

namespace {
/* the shapes of c_dfDIKeyboard and c_dfDIMouse2 (the engine has its own copies in .rdata) */
DIOBJECTDATAFORMAT g_kbd_objs[256];
DIDATAFORMAT kbd_format()
{
    for (int i = 0; i < 256; i++)
        g_kbd_objs[i] = {&GUID_Key, (DWORD)i, (DWORD)(0x80000000u | DIDFT_BUTTON | DIDFT_MAKEINSTANCE(i)), 0};
    return {sizeof(DIDATAFORMAT), sizeof(DIOBJECTDATAFORMAT), DIDF_RELAXIS, 256, 256, g_kbd_objs};
}
DIOBJECTDATAFORMAT g_mouse_objs[11];
DIDATAFORMAT mouse_format()
{
    g_mouse_objs[0] = {&GUID_XAxis, 0, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0};
    g_mouse_objs[1] = {&GUID_YAxis, 4, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0};
    g_mouse_objs[2] = {&GUID_ZAxis, 8, 0x80000000u | DIDFT_AXIS | DIDFT_ANYINSTANCE, 0};
    for (int i = 0; i < 8; i++)
        g_mouse_objs[3 + i] = {nullptr, (DWORD)(12 + i), (DWORD)((i >= 3 ? 0x80000000u : 0) | DIDFT_BUTTON | DIDFT_ANYINSTANCE), 0};
    return {sizeof(DIDATAFORMAT), sizeof(DIOBJECTDATAFORMAT), DIDF_RELAXIS, 20, 11, g_mouse_objs};
}
IDirectInput8A *di()
{
    void *p = nullptr;
    CHECK_EQ(DirectInput8Create(nullptr, 0x0800, IID_IDirectInput8A, &p, nullptr), DI_OK);
    return (IDirectInput8A *)p;
}
BOOL count_dev(LPCDIDEVICEINSTANCEA, LPVOID n) { ++*(int *)n; return DIENUM_CONTINUE; }
} // namespace

/* the exe's GUIDs in .text, unmapped in the port: g_text_key stands for one (garbage where it lies); the game build's
 * fnvnx_image_const (toolchain/game/boot.cpp) gives its bytes from the exe file, here GUID_Key */
GUID g_text_key;
DIOBJECTDATAFORMAT g_text_objs[256];   /* an object table where it lies: garbage; its bytes come from g_kbd_objs */
DIDATAFORMAT g_text_fmt;               /* a header where it lies: garbage; its bytes come from g_real_fmt */
DIDATAFORMAT g_real_fmt;
extern "C" const void *fnvnx_image_const(const void *p, uint32_t n, void *buf)
{
    if (p == &g_text_key && n == sizeof(GUID)) {
        memcpy(buf, &GUID_Key, sizeof(GUID));
        return buf;
    }
    if (p == g_text_objs && n <= sizeof g_text_objs) {
        memcpy(buf, g_kbd_objs, n);
        return buf;
    }
    if (p == &g_text_fmt && n == sizeof g_text_fmt) {
        memcpy(buf, &g_real_fmt, n);
        return buf;
    }
    return p;
}

TEST(input_dinput_format_guids_from_the_image)
{
    memset(&g_text_key, 0xCC, sizeof g_text_key);
    IDirectInput8A *in = di();
    IDirectInputDevice8A *k = nullptr;
    CHECK_EQ(in->CreateDevice(GUID_SysKeyboard, &k, nullptr), DI_OK);
    DIDATAFORMAT f = kbd_format();
    for (int i = 0; i < 256; i++)
        g_kbd_objs[i].pguid = &g_text_key;   /* a table pointing into the exe's .text, as c_dfDIKeyboard does */
    CHECK_EQ(k->SetDataFormat(&f), DI_OK);
    /* the object table and the header where the port does not map them (97deacd38aec's stub boot) */
    memset(g_text_objs, 0xCC, sizeof g_text_objs);
    memset(&g_text_fmt, 0xCC, sizeof g_text_fmt);
    DIDATAFORMAT ft = f;
    ft.rgodf = g_text_objs;
    CHECK_EQ(k->SetDataFormat(&ft), DI_OK);
    g_real_fmt = ft;
    CHECK_EQ(k->SetDataFormat(&g_text_fmt), DI_OK);
    uint8_t ks[256];
    CHECK_EQ(k->Acquire(), DI_OK);
    CHECK_EQ(k->GetDeviceState(256, ks), DI_OK);   /* the format took: state reads work */
    CHECK_EQ(k->Unacquire(), DI_OK);
    g_kbd_objs[0].pguid = &GUID_XAxis;   /* a real mismatch still fails (not optional) */
    g_kbd_objs[0].dwType &= ~0x80000000u;
    CHECK_EQ(k->SetDataFormat(&f), DIERR_INVALIDPARAM);
    k->Release();
    in->Release();
}

TEST(input_dinput_keyboard_immediate_and_buffered)
{
    IDirectInput8A *in = di();
    IDirectInputDevice8A *k = nullptr;
    CHECK_EQ(in->CreateDevice(GUID_Joystick, &k, nullptr), DIERR_DEVICENOTREG);
    CHECK_EQ(in->CreateDevice(GUID_SysKeyboard, &k, nullptr), DI_OK);
    DIDATAFORMAT f = kbd_format();
    uint8_t keys[256];
    CHECK_EQ(k->GetDeviceState(256, keys), DIERR_INVALIDPARAM);   /* no format yet */
    CHECK_EQ(k->SetDataFormat(&f), DI_OK);
    CHECK_EQ(k->SetCooperativeLevel(nullptr, DISCL_NONEXCLUSIVE | DISCL_FOREGROUND), DI_OK);
    CHECK_EQ(k->SetCooperativeLevel(nullptr, DISCL_EXCLUSIVE | DISCL_NONEXCLUSIVE), DIERR_INVALIDPARAM);
    DIPROPDWORD bs = {{sizeof(DIPROPDWORD), sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, 4};
    CHECK_EQ(k->SetProperty(DIPROP_BUFFERSIZE, &bs.diph), DI_OK);
    CHECK_EQ(k->GetDeviceState(256, keys), DIERR_NOTACQUIRED);
    CHECK_EQ(k->Acquire(), DI_OK);
    fnvnx_input_key(DIK_W, 1);
    CHECK_EQ(k->GetDeviceState(256, keys), DI_OK);
    CHECK_EQ(keys[DIK_W], 0x80);
    CHECK_EQ(keys[DIK_S], 0);
    fnvnx_input_key(DIK_W, 0);
    DIDEVICEOBJECTDATA d[8];
    DWORD n = 8;
    CHECK_EQ(k->GetDeviceData(sizeof(DIDEVICEOBJECTDATA), d, &n, DIGDD_PEEK), DI_OK);
    CHECK_EQ(n, 2);
    n = 8;
    CHECK_EQ(k->GetDeviceData(sizeof(DIDEVICEOBJECTDATA), d, &n, 0), DI_OK);
    CHECK_EQ(n, 2);
    CHECK_EQ(d[0].dwOfs, DIK_W); CHECK_EQ(d[0].dwData, 0x80);
    CHECK_EQ(d[1].dwOfs, DIK_W); CHECK_EQ(d[1].dwData, 0);
    CHECK(d[1].dwSequence > d[0].dwSequence);
    for (int i = 0; i < 6; i++) fnvnx_input_key(DIK_A, i & 1 ? 0 : 1);   /* 6 events into 4 slots */
    n = 8;
    CHECK_EQ(k->GetDeviceData(sizeof(DIDEVICEOBJECTDATA), d, &n, 0), DI_BUFFEROVERFLOW);
    CHECK_EQ(n, 4);
    k->Unacquire();
    DIDEVCAPS c;
    c.dwSize = sizeof c;
    CHECK_EQ(k->GetCapabilities(&c), DI_OK);
    CHECK_EQ(c.dwDevType & 0xFF, DI8DEVTYPE_KEYBOARD);
    k->Release();
    int cnt = 0;
    in->EnumDevices(DI8DEVCLASS_GAMECTRL, count_dev, &cnt, DIEDFL_ATTACHEDONLY);
    CHECK_EQ(cnt, 0);   /* no DirectInput joysticks: the gamepad is XInput */
    in->EnumDevices(DI8DEVCLASS_ALL, count_dev, &cnt, 0);
    CHECK_EQ(cnt, 2);
    in->Release();
}

TEST(input_dinput_mouse_and_touch)
{
    IDirectInput8A *in = di();
    IDirectInputDevice8A *m = nullptr;
    CHECK_EQ(in->CreateDevice(GUID_SysMouse, &m, nullptr), DI_OK);
    DIDATAFORMAT f = mouse_format();
    CHECK_EQ(m->SetDataFormat(&f), DI_OK);
    DIPROPDWORD bs = {{sizeof(DIPROPDWORD), sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, 32};
    m->SetProperty(DIPROP_BUFFERSIZE, &bs.diph);
    m->Acquire();
    fnvnx_input_mouse_move(5, -3, 120);
    fnvnx_input_mouse_move(1, 0, 0);
    fnvnx_input_mouse_button(1, 1);
    struct { int32_t lX, lY, lZ; uint8_t rgbButtons[8]; } s;   /* DIMOUSESTATE2 as the game sees it (ILP32) */
    CHECK_EQ(m->GetDeviceState(sizeof s, &s), DI_OK);
    CHECK_EQ(s.lX, 6); CHECK_EQ(s.lY, -3); CHECK_EQ(s.lZ, 120);
    CHECK_EQ(s.rgbButtons[0], 0); CHECK_EQ(s.rgbButtons[1], 0x80);
    CHECK_EQ(m->GetDeviceState(sizeof s, &s), DI_OK);
    CHECK_EQ(s.lX, 0);   /* relative: deltas since the last read */
    fnvnx_input_mouse_button(1, 0);
    DIDEVICEOBJECTDATA d[32];
    DWORD n = 32;
    m->GetDeviceData(sizeof(DIDEVICEOBJECTDATA), d, &n, 0);
    CHECK_EQ(n, 6);   /* x, y, z, x, button down, button up */
    CHECK_EQ(d[0].dwOfs, 0); CHECK_EQ((LONG)d[0].dwData, 5);
    CHECK_EQ(d[2].dwOfs, 8);
    CHECK_EQ(d[4].dwOfs, 13); CHECK_EQ(d[4].dwData, 0x80);

    /* touch: drag moves (2 counts per pixel), a short tap clicks left, a two-finger tap clicks right */
    fnvnx_touch_reset();
    fnvnx_touch_point p = {100, 100, 7};
    fnvnx_touch_frame(&p, 1, 1000);
    p.x = 130; p.y = 90;
    fnvnx_touch_frame(&p, 1, 1016);
    fnvnx_touch_frame(nullptr, 0, 1500);
    m->GetDeviceState(sizeof s, &s);
    CHECK_EQ(s.lX, 60); CHECK_EQ(s.lY, -20);
    n = 32;
    m->GetDeviceData(sizeof(DIDEVICEOBJECTDATA), d, &n, 0);   /* drain */
    fnvnx_touch_frame(&p, 1, 2000);
    fnvnx_touch_frame(nullptr, 0, 2080);
    n = 32;
    m->GetDeviceData(sizeof(DIDEVICEOBJECTDATA), d, &n, 0);
    CHECK_EQ(n, 2);
    CHECK_EQ(d[0].dwOfs, 12); CHECK_EQ(d[0].dwData, 0x80); CHECK_EQ(d[1].dwData, 0);
    fnvnx_touch_point two[2] = {{100, 100, 1}, {200, 100, 2}};
    fnvnx_touch_frame(two, 1, 3000);
    fnvnx_touch_frame(two, 2, 3010);
    fnvnx_touch_frame(nullptr, 0, 3100);
    n = 32;
    m->GetDeviceData(sizeof(DIDEVICEOBJECTDATA), d, &n, 0);
    CHECK_EQ(n, 2);
    CHECK_EQ(d[0].dwOfs, 13);
    m->Release();
    in->Release();
}

TEST(input_hid_usage_to_dik)
{
    CHECK_EQ(fnvnx_hid_usage_to_dik(4), DIK_A);
    CHECK_EQ(fnvnx_hid_usage_to_dik(29), DIK_Z);
    CHECK_EQ(fnvnx_hid_usage_to_dik(30), DIK_1);
    CHECK_EQ(fnvnx_hid_usage_to_dik(39), DIK_0);
    CHECK_EQ(fnvnx_hid_usage_to_dik(41), DIK_ESCAPE);
    CHECK_EQ(fnvnx_hid_usage_to_dik(58), DIK_F1);
    CHECK_EQ(fnvnx_hid_usage_to_dik(69), DIK_F12);
    CHECK_EQ(fnvnx_hid_usage_to_dik(82), DIK_UP);
    CHECK_EQ(fnvnx_hid_usage_to_dik(225), DIK_LSHIFT);
    CHECK_EQ(fnvnx_hid_usage_to_dik(230), DIK_RMENU);
    CHECK_EQ(fnvnx_hid_usage_to_dik(3), 0);
}
