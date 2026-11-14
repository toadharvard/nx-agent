/* dinput.cpp - DirectInput 8 (ANSI): the system keyboard and mouse (docs/AUDIO_INPUT.md "DirectInput").
 *
 * Decision: keyboard and mouse exist (fed by a USB keyboard/mouse and touch-as-mouse on the Switch, through
 * input.h); game controllers are not enumerated through DirectInput - the gamepad is XInput only, which the
 * engine supports fully. Data formats are interpreted generically from the DIDATAFORMAT the engine passes
 * (its own copies of c_dfDIKeyboard / c_dfDIMouse2 live in the exe's .rdata), so any layout works: every format
 * object is matched against the device's objects by type, instance and GUID. Immediate (GetDeviceState) and
 * buffered (DIPROP_BUFFERSIZE + GetDeviceData) access are both implemented; mouse axes are relative by default.
 */
#include <dinput.h>
#include "../include/fnvnx_win32_ext.h"

#include "input.h"
#include "../os/os.h"
#include "../include/fnvnx_clock.h"

#include <stddef.h>
#include <new>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define DW(x) ((uint32_t)(x))

namespace {

/* the game's DIOBJECTDATAFORMAT tables (c_dfDIKeyboard, c_dfDIMouse) point at GUIDs MSVC placed in the exe's .text:
 * the loader maps the whole image as the Windows loader does, so they are read where they lie */
bool guid_eq(const GUID &a, const GUID &b) { return !memcmp(&a, &b, sizeof(GUID)); }
const GUID IID_IUnknown_ = {0x00000000, 0x0000, 0x0000, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
const GUID IID_IDirectInputDevice2A_ = {0x5944e682, 0xc92e, 0x11cf, {0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};
const GUID IID_IDirectInputDevice7A_ = {0x57d7c6bc, 0x2356, 0x11d3, {0x8e, 0x9d, 0x00, 0xc0, 0x4f, 0x68, 0x44, 0xae}};
const GUID GUID_Product_Kbd = {0x6f1d2b61, 0xd5a0, 0x11cf, {0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};
const GUID GUID_Product_Mouse = {0x6f1d2b60, 0xd5a0, 0x11cf, {0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};
const GUID GUID_DevClass_Kbd = {0x4d36e96b, 0xe325, 0x11ce, {0xbf, 0xc1, 0x08, 0x00, 0x2b, 0xe1, 0x03, 0x18}};
const GUID GUID_DevClass_Mouse = {0x4d36e96f, 0xe325, 0x11ce, {0xbf, 0xc1, 0x08, 0x00, 0x2b, 0xe1, 0x03, 0x18}};
const GUID GUID_DevClass_Hid = {0x745a17a0, 0x74d3, 0x11d0, {0xb6, 0xfe, 0x00, 0xa0, 0xc9, 0x0f, 0x57, 0xda}};

/* DirectInput 8 SDK values the shim header lacks (DIPROP_* are small integers cast to a GUID reference) */
enum { P_BUFFERSIZE = 1, P_AXISMODE, P_GRANULARITY, P_RANGE, P_DEADZONE, P_SATURATION, P_FFGAIN, P_FFLOAD, P_AUTOCENTER,
       P_CALIBRATIONMODE, P_CALIBRATION, P_GUIDANDPATH, P_INSTANCENAME, P_PRODUCTNAME, P_KEYNAME = 20, P_SCANCODE = 23,
       P_VIDPID, P_USERNAME, P_TYPENAME };
/* DX3 structure sizes: the layout up to the first DX5 member (560 / 292 / 24 / 16 bytes in the game's ILP32 ABI) */
const size_t DIDX3_INST = offsetof(DIDEVICEINSTANCEA, guidFFDriver), DIDX3_OBJ = offsetof(DIDEVICEOBJECTINSTANCEA, dwFFMaxForce),
             DIDX3_CAPS = offsetof(DIDEVCAPS, dwFFSamplePeriod), DIDX3_DATA = offsetof(DIDEVICEOBJECTDATA, uAppData);
struct DIIMAGEINFO_ { CHAR path[MAX_PATH]; DWORD flags, view; LONG overlay[4]; DWORD obj, npts; LONG callout[10]; LONG rect[4]; DWORD align; };
struct DIPROPSTRING_ { DIPROPHEADER diph; WCHAR wsz[MAX_PATH]; };
struct DIPROPGUIDANDPATH_ { DIPROPHEADER diph; GUID guidClass; WCHAR wszPath[MAX_PATH]; };
/* action mapping (DIACTIONA / DIACTIONFORMATA / DIDEVICEIMAGEINFOHEADERA) */
struct DIACTION_ { UINT_PTR uAppData; DWORD dwSemantic, dwFlags; LPCSTR name; GUID guidInstance; DWORD dwObjID, dwHow; };
struct DIACTIONFORMAT_ {
    DWORD dwSize, dwActionSize, dwDataSize, dwNumActions; DIACTION_ *rgoAction; GUID guidActionMap; DWORD dwGenre, dwBufferSize;
    LONG lAxisMin, lAxisMax; HINSTANCE hInstString; DWORD ftLo, ftHi, dwCRC; CHAR tszActionMap[MAX_PATH];
};
struct DIIMAGEHDR_ { DWORD dwSize, dwSizeImageInfo, dwcViews, dwcButtons, dwcAxes, dwcPOVs, dwBufferSize, dwBufferUsed; void *img; };
FNVNX_CALLBACK_T(SEMCB, BOOL, (LPCDIDEVICEINSTANCEA, IDirectInputDevice8A *, DWORD, DWORD, LPVOID));
enum { DIAH_UNMAPPED = 0, DIAH_DEFAULT = 0x20, DIAH_USERCONFIG = 1, DIDBAM_MASK = 7, DIEDBS_MAPPEDPRI1 = 1,
       DIEDBSFL_VALID = 0x7110 };
#define DIGENRE_ANY_ 0xFFFFFF00u
/* semantics of the keyboard / mouse genres: 0x81000400|DIK, 0x82000(2|3)00|offset (abs / rel axis), 0x8200048C+n */
const uint32_t SEM_KBD = 0x81000400, SEM_MOUSE_ABS = 0x82000200, SEM_MOUSE_REL = 0x82000300, SEM_MOUSE_BTN = 0x82000480;

const struct { uint8_t d; const char *n; } g_keynames[] = {
    {0x01, "Esc"}, {0x02, "1"}, {0x03, "2"}, {0x04, "3"}, {0x05, "4"}, {0x06, "5"}, {0x07, "6"}, {0x08, "7"}, {0x09, "8"},
    {0x0A, "9"}, {0x0B, "0"}, {0x0C, "-"}, {0x0D, "="}, {0x0E, "Backspace"}, {0x0F, "Tab"}, {0x10, "Q"}, {0x11, "W"},
    {0x12, "E"}, {0x13, "R"}, {0x14, "T"}, {0x15, "Y"}, {0x16, "U"}, {0x17, "I"}, {0x18, "O"}, {0x19, "P"}, {0x1A, "["},
    {0x1B, "]"}, {0x1C, "Enter"}, {0x1D, "Ctrl"}, {0x1E, "A"}, {0x1F, "S"}, {0x20, "D"}, {0x21, "F"}, {0x22, "G"},
    {0x23, "H"}, {0x24, "J"}, {0x25, "K"}, {0x26, "L"}, {0x27, ";"}, {0x28, "'"}, {0x29, "`"}, {0x2A, "Shift"},
    {0x2B, "\\"}, {0x2C, "Z"}, {0x2D, "X"}, {0x2E, "C"}, {0x2F, "V"}, {0x30, "B"}, {0x31, "N"}, {0x32, "M"}, {0x33, ","},
    {0x34, "."}, {0x35, "/"}, {0x36, "Right Shift"}, {0x37, "Num *"}, {0x38, "Alt"}, {0x39, "Space"}, {0x3A, "Caps Lock"},
    {0x3B, "F1"}, {0x3C, "F2"}, {0x3D, "F3"}, {0x3E, "F4"}, {0x3F, "F5"}, {0x40, "F6"}, {0x41, "F7"}, {0x42, "F8"},
    {0x43, "F9"}, {0x44, "F10"}, {0x45, "Num Lock"}, {0x46, "Scroll Lock"}, {0x47, "Num 7"}, {0x48, "Num 8"},
    {0x49, "Num 9"}, {0x4A, "Num -"}, {0x4B, "Num 4"}, {0x4C, "Num 5"}, {0x4D, "Num 6"}, {0x4E, "Num +"}, {0x4F, "Num 1"},
    {0x50, "Num 2"}, {0x51, "Num 3"}, {0x52, "Num 0"}, {0x53, "Num ."}, {0x56, "<"}, {0x57, "F11"}, {0x58, "F12"},
    {0x64, "F13"}, {0x65, "F14"}, {0x66, "F15"}, {0x70, "Kana"}, {0x73, "ABNT C1"}, {0x79, "Convert"}, {0x7B, "NoConvert"},
    {0x7D, "Yen"}, {0x8D, "Num ="}, {0x90, "Previous Track"}, {0x99, "Next Track"}, {0x9C, "Num Enter"},
    {0x9D, "Right Ctrl"}, {0xA0, "Mute"}, {0xA2, "Play/Pause"}, {0xA4, "Stop"}, {0xAE, "Volume Down"}, {0xB0, "Volume Up"},
    {0xB5, "Num /"}, {0xB7, "Sys Rq"}, {0xB8, "Right Alt"}, {0xC5, "Pause"}, {0xC7, "Home"}, {0xC8, "Up"}, {0xC9, "Page Up"},
    {0xCB, "Left"}, {0xCD, "Right"}, {0xCF, "End"}, {0xD0, "Down"}, {0xD1, "Page Down"}, {0xD2, "Insert"}, {0xD3, "Delete"},
    {0xDB, "Left Windows"}, {0xDC, "Right Windows"}, {0xDD, "Apps"}, {0xDE, "Power"}, {0xDF, "Sleep"}};

enum Kind { KBD, MOUSE };
enum { MOUSE_AXES = 3, MOUSE_BUTTONS = 8, KEYS = 256 };

struct Object {        /* one device object */
    const GUID *guid;
    uint32_t type;      /* DIDFT_* with instance */
    char name[32];      /* empty: the object does not exist (a keyboard key without a name) */
    uint16_t usage_page, usage;
};

struct Map {           /* data format entry -> device object */
    uint32_t ofs;
    int obj;            /* index into objects */
    UINT_PTR app;       /* uAppData reported with the object's events (action mapping), else -1 */
};

struct Event {
    uint32_t obj;
    int32_t value;
    uint32_t time, seq;
};


uint32_t now_ms() { return clk_peek_tick_ms(); }   /* the injecting side's time, no advance (fnvnx_clock.h) */

struct Device;
os_lock_t g_lock;      /* guards every device and the global state */
Device *g_devices;
uint8_t g_keys[KEYS];
uint8_t g_buttons[MOUSE_BUTTONS];
int32_t g_mouse_abs[MOUSE_AXES];
uint64_t g_kbd_reads;   /* keyboard reads (GetDeviceState/GetDeviceData) that returned data: what swkbd paces by */
uint32_t g_sequence;    /* shared by every device: DIDEVICEOBJECTDATA.dwSequence */

const GUID &inst_guid(Kind k) { return k == KBD ? GUID_SysKeyboard : GUID_SysMouse; }
uint32_t dev_type(Kind k)
{
    return k == KBD ? (DI8DEVTYPE_KEYBOARD | (DI8DEVTYPEKEYBOARD_PCENH << 8)) : (DI8DEVTYPE_MOUSE | (DI8DEVTYPEMOUSE_TRADITIONAL << 8));
}
void fill_instance(Kind k, DIDEVICEINSTANCEA *d)
{
    memset(d, 0, sizeof *d);
    d->dwSize = sizeof *d;
    d->guidInstance = inst_guid(k);
    d->guidProduct = k == KBD ? GUID_Product_Kbd : GUID_Product_Mouse;
    d->dwDevType = dev_type(k);
    snprintf(d->tszInstanceName, sizeof d->tszInstanceName, "%s", k == KBD ? "Keyboard" : "Mouse");
    snprintf(d->tszProductName, sizeof d->tszProductName, "%s", k == KBD ? "Keyboard" : "Mouse");
    d->wUsagePage = 1;
    d->wUsage = k == KBD ? 6 : 2;
}
void widen(WCHAR *w, const char *s) { size_t i = 0; for (; s[i] && i < MAX_PATH - 1; i++) w[i] = (WCHAR)(uint8_t)s[i]; w[i] = 0; }

struct Device final : IDirectInputDevice8A {
    int refs = 1;
    Kind kind;
    Device *next = nullptr, *prev = nullptr;
    Object objs[KEYS];
    int nobjs = 0;
    Map *map = nullptr;
    uint32_t nmap = 0, data_size = 0;
    int obj_to_map[KEYS];
    bool acquired = false, has_format = false, usage_done = false;
    DWORD coop = 0;
    HWND hwnd = nullptr;
    HANDLE event = nullptr;
    uint32_t axis_mode = DIPROPAXISMODE_REL;
    int32_t rel[MOUSE_AXES] = {0, 0, 0};
    /* buffered data */
    Event *queue = nullptr;
    uint32_t qcap = 0, qhead = 0, qcount = 0;
    bool overflow = false;

    explicit Device(Kind k);
    ~Device() { free(map); free(queue); }

    void link()   /* receive input */
    {
        os_lock(&g_lock);
        next = g_devices;
        if (g_devices) g_devices->prev = this;
        g_devices = this;
        os_unlock(&g_lock);
    }
    bool exists(int i) const { return objs[i].name[0] != 0; }
    void set_mode(uint32_t m)
    {
        axis_mode = m;
        if (kind == MOUSE)
            for (int i = 0; i < MOUSE_AXES; i++) objs[i].type = (objs[i].type & ~3u) | (m == DIPROPAXISMODE_REL ? 1u : 2u);
    }
    /* the foreground cooperative level: a foreground device whose window is not the foreground window is unacquired */
    bool fg_ok() const { return !(coop & DISCL_FOREGROUND) || !hwnd || GetForegroundWindow() == hwnd; }
    bool fg_lost()   /* g_lock held */
    {
        if (!acquired || fg_ok()) return false;
        acquired = false;
        qhead = qcount = 0;
        return true;
    }
    void push(int obj, int32_t value)   /* g_lock held */
    {
        if (!acquired) return;
        if (obj < (int)KEYS && obj_to_map[obj] < 0) return;   /* not in the data format */
        if (qcap) {
            if (qcount == qcap) { overflow = true; qhead = (qhead + 1) % qcap; qcount--; }
            Event &e = queue[(qhead + qcount) % qcap];
            e.obj = (uint32_t)obj;
            e.value = value;
            e.time = now_ms();
            e.seq = ++g_sequence;
            qcount++;
        }
        if (event) SetEvent(event);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID r, void **o) override
    {
        if (!o) return E_POINTER;
        if (guid_eq(r, IID_IUnknown_) || guid_eq(r, IID_IDirectInputDevice8A) || guid_eq(r, IID_IDirectInputDevice2A_) ||
            guid_eq(r, IID_IDirectInputDevice7A_)) { *o = this; AddRef(); return S_OK; }   /* 2A/7A: a prefix of the 8A vtable */
        *o = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        int n = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (!n) {
            os_lock(&g_lock);
            if (prev) prev->next = next; else if (g_devices == this) g_devices = next;
            if (next) next->prev = prev;
            os_unlock(&g_lock);
            delete this;
        }
        return (ULONG)n;
    }
    HRESULT STDMETHODCALLTYPE GetCapabilities(LPDIDEVCAPS c) override
    {
        if (!c) return DIERR_INVALIDPARAM;
        if (DW(c->dwSize) != sizeof(DIDEVCAPS) && DW(c->dwSize) != DIDX3_CAPS) return DIERR_INVALIDPARAM;
        DWORD sz = c->dwSize;
        DIDEVCAPS t;
        memset(&t, 0, sizeof t);
        t.dwSize = sz;
        t.dwFlags = DIDC_ATTACHED;
        t.dwDevType = dev_type(kind);
        t.dwAxes = kind == KBD ? 0 : MOUSE_AXES;
        t.dwButtons = kind == KBD ? KEYS : MOUSE_BUTTONS;
        memcpy(c, &t, DW(sz));
        return DI_OK;
    }
    void ensure_usage()   /* keyboard objects: HID usage = the usage the platform maps onto the DIK */
    {
        if (usage_done) return;
        usage_done = true;
        if (kind != KBD) return;
        for (int u = 231; u >= 4; u--) {
            uint8_t d = fnvnx_hid_usage_to_dik((uint8_t)u);
            if (d) objs[d].usage = (uint16_t)u;
        }
    }
    uint32_t default_ofs(int i) const { return kind == KBD ? (uint32_t)i : i < MOUSE_AXES ? (uint32_t)i * 4 : 12 + (uint32_t)(i - MOUSE_AXES); }
    uint32_t ofs_of(int i) const { return !has_format ? default_ofs(i) : obj_to_map[i] >= 0 ? map[obj_to_map[i]].ofs : 0xFFFFFFFFu; }
    void fill_object(int i, void *out, uint32_t sz)   /* sz: DX3 or full DIDEVICEOBJECTINSTANCEA */
    {
        ensure_usage();
        DIDEVICEOBJECTINSTANCEA o;
        memset(&o, 0, sizeof o);
        o.dwSize = sz;
        o.guidType = *objs[i].guid;
        o.dwOfs = ofs_of(i) == 0xFFFFFFFFu ? default_ofs(i) : ofs_of(i);
        o.dwType = objs[i].type;
        o.dwFlags = (objs[i].type & DIDFT_AXIS) ? DIDOI_ASPECTPOSITION : 0;
        snprintf(o.tszName, sizeof o.tszName, "%s", objs[i].name);
        o.wDesignatorIndex = (WORD)((objs[i].type >> 8) & 0xFFFF);
        o.wUsagePage = objs[i].usage_page;
        o.wUsage = objs[i].usage;
        memcpy(out, &o, sz);
    }
    HRESULT STDMETHODCALLTYPE EnumObjects(LPDIENUMDEVICEOBJECTSCALLBACKA cb, LPVOID ref, DWORD flags) override
    {
        if (!cb) return DIERR_INVALIDPARAM;
        uint32_t fl = DW(flags);
        if (fl & ~(0xFFu | 0x00FFFF00u | 0x13000000u)) return DIERR_INVALIDPARAM;
        if (fl & 0x13000000u) return DI_OK;   /* FF actuators / effect triggers / output objects: none */
        if (fl & 0x00FFFF00u) return DI_OK;   /* DIDFT_ENUMCOLLECTION(n): the device has no collections */
        ensure_usage();
        for (int i = 0; i < nobjs; i++) {
            if (!exists(i) || ((fl & 0xFF) && !(objs[i].type & fl & 0xFF))) continue;
            DIDEVICEOBJECTINSTANCEA o;
            fill_object(i, &o, sizeof o);
            if (cb(&o, ref) == DIENUM_STOP) break;
        }
        return DI_OK;
    }
    /* DIPH_DEVICE: *idx = -1; BYOFFSET / BYID / BYUSAGE: the object's index */
    HRESULT resolve(DWORD how, DWORD obj, int *idx)
    {
        *idx = -1;
        if (how == DIPH_DEVICE) return obj ? DIERR_INVALIDPARAM : DI_OK;
        if (how != DIPH_BYOFFSET && how != DIPH_BYID && how != DIPH_BYUSAGE) return DIERR_INVALIDPARAM;
        ensure_usage();
        for (int i = 0; i < nobjs && *idx < 0; i++) {
            if (!exists(i)) continue;
            if (how == DIPH_BYOFFSET ? ofs_of(i) == obj
                : how == DIPH_BYID ? (((objs[i].type >> 8) & 0xFFFF) == ((obj >> 8) & 0xFFFF) && (objs[i].type & obj & 0xFF))
                                   : (((uint32_t)objs[i].usage_page << 16 | objs[i].usage) == obj))
                *idx = i;
        }
        return *idx < 0 ? DIERR_OBJECTNOTFOUND : DI_OK;
    }
    static HRESULT check_header(LPCDIPROPHEADER h, uint32_t need)
    {
        if (!h || DW(h->dwSize) < sizeof(DIPROPHEADER) || DW(h->dwHeaderSize) != sizeof(DIPROPHEADER)) return DIERR_INVALIDPARAM;
        return need && DW(h->dwSize) != need ? DIERR_INVALIDPARAM : DI_OK;
    }
    static uint32_t prop_size(uintptr_t p)
    {
        switch (p) {
        case P_RANGE: return sizeof(DIPROPRANGE);
        case P_KEYNAME: case P_INSTANCENAME: case P_PRODUCTNAME: case P_TYPENAME: case P_USERNAME: return sizeof(DIPROPSTRING_);
        case P_GUIDANDPATH: return sizeof(DIPROPGUIDANDPATH_);
        default: return sizeof(DIPROPDWORD);
        }
    }
    HRESULT STDMETHODCALLTYPE GetProperty(REFGUID g, LPDIPROPHEADER h) override
    {
        uintptr_t prop = (uintptr_t)&g;
        HRESULT hr = check_header(h, 0);
        if (hr != DI_OK) return hr;
        if (prop < P_BUFFERSIZE || prop > P_TYPENAME) return DIERR_UNSUPPORTED;
        hr = check_header(h, prop_size(prop));
        if (hr != DI_OK) return hr;
        int idx;
        hr = resolve(h->dwHow, h->dwObj, &idx);
        if (hr != DI_OK) return hr;
        LPDIPROPDWORD pd = (LPDIPROPDWORD)h;
        const char *nm = kind == KBD ? "Keyboard" : "Mouse";
        switch (prop) {
        case P_BUFFERSIZE: if (h->dwHow != DIPH_DEVICE) return DIERR_INVALIDPARAM; pd->dwData = qcap; return DI_OK;
        case P_AXISMODE:
            if (h->dwHow != DIPH_DEVICE) return DIERR_INVALIDPARAM;
            if (kind == KBD) return DIERR_UNSUPPORTED;
            pd->dwData = axis_mode; return DI_OK;
        case P_GRANULARITY:
            if (idx < 0) return DIERR_INVALIDPARAM;
            if (!(objs[idx].type & DIDFT_AXIS)) return DIERR_UNSUPPORTED;
            pd->dwData = idx == 2 ? 120 : 1; return DI_OK;
        case P_KEYNAME:
            if (kind != KBD) return DIERR_UNSUPPORTED;
            if (idx < 0) return DIERR_INVALIDPARAM;
            widen(((DIPROPSTRING_ *)h)->wsz, objs[idx].name); return DI_OK;
        case P_SCANCODE:
            if (kind != KBD) return DIERR_UNSUPPORTED;
            if (idx < 0) return DIERR_INVALIDPARAM;
            pd->dwData = (idx & 0x80) ? 0xE000u | (uint32_t)(idx & 0x7F) : (uint32_t)idx; return DI_OK;
        case P_INSTANCENAME: case P_PRODUCTNAME: case P_TYPENAME:
            if (h->dwHow != DIPH_DEVICE) return DIERR_INVALIDPARAM;
            widen(((DIPROPSTRING_ *)h)->wsz, nm); return DI_OK;
        case P_GUIDANDPATH:
            if (h->dwHow != DIPH_DEVICE) return DIERR_INVALIDPARAM;
            ((DIPROPGUIDANDPATH_ *)h)->guidClass = kind == KBD ? GUID_DevClass_Kbd : GUID_DevClass_Mouse;
            widen(((DIPROPGUIDANDPATH_ *)h)->wszPath, kind == KBD ? "\\\\?\\fnvnx#keyboard" : "\\\\?\\fnvnx#mouse"); return DI_OK;
        default: return DIERR_UNSUPPORTED;   /* RANGE (relative axes, keys), DEADZONE, SATURATION, FF*, VIDPID, ... */
        }
    }
    HRESULT set_buffer(uint32_t n)   /* g_lock not held */
    {
        if (n > (1u << 20)) return DIERR_OUTOFMEMORY;
        if (n == qcap) return DI_PROPNOEFFECT;
        Event *q = n ? (Event *)calloc(n, sizeof *q) : nullptr;
        if (n && !q) return DIERR_OUTOFMEMORY;
        os_lock(&g_lock);
        free(queue);
        queue = q; qcap = n; qhead = qcount = 0; overflow = false;
        os_unlock(&g_lock);
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE SetProperty(REFGUID g, LPCDIPROPHEADER h) override
    {
        uintptr_t prop = (uintptr_t)&g;
        HRESULT hr = check_header(h, 0);
        if (hr != DI_OK) return hr;
        if (prop < P_BUFFERSIZE || prop > P_TYPENAME) return DIERR_UNSUPPORTED;
        hr = check_header(h, prop_size(prop));
        if (hr != DI_OK) return hr;
        int idx;
        hr = resolve(h->dwHow, h->dwObj, &idx);
        if (hr != DI_OK) return hr;
        uint32_t v = DW(((LPCDIPROPDWORD)h)->dwData);
        if (prop == P_BUFFERSIZE || prop == P_AXISMODE) {
            if (h->dwHow != DIPH_DEVICE) return DIERR_INVALIDPARAM;
            if (prop == P_AXISMODE && kind == KBD) return DIERR_UNSUPPORTED;
            if (prop == P_AXISMODE && v != DIPROPAXISMODE_ABS && v != DIPROPAXISMODE_REL) return DIERR_INVALIDPARAM;
            if (acquired) return DIERR_ACQUIRED;
            if (prop == P_BUFFERSIZE) return set_buffer(v);
            if (v == axis_mode) return DI_PROPNOEFFECT;
            os_lock(&g_lock);
            set_mode(v);
            os_unlock(&g_lock);
            return DI_OK;
        }
        return DIERR_UNSUPPORTED;   /* every other property is read-only or absent on a keyboard / mouse */
    }
    HRESULT STDMETHODCALLTYPE Acquire() override
    {
        if (!has_format) return DIERR_INVALIDPARAM;
        os_lock(&g_lock);
        HRESULT hr;
        if (acquired) hr = S_FALSE;
        else if (!fg_ok()) hr = DIERR_OTHERAPPHASPRIO;
        else { acquired = true; rel[0] = rel[1] = rel[2] = 0; hr = DI_OK; }
        os_unlock(&g_lock);
        return hr;
    }
    HRESULT STDMETHODCALLTYPE Unacquire() override
    {
        os_lock(&g_lock);
        bool was = acquired;
        acquired = false;
        qhead = qcount = 0;
        os_unlock(&g_lock);
        return was ? DI_OK : DI_NOEFFECT;
    }
    HRESULT STDMETHODCALLTYPE GetDeviceState(DWORD cb, LPVOID data) override
    {
        if (!data) return DIERR_INVALIDPARAM;
        if (!has_format || DW(cb) != data_size) return DIERR_INVALIDPARAM;
        fnvnx_input_pump();
        os_lock(&g_lock);
        if (fg_lost()) { os_unlock(&g_lock); return DIERR_INPUTLOST; }
        if (!acquired) { os_unlock(&g_lock); return DIERR_NOTACQUIRED; }
        memset(data, 0, DW(cb));
        uint8_t *p = (uint8_t *)data;
        for (uint32_t i = 0; i < nmap; i++) {
            int o = map[i].obj;
            if (objs[o].type & DIDFT_AXIS) {
                int32_t v = axis_mode == DIPROPAXISMODE_REL ? rel[o] : g_mouse_abs[o];
                memcpy(p + map[i].ofs, &v, 4);
            } else {
                bool down = kind == KBD ? g_keys[o] : g_buttons[o - MOUSE_AXES];
                p[map[i].ofs] = down ? 0x80 : 0;
            }
        }
        rel[0] = rel[1] = rel[2] = 0;
        if (kind == KBD) g_kbd_reads++;
        os_unlock(&g_lock);
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDeviceData(DWORD cbObj, LPDIDEVICEOBJECTDATA rg, LPDWORD inout, DWORD fl) override
    {
        if (!inout) return DIERR_INVALIDPARAM;
        if (DW(cbObj) != sizeof(DIDEVICEOBJECTDATA) && DW(cbObj) != DIDX3_DATA) return DIERR_INVALIDPARAM;
        if (DW(fl) & ~(uint32_t)DIGDD_PEEK) return DIERR_INVALIDPARAM;
        if (!qcap) return DIERR_NOTBUFFERED;
        fnvnx_input_pump();
        os_lock(&g_lock);
        if (fg_lost()) { os_unlock(&g_lock); return DIERR_INPUTLOST; }
        if (!acquired) { os_unlock(&g_lock); return DIERR_NOTACQUIRED; }
        uint32_t want = DW(*inout), n = want < qcount ? want : qcount;
        bool peek = (DW(fl) & DIGDD_PEEK) != 0;
        for (uint32_t i = 0; rg && i < n; i++) {
            Event &e = queue[(qhead + i) % qcap];
            LPDIDEVICEOBJECTDATA d = (LPDIDEVICEOBJECTDATA)((uint8_t *)rg + (size_t)i * DW(cbObj));
            const Map &m = map[obj_to_map[e.obj]];
            d->dwOfs = m.ofs;
            d->dwData = (DWORD)(uint32_t)e.value;
            d->dwTimeStamp = e.time;
            d->dwSequence = e.seq;
            if (DW(cbObj) == sizeof(DIDEVICEOBJECTDATA)) d->uAppData = m.app;
        }
        if (!peek) { qhead = qcap ? (qhead + n) % qcap : 0; qcount -= n; }
        *inout = n;
        HRESULT hr = overflow ? DI_BUFFEROVERFLOW : DI_OK;
        if (!peek) overflow = false;
        if (kind == KBD) g_kbd_reads++;
        os_unlock(&g_lock);
        return hr;
    }
    HRESULT STDMETHODCALLTYPE SetDataFormat(LPCDIDATAFORMAT f) override;
    HRESULT STDMETHODCALLTYPE SetEventNotification(HANDLE h) override
    {
        if (acquired) return DIERR_ACQUIRED;
        os_lock(&g_lock);
        HRESULT hr = DI_OK;
        for (Device *d = g_devices; h && d; d = d->next)
            if (d != this && d->event == h) hr = DIERR_HANDLEEXISTS;
        if (hr == DI_OK) event = h;
        os_unlock(&g_lock);
        return hr;
    }
    HRESULT STDMETHODCALLTYPE SetCooperativeLevel(HWND w, DWORD fl) override
    {
        uint32_t f = DW(fl);
        bool ex = f & DISCL_EXCLUSIVE, nex = f & DISCL_NONEXCLUSIVE, fg = f & DISCL_FOREGROUND, bg = f & DISCL_BACKGROUND;
        if ((f & ~0x1Fu) || ex == nex || fg == bg) return DIERR_INVALIDPARAM;
        if (w && !IsWindow(w)) return DIERR_INVALIDPARAM;   /* NULL: no window, the foreground rule cannot apply */
        if (kind == KBD && ex && bg) return DIERR_UNSUPPORTED;
        if (acquired) return DIERR_ACQUIRED;
        coop = fl;
        hwnd = w;
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetObjectInfo(LPDIDEVICEOBJECTINSTANCEA o, DWORD obj, DWORD how) override
    {
        if (!o) return DIERR_INVALIDPARAM;
        uint32_t sz = DW(o->dwSize);
        if (sz != sizeof(DIDEVICEOBJECTINSTANCEA) && sz != DIDX3_OBJ) return DIERR_INVALIDPARAM;
        if (DW(how) == DIPH_DEVICE) return DIERR_INVALIDPARAM;
        int idx;
        HRESULT hr = resolve(how, obj, &idx);
        if (hr != DI_OK) return hr;
        fill_object(idx, o, sz);
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDeviceInfo(LPDIDEVICEINSTANCEA d) override;
    HRESULT STDMETHODCALLTYPE RunControlPanel(HWND w, DWORD fl) override
    {
        if (fl || (w && !IsWindow(w))) return DIERR_INVALIDPARAM;
        return DIERR_GENERIC;   /* no control panel application exists in this environment */
    }
    HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE inst, DWORD ver, REFGUID g) override
    {
        if (!inst) return DIERR_INVALIDPARAM;
        if (DW(ver) < 0x0800) return DIERR_OLDDIRECTINPUTVERSION;
        if (DW(ver) > 0x0800) return DIERR_BETADIRECTINPUTVERSION;
        return guid_eq(g, inst_guid(kind)) ? DI_OK : DIERR_ALREADYINITIALIZED;   /* created initialised, for its own GUID */
    }
    /* force feedback: neither device has it (DIDC_FORCEFEEDBACK clear), so the SDK result for a non-FF device */
    HRESULT STDMETHODCALLTYPE CreateEffect(REFGUID, const void *p, IDirectInputEffect **e, LPUNKNOWN outer) override
    {
        if (!e || !p) return DIERR_INVALIDPARAM;
        *e = nullptr;
        return outer ? DIERR_NOAGGREGATION : DIERR_UNSUPPORTED;
    }
    HRESULT STDMETHODCALLTYPE EnumEffects(void *cb, LPVOID, DWORD) override { return cb ? DI_OK : DIERR_INVALIDPARAM; }
    HRESULT STDMETHODCALLTYPE GetEffectInfo(void *p, REFGUID) override { return p ? DIERR_UNSUPPORTED : DIERR_INVALIDPARAM; }
    HRESULT STDMETHODCALLTYPE GetForceFeedbackState(LPDWORD p) override { return p ? DIERR_UNSUPPORTED : DIERR_INVALIDPARAM; }
    HRESULT STDMETHODCALLTYPE SendForceFeedbackCommand(DWORD) override;   /* env_const.tsv */
    HRESULT STDMETHODCALLTYPE EnumCreatedEffectObjects(void *cb, LPVOID, DWORD fl) override { return !cb || fl ? DIERR_INVALIDPARAM : DI_OK; }
    HRESULT STDMETHODCALLTYPE Escape(void *p) override { return p ? DIERR_UNSUPPORTED : DIERR_INVALIDPARAM; }
    HRESULT STDMETHODCALLTYPE Poll() override
    {
        os_lock(&g_lock);
        HRESULT hr = fg_lost() ? DIERR_INPUTLOST : acquired ? DI_NOEFFECT : DIERR_NOTACQUIRED;
        os_unlock(&g_lock);
        return hr;
    }
    HRESULT STDMETHODCALLTYPE SendDeviceData(DWORD cb, LPCDIDEVICEOBJECTDATA p, LPDWORD n, DWORD) override
    {
        return !p || !n || cb != sizeof(DIDEVICEOBJECTDATA) ? DIERR_INVALIDPARAM : DIERR_UNSUPPORTED;   /* no output objects */
    }
    HRESULT STDMETHODCALLTYPE EnumEffectsInFile(LPCSTR f, void *cb, LPVOID, DWORD) override { return !f || !cb ? DIERR_INVALIDPARAM : DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE WriteEffectToFile(LPCSTR f, DWORD n, void *p, DWORD) override { return !f || !n || !p ? DIERR_INVALIDPARAM : DIERR_UNSUPPORTED; }

    /* ---- action mapping: keyboard (DIKEYBOARD_*) and mouse (DIMOUSE_*) semantics map directly onto objects */
    int match_sem(uint32_t sem) const
    {
        if (kind == KBD) return (sem & ~0xFFu) == SEM_KBD && exists((int)(sem & 0xFF)) ? (int)(sem & 0xFF) : -1;
        uint32_t hi = sem & ~0xFFu, o = sem & 0xFF;
        if ((hi == SEM_MOUSE_ABS || hi == SEM_MOUSE_REL) && o <= 8 && !(o & 3)) return (int)(o / 4);
        if ((sem & ~0x7Fu) == SEM_MOUSE_BTN && (sem & 0x7F) >= 12 && (sem & 0x7F) < 12 + MOUSE_BUTTONS) return MOUSE_AXES + (int)(sem & 0x7F) - 12;
        return -1;
    }
    static bool af_valid(const DIACTIONFORMAT_ *a)
    {
        return a && DW(a->dwSize) == sizeof *a && DW(a->dwActionSize) == sizeof(DIACTION_) && (!a->dwNumActions || a->rgoAction);
    }
    HRESULT STDMETHODCALLTYPE BuildActionMap(void *v, LPCSTR, DWORD fl) override
    {
        DIACTIONFORMAT_ *a = (DIACTIONFORMAT_ *)v;
        if (!af_valid(a) || (DW(fl) & ~(uint32_t)DIDBAM_MASK)) return DIERR_INVALIDPARAM;
        static const GUID nullg = {0, 0, 0, {0, 0, 0, 0, 0, 0, 0, 0}};
        for (uint32_t i = 0; i < a->dwNumActions; i++) {
            DIACTION_ &x = a->rgoAction[i];
            if (DW(fl) & 2) { x.guidInstance = nullg; x.dwObjID = 0; x.dwHow = DIAH_UNMAPPED; }   /* DIDBAM_INITIALIZE */
            else if ((DW(fl) & 1) && x.dwHow) continue;                                             /* DIDBAM_PRESERVE */
            int o = match_sem(x.dwSemantic);
            if (o >= 0 && (!memcmp(&x.guidInstance, &nullg, sizeof(GUID)) || guid_eq(x.guidInstance, inst_guid(kind)))) {
                x.guidInstance = inst_guid(kind);
                x.dwObjID = objs[o].type;
                x.dwHow = DIAH_DEFAULT;
            }
        }
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE SetActionMap(void *v, LPCSTR, DWORD fl) override
    {
        DIACTIONFORMAT_ *a = (DIACTIONFORMAT_ *)v;
        if (!af_valid(a) || (DW(fl) & ~7u) || (a->dwDataSize & 3)) return DIERR_INVALIDPARAM;
        if (acquired) return DIERR_ACQUIRED;
        Map *m = (Map *)calloc(a->dwNumActions ? a->dwNumActions : 1, sizeof *m);
        if (!m) return DIERR_OUTOFMEMORY;
        bool used[KEYS] = {};
        uint32_t nm = 0;
        for (uint32_t i = 0; i < a->dwNumActions; i++) {   /* the action's data slot is its index: dwOfs = 4 * index */
            const DIACTION_ &x = a->rgoAction[i];
            if (!x.dwHow || !guid_eq(x.guidInstance, inst_guid(kind))) continue;
            for (int k = 0; k < nobjs; k++)
                if (exists(k) && objs[k].type == x.dwObjID && !used[k]) {
                    if ((i + 1) * 4 > a->dwDataSize) { free(m); return DIERR_INVALIDPARAM; }
                    used[k] = true;
                    m[nm].ofs = i * 4; m[nm].obj = k; m[nm].app = x.uAppData; nm++;
                }
        }
        install(m, nm, a->dwDataSize);
        if (a->dwBufferSize) set_buffer(a->dwBufferSize);
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetImageInfo(void *v) override
    {
        DIIMAGEHDR_ *h = (DIIMAGEHDR_ *)v;
        if (!h || DW(h->dwSize) != sizeof *h || DW(h->dwSizeImageInfo) != sizeof(DIIMAGEINFO_)) return DIERR_INVALIDPARAM;
        h->dwcViews = h->dwcButtons = h->dwcAxes = h->dwcPOVs = h->dwBufferUsed = 0;   /* no device images: an empty set */
        return DI_OK;
    }
    void install(Map *m, uint32_t nm, uint32_t size)
    {
        os_lock(&g_lock);
        free(map);
        map = m;
        nmap = nm;
        data_size = size;
        for (int i = 0; i < KEYS; i++) obj_to_map[i] = -1;
        for (uint32_t i = 0; i < nm; i++) obj_to_map[m[i].obj] = (int)i;
        has_format = true;
        qhead = qcount = 0;
        os_unlock(&g_lock);
    }
};

Device::Device(Kind k) : kind(k)
{
    for (int i = 0; i < KEYS; i++) obj_to_map[i] = -1;
    memset(objs, 0, sizeof objs);
    if (k == KBD) {
        for (int i = 0; i < KEYS; i++) {
            objs[i].guid = &GUID_Key;
            objs[i].type = DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(i);
            objs[i].usage_page = 7;
        }
        for (const auto &e : g_keynames) snprintf(objs[e.d].name, sizeof objs[0].name, "%s", e.n);
        nobjs = KEYS;
    } else {
        static const GUID *axes[MOUSE_AXES] = {&GUID_XAxis, &GUID_YAxis, &GUID_ZAxis};
        static const char *names[MOUSE_AXES] = {"X-axis", "Y-axis", "Wheel"};
        static const uint16_t usages[MOUSE_AXES] = {0x30, 0x31, 0x38};
        for (int i = 0; i < MOUSE_AXES; i++) {
            objs[i].guid = axes[i];
            objs[i].type = DIDFT_RELAXIS | DIDFT_MAKEINSTANCE(i);
            snprintf(objs[i].name, sizeof objs[i].name, "%s", names[i]);
            objs[i].usage_page = 1;
            objs[i].usage = usages[i];
        }
        for (int i = 0; i < MOUSE_BUTTONS; i++) {
            Object &o = objs[MOUSE_AXES + i];
            o.guid = &GUID_Button;
            o.type = DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(i);
            snprintf(o.name, sizeof o.name, "Button %d", i);
            o.usage_page = 9;
            o.usage = (uint16_t)(i + 1);
        }
        nobjs = MOUSE_AXES + MOUSE_BUTTONS;
    }
}

HRESULT Device::SetDataFormat(LPCDIDATAFORMAT f)
{
    if (!f) return DIERR_INVALIDPARAM;
    if (acquired) return DIERR_ACQUIRED;
    if (DW(f->dwSize) != sizeof(DIDATAFORMAT) || DW(f->dwObjSize) != sizeof(DIOBJECTDATAFORMAT)) return DIERR_INVALIDPARAM;
    uint32_t fflags = DW(f->dwFlags);   /* exactly one of DIDF_ABSAXIS / DIDF_RELAXIS */
    if ((fflags & ~(uint32_t)(DIDF_ABSAXIS | DIDF_RELAXIS)) || !(fflags & (DIDF_ABSAXIS | DIDF_RELAXIS)) ||
        ((fflags & DIDF_ABSAXIS) && (fflags & DIDF_RELAXIS)))
        return DIERR_INVALIDPARAM;
    uint32_t n = DW(f->dwNumObjs), size = DW(f->dwDataSize);
    if ((size & 3) || size > (1u << 20)) return DIERR_INVALIDPARAM;
    Map *m = n ? (Map *)calloc(n, sizeof *m) : nullptr;
    if (n && !m) return DIERR_OUTOFMEMORY;
    uint8_t *occ = (uint8_t *)calloc(size ? size : 1, 1);   /* bytes of the data block already taken: objects must not overlap */
    if (!occ) { free(m); return DIERR_OUTOFMEMORY; }
    const DIOBJECTDATAFORMAT *od = n ? f->rgodf : nullptr;
    bool used[KEYS];
    for (int i = 0; i < KEYS; i++) used[i] = false;
    uint32_t nm = 0;
    HRESULT hr = DI_OK;
    for (uint32_t i = 0; i < n && hr == DI_OK; i++) {
        const DIOBJECTDATAFORMAT &e = od[i];
        uint32_t t = DW(e.dwType), inst = (t >> 8) & 0xFFFF, ofs = DW(e.dwOfs);
        bool want_axis = (t & DIDFT_AXIS) != 0, want_button = (t & DIDFT_BUTTON) != 0;
        int found = -1;
        for (int k = 0; k < nobjs && found < 0; k++) {
            if (used[k] || !exists(k)) continue;
            uint32_t ot = objs[k].type;
            if (want_axis && !(ot & DIDFT_AXIS)) continue;
            if (want_button && !(ot & DIDFT_BUTTON)) continue;
            if (!want_axis && !want_button && (t & 0xFF) && !(ot & t & 0xFF)) continue;
            if (inst != 0xFFFF && ((ot >> 8) & 0xFFFF) != inst) continue;
            if (e.pguid && !guid_eq(*e.pguid, *objs[k].guid)) continue;
            found = k;
        }
        if (found < 0) {
            if (!(t & DIDFT_OPTIONAL)) hr = DIERR_INVALIDPARAM;
            continue;
        }
        uint32_t width = (objs[found].type & DIDFT_AXIS) ? 4 : 1;
        if (ofs + width > size || (width == 4 && (ofs & 3))) { hr = DIERR_INVALIDPARAM; continue; }
        for (uint32_t b = 0; b < width; b++)
            if (occ[ofs + b]++) hr = DIERR_INVALIDPARAM;
        used[found] = true;
        m[nm].ofs = ofs;
        m[nm].obj = found;
        m[nm].app = (UINT_PTR)-1;
        nm++;
    }
    free(occ);
    if (hr != DI_OK) { free(m); return hr; }
    os_lock(&g_lock);
    set_mode(fflags & DIDF_ABSAXIS ? DIPROPAXISMODE_ABS : DIPROPAXISMODE_REL);
    os_unlock(&g_lock);
    install(m, nm, size);
    return DI_OK;
}

HRESULT Device::GetDeviceInfo(LPDIDEVICEINSTANCEA d)
{
    if (!d) return DIERR_INVALIDPARAM;
    uint32_t sz = DW(d->dwSize);
    if (sz != sizeof(DIDEVICEINSTANCEA) && sz != DIDX3_INST) return DIERR_INVALIDPARAM;
    DIDEVICEINSTANCEA t;
    fill_instance(kind, &t);
    t.dwSize = sz;
    memcpy(d, &t, sz);
    return DI_OK;
}

struct DInput final : IDirectInput8A {
    int refs = 1;
    bool inited = true;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID r, void **o) override
    {
        if (!o) return E_POINTER;
        if (guid_eq(r, IID_IUnknown_) || guid_eq(r, IID_IDirectInput8A)) { *o = this; AddRef(); return S_OK; }
        *o = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        int n = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (!n) delete this;
        return (ULONG)n;
    }
    HRESULT STDMETHODCALLTYPE CreateDevice(REFGUID g, IDirectInputDevice8A **out, LPUNKNOWN outer) override
    {
        if (!out) return DIERR_INVALIDPARAM;
        *out = nullptr;
        if (outer) return DIERR_NOAGGREGATION;
        if (!inited) return DIERR_NOTINITIALIZED;
        Kind k;
        if (guid_eq(g, GUID_SysKeyboard)) k = KBD;
        else if (guid_eq(g, GUID_SysMouse)) k = MOUSE;
        else return DIERR_DEVICENOTREG;
        Device *d = new (std::nothrow) Device(k);
        if (!d) return DIERR_OUTOFMEMORY;
        d->link();
        *out = d;
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE EnumDevices(DWORD type, LPDIENUMDEVICESCALLBACKA cb, LPVOID ref, DWORD flags) override
    {
        if (!cb || (DW(flags) & ~0x00070101u)) return DIERR_INVALIDPARAM;
        if (!inited) return DIERR_NOTINITIALIZED;
        uint32_t t = DW(type), dev = t & 0xFF, sub = (t >> 8) & 0xFF;
        if ((t >> 16) || (dev > DI8DEVCLASS_GAMECTRL && (dev < DI8DEVTYPE_DEVICE || dev > 0x1C)) || (dev <= DI8DEVCLASS_GAMECTRL && sub))
            return DIERR_INVALIDPARAM;
        if (DW(flags) & DIEDFL_FORCEFEEDBACK) return DI_OK;   /* no force-feedback device */
        for (int i = 0; i < 2; i++) {
            Kind k = i == 0 ? KBD : MOUSE;
            bool ok = dev == DI8DEVCLASS_ALL || (k == KBD ? dev == DI8DEVCLASS_KEYBOARD || dev == DI8DEVTYPE_KEYBOARD
                                                          : dev == DI8DEVCLASS_POINTER || dev == DI8DEVTYPE_MOUSE);
            if (!ok || (sub && sub != (dev_type(k) >> 8))) continue;
            DIDEVICEINSTANCEA d;
            fill_instance(k, &d);
            if (cb(&d, ref) == DIENUM_STOP) break;
        }
        return DI_OK;   /* game controllers: none (XInput) */
    }
    HRESULT STDMETHODCALLTYPE GetDeviceStatus(REFGUID g) override
    {
        if (!inited) return DIERR_NOTINITIALIZED;
        return (guid_eq(g, GUID_SysKeyboard) || guid_eq(g, GUID_SysMouse)) ? DI_OK : DIERR_DEVICENOTREG;
    }
    HRESULT STDMETHODCALLTYPE RunControlPanel(HWND w, DWORD fl) override
    {
        if (!inited) return DIERR_NOTINITIALIZED;
        if (fl || (w && !IsWindow(w))) return DIERR_INVALIDPARAM;
        return DIERR_GENERIC;   /* no control panel application exists in this environment */
    }
    HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE inst, DWORD v) override
    {
        if (!inst) return DIERR_INVALIDPARAM;
        if (DW(v) < 0x0800) return DIERR_OLDDIRECTINPUTVERSION;
        if (DW(v) > 0x0800) return DIERR_BETADIRECTINPUTVERSION;
        inited = true;
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE FindDevice(REFGUID cls, LPCSTR name, LPGUID out) override
    {
        if (!name || !out) return DIERR_INVALIDPARAM;
        if (!inited) return DIERR_NOTINITIALIZED;
        for (int i = 0; i < 2; i++) {
            Kind k = i == 0 ? KBD : MOUSE;
            if ((guid_eq(cls, k == KBD ? GUID_DevClass_Kbd : GUID_DevClass_Mouse) || guid_eq(cls, GUID_DevClass_Hid)) &&
                !strcasecmp(name, k == KBD ? "Keyboard" : "Mouse")) { *out = inst_guid(k); return DI_OK; }
        }
        return DIERR_DEVICENOTREG;
    }
    HRESULT STDMETHODCALLTYPE EnumDevicesBySemantics(LPCSTR, void *af, void *vcb, LPVOID ref, DWORD flags) override
    {
        const DIACTIONFORMAT_ *a = (const DIACTIONFORMAT_ *)af;
        SEMCB cb = (SEMCB)vcb;
        if (!cb || !Device::af_valid(a) || (DW(flags) & ~(uint32_t)(DIEDBSFL_VALID))) return DIERR_INVALIDPARAM;
        if (!inited) return DIERR_NOTINITIALIZED;
        if (DW(flags) & DIEDFL_FORCEFEEDBACK) return DI_OK;
        Device *dev[2];
        uint32_t cnt[2];
        unsigned total = 0;
        for (int i = 0; i < 2; i++) {
            dev[i] = new (std::nothrow) Device(i == 0 ? KBD : MOUSE);
            if (!dev[i]) { if (i) dev[0]->Release(); return DIERR_OUTOFMEMORY; }
            dev[i]->link();
            cnt[i] = 0;
            for (uint32_t j = 0; j < a->dwNumActions; j++) cnt[i] += dev[i]->match_sem(a->rgoAction[j].dwSemantic) >= 0;
            total += cnt[i] || a->dwGenre == DIGENRE_ANY_;
        }
        BOOL go = TRUE;
        for (int i = 0; i < 2; i++) {
            if (go && (cnt[i] || a->dwGenre == DIGENRE_ANY_)) {
                DIDEVICEINSTANCEA d;
                fill_instance(dev[i]->kind, &d);
                go = cb(&d, dev[i], cnt[i] ? DIEDBS_MAPPEDPRI1 : 0, --total, ref) != DIENUM_STOP;
            }
            dev[i]->Release();   /* the enumeration's reference; a callback that kept the device AddRef'd it */
        }
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE ConfigureDevices(void *cb, void *vp, DWORD flags, LPVOID) override
    {
        struct CDP { DWORD dwSize, dwcUsers; LPSTR users; DWORD dwcFormats; DIACTIONFORMAT_ *fmts; HWND hwnd; DWORD dics[9]; IUnknown *dds; };
        const CDP *p = (const CDP *)vp;
        if (!p || DW(p->dwSize) != sizeof(CDP) || DW(flags) > 1 || !p->dwcFormats || !p->fmts ||
            (p->hwnd && !IsWindow(p->hwnd))) return DIERR_INVALIDPARAM;
        for (uint32_t i = 0; i < p->dwcFormats; i++)
            if (!Device::af_valid(&p->fmts[i])) return DIERR_INVALIDPARAM;
        if (!inited) return DIERR_NOTINITIALIZED;
        return DIERR_GENERIC;   /* the mapper UI needs a display this environment does not have */
    }
};

#include "dinput.env_const.gen.inc"   /* Device::SendForceFeedbackCommand: manifest/env_const.tsv */
} // namespace

extern "C" HRESULT WINAPI DirectInput8Create(HINSTANCE, DWORD ver, REFIID iid, LPVOID *out, LPUNKNOWN outer)
{
    if (!out) return DIERR_INVALIDPARAM;
    *out = nullptr;
    if (outer) return DIERR_NOAGGREGATION;
    if (DW(ver) < 0x0800) return DIERR_OLDDIRECTINPUTVERSION;
    if (DW(ver) > 0x0800) return DIERR_BETADIRECTINPUTVERSION;
    if (!guid_eq(iid, IID_IDirectInput8A)) return DIERR_NOINTERFACE;
    DInput *d = new (std::nothrow) DInput();
    if (!d) return DIERR_OUTOFMEMORY;
    *out = static_cast<IDirectInput8A *>(d);
    return DI_OK;
}

/* CoCreateInstance(CLSID_DirectInput8): the object is born uninitialised; IDirectInput8::Initialize(hinst, 0x0800) readies it */
extern "C" HRESULT fnvnx_dinput_com_factory(const GUID *iid, void **out)
{
    HRESULT hr = DirectInput8Create(nullptr, 0x0800, *iid, out, nullptr);
    if (hr == DI_OK) static_cast<DInput *>((IDirectInput8A *)*out)->inited = false;
    return hr;
}

/* ---- producers */
extern "C" uint64_t fnvnx_input_kbd_reads(void)
{
    os_lock(&g_lock);
    uint64_t r = g_kbd_reads;
    os_unlock(&g_lock);
    return r;
}
extern "C" unsigned fnvnx_input_kbd_room(void)
{
    unsigned room = 0xFFFFFFFFu;
    bool any = false;
    os_lock(&g_lock);
    for (Device *d = g_devices; d; d = d->next)
        if (d->kind == KBD && d->acquired) { any = true; if (d->qcap && d->qcap - d->qcount < room) room = d->qcap - d->qcount; }
    os_unlock(&g_lock);
    return any ? room : 0;
}
extern "C" void fnvnx_input_key(uint8_t dik, int down)
{
    os_lock(&g_lock);
    uint8_t v = down ? 1 : 0;
    if (g_keys[dik] != v) {
        g_keys[dik] = v;
        for (Device *d = g_devices; d; d = d->next)
            if (d->kind == KBD) d->push(dik, v ? 0x80 : 0);
    }
    os_unlock(&g_lock);
}

extern "C" void fnvnx_input_mouse_move(int32_t dx, int32_t dy, int32_t dw)
{
    os_lock(&g_lock);
    int32_t delta[MOUSE_AXES] = {dx, dy, dw};
    for (int a = 0; a < MOUSE_AXES; a++) {
        if (!delta[a]) continue;
        g_mouse_abs[a] += delta[a];
        if (a < 2) {   /* the pointer stays on the screen in absolute mode */
            int32_t max = GetSystemMetrics(a ? SM_CYSCREEN : SM_CXSCREEN) - 1;
            if (max > 0 && g_mouse_abs[a] > max) g_mouse_abs[a] = max;
            if (g_mouse_abs[a] < 0) g_mouse_abs[a] = 0;
        }
        for (Device *d = g_devices; d; d = d->next)
            if (d->kind == MOUSE && d->acquired) {
                d->rel[a] += delta[a];
                d->push(a, d->axis_mode == DIPROPAXISMODE_REL ? delta[a] : g_mouse_abs[a]);
            }
    }
    os_unlock(&g_lock);
}

extern "C" void fnvnx_input_mouse_button(unsigned b, int down)
{
    if (b >= MOUSE_BUTTONS) return;
    os_lock(&g_lock);
    uint8_t v = down ? 1 : 0;
    if (g_buttons[b] != v) {
        g_buttons[b] = v;
        for (Device *d = g_devices; d; d = d->next)
            if (d->kind == MOUSE) d->push(MOUSE_AXES + (int)b, v ? 0x80 : 0);
    }
    os_unlock(&g_lock);
}
