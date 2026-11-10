/* test_audio.cpp - mixer math, DirectSound semantics (cursors, looping, Lock, notifications, volume/pan, 3D,
 * doppler, errors), slot contract through engine-style stand-in classes, module/COM registration. */
#include "s7_test.h"

#include <dsound.h>
#include "fnvnx_win32_ext.h"
#include "../../platform/audio/backend.h"
#include "../../platform/audio/ds3d.h"
#include "../../platform/audio/mixer.h"

#include <stdint.h>
#include <stdlib.h>
#include <vector>

extern "C" void *fnvnx_audio_input_lookup_export(const char *, const char *);

namespace {

IDirectSound8 *device()
{
    IDirectSound8 *ds = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, &ds, nullptr), DS_OK);
    CHECK_EQ(ds->SetCooperativeLevel(nullptr, DSSCL_PRIORITY), DS_OK);
    return ds;
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

IDirectSoundBuffer8 *buffer(IDirectSound8 *ds, DWORD flags, WAVEFORMATEX *w, DWORD bytes, HRESULT expect = DS_OK)
{
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = sizeof d;
    d.dwFlags = flags;
    d.dwBufferBytes = bytes;
    d.lpwfxFormat = w;
    IDirectSoundBuffer *b = nullptr;
    HRESULT hr = ds->CreateSoundBuffer(&d, &b, nullptr);
    CHECK_EQ(hr, expect);
    if (hr != DS_OK) return nullptr;
    IDirectSoundBuffer8 *b8 = nullptr;
    CHECK_EQ(b->QueryInterface(IID_IDirectSoundBuffer8, (void **)&b8), S_OK);
    b->Release();
    return b8;
}

void fill16(IDirectSoundBuffer8 *b, int16_t v)
{
    void *p1, *p2;
    DWORD n1, n2;
    CHECK_EQ(b->Lock(0, 0, &p1, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER), DS_OK);
    for (DWORD i = 0; i < n1 / 2; i++) ((int16_t *)p1)[i] = v;
    b->Unlock(p1, n1, p2, n2);
}

std::vector<float> render(unsigned frames)
{
    std::vector<float> out(frames * 2);
    mixer_render_f32(out.data(), frames);
    return out;
}

DWORD play_cursor(IDirectSoundBuffer8 *b)
{
    DWORD p = 0, w = 0;
    b->GetCurrentPosition(&p, &w);
    return p;
}

} // namespace

TEST(audio_volume_pan_math)
{
    CHECK_NEAR(mixer_mb_to_gain(0), 1.0, 1e-6);
    CHECK_NEAR(mixer_mb_to_gain(-600), 0.501187, 1e-5);   /* -6 dB */
    CHECK_NEAR(mixer_mb_to_gain(-2000), 0.1, 1e-6);
    CHECK_EQ(mixer_mb_to_gain(-10000), 0);
    float l, r;
    mixer_pan_to_gains(0, &l, &r);
    CHECK_NEAR(l, 1, 1e-6); CHECK_NEAR(r, 1, 1e-6);
    mixer_pan_to_gains(-10000, &l, &r);
    CHECK_NEAR(l, 1, 1e-6); CHECK_EQ(r, 0);
    mixer_pan_to_gains(600, &l, &r);
    CHECK_NEAR(l, 0.501187, 1e-5); CHECK_NEAR(r, 1, 1e-6);
}

TEST(audio_cursor_oneshot_and_loop)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *b = buffer(ds, DSBCAPS_CTRLVOLUME | DSBCAPS_GETCURRENTPOSITION2, &w, 2000); /* 1000 frames */
    fill16(b, 1000);
    CHECK_EQ(b->Play(0, 0, 0), DS_OK);
    render(100);
    CHECK_EQ(play_cursor(b), 200);
    DWORD st = 0;
    b->GetStatus(&st);
    CHECK_EQ(st, DSBSTATUS_PLAYING);
    render(1000);   /* passes the end: a one-shot stops and rewinds */
    b->GetStatus(&st);
    CHECK_EQ(st, 0);
    CHECK_EQ(play_cursor(b), 0);

    CHECK_EQ(b->Play(0, 0, DSBPLAY_LOOPING), DS_OK);
    render(1200);
    b->GetStatus(&st);
    CHECK_EQ(st, DSBSTATUS_PLAYING | DSBSTATUS_LOOPING);
    CHECK_EQ(play_cursor(b), 400);   /* 1200 mod 1000 frames */
    DWORD p, wr;
    b->GetCurrentPosition(&p, &wr);
    CHECK_EQ(wr, (400 + FNVNX_AUDIO_PERIOD * 2) % 2000);   /* one period of lead */
    CHECK_EQ(b->SetCurrentPosition(1001), DS_OK);           /* rounded down to a frame */
    CHECK_EQ(play_cursor(b), 1000);
    b->Stop();
    b->GetStatus(&st);
    CHECK_EQ(st, 0);
    b->Release();
    ds->Release();
}

TEST(audio_resampling_rate_and_frequency)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(24000, 1, 16);
    IDirectSoundBuffer8 *b = buffer(ds, DSBCAPS_CTRLFREQUENCY, &w, 4000);
    b->Play(0, 0, DSBPLAY_LOOPING);
    render(100);
    CHECK_EQ(play_cursor(b), 100);    /* 50 frames of a 24 kHz buffer in 100 output frames */
    CHECK_EQ(b->SetFrequency(96000), DS_OK);
    DWORD f;
    b->GetFrequency(&f);
    CHECK_EQ(f, 96000);
    render(100);
    CHECK_EQ(play_cursor(b), 100 + 400);
    CHECK_EQ(b->SetFrequency(DSBFREQUENCY_ORIGINAL), DS_OK);
    b->GetFrequency(&f);
    CHECK_EQ(f, 24000);
    CHECK_EQ(b->SetFrequency(50), DSERR_INVALIDPARAM);
    b->Release();
    ds->Release();
}

TEST(audio_mix_values_volume_pan_interpolation)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *b = buffer(ds, DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLPAN, &w, 960);
    fill16(b, 16384);
    b->Play(0, 0, DSBPLAY_LOOPING);
    std::vector<float> o = render(16);
    CHECK_NEAR(o[0], 0.5, 1e-4); CHECK_NEAR(o[1], 0.5, 1e-4);
    b->SetPan(DSBPAN_RIGHT);
    o = render(16);
    CHECK_EQ(o[0], 0); CHECK_NEAR(o[1], 0.5, 1e-4);
    b->SetPan(0);
    b->SetVolume(-600);
    o = render(16);
    CHECK_NEAR(o[2], 0.5 * 0.501187, 1e-4);
    LONG v;
    b->GetVolume(&v);
    CHECK_EQ(v, -600);
    CHECK_EQ(b->SetVolume(1), DSERR_INVALIDPARAM);
    b->Release();

    /* linear interpolation: 8-bit stereo ramp at half rate */
    WAVEFORMATEX w8 = pcm(24000, 2, 8);
    IDirectSoundBuffer8 *r = buffer(ds, 0, &w8, 8);
    void *p1, *p2;
    DWORD n1, n2;
    r->Lock(0, 8, &p1, &n1, &p2, &n2, 0);
    uint8_t ramp[8] = {128, 128, 192, 64, 128, 128, 192, 64};   /* L: 0, .5, 0, .5  R: 0, -.5, 0, -.5 */
    memcpy(p1, ramp, 8);
    r->Unlock(p1, n1, p2, n2);
    r->Play(0, 0, DSBPLAY_LOOPING);
    o = render(4);
    CHECK_NEAR(o[0], 0.0, 1e-6);
    CHECK_NEAR(o[2], 0.25, 1e-6);    /* halfway between frame 0 and 1 */
    CHECK_NEAR(o[3], -0.25, 1e-6);
    CHECK_NEAR(o[4], 0.5, 1e-6);
    r->Release();

    /* primary volume = master gain */
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = sizeof d;
    d.dwFlags = DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRLVOLUME;
    IDirectSoundBuffer *prim = nullptr;
    CHECK_EQ(ds->CreateSoundBuffer(&d, &prim, nullptr), DS_OK);
    IDirectSoundBuffer8 *b2 = buffer(ds, 0, &w, 960);
    fill16(b2, 16384);
    b2->Play(0, 0, DSBPLAY_LOOPING);
    prim->SetVolume(-2000);
    o = render(4);
    CHECK_NEAR(o[0], 0.05, 1e-4);
    prim->SetVolume(0);
    b2->Release();
    prim->Release();
    ds->Release();
}

TEST(audio_lock_regions)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(48000, 2, 16);
    IDirectSoundBuffer8 *b = buffer(ds, 0, &w, 2000);
    void *p1, *p2, *base;
    DWORD n1, n2;
    CHECK_EQ(b->Lock(0, 0, &base, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER), DS_OK);
    CHECK_EQ(n1, 2000); CHECK(p2 == nullptr); CHECK_EQ(n2, 0);
    b->Unlock(base, n1, p2, n2);
    CHECK_EQ(b->Lock(1800, 400, &p1, &n1, &p2, &n2, 0), DS_OK);
    CHECK(p1 == (uint8_t *)base + 1800); CHECK_EQ(n1, 200);
    CHECK(p2 == base); CHECK_EQ(n2, 200);
    b->Unlock(p1, n1, p2, n2);
    CHECK_EQ(b->Lock(2000, 4, &p1, &n1, &p2, &n2, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Lock(0, 2004, &p1, &n1, &p2, &n2, 0), DSERR_INVALIDPARAM);
    b->Play(0, 0, DSBPLAY_LOOPING);
    render(10);
    DWORD pc, wc;
    b->GetCurrentPosition(&pc, &wc);
    CHECK_EQ(b->Lock(0, 100, &p1, &n1, &p2, &n2, DSBLOCK_FROMWRITECURSOR), DS_OK);
    CHECK(p1 == (uint8_t *)base + wc);
    b->Release();
    ds->Release();
}

TEST(audio_notifications_signal_events)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *b = buffer(ds, DSBCAPS_CTRLPOSITIONNOTIFY, &w, 2000);
    IDirectSoundNotify *n = nullptr;
    CHECK_EQ(b->QueryInterface(IID_IDirectSoundNotify, (void **)&n), S_OK);
    HANDLE ev_half = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    HANDLE ev_zero = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    HANDLE ev_stop = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    DSBPOSITIONNOTIFY pos[3] = {{1000, ev_half}, {0, ev_zero}, {DSBPN_OFFSETSTOP, ev_stop}};
    CHECK_EQ(n->SetNotificationPositions(3, pos), DS_OK);
    DSBPOSITIONNOTIFY bad = {2000, ev_half};
    CHECK_EQ(n->SetNotificationPositions(1, &bad), DSERR_INVALIDPARAM);
    CHECK_EQ(n->SetNotificationPositions(3, pos), DS_OK);
    b->Play(0, 0, DSBPLAY_LOOPING);
    CHECK_EQ(n->SetNotificationPositions(3, pos), DSERR_INVALIDCALL);   /* only while stopped */
    render(400);
    CHECK_EQ(WaitForSingleObject(ev_zero, 0), WAIT_OBJECT_0);   /* cursor left 0 */
    CHECK_EQ(WaitForSingleObject(ev_half, 0), WAIT_TIMEOUT);
    render(200);
    CHECK_EQ(WaitForSingleObject(ev_half, 0), WAIT_OBJECT_0);   /* crossed byte 1000 (frame 500) */
    render(300);
    CHECK_EQ(WaitForSingleObject(ev_zero, 0), WAIT_TIMEOUT);
    render(200);                                                 /* wraps past frame 1000 -> 0 */
    CHECK_EQ(WaitForSingleObject(ev_zero, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(ev_stop, 0), WAIT_TIMEOUT);
    b->Stop();
    CHECK_EQ(WaitForSingleObject(ev_stop, 0), WAIT_OBJECT_0);
    /* a one-shot that reaches its end signals the remaining offsets and stop */
    b->SetCurrentPosition(0);
    b->Play(0, 0, 0);
    render(2000);
    CHECK_EQ(WaitForSingleObject(ev_half, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(ev_stop, 0), WAIT_OBJECT_0);
    n->Release();
    b->Release();
    CloseHandle(ev_half); CloseHandle(ev_zero); CloseHandle(ev_stop);
    ds->Release();
}

TEST(audio_create_errors_and_interfaces)
{
    IDirectSound8 *ds = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, &ds, nullptr), DS_OK);
    WAVEFORMATEX mono = pcm(22050, 1, 16), stereo = pcm(44100, 2, 16);
    buffer(ds, 0, &mono, 4000, DSERR_PRIOLEVELNEEDED);    /* no cooperative level yet */
    ds->SetCooperativeLevel(nullptr, DSSCL_PRIORITY);
    buffer(ds, DSBCAPS_CTRL3D, &stereo, 4000, DSERR_INVALIDPARAM);           /* 3D must be mono */
    buffer(ds, DSBCAPS_CTRL3D | DSBCAPS_CTRLPAN, &mono, 4000, DSERR_INVALIDPARAM);
    buffer(ds, DSBCAPS_CTRLFX, &mono, 4000, DSERR_BUFFERTOOSMALL);          /* < 150 ms */
    CHECK_EQ((uint32_t)DSERR_BUFFERTOOSMALL, 0x887800B4u);                   /* the engine tests this code */
    WAVEFORMATEX odd = mono;
    odd.nBlockAlign = 3;
    buffer(ds, 0, &odd, 4000, DSERR_BADFORMAT);

    IDirectSoundBuffer8 *b = buffer(ds, 0, &mono, 4000);
    CHECK_EQ(b->SetFrequency(22050), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(b->SetVolume(0), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(b->SetPan(0), DSERR_CONTROLUNAVAIL);
    void *x = nullptr;
    CHECK_EQ(b->QueryInterface(IID_IDirectSound3DBuffer, &x), E_NOINTERFACE);
    CHECK_EQ(b->QueryInterface(IID_IDirectSoundNotify, &x), E_NOINTERFACE);
    DSBCAPS caps;
    caps.dwSize = sizeof caps;
    CHECK_EQ(b->GetCaps(&caps), DS_OK);
    CHECK_EQ(caps.dwBufferBytes, 4000);
    CHECK(caps.dwFlags & DSBCAPS_LOCSOFTWARE);
    DWORD need = 0;
    CHECK_EQ(b->GetFormat(nullptr, 0, &need), DS_OK);
    CHECK_EQ(need, sizeof(WAVEFORMATEX));
    WAVEFORMATEX got;
    CHECK_EQ(b->GetFormat(&got, sizeof got, &need), DS_OK);
    CHECK_EQ(got.nSamplesPerSec, 22050);

    /* duplicates share the data */
    IDirectSoundBuffer *dup = nullptr;
    CHECK_EQ(ds->DuplicateSoundBuffer(b, &dup), DS_OK);
    void *p1, *p2, *q1, *q2;
    DWORD n1, n2, m1, m2;
    b->Lock(0, 0, &p1, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER);
    dup->Lock(0, 0, &q1, &m1, &q2, &m2, DSBLOCK_ENTIREBUFFER);
    CHECK(p1 == q1);
    b->Release();
    dup->Release();

    /* FX: a 3D + FX buffer long enough; parameters round-trip through GetObjectInPath */
    IDirectSoundBuffer8 *fx = buffer(ds, DSBCAPS_CTRLFX | DSBCAPS_CTRL3D, &mono, 22050 * 2);
    DSEFFECTDESC e;
    memset(&e, 0, sizeof e);
    e.dwSize = sizeof e;
    e.guidDSFXClass = GUID_DSFX_STANDARD_I3DL2REVERB;
    DWORD res = 99;
    CHECK_EQ(fx->SetFX(1, &e, &res), DS_OK);
    CHECK_EQ(res, DSFXR_LOCSOFTWARE);
    IDirectSoundFXI3DL2Reverb8 *rv = nullptr;
    CHECK_EQ(fx->GetObjectInPath(GUID_DSFX_STANDARD_I3DL2REVERB, 0, IID_IDirectSoundFXI3DL2Reverb8, (void **)&rv), DS_OK);
    DSFXI3DL2Reverb params;
    rv->GetAllParameters(&params);
    params.lRoom = -500;
    CHECK_EQ(rv->SetAllParameters(&params), DS_OK);
    DSFXI3DL2Reverb back;
    rv->GetAllParameters(&back);
    CHECK_EQ(back.lRoom, -500);
    rv->Release();
    CHECK_EQ(fx->GetObjectInPath(GUID_DSFX_STANDARD_I3DL2REVERB, 1, IID_IDirectSoundFXI3DL2Reverb8, (void **)&rv), DSERR_OBJECTNOTFOUND);
    fx->Play(0, 0, DSBPLAY_LOOPING);
    CHECK_EQ(fx->SetFX(0, nullptr, nullptr), DSERR_INVALIDCALL);
    fx->Release();

    DSCAPS dc;
    dc.dwSize = sizeof dc;
    CHECK_EQ(ds->GetCaps(&dc), DS_OK);
    CHECK_EQ(dc.dwMaxSecondarySampleRate, DSBFREQUENCY_MAX);
    ds->Release();
}

TEST(audio_3d_math)
{
    ds3d_listener l;
    ds3d_listener_default(&l);
    ds3d_source s;
    ds3d_source_default(&s);
    ds3d_result r;
    s.pos = {0, 0, 2};
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, 0.5, 1e-5); CHECK_NEAR(r.gain_r, 0.5, 1e-5);   /* -6 dB at twice min distance */
    s.pos = {0, 0, 0.5f};
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, 1.0, 1e-5);                                     /* inside min distance */
    s.pos = {5, 0, 0};
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, 0.0, 1e-5); CHECK_NEAR(r.gain_r, 0.2, 1e-5);   /* hard right */
    l.rolloff = 2.0f;
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_r, 1.0 / 9.0, 1e-5);
    l.rolloff = 1.0f;
    s.max_distance = 3.0f;
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_r, 1.0 / 3.0, 1e-5);                               /* clamped at max distance */
    ds3d_compute(&l, &s, 1, &r);
    CHECK_EQ(r.gain_r, 0);                                               /* MUTE3DATMAXDISTANCE */
    s.max_distance = 1e9f;
    /* listener turned: facing +x, the source at +x is in front -> centred */
    l.front = {1, 0, 0};
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, r.gain_r, 1e-5);
    l.front = {0, 0, 1};
    /* head-relative ignores the listener position */
    l.pos = {100, 0, 0};
    s.mode = DS3D_MODE_HEADRELATIVE;
    s.pos = {0, 0, 2};
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, 0.5, 1e-5);
    s.mode = DS3D_MODE_DISABLE;
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, 1.0, 1e-6);
    /* doppler: source approaching at c/10 -> f * c / (c - c/10) */
    ds3d_listener_default(&l);
    ds3d_source_default(&s);
    s.pos = {0, 0, 10};
    s.vel = {0, 0, -34.33f};
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.doppler, 1.0 / 0.9, 1e-4);
    l.doppler = 0.0f;
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.doppler, 1.0, 1e-6);
    /* cone: listener behind a 90/180 cone with -2000 mB outside */
    ds3d_listener_default(&l);
    ds3d_source_default(&s);
    s.pos = {0, 0, 1};
    s.cone_orient = {0, 0, 1};        /* pointing away from the listener */
    s.cone_inside = 90;
    s.cone_outside = 180;
    s.cone_outside_volume = -2000;
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, 0.1, 1e-4);
    s.cone_orient = {0, 0, -1};       /* pointing at the listener */
    ds3d_compute(&l, &s, 0, &r);
    CHECK_NEAR(r.gain_l, 1.0, 1e-5);
}

/* The engine calls DirectSound through its own classes whose methods are only placeholders in slot order
 * (e.g. FNVRenderInterface_00aea8b0 / FUN_00aea9d0_Listener for the listener, "Com" for the device). These
 * stand-ins mirror that: the call lands on the SDK method with the same slot index. */
namespace standin {
struct Com {   /* bsgamesound.cpp: q r1 r2 create caps r5 coop */
    virtual int q(const void *, void **);
    virtual int r1();
    virtual int r2();
    virtual int create(DSBUFFERDESC *, Com **, int);
    virtual int caps(void *);
    virtual int r5();
    virtual int coop(void *, int);
};
struct ListenerIface {   /* blk_00ae0000: Function00..Function30, ApplyParameters, ApplyColor, 3C, 40, Commit */
    virtual void f00(); virtual void f04(); virtual void f08(); virtual void f0c(); virtual void f10();
    virtual void f14(); virtual void f18(); virtual void f1c(); virtual void f20(); virtual void f24();
    virtual void f28(); virtual void f2c(); virtual void f30();
    virtual long ApplyParameters(float, float, float, float, float, float, DWORD);   /* slot 13 SetOrientation */
    virtual long ApplyColor(float, float, float, DWORD);                             /* slot 14 SetPosition */
    virtual long vf3C(float, DWORD);                                                 /* slot 15 SetRolloffFactor */
    virtual void f40();
    virtual long Commit();                                                           /* slot 17 Commit */
};
} // namespace standin

TEST(audio_engine_flow_through_standin_slots)
{
    /* BSWin32Audio::SetWindow, 0x00aea230: create, coop(2), caps(96), primary {36, 0x91}, QI listener */
    void *raw = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, (IDirectSound8 **)&raw, nullptr), DS_OK);
    standin::Com *com = (standin::Com *)raw;
    CHECK_EQ(com->coop(nullptr, 2), DS_OK);
    DSCAPS capsdata = {};   /* 96 bytes in the game (ILP32); larger on an LP64 host */
    capsdata.dwSize = sizeof(DSCAPS);
    CHECK_EQ(com->caps(&capsdata), DS_OK);
    DSBUFFERDESC desc;
    memset(&desc, 0, sizeof desc);
    desc.dwSize = sizeof desc;
    desc.dwFlags = 145;   /* PRIMARYBUFFER | CTRL3D | CTRLVOLUME */
    standin::Com *prim = nullptr;
    CHECK_EQ(com->create(&desc, &prim, 0), DS_OK);
    void *lis = nullptr;
    CHECK_EQ(prim->q(&IID_IDirectSound3DListener, &lis), DS_OK);
    standin::ListenerIface *li = (standin::ListenerIface *)lis;
    CHECK_EQ(li->vf3C(2.5f, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(li->ApplyColor(1, 2, 3, DS3D_DEFERRED), DS_OK);
    IDirectSound3DListener *real = (IDirectSound3DListener *)lis;
    float rf = 0;
    real->GetRolloffFactor(&rf);
    CHECK_NEAR(rf, 2.5, 1e-6);
    D3DVECTOR pos;
    real->GetPosition(&pos);
    CHECK_NEAR(pos.x, 0, 1e-6);           /* deferred: not applied yet */
    CHECK_EQ(li->Commit(), DS_OK);
    real->GetPosition(&pos);
    CHECK_NEAR(pos.x, 1, 1e-6); CHECK_NEAR(pos.z, 3, 1e-6);
    CHECK_EQ(li->ApplyParameters(1, 0, 0, 0, 1, 0, DS3D_IMMEDIATE), DS_OK);
    D3DVECTOR f, t;
    real->GetOrientation(&f, &t);
    CHECK_NEAR(f.x, 1, 1e-6); CHECK_NEAR(t.y, 1, 1e-6);

    /* a 3D buffer follows a deferred listener commit */
    IDirectSound8 *ds = (IDirectSound8 *)raw;
    real->SetAllParameters(nullptr, 0);   /* E_POINTER, ignored */
    real->SetOrientation(0, 0, 1, 0, 1, 0, DS3D_IMMEDIATE);
    real->SetPosition(0, 0, 0, DS3D_IMMEDIATE);
    real->SetRolloffFactor(1.0f, DS3D_IMMEDIATE);
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *b = buffer(ds, DSBCAPS_CTRL3D | DSBCAPS_CTRLVOLUME, &w, 960);
    fill16(b, 16384);
    IDirectSound3DBuffer *b3 = nullptr;
    CHECK_EQ(b->QueryInterface(IID_IDirectSound3DBuffer, (void **)&b3), S_OK);
    b3->SetMinDistance(1.0f, DS3D_IMMEDIATE);
    b3->SetPosition(0, 0, 4, DS3D_DEFERRED);
    b->Play(0, 0, DSBPLAY_LOOPING);
    std::vector<float> o = render(4);
    CHECK_NEAR(o[0], 0.5, 1e-4);           /* still at the origin */
    real->CommitDeferredSettings();
    o = render(4);
    CHECK_NEAR(o[0], 0.5 * 0.25, 1e-4);    /* 4 units away: 1/4 */
    b3->Release();
    b->Release();
    real->Release();
    prim->r2();   /* Release */
    ds->Release();
}

TEST(audio_registration_module_and_com)
{
    HMODULE h = LoadLibraryA("DSOUND.DLL");
    CHECK(h != nullptr);
    CHECK(GetProcAddress(h, "DirectSoundCreate8") == (FARPROC)(void *)&DirectSoundCreate8);
    CHECK(GetProcAddress(LoadLibraryA("xinput1_3.dll"), "XInputGetState") != nullptr);
    void *ds = nullptr;
    CHECK_EQ(CoCreateInstance(&CLSID_DirectSound8, nullptr, 1, &IID_IDirectSound8, &ds), S_OK);
    CHECK(ds != nullptr);
    if (ds) ((IDirectSound8 *)ds)->Release();
    CHECK(fnvnx_audio_input_lookup_export("dinput8", "DirectInput8Create") != nullptr);
    CHECK(fnvnx_audio_input_lookup_export("kernel32", "DirectInput8Create") == nullptr);
}
