/* test_dshow_lock.cpp - the rule "no platform lock is held across a call into the game" for the DirectShow graph
 * (platform/audio/dshow.cpp, dshow_filters.cpp; platform/include/fnvnx_lockcount.h). Cases dsl_*.
 *
 * "The game" here is native test objects that implement IBaseFilter / IPin / IEnumPins / IEnumMediaTypes / IReferenceClock /
 * IAsyncReader (as test_combridge.cpp does): they reach the graph only through combridge, so the graph holds PROXIES of them and
 * every method it calls is a call through fnvnx_bnd_guest_invoke, which the host boundary checks (g_bnd_lock_violations: a guest
 * call made while the thread holds a counted platform lock). The game's calls into the graph go through the keys of the faces.
 *
 * Every method of every game object starts with a "callout" that (a) re-enters the graph from inside the callback (AddFilter,
 * RemoveFilter, EnumFilters, FindFilterByName, a state query: a held graph lock would deadlock the test, and the graph must
 * show a consistent filter list) and/or (b) does not return: a C++ exception thrown by the object and caught by the test
 * outside the platform call (mode 1), or a longjmp to the test (mode 2: the platform frames are discarded without running a
 * destructor, which is what a game exception unwinding past the import does). Case dsl_noreturn_every_callout does (b) at EVERY
 * callout position of a scenario that drives every graph operation; afterwards the graph must still be usable from this and
 * from another thread, and no violation may have been recorded. */
#include "s7_test.h"

#include "fnvnx_win32_ext.h"
#include "../test_threads.h"
#define DSH_IMPL
#include "../../platform/audio/dshow_int.h"
#include "../../platform/audio/mixer.h"
#include "../../platform/com/combridge.h"
#include "../s4/host_boundary.h"

#include <setjmp.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

#include <chrono>
#include <functional>
#include <future>
#include <memory>
#include <thread>
#include <vector>

extern "C" HRESULT fnvnx_dshow_com_factory(const GUID *iid, void **out);

namespace {

typedef uintptr_t W;
using dsh::HR;
using dsh::Guid;
struct RawG { uint32_t a; uint16_t b, c; uint8_t d[8]; };
RawG q_graphbuilder = {0x56a868a9, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
RawG q_control = {0x56a868b1, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
RawG q_mfilter = {0x56a86899, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
RawG q_event = {0x56a868b6, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
RawG q_sink = {0x56a868a2, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
RawG q_site = {0xFC4801A3, 0x2BA9, 0x11CF, {0xA2, 0x29, 0x00, 0xAA, 0x00, 0x3D, 0x73, 0x52}};

/* ---- the game's side: a call through a key table by slot (the thunk of the platform object behind a face) */
uint32_t gc(W obj, unsigned slot, std::initializer_list<W> a = {})
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
bool is_face(const void *p) { return p && nxcom::face_from(p) != nullptr; }
unsigned ifx(const char *name)
{
    for (unsigned i = 0; i < nxcom::g_tables.niface; i++)
        if (!strcmp(nxcom::g_tables.ifaces[i].name, name)) return i;
    return ~0u;
}
/* the game's own AddRef / Release / pin calls on a pointer it was given: a face of ours is called through its keys, one of the
   game's own objects directly (it is a native object here) */
void u_addref(dsh::Unk *u) { if (is_face(u)) gc((W)u, 1); else u->AddRef(); }
void u_release(dsh::Unk *u) { if (is_face(u)) gc((W)u, 2); else u->Release(); }
HR pin_receive(dsh::IPin_ *to, dsh::IPin_ *from, const dsh::MT *mt)
{
    return is_face(to) ? (HR)gc((W)to, 4, {(W)from, (W)mt}) : to->ReceiveConnection(from, mt);
}

/* ---- the script every callout consults */
struct GameThrow {};
struct Script {
    int depth = 0;                      /* 1 while a re-entrant action runs: it does not re-enter again */
    int calls = 0;                      /* callouts so far */
    int fire_at = 0;                    /* 1-based callout at which the callout does not return (0: never) */
    int mode = 0;                       /* 1: throw GameThrow, 2: longjmp to *jb */
    bool fired = false;
    jmp_buf *jb = nullptr;
    int reentries = 0;
    std::function<void()> reenter;      /* run at every callout of depth 0 */
};
Script g_sc;
void callout()
{
    g_sc.calls++;
    if (g_sc.fire_at && !g_sc.fired && g_sc.calls == g_sc.fire_at) {
        g_sc.fired = true;
        if (g_sc.mode == 1) throw GameThrow{};
        if (g_sc.mode == 2 && g_sc.jb) longjmp(*g_sc.jb, 1);
    }
    if (g_sc.reenter && g_sc.depth == 0) {
        g_sc.depth++;
        g_sc.reentries++;
        g_sc.reenter();
        g_sc.depth--;
    }
}

const HR S_OK_ = 0, E_NOINTERFACE_ = (HR)0x80004002, kNotImpl = (HR)0x80004001, E_FAIL_ = (HR)0x80004005, VFW_E_NOT_FOUND_ = (HR)0x80040216;
const uint16_t kName1[] = {'F', '1', 0}, kName2[] = {'F', '2', 0}, kNameX[] = {'X', 0};

struct GFilt;
struct GPin;

/* the game's clock */
struct GClock : dsh::IReferenceClock_ {
    int refs = 1;
    dsh::LL t = 1000;
    HR QueryInterface(const void *iid, void **out) override
    {
        callout();
        *out = nullptr;
        if (dsh::geq(iid, dsh::IID_IUnknown_) || dsh::geq(iid, dsh::IID_IReferenceClock_)) { *out = static_cast<dsh::IReferenceClock_ *>(this); refs++; return 0; }
        return E_NOINTERFACE_;
    }
    unsigned long AddRef() override { callout(); return (unsigned long)++refs; }
    unsigned long Release() override { callout(); return (unsigned long)--refs; }
    HR GetTime(dsh::LL *p) override { callout(); *p = t += 10; return 0; }
    HR AdviseTime(dsh::LL, dsh::LL, uintptr_t, uintptr_t *) override { callout(); return kNotImpl; }
    HR AdvisePeriodic(dsh::LL, dsh::LL, uintptr_t, uintptr_t *) override { callout(); return kNotImpl; }
    HR Unadvise(uintptr_t) override { callout(); return kNotImpl; }
};

/* the game's enumerator (IEnumPins, IEnumMediaTypes): items are AddRef'd pins or CoTaskMem'd AM_MEDIA_TYPEs */
struct GEnum final : dsh::IEnum_ {
    int refs = 1;
    std::vector<void *> items;
    bool mts;
    size_t at = 0;
    explicit GEnum(bool m) : mts(m) {}
    HR QueryInterface(const void *iid, void **out) override
    {
        callout();
        *out = nullptr;
        if (dsh::geq(iid, dsh::IID_IUnknown_) || dsh::geq(iid, dsh::IID_IEnumPins_) || dsh::geq(iid, dsh::IID_IEnumMediaTypes_)) { *out = this; refs++; return 0; }
        return E_NOINTERFACE_;
    }
    unsigned long AddRef() override { callout(); return (unsigned long)++refs; }
    unsigned long Release() override
    {
        callout();
        int r = --refs;
        if (!r) delete this;
        return (unsigned long)r;
    }
    HR Next(uint32_t n, void **out, uint32_t *fetched) override
    {
        callout();
        uint32_t k = 0;
        while (k < n && at < items.size()) {
            if (mts) {
                dsh::MT *m = (dsh::MT *)CoTaskMemAlloc(sizeof(dsh::MT));
                memcpy(m, items[at], sizeof *m);
                out[k] = m;
            } else {
                dsh::Unk *u = (dsh::Unk *)items[at];
                u->AddRef();
                out[k] = u;
            }
            at++;
            k++;
        }
        if (fetched) *fetched = k;
        return k == n ? 0 : 1;
    }
    HR Skip(uint32_t n) override { callout(); at += n; return 0; }
    HR Reset() override { callout(); at = 0; return 0; }
    HR Clone(void **) override { callout(); return kNotImpl; }
};

/* the game's IAsyncReader over a WAVE file in memory */
std::vector<uint8_t> g_wav;
struct GReader : dsh::AsyncReader {
    GPin *pin;
    explicit GReader(GPin *p) : pin(p) {}
    HR QueryInterface(const void *iid, void **out) override;
    unsigned long AddRef() override;
    unsigned long Release() override;
    HR RequestAllocator(void *, void *, void **) override { callout(); return kNotImpl; }
    HR Request(void *, uintptr_t) override { callout(); return kNotImpl; }
    HR WaitForNext(uint32_t, void **, uintptr_t *) override { callout(); return kNotImpl; }
    HR SyncReadAligned(void *) override { callout(); return kNotImpl; }
    HR SyncRead(dsh::LL pos, long len, uint8_t *buf) override
    {
        callout();
        if (pos < 0 || pos + len > (dsh::LL)g_wav.size()) return E_FAIL_;
        memcpy(buf, g_wav.data() + pos, (size_t)len);
        return 0;
    }
    HR Length(dsh::LL *t, dsh::LL *a) override { callout(); *t = *a = (dsh::LL)g_wav.size(); return 0; }
    HR BeginFlush() override { callout(); return 0; }
    HR EndFlush() override { callout(); return 0; }
};

dsh::MT wave_type()
{
    dsh::MT m;
    memset(&m, 0, sizeof m);
    m.major = dsh::MEDIATYPE_Stream_;
    m.sub = dsh::MEDIASUBTYPE_WAVE_;
    m.format = dsh::FORMAT_None_;
    return m;
}

struct GPin final : dsh::IPin_ {
    int refs = 1;
    GFilt *owner;
    int dir;
    const char *nm;
    dsh::IPin_ *peer = nullptr;
    dsh::MT cur;
    bool reader_on;
    GReader reader;
    GPin(GFilt *o, int d, const char *n, bool rd) : owner(o), dir(d), nm(n), reader_on(rd), reader(this) { memset(&cur, 0, sizeof cur); }
    HR QueryInterface(const void *iid, void **out) override
    {
        callout();
        *out = nullptr;
        if (dsh::geq(iid, dsh::IID_IUnknown_) || dsh::geq(iid, dsh::IID_IPin_)) { *out = static_cast<dsh::IPin_ *>(this); refs++; return 0; }
        if (reader_on && dsh::geq(iid, dsh::IID_IAsyncReader_)) { *out = static_cast<dsh::AsyncReader *>(&reader); refs++; return 0; }
        return E_NOINTERFACE_;
    }
    unsigned long AddRef() override { callout(); return (unsigned long)++refs; }
    unsigned long Release() override { callout(); return (unsigned long)--refs; }
    HR Connect(dsh::IPin_ *receive, const dsh::MT *mt) override
    {
        callout();
        if (!receive) return (HR)0x80004003;
        if (peer) return dsh::VFW_E_ALREADY_CONNECTED_;
        dsh::MT t = mt ? *mt : wave_type();
        HR hr = pin_receive(receive, this, &t);
        if (hr >= 0) { peer = receive; u_addref(receive); cur = t; }
        return hr;
    }
    HR ReceiveConnection(dsh::IPin_ *c, const dsh::MT *mt) override
    {
        callout();
        if (peer) return dsh::VFW_E_ALREADY_CONNECTED_;
        peer = c;
        u_addref(c);
        cur = *mt;
        return 0;
    }
    HR Disconnect() override
    {
        callout();
        if (!peer) return 1;
        dsh::IPin_ *p = peer;
        peer = nullptr;
        u_release(p);
        return 0;
    }
    HR ConnectedTo(dsh::IPin_ **p) override
    {
        callout();
        *p = peer;
        if (!peer) return dsh::VFW_E_NOT_CONNECTED_;
        u_addref(peer);
        return 0;
    }
    HR ConnectionMediaType(dsh::MT *m) override
    {
        callout();
        if (!peer) { memset(m, 0, sizeof *m); return dsh::VFW_E_NOT_CONNECTED_; }
        *m = cur;
        return 0;
    }
    HR QueryPinInfo(dsh::PinInfo *pi) override;
    HR QueryDirection(int *d) override { callout(); *d = dir; return 0; }
    HR QueryId(dsh::WCH **id) override
    {
        callout();
        *id = dsh::wdup_co((const dsh::WCH *)u"pin");
        return 0;
    }
    HR QueryAccept(const dsh::MT *) override { callout(); return 0; }
    HR EnumMediaTypes(void **e) override
    {
        callout();
        GEnum *en = new GEnum(true);
        static dsh::MT t;
        t = wave_type();
        en->items.push_back(&t);
        *e = en;
        return 0;
    }
    HR QueryInternalConnections(dsh::IPin_ **, uint32_t *) override { callout(); return kNotImpl; }
    HR EndOfStream() override { callout(); return 0; }
    HR BeginFlush() override { callout(); return 0; }
    HR EndFlush() override { callout(); return 0; }
    HR NewSegment(dsh::LL, dsh::LL, double) override { callout(); return 0; }
};

HR GReader::QueryInterface(const void *iid, void **out) { return pin->QueryInterface(iid, out); }
unsigned long GReader::AddRef() { return pin->AddRef(); }
unsigned long GReader::Release() { return pin->Release(); }

struct GFilt : dsh::IBaseFilter_ {
    int refs = 1;
    int state = 0;
    uintptr_t graph_if = 0;
    dsh::WCH name[64] = {};
    int joins = 0, leaves = 0;
    std::vector<GPin *> pins;
    GClock *clock = nullptr;   /* exposed through IReferenceClock when set */
    dsh::IReferenceClock_ *sync = nullptr;
    ~GFilt() { for (GPin *p : pins) delete p; }
    GPin *add_pin(int dir, const char *n, bool rd = false)
    {
        pins.push_back(new GPin(this, dir, n, rd));
        return pins.back();
    }
    HR QueryInterface(const void *iid, void **out) override
    {
        callout();
        *out = nullptr;
        if (dsh::geq(iid, dsh::IID_IUnknown_) || dsh::geq(iid, dsh::IID_IBaseFilter_) || dsh::geq(iid, dsh::IID_IMediaFilter_) || dsh::geq(iid, dsh::IID_IPersist_)) {
            *out = static_cast<dsh::IBaseFilter_ *>(this);
            refs++;
            return 0;
        }
        if (clock && dsh::geq(iid, dsh::IID_IReferenceClock_)) { *out = static_cast<dsh::IReferenceClock_ *>(clock); clock->refs++; return 0; }
        return E_NOINTERFACE_;
    }
    unsigned long AddRef() override { callout(); return (unsigned long)++refs; }
    unsigned long Release() override { callout(); return (unsigned long)--refs; }
    HR GetClassID(Guid *) override { callout(); return kNotImpl; }
    HR Stop() override { callout(); state = dsh::State_Stopped; return 0; }
    HR Pause() override { callout(); state = dsh::State_Paused; return 0; }
    HR Run(dsh::LL) override { callout(); state = dsh::State_Running; return 0; }
    HR GetState(uint32_t, int *s) override { callout(); *s = state; return 0; }
    HR SetSyncSource(dsh::IReferenceClock_ *c) override { callout(); sync = c; return 0; }
    HR GetSyncSource(dsh::IReferenceClock_ **c) override { callout(); *c = sync; return 0; }
    HR EnumPins(void **e) override
    {
        callout();
        GEnum *en = new GEnum(false);
        for (GPin *p : pins) en->items.push_back(static_cast<dsh::IPin_ *>(p));
        *e = en;
        return 0;
    }
    HR FindPin(const dsh::WCH *, dsh::IPin_ **p) override { callout(); *p = nullptr; return VFW_E_NOT_FOUND_; }
    HR QueryFilterInfo(dsh::FilterInfo *fi) override
    {
        callout();
        memset(fi, 0, sizeof *fi);
        memcpy(fi->name, name, sizeof name);
        fi->graph = (void *)graph_if;
        if (graph_if) gc(graph_if, 1);   /* the reference of the out parameter */
        return 0;
    }
    HR JoinFilterGraph(dsh::IFilterGraph_ *g, const dsh::WCH *n) override
    {
        callout();
        if (g) joins++; else leaves++;
        graph_if = (uintptr_t)g;
        if (n) { size_t i = 0; for (; n[i] && i < 63; i++) name[i] = n[i]; name[i] = 0; }
        return 0;
    }
    HR QueryVendorInfo(dsh::WCH **) override { callout(); return kNotImpl; }
};

HR GPin::QueryPinInfo(dsh::PinInfo *pi)
{
    callout();
    memset(pi, 0, sizeof *pi);
    pi->filter = static_cast<dsh::IBaseFilter_ *>(owner);
    owner->AddRef();
    pi->dir = dir;
    for (size_t i = 0; nm[i] && i < 127; i++) pi->name[i] = (uint8_t)nm[i];
    return 0;
}

std::vector<uint8_t> make_wav(unsigned frames)
{
    std::vector<uint8_t> v(44 + frames * 4, 0);
    auto w32 = [&](size_t at, uint32_t x) { memcpy(&v[at], &x, 4); };
    auto w16 = [&](size_t at, uint16_t x) { memcpy(&v[at], &x, 2); };
    memcpy(&v[0], "RIFF", 4);
    w32(4, (uint32_t)(36 + frames * 4));
    memcpy(&v[8], "WAVEfmt ", 8);
    w32(16, 16);
    w16(20, 1);
    w16(22, 2);
    w32(24, 44100);
    w32(28, 44100 * 4);
    w16(32, 4);
    w16(34, 16);
    memcpy(&v[36], "data", 4);
    w32(40, frames * 4);
    for (unsigned i = 0; i < frames; i++) {
        int16_t s = (int16_t)((i / 50) % 2 ? 8000 : -8000);
        memcpy(&v[44 + i * 4], &s, 2);
        memcpy(&v[46 + i * 4], &s, 2);
    }
    return v;
}

/* ---- a graph (the platform's object; the face is what the game holds) and the game's objects */
struct World {
    W graph = 0, control = 0, mfilter = 0, site = 0;
    void *nat = nullptr;                       /* the graph's native interface pointer (events carry a pointer-wide value: not through a 32-bit key call) */
    dsh::IMediaEventSink_ *nsink = nullptr;
    dsh::IMediaEventEx_ *nevent = nullptr;
    GFilt f1, f2, fx;
    GClock clk;
    GPin *o1, *o2, *i1;
    World()
    {
        o1 = f1.add_pin(dsh::PINDIR_OUTPUT, "Out1");
        o2 = f1.add_pin(dsh::PINDIR_OUTPUT, "Out2", true);
        i1 = f2.add_pin(dsh::PINDIR_INPUT, "In1");
        f1.clock = &clk;
        clk.refs = 1;
    }
    bool open()
    {
        void *obj = nullptr;
        if (fnvnx_dshow_com_factory((const GUID *)&q_graphbuilder, &obj) != 0) return false;
        graph = (W)nxcom::to_game(obj, ifx("IGraphBuilder"));
        gc(graph, 0, {(W)&q_control, (W)&control});
        gc(graph, 0, {(W)&q_mfilter, (W)&mfilter});
        gc(graph, 0, {(W)&q_site, (W)&site});
        nat = obj;
        ((dsh::Unk *)obj)->QueryInterface(&q_sink, (void **)&nsink);
        ((dsh::Unk *)obj)->QueryInterface(&q_event, (void **)&nevent);
        return control && mfilter && nevent && nsink && site;
    }
    void close()
    {
        if (nsink) nsink->Release();
        if (nevent) nevent->Release();
        nsink = nullptr;
        nevent = nullptr;
        for (W *p : {&site, &mfilter, &control, &graph})
            if (*p) { gc(*p, 2); *p = 0; }
    }
    dsh::IBaseFilter_ *F(GFilt &f) { return static_cast<dsh::IBaseFilter_ *>(&f); }
    HR add(GFilt &f, const uint16_t *n) { return (HR)gc(graph, 3, {(W)F(f), (W)n}); }
    HR remove(GFilt &f) { return (HR)gc(graph, 4, {(W)F(f)}); }
    /* EnumFilters + Next until exhausted: the pointers (each with its reference) are released at once; returns the count and
       whether `want` was among them */
    int enum_filters(const dsh::IBaseFilter_ *want, bool *seen)
    {
        W en = 0;
        if (gc(graph, 5, {(W)&en}) != 0 || !en) return -1;
        int n = 0;
        if (seen) *seen = false;
        for (;;) {
            W p = 0;
            uint32_t got = 0;
            gc(en, 3, {1, (W)&p, (W)&got});
            if (got != 1) break;
            n++;
            if (seen && (const void *)p == (const void *)want) *seen = true;
            gc(p, 2);
        }
        gc(en, 2);
        return n;
    }
};

/* the re-entrant action: the game calls back into the graph from inside its own method */
struct Reentry { int ok = 0, bad = 0; };
void install_reentry(World &w, Reentry &r)
{
    g_sc.reenter = [&w, &r] {
        bool seen = false;
        int before = w.enum_filters(&*w.F(w.f1), &seen);
        HR a = w.add(w.fx, kNameX);   /* AddFilter from inside a filter method */
        bool seen2 = false;
        int mid = w.enum_filters(nullptr, &seen2);
        W found = 0;
        HR f = (HR)gc(w.graph, 6, {(W)kNameX, (W)&found});   /* FindFilterByName */
        if (found) gc(found, 2);
        uint32_t st = 99;
        HR s = (HR)gc(w.control, 10, {0, (W)&st});            /* the state query */
        HR rm = w.remove(w.fx);                                /* RemoveFilter from inside */
        int after = w.enum_filters(nullptr, nullptr);
        /* a consistent graph: the new filter was visible exactly while it was in, the others did not move */
        bool good = (a >= 0) && mid == before + 1 && f == 0 && found && rm == 0 && after == before && s >= 0;
        (void)st;
        if (good) r.ok++; else r.bad++;
    };
}

/* ---- the scenario: every graph operation, with game objects as filters, pins, clock, site */
struct Result { int steps = 0, skipped = 0; };
template <class F> bool guarded(F f)
{
    if (g_sc.mode == 2) {
        jmp_buf jb;
        g_sc.jb = &jb;
        if (setjmp(jb)) {
            g_sc.jb = nullptr;
            g_sc.depth = 0;
            return false;
        }
        f();
        g_sc.jb = nullptr;
        return true;
    }
    try {
        f();
        return true;
    } catch (const GameThrow &) {
        g_sc.depth = 0;
        return false;
    }
}
void scenario(World &w, Result &r, bool strict)
{
    auto step = [&](const std::function<void()> &f) {
        r.steps++;
        if (!guarded(f)) r.skipped++;
    };
    auto ok = [&](bool c) { if (strict) CHECK(c); };
    step([&] { ok(w.add(w.f1, kName1) >= 0); });
    step([&] { ok(w.add(w.f2, kName2) >= 0); });
    step([&] { ok(w.add(w.f1, kName1) == E_FAIL_); });                    /* already in this graph */
    step([&] { ok(w.enum_filters(nullptr, nullptr) >= 0); });             /* EnumFilters */
    step([&] {
        W f = 0;
        ok(gc(w.graph, 6, {(W)kName1, (W)&f}) == 0 && f == (W)w.F(w.f1));   /* FindFilterByName: the game's own pointer back */
        if (f) gc(f, 2);
    });
    /* pin enumeration and queries as the graph does them, driven through the face of the graph: ConnectDirect needs the pins'
       filters to be in the graph (QueryPinInfo), QueryDirection, then Connect on the game's pin */
    step([&] { ok(gc(w.graph, 7, {(W)static_cast<dsh::IPin_ *>(w.o1), (W)static_cast<dsh::IPin_ *>(w.i1), 0}) == 0); });
    step([&] { ok(gc(w.graph, 8, {(W)static_cast<dsh::IPin_ *>(w.o1)}) == 0); });   /* Reconnect */
    step([&] { ok(gc(w.graph, 9, {(W)static_cast<dsh::IPin_ *>(w.o1)}) == 0); });   /* Disconnect (QueryPinInfo, QueryFilterInfo for the log) */
    step([&] { ok((HR)gc(w.graph, 11, {(W)static_cast<dsh::IPin_ *>(w.o1), (W)static_cast<dsh::IPin_ *>(w.i1)}) >= 0); });   /* Connect */
    step([&] { gc(w.graph, 9, {(W)static_cast<dsh::IPin_ *>(w.o1)}); });
    /* sync source: the game's clock set by the game, then the default one (filter f1 exposes its clock) */
    step([&] { ok(gc(w.mfilter, 8, {(W)static_cast<dsh::IReferenceClock_ *>(&w.clk)}) == 0); });
    step([&] {
        W c = 0;
        ok(gc(w.mfilter, 9, {(W)&c}) == 0);
        if (c) gc(c, 2);
    });
    step([&] { ok(gc(w.graph, 10) == 0); });   /* SetDefaultSyncSource */
    /* IObjectWithSite with a game object */
    step([&] { ok(gc(w.site, 3, {(W)w.F(w.f2)}) == 0); });
    step([&] {
        RawG ibf = {0x56a86895, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
        W s = 0;
        ok(gc(w.site, 4, {(W)&ibf, (W)&s}) == 0 && s == (W)w.F(w.f2));
        if (s) u_release((dsh::Unk *)s);
    });
    step([&] { ok(gc(w.site, 3, {0}) == 0); });
    /* events: EC_DEVICE_LOST carries the game's IUnknown, the queue owns a reference of it until FreeEventParams */
    step([&] {
        ok(w.nsink->Notify(0x1F, (intptr_t)w.F(w.f2), 0) == 0);
        long code = 0;
        intptr_t p1 = 0, p2 = 0;
        ok(w.nevent->GetEvent(&code, &p1, &p2, 0) == 0 && code == 0x1F && p1 == (intptr_t)w.F(w.f2));
        ok(w.nevent->FreeEventParams(code, p1, p2) == 0);
    });
    /* Render of the game's source pin: the graph builds the installed filters behind it (parser, renderer), the pump starts */
    step([&] { ok((HR)gc(w.graph, 12, {(W)static_cast<dsh::IPin_ *>(w.o2)}) >= 0); });
    /* state changes: Pause / Run / GetState / Stop through both control faces */
    step([&] { ok(gc(w.control, 8) == 0); });
    step([&] { ok(gc(w.control, 7) == 0); });
    step([&] {
        uint32_t st = 99;
        gc(w.control, 10, {0, (W)&st});
        ok(st == 2);
    });
    step([&] { ok(gc(w.control, 9) == 0); });
    step([&] { ok(gc(w.mfilter, 5) == 0); });
    step([&] { ok(gc(w.mfilter, 6, {0, 0}) == 0); });                       /* Run(0) */
    step([&] { int st = 99; gc(w.mfilter, 7, {100, (W)&st}); ok(st == 2); });
    step([&] { ok(gc(w.mfilter, 4) == 0); });
    /* RemoveFilter of the game's filters (the first one carries the graph's clock and the connections) */
    step([&] { ok(w.remove(w.f2) == 0); });
    step([&] { ok(w.remove(w.f1) == 0); });
    step([&] { ok(w.remove(w.f1) == VFW_E_NOT_FOUND_); });
}

/* the graph must be usable after anything: from this thread and from another, without a lock left behind, and a new filter must
   go in and out */
bool usable(World &w)
{
    if (fnvnx_bnd_locks_held() != 0) return false;
    g_sc.fire_at = 0;
    g_sc.reenter = nullptr;
    auto ok = std::make_shared<std::promise<bool>>();
    std::future<bool> fut = ok->get_future();
    test_thread([&w, ok] {
        GFilt extra;
        const uint16_t nm[] = {'U', 0};
        bool good = w.enum_filters(nullptr, nullptr) >= 0;
        uint32_t st = 0;
        good = good && (HR)gc(w.control, 10, {0, (W)&st}) >= 0;
        good = good && w.add(extra, nm) >= 0;
        good = good && w.remove(extra) == 0;
        ok->set_value(good);
    }).detach();
    if (fut.wait_for(std::chrono::seconds(20)) != std::future_status::ready) return false;   /* a lock was left held */
    return fut.get() && fnvnx_bnd_locks_held() == 0;
}

void reset_script()
{
    g_sc.depth = g_sc.calls = g_sc.fire_at = g_sc.mode = g_sc.reentries = 0;
    g_sc.fired = false;
    g_sc.jb = nullptr;
    g_sc.reenter = nullptr;
}

}   // namespace

TEST(dsl_game_filter_through_every_operation)
{
    mixer_init(48000, 256);
    alarm(120);   /* a graph lock held across a re-entrant call deadlocks: end the run instead of hanging */
    g_wav = make_wav(22050);
    int v0 = g_bnd_lock_violations;
    reset_script();
    World w;
    CHECK(w.open());
    Result r;
    scenario(w, r, true);
    CHECK_EQ(r.skipped, 0);
    CHECK_EQ(g_sc.calls > 50, true);   /* the game's objects were called, a lot */
    CHECK_EQ(g_bnd_lock_violations, v0);
    CHECK(usable(w));
    /* every reference the platform took of the game's objects went back */
    CHECK_EQ(w.f1.refs, 1);
    CHECK_EQ(w.f2.refs, 1);
    CHECK_EQ(w.clk.refs, 1);
    CHECK_EQ(w.f1.joins, 1);
    CHECK_EQ(w.f1.leaves, 1);
    w.close();
    CHECK_EQ(w.f1.refs, 1);
    CHECK_EQ(g_bnd_lock_violations, v0);
    alarm(0);
}

TEST(dsl_reentrant_from_every_callout)
{
    mixer_init(48000, 256);
    alarm(240);
    g_wav = make_wav(22050);
    int v0 = g_bnd_lock_violations;
    reset_script();
    World w;
    CHECK(w.open());
    Reentry re;
    install_reentry(w, re);   /* EVERY method of every game object re-enters the graph (once deep) */
    Result r;
    scenario(w, r, true);
    CHECK_EQ(r.skipped, 0);
    CHECK(g_sc.reentries > 100);
    CHECK(re.ok > 100);
    CHECK_EQ(re.bad, 0);      /* the graph a callback sees is consistent, at every callout of every operation */
    CHECK_EQ(g_bnd_lock_violations, v0);
    reset_script();
    CHECK(usable(w));
    CHECK_EQ(w.f1.refs, 1);
    CHECK_EQ(w.f2.refs, 1);
    CHECK_EQ(w.fx.refs, 1);
    w.close();
    CHECK_EQ(g_bnd_lock_violations, v0);
    alarm(0);
}

TEST(dsl_other_thread_during_callout)
{
    mixer_init(48000, 256);
    alarm(240);
    g_wav = make_wav(22050);
    int v0 = g_bnd_lock_violations;
    reset_script();
    World w;
    CHECK(w.open());
    /* while the game's method runs, ANOTHER thread uses the graph (it would block on a lock held across the call) */
    int tried = 0, done = 0;
    g_sc.reenter = [&] {
        auto ok = std::make_shared<std::promise<bool>>();
        std::future<bool> fut = ok->get_future();
        tried++;
        test_thread([&w, ok] {
            GFilt extra;
            const uint16_t nm[] = {'T', 0};
            uint32_t st = 0;
            bool good = w.enum_filters(nullptr, nullptr) >= 0 && (HR)gc(w.control, 10, {0, (W)&st}) >= 0;
            good = good && w.add(extra, nm) >= 0 && w.remove(extra) == 0;
            ok->set_value(good);
        }).detach();
        if (fut.wait_for(std::chrono::seconds(20)) == std::future_status::ready && fut.get()) done++;
    };
    Result r;
    scenario(w, r, true);
    CHECK_EQ(r.skipped, 0);
    CHECK(tried > 100);
    CHECK_EQ(done, tried);
    CHECK_EQ(g_bnd_lock_violations, v0);
    reset_script();
    w.close();
    alarm(0);
}

/* a callout that does not return, at every position of the scenario, in both ways: the object throws and the test catches it
   outside the platform call (mode 1), or the test jumps out of it, discarding the platform frames without a destructor (mode 2) */
TEST(dsl_noreturn_every_callout)
{
    mixer_init(48000, 256);
    alarm(900);
    g_wav = make_wav(22050);
    int v0 = g_bnd_lock_violations;
    int total = 0;
    {   /* the dry run counts the callouts */
        reset_script();
        World w;
        CHECK(w.open());
        Result r;
        scenario(w, r, false);
        total = g_sc.calls;
        w.close();
    }
    CHECK(total > 100);
    int fired = 0, unusable = 0;
    for (int mode = 1; mode <= 2; mode++) {
        for (int n = 1; n <= total; n++) {
            reset_script();
            g_sc.mode = mode;
            g_sc.fire_at = n;
            World w;
            if (!w.open()) { CHECK(false); break; }
            Result r;
            scenario(w, r, false);
            fired += g_sc.fired;
            bool u = usable(w);
            unusable += !u;
            if (!u) fprintf(stderr, "  graph unusable after callout %d of %d (mode %d)\n", n, total, mode);
            if (mode == 1) w.close();   /* (a longjmp leaves refs of the abandoned frames: the graph is dropped without a check) */
            else {
                reset_script();
                w.close();
            }
            if (g_bnd_lock_violations != v0) {
                fprintf(stderr, "  lock held across a game call: callout %d of %d (mode %d)\n", n, total, mode);
                v0 = g_bnd_lock_violations;
                CHECK(false);
            }
        }
    }
    CHECK(fired > 200);
    CHECK_EQ(unusable, 0);
    CHECK_EQ(g_bnd_lock_violations, 0);
    alarm(0);
}
