/* dsound.cpp - DirectSound 8 on the software mixer (docs/AUDIO_INPUT.md "DirectSound").
 *
 * Objects: Device (IDirectSound8), Buffer (IDirectSoundBuffer8; a primary buffer also exposes
 * IDirectSound3DListener, a secondary one IDirectSound3DBuffer / IDirectSoundNotify / FX parameter objects).
 * Sub-interfaces are members sharing their owner's reference count. A secondary buffer's storage is a
 * reference-counted block shared with its duplicates; the mixer voice reads it directly, so Lock/Unlock just
 * hand out pointers, as on a software DirectSound device.
 *
 * 3D: each 3D buffer keeps current and deferred parameters; the listener too. DS3D_IMMEDIATE writes both and
 * recomputes; DS3D_DEFERRED writes the deferred copy and CommitDeferredSettings applies everything.
 * FX: SetFX/GetObjectInPath; the I3DL2 reverb (SetAllParameters, SetPreset) runs in the mixer (reverb.h) on the
 * buffer's voice. The Waves reverb keeps its parameters only (the game does not use it).
 */
#include <dsound.h>
#include "../include/fnvnx_win32_ext.h"

#include "backend.h"
#include "ds3d.h"
#include "dsound_fx.h"
#include "mixer.h"
#include "reverb.h"
#include "../os/os.h"

#include <math.h>
#include <stddef.h>
#include <new>
#include <stdlib.h>
#include <string.h>

#define DW(x) ((uint32_t)(x))

namespace {

/* member-wise: the host (LP64) GUID has padding bytes that are not part of its value */
bool guid_eq(const GUID &a, const GUID &b)
{
    return a.Data1 == b.Data1 && a.Data2 == b.Data2 && a.Data3 == b.Data3 && !memcmp(a.Data4, b.Data4, 8);
}
const GUID kIID_IUnknown = {0x00000000, 0x0000, 0x0000, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
#define DSBNOTIFICATIONS_MAX_ 100000u

void notify_event(void *ctx, uint32_t off, void *event)
{
    (void)ctx; (void)off;
    if (event)
        SetEvent((HANDLE)event);
}

struct Shared {
    int refs;
    uint32_t size;
    uint8_t *bytes;
};

struct Device;
struct Buffer;

/* ---- FX parameter objects. The I3DL2 reverb drives the voice's DSP (mixer_voice_set_reverb). */
/* DSFX_I3DL2_ENVIRONMENT_PRESET_DEFAULT .. _PLATE: the DirectX SDK's I3DL2_ENVIRONMENT_PRESET_* values (dsound.h).
 * The game cycles them with SetPreset (0x00af194f). */
static const DSFXI3DL2Reverb k_i3dl2_presets[] = {
    {-1000, -100, 0.0f, 1.49f, 0.83f, -2602, 0.007f, 200, 0.011f, 100.0f, 100.0f, 5000.0f},    /* DEFAULT */
    {-1000, -100, 0.0f, 1.49f, 0.83f, -2602, 0.007f, 200, 0.011f, 100.0f, 100.0f, 5000.0f},    /* GENERIC */
    {-1000, -6000, 0.0f, 0.17f, 0.10f, -1204, 0.001f, 207, 0.002f, 100.0f, 100.0f, 5000.0f},   /* PADDEDCELL */
    {-1000, -454, 0.0f, 0.40f, 0.83f, -1646, 0.002f, 53, 0.003f, 100.0f, 100.0f, 5000.0f},     /* ROOM */
    {-1000, -1200, 0.0f, 1.49f, 0.54f, -370, 0.007f, 1030, 0.011f, 100.0f, 60.0f, 5000.0f},    /* BATHROOM */
    {-1000, -6000, 0.0f, 0.50f, 0.10f, -1376, 0.003f, -1104, 0.004f, 100.0f, 100.0f, 5000.0f}, /* LIVINGROOM */
    {-1000, -300, 0.0f, 2.31f, 0.64f, -711, 0.012f, 83, 0.017f, 100.0f, 100.0f, 5000.0f},      /* STONEROOM */
    {-1000, -476, 0.0f, 4.32f, 0.59f, -789, 0.020f, -289, 0.030f, 100.0f, 100.0f, 5000.0f},    /* AUDITORIUM */
    {-1000, -500, 0.0f, 3.92f, 0.70f, -1230, 0.020f, -2, 0.029f, 100.0f, 100.0f, 5000.0f},     /* CONCERTHALL */
    {-1000, 0, 0.0f, 2.91f, 1.30f, -602, 0.015f, -302, 0.022f, 100.0f, 100.0f, 5000.0f},       /* CAVE */
    {-1000, -698, 0.0f, 7.24f, 0.33f, -1166, 0.020f, 16, 0.030f, 100.0f, 100.0f, 5000.0f},     /* ARENA */
    {-1000, -1000, 0.0f, 10.05f, 0.23f, -602, 0.020f, 198, 0.030f, 100.0f, 100.0f, 5000.0f},   /* HANGAR */
    {-1000, -4000, 0.0f, 0.30f, 0.10f, -1831, 0.002f, -1630, 0.030f, 100.0f, 100.0f, 5000.0f}, /* CARPETEDHALLWAY */
    {-1000, -300, 0.0f, 1.49f, 0.59f, -1219, 0.007f, 441, 0.011f, 100.0f, 100.0f, 5000.0f},    /* HALLWAY */
    {-1000, -237, 0.0f, 2.70f, 0.79f, -1214, 0.013f, 395, 0.020f, 100.0f, 100.0f, 5000.0f},    /* STONECORRIDOR */
    {-1000, -270, 0.0f, 1.49f, 0.86f, -1204, 0.007f, -4, 0.011f, 100.0f, 100.0f, 5000.0f},     /* ALLEY */
    {-1000, -3300, 0.0f, 1.49f, 0.54f, -2560, 0.162f, -613, 0.088f, 79.0f, 100.0f, 5000.0f},   /* FOREST */
    {-1000, -800, 0.0f, 1.49f, 0.67f, -2273, 0.007f, -2217, 0.011f, 50.0f, 100.0f, 5000.0f},   /* CITY */
    {-1000, -2500, 0.0f, 1.49f, 0.21f, -2780, 0.300f, -2014, 0.100f, 27.0f, 100.0f, 5000.0f},  /* MOUNTAINS */
    {-1000, -1000, 0.0f, 1.49f, 0.83f, -10000, 0.061f, 500, 0.025f, 100.0f, 100.0f, 5000.0f},  /* QUARRY */
    {-1000, -2000, 0.0f, 1.49f, 0.50f, -2466, 0.179f, -2514, 0.100f, 21.0f, 100.0f, 5000.0f},  /* PLAIN */
    {-1000, 0, 0.0f, 1.65f, 1.50f, -1363, 0.008f, -1153, 0.012f, 100.0f, 100.0f, 5000.0f},     /* PARKINGLOT */
    {-1000, -1000, 0.0f, 2.81f, 0.14f, 429, 0.014f, 648, 0.021f, 80.0f, 60.0f, 5000.0f},       /* SEWERPIPE */
    {-1000, -4000, 0.0f, 1.49f, 0.10f, -449, 0.007f, 1700, 0.011f, 100.0f, 100.0f, 5000.0f},   /* UNDERWATER */
    {-1000, -600, 0.0f, 1.10f, 0.83f, -400, 0.005f, 500, 0.010f, 100.0f, 100.0f, 5000.0f},     /* SMALLROOM */
    {-1000, -600, 0.0f, 1.30f, 0.83f, -1000, 0.010f, -200, 0.020f, 100.0f, 100.0f, 5000.0f},   /* MEDIUMROOM */
    {-1000, -600, 0.0f, 1.50f, 0.83f, -1600, 0.020f, -1000, 0.040f, 100.0f, 100.0f, 5000.0f},  /* LARGEROOM */
    {-1000, -600, 0.0f, 1.80f, 0.70f, -1300, 0.015f, -800, 0.030f, 100.0f, 100.0f, 5000.0f},   /* MEDIUMHALL */
    {-1000, -600, 0.0f, 1.80f, 0.70f, -2000, 0.030f, -1400, 0.060f, 100.0f, 100.0f, 5000.0f},  /* LARGEHALL */
    {-1000, -200, 0.0f, 1.30f, 0.90f, 0, 0.002f, 0, 0.010f, 100.0f, 75.0f, 5000.0f},           /* PLATE */
};
static void apply_i3dl2(Buffer *b, const DSFXI3DL2Reverb &p);   /* after Buffer */

/* Parameter ranges of the SDK (dsound.h DSFX_*_MIN/MAX); a table row is checked against the struct field. */
struct Rng { uint16_t off; char kind; double lo, hi; };
#define R(T, m, k, lo, hi) {(uint16_t)offsetof(T, m), k, lo, hi}
static bool fx_valid(const void *p, const Rng *r, size_t n)
{
    for (; n--; r++) {
        const char *q = (const char *)p + r->off;
        double v;
        if (r->kind == 'f') { float f; memcpy(&f, q, 4); v = f; }
        else if (r->kind == 'l') { LONG l; memcpy(&l, q, sizeof l); v = (double)l; }
        else { DWORD d; memcpy(&d, q, sizeof d); v = (double)d; }
        if (!(v >= r->lo && v <= r->hi)) return false;   /* false for NaN */
    }
    return true;
}
static HRESULT fx_qi(IUnknown *self, const GUID &own, REFIID r, void **o)
{
    if (!o) return DSERR_INVALIDPARAM;
    *o = nullptr;
    if (!guid_eq(r, kIID_IUnknown) && !guid_eq(r, own)) return E_NOINTERFACE;
    *o = self;
    self->AddRef();
    return S_OK;
}
#define FX_UNKNOWN(iid)                                                                                      \
    int refs = 1;                                                                                            \
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID r, void **o) override { return fx_qi(this, iid, r, o); } \
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); } \
    ULONG STDMETHODCALLTYPE Release() override                                                               \
    {                                                                                                        \
        int n = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);                                              \
        if (n == 0) delete this;                                                                             \
        return (ULONG)n;                                                                                     \
    }

static const Rng r_i3dl2[] = {
    R(DSFXI3DL2Reverb, lRoom, 'l', -10000, 0), R(DSFXI3DL2Reverb, lRoomHF, 'l', -10000, 0),
    R(DSFXI3DL2Reverb, flRoomRolloffFactor, 'f', 0, 10), R(DSFXI3DL2Reverb, flDecayTime, 'f', 0.1, 20),
    R(DSFXI3DL2Reverb, flDecayHFRatio, 'f', 0.1, 2), R(DSFXI3DL2Reverb, lReflections, 'l', -10000, 1000),
    R(DSFXI3DL2Reverb, flReflectionsDelay, 'f', 0, 0.3), R(DSFXI3DL2Reverb, lReverb, 'l', -10000, 2000),
    R(DSFXI3DL2Reverb, flReverbDelay, 'f', 0, 0.1), R(DSFXI3DL2Reverb, flDiffusion, 'f', 0, 100),
    R(DSFXI3DL2Reverb, flDensity, 'f', 0, 100), R(DSFXI3DL2Reverb, flHFReference, 'f', 20, 20000)};

/* The I3DL2 reverb is the one effect with DSP (the mixer voice's reverb); `owner` is cleared when the chain is
 * replaced or the buffer dies, after which the object keeps its parameters but drives nothing. */
struct I3DL2 final : IDirectSoundFXI3DL2Reverb8 {
    Buffer *owner = nullptr;
    DSFXI3DL2Reverb p;
    DWORD preset = 0;
    LONG quality = 2;
    FX_UNKNOWN(IID_IDirectSoundFXI3DL2Reverb8)
    HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDSFXI3DL2Reverb in) override
    {
        if (!in || !fx_valid(in, r_i3dl2, sizeof r_i3dl2 / sizeof *r_i3dl2)) return DSERR_INVALIDPARAM;
        p = *in;
        apply_i3dl2(owner, p);
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetAllParameters(LPDSFXI3DL2Reverb out) override { if (!out) return DSERR_INVALIDPARAM; *out = p; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetPreset(DWORD d) override
    {
        if (DW(d) >= sizeof k_i3dl2_presets / sizeof k_i3dl2_presets[0]) return DSERR_INVALIDPARAM;
        preset = DW(d);
        p = k_i3dl2_presets[preset];
        apply_i3dl2(owner, p);
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetPreset(LPDWORD d) override { if (!d) return DSERR_INVALIDPARAM; *d = preset; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetQuality(LONG q) override { if (q < 0 || q > 3) return DSERR_INVALIDPARAM; quality = q; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetQuality(LONG *q) override { if (!q) return DSERR_INVALIDPARAM; *q = quality; return DS_OK; }
};

template <class I, class T> struct FxObj final : I {
    const GUID &iid;
    const Rng *rng;
    size_t nrng;
    T p;
    FxObj(const GUID &g, const Rng *r, size_t n, const T &d) : iid(g), rng(r), nrng(n), p(d) {}
    FX_UNKNOWN(iid)
    HRESULT STDMETHODCALLTYPE SetAllParameters(const T *in) override
    {
        if (!in || !fx_valid(in, rng, nrng)) return DSERR_INVALIDPARAM;
        p = *in;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetAllParameters(T *out) override { if (!out) return DSERR_INVALIDPARAM; *out = p; return DS_OK; }
};
FNVNX_DS_GUID(DS3DALG_HRTF_FULL_, 0xc2413340, 0x1c1b, 0x11d2, 0x94, 0xf5, 0x00, 0xc0, 0x4f, 0xc2, 0x8a, 0xca);
FNVNX_DS_GUID(DS3DALG_HRTF_LIGHT_, 0xc2413342, 0x1c1b, 0x11d2, 0x94, 0xf5, 0x00, 0xc0, 0x4f, 0xc2, 0x8a, 0xca);
FNVNX_DS_GUID(DSDEVID_DefaultVoicePlayback_, 0xdef00002, 0x9c6d, 0x47ed, 0xaa, 0xf1, 0x4d, 0xda, 0x8f, 0x2b, 0x5c, 0x03);

static const Rng r_chorus[] = {R(FxChorus, wet, 'f', 0, 100), R(FxChorus, depth, 'f', 0, 100), R(FxChorus, fb, 'f', -99, 99),
                               R(FxChorus, freq, 'f', 0, 10), R(FxChorus, wave, 'l', 0, 1), R(FxChorus, delay, 'f', 0, 20),
                               R(FxChorus, phase, 'l', 0, 4)};
static const Rng r_flanger[] = {R(FxChorus, wet, 'f', 0, 100), R(FxChorus, depth, 'f', 0, 100), R(FxChorus, fb, 'f', -99, 99),
                                R(FxChorus, freq, 'f', 0, 10), R(FxChorus, wave, 'l', 0, 1), R(FxChorus, delay, 'f', 0, 4),
                                R(FxChorus, phase, 'l', 0, 4)};   /* flanger: the chorus layout, delay 0..4 ms */
static const Rng r_comp[] = {R(FxCompressor, gain, 'f', -60, 60), R(FxCompressor, attack, 'f', 0.01, 500),
                             R(FxCompressor, release, 'f', 50, 3000), R(FxCompressor, threshold, 'f', -60, 0),
                             R(FxCompressor, ratio, 'f', 1, 100), R(FxCompressor, predelay, 'f', 0, 4)};
static const Rng r_dist[] = {R(FxDistortion, gain, 'f', -60, 0), R(FxDistortion, edge, 'f', 0, 100),
                             R(FxDistortion, eqcenter, 'f', 100, 8000), R(FxDistortion, eqbw, 'f', 100, 8000),
                             R(FxDistortion, lowpass, 'f', 100, 8000)};
static const Rng r_echo[] = {R(FxEcho, wet, 'f', 0, 100), R(FxEcho, fb, 'f', 0, 100), R(FxEcho, ldelay, 'f', 1, 2000),
                             R(FxEcho, rdelay, 'f', 1, 2000), R(FxEcho, pan, 'l', 0, 1)};
static const Rng r_gargle[] = {R(FxGargle, rate, 'd', 1, 1000), R(FxGargle, shape, 'd', 0, 1)};
static const Rng r_peq[] = {R(FxParamEq, center, 'f', 80, 16000), R(FxParamEq, bandwidth, 'f', 1, 36), R(FxParamEq, gain, 'f', -15, 15)};
static const Rng r_waves[] = {R(DSFXWavesReverb, fInGain, 'f', -96, 0), R(DSFXWavesReverb, fReverbMix, 'f', -96, 0),
                              R(DSFXWavesReverb, fReverbTime, 'f', 0.001, 3000), R(DSFXWavesReverb, fHighFreqRTRatio, 'f', 0.001, 0.999)};
template <class I, class T, size_t N> static IUnknown *mk(const GUID &iid, const Rng (&r)[N], const T &d)
{
    return new (std::nothrow) FxObj<I, T>(iid, r, N, d);
}
struct FxClass { const GUID *cls; IUnknown *(*make)(); };
static const FxClass k_fx_classes[] = {
    {&GUID_DSFX_WAVES_REVERB, [] { return mk<IDirectSoundFXWavesReverb8, DSFXWavesReverb>(IID_IDirectSoundFXWavesReverb8, r_waves, DSFXWavesReverb{0.0f, 0.0f, 1000.0f, 0.001f}); }},
    {&GUID_Chorus, [] { return mk<FxIf<FxChorus>, FxChorus>(IID_Chorus, r_chorus, FxChorus{50, 10, 25, 1.1f, 1, 16, 3}); }},
    {&GUID_Flanger, [] { return mk<FxIf<FxChorus>, FxChorus>(IID_Flanger, r_flanger, FxChorus{50, 100, -50, 0.25f, 1, 2, 2}); }},
    {&GUID_Compressor, [] { return mk<FxIf<FxCompressor>, FxCompressor>(IID_Compressor, r_comp, FxCompressor{0, 10, 200, -20, 3, 4}); }},
    {&GUID_Distortion, [] { return mk<FxIf<FxDistortion>, FxDistortion>(IID_Distortion, r_dist, FxDistortion{-18, 15, 2400, 2400, 8000}); }},
    {&GUID_Echo, [] { return mk<FxIf<FxEcho>, FxEcho>(IID_Echo, r_echo, FxEcho{50, 50, 500, 500, 0}); }},
    {&GUID_Gargle, [] { return mk<FxIf<FxGargle>, FxGargle>(IID_Gargle, r_gargle, FxGargle{20, 0}); }},
    {&GUID_ParamEq, [] { return mk<FxIf<FxParamEq>, FxParamEq>(IID_ParamEq, r_peq, FxParamEq{8000, 12, 0}); }},
};

struct Effect {
    GUID cls;
    IUnknown *obj;     /* null: not instantiated (res says why) */
    I3DL2 *i3dl2;      /* obj itself when it is the I3DL2 reverb */
    DWORD res;         /* DSFXR_* */
};

/* ---- 3D buffer: current + pending parameters; DS3D_IMMEDIATE / DS3D_DEFERRED apply per parameter group (mask) */
static D3DVECTOR dv(const ds3d_vec &v) { D3DVECTOR r = {v.x, v.y, v.z}; return r; }
static ds3d_vec sv(float x, float y, float z) { ds3d_vec v = {x, y, z}; return v; }
#define NN(p) do { if (!(p)) return DSERR_INVALIDPARAM; } while (0)
struct Buffer3D final : IDirectSound3DBuffer {
    Buffer *owner;
    ds3d_source cur, pend;
    unsigned dirty = 0;   /* DS3D_S_* groups set DEFERRED and not yet committed */
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT apply(DWORD how, unsigned mask);
    HRESULT set(DWORD how, unsigned mask, const ds3d_source &n)
    {
        if (DW(how) > DS3D_DEFERRED || !ds3d_source_valid(&n)) return DSERR_INVALIDPARAM;
        pend = n;
        return apply(how, mask);
    }
    /* Get* return the committed (applied) value, as DirectSound does for deferred settings */
    HRESULT STDMETHODCALLTYPE GetAllParameters(LPDS3DBUFFER p) override
    {
        NN(p);
        if (DW(p->dwSize) != sizeof(DS3DBUFFER)) return DSERR_INVALIDPARAM;
        p->vPosition = dv(cur.pos);
        p->vVelocity = dv(cur.vel);
        p->dwInsideConeAngle = cur.cone_inside;
        p->dwOutsideConeAngle = cur.cone_outside;
        p->vConeOrientation = dv(cur.cone_orient);
        p->lConeOutsideVolume = cur.cone_outside_volume;
        p->flMinDistance = cur.min_distance;
        p->flMaxDistance = cur.max_distance;
        p->dwMode = cur.mode;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetConeAngles(LPDWORD a, LPDWORD b) override { NN(a); NN(b); *a = cur.cone_inside; *b = cur.cone_outside; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetConeOrientation(D3DVECTOR *v) override { NN(v); *v = dv(cur.cone_orient); return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetConeOutsideVolume(LPLONG v) override { NN(v); *v = cur.cone_outside_volume; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetMaxDistance(D3DVALUE *v) override { NN(v); *v = cur.max_distance; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetMinDistance(D3DVALUE *v) override { NN(v); *v = cur.min_distance; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetMode(LPDWORD v) override { NN(v); *v = cur.mode; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetPosition(D3DVECTOR *v) override { NN(v); *v = dv(cur.pos); return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetVelocity(D3DVECTOR *v) override { NN(v); *v = dv(cur.vel); return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDS3DBUFFER p, DWORD how) override
    {
        NN(p);
        if (DW(p->dwSize) != sizeof(DS3DBUFFER)) return DSERR_INVALIDPARAM;
        ds3d_source n = pend;
        n.pos = sv(p->vPosition.x, p->vPosition.y, p->vPosition.z);
        n.vel = sv(p->vVelocity.x, p->vVelocity.y, p->vVelocity.z);
        n.cone_inside = DW(p->dwInsideConeAngle);
        n.cone_outside = DW(p->dwOutsideConeAngle);
        n.cone_orient = sv(p->vConeOrientation.x, p->vConeOrientation.y, p->vConeOrientation.z);
        n.cone_outside_volume = (int32_t)p->lConeOutsideVolume;
        n.min_distance = p->flMinDistance;
        n.max_distance = p->flMaxDistance;
        n.mode = DW(p->dwMode);
        return set(how, DS3D_S_ALL, n);
    }
    HRESULT STDMETHODCALLTYPE SetConeAngles(DWORD a, DWORD b, DWORD how) override { ds3d_source n = pend; n.cone_inside = DW(a); n.cone_outside = DW(b); return set(how, DS3D_S_CONE_ANGLES, n); }
    HRESULT STDMETHODCALLTYPE SetConeOrientation(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { ds3d_source n = pend; n.cone_orient = sv(x, y, z); return set(how, DS3D_S_CONE_ORIENT, n); }
    HRESULT STDMETHODCALLTYPE SetConeOutsideVolume(LONG v, DWORD how) override { ds3d_source n = pend; n.cone_outside_volume = v < -10000 ? -10001 : v > 0 ? 1 : (int32_t)v; return set(how, DS3D_S_CONE_VOLUME, n); }
    HRESULT STDMETHODCALLTYPE SetMaxDistance(D3DVALUE v, DWORD how) override { ds3d_source n = pend; n.max_distance = v; return set(how, DS3D_S_MAX, n); }
    HRESULT STDMETHODCALLTYPE SetMinDistance(D3DVALUE v, DWORD how) override { ds3d_source n = pend; n.min_distance = v; return set(how, DS3D_S_MIN, n); }
    HRESULT STDMETHODCALLTYPE SetMode(DWORD m, DWORD how) override { ds3d_source n = pend; n.mode = DW(m); return set(how, DS3D_S_MODE, n); }
    HRESULT STDMETHODCALLTYPE SetPosition(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { ds3d_source n = pend; n.pos = sv(x, y, z); return set(how, DS3D_S_POS, n); }
    HRESULT STDMETHODCALLTYPE SetVelocity(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { ds3d_source n = pend; n.vel = sv(x, y, z); return set(how, DS3D_S_VEL, n); }
};

/* ---- notify */
struct Notify final : IDirectSoundNotify {
    Buffer *owner;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE SetNotificationPositions(DWORD n, LPCDSBPOSITIONNOTIFY p) override;
};

#define DS_INCOMPLETE_ ((HRESULT)0x08780014)
struct Ks final : KsProperty {
    Buffer *owner;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE Get(REFGUID, ULONG, LPVOID, ULONG, LPVOID, ULONG, PULONG ret) override { if (ret) *ret = 0; return E_PROP_ID_UNSUPPORTED_; }
    HRESULT STDMETHODCALLTYPE Set(REFGUID, ULONG, LPVOID, ULONG, LPVOID, ULONG) override;   /* env_const.tsv */
    HRESULT STDMETHODCALLTYPE QuerySupport(REFGUID, ULONG, PULONG sup) override { if (sup) *sup = 0; return E_PROP_ID_UNSUPPORTED_; }
};

/* ---- listener (on the primary buffer) */
struct Listener final : IDirectSound3DListener {
    Buffer *owner;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    Device *dev();
    HRESULT apply(DWORD how, unsigned mask);
    ds3d_listener &cur();
    ds3d_listener &pend();
    HRESULT set(DWORD how, unsigned mask, const ds3d_listener &n)
    {
        if (DW(how) > DS3D_DEFERRED || !ds3d_listener_valid(&n)) return DSERR_INVALIDPARAM;
        pend() = n;
        return apply(how, mask);
    }
    HRESULT STDMETHODCALLTYPE GetAllParameters(LPDS3DLISTENER p) override
    {
        NN(p);
        if (DW(p->dwSize) != sizeof(DS3DLISTENER)) return DSERR_INVALIDPARAM;
        const ds3d_listener &c = cur();
        p->vPosition = dv(c.pos);
        p->vVelocity = dv(c.vel);
        p->vOrientFront = dv(c.front);
        p->vOrientTop = dv(c.top);
        p->flDistanceFactor = c.distance_factor;
        p->flRolloffFactor = c.rolloff;
        p->flDopplerFactor = c.doppler;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDistanceFactor(D3DVALUE *v) override { NN(v); *v = cur().distance_factor; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetDopplerFactor(D3DVALUE *v) override { NN(v); *v = cur().doppler; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetOrientation(D3DVECTOR *f, D3DVECTOR *t) override { NN(f); NN(t); *f = dv(cur().front); *t = dv(cur().top); return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetPosition(D3DVECTOR *v) override { NN(v); *v = dv(cur().pos); return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetRolloffFactor(D3DVALUE *v) override { NN(v); *v = cur().rolloff; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetVelocity(D3DVECTOR *v) override { NN(v); *v = dv(cur().vel); return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDS3DLISTENER p, DWORD how) override
    {
        NN(p);
        if (DW(p->dwSize) != sizeof(DS3DLISTENER)) return DSERR_INVALIDPARAM;
        ds3d_listener n = pend();
        n.pos = sv(p->vPosition.x, p->vPosition.y, p->vPosition.z);
        n.vel = sv(p->vVelocity.x, p->vVelocity.y, p->vVelocity.z);
        n.front = sv(p->vOrientFront.x, p->vOrientFront.y, p->vOrientFront.z);
        n.top = sv(p->vOrientTop.x, p->vOrientTop.y, p->vOrientTop.z);
        n.distance_factor = p->flDistanceFactor;
        n.rolloff = p->flRolloffFactor;
        n.doppler = p->flDopplerFactor;
        return set(how, DS3D_L_ALL, n);
    }
    HRESULT STDMETHODCALLTYPE SetDistanceFactor(D3DVALUE v, DWORD how) override { ds3d_listener n = pend(); n.distance_factor = v; return set(how, DS3D_L_DISTANCE, n); }
    HRESULT STDMETHODCALLTYPE SetDopplerFactor(D3DVALUE v, DWORD how) override { ds3d_listener n = pend(); n.doppler = v; return set(how, DS3D_L_DOPPLER, n); }
    HRESULT STDMETHODCALLTYPE SetOrientation(D3DVALUE xf, D3DVALUE yf, D3DVALUE zf, D3DVALUE xt, D3DVALUE yt, D3DVALUE zt, DWORD how) override
    {
        ds3d_listener n = pend();
        n.front = sv(xf, yf, zf);
        n.top = sv(xt, yt, zt);
        return set(how, DS3D_L_ORIENT, n);
    }
    HRESULT STDMETHODCALLTYPE SetPosition(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { ds3d_listener n = pend(); n.pos = sv(x, y, z); return set(how, DS3D_L_POS, n); }
    HRESULT STDMETHODCALLTYPE SetRolloffFactor(D3DVALUE v, DWORD how) override { ds3d_listener n = pend(); n.rolloff = v; return set(how, DS3D_L_ROLLOFF, n); }
    HRESULT STDMETHODCALLTYPE SetVelocity(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { ds3d_listener n = pend(); n.vel = sv(x, y, z); return set(how, DS3D_L_VEL, n); }
    HRESULT STDMETHODCALLTYPE CommitDeferredSettings() override;
};

/* ---- buffer */
struct Buffer final : IDirectSoundBuffer8 {
    int refs = 1;
    Device *dev;
    Buffer *dnext = nullptr, *dprev = nullptr;   /* device list */
    DWORD flags;
    bool primary;
    WAVEFORMATEX fmt;
    uint8_t fmt_extra[22];
    mix_format mf;
    Shared *data = nullptr;       /* secondary: the sample memory; primary: only under DSSCL_WRITEPRIMARY */
    mix_voice *voice = nullptr;
    LONG volume = 0, pan = 0;
    DWORD freq = 0;               /* 0: original */
    bool muted = false;           /* focus rule */
    bool primary_playing = false;
    struct Lk { uint8_t *p1, *p2; uint32_t b1, b2; bool used; } locks[16] = {};   /* outstanding Lock()s */
    unsigned lock_rr = 0;
    Buffer3D b3d;
    Notify notify;
    Ks ks;
    Listener listener;
    Effect *fx = nullptr;
    uint32_t nfx = 0;

    Buffer(Device *d, DWORD fl, bool prim);
    ~Buffer();
    void update_gain();
    bool in_focus();
    void focus();                 /* re-evaluate the focus rule (called from the API entry points) */
    void clear_fx();
    HRESULT ensure_primary();
    void drop_primary();
    uint32_t primary_bytes() const { return (mf.rate / 4) * mf.block; }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **out) override;
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE GetCaps(LPDSBCAPS c) override
    {
        NN(c);
        if (DW(c->dwSize) != sizeof(DSBCAPS)) return DSERR_INVALIDPARAM;
        c->dwFlags = (flags & ~(DWORD)DSBCAPS_LOCDEFER) | DSBCAPS_LOCSOFTWARE;
        c->dwBufferBytes = primary ? primary_bytes() : data->size;
        c->dwUnlockTransferRate = 0;   /* software buffers */
        c->dwPlayCpuOverhead = 0;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetCurrentPosition(LPDWORD play, LPDWORD write) override
    {
        focus();
        uint32_t p = voice ? mixer_voice_play_cursor(voice) : 0;
        uint32_t w = voice && mixer_voice_playing(voice) ? mixer_voice_write_cursor(voice) : p;   /* stopped: both at the resume position */
        if (play) *play = p;
        if (write) *write = w;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetFormat(LPWAVEFORMATEX out, DWORD alloc, LPDWORD written) override
    {
        uint32_t need = (uint32_t)sizeof(WAVEFORMATEX) + fmt.cbSize;
        if (!out) {
            NN(written);
            *written = need;
            return DS_OK;
        }
        uint32_t n = DW(alloc) < need ? DW(alloc) : need;
        uint8_t tmp[sizeof(WAVEFORMATEX) + sizeof fmt_extra];
        memcpy(tmp, &fmt, sizeof(WAVEFORMATEX));
        memcpy(tmp + sizeof(WAVEFORMATEX), fmt_extra, sizeof fmt_extra);
        memcpy(out, tmp, n);
        if (written) *written = n;
        return n < need ? DSERR_INVALIDPARAM : DS_OK;   /* a truncated copy is reported, not hidden */
    }
    HRESULT STDMETHODCALLTYPE GetVolume(LPLONG v) override { NN(v); if (!(flags & DSBCAPS_CTRLVOLUME)) return DSERR_CONTROLUNAVAIL; *v = volume; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetPan(LPLONG v) override { NN(v); if (!(flags & DSBCAPS_CTRLPAN)) return DSERR_CONTROLUNAVAIL; *v = pan; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetFrequency(LPDWORD v) override
    {
        NN(v);
        if (!(flags & DSBCAPS_CTRLFREQUENCY)) return DSERR_CONTROLUNAVAIL;
        *v = freq ? freq : fmt.nSamplesPerSec;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetStatus(LPDWORD s) override
    {
        NN(s);
        focus();
        DWORD st = 0;
        if (voice && mixer_voice_playing(voice)) {
            st |= DSBSTATUS_PLAYING;
            if (mixer_voice_looping(voice)) st |= DSBSTATUS_LOOPING;
            if (flags & DSBCAPS_LOCDEFER) st |= DSBSTATUS_LOCSOFTWARE;
        } else if (primary && primary_playing)
            st |= DSBSTATUS_PLAYING | DSBSTATUS_LOOPING;
        *s = st;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Initialize(IDirectSound *, LPCDSBUFFERDESC) override;   /* env_const.tsv */
    HRESULT STDMETHODCALLTYPE Lock(DWORD off, DWORD bytes, LPVOID *p1, LPDWORD b1, LPVOID *p2, LPDWORD b2, DWORD fl) override;
    HRESULT STDMETHODCALLTYPE Play(DWORD, DWORD prio, DWORD fl) override;
    HRESULT STDMETHODCALLTYPE SetCurrentPosition(DWORD pos) override
    {
        if (primary) return DSERR_INVALIDCALL;
        if (DW(pos) >= data->size) return DSERR_INVALIDPARAM;
        mixer_voice_set_cursor(voice, DW(pos));   /* rounded down to a block */
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE SetFormat(LPCWAVEFORMATEX f) override;
    HRESULT STDMETHODCALLTYPE SetVolume(LONG v) override
    {
        if (!(flags & DSBCAPS_CTRLVOLUME)) return DSERR_CONTROLUNAVAIL;
        if (v < DSBVOLUME_MIN || v > DSBVOLUME_MAX) return DSERR_INVALIDPARAM;
        volume = v;
        if (primary) mixer_set_master_gain(mixer_mb_to_gain((int32_t)v));
        else update_gain();
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE SetPan(LONG v) override
    {
        if (!(flags & DSBCAPS_CTRLPAN)) return DSERR_CONTROLUNAVAIL;
        if (v < DSBPAN_LEFT || v > DSBPAN_RIGHT) return DSERR_INVALIDPARAM;
        pan = v;
        update_gain();
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE SetFrequency(DWORD f) override
    {
        if (primary || !(flags & DSBCAPS_CTRLFREQUENCY)) return DSERR_CONTROLUNAVAIL;
        if (DW(f) != DSBFREQUENCY_ORIGINAL && (DW(f) < DSBFREQUENCY_MIN || DW(f) > DSBFREQUENCY_MAX)) return DSERR_INVALIDPARAM;
        freq = f;
        mixer_voice_set_freq(voice, DW(f) ? DW(f) : DW(fmt.nSamplesPerSec));
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Stop() override
    {
        if (primary) primary_playing = false;
        if (voice) mixer_voice_stop(voice);
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Unlock(LPVOID p1, DWORD b1, LPVOID p2, DWORD b2) override;
    HRESULT STDMETHODCALLTYPE Restore() override;   /* env_const.tsv: buffers are never lost */
    HRESULT STDMETHODCALLTYPE SetFX(DWORD n, LPDSEFFECTDESC d, LPDWORD results) override;
    HRESULT STDMETHODCALLTYPE AcquireResources(DWORD fl, DWORD n, LPDWORD results) override
    {
        if (DW(fl) || DW(n) != nfx) return DSERR_INVALIDPARAM;
        if (!(flags & DSBCAPS_LOCDEFER)) return DSERR_INVALIDCALL;
        HRESULT hr = DS_OK;
        for (uint32_t i = 0; i < nfx; i++) {
            if (results) results[i] = fx[i].res;
            if (fx[i].res != DSFXR_LOCSOFTWARE) hr = DS_INCOMPLETE_;
        }
        return hr;
    }
    HRESULT STDMETHODCALLTYPE GetObjectInPath(REFGUID obj, DWORD idx, REFGUID iid, LPVOID *out) override;
};

struct Device final : IDirectSound8 {
    int refs = 1;
    os_lock_t lock = OS_LOCK_INIT;
    Buffer *buffers = nullptr;
    Buffer *primary = nullptr;
    DWORD coop = 0;
    HWND hwnd = nullptr;          /* cooperative-level window (focus rule) */
    bool inited = true;           /* false: created by CoCreateInstance, Initialize not yet called */
    DWORD speaker = DSSPEAKER_COMBINED(DSSPEAKER_STEREO, DSSPEAKER_GEOMETRY_WIDE);
    ds3d_listener lcur, lpend;
    unsigned ldirty = 0;          /* DS3D_L_* groups set DEFERRED and not yet committed */

    Device() { ds3d_listener_default(&lcur); lpend = lcur; }
    void link(Buffer *b)
    {
        os_lock(&lock);
        b->dnext = buffers;
        if (buffers) buffers->dprev = b;
        buffers = b;
        os_unlock(&lock);
    }
    void unlink(Buffer *b)
    {
        os_lock(&lock);
        if (b->dprev) b->dprev->dnext = b->dnext; else buffers = b->dnext;
        if (b->dnext) b->dnext->dprev = b->dprev;
        if (primary == b) primary = nullptr;
        os_unlock(&lock);
    }
    bool owns(const IDirectSoundBuffer *p)
    {
        os_lock(&lock);
        Buffer *b = buffers;
        while (b && static_cast<const IDirectSoundBuffer *>(b) != p) b = b->dnext;
        os_unlock(&lock);
        return b != nullptr;
    }
    /* recompute the 3D voices; commit=true also applies every deferred setting (listener and buffers) */
    void recompute(bool commit)
    {
        os_lock(&lock);
        if (commit) { ds3d_listener_merge(&lcur, &lpend, ldirty); ldirty = 0; }
        for (Buffer *b = buffers; b; b = b->dnext)
            if (!b->primary) {
                if (commit && b->b3d.dirty) { ds3d_source_merge(&b->b3d.cur, &b->b3d.pend, b->b3d.dirty); b->b3d.dirty = 0; }
                b->update_gain();
            }
        os_unlock(&lock);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **out) override
    {
        if (!out) return E_POINTER;
        if (guid_eq(riid, kIID_IUnknown) || guid_eq(riid, IID_IDirectSound) || guid_eq(riid, IID_IDirectSound8)) {
            *out = static_cast<IDirectSound8 *>(this);
            AddRef();
            return S_OK;
        }
        *out = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        int n = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
        if (n == 0) {
            /* buffers outlive their device on Windows only as zombies; stop them */
            os_lock(&lock);
            for (Buffer *b = buffers; b; b = b->dnext) {
                if (b->voice) mixer_voice_stop(b->voice);
                b->dev = nullptr;
            }
            buffers = nullptr;
            os_unlock(&lock);
            delete this;
        }
        return (ULONG)n;
    }
    HRESULT STDMETHODCALLTYPE CreateSoundBuffer(LPCDSBUFFERDESC d, IDirectSoundBuffer **out, LPUNKNOWN outer) override;
    HRESULT STDMETHODCALLTYPE GetCaps(LPDSCAPS c) override
    {
        NN(c);
        if (!inited) return DSERR_UNINITIALIZED;
        if (DW(c->dwSize) != sizeof(DSCAPS)) return DSERR_INVALIDPARAM;
        memset(c, 0, sizeof *c);
        c->dwSize = sizeof(DSCAPS);
        /* software mixing only: an emulated driver; the hardware-buffer and hardware-memory fields are 0 */
        c->dwFlags = DSCAPS_PRIMARYMONO | DSCAPS_PRIMARYSTEREO | DSCAPS_PRIMARY8BIT | DSCAPS_PRIMARY16BIT |
                     DSCAPS_CONTINUOUSRATE | DSCAPS_EMULDRIVER | DSCAPS_SECONDARYMONO | DSCAPS_SECONDARYSTEREO |
                     DSCAPS_SECONDARY8BIT | DSCAPS_SECONDARY16BIT;
        c->dwMinSecondarySampleRate = DSBFREQUENCY_MIN;
        c->dwMaxSecondarySampleRate = DSBFREQUENCY_MAX;
        c->dwPrimaryBuffers = 1;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE DuplicateSoundBuffer(IDirectSoundBuffer *orig, IDirectSoundBuffer **out) override;
    HRESULT STDMETHODCALLTYPE SetCooperativeLevel(HWND w, DWORD level) override
    {
        if (!inited) return DSERR_UNINITIALIZED;
        if (DW(level) < DSSCL_NORMAL || DW(level) > DSSCL_WRITEPRIMARY) return DSERR_INVALIDPARAM;
        if (w && !IsWindow(w)) return DSERR_INVALIDPARAM;
        os_lock(&lock);
        Buffer *p = primary;
        if (p) p->AddRef();
        hwnd = w;
        bool leave = coop == DSSCL_WRITEPRIMARY && DW(level) != DSSCL_WRITEPRIMARY;
        coop = level;
        os_unlock(&lock);
        if (p && leave) p->drop_primary();   /* the application's primary data stops being played */
        if (p) p->Release();
        recompute(false);                    /* the focus window changed */
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Compact() override
    {
        if (!inited) return DSERR_UNINITIALIZED;
        return coop < DSSCL_PRIORITY ? DSERR_PRIOLEVELNEEDED : DS_OK;   /* nothing to compact in software */
    }
    HRESULT STDMETHODCALLTYPE GetSpeakerConfig(LPDWORD s) override { NN(s); if (!inited) return DSERR_UNINITIALIZED; *s = speaker; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetSpeakerConfig(DWORD s) override
    {
        if (!inited) return DSERR_UNINITIALIZED;
        DWORD cfg = s & 0xFF, geo = (s >> 16) & 0xFF;
        if (cfg > DSSPEAKER_7POINT1 || (s & 0xFF00) || (s >> 24) || (geo && (geo < 5 || geo > 180))) return DSERR_INVALIDPARAM;
        speaker = s;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Initialize(LPCGUID g) override
    {
        if (inited) return DSERR_ALREADYINITIALIZED;
        if (g && !guid_eq(*g, DSDEVID_DefaultPlayback) && !guid_eq(*g, DSDEVID_DefaultVoicePlayback_)) return DSERR_NODRIVER;
        fnvnx_audio_start();
        inited = true;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE VerifyCertification(LPDWORD c) override { NN(c); if (!inited) return DSERR_UNINITIALIZED; *c = 1 /* DS_UNCERTIFIED */; return DS_OK; }
};

/* ---- shared sub-interface plumbing */
#define SUB_IUNKNOWN(T)                                                                          \
    HRESULT T::QueryInterface(REFIID r, void **o) { return owner->QueryInterface(r, o); }      \
    ULONG T::AddRef() { return owner->AddRef(); }                                                \
    ULONG T::Release() { return owner->Release(); }
SUB_IUNKNOWN(Buffer3D)
SUB_IUNKNOWN(Notify)
SUB_IUNKNOWN(Ks)
SUB_IUNKNOWN(Listener)

HRESULT Buffer3D::apply(DWORD how, unsigned mask)
{
    if (DW(how) == DS3D_DEFERRED) {
        dirty |= mask;
        return DS_OK;
    }
    ds3d_source_merge(&cur, &pend, mask);   /* only this parameter; other deferred ones wait for the commit */
    dirty &= ~mask;
    owner->update_gain();
    return DS_OK;
}

Device *Listener::dev() { return owner->dev; }
static ds3d_listener g_orphan_listener; /* listener of a primary buffer whose device is gone */
ds3d_listener &Listener::cur() { return owner->dev ? owner->dev->lcur : g_orphan_listener; }
ds3d_listener &Listener::pend() { return owner->dev ? owner->dev->lpend : g_orphan_listener; }
HRESULT Listener::apply(DWORD how, unsigned mask)
{
    if (!dev()) return DSERR_UNINITIALIZED;
    os_lock(&dev()->lock);
    if (DW(how) == DS3D_DEFERRED)
        dev()->ldirty |= mask;
    else {
        ds3d_listener_merge(&dev()->lcur, &dev()->lpend, mask);
        dev()->ldirty &= ~mask;
    }
    os_unlock(&dev()->lock);
    if (DW(how) != DS3D_DEFERRED) dev()->recompute(false);
    return DS_OK;
}
HRESULT Listener::CommitDeferredSettings()
{
    if (!dev()) return DSERR_UNINITIALIZED;
    dev()->recompute(true);
    return DS_OK;
}

HRESULT Notify::SetNotificationPositions(DWORD n, LPCDSBPOSITIONNOTIFY p)
{
    Buffer *b = owner;
    uint32_t cnt = DW(n);
    if (cnt > DSBNOTIFICATIONS_MAX_ || (cnt && !p)) return DSERR_INVALIDPARAM;
    if (mixer_voice_playing(b->voice)) return DSERR_INVALIDCALL;
    mix_notify *m = cnt ? (mix_notify *)malloc(cnt * sizeof *m) : nullptr;
    if (cnt && !m) return DSERR_OUTOFMEMORY;
    for (uint32_t i = 0; i < cnt; i++) {
        uint32_t off = DW(p[i].dwOffset);
        if (!p[i].hEventNotify || (off != DSBPN_OFFSETSTOP && off >= b->data->size)) { free(m); return DSERR_INVALIDPARAM; }
        uint32_t j = i;   /* insertion sort: the mixer fires in cursor order, DSBPN_OFFSETSTOP last */
        for (; j && m[j - 1].offset > off; j--) m[j] = m[j - 1];
        m[j].offset = off;
        m[j].event = (void *)p[i].hEventNotify;
    }
    mixer_voice_set_notify(b->voice, m, cnt, notify_event, b);
    free(m);
    return DS_OK;
}

/* ---- buffer */
Buffer::Buffer(Device *d, DWORD fl, bool prim) : dev(d), flags(fl), primary(prim)
{
    memset(&fmt, 0, sizeof fmt);
    memset(fmt_extra, 0, sizeof fmt_extra);
    memset(&mf, 0, sizeof mf);
    b3d.owner = notify.owner = ks.owner = listener.owner = this;
    ds3d_source_default(&b3d.cur);
    b3d.pend = b3d.cur;
}

Buffer::~Buffer()
{
    clear_fx();   /* first: it detaches the reverb from the voice */
    if (voice) mixer_voice_destroy(voice);
    voice = nullptr;
    if (data && --data->refs == 0) {
        free(data->bytes);
        free(data);
    }
}

static void apply_i3dl2(Buffer *b, const DSFXI3DL2Reverb &p)
{
    fnvnx_i3dl2 q = {(int32_t)p.lRoom, (int32_t)p.lRoomHF, p.flRoomRolloffFactor, p.flDecayTime, p.flDecayHFRatio,
                     (int32_t)p.lReflections, p.flReflectionsDelay, (int32_t)p.lReverb, p.flReverbDelay, p.flDiffusion,
                     p.flDensity, p.flHFReference};
    if (b && b->voice)
        mixer_voice_set_reverb(b->voice, &q);
}

void Buffer::clear_fx()
{
    if (voice)
        mixer_voice_set_reverb(voice, nullptr);
    for (uint32_t i = 0; i < nfx; i++) {
        if (fx[i].i3dl2) fx[i].i3dl2->owner = nullptr;   /* a client's reference outlives the chain */
        if (fx[i].obj) fx[i].obj->Release();
    }
    free(fx);
    fx = nullptr;
    nfx = 0;
}

/* Focus rule: a buffer without DSBCAPS_GLOBALFOCUS is muted while the cooperative-level window is not the
 * foreground window. DSBCAPS_STICKYFOCUS loses focus only to another DirectSound application: never here. */
bool Buffer::in_focus()
{
    if ((flags & (DSBCAPS_GLOBALFOCUS | DSBCAPS_STICKYFOCUS)) || !dev || !dev->hwnd) return true;
    return GetForegroundWindow() == dev->hwnd;
}

void Buffer::focus()
{
    if (!primary && muted == in_focus()) update_gain();
}

void Buffer::update_gain()
{
    if (!voice) return;
    if (primary) {   /* WRITEPRIMARY voice: master volume is the mixer's, pan is this voice's */
        float l, r;
        mixer_pan_to_gains((int32_t)pan, &l, &r);
        mixer_voice_set_gain(voice, l, r);
        return;
    }
    float g = mixer_mb_to_gain((int32_t)volume), l, r;
    float doppler = 1.0f;
    muted = !in_focus();
    if ((flags & DSBCAPS_CTRL3D) && dev) {
        ds3d_result res;
        ds3d_compute(&dev->lcur, &b3d.cur, (flags & DSBCAPS_MUTE3DATMAXDISTANCE) != 0, &res);
        l = res.gain_l;
        r = res.gain_r;
        doppler = res.doppler;
    } else
        mixer_pan_to_gains((int32_t)pan, &l, &r);
    if (muted) g = 0.0f;
    mixer_voice_set_gain(voice, g * l, g * r);
    mixer_voice_set_doppler(voice, doppler);
}

ULONG Buffer::Release()
{
    int n = __atomic_sub_fetch(&refs, 1, __ATOMIC_ACQ_REL);
    if (n == 0) {
        if (dev) dev->unlink(this);
        delete this;
    }
    return (ULONG)n;
}

HRESULT Buffer::QueryInterface(REFIID r, void **o)
{
    if (!o) return E_POINTER;
    *o = nullptr;
    if (guid_eq(r, kIID_IUnknown) || guid_eq(r, IID_IDirectSoundBuffer))
        *o = static_cast<IDirectSoundBuffer *>(this);
    else if (guid_eq(r, IID_IDirectSoundBuffer8) && !primary)
        *o = static_cast<IDirectSoundBuffer8 *>(this);
    else if (guid_eq(r, IID_KsPropertySet))
        *o = static_cast<KsProperty *>(&ks);
    else if (guid_eq(r, IID_IDirectSound3DBuffer) && !primary && (flags & DSBCAPS_CTRL3D))
        *o = static_cast<IDirectSound3DBuffer *>(&b3d);
    else if (guid_eq(r, IID_IDirectSoundNotify) && !primary && (flags & DSBCAPS_CTRLPOSITIONNOTIFY))
        *o = static_cast<IDirectSoundNotify *>(&notify);
    else if (guid_eq(r, IID_IDirectSound3DListener) && primary && (flags & DSBCAPS_CTRL3D))
        *o = static_cast<IDirectSound3DListener *>(&listener);
    if (!*o) return E_NOINTERFACE;
    AddRef();
    return S_OK;
}

/* WAVEFORMATEX -> mixer format; false when DirectSound would answer DSERR_BADFORMAT */
static bool parse_format(const WAVEFORMATEX *w, mix_format *m)
{
    uint16_t tag = w->wFormatTag;
    if (tag == WAVE_FORMAT_EXTENSIBLE) {
        static const uint8_t k_sub_tail[12] = {0x00, 0x00, 0x10, 0x00, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71};
        const uint8_t *x = (const uint8_t *)w + sizeof(WAVEFORMATEX);   /* wValidBitsPerSample, dwChannelMask, SubFormat */
        uint16_t valid;
        uint32_t sub;
        memcpy(&valid, x, 2);
        memcpy(&sub, x + 6, 4);                                         /* SubFormat.Data1 */
        if (w->cbSize < 22 || valid > w->wBitsPerSample || memcmp(x + 10, k_sub_tail, 12)) return false;
        tag = (uint16_t)sub;
    }
    m->rate = DW(w->nSamplesPerSec);
    m->channels = w->nChannels;
    m->bits = w->wBitsPerSample;
    m->block = w->nBlockAlign;
    m->is_float = tag == WAVE_FORMAT_IEEE_FLOAT;
    if (tag != WAVE_FORMAT_PCM && tag != WAVE_FORMAT_IEEE_FLOAT) return false;
    if (m->is_float && m->bits != 32) return false;
    if (!m->is_float && m->bits != 8 && m->bits != 16 && m->bits != 24 && m->bits != 32) return false;
    if (!m->channels || m->channels > 8 || m->block != m->channels * m->bits / 8) return false;
    if (m->rate < DSBFREQUENCY_MIN || m->rate > DSBFREQUENCY_MAX) return false;
    return DW(w->nAvgBytesPerSec) == m->rate * m->block;
}

/* Primary buffer data under DSSCL_WRITEPRIMARY: a looping voice on application-written memory. */
HRESULT Buffer::ensure_primary()
{
    if (voice) return DS_OK;
    uint32_t bytes = primary_bytes();
    Shared *s = (Shared *)calloc(1, sizeof *s);
    uint8_t *mem = (uint8_t *)calloc(1, bytes);
    if (s && mem) {
        if (mf.bits == 8) memset(mem, 0x80, bytes);
        s->refs = 1; s->size = bytes; s->bytes = mem;
        voice = mixer_voice_create(&mf, mem, bytes);
    }
    if (!voice) { free(s); free(mem); return DSERR_OUTOFMEMORY; }
    data = s;
    update_gain();
    if (primary_playing) mixer_voice_play(voice, 1);
    return DS_OK;
}

void Buffer::drop_primary()
{
    if (voice) mixer_voice_destroy(voice);
    voice = nullptr;
    if (data) { free(data->bytes); free(data); }
    data = nullptr;
    memset(locks, 0, sizeof locks);
}

HRESULT Buffer::SetFormat(LPCWAVEFORMATEX f)
{
    if (!primary) return DSERR_INVALIDCALL;
    if (!f) return DSERR_INVALIDPARAM;
    mix_format m;
    if (!parse_format(f, &m) || m.is_float) return DSERR_BADFORMAT;   /* primary formats are PCM */
    if (dev && dev->coop < DSSCL_PRIORITY) return DSERR_PRIOLEVELNEEDED;
    /* the device output stays 48 kHz stereo; the buffer keeps the format that was set (the mixer resamples) */
    memset(fmt_extra, 0, sizeof fmt_extra);
    memcpy(&fmt, f, sizeof fmt);
    fmt.cbSize = f->wFormatTag == WAVE_FORMAT_EXTENSIBLE ? 22 : 0;
    if (fmt.cbSize) memcpy(fmt_extra, (const uint8_t *)f + sizeof(WAVEFORMATEX), 22);
    mf = m;
    drop_primary();   /* WRITEPRIMARY data restarts in the new format */
    return DS_OK;
}

HRESULT Buffer::Lock(DWORD off, DWORD bytes, LPVOID *p1, LPDWORD b1, LPVOID *p2, LPDWORD b2, DWORD fl)
{
    focus();
    if (!p1 || !b1 || (p2 == nullptr) != (b2 == nullptr)) return DSERR_INVALIDPARAM;
    *p1 = nullptr; *b1 = 0;
    if (p2) { *p2 = nullptr; *b2 = 0; }
    if (DW(fl) & ~(DWORD)(DSBLOCK_FROMWRITECURSOR | DSBLOCK_ENTIREBUFFER)) return DSERR_INVALIDPARAM;
    if (primary) {
        if (!dev || dev->coop != DSSCL_WRITEPRIMARY) return DSERR_PRIOLEVELNEEDED;
        HRESULT hr = ensure_primary();
        if (hr != DS_OK) return hr;
    }
    uint32_t size = data->size, o = DW(off), n = DW(bytes);
    if (DW(fl) & DSBLOCK_FROMWRITECURSOR) o = mixer_voice_write_cursor(voice);
    if (DW(fl) & DSBLOCK_ENTIREBUFFER) n = size;
    if (o >= size || n > size || n == 0) return DSERR_INVALIDPARAM;
    uint32_t first = n < size - o ? n : size - o, second = n - first;
    if (!p2) second = 0;   /* no second pointer: the lock ends at the end of the buffer */
    Lk &l = locks[lock_rr++ % 16];   /* the oldest outstanding lock is forgotten when 16 are open */
    l.p1 = data->bytes + o; l.b1 = first; l.p2 = second ? data->bytes : nullptr; l.b2 = second; l.used = true;
    *p1 = l.p1; *b1 = first;
    if (p2) { *p2 = l.p2; *b2 = second; }
    return DS_OK;
}

HRESULT Buffer::Unlock(LPVOID p1, DWORD b1, LPVOID p2, DWORD b2)
{
    focus();
    if (primary && (!dev || dev->coop != DSSCL_WRITEPRIMARY)) return DSERR_PRIOLEVELNEEDED;
    for (Lk &l : locks)
        if (l.used && l.p1 == p1 && l.p2 == p2 && DW(b1) <= l.b1 && DW(b2) <= l.b2) {   /* written bytes <= locked */
            l.used = false;
            return DS_OK;
        }
    return DSERR_INVALIDPARAM;
}

HRESULT Buffer::Play(DWORD reserved, DWORD prio, DWORD fl)
{
    if (reserved || (DW(fl) & ~0x3Fu)) return DSERR_INVALIDPARAM;
    focus();
    if (primary) {
        if (prio || (DW(fl) & ~1u) || !(DW(fl) & DSBPLAY_LOOPING)) return DSERR_INVALIDPARAM;
        primary_playing = true;
        if (voice) mixer_voice_play(voice, 1);
        return DS_OK;
    }
    /* priority and the voice-management flags exist only on DSBCAPS_LOCDEFER buffers */
    if ((prio || (DW(fl) & ~(DWORD)DSBPLAY_LOOPING)) && !(flags & DSBCAPS_LOCDEFER)) return DSERR_INVALIDPARAM;
    if ((DW(fl) & DSBPLAY_LOCHARDWARE) && (DW(fl) & DSBPLAY_LOCSOFTWARE)) return DSERR_INVALIDPARAM;
    if (DW(fl) & DSBPLAY_LOCHARDWARE) return DSERR_CONTROLUNAVAIL;   /* no hardware voices on this device */
    mixer_voice_play(voice, (DW(fl) & DSBPLAY_LOOPING) != 0);
    return DS_OK;
}

HRESULT Buffer::SetFX(DWORD n, LPDSEFFECTDESC d, LPDWORD results)
{
    if (!(flags & DSBCAPS_CTRLFX)) return DSERR_CONTROLUNAVAIL;
    if (mixer_voice_playing(voice)) return DSERR_INVALIDCALL;
    if (!DW(n) != !d) return DSERR_INVALIDPARAM;
    for (uint32_t i = 0; i < DW(n); i++) {
        DWORD f = d[i].dwFlags;
        if (DW(d[i].dwSize) != sizeof(DSEFFECTDESC) || (f & ~(DWORD)(DSFX_LOCHARDWARE | DSFX_LOCSOFTWARE)) ||
            ((f & DSFX_LOCHARDWARE) && (f & DSFX_LOCSOFTWARE)))
            return DSERR_INVALIDPARAM;
    }
    clear_fx();
    if (!DW(n)) return DS_OK;
    fx = (Effect *)calloc(DW(n), sizeof *fx);
    if (!fx) return DSERR_OUTOFMEMORY;
    nfx = DW(n);
    HRESULT hr = DS_OK;
    for (uint32_t i = 0; i < nfx; i++) {
        fx[i].cls = d[i].guidDSFXClass;
        fx[i].res = DSFXR_UNKNOWN;
        if (d[i].dwFlags & DSFX_LOCHARDWARE)
            fx[i].res = DSFXR_FAILED;   /* no hardware effects */
        else if (guid_eq(fx[i].cls, GUID_DSFX_STANDARD_I3DL2REVERB)) {
            fx[i].i3dl2 = new (std::nothrow) I3DL2();
            fx[i].obj = fx[i].i3dl2;
            if (fx[i].i3dl2) {
                fx[i].i3dl2->owner = this;
                fx[i].i3dl2->p = k_i3dl2_presets[0];
                apply_i3dl2(this, fx[i].i3dl2->p);
            }
        } else
            for (const FxClass &c : k_fx_classes)
                if (guid_eq(fx[i].cls, *c.cls)) fx[i].obj = c.make();
        if (fx[i].obj) fx[i].res = DSFXR_LOCSOFTWARE;
        if (fx[i].res != DSFXR_LOCSOFTWARE) hr = DS_INCOMPLETE_;
        if (results) results[i] = fx[i].res;
    }
    return hr;
}

HRESULT Buffer::GetObjectInPath(REFGUID obj, DWORD idx, REFGUID iid, LPVOID *out)
{
    NN(out);
    *out = nullptr;
    uint32_t seen = 0;
    for (uint32_t i = 0; i < nfx; i++) {
        if (!guid_eq(obj, GUID_All_Objects) && !guid_eq(obj, fx[i].cls)) continue;
        if (seen++ != DW(idx)) continue;
        return fx[i].obj ? fx[i].obj->QueryInterface(iid, out) : E_NOINTERFACE;
    }
    return DSERR_OBJECTNOTFOUND;
}

HRESULT Device::CreateSoundBuffer(LPCDSBUFFERDESC d, IDirectSoundBuffer **out, LPUNKNOWN outer)
{
    NN(out);
    *out = nullptr;
    if (!inited) return DSERR_UNINITIALIZED;
    if (outer) return DSERR_NOAGGREGATION;
    if (!d || (DW(d->dwSize) != sizeof(DSBUFFERDESC) && DW(d->dwSize) != offsetof(DSBUFFERDESC, guid3DAlgorithm)))
        return DSERR_INVALIDPARAM;
    if (!coop) return DSERR_PRIOLEVELNEEDED;
    DWORD fl = d->dwFlags;
    if (fl & ~(DWORD)0x000FC3FF) return DSERR_INVALIDPARAM;   /* unknown DSBCAPS bits */
    if ((fl & DSBCAPS_CTRL3D) && (fl & DSBCAPS_CTRLPAN)) return DSERR_INVALIDPARAM;
    if ((fl & DSBCAPS_MUTE3DATMAXDISTANCE) && !(fl & DSBCAPS_CTRL3D)) return DSERR_INVALIDPARAM;
    if ((fl & DSBCAPS_LOCHARDWARE) && (fl & DSBCAPS_LOCSOFTWARE)) return DSERR_INVALIDPARAM;
    if (fl & DSBCAPS_LOCHARDWARE) return DSERR_CONTROLUNAVAIL;   /* no hardware buffers */

    if (fl & DSBCAPS_PRIMARYBUFFER) {
        if (d->dwBufferBytes || d->lpwfxFormat ||
            (fl & (DSBCAPS_CTRLFREQUENCY | DSBCAPS_CTRLFX | DSBCAPS_CTRLPOSITIONNOTIFY | DSBCAPS_STATIC | DSBCAPS_LOCDEFER |
                   DSBCAPS_MUTE3DATMAXDISTANCE)))
            return DSERR_INVALIDPARAM;
        os_lock(&lock);
        Buffer *p = primary;
        if (p) p->AddRef();
        os_unlock(&lock);
        if (p) { *out = p; return DS_OK; }
        p = new (std::nothrow) Buffer(this, fl, true);
        if (!p) return DSERR_OUTOFMEMORY;
        WAVEFORMATEX w = {WAVE_FORMAT_PCM, 2, 22050, 22050 * 4, 4, 16, 0};   /* DirectSound's default primary format */
        p->fmt = w;
        parse_format(&w, &p->mf);
        link(p);
        os_lock(&lock);
        primary = p;
        os_unlock(&lock);
        *out = p;
        return DS_OK;
    }

    HRESULT result = DS_OK;
    if (DW(d->dwSize) == sizeof(DSBUFFERDESC) && !guid_eq(d->guid3DAlgorithm, DS3DALG_DEFAULT)) {
        const GUID &a = d->guid3DAlgorithm;
        if (!(fl & DSBCAPS_CTRL3D)) return DSERR_INVALIDPARAM;
        if (guid_eq(a, DS3DALG_HRTF_FULL_) || guid_eq(a, DS3DALG_HRTF_LIGHT_))
            result = DS_NO_VIRTUALIZATION;   /* no HRTF here: the default algorithm is used, as the SDK reports */
        else if (!guid_eq(a, DS3DALG_NO_VIRTUALIZATION))
            return DSERR_INVALIDPARAM;
    }
    const WAVEFORMATEX *w = d->lpwfxFormat;
    if (!w) return DSERR_INVALIDPARAM;
    mix_format mf;
    if (!parse_format(w, &mf)) return DSERR_BADFORMAT;
    if ((fl & DSBCAPS_CTRL3D) && mf.channels != 1) return DSERR_INVALIDPARAM;
    uint32_t bytes = DW(d->dwBufferBytes) / mf.block * mf.block;   /* whole blocks */
    if (bytes < DSBSIZE_MIN || bytes > DSBSIZE_MAX) return DSERR_INVALIDPARAM;
    if ((fl & DSBCAPS_CTRLFX) && (uint64_t)bytes * 1000 < (uint64_t)DSBSIZE_FX_MIN * DW(w->nAvgBytesPerSec))
        return DSERR_BUFFERTOOSMALL;

    Buffer *b = new (std::nothrow) Buffer(this, fl, false);
    Shared *s = (Shared *)calloc(1, sizeof *s);
    uint8_t *mem = (uint8_t *)calloc(1, bytes);
    if (!b || !s || !mem) { delete b; free(s); free(mem); return DSERR_OUTOFMEMORY; }
    /* silence: 8-bit PCM is unsigned */
    if (mf.bits == 8) memset(mem, 0x80, bytes);
    s->refs = 1; s->size = bytes; s->bytes = mem;
    b->data = s;
    memcpy(&b->fmt, w, sizeof(WAVEFORMATEX));
    b->fmt.cbSize = w->wFormatTag == WAVE_FORMAT_EXTENSIBLE ? 22 : 0;
    if (b->fmt.cbSize) memcpy(b->fmt_extra, (const uint8_t *)w + sizeof(WAVEFORMATEX), 22);
    b->mf = mf;
    b->voice = mixer_voice_create(&mf, mem, bytes);
    if (!b->voice) { delete b; return DSERR_OUTOFMEMORY; }
    link(b);
    b->update_gain();
    *out = b;
    return result;
}

HRESULT Device::DuplicateSoundBuffer(IDirectSoundBuffer *orig, IDirectSoundBuffer **out)
{
    NN(out);
    *out = nullptr;
    if (!inited) return DSERR_UNINITIALIZED;
    if (!orig || !owns(orig)) return DSERR_INVALIDPARAM;   /* only this device's buffers can be duplicated */
    Buffer *o = static_cast<Buffer *>(orig);
    if (o->primary || (o->flags & DSBCAPS_CTRLFX)) return DSERR_INVALIDCALL;
    Buffer *b = new (std::nothrow) Buffer(this, o->flags, false);
    if (!b) return DSERR_OUTOFMEMORY;
    b->data = o->data;
    b->data->refs++;
    b->fmt = o->fmt;
    memcpy(b->fmt_extra, o->fmt_extra, sizeof b->fmt_extra);
    b->mf = o->mf;
    b->volume = o->volume;
    b->pan = o->pan;
    b->freq = o->freq;
    b->b3d.cur = o->b3d.cur;
    b->b3d.pend = o->b3d.pend;
    b->b3d.dirty = o->b3d.dirty;
    b->voice = mixer_voice_create(&b->mf, b->data->bytes, b->data->size);
    if (!b->voice) { delete b; return DSERR_OUTOFMEMORY; }
    if (b->freq) mixer_voice_set_freq(b->voice, DW(b->freq));
    link(b);
    b->update_gain();
    *out = b;
    return DS_OK;
}

#include "dsound.env_const.gen.inc"   /* Ks::Set, Buffer::Initialize, Buffer::Restore: manifest/env_const.tsv */
} // namespace

/* One output device: NULL, DSDEVID_DefaultPlayback / DefaultVoicePlayback and the enumerated GUIDs name it. */
static bool known_device(LPCGUID g)
{
    return !g || guid_eq(*g, DSDEVID_DefaultPlayback) || guid_eq(*g, DSDEVID_DefaultVoicePlayback_);
}

extern "C" HRESULT WINAPI DirectSoundCreate8(LPCGUID guid, LPDIRECTSOUND8 *out, LPUNKNOWN outer)
{
    if (!out) return DSERR_INVALIDPARAM;
    *out = nullptr;
    if (outer) return DSERR_NOAGGREGATION;
    if (!known_device(guid)) return DSERR_NODRIVER;
    fnvnx_audio_start();
    Device *d = new (std::nothrow) Device();
    if (!d) return DSERR_OUTOFMEMORY;
    *out = d;
    return DS_OK;
}

extern "C" HRESULT WINAPI DirectSoundCreate(LPCGUID guid, LPDIRECTSOUND *out, LPUNKNOWN outer)
{
    if (!out) return DSERR_INVALIDPARAM;
    LPDIRECTSOUND8 d8 = nullptr;
    HRESULT hr = DirectSoundCreate8(guid, &d8, outer);
    *out = d8;   /* NULL on failure */
    return hr;
}

extern "C" HRESULT WINAPI DirectSoundEnumerateA(LPDSENUMCALLBACKA cb, LPVOID ctx)
{
    if (!cb) return DSERR_INVALIDPARAM;
    if (cb(nullptr, "Primary Sound Driver", "", ctx))
        cb((LPGUID)&DSDEVID_DefaultPlayback, "Nintendo Switch audio", "audren", ctx);
    return DS_OK;
}

/* CoCreateInstance(CLSID_DirectSound8) */
extern "C" HRESULT fnvnx_dsound_com_factory(const GUID *iid, void **out)
{
    LPDIRECTSOUND8 d = nullptr;
    HRESULT hr = DirectSoundCreate8(nullptr, &d, nullptr);
    if (hr != DS_OK) return hr;
    static_cast<Device *>(d)->inited = false;   /* a COM-created device needs IDirectSound::Initialize */
    hr = d->QueryInterface(*iid, out);
    d->Release();
    return hr;
}
