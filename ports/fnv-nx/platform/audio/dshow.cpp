/* dshow.cpp - DirectShow's FilterGraph (CLSID_FilterGraph) and FilterMapper (CLSID_FilterMapper) for the game's music.
 *
 * The exe plays Data/Music (MP3) with its own source filter, an async reader over its archive files,
 * and a graph: CoCreateInstance(CLSID_FilterGraph, IID_IFilterGraph), AddFilter(source),
 * QueryInterface(IID_IGraphBuilder), Render(source output pin), then IBasicAudio, IMediaControl, IMediaPosition and
 * IMediaEvent from the graph; it also asks CLSID_FilterMapper for IFilterMapper to list installed filters.
 *
 * The graph is a set of filters with pins and connections, as the SDK describes. The installed filters are exactly
 * the MPEG-1 audio splitter, the MP3 decoder, the WAVE parser and the DirectSound renderer (dshow_filters.cpp:
 * real IBaseFilter / IPin objects, visible in EnumFilters). Connect / Render is the graph's intelligent connect over
 * the registry (the mapper's registration set): direct, else through filters whose input accepts the type, built
 * recursively down to a renderer; a failed branch is undone. A stream of any other type cannot be rendered.
 *
 * The control interfaces (IMediaControl, IMediaFilter, IMediaSeeking, IMediaPosition, IMediaEvent(Ex),
 * IMediaEventSink, IBasicAudio) are sub-objects sharing the graph's identity and count; the video interfaces report
 * E_NOINTERFACE for every method, as a graph with no video renderer does.
 *
 * Lock order (outer to inner): Graph::lock (GraphCore::lock) > mixer lock (g_lock in mixer.cpp). The graph lock
 * guards the filter list, the state, the clock and site slots, the event queue and the renderers' streams.
 *
 * THE RULE (whole class, structural): an interface method of an object that is not provably the platform's own concrete
 * type -- a filter, pin, enumerator, clock or site a GAME may have implemented (then the pointer is a combridge proxy and
 * every method is a call into the game) -- is called with NO platform lock held, AddRef and Release included. A call into
 * the game may not return and the game may re-enter the graph from inside it. Every graph operation is therefore
 *     snapshot under the lock  ->  release it  ->  call out  ->  (re-acquire, validate, commit).
 * The references the graph holds (a filter added, the clock, the site) are taken OUTSIDE the lock when they are acquired
 * and are long-lived (Hold). A snapshot copies raw pointers; what keeps them alive is the Hold's in-use count (taken under
 * the lock, a plain integer -- no call), not an AddRef: removing a Hold from its slot only RETIRES it, and the reference
 * is released, outside the lock, by whoever ends the last use. Nothing holds a lock or a half-updated table across a call:
 * a call that never returns leaves, at worst, one in-use count that keeps one removed filter's reference unreleased.
 * The test boundary checks it (os_lock counts per thread in the host backend, tests/s4/host_boundary.cpp). The renderer's methods and
 * decoders (as_renderer, as_pcm: the platform's own classes, known by the kind the graph gave them) run under the lock;
 * they call the mixer only. */
#define DSH_IMPL
#include "dshow_int.h"
#include "../com/combridge.h"

#include <math.h>
#include <new>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

extern "C" {
HANDLE CreateEventA(void *, BOOL, BOOL, LPCSTR);
BOOL SetEvent(HANDLE);
BOOL ResetEvent(HANDLE);
BOOL CloseHandle(HANDLE);
}

namespace dsh {
namespace {

typedef GraphLock GL;   /* the graph lock (dshow_int.h) */

/* ---- the registry: what the mapper registers and the graph's intelligent connect searches */
struct RegType { Guid maj, sub; };
struct RegPin { WCH name[32]; bool rendered, output; Vec<RegType> types; };
struct RegEntry { Guid clsid; WCH name[64]; uint32_t merit; Vec<RegPin *> pins; bool instance; Guid mrid; ~RegEntry() { for (size_t i = 0; i < pins.n; i++) delete pins[i]; } };
os_lock_t g_reg_lock = {0};
Vec<RegEntry *> g_reg;
bool g_reg_seeded = false;

void reg_seed_locked()
{
    if (g_reg_seeded) return;
    g_reg_seeded = true;
    for (const ClassDesc &c : g_classes) {
        RegEntry *e = new RegEntry();
        e->clsid = *c.clsid;
        wfrom_ascii(e->name, 64, c.name);
        e->merit = c.merit;
        e->instance = false;
        memset(&e->mrid, 0, sizeof e->mrid);
        for (const PinDesc &p : c.pins) {
            if (!p.name) continue;
            RegPin *rp = new RegPin();
            wfrom_ascii(rp->name, 32, p.name);
            rp->rendered = p.rendered;
            rp->output = p.output;
            rp->types.push(RegType{p.type[0], p.type[1]});
            e->pins.push(rp);
        }
        g_reg.push(e);
    }
}
RegEntry *reg_find_locked(const Guid &clsid, bool instance_only = false, const Guid *mrid = nullptr)
{
    for (size_t i = 0; i < g_reg.n; i++) {
        RegEntry *e = g_reg[i];
        if (mrid) { if (e->instance && geq(e->mrid, *mrid)) return e; }
        else if (!e->instance && !instance_only && geq(e->clsid, clsid)) return e;
    }
    return nullptr;
}
bool tmatch(const Guid &a, const Guid &b) { return geq(a, GUID_NULL_) || geq(b, GUID_NULL_) || geq(a, b); }

struct Mapper final : IFilterMapper_ {
    int refs = 1;
    HR QueryInterface(const void *iid, void **o) override
    {
        if (!o) return E_POINTER_;
        *o = nullptr;
        if (!geq(iid, IID_IUnknown_) && !geq(iid, IID_IFilterMapper_)) return E_NOINTERFACE_;
        *o = this;
        AddRef();
        return S_OK_;
    }
    unsigned long AddRef() override { return __atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    unsigned long Release() override
    {
        int r = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (!r) delete this;
        return (unsigned long)r;
    }
    HR RegisterFilter(Guid clsid, const WCH *name, uint32_t merit) override
    {
        if (!name) return E_POINTER_;
        os_lock(&g_reg_lock);
        reg_seed_locked();
        RegEntry *e = reg_find_locked(clsid);
        if (!e) {
            e = new RegEntry();
            e->clsid = clsid;
            e->instance = false;
            memset(&e->mrid, 0, sizeof e->mrid);
            g_reg.push(e);
        }
        wcopy(e->name, 64, name);
        e->merit = merit;
        os_unlock(&g_reg_lock);
        return S_OK_;
    }
    HR RegisterFilterInstance(Guid clsid, const WCH *name, Guid *mrid) override
    {
        if (!name || !mrid) return E_POINTER_;
        os_lock(&g_reg_lock);
        reg_seed_locked();
        RegEntry *base = reg_find_locked(clsid);
        RegEntry *e = new RegEntry();
        e->clsid = clsid;
        e->instance = true;
        e->merit = base ? base->merit : MERIT_DO_NOT_USE_;   /* MERIT_DO_NOT_USE: an instance of a filter nobody registered */
        wcopy(e->name, 64, name);
        for (int i = 0; i < 4; i++) { uint32_t r = os_random32(); memcpy((uint8_t *)&e->mrid + 4 * i, &r, 4); }
        *mrid = e->mrid;
        if (base)
            for (size_t i = 0; i < base->pins.n; i++) {   /* an instance has the pins of its filter */
                RegPin *p = new RegPin();
                memcpy(p->name, base->pins[i]->name, sizeof p->name);
                p->rendered = base->pins[i]->rendered;
                p->output = base->pins[i]->output;
                for (size_t k = 0; k < base->pins[i]->types.n; k++) p->types.push(base->pins[i]->types[k]);
                e->pins.push(p);
            }
        g_reg.push(e);
        os_unlock(&g_reg_lock);
        return S_OK_;
    }
    HR RegisterPin(Guid filter, const WCH *name, int rendered, int output, int, int, Guid, const WCH *) override
    {
        if (!name) return E_POINTER_;
        os_lock(&g_reg_lock);
        reg_seed_locked();
        RegEntry *e = reg_find_locked(filter);
        HR hr = VFW_E_NOT_FOUND_;
        if (e) {
            RegPin *p = nullptr;
            for (size_t i = 0; i < e->pins.n; i++)
                if (weq_nocase(e->pins[i]->name, name)) p = e->pins[i];
            if (!p) {
                p = new RegPin();
                e->pins.push(p);
            }
            wcopy(p->name, 32, name);
            p->rendered = rendered != 0;
            p->output = output != 0;
            hr = S_OK_;
        }
        os_unlock(&g_reg_lock);
        return hr;
    }
    HR RegisterPinType(Guid filter, const WCH *pin, Guid major, Guid sub) override
    {
        if (!pin) return E_POINTER_;
        os_lock(&g_reg_lock);
        reg_seed_locked();
        RegEntry *e = reg_find_locked(filter);
        HR hr = VFW_E_NOT_FOUND_;
        if (e)
            for (size_t i = 0; i < e->pins.n; i++)
                if (weq_nocase(e->pins[i]->name, pin)) {
                    e->pins[i]->types.push(RegType{major, sub});
                    hr = S_OK_;
                }
        os_unlock(&g_reg_lock);
        return hr;
    }
    HR UnregisterFilter(Guid clsid) override
    {
        os_lock(&g_reg_lock);
        reg_seed_locked();
        HR hr = VFW_E_NOT_FOUND_;
        for (size_t i = 0; i < g_reg.n;)
            if (!g_reg[i]->instance && geq(g_reg[i]->clsid, clsid)) { delete g_reg[i]; g_reg.erase(i); hr = S_OK_; }
            else i++;
        os_unlock(&g_reg_lock);
        return hr;
    }
    HR UnregisterFilterInstance(Guid mrid) override
    {
        os_lock(&g_reg_lock);
        HR hr = VFW_E_NOT_FOUND_;
        for (size_t i = 0; i < g_reg.n; i++)
            if (g_reg[i]->instance && geq(g_reg[i]->mrid, mrid)) { delete g_reg[i]; g_reg.erase(i); hr = S_OK_; break; }
        os_unlock(&g_reg_lock);
        return hr;
    }
    HR UnregisterPin(Guid filter, const WCH *name) override
    {
        if (!name) return E_POINTER_;
        os_lock(&g_reg_lock);
        reg_seed_locked();
        RegEntry *e = reg_find_locked(filter);
        HR hr = VFW_E_NOT_FOUND_;
        if (e)
            for (size_t i = 0; i < e->pins.n; i++)
                if (weq_nocase(e->pins[i]->name, name)) { delete e->pins[i]; e->pins.erase(i); hr = S_OK_; break; }
        os_unlock(&g_reg_lock);
        return hr;
    }
    /* SDK: merit is a minimum; the input pin (type, wildcards with GUID_NULL) when input_needed; a pin that renders
     * when render; an output pin of the given type when output_needed */
    HR EnumMatchingFilters(void **out, uint32_t merit, int in_needed, Guid imaj, Guid isub, int render, int out_needed, Guid omaj,
                           Guid osub) override
    {
        if (!out) return E_POINTER_;
        Vec<void *> items;
        Vec<RegFilter *> mem;
        os_lock(&g_reg_lock);
        reg_seed_locked();
        for (size_t i = 0; i < g_reg.n; i++) {
            RegEntry *e = g_reg[i];
            if (e->merit < merit) continue;
            bool in_ok = !in_needed && !render, out_ok = !out_needed;
            for (size_t p = 0; p < e->pins.n; p++) {
                RegPin *rp = e->pins[p];
                if (!rp->output && (in_needed || render) && (!render || rp->rendered)) {
                    if (!in_needed) in_ok = true;
                    for (size_t t = 0; t < rp->types.n; t++)
                        if (tmatch(imaj, rp->types[t].maj) && tmatch(isub, rp->types[t].sub)) in_ok = true;
                }
                if (rp->output && out_needed)
                    for (size_t t = 0; t < rp->types.n; t++)
                        if (tmatch(omaj, rp->types[t].maj) && tmatch(osub, rp->types[t].sub)) out_ok = true;
            }
            if (in_ok && out_ok) {
                RegFilter *rf = new RegFilter{e->clsid, e->name};   /* the enumerator copies; the name stays in the entry */
                mem.push(rf);
                items.push(rf);
            }
        }
        IEnum_ *en = make_enum(EK_REG, items.p, items.n);
        os_unlock(&g_reg_lock);
        for (size_t i = 0; i < mem.n; i++) delete mem[i];
        *out = en;
        return en ? S_OK_ : E_OUTOFMEMORY_;
    }
};

/* ---- IDispatch of the dual interfaces: names and Invoke over a member table */
struct Var { uint16_t vt, r1, r2, r3; union { int32_t l; double d; float f; int16_t i; const WCH *bstr; void *p; int64_t ll; }; };
struct DispParams { Var *rg; long *named; unsigned cargs, cnamed; };
struct Excep { uint16_t code, r; void *src, *desc, *help; uint32_t ctx; void *res; void *fill; HR scode; };
enum { VT_I2_ = 2, VT_I4_ = 3, VT_R4_ = 4, VT_R8_ = 5, VT_BSTR_ = 8, VT_BOOL_ = 11, VT_UI1_ = 17, VT_BYREF_ = 0x4000 };
enum { DM_METHOD = 1, DM_GET = 2, DM_PUT = 4 };
/* DISPID rule (control.idl carries no id() attributes): the type library compiler (widl/MIDL) numbers the members of an
 * interface derived from IDispatch as 0x60020000 + the index of the declaration among that interface's own methods,
 * every [propget]/[propput] accessor counting as one declaration; the accessors of one name share the id of the first
 * declared. `id` below is that index, copied from the declaration order of control.idl. */
struct DM { const char *name; uint8_t flags; uint8_t id; };
const long DISPID_BASE = 0x60020000;

bool w_ieq(const WCH *w, const char *a)
{
    for (; *w && *a; w++, a++) {
        WCH x = *w, y = (uint8_t)*a;
        if (x >= 'A' && x <= 'Z') x += 32;
        if (y >= 'A' && y <= 'Z') y += 32;
        if (x != y) return false;
    }
    return !*w && !*a;
}
bool parse_num(const WCH *s, double *out)
{
    char b[64];
    size_t n = 0;
    while (*s && n < 63) b[n++] = (*s > 127) ? '?' : (char)*s++;
    b[n] = 0;
    char *end;
    double v = strtod(b, &end);
    if (end == b) return false;
    while (*end == ' ') end++;
    if (*end) return false;
    *out = v;
    return true;
}
bool var_r8(const Var &v, double *out)
{
    uint16_t t = v.vt & ~VT_BYREF_;
    bool ref = v.vt & VT_BYREF_;
    switch (t) {
    case VT_I2_: *out = ref ? *(int16_t *)v.p : v.i; return true;
    case VT_I4_: *out = ref ? *(int32_t *)v.p : v.l; return true;
    case VT_R4_: *out = ref ? *(float *)v.p : v.f; return true;
    case VT_R8_: *out = ref ? *(double *)v.p : v.d; return true;
    case VT_BOOL_: *out = (ref ? *(int16_t *)v.p : v.i) ? -1 : 0; return true;
    case VT_UI1_: *out = ref ? *(uint8_t *)v.p : (uint8_t)v.l; return true;
    case VT_BSTR_: return parse_num(ref ? *(const WCH **)v.p : v.bstr, out);
    }
    return false;
}
bool var_i4(const Var &v, long *out)
{
    double d;
    if (!var_r8(v, &d) || d > 2147483647.0 || d < -2147483648.0) return false;
    *out = (long)llround(d);
    return true;
}
bool var_bstr(const Var &v, const WCH **out)
{
    if ((v.vt & ~VT_BYREF_) != VT_BSTR_) return false;
    *out = (v.vt & VT_BYREF_) ? *(const WCH **)v.p : v.bstr;
    return true;
}
struct Args {
    Var *rg;
    unsigned n;
    Var &at(unsigned i) { return rg[n - 1 - i]; }   /* parameters arrive last first */
    bool i4(unsigned i, long *o) { return i < n && var_i4(at(i), o); }
    bool r8(unsigned i, double *o) { return i < n && var_r8(at(i), o); }
    bool bstr(unsigned i, const WCH **o) { return i < n && var_bstr(at(i), o); }
    bool out_i4(unsigned i, long v)
    {
        if (i >= n || at(i).vt != (VT_I4_ | VT_BYREF_) || !at(i).p) return false;
        *(int32_t *)at(i).p = (int32_t)v;
        return true;
    }
};
typedef HR (*InvFn)(void *self, unsigned member, int kind, Args &a, Var *res);
HR disp_names(const DM *tbl, unsigned n, const void *riid, WCH **names, unsigned cnames, long *ids)
{
    if (!geq(riid, GUID_NULL_)) return DISP_E_UNKNOWNINTERFACE_;
    if (!names || !ids || !cnames) return E_POINTER_;
    HR hr = S_OK_;
    for (unsigned k = 0; k < cnames; k++) {
        ids[k] = -1;
        for (unsigned i = 0; k == 0 && i < n; i++)
            if (w_ieq(names[0], tbl[i].name)) ids[0] = DISPID_BASE + (long)tbl[i].id;
        if (ids[k] == -1) hr = DISP_E_UNKNOWNNAME_;   /* named parameters are not supported */
    }
    return hr;
}
HR disp_invoke(const DM *tbl, unsigned n, void *self, InvFn fn, long id, uint16_t flags, DispParams *dp, Var *res, Excep *ex, unsigned *argerr)
{
    unsigned m = n;
    for (unsigned i = 0; i < n && id >= DISPID_BASE; i++)
        if (id - DISPID_BASE == tbl[i].id) m = i;
    if (m >= n) return DISP_E_MEMBERNOTFOUND_;
    int kind;
    if (flags & (4 | 8)) kind = DM_PUT;
    else if (flags & 2) kind = DM_GET;
    else if (flags & 1) kind = (tbl[m].flags & DM_METHOD) ? DM_METHOD : DM_GET;
    else return DISP_E_MEMBERNOTFOUND_;
    if (!(tbl[m].flags & kind)) return DISP_E_MEMBERNOTFOUND_;
    unsigned cargs = dp ? dp->cargs : 0;
    Args a = {dp ? dp->rg : nullptr, cargs};
    if (cargs && !a.rg) return E_POINTER_;
    HR hr = fn(self, m, kind, a, res);
    if (hr == DISP_E_BADPARAMCOUNT_ || hr == DISP_E_TYPEMISMATCH_) { if (argerr) *argerr = 0; return hr; }
    if (hr < 0 && ex) {   /* a failed HRESULT of the method travels as an exception */
        memset(ex, 0, sizeof *ex);
        ex->scode = hr;
        return DISP_E_EXCEPTION_;
    }
    return hr;
}

/* ---- the graph and its sub-objects */
struct Graph;
/* a reference the graph holds of an object it did not make: released outside the lock, when retired and no call is in flight */
struct Hold {
    Unk *u = nullptr;        /* the long-lived reference (acquired outside the lock) */
    int use = 0;             /* calls in flight on a raw pointer taken from this hold (graph lock) */
    bool retired = false;    /* unlinked from its slot: the last use ends releases u */
};
struct Entry : Hold { IBaseFilter_ *f = nullptr; BFilter *bf = nullptr; WCH name[128] = {}; };
struct Ev { long code; intptr_t p1, p2; Unk *keep; };   /* keep: the platform's reference of the IUnknown an EC_DEVICE_LOST carries in p1 (a proxy of the game's object) */

#define SUB_UNK                                                                                               \
    Graph *g;                                                                                                 \
    HR QueryInterface(const void *iid, void **out) override;                                                  \
    unsigned long AddRef() override;                                                                          \
    unsigned long Release() override;
#define SUB_DISPATCH                                                                                          \
    HR GetTypeInfoCount(unsigned *n) override { if (!n) return E_POINTER_; *n = 1; return S_OK_; }           \
    HR GetTypeInfo(unsigned i, uint32_t, void **o) override                                                   \
    {                                                                                                         \
        if (!o) return E_POINTER_;                                                                            \
        *o = nullptr;                                                                                         \
        return i ? DISP_E_BADINDEX_ : TYPE_E_CANTLOADLIBRARY_;   /* the quartz type library is not here */   \
    }                                                                                                         \
    HR GetIDsOfNames(const void *riid, WCH **names, unsigned n, uint32_t, long *ids) override;               \
    HR Invoke(long id, const void *riid, uint32_t, uint16_t fl, void *dp, void *res, void *ex, unsigned *ae) override;

const DM kControlDM[] = {{"Run", DM_METHOD, 0}, {"Pause", DM_METHOD, 1}, {"Stop", DM_METHOD, 2}, {"GetState", DM_METHOD, 3},
                         {"RenderFile", DM_METHOD, 4}, {"AddSourceFilter", DM_METHOD, 5}, {"FilterCollection", DM_GET, 6},
                         {"RegFilterCollection", DM_GET, 7}, {"StopWhenReady", DM_METHOD, 8}};
const DM kEventDM[] = {{"GetEventHandle", DM_METHOD, 0}, {"GetEvent", DM_METHOD, 1}, {"WaitForCompletion", DM_METHOD, 2},
                       {"CancelDefaultHandling", DM_METHOD, 3}, {"RestoreDefaultHandling", DM_METHOD, 4},
                       {"FreeEventParams", DM_METHOD, 5}};
const DM kPositionDM[] = {{"Duration", DM_GET, 0}, {"CurrentPosition", DM_GET | DM_PUT, 1}, {"StopTime", DM_GET | DM_PUT, 3},
                          {"PrerollTime", DM_GET | DM_PUT, 5}, {"Rate", DM_GET | DM_PUT, 7}, {"CanSeekForward", DM_METHOD, 9},
                          {"CanSeekBackward", DM_METHOD, 10}};
const DM kAudioDM[] = {{"Volume", DM_GET | DM_PUT, 0}, {"Balance", DM_GET | DM_PUT, 2}};
#define NDM(t) (sizeof(t) / sizeof(t[0]))

struct Control final : IMediaControl_ {
    SUB_UNK SUB_DISPATCH
    HR Run() override;
    HR Pause() override;
    HR Stop() override;
    HR GetState(long ms, long *state) override;
    HR RenderFile(const WCH *) override;
    /* Wine 9.0's quartz returns S_OK from all three after a FIXME and leaves the out pointer alone */
    HR AddSourceFilter(const WCH *, void **) override { fnvnx_wine_fixme(200); return S_OK_; }
    HR get_FilterCollection(void **) override { fnvnx_wine_fixme(201); return S_OK_; }
    HR get_RegFilterCollection(void **) override { fnvnx_wine_fixme(202); return S_OK_; }
    HR StopWhenReady() override;
};
struct Event final : IMediaEventEx_ {
    SUB_UNK SUB_DISPATCH
    HR GetEventHandle(uintptr_t *h) override;
    HR GetEvent(long *code, intptr_t *p1, intptr_t *p2, long ms) override;
    HR WaitForCompletion(long ms, long *code) override;
    HR CancelDefaultHandling(long) override;
    HR RestoreDefaultHandling(long) override;
    HR FreeEventParams(long, intptr_t, intptr_t) override;
    HR SetNotifyWindow(uintptr_t hwnd, long msg, intptr_t inst) override;
    HR SetNotifyFlags(long) override;
    HR GetNotifyFlags(long *) override;
};
struct Sink final : IMediaEventSink_ {
    SUB_UNK
    HR Notify(long code, intptr_t p1, intptr_t p2) override;
};
struct MFilter final : IMediaFilter_ {
    SUB_UNK
    HR GetClassID(Guid *c) override { if (!c) return E_POINTER_; *c = CLSID_FilterGraph_; return S_OK_; }
    HR Stop() override;
    HR Pause() override;
    HR Run(LL start) override;
    HR GetState(uint32_t ms, int *st) override;
    HR SetSyncSource(IReferenceClock_ *) override;
    HR GetSyncSource(IReferenceClock_ **) override;
};
struct Seeking final : IMediaSeeking_ {
    SUB_UNK
    HR GetCapabilities(uint32_t *) override;
    HR CheckCapabilities(uint32_t *) override;
    HR IsFormatSupported(const Guid *) override;
    HR QueryPreferredFormat(Guid *) override;
    HR GetTimeFormat(Guid *) override;
    HR IsUsingTimeFormat(const Guid *) override;
    HR SetTimeFormat(const Guid *) override;
    HR GetDuration(LL *) override;
    HR GetStopPosition(LL *) override;
    HR GetCurrentPosition(LL *) override;
    HR ConvertTimeFormat(LL *, const Guid *, LL, const Guid *) override;
    HR SetPositions(LL *, uint32_t, LL *, uint32_t) override;
    HR GetPositions(LL *, LL *) override;
    HR GetAvailable(LL *, LL *) override;
    HR SetRate(double) override;
    HR GetRate(double *) override;
    HR GetPreroll(LL *) override;
};
struct Position final : IMediaPosition_ {
    SUB_UNK SUB_DISPATCH
    HR get_Duration(REFTIME *) override;
    HR put_CurrentPosition(REFTIME) override;
    HR get_CurrentPosition(REFTIME *) override;
    HR get_StopTime(REFTIME *) override;
    HR put_StopTime(REFTIME) override;
    HR get_PrerollTime(REFTIME *) override;
    HR put_PrerollTime(REFTIME) override;
    HR put_Rate(double) override;
    HR get_Rate(double *) override;
    HR CanSeekForward(long *) override;
    HR CanSeekBackward(long *) override;
};
struct Audio final : IBasicAudio_ {
    SUB_UNK SUB_DISPATCH
    HR put_Volume(long) override;
    HR get_Volume(long *) override;
    HR put_Balance(long) override;
    HR get_Balance(long *) override;
};
struct Version final : IGraphVersion_ {
    SUB_UNK
    HR QueryVersion(long *v) override;
};
struct Site final : IObjectWithSite_ {
    SUB_UNK
    HR SetSite(Unk *) override;
    HR GetSite(const void *iid, void **) override;
};
/* IVideoWindow and IBasicVideo (dshow_int.h): the graph forwards to a video renderer; with none every method fails with
 * E_NOINTERFACE, as an audio-only graph does (the bodies are the table rows of env_const.tsv, one per method). */
#define NOVIDEO_DISPATCH                                                                                      \
    HR GetTypeInfoCount(unsigned *n) override { if (!n) return E_POINTER_; *n = 1; return S_OK_; }           \
    HR GetTypeInfo(unsigned i, uint32_t, void **o) override { if (o) *o = nullptr; return i ? DISP_E_BADINDEX_ : TYPE_E_CANTLOADLIBRARY_; } \
    HR GetIDsOfNames(const void *, WCH **, unsigned, uint32_t, long *) override;                            \
    HR Invoke(long, const void *, uint32_t, uint16_t, void *, void *, void *, unsigned *) override;
struct VideoWindow final : IVideoWindow_ {
    SUB_UNK NOVIDEO_DISPATCH
    HR put_Caption(WCH *) override;
    HR get_Caption(WCH **) override;
    HR put_WindowStyle(long) override;
    HR get_WindowStyle(long *) override;
    HR put_WindowStyleEx(long) override;
    HR get_WindowStyleEx(long *) override;
    HR put_AutoShow(long) override;
    HR get_AutoShow(long *) override;
    HR put_WindowState(long) override;
    HR get_WindowState(long *) override;
    HR put_BackgroundPalette(long) override;
    HR get_BackgroundPalette(long *) override;
    HR put_Visible(long) override;
    HR get_Visible(long *) override;
    HR put_Left(long) override;
    HR get_Left(long *) override;
    HR put_Width(long) override;
    HR get_Width(long *) override;
    HR put_Top(long) override;
    HR get_Top(long *) override;
    HR put_Height(long) override;
    HR get_Height(long *) override;
    HR put_Owner(intptr_t) override;
    HR get_Owner(intptr_t *) override;
    HR put_MessageDrain(intptr_t) override;
    HR get_MessageDrain(intptr_t *) override;
    HR get_BorderColor(long *) override;
    HR put_BorderColor(long) override;
    HR get_FullScreenMode(long *) override;
    HR put_FullScreenMode(long) override;
    HR SetWindowForeground(long) override;
    HR NotifyOwnerMessage(intptr_t hwnd, long msg, intptr_t wparam, intptr_t lparam) override;
    HR SetWindowPosition(long l, long t, long w, long h) override;
    HR GetWindowPosition(long *l, long *t, long *w, long *h) override;
    HR GetMinIdealImageSize(long *w, long *h) override;
    HR GetMaxIdealImageSize(long *w, long *h) override;
    HR GetRestorePosition(long *l, long *t, long *w, long *h) override;
    HR HideCursor(long) override;
    HR IsCursorHidden(long *) override;
};
struct BasicVideo final : IBasicVideo_ {
    SUB_UNK NOVIDEO_DISPATCH
    HR get_AvgTimePerFrame(REFTIME *) override;
    HR get_BitRate(long *) override;
    HR get_BitErrorRate(long *) override;
    HR get_VideoWidth(long *) override;
    HR get_VideoHeight(long *) override;
    HR put_SourceLeft(long) override;
    HR get_SourceLeft(long *) override;
    HR put_SourceWidth(long) override;
    HR get_SourceWidth(long *) override;
    HR put_SourceTop(long) override;
    HR get_SourceTop(long *) override;
    HR put_SourceHeight(long) override;
    HR get_SourceHeight(long *) override;
    HR put_DestinationLeft(long) override;
    HR get_DestinationLeft(long *) override;
    HR put_DestinationWidth(long) override;
    HR get_DestinationWidth(long *) override;
    HR put_DestinationTop(long) override;
    HR get_DestinationTop(long *) override;
    HR put_DestinationHeight(long) override;
    HR get_DestinationHeight(long *) override;
    HR SetSourcePosition(long l, long t, long w, long h) override;
    HR GetSourcePosition(long *l, long *t, long *w, long *h) override;
    HR SetDefaultSourcePosition() override;
    HR SetDestinationPosition(long l, long t, long w, long h) override;
    HR GetDestinationPosition(long *l, long *t, long *w, long *h) override;
    HR SetDefaultDestinationPosition() override;
    HR GetVideoSize(long *w, long *h) override;
    HR GetVideoPaletteEntries(long start, long count, long *retrieved, long *palette) override;
    HR GetCurrentImage(long *size, long *dib) override;
    HR IsUsingDefaultSource() override;
    HR IsUsingDefaultDestination() override;
};

struct Graph final : IFilterGraph2_, GraphCore {
    int refs = 1;
    Control control;
    Event event;
    Sink sink;
    MFilter mfilter;
    Seeking seeking;
    Position position;
    Audio audio;
    Site site_if;
    Version version_if;
    VideoWindow vwin;
    BasicVideo bvid;
    Vec<Entry *> filters;
    int state = State_Stopped;
    /* clock */
    Hold *clock = nullptr;   /* the sync source (a Hold of an IReferenceClock_) */
    bool clock_explicit = false;
    /* seeking engine */
    bool fmt_sample = false;
    LL stop_req = -1, preroll = 0;
    double play_rate = 1.0;
    /* events */
    Vec<Ev> queue;
    Vec<Ev> lent;   /* events GetEvent handed out whose parameters are not freed yet (FreeEventParams) */
    HANDLE evt = nullptr;
    long notify_flags = 0, notify_msg = 0;
    uintptr_t notify_hwnd = 0;
    intptr_t notify_inst = 0;
    int win_pending = 0;
    bool cancel_complete = false, latch = false;
    long latch_code = 0;
    Vec<intptr_t> done;   /* filters that reported EC_COMPLETE since the stream started */
    Hold *site = nullptr;    /* IObjectWithSite */
    uintptr_t logh = 0;
    void *pump = nullptr;   /* the clock's pump (fnvnx_clock.h) */
    volatile int quit = 0;

    Graph()
    {
        control.g = event.g = sink.g = mfilter.g = seeking.g = position.g = audio.g = site_if.g = version_if.g = vwin.g = bvid.g = this;
        evt = CreateEventA(nullptr, TRUE, FALSE, nullptr);
        clk_device_watch(evt, 1);   /* the pump signals it: a wait on it runs the pump on virtual time */
    }
    ~Graph();
    HR QueryInterface(const void *iid, void **out) override;
    unsigned long AddRef() override { return __atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    unsigned long Release() override
    {
        int r = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (!r) delete this;
        return (unsigned long)r;
    }
    /* IFilterGraph / IGraphBuilder / IFilterGraph2 */
    HR AddFilter(IBaseFilter_ *f, const WCH *name) override;
    HR RemoveFilter(IBaseFilter_ *f) override;
    HR EnumFilters(void **) override;
    HR FindFilterByName(const WCH *, IBaseFilter_ **) override;
    HR ConnectDirect(IPin_ *, IPin_ *, const MT *) override;
    HR Reconnect(IPin_ *p) override { return reconnect(p, nullptr); }
    HR Disconnect(IPin_ *) override;
    HR SetDefaultSyncSource() override;
    HR Connect(IPin_ *, IPin_ *) override;
    HR Render(IPin_ *pin) override { return render(pin, false); }
    HR RenderFile(const WCH *, const WCH *) override;
    HR AddSourceFilter(const WCH *, const WCH *, IBaseFilter_ **) override;
    HR SetLogFile(uintptr_t h) override { logh = h; return S_OK_; }
    HR Abort() override { aborted = 1; return S_OK_; }
    HR ShouldOperationContinue() override { return aborted ? S_FALSE_ : S_OK_; }
    HR AddSourceFilterForMoniker(void *, void *, const WCH *, IBaseFilter_ **) override;
    HR ReconnectEx(IPin_ *p, const MT *mt) override { return reconnect(p, mt); }
    HR RenderEx(IPin_ *, uint32_t, uint32_t *) override;
    /* GraphCore */
    bool event_locked(long code, intptr_t p1, intptr_t p2) override { return post_locked(Ev{code, p1, p2, nullptr}); }
    bool post_locked(const Ev &ev);
    void completion_locked(BFilter *r) override;
    void start_pump() override;
    void flush_window() override;

    void logf(const char *fmt, ...);
    void pin_label(IPin_ *p, char *buf, size_t n);
    HR add_filter(Unk *u, const WCH *name, bool joined_name);
    HR reconnect(IPin_ *, const MT *);
    HR render(IPin_ *pin, bool existing_only);
    bool pin_in_graph(IPin_ *p);
    HR connect_logged(IPin_ *out, IPin_ *in);
    HR build(IPin_ *out, IPin_ *target, bool existing_only, int depth);
    HR downstream(BFilter *f, IPin_ *target, bool existing_only, int depth);
    void disconnect_all(IBaseFilter_ *f);
    void snapshot(Vec<Entry *> &v);   /* most recently added first; each entry in use until snap_free (no AddRef) */
    void snap_free(Vec<Entry *> &v);
    template <class H> void put(H *h);            /* one use less; the last use of a retired hold releases it (outside the lock) */
    template <class H> void finish(H *h) { h->u->Release(); delete h; }
    IReferenceClock_ *clock_use(Hold **h);      /* the clock, in use until put(*h); null if none */
    Hold *set_clock(Unk *ck, bool is_explicit);   /* takes the caller's reference of ck (AddRef'd outside the lock); the new hold, in use */
    int depth_of(IBaseFilter_ *f, int limit);
    HR run_all(LL start);
    HR pause_all();
    HR stop_all();
    /* the streams (graph lock held) */
    void renderers_locked(Vec<Renderer *> &v);
    bool dur_locked(LL *d);
    HR seek100(const LL *start, int sflags, const LL *stop, int stflags, LL *rstart, LL *rstop);
    HR cur_stop_locked(LL *cur, LL *stop);
    void clear_completion_locked() { latch = false; latch_code = 0; done.n = 0; }
    HR audio_target(Renderer **r);
};

#define SUB_UNK_IMPL(T)                                                                                       \
    HR T::QueryInterface(const void *iid, void **out) { return g->QueryInterface(iid, out); }               \
    unsigned long T::AddRef() { return g->AddRef(); }                                                         \
    unsigned long T::Release() { return g->Release(); }
SUB_UNK_IMPL(Control) SUB_UNK_IMPL(Event) SUB_UNK_IMPL(Sink) SUB_UNK_IMPL(MFilter) SUB_UNK_IMPL(Seeking) SUB_UNK_IMPL(Position)
SUB_UNK_IMPL(Audio) SUB_UNK_IMPL(Version) SUB_UNK_IMPL(Site) SUB_UNK_IMPL(VideoWindow) SUB_UNK_IMPL(BasicVideo)
#include "dshow.env_const.gen.inc"   /* the video methods: manifest/env_const.tsv rows of audio/dshow.cpp */

HR Graph::QueryInterface(const void *iid, void **out)
{
    if (!out) return E_POINTER_;
    *out = nullptr;
    if (geq(iid, IID_IUnknown_) || geq(iid, IID_IFilterGraph_) || geq(iid, IID_IGraphBuilder_) || geq(iid, IID_IFilterGraph2_))
        *out = static_cast<IFilterGraph2_ *>(this);
    else if (geq(iid, IID_IMediaControl_) || geq(iid, IID_IDispatch_)) *out = static_cast<IMediaControl_ *>(&control);
    else if (geq(iid, IID_IMediaEvent_) || geq(iid, IID_IMediaEventEx_)) *out = static_cast<IMediaEventEx_ *>(&event);
    else if (geq(iid, IID_IMediaEventSink_)) *out = static_cast<IMediaEventSink_ *>(&sink);
    else if (geq(iid, IID_IMediaFilter_) || geq(iid, IID_IPersist_)) *out = static_cast<IMediaFilter_ *>(&mfilter);
    else if (geq(iid, IID_IMediaSeeking_)) *out = static_cast<IMediaSeeking_ *>(&seeking);
    else if (geq(iid, IID_IMediaPosition_)) *out = static_cast<IMediaPosition_ *>(&position);
    else if (geq(iid, IID_IBasicAudio_)) *out = static_cast<IBasicAudio_ *>(&audio);
    else if (geq(iid, IID_IVideoWindow_)) *out = static_cast<IVideoWindow_ *>(&vwin);
    else if (geq(iid, IID_IBasicVideo_)) *out = static_cast<IBasicVideo_ *>(&bvid);
    else if (geq(iid, IID_IGraphVersion_)) *out = static_cast<IGraphVersion_ *>(&version_if);
    else if (geq(iid, IID_IObjectWithSite_)) *out = static_cast<IObjectWithSite_ *>(&site_if);
    else return E_NOINTERFACE_;
    AddRef();
    return S_OK_;
}

/* ---- logging and pin helpers */
void Graph::logf(const char *fmt, ...)
{
    if (!logh) return;
    char b[300];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof b - 3) n = sizeof b - 3;
    b[n++] = '\r';
    b[n++] = '\n';
    DWORD w = 0;
    WriteFile((HANDLE)logh, b, (DWORD)n, &w, nullptr);
}
static void ascii(const WCH *w, char *d, size_t n)
{
    size_t i = 0;
    for (; w && w[i] && i + 1 < n; i++) d[i] = w[i] < 128 ? (char)w[i] : '?';
    d[i] = 0;
}
void Graph::pin_label(IPin_ *p, char *buf, size_t n)
{
    PinInfo pi = {};
    char fn[80] = "?", pn[48] = "?";
    if (p && p->QueryPinInfo(&pi) >= 0) {
        ascii(pi.name, pn, sizeof pn);
        if (pi.filter) {
            FilterInfo fi = {};
            if (((IBaseFilter_ *)pi.filter)->QueryFilterInfo(&fi) >= 0) {
                ascii(fi.name, fn, sizeof fn);
                if (fi.graph) ((Unk *)fi.graph)->Release();
            }
            ((Unk *)pi.filter)->Release();
        }
    }
    snprintf(buf, n, "%s.%s", fn, pn);
}
bool Graph::pin_in_graph(IPin_ *p)
{
    PinInfo pi = {};
    if (p->QueryPinInfo(&pi) < 0) return false;
    if (!pi.filter) return true;   /* a bare reader pin made by Render itself */
    bool in = false;
    {
        GL l(this);
        for (size_t i = 0; i < filters.n; i++)
            if (filters[i]->f == (IBaseFilter_ *)pi.filter) in = true;
    }
    ((Unk *)pi.filter)->Release();
    return in;
}

/* ---- holds: the references the graph keeps, released outside the lock */
template <class H> void Graph::put(H *h)
{
    bool fin;
    {
        GL l(this);
        fin = --h->use == 0 && h->retired;
    }
    if (fin) finish(h);
}
void Graph::snapshot(Vec<Entry *> &v)
{
    GL l(this);
    for (size_t i = filters.n; i-- > 0;)   /* most recently added first, as EnumFilters does */
        if (filters[i]->f) { filters[i]->use++; v.push(filters[i]); }
}
void Graph::snap_free(Vec<Entry *> &v)
{
    for (size_t i = 0; i < v.n; i++) put(v[i]);
    v.n = 0;
}
IReferenceClock_ *Graph::clock_use(Hold **h)
{
    GL l(this);
    *h = clock;
    if (!clock) return nullptr;
    clock->use++;
    return static_cast<IReferenceClock_ *>(clock->u);
}
Hold *Graph::set_clock(Unk *ck, bool is_explicit)   /* the new clock's hold, in use by the caller (null: no clock) */
{
    Hold *h = nullptr;
    if (ck) {
        h = new (std::nothrow) Hold();
        if (!h) ck->Release();
        else { h->u = ck; h->use = 1; }
    }
    Hold *old;
    bool fin = false;
    {
        GL l(this);
        old = clock;
        clock = h;
        clock_explicit = is_explicit;
        if (old) { old->retired = true; fin = old->use == 0; }
    }
    if (fin) finish(old);
    return h;
}

/* ---- filters in the graph */
HR Graph::add_filter(Unk *u, const WCH *name, bool)
{
    IBaseFilter_ *bf = nullptr;
    if (u->QueryInterface(&IID_IBaseFilter_, (void **)&bf) < 0) bf = nullptr;
    else bf->Release();   /* the graph's own reference below keeps it */
    Entry *e = new (std::nothrow) Entry();
    if (!e) return E_OUTOFMEMORY_;
    e->u = u;
    e->f = bf;
    e->bf = bf ? internal_filter(u) : nullptr;
    e->use = 1;   /* this call's own: the name it passes to JoinFilterGraph must outlive a re-entrant RemoveFilter */
    WCH base[128];
    wcopy(base, 120, name && *name ? name : (const WCH *)u"Filter");
    u->AddRef();   /* the graph's long-lived reference: taken before the lock, a call into the filter */
    HR hr = S_OK_, fail = S_OK_;
    {
        GL l(this);
        for (size_t i = 0; i < filters.n; i++)
            if (filters[i]->u == u) fail = E_FAIL_;   /* already in this graph */
        if (fail >= 0) {
            wcopy(e->name, 128, base);
            for (unsigned n = 1;; n++) {   /* names are unique: "name 0001" and VFW_S_DUPLICATE_NAME */
                bool dup = false;
                for (size_t i = 0; i < filters.n; i++)
                    if (weq_nocase(filters[i]->name, e->name)) dup = true;
                if (!dup || n > 9999) break;
                hr = VFW_S_DUPLICATE_NAME_;
                wcopy(e->name, 128, base);
                char sfx[12];
                snprintf(sfx, sizeof sfx, " %04u", n);
                size_t l0 = wlen(e->name);
                for (size_t k = 0; sfx[k] && l0 + k < 127; k++) e->name[l0 + k] = (uint8_t)sfx[k], e->name[l0 + k + 1] = 0;
            }
            if (filters.push(e)) bump_version();
            else fail = E_OUTOFMEMORY_;
        }
    }
    if (fail < 0) {
        u->Release();
        delete e;
        return fail;
    }
    if (bf) {
        HR j = bf->JoinFilterGraph(static_cast<IFilterGraph2_ *>(this), e->name);
        if (j < 0) {   /* it may already be gone again (the filter removed itself from inside the call): then its remover owns the release */
            {
                GL l(this);
                for (size_t i = 0; i < filters.n; i++)
                    if (filters[i] == e) { filters.erase(i); e->retired = true; bump_version(); break; }
            }
            put(e);
            return j;
        }
    }
    char nm[128];
    ascii(e->name, nm, sizeof nm);
    put(e);
    logf("AddFilter '%s'%s", nm, hr == VFW_S_DUPLICATE_NAME_ ? " (renamed)" : "");
    return hr;
}
HR Version::QueryVersion(long *v)
{
    if (!v) return E_POINTER_;
    *v = (long)__atomic_load_n(&g->version, __ATOMIC_ACQUIRE);
    return S_OK_;
}
HR Graph::AddFilter(IBaseFilter_ *f, const WCH *name)
{
    if (!f) return E_POINTER_;
    return add_filter((Unk *)f, name, false);
}
void Graph::disconnect_all(IBaseFilter_ *f)
{
    void *e = nullptr;
    if (f->EnumPins(&e) < 0 || !e) return;
    IPin_ *p;
    uint32_t got;
    while (((IEnum_ *)e)->Next(1, (void **)&p, &got) == 0 && got == 1) {
        Disconnect(p);
        p->Release();
    }
    ((Unk *)e)->Release();
}
HR Graph::RemoveFilter(IBaseFilter_ *f)
{
    if (!f) return E_POINTER_;
    Entry *e = nullptr;
    {
        GL l(this);
        for (size_t i = 0; i < filters.n; i++)
            if (filters[i]->u == (Unk *)f) { e = filters[i]; filters.erase(i); break; }
        if (e) {   /* retired: unlinked at once; the reference goes back when the last call using it ends (put below) */
            e->retired = true;
            e->use++;
            stop_req = -1;
            done.n = 0;
            bump_version();
        }
    }
    if (!e) return VFW_E_NOT_FOUND_;
    char nm[128];
    ascii(e->name, nm, sizeof nm);
    logf("RemoveFilter '%s'", nm);
    if (e->f) {
        int st = 0;
        if (e->f->GetState(0, &st) >= 0 && st != State_Stopped) e->f->Stop();
        disconnect_all(e->f);
        Hold *ch = nullptr;
        IReferenceClock_ *cur = clock_use(&ch);
        IReferenceClock_ *ck = nullptr;
        if (cur && e->f->QueryInterface(&IID_IReferenceClock_, (void **)&ck) >= 0) {   /* its clock goes with it */
            if (ck == cur) {
                bool mine = false;
                {
                    GL l(this);
                    if (clock == ch) { clock = nullptr; clock_explicit = false; ch->retired = true; mine = true; }
                }
                if (mine) {
                    Vec<Entry *> v;
                    snapshot(v);
                    for (size_t i = 0; i < v.n; i++) v[i]->f->SetSyncSource(nullptr);
                    snap_free(v);
                }
            }
            ck->Release();
        }
        if (ch) put(ch);
        e->f->JoinFilterGraph(nullptr, nullptr);
    }
    put(e);   /* the graph's own reference of the filter goes back here unless another call still uses it */
    return S_OK_;
}
HR Graph::EnumFilters(void **out)
{
    if (!out) return E_POINTER_;
    Vec<Entry *> v;
    snapshot(v);
    Vec<void *> items;
    for (size_t i = 0; i < v.n; i++) items.push(v[i]->f);
    IEnum_ *e = make_enum(EK_UNK, items.p, items.n);   /* AddRefs each item, outside the lock */
    snap_free(v);
    *out = e;
    return e ? S_OK_ : E_OUTOFMEMORY_;
}
HR Graph::FindFilterByName(const WCH *name, IBaseFilter_ **out)
{
    if (!name || !out) return E_POINTER_;
    *out = nullptr;
    Entry *hit = nullptr;
    {
        GL l(this);
        for (size_t i = 0; i < filters.n && !hit; i++)
            if (filters[i]->f && weq_nocase(filters[i]->name, name)) { hit = filters[i]; hit->use++; }
    }
    if (!hit) return VFW_E_NOT_FOUND_;
    hit->f->AddRef();
    *out = hit->f;
    put(hit);
    return S_OK_;
}

/* ---- connections */
HR Graph::Disconnect(IPin_ *pin)
{
    if (!pin) return E_POINTER_;
    IPin_ *peer = nullptr;
    if (pin->ConnectedTo(&peer) < 0 || !peer) return S_FALSE_;
    HR hr = pin->Disconnect();
    if (hr >= 0) hr = peer->Disconnect();
    peer->Release();
    char a[100], b[100];
    pin_label(pin, a, sizeof a);
    logf("Disconnect %s: 0x%08x", a, (unsigned)hr);
    (void)b;
    return hr;
}
HR Graph::ConnectDirect(IPin_ *out, IPin_ *in, const MT *mt)
{
    if (!out || !in) return E_POINTER_;
    int d1 = -1, d2 = -1;
    if (out->QueryDirection(&d1) < 0 || in->QueryDirection(&d2) < 0 || d1 != PINDIR_OUTPUT || d2 != PINDIR_INPUT) return VFW_E_INVALID_DIRECTION_;
    if (!pin_in_graph(out) || !pin_in_graph(in)) return VFW_E_NOT_IN_GRAPH_;
    return connect_logged(out, in);
}
HR Graph::connect_logged(IPin_ *out, IPin_ *in)
{
    HR hr = out->Connect(in, nullptr);
    if (logh) {
        char a[100], b[100];
        pin_label(out, a, sizeof a);
        pin_label(in, b, sizeof b);
        logf("Connect %s -> %s: 0x%08x", a, b, (unsigned)hr);
    }
    return hr;
}
HR Graph::reconnect(IPin_ *pin, const MT *mt)
{
    if (!pin) return E_POINTER_;
    IPin_ *peer = nullptr;
    if (pin->ConnectedTo(&peer) < 0 || !peer) return VFW_E_NOT_CONNECTED_;
    int d = -1;
    pin->QueryDirection(&d);
    IPin_ *out = d == PINDIR_OUTPUT ? pin : peer, *in = d == PINDIR_OUTPUT ? peer : pin;
    MT cur = {};
    HR hr = out->ConnectionMediaType(&cur);
    if (hr >= 0) hr = Disconnect(out);
    if (hr >= 0) {
        hr = out->Connect(in, mt ? mt : &cur);
        char a[100];
        pin_label(out, a, sizeof a);
        logf("Reconnect %s: 0x%08x", a, (unsigned)hr);
    }
    mt_free(&cur);
    peer->Release();
    return hr;
}

/* ---- intelligent connect */
static void enum_types(IPin_ *p, Vec<MT> &v)
{
    void *e = nullptr;
    if (p->EnumMediaTypes(&e) < 0 || !e) return;
    MT *m;
    uint32_t got;
    while (((IEnum_ *)e)->Next(1, (void **)&m, &got) == 0 && got == 1) {
        MT c;
        if (mt_copy(&c, m) >= 0) v.push(c);
        mt_free(m);
        CoTaskMemFree(m);
    }
    ((Unk *)e)->Release();
}
HR Graph::downstream(BFilter *f, IPin_ *target, bool existing_only, int depth)
{
    int ok = 0, fail = 0, outs = 0;
    HR last = target ? VFW_E_CANNOT_CONNECT_ : VFW_E_CANNOT_RENDER_;
    for (size_t i = 0; i < f->pins.n; i++) {
        BPin *p = f->pins[i];
        if (p->dir != PINDIR_OUTPUT) continue;
        outs++;
        if (p->peer) { ok++; continue; }
        HR hr = build(static_cast<IPin_ *>(p), target, existing_only, depth + 1);
        if (hr >= 0) ok++;
        else { fail++; last = hr; }
    }
    if (!outs) return target ? VFW_E_CANNOT_CONNECT_ : S_OK_;   /* a sink: everything is rendered */
    if (target) return ok ? S_OK_ : last;
    return fail == 0 ? S_OK_ : (ok ? VFW_S_PARTIAL_RENDER_ : last);
}
HR Graph::build(IPin_ *out, IPin_ *target, bool existing_only, int depth)
{
    if (depth > 6) return VFW_E_CANNOT_CONNECT_;
    if (aborted) return E_ABORT_;
    HR fail = target ? VFW_E_CANNOT_CONNECT_ : VFW_E_CANNOT_RENDER_;
    if (target) {
        HR hr = connect_logged(out, target);
        if (hr >= 0 || hr == VFW_E_ALREADY_CONNECTED_ || hr == E_ABORT_) return hr;
    }
    Vec<MT> ts;
    enum_types(out, ts);
    if (!ts.n) {   /* a pin that offers nothing: a stream whose kind is found by its bytes */
        MT s = {};
        s.major = MEDIATYPE_Stream_;
        s.format = FORMAT_None_;
        ts.push(s);
    }
    HR result = fail;
    /* (a) filters already in the graph with a free input pin */
    Vec<Entry *> snap;
    snapshot(snap);
    PinInfo opi = {};
    out->QueryPinInfo(&opi);
    for (size_t i = 0; i < snap.n && result < 0; i++) {
        BFilter *bf = snap[i]->bf;
        if (!bf || (void *)static_cast<IBaseFilter_ *>(bf) == opi.filter) continue;
        for (size_t k = 0; k < bf->pins.n && result < 0; k++) {
            BPin *p = bf->pins[k];
            if (p->dir != PINDIR_INPUT || p->peer || static_cast<IPin_ *>(p) == target) continue;
            if (aborted) { result = E_ABORT_; break; }
            if (connect_logged(out, static_cast<IPin_ *>(p)) < 0) continue;
            HR hr = downstream(bf, target, existing_only, depth);
            if (hr >= 0) result = hr;
            else Disconnect(out);
        }
    }
    snap_free(snap);
    if (opi.filter) ((Unk *)opi.filter)->Release();
    /* (b) installed filters, best merit first, whose input takes one of the types */
    struct Cand { Guid clsid; WCH name[64]; uint32_t merit; };
    Vec<Cand> cands;
    if (!existing_only && result < 0) {
        os_lock(&g_reg_lock);
        reg_seed_locked();
        for (size_t i = 0; i < g_reg.n; i++) {
            RegEntry *e = g_reg[i];
            if (e->instance) continue;
            bool hit = false;
            for (size_t p = 0; p < e->pins.n; p++)
                if (!e->pins[p]->output)
                    for (size_t t = 0; t < e->pins[p]->types.n; t++)
                        for (size_t k = 0; k < ts.n; k++)
                            if (geq(e->pins[p]->types[t].maj, ts[k].major) && tmatch(e->pins[p]->types[t].sub, ts[k].sub)) hit = true;
            if (!hit) continue;
            Cand c;
            c.clsid = e->clsid;
            wcopy(c.name, 64, e->name);
            c.merit = e->merit;
            size_t at = cands.n;   /* insertion by merit, stable */
            cands.push(c);
            while (at > 0 && cands[at - 1].merit < c.merit) { cands[at] = cands[at - 1]; at--; }
            cands[at] = c;
        }
        os_unlock(&g_reg_lock);
    }
    for (size_t i = 0; i < cands.n && result < 0; i++) {
        if (aborted) { result = E_ABORT_; break; }
        BFilter *nf = make_filter(cands[i].clsid);
        if (!nf) continue;   /* registered, but not a filter this environment can create */
        if (add_filter(static_cast<IBaseFilter_ *>(nf), cands[i].name, true) < 0) { nf->Release(); continue; }
        BPin *in = nullptr;
        for (size_t k = 0; k < nf->pins.n && !in; k++)
            if (nf->pins[k]->dir == PINDIR_INPUT) in = nf->pins[k];
        HR hr = in ? connect_logged(out, static_cast<IPin_ *>(in)) : E_FAIL_;
        if (hr >= 0) {
            hr = downstream(nf, target, existing_only, depth);
            if (hr < 0) Disconnect(out);
        }
        if (hr >= 0) result = hr;
        else RemoveFilter(static_cast<IBaseFilter_ *>(nf));
        nf->Release();   /* the graph holds it (or it is gone) */
    }
    for (size_t i = 0; i < ts.n; i++) mt_free(&ts[i]);
    return result;
}
HR Graph::Connect(IPin_ *out, IPin_ *in)
{
    if (!out || !in) return E_POINTER_;
    aborted = 0;
    int d1 = -1, d2 = -1;
    if (out->QueryDirection(&d1) < 0 || in->QueryDirection(&d2) < 0 || d1 != PINDIR_OUTPUT || d2 != PINDIR_INPUT) return VFW_E_INVALID_DIRECTION_;
    if (!pin_in_graph(out) || !pin_in_graph(in)) return VFW_E_NOT_IN_GRAPH_;
    HR hr = build(out, in, false, 0);
    if (hr >= 0) { GL l(this); stop_req = -1; }
    return hr;
}
HR Graph::render(IPin_ *pin, bool existing_only)
{
    if (!pin) return E_POINTER_;
    aborted = 0;
    IPin_ *ip = nullptr, *legacy = nullptr;
    if (pin->QueryInterface(&IID_IPin_, (void **)&ip) < 0 || !ip) {   /* a source that only exposes IAsyncReader */
        AsyncReader *rd = nullptr;
        if (pin->QueryInterface(&IID_IAsyncReader_, (void **)&rd) < 0 || !rd) return VFW_E_NOT_CONNECTED_;
        legacy = ip = make_reader_pin(rd);
        rd->Release();   /* borrowed for this call */
        if (!ip) return E_OUTOFMEMORY_;
    }
    HR hr;
    int d = -1;
    IPin_ *peer = nullptr;
    if (ip->QueryDirection(&d) < 0 || d != PINDIR_OUTPUT) hr = VFW_E_INVALID_DIRECTION_;
    else if (ip->ConnectedTo(&peer) >= 0 && peer) { peer->Release(); hr = VFW_E_ALREADY_CONNECTED_; }
    else if (!pin_in_graph(ip)) hr = VFW_E_NOT_IN_GRAPH_;
    else {
        logf("Render");
        hr = build(ip, nullptr, existing_only, 0);
        if (hr >= 0) {
            GL l(this);
            stop_req = -1;
        }
        logf("Render: 0x%08x", (unsigned)hr);
    }
    if (hr >= 0 && play_rate != 1.0) {   /* the graph's rate applies to the new stream */
        GL l(this);
        Vec<Renderer *> rs;
        renderers_locked(rs);
        for (size_t i = 0; i < rs.n; i++) rs[i]->set_play_rate(play_rate);
    }
    if (legacy) { reader_pin_orphan(legacy); legacy->Release(); }
    else ip->Release();
    return hr;
}
HR Graph::RenderEx(IPin_ *pin, uint32_t flags, uint32_t *context)
{
    if ((flags & ~1u) || context) return E_INVALIDARG_;   /* AM_RENDEREX_RENDERTOEXISTINGRENDERERS only; the context is reserved */
    return render(pin, (flags & 1) != 0);
}
HR Graph::AddSourceFilter(const WCH *file, const WCH *name, IBaseFilter_ **out)
{
    if (!file || !out) return E_POINTER_;
    aborted = 0;
    *out = nullptr;
    BFilter *src = nullptr;
    HR hr = make_file_source(file, &src);
    if (hr < 0) return hr;
    hr = add_filter(static_cast<IBaseFilter_ *>(src), name && *name ? name : file, false);
    if (hr < 0) { src->Release(); return hr; }
    *out = static_cast<IBaseFilter_ *>(src);   /* the graph's reference and this one */
    return S_OK_;
}
HR Graph::RenderFile(const WCH *file, const WCH *playlist)
{
    if (!file) return E_POINTER_;
    if (playlist) return E_INVALIDARG_;   /* reserved: must be NULL */
    IBaseFilter_ *src = nullptr;
    HR hr = AddSourceFilter(file, nullptr, &src);
    if (hr < 0) return hr;
    BFilter *bf = internal_filter(src);
    BPin *out = nullptr;
    for (size_t i = 0; bf && i < bf->pins.n; i++)
        if (bf->pins[i]->dir == PINDIR_OUTPUT) out = bf->pins[i];
    hr = out ? render(static_cast<IPin_ *>(out), false) : E_FAIL_;
    if (hr < 0) RemoveFilter(src);
    src->Release();
    return hr;
}
HR Graph::AddSourceFilterForMoniker(void *moniker, void *ctx, const WCH *name, IBaseFilter_ **out)
{
    if (!moniker || !out) return E_POINTER_;
    *out = nullptr;
    IBaseFilter_ *f = nullptr;
    HR hr = ((IMoniker_ *)moniker)->BindToObject(ctx, nullptr, &IID_IBaseFilter_, (void **)&f);   /* the moniker names the filter */
    if (hr < 0) return hr;
    if (!f) return E_FAIL_;
    hr = add_filter((Unk *)f, name, false);
    if (hr < 0) { f->Release(); return hr; }
    *out = f;
    return hr;
}

/* ---- sync source, state */
HR Graph::SetDefaultSyncSource()
{
    Vec<Entry *> v;
    snapshot(v);
    IReferenceClock_ *ck = nullptr;
    for (size_t i = v.n; i-- > 0 && !ck;)   /* oldest first */
        if (v[i]->f->QueryInterface(&IID_IReferenceClock_, (void **)&ck) < 0) ck = nullptr;
    Hold *h = set_clock(ck, false);   /* the QueryInterface reference becomes the graph's */
    for (size_t i = 0; i < v.n; i++) v[i]->f->SetSyncSource(h ? static_cast<IReferenceClock_ *>(h->u) : nullptr);
    if (h) put(h);
    snap_free(v);
    return S_OK_;
}
HR MFilter::SetSyncSource(IReferenceClock_ *c)
{
    if (c) c->AddRef();   /* the graph's reference, taken outside the lock */
    Hold *h = g->set_clock(c, true);
    if (c && !h) return E_OUTOFMEMORY_;
    Vec<Entry *> v;
    g->snapshot(v);
    HR hr = S_OK_;
    for (size_t i = 0; i < v.n; i++) {
        HR x = v[i]->f->SetSyncSource(c);
        if (x < 0) hr = x;
    }
    g->snap_free(v);
    if (h) g->put(h);   /* c stays alive until here: the hold is in use */
    return hr;
}
HR MFilter::GetSyncSource(IReferenceClock_ **c)
{
    if (!c) return E_POINTER_;
    Hold *h = nullptr;
    *c = g->clock_use(&h);
    if (*c) (*c)->AddRef();
    if (h) g->put(h);
    return S_OK_;
}
int Graph::depth_of(IBaseFilter_ *f, int limit)
{
    if (limit <= 0) return 0;
    void *e = nullptr;
    if (f->EnumPins(&e) < 0 || !e) return 0;
    int best = 0;
    IPin_ *p;
    uint32_t got;
    while (((IEnum_ *)e)->Next(1, (void **)&p, &got) == 0 && got == 1) {
        int d = -1;
        IPin_ *peer = nullptr;
        if (p->QueryDirection(&d) >= 0 && d == PINDIR_INPUT && p->ConnectedTo(&peer) >= 0 && peer) {
            PinInfo pi = {};
            if (peer->QueryPinInfo(&pi) >= 0 && pi.filter) {
                int x = 1 + depth_of((IBaseFilter_ *)pi.filter, limit - 1);
                if (x > best) best = x;
                ((Unk *)pi.filter)->Release();
            }
            peer->Release();
        }
        p->Release();
    }
    ((Unk *)e)->Release();
    return best;
}
/* order of a state change: Run and Pause start at the renderers and go upstream, Stop starts at the sources */
static void order_by_depth(Vec<Entry *> &v, Vec<int> &dp)
{
    for (size_t i = 1; i < v.n; i++)
        for (size_t k = i; k > 0 && dp[k - 1] > dp[k]; k--) {
            Entry *t = v[k]; v[k] = v[k - 1]; v[k - 1] = t;
            int d = dp[k]; dp[k] = dp[k - 1]; dp[k - 1] = d;
        }
}
HR Graph::run_all(LL start)
{
    int st0;
    bool need_clock;
    {
        GL l(this);
        st0 = state;
        need_clock = !clock_explicit && !clock;
    }
    if (st0 == State_Running) return S_OK_;
    if (need_clock) SetDefaultSyncSource();
    Vec<Entry *> v;
    snapshot(v);
    Vec<int> dp;
    for (size_t i = 0; i < v.n; i++) dp.push(depth_of(v[i]->f, 16));
    order_by_depth(v, dp);
    HR hr = S_OK_;
    {
        GL l(this);
        clear_completion_locked();
    }
    LL t = start;
    if (t < 0) {
        t = 0;
        Hold *h = nullptr;
        if (IReferenceClock_ *c = clock_use(&h)) { c->GetTime(&t); put(h); }
    }
    if (st0 == State_Stopped)
        for (size_t i = v.n; i-- > 0;) { HR h = v[i]->f->Pause(); if (h < 0 && hr >= 0) hr = h; }
    for (size_t i = v.n; i-- > 0 && hr >= 0;) hr = v[i]->f->Run(t);
    if (hr < 0) for (size_t i = 0; i < v.n; i++) v[i]->f->Stop();
    {
        GL l(this);
        state = hr >= 0 ? State_Running : State_Stopped;
    }
    snap_free(v);
    return hr;
}
HR Graph::pause_all()
{
    Vec<Entry *> v;
    snapshot(v);
    Vec<int> dp;
    for (size_t i = 0; i < v.n; i++) dp.push(depth_of(v[i]->f, 16));
    order_by_depth(v, dp);
    HR hr = S_OK_;
    for (size_t i = v.n; i-- > 0;) { HR h = v[i]->f->Pause(); if (h < 0 && hr >= 0) hr = h; }
    snap_free(v);
    GL l(this);
    state = State_Paused;
    return hr;
}
HR Graph::stop_all()
{
    Vec<Entry *> v;
    snapshot(v);
    Vec<int> dp;
    for (size_t i = 0; i < v.n; i++) dp.push(depth_of(v[i]->f, 16));
    order_by_depth(v, dp);
    HR hr = S_OK_;
    for (size_t i = 0; i < v.n; i++) { HR h = v[i]->f->Stop(); if (h < 0 && hr >= 0) hr = h; }
    snap_free(v);
    GL l(this);
    state = State_Stopped;
    clear_completion_locked();
    return hr;
}
HR Control::Run()
{
    return g->run_all(-1);
}
HR Control::Pause() { return g->pause_all(); }
HR Control::Stop() { return g->stop_all(); }
HR Control::StopWhenReady()
{
    HR hr = g->pause_all();   /* cue, then stop: nothing here needs a frame to be shown first */
    return hr < 0 ? hr : g->stop_all();
}
HR Control::GetState(long ms, long *st)
{
    if (!st) return E_POINTER_;
    Vec<Entry *> v;
    g->snapshot(v);
    HR hr = S_OK_;
    for (size_t i = 0; i < v.n; i++) {
        int s = 0;
        HR h = v[i]->f->GetState(ms < 0 ? 0xFFFFFFFFu : (uint32_t)ms, &s);
        if (h < 0) hr = h;
        else if (h == VFW_S_STATE_INTERMEDIATE_ && hr >= 0) hr = h;
        else if (h == VFW_S_CANT_CUE_ && hr == S_OK_) hr = h;
    }
    g->snap_free(v);
    GL l(g);
    *st = g->state;
    return hr;
}
HR Control::RenderFile(const WCH *bstr)
{
    if (!bstr) return E_POINTER_;
    return g->RenderFile(bstr, nullptr);
}
HR MFilter::Stop() { return g->stop_all(); }
HR MFilter::Pause() { return g->pause_all(); }
HR MFilter::Run(LL start) { return g->run_all(start); }
HR MFilter::GetState(uint32_t ms, int *st)
{
    if (!st) return E_POINTER_;
    long s = 0;
    HR hr = g->control.GetState(ms == 0xFFFFFFFFu ? -1 : (long)ms, &s);
    *st = (int)s;
    return hr;
}

/* ---- the streams: IMediaSeeking and IMediaPosition share one engine in 100 ns units */
void Graph::renderers_locked(Vec<Renderer *> &v)
{
    for (size_t i = 0; i < filters.n; i++)
        if (Renderer *r = as_renderer(filters[i]->bf))
            if (r->connected()) v.push(r);
}
bool Graph::dur_locked(LL *d)
{
    Vec<Renderer *> rs;
    renderers_locked(rs);
    if (!rs.n) return false;
    LL best = 0;
    for (size_t i = 0; i < rs.n; i++) {
        LL x = (LL)((LL)rs[i]->total_frames() * 10000000 / rs[i]->src_rate());
        if (x > best) best = x;
    }
    *d = best;
    return true;
}
HR Graph::cur_stop_locked(LL *cur, LL *stop)
{
    Vec<Renderer *> rs;
    renderers_locked(rs);
    LL dur;
    if (!rs.n || !dur_locked(&dur)) return VFW_E_NOT_CONNECTED_;
    if (cur) *cur = (LL)((LL)rs[0]->position_frames() * 10000000 / rs[0]->src_rate());
    if (stop) *stop = stop_req < 0 || stop_req > dur ? dur : stop_req;
    return S_OK_;
}
HR Graph::seek100(const LL *start, int sflags, const LL *stop, int stflags, LL *rstart, LL *rstop)
{
    GL l(this);
    LL dur, cur, cstop;
    if (cur_stop_locked(&cur, &cstop) < 0 || !dur_locked(&dur)) return VFW_E_NOT_CONNECTED_;
    int sp = sflags & SEEK_PosMask, tp = stflags & SEEK_PosMask;
    LL ns = cur, nt = cstop;
    if (sp != SEEK_NoPositioning) {
        if (!start) return E_POINTER_;
        ns = sp == SEEK_Absolute ? *start : cur + *start;   /* relative and incremental: from the current position */
    }
    if (tp != SEEK_NoPositioning) {
        if (!stop) return E_POINTER_;
        nt = tp == SEEK_Absolute ? *stop : tp == SEEK_Relative ? cstop + *stop : ns + *stop;
    }
    if (ns < 0 || nt < 0) return E_INVALIDARG_;
    if (ns > dur) ns = dur;
    if (nt > dur) nt = dur;
    if (ns > nt) return VFW_E_START_TIME_AFTER_END_;
    if (sp != SEEK_NoPositioning || tp != SEEK_NoPositioning) {
        Vec<Renderer *> rs;
        renderers_locked(rs);
        for (size_t i = 0; i < rs.n; i++) {
            uint32_t r = rs[i]->src_rate();
            uint64_t f0 = (uint64_t)((LL)ns * r / 10000000), f1 = nt >= dur ? ~0ull : (uint64_t)((LL)nt * r / 10000000);
            rs[i]->seek_frames(f0, f1);
        }
        stop_req = nt >= dur ? -1 : nt;
        clear_completion_locked();
    }
    if (rstart) *rstart = ns;
    if (rstop) *rstop = nt;
    return S_OK_;
}
uint32_t first_rate(Graph *g)
{
    GL l(g);
    Vec<Renderer *> rs;
    g->renderers_locked(rs);
    return rs.n ? rs[0]->src_rate() : 0;
}
HR Seeking::GetCapabilities(uint32_t *c)
{
    if (!c) return E_POINTER_;
    GL l(g);
    LL d;
    *c = g->dur_locked(&d) ? SEEK_CanSeekAbsolute | SEEK_CanSeekForwards | SEEK_CanSeekBackwards | SEEK_CanGetCurrentPos | SEEK_CanGetStopPos | SEEK_CanGetDuration : 0;
    return S_OK_;
}
HR Seeking::CheckCapabilities(uint32_t *want)
{
    if (!want) return E_POINTER_;
    uint32_t have = 0, req = *want;
    GetCapabilities(&have);
    *want = req & have;   /* what is supported of what was asked */
    return *want == 0 ? E_FAIL_ : (*want == req ? S_OK_ : S_FALSE_);
}
HR Seeking::IsFormatSupported(const Guid *f)
{
    if (!f) return E_POINTER_;
    return geq(*f, TIME_FORMAT_MEDIA_TIME_) || geq(*f, TIME_FORMAT_SAMPLE_) ? S_OK_ : S_FALSE_;
}
HR Seeking::QueryPreferredFormat(Guid *f) { if (!f) return E_POINTER_; *f = TIME_FORMAT_MEDIA_TIME_; return S_OK_; }
HR Seeking::GetTimeFormat(Guid *f) { if (!f) return E_POINTER_; *f = g->fmt_sample ? TIME_FORMAT_SAMPLE_ : TIME_FORMAT_MEDIA_TIME_; return S_OK_; }
HR Seeking::IsUsingTimeFormat(const Guid *f)
{
    if (!f) return E_POINTER_;
    return geq(*f, g->fmt_sample ? TIME_FORMAT_SAMPLE_ : TIME_FORMAT_MEDIA_TIME_) ? S_OK_ : S_FALSE_;
}
HR Seeking::SetTimeFormat(const Guid *f)
{
    if (!f) return E_POINTER_;
    if (IsFormatSupported(f) != S_OK_) return E_INVALIDARG_;
    g->fmt_sample = geq(*f, TIME_FORMAT_SAMPLE_);
    return S_OK_;
}
/* positions in the current time format <-> 100 ns */
static LL from_100(Graph *g, LL t, uint32_t rate) { return g->fmt_sample && rate ? (LL)((LL)t * rate / 10000000) : t; }
HR Seeking::GetDuration(LL *d)
{
    if (!d) return E_POINTER_;
    LL x;
    {
        GL l(g);
        if (!g->dur_locked(&x)) return VFW_E_NOT_CONNECTED_;
    }
    *d = from_100(g, x, first_rate(g));
    return S_OK_;
}
HR Seeking::GetStopPosition(LL *s) { return s ? GetPositions(nullptr, s) : E_POINTER_; }
HR Seeking::GetCurrentPosition(LL *c) { return c ? GetPositions(c, nullptr) : E_POINTER_; }
HR Seeking::GetPositions(LL *c, LL *s)
{
    LL cur, stop;
    {
        GL l(g);
        HR hr = g->cur_stop_locked(&cur, &stop);
        if (hr < 0) return hr;
    }
    uint32_t r = first_rate(g);
    if (c) *c = from_100(g, cur, r);
    if (s) *s = from_100(g, stop, r);
    return S_OK_;
}
HR Seeking::GetAvailable(LL *early, LL *late)
{
    LL d = 0;
    HR hr = GetDuration(&d);   /* the whole stream is in memory */
    if (hr < 0) return hr;
    if (early) *early = 0;
    if (late) *late = d;
    return S_OK_;
}
HR Seeking::ConvertTimeFormat(LL *target, const Guid *tf, LL source, const Guid *sf)
{
    if (!target) return E_POINTER_;
    bool ts = tf ? geq(*tf, TIME_FORMAT_SAMPLE_) : g->fmt_sample, ss = sf ? geq(*sf, TIME_FORMAT_SAMPLE_) : g->fmt_sample;
    if ((tf && IsFormatSupported(tf) != S_OK_) || (sf && IsFormatSupported(sf) != S_OK_)) return E_INVALIDARG_;
    if (ts == ss) { *target = source; return S_OK_; }
    uint32_t r = first_rate(g);
    if (!r) return VFW_E_NOT_CONNECTED_;
    *target = ts ? (LL)((LL)source * r / 10000000) : (LL)((LL)source * 10000000 / r);
    return S_OK_;
}
HR Seeking::SetPositions(LL *cur, uint32_t cf, LL *stop, uint32_t sf)
{
    if ((cf & ~(uint32_t)(SEEK_PosMask | SEEK_KeyFrame | SEEK_ReturnTime | SEEK_Segment | SEEK_NoFlush)) ||
        (sf & ~(uint32_t)(SEEK_PosMask | SEEK_KeyFrame | SEEK_ReturnTime | SEEK_Segment | SEEK_NoFlush)))
        return E_INVALIDARG_;
    uint32_t r = first_rate(g);
    LL c = cur ? (g->fmt_sample && r ? (LL)((LL)*cur * 10000000 / r) : *cur) : 0;
    LL s = stop ? (g->fmt_sample && r ? (LL)((LL)*stop * 10000000 / r) : *stop) : 0;
    LL rc = 0, rs = 0;
    HR hr = g->seek100(&c, (int)cf, &s, (int)sf, &rc, &rs);
    if (hr < 0) return hr;
    if (cur && (cf & SEEK_ReturnTime)) *cur = from_100(g, rc, r);
    if (stop && (sf & SEEK_ReturnTime)) *stop = from_100(g, rs, r);
    return S_OK_;
}
HR Seeking::SetRate(double r)
{
    if (!(r > 0)) return E_INVALIDARG_;
    GL l(g);
    Vec<Renderer *> rs;
    g->renderers_locked(rs);
    double old = g->play_rate;
    for (size_t i = 0; i < rs.n; i++) {
        HR hr = rs[i]->set_play_rate(r);
        if (hr < 0) {   /* outside the voice's frequency range: undo what was applied */
            for (size_t k = 0; k < i; k++) rs[k]->set_play_rate(old);
            return hr;
        }
    }
    g->play_rate = r;
    return S_OK_;
}
HR Seeking::GetRate(double *r) { if (!r) return E_POINTER_; *r = g->play_rate; return S_OK_; }
HR Seeking::GetPreroll(LL *p) { if (!p) return E_POINTER_; *p = g->preroll; return S_OK_; }

HR Position::get_Duration(REFTIME *t)
{
    if (!t) return E_POINTER_;
    GL l(g);
    LL d;
    if (!g->dur_locked(&d)) return VFW_E_NOT_CONNECTED_;
    *t = (double)d / 1e7;
    return S_OK_;
}
HR Position::put_CurrentPosition(REFTIME t)
{
    if (t < 0) return E_INVALIDARG_;
    LL v = (LL)(t * 1e7 + 0.5);
    return g->seek100(&v, SEEK_Absolute, nullptr, SEEK_NoPositioning, nullptr, nullptr);
}
HR Position::get_CurrentPosition(REFTIME *t)
{
    if (!t) return E_POINTER_;
    GL l(g);
    LL c;
    HR hr = g->cur_stop_locked(&c, nullptr);
    if (hr >= 0) *t = (double)c / 1e7;
    return hr;
}
HR Position::get_StopTime(REFTIME *t)
{
    if (!t) return E_POINTER_;
    GL l(g);
    LL s;
    HR hr = g->cur_stop_locked(nullptr, &s);
    if (hr >= 0) *t = (double)s / 1e7;
    return hr;
}
HR Position::put_StopTime(REFTIME t)
{
    if (t < 0) return E_INVALIDARG_;
    LL v = (LL)(t * 1e7 + 0.5);
    return g->seek100(nullptr, SEEK_NoPositioning, &v, SEEK_Absolute, nullptr, nullptr);
}
HR Position::get_PrerollTime(REFTIME *t) { if (!t) return E_POINTER_; *t = (double)g->preroll / 1e7; return S_OK_; }
HR Position::put_PrerollTime(REFTIME t)
{
    if (t < 0) return E_INVALIDARG_;
    g->preroll = (LL)(t * 1e7 + 0.5);
    return S_OK_;
}
HR Position::put_Rate(double r) { return g->seeking.SetRate(r); }
HR Position::get_Rate(double *r) { return g->seeking.GetRate(r); }
HR Position::CanSeekForward(long *c)
{
    if (!c) return E_POINTER_;
    uint32_t caps = 0;
    g->seeking.GetCapabilities(&caps);
    if (!caps) return VFW_E_NOT_CONNECTED_;
    *c = (caps & SEEK_CanSeekForwards) ? -1 : 0;
    return S_OK_;
}
HR Position::CanSeekBackward(long *c)
{
    if (!c) return E_POINTER_;
    uint32_t caps = 0;
    g->seeking.GetCapabilities(&caps);
    if (!caps) return VFW_E_NOT_CONNECTED_;
    *c = (caps & SEEK_CanSeekBackwards) ? -1 : 0;
    return S_OK_;
}

/* ---- IBasicAudio: forwarded to the first connected audio renderer of the graph; with none, E_NOINTERFACE */
HR Graph::audio_target(Renderer **r)
{
    *r = nullptr;
    GL l(this);
    for (size_t i = 0; i < filters.n; i++)
        if (Renderer *x = as_renderer(filters[i]->bf)) {
            if (x->connected()) { *r = x; return S_OK_; }
        }
    return E_NOINTERFACE_;   /* no audio renderer: the interface has nothing to forward to */
}
HR Audio::put_Volume(long v)
{
    Renderer *r;
    if (v > 0 || v < -10000) return E_INVALIDARG_;
    HR hr = g->audio_target(&r);
    if (hr < 0) return hr;
    GL l(g);
    return r->set_volume(v);
}
HR Audio::get_Volume(long *v)
{
    if (!v) return E_POINTER_;
    Renderer *r;
    HR hr = g->audio_target(&r);
    if (hr < 0) return hr;
    GL l(g);
    *v = r->get_volume();
    return S_OK_;
}
HR Audio::put_Balance(long b)
{
    Renderer *r;
    if (b < -10000 || b > 10000) return E_INVALIDARG_;
    HR hr = g->audio_target(&r);
    if (hr < 0) return hr;
    GL l(g);
    return r->set_balance(b);
}
HR Audio::get_Balance(long *b)
{
    if (!b) return E_POINTER_;
    Renderer *r;
    HR hr = g->audio_target(&r);
    if (hr < 0) return hr;
    GL l(g);
    *b = r->get_balance();
    return S_OK_;
}

/* ---- events */
bool Graph::post_locked(const Ev &ev)
{
    long code = ev.code;
    queue.push(ev);
    if (evt) SetEvent(evt);
    if (code == EC_ERRORABORT_ || code == EC_USERABORT_) { latch = true; latch_code = code; }
    if (notify_hwnd && !(notify_flags & 1)) { win_pending++; return true; }
    return false;
}
void Graph::flush_window()
{
    uintptr_t h;
    long m;
    intptr_t inst;
    int n;
    {
        GL l(this);
        n = win_pending;
        win_pending = 0;
        h = notify_hwnd;
        m = notify_msg;
        inst = notify_inst;
    }
    for (int i = 0; i < n && h; i++) PostMessageA((HWND)h, (UINT)m, 0, (LPARAM)inst);
}
void Graph::completion_locked(BFilter *r)
{
    if (cancel_complete) {   /* default handling cancelled: every renderer's EC_COMPLETE reaches the application */
        event_locked(EC_COMPLETE_, S_OK_, (intptr_t)r);
        done.push((intptr_t)r);
    } else {
        for (size_t i = 0; i < done.n; i++)
            if (done[i] == (intptr_t)r) return;
        done.push((intptr_t)r);
    }
    Vec<Renderer *> rs;
    renderers_locked(rs);
    size_t finished = 0;
    for (size_t i = 0; i < rs.n; i++)
        if (rs[i]->is_complete()) finished++;
    if (finished < rs.n) return;
    latch = true;
    latch_code = EC_COMPLETE_;
    state = State_Stopped;   /* the stream is over: GetState reports it, and Run starts it again from the end's position */
    if (!cancel_complete) event_locked(EC_COMPLETE_, S_OK_, 0);   /* one EC_COMPLETE for the graph, not one per filter */
}
HR Sink::Notify(long code, intptr_t p1, intptr_t p2)
{
    /* EC_DEVICE_LOST carries an IUnknown in p1 that the queue owns until FreeEventParams. It is an interface pointer of the
     * sender: a face of ours or the game's own object, reached only through its proxy (combridge: the pointer alone decides which),
     * and its AddRef is a call into the game, so it is made before the lock. The game gets p1 back unchanged from GetEvent. */
    Unk *keep = nullptr;
    if (code == EC_DEVICE_LOST_ && p1) {
        int ix = nxcom::iface_for_iid((const uint8_t *)&IID_IUnknown_, 2);
        if (ix >= 0) keep = (Unk *)nxcom::from_game((const void *)p1, (unsigned)ix);
        if (keep) keep->AddRef();
    }
    {
        GL l(g);
        if (code == EC_COMPLETE_ && !g->cancel_complete) {   /* default handling: counted per reporting filter */
            for (size_t i = 0; i < g->done.n; i++)
                if (g->done[i] == p2 && p2) return S_OK_;
            if (p2) g->done.push(p2);
            Vec<Renderer *> rs;
            g->renderers_locked(rs);
            if (rs.n && g->done.n < rs.n) return S_OK_;
            g->latch = true;
            g->latch_code = EC_COMPLETE_;
        }
        g->post_locked(Ev{code, p1, p2, keep});
    }
    g->flush_window();
    return S_OK_;
}
HR Event::GetEventHandle(uintptr_t *h)
{
    if (!h) return E_POINTER_;
    *h = (uintptr_t)g->evt;
    return S_OK_;
}
HR Event::GetEvent(long *code, intptr_t *p1, intptr_t *p2, long ms)
{
    if (!code || !p1 || !p2) return E_POINTER_;
    uint64_t until = clk_peek_ns() + (ms < 0 ? 0 : (uint64_t)ms * 1000000ull);
    for (;;) {
        {
            GL l(g);
            if (g->queue.n) {
                Ev e = g->queue[0];
                g->queue.erase(0);
                if (e.keep) g->lent.push(e);
                if (!g->queue.n && g->evt) ResetEvent(g->evt);
                *code = e.code;
                *p1 = e.p1;
                *p2 = e.p2;
                return S_OK_;
            }
        }
        if (ms >= 0 && clk_peek_ns() >= until) return E_ABORT_;   /* no event */
        clk_sleep_ns(2 * 1000000ull);
    }
}
HR Event::WaitForCompletion(long ms, long *code)
{
    if (!code) return E_POINTER_;
    uint64_t until = clk_peek_ns() + (ms < 0 ? 0 : (uint64_t)ms * 1000000ull);
    for (;;) {
        {
            GL l(g);
            if (g->latch) { *code = g->latch_code; return S_OK_; }
        }
        if (ms >= 0 && clk_peek_ns() >= until) { *code = 0; return E_ABORT_; }
        clk_sleep_ns(2 * 1000000ull);
    }
}
HR Event::CancelDefaultHandling(long c)
{
    if (c != EC_COMPLETE_) return E_INVALIDARG_;   /* the only event this graph handles by default */
    GL l(g);
    if (g->cancel_complete) return S_FALSE_;
    g->cancel_complete = true;
    return S_OK_;
}
HR Event::RestoreDefaultHandling(long c)
{
    if (c != EC_COMPLETE_) return E_INVALIDARG_;
    GL l(g);
    if (!g->cancel_complete) return S_FALSE_;
    g->cancel_complete = false;
    return S_OK_;
}
HR Event::FreeEventParams(long code, intptr_t p1, intptr_t)
{
    Unk *keep = nullptr;
    {
        GL l(g);
        for (size_t i = 0; i < g->lent.n; i++)
            if (g->lent[i].code == code && g->lent[i].p1 == p1) { keep = g->lent[i].keep; g->lent.erase(i); break; }
    }
    if (keep) keep->Release();   /* a call into the game: outside the lock */
    return S_OK_;
}
HR Event::SetNotifyWindow(uintptr_t hwnd, long msg, intptr_t inst)
{
    GL l(g);
    g->notify_hwnd = hwnd;
    g->notify_msg = hwnd ? msg : 0;
    g->notify_inst = hwnd ? inst : 0;
    return S_OK_;
}
HR Event::SetNotifyFlags(long f)
{
    if (f & ~1L) return E_INVALIDARG_;   /* AM_MEDIAEVENT_NONOTIFY is the only flag */
    GL l(g);
    g->notify_flags = f;
    return S_OK_;
}
HR Event::GetNotifyFlags(long *f)
{
    if (!f) return E_POINTER_;
    GL l(g);
    *f = g->notify_flags;
    return S_OK_;
}

/* ---- site */
HR Site::SetSite(Unk *s)
{
    Hold *h = nullptr;
    if (s) {
        s->AddRef();   /* outside the lock */
        h = new (std::nothrow) Hold();
        if (!h) { s->Release(); return E_OUTOFMEMORY_; }
        h->u = s;
    }
    Hold *old;
    bool fin = false;
    {
        GL l(g);
        old = g->site;
        g->site = h;
        if (old) { old->retired = true; fin = old->use == 0; }
    }
    if (fin) g->finish(old);
    return S_OK_;
}
HR Site::GetSite(const void *iid, void **out)
{
    if (!out) return E_POINTER_;
    *out = nullptr;
    Hold *h;
    {
        GL l(g);
        h = g->site;
        if (h) h->use++;
    }
    if (!h) return E_FAIL_;
    HR hr = h->u->QueryInterface(iid, out);
    g->put(h);
    return hr;
}

/* ---- the pump: keeps the renderers' rings filled, reports the end of the stream */
void pump_step(void *p, uint32_t)   /* once per 10 ms of the clock */
{
    Graph *g = (Graph *)p;
    if (g->quit)
        return;
    {
        GL l(g);
        for (size_t i = 0; i < g->filters.n; i++)
            if (Renderer *r = as_renderer(g->filters[i]->bf)) r->tick_locked();
    }
    g->flush_window();
}
void Graph::start_pump()   /* graph lock held (a renderer connected) */
{
    if (pump) return;
    pump = clk_pump_start(pump_step, nullptr, this, 100, 1, 128 * 1024, 1, -1);
}
Graph::~Graph()
{
    refs = 1 << 20;   /* the teardown below takes and drops references of its own (QueryFilterInfo's graph pointer) */
    quit = 1;
    clk_pump_stop(pump);
    stop_all();
    while (filters.n) {   /* (no other thread uses a graph that is being destroyed) */
        Entry *e = filters[filters.n - 1];
        filters.erase(filters.n - 1);
        if (e->f) {
            disconnect_all(e->f);
            e->f->JoinFilterGraph(nullptr, nullptr);
        }
        e->u->Release();
        delete e;
    }
    if (clock) finish(clock);
    if (site) finish(site);
    for (size_t i = 0; i < queue.n; i++)
        if (queue[i].keep) queue[i].keep->Release();
    for (size_t i = 0; i < lent.n; i++)
        if (lent[i].keep) lent[i].keep->Release();
    if (evt) { clk_device_watch(evt, 0); CloseHandle(evt); }
}

/* ---- IDispatch members: each interface's vtable behaviour behind a name and a VARIANT convention */
static HR ret_r8(Var *res, double d) { if (!res) return E_POINTER_; res->vt = VT_R8_; res->d = d; return S_OK_; }
static HR ret_i4(Var *res, long v) { if (!res) return E_POINTER_; res->vt = VT_I4_; res->l = (int32_t)v; return S_OK_; }
#define NEED(cnt) if (a.n != (unsigned)(cnt)) return DISP_E_BADPARAMCOUNT_
HR inv_control(void *s, unsigned m, int, Args &a, Var *res)
{
    Control *c = (Control *)s;
    long ms, st = 0;
    const WCH *bs;
    switch (m) {
    case 0: NEED(0); return c->Run();
    case 1: NEED(0); return c->Pause();
    case 2: NEED(0); return c->Stop();
    case 3: {
        NEED(2);
        if (!a.i4(0, &ms)) return DISP_E_TYPEMISMATCH_;
        HR hr = c->GetState(ms, &st);
        return a.out_i4(1, st) ? hr : DISP_E_TYPEMISMATCH_;
    }
    case 4: NEED(1); return a.bstr(0, &bs) ? c->RenderFile(bs) : DISP_E_TYPEMISMATCH_;
    case 5: { NEED(2); void *o; return c->AddSourceFilter(nullptr, &o); }
    case 6: { NEED(0); void *o; return c->get_FilterCollection(&o); }
    case 7: { NEED(0); void *o; return c->get_RegFilterCollection(&o); }
    case 8: NEED(0); return c->StopWhenReady();
    }
    (void)res;
    return DISP_E_MEMBERNOTFOUND_;
}
HR inv_event(void *s, unsigned m, int, Args &a, Var *)
{
    Event *e = (Event *)s;
    long x, y, z, w;
    intptr_t p1 = 0, p2 = 0;
    uintptr_t h;
    switch (m) {
    case 0: { NEED(1); HR hr = e->GetEventHandle(&h); return a.out_i4(0, (long)h) ? hr : DISP_E_TYPEMISMATCH_; }
    case 1: {
        NEED(4);
        if (!a.i4(3, &w)) return DISP_E_TYPEMISMATCH_;
        long code = 0;
        HR hr = e->GetEvent(&code, &p1, &p2, w);
        if (hr < 0) return hr;
        return a.out_i4(0, code) && a.out_i4(1, (long)p1) && a.out_i4(2, (long)p2) ? hr : DISP_E_TYPEMISMATCH_;
    }
    case 2: {
        NEED(2);
        if (!a.i4(0, &x)) return DISP_E_TYPEMISMATCH_;
        long code = 0;
        HR hr = e->WaitForCompletion(x, &code);
        return a.out_i4(1, code) ? hr : DISP_E_TYPEMISMATCH_;
    }
    case 3: NEED(1); return a.i4(0, &x) ? e->CancelDefaultHandling(x) : DISP_E_TYPEMISMATCH_;
    case 4: NEED(1); return a.i4(0, &x) ? e->RestoreDefaultHandling(x) : DISP_E_TYPEMISMATCH_;
    case 5: NEED(3); return a.i4(0, &x) && a.i4(1, &y) && a.i4(2, &z) ? e->FreeEventParams(x, y, z) : DISP_E_TYPEMISMATCH_;
    }
    return DISP_E_MEMBERNOTFOUND_;
}
HR inv_position(void *s, unsigned m, int kind, Args &a, Var *res)
{
    Position *p = (Position *)s;
    double d = 0;
    long c = 0;
    HR hr;
    if (kind == DM_PUT) {
        NEED(1);
        if (!a.r8(0, &d)) return DISP_E_TYPEMISMATCH_;
        switch (m) {
        case 1: return p->put_CurrentPosition(d);
        case 2: return p->put_StopTime(d);
        case 3: return p->put_PrerollTime(d);
        case 4: return p->put_Rate(d);
        }
        return DISP_E_MEMBERNOTFOUND_;
    }
    NEED(0);
    switch (m) {
    case 0: hr = p->get_Duration(&d); break;
    case 1: hr = p->get_CurrentPosition(&d); break;
    case 2: hr = p->get_StopTime(&d); break;
    case 3: hr = p->get_PrerollTime(&d); break;
    case 4: hr = p->get_Rate(&d); break;
    case 5: hr = p->CanSeekForward(&c); return hr < 0 ? hr : ret_i4(res, c);
    case 6: hr = p->CanSeekBackward(&c); return hr < 0 ? hr : ret_i4(res, c);
    default: return DISP_E_MEMBERNOTFOUND_;
    }
    return hr < 0 ? hr : ret_r8(res, d);
}
HR inv_audio(void *s, unsigned m, int kind, Args &a, Var *res)
{
    Audio *au = (Audio *)s;
    long v = 0;
    if (kind == DM_PUT) {
        NEED(1);
        if (!a.i4(0, &v)) return DISP_E_TYPEMISMATCH_;
        return m == 0 ? au->put_Volume(v) : au->put_Balance(v);
    }
    NEED(0);
    HR hr = m == 0 ? au->get_Volume(&v) : au->get_Balance(&v);
    return hr < 0 ? hr : ret_i4(res, v);
}
#define DISP_IMPL(T, tbl, fn)                                                                                 \
    HR T::GetIDsOfNames(const void *riid, WCH **names, unsigned n, uint32_t, long *ids) { return disp_names(tbl, NDM(tbl), riid, names, n, ids); } \
    HR T::Invoke(long id, const void *riid, uint32_t, uint16_t fl, void *dp, void *res, void *ex, unsigned *ae)  \
    {                                                                                                         \
        if (!geq(riid, GUID_NULL_)) return DISP_E_UNKNOWNINTERFACE_;                                         \
        return disp_invoke(tbl, NDM(tbl), this, fn, id, fl, (DispParams *)dp, (Var *)res, (Excep *)ex, ae);  \
    }
DISP_IMPL(Control, kControlDM, inv_control)
DISP_IMPL(Event, kEventDM, inv_event)
DISP_IMPL(Position, kPositionDM, inv_position)
DISP_IMPL(Audio, kAudioDM, inv_audio)

}   // namespace

GraphCore *core_of(IFilterGraph_ *g) { return static_cast<Graph *>(static_cast<IFilterGraph2_ *>(g)); }

}   // namespace dsh

/* CoCreateInstance(CLSID_FilterGraph / CLSID_FilterMapper) (platform/input/register.cpp registers them) */
extern "C" HRESULT fnvnx_dshow_com_factory(const GUID *iid, void **out)
{
    using namespace dsh;
    if (!out) return E_POINTER_;
    *out = nullptr;
    Graph *g = new (std::nothrow) Graph();
    if (!g) return E_OUTOFMEMORY_;
    HR r = g->QueryInterface(iid, out);
    g->Release();
    return r;
}
extern "C" HRESULT fnvnx_dshow_mapper_factory(const GUID *iid, void **out)
{
    using namespace dsh;
    if (!out) return E_POINTER_;
    *out = nullptr;
    Mapper *m = new (std::nothrow) Mapper();
    if (!m) return E_OUTOFMEMORY_;
    HR r = m->QueryInterface(iid, out);
    m->Release();
    return r;
}
