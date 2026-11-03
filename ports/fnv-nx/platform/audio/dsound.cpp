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
 * FX: SetFX/GetObjectInPath accept effects and keep their parameters; no DSP runs yet (open item).
 */
#include <dsound.h>
#include "fnvnx_win32_ext.h"

#include "backend.h"
#include "ds3d.h"
#include "mixer.h"
#include "../os/os.h"

#include <math.h>
#include <stddef.h>
#include <new>
#include <stdlib.h>
#include <string.h>

#define DW(x) ((uint32_t)(x))

namespace {

bool guid_eq(const GUID &a, const GUID &b) { return !memcmp(&a, &b, sizeof(GUID)); }

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

/* ---- FX parameter objects (no DSP yet) */
struct I3DL2 final : IDirectSoundFXI3DL2Reverb8 {
    Buffer *owner;
    DSFXI3DL2Reverb p;
    DWORD preset = 0;
    LONG quality = 2;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDSFXI3DL2Reverb in) override { if (!in) return E_POINTER; p = *in; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetAllParameters(LPDSFXI3DL2Reverb out) override { if (!out) return E_POINTER; *out = p; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetPreset(DWORD d) override { preset = d; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetPreset(LPDWORD d) override { if (!d) return E_POINTER; *d = preset; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetQuality(LONG q) override { quality = q; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetQuality(LONG *q) override { if (!q) return E_POINTER; *q = quality; return DS_OK; }
};
struct Waves final : IDirectSoundFXWavesReverb8 {
    Buffer *owner;
    DSFXWavesReverb p;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDSFXWavesReverb in) override { if (!in) return E_POINTER; p = *in; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetAllParameters(LPDSFXWavesReverb out) override { if (!out) return E_POINTER; *out = p; return DS_OK; }
};

struct Effect {
    GUID cls;
    I3DL2 *i3dl2;
    Waves *waves;
};

/* ---- 3D buffer */
struct Buffer3D final : IDirectSound3DBuffer {
    Buffer *owner;
    ds3d_source cur, pend;
    bool dirty = false;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT apply(DWORD how);
    HRESULT STDMETHODCALLTYPE GetAllParameters(LPDS3DBUFFER p) override
    {
        if (!p) return E_POINTER;
        if (DW(p->dwSize) < sizeof(DS3DBUFFER)) return DSERR_INVALIDPARAM;
        p->vPosition = {cur.pos.x, cur.pos.y, cur.pos.z};
        p->vVelocity = {cur.vel.x, cur.vel.y, cur.vel.z};
        p->dwInsideConeAngle = cur.cone_inside;
        p->dwOutsideConeAngle = cur.cone_outside;
        p->vConeOrientation = {cur.cone_orient.x, cur.cone_orient.y, cur.cone_orient.z};
        p->lConeOutsideVolume = cur.cone_outside_volume;
        p->flMinDistance = cur.min_distance;
        p->flMaxDistance = cur.max_distance;
        p->dwMode = cur.mode;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetConeAngles(LPDWORD a, LPDWORD b) override { if (a) *a = cur.cone_inside; if (b) *b = cur.cone_outside; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetConeOrientation(D3DVECTOR *v) override { if (!v) return E_POINTER; *v = {cur.cone_orient.x, cur.cone_orient.y, cur.cone_orient.z}; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetConeOutsideVolume(LPLONG v) override { if (!v) return E_POINTER; *v = cur.cone_outside_volume; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetMaxDistance(D3DVALUE *v) override { if (!v) return E_POINTER; *v = cur.max_distance; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetMinDistance(D3DVALUE *v) override { if (!v) return E_POINTER; *v = cur.min_distance; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetMode(LPDWORD v) override { if (!v) return E_POINTER; *v = cur.mode; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetPosition(D3DVECTOR *v) override { if (!v) return E_POINTER; *v = {cur.pos.x, cur.pos.y, cur.pos.z}; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetVelocity(D3DVECTOR *v) override { if (!v) return E_POINTER; *v = {cur.vel.x, cur.vel.y, cur.vel.z}; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDS3DBUFFER p, DWORD how) override
    {
        if (!p) return E_POINTER;
        if (DW(p->dwSize) < sizeof(DS3DBUFFER)) return DSERR_INVALIDPARAM;
        pend.pos = {p->vPosition.x, p->vPosition.y, p->vPosition.z};
        pend.vel = {p->vVelocity.x, p->vVelocity.y, p->vVelocity.z};
        pend.cone_inside = DW(p->dwInsideConeAngle);
        pend.cone_outside = DW(p->dwOutsideConeAngle);
        pend.cone_orient = {p->vConeOrientation.x, p->vConeOrientation.y, p->vConeOrientation.z};
        pend.cone_outside_volume = (int32_t)p->lConeOutsideVolume;
        pend.min_distance = p->flMinDistance;
        pend.max_distance = p->flMaxDistance;
        pend.mode = DW(p->dwMode);
        return apply(how);
    }
    HRESULT STDMETHODCALLTYPE SetConeAngles(DWORD a, DWORD b, DWORD how) override
    {
        if (DW(a) > 360 || DW(b) > 360) return DSERR_INVALIDPARAM;
        pend.cone_inside = DW(a); pend.cone_outside = DW(b); return apply(how);
    }
    HRESULT STDMETHODCALLTYPE SetConeOrientation(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { pend.cone_orient = {x, y, z}; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetConeOutsideVolume(LONG v, DWORD how) override
    {
        if (v < DSBVOLUME_MIN || v > DSBVOLUME_MAX) return DSERR_INVALIDPARAM;
        pend.cone_outside_volume = (int32_t)v; return apply(how);
    }
    HRESULT STDMETHODCALLTYPE SetMaxDistance(D3DVALUE v, DWORD how) override { if (!(v > 0.0f)) return DSERR_INVALIDPARAM; pend.max_distance = v; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetMinDistance(D3DVALUE v, DWORD how) override { if (!(v > 0.0f)) return DSERR_INVALIDPARAM; pend.min_distance = v; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetMode(DWORD m, DWORD how) override { if (DW(m) > DS3DMODE_DISABLE) return DSERR_INVALIDPARAM; pend.mode = DW(m); return apply(how); }
    HRESULT STDMETHODCALLTYPE SetPosition(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { pend.pos = {x, y, z}; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetVelocity(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { pend.vel = {x, y, z}; return apply(how); }
};

/* ---- notify */
struct Notify final : IDirectSoundNotify {
    Buffer *owner;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE SetNotificationPositions(DWORD n, LPCDSBPOSITIONNOTIFY p) override;
};

/* ---- listener (on the primary buffer) */
struct Listener final : IDirectSound3DListener {
    Buffer *owner;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    Device *dev();
    HRESULT apply(DWORD how);
    ds3d_listener &cur();
    ds3d_listener &pend();
    HRESULT STDMETHODCALLTYPE GetAllParameters(LPDS3DLISTENER p) override
    {
        if (!p) return E_POINTER;
        if (DW(p->dwSize) < sizeof(DS3DLISTENER)) return DSERR_INVALIDPARAM;
        ds3d_listener &c = cur();
        p->vPosition = {c.pos.x, c.pos.y, c.pos.z};
        p->vVelocity = {c.vel.x, c.vel.y, c.vel.z};
        p->vOrientFront = {c.front.x, c.front.y, c.front.z};
        p->vOrientTop = {c.top.x, c.top.y, c.top.z};
        p->flDistanceFactor = c.distance_factor;
        p->flRolloffFactor = c.rolloff;
        p->flDopplerFactor = c.doppler;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDistanceFactor(D3DVALUE *v) override { if (!v) return E_POINTER; *v = cur().distance_factor; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetDopplerFactor(D3DVALUE *v) override { if (!v) return E_POINTER; *v = cur().doppler; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetOrientation(D3DVECTOR *f, D3DVECTOR *t) override
    {
        if (!f || !t) return E_POINTER;
        *f = {cur().front.x, cur().front.y, cur().front.z};
        *t = {cur().top.x, cur().top.y, cur().top.z};
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetPosition(D3DVECTOR *v) override { if (!v) return E_POINTER; *v = {cur().pos.x, cur().pos.y, cur().pos.z}; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetRolloffFactor(D3DVALUE *v) override { if (!v) return E_POINTER; *v = cur().rolloff; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetVelocity(D3DVECTOR *v) override { if (!v) return E_POINTER; *v = {cur().vel.x, cur().vel.y, cur().vel.z}; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDS3DLISTENER p, DWORD how) override
    {
        if (!p) return E_POINTER;
        if (DW(p->dwSize) < sizeof(DS3DLISTENER)) return DSERR_INVALIDPARAM;
        ds3d_listener &n = pend();
        n.pos = {p->vPosition.x, p->vPosition.y, p->vPosition.z};
        n.vel = {p->vVelocity.x, p->vVelocity.y, p->vVelocity.z};
        n.front = {p->vOrientFront.x, p->vOrientFront.y, p->vOrientFront.z};
        n.top = {p->vOrientTop.x, p->vOrientTop.y, p->vOrientTop.z};
        n.distance_factor = p->flDistanceFactor;
        n.rolloff = p->flRolloffFactor;
        n.doppler = p->flDopplerFactor;
        return apply(how);
    }
    HRESULT STDMETHODCALLTYPE SetDistanceFactor(D3DVALUE v, DWORD how) override { if (!(v > 0.0f)) return DSERR_INVALIDPARAM; pend().distance_factor = v; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetDopplerFactor(D3DVALUE v, DWORD how) override { if (v < 0.0f || v > 10.0f) return DSERR_INVALIDPARAM; pend().doppler = v; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetOrientation(D3DVALUE xf, D3DVALUE yf, D3DVALUE zf, D3DVALUE xt, D3DVALUE yt, D3DVALUE zt, DWORD how) override
    {
        pend().front = {xf, yf, zf};
        pend().top = {xt, yt, zt};
        return apply(how);
    }
    HRESULT STDMETHODCALLTYPE SetPosition(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { pend().pos = {x, y, z}; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetRolloffFactor(D3DVALUE v, DWORD how) override { if (v < 0.0f || v > 10.0f) return DSERR_INVALIDPARAM; pend().rolloff = v; return apply(how); }
    HRESULT STDMETHODCALLTYPE SetVelocity(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD how) override { pend().vel = {x, y, z}; return apply(how); }
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
    Shared *data = nullptr;
    mix_voice *voice = nullptr;
    LONG volume = 0, pan = 0;
    DWORD freq = 0;               /* 0: original */
    Buffer3D b3d;
    Notify notify;
    Listener listener;
    Effect *fx = nullptr;
    uint32_t nfx = 0;

    Buffer(Device *d, DWORD fl, bool prim);
    ~Buffer();
    void update_gain();
    void clear_fx();

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **out) override;
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)__atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE GetCaps(LPDSBCAPS c) override
    {
        if (!c) return E_POINTER;
        if (DW(c->dwSize) < sizeof(DSBCAPS)) return DSERR_INVALIDPARAM;
        c->dwFlags = (flags & ~(DWORD)DSBCAPS_LOCDEFER) | DSBCAPS_LOCSOFTWARE;
        c->dwBufferBytes = data ? data->size : (DWORD)(FNVNX_AUDIO_PERIOD * 4);
        c->dwUnlockTransferRate = 0;
        c->dwPlayCpuOverhead = 0;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetCurrentPosition(LPDWORD play, LPDWORD write) override
    {
        uint32_t p = voice ? mixer_voice_play_cursor(voice) : 0, w = voice ? mixer_voice_write_cursor(voice) : 0;
        if (play) *play = p;
        if (write) *write = w;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetFormat(LPWAVEFORMATEX out, DWORD alloc, LPDWORD written) override
    {
        uint32_t need = (uint32_t)sizeof(WAVEFORMATEX) + fmt.cbSize;
        if (!out) {
            if (!written) return DSERR_INVALIDPARAM;
            *written = need;
            return DS_OK;
        }
        if (DW(alloc) < need) {
            if (DW(alloc) < sizeof(WAVEFORMATEX)) return DSERR_INVALIDPARAM;
            need = sizeof(WAVEFORMATEX);
        }
        memcpy(out, &fmt, sizeof(WAVEFORMATEX));
        if (need > sizeof(WAVEFORMATEX))
            memcpy((uint8_t *)out + sizeof(WAVEFORMATEX), fmt_extra, fmt.cbSize);
        if (written) *written = need;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetVolume(LPLONG v) override { if (!v) return DSERR_INVALIDPARAM; if (!(flags & DSBCAPS_CTRLVOLUME)) return DSERR_CONTROLUNAVAIL; *v = volume; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetPan(LPLONG v) override { if (!v) return DSERR_INVALIDPARAM; if (!(flags & DSBCAPS_CTRLPAN)) return DSERR_CONTROLUNAVAIL; *v = pan; return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetFrequency(LPDWORD v) override
    {
        if (!v) return DSERR_INVALIDPARAM;
        if (!(flags & DSBCAPS_CTRLFREQUENCY)) return DSERR_CONTROLUNAVAIL;
        *v = freq ? freq : fmt.nSamplesPerSec;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetStatus(LPDWORD s) override
    {
        if (!s) return DSERR_INVALIDPARAM;
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
    HRESULT STDMETHODCALLTYPE Initialize(IDirectSound *, LPCDSBUFFERDESC) override { return DSERR_ALREADYINITIALIZED; }
    HRESULT STDMETHODCALLTYPE Lock(DWORD off, DWORD bytes, LPVOID *p1, LPDWORD b1, LPVOID *p2, LPDWORD b2, DWORD fl) override
    {
        if (!p1 || !b1) return DSERR_INVALIDPARAM;
        if (primary || !data) return DSERR_PRIOLEVELNEEDED;
        uint32_t size = data->size, o = DW(off), n = DW(bytes);
        if (DW(fl) & DSBLOCK_FROMWRITECURSOR) o = mixer_voice_write_cursor(voice);
        if (DW(fl) & DSBLOCK_ENTIREBUFFER) n = size;
        if (o >= size || n > size || n == 0) return DSERR_INVALIDPARAM;
        uint32_t first = n < size - o ? n : size - o;
        *p1 = data->bytes + o;
        *b1 = first;
        if (p2) *p2 = (n > first) ? data->bytes : nullptr;
        if (b2) *b2 = n - first;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Play(DWORD, DWORD prio, DWORD fl) override
    {
        if (primary) {
            if (!(DW(fl) & DSBPLAY_LOOPING)) return DSERR_INVALIDPARAM;
            primary_playing = true;
            return DS_OK;
        }
        if (prio && !(flags & DSBCAPS_LOCDEFER)) return DSERR_INVALIDCALL;
        mixer_voice_play(voice, (DW(fl) & DSBPLAY_LOOPING) != 0);
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE SetCurrentPosition(DWORD pos) override
    {
        if (primary) return DSERR_INVALIDCALL;
        if (DW(pos) >= data->size) return DSERR_INVALIDPARAM;
        mixer_voice_set_cursor(voice, DW(pos));
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
        if (primary) { primary_playing = false; return DS_OK; }
        mixer_voice_stop(voice);
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Unlock(LPVOID, DWORD, LPVOID, DWORD) override { return primary ? DSERR_PRIOLEVELNEEDED : DS_OK; }
    HRESULT STDMETHODCALLTYPE Restore() override { return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetFX(DWORD n, LPDSEFFECTDESC d, LPDWORD results) override;
    HRESULT STDMETHODCALLTYPE AcquireResources(DWORD, DWORD n, LPDWORD results) override
    {
        if (!(flags & DSBCAPS_LOCDEFER)) return DSERR_INVALIDCALL;
        for (uint32_t i = 0; results && i < DW(n) && i < nfx; i++) results[i] = DSFXR_LOCSOFTWARE;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE GetObjectInPath(REFGUID obj, DWORD idx, REFGUID iid, LPVOID *out) override;

    bool primary_playing = false;
};

struct Device final : IDirectSound8 {
    int refs = 1;
    os_lock_t lock = OS_LOCK_INIT;
    Buffer *buffers = nullptr;
    Buffer *primary = nullptr;
    DWORD coop = 0;
    DWORD speaker = DSSPEAKER_COMBINED(DSSPEAKER_STEREO, DSSPEAKER_GEOMETRY_WIDE);
    ds3d_listener lcur, lpend;

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
    /* recompute the 3D voices; commit=true also applies every deferred setting */
    void recompute(bool commit)
    {
        os_lock(&lock);
        if (commit) lcur = lpend;
        for (Buffer *b = buffers; b; b = b->dnext)
            if (!b->primary && (b->flags & DSBCAPS_CTRL3D)) {
                if (commit && b->b3d.dirty) { b->b3d.cur = b->b3d.pend; b->b3d.dirty = false; }
                b->update_gain();
            }
        os_unlock(&lock);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **out) override
    {
        if (!out) return E_POINTER;
        if (guid_eq(riid, IID_IUnknown_) || guid_eq(riid, IID_IDirectSound) || guid_eq(riid, IID_IDirectSound8)) {
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
        if (!c) return E_POINTER;
        if (DW(c->dwSize) < sizeof(DSCAPS)) return DSERR_INVALIDPARAM;
        DWORD sz = c->dwSize;
        memset(c, 0, sizeof *c);
        c->dwSize = sz;
        c->dwFlags = DSCAPS_PRIMARYMONO | DSCAPS_PRIMARYSTEREO | DSCAPS_PRIMARY8BIT | DSCAPS_PRIMARY16BIT |
                     DSCAPS_CONTINUOUSRATE | DSCAPS_CERTIFIED | DSCAPS_SECONDARYMONO | DSCAPS_SECONDARYSTEREO |
                     DSCAPS_SECONDARY8BIT | DSCAPS_SECONDARY16BIT;
        c->dwMinSecondarySampleRate = DSBFREQUENCY_MIN;
        c->dwMaxSecondarySampleRate = DSBFREQUENCY_MAX;
        c->dwPrimaryBuffers = 1;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE DuplicateSoundBuffer(IDirectSoundBuffer *orig, IDirectSoundBuffer **out) override;
    HRESULT STDMETHODCALLTYPE SetCooperativeLevel(HWND, DWORD level) override
    {
        if (DW(level) < DSSCL_NORMAL || DW(level) > DSSCL_WRITEPRIMARY) return DSERR_INVALIDPARAM;
        coop = level;
        return DS_OK;
    }
    HRESULT STDMETHODCALLTYPE Compact() override { return DS_OK; }
    HRESULT STDMETHODCALLTYPE GetSpeakerConfig(LPDWORD s) override { if (!s) return DSERR_INVALIDPARAM; *s = speaker; return DS_OK; }
    HRESULT STDMETHODCALLTYPE SetSpeakerConfig(DWORD s) override { speaker = s; return DS_OK; }
    HRESULT STDMETHODCALLTYPE Initialize(LPCGUID) override { return DS_OK; }
    HRESULT STDMETHODCALLTYPE VerifyCertification(LPDWORD c) override { if (!c) return DSERR_INVALIDPARAM; *c = 0 /* DS_CERTIFIED */; return DS_OK; }

    static const GUID IID_IUnknown_;
};
const GUID Device::IID_IUnknown_ = {0x00000000, 0x0000, 0x0000, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};

/* ---- shared sub-interface plumbing */
#define SUB_IUNKNOWN(T)                                                                          \
    HRESULT T::QueryInterface(REFIID r, void **o) { return owner->QueryInterface(r, o); }      \
    ULONG T::AddRef() { return owner->AddRef(); }                                                \
    ULONG T::Release() { return owner->Release(); }
SUB_IUNKNOWN(Buffer3D)
SUB_IUNKNOWN(Notify)
SUB_IUNKNOWN(Listener)
SUB_IUNKNOWN(I3DL2)
SUB_IUNKNOWN(Waves)

HRESULT Buffer3D::apply(DWORD how)
{
    if (DW(how) == DS3D_DEFERRED) {
        dirty = true;
        return DS_OK;
    }
    cur = pend;
    dirty = false;
    owner->update_gain();
    return DS_OK;
}

Device *Listener::dev() { return owner->dev; }
static ds3d_listener g_orphan_listener; /* listener of a primary buffer whose device is gone */
ds3d_listener &Listener::cur() { return owner->dev ? owner->dev->lcur : g_orphan_listener; }
ds3d_listener &Listener::pend() { return owner->dev ? owner->dev->lpend : g_orphan_listener; }
HRESULT Listener::apply(DWORD how)
{
    if (!dev()) return DSERR_UNINITIALIZED;
    if (DW(how) == DS3D_DEFERRED) return DS_OK;
    /* immediate listener change: only the listener's pending values are applied, 3D buffers keep theirs */
    os_lock(&dev()->lock);
    dev()->lcur = dev()->lpend;
    os_unlock(&dev()->lock);
    dev()->recompute(false);
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
    if (DW(n) && !p) return DSERR_INVALIDPARAM;
    if (mixer_voice_playing(b->voice)) return DSERR_INVALIDCALL;
    mix_notify *m = DW(n) ? (mix_notify *)malloc(DW(n) * sizeof *m) : nullptr;
    if (DW(n) && !m) return DSERR_OUTOFMEMORY;
    for (uint32_t i = 0; i < DW(n); i++) {
        uint32_t off = DW(p[i].dwOffset);
        if (off != DSBPN_OFFSETSTOP && off >= b->data->size) { free(m); return DSERR_INVALIDPARAM; }
        m[i].offset = off;
        m[i].event = (void *)p[i].hEventNotify;
    }
    mixer_voice_set_notify(b->voice, m, DW(n), notify_event, b);
    free(m);
    return DS_OK;
}

/* ---- buffer */
Buffer::Buffer(Device *d, DWORD fl, bool prim) : dev(d), flags(fl), primary(prim)
{
    memset(&fmt, 0, sizeof fmt);
    memset(fmt_extra, 0, sizeof fmt_extra);
    memset(&mf, 0, sizeof mf);
    b3d.owner = notify.owner = listener.owner = this;
    ds3d_source_default(&b3d.cur);
    b3d.pend = b3d.cur;
}

Buffer::~Buffer()
{
    if (voice) mixer_voice_destroy(voice);
    if (data && --data->refs == 0) {
        free(data->bytes);
        free(data);
    }
    clear_fx();
}

void Buffer::clear_fx()
{
    for (uint32_t i = 0; i < nfx; i++) {
        delete fx[i].i3dl2;
        delete fx[i].waves;
    }
    free(fx);
    fx = nullptr;
    nfx = 0;
}

void Buffer::update_gain()
{
    if (!voice || primary) return;
    float g = mixer_mb_to_gain((int32_t)volume), l, r;
    float doppler = 1.0f;
    if ((flags & DSBCAPS_CTRL3D) && dev) {
        ds3d_result res;
        ds3d_compute(&dev->lcur, &b3d.cur, (flags & DSBCAPS_MUTE3DATMAXDISTANCE) != 0, &res);
        l = res.gain_l;
        r = res.gain_r;
        doppler = res.doppler;
    } else
        mixer_pan_to_gains((int32_t)pan, &l, &r);
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
    if (guid_eq(r, Device::IID_IUnknown_) || guid_eq(r, IID_IDirectSoundBuffer))
        *o = static_cast<IDirectSoundBuffer *>(this);
    else if (guid_eq(r, IID_IDirectSoundBuffer8) && !primary)
        *o = static_cast<IDirectSoundBuffer8 *>(this);
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

/* WAVEFORMATEX -> mixer format; 0 if unsupported */
static bool parse_format(const WAVEFORMATEX *w, mix_format *m)
{
    uint16_t tag = w->wFormatTag;
    if (tag == WAVE_FORMAT_EXTENSIBLE) {
        if (w->cbSize < 22) return false;
        uint32_t sub;
        memcpy(&sub, (const uint8_t *)w + sizeof(WAVEFORMATEX) + 6, 4); /* SubFormat.Data1 */
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
    return true;
}

HRESULT Buffer::SetFormat(LPCWAVEFORMATEX f)
{
    if (!primary) return DSERR_INVALIDCALL;
    if (!f) return DSERR_INVALIDPARAM;
    mix_format m;
    if (!parse_format(f, &m)) return DSERR_BADFORMAT;
    if (dev && dev->coop < DSSCL_PRIORITY) return DSERR_PRIOLEVELNEEDED;
    memcpy(&fmt, f, sizeof fmt);
    fmt.cbSize = 0; /* the output format is fixed (48 kHz stereo); remember the request only */
    mf = m;
    return DS_OK;
}

HRESULT Buffer::SetFX(DWORD n, LPDSEFFECTDESC d, LPDWORD results)
{
    if (!(flags & DSBCAPS_CTRLFX)) return DSERR_CONTROLUNAVAIL;
    if (mixer_voice_playing(voice)) return DSERR_INVALIDCALL;
    if (DW(n) && !d) return DSERR_INVALIDPARAM;
    clear_fx();
    if (!DW(n)) return DS_OK;
    fx = (Effect *)calloc(DW(n), sizeof *fx);
    if (!fx) return DSERR_OUTOFMEMORY;
    nfx = DW(n);
    for (uint32_t i = 0; i < nfx; i++) {
        fx[i].cls = d[i].guidDSFXClass;
        if (guid_eq(fx[i].cls, GUID_DSFX_STANDARD_I3DL2REVERB)) {
            fx[i].i3dl2 = new (std::nothrow) I3DL2();
            if (fx[i].i3dl2) {
                fx[i].i3dl2->owner = this;
                fx[i].i3dl2->p = {-1000, -100, 0.0f, 1.49f, 0.83f, -2602, 0.007f, 200, 0.011f, 100.0f, 100.0f, 5000.0f};
            }
        } else if (guid_eq(fx[i].cls, GUID_DSFX_WAVES_REVERB)) {
            fx[i].waves = new (std::nothrow) Waves();
            if (fx[i].waves) {
                fx[i].waves->owner = this;
                fx[i].waves->p = {0.0f, 0.0f, 1000.0f, 0.001f};
            }
        }
        if (results) results[i] = DSFXR_LOCSOFTWARE;
    }
    return DS_OK;
}

HRESULT Buffer::GetObjectInPath(REFGUID obj, DWORD idx, REFGUID iid, LPVOID *out)
{
    if (!out) return E_POINTER;
    *out = nullptr;
    uint32_t seen = 0;
    for (uint32_t i = 0; i < nfx; i++) {
        if (!guid_eq(obj, GUID_All_Objects) && !guid_eq(obj, fx[i].cls)) continue;
        if (seen++ != DW(idx)) continue;
        if (fx[i].i3dl2 && guid_eq(iid, IID_IDirectSoundFXI3DL2Reverb8)) *out = static_cast<IDirectSoundFXI3DL2Reverb8 *>(fx[i].i3dl2);
        else if (fx[i].waves && guid_eq(iid, IID_IDirectSoundFXWavesReverb8)) *out = static_cast<IDirectSoundFXWavesReverb8 *>(fx[i].waves);
        if (!*out) return E_NOINTERFACE;
        AddRef();
        return DS_OK;
    }
    return DSERR_OBJECTNOTFOUND;
}

HRESULT Device::CreateSoundBuffer(LPCDSBUFFERDESC d, IDirectSoundBuffer **out, LPUNKNOWN outer)
{
    if (!out) return DSERR_INVALIDPARAM;
    *out = nullptr;
    if (outer) return DSERR_NOAGGREGATION;
    if (!d || (DW(d->dwSize) != sizeof(DSBUFFERDESC) && DW(d->dwSize) != offsetof(DSBUFFERDESC, guid3DAlgorithm)))
        return DSERR_INVALIDPARAM;
    if (!coop) return DSERR_PRIOLEVELNEEDED;
    DWORD fl = d->dwFlags;
    if ((fl & DSBCAPS_CTRL3D) && (fl & DSBCAPS_CTRLPAN)) return DSERR_INVALIDPARAM;
    if ((fl & DSBCAPS_LOCHARDWARE) && (fl & DSBCAPS_LOCSOFTWARE)) return DSERR_INVALIDPARAM;
    if (fl & DSBCAPS_LOCHARDWARE) return DSERR_CONTROLUNAVAIL;   /* no hardware buffers */

    if (fl & DSBCAPS_PRIMARYBUFFER) {
        if (d->dwBufferBytes || d->lpwfxFormat || (fl & (DSBCAPS_CTRLFREQUENCY | DSBCAPS_CTRLFX | DSBCAPS_CTRLPOSITIONNOTIFY)))
            return DSERR_INVALIDPARAM;
        os_lock(&lock);
        Buffer *p = primary;
        if (p) p->AddRef();
        os_unlock(&lock);
        if (p) { *out = p; return DS_OK; }
        p = new (std::nothrow) Buffer(this, fl, true);
        if (!p) return DSERR_OUTOFMEMORY;
        WAVEFORMATEX w = {WAVE_FORMAT_PCM, 2, 22050, 22050 * 4, 4, 16, 0};
        p->fmt = w;
        parse_format(&w, &p->mf);
        link(p);
        os_lock(&lock);
        primary = p;
        os_unlock(&lock);
        *out = p;
        return DS_OK;
    }

    const WAVEFORMATEX *w = d->lpwfxFormat;
    if (!w) return DSERR_INVALIDPARAM;
    mix_format mf;
    if (!parse_format(w, &mf)) return DSERR_BADFORMAT;
    if ((fl & DSBCAPS_CTRL3D) && mf.channels != 1) return DSERR_INVALIDPARAM;
    uint32_t bytes = DW(d->dwBufferBytes) / mf.block * mf.block;
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
    return DS_OK;
}

HRESULT Device::DuplicateSoundBuffer(IDirectSoundBuffer *orig, IDirectSoundBuffer **out)
{
    if (!orig || !out) return DSERR_INVALIDPARAM;
    *out = nullptr;
    Buffer *o = static_cast<Buffer *>(orig);   /* only our own buffers exist in this process */
    if (o->primary) return DSERR_INVALIDCALL;
    if (o->flags & DSBCAPS_CTRLFX) return DSERR_INVALIDCALL;
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
    b->voice = mixer_voice_create(&b->mf, b->data->bytes, b->data->size);
    if (!b->voice) { delete b; return DSERR_OUTOFMEMORY; }
    if (b->freq) mixer_voice_set_freq(b->voice, DW(b->freq));
    link(b);
    b->update_gain();
    *out = b;
    return DS_OK;
}

} // namespace

extern "C" HRESULT WINAPI DirectSoundCreate8(LPCGUID guid, LPDIRECTSOUND8 *out, LPUNKNOWN outer)
{
    (void)guid; /* NULL, DSDEVID_DefaultPlayback or an enumerated GUID: one device either way */
    if (!out) return DSERR_INVALIDPARAM;
    *out = nullptr;
    if (outer) return DSERR_NOAGGREGATION;
    fnvnx_audio_start();
    Device *d = new (std::nothrow) Device();
    if (!d) return DSERR_OUTOFMEMORY;
    *out = d;
    return DS_OK;
}

extern "C" HRESULT WINAPI DirectSoundCreate(LPCGUID guid, LPDIRECTSOUND *out, LPUNKNOWN outer)
{
    LPDIRECTSOUND8 d8 = nullptr;
    HRESULT hr = DirectSoundCreate8(guid, &d8, outer);
    if (out) *out = d8;
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
    hr = d->QueryInterface(*iid, out);
    d->Release();
    return hr;
}
