/* test_combridge.cpp - the COM bridge (platform/com) driven as the GAME drives it: a face is obtained from the thunk of a
 * creation function, and every method is called ONLY through the key table by slot, with x86-style word arguments through the
 * boundary's invoke (fnvnx_bnd_guest_invoke: on the host a key is the native address of its thunk). Interfaces the game
 * implements are native objects here; the platform's calls into them are recorded at the boundary (g_bnd_invoke_hook), so a
 * direct C++ call of a game object cannot pass for a call through the proxy. Cases cb_*. */
#include "s7_test.h"

#include <dinput.h>
#include <dsound.h>
#include "fnvnx_win32_ext.h"
#include "../../platform/audio/dshow_int.h"
#include "../../platform/com/combridge.h"
#include "../s4/host_boundary.h"

#include <stdint.h>
#include <string.h>
#include <vector>

extern "C" {
uintptr_t nxb_DirectSoundCreate8(uintptr_t, uintptr_t, uintptr_t);
uintptr_t nxb_DirectSoundCreate(uintptr_t, uintptr_t, uintptr_t);
uintptr_t nxb_DirectSoundEnumerateA(uintptr_t, uintptr_t);
uintptr_t nxb_DirectInput8Create(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
uintptr_t nxb_CoCreateInstance(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
HRESULT fnvnx_dshow_com_factory(const GUID *iid, void **out);
}

namespace {

typedef uintptr_t W;
const uint32_t S_OK_ = 0, E_NOINTERFACE_ = 0x80004002u, E_INVALIDARG_ = 0x80070057u;
const GUID IID_Unknown = {0, 0, 0, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
const GUID IID_Bogus = {0x12345678, 0x1111, 0x2222, {1, 2, 3, 4, 5, 6, 7, 8}};
const GUID IID_Dev2A = {0x5944e682, 0xc92e, 0x11cf, {0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};
const GUID CLSID_FG = {0xe436ebb3, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
struct RawG { uint32_t a; uint16_t b, c; uint8_t d[8]; };   /* the 16-byte GUID of DirectShow's interfaces */

/* ---- the game's side: calling through a table by slot */
uint32_t gcall(W obj, unsigned slot, std::initializer_list<W> a = {})
{
    const uintptr_t *vt = *(const uintptr_t *const *)obj;
    fnvnx_gword w[FNVNX_BND_MAX_WORDS] = {};
    unsigned n = 0;
    w[n++] = obj;
    for (W x : a) w[n++] = x;
    fnvnx_bnd_ret r = {0, 0, 0.0};
    fnvnx_bnd_guest_invoke(vt[slot], (uint32_t)obj, n, w, FNVNX_BND_RET_INT, &r);
    return (uint32_t)r.eax;
}
unsigned ifx(const char *name)
{
    for (unsigned i = 0; i < nxcom::g_tables.niface; i++)
        if (!strcmp(nxcom::g_tables.ifaces[i].name, name)) return i;
    return ~0u;
}
/* the first word of obj is the key table of interface `name`, and every key in it is the thunk of that slot */
bool is_face_of(W obj, const char *name)
{
    nxcom::Face *f = nxcom::face_from((const void *)obj);
    unsigned ix = ifx(name);
    if (!f || ix == ~0u || f->iface != ix) return false;
    const nxcom::Iface &I = nxcom::g_tables.ifaces[ix];
    if (f->vt != nxcom::g_tables.keys + I.keyoff) return false;
    for (unsigned s = 0; s < I.nslots; s++)
        if (f->vt[s] != (uintptr_t)I.thunks[s]) return false;   /* host: the key of a thunk is its native address */
    return true;
}
unsigned long refs_of(W face, unsigned slot_addref = 1, unsigned slot_release = 2)
{
    gcall(face, slot_addref);
    return gcall(face, slot_release);
}

WAVEFORMATEX pcm(unsigned rate, unsigned ch, unsigned bits)
{
    WAVEFORMATEX w;
    memset(&w, 0, sizeof w);
    w.wFormatTag = WAVE_FORMAT_PCM;
    w.nChannels = (WORD)ch;
    w.nSamplesPerSec = rate;
    w.wBitsPerSample = (WORD)bits;
    w.nBlockAlign = (WORD)(ch * bits / 8);
    w.nAvgBytesPerSec = rate * w.nBlockAlign;
    return w;
}

/* ---- recorder of the boundary */
struct Rec { fnvnx_gaddr addr; uint32_t ecx; uint32_t n; fnvnx_gword a[FNVNX_BND_MAX_WORDS]; };
std::vector<Rec> g_rec;
int rec_hook(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t, struct fnvnx_bnd_ret *)
{
    Rec r = {addr, ecx, nargs, {}};
    for (uint32_t i = 0; i < nargs && i < FNVNX_BND_MAX_WORDS; i++) r.a[i] = args[i];
    g_rec.push_back(r);
    return 0;   /* the call happens (natively) */
}
struct Recording {
    Recording() { g_rec.clear(); g_bnd_invoke_hook = rec_hook; }
    ~Recording() { g_bnd_invoke_hook = nullptr; }
};
/* the recorded calls of the native object at `obj` on the vtable slot `slot` */
unsigned recorded(const void *obj, unsigned slot)
{
    const uintptr_t *vt = *(const uintptr_t *const *)obj;
    unsigned n = 0;
    for (const Rec &r : g_rec)
        if (r.addr == vt[slot] && r.n >= 1 && r.a[0] == (uintptr_t)obj) n++;
    return n;
}

/* ---- DirectSound: creation, identity, refcounts, interfaces out and in */
TEST(cb_selfcheck)
{
    CHECK_EQ(nxcom::selfcheck(), 0u);   /* every method's vtable slot is the spec's */
    CHECK_EQ(nxcom::init(), 0u);        /* every row of the table has its key */
    CHECK(nxcom::g_tables.niface >= 40);
    unsigned keys = 0;
    for (unsigned i = 0; i < nxcom::g_tables.niface; i++) keys += nxcom::g_tables.ifaces[i].hidden ? 0 : nxcom::g_tables.ifaces[i].nslots;
    CHECK_EQ(keys, nxcom::g_tables.nkeys);
    for (unsigned k = 0; k < nxcom::g_tables.nkeys; k++) CHECK(nxcom::g_tables.keys[k] != 0);
}

TEST(cb_dsound_faces)
{
    W ds = 0;
    CHECK_EQ(nxb_DirectSoundCreate8(0, (W)&ds, 0), S_OK_);
    CHECK(is_face_of(ds, "IDirectSound8"));
    CHECK(ds != 0 && ds != (W)nullptr);
    /* QueryInterface(IUnknown): one face per object, whichever interface asked */
    W u1 = 0, u2 = 0, u3 = 0;
    CHECK_EQ(gcall(ds, 0, {(W)&IID_Unknown, (W)&u1}), S_OK_);
    CHECK(is_face_of(u1, "IUnknownCom"));
    CHECK_EQ(gcall(u1, 0, {(W)&IID_Unknown, (W)&u2}), S_OK_);
    CHECK_EQ(u1, u2);
    W d1 = 0;
    CHECK_EQ(gcall(ds, 0, {(W)&IID_IDirectSound, (W)&d1}), S_OK_);   /* the same object as an IDirectSound */
    CHECK(is_face_of(d1, "IDirectSound"));
    CHECK_EQ(gcall(d1, 0, {(W)&IID_Unknown, (W)&u3}), S_OK_);
    CHECK_EQ(u3, u1);
    W again = 0;
    CHECK_EQ(gcall(u1, 0, {(W)&IID_IDirectSound8, (W)&again}), S_OK_);
    CHECK_EQ(again, ds);   /* the same interface of the same object is the same face */
    /* an IID that is not an interface of the spec: E_NOINTERFACE, nothing written, no reference left */
    W none = 0x1234;
    CHECK_EQ(gcall(ds, 0, {(W)&IID_Bogus, (W)&none}), E_NOINTERFACE_);
    CHECK_EQ(none, 0u);
    /* the counts are the object's: Device starts with 1; four QueryInterfaces took 5 in all (u1, u2, d1, u3, again) */
    unsigned long n = refs_of(ds);
    CHECK_EQ(n, 6u);   /* 1 + 5 */
    for (W p : {u1, u2, d1, u3, again}) gcall(p, 2);
    CHECK_EQ(refs_of(ds), 1u);
    CHECK(nxcom::live_faces() >= 1);
    CHECK_EQ(gcall(ds, 2), 0u);   /* the object is gone */
    CHECK_EQ(nxcom::live_faces(), 0u);
}

TEST(cb_dsound_buffers)
{
    W ds = 0;
    CHECK_EQ(nxb_DirectSoundCreate(0, (W)&ds, 0), S_OK_);   /* IDirectSound: its table is the 11-slot prefix */
    CHECK(is_face_of(ds, "IDirectSound"));
    CHECK_EQ(gcall(ds, 6, {0, DSSCL_PRIORITY}), S_OK_);
    DSCAPS caps;
    memset(&caps, 0, sizeof caps);
    caps.dwSize = sizeof caps;
    CHECK_EQ(gcall(ds, 4, {(W)&caps}), S_OK_);
    CHECK(caps.dwFlags != 0);
    /* a method that returns an interface: CreateSoundBuffer -> a face of IDirectSoundBuffer */
    WAVEFORMATEX wf = pcm(22050, 1, 16);
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = sizeof d;
    d.dwFlags = DSBCAPS_CTRL3D | DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLFREQUENCY;
    d.dwBufferBytes = 4410;
    d.lpwfxFormat = &wf;
    W b = 0x77;
    CHECK_EQ(gcall(ds, 3, {(W)&d, (W)&b, 0}), S_OK_);
    CHECK(is_face_of(b, "IDirectSoundBuffer"));
    /* QueryInterface to the 8: another interface of the same object, a longer table */
    W b8 = 0;
    CHECK_EQ(gcall(b, 0, {(W)&IID_IDirectSoundBuffer8, (W)&b8}), S_OK_);
    CHECK(is_face_of(b8, "IDirectSoundBuffer8"));
    CHECK(b8 != b);
    W bu = 0, bu8 = 0;
    CHECK_EQ(gcall(b, 0, {(W)&IID_Unknown, (W)&bu}), S_OK_);
    CHECK_EQ(gcall(b8, 0, {(W)&IID_Unknown, (W)&bu8}), S_OK_);
    CHECK_EQ(bu, bu8);
    /* a method taking a platform interface: DuplicateSoundBuffer(face) */
    W dup = 0;
    CHECK_EQ(gcall(ds, 5, {b, (W)&dup}), S_OK_);
    CHECK(is_face_of(dup, "IDirectSoundBuffer"));
    CHECK(dup != b);
    /* ... and the face of another interface of the same object is accepted where the base is expected (b8 derives from the buffer) */
    W dup2 = 0;
    CHECK_EQ(gcall(ds, 5, {b8, (W)&dup2}), S_OK_);
    CHECK(is_face_of(dup2, "IDirectSoundBuffer"));
    /* an interface out of a sub-object: the 3D buffer; float arguments go in their own slots (typed call of the key) */
    W b3 = 0;
    CHECK_EQ(gcall(b, 0, {(W)&IID_IDirectSound3DBuffer, (W)&b3}), S_OK_);
    CHECK(is_face_of(b3, "IDirectSound3DBuffer"));
    typedef uintptr_t (*SetPos)(uintptr_t, float, float, float, uintptr_t);
    CHECK_EQ(((SetPos)nxcom::g_tables.keys[nxcom::g_tables.ifaces[ifx("IDirectSound3DBuffer")].keyoff + 19])(b3, 1.5f, -2.0f, 3.25f, DS3D_IMMEDIATE), S_OK_);
    D3DVECTOR pos = {0, 0, 0};
    CHECK_EQ(gcall(b3, 10, {(W)&pos}), S_OK_);
    CHECK_NEAR(pos.x, 1.5, 1e-6);
    CHECK_NEAR(pos.y, -2.0, 1e-6);
    CHECK_NEAR(pos.z, 3.25, 1e-6);
    /* ordinary methods through the table: volume set and read back */
    CHECK_EQ(gcall(b, 15, {(W)(LONG)-500}), S_OK_);
    LONG vol = 0;
    CHECK_EQ(gcall(b, 6, {(W)&vol}), S_OK_);
    CHECK_EQ(vol, -500);
    /* the sub-object's count is the buffer's */
    unsigned long before = refs_of(b);
    gcall(b3, 2);
    CHECK_EQ(refs_of(b), before - 1);
    /* GetObjectInPath: the interface is chosen by the IID argument; no FX here, so the contract's answer */
    W fx = 0x55;
    CHECK(gcall(b8, 23, {(W)&GUID_All_Objects, 0, (W)&IID_Unknown, (W)&fx}) != S_OK_);
    CHECK_EQ(fx, 0u);
    /* the game gives the platform an interface of ITS OWN where a buffer of ours is expected: not ours (DSERR_INVALIDPARAM) */
    struct GameBuf : IDirectSoundBuffer {
        int calls = 0;
#define NI HRESULT STDMETHODCALLTYPE
        NI QueryInterface(REFIID, void **o) override { *o = nullptr; return E_NOINTERFACE; }
        ULONG STDMETHODCALLTYPE AddRef() override { return 2; }
        ULONG STDMETHODCALLTYPE Release() override { return 1; }
        NI GetCaps(LPDSBCAPS) override { return 0; }
        NI GetCurrentPosition(LPDWORD, LPDWORD) override { return 0; }
        NI GetFormat(LPWAVEFORMATEX, DWORD, LPDWORD) override { return 0; }
        NI GetVolume(LPLONG) override { return 0; }
        NI GetPan(LPLONG) override { return 0; }
        NI GetFrequency(LPDWORD) override { return 0; }
        NI GetStatus(LPDWORD) override { return 0; }
        NI Initialize(IDirectSound *, LPCDSBUFFERDESC) override { return 0; }
        NI Lock(DWORD, DWORD, LPVOID *, LPDWORD, LPVOID *, LPDWORD, DWORD) override { return 0; }
        NI Play(DWORD, DWORD, DWORD) override { return 0; }
        NI SetCurrentPosition(DWORD) override { return 0; }
        NI SetFormat(LPCWAVEFORMATEX) override { return 0; }
        NI SetVolume(LONG) override { return 0; }
        NI SetPan(LONG) override { return 0; }
        NI SetFrequency(DWORD) override { return 0; }
        NI Stop() override { return 0; }
        NI Unlock(LPVOID, DWORD, LPVOID, DWORD) override { return 0; }
        NI Restore() override { return 0; }
#undef NI
    } gb;
    W dup3 = 0x99;
    CHECK_EQ(gcall(ds, 5, {(W)&gb, (W)&dup3}), E_INVALIDARG_);
    CHECK_EQ(dup3, 0u);
    CHECK_EQ(nxcom::live_proxies(), 0u);   /* the proxy made for the argument went with the call */
    /* release everything: the faces go with the last reference the game holds */
    for (W p : {b, b8, bu, bu8, dup, dup2}) gcall(p, 2);
    gcall(ds, 2);
    CHECK_EQ(nxcom::live_faces(), 0u);
}

/* ---- an enumeration callback: a game function pointer reaches the platform as a word and is called through the boundary */
int g_enum_calls;
BOOL enum_cb(LPGUID, LPCSTR desc, LPCSTR, LPVOID ctx)
{
    g_enum_calls++;
    CHECK(desc != nullptr);
    CHECK_EQ((W)ctx, 0xc0ffee);
    return TRUE;
}
TEST(cb_enum_callback)
{
    Recording rec;
    g_enum_calls = 0;
    CHECK_EQ(nxb_DirectSoundEnumerateA((W)&enum_cb, 0xc0ffee), S_OK_);
    CHECK_EQ(g_enum_calls, 2);
    unsigned via = 0;
    for (const Rec &r : g_rec) via += r.addr == (W)&enum_cb;
    CHECK_EQ(via, 2u);   /* both calls went through the boundary's invoke */
}

/* ---- DirectInput: creation by IID, callbacks, devices */
int g_di_calls;
BOOL di_cb(LPCDIDEVICEINSTANCEA inst, LPVOID ctx)
{
    g_di_calls++;
    CHECK(inst != nullptr);
    CHECK_EQ((W)ctx, 0xd00d);
    return TRUE;
}
TEST(cb_dinput)
{
    Recording rec;
    W di = 0;
    CHECK_EQ(nxb_DirectInput8Create(1, 0x0800, (W)&IID_IDirectInput8A, (W)&di, 0), S_OK_);
    CHECK(is_face_of(di, "IDirectInput8A"));
    W dk = 0x99;
    CHECK_EQ(nxb_DirectInput8Create(1, 0x0800, (W)&IID_IDirectInputDevice8A, (W)&dk, 0), (uint32_t)DIERR_NOINTERFACE);
    CHECK_EQ(dk, 0u);
    CHECK_EQ(gcall(di, 4, {DI8DEVCLASS_ALL, (W)&di_cb, 0xd00d, DIEDFL_ALLDEVICES}), (uint32_t)DI_OK);
    CHECK_EQ(g_di_calls, 2);
    unsigned via = 0;
    for (const Rec &r : g_rec) via += r.addr == (W)&di_cb;
    CHECK_EQ(via, 2u);
    W dev = 0;
    CHECK_EQ(gcall(di, 3, {(W)&GUID_SysKeyboard, (W)&dev, 0}), (uint32_t)DI_OK);
    CHECK(is_face_of(dev, "IDirectInputDevice8A"));
    /* IDirectInputDevice2A / 7A are prefixes of the 8A table: the same interface, the same face */
    W dev2 = 0;
    CHECK_EQ(gcall(dev, 0, {(W)&IID_Dev2A, (W)&dev2}), (uint32_t)DI_OK);
    CHECK_EQ(dev2, dev);
    DIDEVICEINSTANCEA inst;
    memset(&inst, 0, sizeof inst);
    inst.dwSize = sizeof inst;
    CHECK_EQ(gcall(dev, 15, {(W)&inst}), (uint32_t)DI_OK);
    CHECK(inst.dwDevType != 0);
    /* force feedback does not exist: CreateEffect's out interface stays null */
    W eff = 0x66;
    unsigned char effbuf[64] = {1};
    CHECK(gcall(dev, 18, {(W)&GUID_SysKeyboard, (W)effbuf, (W)&eff, 0}) != (uint32_t)DI_OK);
    CHECK_EQ(eff, 0u);
    gcall(dev, 2);
    gcall(dev2, 2);
    gcall(di, 2);
    CHECK_EQ(nxcom::live_faces(), 0u);
}

/* ---- CoCreateInstance: the class factories of the platform's servers */
TEST(cb_cocreate)
{
    CoInitializeEx(nullptr, 0);
    W p = 0;
    CHECK_EQ(nxb_CoCreateInstance((W)&CLSID_DirectSound8, 0, CLSCTX_INPROC_SERVER, (W)&IID_IDirectSound8, (W)&p), S_OK_);
    CHECK(is_face_of(p, "IDirectSound8"));
    CHECK_EQ(gcall(p, 10, {0}), S_OK_);   /* a COM-created device: IDirectSound::Initialize(NULL) readies it */
    DSCAPS caps;
    memset(&caps, 0, sizeof caps);
    caps.dwSize = sizeof caps;
    CHECK_EQ(gcall(p, 4, {(W)&caps}), S_OK_);
    W u = 0;
    CHECK_EQ(nxb_CoCreateInstance((W)&CLSID_DirectSound8, 0, CLSCTX_INPROC_SERVER, (W)&IID_Unknown, (W)&u), S_OK_);
    CHECK(is_face_of(u, "IUnknownCom"));
    W u2 = 0;
    CHECK_EQ(gcall(p, 0, {(W)&IID_Unknown, (W)&u2}), S_OK_);   /* a second object: another identity */
    CHECK(u2 != u);
    W q = 0x31;
    CHECK(nxb_CoCreateInstance((W)&IID_Bogus, 0, CLSCTX_INPROC_SERVER, (W)&IID_IDirectSound8, (W)&q) != S_OK_);   /* not a class */
    CHECK_EQ(q, 0u);
    W r = 0x31;
    CHECK_EQ(nxb_CoCreateInstance((W)&CLSID_DirectSound8, 0, CLSCTX_INPROC_SERVER, (W)&IID_Bogus, (W)&r), E_NOINTERFACE_);   /* no such interface */
    CHECK_EQ(r, 0u);
    for (W x : {p, u, u2}) gcall(x, 2);
    CHECK_EQ(nxcom::live_faces(), 0u);
}

/* ---- DirectShow: the platform's graph over objects the GAME implements */
struct GameFilter : dsh::IBaseFilter_ {
    int refs = 1;
    int joined = 0;
    uintptr_t graph_seen = 0;
    dsh::WCH name[64] = {};
    dsh::HR QueryInterface(const void *iid, void **out) override
    {
        *out = nullptr;
        if (dsh::geq(iid, dsh::IID_IUnknown_) || dsh::geq(iid, dsh::IID_IBaseFilter_) || dsh::geq(iid, dsh::IID_IMediaFilter_) ||
            dsh::geq(iid, dsh::IID_IPersist_)) { *out = static_cast<dsh::IBaseFilter_ *>(this); refs++; return 0; }
        return (dsh::HR)0x80004002;
    }
    unsigned long AddRef() override { return (unsigned long)++refs; }
    unsigned long Release() override { return (unsigned long)--refs; }
    dsh::HR GetClassID(dsh::Guid *) override { return 0; }
    dsh::HR Stop() override { return 0; }
    dsh::HR Pause() override { return 0; }
    dsh::HR Run(dsh::LL) override { return 0; }
    dsh::HR GetState(uint32_t, int *s) override { *s = 0; return 0; }
    dsh::HR SetSyncSource(dsh::IReferenceClock_ *) override { return 0; }
    dsh::HR GetSyncSource(dsh::IReferenceClock_ **c) override { *c = nullptr; return 0; }
    dsh::HR EnumPins(void **e) override { *e = nullptr; return (dsh::HR)0x80004001; }
    dsh::HR FindPin(const dsh::WCH *, dsh::IPin_ **p) override { *p = nullptr; return (dsh::HR)0x80040216; }
    dsh::HR QueryFilterInfo(dsh::FilterInfo *) override { return 0; }
    dsh::HR JoinFilterGraph(dsh::IFilterGraph_ *g, const dsh::WCH *n) override
    {
        joined++;
        graph_seen = (uintptr_t)g;
        if (n) { size_t i = 0; for (; n[i] && i < 63; i++) name[i] = n[i]; name[i] = 0; }
        return 0;
    }
    dsh::HR QueryVendorInfo(dsh::WCH **) override { return (dsh::HR)0x80004001; }
};

TEST(cb_dshow_game_filter)
{
    CoInitializeEx(nullptr, 0);
    /* the graph is a platform object; its face is what the game would hold (the factory is the one CoCreateInstance runs) */
    RawG iid = {0x56a868a9, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};   /* IGraphBuilder */
    void *obj = nullptr;
    CHECK_EQ(fnvnx_dshow_com_factory((const GUID *)&iid, &obj), 0);
    W graph = (W)nxcom::to_game(obj, ifx("IGraphBuilder"));
    CHECK(is_face_of(graph, "IGraphBuilder"));
    GameFilter gf;
    const uint16_t nm[] = {'g', 'a', 'm', 'e', 0};
    Recording rec;
    /* IFilterGraph::AddFilter(slot 3): the game's object, not a face of ours, so the graph gets a proxy of it */
    CHECK_EQ(gcall(graph, 3, {(W)(dsh::IBaseFilter_ *)&gf, (W)nm}), 0u);
    CHECK_EQ(gf.joined, 1);
    CHECK_EQ(recorded(&gf, 13), 1u);   /* JoinFilterGraph (slot 13) was a call through the boundary */
    CHECK(recorded(&gf, 0) >= 1);      /* the graph asked the object for IBaseFilter */
    CHECK_EQ(gf.name[0], (uint16_t)'g');
    /* the graph passed ITSELF to the game: as a face, which the game can call through */
    W gseen = gf.graph_seen;
    CHECK(nxcom::face_from((const void *)gseen) != nullptr);
    CHECK(is_face_of(gseen, "IFilterGraph"));
    W en = 0;
    CHECK_EQ(gcall(gseen, 5, {(W)&en}), 0u);   /* EnumFilters through the face the game was given */
    CHECK(is_face_of(en, "IEnumFilters"));
    int refs_in_graph = gf.refs;
    /* Next: the enumerator hands the game back ITS OWN object (not a face), with the reference the call carries */
    W got = 0;
    uint32_t fetched = 0;
    CHECK_EQ(gcall(en, 3, {1, (W)&got, (W)&fetched}), 0u);
    CHECK_EQ(fetched, 1u);
    CHECK_EQ(got, (W)(dsh::IBaseFilter_ *)&gf);
    CHECK_EQ(gf.refs, refs_in_graph + 1);
    gf.Release();   /* the game drops it */
    CHECK_EQ(gf.refs, refs_in_graph);
    gcall(en, 2);
    CHECK_EQ(nxcom::live_faces(), 1u);   /* the graph (the face lent to JoinFilterGraph carries no reference) */
    /* the proxies are one per (game object, interface), alive while the graph holds the filter */
    CHECK(nxcom::live_proxies() >= 1);
    /* RemoveFilter(slot 4) */
    unsigned before = recorded(&gf, 13);
    CHECK_EQ(gcall(graph, 4, {(W)(dsh::IBaseFilter_ *)&gf}), 0u);
    CHECK_EQ(recorded(&gf, 13), before + 1);   /* JoinFilterGraph(NULL): it leaves the graph */
    CHECK_EQ(gf.joined, 2);
    CHECK_EQ(gf.refs, 1);                      /* every reference the platform held went back to the game */
    CHECK_EQ(nxcom::live_proxies(), 0u);
    /* every call the game's object saw was a call through the boundary: the counts agree with the recorder */
    unsigned total = 0;
    for (const Rec &r : g_rec) total += r.n >= 1 && r.a[0] == (uintptr_t)(dsh::IBaseFilter_ *)&gf;
    CHECK(total >= 4);
    gcall(gseen, 1);   /* the game keeps a reference of the graph through the face it was lent, and drops it */
    gcall(gseen, 2);
    CHECK_EQ(gcall(graph, 2), 0u);   /* the last one: the graph is gone */
    CHECK_EQ(nxcom::live_faces(), 0u);
}

}   // namespace
