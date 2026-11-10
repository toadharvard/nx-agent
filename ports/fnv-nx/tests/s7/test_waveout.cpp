/* test_waveout.cpp - winmm waveOut (platform/audio/waveout.cpp): format/flag/handle validation tables, open/close
 * callbacks (FUNCTION, EVENT, THREAD), header life cycle with exact flag values, PCM arriving at the shared mixer
 * sink bit-exactly, 8-bit mono 22.05 kHz conversion, pause/restart/reset, loops, volume, position, caps, error text.
 * The mixer is stepped by hand (manual backend); fnvnx_waveout_service() runs the feeder pass and drains the
 * callback queue, so every check is deterministic. */
#include "s7_test.h"

#include <dsound.h>
#include "fnvnx_win32_ext.h"
#include "../../platform/audio/backend.h"
#include "../../platform/audio/mixer.h"

#include <stdint.h>
#include <string.h>
#include <vector>

extern "C" {
void fnvnx_waveout_service(void);
UINT waveOutGetNumDevs(void);
UINT waveOutGetDevCapsA(uintptr_t, void *, UINT);
UINT waveOutGetErrorTextA(UINT, char *, UINT);
UINT waveOutOpen(void **, UINT, const void *, uintptr_t, uintptr_t, DWORD);
UINT waveOutClose(void *);
UINT waveOutPrepareHeader(void *, void *, UINT);
UINT waveOutUnprepareHeader(void *, void *, UINT);
UINT waveOutWrite(void *, void *, UINT);
UINT waveOutPause(void *);
UINT waveOutRestart(void *);
UINT waveOutReset(void *);
UINT waveOutSetVolume(void *, DWORD);
UINT waveOutGetVolume(void *, DWORD *);
UINT waveOutGetPosition(void *, void *, UINT);
}

namespace {
struct Hdr { char *lpData; uint32_t len, rec; uintptr_t user; uint32_t flags, loops; Hdr *next; uintptr_t res; };
#pragma pack(push, 1)
struct Mmt { UINT type; union { uint32_t v; uint8_t raw[8]; } u; };
struct Caps { uint16_t mid, pid; UINT ver; char name[32]; uint32_t formats; uint16_t ch, res; uint32_t support; };
#pragma pack(pop)
#pragma pack(push, 1)
struct Fmt { WAVEFORMATEX w; uint8_t ext[22]; };
#pragma pack(pop)

const UINT MAPPER = 0xFFFFFFFFu;
enum { OK = 0, BADDEV = 2, INVALHANDLE = 5, BADERRNUM = 9, INVALFLAG = 10, INVALPARAM = 11,
       BADFORMAT = 32, STILLPLAYING = 33, UNPREPARED = 34 };
enum { F_DONE = 1, F_PREP = 2, F_BEGIN = 4, F_END = 8, F_QUEUE = 0x10 };
enum { CB_NULL = 0, CB_WINDOW = 0x10000, CB_THREAD = 0x20000, CB_FUNC = 0x30000, CB_EVENT = 0x50000 };

Fmt pcm(unsigned rate, unsigned ch, unsigned bits)
{
    Fmt f; memset(&f, 0, sizeof f);
    f.w.wFormatTag = 1; f.w.nChannels = (WORD)ch; f.w.nSamplesPerSec = rate; f.w.wBitsPerSample = (WORD)bits;
    f.w.nBlockAlign = (WORD)(ch * bits / 8); f.w.nAvgBytesPerSec = rate * f.w.nBlockAlign;
    return f;
}
Fmt extensible(Fmt f, uint32_t sub1)
{
    f.w.wFormatTag = 0xFFFE; f.w.cbSize = 22;
    memcpy(f.ext, &f.w.wBitsPerSample, 2);                      /* wValidBitsPerSample */
    uint32_t mask = f.w.nChannels == 1 ? 4 : 3;
    memcpy(f.ext + 2, &mask, 4);
    uint8_t g[16] = {0, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71};
    memcpy(g, &sub1, 4);
    memcpy(f.ext + 6, g, 16);
    return f;
}
UINT query(const Fmt &f, UINT dev = 0, DWORD fl = 0) { return waveOutOpen(nullptr, dev, &f, 0, 0, 1 | fl); }

struct Ev { UINT msg; uintptr_t hwo, inst, p1; };
std::vector<Ev> g_ev;
void CALLBACK_(void *hwo, UINT msg, uintptr_t inst, uintptr_t p1, uintptr_t)
{
    g_ev.push_back({msg, (uintptr_t)hwo, inst, p1});
}

void *open(const Fmt &f, uintptr_t cb = (uintptr_t)&CALLBACK_, DWORD kind = CB_FUNC, uintptr_t inst = 77)
{
    void *h = nullptr;
    g_ev.clear();
    CHECK_EQ(waveOutOpen(&h, 0, &f, cb, inst, kind), OK);
    return h;
}
void settle() { fnvnx_waveout_service(); }
void shut(void *h)
{
    CHECK_EQ(waveOutReset(h), OK);
    CHECK_EQ(waveOutClose(h), OK);
    settle();
}
void play(void *h, Hdr &hd, void *data, uint32_t bytes, uint32_t fl = 0, uint32_t loops = 0)
{
    memset(&hd, 0, sizeof hd);
    hd.lpData = (char *)data; hd.len = bytes; hd.flags = fl; hd.loops = loops;
    CHECK_EQ(waveOutPrepareHeader(h, &hd, sizeof hd), OK);
    CHECK_EQ(waveOutWrite(h, &hd, sizeof hd), OK);
}
std::vector<float> sink(uint32_t frames)
{
    std::vector<float> o((size_t)frames * 2);
    mixer_render_f32(o.data(), frames);
    return o;
}
/* render in backend-sized periods, running the feeder after each; collects the interleaved output */
std::vector<float> run(uint32_t frames)
{
    std::vector<float> all;
    for (uint32_t d = 0; d < frames;) {
        uint32_t n = frames - d < 512 ? frames - d : 512;
        std::vector<float> p = sink(n);
        all.insert(all.end(), p.begin(), p.end());
        settle();
        d += n;
    }
    return all;
}
}

TEST(waveout_format_table)
{
    fnvnx_audio_start();
    CHECK_EQ(waveOutGetNumDevs(), 1u);
    struct { Fmt f; UINT rc; } t[] = {
        {pcm(48000, 2, 16), OK}, {pcm(48000, 1, 16), OK}, {pcm(8000, 1, 8), OK}, {pcm(22050, 2, 8), OK},
        {pcm(1, 1, 8), OK}, {pcm(384000, 2, 16), OK}, {pcm(11025, 2, 16), OK},
        {pcm(0, 2, 16), BADFORMAT}, {pcm(384001, 2, 16), BADFORMAT}, {pcm(48000, 0, 16), BADFORMAT},
        {pcm(48000, 3, 16), BADFORMAT}, {pcm(48000, 2, 24), BADFORMAT}, {pcm(48000, 2, 32), BADFORMAT},
        {pcm(48000, 2, 4), BADFORMAT}, {pcm(48000, 2, 0), BADFORMAT},
        {extensible(pcm(44100, 2, 16), 1), OK}, {extensible(pcm(44100, 1, 8), 1), OK},
        {extensible(pcm(44100, 2, 16), 3), BADFORMAT},
    };
    for (auto &e : t) CHECK_EQ(query(e.f), e.rc);
    Fmt f = pcm(48000, 2, 16); f.w.nBlockAlign = 3; CHECK_EQ(query(f), BADFORMAT);
    f = pcm(48000, 2, 16); f.w.nAvgBytesPerSec += 1; CHECK_EQ(query(f), BADFORMAT);
    f = pcm(48000, 2, 16); f.w.wFormatTag = 3; CHECK_EQ(query(f), BADFORMAT);
    f = pcm(48000, 2, 16); f.w.wFormatTag = 2; CHECK_EQ(query(f), BADFORMAT);
    f = extensible(pcm(48000, 2, 16), 1); f.w.cbSize = 21; CHECK_EQ(query(f), BADFORMAT);
    /* devices, flags, parameters */
    f = pcm(48000, 2, 16);
    CHECK_EQ(query(f, 0), OK); CHECK_EQ(query(f, MAPPER), OK);
    CHECK_EQ(query(f, 1), BADDEV); CHECK_EQ(query(f, 7), BADDEV); CHECK_EQ(query(f, MAPPER - 1), BADDEV);
    CHECK_EQ(waveOutOpen(nullptr, 0, &f, 0, 0, 0x40000 | 1), INVALFLAG);
    CHECK_EQ(waveOutOpen(nullptr, 0, &f, 0, 0, 0x60000 | 1), INVALFLAG);
    CHECK_EQ(waveOutOpen(nullptr, 0, &f, 0, 0, 0x70000 | 1), INVALFLAG);
    CHECK_EQ(waveOutOpen(nullptr, 0, &f, 0, 0, 0x100000 | 1), INVALFLAG);
    CHECK_EQ(waveOutOpen(nullptr, 0, &f, 0, 0, 0x10), INVALFLAG);
    void *h = (void *)1234;
    CHECK_EQ(waveOutOpen(&h, 0, nullptr, 0, 0, 0), INVALPARAM);
    CHECK_EQ(waveOutOpen(nullptr, 0, &f, 0, 0, 0), INVALPARAM);
    CHECK_EQ(waveOutOpen(&h, 0, &f, 0, 0, 1), OK);
    CHECK(h == (void *)1234);                                   /* QUERY never writes the handle */
    CHECK_EQ(waveOutOpen(&h, 5, &f, 0, 0, 0), BADDEV);
    CHECK_EQ(waveOutOpen(&h, 0, &f, 0, 0, CB_FUNC), INVALPARAM);    /* function callback without a function */
    CHECK_EQ(waveOutOpen(&h, 0, &f, 0, 0, CB_EVENT), INVALPARAM);   /* not an event */
    CHECK_EQ(waveOutOpen(&h, 0, &f, 0x7777, 0, CB_EVENT), INVALPARAM);
    CHECK_EQ(waveOutOpen(&h, 0, &f, 0x7777, 0, CB_WINDOW), INVALPARAM);   /* not a window */
    CHECK_EQ(waveOutOpen(&h, 0, &f, 0, 0, CB_THREAD), INVALPARAM);
}

TEST(waveout_open_close_callbacks)
{
    Fmt f = pcm(48000, 2, 16);
    void *h = open(f);
    settle();
    CHECK_EQ(g_ev.size(), 1u);
    if (g_ev.size() == 1) { CHECK_EQ(g_ev[0].msg, 0x3BBu); CHECK_EQ(g_ev[0].inst, 77u); CHECK((void *)g_ev[0].hwo == h); }
    CHECK(h != nullptr);
    CHECK_EQ(waveOutClose(h), OK);
    settle();
    CHECK_EQ(g_ev.size(), 2u);
    if (g_ev.size() == 2) { CHECK_EQ(g_ev[1].msg, 0x3BCu); CHECK_EQ(g_ev[1].inst, 77u); }
    CHECK_EQ(waveOutClose(h), INVALHANDLE);                     /* the handle is invalid after close */

    HANDLE ev = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    void *h2 = nullptr;
    CHECK_EQ(waveOutOpen(&h2, MAPPER, &f, (uintptr_t)ev, 0, CB_EVENT), OK);
    settle();
    CHECK_EQ(WaitForSingleObject(ev, 0), 0u);                   /* signalled by WOM_OPEN */
    CHECK_EQ(WaitForSingleObject(ev, 0), 0x102u);               /* auto-reset */
    CHECK_EQ(waveOutClose(h2), OK);
    settle();
    CHECK_EQ(WaitForSingleObject(ev, 0), 0u);                   /* WOM_CLOSE */
    CloseHandle(ev);

    MSG m; memset(&m, 0, sizeof m);
    void *h3 = nullptr;
    CHECK_EQ(waveOutOpen(&h3, 0, &f, 1, 0, CB_THREAD), OK);
    settle();
    CHECK(PeekMessageA(&m, nullptr, 0, 0, 1));
    CHECK_EQ(m.message, 0x3BBu);
    CHECK_EQ(waveOutClose(h3), OK);
    settle();
    while (PeekMessageA(&m, nullptr, 0, 0, 1)) {}
}

TEST(waveout_life_cycle)
{
    Fmt f = pcm(48000, 2, 16);
    void *h = open(f);
    std::vector<int16_t> pcm16(1024 * 2);
    for (size_t i = 0; i < pcm16.size(); i++) pcm16[i] = (int16_t)(((int)i * 37) % 30000 - 15000);
    Hdr hd; memset(&hd, 0, sizeof hd);
    hd.lpData = (char *)pcm16.data(); hd.len = (uint32_t)(pcm16.size() * 2);
    CHECK_EQ(waveOutWrite(h, &hd, sizeof hd), UNPREPARED);
    CHECK_EQ(waveOutPrepareHeader(h, &hd, sizeof hd - 1), INVALPARAM);
    CHECK_EQ(waveOutPrepareHeader(h, nullptr, sizeof hd), INVALPARAM);
    CHECK_EQ(waveOutPrepareHeader(h, &hd, sizeof hd), OK);
    CHECK_EQ(hd.flags, (uint32_t)F_PREP);
    CHECK_EQ(waveOutWrite(h, &hd, 4), INVALPARAM);
    CHECK_EQ(waveOutWrite(h, nullptr, sizeof hd), INVALPARAM);
    CHECK_EQ(waveOutWrite(h, &hd, sizeof hd), OK);
    CHECK_EQ(hd.flags, (uint32_t)(F_PREP | F_QUEUE));
    CHECK_EQ(waveOutWrite(h, &hd, sizeof hd), STILLPLAYING);
    CHECK_EQ(waveOutUnprepareHeader(h, &hd, sizeof hd), STILLPLAYING);
    CHECK_EQ(waveOutClose(h), STILLPLAYING);
    std::vector<float> o = run(1024);                            /* full volume: bit-exact PCM at the sink */
    bool same = o.size() == pcm16.size();
    for (size_t i = 0; same && i < o.size(); i++) same = o[i] == (float)pcm16[i] * (1.0f / 32768.0f);
    CHECK(same);
    CHECK_EQ(hd.flags, (uint32_t)(F_PREP | F_DONE));
    size_t done = 0;
    for (auto &e : g_ev) if (e.msg == 0x3BD) { done++; CHECK_EQ(e.p1, (uintptr_t)&hd); CHECK_EQ(e.inst, 77u); }
    CHECK_EQ(done, 1u);
    std::vector<float> after = run(64);                          /* queue empty: the sink is silent again */
    bool silent = true;
    for (float v : after) silent = silent && v == 0.0f;
    CHECK(silent);
    Mmt p; memset(&p, 0, sizeof p); p.type = 2;
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 1024u);
    CHECK_EQ(waveOutUnprepareHeader(h, &hd, sizeof hd), OK);
    CHECK_EQ(hd.flags & F_PREP, 0u);
    CHECK_EQ(waveOutUnprepareHeader(h, &hd, sizeof hd), OK);
    /* a block longer than the device ring streams through it intact, in order with its neighbour */
    std::vector<int16_t> big(3 * 48000 * 2), small(300 * 2);
    for (size_t i = 0; i < big.size(); i++) big[i] = (int16_t)((int)(i % 20011) - 10000);
    for (size_t i = 0; i < small.size(); i++) small[i] = (int16_t)(-(int)(i % 997));
    Hdr a, b;
    g_ev.clear();
    play(h, a, big.data(), (uint32_t)(big.size() * 2));
    play(h, b, small.data(), (uint32_t)(small.size() * 2));
    std::vector<float> ob = run(3 * 48000 + 300);
    bool ok = ob.size() == big.size() + small.size();
    for (size_t i = 0; ok && i < big.size(); i++) ok = ob[i] == (float)big[i] * (1.0f / 32768.0f);
    for (size_t i = 0; ok && i < small.size(); i++) ok = ob[big.size() + i] == (float)small[i] * (1.0f / 32768.0f);
    CHECK(ok);
    std::vector<uintptr_t> order;
    for (auto &e : g_ev) if (e.msg == 0x3BD) order.push_back(e.p1);
    CHECK_EQ(order.size(), 2u);
    if (order.size() == 2) { CHECK(order[0] == (uintptr_t)&a); CHECK(order[1] == (uintptr_t)&b); }
    CHECK_EQ(waveOutUnprepareHeader(h, &a, sizeof a), OK);
    CHECK_EQ(waveOutUnprepareHeader(h, &b, sizeof b), OK);
    CHECK_EQ(waveOutClose(h), OK);
    settle();
}

TEST(waveout_mono8_22k_conversion)
{
    Fmt f = pcm(22050, 1, 8);
    void *h = open(f);
    std::vector<uint8_t> d(22050, 0xC0);                         /* +0.5 */
    Hdr hd;
    play(h, hd, d.data(), (uint32_t)d.size());
    std::vector<float> o = run(48000 + 1200);
    uint32_t n = 0;
    bool both = true;
    for (size_t i = 0; i + 1 < o.size(); i += 2) {
        if (o[i] != 0.0f) n++;
        both = both && o[i] == o[i + 1];
    }
    CHECK(n >= 47997 && n <= 48001);   /* the last source frame needs its successor: <= 3 sink frames wait */
    CHECK(both);
    CHECK_NEAR(o[100], 0.5f, 1e-6);
    CHECK_EQ(hd.flags & F_DONE, (uint32_t)F_DONE);
    Mmt p; memset(&p, 0, sizeof p); p.type = 1;
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.type, 1u);
    CHECK_EQ(p.u.v, 1000u);
    p.type = 4;
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 22050u);
    p.type = 0x20;                                              /* unsupported: answered in bytes */
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.type, 4u);
    CHECK_EQ(waveOutGetPosition(h, &p, 8), INVALPARAM);
    CHECK_EQ(waveOutGetPosition(h, nullptr, sizeof p), INVALPARAM);
    CHECK_EQ(waveOutUnprepareHeader(h, &hd, sizeof hd), OK);
    shut(h);
    /* 8-bit unsigned zero point and the two-channel layout */
    Fmt s = pcm(48000, 2, 8);
    h = open(s);
    uint8_t st[8] = {0x80, 0xFF, 0x00, 0x80, 0xC0, 0x40, 0x80, 0x80};
    play(h, hd, st, 8);
    o = run(4);
    CHECK_NEAR(o[0], 0.0, 1e-7); CHECK_NEAR(o[1], 127.0 / 128, 1e-7); CHECK_NEAR(o[2], -1.0, 1e-7);
    CHECK_NEAR(o[4], 0.5, 1e-7); CHECK_NEAR(o[5], -0.5, 1e-7);
    shut(h);
}

TEST(waveout_pause_reset)
{
    Fmt f = pcm(48000, 2, 16);
    void *h = open(f);
    std::vector<int16_t> d(4800 * 2, 8000);
    Hdr a, b, c;
    play(h, a, d.data(), (uint32_t)(d.size() * 2));
    play(h, b, d.data(), (uint32_t)(d.size() * 2));
    play(h, c, d.data(), (uint32_t)(d.size() * 2));
    run(1000);
    Mmt p; memset(&p, 0, sizeof p); p.type = 2;
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 1000u);
    CHECK_EQ(waveOutPause(h), OK);
    std::vector<float> o = run(700);
    bool silent = true;
    for (float v : o) silent = silent && v == 0.0f;
    CHECK(silent);
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 1000u);                                      /* frozen */
    CHECK_EQ(waveOutRestart(h), OK);
    o = run(500);
    CHECK_NEAR(o[0], 8000.0 / 32768, 1e-6);
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 1500u);
    CHECK_EQ(waveOutPause(h), OK);
    g_ev.clear();
    CHECK_EQ(waveOutReset(h), OK);
    settle();
    std::vector<uintptr_t> order;
    for (auto &e : g_ev) if (e.msg == 0x3BD) order.push_back(e.p1);
    CHECK_EQ(order.size(), 3u);
    if (order.size() == 3) { CHECK(order[0] == (uintptr_t)&a); CHECK(order[1] == (uintptr_t)&b); CHECK(order[2] == (uintptr_t)&c); }
    CHECK_EQ(a.flags, (uint32_t)(F_PREP | F_DONE));
    CHECK_EQ(c.flags, (uint32_t)(F_PREP | F_DONE));
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 0u);
    o = run(128);                                                /* reset also cleared the paused state */
    silent = true;
    for (float v : o) silent = silent && v == 0.0f;
    CHECK(silent);
    play(h, a, d.data(), 100 * 4);
    o = run(100);
    CHECK_NEAR(o[0], 8000.0 / 32768, 1e-6);
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 100u);
    CHECK_EQ(waveOutUnprepareHeader(h, &a, sizeof a), OK);
    CHECK_EQ(waveOutUnprepareHeader(h, &b, sizeof b), OK);
    CHECK_EQ(waveOutUnprepareHeader(h, &c, sizeof c), OK);
    shut(h);
}

TEST(waveout_loops)
{
    Fmt f = pcm(48000, 2, 16);
    void *h = open(f);
    std::vector<int16_t> a(100 * 2), b(50 * 2);
    for (size_t i = 0; i < a.size(); i++) a[i] = (int16_t)(1000 + (int)i);
    for (size_t i = 0; i < b.size(); i++) b[i] = (int16_t)(-2000 - (int)i);
    Hdr x, y, z;
    play(h, x, a.data(), (uint32_t)(a.size() * 2), F_BEGIN | F_END, 3);
    std::vector<float> o = run(300 + 40);
    bool ok = true;
    for (int rep = 0; rep < 3; rep++)
        for (size_t i = 0; i < a.size(); i++) ok = ok && o[rep * a.size() + i] == (float)a[i] / 32768.0f;
    for (size_t i = 300 * 2; i < o.size(); i++) ok = ok && o[i] == 0.0f;
    CHECK(ok);
    CHECK_EQ(x.flags & F_DONE, (uint32_t)F_DONE);
    Mmt p; memset(&p, 0, sizeof p); p.type = 2;
    CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), OK);
    CHECK_EQ(p.u.v, 300u);                                       /* counts every pass */
    /* two-block loop A B A B, then C once */
    std::vector<int16_t> cc(20 * 2, 555);
    g_ev.clear();
    CHECK_EQ(waveOutUnprepareHeader(h, &x, sizeof x), OK);
    play(h, x, a.data(), (uint32_t)(a.size() * 2), F_BEGIN, 2);
    play(h, y, b.data(), (uint32_t)(b.size() * 2), F_END, 0);
    play(h, z, cc.data(), (uint32_t)(cc.size() * 2));
    o = run(2 * 150 + 20 + 30);
    std::vector<float> want;
    for (int rep = 0; rep < 2; rep++) {
        for (int16_t v : a) want.push_back((float)v / 32768.0f);
        for (int16_t v : b) want.push_back((float)v / 32768.0f);
    }
    for (int16_t v : cc) want.push_back((float)v / 32768.0f);
    ok = o.size() >= want.size();
    for (size_t i = 0; ok && i < want.size(); i++) ok = o[i] == want[i];
    CHECK(ok);
    std::vector<uintptr_t> order;
    for (auto &e : g_ev) if (e.msg == 0x3BD) order.push_back(e.p1);
    CHECK_EQ(order.size(), 3u);
    if (order.size() == 3) { CHECK(order[0] == (uintptr_t)&x); CHECK(order[1] == (uintptr_t)&y); CHECK(order[2] == (uintptr_t)&z); }
    CHECK_EQ(waveOutUnprepareHeader(h, &x, sizeof x), OK);
    CHECK_EQ(waveOutUnprepareHeader(h, &y, sizeof y), OK);
    CHECK_EQ(waveOutUnprepareHeader(h, &z, sizeof z), OK);
    /* a zero-length block completes at once, in order */
    g_ev.clear();
    play(h, x, a.data(), 0);
    settle();
    CHECK_EQ(x.flags & F_DONE, (uint32_t)F_DONE);
    shut(h);
}

TEST(waveout_invalid_handles)
{
    Hdr hd; memset(&hd, 0, sizeof hd);
    void *bad[] = {nullptr, (void *)1, (void *)0x12345678, (void *)(uintptr_t)0xFFFFFFFFu};
    for (void *h : bad) {
        CHECK_EQ(waveOutClose(h), INVALHANDLE);
        CHECK_EQ(waveOutPrepareHeader(h, &hd, sizeof hd), INVALHANDLE);
        CHECK_EQ(waveOutUnprepareHeader(h, &hd, sizeof hd), INVALHANDLE);
        CHECK_EQ(waveOutWrite(h, &hd, sizeof hd), INVALHANDLE);
        CHECK_EQ(waveOutPause(h), INVALHANDLE);
        CHECK_EQ(waveOutRestart(h), INVALHANDLE);
        CHECK_EQ(waveOutReset(h), INVALHANDLE);
        Mmt p; memset(&p, 0, sizeof p); p.type = 4;
        CHECK_EQ(waveOutGetPosition(h, &p, sizeof p), INVALHANDLE);
    }
    DWORD v = 0;
    CHECK_EQ(waveOutSetVolume((void *)0x12345678, 1), INVALHANDLE);
    CHECK_EQ(waveOutGetVolume((void *)0x12345678, &v), INVALHANDLE);
    CHECK_EQ(waveOutSetVolume((void *)5, 1), BADDEV);
    CHECK_EQ(waveOutGetVolume((void *)0, nullptr), INVALPARAM);
}

TEST(waveout_volume_caps_text_mixing)
{
    Fmt f = pcm(48000, 2, 16);
    void *h = open(f), *h2 = open(f);
    DWORD v = 0;
    CHECK_EQ(waveOutGetVolume(h, &v), OK);
    CHECK_EQ(v, 0xFFFFFFFFu);
    CHECK_EQ(waveOutSetVolume(h, 0x4000FFFFu), OK);              /* right 0x4000, left full */
    CHECK_EQ(waveOutGetVolume(h, &v), OK);
    CHECK_EQ(v, 0x4000FFFFu);
    CHECK_EQ(waveOutGetVolume(h2, &v), OK);
    CHECK_EQ(v, 0xFFFFFFFFu);
    std::vector<int16_t> d(64 * 2);
    for (size_t i = 0; i < d.size(); i += 2) { d[i] = 8000; d[i + 1] = 8000; }
    Hdr a, b;
    play(h, a, d.data(), (uint32_t)(d.size() * 2));
    std::vector<float> o = run(64);
    CHECK_NEAR(o[0], 8000.0 / 32768, 1e-6);
    CHECK_NEAR(o[1], 8000.0 / 32768 * 0x4000 / 65535.0, 1e-5);
    /* two handles mix into the one sink */
    play(h, a, d.data(), (uint32_t)(d.size() * 2));
    play(h2, b, d.data(), (uint32_t)(d.size() * 2));
    o = run(64);
    CHECK_NEAR(o[0], 2 * 8000.0 / 32768, 1e-6);
    CHECK_NEAR(o[1], 8000.0 / 32768 * (1 + 0x4000 / 65535.0), 1e-5);
    CHECK_EQ(waveOutSetVolume(nullptr, 0x80008000u), OK);       /* device-wide, by id */
    CHECK_EQ(waveOutGetVolume((void *)(uintptr_t)MAPPER, &v), OK);
    CHECK_EQ(v, 0x80008000u);
    play(h2, b, d.data(), (uint32_t)(d.size() * 2));
    o = run(64);
    CHECK_NEAR(o[0], 8000.0 / 32768 * 0x8000 / 65535.0, 1e-5);
    CHECK_EQ(waveOutSetVolume(nullptr, 0xFFFFFFFFu), OK);
    CHECK_EQ(waveOutUnprepareHeader(h, &a, sizeof a), OK);
    CHECK_EQ(waveOutUnprepareHeader(h2, &b, sizeof b), OK);
    shut(h); shut(h2);

    Caps c; memset(&c, 0xEE, sizeof c);
    CHECK_EQ(waveOutGetDevCapsA(0, &c, sizeof c), OK);
    CHECK_EQ(c.mid, 1u); CHECK_EQ(c.pid, 0x64u); CHECK_EQ(c.ver, 0x0600u);
    CHECK(!strcmp(c.name, "Speakers"));
    CHECK_EQ(c.formats, 0xFFFFFu); CHECK_EQ(c.ch, 2u);
    CHECK_EQ(c.support, 0x04u | 0x08u | 0x10u);
    memset(&c, 0xEE, sizeof c);
    CHECK_EQ(waveOutGetDevCapsA(MAPPER, &c, 4), OK);
    CHECK_EQ(c.mid, 1u); CHECK_EQ(c.pid, 0x64u);
    CHECK_EQ(c.ver, 0xEEEEEEEEu);                                /* truncated to cbwoc */
    CHECK_EQ(waveOutGetDevCapsA(1, &c, sizeof c), BADDEV);
    CHECK_EQ(waveOutGetDevCapsA(0, nullptr, sizeof c), INVALPARAM);

    char t[256];
    for (UINT e = 0; e <= 20; e++) { CHECK_EQ(waveOutGetErrorTextA(e, t, sizeof t), OK); CHECK(t[0] != 0); }
    for (UINT e = 32; e <= 35; e++) { CHECK_EQ(waveOutGetErrorTextA(e, t, sizeof t), OK); CHECK(t[0] != 0); }
    CHECK_EQ(waveOutGetErrorTextA(21, t, sizeof t), BADERRNUM);
    CHECK_EQ(waveOutGetErrorTextA(36, t, sizeof t), BADERRNUM);
    CHECK_EQ(waveOutGetErrorTextA(0, nullptr, 10), INVALPARAM);
    memset(t, 'x', sizeof t);
    CHECK_EQ(waveOutGetErrorTextA(33, t, 8), OK);
    CHECK_EQ(strlen(t), 7u);                                     /* truncated, NUL-terminated */
    CHECK_EQ(t[8], 'x');
}

/* ---- pull model: exact for every schedule ---- */
extern "C" {
UINT timeBeginPeriod(UINT);
UINT timeEndPeriod(UINT);
UINT timeGetDevCaps(void *, UINT);
unsigned fnvnx_timer_resolution_us(void);
}

namespace {
/* 24 blocks of 1..N frames of distinct 16-bit stereo data; returns total frames */
struct Train {
    std::vector<int16_t> pcm; std::vector<Hdr> h; std::vector<uint32_t> cum;
    Train() : h(24) {
        uint32_t t = 0;
        for (int j = 0; j < 24; j++) { t += 7 + (uint32_t)j * 13; cum.push_back(t); }
        pcm.resize((size_t)t * 2);
        for (size_t i = 0; i < pcm.size(); i++) pcm[i] = (int16_t)((int)(i * 31 % 20011) - 10000);
    }
    uint32_t total() const { return cum.back(); }
    void queue(void *hw) {
        uint32_t at = 0;
        for (int j = 0; j < 24; j++) { play(hw, h[j], pcm.data() + (size_t)at * 2, (cum[j] - at) * 4); at = cum[j]; }
    }
};
std::vector<uintptr_t> done_order() { std::vector<uintptr_t> o; for (auto &e : g_ev) if (e.msg == 0x3BD) o.push_back(e.p1); return o; }
uint64_t pos_frames(void *h) { Mmt p; memset(&p, 0, sizeof p); p.type = 2; waveOutGetPosition(h, &p, sizeof p); return p.u.v; }
}

TEST(waveout_pull_exact_any_split)
{
    Fmt f = pcm(48000, 2, 16);
    Train tr;
    void *h = open(f);
    tr.queue(h);
    std::vector<float> one = sink(tr.total() + 50);               /* ONE render spanning every block */
    settle();
    std::vector<uintptr_t> o1 = done_order();
    CHECK_EQ(o1.size(), 24u);
    for (size_t j = 0; j < o1.size() && j < 24; j++) CHECK(o1[j] == (uintptr_t)&tr.h[j]);
    CHECK_EQ(pos_frames(h), tr.total());
    bool ok = true;
    for (size_t i = 0; i < tr.pcm.size(); i++) ok = ok && one[i] == (float)tr.pcm[i] * (1.0f / 32768.0f);
    for (size_t i = tr.pcm.size(); i < one.size(); i++) ok = ok && one[i] == 0.0f;
    CHECK(ok);
    for (auto &x : tr.h) CHECK_EQ(waveOutUnprepareHeader(h, &x, sizeof x), OK);
    shut(h);
    /* the same in 1-frame steps: identical PCM, and block j completes at exactly frame cum[j] */
    h = open(f);
    tr.queue(h);
    std::vector<float> step;
    size_t seen = 0;
    bool exact = true;
    for (uint32_t k = 1; k <= tr.total() + 50; k++) {
        std::vector<float> p = sink(1);
        step.insert(step.end(), p.begin(), p.end());
        settle();
        size_t want = 0;
        while (want < 24 && tr.cum[want] <= k) want++;
        exact = exact && done_order().size() == want;
        seen = want;
    }
    CHECK(exact);
    CHECK_EQ(seen, 24u);
    CHECK(done_order() == o1 || [&] { std::vector<uintptr_t> a = done_order(); return a.size() == 24 && a[0] == (uintptr_t)&tr.h[0] && a[23] == (uintptr_t)&tr.h[23]; }());
    CHECK(step == one);
    CHECK_EQ(pos_frames(h), tr.total());
    shut(h);
    /* a block that is not a whole number of frames is refused, not truncated */
    h = open(f);
    Hdr bad; uint8_t buf[16] = {0};
    memset(&bad, 0, sizeof bad); bad.lpData = (char *)buf; bad.len = 6;
    CHECK_EQ(waveOutPrepareHeader(h, &bad, sizeof bad), OK);
    CHECK_EQ(waveOutWrite(h, &bad, sizeof bad), INVALPARAM);
    CHECK_EQ(bad.flags & F_QUEUE, 0u);
    shut(h);
}

TEST(waveout_pull_position_is_resampler_consumption)
{
    /* 22.05 kHz mono 8-bit into the 48 kHz sink. Output i sits at source position p_i = i*step (32.32), step =
     * floor(22050/48000 * 2^32); it reads frame floor(p_i) and, if p_i has a fraction, floor(p_i)+1. After K sink
     * frames the mixer has pulled (p>>32)+1+((uint32)p != 0) with p = (K-1)*step, however K is split. */
    const uint64_t step = (uint64_t)((double)22050 / 48000 * 4294967296.0);
    std::vector<uint8_t> d(22050);
    for (size_t i = 0; i < d.size(); i++) d[i] = (uint8_t)(i * 7);
    const uint32_t splits[] = {1, 3, 512, 977, 100000};
    for (uint32_t sp : splits) {
        void *h = open(pcm(22050, 1, 8));
        Hdr hd;
        play(h, hd, d.data(), (uint32_t)d.size());
        uint32_t K = 0;
        for (int r = 0; r < 4; r++) {
            uint32_t n = sp > 5000 ? 5000 : sp;
            for (uint32_t t = 0; t < (sp == 1 ? 40u : 1u); t++) { sink(n); K += n; }
            const uint64_t p = (uint64_t)(K - 1) * step;
            uint64_t want = (p >> 32) + 1 + ((uint32_t)p != 0);
            CHECK_EQ(pos_frames(h), want);
        }
        shut(h);
    }
    /* run to the end: every frame consumed, the block done at its last frame */
    void *h = open(pcm(22050, 1, 8));
    Hdr hd;
    play(h, hd, d.data(), (uint32_t)d.size());
    sink(48000 + 10);
    settle();
    CHECK_EQ(pos_frames(h), 22050u);
    CHECK_EQ(hd.flags & F_DONE, (uint32_t)F_DONE);
    shut(h);
}

TEST(timer_period_bookkeeping)
{
    UINT caps[2] = {0, 0};
    CHECK_EQ(timeGetDevCaps(caps, 8), 0u);
    CHECK_EQ(caps[0], 1u); CHECK_EQ(caps[1], 1000000u);
    CHECK_EQ(timeGetDevCaps(caps, 4), 129u);
    CHECK_EQ(timeGetDevCaps(nullptr, 8), 11u);
    CHECK_EQ(fnvnx_timer_resolution_us(), 15600u);
    CHECK_EQ(timeBeginPeriod(0), 97u);
    CHECK_EQ(timeBeginPeriod(1000001), 97u);
    CHECK_EQ(timeEndPeriod(5), 97u);                           /* no matching begin */
    CHECK_EQ(timeBeginPeriod(5), 0u);
    CHECK_EQ(timeBeginPeriod(5), 0u);
    CHECK_EQ(timeBeginPeriod(2), 0u);
    CHECK_EQ(fnvnx_timer_resolution_us(), 2000u);
    CHECK_EQ(timeEndPeriod(3), 97u);
    CHECK_EQ(timeEndPeriod(2), 0u);
    CHECK_EQ(timeEndPeriod(2), 97u);
    CHECK_EQ(fnvnx_timer_resolution_us(), 5000u);
    CHECK_EQ(timeEndPeriod(5), 0u);
    CHECK_EQ(fnvnx_timer_resolution_us(), 5000u);              /* one reference left */
    CHECK_EQ(timeEndPeriod(5), 0u);
    CHECK_EQ(fnvnx_timer_resolution_us(), 15600u);
    CHECK_EQ(timeEndPeriod(5), 97u);
}
