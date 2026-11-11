/* test_vclock_audio.cpp - the audio device on virtual time (definition VC1, platform/include/fnvnx_clock.h): waveOut block
 * completions (WOM_DONE at t_write + n/rate, after the previous block's completion, pauses shifting the timeline) and
 * DirectSound cursors and notifications as functions of the virtual time of the calling thread. Every case returns at once
 * unless the linked clock says "virtual": the default binary runs them as no-ops, `make -C tests/s7 vclock` runs them.
 *
 * The device is the clock's pump (null backend): periods of FNVNX_AUDIO_PERIOD frames at 48 kHz from the instant the audio
 * started, n * 512 / 48000 s each, rounded down to 100 ns. Expected values are computed here from that model. */
#include "s7_test.h"

#include <dsound.h>
#include "fnvnx_win32_ext.h"
#include "../../platform/audio/backend.h"
#include "../../platform/audio/mixer.h"
#include "../../platform/include/fnvnx_clock.h"

#include <stdint.h>
#include <string.h>

extern "C" {
DWORD GetCurrentThreadId(void);
void fnvnx_waveout_service(void);
UINT waveOutOpen(void **, UINT, const void *, uintptr_t, uintptr_t, DWORD);
UINT waveOutClose(void *);
UINT waveOutPrepareHeader(void *, void *, UINT);
UINT waveOutUnprepareHeader(void *, void *, UINT);
UINT waveOutWrite(void *, void *, UINT);
UINT waveOutPause(void *);
UINT waveOutRestart(void *);
UINT waveOutGetPosition(void *, void *, UINT);
}

namespace {
struct Hdr { char *lpData; uint32_t len, rec; uintptr_t user; uint32_t flags, loops; Hdr *next; uintptr_t res; };
#pragma pack(push, 1)
struct Mmt { UINT type; union { uint32_t v; uint8_t raw[8]; } u; };
#pragma pack(pop)
const uint64_t NS = 1000000000ull;

bool virt() { return !strcmp(clk_name(), "virtual"); }
uint64_t now_ns() { return clk_peek_ns(); }

/* the instant of the n-th period of a device that started at t0 (the model of clock_virtual.cpp) */
uint64_t boundary(uint64_t t0, uint64_t n) { return t0 + (n * FNVNX_AUDIO_PERIOD * NS / FNVNX_AUDIO_RATE) / 100 * 100; }

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

/* a fresh audio device started at virtual t = 0 */
void audio_restart()
{
    fnvnx_audio_stop();
    fnvnx_audio_configure("null");
    clk_reset();
}
void audio_end()
{
    fnvnx_audio_stop();
    fnvnx_audio_configure("manual");
}
}   // namespace

TEST(vc_audio_waveout_completion_times)
{
    if (!virt()) return;
    audio_restart();
    WAVEFORMATEX w = pcm(48000, 2, 16);
    HANDLE ev = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    void *h = nullptr;
    CHECK_EQ(waveOutOpen(&h, 0, &w, (uintptr_t)ev, 0, 0x50000 /* CALLBACK_EVENT */), 0);
    fnvnx_waveout_service();
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_OBJECT_0);      /* WOM_OPEN signalled the event at t = 0 */
    CHECK_EQ(now_ns(), 0);
    static char data[4800 * 4];
    Hdr a, b, c;
    memset(&a, 0, sizeof a); memset(&b, 0, sizeof b); memset(&c, 0, sizeof c);
    a.lpData = b.lpData = c.lpData = data;
    a.len = 4800 * 4;       /* 100 ms */
    b.len = 2400 * 4;       /* 50 ms  */
    c.len = 4800 * 4;       /* 100 ms */
    CHECK_EQ(waveOutPrepareHeader(h, &a, sizeof a), 0);
    CHECK_EQ(waveOutPrepareHeader(h, &b, sizeof b), 0);
    CHECK_EQ(waveOutPrepareHeader(h, &c, sizeof c), 0);
    CHECK_EQ(waveOutWrite(h, &a, sizeof a), 0);               /* written at t = 0: completes at 100 ms */
    CHECK_EQ(WaitForSingleObject(ev, 20000), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), 100000000);
    CHECK(a.flags & 1);                                       /* WHDR_DONE */
    /* two more blocks written together at t = 100 ms: B completes 50 ms after, C 100 ms after B */
    CHECK_EQ(waveOutWrite(h, &b, sizeof b), 0);
    CHECK_EQ(waveOutWrite(h, &c, sizeof c), 0);
    CHECK_EQ(WaitForSingleObject(ev, 20000), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), 150000000);
    CHECK_EQ(WaitForSingleObject(ev, 20000), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), 250000000);
    /* an idle device: the next block starts when it is written */
    Sleep(1000);                                              /* t = 1.25 s */
    CHECK_EQ(now_ns(), 1250000000);
    CHECK_EQ(waveOutWrite(h, &a, sizeof a), 0);
    CHECK_EQ(WaitForSingleObject(ev, 20000), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), 1350000000);
    /* a pause shifts the timeline by its length */
    CHECK_EQ(waveOutWrite(h, &a, sizeof a), 0);               /* written at 1.35 s, would end at 1.45 s */
    Sleep(30);
    CHECK_EQ(waveOutPause(h), 0);                             /* t = 1.38 s */
    Sleep(100);
    CHECK_EQ(waveOutRestart(h), 0);                           /* t = 1.48 s: paused for 100 ms */
    CHECK_EQ(WaitForSingleObject(ev, 20000), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), 1550000000);
    Mmt pos;
    memset(&pos, 0, sizeof pos);
    pos.type = 2;                                             /* TIME_SAMPLES */
    CHECK_EQ(waveOutGetPosition(h, &pos, sizeof pos), 0);
    CHECK_EQ(pos.u.v, 4800 + 2400 + 4800 + 4800 + 4800);
    CHECK_EQ(waveOutUnprepareHeader(h, &a, sizeof a), 0);
    CHECK_EQ(waveOutClose(h), 0);
    CloseHandle(ev);
    audio_end();
}

TEST(vc_audio_waveout_callback_function_time)
{
    if (!virt()) return;
    audio_restart();
    /* a thread-message callback: the message carries the completion's instant, and the receiver takes it on retrieval */
    WAVEFORMATEX w = pcm(22050, 1, 16);
    void *h = nullptr;
    CHECK_EQ(waveOutOpen(&h, 0, &w, (uintptr_t)GetCurrentThreadId(), 0, 0x20000 /* CALLBACK_THREAD */), 0);
    static char data[2205 * 2];
    Hdr a;
    memset(&a, 0, sizeof a);
    a.lpData = data;
    a.len = sizeof data;                                      /* 2205 frames at 22050 Hz: 100 ms */
    Sleep(40);
    CHECK_EQ(waveOutPrepareHeader(h, &a, sizeof a), 0);
    CHECK_EQ(waveOutWrite(h, &a, sizeof a), 0);               /* written at 40 ms: completes at 140 ms */
    MSG m;
    int got = 0;
    for (int i = 0; i < 2000 && !got; i++) {                  /* polling with Sleep: the device runs as the thread's t advances */
        Sleep(1);
        if (PeekMessageA(&m, nullptr, 0x3BD, 0x3BD, PM_REMOVE)) got = 1;
    }
    CHECK(got);
    if (got) {
        CHECK_EQ(m.message, 0x3BD);
        CHECK_EQ(m.time, 140);                                /* MSG.time is the completion's instant in ms */
        CHECK(now_ns() >= 140000000);
    }
    CHECK_EQ(waveOutUnprepareHeader(h, &a, sizeof a), 0);
    CHECK_EQ(waveOutClose(h), 0);
    audio_end();
}

TEST(vc_audio_dsound_cursor_is_a_function_of_t)
{
    if (!virt()) return;
    audio_restart();
    IDirectSound8 *ds = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, &ds, nullptr), DS_OK);                 /* the audio starts at t = 0 */
    CHECK_EQ(ds->SetCooperativeLevel(nullptr, DSSCL_PRIORITY), DS_OK);
    WAVEFORMATEX w = pcm(48000, 1, 16);
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = sizeof d;
    d.dwFlags = DSBCAPS_GETCURRENTPOSITION2 | DSBCAPS_CTRLPOSITIONNOTIFY;
    d.dwBufferBytes = 96000;                                  /* 1 s */
    d.lpwfxFormat = &w;
    IDirectSoundBuffer *b = nullptr;
    CHECK_EQ(ds->CreateSoundBuffer(&d, &b, nullptr), DS_OK);
    DWORD p = 1, wr = 1;
    CHECK_EQ(b->GetCurrentPosition(&p, &wr), DS_OK);
    CHECK_EQ(p, 0); CHECK_EQ(wr, 0);                          /* stopped */
    CHECK_EQ(b->Play(0, 0, DSBPLAY_LOOPING), DS_OK);          /* at t = 0 */
    Sleep(100);
    uint64_t n = 0;
    while (boundary(0, n + 1) <= 100000000) n++;              /* periods that happened by t = 100 ms */
    CHECK_EQ(n, 9);
    CHECK_EQ(b->GetCurrentPosition(&p, &wr), DS_OK);
    CHECK_EQ(p, n * FNVNX_AUDIO_PERIOD * 2);                  /* the play cursor: the frames of those periods */
    CHECK_EQ(wr, p + FNVNX_AUDIO_PERIOD * 2);                 /* the write cursor: one period ahead */
    DWORD p2, w2;
    CHECK_EQ(b->GetCurrentPosition(&p2, &w2), DS_OK);         /* a call does not move time */
    CHECK_EQ(p2, p);
    Sleep(100);
    while (boundary(0, n + 1) <= 200000000) n++;
    CHECK_EQ(b->GetCurrentPosition(&p, &wr), DS_OK);
    CHECK_EQ(p, n * FNVNX_AUDIO_PERIOD * 2);
    Sleep(900);                                               /* t = 1.1 s: the loop wrapped */
    while (boundary(0, n + 1) <= 1100000000) n++;
    CHECK_EQ(b->GetCurrentPosition(&p, &wr), DS_OK);
    CHECK_EQ(p, (n * FNVNX_AUDIO_PERIOD * 2) % 96000);
    DWORD st = 0;
    CHECK_EQ(b->GetStatus(&st), DS_OK);
    CHECK(st & DSBSTATUS_PLAYING);
    b->Stop();
    b->Release();
    ds->Release();
    audio_end();
}

TEST(vc_audio_dsound_notification_times)
{
    if (!virt()) return;
    audio_restart();
    IDirectSound8 *ds = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, &ds, nullptr), DS_OK);
    ds->SetCooperativeLevel(nullptr, DSSCL_PRIORITY);
    WAVEFORMATEX w = pcm(48000, 1, 16);
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = sizeof d;
    d.dwFlags = DSBCAPS_GETCURRENTPOSITION2 | DSBCAPS_CTRLPOSITIONNOTIFY;
    d.dwBufferBytes = 96000;
    d.lpwfxFormat = &w;
    IDirectSoundBuffer *b = nullptr;
    CHECK_EQ(ds->CreateSoundBuffer(&d, &b, nullptr), DS_OK);
    IDirectSoundNotify *n = nullptr;
    CHECK_EQ(b->QueryInterface(IID_IDirectSoundNotify, (void **)&n), S_OK);
    HANDLE ev = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    DSBPOSITIONNOTIFY pos[2] = {{24000, ev}, {48000, ev}};    /* bytes: 250 ms and 500 ms into the buffer */
    CHECK_EQ(n->SetNotificationPositions(2, pos), DS_OK);
    CHECK_EQ(b->Play(0, 0, DSBPLAY_LOOPING), DS_OK);
    /* the wait runs the device: no real time passes, the thread's t becomes the instant of the period that crossed the offset */
    double r0 = 0;
    (void)r0;
    CHECK_EQ(WaitForSingleObject(ev, 20000), WAIT_OBJECT_0);
    uint64_t k = 0;
    while ((k + 1) * FNVNX_AUDIO_PERIOD * 2 <= 24000) k++;
    k++;                                                      /* the period whose byte span contains offset 24000 */
    CHECK_EQ(now_ns(), boundary(0, k));
    CHECK_EQ(WaitForSingleObject(ev, 20000), WAIT_OBJECT_0);
    k = 0;
    while ((k + 1) * FNVNX_AUDIO_PERIOD * 2 <= 48000) k++;
    k++;
    CHECK_EQ(now_ns(), boundary(0, k));
    b->Stop();
    n->Release();
    b->Release();
    ds->Release();
    CloseHandle(ev);
    audio_end();
}

/* the audio device's observations agree between two runs in one process (the clock re-initialised) */
TEST(vc_audio_determinism)
{
    if (!virt()) return;
    uint64_t first[4] = {0, 0, 0, 0};
    for (int run = 0; run < 6; run++) {
        audio_restart();
        WAVEFORMATEX w = pcm(44100, 2, 16);
        HANDLE ev = CreateEventA(nullptr, FALSE, FALSE, nullptr);
        void *h = nullptr;
        CHECK_EQ(waveOutOpen(&h, 0, &w, (uintptr_t)ev, 0, 0x50000), 0);
        fnvnx_waveout_service();
        WaitForSingleObject(ev, 0);
        static char data[4410 * 4];
        Hdr a;
        memset(&a, 0, sizeof a);
        a.lpData = data;
        a.len = sizeof data;
        waveOutPrepareHeader(h, &a, sizeof a);
        Sleep(13);
        waveOutWrite(h, &a, sizeof a);
        WaitForSingleObject(ev, 20000);
        uint64_t got[4] = {now_ns(), 0, 0, 0};
        Sleep(7);
        waveOutWrite(h, &a, sizeof a);
        WaitForSingleObject(ev, 20000);
        got[1] = now_ns();
        Mmt pos;
        memset(&pos, 0, sizeof pos);
        pos.type = 2;
        waveOutGetPosition(h, &pos, sizeof pos);
        got[2] = pos.u.v;
        got[3] = a.flags;
        if (run == 0) memcpy(first, got, sizeof first);
        else for (int i = 0; i < 4; i++) CHECK_EQ(got[i], first[i]);
        waveOutUnprepareHeader(h, &a, sizeof a);
        waveOutClose(h);
        CloseHandle(ev);
    }
    CHECK_EQ(first[0], 113000000);                            /* 13 ms + 100 ms (4410 frames at 44.1 kHz) */
    audio_end();
}
