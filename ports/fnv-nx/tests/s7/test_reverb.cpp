/* test_reverb.cpp - the I3DL2 reverb DSP (platform/audio/reverb.h) and its DirectSound path (SetFX ->
 * IDirectSoundFXI3DL2Reverb8 SetAllParameters / SetPreset -> the mixer voice). Measured on impulse responses:
 * the late decay follows decay_time, decay_hf_ratio shortens the high band, the first reflection arrives at
 * reflections_delay, room -10000 leaves the direct sound alone, 20 s decay stays bounded. */
#include "s7_test.h"

#include <dsound.h>
#include <stdint.h>
#include <vector>

#include "../../platform/audio/mixer.h"
#include "../../platform/audio/reverb.h"

namespace {
const uint32_t RATE = 48000;

fnvnx_i3dl2 params(float decay, float hf_ratio)
{
    fnvnx_i3dl2 p = {0, 0, 0.0f, decay, hf_ratio, -10000, 0.007f, 0, 0.011f, 100.0f, 100.0f, 5000.0f};
    return p;   /* room 0, reflections off, reverb 0 mB */
}

/* impulse response of `seconds`, left channel */
std::vector<float> impulse(fnvnx_reverb *r, float seconds)
{
    uint32_t n = (uint32_t)(seconds * RATE);
    std::vector<float> io(n * 2, 0.0f);
    io[0] = io[1] = 1.0f;
    fnvnx_reverb_process(r, io.data(), n);
    std::vector<float> l(n);
    for (uint32_t i = 0; i < n; i++) l[i] = io[2 * i];
    return l;
}

/* energy in dB over [t0, t0 + 50 ms) */
double level_db(const std::vector<float> &x, float t0)
{
    uint32_t a = (uint32_t)(t0 * RATE), b = a + RATE / 20;
    double e = 1e-30;
    for (uint32_t i = a; i < b && i < x.size(); i++) e += (double)x[i] * x[i];
    return 10.0 * log10(e);
}

/* crude band split: one-pole low-pass at 500 Hz and its complement */
void split(const std::vector<float> &x, std::vector<float> &lo, std::vector<float> &hi)
{
    float a = expf(-2.0f * 3.14159265f * 500.0f / RATE), y = 0.0f;
    lo.resize(x.size());
    hi.resize(x.size());
    for (size_t i = 0; i < x.size(); i++) {
        y = (1.0f - a) * x[i] + a * y;
        lo[i] = y;
        hi[i] = x[i] - y;
    }
}
} // namespace

TEST(reverb_decay_time)
{
    for (float T : {0.5f, 1.49f, 4.0f}) {
        fnvnx_reverb *r = fnvnx_reverb_create(RATE);
        CHECK(r != nullptr);
        fnvnx_i3dl2 p = params(T, 1.0f);
        fnvnx_reverb_set(r, &p);
        std::vector<float> x = impulse(r, 0.2f + T * 0.6f);
        /* slope between 0.1 s and 0.1 s + T/2: -30 dB expected (60 dB per decay_time) */
        double d = level_db(x, 0.1f) - level_db(x, 0.1f + T * 0.5f);
        CHECK_NEAR(d, 30.0, 6.0);
        fnvnx_reverb_destroy(r);
    }
}

TEST(reverb_hf_ratio_and_room_hf)
{
    fnvnx_reverb *r = fnvnx_reverb_create(RATE);
    fnvnx_i3dl2 p = params(2.0f, 0.3f);
    fnvnx_reverb_set(r, &p);
    std::vector<float> x = impulse(r, 1.2f), lo, hi;
    split(x, lo, hi);
    double dlo = level_db(lo, 0.1f) - level_db(lo, 0.9f), dhi = level_db(hi, 0.1f) - level_db(hi, 0.9f);
    CHECK(dhi > dlo + 6.0);   /* the high band dies faster */
    CHECK_NEAR(dlo, 24.0, 7.0);   /* 0.8 s of a 2 s decay */
    fnvnx_reverb_destroy(r);

    /* room_hf -10000 mB: the wet signal loses its highs above hf_reference (1 kHz here) */
    double hi_db[2], lo_db[2];
    for (int k = 0; k < 2; k++) {
        r = fnvnx_reverb_create(RATE);
        p = params(1.0f, 1.0f);
        p.hf_reference = 1000.0f;
        p.room_hf = k ? -10000 : 0;
        fnvnx_reverb_set(r, &p);
        std::vector<float> y = impulse(r, 0.5f), yl, yh;
        y[0] = 0.0f;   /* drop the direct impulse */
        /* high band: above ~4 kHz */
        float a = expf(-2.0f * 3.14159265f * 4000.0f / RATE), z = 0.0f;
        yh.resize(y.size());
        for (size_t i = 0; i < y.size(); i++) { z = (1.0f - a) * y[i] + a * z; yh[i] = y[i] - z; }
        split(y, yl, lo);
        hi_db[k] = level_db(yh, 0.05f);
        lo_db[k] = level_db(yl, 0.05f);
        fnvnx_reverb_destroy(r);
    }
    CHECK(hi_db[1] < hi_db[0] - 10.0);       /* the highs drop */
    CHECK_NEAR(lo_db[1], lo_db[0], 3.0);     /* the lows stay */
}

TEST(reverb_reflections_delay_room_off_and_bounds)
{
    fnvnx_reverb *r = fnvnx_reverb_create(RATE);
    fnvnx_i3dl2 p = {0, 0, 0.0f, 1.0f, 1.0f, 0, 0.050f, -10000, 0.1f, 100.0f, 100.0f, 5000.0f};   /* reflections only */
    fnvnx_reverb_set(r, &p);
    std::vector<float> x = impulse(r, 0.2f);
    uint32_t first = 0;
    for (uint32_t i = 1; i < x.size() && !first; i++)
        if (fabsf(x[i]) > 1e-4f) first = i;
    CHECK_NEAR(first, 0.050 * RATE, 2.0);
    fnvnx_reverb_destroy(r);

    /* room -10000: the direct sound only, unchanged */
    r = fnvnx_reverb_create(RATE);
    p = params(3.0f, 1.0f);
    p.room = -10000;
    fnvnx_reverb_set(r, &p);
    float io[8] = {0.5f, -0.25f, 0.1f, 0.2f, 0.0f, 0.0f, -1.0f, 1.0f}, ref[8];
    memcpy(ref, io, sizeof io);
    fnvnx_reverb_process(r, io, 4);
    CHECK(!memcmp(io, ref, sizeof io));
    fnvnx_reverb_destroy(r);

    /* 20 s, the longest decay, fed with full-scale noise for 2 s: bounded, and silent input decays */
    r = fnvnx_reverb_create(RATE);
    p = params(20.0f, 2.0f);
    p.reverb = 2000;
    fnvnx_reverb_set(r, &p);
    std::vector<float> n(RATE * 2 * 2);
    uint32_t s = 1;
    for (float &v : n) { s = s * 1664525u + 1013904223u; v = (float)(int32_t)s / 2147483648.0f; }
    fnvnx_reverb_process(r, n.data(), RATE * 2);
    float peak = 0.0f;
    for (float v : n) peak = fmaxf(peak, fabsf(v));
    CHECK(peak < 64.0f && peak == peak);
    std::vector<float> z(RATE * 2, 0.0f);
    fnvnx_reverb_process(r, z.data(), RATE);
    float tail = 0.0f;
    for (float v : z) tail = fmaxf(tail, fabsf(v));
    CHECK(tail < peak);
    fnvnx_reverb_reset(r);
    std::fill(z.begin(), z.end(), 0.0f);
    fnvnx_reverb_process(r, z.data(), RATE / 2);
    tail = 0.0f;
    for (float v : z) tail = fmaxf(tail, fabsf(v));
    CHECK(tail == 0.0f);   /* reset clears the tail */
    fnvnx_reverb_destroy(r);
}

TEST(reverb_through_directsound_setpreset)
{
    mixer_init(RATE, 256);
    IDirectSound8 *ds = nullptr;
    CHECK_EQ(DirectSoundCreate8(nullptr, &ds, nullptr), DS_OK);
    ds->SetCooperativeLevel(nullptr, DSSCL_PRIORITY);
    WAVEFORMATEX w;
    memset(&w, 0, sizeof w);
    w.wFormatTag = WAVE_FORMAT_PCM;
    w.nChannels = 1;
    w.nSamplesPerSec = RATE;
    w.wBitsPerSample = 16;
    w.nBlockAlign = 2;
    w.nAvgBytesPerSec = RATE * 2;
    DSBUFFERDESC d;
    memset(&d, 0, sizeof d);
    d.dwSize = sizeof d;
    d.dwFlags = DSBCAPS_CTRLFX | DSBCAPS_CTRLVOLUME;
    d.dwBufferBytes = RATE * 2;   /* 1 s: a click, then silence */
    d.lpwfxFormat = &w;
    IDirectSoundBuffer *b0 = nullptr;
    CHECK_EQ(ds->CreateSoundBuffer(&d, &b0, nullptr), DS_OK);
    IDirectSoundBuffer8 *b = nullptr;
    b0->QueryInterface(IID_IDirectSoundBuffer8, (void **)&b);
    b0->Release();
    void *p1, *p2;
    DWORD n1, n2;
    b->Lock(0, 0, &p1, &n1, &p2, &n2, DSBLOCK_ENTIREBUFFER);
    memset(p1, 0, n1);
    for (int i = 0; i < 64; i++) ((int16_t *)p1)[i] = 20000;
    b->Unlock(p1, n1, p2, n2);

    DSEFFECTDESC e;
    memset(&e, 0, sizeof e);
    e.dwSize = sizeof e;
    e.guidDSFXClass = GUID_DSFX_STANDARD_I3DL2REVERB;
    DWORD res = 0;
    CHECK_EQ(b->SetFX(1, &e, &res), DS_OK);
    IDirectSoundFXI3DL2Reverb8 *rv = nullptr;
    CHECK_EQ(b->GetObjectInPath(GUID_DSFX_STANDARD_I3DL2REVERB, 0, IID_IDirectSoundFXI3DL2Reverb8, (void **)&rv), DS_OK);
    CHECK_EQ(rv->SetPreset(11), DS_OK);   /* HANGAR: 10 s */
    DSFXI3DL2Reverb got;
    CHECK_EQ(rv->GetAllParameters(&got), DS_OK);
    CHECK_NEAR(got.flDecayTime, 10.05, 1e-4);
    CHECK_EQ(rv->SetPreset(30), DSERR_INVALIDPARAM);
    CHECK_EQ(b->Play(0, 0, 0), DS_OK);
    std::vector<float> out(RATE / 2 * 2);
    mixer_render_f32(out.data(), RATE / 2);
    double tail = 0.0;   /* 200..500 ms: the click is long over, the hangar still rings */
    for (uint32_t i = RATE / 5; i < RATE / 2; i++) tail += (double)out[2 * i] * out[2 * i];
    CHECK(tail > 1e-4);
    b->Stop();
    rv->Release();
    CHECK_EQ(b->SetFX(0, nullptr, nullptr), DS_OK);   /* removes the effect: dry again */
    b->SetCurrentPosition(0);
    b->Play(0, 0, 0);
    mixer_render_f32(out.data(), RATE / 2);
    tail = 0.0;
    for (uint32_t i = RATE / 5; i < RATE / 2; i++) tail += (double)out[2 * i] * out[2 * i];
    CHECK(tail == 0.0);
    b->Stop();
    b->Release();
    ds->Release();
}
