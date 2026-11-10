/* test_dshow_contract.cpp - the DirectShow FilterGraph contract (DirectShow SDK) on the decided environment: the
 * installed filters are the MPEG-1 audio splitter, the MP3 decoder, the WAVE parser and the DirectSound renderer;
 * nothing else renders. The game's side is a fake source filter with an output pin that offers a media type and
 * serves bytes through IAsyncReader (tone.mp3 / in-memory WAV / unknown bytes). Audio is stepped through the mixer
 * (mixer_render_f32) as test_dshow.cpp does. Priorities: 1 graph building, 2 control/state/seeking, 3 IBasicAudio
 * + IDispatch, 4 events, 5 CLSID_FilterMapper, 6 QueryInterface. */
#include "s7_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>
#include <algorithm>
#include <string>
#include <vector>

#include "fnvnx_win32_ext.h"
#include "../../platform/audio/mixer.h"
#include "../../platform/audio/dshow_int.h"

using namespace dsh;

namespace {
const GUID *G_(const Guid &g) { return (const GUID *)&g; }
const GUID CLSID_Mapper_ = {0xe436ebb2, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
const GUID CLSID_Graph_ = {0xe436ebb3, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};

std::vector<uint16_t> WS(const char *s) { std::vector<uint16_t> v; while (*s) v.push_back((uint8_t)*s++); v.push_back(0); return v; }
std::string A(const WCH *w) { std::string s; while (w && *w) s += (char)*w++; return s; }

struct TEnum final : IEnum_ {   /* a snapshot enumerator for the fake source (pins, media types) */
    int refs = 1;
    std::vector<void *> it;
    bool media = false;
    size_t i = 0;
    HR QueryInterface(const void *, void **o) override { *o = this; refs++; return 0; }
    unsigned long AddRef() override { return ++refs; }
    unsigned long Release() override { int r = --refs; if (!r) delete this; return r; }
    HR Next(uint32_t n, void **out, uint32_t *f) override
    {
        uint32_t k = 0;
        for (; k < n && i < it.size(); k++, i++) {
            if (media) { MT *m = (MT *)malloc(sizeof(MT)); *m = *(MT *)it[i]; out[k] = m; }
            else { ((Unk *)it[i])->AddRef(); out[k] = it[i]; }
        }
        if (f) *f = k;
        return k == n ? 0 : 1;
    }
    HR Skip(uint32_t n) override { i += n; return i <= it.size() ? 0 : 1; }
    HR Reset() override { i = 0; return 0; }
    HR Clone(void **o) override { TEnum *e = new TEnum(*this); e->refs = 1; *o = e; return 0; }
};

struct SrcFilter;
struct SrcPin final : IPin_, AsyncReader {   /* the game's output pin: IPin and IAsyncReader on one object */
    int refs = 1;
    SrcFilter *f;
    IPin_ *peer = nullptr;
    MT type = {};
    const std::vector<uint8_t> *data;
    int reads = 0;
    bool fail_reads = false;
    HR QueryInterface(const void *iid, void **o) override;
    unsigned long AddRef() override { return ++refs; }
    unsigned long Release() override { return --refs; }
    HR Connect(IPin_ *r, const MT *mt) override
    {
        if (peer) return VFW_E_ALREADY_CONNECTED_;
        MT t = mt ? *mt : type;
        HR hr = r->ReceiveConnection(this, &t);
        if (hr < 0) return hr == VFW_E_TYPE_NOT_ACCEPTED_ ? VFW_E_NO_ACCEPTABLE_TYPES_ : hr;
        peer = r;
        r->AddRef();
        return 0;
    }
    HR ReceiveConnection(IPin_ *, const MT *) override { return VFW_E_INVALID_DIRECTION_; }
    HR Disconnect() override { if (!peer) return 1; peer->Release(); peer = nullptr; return 0; }
    HR ConnectedTo(IPin_ **p) override { if (!peer) return VFW_E_NOT_CONNECTED_; *p = peer; peer->AddRef(); return 0; }
    HR ConnectionMediaType(MT *m) override { *m = type; return peer ? 0 : VFW_E_NOT_CONNECTED_; }
    HR QueryPinInfo(PinInfo *) override;
    HR QueryDirection(int *d) override { *d = PINDIR_OUTPUT; return 0; }
    HR QueryId(WCH **) override { return E_NOTIMPL_; }
    HR QueryAccept(const MT *m) override { return geq(m->major, type.major) && (geq(m->sub, type.sub) || geq(m->sub, GUID_NULL_)) ? 0 : 1; }
    HR EnumMediaTypes(void **o) override { TEnum *e = new TEnum; e->media = true; e->it.push_back(&type); *o = e; return 0; }
    HR QueryInternalConnections(IPin_ **, uint32_t *) override { return E_NOTIMPL_; }
    HR EndOfStream() override { return 0; }
    HR BeginFlush() override { return 0; }
    HR EndFlush() override { return 0; }
    HR NewSegment(LL, LL, double) override { return 0; }
    HR RequestAllocator(void *, void *, void **) override { return E_NOTIMPL_; }
    HR Request(void *, uintptr_t) override { return E_NOTIMPL_; }
    HR WaitForNext(uint32_t, void **, uintptr_t *) override { return E_NOTIMPL_; }
    HR SyncReadAligned(void *) override { return E_NOTIMPL_; }
    HR SyncRead(LL pos, long len, uint8_t *buf) override
    {
        reads++;
        if (fail_reads || pos < 0 || pos + len > (LL)data->size()) return E_FAIL_;
        memcpy(buf, data->data() + pos, (size_t)len);
        return 0;
    }
    HR Length(LL *t, LL *a) override { *t = *a = (LL)data->size(); return 0; }
};
struct SrcFilter final : IBaseFilter_ {
    int refs = 1, joins = 0, leaves = 0, st = 0;
    IFilterGraph_ *graph = nullptr;
    std::string name;
    SrcPin pin;
    SrcFilter(const std::vector<uint8_t> *d, const Guid &sub, const Guid &major = MEDIATYPE_Stream_)
    {
        pin.f = this;
        pin.data = d;
        pin.type.major = major;
        pin.type.sub = sub;
        pin.type.format = FORMAT_None_;
    }
    ~SrcFilter() { if (graph) ((IFilterGraph_ *)graph)->RemoveFilter(this); }   /* the game's filter leaves before it dies */
    HR QueryInterface(const void *iid, void **o) override
    {
        *o = nullptr;
        if (geq(iid, IID_IUnknown_) || geq(iid, IID_IBaseFilter_) || geq(iid, IID_IMediaFilter_)) { *o = this; refs++; return 0; }
        return E_NOINTERFACE_;
    }
    unsigned long AddRef() override { return ++refs; }
    unsigned long Release() override { return --refs; }
    HR GetClassID(Guid *) override { return E_NOTIMPL_; }
    HR Stop() override { st = 0; return 0; }
    HR Pause() override { st = 1; return 0; }
    HR Run(LL) override { st = 2; return 0; }
    HR GetState(uint32_t, int *s) override { *s = st; return 0; }
    HR SetSyncSource(IReferenceClock_ *) override { return 0; }
    HR GetSyncSource(IReferenceClock_ **c) override { *c = nullptr; return 0; }
    HR EnumPins(void **o) override { TEnum *e = new TEnum; e->it.push_back(static_cast<IPin_ *>(&pin)); *o = e; return 0; }
    HR FindPin(const WCH *, IPin_ **) override { return E_NOTIMPL_; }
    HR QueryFilterInfo(FilterInfo *) override { return E_NOTIMPL_; }
    HR JoinFilterGraph(IFilterGraph_ *g, const WCH *n) override
    {
        graph = g;
        if (g) { joins++; name = A(n); } else leaves++;
        return 0;
    }
    HR QueryVendorInfo(WCH **) override { return E_NOTIMPL_; }
};
HR SrcPin::QueryInterface(const void *iid, void **o)
{
    *o = nullptr;
    if (geq(iid, IID_IUnknown_) || geq(iid, IID_IPin_)) *o = static_cast<IPin_ *>(this);
    else if (geq(iid, IID_IAsyncReader_)) *o = static_cast<AsyncReader *>(this);
    else return E_NOINTERFACE_;
    refs++;
    return 0;
}
HR SrcPin::QueryPinInfo(PinInfo *pi)
{
    pi->filter = static_cast<IBaseFilter_ *>(f);
    f->AddRef();
    pi->dir = PINDIR_OUTPUT;
    pi->name[0] = 'O';
    pi->name[1] = 0;
    return 0;
}

std::vector<uint8_t> g_mp3, g_wav, g_junk, g_adpcm, g_wav8;
bool load(const char *path, std::vector<uint8_t> &v)
{
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    uint8_t b[4096];
    size_t n;
    while ((n = fread(b, 1, sizeof b, f)) > 0) v.insert(v.end(), b, b + n);
    fclose(f);
    return !v.empty();
}
void put32(std::vector<uint8_t> &v, uint32_t x) { for (int i = 0; i < 4; i++) v.push_back((uint8_t)(x >> (8 * i))); }
void put16(std::vector<uint8_t> &v, uint32_t x) { v.push_back((uint8_t)x); v.push_back((uint8_t)(x >> 8)); }
/* RIFF/WAVE: tag 1 PCM, 0xFFFE extensible (PCM subformat), anything else is a codec the environment does not have */
std::vector<uint8_t> make_wav(uint16_t tag, int ch, uint32_t rate, int bits, uint32_t frames)
{
    std::vector<uint8_t> v = {'R', 'I', 'F', 'F'};
    uint32_t block = ch * bits / 8, fmt = tag == 0xFFFE ? 40 : 16, data = frames * block;
    put32(v, 4 + 8 + fmt + 8 + data);
    for (char c : std::string("WAVEfmt ")) v.push_back((uint8_t)c);
    put32(v, fmt);
    put16(v, tag); put16(v, ch); put32(v, rate); put32(v, rate * block); put16(v, block); put16(v, bits);
    if (tag == 0xFFFE) {
        put16(v, 22); put16(v, bits); put32(v, ch == 2 ? 3 : 4);
        const uint8_t sub[16] = {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71};
        v.insert(v.end(), sub, sub + 16);
    }
    for (char c : std::string("data")) v.push_back((uint8_t)c);
    put32(v, data);
    for (uint32_t i = 0; i < frames; i++)
        for (int c = 0; c < ch; c++) {
            double s = 0.5 * sin(6.283185307 * 400.0 * i / rate);
            if (bits == 8) v.push_back((uint8_t)(128 + 127 * s));
            else put16(v, (uint16_t)(int16_t)(32767 * s));
        }
    return v;
}
void init_data()
{
    if (!g_mp3.empty()) return;
    const char *dir = getenv("S7_DATA");
    char path[512];
    snprintf(path, sizeof path, "%s/tone.mp3", dir ? dir : "tests/s7/data");
    load(path, g_mp3);
    g_wav = make_wav(1, 2, 8000, 16, 8000);   /* exactly 1 s */
    g_wav8 = make_wav(0xFFFE, 1, 8000, 16, 4000);
    g_adpcm = make_wav(2, 2, 8000, 16, 8000);
    g_junk.assign(4096, 0x55);
}

std::vector<float> play(unsigned ms)
{
    std::vector<float> all, chunk(960 * 2);
    for (unsigned t = 0; t < ms; t += 20) {
        mixer_render_f32(chunk.data(), 960);
        all.insert(all.end(), chunk.begin(), chunk.end());
        usleep(5000);
    }
    return all;
}
double rms(const std::vector<float> &x, int ch)
{
    double e = 0;
    size_t n = x.size() / 2;
    for (size_t i = 0; i < n; i++) e += (double)x[2 * i + ch] * x[2 * i + ch];
    return n ? sqrt(e / n) : 0;
}

template <class T> T *qi(Unk *u, const Guid &g) { T *t = nullptr; return u && u->QueryInterface(&g, (void **)&t) >= 0 ? t : nullptr; }
struct Graph2 {   /* the graph with the interfaces a test drives */
    IFilterGraph2_ *g = nullptr;
    IMediaControl_ *mc = nullptr;
    IMediaSeeking_ *ms = nullptr;
    IMediaPosition_ *mp = nullptr;
    IMediaEventEx_ *me = nullptr;
    IBasicAudio_ *ba = nullptr;
    IMediaFilter_ *mf = nullptr;
    Graph2()
    {
        mixer_init(48000, 256);
        CoCreateInstance(&CLSID_Graph_, nullptr, 1, G_(IID_IFilterGraph2_), (void **)&g);
        mc = qi<IMediaControl_>(g, IID_IMediaControl_);
        ms = qi<IMediaSeeking_>(g, IID_IMediaSeeking_);
        mp = qi<IMediaPosition_>(g, IID_IMediaPosition_);
        me = qi<IMediaEventEx_>(g, IID_IMediaEventEx_);
        ba = qi<IBasicAudio_>(g, IID_IBasicAudio_);
        mf = qi<IMediaFilter_>(g, IID_IMediaFilter_);
    }
    ~Graph2()
    {
        if (mc) mc->Stop();
        for (Unk *u : {(Unk *)mc, (Unk *)ms, (Unk *)mp, (Unk *)me, (Unk *)ba, (Unk *)mf, (Unk *)g})
            if (u) u->Release();
    }
    int nfilters()
    {
        void *e = nullptr;
        if (g->EnumFilters(&e) < 0) return -1;
        int n = 0;
        void *f;
        uint32_t got;
        while (((IEnum_ *)e)->Next(1, &f, &got) == 0) { n++; ((Unk *)f)->Release(); }
        ((Unk *)e)->Release();
        return n;
    }
    IBaseFilter_ *find(const char *n) { IBaseFilter_ *f = nullptr; auto w = WS(n); return g->FindFilterByName(w.data(), &f) >= 0 ? f : nullptr; }
};
IPin_ *pin_of(IBaseFilter_ *f, int dir, int idx = 0)
{
    void *e = nullptr;
    if (!f || f->EnumPins(&e) < 0) return nullptr;
    IPin_ *p = nullptr;
    void *x;
    uint32_t got;
    while (((IEnum_ *)e)->Next(1, &x, &got) == 0) {
        int d = -1;
        ((IPin_ *)x)->QueryDirection(&d);
        if (d == dir && idx-- == 0) { p = (IPin_ *)x; break; }
        ((Unk *)x)->Release();
    }
    ((Unk *)e)->Release();
    return p;
}
LL secs(double s) { return (LL)(s * 1e7 + 0.5); }
} // namespace

/* ---- 1. graph building */
TEST(dshow_contract_graph_building)
{
    init_data();
    CHECK(!g_mp3.empty());
    Graph2 G;
    CHECK(G.g != nullptr);
    if (!G.g || g_mp3.empty()) return;
    /* AddFilter: JoinFilterGraph, names, duplicates */
    SrcFilter a(&g_mp3, MEDIASUBTYPE_MPEG1Audio_), b(&g_mp3, GUID_NULL_);
    auto n1 = WS("src");
    CHECK_EQ(G.g->AddFilter(&a, n1.data()), 0);
    CHECK_EQ(a.joins, 1);
    CHECK(a.graph != nullptr && a.name == "src");
    IGraphVersion_ *gv = nullptr;   /* IGraphVersion: the counter moves on every graph change */
    CHECK_EQ(G.g->QueryInterface(&IID_IGraphVersion_, (void **)&gv), 0);
    long v0 = -1, v1 = -1;
    CHECK(gv != nullptr);
    if (gv) {
        CHECK_EQ(gv->QueryVersion(nullptr), E_POINTER_);
        CHECK_EQ(gv->QueryVersion(&v0), 0);
    }
    CHECK_EQ(G.g->AddFilter(&b, n1.data()), VFW_S_DUPLICATE_NAME_);
    if (gv) {
        CHECK_EQ(gv->QueryVersion(&v1), 0);
        CHECK(v1 > v0);
        gv->Release();
    }
    CHECK(b.name != "src" && b.name.compare(0, 3, "src") == 0);
    CHECK_EQ(G.g->AddFilter(nullptr, n1.data()), E_POINTER_);
    CHECK_EQ(G.nfilters(), 2);
    IBaseFilter_ *f = G.find("src");
    CHECK(f == static_cast<IBaseFilter_ *>(&a));
    if (f) f->Release();
    auto nope = WS("nope");
    IBaseFilter_ *none = nullptr;
    CHECK_EQ(G.g->FindFilterByName(nope.data(), &none), VFW_E_NOT_FOUND_);
    /* EnumFilters: most recently added first; Skip/Reset/Clone */
    void *e = nullptr;
    CHECK_EQ(G.g->EnumFilters(&e), 0);
    void *x = nullptr;
    uint32_t got = 0;
    CHECK_EQ(((IEnum_ *)e)->Next(1, &x, &got), 0);
    CHECK(x == (void *)static_cast<IBaseFilter_ *>(&b));
    if (x) ((Unk *)x)->Release();
    void *c = nullptr;
    CHECK_EQ(((IEnum_ *)e)->Clone(&c), 0);
    CHECK_EQ(((IEnum_ *)e)->Skip(1), 0);
    CHECK_EQ(((IEnum_ *)e)->Next(1, &x, &got), 1);   /* S_FALSE: none left */
    CHECK_EQ(((IEnum_ *)e)->Reset(), 0);
    CHECK_EQ(((IEnum_ *)c)->Next(1, &x, &got), 0);   /* the clone kept its position */
    CHECK(x == (void *)static_cast<IBaseFilter_ *>(&a));
    if (x) ((Unk *)x)->Release();
    ((Unk *)c)->Release();
    ((Unk *)e)->Release();
    CHECK_EQ(G.g->RemoveFilter(&b), 0);
    CHECK_EQ(b.leaves, 1);
    CHECK_EQ(G.g->RemoveFilter(&b), VFW_E_NOT_FOUND_);
    CHECK_EQ(G.nfilters(), 1);

    /* Render(MP3 stream) builds source -> splitter -> decoder -> DirectSound renderer, visible in the graph */
    CHECK_EQ(G.g->Render(static_cast<IPin_ *>(&a.pin)), 0);
    CHECK_EQ(G.nfilters(), 4);
    IBaseFilter_ *sp = G.find("MPEG-I Stream Splitter"), *dec = G.find("MPEG Audio Decoder"), *rend = G.find("DirectSound Device");
    CHECK(sp && dec && rend);
    IPin_ *spin = pin_of(sp, PINDIR_INPUT), *sout = pin_of(sp, PINDIR_OUTPUT), *din = pin_of(dec, PINDIR_INPUT);
    IPin_ *dout = pin_of(dec, PINDIR_OUTPUT), *rin = pin_of(rend, PINDIR_INPUT);
    CHECK(spin && sout && din && dout && rin);
    if (!(spin && sout && din && dout && rin)) return;
    CHECK(a.pin.peer == spin);
    IPin_ *pp = nullptr;
    CHECK_EQ(rin->ConnectedTo(&pp), 0);
    CHECK(pp == dout);
    if (pp) pp->Release();
    MT mt = {};
    CHECK_EQ(rin->ConnectionMediaType(&mt), 0);
    CHECK(geq(mt.major, MEDIATYPE_Audio_) && geq(mt.sub, MEDIASUBTYPE_PCM_) && geq(mt.format, FORMAT_WaveFormatEx_));
    CHECK(mt.cb >= 16 && mt.pb && (((WFX *)mt.pb)->rate == 32000 || ((WFX *)mt.pb)->rate == 44100 || ((WFX *)mt.pb)->rate == 48000) && ((WFX *)mt.pb)->ch == 2 && ((WFX *)mt.pb)->bits == 16);
    PinInfo pi = {};
    CHECK_EQ(rin->QueryPinInfo(&pi), 0);
    CHECK(pi.filter == (void *)rend && pi.dir == PINDIR_INPUT);
    if (pi.filter) ((Unk *)pi.filter)->Release();
    FilterInfo fi = {};
    CHECK_EQ(rend->QueryFilterInfo(&fi), 0);
    CHECK(A(fi.name) == "DirectSound Device");
    WCH *vend = nullptr;
    CHECK_EQ(rend->QueryVendorInfo(&vend), E_NOTIMPL_);   /* the SDK's answer for a filter without vendor info */
    uint32_t nint = 4;
    IPin_ *ints[4];
    CHECK_EQ(din->QueryInternalConnections(ints, &nint), 0);
    CHECK(nint == 1 && ints[0] == dout);
    if (nint == 1) ints[0]->Release();
    /* ConnectDirect: directions, already connected, types; Disconnect / Reconnect */
    CHECK_EQ(G.g->ConnectDirect(rin, dout, nullptr), VFW_E_INVALID_DIRECTION_);
    CHECK_EQ(G.g->ConnectDirect(dout, rin, nullptr), VFW_E_ALREADY_CONNECTED_);
    CHECK_EQ(G.g->Disconnect(rin), 0);
    CHECK_EQ(rin->ConnectedTo(&pp), VFW_E_NOT_CONNECTED_);
    CHECK_EQ(dout->ConnectedTo(&pp), VFW_E_NOT_CONNECTED_);
    CHECK_EQ(G.g->Disconnect(rin), 1);   /* S_FALSE: not connected */
    CHECK_EQ(G.g->Disconnect(sout), 0);
    CHECK_EQ(G.g->ConnectDirect(sout, rin, nullptr), VFW_E_NO_ACCEPTABLE_TYPES_);   /* Audio/MP3 into a PCM renderer */
    CHECK_EQ(G.g->ConnectDirect(sout, din, nullptr), 0);
    CHECK_EQ(G.g->ConnectDirect(dout, rin, nullptr), 0);
    CHECK_EQ(G.g->Reconnect(rin), 0);
    CHECK_EQ(rin->ConnectedTo(&pp), 0);
    if (pp) pp->Release();
    /* RemoveFilter disconnects the pins; Connect(source, renderer input) is intelligent: through splitter and decoder */
    CHECK_EQ(G.g->RemoveFilter(sp), 0);
    CHECK_EQ(a.pin.peer == nullptr, 1);
    CHECK_EQ(G.nfilters(), 3);
    CHECK_EQ(G.g->Connect(static_cast<IPin_ *>(&a.pin), rin), VFW_E_ALREADY_CONNECTED_);   /* rin is still fed by the decoder */
    CHECK_EQ(G.g->Disconnect(rin), 0);
    CHECK_EQ(G.g->Connect(static_cast<IPin_ *>(&a.pin), rin), 0);   /* intelligent: a new splitter, the decoder already there */
    CHECK_EQ(G.nfilters(), 4);
    for (Unk *u : {(Unk *)sp, (Unk *)dec, (Unk *)rend, (Unk *)spin, (Unk *)sout, (Unk *)din, (Unk *)dout, (Unk *)rin}) u->Release();
    if (mt.pb) CoTaskMemFree(mt.pb);
}

TEST(dshow_contract_render_types)
{
    init_data();
    Graph2 G;
    if (!G.g) return;
    /* WAVE: PCM through the parser; the type is sniffed when the pin says MEDIASUBTYPE_NULL */
    LL d = 0;
    SrcFilter w(&g_wav, GUID_NULL_), m(&g_mp3, GUID_NULL_), w8(&g_wav8, MEDIASUBTYPE_WAVE_);
    {
        Graph2 A;
        CHECK_EQ(A.g->AddFilter(&w, nullptr), 0);
        CHECK_EQ(A.g->Render(static_cast<IPin_ *>(&w.pin)), 0);
        CHECK_EQ(A.nfilters(), 3);
        IBaseFilter_ *wp = A.find("WAVE Parser"), *rn = A.find("DirectSound Device");
        CHECK(wp != nullptr && rn != nullptr);
        if (wp) wp->Release();
        if (rn) rn->Release();
        CHECK_EQ(A.ms->GetDuration(&d), 0);
        CHECK_EQ(d, secs(1.0));   /* exact: data bytes / block align */
    }
    {   /* MP3 with MEDIASUBTYPE_NULL is sniffed (frame sync / ID3), duration from a full scan */
        Graph2 B;
        B.g->AddFilter(&m, nullptr);
        CHECK_EQ(B.g->Render(static_cast<IPin_ *>(&m.pin)), 0);
        CHECK_EQ(B.ms->GetDuration(&d), 0);
        CHECK(d > secs(1.4) && d < secs(1.6));
        CHECK(m.pin.reads > 0);
    }
    {   /* a WAVE_FORMAT_EXTENSIBLE PCM file with the mono payload plays too */
        Graph2 C;
        C.g->AddFilter(&w8, nullptr);
        CHECK_EQ(C.g->Render(static_cast<IPin_ *>(&w8.pin)), 0);
        CHECK_EQ(C.ms->GetDuration(&d), 0);
        CHECK_EQ(d, secs(0.5));
    }
    /* nothing else renders: a wave codec the environment lacks, unknown bytes, a non-stream type */
    Graph2 F;
    SrcFilter ad(&g_adpcm, MEDIASUBTYPE_WAVE_), junk(&g_junk, GUID_NULL_), vid(&g_junk, GUID_NULL_, MEDIATYPE_Audio_);
    F.g->AddFilter(&ad, nullptr);
    F.g->AddFilter(&junk, nullptr);
    F.g->AddFilter(&vid, nullptr);
    CHECK_EQ(F.g->Render(static_cast<IPin_ *>(&ad.pin)), VFW_E_CANNOT_RENDER_);
    CHECK_EQ(F.g->Render(static_cast<IPin_ *>(&junk.pin)), VFW_E_CANNOT_RENDER_);
    CHECK_EQ(F.g->Render(static_cast<IPin_ *>(&vid.pin)), VFW_E_CANNOT_RENDER_);
    CHECK_EQ(F.nfilters(), 3);   /* a failed Render leaves no filter behind */
    CHECK_EQ(F.g->Connect(static_cast<IPin_ *>(&junk.pin), pin_of(&ad, PINDIR_OUTPUT)), VFW_E_INVALID_DIRECTION_);
    /* a read error while the source is pulled */
    g_junk = g_mp3;
    junk.pin.fail_reads = true;
    CHECK(F.g->Render(static_cast<IPin_ *>(&junk.pin)) < 0);
    g_junk.assign(4096, 0x55);
    /* RenderFile / AddSourceFilter through the layer's files */
    char dir[256];
    snprintf(dir, sizeof dir, "%s/dshow_contract", getenv("FNVNX_ROOT") ? getenv("FNVNX_ROOT") : "/tmp");
    mkdir(dir, 0755);
    fnvnx_path_mount("C:\\dshowtest\\", dir);
    auto put = [&](const char *name, const std::vector<uint8_t> &v) {
        std::string p = std::string(dir) + "/" + name;
        FILE *f = fopen(p.c_str(), "wb");
        if (f) { fwrite(v.data(), 1, v.size(), f); fclose(f); }
    };
    put("a.mp3", g_mp3);
    put("b.wav", g_wav);
    put("c.bin", g_junk);
    Graph2 H;
    auto fa = WS("C:\\dshowtest\\a.mp3"), fb = WS("C:\\dshowtest\\b.wav"), fc = WS("C:\\dshowtest\\c.bin"), fd = WS("C:\\dshowtest\\none.mp3");
    CHECK_EQ(H.g->RenderFile(fa.data(), nullptr), 0);
    CHECK_EQ(H.nfilters(), 4);   /* file source, splitter, decoder, renderer */
    CHECK_EQ(H.g->RenderFile(fc.data(), nullptr), VFW_E_UNKNOWN_FILE_TYPE_);
    CHECK_EQ(H.g->RenderFile(fd.data(), nullptr), (HR)0x80070002);   /* ERROR_FILE_NOT_FOUND */
    IBaseFilter_ *src = nullptr;
    auto nm = WS("wavsrc");
    CHECK_EQ(H.g->AddSourceFilter(fb.data(), nm.data(), &src), 0);
    CHECK(src != nullptr);
    IFileSource_ *fs = qi<IFileSource_>(src, IID_IFileSourceFilter_);
    WCH *cur = nullptr;
    CHECK(fs && fs->GetCurFile(&cur, nullptr) == 0 && A(cur) == "C:\\dshowtest\\b.wav");
    if (fs) fs->Release();
    IBaseFilter_ *none2 = nullptr;
    CHECK_EQ(H.g->AddSourceFilter(fc.data(), nullptr, &none2), VFW_E_UNKNOWN_FILE_TYPE_);
    if (src) src->Release();
    /* SetLogFile: one line per build step goes to the handle; Abort / ShouldOperationContinue */
    std::string lp = std::string(dir) + "/graph.log";
    HANDLE hl = CreateFileA("C:\\dshowtest\\graph.log", GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
    CHECK(hl != INVALID_HANDLE_VALUE);
    CHECK_EQ(H.g->SetLogFile((uintptr_t)hl), 0);
    CHECK_EQ(H.g->RenderFile(fb.data(), nullptr), 0);
    H.g->SetLogFile(0);
    CloseHandle(hl);
    std::vector<uint8_t> log;
    load(lp.c_str(), log);
    CHECK(log.size() > 20);
    CHECK_EQ(H.g->ShouldOperationContinue(), 0);
    CHECK_EQ(H.g->Abort(), 0);
    CHECK_EQ(H.g->ShouldOperationContinue(), 1);
    CHECK_EQ(H.g->RenderFile(fb.data(), nullptr), 0);   /* the next operation starts clean */
    CHECK_EQ(H.g->ShouldOperationContinue(), 0);
}

namespace {
struct Var { uint16_t vt, r1, r2, r3; union { int32_t l; double d; const WCH *bstr; void *p; int64_t ll; }; };
struct DispParams { Var *rg; long *named; unsigned cargs, cnamed; };
const uint16_t VT_I4_ = 3, VT_R8_ = 5, VT_BSTR_ = 8, VT_BYREF_ = 0x4000;
struct VW : IDispatch_ { virtual HR put_Caption(const WCH *) = 0; virtual HR get_Caption(void **) = 0; };
struct BV : IDispatch_ { virtual HR get_AvgTimePerFrame(double *) = 0; };

/* a ready graph: the 1 s WAV rendered through the parser into the renderer */
struct WavGraph : Graph2 {
    SrcFilter src;
    WavGraph() : src(&g_wav, MEDIASUBTYPE_WAVE_)
    {
        g->AddFilter(&src, nullptr);
        g->Render(static_cast<IPin_ *>(&src.pin));
    }
    ~WavGraph() { mc->Stop(); g->RemoveFilter(&src); }
};
} // namespace

/* ---- 2. control, state, clock, seeking, position */
TEST(dshow_contract_control_and_seeking)
{
    init_data();
    {   /* an empty graph: Run succeeds (SDK), the state machine is real, no seekable stream yet */
        Graph2 E;
        long st = -1;
        double dur = 0;
        CHECK_EQ(E.mc->Run(), 0);
        CHECK_EQ(E.mc->GetState(0, &st), 0);
        CHECK_EQ(st, State_Running);
        CHECK_EQ(E.mc->Pause(), 0);
        E.mc->GetState(100, &st);
        CHECK_EQ(st, State_Paused);
        CHECK_EQ(E.mc->StopWhenReady(), 0);
        E.mc->GetState(0, &st);
        CHECK_EQ(st, State_Stopped);
        CHECK_EQ(E.mp->get_Duration(&dur), VFW_E_NOT_CONNECTED_);
        LL d = 0;
        CHECK_EQ(E.ms->GetDuration(&d), VFW_E_NOT_CONNECTED_);
    }
    WavGraph W;
    CHECK(W.mc && W.ms && W.mp && W.mf);
    if (!(W.mc && W.ms && W.mp && W.mf)) return;
    long st = -1;
    int fs = -1;
    CHECK_EQ(W.mc->Pause(), 0);
    CHECK_EQ(W.src.st, State_Paused);   /* every filter of the graph changes state, the game's too */
    CHECK_EQ(W.mf->GetState(0, &fs), 0);
    CHECK_EQ(fs, State_Paused);
    /* IMediaFilter: class, the reference clock is the renderer's */
    Guid cid = {};
    CHECK_EQ(W.mf->GetClassID(&cid), 0);
    CHECK(geq(cid, CLSID_FilterGraph_));
    CHECK_EQ(W.mc->Run(), 0);
    CHECK_EQ(W.src.st, State_Running);
    IReferenceClock_ *clk = nullptr;
    CHECK_EQ(W.mf->GetSyncSource(&clk), 0);
    CHECK(clk != nullptr);
    LL t1 = 0, t2 = 0;
    if (clk) {
        clk->GetTime(&t1);
        play(200);
        clk->GetTime(&t2);
        CHECK(t2 - t1 > 1200000 && t2 - t1 < 2800000);   /* 100 ns units, from frames rendered (0.2 s) */
    }
    IReferenceClock_ *none = nullptr;
    CHECK_EQ(W.mf->SetSyncSource(nullptr), 0);
    CHECK_EQ(W.mf->GetSyncSource(&none), 0);
    CHECK(none == nullptr);
    CHECK_EQ(W.g->SetDefaultSyncSource(), 0);
    CHECK_EQ(W.mf->GetSyncSource(&none), 0);
    CHECK(none != nullptr);
    if (none) none->Release();
    if (clk) clk->Release();
    CHECK_EQ(W.mc->Stop(), 0);

    /* IMediaSeeking */
    uint32_t caps = 0, want = SEEK_CanSeekAbsolute | SEEK_CanGetDuration;
    CHECK_EQ(W.ms->GetCapabilities(&caps), 0);
    CHECK_EQ(caps, 0x3F);
    CHECK_EQ(W.ms->CheckCapabilities(&want), 0);
    uint32_t mix = SEEK_CanSeekAbsolute | 0x40;   /* can't play backwards */
    CHECK_EQ(W.ms->CheckCapabilities(&mix), 1);
    CHECK_EQ(mix, SEEK_CanSeekAbsolute);
    uint32_t bad = 0x40;
    CHECK_EQ(W.ms->CheckCapabilities(&bad), E_FAIL_);
    Guid f = {};
    CHECK_EQ(W.ms->IsFormatSupported(&TIME_FORMAT_MEDIA_TIME_), 0);
    CHECK_EQ(W.ms->IsFormatSupported(&TIME_FORMAT_SAMPLE_), 0);
    Guid frame = TIME_FORMAT_SAMPLE_;
    frame.a = 0x7b785570;   /* TIME_FORMAT_FRAME */
    CHECK_EQ(W.ms->IsFormatSupported(&frame), 1);
    CHECK_EQ(W.ms->QueryPreferredFormat(&f), 0);
    CHECK(geq(f, TIME_FORMAT_MEDIA_TIME_));
    CHECK_EQ(W.ms->IsUsingTimeFormat(&TIME_FORMAT_MEDIA_TIME_), 0);
    CHECK_EQ(W.ms->SetTimeFormat(&frame), E_INVALIDARG_);
    LL smp = 0, d = 0, c = 0, s = 0;
    CHECK_EQ(W.ms->ConvertTimeFormat(&smp, &TIME_FORMAT_SAMPLE_, secs(0.5), nullptr), 0);
    CHECK_EQ(smp, 4000);
    CHECK_EQ(W.ms->GetDuration(&d), 0);
    CHECK_EQ(d, secs(1.0));
    CHECK_EQ(W.ms->GetStopPosition(&s), 0);
    CHECK_EQ(s, d);
    LL e0 = -1, l0 = -1;
    CHECK_EQ(W.ms->GetAvailable(&e0, &l0), 0);
    CHECK(e0 == 0 && l0 == d);
    /* SetPositions: every flag */
    LL cur = secs(0.5);
    CHECK_EQ(W.ms->SetPositions(&cur, SEEK_Absolute | SEEK_ReturnTime, nullptr, SEEK_NoPositioning), 0);
    CHECK_EQ(cur, secs(0.5));
    CHECK_EQ(W.ms->GetCurrentPosition(&c), 0);
    CHECK_EQ(c, secs(0.5));
    cur = secs(0.25);
    CHECK_EQ(W.ms->SetPositions(&cur, SEEK_Relative | SEEK_ReturnTime, nullptr, SEEK_NoPositioning), 0);
    CHECK_EQ(cur, secs(0.75));
    cur = -secs(0.5);
    CHECK_EQ(W.ms->SetPositions(&cur, SEEK_Incremental | SEEK_ReturnTime | SEEK_KeyFrame | SEEK_NoFlush, nullptr, SEEK_NoPositioning), 0);
    CHECK_EQ(cur, secs(0.25));
    LL stop = secs(0.8);
    CHECK_EQ(W.ms->SetPositions(nullptr, SEEK_NoPositioning, &stop, SEEK_Absolute), 0);
    CHECK_EQ(W.ms->GetPositions(&c, &s), 0);
    CHECK(c == secs(0.25) && s == secs(0.8));
    stop = secs(0.1);
    CHECK_EQ(W.ms->SetPositions(nullptr, SEEK_NoPositioning, &stop, SEEK_Relative | SEEK_ReturnTime), 0);   /* 0.8 + 0.1 */
    CHECK_EQ(stop, secs(0.9));
    stop = secs(0.2);
    CHECK_EQ(W.ms->SetPositions(&cur, SEEK_Absolute, &stop, SEEK_Absolute), VFW_E_START_TIME_AFTER_END_);   /* start 0.25 > stop 0.2 */
    cur = -1;
    CHECK_EQ(W.ms->SetPositions(&cur, SEEK_Absolute, nullptr, SEEK_NoPositioning), E_INVALIDARG_);
    stop = secs(1.0);
    W.ms->SetPositions(nullptr, SEEK_NoPositioning, &stop, SEEK_Absolute);
    /* rate: 2x plays twice as fast through the voice's frequency; 0 is invalid */
    double r = 0;
    CHECK_EQ(W.ms->SetRate(0.0), E_INVALIDARG_);
    CHECK_EQ(W.ms->SetRate(2.0), 0);
    CHECK_EQ(W.ms->GetRate(&r), 0);
    CHECK_NEAR(r, 2.0, 1e-12);
    cur = 0;
    W.ms->SetPositions(&cur, SEEK_Absolute, nullptr, SEEK_NoPositioning);
    W.mc->Run();
    play(300);
    W.ms->GetCurrentPosition(&c);
    CHECK(c > secs(0.45) && c < secs(0.8));
    W.mc->Pause();
    CHECK_EQ(W.ms->SetRate(1.0), 0);
    /* preroll round-trips through IMediaPosition (REFTIME) and IMediaSeeking (100 ns) */
    CHECK_EQ(W.mp->put_PrerollTime(0.25), 0);
    LL pre = 0;
    CHECK_EQ(W.ms->GetPreroll(&pre), 0);
    CHECK_EQ(pre, secs(0.25));
    double pt = 0;
    CHECK_EQ(W.mp->get_PrerollTime(&pt), 0);
    CHECK_NEAR(pt, 0.25, 1e-9);

    /* IMediaPosition: the same engine in seconds; the stop time ends playback with EC_COMPLETE */
    double dur = 0, pos = 0, rt = 0;
    long can = 0;
    CHECK_EQ(W.mp->get_Duration(&dur), 0);
    CHECK_NEAR(dur, 1.0, 1e-9);
    CHECK_EQ(W.mp->put_CurrentPosition(0.25), 0);
    CHECK_EQ(W.mp->get_CurrentPosition(&pos), 0);
    CHECK_NEAR(pos, 0.25, 1e-6);
    CHECK_EQ(W.mp->put_CurrentPosition(-1.0), E_INVALIDARG_);
    CHECK_EQ(W.mp->put_StopTime(0.5), 0);
    CHECK_EQ(W.mp->get_StopTime(&pos), 0);
    CHECK_NEAR(pos, 0.5, 1e-9);
    CHECK_EQ(W.mp->put_StopTime(0.1), VFW_E_START_TIME_AFTER_END_);
    CHECK_EQ(W.mp->put_Rate(1.5), 0);
    CHECK_EQ(W.mp->get_Rate(&rt), 0);
    CHECK_NEAR(rt, 1.5, 1e-12);
    CHECK_EQ(W.mp->put_Rate(1.0), 0);
    CHECK_EQ(W.mp->CanSeekForward(&can), 0);
    CHECK_EQ(can, -1);
    CHECK_EQ(W.mp->CanSeekBackward(&can), 0);
    CHECK_EQ(can, -1);
    long code = 0;
    CHECK_EQ(W.mc->Run(), 0);
    play(500);
    CHECK_EQ(W.me->WaitForCompletion(1000, &code), 0);
    CHECK_EQ(code, EC_COMPLETE_);
    W.mp->get_CurrentPosition(&pos);
    CHECK_NEAR(pos, 0.5, 0.03);
    /* seeking in MP3: the frame holding the time, sample-accurate */
    SrcFilter m(&g_mp3, MEDIASUBTYPE_MPEG1Audio_);
    Graph2 M;
    M.g->AddFilter(&m, nullptr);
    CHECK_EQ(M.g->Render(static_cast<IPin_ *>(&m.pin)), 0);
    cur = secs(1.0);
    CHECK_EQ(M.ms->SetPositions(&cur, SEEK_Absolute | SEEK_ReturnTime, nullptr, SEEK_NoPositioning), 0);
    CHECK_EQ(M.ms->GetCurrentPosition(&c), 0);
    CHECK(c >= secs(1.0) - secs(0.03) && c <= secs(1.0));
    M.mc->Run();
    std::vector<float> a = play(200);
    CHECK(rms(a, 0) > 0.1);
    M.mc->Stop();
    M.g->RemoveFilter(&m);
}

/* ---- 3. IBasicAudio, IDispatch */
TEST(dshow_contract_audio_and_dispatch)
{
    init_data();
    {
        Graph2 E;   /* no audio renderer in the graph: the forwarded interface has no target */
        CHECK_EQ(E.ba->put_Volume(0), E_NOINTERFACE_);
        long v = 0;
        CHECK_EQ(E.ba->get_Volume(&v), E_NOINTERFACE_);
    }
    WavGraph W;
    if (!W.ba) return;
    long v = 1, b = 1;
    CHECK_EQ(W.ba->put_Volume(-10001), E_INVALIDARG_);
    CHECK_EQ(W.ba->put_Volume(1), E_INVALIDARG_);
    CHECK_EQ(W.ba->put_Balance(10001), E_INVALIDARG_);
    CHECK_EQ(W.ba->put_Balance(-10001), E_INVALIDARG_);
    CHECK_EQ(W.ba->get_Volume(nullptr), E_POINTER_);
    CHECK_EQ(W.ba->put_Balance(-10000), 0);   /* right side silent */
    CHECK_EQ(W.ba->get_Balance(&b), 0);
    CHECK_EQ(b, -10000);
    W.mc->Run();
    std::vector<float> a = play(200);
    CHECK(rms(a, 0) > 0.1 && rms(a, 1) < 1e-4);
    W.ba->put_Balance(10000);
    a = play(100);
    CHECK(rms(a, 1) > 0.1 && rms(a, 0) < 1e-4);
    W.ba->put_Balance(0);
    CHECK_EQ(W.ba->put_Volume(-10000), 0);
    a = play(100);
    CHECK(rms(a, 0) < 1e-6 && rms(a, 1) < 1e-6);
    CHECK_EQ(W.ba->put_Volume(-600), 0);
    CHECK_EQ(W.ba->get_Volume(&v), 0);
    CHECK_EQ(v, -600);
    W.mc->Stop();

    /* IDispatch of the dual interfaces: type info count 1, names (case-insensitive), Invoke with coercion */
    unsigned n = 9;
    CHECK_EQ(W.ba->GetTypeInfoCount(&n), 0);
    CHECK_EQ(n, 1);
    void *ti = nullptr;
    CHECK_EQ(W.ba->GetTypeInfo(1, 0, &ti), DISP_E_BADINDEX_);
    long ids[2] = {0, 0};
    auto vol = WS("volume"), bal = WS("BALANCE"), what = WS("Nonesuch");
    WCH *names[2] = {vol.data(), nullptr};
    CHECK_EQ(W.ba->GetIDsOfNames(&GUID_NULL_, names, 1, 0, ids), 0);
    long vol_id = ids[0];
    names[0] = bal.data();
    CHECK_EQ(W.ba->GetIDsOfNames(&GUID_NULL_, names, 1, 0, ids), 0);
    long bal_id = ids[0];
    CHECK(vol_id != bal_id && vol_id != -1);
    names[0] = what.data();
    CHECK_EQ(W.ba->GetIDsOfNames(&GUID_NULL_, names, 1, 0, ids), DISP_E_UNKNOWNNAME_);
    CHECK_EQ(ids[0], -1);
    CHECK_EQ(W.ba->GetIDsOfNames(&IID_IPin_, names, 1, 0, ids), DISP_E_UNKNOWNINTERFACE_);
    long put_id = -3;
    Var arg = {};
    DispParams dp = {&arg, &put_id, 1, 1};
    arg.vt = VT_I4_;
    arg.l = -500;
    CHECK_EQ(W.ba->Invoke(vol_id, &GUID_NULL_, 0, 4, &dp, nullptr, nullptr, nullptr), 0);
    CHECK_EQ(W.ba->get_Volume(&v), 0);
    CHECK_EQ(v, -500);
    arg.vt = VT_R8_;   /* coerced */
    arg.d = 250.0;
    CHECK_EQ(W.ba->Invoke(bal_id, &GUID_NULL_, 0, 4, &dp, nullptr, nullptr, nullptr), 0);
    CHECK_EQ(W.ba->get_Balance(&b), 0);
    CHECK_EQ(b, 250);
    auto s250 = WS("-300");
    arg.vt = VT_BSTR_;
    arg.bstr = s250.data();
    CHECK_EQ(W.ba->Invoke(bal_id, &GUID_NULL_, 0, 4, &dp, nullptr, nullptr, nullptr), 0);
    W.ba->get_Balance(&b);
    CHECK_EQ(b, -300);
    Var res = {};
    DispParams none = {nullptr, nullptr, 0, 0};
    CHECK_EQ(W.ba->Invoke(vol_id, &GUID_NULL_, 0, 2, &none, &res, nullptr, nullptr), 0);
    CHECK(res.vt == VT_I4_ && res.l == -500);
    CHECK_EQ(W.ba->Invoke(vol_id, &GUID_NULL_, 0, 2, &dp, &res, nullptr, nullptr), DISP_E_BADPARAMCOUNT_);
    CHECK_EQ(W.ba->Invoke(0x7777, &GUID_NULL_, 0, 1, &none, &res, nullptr, nullptr), DISP_E_MEMBERNOTFOUND_);
    arg.vt = VT_BSTR_;
    auto junk = WS("x");
    arg.bstr = junk.data();
    CHECK_EQ(W.ba->Invoke(vol_id, &GUID_NULL_, 0, 4, &dp, nullptr, nullptr, nullptr), DISP_E_TYPEMISMATCH_);
    /* IMediaControl: Run as a method, GetState with a by-ref out argument; IMediaPosition Duration as a property */
    auto run = WS("Run"), gs = WS("GetState"), dur = WS("Duration");
    WCH *nm[1] = {run.data()};
    CHECK_EQ(W.mc->GetIDsOfNames(&GUID_NULL_, nm, 1, 0, ids), 0);
    CHECK_EQ(W.mc->Invoke(ids[0], &GUID_NULL_, 0, 1, &none, nullptr, nullptr, nullptr), 0);
    int32_t state = -1;
    Var ga[2] = {};
    ga[1].vt = VT_I4_; ga[1].l = 0;                       /* rgvarg is in reverse order: the first parameter last */
    ga[0].vt = VT_I4_ | VT_BYREF_; ga[0].p = &state;
    DispParams gp = {ga, nullptr, 2, 0};
    nm[0] = gs.data();
    CHECK_EQ(W.mc->GetIDsOfNames(&GUID_NULL_, nm, 1, 0, ids), 0);
    CHECK_EQ(W.mc->Invoke(ids[0], &GUID_NULL_, 0, 1, &gp, nullptr, nullptr, nullptr), 0);
    CHECK_EQ(state, State_Running);
    nm[0] = dur.data();
    CHECK_EQ(W.mp->GetIDsOfNames(&GUID_NULL_, nm, 1, 0, ids), 0);
    CHECK_EQ(W.mp->Invoke(ids[0], &GUID_NULL_, 0, 2, &none, &res, nullptr, nullptr), 0);
    CHECK(res.vt == VT_R8_ && fabs(res.d - 1.0) < 1e-9);
}

/* ---- 4. events */
TEST(dshow_contract_events)
{
    init_data();
    WavGraph W;
    if (!W.me) return;
    uintptr_t h = 0;
    CHECK_EQ(W.me->GetEventHandle(&h), 0);
    CHECK(h != 0);
    CHECK_EQ(WaitForSingleObject((HANDLE)h, 0), WAIT_TIMEOUT);   /* empty queue: not signalled */
    long code = 0;
    intptr_t p1 = 0, p2 = 0;
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 0), E_ABORT_);
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 30), E_ABORT_);
    CHECK_EQ(W.me->WaitForCompletion(30, &code), E_ABORT_);
    /* IMediaEventSink: filters queue events, FIFO; the handle is signalled while the queue is non-empty */
    IMediaEventSink_ *sink = qi<IMediaEventSink_>(W.g, IID_IMediaEventSink_);
    CHECK(sink != nullptr);
    if (!sink) return;
    CHECK_EQ(sink->Notify(EC_ERRORABORT_, E_FAIL_, 0), 0);
    CHECK_EQ(sink->Notify(0x1234, 5, 6), 0);
    CHECK_EQ(WaitForSingleObject((HANDLE)h, 0), WAIT_OBJECT_0);
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 0), 0);
    CHECK(code == EC_ERRORABORT_ && p1 == E_FAIL_);
    CHECK_EQ(WaitForSingleObject((HANDLE)h, 0), WAIT_OBJECT_0);
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 0), 0);
    CHECK(code == 0x1234 && p1 == 5 && p2 == 6);
    CHECK_EQ(W.me->FreeEventParams(code, p1, p2), 0);
    CHECK_EQ(WaitForSingleObject((HANDLE)h, 0), WAIT_TIMEOUT);
    /* an abort event ends WaitForCompletion with its code */
    sink->Notify(EC_USERABORT_, 0, 0);
    CHECK_EQ(W.me->WaitForCompletion(100, &code), 0);
    CHECK_EQ(code, EC_USERABORT_);
    W.me->GetEvent(&code, &p1, &p2, 0);
    /* EC_COMPLETE exactly when the renderer has played the last frame; the graph's default handling aggregates */
    LL c = secs(0.9);
    W.ms->SetPositions(&c, SEEK_Absolute, nullptr, SEEK_NoPositioning);
    W.mc->Run();
    play(40);
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 0), E_ABORT_);   /* 0.1 s are left */
    play(300);
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 1000), 0);
    CHECK_EQ(code, EC_COMPLETE_);
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 0), E_ABORT_);   /* one EC_COMPLETE, not one per filter */
    /* default handling: cancel / restore report S_FALSE when already so */
    CHECK_EQ(W.me->CancelDefaultHandling(EC_COMPLETE_), 0);
    CHECK_EQ(W.me->CancelDefaultHandling(EC_COMPLETE_), 1);
    CHECK_EQ(W.me->RestoreDefaultHandling(EC_COMPLETE_), 0);
    CHECK_EQ(W.me->RestoreDefaultHandling(EC_COMPLETE_), 1);
    /* the notify window: a message with lInstanceData per queued event; AM_MEDIAEVENT_NONOTIFY mutes it */
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = [](HWND h, UINT m, WPARAM w, LPARAM l) -> LRESULT { return DefWindowProcA(h, m, w, l); };
    wc.lpszClassName = "DshowContractCls";
    CHECK(RegisterClassA(&wc) != 0);
    HWND hw = CreateWindowExA(0, "DshowContractCls", "t", 0, 0, 0, 8, 8, NULL, NULL, NULL, NULL);
    CHECK(hw != NULL);
    long fl = -1;
    CHECK_EQ(W.me->GetNotifyFlags(&fl), 0);
    CHECK_EQ(fl, 0);
    CHECK_EQ(W.me->SetNotifyWindow((uintptr_t)hw, 0x8001, 77), 0);
    sink->Notify(0x1235, 0, 0);
    MSG m;
    CHECK(PeekMessageA(&m, NULL, 0x8001, 0x8001, PM_REMOVE));
    CHECK(m.message == 0x8001 && m.lParam == 77 && m.hwnd == hw);
    CHECK_EQ(W.me->SetNotifyFlags(1), 0);
    CHECK_EQ(W.me->GetNotifyFlags(&fl), 0);
    CHECK_EQ(fl, 1);
    sink->Notify(0x1236, 0, 0);
    CHECK(!PeekMessageA(&m, NULL, 0x8001, 0x8001, PM_REMOVE));
    CHECK_EQ(W.me->SetNotifyFlags(2), E_INVALIDARG_);
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 0), 0);   /* the events are queued either way */
    CHECK_EQ(W.me->GetEvent(&code, &p1, &p2, 0), 0);
    CHECK_EQ(W.me->SetNotifyWindow(0, 0, 0), 0);
    sink->Release();
}

/* ---- 5. CLSID_FilterMapper / IFilterMapper */
namespace {
std::vector<std::string> matches(IFilterMapper_ *fm, uint32_t merit, int in, Guid imaj, Guid isub, int render, int out, Guid omaj, Guid osub,
                                 std::vector<Guid> *ids = nullptr)
{
    std::vector<std::string> v;
    void *e = nullptr;
    if (fm->EnumMatchingFilters(&e, merit, in, imaj, isub, render, out, omaj, osub) < 0) return {"<error>"};
    void *r[1];
    uint32_t got = 0;
    while (((IEnum_ *)e)->Next(1, r, &got) == 0 && got == 1) {
        RegFilter *rf = (RegFilter *)r[0];
        v.push_back(A(rf->name));
        if (ids) ids->push_back(rf->clsid);
        CoTaskMemFree(rf->name);
        CoTaskMemFree(rf);
    }
    ((Unk *)e)->Release();
    std::sort(v.begin(), v.end());
    return v;
}
bool is(const std::vector<std::string> &v, std::initializer_list<const char *> l)
{
    std::vector<std::string> w(l.begin(), l.end());
    std::sort(w.begin(), w.end());
    return v == w;
}
} // namespace

TEST(dshow_contract_filter_mapper)
{
    init_data();
    IFilterMapper_ *fm = nullptr, *fm2 = nullptr;
    CHECK_EQ(CoCreateInstance(&CLSID_Mapper_, nullptr, 1, G_(IID_IFilterMapper_), (void **)&fm), 0);
    CHECK_EQ(CoCreateInstance(&CLSID_Mapper_, nullptr, 1, G_(IID_IFilterMapper_), (void **)&fm2), 0);   /* the game asks twice */
    if (!fm || !fm2) return;
    const Guid N = GUID_NULL_;
    CHECK(is(matches(fm, 0, 0, N, N, 0, 0, N, N), {"MPEG-I Stream Splitter", "MPEG Audio Decoder", "WAVE Parser", "DirectSound Device"}));
    CHECK(is(matches(fm, 0, 1, MEDIATYPE_Stream_, MEDIASUBTYPE_MPEG1Audio_, 0, 0, N, N), {"MPEG-I Stream Splitter"}));
    CHECK(is(matches(fm, 0, 1, MEDIATYPE_Stream_, MEDIASUBTYPE_WAVE_, 0, 0, N, N), {"WAVE Parser"}));
    CHECK(is(matches(fm, 0, 1, MEDIATYPE_Stream_, N, 0, 0, N, N), {"MPEG-I Stream Splitter", "WAVE Parser"}));
    CHECK(is(matches(fm, 0, 1, MEDIATYPE_Audio_, MEDIASUBTYPE_MP3_, 0, 0, N, N), {"MPEG Audio Decoder"}));
    CHECK(is(matches(fm, 0, 0, N, N, 1, 0, N, N), {"DirectSound Device"}));   /* renderers */
    CHECK(is(matches(fm, 0, 0, N, N, 0, 1, MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_), {"MPEG Audio Decoder", "WAVE Parser"}));
    CHECK(is(matches(fm, 0x700000, 0, N, N, 0, 0, N, N), {"DirectSound Device"}));   /* merit */
    CHECK(is(matches(fm, 0, 1, MEDIATYPE_Stream_, MEDIASUBTYPE_MPEG1Audio_, 1, 0, N, N), {}));   /* no renderer takes a stream */
    std::vector<Guid> ids;
    matches(fm, 0, 1, MEDIATYPE_Stream_, MEDIASUBTYPE_WAVE_, 0, 0, N, N, &ids);
    CHECK(ids.size() == 1 && geq(ids[0], CLSID_WAVEParser_));
    /* IEnumRegFilters: Skip / Reset / Clone */
    void *e = nullptr, *c = nullptr, *r[4];
    uint32_t got = 0;
    CHECK_EQ(fm->EnumMatchingFilters(&e, 0, 0, N, N, 0, 0, N, N), 0);
    CHECK_EQ(((IEnum_ *)e)->Skip(3), 0);
    CHECK_EQ(((IEnum_ *)e)->Clone(&c), 0);
    CHECK_EQ(((IEnum_ *)e)->Next(2, r, &got), 1);
    CHECK_EQ(got, 1);
    CoTaskMemFree(((RegFilter *)r[0])->name);
    CoTaskMemFree(r[0]);
    CHECK_EQ(((IEnum_ *)e)->Reset(), 0);
    CHECK_EQ(((IEnum_ *)e)->Next(4, r, &got), 0);
    for (uint32_t i = 0; i < got; i++) { CoTaskMemFree(((RegFilter *)r[i])->name); CoTaskMemFree(r[i]); }
    CHECK_EQ(((IEnum_ *)c)->Next(1, r, &got), 0);
    CoTaskMemFree(((RegFilter *)r[0])->name);
    CoTaskMemFree(r[0]);
    ((Unk *)c)->Release();
    ((Unk *)e)->Release();
    /* Register*: an in-memory registration set shared by both mapper objects */
    Guid X = {0x11223344, 1, 2, {1, 2, 3, 4, 5, 6, 7, 8}};
    auto nm = WS("Test Filter"), pin = WS("In");
    CHECK(fm->RegisterPin(X, pin.data(), 0, 0, 0, 0, N, nullptr) < 0);   /* the filter isn't registered yet */
    CHECK_EQ(fm->RegisterFilter(X, nm.data(), 0x500000), 0);
    CHECK_EQ(fm->RegisterPin(X, pin.data(), 0, 0, 0, 0, N, nullptr), 0);
    CHECK_EQ(fm->RegisterPinType(X, pin.data(), MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_), 0);
    CHECK(is(matches(fm2, 0, 1, MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_, 0, 0, N, N), {"Test Filter", "DirectSound Device"}));
    CHECK(is(matches(fm2, 0x600000, 1, MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_, 0, 0, N, N), {"DirectSound Device"}));
    CHECK_EQ(fm->UnregisterPin(X, pin.data()), 0);
    CHECK(is(matches(fm2, 0, 1, MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_, 0, 0, N, N), {"DirectSound Device"}));
    Guid mr = {};
    CHECK_EQ(fm->RegisterFilterInstance(X, nm.data(), &mr), 0);
    CHECK(!geq(mr, N));
    CHECK_EQ(fm->UnregisterFilterInstance(mr), 0);
    CHECK(fm->UnregisterFilterInstance(mr) < 0);
    CHECK_EQ(fm->UnregisterFilter(X), 0);
    CHECK(fm->UnregisterFilter(X) < 0);
    CHECK(is(matches(fm2, 0, 0, N, N, 0, 0, N, N), {"MPEG-I Stream Splitter", "MPEG Audio Decoder", "WAVE Parser", "DirectSound Device"}));
    /* the graph renders from the same registry: unregistering the WAVE parser leaves WAVE unrenderable */
    Graph2 G;
    SrcFilter w(&g_wav, MEDIASUBTYPE_WAVE_);
    G.g->AddFilter(&w, nullptr);
    CHECK_EQ(fm->UnregisterFilter(CLSID_WAVEParser_), 0);
    CHECK_EQ(G.g->Render(static_cast<IPin_ *>(&w.pin)), VFW_E_CANNOT_RENDER_);
    auto wp = WS("WAVE Parser");
    CHECK_EQ(fm->RegisterFilter(CLSID_WAVEParser_, wp.data(), MERIT_NORMAL_), 0);
    CHECK_EQ(fm->RegisterPin(CLSID_WAVEParser_, WS("Input").data(), 0, 0, 0, 0, N, nullptr), 0);
    CHECK_EQ(fm->RegisterPinType(CLSID_WAVEParser_, WS("Input").data(), MEDIATYPE_Stream_, MEDIASUBTYPE_WAVE_), 0);
    CHECK_EQ(fm->RegisterPin(CLSID_WAVEParser_, WS("Output").data(), 0, 1, 0, 0, N, nullptr), 0);
    CHECK_EQ(fm->RegisterPinType(CLSID_WAVEParser_, WS("Output").data(), MEDIATYPE_Audio_, MEDIASUBTYPE_PCM_), 0);
    CHECK_EQ(G.g->Render(static_cast<IPin_ *>(&w.pin)), 0);
    G.g->RemoveFilter(&w);
    fm->Release();
    fm2->Release();
}

/* ---- 6. QueryInterface on the graph, IObjectWithSite, no-video behaviour */
TEST(dshow_contract_interfaces)
{
    init_data();
    Graph2 G;
    if (!G.g) return;
    for (const Guid *iid : {&IID_IUnknown_, &IID_IFilterGraph_, &IID_IGraphBuilder_, &IID_IFilterGraph2_, &IID_IMediaControl_,
                            &IID_IMediaEvent_, &IID_IMediaEventEx_, &IID_IMediaEventSink_, &IID_IMediaFilter_, &IID_IPersist_,
                            &IID_IMediaSeeking_, &IID_IMediaPosition_, &IID_IBasicAudio_, &IID_IVideoWindow_, &IID_IBasicVideo_,
                            &IID_IObjectWithSite_, &IID_IDispatch_}) {
        Unk *u = nullptr;
        CHECK_EQ(G.g->QueryInterface(iid, (void **)&u), 0);
        if (u) u->Release();
    }
    void *junk = nullptr;
    CHECK_EQ(G.g->QueryInterface(&IID_IPin_, &junk), E_NOINTERFACE_);
    CHECK(junk == nullptr);
    /* identity: IUnknown from any interface is the same pointer */
    Unk *u1 = nullptr, *u2 = nullptr;
    G.g->QueryInterface(&IID_IUnknown_, (void **)&u1);
    G.mc->QueryInterface(&IID_IUnknown_, (void **)&u2);
    CHECK(u1 == u2);
    if (u1) u1->Release();
    if (u2) u2->Release();
    /* no video renderer in the graph: IVideoWindow / IBasicVideo answer E_NOINTERFACE, as an audio-only graph does */
    VW *vw = qi<VW>(G.g, IID_IVideoWindow_);
    BV *bv = qi<BV>(G.g, IID_IBasicVideo_);
    CHECK(vw && bv);
    if (vw && bv) {
        void *cap = nullptr;
        double fr = 0;
        unsigned n = 9;
        CHECK_EQ(vw->put_Caption(nullptr), E_NOINTERFACE_);
        CHECK_EQ(vw->get_Caption(&cap), E_NOINTERFACE_);
        CHECK_EQ(bv->get_AvgTimePerFrame(&fr), E_NOINTERFACE_);
        CHECK_EQ(vw->GetTypeInfoCount(&n), 0);
        CHECK_EQ(n, 1);
    }
    if (vw) vw->Release();
    if (bv) bv->Release();
    /* IObjectWithSite: the site round-trips and is released with the graph */
    IObjectWithSite_ *os = qi<IObjectWithSite_>(G.g, IID_IObjectWithSite_);
    SrcFilter site(&g_mp3, GUID_NULL_);
    void *got = nullptr;
    CHECK(os != nullptr);
    if (os) {
        CHECK(os->GetSite(&IID_IUnknown_, &got) < 0);
        CHECK_EQ(os->SetSite(&site), 0);
        CHECK_EQ(site.refs, 2);
        CHECK_EQ(os->GetSite(&IID_IBaseFilter_, &got), 0);
        CHECK(got == (void *)static_cast<IBaseFilter_ *>(&site));
        if (got) ((Unk *)got)->Release();
        CHECK_EQ(os->SetSite(nullptr), 0);
        CHECK_EQ(site.refs, 1);
        os->Release();
    }
}
