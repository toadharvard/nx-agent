/* test_nolock_audio.cpp - the rule "no platform lock is held across a call into the game" for the audio, input and COM
 * callouts (the Win32-layer and CRT ones are tests/s4/test_nolock.cpp): every enumeration callback, the waveOut callback and
 * the game's COM objects behind the proxies (platform/com/combridge.cpp) are driven with a callback / method that DOES NOT
 * RETURN (a longjmp out to the test, which discards the platform's frames without cleanup: what a game exception unwinding
 * past the import does). Afterwards the thread must hold no platform lock (fnvnx_bnd_locks_held(), counted by the host
 * backend), the same API must work again here and on another thread, and the run-wide violation count must stay 0 (host_boundary.h).
 * Cases nlk_*. DirectShow's callouts have their own, exhaustive case: dsl_noreturn_every_callout (test_dshow_lock.cpp). */
#include "s7_test.h"

#include <dinput.h>
#include <dsound.h>
#include "fnvnx_win32_ext.h"
#include "../test_threads.h"
#include "../../platform/audio/backend.h"
#include "../../platform/com/combridge.h"
#include "../s4/host_boundary.h"

#include <setjmp.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <thread>

extern "C" {
HRESULT nlk_DirectSoundEnumerateA(void *cb, void *ctx) __asm__("DirectSoundEnumerateA");
void fnvnx_waveout_service(void);
UINT waveOutOpen(void **, UINT, const void *, uintptr_t, uintptr_t, DWORD);
UINT waveOutClose(void *);
}

namespace {

jmp_buf g_jb;
[[noreturn]] void escape() { longjmp(g_jb, 1); }
#define ESCAPES(...) ([&]() -> bool { if (setjmp(g_jb)) return true; __VA_ARGS__; return false; }())
bool clean() { return fnvnx_bnd_locks_held() == 0; }
void on_other_thread(void (*f)(void))
{
    test_thread t(f);
    t.join();
}

/* ---- DirectSound */
int g_ds_calls;
BOOL ds_escape(void *, const char *, const char *, void *) { escape(); }
BOOL ds_ok(void *, const char *, const char *, void *) { g_ds_calls++; return TRUE; }

TEST(nlk_escape_dsound_enumerate)
{
    CHECK(ESCAPES(nlk_DirectSoundEnumerateA((void *)ds_escape, nullptr)));
    CHECK(clean());
    g_ds_calls = 0;
    CHECK_EQ(nlk_DirectSoundEnumerateA((void *)ds_ok, nullptr), 0);
    CHECK_EQ(g_ds_calls, 2);
    on_other_thread([] {
        g_ds_calls = 0;
        CHECK_EQ(nlk_DirectSoundEnumerateA((void *)ds_ok, nullptr), 0);
        CHECK_EQ(g_ds_calls, 2);
    });
    CHECK(clean());
}

/* ---- DirectInput: device, object and action-map enumeration */
int g_di_calls;
BOOL di_escape(LPCDIDEVICEINSTANCEA, LPVOID) { escape(); }
BOOL di_ok(LPCDIDEVICEINSTANCEA, LPVOID) { g_di_calls++; return DIENUM_CONTINUE; }
BOOL obj_escape(LPCDIDEVICEOBJECTINSTANCEA, LPVOID) { escape(); }
BOOL obj_ok(LPCDIDEVICEOBJECTINSTANCEA, LPVOID) { g_di_calls++; return DIENUM_CONTINUE; }
BOOL sem_escape(LPCDIDEVICEINSTANCEA, IDirectInputDevice8A *, DWORD, DWORD, LPVOID) { escape(); }
BOOL sem_ok(LPCDIDEVICEINSTANCEA, IDirectInputDevice8A *d, DWORD, DWORD, LPVOID) { g_di_calls++; (void)d; return DIENUM_CONTINUE; }

IDirectInput8A *di()
{
    void *p = nullptr;
    CHECK_EQ(DirectInput8Create((HINSTANCE)1, 0x0800, IID_IDirectInput8A, &p, nullptr), DI_OK);
    return (IDirectInput8A *)p;
}

struct Act { UINT_PTR app; DWORD sem, flags; LPCSTR name; GUID inst; DWORD obj, how; };   /* DIACTIONA */

TEST(nlk_escape_dinput_enumerations)
{
    IDirectInput8A *in = di();
    CHECK(in != nullptr);
    if (!in) return;
    CHECK(ESCAPES(in->EnumDevices(DI8DEVCLASS_ALL, di_escape, nullptr, 0)));
    CHECK(clean());
    g_di_calls = 0;
    CHECK_EQ(in->EnumDevices(DI8DEVCLASS_ALL, di_ok, nullptr, 0), DI_OK);
    CHECK_EQ(g_di_calls, 2);
    IDirectInputDevice8A *kb = nullptr;
    CHECK_EQ(in->CreateDevice(GUID_SysKeyboard, &kb, nullptr), DI_OK);
    CHECK(kb != nullptr);
    if (kb) {
        CHECK(ESCAPES(kb->EnumObjects(obj_escape, nullptr, DIDFT_ALL)));
        CHECK(clean());
        g_di_calls = 0;
        CHECK_EQ(kb->EnumObjects(obj_ok, nullptr, DIDFT_ALL), DI_OK);
        CHECK(g_di_calls > 0);
    }
    /* the action-map enumeration makes a device per class and hands it to the callback with a reference of its own */
    struct F { DWORD size, actsize, datasize, n; Act *a; GUID map; DWORD genre, bufsize; LONG amin, amax; HINSTANCE hinst; DWORD ft0, ft1, crc; CHAR name[MAX_PATH]; } f;
    memset(&f, 0, sizeof f);
    Act acts[1] = {};
    acts[0].app = 77; acts[0].sem = 0x81000400u | DIK_A;
    f.size = sizeof f; f.actsize = sizeof(Act); f.datasize = 16; f.n = 1; f.a = acts;
    CHECK(ESCAPES(in->EnumDevicesBySemantics(nullptr, &f, (void *)sem_escape, nullptr, 0)));
    CHECK(clean());
    g_di_calls = 0;
    CHECK_EQ(in->EnumDevicesBySemantics(nullptr, &f, (void *)sem_ok, nullptr, 0), DI_OK);
    CHECK(g_di_calls > 0);
    on_other_thread([] {
        IDirectInput8A *i2 = di();
        if (!i2) return;
        g_di_calls = 0;
        CHECK_EQ(i2->EnumDevices(DI8DEVCLASS_ALL, di_ok, nullptr, 0), DI_OK);
        CHECK_EQ(g_di_calls, 2);
        i2->Release();
    });
    if (kb) kb->Release();
    in->Release();
    CHECK(clean());
}

/* ---- waveOut: the callback runs on the platform's own delivery thread, which a test cannot escape from (the jump buffer
 * is the test thread's); a callback that does not return is a callback that is parked for as long as the test runs: no
 * platform lock may be held meanwhile, so the whole waveOut API keeps working from this thread and another */
WAVEFORMATEX pcm44()
{
    WAVEFORMATEX w;
    memset(&w, 0, sizeof w);
    w.wFormatTag = 1; w.nChannels = 2; w.nSamplesPerSec = 44100; w.wBitsPerSample = 16;
    w.nBlockAlign = 4; w.nAvgBytesPerSec = 44100 * 4;
    return w;
}
int g_wo_calls, g_wo_parked, g_wo_release, g_wo_held;
void wo_cb(void *, UINT, uintptr_t, uintptr_t, uintptr_t)
{
    int n = __atomic_add_fetch(&g_wo_calls, 1, __ATOMIC_SEQ_CST);
    if (fnvnx_bnd_locks_held()) __atomic_fetch_add(&g_wo_held, 1, __ATOMIC_SEQ_CST);
    if (n == 1) {   /* the first callback never returns (until the test lets it) */
        __atomic_store_n(&g_wo_parked, 1, __ATOMIC_SEQ_CST);
        while (!__atomic_load_n(&g_wo_release, __ATOMIC_SEQ_CST)) usleep(200);
    }
}
void wait_for(int *flag)
{
    for (int i = 0; i < 20000 && !__atomic_load_n(flag, __ATOMIC_SEQ_CST); i++) usleep(200);
}

TEST(nlk_waveout_callback_parked_holds_no_lock)
{
    WAVEFORMATEX f = pcm44();
    void *h = nullptr;
    g_wo_calls = g_wo_parked = g_wo_release = g_wo_held = 0;
    CHECK_EQ(waveOutOpen(&h, 0, &f, (uintptr_t)&wo_cb, 77, 0x30000), 0u);   /* WOM_OPEN: the callback parks */
    wait_for(&g_wo_parked);
    CHECK_EQ(g_wo_parked, 1);
    /* the delivery thread is inside the game's callback: the API works, queues more messages, from here and from another thread */
    void *h2 = nullptr;
    CHECK_EQ(waveOutOpen(&h2, 0, &f, (uintptr_t)&wo_cb, 78, 0x30000), 0u);
    CHECK_EQ(waveOutClose(h2), 0u);
    on_other_thread([] {
        WAVEFORMATEX g = pcm44();
        void *h3 = nullptr;
        CHECK_EQ(waveOutOpen(&h3, 0, &g, (uintptr_t)&wo_cb, 79, 0x30000), 0u);
        CHECK_EQ(waveOutClose(h3), 0u);
    });
    CHECK_EQ(waveOutClose(h), 0u);
    CHECK_EQ(g_wo_calls, 1);               /* nothing was delivered behind the parked callback */
    __atomic_store_n(&g_wo_release, 1, __ATOMIC_SEQ_CST);   /* it returns now: the queue drains */
    fnvnx_waveout_service();
    CHECK_EQ(g_wo_calls, 6);               /* open, close of three handles */
    CHECK_EQ(g_wo_held, 0);                /* the delivery thread held no platform lock at any callback */
    CHECK(clean());
}

/* ---- the game's COM objects behind the proxies: AddRef / Release / QueryInterface that never return */
struct GameUnk {
    const void *const *vt;
    int refs;
    int mode;   /* 0 normal, 1 AddRef escapes, 2 Release escapes, 3 QueryInterface escapes */
};
HRESULT g_qi(void *self, const void *, void **out)
{
    GameUnk *g = (GameUnk *)self;
    if (g->mode == 3) escape();
    *out = self;
    g->refs++;
    return 0;
}
unsigned long g_addref(void *self)
{
    GameUnk *g = (GameUnk *)self;
    if (g->mode == 1) escape();
    return (unsigned long)++g->refs;
}
unsigned long g_release(void *self)
{
    GameUnk *g = (GameUnk *)self;
    if (g->mode == 2) escape();
    return (unsigned long)--g->refs;
}
const void *const g_vt[3] = {(const void *)g_qi, (const void *)g_addref, (const void *)g_release};
struct IUnk {
    virtual HRESULT QueryInterface(const void *, void **) = 0;
    virtual unsigned long AddRef() = 0;
    virtual unsigned long Release() = 0;
};
unsigned unk_if()
{
    for (unsigned i = 0; i < nxcom::g_tables.niface; i++)
        if (!strcmp(nxcom::g_tables.ifaces[i].name, "IUnknownCom")) return i;
    return ~0u;
}

TEST(nlk_escape_game_com_object)
{
    nxcom::init();
    unsigned ix = unk_if();
    CHECK(ix != ~0u);
    static const GUID iid = {0, 0, 0, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
    GameUnk g = {g_vt, 1, 0};
    IUnk *p = (IUnk *)nxcom::adopt_game(&g, ix);   /* the game's reference becomes the proxy's */
    CHECK(p != nullptr);
    if (!p) return;
    p->AddRef();                                    /* the platform's second reference: the game is not asked */
    CHECK_EQ(g.refs, 1);
    p->Release();
    /* the last release goes to the game: it never returns */
    g.mode = 2;
    CHECK(ESCAPES(p->Release()));
    CHECK(clean());
    g.mode = 0;
    /* the proxy table survived: the same game object is adopted again (a reference of the game's own), used from another thread */
    g.refs = 1;
    p = (IUnk *)nxcom::adopt_game(&g, ix);
    CHECK(p != nullptr);
    g.mode = 3;
    void *q = nullptr;
    CHECK(ESCAPES(p->QueryInterface(&iid, &q)));   /* QueryInterface of the game's object never returns */
    CHECK(clean());
    g.mode = 0;
    static IUnk *pp;
    pp = p;
    on_other_thread([] {
        void *o = nullptr;
        CHECK_EQ(pp->QueryInterface(&iid, &o), 0);
        if (o) ((IUnk *)nxcom::adopt_game(o, unk_if()))->Release();
        pp->AddRef();
        pp->Release();
    });
    /* a platform object handed to the game whose AddRef never returns: to_game takes the proxy's reference back first */
    g.mode = 1;
    pp->AddRef();   /* prefs 2: no call to the game */
    void *back = nullptr;
    CHECK(ESCAPES(back = nxcom::to_game(p, ix)));   /* the proxy goes back to the game: its AddRef is the game's call */
    CHECK(clean());
    g.mode = 0;
    pp->Release();
    pp->Release();
    CHECK(clean());
}

}   // namespace
