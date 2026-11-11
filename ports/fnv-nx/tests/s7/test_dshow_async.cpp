/* test_dshow_async.cpp - the platform's DirectShow bodies that stood in for missing functions: the file source's
 * overlapped IAsyncReader (RequestAllocator, Request, WaitForNext, SyncReadAligned, BeginFlush, EndFlush), the memory
 * allocator and samples it hands out, the reader pin's ConnectionMediaType, and the three IMediaControl methods that do
 * what Wine 9.0 does. Expected values come from wine-9.0:dlls/quartz/filesource.c (FileAsyncReader_*),
 * dlls/quartz/memallocator.c (BaseMemAllocator_*, StdMediaSample2_*), libs/strmbase/pin.c (pin_ConnectionMediaType) and
 * dlls/quartz/filtergraph.c (MediaControl_*). Everything runs on the real platform files (no double of a platform symbol). */
#include "s7_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <string>
#include <vector>

#include "fnvnx_win32_ext.h"
#include "../../platform/audio/mixer.h"
#include "../../platform/audio/dshow_int.h"

using namespace dsh;

namespace {
const GUID *G_(const Guid &g) { return (const GUID *)&g; }
const GUID CLSID_Graph_ = {0xe436ebb3, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
std::vector<uint16_t> WS(const char *s) { std::vector<uint16_t> v; while (*s) v.push_back((uint8_t)*s++); v.push_back(0); return v; }
template <class T> T *qi(Unk *u, const Guid &g) { T *t = nullptr; return u && u->QueryInterface(&g, (void **)&t) >= 0 ? t : nullptr; }
LL mt(LL bytes) { return bytes * 10000000; }   /* MEDIATIME_FROM_BYTES */

/* RIFF/WAVE, 16-bit stereo 8 kHz, `frames` frames: 44 + 4 * frames bytes */
std::vector<uint8_t> make_wav(uint32_t frames)
{
    std::vector<uint8_t> v = {'R', 'I', 'F', 'F'};
    auto p32 = [&](uint32_t x) { for (int i = 0; i < 4; i++) v.push_back((uint8_t)(x >> (8 * i))); };
    auto p16 = [&](uint32_t x) { v.push_back((uint8_t)x); v.push_back((uint8_t)(x >> 8)); };
    p32(36 + frames * 4);
    for (char c : std::string("WAVEfmt ")) v.push_back((uint8_t)c);
    p32(16); p16(1); p16(2); p32(8000); p32(32000); p16(4); p16(16);
    for (char c : std::string("data")) v.push_back((uint8_t)c);
    p32(frames * 4);
    for (uint32_t i = 0; i < frames * 4; i++) v.push_back((uint8_t)(i * 7 + 3));
    return v;
}

bool put_wav(const char *name, const std::vector<uint8_t> &wav)
{
    char dir[256];
    snprintf(dir, sizeof dir, "%s/dshow_async", getenv("FNVNX_ROOT") ? getenv("FNVNX_ROOT") : "/tmp");
    mkdir(dir, 0755);
    fnvnx_path_mount("C:\\dshowasync\\", dir);
    std::string p = std::string(dir) + "/" + name;
    FILE *f = fopen(p.c_str(), "wb");
    if (!f) return false;
    fwrite(wav.data(), 1, wav.size(), f);
    fclose(f);
    return true;
}
IPin_ *out_pin(IBaseFilter_ *f)
{
    void *e = nullptr;
    if (!f || f->EnumPins(&e) < 0) return nullptr;
    void *x = nullptr;
    uint32_t got = 0;
    IPin_ *p = nullptr;
    if (((IEnum_ *)e)->Next(1, &x, &got) == 0) p = (IPin_ *)x;
    ((Unk *)e)->Release();
    return p;
}
HR hr_win32(uint32_t e) { return (HR)(0x80070000u | e); }
const uint32_t kWait = 50;
} // namespace

TEST(dshow_async_reader)
{
    mixer_init(48000, 256);
    std::vector<uint8_t> wav = make_wav(1000);   /* 4044 bytes */
    CHECK(put_wav("r.wav", wav));

    IFilterGraph2_ *g = nullptr;
    CoCreateInstance(&CLSID_Graph_, nullptr, 1, G_(IID_IFilterGraph2_), (void **)&g);
    CHECK(g != nullptr);
    if (!g) return;
    IBaseFilter_ *src = nullptr;
    auto fn = WS("C:\\dshowasync\\r.wav");
    CHECK_EQ(g->AddSourceFilter(fn.data(), nullptr, &src), 0);
    IPin_ *pin = out_pin(src);
    AsyncReader *rd = qi<AsyncReader>(pin, IID_IAsyncReader_);
    CHECK(rd != nullptr);
    if (!rd) return;
    LL total = 0, avail = 0;
    CHECK_EQ(rd->Length(&total, &avail), 0);
    CHECK_EQ(total, (LL)wav.size());

    /* RequestAllocator: cbAlign 0 becomes 1 (FileAsyncReader_RequestAllocator); the allocator echoes the properties */
    AllocProps props = {4, 1024, 0, 0};
    IMemAllocator_ *al = nullptr;
    CHECK_EQ(rd->RequestAllocator(nullptr, &props, (void **)&al), 0);
    CHECK(al != nullptr);
    CHECK_EQ(props.cbAlign, 1);
    CHECK_EQ(props.cBuffers, 4);
    CHECK_EQ(rd->RequestAllocator(nullptr, nullptr, (void **)&al), E_POINTER_);
    if (!al) return;
    CHECK_EQ(al->Commit(), 0);
    IMediaSample_ *s1 = nullptr, *s2 = nullptr, *s3 = nullptr;
    CHECK_EQ(al->GetBuffer(&s1, nullptr, nullptr, 0), 0);
    CHECK_EQ(al->GetBuffer(&s2, nullptr, nullptr, 0), 0);
    CHECK_EQ(al->GetBuffer(&s3, nullptr, nullptr, 0), 0);
    CHECK(s1 && s2 && s3);
    if (!s1 || !s2 || !s3) return;
    CHECK_EQ(s1->GetSize(), 1024);

    /* Request + WaitForNext: the read happens, the sample gets its length and its time in media units */
    LL a = 0, b = mt(1024);
    CHECK_EQ(s1->SetTime(&a, &b), 0);
    CHECK_EQ(rd->Request(s1, 7), 0);
    a = mt(1024); b = mt(2048);
    s2->SetTime(&a, &b);
    CHECK_EQ(rd->Request(s2, 9), 0);
    CHECK_EQ(rd->Request(nullptr, 1), E_POINTER_);
    void *got = nullptr;
    uintptr_t cookie = 0;
    CHECK_EQ(rd->WaitForNext(kWait, &got, &cookie), 0);
    CHECK(got == (void *)s1);
    CHECK_EQ(cookie, (uintptr_t)7);
    CHECK_EQ(s1->GetActualDataLength(), 1024);
    LL st = -1, en = -1;
    CHECK_EQ(s1->GetTime(&st, &en), 0);
    CHECK_EQ(st, 0);
    CHECK_EQ(en, mt(1024));
    uint8_t *data = nullptr;
    CHECK_EQ(s1->GetPointer(&data), 0);
    CHECK(data && memcmp(data, wav.data(), 1024) == 0);
    CHECK_EQ(rd->WaitForNext(kWait, &got, &cookie), 0);
    CHECK(got == (void *)s2);
    CHECK_EQ(cookie, (uintptr_t)9);
    CHECK(s2->GetPointer(&data) == 0 && memcmp(data, wav.data() + 1024, 1024) == 0);
    /* nothing outstanding: the wait times out */
    CHECK_EQ(rd->WaitForNext(20, &got, &cookie), VFW_E_TIMEOUT_);
    CHECK(got == nullptr && cookie == 0);

    /* a request at or past the end of the file: ERROR_HANDLE_EOF; a partial read reports what was read */
    a = mt(5000); b = mt(6024);
    s3->SetTime(&a, &b);
    CHECK_EQ(rd->Request(s3, 1), hr_win32(38));
    a = mt(4000); b = mt(5024);
    s3->SetTime(&a, &b);
    CHECK_EQ(rd->Request(s3, 11), 0);
    CHECK_EQ(rd->WaitForNext(kWait, &got, &cookie), 0);
    CHECK(got == (void *)s3);
    CHECK_EQ(s3->GetActualDataLength(), 44);
    CHECK_EQ(s3->GetTime(&st, &en), 0);
    CHECK_EQ(st, mt(4000));
    CHECK_EQ(en, mt(4044));
    CHECK(s3->GetPointer(&data) == 0 && memcmp(data, wav.data() + 4000, 44) == 0);
    /* a sample whose time was never set: GetTime's error comes back from Request */
    s3->SetTime(nullptr, nullptr);
    CHECK_EQ(rd->Request(s3, 1), VFW_E_SAMPLE_TIME_NOT_SET_);

    /* SyncReadAligned: S_OK for a full read, S_FALSE for a partial one, S_OK with length 0 past the end */
    a = mt(100); b = mt(1124);
    s1->SetTime(&a, &b);
    CHECK_EQ(rd->SyncReadAligned(s1), 0);
    CHECK_EQ(s1->GetActualDataLength(), 1024);
    s1->GetPointer(&data);
    CHECK(memcmp(data, wav.data() + 100, 1024) == 0);
    a = mt(4000); b = mt(5024);
    s1->SetTime(&a, &b);
    CHECK_EQ(rd->SyncReadAligned(s1), 1);
    CHECK_EQ(s1->GetActualDataLength(), 44);
    a = mt(4044); b = mt(5068);
    s1->SetTime(&a, &b);
    CHECK_EQ(rd->SyncReadAligned(s1), 0);
    CHECK_EQ(s1->GetActualDataLength(), 0);
    s1->SetTime(nullptr, nullptr);
    CHECK_EQ(rd->SyncReadAligned(s1), VFW_E_SAMPLE_TIME_NOT_SET_);

    /* BeginFlush drops the requests and refuses new ones; EndFlush lets the reader work again with nothing pending */
    a = 0; b = mt(1024);
    s1->SetTime(&a, &b);
    CHECK_EQ(rd->Request(s1, 3), 0);
    CHECK_EQ(rd->BeginFlush(), 0);
    CHECK_EQ(rd->WaitForNext(kWait, &got, &cookie), VFW_E_WRONG_STATE_);
    CHECK_EQ(rd->Request(s1, 4), VFW_E_WRONG_STATE_);
    CHECK_EQ(rd->EndFlush(), 0);
    CHECK_EQ(rd->WaitForNext(20, &got, &cookie), VFW_E_TIMEOUT_);
    CHECK_EQ(rd->Request(s1, 5), 0);
    CHECK_EQ(rd->WaitForNext(kWait, &got, &cookie), 0);
    CHECK_EQ(cookie, (uintptr_t)5);

    /* a preferred allocator is used as given */
    IMemAllocator_ *al2 = nullptr, *mine = make_mem_allocator();
    AllocProps pr2 = {2, 256, 8, 0};
    CHECK(mine != nullptr);
    CHECK_EQ(rd->RequestAllocator(mine, &pr2, (void **)&al2), 0);
    CHECK(al2 == mine);
    AllocProps now = {};
    CHECK_EQ(mine->GetProperties(&now), 0);
    CHECK(now.cBuffers == 2 && now.cbBuffer == 256 && now.cbAlign == 8);
    if (al2) al2->Release();
    mine->Release();

    s1->Release(); s2->Release(); s3->Release();
    al->Decommit();
    al->Release();
    rd->Release();
    pin->Release();
    src->Release();
    g->Release();
}

TEST(dshow_memory_allocator)
{
    IMemAllocator_ *al = make_mem_allocator();
    CHECK(al != nullptr);
    if (!al) return;
    /* a fresh allocator: nothing set, nothing committed (BaseMemAllocator_Commit / _GetBuffer) */
    AllocProps pr = {};
    CHECK_EQ(al->GetProperties(&pr), 0);
    CHECK(pr.cBuffers == 0 && pr.cbBuffer == 0 && pr.cbAlign == 0 && pr.cbPrefix == 0);
    CHECK_EQ(al->Commit(), VFW_E_BADALIGN_);
    AllocProps rq = {2, 100, 0, 0}, act = {};
    CHECK_EQ(al->SetProperties(&rq, &act), VFW_E_BADALIGN_);
    rq = {2, 0, 1, 0};
    CHECK_EQ(al->SetProperties(&rq, &act), 0);
    CHECK_EQ(al->Commit(), VFW_E_SIZENOTSET_);
    rq = {0, 100, 1, 0};
    CHECK_EQ(al->SetProperties(&rq, &act), 0);
    CHECK_EQ(al->Commit(), VFW_E_BUFFER_NOTSET_);
    IMediaSample_ *s = nullptr;
    CHECK_EQ(al->GetBuffer(&s, nullptr, nullptr, 0), VFW_E_NOT_COMMITTED_);
    CHECK(s == nullptr);
    CHECK_EQ(al->Decommit(), 0);   /* not committed: S_OK */
    /* an alignment the page size is not a multiple of is refused at Commit */
    rq = {2, 100, 3, 0};
    CHECK_EQ(al->SetProperties(&rq, &act), 0);
    CHECK_EQ(al->Commit(), VFW_E_BADALIGN_);

    /* aligned buffers with a prefix: each sample takes cbBuffer + cbPrefix rounded up to cbAlign, the buffer follows the prefix */
    rq = {2, 100, 64, 8};
    CHECK_EQ(al->SetProperties(&rq, &act), 0);
    CHECK(act.cBuffers == 2 && act.cbBuffer == 100 && act.cbAlign == 64 && act.cbPrefix == 8);
    CHECK_EQ(al->Commit(), 0);
    CHECK_EQ(al->Commit(), 0);   /* committed again: S_OK */
    CHECK_EQ(al->SetProperties(&rq, &act), VFW_E_ALREADY_COMMITTED_);
    IMediaSample_ *a = nullptr, *b = nullptr, *c = nullptr;
    CHECK_EQ(al->GetBuffer(&a, nullptr, nullptr, 0), 0);
    CHECK_EQ(al->GetBuffer(&b, nullptr, nullptr, 0), 0);
    CHECK_EQ(al->GetBuffer(&c, nullptr, nullptr, 4 /* AM_GBF_NOWAIT */), VFW_E_TIMEOUT_);   /* both buffers are out */
    CHECK(c == nullptr);
    if (!a || !b) return;
    uint8_t *pa = nullptr, *pb = nullptr;
    a->GetPointer(&pa);
    b->GetPointer(&pb);
    CHECK(pa && pb);
    CHECK_EQ((uintptr_t)(pa > pb ? pa - pb : pb - pa), (uintptr_t)128);   /* stride: 108 rounded up to 64 */
    CHECK_EQ((uintptr_t)(pa - 8) % 64, (uintptr_t)0);
    CHECK_EQ((uintptr_t)(pb - 8) % 64, (uintptr_t)0);
    CHECK_EQ(al->SetProperties(&rq, &act), VFW_E_ALREADY_COMMITTED_);
    /* Decommit with buffers out is queued: no new buffer, then the last release frees the buffers */
    CHECK_EQ(al->Decommit(), 0);
    CHECK_EQ(al->GetBuffer(&c, nullptr, nullptr, 4), VFW_E_NOT_COMMITTED_);
    CHECK_EQ(a->Release(), 0u);
    CHECK_EQ(b->Release(), 0u);
    CHECK_EQ(al->GetBuffer(&c, nullptr, nullptr, 4), VFW_E_NOT_COMMITTED_);
    CHECK_EQ(al->SetProperties(&rq, &act), 0);   /* nothing outstanding, not committed */
    CHECK_EQ(al->Release(), 0u);
}

TEST(dshow_media_sample)
{
    IMemAllocator_ *al = make_mem_allocator();
    AllocProps rq = {1, 64, 1, 0}, act = {};
    CHECK(al && al->SetProperties(&rq, &act) == 0 && al->Commit() == 0);
    if (!al) return;
    IMediaSample_ *s = nullptr;
    CHECK_EQ(al->GetBuffer(&s, nullptr, nullptr, 0), 0);
    if (!s) return;
    IMediaSample_ *q = nullptr;
    CHECK_EQ(s->QueryInterface(&IID_IMediaSample2_, (void **)&q), 0);
    CHECK(q == s);
    if (q) q->Release();
    CHECK_EQ(s->QueryInterface(&IID_IPin_, (void **)&q), E_NOINTERFACE_);
    CHECK_EQ(s->GetSize(), 64);
    CHECK_EQ(s->GetActualDataLength(), 64);
    CHECK_EQ(s->SetActualDataLength(65), VFW_E_BUFFER_OVERFLOW_);
    CHECK_EQ(s->SetActualDataLength(-1), VFW_E_BUFFER_OVERFLOW_);
    CHECK_EQ(s->SetActualDataLength(10), 0);
    CHECK_EQ(s->GetActualDataLength(), 10);
    /* time: unset, start only (VFW_S_NO_STOP_TIME, stop = start + 1), start and stop */
    LL st = 0, en = 0;
    CHECK_EQ(s->GetTime(&st, &en), VFW_E_SAMPLE_TIME_NOT_SET_);
    LL a = 100, b = 200;
    CHECK_EQ(s->SetTime(&a, nullptr), 0);
    CHECK_EQ(s->GetTime(&st, &en), VFW_S_NO_STOP_TIME_);
    CHECK(st == 100 && en == 101);
    CHECK_EQ(s->SetTime(&a, &b), 0);
    CHECK_EQ(s->GetTime(&st, &en), 0);
    CHECK(st == 100 && en == 200);
    CHECK_EQ(s->SetTime(nullptr, &b), 0);
    CHECK_EQ(s->GetTime(&st, &en), VFW_E_SAMPLE_TIME_NOT_SET_);
    /* media time: unset, a start without a stop is E_POINTER, set */
    CHECK_EQ(s->GetMediaTime(&st, &en), VFW_E_MEDIA_TIME_NOT_SET_);
    CHECK_EQ(s->SetMediaTime(&a, nullptr), E_POINTER_);
    CHECK_EQ(s->SetMediaTime(&a, &b), 0);
    CHECK_EQ(s->GetMediaTime(&st, &en), 0);
    CHECK(st == 100 && en == 200);
    CHECK_EQ(s->SetMediaTime(nullptr, nullptr), 0);
    CHECK_EQ(s->GetMediaTime(&st, &en), VFW_E_MEDIA_TIME_NOT_SET_);
    /* flags: S_FALSE until set */
    CHECK_EQ(s->IsSyncPoint(), 1);
    CHECK_EQ(s->SetSyncPoint(1), 0);
    CHECK_EQ(s->IsSyncPoint(), 0);
    CHECK_EQ(s->SetSyncPoint(0), 0);
    CHECK_EQ(s->IsSyncPoint(), 1);
    CHECK_EQ(s->IsPreroll(), 1);
    s->SetPreroll(1);
    CHECK_EQ(s->IsPreroll(), 0);
    CHECK_EQ(s->IsDiscontinuity(), 1);
    s->SetDiscontinuity(1);
    CHECK_EQ(s->IsDiscontinuity(), 0);
    /* media type: none gives S_FALSE and a null pointer; a copy comes back as a CoTaskMem block */
    MT *m = (MT *)1;
    CHECK_EQ(s->GetMediaType(&m), 1);
    CHECK(m == nullptr);
    MT t = {};
    t.major = MEDIATYPE_Audio_;
    t.sub = MEDIASUBTYPE_PCM_;
    CHECK_EQ(s->SetMediaType(&t), 0);
    CHECK_EQ(s->GetMediaType(&m), 0);
    CHECK(m && geq(m->major, MEDIATYPE_Audio_) && geq(m->sub, MEDIASUBTYPE_PCM_));
    if (m) { mt_free(m); CoTaskMemFree(m); }
    CHECK_EQ(s->SetMediaType(nullptr), 0);
    CHECK_EQ(s->GetMediaType(&m), 1);
    /* GetProperties copies min(cb, sizeof props); SetProperties writes everything before pbBuffer */
    uint8_t buf[64];
    memset(buf, 0xAA, sizeof buf);
    CHECK_EQ(s->GetProperties(8, buf), 0);
    CHECK(*(uint32_t *)buf != 0xAAAAAAAAu);   /* cbData: the first 8 bytes were written */
    CHECK_EQ(buf[8], 0xAA);
    CHECK_EQ(s->Release(), 0u);
    al->Decommit();
    CHECK_EQ(al->Release(), 0u);
}

TEST(dshow_reader_pin_connection_type)
{
    mixer_init(48000, 256);
    CHECK(put_wav("p.wav", make_wav(1000)));
    BFilter *srcf = nullptr;
    auto fn = WS("C:\\dshowasync\\p.wav");
    CHECK_EQ(make_file_source(fn.data(), &srcf), 0);
    BFilter *parser = make_filter(CLSID_WAVEParser_);
    CHECK(srcf != nullptr && parser != nullptr);
    if (!srcf || !parser) return;
    IPin_ *sp = out_pin(srcf);
    AsyncReader *rd = qi<AsyncReader>(sp, IID_IAsyncReader_);
    IPin_ *in = nullptr;
    void *e = nullptr;
    CHECK_EQ(parser->EnumPins(&e), 0);
    void *x = nullptr;
    uint32_t got = 0;
    if (((IEnum_ *)e)->Next(1, &x, &got) == 0) in = (IPin_ *)x;
    ((Unk *)e)->Release();
    CHECK(in != nullptr && rd != nullptr);
    if (!in || !rd) return;
    /* pin_ConnectionMediaType: not connected = VFW_E_NOT_CONNECTED and a zeroed type; connected = a copy of the
     * connection's type. The pin is the borrowed-reader pin the graph makes for a source that has only IAsyncReader. */
    IPin_ *rp = make_reader_pin(rd);
    CHECK(rp != nullptr);
    if (!rp) return;
    MT m;
    memset(&m, 0x5A, sizeof m);
    CHECK_EQ(rp->ConnectionMediaType(&m), VFW_E_NOT_CONNECTED_);
    CHECK(m.cb == 0 && m.pb == nullptr && m.punk == nullptr);
    CHECK_EQ(rp->ConnectionMediaType(nullptr), E_POINTER_);
    MT want = {};
    want.major = MEDIATYPE_Stream_;
    want.sub = MEDIASUBTYPE_WAVE_;
    want.format = FORMAT_None_;
    CHECK_EQ(rp->Connect(in, &want), 0);
    CHECK_EQ(rp->ConnectionMediaType(&m), 0);
    CHECK(geq(m.major, MEDIATYPE_Stream_) && geq(m.sub, MEDIASUBTYPE_WAVE_) && geq(m.format, FORMAT_None_));
    mt_free(&m);
    CHECK_EQ(rp->Disconnect(), 0);
    CHECK_EQ(rp->ConnectionMediaType(&m), VFW_E_NOT_CONNECTED_);
    reader_pin_orphan(rp);
    rp->Release();
    rd->Release();
    sp->Release();
    in->Release();
    parser->Release();
    srcf->Release();
}

TEST(dshow_media_control_wine_bodies)
{
    mixer_init(48000, 256);
    IFilterGraph2_ *g = nullptr;
    CoCreateInstance(&CLSID_Graph_, nullptr, 1, G_(IID_IFilterGraph2_), (void **)&g);
    CHECK(g != nullptr);
    if (!g) return;
    IMediaControl_ *mc = qi<IMediaControl_>(g, IID_IMediaControl_);
    CHECK(mc != nullptr);
    if (mc) {
        /* wine-9.0:dlls/quartz/filtergraph.c MediaControl_AddSourceFilter / _get_FilterCollection / _get_RegFilterCollection:
         * a FIXME, S_OK, the out pointer is not touched */
        auto fn = WS("C:\\none.mp3");
        void *out = (void *)0x1234;
        CHECK_EQ(mc->AddSourceFilter(fn.data(), &out), 0);
        CHECK(out == (void *)0x1234);
        CHECK_EQ(mc->get_FilterCollection(&out), 0);
        CHECK(out == (void *)0x1234);
        CHECK_EQ(mc->get_RegFilterCollection(&out), 0);
        CHECK(out == (void *)0x1234);
        mc->Release();
    }
    g->Release();
}
