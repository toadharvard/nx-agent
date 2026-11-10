/* test_dsound_contract.cpp - DirectSound 8 contract (DirectX 9 SDK / MSDN): argument validation and HRESULTs of
 * IDirectSound8, IDirectSoundBuffer8, IDirectSoundNotify, IDirectSound3D*, IKsPropertySet and the FX objects on the
 * decided device (one primary output, software buffers only, no EAX, no capture). Mixer driven by mixer_render_f32. */
#include "s7_test.h"

#include <dsound.h>
#include "fnvnx_win32_ext.h"
#include "../../platform/audio/backend.h"
#include "../../platform/audio/mixer.h"

#include <stddef.h>
#include <stdint.h>
#include <vector>

namespace {

#define E_PROP_ID_UNSUPPORTED_ ((HRESULT)0x80070490)
#define DS_UNCERTIFIED_ 1
#define G(name, l, w1, w2, ...) const GUID name = {l, w1, w2, {__VA_ARGS__}}
G(IID_Ks, 0x31efac30, 0x515c, 0x11d0, 0xa9, 0xaa, 0x00, 0xaa, 0x00, 0x61, 0xbe, 0x93);
G(GUID_Echo, 0xef3e932c, 0xd40b, 0x4f51, 0x8c, 0xcf, 0x3f, 0x98, 0xf1, 0xb2, 0x9d, 0x5d);
G(IID_Echo, 0x8bd28edf, 0x50db, 0x4e92, 0xa2, 0xbd, 0x44, 0x54, 0x88, 0xd1, 0xed, 0x42);
G(GUID_Gargle, 0xdafd8210, 0x5711, 0x4b91, 0x9f, 0xe3, 0xf7, 0x5b, 0x7a, 0xe2, 0x79, 0xbf);
G(IID_Gargle, 0xd616f352, 0xd622, 0x11ce, 0xaa, 0xc5, 0x00, 0x20, 0xaf, 0x0b, 0x99, 0xa3);
G(IID_IUnk, 0, 0, 0, 0xc0, 0, 0, 0, 0, 0, 0, 0x46);
G(GUID_Bogus, 0x12345678, 0x1111, 0x2222, 1, 2, 3, 4, 5, 6, 7, 8);
G(HRTF_FULL, 0xc2413340, 0x1c1b, 0x11d2, 0x94, 0xf5, 0x00, 0xc0, 0x4f, 0xc2, 0x8a, 0xca);

struct Ks : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE Get(REFGUID, ULONG, LPVOID, ULONG, LPVOID, ULONG, PULONG) = 0;
    virtual HRESULT STDMETHODCALLTYPE Set(REFGUID, ULONG, LPVOID, ULONG, LPVOID, ULONG) = 0;
    virtual HRESULT STDMETHODCALLTYPE QuerySupport(REFGUID, ULONG, PULONG) = 0;
};
struct EchoParams { float wet, fb, ldelay, rdelay; LONG pan; };
struct IEcho : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE SetAllParameters(const EchoParams *) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetAllParameters(EchoParams *) = 0;
};
struct GargleParams { DWORD rate, shape; };
struct IGargle : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE SetAllParameters(const GargleParams *) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetAllParameters(GargleParams *) = 0;
};

IDirectSound8 *device(DWORD level = DSSCL_PRIORITY, HWND w = nullptr)
{
    IDirectSound8 *ds = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, &ds, nullptr), DS_OK);
    CHECK_EQ(ds->SetCooperativeLevel(w, level), DS_OK);
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
HRESULT create(IDirectSound8 *ds, DWORD flags, WAVEFORMATEX *w, DWORD bytes, IDirectSoundBuffer **out, const GUID *alg = nullptr)
{
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = sizeof d;
    d.dwFlags = flags;
    d.dwBufferBytes = bytes;
    d.lpwfxFormat = w;
    if (alg) d.guid3DAlgorithm = *alg;
    if (out) *out = nullptr;
    return ds->CreateSoundBuffer(&d, out, nullptr);
}
IDirectSoundBuffer8 *buf(IDirectSound8 *ds, DWORD flags, WAVEFORMATEX *w, DWORD bytes)
{
    IDirectSoundBuffer *b = nullptr;
    IDirectSoundBuffer8 *b8 = nullptr;
    CHECK(create(ds, flags, w, bytes, &b) >= 0);
    if (b) { CHECK_EQ(b->QueryInterface(IID_IDirectSoundBuffer8, (void **)&b8), S_OK); b->Release(); }
    return b8;
}
std::vector<float> render(unsigned frames)
{
    std::vector<float> out(frames * 2);
    mixer_render_f32(out.data(), frames);
    return out;
}
float peak(const std::vector<float> &v)
{
    float m = 0;
    for (float f : v) m = fabsf(f) > m ? fabsf(f) : m;
    return m;
}
void fill(IDirectSoundBuffer *b, int16_t v)
{
    void *p1, *p2;
    DWORD n1, n2;
    CHECK_EQ(b->Lock(0, 0, &p1, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER), DS_OK);
    for (DWORD i = 0; i < n1 / 2; i++) ((int16_t *)p1)[i] = v;
    CHECK_EQ(b->Unlock(p1, n1, p2, n2), DS_OK);
}

} // namespace

TEST(dsc_device_contract)
{
    IDirectSound8 *ds = nullptr;
    GUID bogus = GUID_Bogus;
    CHECK_EQ(DirectSoundCreate8(&bogus, &ds, nullptr), DSERR_NODRIVER);
    CHECK(ds == nullptr);
    CHECK_EQ(DirectSoundCreate8(&DSDEVID_DefaultPlayback, &ds, nullptr), DS_OK);
    CHECK_EQ(ds->Initialize(nullptr), DSERR_ALREADYINITIALIZED);
    IDirectSound *old = (IDirectSound *)0x1;
    CHECK_EQ(DirectSoundCreate(&bogus, &old, nullptr), DSERR_NODRIVER);
    CHECK(old == nullptr);
    CHECK_EQ(ds->Compact(), DSERR_PRIOLEVELNEEDED);   /* no cooperative level yet */
    CHECK_EQ(ds->SetCooperativeLevel(nullptr, DSSCL_NORMAL), DS_OK);
    CHECK_EQ(ds->Compact(), DSERR_PRIOLEVELNEEDED);
    CHECK_EQ(ds->SetCooperativeLevel(nullptr, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(ds->SetCooperativeLevel(nullptr, 5), DSERR_INVALIDPARAM);
    CHECK_EQ(ds->SetCooperativeLevel((HWND)0x1234, DSSCL_PRIORITY), DSERR_INVALIDPARAM);   /* not a window */
    CHECK_EQ(ds->SetCooperativeLevel(nullptr, DSSCL_PRIORITY), DS_OK);
    CHECK_EQ(ds->Compact(), DS_OK);

    DSCAPS c;
    memset(&c, 0, sizeof c);
    c.dwSize = sizeof c - 4;
    CHECK_EQ(ds->GetCaps(&c), DSERR_INVALIDPARAM);
    CHECK_EQ(ds->GetCaps(nullptr), DSERR_INVALIDPARAM);
    c.dwSize = sizeof c;
    CHECK_EQ(ds->GetCaps(&c), DS_OK);
    CHECK(c.dwFlags & DSCAPS_EMULDRIVER);                      /* software device */
    CHECK(!(c.dwFlags & DSCAPS_CERTIFIED));
    CHECK_EQ(c.dwMinSecondarySampleRate, DSBFREQUENCY_MIN);
    CHECK_EQ(c.dwMaxSecondarySampleRate, DSBFREQUENCY_MAX);
    CHECK_EQ(c.dwPrimaryBuffers, 1);
    CHECK_EQ(c.dwMaxHwMixingAllBuffers + c.dwMaxHw3DAllBuffers + c.dwTotalHwMemBytes + c.dwFreeHwMemBytes, 0);

    DWORD v = 99, sp = 0;
    CHECK_EQ(ds->VerifyCertification(&v), DS_OK);
    CHECK_EQ(v, DS_UNCERTIFIED_);
    CHECK_EQ(ds->VerifyCertification(nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(ds->SetSpeakerConfig(0x100), DSERR_INVALIDPARAM);   /* unknown configuration */
    CHECK_EQ(ds->SetSpeakerConfig(8), DSERR_INVALIDPARAM);
    CHECK_EQ(ds->SetSpeakerConfig(DSSPEAKER_COMBINED(DSSPEAKER_5POINT1, 0)), DS_OK);
    CHECK_EQ(ds->GetSpeakerConfig(&sp), DS_OK);
    CHECK_EQ(sp, DSSPEAKER_COMBINED(DSSPEAKER_5POINT1, 0));
    CHECK_EQ(ds->GetSpeakerConfig(nullptr), DSERR_INVALIDPARAM);
    ds->Release();
}

TEST(dsc_create_buffer_validation)
{
    IDirectSound8 *ds = device();
    IDirectSoundBuffer *b = nullptr;
    WAVEFORMATEX m = pcm(22050, 1, 16), st = pcm(44100, 2, 16);
    CHECK_EQ(create(ds, 0, &m, 4000, &b), DS_OK); b->Release();
    CHECK_EQ(create(ds, 0x1000, &m, 4000, &b), DSERR_INVALIDPARAM);                 /* unknown flag bit */
    CHECK_EQ(create(ds, DSBCAPS_MUTE3DATMAXDISTANCE, &m, 4000, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_CTRL3D | DSBCAPS_CTRLPAN, &m, 4000, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_CTRL3D, &st, 4000, &b), DSERR_INVALIDPARAM);        /* 3D needs mono */
    CHECK_EQ(create(ds, DSBCAPS_LOCHARDWARE | DSBCAPS_LOCSOFTWARE, &m, 4000, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_LOCHARDWARE, &m, 4000, &b), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(create(ds, 0, nullptr, 4000, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, 0, &m, 3, &b), DSERR_INVALIDPARAM);                         /* below DSBSIZE_MIN */
    CHECK_EQ(create(ds, 0, &m, 0x10000000, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, 0, &m, 4000, nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_CTRLFX, &m, 100, &b), DSERR_BUFFERTOOSMALL);
    /* primary rules */
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER, &m, 0, &b), DSERR_INVALIDPARAM);     /* format must be NULL */
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER, nullptr, 4000, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER | DSBCAPS_STATIC, nullptr, 0, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER | DSBCAPS_LOCDEFER, nullptr, 0, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER | DSBCAPS_MUTE3DATMAXDISTANCE, nullptr, 0, &b), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRLVOLUME, nullptr, 0, &b), DS_OK);
    if (b) b->Release();
    /* formats */
    WAVEFORMATEX f = pcm(100, 1, 8);
    CHECK_EQ(create(ds, 0, &f, 400, &b), DS_OK); if (b) b->Release();
    f = pcm(200000, 2, 16);
    CHECK_EQ(create(ds, 0, &f, 4000, &b), DS_OK); if (b) b->Release();
    f = pcm(99, 1, 16);
    CHECK_EQ(create(ds, 0, &f, 400, &b), DSERR_BADFORMAT);
    f = pcm(200001, 1, 16);
    CHECK_EQ(create(ds, 0, &f, 400, &b), DSERR_BADFORMAT);
    f = pcm(22050, 1, 16); f.nAvgBytesPerSec += 1;
    CHECK_EQ(create(ds, 0, &f, 400, &b), DSERR_BADFORMAT);
    f = pcm(22050, 1, 16); f.nBlockAlign = 4;
    CHECK_EQ(create(ds, 0, &f, 400, &b), DSERR_BADFORMAT);
    f = pcm(22050, 1, 16); f.wFormatTag = 2; /* ADPCM */
    CHECK_EQ(create(ds, 0, &f, 400, &b), DSERR_BADFORMAT);
    f = pcm(22050, 0, 16);
    CHECK_EQ(create(ds, 0, &f, 400, &b), DSERR_BADFORMAT);
    /* WAVEFORMATEXTENSIBLE as bytes: format, wValidBits, dwChannelMask, SubFormat (offsets from sizeof(WAVEFORMATEX)) */
    const size_t B = sizeof(WAVEFORMATEX);
    uint8_t ex_bytes[96] = {0};
    WAVEFORMATEX &exw = *(WAVEFORMATEX *)ex_bytes;
    exw = pcm(48000, 2, 16);
    exw.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
    exw.cbSize = 22;
    const uint8_t sub_pcm[16] = {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71};
    ex_bytes[B] = 16; ex_bytes[B + 2] = 3;
    memcpy(ex_bytes + B + 6, sub_pcm, 16);
    CHECK_EQ(create(ds, 0, &exw, 4000, &b), DS_OK);
    if (b) {
        IDirectSoundBuffer8 *b8 = nullptr;
        b->QueryInterface(IID_IDirectSoundBuffer8, (void **)&b8);
        DWORD n = 0;
        CHECK_EQ(b8->GetFormat(nullptr, 0, &n), DS_OK);
        CHECK_EQ(n, B + 22);
        b8->Release();
        b->Release();
    }
    ex_bytes[B + 6 + 4] = 7;   /* not a KSDATAFORMAT subtype */
    CHECK_EQ(create(ds, 0, &exw, 4000, &b), DSERR_BADFORMAT);
    f = pcm(44100, 2, 32); f.wFormatTag = WAVE_FORMAT_IEEE_FLOAT;
    HRESULT hr = create(ds, 0, &f, 4000, &b);
    CHECK(hr == DS_OK || hr == DSERR_BADFORMAT);
    if (b) b->Release();
    /* 3D algorithm */
    GUID bogus = GUID_Bogus, hrtf = HRTF_FULL;
    CHECK_EQ(create(ds, DSBCAPS_CTRL3D, &m, 4000, &b, &bogus), DSERR_INVALIDPARAM);
    CHECK_EQ(create(ds, 0, &m, 4000, &b, &hrtf), DSERR_INVALIDPARAM);              /* algorithm needs CTRL3D */
    CHECK_EQ(create(ds, DSBCAPS_CTRL3D, &m, 4000, &b, &hrtf), DS_NO_VIRTUALIZATION);   /* no HRTF: default algorithm */
    CHECK(b != nullptr);
    if (b) b->Release();
    /* descriptor size */
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = offsetof(DSBUFFERDESC, guid3DAlgorithm) + 4; d.lpwfxFormat = &m; d.dwBufferBytes = 4000;
    CHECK_EQ(ds->CreateSoundBuffer(&d, &b, nullptr), DSERR_INVALIDPARAM);
    d.dwSize = offsetof(DSBUFFERDESC, guid3DAlgorithm);
    CHECK_EQ(ds->CreateSoundBuffer(&d, &b, nullptr), DS_OK); if (b) b->Release();
    ds->Release();
}

TEST(dsc_duplicate_buffer)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX m = pcm(22050, 1, 16);
    IDirectSoundBuffer8 *b = buf(ds, DSBCAPS_CTRLVOLUME, &m, 4000);
    IDirectSoundBuffer *dup = nullptr, *prim = nullptr;
    CHECK_EQ(ds->DuplicateSoundBuffer(b, &dup), DS_OK);
    void *p1, *p2, *q1, *q2;
    DWORD n1, n2, m1, m2;
    CHECK_EQ(b->Lock(0, 4, &p1, &n1, &p2, &n2, 0), DS_OK);
    CHECK_EQ(dup->Lock(0, 4, &q1, &m1, &q2, &m2, 0), DS_OK);
    CHECK(p1 == q1);   /* the sample memory is shared */
    b->Unlock(p1, n1, p2, n2);
    dup->Unlock(q1, m1, q2, m2);
    IDirectSoundBuffer *none = nullptr;
    CHECK_EQ(ds->DuplicateSoundBuffer(nullptr, &none), DSERR_INVALIDPARAM);
    char fake[64] = {0};
    IDirectSoundBuffer *foreign = nullptr;
    CHECK_EQ(ds->DuplicateSoundBuffer((IDirectSoundBuffer *)fake, &foreign), DSERR_INVALIDPARAM);
    CHECK(foreign == nullptr);
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER, nullptr, 0, &prim), DS_OK);
    IDirectSoundBuffer *pd = nullptr;
    CHECK_EQ(ds->DuplicateSoundBuffer(prim, &pd), DSERR_INVALIDCALL);
    prim->Release();
    dup->Release();
    b->Release();
    ds->Release();
}

TEST(dsc_lock_unlock)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX m = pcm(22050, 1, 16);
    IDirectSoundBuffer8 *b = buf(ds, 0, &m, 2000);
    void *p1 = nullptr, *p2 = nullptr, *base;
    DWORD n1, n2;
    CHECK_EQ(b->Lock(0, 0, &base, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER), DS_OK);
    CHECK_EQ(n1, 2000); CHECK_EQ(n2, 0); CHECK(p2 == nullptr);
    CHECK_EQ(b->Unlock(base, n1, p2, n2), DS_OK);
    CHECK_EQ(b->Lock(1800, 400, &p1, &n1, &p2, &n2, 0), DS_OK);   /* wraps into two pointers */
    CHECK(p1 == (char *)base + 1800 && p2 == base);
    CHECK_EQ(n1, 200); CHECK_EQ(n2, 200);
    CHECK_EQ(b->Unlock(p1, 100, p2, 50), DS_OK);                  /* fewer bytes written than locked */
    CHECK_EQ(b->Unlock(p1, 100, p2, 50), DSERR_INVALIDPARAM);     /* no longer locked */
    CHECK_EQ(b->Lock(1800, 400, &p1, &n1, nullptr, nullptr, 0), DS_OK);   /* no second pointer: not wrapped */
    CHECK_EQ(n1, 200);
    CHECK_EQ(b->Unlock(p1, n1, nullptr, 0), DS_OK);
    CHECK_EQ(b->Lock(1800, 400, &p1, &n1, &p2, nullptr, 0), DSERR_INVALIDPARAM);   /* one of p2/b2 only */
    CHECK_EQ(b->Lock(1800, 400, &p1, &n1, nullptr, &n2, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Lock(0, 100, nullptr, &n1, &p2, &n2, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Lock(0, 100, &p1, nullptr, &p2, &n2, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Lock(0, 100, &p1, &n1, &p2, &n2, 0x8), DSERR_INVALIDPARAM);       /* unknown lock flag */
    CHECK_EQ(b->Lock(0, 2001, &p1, &n1, &p2, &n2, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Lock(2000, 4, &p1, &n1, &p2, &n2, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Lock(0, 100, &p1, &n1, &p2, &n2, 0), DS_OK);
    CHECK_EQ(b->Unlock((char *)p1 + 2, n1, p2, n2), DSERR_INVALIDPARAM);          /* not the locked pointer */
    CHECK_EQ(b->Unlock(p1, n1 + 2, p2, n2), DSERR_INVALIDPARAM);                  /* more than locked */
    CHECK_EQ(b->Unlock(nullptr, 0, nullptr, 0), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Unlock(p1, n1, p2, n2), DS_OK);
    /* primary buffer: only under DSSCL_WRITEPRIMARY */
    IDirectSoundBuffer *pr = nullptr;
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER, nullptr, 0, &pr), DS_OK);
    CHECK_EQ(pr->Lock(0, 0, &p1, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER), DSERR_PRIOLEVELNEEDED);
    pr->Release();
    b->Release();
    ds->Release();
}

TEST(dsc_play_status_position)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *b = buf(ds, DSBCAPS_GETCURRENTPOSITION2, &w, 2000);
    IDirectSoundBuffer8 *d = buf(ds, DSBCAPS_LOCDEFER, &w, 2000);
    CHECK_EQ(b->Play(1, 0, 0), DSERR_INVALIDPARAM);                       /* dwReserved1 */
    CHECK_EQ(b->Play(0, 0, 0x100), DSERR_INVALIDPARAM);                   /* unknown flag */
    CHECK_EQ(b->Play(0, 5, 0), DSERR_INVALIDPARAM);                       /* priority needs LOCDEFER */
    CHECK_EQ(b->Play(0, 0, DSBPLAY_LOCSOFTWARE), DSERR_INVALIDPARAM);     /* voice management needs LOCDEFER */
    CHECK_EQ(b->Play(0, 0, DSBPLAY_TERMINATEBY_TIME), DSERR_INVALIDPARAM);
    CHECK_EQ(d->Play(0, 0, DSBPLAY_LOCHARDWARE | DSBPLAY_LOCSOFTWARE), DSERR_INVALIDPARAM);
    CHECK_EQ(d->Play(0, 0, DSBPLAY_LOCHARDWARE), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(d->Play(0, 7, DSBPLAY_LOCSOFTWARE | DSBPLAY_TERMINATEBY_PRIORITY | DSBPLAY_LOOPING), DS_OK);
    DWORD st = 0;
    d->GetStatus(&st);
    CHECK_EQ(st, DSBSTATUS_PLAYING | DSBSTATUS_LOOPING | DSBSTATUS_LOCSOFTWARE);
    CHECK_EQ(d->GetStatus(nullptr), DSERR_INVALIDPARAM);
    d->Stop();
    DWORD p = 7, wc = 7;
    CHECK_EQ(b->SetCurrentPosition(7), DS_OK);                            /* aligned down to the block */
    b->GetCurrentPosition(&p, &wc);
    CHECK_EQ(p, 6); CHECK_EQ(wc, 6);                                      /* stopped: write cursor == play cursor */
    CHECK_EQ(b->SetCurrentPosition(2000), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Play(0, 0, DSBPLAY_LOOPING), DS_OK);
    render(300);
    b->GetCurrentPosition(&p, &wc);
    CHECK_EQ(p, 606);
    CHECK(wc != p);                                                       /* write cursor leads while playing */
    CHECK_EQ(b->GetCurrentPosition(nullptr, nullptr), DS_OK);
    DSBCAPS c;
    memset(&c, 0, sizeof c);
    c.dwSize = sizeof c - 4;
    CHECK_EQ(b->GetCaps(&c), DSERR_INVALIDPARAM);
    c.dwSize = sizeof c;
    CHECK_EQ(b->GetCaps(&c), DS_OK);
    CHECK_EQ(c.dwBufferBytes, 2000);
    CHECK(c.dwFlags & DSBCAPS_LOCSOFTWARE);
    CHECK_EQ(b->Restore(), DS_OK);
    b->Release(); d->Release();
    ds->Release();
}

TEST(dsc_controls_and_format)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(22050, 1, 16);
    IDirectSoundBuffer8 *none = buf(ds, 0, &w, 2000);
    IDirectSoundBuffer8 *all = buf(ds, DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLPAN | DSBCAPS_CTRLFREQUENCY, &w, 2000);
    LONG l = 0; DWORD f = 0;
    CHECK_EQ(none->SetVolume(0), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(none->GetVolume(&l), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(none->SetPan(0), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(none->SetFrequency(8000), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(all->SetVolume(-10001), DSERR_INVALIDPARAM);
    CHECK_EQ(all->SetVolume(1), DSERR_INVALIDPARAM);
    CHECK_EQ(all->SetVolume(-10000), DS_OK);
    CHECK_EQ(all->GetVolume(&l), DS_OK); CHECK_EQ(l, -10000);
    CHECK_EQ(all->GetVolume(nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(all->SetPan(10001), DSERR_INVALIDPARAM);
    CHECK_EQ(all->SetPan(-10000), DS_OK);
    CHECK_EQ(all->GetPan(&l), DS_OK); CHECK_EQ(l, -10000);
    CHECK_EQ(all->SetFrequency(99), DSERR_INVALIDPARAM);
    CHECK_EQ(all->SetFrequency(200001), DSERR_INVALIDPARAM);
    CHECK_EQ(all->SetFrequency(44100), DS_OK);
    CHECK_EQ(all->GetFrequency(&f), DS_OK); CHECK_EQ(f, 44100);
    CHECK_EQ(all->SetFrequency(DSBFREQUENCY_ORIGINAL), DS_OK);
    CHECK_EQ(all->GetFrequency(&f), DS_OK); CHECK_EQ(f, 22050);
    /* GetFormat buffer protocol */
    WAVEFORMATEX o;
    DWORD n = 0;
    CHECK_EQ(all->GetFormat(nullptr, 0, nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(all->GetFormat(nullptr, 0, &n), DS_OK); CHECK_EQ(n, sizeof(WAVEFORMATEX));
    CHECK_EQ(all->GetFormat(&o, 4, &n), DSERR_INVALIDPARAM);              /* too small: truncated copy is not success */
    CHECK_EQ(all->GetFormat(&o, sizeof o, &n), DS_OK);
    CHECK(o.nSamplesPerSec == 22050 && o.nChannels == 1);
    CHECK_EQ(all->SetFormat(&w), DSERR_INVALIDCALL);                      /* secondary buffers */
    /* primary: SetFormat needs DSSCL_PRIORITY, keeps what was set */
    IDirectSoundBuffer *pr = nullptr;
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLPAN, nullptr, 0, &pr), DS_OK);
    WAVEFORMATEX s = pcm(44100, 1, 8), bad = pcm(44100, 2, 16);
    bad.nAvgBytesPerSec = 5;
    CHECK_EQ(pr->SetFormat(nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(pr->SetFormat(&bad), DSERR_BADFORMAT);
    CHECK_EQ(pr->SetFormat(&s), DS_OK);
    CHECK_EQ(pr->GetFormat(&o, sizeof o, &n), DS_OK);
    CHECK(o.nSamplesPerSec == 44100 && o.nChannels == 1 && o.wBitsPerSample == 8);
    WAVEFORMATEX fl = pcm(44100, 2, 32); fl.wFormatTag = WAVE_FORMAT_IEEE_FLOAT;
    CHECK_EQ(pr->SetFormat(&fl), DSERR_BADFORMAT);                        /* primary formats are PCM */
    pr->Release();
    IDirectSound8 *d2 = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, &d2, nullptr), DS_OK);
    d2->SetCooperativeLevel(nullptr, DSSCL_NORMAL);
    CHECK_EQ(create(d2, DSBCAPS_PRIMARYBUFFER, nullptr, 0, &pr), DS_OK);
    CHECK_EQ(pr->SetFormat(&s), DSERR_PRIOLEVELNEEDED);
    pr->Release(); d2->Release();
    none->Release(); all->Release(); ds->Release();
}

TEST(dsc_write_primary)
{
    IDirectSound8 *ds = device(DSSCL_WRITEPRIMARY);
    IDirectSoundBuffer *pr = nullptr;
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER, nullptr, 0, &pr), DS_OK);
    WAVEFORMATEX s = pcm(48000, 2, 16);
    CHECK_EQ(pr->SetFormat(&s), DS_OK);
    void *p1 = nullptr, *p2 = nullptr;
    DWORD n1 = 0, n2 = 0, p = 0, w = 0;
    CHECK_EQ(pr->Lock(0, 0, &p1, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER), DS_OK);
    CHECK(n1 >= 4096 && p1);
    DSBCAPS c;
    memset(&c, 0, sizeof c); c.dwSize = sizeof c;
    CHECK_EQ(pr->GetCaps(&c), DS_OK);
    CHECK_EQ(c.dwBufferBytes, n1);
    CHECK(c.dwFlags & DSBCAPS_PRIMARYBUFFER);
    for (DWORD i = 0; p1 && i < n1 / 2; i++) ((int16_t *)p1)[i] = 8000;
    CHECK_EQ(pr->Unlock(p1, n1, p2, n2), DS_OK);
    CHECK_EQ(pr->Play(0, 0, 0), DSERR_INVALIDPARAM);                      /* primary must loop */
    CHECK_EQ(peak(render(256)), 0);                                       /* not playing yet */
    CHECK_EQ(pr->Play(0, 0, DSBPLAY_LOOPING), DS_OK);
    std::vector<float> o = render(256);
    CHECK_NEAR(peak(o), 8000 / 32768.0, 0.01);                            /* the application's data is what is played */
    pr->GetCurrentPosition(&p, &w);
    CHECK_EQ(p, 256 * 4);
    DWORD st = 0;
    pr->GetStatus(&st);
    CHECK_EQ(st, DSBSTATUS_PLAYING | DSBSTATUS_LOOPING);
    pr->Stop();
    CHECK_EQ(peak(render(256)), 0);
    CHECK_EQ(ds->SetCooperativeLevel(nullptr, DSSCL_PRIORITY), DS_OK);    /* leaving WRITEPRIMARY drops the data */
    CHECK_EQ(pr->Lock(0, 0, &p1, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER), DSERR_PRIOLEVELNEEDED);
    pr->Release();
    ds->Release();
}

TEST(dsc_focus_rule)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = DefWindowProcA;
    wc.lpszClassName = "dsc_focus";
    RegisterClassA(&wc);
    HWND a = CreateWindowExA(0, "dsc_focus", "a", 0x10000000, 0, 0, 50, 50, nullptr, nullptr, nullptr, nullptr);
    HWND b = CreateWindowExA(0, "dsc_focus", "b", 0x10000000, 0, 0, 50, 50, nullptr, nullptr, nullptr, nullptr);
    CHECK(a && b);
    IDirectSound8 *ds = device(DSSCL_PRIORITY, a);
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *loc = buf(ds, 0, &w, 4000), *glob = buf(ds, DSBCAPS_GLOBALFOCUS, &w, 4000);
    IDirectSoundBuffer8 *sticky = buf(ds, DSBCAPS_STICKYFOCUS, &w, 4000);
    fill(loc, 8000); fill(glob, 8000); fill(sticky, 8000);
    SetForegroundWindow(b);   /* another window of the process: the cooperative-level window is not active */
    loc->Play(0, 0, DSBPLAY_LOOPING);
    CHECK_NEAR(peak(render(64)), 0, 1e-6);
    glob->Play(0, 0, DSBPLAY_LOOPING);
    CHECK_NEAR(peak(render(64)), 8000 / 32768.0, 0.01);
    glob->Stop();
    sticky->Play(0, 0, DSBPLAY_LOOPING);
    CHECK_NEAR(peak(render(64)), 8000 / 32768.0, 0.01);   /* sticky focus is lost only to another DirectSound application */
    sticky->Stop();
    SetForegroundWindow(a);
    DWORD st;
    loc->GetStatus(&st);                      /* any buffer call re-evaluates the focus */
    CHECK_NEAR(peak(render(64)), 8000 / 32768.0, 0.01);
    loc->Release(); glob->Release(); sticky->Release();
    ds->Release();
    DestroyWindow(a); DestroyWindow(b);
}

TEST(dsc_notify_contract)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *b = buf(ds, DSBCAPS_CTRLPOSITIONNOTIFY, &w, 2000);
    IDirectSoundNotify *n = nullptr;
    CHECK_EQ(b->QueryInterface(IID_IDirectSoundNotify, (void **)&n), S_OK);
    HANDLE e1 = CreateEventA(nullptr, FALSE, FALSE, nullptr), e2 = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    DSBPOSITIONNOTIFY unsorted[3] = {{1500, e2}, {500, e1}, {DSBPN_OFFSETSTOP, e2}};
    DSBPOSITIONNOTIFY noev = {100, nullptr};
    CHECK_EQ(n->SetNotificationPositions(1, nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(n->SetNotificationPositions(1, &noev), DSERR_INVALIDPARAM);          /* event handle required */
    CHECK_EQ(n->SetNotificationPositions(100001, unsorted), DSERR_INVALIDPARAM);  /* DSBNOTIFICATIONS_MAX */
    CHECK_EQ(n->SetNotificationPositions(3, unsorted), DS_OK);                    /* the device sorts the offsets */
    b->Play(0, 0, DSBPLAY_LOOPING);
    render(300);                                                                  /* byte 600 passed offset 500 */
    CHECK_EQ(WaitForSingleObject(e1, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(e2, 0), WAIT_TIMEOUT);
    render(500);                                                                  /* byte 1600 passed offset 1500 */
    CHECK_EQ(WaitForSingleObject(e2, 0), WAIT_OBJECT_0);
    b->Stop();
    CHECK_EQ(WaitForSingleObject(e2, 0), WAIT_OBJECT_0);                          /* DSBPN_OFFSETSTOP */
    CHECK_EQ(n->SetNotificationPositions(0, nullptr), DS_OK);                     /* clears */
    b->Play(0, 0, DSBPLAY_LOOPING);
    render(1200);
    CHECK_EQ(WaitForSingleObject(e1, 0), WAIT_TIMEOUT);
    b->Stop();
    n->Release(); b->Release(); ds->Release();
    CloseHandle(e1); CloseHandle(e2);
}

TEST(dsc_3d_buffer_contract)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(22050, 1, 16);
    IDirectSoundBuffer8 *b = buf(ds, DSBCAPS_CTRL3D, &w, 4000);
    IDirectSound3DBuffer *t = nullptr;
    CHECK_EQ(b->QueryInterface(IID_IDirectSound3DBuffer, (void **)&t), S_OK);
    D3DVECTOR v;
    DWORD a1 = 0, a2 = 0, mode = 9;
    LONG vol = 0;
    float f = 0;
    CHECK_EQ(t->SetPosition(1, 2, 3, DS3D_DEFERRED), DS_OK);
    CHECK_EQ(t->SetVelocity(4, 5, 6, DS3D_IMMEDIATE), DS_OK);   /* must not commit the deferred position */
    CHECK_EQ(t->GetVelocity(&v), DS_OK); CHECK_EQ(v.x, 4);
    CHECK_EQ(t->GetPosition(&v), DS_OK); CHECK_EQ(v.z, 0);       /* Get returns the applied value, not the deferred one */
    CHECK_EQ(t->SetPosition(0, 0, 500, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(t->SetPosition(0, 0, 0, 2), DSERR_INVALIDPARAM);   /* dwApply */
    CHECK_EQ(t->SetVelocity(0, 0, 0, 7), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetMode(DS3DMODE_DISABLE + 1, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetMode(DS3DMODE_HEADRELATIVE, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(t->GetMode(&mode), DS_OK); CHECK_EQ(mode, DS3DMODE_HEADRELATIVE);
    CHECK_EQ(t->SetMinDistance(0, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetMaxDistance(-1, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetConeAngles(361, 361, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetConeAngles(200, 100, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);   /* inside <= outside */
    CHECK_EQ(t->SetConeAngles(90, 180, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(t->GetConeAngles(&a1, &a2), DS_OK); CHECK(a1 == 90 && a2 == 180);
    CHECK_EQ(t->SetConeOutsideVolume(1, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetConeOutsideVolume(-10001, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetConeOutsideVolume(-500, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(t->GetConeOutsideVolume(&vol), DS_OK); CHECK_EQ(vol, -500);
    CHECK_EQ(t->GetConeAngles(nullptr, &a2), DSERR_INVALIDPARAM);
    CHECK_EQ(t->GetConeAngles(&a1, nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(t->GetPosition(nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(t->GetMinDistance(nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(t->GetMode(nullptr), DSERR_INVALIDPARAM);
    DS3DBUFFER all;
    memset(&all, 0, sizeof all);
    CHECK_EQ(t->GetAllParameters(nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(t->GetAllParameters(&all), DSERR_INVALIDPARAM);     /* dwSize unset */
    all.dwSize = sizeof all;
    CHECK_EQ(t->GetAllParameters(&all), DS_OK);
    all.dwInsideConeAngle = 400;
    CHECK_EQ(t->SetAllParameters(&all, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    all.dwInsideConeAngle = 360; all.dwOutsideConeAngle = 360; all.flMinDistance = 2; all.flMaxDistance = 50;
    all.dwSize = sizeof all - 4;
    CHECK_EQ(t->SetAllParameters(&all, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    all.dwSize = sizeof all;
    CHECK_EQ(t->SetAllParameters(nullptr, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(t->SetAllParameters(&all, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(t->GetMaxDistance(&f), DS_OK); CHECK_EQ(f, 50);
    t->Release(); b->Release();

    /* listener: deferred settings of one parameter are not committed by an immediate set of another */
    IDirectSoundBuffer *pr = nullptr;
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRL3D, nullptr, 0, &pr), DS_OK);
    IDirectSound3DListener *l = nullptr;
    CHECK_EQ(pr->QueryInterface(IID_IDirectSound3DListener, (void **)&l), S_OK);
    CHECK_EQ(l->SetRolloffFactor(11, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(l->SetDopplerFactor(-1, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(l->SetDistanceFactor(0, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    CHECK_EQ(l->SetPosition(0, 0, 0, 3), DSERR_INVALIDPARAM);
    CHECK_EQ(l->SetOrientation(0, 0, 0, 0, 1, 0, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);   /* zero front */
    CHECK_EQ(l->SetOrientation(0, 0, 1, 0, 0, 0, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);   /* zero top */
    CHECK_EQ(l->SetOrientation(0, 0, 1, 0, 0, 2, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);   /* parallel */
    CHECK_EQ(l->SetOrientation(1, 0, 0, 0, 1, 0, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(l->SetPosition(7, 0, 0, DS3D_DEFERRED), DS_OK);
    CHECK_EQ(l->SetRolloffFactor(2, DS3D_IMMEDIATE), DS_OK);
    CHECK_EQ(l->GetPosition(&v), DS_OK); CHECK_EQ(v.x, 0);       /* deferred: not applied yet */
    CHECK_EQ(l->GetOrientation(&v, nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(l->GetRolloffFactor(nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(l->GetDopplerFactor(nullptr), DSERR_INVALIDPARAM);
    DS3DLISTENER la;
    memset(&la, 0, sizeof la);
    CHECK_EQ(l->GetAllParameters(&la), DSERR_INVALIDPARAM);
    la.dwSize = sizeof la;
    CHECK_EQ(l->GetAllParameters(&la), DS_OK);
    CHECK_EQ(la.vPosition.x, 0); CHECK_EQ(la.flRolloffFactor, 2);
    la.flDopplerFactor = 20;
    CHECK_EQ(l->SetAllParameters(&la, DS3D_IMMEDIATE), DSERR_INVALIDPARAM);
    la.flDopplerFactor = 1;
    CHECK_EQ(l->SetAllParameters(&la, DS3D_DEFERRED), DS_OK);
    CHECK_EQ(l->CommitDeferredSettings(), DS_OK);
    l->Release(); pr->Release();
    ds->Release();
}

TEST(dsc_3d_deferred_applies_per_parameter)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(48000, 1, 16);
    IDirectSoundBuffer8 *b = buf(ds, DSBCAPS_CTRL3D, &w, 4000);
    IDirectSound3DBuffer *t = nullptr;
    b->QueryInterface(IID_IDirectSound3DBuffer, (void **)&t);
    fill(b, 10000);
    b->Play(0, 0, DSBPLAY_LOOPING);
    t->SetPosition(0, 0, 1, DS3D_IMMEDIATE);
    float near_peak = peak(render(64));
    t->SetPosition(0, 0, 100, DS3D_DEFERRED);                    /* deferred: the voice must not change ... */
    t->SetConeOutsideVolume(-100, DS3D_IMMEDIATE);               /* ... when another parameter is set immediately */
    CHECK_NEAR(peak(render(64)), near_peak, near_peak * 0.01);
    CHECK_EQ(t->GetPosition(nullptr), DSERR_INVALIDPARAM);
    IDirectSound3DListener *l = nullptr;
    IDirectSoundBuffer *pr = nullptr;
    create(ds, DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRL3D, nullptr, 0, &pr);
    pr->QueryInterface(IID_IDirectSound3DListener, (void **)&l);
    CHECK_EQ(l->CommitDeferredSettings(), DS_OK);               /* commits every deferred parameter */
    CHECK(peak(render(64)) < near_peak * 0.05);
    l->Release(); pr->Release(); t->Release(); b->Release(); ds->Release();
}

TEST(dsc_ks_property_set)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(22050, 1, 16);
    IDirectSoundBuffer8 *b = buf(ds, DSBCAPS_CTRL3D, &w, 4000);
    Ks *ks = nullptr;
    CHECK_EQ(b->QueryInterface(IID_Ks, (void **)&ks), S_OK);
    if (ks) {
        DWORD sup = 77;
        char data[8] = {0};
        ULONG ret = 5;
        CHECK_EQ(ks->QuerySupport(IID_Ks, 1, &sup), E_PROP_ID_UNSUPPORTED_);
        CHECK_EQ(sup, 0);
        CHECK_EQ(ks->Get(IID_Ks, 1, nullptr, 0, data, 8, &ret), E_PROP_ID_UNSUPPORTED_);
        CHECK_EQ(ks->Set(IID_Ks, 1, nullptr, 0, data, 8), E_PROP_ID_UNSUPPORTED_);
        ks->Release();
    }
    IDirectSoundBuffer *pr = nullptr;
    CHECK_EQ(create(ds, DSBCAPS_PRIMARYBUFFER, nullptr, 0, &pr), DS_OK);
    CHECK_EQ(pr->QueryInterface(IID_Ks, (void **)&ks), S_OK);
    if (ks) ks->Release();
    pr->Release(); b->Release(); ds->Release();
}

TEST(dsc_fx_contract)
{
    IDirectSound8 *ds = device();
    WAVEFORMATEX w = pcm(44100, 2, 16);
    IDirectSoundBuffer8 *b = buf(ds, DSBCAPS_CTRLFX, &w, 44100 * 4 / 2);
    IDirectSoundBuffer8 *plain = buf(ds, 0, &w, 44100 * 4 / 2);
    DSEFFECTDESC d[2];
    memset(d, 0, sizeof d);
    DWORD res[2] = {9, 9};
    CHECK_EQ(plain->SetFX(0, nullptr, nullptr), DSERR_CONTROLUNAVAIL);
    CHECK_EQ(b->SetFX(1, nullptr, nullptr), DSERR_INVALIDPARAM);
    CHECK_EQ(b->SetFX(0, d, nullptr), DSERR_INVALIDPARAM);
    d[0].dwSize = sizeof(DSEFFECTDESC) - 4; d[0].guidDSFXClass = GUID_Echo;
    CHECK_EQ(b->SetFX(1, d, res), DSERR_INVALIDPARAM);                    /* dwSize */
    d[0].dwSize = sizeof(DSEFFECTDESC); d[0].dwFlags = 0x80;
    CHECK_EQ(b->SetFX(1, d, res), DSERR_INVALIDPARAM);                    /* unknown effect flag */
    d[0].dwFlags = DSFX_LOCHARDWARE | DSFX_LOCSOFTWARE;
    CHECK_EQ(b->SetFX(1, d, res), DSERR_INVALIDPARAM);
    d[0].dwFlags = DSFX_LOCSOFTWARE;
    d[1] = d[0]; d[1].guidDSFXClass = GUID_Gargle;
    CHECK_EQ(b->SetFX(2, d, res), DS_OK);
    CHECK_EQ(res[0], DSFXR_LOCSOFTWARE); CHECK_EQ(res[1], DSFXR_LOCSOFTWARE);
    IEcho *e = nullptr;
    CHECK_EQ(b->GetObjectInPath(GUID_Echo, 0, IID_Echo, (void **)&e), DS_OK);
    CHECK_EQ(b->GetObjectInPath(GUID_Echo, 1, IID_Echo, (void **)&e), DSERR_OBJECTNOTFOUND);
    IUnknown *u = nullptr;
    CHECK_EQ(b->GetObjectInPath(GUID_All_Objects, 1, IID_IDirectSoundFXI3DL2Reverb8, (void **)&u), E_NOINTERFACE);
    CHECK_EQ(b->GetObjectInPath(GUID_All_Objects, 0, IID_IUnk, (void **)&u), DS_OK);
    if (u) u->Release();
    if (e) {
        EchoParams p;
        CHECK_EQ(e->GetAllParameters(&p), DS_OK);
        CHECK_EQ(p.wet, 50); CHECK_EQ(p.ldelay, 500); CHECK_EQ(p.pan, 0);   /* SDK defaults */
        p.wet = 101;
        CHECK_EQ(e->SetAllParameters(&p), DSERR_INVALIDPARAM);
        p.wet = 10; p.fb = 20; p.ldelay = 0.5f;
        CHECK_EQ(e->SetAllParameters(&p), DSERR_INVALIDPARAM);               /* delay 1..2000 ms */
        p.ldelay = 100; p.rdelay = 2000; p.pan = 2;
        CHECK_EQ(e->SetAllParameters(&p), DSERR_INVALIDPARAM);
        p.pan = 1;
        CHECK_EQ(e->SetAllParameters(&p), DS_OK);
        CHECK_EQ(e->SetAllParameters(nullptr), DSERR_INVALIDPARAM);
        CHECK_EQ(e->GetAllParameters(nullptr), DSERR_INVALIDPARAM);
        IUnknown *same = nullptr;
        CHECK_EQ(e->QueryInterface(IID_Echo, (void **)&same), S_OK);          /* own interfaces */
        if (same) same->Release();
        CHECK_EQ(e->QueryInterface(IID_IDirectSoundFXI3DL2Reverb8, (void **)&same), E_NOINTERFACE);
        CHECK_EQ(b->SetFX(0, nullptr, nullptr), DS_OK);                       /* effects are detached ... */
        CHECK_EQ(e->GetAllParameters(&p), DS_OK);                             /* ... but a held reference stays valid */
        e->Release();
    }
    IGargle *g = nullptr;
    CHECK_EQ(b->SetFX(2, d, res), DS_OK);
    CHECK_EQ(b->GetObjectInPath(GUID_Gargle, 0, IID_Gargle, (void **)&g), DS_OK);
    if (g) {
        GargleParams gp = {0, 0};
        CHECK_EQ(g->SetAllParameters(&gp), DSERR_INVALIDPARAM);
        gp = {1000, 2};
        CHECK_EQ(g->SetAllParameters(&gp), DSERR_INVALIDPARAM);
        gp = {1000, 1};
        CHECK_EQ(g->SetAllParameters(&gp), DS_OK);
        g->Release();
    }
    /* I3DL2 reverb ranges, quality, AcquireResources */
    d[0].guidDSFXClass = GUID_DSFX_STANDARD_I3DL2REVERB;
    CHECK_EQ(b->SetFX(1, d, res), DS_OK);
    IDirectSoundFXI3DL2Reverb8 *r = nullptr;
    CHECK_EQ(b->GetObjectInPath(GUID_DSFX_STANDARD_I3DL2REVERB, 0, IID_IDirectSoundFXI3DL2Reverb8, (void **)&r), DS_OK);
    if (r) {
        DSFXI3DL2Reverb p;
        CHECK_EQ(r->SetPreset(0), DS_OK);
        CHECK_EQ(r->GetAllParameters(&p), DS_OK);
        p.flDecayTime = 25;
        CHECK_EQ(r->SetAllParameters(&p), DSERR_INVALIDPARAM);
        p.flDecayTime = 1; p.lRoom = 1;
        CHECK_EQ(r->SetAllParameters(&p), DSERR_INVALIDPARAM);
        CHECK_EQ(r->SetQuality(4), DSERR_INVALIDPARAM);
        CHECK_EQ(r->SetQuality(3), DS_OK);
        r->Release();
    }
    CHECK_EQ(b->AcquireResources(0, 1, res), DSERR_INVALIDCALL);          /* only for DSBCAPS_LOCDEFER buffers */
    plain->Release(); b->Release(); ds->Release();
}
