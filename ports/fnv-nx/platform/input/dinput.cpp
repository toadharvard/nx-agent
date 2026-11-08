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
#include "fnvnx_win32_ext.h"

#include "input.h"
#include "../os/os.h"

#include <new>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DW(x) ((uint32_t)(x))

namespace {

/* both sides through fnvnx_image_const: the game's DIOBJECTDATAFORMAT tables (c_dfDIKeyboard, c_dfDIMouse in its
 * .data) point at GUIDs MSVC placed in the exe's .text, which the port does not map (a stub boot faulted here) */
bool guid_eq(const GUID &a, const GUID &b)
{
    GUID ta, tb;
    const void *pa = fnvnx_image_const(&a, sizeof(GUID), &ta), *pb = fnvnx_image_const(&b, sizeof(GUID), &tb);
    return !memcmp(pa, pb, sizeof(GUID));
}
const GUID IID_IUnknown_ = {0x00000000, 0x0000, 0x0000, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
const GUID GUID_Product_Kbd = {0x6f1d2b61, 0xd5a0, 0x11cf, {0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};
const GUID GUID_Product_Mouse = {0x6f1d2b60, 0xd5a0, 0x11cf, {0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};

enum Kind { KBD, MOUSE };
enum { MOUSE_AXES = 3, MOUSE_BUTTONS = 8, KEYS = 256 };

struct Object {        /* one device object */
    const GUID *guid;
    uint32_t type;      /* DIDFT_* with instance */
    char name[32];
};

struct Map {           /* data format entry -> device object */
    uint32_t ofs;
    int obj;            /* index into objects */
};

struct Event {
    uint32_t obj;
    int32_t value;
    uint32_t time;
};

uint32_t now_ms() { return (uint32_t)(os_monotonic_ns() / 1000000u); }

struct Device;
os_lock_t g_lock;      /* guards every device and the global state */
Device *g_devices;
uint8_t g_keys[KEYS];
uint8_t g_buttons[MOUSE_BUTTONS];
int32_t g_mouse_abs[MOUSE_AXES];
uint32_t g_sequence;

struct Device final : IDirectInputDevice8A {
    int refs = 1;
    Kind kind;
    Device *next = nullptr, *prev = nullptr;
    Object objs[KEYS];
    int nobjs = 0;
    Map *map = nullptr;
    uint32_t nmap = 0, data_size = 0;
    int obj_to_map[KEYS];
    bool acquired = false, has_format = false;
    DWORD coop = 0;
    HANDLE event = nullptr;
    uint32_t axis_mode = DIPROPAXISMODE_REL;
    int32_t rel[MOUSE_AXES] = {0, 0, 0};
    /* buffered data */
    Event *queue = nullptr;
    uint32_t qcap = 0, qhead = 0, qcount = 0;
    bool overflow = false;

    explicit Device(Kind k);
    ~Device() { free(map); free(queue); }

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
            qcount++;
        }
        if (event) SetEvent(event);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID r, void **o) override
    {
        if (!o) return E_POINTER;
        if (guid_eq(r, IID_IUnknown_) || guid_eq(r, IID_IDirectInputDevice8A)) { *o = this; AddRef(); return S_OK; }
        *o = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        int n = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (!n) {
            os_lock(&g_lock);
            if (prev) prev->next = next; else g_devices = next;
            if (next) next->prev = prev;
            os_unlock(&g_lock);
            delete this;
        }
        return (ULONG)n;
    }
    HRESULT STDMETHODCALLTYPE GetCapabilities(LPDIDEVCAPS c) override
    {
        if (!c) return E_POINTER;
        if (DW(c->dwSize) != sizeof(DIDEVCAPS) && DW(c->dwSize) != 24 /* DIDEVCAPS_DX3 */) return DIERR_INVALIDPARAM;
        DWORD sz = c->dwSize;
        memset(c, 0, DW(sz));
        c->dwSize = sz;
        c->dwFlags = DIDC_ATTACHED;
        c->dwDevType = kind == KBD ? (DI8DEVTYPE_KEYBOARD | (DI8DEVTYPEKEYBOARD_PCENH << 8))
                                   : (DI8DEVTYPE_MOUSE | (DI8DEVTYPEMOUSE_TRADITIONAL << 8));
        c->dwAxes = kind == KBD ? 0 : MOUSE_AXES;
        c->dwButtons = kind == KBD ? 128 : MOUSE_BUTTONS;
        return DI_OK;
    }
    void fill_object(int i, LPDIDEVICEOBJECTINSTANCEA o)
    {
        DWORD sz = o->dwSize;
        memset(o, 0, DW(sz));
        o->dwSize = sz;
        o->guidType = *objs[i].guid;
        o->dwOfs = (obj_to_map[i] >= 0) ? map[obj_to_map[i]].ofs : (kind == KBD ? (uint32_t)i : default_ofs(i));
        o->dwType = objs[i].type;
        o->dwFlags = (objs[i].type & DIDFT_AXIS) ? DIDOI_ASPECTPOSITION : 0;
        snprintf(o->tszName, sizeof o->tszName, "%s", objs[i].name);
    }
    uint32_t default_ofs(int i) const { return i < MOUSE_AXES ? (uint32_t)i * 4 : 12 + (uint32_t)(i - MOUSE_AXES); }
    HRESULT STDMETHODCALLTYPE EnumObjects(LPDIENUMDEVICEOBJECTSCALLBACKA cb, LPVOID ref, DWORD flags) override
    {
        if (!cb) return DIERR_INVALIDPARAM;
        uint32_t f = DW(flags) & 0xFF;
        for (int i = 0; i < nobjs; i++) {
            if (f && !(objs[i].type & f)) continue;
            DIDEVICEOBJECTINSTANCEA o;
            o.dwSize = sizeof o;
            fill_object(i, &o);
            if (cb(&o, ref) == DIENUM_STOP) break;
        }
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetProperty(REFGUID g, LPDIPROPHEADER h) override
    {
        if (!h) return DIERR_INVALIDPARAM;
        uintptr_t prop = (uintptr_t)&g;
        if (prop == 1 /* BUFFERSIZE */ || prop == 2 /* AXISMODE */ || prop == 3 /* GRANULARITY */) {
            if (DW(h->dwSize) < sizeof(DIPROPDWORD)) return DIERR_INVALIDPARAM;
            DWORD v = prop == 1 ? qcap : prop == 2 ? axis_mode : (kind == MOUSE && DW(h->dwObj) == 8 ? 120 : 1);
            ((LPDIPROPDWORD)h)->dwData = v;
            return DI_OK;
        }
        if (prop == 4 /* RANGE */) return DIERR_UNSUPPORTED;   /* relative axes have no range */
        return DIERR_UNSUPPORTED;
    }
    HRESULT STDMETHODCALLTYPE SetProperty(REFGUID g, LPCDIPROPHEADER h) override
    {
        if (!h) return DIERR_INVALIDPARAM;
        uintptr_t prop = (uintptr_t)&g;
        if (prop == 1) {
            if (acquired) return DIERR_ACQUIRED;
            uint32_t n = DW(((LPCDIPROPDWORD)h)->dwData);
            if (n > 4096) n = 4096;
            Event *q = n ? (Event *)calloc(n, sizeof *q) : nullptr;
            if (n && !q) return DIERR_OUTOFMEMORY;
            os_lock(&g_lock);
            free(queue);
            queue = q; qcap = n; qhead = qcount = 0; overflow = false;
            os_unlock(&g_lock);
            return DI_OK;
        }
        if (prop == 2) {
            if (acquired) return DIERR_ACQUIRED;
            axis_mode = DW(((LPCDIPROPDWORD)h)->dwData) ? DIPROPAXISMODE_REL : DIPROPAXISMODE_ABS;
            return DI_OK;
        }
        return DI_PROPNOEFFECT;
    }
    HRESULT STDMETHODCALLTYPE Acquire() override
    {
        if (!has_format) return DIERR_INVALIDPARAM;
        os_lock(&g_lock);
        bool was = acquired;
        acquired = true;
        rel[0] = rel[1] = rel[2] = 0;
        os_unlock(&g_lock);
        return was ? S_FALSE : DI_OK;
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
        os_unlock(&g_lock);
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDeviceData(DWORD cbObj, LPDIDEVICEOBJECTDATA rg, LPDWORD inout, DWORD fl) override
    {
        if (!inout) return DIERR_INVALIDPARAM;
        if (DW(cbObj) != sizeof(DIDEVICEOBJECTDATA) && DW(cbObj) != 16 /* DX3 form */) return DIERR_INVALIDPARAM;
        if (!qcap) return DIERR_NOTBUFFERED;
        fnvnx_input_pump();
        os_lock(&g_lock);
        if (!acquired) { os_unlock(&g_lock); return DIERR_NOTACQUIRED; }
        uint32_t want = DW(*inout), n = want < qcount ? want : qcount;
        bool peek = (DW(fl) & DIGDD_PEEK) != 0;
        for (uint32_t i = 0; rg && i < n; i++) {
            Event &e = queue[(qhead + i) % qcap];
            LPDIDEVICEOBJECTDATA d = (LPDIDEVICEOBJECTDATA)((uint8_t *)rg + (size_t)i * DW(cbObj));
            d->dwOfs = map[obj_to_map[e.obj]].ofs;
            d->dwData = (DWORD)(uint32_t)e.value;
            d->dwTimeStamp = e.time;
            d->dwSequence = ++g_sequence;
            if (DW(cbObj) == sizeof(DIDEVICEOBJECTDATA)) d->uAppData = (UINT_PTR)-1;
        }
        if (!peek) { qhead = qcap ? (qhead + n) % qcap : 0; qcount -= n; }
        *inout = n;
        HRESULT hr = overflow ? DI_BUFFEROVERFLOW : DI_OK;
        if (!peek) overflow = false;
        os_unlock(&g_lock);
        return hr;
    }
    HRESULT STDMETHODCALLTYPE SetDataFormat(LPCDIDATAFORMAT f) override;
    HRESULT STDMETHODCALLTYPE SetEventNotification(HANDLE h) override
    {
        if (acquired) return DIERR_ACQUIRED;
        event = h;
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE SetCooperativeLevel(HWND, DWORD fl) override
    {
        uint32_t f = DW(fl);
        bool ex = f & DISCL_EXCLUSIVE, nex = f & DISCL_NONEXCLUSIVE, fg = f & DISCL_FOREGROUND, bg = f & DISCL_BACKGROUND;
        if (ex == nex || fg == bg) return DIERR_INVALIDPARAM;
        if (kind == KBD && ex && bg) return DIERR_UNSUPPORTED;
        coop = fl;
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetObjectInfo(LPDIDEVICEOBJECTINSTANCEA o, DWORD obj, DWORD how) override
    {
        if (!o) return E_POINTER;
        int idx = -1;
        if (DW(how) == DIPH_BYOFFSET) {
            for (uint32_t i = 0; i < nmap; i++) if (map[i].ofs == DW(obj)) idx = map[i].obj;
        } else if (DW(how) == DIPH_BYID) {
            for (int i = 0; i < nobjs; i++) if ((objs[i].type & 0x00FFFFFF) == (DW(obj) & 0x00FFFFFF)) idx = i;
        } else
            return DIERR_UNSUPPORTED;
        if (idx < 0) return DIERR_OBJECTNOTFOUND;
        fill_object(idx, o);
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDeviceInfo(LPDIDEVICEINSTANCEA d) override;
    HRESULT STDMETHODCALLTYPE RunControlPanel(HWND, DWORD) override { return DI_OK; }
    HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE, DWORD, REFGUID) override { return DI_OK; }
    HRESULT STDMETHODCALLTYPE CreateEffect(REFGUID, const void *, IDirectInputEffect **e, LPUNKNOWN) override { if (e) *e = nullptr; return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE EnumEffects(void *, LPVOID, DWORD) override { return DI_OK; }
    HRESULT STDMETHODCALLTYPE GetEffectInfo(void *, REFGUID) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE GetForceFeedbackState(LPDWORD) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE SendForceFeedbackCommand(DWORD) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE EnumCreatedEffectObjects(void *, LPVOID, DWORD) override { return DI_OK; }
    HRESULT STDMETHODCALLTYPE Escape(void *) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE Poll() override { return acquired ? DI_NOEFFECT : DIERR_NOTACQUIRED; }
    HRESULT STDMETHODCALLTYPE SendDeviceData(DWORD, LPCDIDEVICEOBJECTDATA, LPDWORD, DWORD) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE EnumEffectsInFile(LPCSTR, void *, LPVOID, DWORD) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE WriteEffectToFile(LPCSTR, DWORD, void *, DWORD) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE BuildActionMap(void *, LPCSTR, DWORD) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE SetActionMap(void *, LPCSTR, DWORD) override { return DIERR_UNSUPPORTED; }
    HRESULT STDMETHODCALLTYPE GetImageInfo(void *) override { return DIERR_UNSUPPORTED; }
};

Device::Device(Kind k) : kind(k)
{
    for (int i = 0; i < KEYS; i++) obj_to_map[i] = -1;
    if (k == KBD) {
        for (int i = 0; i < KEYS; i++) {
            objs[i].guid = &GUID_Key;
            objs[i].type = DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(i);
            snprintf(objs[i].name, sizeof objs[i].name, "Key %d", i);
        }
        nobjs = KEYS;
    } else {
        static const GUID *axes[MOUSE_AXES] = {&GUID_XAxis, &GUID_YAxis, &GUID_ZAxis};
        static const char *names[MOUSE_AXES] = {"X-axis", "Y-axis", "Wheel"};
        for (int i = 0; i < MOUSE_AXES; i++) {
            objs[i].guid = axes[i];
            objs[i].type = DIDFT_RELAXIS | DIDFT_MAKEINSTANCE(i);
            snprintf(objs[i].name, sizeof objs[i].name, "%s", names[i]);
        }
        for (int i = 0; i < MOUSE_BUTTONS; i++) {
            objs[MOUSE_AXES + i].guid = &GUID_Button;
            objs[MOUSE_AXES + i].type = DIDFT_PSHBUTTON | DIDFT_MAKEINSTANCE(i);
            snprintf(objs[MOUSE_AXES + i].name, sizeof objs[0].name, "Button %d", i);
        }
        nobjs = MOUSE_AXES + MOUSE_BUTTONS;
    }
}

HRESULT Device::SetDataFormat(LPCDIDATAFORMAT f)
{
    if (!f) return DIERR_INVALIDPARAM;
    if (acquired) return DIERR_ACQUIRED;
    if (DW(f->dwSize) != sizeof(DIDATAFORMAT) || DW(f->dwObjSize) != sizeof(DIOBJECTDATAFORMAT)) return DIERR_INVALIDPARAM;
    uint32_t n = DW(f->dwNumObjs), size = DW(f->dwDataSize);
    if (size & 3) return DIERR_INVALIDPARAM;
    Map *m = n ? (Map *)calloc(n, sizeof *m) : nullptr;
    if (n && !m) return DIERR_OUTOFMEMORY;
    /* the game's rgodf tables (c_dfDIKeyboard 0x00ee6f10, c_dfDIMouse 0x00ee6e60) are themselves in the exe's .text,
     * which the port does not map: read the whole array through fnvnx_image_const (p itself when mapped) */
    if (n > 4096) { free(m); return DIERR_INVALIDPARAM; }
    DIOBJECTDATAFORMAT *tbuf = n ? (DIOBJECTDATAFORMAT *)malloc(n * sizeof *tbuf) : nullptr;
    if (n && !tbuf) { free(m); return DIERR_OUTOFMEMORY; }
    const DIOBJECTDATAFORMAT *rgodf =
        n ? (const DIOBJECTDATAFORMAT *)fnvnx_image_const(f->rgodf, n * (uint32_t)sizeof *tbuf, tbuf) : nullptr;
    int o2m[KEYS];
    bool used[KEYS];
    for (int i = 0; i < KEYS; i++) { o2m[i] = -1; used[i] = false; }
    uint32_t nm = 0;
    for (uint32_t i = 0; i < n; i++) {
        const DIOBJECTDATAFORMAT &e = rgodf[i];
        uint32_t t = DW(e.dwType), inst = (t >> 8) & 0xFFFF, ofs = DW(e.dwOfs);
        bool want_axis = (t & DIDFT_AXIS) != 0, want_button = (t & DIDFT_BUTTON) != 0;
        int found = -1;
        for (int k = 0; k < nobjs && found < 0; k++) {
            if (used[k]) continue;
            uint32_t ot = objs[k].type;
            if (want_axis && !(ot & DIDFT_AXIS)) continue;
            if (want_button && !(ot & DIDFT_BUTTON)) continue;
            if (!want_axis && !want_button && (t & 0xFF) && !(ot & t & 0xFF)) continue;
            if (inst != 0xFFFF && ((ot >> 8) & 0xFFFF) != inst) continue;
            if (e.pguid && !guid_eq(*e.pguid, *objs[k].guid)) continue;
            found = k;
        }
        if (found < 0) {
            if (t & DIDFT_OPTIONAL) continue;
            free(m);
            free(tbuf);
            return DIERR_INVALIDPARAM;
        }
        uint32_t width = (objs[found].type & DIDFT_AXIS) ? 4 : 1;
        if (ofs + width > size) { free(m); free(tbuf); return DIERR_INVALIDPARAM; }
        used[found] = true;
        o2m[found] = (int)nm;
        m[nm].ofs = ofs;
        m[nm].obj = found;
        nm++;
    }
    free(tbuf);
    os_lock(&g_lock);
    free(map);
    map = m;
    nmap = nm;
    data_size = size;
    memcpy(obj_to_map, o2m, sizeof o2m);
    has_format = true;
    qhead = qcount = 0;
    os_unlock(&g_lock);
    return DI_OK;
}

HRESULT Device::GetDeviceInfo(LPDIDEVICEINSTANCEA d)
{
    if (!d) return E_POINTER;
    if (DW(d->dwSize) != sizeof(DIDEVICEINSTANCEA)) return DIERR_INVALIDPARAM;
    memset(d, 0, sizeof *d);
    d->dwSize = sizeof *d;
    d->guidInstance = kind == KBD ? GUID_SysKeyboard : GUID_SysMouse;
    d->guidProduct = kind == KBD ? GUID_Product_Kbd : GUID_Product_Mouse;
    d->dwDevType = kind == KBD ? (DI8DEVTYPE_KEYBOARD | (DI8DEVTYPEKEYBOARD_PCENH << 8))
                               : (DI8DEVTYPE_MOUSE | (DI8DEVTYPEMOUSE_TRADITIONAL << 8));
    snprintf(d->tszInstanceName, sizeof d->tszInstanceName, "%s", kind == KBD ? "Keyboard" : "Mouse");
    snprintf(d->tszProductName, sizeof d->tszProductName, "%s", kind == KBD ? "Keyboard" : "Mouse");
    d->wUsagePage = 1;
    d->wUsage = kind == KBD ? 6 : 2;
    return DI_OK;
}

struct DInput final : IDirectInput8A {
    int refs = 1;
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
        if (!out) return E_POINTER;
        *out = nullptr;
        if (outer) return DIERR_NOAGGREGATION;
        Kind k;
        if (guid_eq(g, GUID_SysKeyboard)) k = KBD;
        else if (guid_eq(g, GUID_SysMouse)) k = MOUSE;
        else return DIERR_DEVICENOTREG;
        Device *d = new (std::nothrow) Device(k);
        if (!d) return DIERR_OUTOFMEMORY;
        os_lock(&g_lock);
        d->next = g_devices;
        if (g_devices) g_devices->prev = d;
        g_devices = d;
        os_unlock(&g_lock);
        *out = d;
        return DI_OK;
    }
    HRESULT STDMETHODCALLTYPE EnumDevices(DWORD type, LPDIENUMDEVICESCALLBACKA cb, LPVOID ref, DWORD) override
    {
        if (!cb) return DIERR_INVALIDPARAM;
        uint32_t t = DW(type) & 0xFF;
        bool kbd = t == DI8DEVCLASS_ALL || t == DI8DEVCLASS_KEYBOARD || t == DI8DEVTYPE_KEYBOARD;
        bool mouse = t == DI8DEVCLASS_ALL || t == DI8DEVCLASS_POINTER || t == DI8DEVTYPE_MOUSE;
        for (int i = 0; i < 2; i++) {
            if (i == 0 ? !kbd : !mouse) continue;
            Device tmp(i == 0 ? KBD : MOUSE);
            DIDEVICEINSTANCEA d;
            d.dwSize = sizeof d;
            tmp.GetDeviceInfo(&d);
            if (cb(&d, ref) == DIENUM_STOP) break;
        }
        return DI_OK;   /* game controllers: none (XInput) */
    }
    HRESULT STDMETHODCALLTYPE GetDeviceStatus(REFGUID g) override
    {
        return (guid_eq(g, GUID_SysKeyboard) || guid_eq(g, GUID_SysMouse)) ? DI_OK : DIERR_DEVICENOTREG;
    }
    HRESULT STDMETHODCALLTYPE RunControlPanel(HWND, DWORD) override { return DI_OK; }
    HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE, DWORD v) override { return DW(v) < 0x0800 ? DIERR_OLDDIRECTINPUTVERSION : DI_OK; }
    HRESULT STDMETHODCALLTYPE FindDevice(REFGUID, LPCSTR, LPGUID) override { return DIERR_DEVICENOTREG; }
    HRESULT STDMETHODCALLTYPE EnumDevicesBySemantics(LPCSTR, void *, void *, LPVOID, DWORD) override { return DI_OK; }
    HRESULT STDMETHODCALLTYPE ConfigureDevices(void *, void *, DWORD, LPVOID) override { return DIERR_UNSUPPORTED; }
};

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

extern "C" HRESULT fnvnx_dinput_com_factory(const GUID *iid, void **out)
{
    return DirectInput8Create(nullptr, 0x0800, *iid, out, nullptr);
}

/* ---- producers */
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
