/* dshow.cpp - DirectShow's FilterGraph for the game's music (CLSID_FilterGraph through CoCreateInstance).
 *
 * The exe plays Data/Music (MP3) with its own source filter, an async reader over its archive files (0x00831e20),
 * and a graph (0x00830fd5..): CoCreateInstance(CLSID_FilterGraph, IID_IFilterGraph), AddFilter(source),
 * QueryInterface(IID_IGraphBuilder), Render(source output pin), then IBasicAudio, IMediaControl, IMediaPosition and
 * IMediaEvent from the graph. Here Render pulls the whole file through the pin's IAsyncReader (Length, SyncRead),
 * decodes it with minimp3 (CC0, ext/minimp3, tools/fetch.sh) and plays it through a looping mixer voice that a pump
 * thread keeps filled (streaming: the compressed file stays in memory, about a second of PCM is decoded ahead).
 * IMediaControl Run/Pause/Stop/GetState, IMediaPosition duration / position / seek, IBasicAudio volume and balance
 * (hundredths of a dB, as DirectShow), IMediaEvent EC_COMPLETE at the end of the track with an event handle that is
 * signalled while events wait. Other formats than MP3 fail Render with VFW_E_CANNOT_RENDER.
 *
 * The interfaces are declared here with DirectShow's vtable order (strmif.h / control.h); only what the game calls
 * does more than return E_NOTIMPL. */
#include "mixer.h"
#include "../os/os.h"
#include "fnvnx_win32_ext.h"

#define MINIMP3_IMPLEMENTATION
#if !defined(__aarch64__)
#define MINIMP3_NO_SIMD   /* x86 hosts: the shim owns the SSE headers; AArch64 (the device) keeps NEON */
#endif
#define MINIMP3_NO_STDIO
#include "minimp3_ex.h"

#include <math.h>
#include <new>
#include <stdlib.h>
#include <string.h>

extern "C" {
HANDLE CreateEventA(void *, BOOL, BOOL, LPCSTR);
BOOL SetEvent(HANDLE);
BOOL ResetEvent(HANDLE);
BOOL CloseHandle(HANDLE);
}

namespace {

typedef int32_t HR;   /* HRESULT: 32 bits (a long on the game's ILP32, not on an LP64 host) */
typedef double REFTIME;
const HR S_OK_ = 0, S_FALSE_ = 1, E_NOTIMPL_ = (HR)0x80004001L, E_NOINTERFACE_ = (HR)0x80004002L,
         E_POINTER_ = (HR)0x80004003L, E_FAIL_ = (HR)0x80004005L, E_OUTOFMEMORY_ = (HR)0x8007000EL,
         VFW_E_CANNOT_RENDER_ = (HR)0x80040218L, VFW_E_NOT_CONNECTED_ = (HR)0x80040209L;
const long EC_COMPLETE_ = 1;
enum { State_Stopped = 0, State_Paused = 1, State_Running = 2 };

struct Guid { uint32_t a; uint16_t b, c; uint8_t d[8]; };
bool eq(const void *x, const Guid &g) { return x && !memcmp(x, &g, sizeof g); }
#define DS_IID(n, a, b, c, ...) const Guid n = {a, b, c, {__VA_ARGS__}}
DS_IID(IID_IUnknown_, 0x00000000, 0x0000, 0x0000, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46);
DS_IID(IID_IDispatch_, 0x00020400, 0x0000, 0x0000, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46);
DS_IID(IID_IFilterGraph_, 0x56a8689f, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70);
DS_IID(IID_IGraphBuilder_, 0x56a868a9, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70);
DS_IID(IID_IMediaControl_, 0x56a868b1, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70);
DS_IID(IID_IMediaPosition_, 0x56a868b2, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70);
DS_IID(IID_IBasicAudio_, 0x56a868b3, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70);
DS_IID(IID_IMediaEvent_, 0x56a868b6, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70);
DS_IID(IID_IAsyncReader_, 0x56a868aa, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70);

/* the game's objects, called through their vtables */
struct Unk {
    virtual HR QueryInterface(const void *iid, void **out) = 0;
    virtual unsigned long AddRef() = 0;
    virtual unsigned long Release() = 0;
};
struct AsyncReader : Unk {   /* IAsyncReader */
    virtual HR RequestAllocator(void *, void *, void **) = 0;
    virtual HR Request(void *, uintptr_t) = 0;
    virtual HR WaitForNext(uint32_t, void **, uintptr_t *) = 0;
    virtual HR SyncReadAligned(void *) = 0;
    virtual HR SyncRead(long long pos, long len, uint8_t *buf) = 0;
    virtual HR Length(long long *total, long long *available) = 0;
    virtual HR BeginFlush() = 0;
    virtual HR EndFlush() = 0;
};

/* our interfaces, in DirectShow's order */
struct IFilterGraph_ : Unk {
    virtual HR AddFilter(Unk *filter, const uint16_t *name) = 0;
    virtual HR RemoveFilter(Unk *filter) = 0;
    virtual HR EnumFilters(void **) = 0;
    virtual HR FindFilterByName(const uint16_t *, void **) = 0;
    virtual HR ConnectDirect(Unk *, Unk *, const void *) = 0;
    virtual HR Reconnect(Unk *) = 0;
    virtual HR Disconnect(Unk *) = 0;
    virtual HR SetDefaultSyncSource() = 0;
};
struct IGraphBuilder_ : IFilterGraph_ {
    virtual HR Connect(Unk *, Unk *) = 0;
    virtual HR Render(Unk *pin) = 0;
    virtual HR RenderFile(const uint16_t *, const uint16_t *) = 0;
    virtual HR AddSourceFilter(const uint16_t *, const uint16_t *, void **) = 0;
    virtual HR SetLogFile(uintptr_t) = 0;
    virtual HR Abort() = 0;
    virtual HR ShouldOperationContinue() = 0;
};
struct IDispatch_ : Unk {
    virtual HR GetTypeInfoCount(unsigned *) = 0;
    virtual HR GetTypeInfo(unsigned, uint32_t, void **) = 0;
    virtual HR GetIDsOfNames(const void *, void *, unsigned, uint32_t, long *) = 0;
    virtual HR Invoke(long, const void *, uint32_t, uint16_t, void *, void *, void *, unsigned *) = 0;
};
struct IMediaControl_ : IDispatch_ {
    virtual HR Run() = 0;
    virtual HR Pause() = 0;
    virtual HR Stop() = 0;
    virtual HR GetState(long ms, long *state) = 0;
    virtual HR RenderFile(void *) = 0;
    virtual HR AddSourceFilter(void *, void **) = 0;
    virtual HR get_FilterCollection(void **) = 0;
    virtual HR get_RegFilterCollection(void **) = 0;
    virtual HR StopWhenReady() = 0;
};
struct IMediaEvent_ : IDispatch_ {
    virtual HR GetEventHandle(uintptr_t *h) = 0;
    virtual HR GetEvent(long *code, intptr_t *p1, intptr_t *p2, long ms) = 0;
    virtual HR WaitForCompletion(long ms, long *code) = 0;
    virtual HR CancelDefaultHandling(long) = 0;
    virtual HR RestoreDefaultHandling(long) = 0;
    virtual HR FreeEventParams(long, intptr_t, intptr_t) = 0;
};
struct IMediaPosition_ : IDispatch_ {
    virtual HR get_Duration(REFTIME *) = 0;
    virtual HR put_CurrentPosition(REFTIME) = 0;
    virtual HR get_CurrentPosition(REFTIME *) = 0;
    virtual HR get_StopTime(REFTIME *) = 0;
    virtual HR put_StopTime(REFTIME) = 0;
    virtual HR get_PrerollTime(REFTIME *) = 0;
    virtual HR put_PrerollTime(REFTIME) = 0;
    virtual HR put_Rate(double) = 0;
    virtual HR get_Rate(double *) = 0;
    virtual HR CanSeekForward(long *) = 0;
    virtual HR CanSeekBackward(long *) = 0;
};
struct IBasicAudio_ : IDispatch_ {
    virtual HR put_Volume(long) = 0;
    virtual HR get_Volume(long *) = 0;
    virtual HR put_Balance(long) = 0;
    virtual HR get_Balance(long *) = 0;
};

const uint32_t RING_FRAMES = 1 << 15;   /* ~0.7 s at 44.1 kHz */
const uint32_t AHEAD_FRAMES = RING_FRAMES - 4096;

struct Graph;
#define FORWARD_UNKNOWN                                                                                       \
    HR QueryInterface(const void *iid, void **out) override;                                                 \
    unsigned long AddRef() override;                                                                          \
    unsigned long Release() override;
#define DISPATCH_STUBS                                                                                        \
    HR GetTypeInfoCount(unsigned *n) override { if (n) *n = 0; return S_OK_; }                               \
    HR GetTypeInfo(unsigned, uint32_t, void **) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }                              \
    HR GetIDsOfNames(const void *, void *, unsigned, uint32_t, long *) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }       \
    HR Invoke(long, const void *, uint32_t, uint16_t, void *, void *, void *, unsigned *) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }

struct Control final : IMediaControl_ {
    Graph *g;
    FORWARD_UNKNOWN DISPATCH_STUBS
    HR Run() override;
    HR Pause() override;
    HR Stop() override;
    HR GetState(long ms, long *state) override;
    HR RenderFile(void *) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR AddSourceFilter(void *, void **) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR get_FilterCollection(void **) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR get_RegFilterCollection(void **) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR StopWhenReady() override { return Stop(); }
};
struct Event final : IMediaEvent_ {
    Graph *g;
    FORWARD_UNKNOWN DISPATCH_STUBS
    HR GetEventHandle(uintptr_t *h) override;
    HR GetEvent(long *code, intptr_t *p1, intptr_t *p2, long ms) override;
    HR WaitForCompletion(long ms, long *code) override;
    HR CancelDefaultHandling(long) override { return S_OK_; }
    HR RestoreDefaultHandling(long) override { return S_OK_; }
    HR FreeEventParams(long, intptr_t, intptr_t) override { return S_OK_; }
};
struct Position final : IMediaPosition_ {
    Graph *g;
    FORWARD_UNKNOWN DISPATCH_STUBS
    HR get_Duration(REFTIME *t) override;
    HR put_CurrentPosition(REFTIME t) override;
    HR get_CurrentPosition(REFTIME *t) override;
    HR get_StopTime(REFTIME *t) override { return get_Duration(t); }
    HR put_StopTime(REFTIME) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR get_PrerollTime(REFTIME *t) override { if (!t) return E_POINTER_; *t = 0; return S_OK_; }
    HR put_PrerollTime(REFTIME) override { return S_OK_; }
    HR put_Rate(double r) override { return r == 1.0 ? S_OK_ : (FNVNX_UNIMPL(), E_NOTIMPL_); }
    HR get_Rate(double *r) override { if (!r) return E_POINTER_; *r = 1.0; return S_OK_; }
    HR CanSeekForward(long *c) override { if (!c) return E_POINTER_; *c = -1; return S_OK_; }
    HR CanSeekBackward(long *c) override { if (!c) return E_POINTER_; *c = -1; return S_OK_; }
};
struct Audio final : IBasicAudio_ {
    Graph *g;
    FORWARD_UNKNOWN DISPATCH_STUBS
    HR put_Volume(long v) override;
    HR get_Volume(long *v) override;
    HR put_Balance(long b) override;
    HR get_Balance(long *b) override;
};

struct Graph final : IGraphBuilder_ {
    int refs = 1;
    Control control;
    Event event;
    Position position;
    Audio audio;
    Unk *filters[8] = {};
    os_lock_t lock;
    /* the track */
    uint8_t *file = nullptr;
    size_t file_size = 0;
    mp3dec_ex_t dec;
    bool have_dec = false;
    uint32_t rate = 0, channels = 0;
    uint64_t total_frames = 0;
    /* playback */
    mix_voice *voice = nullptr;
    int16_t *ring = nullptr;
    uint64_t decoded = 0, start_frame = 0;   /* frames written to the ring / frame the ring started at */
    uint64_t played = 0;                       /* frames past start_frame the voice has played */
    uint32_t last_cursor = 0;
    int state = State_Stopped;
    bool eos = false, complete_posted = false;
    long volume = 0, balance = 0;
    HANDLE evt = nullptr;
    long pending = 0;                          /* EC_COMPLETE events waiting */
    os_thread *pump = nullptr;
    volatile int quit = 0;
    mp3d_sample_t pcm[4096 * MINIMP3_MAX_SAMPLES_PER_FRAME / 1152];   /* decode scratch, 4096 frames x 2 channels */

    Graph()
    {
        control.g = event.g = position.g = audio.g = this;
        memset(&dec, 0, sizeof dec);
        evt = CreateEventA(nullptr, TRUE, FALSE, nullptr);
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
    HR AddFilter(Unk *f, const uint16_t *) override
    {
        if (!f) return E_POINTER_;
        for (Unk *&s : filters)
            if (!s) {
                s = f;
                f->AddRef();
                return S_OK_;
            }
        return E_OUTOFMEMORY_;
    }
    HR RemoveFilter(Unk *f) override
    {
        for (Unk *&s : filters)
            if (s == f) {
                s->Release();
                s = nullptr;
                return S_OK_;
            }
        return E_FAIL_;
    }
    HR EnumFilters(void **) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR FindFilterByName(const uint16_t *, void **) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR ConnectDirect(Unk *, Unk *, const void *) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR Reconnect(Unk *) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR Disconnect(Unk *) override { return S_OK_; }
    HR SetDefaultSyncSource() override { return S_OK_; }
    HR Connect(Unk *, Unk *) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR Render(Unk *pin) override;
    HR RenderFile(const uint16_t *, const uint16_t *) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR AddSourceFilter(const uint16_t *, const uint16_t *, void **) override { return FNVNX_UNIMPL(), E_NOTIMPL_; }
    HR SetLogFile(uintptr_t) override { return S_OK_; }
    HR Abort() override { return S_OK_; }
    HR ShouldOperationContinue() override { return S_OK_; }

    void apply_gain();
    void fill_locked();   /* top the ring up to AHEAD_FRAMES past the play position */
    void track_cursor_locked();
    void seek_locked(uint64_t frame);
    void post_complete_locked();
};

/* the subobjects share the graph's identity and count */
#define FORWARD_IMPL(T)                                                                                       \
    HR T::QueryInterface(const void *iid, void **out) { return g->QueryInterface(iid, out); }                \
    unsigned long T::AddRef() { return g->AddRef(); }                                                         \
    unsigned long T::Release() { return g->Release(); }
FORWARD_IMPL(Control)
FORWARD_IMPL(Event)
FORWARD_IMPL(Position)
FORWARD_IMPL(Audio)

HR Graph::QueryInterface(const void *iid, void **out)
{
    if (!out) return E_POINTER_;
    *out = nullptr;
    if (eq(iid, IID_IUnknown_) || eq(iid, IID_IFilterGraph_) || eq(iid, IID_IGraphBuilder_))
        *out = static_cast<IGraphBuilder_ *>(this);
    else if (eq(iid, IID_IMediaControl_))
        *out = static_cast<IMediaControl_ *>(&control);
    else if (eq(iid, IID_IMediaEvent_))
        *out = static_cast<IMediaEvent_ *>(&event);
    else if (eq(iid, IID_IMediaPosition_))
        *out = static_cast<IMediaPosition_ *>(&position);
    else if (eq(iid, IID_IBasicAudio_))
        *out = static_cast<IBasicAudio_ *>(&audio);
    else
        return E_NOINTERFACE_;
    AddRef();
    return S_OK_;
}

void pump_main(void *p);

HR Graph::Render(Unk *pin)
{
    if (!pin) return E_POINTER_;
    AsyncReader *rd = nullptr;
    if (pin->QueryInterface(&IID_IAsyncReader_, (void **)&rd) < 0 || !rd) return VFW_E_NOT_CONNECTED_;
    long long total = 0, avail = 0;
    HR hr = rd->Length(&total, &avail);
    uint8_t *buf = hr >= 0 && total > 0 && total < (256ll << 20) ? (uint8_t *)malloc((size_t)total) : nullptr;
    for (long long at = 0; buf && at < total;) {   /* the whole file, in pieces the reader can serve */
        long n = total - at < (1 << 16) ? (long)(total - at) : (1 << 16);
        if (rd->SyncRead(at, n, buf + at) < 0) {
            free(buf);
            buf = nullptr;
            break;
        }
        at += n;
    }
    rd->Release();
    if (!buf) return VFW_E_CANNOT_RENDER_;
    os_lock(&lock);
    if (have_dec) mp3dec_ex_close(&dec);
    free(file);
    file = buf;
    file_size = (size_t)total;
    have_dec = mp3dec_ex_open_buf(&dec, file, file_size, MP3D_SEEK_TO_SAMPLE) == 0 && dec.info.hz && dec.info.channels;
    if (!have_dec) {
        if (fnvnx_plat_on)   /* the platform lane: an MP3 that minimp3 cannot open */
            fnvnx_plat_note(FNVNX_PLAT_DECODE, "dshow::mp3_open", FNVNX_PLAT_RA, "minimp3");
        os_unlock(&lock);
        return VFW_E_CANNOT_RENDER_;
    }
    rate = (uint32_t)dec.info.hz;
    channels = dec.info.channels > 2 ? 2 : (uint32_t)dec.info.channels;
    total_frames = dec.samples / (uint64_t)dec.info.channels;
    if (!ring) ring = (int16_t *)calloc(RING_FRAMES, 2 * sizeof(int16_t));
    mix_format f = {rate, 2, 16, 4, 0};
    if (voice) mixer_voice_destroy(voice);
    voice = ring ? mixer_voice_create(&f, (uint8_t *)ring, RING_FRAMES * 4) : nullptr;
    if (voice) mixer_voice_set_freq(voice, rate);
    seek_locked(0);
    apply_gain();
    bool ok = voice != nullptr;
    if (ok && !pump) {   /* one pump per graph, started once */
        pump = os_thread_create(pump_main, this, 128 * 1024, 1, -1);
        if (pump && os_thread_start(pump)) {
            os_thread_free(pump);
            pump = nullptr;
        }
    }
    os_unlock(&lock);
    return ok ? S_OK_ : E_OUTOFMEMORY_;
}

void Graph::apply_gain()
{
    if (!voice) return;
    float g = mixer_mb_to_gain(volume), l, r;
    mixer_pan_to_gains(balance, &l, &r);
    mixer_voice_set_gain(voice, g * l, g * r);
}

void Graph::seek_locked(uint64_t frame)
{
    if (frame > total_frames) frame = total_frames;
    if (have_dec) mp3dec_ex_seek(&dec, frame * dec.info.channels);
    start_frame = frame;
    decoded = 0;
    played = 0;
    last_cursor = 0;
    eos = false;
    complete_posted = false;
    if (ring) memset(ring, 0, RING_FRAMES * 4);
    if (voice) mixer_voice_set_cursor(voice, 0);
    fill_locked();
}

void Graph::track_cursor_locked()
{
    if (!voice) return;
    uint32_t c = mixer_voice_play_cursor(voice) / 4;
    uint32_t d = c >= last_cursor ? c - last_cursor : c + RING_FRAMES - last_cursor;
    played += d;
    last_cursor = c;
}

void Graph::fill_locked()
{
    if (!have_dec || !ring) return;
    mp3d_sample_t *tmp = pcm;
    while (!eos && decoded < played + AHEAD_FRAMES) {
        uint64_t want = played + AHEAD_FRAMES - decoded;
        if (want > 4096) want = 4096;
        size_t got = mp3dec_ex_read(&dec, tmp, (size_t)want * dec.info.channels) / (size_t)dec.info.channels;
        if (!got) {
            eos = true;
            break;
        }
        for (size_t i = 0; i < got; i++) {
            uint32_t at = (uint32_t)((decoded + i) % RING_FRAMES);
            int16_t l = tmp[i * dec.info.channels], r = channels > 1 ? tmp[i * dec.info.channels + 1] : l;
            ring[2 * at] = l;
            ring[2 * at + 1] = r;
        }
        decoded += got;
    }
    if (eos)   /* silence after the end, so the voice plays nothing old while it runs out */
        for (uint64_t f = decoded; f < played + RING_FRAMES && f < decoded + RING_FRAMES; f++) {
            uint32_t at = (uint32_t)(f % RING_FRAMES);
            ring[2 * at] = ring[2 * at + 1] = 0;
        }
}

void Graph::post_complete_locked()
{
    if (complete_posted) return;
    complete_posted = true;
    pending++;
    if (evt) SetEvent(evt);
}

void pump_main(void *p)
{
    Graph *g = (Graph *)p;
    while (!g->quit) {
        os_lock(&g->lock);
        if (g->state == State_Running) {
            g->track_cursor_locked();
            g->fill_locked();
            if (g->eos && g->played >= g->decoded) {   /* the end of the track was heard */
                mixer_voice_stop(g->voice);
                g->state = State_Stopped;
                g->post_complete_locked();
            }
        }
        os_unlock(&g->lock);
        os_sleep_ns(10 * 1000000ull);
    }
}

Graph::~Graph()
{
    quit = 1;
    if (pump) {
        os_thread_join(pump);
        os_thread_free(pump);
    }
    if (voice) mixer_voice_destroy(voice);
    if (have_dec) mp3dec_ex_close(&dec);
    free(file);
    free(ring);
    for (Unk *f : filters)
        if (f) f->Release();
    if (evt) CloseHandle(evt);
}

HR Control::Run()
{
    os_lock(&g->lock);
    HR r = g->voice ? S_OK_ : VFW_E_NOT_CONNECTED_;
    if (g->voice && g->state != State_Running) {
        if (g->eos && g->played >= g->decoded) g->seek_locked(g->start_frame + g->played);   /* at the end: stays */
        g->track_cursor_locked();
        mixer_voice_play(g->voice, 1);
        g->state = State_Running;
    }
    os_unlock(&g->lock);
    return r;
}
HR Control::Pause()
{
    os_lock(&g->lock);
    if (g->voice && g->state == State_Running) {
        g->track_cursor_locked();
        mixer_voice_stop(g->voice);
    }
    g->state = State_Paused;
    os_unlock(&g->lock);
    return S_OK_;
}
HR Control::Stop()
{
    os_lock(&g->lock);
    if (g->voice && g->state == State_Running) {
        g->track_cursor_locked();
        mixer_voice_stop(g->voice);
    }
    g->state = State_Stopped;   /* DirectShow keeps the position on Stop; the game seeks itself */
    os_unlock(&g->lock);
    return S_OK_;
}
HR Control::GetState(long, long *s)
{
    if (!s) return E_POINTER_;
    os_lock(&g->lock);
    *s = g->state;
    os_unlock(&g->lock);
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
    uint64_t until = os_monotonic_ns() + (ms < 0 ? 0 : (uint64_t)ms * 1000000ull);
    for (;;) {
        os_lock(&g->lock);
        if (g->pending > 0) {
            g->pending--;
            if (!g->pending && g->evt) ResetEvent(g->evt);
            os_unlock(&g->lock);
            *code = EC_COMPLETE_;
            *p1 = S_OK_;
            *p2 = 0;
            return S_OK_;
        }
        os_unlock(&g->lock);
        if (ms >= 0 && os_monotonic_ns() >= until) return (HR)0x80004004L;   /* E_ABORT: no event */
        os_sleep_ns(2 * 1000000ull);
    }
}
HR Event::WaitForCompletion(long ms, long *code)
{
    if (!code) return E_POINTER_;
    uint64_t until = os_monotonic_ns() + (ms < 0 ? 0 : (uint64_t)ms * 1000000ull);
    for (;;) {
        os_lock(&g->lock);
        bool done = g->complete_posted;
        os_unlock(&g->lock);
        if (done) {
            *code = EC_COMPLETE_;
            return S_OK_;
        }
        if (ms >= 0 && os_monotonic_ns() >= until) {
            *code = 0;
            return (HR)0x80004004L;
        }
        os_sleep_ns(2 * 1000000ull);
    }
}

HR Position::get_Duration(REFTIME *t)
{
    if (!t) return E_POINTER_;
    os_lock(&g->lock);
    *t = g->rate ? (double)g->total_frames / g->rate : 0.0;
    os_unlock(&g->lock);
    return S_OK_;
}
HR Position::get_CurrentPosition(REFTIME *t)
{
    if (!t) return E_POINTER_;
    os_lock(&g->lock);
    if (g->state == State_Running) g->track_cursor_locked();
    uint64_t f = g->start_frame + (g->played < g->decoded ? g->played : g->decoded);
    *t = g->rate ? (double)f / g->rate : 0.0;
    os_unlock(&g->lock);
    return S_OK_;
}
HR Position::put_CurrentPosition(REFTIME t)
{
    os_lock(&g->lock);
    bool run = g->state == State_Running;
    if (run) mixer_voice_stop(g->voice);
    g->seek_locked(g->rate && t > 0 ? (uint64_t)(t * g->rate) : 0);
    if (run) mixer_voice_play(g->voice, 1);
    os_unlock(&g->lock);
    return S_OK_;
}

HR Audio::put_Volume(long v)
{
    if (v > 0 || v < -10000) return (HR)0x80070057L;   /* E_INVALIDARG */
    os_lock(&g->lock);
    g->volume = v;
    g->apply_gain();
    os_unlock(&g->lock);
    return S_OK_;
}
HR Audio::get_Volume(long *v)
{
    if (!v) return E_POINTER_;
    *v = g->volume;
    return S_OK_;
}
HR Audio::put_Balance(long b)
{
    if (b < -10000 || b > 10000) return (HR)0x80070057L;
    os_lock(&g->lock);
    g->balance = b;
    g->apply_gain();
    os_unlock(&g->lock);
    return S_OK_;
}
HR Audio::get_Balance(long *b)
{
    if (!b) return E_POINTER_;
    *b = g->balance;
    return S_OK_;
}

} // namespace

/* CoCreateInstance(CLSID_FilterGraph) (platform/input/register.cpp registers it) */
extern "C" HRESULT fnvnx_dshow_com_factory(const GUID *iid, void **out)
{
    if (!out) return E_POINTER_;
    *out = nullptr;
    Graph *g = new (std::nothrow) Graph();
    if (!g) return E_OUTOFMEMORY_;
    HR r = g->QueryInterface(iid, out);
    g->Release();
    return r;
}
