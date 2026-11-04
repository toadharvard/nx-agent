/* test_dshow.cpp - DirectShow FilterGraph for the game's music (platform/audio/dshow.cpp), driven as the exe does it
 * (0x00830fd5..): CoCreateInstance(CLSID_FilterGraph, IID_IFilterGraph), AddFilter(its source), QI IGraphBuilder,
 * Render(source pin), then IBasicAudio, IMediaControl, IMediaPosition, IMediaEvent. The source is a fake async
 * reader serving tests/s7/data/tone.mp3 (our own: 1.5 s of 440 Hz left / 660 Hz right, lame -b 96). */
#include "s7_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include <vector>

#include "fnvnx_win32_ext.h"
#include "../../platform/audio/mixer.h"

namespace {
typedef int32_t HR;   /* HRESULT: 32 bits (a long on the game's ILP32, not on an LP64 host) */
struct G { uint32_t a; uint16_t b, c; uint8_t d[8]; };
/* CoCreateInstance compares the shim's GUID (24 bytes on an LP64 host, 16 on the game's ILP32) */
const GUID CLSID_FilterGraph_ = {0xe436ebb3, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
#define DSG(n, a) const G n = {a, 0x0ad4, 0x11ce, {0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}}
DSG(IID_IFilterGraph_, 0x56a8689f);
DSG(IID_IGraphBuilder_, 0x56a868a9);
DSG(IID_IMediaControl_, 0x56a868b1);
DSG(IID_IMediaPosition_, 0x56a868b2);
DSG(IID_IBasicAudio_, 0x56a868b3);
DSG(IID_IMediaEvent_, 0x56a868b6);
DSG(IID_IAsyncReader_, 0x56a868aa);

struct Unk {
    virtual HR QueryInterface(const void *iid, void **out) = 0;
    virtual unsigned long AddRef() = 0;
    virtual unsigned long Release() = 0;
};
struct FG : Unk {
    virtual HR AddFilter(Unk *, const uint16_t *) = 0;
    virtual HR RemoveFilter(Unk *) = 0;
    virtual HR EnumFilters(void **) = 0;
    virtual HR FindFilterByName(const uint16_t *, void **) = 0;
    virtual HR ConnectDirect(Unk *, Unk *, const void *) = 0;
    virtual HR Reconnect(Unk *) = 0;
    virtual HR Disconnect(Unk *) = 0;
    virtual HR SetDefaultSyncSource() = 0;
};
struct GB : FG {
    virtual HR Connect(Unk *, Unk *) = 0;
    virtual HR Render(Unk *) = 0;
};
struct Disp : Unk {
    virtual HR GetTypeInfoCount(unsigned *) = 0;
    virtual HR GetTypeInfo(unsigned, uint32_t, void **) = 0;
    virtual HR GetIDsOfNames(const void *, void *, unsigned, uint32_t, long *) = 0;
    virtual HR Invoke(long, const void *, uint32_t, uint16_t, void *, void *, void *, unsigned *) = 0;
};
struct MC : Disp {
    virtual HR Run() = 0;
    virtual HR Pause() = 0;
    virtual HR Stop() = 0;
    virtual HR GetState(long, long *) = 0;
};
struct ME : Disp {
    virtual HR GetEventHandle(uintptr_t *) = 0;
    virtual HR GetEvent(long *, intptr_t *, intptr_t *, long) = 0;
};
struct MP : Disp {
    virtual HR get_Duration(double *) = 0;
    virtual HR put_CurrentPosition(double) = 0;
    virtual HR get_CurrentPosition(double *) = 0;
};
struct BA : Disp {
    virtual HR put_Volume(long) = 0;
    virtual HR get_Volume(long *) = 0;
    virtual HR put_Balance(long) = 0;
    virtual HR get_Balance(long *) = 0;
};

/* the game's side: a filter object and an output pin whose IAsyncReader serves a file */
std::vector<uint8_t> g_file;
int g_reads, g_refs_filter = 1;
struct Reader : Unk {
    int refs = 1;
    HR QueryInterface(const void *iid, void **out) override
    {
        if (!memcmp(iid, &IID_IAsyncReader_, 16)) { *out = this; refs++; return 0; }
        *out = nullptr;
        return (HR)0x80004002L;
    }
    unsigned long AddRef() override { return ++refs; }
    unsigned long Release() override { return --refs; }
    virtual HR RequestAllocator(void *, void *, void **) { return (HR)0x80004001L; }
    virtual HR Request(void *, uintptr_t) { return (HR)0x80004001L; }
    virtual HR WaitForNext(uint32_t, void **, uintptr_t *) { return (HR)0x80004001L; }
    virtual HR SyncReadAligned(void *) { return (HR)0x80004001L; }
    virtual HR SyncRead(long long pos, long len, uint8_t *buf)
    {
        g_reads++;
        if (pos < 0 || pos + len > (long long)g_file.size()) return (HR)0x80004005L;
        memcpy(buf, g_file.data() + pos, (size_t)len);
        return 0;
    }
    virtual HR Length(long long *t, long long *a) { *t = *a = (long long)g_file.size(); return 0; }
};
struct Filter : Unk {
    HR QueryInterface(const void *, void **out) override { *out = nullptr; return (HR)0x80004002L; }
    unsigned long AddRef() override { return ++g_refs_filter; }
    unsigned long Release() override { return --g_refs_filter; }
};

bool load(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    uint8_t b[4096];
    size_t n;
    while ((n = fread(b, 1, sizeof b, f)) > 0) g_file.insert(g_file.end(), b, b + n);
    fclose(f);
    return !g_file.empty();
}

/* render `ms` of output in 20 ms steps at ~4x real time (the pump refills every 10 ms) */
std::vector<float> play(unsigned ms)
{
    std::vector<float> all;
    std::vector<float> chunk(960 * 2);
    for (unsigned t = 0; t < ms; t += 20) {
        mixer_render_f32(chunk.data(), 960);
        all.insert(all.end(), chunk.begin(), chunk.end());
        usleep(5000);
    }
    return all;
}

/* zero crossings per second of one channel over [a, b) frames */
double freq(const std::vector<float> &x, int ch, size_t a, size_t b)
{
    int z = 0;
    for (size_t i = a + 1; i < b; i++)
        z += (x[2 * (i - 1) + ch] < 0) != (x[2 * i + ch] < 0);
    return z / 2.0 / ((b - a) / 48000.0);
}
double rms(const std::vector<float> &x, size_t a, size_t b)
{
    double e = 0;
    for (size_t i = 2 * a; i < 2 * b && i < x.size(); i++) e += (double)x[i] * x[i];
    return sqrt(e / (2.0 * (b - a)));
}
} // namespace

TEST(dshow_music_graph_as_the_game_drives_it)
{
    const char *dir = getenv("S7_DATA");
    char path[512];
    snprintf(path, sizeof path, "%s/tone.mp3", dir ? dir : "tests/s7/data");
    CHECK(load(path));
    if (g_file.empty()) return;
    mixer_init(48000, 256);
    FG *fg = nullptr;
    CHECK_EQ(CoCreateInstance(&CLSID_FilterGraph_, nullptr, 1, (const GUID *)&IID_IFilterGraph_,
                              (void **)&fg), 0);
    CHECK(fg != nullptr);
    if (!fg) return;
    Filter src;
    Reader pin;
    CHECK_EQ(fg->AddFilter(&src, nullptr), 0);
    CHECK_EQ(g_refs_filter, 2);   /* the graph holds the game's filter */
    GB *gb = nullptr;
    CHECK_EQ(fg->QueryInterface(&IID_IGraphBuilder_, (void **)&gb), 0);
    CHECK_EQ(gb->Render(&pin), 0);
    CHECK(g_reads > 0 && pin.refs == 1);   /* the file was pulled through SyncRead, the reader released */
    BA *ba = nullptr;
    MC *mc = nullptr;
    MP *mp = nullptr;
    ME *me = nullptr;
    CHECK_EQ(gb->QueryInterface(&IID_IBasicAudio_, (void **)&ba), 0);
    CHECK_EQ(fg->QueryInterface(&IID_IMediaControl_, (void **)&mc), 0);
    CHECK_EQ(fg->QueryInterface(&IID_IMediaPosition_, (void **)&mp), 0);
    CHECK_EQ(fg->QueryInterface(&IID_IMediaEvent_, (void **)&me), 0);
    double dur = 0;
    CHECK_EQ(mp->get_Duration(&dur), 0);
    CHECK_NEAR(dur, 1.5, 0.08);   /* lame pads by a frame or two */
    long st = -1;
    mc->GetState(0, &st);
    CHECK_EQ(st, 0);

    CHECK_EQ(mc->Run(), 0);
    std::vector<float> a = play(600);
    CHECK_NEAR(freq(a, 0, 4800, 24000), 440.0, 15.0);   /* left 440 Hz, right 660 Hz, resampled 44.1 -> 48 kHz */
    CHECK_NEAR(freq(a, 1, 4800, 24000), 660.0, 15.0);
    CHECK(rms(a, 4800, 24000) > 0.2);
    double pos = 0;
    mp->get_CurrentPosition(&pos);
    CHECK(pos > 0.3 && pos < 0.75);

    /* volume: -10000 is silence, back to 0 is full */
    CHECK_EQ(ba->put_Volume(-10000), 0);
    std::vector<float> q = play(100);
    CHECK(rms(q, 0, 4800) < 1e-6);
    CHECK_EQ(ba->put_Volume(0), 0);
    long v = 1;
    ba->get_Volume(&v);
    CHECK_EQ(v, 0);
    CHECK_EQ(ba->put_Volume(1), (HR)0x80070057L);

    /* pause holds the position; seek moves it */
    CHECK_EQ(mc->Pause(), 0);
    double p1 = 0, p2 = 0;
    mp->get_CurrentPosition(&p1);
    play(100);
    mp->get_CurrentPosition(&p2);
    CHECK_NEAR(p1, p2, 1e-9);
    CHECK_EQ(mp->put_CurrentPosition(1.2), 0);
    mp->get_CurrentPosition(&p1);
    CHECK_NEAR(p1, 1.2, 0.01);

    /* the end: EC_COMPLETE, the event handle signalled, the graph stopped */
    uintptr_t h = 0;
    CHECK_EQ(me->GetEventHandle(&h), 0);
    CHECK(h != 0);
    long code = 0;
    intptr_t p1e = 0, p2e = 0;
    CHECK(me->GetEvent(&code, &p1e, &p2e, 0) < 0);   /* nothing yet */
    CHECK_EQ(mc->Run(), 0);
    play(600);   /* 0.3 s were left */
    CHECK_EQ(WaitForSingleObject((HANDLE)h, 2000), WAIT_OBJECT_0);
    CHECK_EQ(me->GetEvent(&code, &p1e, &p2e, 1000), 0);
    CHECK_EQ(code, 1);   /* EC_COMPLETE */
    mc->GetState(0, &st);
    CHECK_EQ(st, 0);
    std::vector<float> after = play(100);
    CHECK(rms(after, 0, 4800) < 1e-6);   /* silent after the end */
    /* replay from the start */
    CHECK_EQ(mp->put_CurrentPosition(0.0), 0);
    CHECK_EQ(mc->Run(), 0);
    std::vector<float> again = play(200);
    CHECK(rms(again, 2400, 9600) > 0.2);
    mc->Stop();

    ba->Release();
    mc->Release();
    mp->Release();
    me->Release();
    gb->Release();
    fg->Release();
    CHECK_EQ(g_refs_filter, 1);   /* released with the graph */

    /* not MP3: Render fails as DirectShow's does */
    g_file.assign(4096, 0x55);
    FG *fg2 = nullptr;
    CoCreateInstance(&CLSID_FilterGraph_, nullptr, 1, (const GUID *)&IID_IGraphBuilder_, (void **)&fg2);
    Reader pin2;
    CHECK_EQ(((GB *)fg2)->Render(&pin2), (HR)0x80040218L);   /* VFW_E_CANNOT_RENDER */
    fg2->Release();
}
