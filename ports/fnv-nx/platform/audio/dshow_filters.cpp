/* dshow_filters.cpp - the installed DirectShow filters (MPEG-1 audio splitter, MP3 decoder, WAVE parser, DirectSound
 * renderer, and the file source the graph adds for AddSourceFilter), their pins and the enumerators.
 *
 * Each is a real IBaseFilter with real IPins: media types are negotiated through Connect / ReceiveConnection, a
 * connection is a pair of pins holding each other, and the pull path runs along the connections: the renderer pulls
 * PCM from the decoder or parser pin it is connected to, which reads the stream the splitter / parser loaded from
 * the source's IAsyncReader (Length, SyncRead; the whole stream is read once when the input pin connects, so the
 * game's reader is not touched while audio plays). The renderer feeds a looping mixer voice through a ring that the
 * graph's pump thread keeps filled.
 *
 * Lock order: Graph::lock (GraphCore::lock) > mixer lock. Renderer state and the decoders (the platform's own types) are
 * used with the graph lock held; the mixer never calls back. Connection and loading code runs without it, and nothing
 * here calls an interface method of a foreign object (a peer pin, the source's reader, the sync clock) under either lock. */
#define DSH_IMPL
#include "dshow_int.h"

#define MINIMP3_IMPLEMENTATION
#if !defined(__aarch64__)
#define MINIMP3_NO_SIMD   /* x86 hosts: the shim owns the SSE headers; AArch64 (the device) keeps NEON */
#endif
#define MINIMP3_NO_STDIO
#include "minimp3_ex.h"

#include <math.h>
#include <new>
#include <stdlib.h>

extern "C" {
HANDLE CreateEventA(void *, BOOL, BOOL, LPCSTR);
BOOL SetEvent(HANDLE);
BOOL ResetEvent(HANDLE);
DWORD WaitForSingleObject(HANDLE, DWORD);
HANDLE CreateSemaphoreA(void *, LONG, LONG, LPCSTR);
BOOL ReleaseSemaphore(HANDLE, LONG, LPLONG);
BOOL CloseHandle(HANDLE);
}

namespace dsh {

/* ---- utilities */
size_t wlen(const WCH *s) { size_t n = 0; while (s && s[n]) n++; return n; }
static WCH lowc(WCH c) { return c >= 'A' && c <= 'Z' ? (WCH)(c + 32) : c; }
bool weq_nocase(const WCH *a, const WCH *b)
{
    size_t i = 0;
    for (; a[i] && b[i]; i++)
        if (lowc(a[i]) != lowc(b[i])) return false;
    return a[i] == b[i];
}
void wcopy(WCH *dst, size_t cap, const WCH *src)
{
    size_t n = wlen(src);
    if (n >= cap) n = cap - 1;
    for (size_t i = 0; i < n; i++) dst[i] = src[i];
    dst[n] = 0;
}
void wfrom_ascii(WCH *dst, size_t cap, const char *s)
{
    size_t i = 0;
    for (; s[i] && i + 1 < cap; i++) dst[i] = (uint8_t)s[i];
    dst[i] = 0;
}
WCH *wdup_co(const WCH *s)
{
    size_t n = wlen(s);
    WCH *d = (WCH *)CoTaskMemAlloc((n + 1) * sizeof(WCH));
    if (d) memcpy(d, s, (n + 1) * sizeof(WCH));
    return d;
}
HR mt_copy(MT *d, const MT *s)
{
    *d = *s;
    d->punk = nullptr;
    d->pb = nullptr;
    if (s->cb && s->pb) {
        d->pb = (uint8_t *)CoTaskMemAlloc(s->cb);
        if (!d->pb) { d->cb = 0; return E_OUTOFMEMORY_; }
        memcpy(d->pb, s->pb, s->cb);
    } else
        d->cb = 0;
    return S_OK_;
}
void mt_free(MT *m)
{
    if (m->pb) CoTaskMemFree(m->pb);
    m->pb = nullptr;
    m->cb = 0;
}
void mt_wfx(MT *m, const Guid &sub, uint16_t tag, uint16_t ch, uint32_t rate, uint16_t bits)
{
    memset(m, 0, sizeof *m);
    m->major = MEDIATYPE_Audio_;
    m->sub = sub;
    m->format = FORMAT_WaveFormatEx_;
    uint16_t block = bits ? (uint16_t)(ch * bits / 8) : 1;
    m->fixed = tag == 1;
    m->sample_size = tag == 1 ? block : 0;
    m->cb = sizeof(WFX);
    m->pb = (uint8_t *)CoTaskMemAlloc(sizeof(WFX));
    if (!m->pb) { m->cb = 0; return; }
    WFX w = {tag, ch, rate, rate * block, block, bits, 0};
    memcpy(m->pb, &w, sizeof w);
}
Blob *blob_new(size_t n)
{
    Blob *b = (Blob *)malloc(sizeof(Blob) + n);
    if (b) { b->refs = 1; b->size = n; }
    return b;
}
Blob *blob_ref(Blob *b) { if (b) __atomic_add_fetch(&b->refs, 1, __ATOMIC_ACQ_REL); return b; }
void blob_unref(Blob *b)
{
    if (b && !__atomic_sub_fetch(&b->refs, 1, __ATOMIC_ACQ_REL)) free(b);
}

/* ---- stream sniffing: the graph's byte-pattern check (ID3v2 + MPEG-1/2 Layer III frame sync, RIFF....WAVE) */
static bool mp3_header(const uint8_t *p, uint32_t *hz, uint32_t *ch, uint32_t *len, uint32_t *kbps)
{
    static const uint16_t br1[16] = {0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0};
    static const uint16_t br2[16] = {0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0};
    static const uint32_t sr1[4] = {44100, 48000, 32000, 0};
    if (p[0] != 0xFF || (p[1] & 0xE0) != 0xE0) return false;
    unsigned ver = (p[1] >> 3) & 3, layer = (p[1] >> 1) & 3, bi = p[2] >> 4, si = (p[2] >> 2) & 3;
    if (ver < 2 || layer != 1 || !bi || bi == 15 || si == 3) return false;   /* MPEG-2 / MPEG-1, Layer III, a real bitrate */
    uint32_t rate = ver == 3 ? sr1[si] : sr1[si] / 2, k = ver == 3 ? br1[bi] : br2[bi];
    if (hz) *hz = rate;
    if (ch) *ch = ((p[3] >> 6) & 3) == 3 ? 1 : 2;
    if (kbps) *kbps = k;
    if (len) *len = (ver == 3 ? 144000u : 72000u) * k / rate + ((p[2] >> 1) & 1);
    return true;
}
typedef bool (*ReadFn)(void *ctx, uint64_t pos, size_t len, uint8_t *buf);
static StreamKind sniff_core(ReadFn rd, void *ctx, uint64_t total, uint32_t *hz, uint32_t *ch, uint32_t *kbps)
{
    uint8_t h[12];
    if (total < 4 || !rd(ctx, 0, total < 12 ? (size_t)total : 12, h)) return SK_UNKNOWN;
    if (total >= 12 && !memcmp(h, "RIFF", 4) && !memcmp(h + 8, "WAVE", 4)) return SK_WAVE;
    uint64_t o = 0;
    if (total >= 10 && !memcmp(h, "ID3", 3)) {
        uint8_t t[10];
        if (!rd(ctx, 0, 10, t)) return SK_UNKNOWN;
        o = 10 + (((uint64_t)(t[6] & 0x7F) << 21) | ((uint64_t)(t[7] & 0x7F) << 14) | ((uint64_t)(t[8] & 0x7F) << 7) | (t[9] & 0x7F)) +
            ((t[5] & 0x10) ? 10 : 0);
    }
    uint8_t f[4];
    uint32_t len = 0;
    if (o + 4 > total || !rd(ctx, o, 4, f) || !mp3_header(f, hz, ch, &len, kbps)) return SK_UNKNOWN;
    if (o + len + 4 <= total) {   /* the next frame must follow at once */
        uint8_t g[4];
        if (!rd(ctx, o + len, 4, g) || !mp3_header(g, nullptr, nullptr, nullptr, nullptr)) return SK_UNKNOWN;
    }
    return SK_MP3;
}
static bool rd_mem(void *c, uint64_t pos, size_t len, uint8_t *buf)
{
    Blob *b = (Blob *)c;
    if (pos + len > b->size) return false;
    memcpy(buf, b->data + pos, len);
    return true;
}
static bool rd_reader(void *c, uint64_t pos, size_t len, uint8_t *buf) { return ((AsyncReader *)c)->SyncRead((LL)pos, (long)len, buf) >= 0; }
StreamKind sniff_reader(AsyncReader *rd)
{
    LL total = 0, avail = 0;
    if (rd->Length(&total, &avail) < 0 || total <= 0) return SK_UNKNOWN;
    return sniff_core(rd_reader, rd, (uint64_t)total, nullptr, nullptr, nullptr);
}
HR read_all(AsyncReader *rd, Blob **out, volatile int *aborted)
{
    LL total = 0, avail = 0;
    HR hr = rd->Length(&total, &avail);
    if (hr < 0) return hr;
    if (total <= 0) return E_FAIL_;
    Blob *b = blob_new((size_t)total);
    if (!b) return E_OUTOFMEMORY_;
    for (LL at = 0; at < total;) {   /* in pieces the reader can serve */
        if (aborted && *aborted) { blob_unref(b); return E_ABORT_; }
        long n = total - at < (1 << 16) ? (long)(total - at) : (1 << 16);
        hr = rd->SyncRead(at, n, b->data + at);
        if (hr < 0) { blob_unref(b); return hr; }
        at += n;
    }
    *out = b;
    return S_OK_;
}

/* ---- enumerators */
namespace {
struct SnapEnum final : IEnum_ {
    int refs = 1;
    EnumKind kind;
    Vec<void *> items;
    size_t i = 0;
    explicit SnapEnum(EnumKind k) : kind(k) {}
    virtual ~SnapEnum()
    {
        for (size_t k = 0; k < items.n; k++) free_item(items[k]);
    }
    void free_item(void *p)
    {
        if (kind == EK_UNK) ((Unk *)p)->Release();
        else if (kind == EK_MT) { mt_free((MT *)p); free(p); }
        else { free(((RegFilter *)p)->name); free(p); }
    }
    void *copy_item(void *p)
    {
        if (kind == EK_UNK) { ((Unk *)p)->AddRef(); return p; }
        if (kind == EK_MT) {
            MT *m = (MT *)malloc(sizeof(MT));
            if (m && mt_copy(m, (MT *)p) < 0) { free(m); return nullptr; }
            return m;
        }
        RegFilter *r = (RegFilter *)malloc(sizeof(RegFilter));
        if (!r) return nullptr;
        r->clsid = ((RegFilter *)p)->clsid;
        size_t n = wlen(((RegFilter *)p)->name);
        r->name = (WCH *)malloc((n + 1) * sizeof(WCH));
        if (!r->name) { free(r); return nullptr; }
        memcpy(r->name, ((RegFilter *)p)->name, (n + 1) * sizeof(WCH));
        return r;
    }
    HR QueryInterface(const void *iid, void **o) override
    {
        if (!o) return E_POINTER_;
        *o = nullptr;
        if (!geq(iid, IID_IUnknown_) && !geq(iid, IID_IEnumPins_) && !geq(iid, IID_IEnumFilters_) && !geq(iid, IID_IEnumMediaTypes_) &&
            !geq(iid, IID_IEnumRegFilters_))
            return E_NOINTERFACE_;
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
    HR Next(uint32_t n, void **out, uint32_t *fetched) override
    {
        if (!out) return E_POINTER_;
        if (!fetched && n != 1) return E_INVALIDARG_;
        uint32_t k = 0;
        for (; k < n && i < items.n; k++, i++) {
            out[k] = copy_item(items[i]);
            if (!out[k]) { while (k) free_item(out[--k]); return E_OUTOFMEMORY_; }
        }
        if (fetched) *fetched = k;
        return k == n ? S_OK_ : S_FALSE_;
    }
    HR Skip(uint32_t n) override
    {
        if (i + n > items.n) { i = items.n; return S_FALSE_; }
        i += n;
        return S_OK_;
    }
    HR Reset() override { i = 0; return S_OK_; }
    HR Clone(void **o) override
    {
        if (!o) return E_POINTER_;
        SnapEnum *e = new (std::nothrow) SnapEnum(kind);
        if (!e) return E_OUTOFMEMORY_;
        for (size_t k = 0; k < items.n; k++) e->items.push(copy_item(items[k]));
        e->i = i;
        *o = e;
        return S_OK_;
    }
};
}   // namespace
IEnum_ *make_enum(EnumKind k, void **items, size_t n)
{
    SnapEnum *e = new (std::nothrow) SnapEnum(k);
    if (!e) return nullptr;
    for (size_t j = 0; j < n; j++) {
        void *c = e->copy_item(items[j]);
        if (!c || !e->items.push(c)) { e->Release(); return nullptr; }
    }
    return e;
}

/* ---- pins */
BPin::BPin(BFilter *o, int d, const char *n) : owner(o), dir(d)
{
    wfrom_ascii(name, 32, n);
}
BPin::~BPin()
{
    if (peer) peer->Release();
    mt_free(&cur);
    for (size_t i = 0; i < types.n; i++) mt_free(&types[i]);
    if (file) CloseHandle((HANDLE)file);
    if (flush_evt) CloseHandle((HANDLE)flush_evt);
}
void BPin::add_type(const Guid &major, const Guid &sub)
{
    MT m = {};
    m.major = major;
    m.sub = sub;
    m.format = FORMAT_None_;
    types.push(m);
}
void BPin::set_type(const MT &m)
{
    for (size_t i = 0; i < types.n; i++) mt_free(&types[i]);
    types.n = 0;
    MT c;
    if (mt_copy(&c, &m) >= 0) types.push(c);
}
HR BPin::QueryInterface(const void *iid, void **out)
{
    if (!out) return E_POINTER_;
    *out = nullptr;
    if (geq(iid, IID_IUnknown_) || geq(iid, IID_IPin_)) *out = static_cast<IPin_ *>(this);
    else if (geq(iid, IID_DshPin_)) *out = this;
    else if (geq(iid, IID_IAsyncReader_) && serves) *out = static_cast<AsyncReader *>(this);
    else return E_NOINTERFACE_;
    AddRef();
    return S_OK_;
}
unsigned long BPin::AddRef() { return owner->AddRef(); }
unsigned long BPin::Release() { return owner->Release(); }
BPin *internal_pin(IPin_ *p)
{
    void *o = nullptr;
    if (!p || p->QueryInterface(&IID_DshPin_, &o) < 0) return nullptr;
    ((BPin *)o)->Release();
    return (BPin *)o;
}
BFilter *internal_filter(Unk *f)
{
    void *o = nullptr;
    if (!f || f->QueryInterface(&IID_DshFilter_, &o) < 0) return nullptr;
    ((BFilter *)o)->Release();
    return (BFilter *)o;
}

static bool type_match(const MT &want, const MT *t)   /* `t` offered/accepted; `want` asked for */
{
    if (!geq(want.major, GUID_NULL_) && !geq(want.major, t->major)) return false;
    return geq(want.sub, GUID_NULL_) || geq(t->sub, GUID_NULL_) || geq(want.sub, t->sub);
}
bool BFilter::accepts(BPin *p, const MT *mt)
{
    for (size_t i = 0; i < p->types.n; i++) {
        MT *t = &p->types[i];
        if (!geq(mt->major, t->major)) continue;
        if (geq(mt->sub, t->sub)) return true;
        if (geq(mt->sub, GUID_NULL_) && geq(t->major, MEDIATYPE_Stream_)) return true;   /* a stream of a type to be sniffed */
    }
    return false;
}
HR BPin::QueryAccept(const MT *mt)
{
    if (!mt) return E_POINTER_;
    return owner->accepts(this, mt) ? S_OK_ : S_FALSE_;
}
HR BPin::EnumMediaTypes(void **out)
{
    if (!out) return E_POINTER_;
    Vec<void *> v;
    for (size_t i = 0; i < types.n; i++) v.push(&types[i]);
    IEnum_ *e = make_enum(EK_MT, v.p, v.n);
    *out = e;
    return e ? S_OK_ : E_OUTOFMEMORY_;
}

HR BPin::Connect(IPin_ *r, const MT *want)
{
    if (!r) return E_POINTER_;
    if (dir != PINDIR_OUTPUT) return E_UNEXPECTED_;
    if (peer) return VFW_E_ALREADY_CONNECTED_;
    if (owner->fstate != State_Stopped) return VFW_E_NOT_STOPPED_;
    int rd = -1;
    if (r->QueryDirection(&rd) < 0 || rd != PINDIR_INPUT) return VFW_E_INVALID_DIRECTION_;
    /* candidates: a fully specified type alone, else ours then the receiver's, filtered by a partial one */
    Vec<MT> cand;
    bool explicit_type = want && !geq(want->major, GUID_NULL_) && !geq(want->sub, GUID_NULL_);
    HR last = VFW_E_NO_ACCEPTABLE_TYPES_;
    if (explicit_type) {
        MT c;
        if (mt_copy(&c, want) >= 0) cand.push(c);
        last = VFW_E_TYPE_NOT_ACCEPTED_;
    } else {
        MT any = {};
        const MT &w = want ? *want : any;
        for (size_t i = 0; i < types.n; i++)
            if (type_match(w, &types[i])) {
                MT c;
                if (mt_copy(&c, &types[i]) >= 0) cand.push(c);
            }
        void *e = nullptr;
        if (r->EnumMediaTypes(&e) >= 0 && e) {
            MT *m;
            uint32_t got;
            while (((IEnum_ *)e)->Next(1, (void **)&m, &got) == 0 && got == 1) {
                MT c;
                if (type_match(w, m) && mt_copy(&c, m) >= 0) cand.push(c);
                mt_free(m);
                CoTaskMemFree(m);
            }
            ((Unk *)e)->Release();
        }
    }
    HR hr = last;
    for (size_t i = 0; i < cand.n && hr < 0; i++) {
        if (QueryAccept(&cand[i]) != S_OK_) continue;
        hr = last;
        hr = r->ReceiveConnection(this, &cand[i]);
        if (hr >= 0) {
            peer = r;
            r->AddRef();
            mt_free(&cur);
            mt_copy(&cur, &cand[i]);
            if (owner->graph) owner->graph->bump_version();
        } else if (hr == VFW_E_ALREADY_CONNECTED_ || hr == VFW_E_NOT_STOPPED_ || hr == E_OUTOFMEMORY_ || hr == E_ABORT_ || explicit_type)
            last = hr;   /* a reason that no other type can cure (an explicit type reports its own failure) */
    }
    for (size_t i = 0; i < cand.n; i++) mt_free(&cand[i]);
    return hr >= 0 ? S_OK_ : last;
}
HR BPin::ReceiveConnection(IPin_ *c, const MT *mt)
{
    if (!c || !mt) return E_POINTER_;
    if (dir != PINDIR_INPUT) return VFW_E_INVALID_DIRECTION_;
    if (peer) return VFW_E_ALREADY_CONNECTED_;
    if (owner->fstate != State_Stopped) return VFW_E_NOT_STOPPED_;
    int cd = -1;
    if (c->QueryDirection(&cd) < 0 || cd != PINDIR_OUTPUT) return VFW_E_INVALID_DIRECTION_;
    if (QueryAccept(mt) != S_OK_) return VFW_E_TYPE_NOT_ACCEPTED_;
    HR hr = as_stage(owner)->input_connected(this, c, mt);
    if (hr < 0) return hr;
    peer = c;
    c->AddRef();
    mt_free(&cur);
    mt_copy(&cur, mt);
    if (owner->graph) owner->graph->bump_version();
    return S_OK_;
}
HR BPin::Disconnect()
{
    if (!peer) return S_FALSE_;
    if (owner->fstate != State_Stopped) return VFW_E_NOT_STOPPED_;
    IPin_ *p = peer;
    peer = nullptr;
    mt_free(&cur);
    if (dir == PINDIR_INPUT) as_stage(owner)->input_disconnected(this);
    if (owner->graph) owner->graph->bump_version();
    p->Release();   /* last: it may free the filter on the other side */
    return S_OK_;
}
HR BPin::ConnectedTo(IPin_ **p)
{
    if (!p) return E_POINTER_;
    *p = peer;
    if (!peer) return VFW_E_NOT_CONNECTED_;
    peer->AddRef();
    return S_OK_;
}
HR BPin::ConnectionMediaType(MT *m)
{
    if (!m) return E_POINTER_;
    if (!peer) { memset(m, 0, sizeof *m); return VFW_E_NOT_CONNECTED_; }
    return mt_copy(m, &cur);
}
HR BPin::QueryPinInfo(PinInfo *pi)
{
    if (!pi) return E_POINTER_;
    pi->filter = static_cast<IBaseFilter_ *>(owner);
    owner->AddRef();
    pi->dir = dir;
    wcopy(pi->name, 128, name);
    return S_OK_;
}
HR BPin::QueryDirection(int *d) { if (!d) return E_POINTER_; *d = dir; return S_OK_; }
HR BPin::QueryId(WCH **id)
{
    if (!id) return E_POINTER_;
    *id = wdup_co(name);
    return *id ? S_OK_ : E_OUTOFMEMORY_;
}
HR BPin::QueryInternalConnections(IPin_ **out, uint32_t *n)
{
    if (!n) return E_POINTER_;
    uint32_t want = 0;
    for (size_t i = 0; i < owner->pins.n; i++)
        if (owner->pins[i]->dir != dir) want++;
    if (*n < want || (want && !out)) { *n = want; return VFW_E_BUFFER_OVERFLOW_; }
    uint32_t k = 0;
    for (size_t i = 0; i < owner->pins.n; i++)
        if (owner->pins[i]->dir != dir) {
            out[k] = static_cast<IPin_ *>(owner->pins[i]);
            out[k++]->AddRef();
        }
    *n = k;
    return S_OK_;
}
/* the stream-control calls travel downstream; a renderer acts on them (below) */
HR BPin::EndOfStream()
{
    if (dir != PINDIR_INPUT) return E_UNEXPECTED_;
    if (!peer) return VFW_E_NOT_CONNECTED_;
    if (Renderer *r = as_renderer(owner)) return r->stream_event(0, 0);
    HR hr = S_OK_;
    for (size_t i = 0; i < owner->pins.n; i++)
        if (owner->pins[i]->dir == PINDIR_OUTPUT && owner->pins[i]->peer) {
            HR h = owner->pins[i]->peer->EndOfStream();
            if (h < 0) hr = h;
        }
    return hr;
}
HR BPin::BeginFlush()
{
    if (serves) {   /* IAsyncReader::BeginFlush (filesource.c FileAsyncReader_BeginFlush): drop the requests, wake WaitForNext */
        os_lock(&rlock);
        flushing = true;
        reqs.n = 0;
        if (!flush_evt) flush_evt = (uintptr_t)CreateEventA(nullptr, 1, 0, nullptr);
        if (flush_evt) SetEvent((HANDLE)flush_evt);
        os_unlock(&rlock);
        return S_OK_;
    }
    if (dir != PINDIR_INPUT) return E_UNEXPECTED_;
    if (Renderer *r = as_renderer(owner)) return r->stream_event(1, 0);
    HR hr = S_OK_;
    for (size_t i = 0; i < owner->pins.n; i++)
        if (owner->pins[i]->dir == PINDIR_OUTPUT && owner->pins[i]->peer) hr = owner->pins[i]->peer->BeginFlush();
    return hr;
}
HR BPin::EndFlush()
{
    if (serves) {   /* FileAsyncReader_EndFlush */
        os_lock(&rlock);
        flushing = false;
        if (flush_evt) ResetEvent((HANDLE)flush_evt);
        os_unlock(&rlock);
        return S_OK_;
    }
    if (dir != PINDIR_INPUT) return E_UNEXPECTED_;
    if (Renderer *r = as_renderer(owner)) return r->stream_event(2, 0);
    HR hr = S_OK_;
    for (size_t i = 0; i < owner->pins.n; i++)
        if (owner->pins[i]->dir == PINDIR_OUTPUT && owner->pins[i]->peer) hr = owner->pins[i]->peer->EndFlush();
    return hr;
}
HR BPin::NewSegment(LL start, LL stop, double rate)
{
    if (dir != PINDIR_INPUT) return E_UNEXPECTED_;
    if (rate <= 0) return E_INVALIDARG_;
    seg_start = start;
    seg_stop = stop;
    seg_rate = rate;
    if (Renderer *r = as_renderer(owner)) return r->stream_event(3, rate);
    HR hr = S_OK_;
    for (size_t i = 0; i < owner->pins.n; i++)
        if (owner->pins[i]->dir == PINDIR_OUTPUT && owner->pins[i]->peer) hr = owner->pins[i]->peer->NewSegment(start, stop, rate);
    return hr;
}
/* a file source's IAsyncReader (wine-9.0:dlls/quartz/filesource.c, FileAsyncReader_*). The layer's file read is
 * complete when it returns, so a request is read when it is made (ReadFile on an overlapped handle may complete at once:
 * FileAsyncReader_Request takes that branch) and WaitForNext hands completed requests back; the request list grows with
 * the samples the caller has outstanding. Calls into the caller's samples are made with no platform lock held. */
#define BYTES_FROM_MEDIATIME_(t) ((t) / 10000000)
#define MEDIATIME_FROM_BYTES_(b) ((LL)(b) * 10000000)
bool BPin::rd_at(LL pos, long len, uint8_t *buf, uint32_t *got, uint32_t *err)
{
    *got = 0;
    if (!file) { *err = 6; return false; }                      /* ERROR_INVALID_HANDLE */
    if (len > 0 && pos >= fsize) { *err = 38; return false; }   /* ERROR_HANDLE_EOF: an overlapped read at or past the end */
    LARGE_INTEGER li;
    li.QuadPart = pos;
    if (!SetFilePointerEx((HANDLE)file, li, nullptr, 0)) { *err = GetLastError(); return false; }
    DWORD n = 0;
    if (!ReadFile((HANDLE)file, buf, (DWORD)len, &n, nullptr)) { *err = GetLastError(); return false; }
    *got = n;
    return true;
}
HR BPin::RequestAllocator(void *preferred_, void *props_, void **ret)
{
    IMemAllocator_ *preferred = (IMemAllocator_ *)preferred_;
    AllocProps *props = (AllocProps *)props_;
    if (!props || !ret) return E_POINTER_;
    if (!props->cbAlign) props->cbAlign = 1;
    *ret = nullptr;
    IMemAllocator_ *a;
    if (preferred) { preferred->AddRef(); a = preferred; }
    else if (!(a = make_mem_allocator())) return E_OUTOFMEMORY_;
    HR hr = a->SetProperties(props, props);
    if (hr < 0) { a->Release(); return hr; }
    *ret = a;
    return S_OK_;
}
HR BPin::Request(void *sample_, uintptr_t cookie)
{
    IMediaSample_ *sample = (IMediaSample_ *)sample_;
    if (!sample) return E_POINTER_;
    LL start, end;
    HR hr = sample->GetTime(&start, &end);
    if (hr < 0) return hr;
    if (BYTES_FROM_MEDIATIME_(start) >= fsize) return HR_ERROR_HANDLE_EOF_;
    uint8_t *data;
    hr = sample->GetPointer(&data);
    if (hr < 0) return hr;
    os_lock(&rlock);
    if (flushing) { os_unlock(&rlock); return VFW_E_WRONG_STATE_; }
    uint32_t got = 0, err = 0;
    if (!rd_at(BYTES_FROM_MEDIATIME_(start), (long)BYTES_FROM_MEDIATIME_(end - start), data, &got, &err)) {
        os_unlock(&rlock);
        return (HR)(0x80070000u | (err & 0xFFFF));   /* HRESULT_FROM_WIN32 */
    }
    Req r = {sample, cookie, BYTES_FROM_MEDIATIME_(start), got};
    hr = reqs.push(r) ? S_OK_ : E_OUTOFMEMORY_;
    os_unlock(&rlock);
    return hr;
}
HR BPin::WaitForNext(uint32_t timeout, void **sample, uintptr_t *cookie)
{
    if (!sample || !cookie) return E_POINTER_;
    *sample = nullptr;
    *cookie = 0;
    for (;;) {
        os_lock(&rlock);
        if (flushing) { os_unlock(&rlock); return VFW_E_WRONG_STATE_; }
        if (reqs.n) {
            Req r = reqs.p[0];
            for (size_t i = 1; i < reqs.n; i++) reqs.p[i - 1] = reqs.p[i];
            reqs.n--;
            os_unlock(&rlock);
            IMediaSample_ *s = (IMediaSample_ *)r.sample;
            s->SetActualDataLength((int32_t)r.size);
            LL st = MEDIATIME_FROM_BYTES_(r.pos), en = st + MEDIATIME_FROM_BYTES_(r.size);
            s->SetTime(&st, &en);
            *sample = s;
            *cookie = r.cookie;
            return S_OK_;
        }
        if (!flush_evt) flush_evt = (uintptr_t)CreateEventA(nullptr, 1, 0, nullptr);
        HANDLE ev = (HANDLE)flush_evt;
        os_unlock(&rlock);
        if (!ev) return E_OUTOFMEMORY_;
        if (WaitForSingleObject(ev, timeout) != 0) return VFW_E_TIMEOUT_;   /* WAIT_OBJECT_0: a flush began */
    }
}
HR BPin::SyncReadAligned(void *sample_)
{
    IMediaSample_ *sample = (IMediaSample_ *)sample_;
    if (!sample) return E_POINTER_;
    LL st, en;
    HR hr = sample->GetTime(&st, &en);
    if (hr < 0) return hr;
    uint8_t *buf;
    hr = sample->GetPointer(&buf);
    if (hr < 0) return hr;
    long length = (long)BYTES_FROM_MEDIATIME_(en - st);
    uint32_t got = 0, err = 0;
    if (rd_at(BYTES_FROM_MEDIATIME_(st), length, buf, &got, &err)) hr = ((long)got == length) ? S_OK_ : S_FALSE_;
    else if (err == 38) hr = S_OK_;
    else return (HR)(0x80070000u | (err & 0xFFFF));
    sample->SetActualDataLength((int32_t)got);   /* filesource.c ignores its result */
    return hr;
}
HR BPin::SyncRead(LL pos, long len, uint8_t *buf)
{
    if (!file || pos < 0 || len < 0 || pos + len > fsize) return E_FAIL_;
    LARGE_INTEGER li;
    li.QuadPart = pos;
    if (!SetFilePointerEx((HANDLE)file, li, nullptr, 0)) return E_FAIL_;
    DWORD got = 0;
    if (!ReadFile((HANDLE)file, buf, (DWORD)len, &got, nullptr) || (long)got != len) return E_FAIL_;
    return S_OK_;
}
HR BPin::Length(LL *t, LL *a)
{
    if (!t || !a) return E_POINTER_;
    *t = *a = fsize;
    return file ? S_OK_ : E_FAIL_;
}

/* ---- the memory allocator and its samples (wine-9.0:dlls/quartz/memallocator.c: BaseMemAllocator_*, StdMediaSample2_*,
 * StdMemAllocator_Alloc / _Free; the buffer layout is the SDK allocator's: each sample takes cbBuffer + cbPrefix rounded up
 * to cbAlign, the buffer starts after the prefix). One lock guards the lists; no call leaves the object under it. */
namespace {
const uint32_t AM_SAMPLE_SPLICEPOINT_ = 1, AM_SAMPLE_PREROLL_ = 2, AM_SAMPLE_DATADISCONTINUITY_ = 4, AM_SAMPLE_TYPECHANGED_ = 8,
               AM_SAMPLE_TIMEVALID_ = 0x10, AM_SAMPLE_STOPVALID_ = 0x100, AM_GBF_NOWAIT_ = 4;
struct SampleProps {   /* AM_SAMPLE2_PROPERTIES */
    uint32_t cbData, dwTypeSpecificFlags, dwSampleFlags;
    int32_t lActual;
    LL tStart, tStop;
    uint32_t dwStreamId;
    MT *pMediaType;
    uint8_t *pbBuffer;
    int32_t cbBuffer;
};
const size_t PROP_WRITABLE_ = offsetof(SampleProps, pbBuffer);
struct MemAlloc;
struct MemSample final : IMediaSample_ {
    int refs = 0;
    SampleProps props = {};
    MemAlloc *parent;
    LL media_start = 0, media_end = 0;
    bool media_valid = false;
    explicit MemSample(MemAlloc *p) : parent(p) {}
    HR QueryInterface(const void *iid, void **out) override
    {
        if (!out) return E_POINTER_;
        *out = nullptr;
        if (!geq(iid, IID_IUnknown_) && !geq(iid, IID_IMediaSample_) && !geq(iid, IID_IMediaSample2_)) return E_NOINTERFACE_;
        *out = static_cast<IMediaSample_ *>(this);
        AddRef();
        return S_OK_;
    }
    unsigned long AddRef() override { return (unsigned long)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    unsigned long Release() override;
    void drop_type()
    {
        if (props.pMediaType) { mt_free(props.pMediaType); CoTaskMemFree(props.pMediaType); props.pMediaType = nullptr; }
    }
    HR GetPointer(uint8_t **b) override { if (!b) return E_POINTER_; *b = props.pbBuffer; return *b ? S_OK_ : E_FAIL_; }
    int32_t GetSize() override { return props.cbBuffer; }
    HR GetTime(LL *s, LL *e) override
    {
        if (!(props.dwSampleFlags & AM_SAMPLE_TIMEVALID_)) return VFW_E_SAMPLE_TIME_NOT_SET_;
        if (!s || !e) return E_POINTER_;
        if (!(props.dwSampleFlags & AM_SAMPLE_STOPVALID_)) { *s = props.tStart; *e = props.tStart + 1; return VFW_S_NO_STOP_TIME_; }
        *s = props.tStart;
        *e = props.tStop;
        return S_OK_;
    }
    HR SetTime(LL *s, LL *e) override
    {
        if (s) {
            props.tStart = *s;
            props.dwSampleFlags |= AM_SAMPLE_TIMEVALID_;
            if (e) { props.tStop = *e; props.dwSampleFlags |= AM_SAMPLE_STOPVALID_; }
            else props.dwSampleFlags &= ~AM_SAMPLE_STOPVALID_;
        } else
            props.dwSampleFlags &= ~(AM_SAMPLE_TIMEVALID_ | AM_SAMPLE_STOPVALID_);
        return S_OK_;
    }
    HR flag_get(uint32_t f) { return (props.dwSampleFlags & f) ? S_OK_ : S_FALSE_; }
    void flag_set(uint32_t f, int on) { if (on) props.dwSampleFlags |= f; else props.dwSampleFlags &= ~f; }
    HR IsSyncPoint() override { return flag_get(AM_SAMPLE_SPLICEPOINT_); }
    HR SetSyncPoint(int on) override { flag_set(AM_SAMPLE_SPLICEPOINT_, on); return S_OK_; }
    HR IsPreroll() override { return flag_get(AM_SAMPLE_PREROLL_); }
    HR SetPreroll(int on) override { flag_set(AM_SAMPLE_PREROLL_, on); return S_OK_; }
    int32_t GetActualDataLength() override { return props.lActual; }
    HR SetActualDataLength(int32_t n) override
    {
        if (n > props.cbBuffer || n < 0) return VFW_E_BUFFER_OVERFLOW_;
        props.lActual = n;
        return S_OK_;
    }
    HR GetMediaType(MT **pp) override
    {
        if (!props.pMediaType) { if (pp) *pp = nullptr; return S_FALSE_; }
        if (!pp) return E_POINTER_;
        MT *m = (MT *)CoTaskMemAlloc(sizeof(MT));
        if (!m) return E_OUTOFMEMORY_;
        HR hr = mt_copy(m, props.pMediaType);
        if (hr < 0) { CoTaskMemFree(m); return hr; }
        *pp = m;
        return S_OK_;
    }
    HR SetMediaType(MT *m) override
    {
        drop_type();
        if (!m) { props.dwSampleFlags &= ~AM_SAMPLE_TYPECHANGED_; return S_OK_; }
        props.dwSampleFlags |= AM_SAMPLE_TYPECHANGED_;
        MT *c = (MT *)CoTaskMemAlloc(sizeof(MT));
        if (!c) return E_OUTOFMEMORY_;
        HR hr = mt_copy(c, m);
        if (hr < 0) { CoTaskMemFree(c); return hr; }
        props.pMediaType = c;
        return S_OK_;
    }
    HR IsDiscontinuity() override { return flag_get(AM_SAMPLE_DATADISCONTINUITY_); }
    HR SetDiscontinuity(int on) override { flag_set(AM_SAMPLE_DATADISCONTINUITY_, on); return S_OK_; }
    HR GetMediaTime(LL *s, LL *e) override
    {
        if (!media_valid) return VFW_E_MEDIA_TIME_NOT_SET_;
        if (!s || !e) return E_POINTER_;
        *s = media_start;
        *e = media_end;
        return S_OK_;
    }
    HR SetMediaTime(LL *s, LL *e) override
    {
        if (s) {
            if (!e) return E_POINTER_;
            media_start = *s;
            media_end = *e;
            media_valid = true;
        } else
            media_valid = false;
        return S_OK_;
    }
    HR GetProperties(uint32_t cb, uint8_t *pb) override
    {
        if (!pb) return E_POINTER_;
        memcpy(pb, &props, cb < sizeof props ? cb : sizeof props);
        return S_OK_;
    }
    HR SetProperties(uint32_t cb, const uint8_t *pb) override
    {
        if (!pb) return E_POINTER_;
        memcpy(&props, pb, cb < PROP_WRITABLE_ ? cb : PROP_WRITABLE_);   /* pbBuffer and cbBuffer are read-only */
        return S_OK_;
    }
};
struct MemAlloc final : IMemAllocator_ {
    int refs = 1;
    AllocProps props = {};
    Vec<MemSample *> free_list, used_list;
    uint8_t *memory = nullptr, *base = nullptr;
    bool committed = false, decommit_queued = false;
    int waiting = 0;
    uintptr_t sem = 0;
    os_lock_t lk = {0};
    ~MemAlloc() { if (sem) CloseHandle((HANDLE)sem); }
    HR QueryInterface(const void *iid, void **out) override
    {
        if (!out) return E_POINTER_;
        *out = nullptr;
        if (!geq(iid, IID_IUnknown_) && !geq(iid, IID_IMemAllocator_)) return E_NOINTERFACE_;
        *out = static_cast<IMemAllocator_ *>(this);
        AddRef();
        return S_OK_;
    }
    unsigned long AddRef() override { return (unsigned long)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    unsigned long Release() override
    {
        int r = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (!r) {
            if (committed) free_buffers();
            delete this;
        }
        return (unsigned long)r;
    }
    void remove_used(MemSample *s)
    {
        for (size_t i = 0; i < used_list.n; i++)
            if (used_list.p[i] == s) { used_list.p[i] = used_list.p[--used_list.n]; return; }
    }
    int32_t stride() const
    {
        int64_t sz = (int64_t)props.cbBuffer + props.cbPrefix;
        int64_t r = sz % props.cbAlign;
        return (int32_t)(r ? sz + props.cbAlign - r : sz);
    }
    HR alloc_buffers()
    {
        if (4096 % props.cbAlign != 0) return VFW_E_BADALIGN_;   /* no coarser alignment than the page size */
        int64_t total = (int64_t)stride() * props.cBuffers;
        if (total <= 0 || total + props.cbAlign > 0x7fffffff) return E_OUTOFMEMORY_;
        memory = (uint8_t *)malloc((size_t)total + (size_t)props.cbAlign);
        if (!memory) return E_OUTOFMEMORY_;
        uintptr_t a = ((uintptr_t)memory + (uintptr_t)props.cbAlign - 1) / (uintptr_t)props.cbAlign * (uintptr_t)props.cbAlign;
        base = (uint8_t *)a;
        for (int32_t i = props.cBuffers - 1; i >= 0; i--) {
            MemSample *s = new (std::nothrow) MemSample(this);
            if (!s || !free_list.push(s)) {
                delete s;
                free_buffers_locked();
                return E_OUTOFMEMORY_;
            }
            s->props.cbData = sizeof(SampleProps);
            s->props.cbBuffer = s->props.lActual = props.cbBuffer;
            s->props.pbBuffer = base + (size_t)i * (size_t)stride() + (size_t)props.cbPrefix;
        }
        return S_OK_;
    }
    void free_buffers_locked()
    {
        for (size_t i = 0; i < used_list.n; i++) used_list.p[i]->parent = nullptr;   /* outstanding samples outlive the buffers' owner list */
        used_list.n = 0;
        for (size_t i = 0; i < free_list.n; i++) { free_list.p[i]->drop_type(); delete free_list.p[i]; }
        free_list.n = 0;
        free(memory);
        memory = base = nullptr;
    }
    void free_buffers() { free_buffers_locked(); }
    HR SetProperties(AllocProps *req, AllocProps *act) override
    {
        if (!req || !act) return E_POINTER_;
        os_lock(&lk);
        HR hr;
        if (used_list.n) hr = VFW_E_BUFFERS_OUTSTANDING_;
        else if (committed) hr = VFW_E_ALREADY_COMMITTED_;
        else if (req->cbAlign == 0) hr = VFW_E_BADALIGN_;
        else { props = *req; *act = props; hr = S_OK_; }
        os_unlock(&lk);
        return hr;
    }
    HR GetProperties(AllocProps *p) override
    {
        if (!p) return E_POINTER_;
        os_lock(&lk);
        *p = props;
        os_unlock(&lk);
        return S_OK_;
    }
    HR Commit() override
    {
        os_lock(&lk);
        HR hr;
        if (props.cbAlign <= 0) hr = VFW_E_BADALIGN_;
        else if (props.cbBuffer <= 0) hr = VFW_E_SIZENOTSET_;
        else if (props.cBuffers <= 0) hr = VFW_E_BUFFER_NOTSET_;
        else if (decommit_queued && committed) { decommit_queued = false; hr = S_OK_; }
        else if (committed) hr = S_OK_;
        else {
            sem = (uintptr_t)CreateSemaphoreA(nullptr, props.cBuffers, props.cBuffers, nullptr);
            if (!sem) hr = E_OUTOFMEMORY_;
            else {
                hr = alloc_buffers();
                if (hr >= 0) committed = true;
                else { CloseHandle((HANDLE)sem); sem = 0; }
            }
        }
        os_unlock(&lk);
        return hr;
    }
    HR Decommit() override
    {
        os_lock(&lk);
        HR hr = S_OK_;
        if (committed) {
            if (used_list.n) {
                decommit_queued = true;
                ReleaseSemaphore((HANDLE)sem, waiting, nullptr);   /* every waiting GetBuffer learns that none will come */
            } else {
                committed = false;
                CloseHandle((HANDLE)sem);
                sem = 0;
                free_buffers_locked();
            }
        }
        os_unlock(&lk);
        return hr;
    }
    HR GetBuffer(IMediaSample_ **out, LL *, LL *, uint32_t flags) override
    {
        if (!out) return E_POINTER_;
        *out = nullptr;
        os_lock(&lk);
        if (!committed || decommit_queued) { os_unlock(&lk); return VFW_E_NOT_COMMITTED_; }
        waiting++;
        HANDLE h = (HANDLE)sem;
        os_unlock(&lk);
        if (WaitForSingleObject(h, (flags & AM_GBF_NOWAIT_) ? 0 : 0xFFFFFFFFu) != 0) {
            os_lock(&lk);
            waiting--;
            os_unlock(&lk);
            return VFW_E_TIMEOUT_;
        }
        os_lock(&lk);
        waiting--;
        HR hr = S_OK_;
        if (!committed) hr = VFW_E_NOT_COMMITTED_;
        else if (decommit_queued) hr = VFW_E_TIMEOUT_;
        else {
            MemSample *s = free_list.p[--free_list.n];
            used_list.push(s);
            s->AddRef();
            *out = s;
        }
        os_unlock(&lk);
        return hr;
    }
    HR ReleaseBuffer(IMediaSample_ *sample) override
    {
        if (!sample) return E_POINTER_;
        MemSample *s = static_cast<MemSample *>(sample);
        os_lock(&lk);
        remove_used(s);
        free_list.push(s);
        if (!used_list.n && decommit_queued && committed) {
            committed = decommit_queued = false;
            CloseHandle((HANDLE)sem);
            sem = 0;
            free_buffers_locked();
        }
        HANDLE h = (HANDLE)sem;
        if (h) ReleaseSemaphore(h, 1, nullptr);
        os_unlock(&lk);
        return S_OK_;
    }
};
unsigned long MemSample::Release()
{
    int r = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
    if (!r) {
        drop_type();
        props.dwSampleFlags = 0;
        media_valid = false;
        if (parent) parent->ReleaseBuffer(this);
        else delete this;
    }
    return (unsigned long)r;
}
}   // namespace
IMemAllocator_ *make_mem_allocator() { return new (std::nothrow) MemAlloc(); }

/* ---- the base filter */
BFilter::~BFilter()
{
    for (size_t i = 0; i < pins.n; i++) delete pins[i];
    if (sync) sync->Release();
}
BPin *BFilter::add_pin(int dir, const char *n)
{
    BPin *p = new (std::nothrow) BPin(this, dir, n);
    if (p && !pins.push(p)) { delete p; p = nullptr; }
    return p;
}
unsigned long BFilter::Release()
{
    int r = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
    if (!r) delete this;
    return (unsigned long)r;
}
HR BFilter::QueryInterface(const void *iid, void **out)
{
    if (!out) return E_POINTER_;
    *out = nullptr;
    if (geq(iid, IID_IUnknown_) || geq(iid, IID_IPersist_) || geq(iid, IID_IMediaFilter_) || geq(iid, IID_IBaseFilter_))
        *out = static_cast<IBaseFilter_ *>(this);
    else if (geq(iid, IID_IReferenceClock_) && kind == K_RENDERER) *out = static_cast<IReferenceClock_ *>(as_renderer(this));
    else if (geq(iid, IID_DshFilter_)) *out = this;
    else return E_NOINTERFACE_;
    AddRef();
    return S_OK_;
}
HR BFilter::GetState(uint32_t, int *st)
{
    if (!st) return E_POINTER_;
    *st = fstate;
    return S_OK_;
}
HR BFilter::SetSyncSource(IReferenceClock_ *c)
{
    if (c) c->AddRef();
    IReferenceClock_ *old = sync;
    sync = c;
    if (old) old->Release();
    return S_OK_;
}
HR BFilter::GetSyncSource(IReferenceClock_ **c)
{
    if (!c) return E_POINTER_;
    *c = sync;
    if (sync) sync->AddRef();
    return S_OK_;
}
HR BFilter::EnumPins(void **out)
{
    if (!out) return E_POINTER_;
    Vec<void *> v;
    for (size_t i = 0; i < pins.n; i++) v.push(static_cast<IPin_ *>(pins[i]));
    IEnum_ *e = make_enum(EK_UNK, v.p, v.n);
    *out = e;
    return e ? S_OK_ : E_OUTOFMEMORY_;
}
HR BFilter::FindPin(const WCH *id, IPin_ **out)
{
    if (!id || !out) return E_POINTER_;
    *out = nullptr;
    for (size_t i = 0; i < pins.n; i++)
        if (weq_nocase(id, pins[i]->name)) {
            *out = static_cast<IPin_ *>(pins[i]);
            (*out)->AddRef();
            return S_OK_;
        }
    return VFW_E_NOT_FOUND_;
}
HR BFilter::QueryFilterInfo(FilterInfo *fi)
{
    if (!fi) return E_POINTER_;
    wcopy(fi->name, 128, name);
    fi->graph = graph_if;
    if (graph_if) graph_if->AddRef();
    return S_OK_;
}
HR BFilter::JoinFilterGraph(IFilterGraph_ *g, const WCH *n)
{
    graph_if = g;   /* not AddRef'd, as the SDK says */
    graph = g ? core_of(g) : nullptr;
    if (g) wcopy(name, 128, n ? n : (const WCH *)u"");
    else name[0] = 0;
    return S_OK_;
}

/* ---- the MPEG-1 audio splitter: a Layer III stream in, the frames out */
namespace {
struct Splitter final : Stage, ByteSource {
    Blob *blob = nullptr;
    uint32_t hz = 0, ch = 0, kbps = 0;
    BPin *in, *out;
    Splitter() : Stage(K_SPLITTER, &CLSID_MPEG1Splitter_)
    {
        in = add_pin(PINDIR_INPUT, "Input");
        out = add_pin(PINDIR_OUTPUT, "Audio");
        in->add_type(MEDIATYPE_Stream_, MEDIASUBTYPE_MPEG1Audio_);
        MT m;
        mt_wfx(&m, MEDIASUBTYPE_MP3_, 0x55, 2, 44100, 0);
        out->set_type(m);
        mt_free(&m);
    }
    ~Splitter() override { blob_unref(blob); }
    HR input_connected(BPin *, IPin_ *peer, const MT *) override
    {
        AsyncReader *rd = nullptr;
        if (peer->QueryInterface(&IID_IAsyncReader_, (void **)&rd) < 0 || !rd) return E_NOINTERFACE_;
        HR hr = S_OK_;
        Blob *b = nullptr;
        if (sniff_reader(rd) != SK_MP3) hr = VFW_E_TYPE_NOT_ACCEPTED_;
        else hr = read_all(rd, &b, graph ? &graph->aborted : nullptr);
        rd->Release();   /* the stream is in memory: the source's reader is not kept */
        if (hr < 0) return hr;
        blob_unref(blob);
        blob = b;
        sniff_core(rd_mem, b, b->size, &hz, &ch, &kbps);
        MT m;
        mt_wfx(&m, MEDIASUBTYPE_MP3_, 0x55, (uint16_t)ch, hz, 0);
        m.sample_size = 0;
        if (m.pb) ((WFX *)m.pb)->avg = kbps * 125;
        out->set_type(m);
        mt_free(&m);
        return S_OK_;
    }
    void input_disconnected(BPin *) override { blob_unref(blob); blob = nullptr; }
    Blob *bytes(BPin *) override { return blob; }
};

/* ---- the MPEG audio (Layer III) decoder, minimp3 */
struct Decoder final : Stage, PcmSource {
    mp3dec_ex_t dec;
    bool open = false;
    Blob *blob = nullptr;
    uint32_t hz = 0, ch = 0;
    BPin *in, *out;
    mp3d_sample_t tmp[2048 * 2];
    Decoder() : Stage(K_DECODER, &CLSID_MPEGAudioDecoder_)
    {
        memset(&dec, 0, sizeof dec);
        in = add_pin(PINDIR_INPUT, "In");
        out = add_pin(PINDIR_OUTPUT, "Out");
        in->add_type(MEDIATYPE_Audio_, MEDIASUBTYPE_MP3_);
        MT m;
        mt_wfx(&m, MEDIASUBTYPE_PCM_, 1, 2, 44100, 16);
        out->set_type(m);
        mt_free(&m);
    }
    ~Decoder() override { close(); }
    void close()
    {
        if (open) mp3dec_ex_close(&dec);
        open = false;
        blob_unref(blob);
        blob = nullptr;
    }
    HR input_connected(BPin *, IPin_ *peer, const MT *) override
    {
        BPin *up = internal_pin(peer);   /* our splitter: the byte stream is its output pin's */
        ByteSource *bs = up ? as_bytes(up->owner) : nullptr;
        Blob *b = bs ? bs->bytes(up) : nullptr;
        if (!b) return VFW_E_NO_TRANSPORT_;
        close();
        blob = blob_ref(b);
        memset(&dec, 0, sizeof dec);
        open = mp3dec_ex_open_buf(&dec, blob->data, blob->size, MP3D_SEEK_TO_SAMPLE) == 0 && dec.info.hz && dec.info.channels;
        if (!open) {
            if (fnvnx_plat_on) fnvnx_plat_note(FNVNX_NOTE_DECODE, "dshow::mp3_open", FNVNX_NOTE_RA, "minimp3");
            close();
            return VFW_E_TYPE_NOT_ACCEPTED_;
        }
        hz = (uint32_t)dec.info.hz;
        ch = dec.info.channels > 2 ? 2 : (uint32_t)dec.info.channels;
        MT m;
        mt_wfx(&m, MEDIASUBTYPE_PCM_, 1, (uint16_t)ch, hz, 16);
        out->set_type(m);
        mt_free(&m);
        return S_OK_;
    }
    void input_disconnected(BPin *) override { close(); }
    bool pcm_info(BPin *, uint32_t *rate, uint64_t *frames) override
    {
        if (!open) return false;
        *rate = hz;
        *frames = dec.samples / (uint64_t)dec.info.channels;
        return true;
    }
    bool pcm_seek(BPin *, uint64_t frame) override
    {
        return open && mp3dec_ex_seek(&dec, frame * dec.info.channels) == 0;   /* the decoder's reservoir is re-primed by minimp3 */
    }
    size_t pcm_read(BPin *, int16_t *dst, size_t frames) override
    {
        if (!open) return 0;
        size_t done = 0;
        while (done < frames) {
            size_t want = frames - done < 2048 ? frames - done : 2048;
            size_t got = mp3dec_ex_read(&dec, tmp, want * dec.info.channels) / (size_t)dec.info.channels;
            if (!got) break;
            for (size_t i = 0; i < got; i++) {
                int16_t l = tmp[i * dec.info.channels], r = ch > 1 ? tmp[i * dec.info.channels + 1] : l;
                dst[2 * (done + i)] = l;
                dst[2 * (done + i) + 1] = r;
            }
            done += got;
        }
        return done;
    }
};

/* ---- the WAVE parser: RIFF/WAVE with PCM (or WAVE_FORMAT_EXTENSIBLE PCM), 8 or 16 bits, mono or stereo */
struct Parser final : Stage, PcmSource {
    Blob *blob = nullptr;
    uint32_t rate = 0, ch = 0, bits = 0;
    uint64_t data_off = 0, frames = 0, pos = 0;
    BPin *in, *out;
    Parser() : Stage(K_PARSER, &CLSID_WAVEParser_)
    {
        in = add_pin(PINDIR_INPUT, "input pin");
        out = add_pin(PINDIR_OUTPUT, "Output");
        in->add_type(MEDIATYPE_Stream_, MEDIASUBTYPE_WAVE_);
        MT m;
        mt_wfx(&m, MEDIASUBTYPE_PCM_, 1, 2, 44100, 16);
        out->set_type(m);
        mt_free(&m);
    }
    ~Parser() override { blob_unref(blob); }
    static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
    static uint32_t le16(const uint8_t *p) { return p[0] | p[1] << 8; }
    bool parse(Blob *b)
    {
        const uint8_t *d = b->data;
        size_t n = b->size, at = 12;
        bool have_fmt = false;
        while (at + 8 <= n) {
            uint32_t sz = le32(d + at + 4);
            size_t body = at + 8;
            if (!memcmp(d + at, "fmt ", 4)) {
                if (sz < 16 || body + sz > n) return false;
                uint32_t tag = le16(d + body);
                ch = le16(d + body + 2);
                rate = le32(d + body + 4);
                uint32_t block = le16(d + body + 12);
                bits = le16(d + body + 14);
                if (tag == 0xFFFE) {   /* the sub-format GUID must be PCM; container size is wBitsPerSample */
                    if (sz < 40 || le16(d + body + 16) < 22 || memcmp(d + body + 24, &MEDIASUBTYPE_PCM_, 16)) return false;
                } else if (tag != 1)
                    return false;   /* a codec the environment doesn't have */
                if ((bits != 8 && bits != 16) || ch < 1 || ch > 2 || !rate || block != ch * bits / 8) return false;
                have_fmt = true;
            } else if (!memcmp(d + at, "data", 4)) {
                if (!have_fmt) return false;
                data_off = body;
                uint64_t avail = n - body;
                frames = (sz > avail ? avail : sz) / (ch * bits / 8);   /* a streamed file's size field may overshoot */
                return frames > 0;
            }
            at = body + sz + (sz & 1);
        }
        return false;
    }
    HR input_connected(BPin *, IPin_ *peer, const MT *) override
    {
        AsyncReader *rd = nullptr;
        if (peer->QueryInterface(&IID_IAsyncReader_, (void **)&rd) < 0 || !rd) return E_NOINTERFACE_;
        HR hr = S_OK_;
        Blob *b = nullptr;
        if (sniff_reader(rd) != SK_WAVE) hr = VFW_E_TYPE_NOT_ACCEPTED_;
        else hr = read_all(rd, &b, graph ? &graph->aborted : nullptr);
        rd->Release();
        if (hr < 0) return hr;
        if (!parse(b)) { blob_unref(b); return VFW_E_TYPE_NOT_ACCEPTED_; }
        blob_unref(blob);
        blob = b;
        pos = 0;
        MT m;
        mt_wfx(&m, MEDIASUBTYPE_PCM_, 1, (uint16_t)ch, rate, (uint16_t)bits);
        out->set_type(m);
        mt_free(&m);
        return S_OK_;
    }
    void input_disconnected(BPin *) override { blob_unref(blob); blob = nullptr; frames = 0; }
    bool pcm_info(BPin *, uint32_t *r, uint64_t *f) override
    {
        if (!blob) return false;
        *r = rate;
        *f = frames;
        return true;
    }
    bool pcm_seek(BPin *, uint64_t frame) override
    {
        if (!blob) return false;
        pos = frame > frames ? frames : frame;
        return true;
    }
    size_t pcm_read(BPin *, int16_t *dst, size_t want) override
    {
        if (!blob) return 0;
        size_t n = frames - pos < want ? (size_t)(frames - pos) : want;
        const uint8_t *s = blob->data + data_off + pos * ch * bits / 8;
        for (size_t i = 0; i < n; i++)
            for (uint32_t c = 0; c < 2; c++) {
                uint32_t k = c < ch ? c : 0;
                dst[2 * i + c] = bits == 16 ? (int16_t)le16(s + (i * ch + k) * 2) : (int16_t)(((int)s[i * ch + k] - 128) << 8);
            }
        pos += n;
        return n;
    }
};

/* ---- the DirectSound renderer: a looping mixer voice over a ring the pump keeps topped up */
const uint32_t RING_FRAMES = 1 << 15;   /* ~0.7 s at 44.1 kHz */
const uint32_t AHEAD_FRAMES = RING_FRAMES - 4096;
struct Advise { uintptr_t h; LL at, period; bool periodic; uintptr_t cookie; };

struct DSoundRender final : Renderer {
    BPin *in;
    BPin *up = nullptr;
    PcmSource *src = nullptr;   /* what feeds up: set with it */
    mix_voice *voice = nullptr;
    int16_t *ring = nullptr;
    uint32_t rate = 0, last_cursor = 0;
    uint64_t total = 0, decoded = 0, start_frame = 0, played = 0, stop_frame = ~0ull;
    bool eos = false, completed = false, errored = false, flushing = false;
    double mult = 1.0, wall_s = 0.0;
    long volume = 0, balance = 0;
    Vec<Advise> adv;
    uintptr_t next_cookie = 1;
    DSoundRender()
    {
        in = add_pin(PINDIR_INPUT, "Audio Input pin (rendered)");
        in->add_type(MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_);
    }
    ~DSoundRender() override { detach(); }
    typedef GraphLock Lk;   /* the graph lock, when the filter is in a graph */
    bool accepts(BPin *p, const MT *mt) override
    {
        if (!geq(mt->major, MEDIATYPE_Audio_) || !geq(mt->sub, MEDIASUBTYPE_PCM_) || !geq(mt->format, FORMAT_WaveFormatEx_)) return false;
        if (mt->cb < 16 || !mt->pb) return false;
        const WFX *w = (const WFX *)mt->pb;
        return w->tag == 1 && w->ch >= 1 && w->ch <= 2 && (w->bits == 8 || w->bits == 16) && w->rate >= 100 && w->rate <= 200000;
    }
    void apply_gain_locked()
    {
        if (!voice) return;
        float g = mixer_mb_to_gain(volume), l, r;
        mixer_pan_to_gains(balance, &l, &r);
        mixer_voice_set_gain(voice, g * l, g * r);
    }
    HR input_connected(BPin *, IPin_ *peer, const MT *) override
    {
        BPin *u = internal_pin(peer);
        uint32_t r = 0;
        uint64_t fr = 0;
        PcmSource *ps = u ? as_pcm(u->owner) : nullptr;
        if (!ps || !ps->pcm_info(u, &r, &fr)) return VFW_E_NO_TRANSPORT_;   /* only a stream of ours can be pulled */
        Lk lk(graph);
        if (!ring) ring = (int16_t *)calloc(RING_FRAMES, 2 * sizeof(int16_t));
        mix_format f = {r, 2, 16, 4, 0};
        voice = ring ? mixer_voice_create(&f, (uint8_t *)ring, RING_FRAMES * 4) : nullptr;
        if (!voice) return E_OUTOFMEMORY_;
        up = u;
        src = ps;
        rate = r;
        total = fr;
        stop_frame = ~0ull;
        mixer_voice_set_freq(voice, (uint32_t)(r * mult + 0.5));
        apply_gain_locked();
        seek_locked(0, ~0ull);
        if (graph) graph->start_pump();
        return S_OK_;
    }
    void detach()
    {
        Lk lk(graph);
        if (voice) mixer_voice_destroy(voice);
        voice = nullptr;
        free(ring);
        ring = nullptr;
        up = nullptr;
        src = nullptr;
        total = rate = 0;
    }
    void input_disconnected(BPin *) override { detach(); }
    void seek_locked(uint64_t frame, uint64_t stop)
    {
        if (frame > total) frame = total;
        bool run = voice && fstate == State_Running;
        if (voice && run) mixer_voice_stop(voice);
        if (src) src->pcm_seek(up, frame);
        start_frame = frame;
        stop_frame = stop;
        decoded = played = 0;
        last_cursor = 0;
        eos = completed = errored = false;
        if (ring) memset(ring, 0, RING_FRAMES * 4);
        if (voice) mixer_voice_set_cursor(voice, 0);
        fill_locked();
        if (run) mixer_voice_play(voice, 1);
    }
    uint64_t limit() const { return total < stop_frame ? total : stop_frame; }
    void track_locked()
    {
        if (!voice) return;
        uint32_t c = mixer_voice_play_cursor(voice) / 4;
        uint32_t d = c >= last_cursor ? c - last_cursor : c + RING_FRAMES - last_cursor;
        played += d;
        wall_s += d / (rate * mult);
        last_cursor = c;
    }
    void fill_locked()
    {
        if (!up || !ring) return;
        int16_t tmp[4096 * 2];
        while (!eos && decoded < played + AHEAD_FRAMES) {
            uint64_t at_abs = start_frame + decoded, lim = limit();
            if (at_abs >= lim) { eos = true; break; }
            uint64_t want = played + AHEAD_FRAMES - decoded;
            if (want > 4096) want = 4096;
            if (want > lim - at_abs) want = lim - at_abs;
            size_t got = src->pcm_read(up, tmp, (size_t)want);
            if (!got) { eos = errored = true; break; }   /* the stream ended before its length: a read error */
            for (size_t i = 0; i < got; i++) {
                uint32_t at = (uint32_t)((decoded + i) % RING_FRAMES);
                ring[2 * at] = tmp[2 * i];
                ring[2 * at + 1] = tmp[2 * i + 1];
            }
            decoded += got;
        }
        if (eos)   /* silence after the end, so the voice plays nothing old while it runs out */
            for (uint64_t f = decoded; f < played + RING_FRAMES && f < decoded + RING_FRAMES; f++) {
                uint32_t at = (uint32_t)(f % RING_FRAMES);
                ring[2 * at] = ring[2 * at + 1] = 0;
            }
    }
    LL time_locked()
    {
        if (voice && fstate == State_Running) track_locked();
        return (LL)(wall_s * 1e7);
    }
    void service_advises_locked()
    {
        if (!adv.n) return;
        LL now = time_locked();
        for (size_t i = 0; i < adv.n;) {
            Advise &a = adv[i];
            if (now < a.at) { i++; continue; }
            if (a.periodic) {
                for (int k = 0; k < 1000 && now >= a.at; k++, a.at += a.period) ReleaseSemaphore((HANDLE)a.h, 1, nullptr);
                i++;
            } else {
                SetEvent((HANDLE)a.h);
                adv.erase(i);
            }
        }
    }
    void set_state(int st) override
    {
        Lk lk(graph);
        if (voice) {
            if (st == State_Running && fstate != State_Running) {
                if (eos && played >= decoded) seek_locked(start_frame + played, stop_frame);   /* at the end: stays */
                track_locked();
                mixer_voice_play(voice, 1);
            } else if (st != State_Running && fstate == State_Running) {
                track_locked();
                mixer_voice_stop(voice);
            }
        }
        fstate = st;
    }
    void tick_locked() override
    {
        if (!voice || fstate != State_Running) return;
        track_locked();
        if (!flushing) fill_locked();
        service_advises_locked();
        if (eos && played >= decoded) {   /* the end of the stream was heard */
            mixer_voice_stop(voice);
            fstate = State_Stopped;
            completed = true;
            if (errored) {
                if (graph) graph->event_locked(EC_ERRORABORT_, E_FAIL_, 0);
            } else if (graph)
                graph->completion_locked(this);
        }
    }
    /* Renderer interface for the graph */
    bool connected() override { return voice != nullptr; }
    uint64_t total_frames() override { return total; }
    uint32_t src_rate() override { return rate; }
    uint64_t position_frames() override
    {
        if (voice && fstate == State_Running) track_locked();
        return start_frame + (played < decoded ? played : decoded);
    }
    bool seek_frames(uint64_t frame, uint64_t stop) override
    {
        if (!voice) return false;
        seek_locked(frame, stop);
        return true;
    }
    HR set_play_rate(double r) override
    {
        if (r <= 0) return E_INVALIDARG_;
        double f = rate * r;
        if (f < 100 || f > 200000) return VFW_E_UNSUPPORTED_AUDIO_;   /* outside DirectSound's frequency range */
        if (voice) { track_locked(); mixer_voice_set_freq(voice, (uint32_t)(f + 0.5)); }
        mult = r;
        return S_OK_;
    }
    bool is_complete() override { return completed; }
    void rearm() override { completed = false; }
    HR set_volume(long v) override
    {
        if (v > 0 || v < -10000) return E_INVALIDARG_;
        volume = v;
        apply_gain_locked();
        return S_OK_;
    }
    HR set_balance(long b) override
    {
        if (b < -10000 || b > 10000) return E_INVALIDARG_;
        balance = b;
        apply_gain_locked();
        return S_OK_;
    }
    long get_volume() override { return volume; }
    long get_balance() override { return balance; }
    /* IReferenceClock */
    HR GetTime(LL *t) override
    {
        if (!t) return E_POINTER_;
        Lk lk(graph);
        *t = time_locked();
        return S_OK_;
    }
    HR AdviseTime(LL base, LL stream, uintptr_t evt, uintptr_t *cookie) override
    {
        if (!evt || !cookie) return E_INVALIDARG_;
        Lk lk(graph);
        Advise a = {evt, base + stream, 0, false, next_cookie++};
        *cookie = a.cookie;
        return adv.push(a) ? S_OK_ : E_OUTOFMEMORY_;
    }
    HR AdvisePeriodic(LL start, LL period, uintptr_t sem, uintptr_t *cookie) override
    {
        if (!sem || !cookie || period <= 0) return E_INVALIDARG_;
        Lk lk(graph);
        Advise a = {sem, start, period, true, next_cookie++};
        *cookie = a.cookie;
        return adv.push(a) ? S_OK_ : E_OUTOFMEMORY_;
    }
    HR Unadvise(uintptr_t cookie) override
    {
        Lk lk(graph);
        for (size_t i = 0; i < adv.n; i++)
            if (adv[i].cookie == cookie) { adv.erase(i); return S_OK_; }
        return S_FALSE_;
    }
    HR stream_event(int ev, double r) override
    {
        if (ev == 3) return set_play_rate(r);   /* NewSegment: the segment's rate is the playback rate */
        Lk lk(graph);
        if (ev == 0) eos = true;   /* the source says the stream ends where it is */
        else if (ev == 1) flushing = true;
        else if (ev == 2) {
            flushing = false;
            if (voice) seek_locked(position_frames(), stop_frame);   /* what was queued is discarded */
        }
        return S_OK_;
    }
};
}   // namespace

/* the renderer's pin reacts to the stream-control calls: EndOfStream ends the stream where it is, BeginFlush /
 * EndFlush discard what is queued, NewSegment's rate becomes the voice's playback rate */
Renderer *as_renderer(BFilter *f) { return f && f->kind == K_RENDERER ? static_cast<Renderer *>(f) : nullptr; }
Stage *as_stage(BFilter *f) { return f && f->kind != K_FILESRC ? static_cast<Stage *>(f) : nullptr; }
PcmSource *as_pcm(BFilter *f)
{
    if (!f) return nullptr;
    if (f->kind == K_DECODER) return static_cast<Decoder *>(f);
    if (f->kind == K_PARSER) return static_cast<Parser *>(f);
    return nullptr;
}
ByteSource *as_bytes(BFilter *f) { return f && f->kind == K_SPLITTER ? static_cast<Splitter *>(f) : nullptr; }

/* ---- the file source (AddSourceFilter / RenderFile): the file through the layer's CreateFileA / ReadFile */
namespace {
struct FileSrc final : BFilter, IFileSource_ {
    BPin *out;
    WCH path[260] = {};
    FileSrc() : BFilter(K_FILESRC, &CLSID_AsyncReader_) { out = add_pin(PINDIR_OUTPUT, "Output"); out->serves = true; }
    bool accepts(BPin *, const MT *mt) override { return geq(mt->major, MEDIATYPE_Stream_); }
    HR QueryInterface(const void *iid, void **o) override
    {
        if (o && geq(iid, IID_IFileSourceFilter_)) { *o = static_cast<IFileSource_ *>(this); AddRef(); return S_OK_; }
        return BFilter::QueryInterface(iid, o);
    }
    unsigned long AddRef() override { return BFilter::AddRef(); }
    unsigned long Release() override { return BFilter::Release(); }
    HR Load(const WCH *file, const MT *) override
    {
        if (!file) return E_POINTER_;
        char a[780];
        size_t n = 0;
        for (size_t i = 0; file[i] && n < 770; i++) {   /* UTF-16 to UTF-8 */
            uint32_t c = file[i];
            if (c >= 0xD800 && c < 0xDC00 && file[i + 1] >= 0xDC00 && file[i + 1] < 0xE000) c = 0x10000 + ((c - 0xD800) << 10) + (file[++i] - 0xDC00);
            if (c < 0x80) a[n++] = (char)c;
            else if (c < 0x800) { a[n++] = (char)(0xC0 | c >> 6); a[n++] = (char)(0x80 | (c & 63)); }
            else if (c < 0x10000) { a[n++] = (char)(0xE0 | c >> 12); a[n++] = (char)(0x80 | ((c >> 6) & 63)); a[n++] = (char)(0x80 | (c & 63)); }
            else { a[n++] = (char)(0xF0 | c >> 18); a[n++] = (char)(0x80 | ((c >> 12) & 63)); a[n++] = (char)(0x80 | ((c >> 6) & 63)); a[n++] = (char)(0x80 | (c & 63)); }
        }
        a[n] = 0;
        HANDLE h = CreateFileA(a, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
        if (h == INVALID_HANDLE_VALUE) return (HR)(0x80070000u | (GetLastError() & 0xFFFF));
        LARGE_INTEGER sz;
        if (!GetFileSizeEx(h, &sz)) { CloseHandle(h); return E_FAIL_; }
        if (out->file) CloseHandle((HANDLE)out->file);
        out->file = (uintptr_t)h;
        out->fsize = sz.QuadPart;
        StreamKind k = sniff_reader(out);
        if (k == SK_UNKNOWN) { CloseHandle(h); out->file = 0; out->fsize = 0; return VFW_E_UNKNOWN_FILE_TYPE_; }
        MT m = {};
        m.major = MEDIATYPE_Stream_;
        m.sub = k == SK_MP3 ? MEDIASUBTYPE_MPEG1Audio_ : MEDIASUBTYPE_WAVE_;
        m.format = FORMAT_None_;
        out->set_type(m);
        wcopy(path, 260, file);
        return S_OK_;
    }
    HR GetCurFile(WCH **file, MT *mt) override
    {
        if (!file) return E_POINTER_;
        *file = path[0] ? wdup_co(path) : nullptr;
        if (mt) {
            if (out->types.n) return mt_copy(mt, &out->types[0]);
            memset(mt, 0, sizeof *mt);
        }
        return S_OK_;
    }
};

/* a pin over a source that only exposes IAsyncReader (not an IPin): the connection's far end for Render */
struct ReaderPin final : IPin_ {
    int refs = 1;
    AsyncReader *rd;
    IPin_ *peer = nullptr;
    MT cur = {};   /* the connection type (valid while peer) */
    explicit ReaderPin(AsyncReader *r) : rd(r) {}
    ~ReaderPin() { mt_free(&cur); }
    HR QueryInterface(const void *iid, void **o) override
    {
        if (!o) return E_POINTER_;
        *o = nullptr;
        if (geq(iid, IID_IUnknown_) || geq(iid, IID_IPin_)) *o = static_cast<IPin_ *>(this);
        else if (rd && geq(iid, IID_IAsyncReader_)) *o = rd;   /* borrowed: valid for the duration of the Render call */
        else return E_NOINTERFACE_;
        if (*o == rd) rd->AddRef(); else AddRef();
        return S_OK_;
    }
    unsigned long AddRef() override { return __atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    unsigned long Release() override
    {
        int r = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (!r) delete this;
        return (unsigned long)r;
    }
    HR Connect(IPin_ *r, const MT *mt) override
    {
        if (peer) return VFW_E_ALREADY_CONNECTED_;
        MT t = {};
        t.major = MEDIATYPE_Stream_;
        t.format = FORMAT_None_;
        const MT *use = mt ? mt : &t;
        MT c;
        HR hr = mt_copy(&c, use);
        if (hr < 0) return hr;
        hr = r->ReceiveConnection(this, use);
        if (hr >= 0) {
            mt_free(&cur);
            cur = c;
            peer = r;
            r->AddRef();
        } else
            mt_free(&c);
        return hr;
    }
    HR ReceiveConnection(IPin_ *, const MT *) override;
    HR Disconnect() override
    {
        if (!peer) return S_FALSE_;
        IPin_ *p = peer;
        peer = nullptr;
        mt_free(&cur);
        p->Release();
        return S_OK_;
    }
    HR ConnectedTo(IPin_ **p) override { if (!peer) return VFW_E_NOT_CONNECTED_; *p = peer; peer->AddRef(); return S_OK_; }
    HR ConnectionMediaType(MT *m) override   /* strmbase pin.c pin_ConnectionMediaType */
    {
        if (!m) return E_POINTER_;
        if (!peer) { memset(m, 0, sizeof *m); return VFW_E_NOT_CONNECTED_; }
        return mt_copy(m, &cur);
    }
    HR QueryPinInfo(PinInfo *pi) override { pi->filter = nullptr; pi->dir = PINDIR_OUTPUT; pi->name[0] = 0; return S_OK_; }
    HR QueryDirection(int *d) override { *d = PINDIR_OUTPUT; return S_OK_; }
    HR QueryId(WCH **id) override { *id = wdup_co((const WCH *)u"Output"); return S_OK_; }
    HR QueryAccept(const MT *mt) override { return geq(mt->major, MEDIATYPE_Stream_) ? S_OK_ : S_FALSE_; }
    HR EnumMediaTypes(void **e) override
    {
        MT t = {};
        t.major = MEDIATYPE_Stream_;
        t.format = FORMAT_None_;
        void *v[1] = {&t};
        *e = make_enum(EK_MT, v, 1);
        return *e ? S_OK_ : E_OUTOFMEMORY_;
    }
    HR QueryInternalConnections(IPin_ **, uint32_t *n) override { *n = 0; return S_OK_; }
    HR EndOfStream() override;
    HR BeginFlush() override;
    HR EndFlush() override;
    HR NewSegment(LL, LL, double) override;
};
}   // namespace
#include "dshow_filters.env_const.gen.inc"   /* BFilter::QueryVendorInfo and the output pin's stream calls: manifest/env_const.tsv */

void reader_pin_orphan(IPin_ *p) { static_cast<ReaderPin *>(p)->rd = nullptr; }
IPin_ *make_reader_pin(AsyncReader *rd) { return new (std::nothrow) ReaderPin(rd); }
HR make_file_source(const WCH *file, BFilter **out)
{
    FileSrc *f = new (std::nothrow) FileSrc();
    if (!f) return E_OUTOFMEMORY_;
    HR hr = f->Load(file, nullptr);
    if (hr < 0) { f->Release(); return hr; }
    *out = f;
    return S_OK_;
}
BFilter *make_filter(const Guid &c)
{
    if (geq(c, CLSID_MPEG1Splitter_)) return new (std::nothrow) Splitter();
    if (geq(c, CLSID_MPEGAudioDecoder_)) return new (std::nothrow) Decoder();
    if (geq(c, CLSID_WAVEParser_)) return new (std::nothrow) Parser();
    if (geq(c, CLSID_DSoundRender_)) return new (std::nothrow) DSoundRender();
    return nullptr;
}

/* the installed set: what the graph's intelligent connect and the filter mapper work from */
const ClassDesc g_classes[4] = {
    {&CLSID_DSoundRender_, "DirectSound Device", MERIT_PREFERRED_, K_RENDERER,
     {{"Audio Input pin (rendered)", false, true, {MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_}}, {nullptr, false, false, {}}}},
    {&CLSID_MPEG1Splitter_, "MPEG-I Stream Splitter", MERIT_NORMAL_, K_SPLITTER,
     {{"Input", false, false, {MEDIATYPE_Stream_, MEDIASUBTYPE_MPEG1Audio_}}, {"Audio", true, false, {MEDIATYPE_Audio_, MEDIASUBTYPE_MP3_}}}},
    {&CLSID_MPEGAudioDecoder_, "MPEG Audio Decoder", MERIT_NORMAL_, K_DECODER,
     {{"In", false, false, {MEDIATYPE_Audio_, MEDIASUBTYPE_MP3_}}, {"Out", true, false, {MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_}}}},
    {&CLSID_WAVEParser_, "WAVE Parser", MERIT_NORMAL_, K_PARSER,
     {{"input pin", false, false, {MEDIATYPE_Stream_, MEDIASUBTYPE_WAVE_}}, {"Output", true, false, {MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_}}}},
};

}   // namespace dsh
