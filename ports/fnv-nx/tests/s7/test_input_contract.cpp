/* test_input_contract.cpp - the documented contract of XInput 1.3 and DirectInput 8 (ANSI) for the decided
 * environment: one XInput pad (user 0, always connected), DirectInput system keyboard + mouse, no force feedback,
 * an empty game-controller class. Every method of the interfaces, every error path. */
#include "s7_test.h"

#include <dinput.h>
#include <xinput.h>
#include "fnvnx_win32_ext.h"
#include "../../platform/input/input.h"

#include <stddef.h>
#include <stdint.h>
#include <unistd.h>

#define PR(n) (*(const GUID *)(uintptr_t)(n))   /* DIPROP_* as the SDK defines them (small integers) */
#ifndef ERROR_EMPTY
#define ERROR_EMPTY 4306L
#endif

extern "C" HRESULT fnvnx_dinput_com_factory(const GUID *iid, void **out);

namespace {
const HRESULT E_INVALID = E_INVALIDARG, E_FAIL_ = E_FAIL;
bool same(const GUID &a, const GUID &b) { return a.Data1 == b.Data1 && a.Data2 == b.Data2 && a.Data3 == b.Data3 && !memcmp(a.Data4, b.Data4, 8); }
const size_t X3_INST = offsetof(DIDEVICEINSTANCEA, guidFFDriver), X3_OBJ = offsetof(DIDEVICEOBJECTINSTANCEA, dwFFMaxForce),
             X3_CAPS = offsetof(DIDEVCAPS, dwFFSamplePeriod);
struct ImgInfo { CHAR path[MAX_PATH]; DWORD flags, view; LONG overlay[4]; DWORD obj, npts; LONG callout[10]; LONG rect[4]; DWORD align; };

/* ================= XInput */
void pad(uint64_t b, int lx = 0, int ly = 0, int rx = 0, int ry = 0, uint32_t connected = 1)
{
    fnvnx_pad_raw r = {connected, b, lx, ly, rx, ry};
    fnvnx_pad_inject(0, &r);
}
typedef DWORD (WINAPI *ks_fn)(DWORD, DWORD, XINPUT_KEYSTROKE *);
ks_fn ks_proc() { return (ks_fn)(void *)GetProcAddress(LoadLibraryA("xinput1_3.dll"), "XInputGetKeystroke"); }
/* 0 = ERROR_EMPTY, else the keystroke's VK | flags << 16 */
uint32_t next_ks(DWORD user = 0)
{
    ks_fn f = ks_proc();
    XINPUT_KEYSTROKE k;
    memset(&k, 0xEE, sizeof k);
    if (!f || f(user, 0, &k) != ERROR_SUCCESS) return 0;
    return k.VirtualKey | (uint32_t)k.Flags << 16;
}
void drain_ks() { while (next_ks()) {} }
} // namespace

TEST(contract_xinput_state)
{
    fnvnx_pad_set_layout(FNVNX_PAD_LAYOUT_LABEL);
    XInputEnable(TRUE);
    pad(0);
    XINPUT_STATE st;
    CHECK_EQ(XInputGetState(0, &st), ERROR_SUCCESS);
    for (DWORD u = 1; u <= 3; u++) CHECK_EQ(XInputGetState(u, &st), ERROR_DEVICE_NOT_CONNECTED);
    CHECK_EQ(XInputGetState(4, &st), ERROR_BAD_ARGUMENTS);
    CHECK_EQ(XInputGetState(255, &st), ERROR_BAD_ARGUMENTS);
    CHECK_EQ(XInputGetState(0xFFFFFFFFu, &st), ERROR_BAD_ARGUMENTS);
    CHECK_EQ(XInputGetState(0, nullptr), ERROR_BAD_ARGUMENTS);
    /* the one controller is always connected: an absent source reads neutral, never DEVICE_NOT_CONNECTED */
    pad(FNVNX_NPAD_A, 0, 0, 0, 0, 0);
    CHECK_EQ(XInputGetState(0, &st), ERROR_SUCCESS);
    CHECK_EQ(st.Gamepad.wButtons, 0);
    /* every button 1:1, sticks, sign convention (y up), triggers digital 0/255 (Switch ZL/ZR) */
    pad(FNVNX_NPAD_A | FNVNX_NPAD_B | FNVNX_NPAD_X | FNVNX_NPAD_Y | FNVNX_NPAD_L | FNVNX_NPAD_R | FNVNX_NPAD_PLUS |
            FNVNX_NPAD_MINUS | FNVNX_NPAD_STICKL | FNVNX_NPAD_UP | FNVNX_NPAD_DOWN | FNVNX_NPAD_LEFT |
            FNVNX_NPAD_RIGHT | FNVNX_NPAD_ZL,
        -32768, 32767, 1, -1);
    XInputGetState(0, &st);
    CHECK_EQ(st.Gamepad.wButtons, 0xF37F);   /* all but the right click: both clicks together are the text-entry combination, withheld from the game */
    pad(FNVNX_NPAD_STICKR);
    XInputGetState(0, &st);
    CHECK_EQ(st.Gamepad.wButtons, 0x0080);   /* a single click is still the game's button */
    pad(FNVNX_NPAD_A | FNVNX_NPAD_B | FNVNX_NPAD_X | FNVNX_NPAD_Y | FNVNX_NPAD_L | FNVNX_NPAD_R | FNVNX_NPAD_PLUS |
            FNVNX_NPAD_MINUS | FNVNX_NPAD_STICKL | FNVNX_NPAD_STICKR | FNVNX_NPAD_UP | FNVNX_NPAD_DOWN | FNVNX_NPAD_LEFT |
            FNVNX_NPAD_RIGHT | FNVNX_NPAD_ZL,
        -32768, 32767, 1, -1);
    XInputGetState(0, &st);
    CHECK_EQ(st.Gamepad.wButtons, 0xF33F);   /* the combination opens the applet and reaches the game as neither click */
    pad(FNVNX_NPAD_A | FNVNX_NPAD_B | FNVNX_NPAD_X | FNVNX_NPAD_Y | FNVNX_NPAD_L | FNVNX_NPAD_R | FNVNX_NPAD_PLUS |
            FNVNX_NPAD_MINUS | FNVNX_NPAD_STICKL | FNVNX_NPAD_UP | FNVNX_NPAD_DOWN | FNVNX_NPAD_LEFT |
            FNVNX_NPAD_RIGHT | FNVNX_NPAD_ZL,
        -32768, 32767, 1, -1);
    XInputGetState(0, &st);
    CHECK_EQ(st.Gamepad.bLeftTrigger, 255);
    CHECK_EQ(st.Gamepad.bRightTrigger, 0);
    CHECK_EQ(st.Gamepad.sThumbLX, -32768); CHECK_EQ(st.Gamepad.sThumbLY, 32767);
    CHECK_EQ(st.Gamepad.sThumbRX, 1); CHECK_EQ(st.Gamepad.sThumbRY, -1);
    /* dwPacketNumber changes exactly when the state changes */
    pad(FNVNX_NPAD_A);
    XInputGetState(0, &st);
    DWORD p = st.dwPacketNumber;
    for (int i = 0; i < 3; i++) { XInputGetState(0, &st); CHECK_EQ(st.dwPacketNumber, p); }
    pad(FNVNX_NPAD_A, 1);
    XInputGetState(0, &st);
    CHECK_EQ(st.dwPacketNumber, p + 1);
    /* XInputEnable(FALSE): neutral reads (the neutral state is a change), then TRUE restores */
    XInputEnable(FALSE);
    XInputGetState(0, &st);
    CHECK_EQ(st.Gamepad.wButtons, 0); CHECK_EQ(st.Gamepad.sThumbLX, 0);
    CHECK_EQ(st.dwPacketNumber, p + 2);
    XInputEnable(TRUE);
    XInputGetState(0, &st);
    CHECK_EQ(st.Gamepad.wButtons, XINPUT_GAMEPAD_A); CHECK_EQ(st.dwPacketNumber, p + 3);
    pad(0);
}

TEST(contract_xinput_vibration_caps_misc)
{
    XInputEnable(TRUE);
    float lo, hi;
    XINPUT_VIBRATION v = {65535, 0};
    CHECK_EQ(XInputSetState(0, &v), ERROR_SUCCESS);
    fnvnx_pad_last_vibration(0, &lo, &hi);
    CHECK_NEAR(lo, 1.0, 1e-6); CHECK_NEAR(hi, 0.0, 1e-6);
    CHECK_EQ(XInputSetState(1, &v), ERROR_DEVICE_NOT_CONNECTED);
    CHECK_EQ(XInputSetState(4, &v), ERROR_BAD_ARGUMENTS);
    CHECK_EQ(XInputSetState(0, nullptr), ERROR_BAD_ARGUMENTS);
    v = {0, 65535};
    XInputSetState(0, &v);
    XInputEnable(FALSE);   /* vibration stops ... */
    fnvnx_pad_last_vibration(0, &lo, &hi);
    CHECK_NEAR(lo, 0.0, 1e-6); CHECK_NEAR(hi, 0.0, 1e-6);
    v = {65535, 65535};
    CHECK_EQ(XInputSetState(0, &v), ERROR_SUCCESS);   /* ... and stays stopped while disabled */
    fnvnx_pad_last_vibration(0, &lo, &hi);
    CHECK_NEAR(lo, 0.0, 1e-6);
    XInputEnable(TRUE);   /* ... TRUE restores the last request */
    fnvnx_pad_last_vibration(0, &lo, &hi);
    CHECK_NEAR(lo, 1.0, 1e-6); CHECK_NEAR(hi, 1.0, 1e-6);
    v = {0, 0};
    XInputSetState(0, &v);

    XINPUT_CAPABILITIES c;
    memset(&c, 0xEE, sizeof c);
    CHECK_EQ(XInputGetCapabilities(0, 0, &c), ERROR_SUCCESS);
    CHECK_EQ(c.Type, XINPUT_DEVTYPE_GAMEPAD); CHECK_EQ(c.SubType, XINPUT_DEVSUBTYPE_GAMEPAD); CHECK_EQ(c.Flags, 0);
    CHECK_EQ(c.Gamepad.wButtons, 0xF3FF); CHECK_EQ(c.Gamepad.bLeftTrigger, 255); CHECK_EQ(c.Gamepad.bRightTrigger, 255);
    CHECK_EQ(c.Gamepad.sThumbLX, (SHORT)0xFFC0); CHECK_EQ(c.Gamepad.sThumbRY, (SHORT)0xFFC0);
    CHECK_EQ(c.Vibration.wLeftMotorSpeed, 0xFFFF); CHECK_EQ(c.Vibration.wRightMotorSpeed, 0xFFFF);
    CHECK_EQ(XInputGetCapabilities(0, XINPUT_FLAG_GAMEPAD, &c), ERROR_SUCCESS);
    CHECK_EQ(XInputGetCapabilities(XUSER_INDEX_ANY, 0, &c), ERROR_SUCCESS);
    CHECK_EQ(XInputGetCapabilities(0, 2, &c), ERROR_BAD_ARGUMENTS);
    CHECK_EQ(XInputGetCapabilities(0, 0, nullptr), ERROR_BAD_ARGUMENTS);
    CHECK_EQ(XInputGetCapabilities(2, 0, &c), ERROR_DEVICE_NOT_CONNECTED);
    CHECK_EQ(XInputGetCapabilities(4, 0, &c), ERROR_BAD_ARGUMENTS);

    HMODULE m = LoadLibraryA("xinput1_3.dll");
    typedef DWORD (WINAPI *guid_fn)(DWORD, GUID *, GUID *);
    typedef DWORD (WINAPI *bat_fn)(DWORD, BYTE, XINPUT_BATTERY_INFORMATION *);
    guid_fn gg = (guid_fn)(void *)GetProcAddress(m, "XInputGetDSoundAudioDeviceGuids");
    bat_fn gb = (bat_fn)(void *)GetProcAddress(m, "XInputGetBatteryInformation");
    CHECK(gg && gb && ks_proc());
    GUID a, b, z;
    memset(&z, 0, sizeof z);
    memset(&a, 0xEE, sizeof a); memset(&b, 0xEE, sizeof b);
    if (gg) {
        CHECK_EQ(gg(0, &a, &b), ERROR_SUCCESS);
        CHECK(same(a, z) && same(b, z));
        CHECK_EQ(gg(1, &a, &b), ERROR_DEVICE_NOT_CONNECTED);
        CHECK_EQ(gg(4, &a, &b), ERROR_BAD_ARGUMENTS);
        CHECK_EQ(gg(0, nullptr, &b), ERROR_BAD_ARGUMENTS);
    }
    XINPUT_BATTERY_INFORMATION bi = {9, 9};
    if (gb) {
        CHECK_EQ(gb(0, BATTERY_DEVTYPE_GAMEPAD, &bi), ERROR_SUCCESS);
        CHECK_EQ(bi.BatteryType, BATTERY_TYPE_WIRED); CHECK_EQ(bi.BatteryLevel, BATTERY_LEVEL_FULL);
        CHECK_EQ(gb(0, BATTERY_DEVTYPE_HEADSET, &bi), ERROR_SUCCESS);
        CHECK_EQ(bi.BatteryType, BATTERY_TYPE_DISCONNECTED);
        CHECK_EQ(gb(2, BATTERY_DEVTYPE_GAMEPAD, &bi), ERROR_DEVICE_NOT_CONNECTED);
        CHECK_EQ(gb(9, BATTERY_DEVTYPE_GAMEPAD, &bi), ERROR_BAD_ARGUMENTS);
        CHECK_EQ(gb(0, 7, &bi), ERROR_BAD_ARGUMENTS);
        CHECK_EQ(gb(0, BATTERY_DEVTYPE_GAMEPAD, nullptr), ERROR_BAD_ARGUMENTS);
    }
}

TEST(contract_xinput_keystroke)
{
    XInputEnable(TRUE);
    pad(0);
    drain_ks();
    CHECK_EQ(next_ks(), 0);   /* ERROR_EMPTY */
    ks_fn f = ks_proc();
    XINPUT_KEYSTROKE k;
    if (f) {
        CHECK_EQ(f(0, 0, &k), ERROR_EMPTY);
        CHECK_EQ(f(XUSER_INDEX_ANY, 0, &k), ERROR_EMPTY);
        CHECK_EQ(f(1, 0, &k), ERROR_DEVICE_NOT_CONNECTED);
        CHECK_EQ(f(4, 0, &k), ERROR_BAD_ARGUMENTS);
        CHECK_EQ(f(0, 0, nullptr), ERROR_BAD_ARGUMENTS);
    }
    pad(FNVNX_NPAD_A);
    if (f) {
        memset(&k, 0xEE, sizeof k);
        CHECK_EQ(f(XUSER_INDEX_ANY, 0, &k), ERROR_SUCCESS);
        CHECK_EQ(k.VirtualKey, VK_PAD_A); CHECK_EQ(k.Flags, XINPUT_KEYSTROKE_KEYDOWN);
        CHECK_EQ(k.UserIndex, 0); CHECK_EQ(k.Unicode, 0); CHECK_EQ(k.HidCode, 0);
    }
    CHECK_EQ(next_ks(), 0);   /* a held key makes no event until the repeat delay */
    pad(0);
    CHECK_EQ(next_ks(), VK_PAD_A | (uint32_t)XINPUT_KEYSTROKE_KEYUP << 16);
    /* triggers use the SDK threshold; sticks the deadzones; 8-way thumb directions; ordering = state order */
    pad(FNVNX_NPAD_ZR | FNVNX_NPAD_PLUS | FNVNX_NPAD_UP, 20000, 20000, 0, -20000);
    uint32_t got[8], n = 0;
    while (n < 8 && (got[n] = next_ks())) n++;
    CHECK_EQ(n, 5);
    uint32_t want[] = {VK_PAD_DPAD_UP, VK_PAD_START, VK_PAD_RTRIGGER, 0x5825 /* LTHUMB_UPRIGHT */, 0x5831 /* RTHUMB_DOWN */};
    for (uint32_t w : want) {
        bool hit = false;
        for (uint32_t i = 0; i < n; i++) hit |= got[i] == (w | (uint32_t)XINPUT_KEYSTROKE_KEYDOWN << 16);
        CHECK(hit);
    }
    pad(0, 5000, 0, 0, 0);   /* inside the deadzone, trigger below threshold: only releases */
    for (n = 0; n < 8 && (got[n] = next_ks()); n++) CHECK_EQ(got[n] >> 16, XINPUT_KEYSTROKE_KEYUP);
    CHECK_EQ(n, 5);
    /* auto-repeat of a held key: KEYDOWN | REPEAT */
    pad(FNVNX_NPAD_B);
    drain_ks();
    usleep(450000);
    CHECK_EQ(next_ks(), VK_PAD_B | (uint32_t)(XINPUT_KEYSTROKE_KEYDOWN | XINPUT_KEYSTROKE_REPEAT) << 16);
    pad(0);
    drain_ks();
}

/* ================= DirectInput 8 */
namespace {
HWND g_w1, g_w2;
IDirectInput8A *di()
{
    void *p = nullptr;
    CHECK_EQ(DirectInput8Create((HINSTANCE)1, 0x0800, IID_IDirectInput8A, &p, nullptr), DI_OK);
    return (IDirectInput8A *)p;
}
IDirectInputDevice8A *dev(IDirectInput8A *in, const GUID &g)
{
    IDirectInputDevice8A *d = nullptr;
    CHECK_EQ(in->CreateDevice(g, &d, nullptr), DI_OK);
    return d;
}
static const GUID kClsKbd = {0x4d36e96b, 0xe325, 0x11ce, {0xbf, 0xc1, 0x08, 0x00, 0x2b, 0xe1, 0x03, 0x18}};
static const GUID kClsMouse = {0x4d36e96f, 0xe325, 0x11ce, {0xbf, 0xc1, 0x08, 0x00, 0x2b, 0xe1, 0x03, 0x18}};

struct EnumRec { int n; DWORD size; char name[2][64]; GUID inst[2]; DWORD type[2]; };
BOOL enum_cb(LPCDIDEVICEINSTANCEA d, LPVOID p)
{
    EnumRec *r = (EnumRec *)p;
    if (r->n < 2) { snprintf(r->name[r->n], 64, "%s", d->tszInstanceName); r->inst[r->n] = d->guidInstance; r->type[r->n] = d->dwDevType; }
    r->size = d->dwSize;
    r->n++;
    return DIENUM_CONTINUE;
}
BOOL stop_cb(LPCDIDEVICEINSTANCEA, LPVOID p) { ++*(int *)p; return DIENUM_STOP; }
int count_enum(IDirectInput8A *in, DWORD type, DWORD flags, HRESULT *hr = nullptr)
{
    EnumRec r = {};
    HRESULT h = in->EnumDevices(type, enum_cb, &r, flags);
    if (hr) *hr = h;
    return r.n;
}
} // namespace

TEST(contract_di8_factory_create_enum)
{
    void *p = nullptr;
    CHECK_EQ(DirectInput8Create((HINSTANCE)1, 0x0800, IID_IDirectInput8A, nullptr, nullptr), E_INVALID);
    CHECK_EQ(DirectInput8Create((HINSTANCE)1, 0x0800, IID_IDirectInput8A, &p, (LPUNKNOWN)8), DIERR_NOAGGREGATION);
    CHECK_EQ(DirectInput8Create((HINSTANCE)1, 0x0700, IID_IDirectInput8A, &p, nullptr), DIERR_OLDDIRECTINPUTVERSION);
    CHECK_EQ(DirectInput8Create((HINSTANCE)1, 0x0900, IID_IDirectInput8A, &p, nullptr), DIERR_BETADIRECTINPUTVERSION);
    CHECK_EQ(DirectInput8Create((HINSTANCE)1, 0x0800, GUID_Key, &p, nullptr), DIERR_NOINTERFACE);
    IDirectInput8A *in = di();
    IDirectInputDevice8A *d = nullptr;
    CHECK_EQ(in->CreateDevice(GUID_SysKeyboard, nullptr, nullptr), E_INVALID);
    CHECK_EQ(in->CreateDevice(GUID_SysKeyboard, &d, (LPUNKNOWN)8), DIERR_NOAGGREGATION);
    CHECK(d == nullptr);
    CHECK_EQ(in->CreateDevice(GUID_Joystick, &d, nullptr), DIERR_DEVICENOTREG);
    CHECK_EQ(in->CreateDevice(GUID_Key, &d, nullptr), DIERR_DEVICENOTREG);
    CHECK_EQ(in->CreateDevice(GUID_SysMouse, &d, nullptr), DI_OK);
    d->Release();

    /* EnumDevices: classes, device types, subtypes */
    CHECK_EQ(count_enum(in, DI8DEVCLASS_ALL, DIEDFL_ALLDEVICES), 2);
    CHECK_EQ(count_enum(in, DI8DEVCLASS_ALL, DIEDFL_ATTACHEDONLY), 2);
    CHECK_EQ(count_enum(in, DI8DEVCLASS_DEVICE, 0), 0);
    CHECK_EQ(count_enum(in, DI8DEVCLASS_POINTER, 0), 1);
    CHECK_EQ(count_enum(in, DI8DEVCLASS_KEYBOARD, 0), 1);
    CHECK_EQ(count_enum(in, DI8DEVCLASS_GAMECTRL, 0), 0);
    CHECK_EQ(count_enum(in, DI8DEVTYPE_MOUSE, 0), 1);
    CHECK_EQ(count_enum(in, DI8DEVTYPE_KEYBOARD, 0), 1);
    CHECK_EQ(count_enum(in, DI8DEVTYPE_KEYBOARD | (DI8DEVTYPEKEYBOARD_PCENH << 8), 0), 1);
    CHECK_EQ(count_enum(in, DI8DEVTYPE_KEYBOARD | (1 << 8), 0), 0);
    CHECK_EQ(count_enum(in, DI8DEVTYPE_JOYSTICK, 0), 0);
    CHECK_EQ(count_enum(in, DI8DEVTYPE_GAMEPAD, 0), 0);
    CHECK_EQ(count_enum(in, DI8DEVTYPE_DEVICE, 0), 0);
    CHECK_EQ(count_enum(in, DI8DEVCLASS_ALL, DIEDFL_FORCEFEEDBACK), 0);
    CHECK_EQ(count_enum(in, DI8DEVCLASS_ALL, 0x10000 | 0x20000 | 0x40000), 2);   /* INCLUDEALIASES / PHANTOMS / HIDDEN */
    HRESULT hr;
    count_enum(in, DI8DEVCLASS_ALL, 0x80, &hr);
    CHECK_EQ(hr, E_INVALID);
    count_enum(in, 0x30, 0, &hr);
    CHECK_EQ(hr, E_INVALID);
    count_enum(in, 5, 0, &hr);
    CHECK_EQ(hr, E_INVALID);
    CHECK_EQ(in->EnumDevices(DI8DEVCLASS_ALL, nullptr, nullptr, 0), E_INVALID);
    int stops = 0;
    CHECK_EQ(in->EnumDevices(DI8DEVCLASS_ALL, stop_cb, &stops, 0), DI_OK);
    CHECK_EQ(stops, 1);
    EnumRec r = {};
    in->EnumDevices(DI8DEVCLASS_ALL, enum_cb, &r, 0);
    CHECK_EQ(r.size, sizeof(DIDEVICEINSTANCEA));
    CHECK(!strcmp(r.name[0], "Keyboard") && !strcmp(r.name[1], "Mouse"));
    CHECK(same(r.inst[0], GUID_SysKeyboard) && same(r.inst[1], GUID_SysMouse));
    CHECK_EQ(r.type[0] & 0xFF, DI8DEVTYPE_KEYBOARD); CHECK_EQ(r.type[1] & 0xFF, DI8DEVTYPE_MOUSE);

    CHECK_EQ(in->GetDeviceStatus(GUID_SysKeyboard), DI_OK);
    CHECK_EQ(in->GetDeviceStatus(GUID_SysMouse), DI_OK);
    CHECK_EQ(in->GetDeviceStatus(GUID_Joystick), DIERR_DEVICENOTREG);

    /* RunControlPanel: no panel application here -> DIERR_GENERIC; flags / hwnd validated first */
    CHECK_EQ(in->RunControlPanel(nullptr, 1), E_INVALID);
    CHECK_EQ(in->RunControlPanel((HWND)0x1234, 0), E_INVALID);
    CHECK_EQ(in->RunControlPanel(nullptr, 0), E_FAIL_);
    CHECK_EQ(in->Initialize(nullptr, 0x0800), E_INVALID);
    CHECK_EQ(in->Initialize((HINSTANCE)1, 0x0700), DIERR_OLDDIRECTINPUTVERSION);
    CHECK_EQ(in->Initialize((HINSTANCE)1, 0x0900), DIERR_BETADIRECTINPUTVERSION);
    CHECK_EQ(in->Initialize((HINSTANCE)1, 0x0800), DI_OK);

    GUID g;
    CHECK_EQ(in->FindDevice(kClsKbd, "Keyboard", &g), DI_OK);
    CHECK(same(g, GUID_SysKeyboard));
    CHECK_EQ(in->FindDevice(kClsMouse, "mouse", &g), DI_OK);
    CHECK(same(g, GUID_SysMouse));
    CHECK_EQ(in->FindDevice(kClsMouse, "Keyboard", &g), DIERR_DEVICENOTREG);
    CHECK_EQ(in->FindDevice(kClsKbd, "Gamepad", &g), DIERR_DEVICENOTREG);
    CHECK_EQ(in->FindDevice(kClsKbd, nullptr, &g), E_INVALID);
    CHECK_EQ(in->FindDevice(kClsKbd, "Keyboard", nullptr), E_INVALID);
    in->Release();

    /* CoCreateInstance route: born uninitialised */
    void *q = nullptr;
    CHECK_EQ(fnvnx_dinput_com_factory(&IID_IDirectInput8A, &q), DI_OK);
    IDirectInput8A *c = (IDirectInput8A *)q;
    CHECK_EQ(c->CreateDevice(GUID_SysKeyboard, &d, nullptr), DIERR_NOTINITIALIZED);
    CHECK_EQ(c->EnumDevices(DI8DEVCLASS_ALL, enum_cb, &r, 0), DIERR_NOTINITIALIZED);
    CHECK_EQ(c->Initialize((HINSTANCE)1, 0x0800), DI_OK);
    CHECK_EQ(c->CreateDevice(GUID_SysKeyboard, &d, nullptr), DI_OK);
    d->Release();
    c->Release();
}

namespace {
/* DIACTIONA / DIACTIONFORMATA as the SDK lays them out (ILP32) */
struct Act { UINT_PTR app; DWORD sem, flags; LPCSTR name; GUID inst; DWORD obj, how; };
struct ActFmt { DWORD size, actsize, datasize, n; Act *a; GUID map; DWORD genre, bufsize; LONG amin, amax; HINSTANCE hinst; DWORD ft0, ft1, crc; CHAR name[MAX_PATH]; };
struct ImgHdr { DWORD size, imgsize, views, buttons, axes, povs, bufsize, bufused; void *img; };
ActFmt make_fmt(Act *a, DWORD n, DWORD genre = 0)
{
    ActFmt f;
    memset(&f, 0, sizeof f);
    f.size = sizeof f; f.actsize = sizeof(Act); f.datasize = 16; f.n = n; f.a = a; f.genre = genre;
    return f;
}
struct SemRec { int n; DWORD flags[2], remaining[2]; DWORD type[2]; IDirectInputDevice8A *dev[2]; };
BOOL sem_cb(LPCDIDEVICEINSTANCEA d, IDirectInputDevice8A *dv, DWORD flags, DWORD remaining, LPVOID p)
{
    SemRec *r = (SemRec *)p;
    if (r->n < 2) { r->flags[r->n] = flags; r->remaining[r->n] = remaining; r->type[r->n] = d->dwDevType & 0xFF; dv->AddRef(); r->dev[r->n] = dv; }
    r->n++;
    return DIENUM_CONTINUE;
}
uint32_t SEM_KEY(int dik) { return 0x81000400u | (uint32_t)dik; }
DIDATAFORMAT custom(DIOBJECTDATAFORMAT *o, DWORD n, DWORD size, DWORD flags = DIDF_RELAXIS)
{
    return {sizeof(DIDATAFORMAT), sizeof(DIOBJECTDATAFORMAT), flags, size, n, o};
}
} // namespace

TEST(contract_di8_semantics_and_action_maps)
{
    IDirectInput8A *in = di();
    Act acts[4];
    memset(acts, 0, sizeof acts);
    acts[0].app = 77; acts[0].sem = SEM_KEY(DIK_A);
    acts[1].app = 88; acts[1].sem = 0x82000300;          /* DIMOUSE_XAXIS */
    acts[2].app = 99; acts[2].sem = 0x8200048C;          /* DIMOUSE_BUTTON0 */
    acts[3].app = 11; acts[3].sem = 0x12345678;          /* a genre this environment does not know */
    ActFmt f = make_fmt(acts, 4);
    SemRec r = {};
    CHECK_EQ(in->EnumDevicesBySemantics(nullptr, &f, (void *)sem_cb, &r, 0), DI_OK);
    CHECK_EQ(r.n, 2);   /* the keyboard (1 action) and the mouse (2); the unknown semantic maps nowhere */
    CHECK_EQ(r.type[0], DI8DEVTYPE_KEYBOARD); CHECK_EQ(r.type[1], DI8DEVTYPE_MOUSE);
    CHECK_EQ(r.flags[0], 1); CHECK_EQ(r.remaining[0], 1); CHECK_EQ(r.remaining[1], 0);
    CHECK_EQ(in->EnumDevicesBySemantics(nullptr, &f, (void *)sem_cb, &r, DIEDFL_FORCEFEEDBACK), DI_OK);
    CHECK_EQ(r.n, 2);   /* nothing more */
    CHECK_EQ(in->EnumDevicesBySemantics(nullptr, &f, (void *)sem_cb, &r, 0x8), E_INVALID);
    CHECK_EQ(in->EnumDevicesBySemantics(nullptr, &f, nullptr, &r, 0), E_INVALID);
    CHECK_EQ(in->EnumDevicesBySemantics(nullptr, nullptr, (void *)sem_cb, &r, 0), E_INVALID);
    Act only = {};
    only.sem = SEM_KEY(DIK_A);
    ActFmt kf = make_fmt(&only, 1);
    SemRec k1 = {};
    in->EnumDevicesBySemantics(nullptr, &kf, (void *)sem_cb, &k1, 0);
    CHECK_EQ(k1.n, 1); CHECK_EQ(k1.type[0], DI8DEVTYPE_KEYBOARD);
    Act unk = {};
    unk.sem = 0x12345678;
    ActFmt uf = make_fmt(&unk, 1, 0xFFFFFF00u);   /* DIGENRE_ANY: devices enumerate with no mapped action */
    SemRec u1 = {};
    in->EnumDevicesBySemantics(nullptr, &uf, (void *)sem_cb, &u1, 0);
    CHECK_EQ(u1.n, 2); CHECK_EQ(u1.flags[0], 0);
    ActFmt uf2 = make_fmt(&unk, 1);
    SemRec u2 = {};
    in->EnumDevicesBySemantics(nullptr, &uf2, (void *)sem_cb, &u2, 0);
    CHECK_EQ(u2.n, 0);

    /* BuildActionMap on the keyboard device, then SetActionMap: events carry the action's uAppData, dwOfs = 4 * index */
    IDirectInputDevice8A *k = r.dev[0], *m = r.dev[1];
    CHECK_EQ(k->BuildActionMap(&f, nullptr, 8), E_INVALID);
    CHECK_EQ(k->BuildActionMap(nullptr, nullptr, 0), E_INVALID);
    CHECK_EQ(k->BuildActionMap(&f, nullptr, 0), DI_OK);
    CHECK(same(acts[0].inst, GUID_SysKeyboard)); CHECK_EQ(acts[0].how, 0x20);
    CHECK_EQ(acts[0].obj, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(DIK_A));
    CHECK_EQ(acts[1].how, 0); CHECK_EQ(acts[3].how, 0);
    CHECK_EQ(m->BuildActionMap(&f, nullptr, 0), DI_OK);
    CHECK(same(acts[1].inst, GUID_SysMouse)); CHECK_EQ(acts[1].how, 0x20);
    CHECK(same(acts[0].inst, GUID_SysKeyboard));   /* the keyboard's mapping is kept */
    CHECK_EQ(acts[2].obj, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(0));
    f.datasize = 16; f.bufsize = 8;
    CHECK_EQ(k->SetActionMap(&f, nullptr, 0), DI_OK);
    DIPROPDWORD bs = {{sizeof bs, sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, 0};
    CHECK_EQ(k->GetProperty(PR(1), &bs.diph), DI_OK); CHECK_EQ(bs.dwData, 8);
    CHECK_EQ(k->Acquire(), DI_OK);
    CHECK_EQ(k->SetActionMap(&f, nullptr, 0), DIERR_ACQUIRED);
    fnvnx_input_key(DIK_A, 1);
    DIDEVICEOBJECTDATA d[4];
    DWORD n = 4;
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 0), DI_OK);
    CHECK_EQ(n, 1); CHECK_EQ(d[0].dwOfs, 0); CHECK_EQ(d[0].uAppData, 77); CHECK_EQ(d[0].dwData, 0x80);
    uint8_t st[16];
    CHECK_EQ(k->GetDeviceState(16, st), DI_OK); CHECK_EQ(st[0], 0x80);
    fnvnx_input_key(DIK_A, 0);
    k->Unacquire();
    r.dev[0]->Release(); r.dev[1]->Release();   /* the references the callback took (k, m) */
    for (int i = 0; i < 2; i++) { k1.dev[i] && (k1.dev[i]->Release(), 0); u1.dev[i] && (u1.dev[i]->Release(), 0); }

    /* ConfigureDevices needs a display: parameters validated, then DIERR_GENERIC */
    struct CDP { DWORD size, users; LPSTR names; DWORD nfmt; ActFmt *fmts; HWND hwnd; DWORD dics[9]; IUnknown *dds; } cdp;
    memset(&cdp, 0, sizeof cdp);
    cdp.size = sizeof cdp; cdp.nfmt = 1; cdp.fmts = &f;
    CHECK_EQ(in->ConfigureDevices(nullptr, nullptr, 0, nullptr), E_INVALID);
    CHECK_EQ(in->ConfigureDevices(nullptr, &cdp, 4, nullptr), E_INVALID);
    cdp.hwnd = (HWND)0x1234;
    CHECK_EQ(in->ConfigureDevices(nullptr, &cdp, 0, nullptr), E_INVALID);
    cdp.hwnd = nullptr;
    CHECK_EQ(in->ConfigureDevices(nullptr, &cdp, 0, nullptr), E_FAIL_);
    in->Release();
}

TEST(contract_di8_device_info_objects_properties)
{
    IDirectInput8A *in = di();
    IDirectInputDevice8A *k = dev(in, GUID_SysKeyboard), *m = dev(in, GUID_SysMouse);
    void *q;
    CHECK_EQ(k->QueryInterface(IID_IDirectInputDevice8A, &q), DI_OK); k->Release();
    static const GUID dx7 = {0x57d7c6bc, 0x2356, 0x11d3, {0x8e, 0x9d, 0x00, 0xc0, 0x4f, 0x68, 0x44, 0xae}};
    CHECK_EQ(k->QueryInterface(dx7, &q), DI_OK); k->Release();
    CHECK_EQ(k->QueryInterface(GUID_Key, &q), E_NOINTERFACE);
    DIDEVCAPS c;
    memset(&c, 0, sizeof c); c.dwSize = sizeof c;
    CHECK_EQ(k->GetCapabilities(&c), DI_OK);
    CHECK_EQ(c.dwFlags, DIDC_ATTACHED); CHECK_EQ(c.dwDevType, DI8DEVTYPE_KEYBOARD | (DI8DEVTYPEKEYBOARD_PCENH << 8));
    CHECK_EQ(c.dwAxes, 0); CHECK_EQ(c.dwButtons, 256); CHECK_EQ(c.dwPOVs, 0);
    memset(&c, 0, sizeof c); c.dwSize = sizeof c;
    CHECK_EQ(m->GetCapabilities(&c), DI_OK);
    CHECK_EQ(c.dwAxes, 3); CHECK_EQ(c.dwButtons, 8); CHECK_EQ(c.dwDevType & 0xFF, DI8DEVTYPE_MOUSE);
    c.dwSize = (DWORD)X3_CAPS;
    CHECK_EQ(m->GetCapabilities(&c), DI_OK);
    c.dwSize = 30;
    CHECK_EQ(m->GetCapabilities(&c), E_INVALID);
    CHECK_EQ(m->GetCapabilities(nullptr), E_INVALID);

    DIDEVICEINSTANCEA di_;
    di_.dwSize = sizeof di_;
    CHECK_EQ(k->GetDeviceInfo(&di_), DI_OK);
    CHECK(same(di_.guidInstance, GUID_SysKeyboard)); CHECK(!strcmp(di_.tszProductName, "Keyboard"));
    di_.dwSize = (DWORD)X3_INST;
    CHECK_EQ(m->GetDeviceInfo(&di_), DI_OK);
    CHECK_EQ(di_.dwSize, X3_INST); CHECK(!strcmp(di_.tszInstanceName, "Mouse"));
    di_.dwSize = 100;
    CHECK_EQ(m->GetDeviceInfo(&di_), E_INVALID);

    /* EnumObjects: key names, usages, type filters, invalid flags */
    struct Obj { int n, axes, buttons; char a_name[32]; WORD a_page, a_usage; DWORD a_ofs; DWORD size; };
    static Obj o;
    memset(&o, 0, sizeof o);
    auto cb = [](LPCDIDEVICEOBJECTINSTANCEA x, LPVOID p) -> BOOL {
        Obj *r = (Obj *)p;
        r->n++; r->axes += (x->dwType & DIDFT_AXIS) != 0; r->buttons += (x->dwType & DIDFT_BUTTON) != 0; r->size = x->dwSize;
        if ((x->dwType & 0xFF00) == DIDFT_MAKEINSTANCE(DIK_A) && !strcmp(x->tszName, "A")) { snprintf(r->a_name, 32, "%s", x->tszName); r->a_page = x->wUsagePage; r->a_usage = x->wUsage; r->a_ofs = x->dwOfs; }
        return DIENUM_CONTINUE;
    };
    CHECK_EQ(k->EnumObjects(cb, &o, DIDFT_ALL), DI_OK);
    CHECK(o.n >= 100 && o.n <= 256); CHECK_EQ(o.buttons, o.n); CHECK_EQ(o.axes, 0); CHECK_EQ(o.size, sizeof(DIDEVICEOBJECTINSTANCEA));
    CHECK(!strcmp(o.a_name, "A")); CHECK_EQ(o.a_page, 7); CHECK_EQ(o.a_usage, 4); CHECK_EQ(o.a_ofs, DIK_A);
    int keys = o.n;
    memset(&o, 0, sizeof o);
    k->EnumObjects(cb, &o, DIDFT_AXIS); CHECK_EQ(o.n, 0);
    k->EnumObjects(cb, &o, DIDFT_POV); CHECK_EQ(o.n, 0);
    memset(&o, 0, sizeof o);
    k->EnumObjects(cb, &o, DIDFT_BUTTON); CHECK_EQ(o.n, keys);
    memset(&o, 0, sizeof o);
    k->EnumObjects(cb, &o, DIDFT_PSHBUTTON); CHECK_EQ(o.n, keys);
    memset(&o, 0, sizeof o);
    k->EnumObjects(cb, &o, DIDFT_TGLBUTTON); CHECK_EQ(o.n, 0);
    CHECK_EQ(k->EnumObjects(cb, &o, 0x40000000u), E_INVALID);
    CHECK_EQ(k->EnumObjects(nullptr, &o, 0), E_INVALID);
    memset(&o, 0, sizeof o);
    m->EnumObjects(cb, &o, DIDFT_ALL); CHECK_EQ(o.n, 11); CHECK_EQ(o.axes, 3); CHECK_EQ(o.buttons, 8);
    memset(&o, 0, sizeof o);
    m->EnumObjects(cb, &o, DIDFT_AXIS); CHECK_EQ(o.n, 3);
    memset(&o, 0, sizeof o);
    m->EnumObjects(cb, &o, DIDFT_BUTTON); CHECK_EQ(o.n, 8);

    /* GetObjectInfo: BYOFFSET / BYID / BYUSAGE, both struct sizes, errors */
    DIDEVICEOBJECTINSTANCEA oi;
    memset(&oi, 0, sizeof oi); oi.dwSize = sizeof oi;
    CHECK_EQ(k->GetObjectInfo(&oi, DIK_ESCAPE, DIPH_BYOFFSET), DI_OK); CHECK(!strcmp(oi.tszName, "Esc"));
    CHECK_EQ(oi.dwType, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(DIK_ESCAPE));
    CHECK_EQ(k->GetObjectInfo(&oi, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(DIK_UP), DIPH_BYID), DI_OK); CHECK(!strcmp(oi.tszName, "Up"));
    CHECK_EQ(k->GetObjectInfo(&oi, (7u << 16) | 4, DIPH_BYUSAGE), DI_OK); CHECK(!strcmp(oi.tszName, "A"));
    CHECK_EQ(k->GetObjectInfo(&oi, 0x55, DIPH_BYOFFSET), DIERR_OBJECTNOTFOUND);   /* a code without a key */
    CHECK_EQ(k->GetObjectInfo(&oi, 0, DIPH_DEVICE), E_INVALID);
    CHECK_EQ(k->GetObjectInfo(&oi, 0, 9), E_INVALID);
    CHECK_EQ(k->GetObjectInfo(nullptr, 1, DIPH_BYOFFSET), E_INVALID);
    struct { DIDEVICEOBJECTINSTANCEA o; uint8_t guard[8]; } g3;
    memset(&g3, 0xEE, sizeof g3);
    g3.o.dwSize = (DWORD)X3_OBJ;
    CHECK_EQ(k->GetObjectInfo(&g3.o, DIK_A, DIPH_BYOFFSET), DI_OK);
    CHECK_EQ(g3.o.dwSize, X3_OBJ); CHECK_EQ(g3.o.dwType, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(DIK_A));
    CHECK_EQ(*((uint8_t *)&g3.o + X3_OBJ), 0xEE);   /* nothing written past the DX3 size */
    CHECK_EQ(*((uint8_t *)&g3.o + X3_OBJ + 3), 0xEE);
    oi.dwSize = 100;
    CHECK_EQ(k->GetObjectInfo(&oi, DIK_A, DIPH_BYOFFSET), E_INVALID);
    oi.dwSize = sizeof oi;
    CHECK_EQ(m->GetObjectInfo(&oi, 8, DIPH_BYOFFSET), DI_OK); CHECK_EQ(oi.wUsagePage, 1); CHECK_EQ(oi.wUsage, 0x38);
    CHECK_EQ(m->GetObjectInfo(&oi, 12 + 3, DIPH_BYOFFSET), DI_OK); CHECK_EQ(oi.wUsagePage, 9); CHECK_EQ(oi.wUsage, 4);

    /* properties */
    struct PS { DIPROPHEADER h; WCHAR w[MAX_PATH]; } ps;
    memset(&ps, 0, sizeof ps);
    ps.h = {sizeof ps, sizeof(DIPROPHEADER), DIK_A, DIPH_BYOFFSET};
    CHECK_EQ(k->GetProperty(PR(20), &ps.h), DI_OK); CHECK(ps.w[0] == 'A' && ps.w[1] == 0);   /* DIPROP_KEYNAME */
    ps.h.dwObj = 0;
    CHECK_EQ(m->GetProperty(PR(20), &ps.h), DIERR_UNSUPPORTED);   /* the mouse has no key names */
    ps.h.dwObj = 0x55;
    CHECK_EQ(k->GetProperty(PR(20), &ps.h), DIERR_OBJECTNOTFOUND);
    ps.h = {sizeof ps, sizeof(DIPROPHEADER), 0, DIPH_DEVICE};
    CHECK_EQ(k->GetProperty(PR(14), &ps.h), DI_OK); CHECK(ps.w[0] == 'K');   /* PRODUCTNAME */
    CHECK_EQ(m->GetProperty(PR(13), &ps.h), DI_OK); CHECK(ps.w[0] == 'M');   /* INSTANCENAME */
    CHECK_EQ(k->GetProperty(PR(26), &ps.h), DI_OK);                          /* TYPENAME */
    ps.h.dwSize = 20;
    CHECK_EQ(k->GetProperty(PR(14), &ps.h), E_INVALID);                      /* wrong size for a string property */
    struct PG { DIPROPHEADER h; GUID cls; WCHAR path[MAX_PATH]; } pg;
    memset(&pg, 0, sizeof pg);
    pg.h = {sizeof pg, sizeof(DIPROPHEADER), 0, DIPH_DEVICE};
    CHECK_EQ(k->GetProperty(PR(12), &pg.h), DI_OK); CHECK(pg.path[0] != 0);  /* GUIDANDPATH */
    DIPROPDWORD pd = {{sizeof pd, sizeof(DIPROPHEADER), DIK_A, DIPH_BYOFFSET}, 0};
    CHECK_EQ(k->GetProperty(PR(23), &pd.diph), DI_OK); CHECK_EQ(pd.dwData, DIK_A);   /* SCANCODE */
    pd.diph.dwObj = DIK_RCONTROL;
    CHECK_EQ(k->GetProperty(PR(23), &pd.diph), DI_OK); CHECK_EQ(pd.dwData, 0xE01D);
    pd.diph = {sizeof pd, sizeof(DIPROPHEADER), 0, DIPH_DEVICE};
    CHECK_EQ(k->GetProperty(PR(24), &pd.diph), DIERR_UNSUPPORTED);           /* VIDPID */
    CHECK_EQ(k->GetProperty(PR(5), &pd.diph), DIERR_UNSUPPORTED);            /* DEADZONE */
    CHECK_EQ(k->GetProperty(PR(6), &pd.diph), DIERR_UNSUPPORTED);            /* SATURATION */
    CHECK_EQ(k->GetProperty(PR(99), &pd.diph), DIERR_UNSUPPORTED);
    CHECK_EQ(k->GetProperty(PR(2), &pd.diph), DIERR_UNSUPPORTED);            /* AXISMODE: no axes */
    CHECK_EQ(k->SetProperty(PR(5), &pd.diph), DIERR_UNSUPPORTED);
    CHECK_EQ(k->SetProperty(PR(24), &pd.diph), DIERR_UNSUPPORTED);
    CHECK_EQ(k->GetProperty(PR(1), nullptr), E_INVALID);
    pd.diph.dwHeaderSize = 8;
    CHECK_EQ(k->GetProperty(PR(1), &pd.diph), E_INVALID);
    pd.diph = {sizeof pd, sizeof(DIPROPHEADER), 4, DIPH_DEVICE};
    CHECK_EQ(k->GetProperty(PR(1), &pd.diph), E_INVALID);                    /* DIPH_DEVICE needs dwObj 0 */
    pd.diph = {sizeof pd, sizeof(DIPROPHEADER), 0, 7};
    CHECK_EQ(k->GetProperty(PR(1), &pd.diph), E_INVALID);                    /* unknown dwHow */
    pd.diph = {16, sizeof(DIPROPHEADER), 0, DIPH_DEVICE};
    CHECK_EQ(k->GetProperty(PR(1), &pd.diph), E_INVALID);                    /* header only, no data */
    /* mouse: GRANULARITY of the object, RANGE not available on relative axes */
    pd.diph = {sizeof pd, sizeof(DIPROPHEADER), 0, DIPH_BYOFFSET};
    CHECK_EQ(m->GetProperty(PR(3), &pd.diph), DI_OK); CHECK_EQ(pd.dwData, 1);
    pd.diph.dwObj = 8;
    CHECK_EQ(m->GetProperty(PR(3), &pd.diph), DI_OK); CHECK_EQ(pd.dwData, 120);
    pd.diph.dwObj = 12;
    CHECK_EQ(m->GetProperty(PR(3), &pd.diph), DIERR_UNSUPPORTED);            /* a button has no granularity */
    DIPROPRANGE pr = {{sizeof pr, sizeof(DIPROPHEADER), 0, DIPH_BYOFFSET}, -10, 10};
    CHECK_EQ(m->GetProperty(PR(4), &pr.diph), DIERR_UNSUPPORTED);
    CHECK_EQ(m->SetProperty(PR(4), &pr.diph), DIERR_UNSUPPORTED);
    CHECK_EQ(m->SetProperty(PR(3), &pd.diph), DIERR_UNSUPPORTED);            /* read-only */
    /* BUFFERSIZE / AXISMODE: values, no-effect, validation, ACQUIRED */
    DIPROPDWORD bs = {{sizeof bs, sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, 16};
    CHECK_EQ(m->SetProperty(PR(1), &bs.diph), DI_OK);
    CHECK_EQ(m->SetProperty(PR(1), &bs.diph), DI_PROPNOEFFECT);
    bs.dwData = 0;
    CHECK_EQ(m->GetProperty(PR(1), &bs.diph), DI_OK); CHECK_EQ(bs.dwData, 16);
    bs.dwData = 5;
    CHECK_EQ(m->SetProperty(PR(2), &bs.diph), E_INVALID);                    /* neither ABS nor REL */
    bs.dwData = DIPROPAXISMODE_ABS;
    CHECK_EQ(m->SetProperty(PR(2), &bs.diph), DI_OK);
    bs.dwData = 0;
    CHECK_EQ(m->GetProperty(PR(2), &bs.diph), DI_OK); CHECK_EQ(bs.dwData, DIPROPAXISMODE_ABS);
    bs.dwData = DIPROPAXISMODE_REL;
    m->SetProperty(PR(2), &bs.diph);
    DIOBJECTDATAFORMAT mo[2] = {{&GUID_XAxis, 0, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0}, {&GUID_Button, 4, DIDFT_BUTTON | DIDFT_ANYINSTANCE, 0}};
    DIDATAFORMAT mf = custom(mo, 2, 8);
    CHECK_EQ(m->SetDataFormat(&mf), DI_OK);
    CHECK_EQ(m->Acquire(), DI_OK);
    bs.dwData = DIPROPAXISMODE_ABS;
    CHECK_EQ(m->SetProperty(PR(1), &bs.diph), DIERR_ACQUIRED);
    CHECK_EQ(m->SetProperty(PR(2), &bs.diph), DIERR_ACQUIRED);
    m->Unacquire();
    k->Release(); m->Release(); in->Release();
}

TEST(contract_di8_custom_data_formats)
{
    IDirectInput8A *in = di();
    IDirectInputDevice8A *m = dev(in, GUID_SysMouse), *k = dev(in, GUID_SysKeyboard);
    /* arbitrary mouse layout: buttons at 0 and 1, X axis at 4 */
    DIOBJECTDATAFORMAT o[3] = {{&GUID_Button, 0, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(1), 0},
                               {&GUID_Button, 1, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(0), 0},
                               {&GUID_XAxis, 4, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0}};
    DIDATAFORMAT f = custom(o, 3, 8);
    CHECK_EQ(m->SetDataFormat(&f), DI_OK);
    /* validation: flags, alignment, bounds, overlap, mandatory objects */
    f.dwFlags = 0; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);
    f.dwFlags = DIDF_ABSAXIS | DIDF_RELAXIS; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);
    f.dwFlags = 4; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);
    f.dwFlags = DIDF_RELAXIS;
    o[2].dwOfs = 2; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);        /* DWORD axis misaligned */
    o[2].dwOfs = 8; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);        /* past dwDataSize */
    o[2].dwOfs = 0; o[0].dwOfs = 2; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);   /* button inside the axis: overlap */
    o[2].dwOfs = 4; o[0].dwOfs = 0;
    o[1].dwOfs = 0; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);        /* two buttons on one byte */
    o[1].dwOfs = 1;
    f.dwDataSize = 6; CHECK_EQ(m->SetDataFormat(&f), E_INVALID);      /* not a DWORD multiple */
    f.dwDataSize = 8;
    o[1].pguid = &GUID_YAxis; o[1].dwType = DIDFT_AXIS | DIDFT_MAKEINSTANCE(7);
    CHECK_EQ(m->SetDataFormat(&f), E_INVALID);                        /* no such object, not optional */
    o[1].dwType |= DIDFT_OPTIONAL;
    CHECK_EQ(m->SetDataFormat(&f), DI_OK);                            /* optional: skipped */
    o[1] = {&GUID_Button, 1, DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(0), 0};
    CHECK_EQ(m->SetDataFormat(&f), DI_OK);
    CHECK_EQ(m->SetDataFormat(nullptr), E_INVALID);
    DIDATAFORMAT bad = f; bad.dwSize = 8;
    CHECK_EQ(m->SetDataFormat(&bad), E_INVALID);
    bad = f; bad.dwObjSize = 8;
    CHECK_EQ(m->SetDataFormat(&bad), E_INVALID);

    CHECK_EQ(m->Acquire(), DI_OK);
    CHECK_EQ(m->SetDataFormat(&f), DIERR_ACQUIRED);
    uint8_t buf[8];
    CHECK_EQ(m->GetDeviceState(7, buf), E_INVALID);
    CHECK_EQ(m->GetDeviceState(8, nullptr), E_INVALID);
    int32_t base;
    m->GetDeviceState(8, buf);   /* flush relative motion */
    fnvnx_input_mouse_button(0, 1);
    fnvnx_input_mouse_move(7, 3, 0);
    fnvnx_input_mouse_move(2, 0, 0);
    CHECK_EQ(m->GetDeviceState(8, buf), DI_OK);
    CHECK_EQ(buf[0], 0);          /* instance 1 (right) at offset 0 */
    CHECK_EQ(buf[1], 0x80);       /* instance 0 (left) at offset 1 */
    memcpy(&base, buf + 4, 4); CHECK_EQ(base, 9);
    m->GetDeviceState(8, buf);
    memcpy(&base, buf + 4, 4); CHECK_EQ(base, 0);   /* relative: deltas since the last call */
    fnvnx_input_mouse_button(0, 0);
    m->Unacquire();

    /* DIDF_ABSAXIS: the axis accumulates and stays on the screen */
    f.dwFlags = DIDF_ABSAXIS;
    CHECK_EQ(m->SetDataFormat(&f), DI_OK);
    DIPROPDWORD am = {{sizeof am, sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, 0};
    CHECK_EQ(m->GetProperty(PR(2), &am.diph), DI_OK); CHECK_EQ(am.dwData, DIPROPAXISMODE_ABS);
    DIDEVICEOBJECTINSTANCEA oi;
    oi.dwSize = sizeof oi;
    CHECK_EQ(m->GetObjectInfo(&oi, 4, DIPH_BYOFFSET), DI_OK); CHECK(oi.dwType & DIDFT_ABSAXIS);
    m->Acquire();
    fnvnx_input_mouse_move(-100000, 0, 0);
    m->GetDeviceState(8, buf);
    memcpy(&base, buf + 4, 4); CHECK_EQ(base, 0);
    fnvnx_input_mouse_move(10, 0, 0);
    fnvnx_input_mouse_move(5, 0, 0);
    m->GetDeviceState(8, buf);
    memcpy(&base, buf + 4, 4); CHECK_EQ(base, 15);
    m->GetDeviceState(8, buf);
    memcpy(&base, buf + 4, 4); CHECK_EQ(base, 15);   /* absolute: no reset between calls */
    fnvnx_input_mouse_move(1000000, 0, 0);
    m->GetDeviceState(8, buf);
    memcpy(&base, buf + 4, 4); CHECK(base > 15 && base < 100000);
    m->Unacquire();
    f.dwFlags = DIDF_RELAXIS;
    m->SetDataFormat(&f);

    /* keyboard subset: only A and S, A at 3, S at 1 */
    DIOBJECTDATAFORMAT ko[2] = {{&GUID_Key, 3, DIDFT_BUTTON | DIDFT_MAKEINSTANCE(DIK_A), 0}, {&GUID_Key, 1, DIDFT_BUTTON | DIDFT_MAKEINSTANCE(DIK_S), 0}};
    DIDATAFORMAT kf = custom(ko, 2, 4);
    CHECK_EQ(k->SetDataFormat(&kf), DI_OK);
    CHECK_EQ(k->Acquire(), DI_OK);
    fnvnx_input_key(DIK_A, 1); fnvnx_input_key(DIK_D, 1);
    CHECK_EQ(k->GetDeviceState(4, buf), DI_OK);
    CHECK_EQ(buf[3], 0x80); CHECK_EQ(buf[1], 0); CHECK_EQ(buf[0], 0);
    fnvnx_input_key(DIK_A, 0); fnvnx_input_key(DIK_D, 0);
    k->Unacquire();
    /* an absent key that is not optional fails: DIK 0x55 has no key */
    ko[0] = {&GUID_Key, 0, DIDFT_BUTTON | DIDFT_MAKEINSTANCE(0x55), 0};
    CHECK_EQ(k->SetDataFormat(&kf), E_INVALID);
    k->Release(); m->Release(); in->Release();
}

TEST(contract_di8_cooperative_acquire_foreground)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = [](HWND h, UINT m, WPARAM w, LPARAM l) -> LRESULT { return DefWindowProcA(h, m, w, l); };
    wc.lpszClassName = "s7ic_window";
    RegisterClassA(&wc);
    g_w1 = CreateWindowExA(0, "s7ic_window", "w1", 0x10000000 /* WS_VISIBLE */, 0, 0, 100, 100, NULL, NULL, NULL, NULL);
    g_w2 = CreateWindowExA(0, "s7ic_window", "w2", 0x10000000, 0, 0, 100, 100, NULL, NULL, NULL, NULL);
    CHECK(g_w1 && g_w2);
    SetForegroundWindow(g_w1);
    CHECK(GetForegroundWindow() == g_w1);
    IDirectInput8A *in = di();
    IDirectInputDevice8A *k = dev(in, GUID_SysKeyboard);
    DIOBJECTDATAFORMAT ko[1] = {{&GUID_Key, 0, DIDFT_BUTTON | DIDFT_ANYINSTANCE, DIDFT_OPTIONAL * 0}};
    ko[0].dwType = DIDFT_BUTTON | DIDFT_MAKEINSTANCE(DIK_A);
    DIDATAFORMAT kf = custom(ko, 1, 4);
    CHECK_EQ(k->Acquire(), E_INVALID);                  /* no data format yet */
    CHECK_EQ(k->SetDataFormat(&kf), DI_OK);
    /* SetCooperativeLevel: flag combinations, window, unsupported combination */
    CHECK_EQ(k->SetCooperativeLevel(g_w1, 0), E_INVALID);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_EXCLUSIVE | DISCL_NONEXCLUSIVE | DISCL_FOREGROUND), E_INVALID);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_EXCLUSIVE), E_INVALID);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_EXCLUSIVE | DISCL_FOREGROUND | DISCL_BACKGROUND), E_INVALID);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_NONEXCLUSIVE | DISCL_FOREGROUND | 0x100), E_INVALID);
    CHECK_EQ(k->SetCooperativeLevel((HWND)0x7777, DISCL_NONEXCLUSIVE | DISCL_FOREGROUND), E_INVALID);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_EXCLUSIVE | DISCL_BACKGROUND), DIERR_UNSUPPORTED);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_NONEXCLUSIVE | DISCL_FOREGROUND | DISCL_NOWINKEY), DI_OK);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_NONEXCLUSIVE | DISCL_FOREGROUND), DI_OK);
    CHECK_EQ(k->Poll(), DIERR_NOTACQUIRED);
    CHECK_EQ(k->Acquire(), DI_OK);
    CHECK_EQ(k->Acquire(), S_FALSE);                    /* already acquired */
    CHECK_EQ(k->Poll(), DI_NOEFFECT);
    CHECK_EQ(k->SetCooperativeLevel(g_w1, DISCL_NONEXCLUSIVE | DISCL_BACKGROUND), DIERR_ACQUIRED);
    CHECK_EQ(k->Unacquire(), DI_OK);
    CHECK_EQ(k->Unacquire(), S_FALSE);                  /* not acquired */
    CHECK_EQ(k->Acquire(), DI_OK);
    /* the window loses the foreground: INPUTLOST once, NOTACQUIRED after; no re-acquire until it is foreground again */
    uint8_t st[4];
    CHECK_EQ(k->GetDeviceState(4, st), DI_OK);
    SetForegroundWindow(g_w2);
    CHECK_EQ(k->GetDeviceState(4, st), DIERR_INPUTLOST);
    CHECK_EQ(k->GetDeviceState(4, st), DIERR_NOTACQUIRED);
    CHECK_EQ(k->Acquire(), DIERR_OTHERAPPHASPRIO);
    /* a background device is unaffected */
    IDirectInputDevice8A *b = dev(in, GUID_SysKeyboard);
    b->SetDataFormat(&kf);
    CHECK_EQ(b->SetCooperativeLevel(g_w1, DISCL_NONEXCLUSIVE | DISCL_BACKGROUND), DI_OK);
    CHECK_EQ(b->Acquire(), DI_OK);
    CHECK_EQ(b->GetDeviceState(4, st), DI_OK);
    /* event notification: set on a state change; one handle per device */
    HANDLE ev = CreateEventA(NULL, FALSE, FALSE, NULL);
    b->Unacquire();
    CHECK_EQ(b->SetEventNotification(ev), DI_OK);
    CHECK_EQ(k->SetEventNotification(ev), DIERR_HANDLEEXISTS);
    b->Acquire();
    CHECK_EQ(b->SetEventNotification(ev), DIERR_ACQUIRED);
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_TIMEOUT);
    fnvnx_input_key(DIK_A, 1);
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_OBJECT_0);
    fnvnx_input_key(DIK_A, 0);
    b->Unacquire();
    CHECK_EQ(b->SetEventNotification(nullptr), DI_OK);
    SetForegroundWindow(g_w1);
    CHECK_EQ(k->Acquire(), DI_OK);
    k->Unacquire();
    b->Release(); k->Release(); in->Release();
    CloseHandle(ev);
    DestroyWindow(g_w1); DestroyWindow(g_w2);
}

TEST(contract_di8_buffered_data)
{
    IDirectInput8A *in = di();
    IDirectInputDevice8A *k = dev(in, GUID_SysKeyboard), *m = dev(in, GUID_SysMouse);
    DIOBJECTDATAFORMAT ko[2] = {{&GUID_Key, 0, DIDFT_BUTTON | DIDFT_MAKEINSTANCE(DIK_A), 0}, {&GUID_Key, 1, DIDFT_BUTTON | DIDFT_MAKEINSTANCE(DIK_S), 0}};
    DIOBJECTDATAFORMAT mo[1] = {{&GUID_XAxis, 0, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0}};
    DIDATAFORMAT kf = custom(ko, 2, 4), mf = custom(mo, 1, 4);
    k->SetDataFormat(&kf); m->SetDataFormat(&mf);
    DIDEVICEOBJECTDATA d[8];
    DWORD n = 8;
    k->Acquire();
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 0), DIERR_NOTBUFFERED);
    DIPROPDWORD bs = {{sizeof bs, sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, 0};
    k->Unacquire();
    bs.dwData = 4;
    CHECK_EQ(k->SetProperty(PR(1), &bs.diph), DI_OK);
    bs.dwData = 2;
    CHECK_EQ(m->SetProperty(PR(1), &bs.diph), DI_OK);
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 0), DIERR_NOTACQUIRED);
    k->Acquire(); m->Acquire();
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, nullptr, 0), E_INVALID);
    CHECK_EQ(k->GetDeviceData(12, d, &n, 0), E_INVALID);
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 2), E_INVALID);
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 0xFF), E_INVALID);
    /* dwSequence is monotonic across devices and is assigned when the event happens */
    fnvnx_input_key(DIK_A, 1);
    fnvnx_input_mouse_move(4, 0, 0);
    fnvnx_input_key(DIK_S, 1);
    fnvnx_input_key(DIK_D, 1);   /* not in the format: no event */
    DIDEVICEOBJECTDATA mm[2];
    usleep(20000);
    n = 8;
    CHECK_EQ(k->GetDeviceData(sizeof d[0], nullptr, &n, DIGDD_PEEK), DI_OK);   /* NULL rgdod + PEEK: count only */
    CHECK_EQ(n, 2);
    n = 8;
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, DIGDD_PEEK), DI_OK);
    CHECK_EQ(n, 2);
    n = 8;
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 0), DI_OK);
    CHECK_EQ(n, 2);
    DWORD mn = 2;
    CHECK_EQ(m->GetDeviceData(sizeof mm[0], mm, &mn, 0), DI_OK);
    CHECK_EQ(mn, 1);
    CHECK(d[0].dwSequence < mm[0].dwSequence && mm[0].dwSequence < d[1].dwSequence);
    CHECK_EQ(d[0].dwOfs, 0); CHECK_EQ(d[1].dwOfs, 1); CHECK_EQ(d[0].dwData, 0x80);
    CHECK((UINT_PTR)d[0].uAppData == (UINT_PTR)-1);
    DWORD now = GetTickCount();
    CHECK(now - d[0].dwTimeStamp < 2000);
    fnvnx_input_key(DIK_A, 0); fnvnx_input_key(DIK_S, 0); fnvnx_input_key(DIK_D, 0);
    n = 0;
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 0), DI_OK);   /* want 0: nothing removed */
    CHECK_EQ(n, 0);
    n = 8;
    CHECK_EQ(k->GetDeviceData(sizeof d[0], nullptr, &n, 0), DI_OK);   /* NULL rgdod without PEEK: discard */
    CHECK_EQ(n, 2);
    n = 8;
    k->GetDeviceData(sizeof d[0], d, &n, 0);
    CHECK_EQ(n, 0);
    /* overflow: 5 events into 2 slots */
    for (int i = 0; i < 5; i++) fnvnx_input_mouse_move(1, 0, 0);
    mn = 8;
    CHECK_EQ(m->GetDeviceData(sizeof mm[0], d, &mn, 0), DI_BUFFEROVERFLOW);
    CHECK_EQ(mn, 2);
    mn = 8;
    CHECK_EQ(m->GetDeviceData(sizeof mm[0], d, &mn, 0), DI_OK);   /* reported once */
    /* the DX3 form: 16-byte records */
    fnvnx_input_key(DIK_A, 1);
    struct R16 { DWORD ofs, data, ts, seq; } r16[2];
    n = 2;
    CHECK_EQ(k->GetDeviceData(sizeof r16[0], (LPDIDEVICEOBJECTDATA)r16, &n, 0), DI_OK);
    CHECK_EQ(n, 1); CHECK_EQ(r16[0].data, 0x80);
    fnvnx_input_key(DIK_A, 0);
    n = 8;
    k->GetDeviceData(sizeof d[0], d, &n, 0);
    k->Release(); m->Release(); in->Release();
}

TEST(contract_di8_force_feedback_absent)
{
    IDirectInput8A *in = di();
    for (int i = 0; i < 2; i++) {
        IDirectInputDevice8A *d = dev(in, i ? GUID_SysMouse : GUID_SysKeyboard);
        DIDEVCAPS c;
        c.dwSize = sizeof c;
        d->GetCapabilities(&c);
        CHECK(!(c.dwFlags & 0x2 /* DIDC_FORCEFEEDBACK is 0x100 */) && !(c.dwFlags & 0x100));
        IDirectInputEffect *e = (IDirectInputEffect *)8;
        char eff[64] = {};
        CHECK_EQ(d->CreateEffect(GUID_Key, eff, &e, nullptr), DIERR_UNSUPPORTED);
        CHECK(e == nullptr);
        CHECK_EQ(d->CreateEffect(GUID_Key, eff, &e, (LPUNKNOWN)8), DIERR_NOAGGREGATION);
        CHECK_EQ(d->CreateEffect(GUID_Key, nullptr, &e, nullptr), E_INVALID);
        int calls = 0;
        auto ecb = [](void *, void *p) -> BOOL { ++*(int *)p; return TRUE; };
        typedef BOOL (*ecb_t)(void *, void *);
        ecb_t cb = (ecb_t)ecb;
        CHECK_EQ(d->EnumEffects((void *)cb, &calls, 0), DI_OK);
        CHECK_EQ(d->EnumEffects(nullptr, &calls, 0), E_INVALID);
        CHECK_EQ(d->EnumCreatedEffectObjects((void *)cb, &calls, 0), DI_OK);
        CHECK_EQ(d->EnumCreatedEffectObjects((void *)cb, &calls, 1), E_INVALID);
        CHECK_EQ(d->EnumCreatedEffectObjects(nullptr, &calls, 0), E_INVALID);
        CHECK_EQ(calls, 0);
        char ei[64] = {};
        DWORD st;
        CHECK_EQ(d->GetEffectInfo(ei, GUID_Key), DIERR_UNSUPPORTED);
        CHECK_EQ(d->GetEffectInfo(nullptr, GUID_Key), E_INVALID);
        CHECK_EQ(d->GetForceFeedbackState(&st), DIERR_UNSUPPORTED);
        CHECK_EQ(d->GetForceFeedbackState(nullptr), E_INVALID);
        CHECK_EQ(d->SendForceFeedbackCommand(1), DIERR_UNSUPPORTED);
        CHECK_EQ(d->Escape(ei), DIERR_UNSUPPORTED);
        CHECK_EQ(d->Escape(nullptr), E_INVALID);
        DIDEVICEOBJECTDATA od[1] = {};
        DWORD n = 1;
        CHECK_EQ(d->SendDeviceData(sizeof od[0], od, &n, 0), DIERR_UNSUPPORTED);
        CHECK_EQ(d->SendDeviceData(sizeof od[0], nullptr, &n, 0), E_INVALID);
        CHECK_EQ(d->EnumEffectsInFile("x.ffe", (void *)cb, &calls, 0), DIERR_UNSUPPORTED);
        CHECK_EQ(d->EnumEffectsInFile(nullptr, (void *)cb, &calls, 0), E_INVALID);
        CHECK_EQ(d->WriteEffectToFile("x.ffe", 1, ei, 0), DIERR_UNSUPPORTED);
        CHECK_EQ(d->WriteEffectToFile(nullptr, 1, ei, 0), E_INVALID);
        CHECK_EQ(d->RunControlPanel(nullptr, 3), E_INVALID);
        CHECK_EQ(d->RunControlPanel(nullptr, 0), E_FAIL_);
        const GUID &own = i ? GUID_SysMouse : GUID_SysKeyboard;
        CHECK_EQ(d->Initialize(nullptr, 0x0800, own), E_INVALID);
        CHECK_EQ(d->Initialize((HINSTANCE)1, 0x0700, own), DIERR_OLDDIRECTINPUTVERSION);
        CHECK_EQ(d->Initialize((HINSTANCE)1, 0x0900, own), DIERR_BETADIRECTINPUTVERSION);
        CHECK_EQ(d->Initialize((HINSTANCE)1, 0x0800, own), DI_OK);
        CHECK_EQ(d->Initialize((HINSTANCE)1, 0x0800, GUID_Joystick), DIERR_ALREADYINITIALIZED);
        ImgHdr ih = {sizeof ih, sizeof(ImgInfo), 5, 5, 5, 5, 0, 0, nullptr};
        CHECK_EQ(d->GetImageInfo(&ih), DI_OK);
        CHECK_EQ(ih.views, 0); CHECK_EQ(ih.bufused, 0);
        ih.imgsize = 8;
        CHECK_EQ(d->GetImageInfo(&ih), E_INVALID);
        CHECK_EQ(d->GetImageInfo(nullptr), E_INVALID);
        d->Release();
    }
    in->Release();
}

TEST(contract_di8_keymap_covers_the_keyboard)
{
    IDirectInput8A *in = di();
    IDirectInputDevice8A *k = dev(in, GUID_SysKeyboard);
    static int usage_of[256], keys;
    memset(usage_of, 0, sizeof usage_of);
    keys = 0;
    auto cb = [](LPCDIDEVICEOBJECTINSTANCEA x, LPVOID) -> BOOL { usage_of[DIDFT_GETINSTANCE(x->dwType)] = x->wUsage; keys++; return TRUE; };
    k->EnumObjects(cb, nullptr, DIDFT_BUTTON);
    /* every key object reachable from a HID keyboard usage, except the consumer-page / ACPI keys, which have none */
    static const int consumer[] = {0x90, 0x99, 0xA2, 0xA4, 0xDF};
    int unreachable = 0;
    for (int d = 1; d < 256; d++) {
        DIDEVICEOBJECTINSTANCEA oi;
        oi.dwSize = sizeof oi;
        if (k->GetObjectInfo(&oi, d, DIPH_BYOFFSET) != DI_OK) continue;   /* no key */
        bool allowed = false;
        for (int c : consumer) allowed |= c == d;
        if (!usage_of[d] && !allowed) { unreachable++; fprintf(stderr, "  no HID usage for DIK 0x%02X (%s)\n", d, oi.tszName); }
        if (usage_of[d]) CHECK_EQ(fnvnx_hid_usage_to_dik((uint8_t)usage_of[d]), d);   /* the mapping round-trips */
    }
    CHECK_EQ(unreachable, 0);
    CHECK_EQ(fnvnx_hid_usage_to_dik(104), 0x64); CHECK_EQ(fnvnx_hid_usage_to_dik(106), 0x66);   /* F13..F15 */
    CHECK_EQ(fnvnx_hid_usage_to_dik(103), 0x8D);                                                 /* keypad = */
    k->Release(); in->Release();
}
