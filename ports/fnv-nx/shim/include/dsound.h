/* fnv-nx shim: <dsound.h> - DirectSound 8 (stream S7-audio-input, docs/AUDIO_INPUT.md).
 *
 * COM interfaces are C++ abstract classes with their methods in the SDK's vtable order; the slot number of each
 * method is noted in front of it. The engine calls DirectSound through its own stand-in classes (SndDevice,
 * SndBuffer, Snd3DBuffer, "Com" with methods q/r1/r2/create/caps/r5/coop, FNVRenderInterface_00aea8b0 for the
 * listener, ...), so the slot index is the contract, not the method name. With single inheritance and only
 * non-destructor virtuals GCC's vtable layout equals MSVC's, and under ILP32 every slot is 4 bytes.
 *
 * Structures have their 32-bit (ILP32 == Win32) layouts; the static_asserts check them when building for the
 * game's data model. Implementation: platform/audio/dsound.cpp (software mixer).
 */
#ifndef FNVNX_SHIM_DSOUND_H
#define FNVNX_SHIM_DSOUND_H
#include "fnvnx_win32.h"
/* outside the game build (host tests) fnvnx_compat.h is not force-included */
#ifndef STDMETHODCALLTYPE
#define STDMETHODCALLTYPE
#endif
#ifndef WINAPI
#define WINAPI
#endif

#ifndef DIRECTSOUND_VERSION
#define DIRECTSOUND_VERSION 0x0900
#endif

#ifndef FNVNX_IUNKNOWN_DEFINED
#define FNVNX_IUNKNOWN_DEFINED
struct IUnknown
{
    virtual HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppvObject) = 0;
    virtual ULONG STDMETHODCALLTYPE AddRef() = 0;
    virtual ULONG STDMETHODCALLTYPE Release() = 0;
};
typedef IUnknown *LPUNKNOWN;
#endif
#ifndef E_NOTIMPL
#define E_NOTIMPL ((HRESULT)0x80004001L)
#endif
#ifndef E_NOINTERFACE
#define E_NOINTERFACE ((HRESULT)0x80004002L)
#endif
#ifndef E_INVALIDARG
#define E_INVALIDARG ((HRESULT)0x80070057L)
#endif
#ifndef E_POINTER
#define E_POINTER ((HRESULT)0x80004003L)
#endif
#ifndef CLASS_E_NOAGGREGATION
#define CLASS_E_NOAGGREGATION ((HRESULT)0x80040110L)
#endif
#ifndef S_FALSE
#define S_FALSE ((HRESULT)1)
#endif
typedef const GUID *LPCGUID;
typedef GUID *LPGUID;

/* ---- <mmreg.h> pieces: WAVEFORMATEX is byte-packed (18 bytes) */
#ifndef _WAVEFORMATEX_
#define _WAVEFORMATEX_
#pragma pack(push, 1)
typedef struct tWAVEFORMATEX {
    WORD wFormatTag;
    WORD nChannels;
    DWORD nSamplesPerSec;
    DWORD nAvgBytesPerSec;
    WORD nBlockAlign;
    WORD wBitsPerSample;
    WORD cbSize;
} WAVEFORMATEX, *PWAVEFORMATEX, *LPWAVEFORMATEX;
#pragma pack(pop)
typedef const WAVEFORMATEX *LPCWAVEFORMATEX;
#endif
#ifndef WAVE_FORMAT_PCM
#define WAVE_FORMAT_PCM 1
#endif
#ifndef WAVE_FORMAT_IEEE_FLOAT
#define WAVE_FORMAT_IEEE_FLOAT 3
#endif
#ifndef WAVE_FORMAT_EXTENSIBLE
#define WAVE_FORMAT_EXTENSIBLE 0xFFFE
#endif

#ifndef D3DVECTOR_DEFINED
typedef struct _D3DVECTOR { float x, y, z; } D3DVECTOR;
#define D3DVECTOR_DEFINED
#endif
typedef D3DVECTOR *LPD3DVECTOR;
typedef const D3DVECTOR *LPCD3DVECTOR;
typedef float D3DVALUE;

/* ---- structures */
typedef struct _DSCAPS {
    DWORD dwSize, dwFlags, dwMinSecondarySampleRate, dwMaxSecondarySampleRate, dwPrimaryBuffers,
        dwMaxHwMixingAllBuffers, dwMaxHwMixingStaticBuffers, dwMaxHwMixingStreamingBuffers,
        dwFreeHwMixingAllBuffers, dwFreeHwMixingStaticBuffers, dwFreeHwMixingStreamingBuffers,
        dwMaxHw3DAllBuffers, dwMaxHw3DStaticBuffers, dwMaxHw3DStreamingBuffers, dwFreeHw3DAllBuffers,
        dwFreeHw3DStaticBuffers, dwFreeHw3DStreamingBuffers, dwTotalHwMemBytes, dwFreeHwMemBytes,
        dwMaxContigFreeHwMemBytes, dwUnlockTransferRateHwBuffers, dwPlayCpuOverheadSwBuffers, dwReserved1,
        dwReserved2;
} DSCAPS, *LPDSCAPS;
typedef const DSCAPS *LPCDSCAPS;

typedef struct _DSBCAPS {
    DWORD dwSize, dwFlags, dwBufferBytes, dwUnlockTransferRate, dwPlayCpuOverhead;
} DSBCAPS, *LPDSBCAPS;
typedef const DSBCAPS *LPCDSBCAPS;

typedef struct _DSEFFECTDESC {
    DWORD dwSize;
    DWORD dwFlags;
    GUID guidDSFXClass;
    DWORD_PTR dwReserved1;
    DWORD_PTR dwReserved2;
} DSEFFECTDESC, *LPDSEFFECTDESC;
typedef const DSEFFECTDESC *LPCDSEFFECTDESC;

typedef struct _DSBUFFERDESC {
    DWORD dwSize;
    DWORD dwFlags;
    DWORD dwBufferBytes;
    DWORD dwReserved;
    LPWAVEFORMATEX lpwfxFormat;
    GUID guid3DAlgorithm;
} DSBUFFERDESC, *LPDSBUFFERDESC;
typedef const DSBUFFERDESC *LPCDSBUFFERDESC;

typedef struct _DS3DBUFFER {
    DWORD dwSize;
    D3DVECTOR vPosition;
    D3DVECTOR vVelocity;
    DWORD dwInsideConeAngle;
    DWORD dwOutsideConeAngle;
    D3DVECTOR vConeOrientation;
    LONG lConeOutsideVolume;
    D3DVALUE flMinDistance;
    D3DVALUE flMaxDistance;
    DWORD dwMode;
} DS3DBUFFER, *LPDS3DBUFFER;
typedef const DS3DBUFFER *LPCDS3DBUFFER;

typedef struct _DS3DLISTENER {
    DWORD dwSize;
    D3DVECTOR vPosition;
    D3DVECTOR vVelocity;
    D3DVECTOR vOrientFront;
    D3DVECTOR vOrientTop;
    D3DVALUE flDistanceFactor;
    D3DVALUE flRolloffFactor;
    D3DVALUE flDopplerFactor;
} DS3DLISTENER, *LPDS3DLISTENER;
typedef const DS3DLISTENER *LPCDS3DLISTENER;

typedef struct _DSBPOSITIONNOTIFY {
    DWORD dwOffset;
    HANDLE hEventNotify;
} DSBPOSITIONNOTIFY, *LPDSBPOSITIONNOTIFY;
typedef const DSBPOSITIONNOTIFY *LPCDSBPOSITIONNOTIFY;

typedef struct _DSFXI3DL2Reverb {
    LONG lRoom, lRoomHF;
    FLOAT flRoomRolloffFactor, flDecayTime, flDecayHFRatio;
    LONG lReflections;
    FLOAT flReflectionsDelay;
    LONG lReverb;
    FLOAT flReverbDelay, flDiffusion, flDensity, flHFReference;
} DSFXI3DL2Reverb, *LPDSFXI3DL2Reverb;
typedef const DSFXI3DL2Reverb *LPCDSFXI3DL2Reverb;

typedef struct _DSFXWavesReverb {
    FLOAT fInGain, fReverbMix, fReverbTime, fHighFreqRTRatio;
} DSFXWavesReverb, *LPDSFXWavesReverb;
typedef const DSFXWavesReverb *LPCDSFXWavesReverb;

#if defined(__ILP32__) || defined(_WIN32)
static_assert(sizeof(WAVEFORMATEX) == 18, "WAVEFORMATEX");
static_assert(sizeof(DSCAPS) == 96, "DSCAPS");
static_assert(sizeof(DSBCAPS) == 20, "DSBCAPS");
static_assert(sizeof(DSBUFFERDESC) == 36, "DSBUFFERDESC");
static_assert(sizeof(DSEFFECTDESC) == 32, "DSEFFECTDESC");
static_assert(sizeof(DS3DBUFFER) == 64, "DS3DBUFFER");
static_assert(sizeof(DS3DLISTENER) == 64, "DS3DLISTENER");
static_assert(sizeof(DSBPOSITIONNOTIFY) == 8, "DSBPOSITIONNOTIFY");
static_assert(sizeof(DSFXI3DL2Reverb) == 48, "DSFXI3DL2Reverb");
#endif

/* ---- constants */
#define _FACDS 0x878
#define MAKE_DSHRESULT(code) ((HRESULT)(int)(0x80000000u | ((unsigned)_FACDS << 16) | (code)))
#define DS_OK S_OK
#define DS_NO_VIRTUALIZATION ((HRESULT)0x0878000AL)
#define DSERR_ALLOCATED MAKE_DSHRESULT(10)
#define DSERR_CONTROLUNAVAIL MAKE_DSHRESULT(30)
#define DSERR_INVALIDPARAM E_INVALIDARG
#define DSERR_INVALIDCALL MAKE_DSHRESULT(50)
#define DSERR_GENERIC E_FAIL
#define DSERR_PRIOLEVELNEEDED MAKE_DSHRESULT(70)
#define DSERR_OUTOFMEMORY E_OUTOFMEMORY
#define DSERR_BADFORMAT MAKE_DSHRESULT(100)
#define DSERR_UNSUPPORTED E_NOTIMPL
#define DSERR_NODRIVER MAKE_DSHRESULT(120)
#define DSERR_ALREADYINITIALIZED MAKE_DSHRESULT(130)
#define DSERR_NOAGGREGATION CLASS_E_NOAGGREGATION
#define DSERR_BUFFERLOST MAKE_DSHRESULT(150)
#define DSERR_OTHERAPPHASPRIO MAKE_DSHRESULT(160)
#define DSERR_UNINITIALIZED MAKE_DSHRESULT(170)
#define DSERR_NOINTERFACE E_NOINTERFACE
#define DSERR_ACCESSDENIED ((HRESULT)(int)0x80070005u)
#define DSERR_BUFFERTOOSMALL MAKE_DSHRESULT(180) /* 0x887800B4, checked by the engine */
#define DSERR_DS8_REQUIRED MAKE_DSHRESULT(190)
#define DSERR_SENDLOOP MAKE_DSHRESULT(200)
#define DSERR_BADSENDBUFFERGUID MAKE_DSHRESULT(210)
#define DSERR_OBJECTNOTFOUND MAKE_DSHRESULT(4449)
#define DSERR_FXUNAVAILABLE MAKE_DSHRESULT(220)

#define DSCAPS_PRIMARYMONO 0x00000001
#define DSCAPS_PRIMARYSTEREO 0x00000002
#define DSCAPS_PRIMARY8BIT 0x00000004
#define DSCAPS_PRIMARY16BIT 0x00000008
#define DSCAPS_CONTINUOUSRATE 0x00000010
#define DSCAPS_EMULDRIVER 0x00000020
#define DSCAPS_CERTIFIED 0x00000040
#define DSCAPS_SECONDARYMONO 0x00000100
#define DSCAPS_SECONDARYSTEREO 0x00000200
#define DSCAPS_SECONDARY8BIT 0x00000400
#define DSCAPS_SECONDARY16BIT 0x00000800

#define DSSCL_NORMAL 0x00000001
#define DSSCL_PRIORITY 0x00000002
#define DSSCL_EXCLUSIVE 0x00000003
#define DSSCL_WRITEPRIMARY 0x00000004

#define DSSPEAKER_DIRECTOUT 0x00000000
#define DSSPEAKER_HEADPHONE 0x00000001
#define DSSPEAKER_MONO 0x00000002
#define DSSPEAKER_QUAD 0x00000003
#define DSSPEAKER_STEREO 0x00000004
#define DSSPEAKER_SURROUND 0x00000005
#define DSSPEAKER_5POINT1 0x00000006
#define DSSPEAKER_7POINT1 0x00000007
#define DSSPEAKER_COMBINED(c, g) ((DWORD)(((BYTE)(c)) | ((DWORD)((BYTE)(g))) << 16))
#define DSSPEAKER_CONFIG(a) ((BYTE)(a))
#define DSSPEAKER_GEOMETRY_WIDE 0x00000014

#define DSBCAPS_PRIMARYBUFFER 0x00000001
#define DSBCAPS_STATIC 0x00000002
#define DSBCAPS_LOCHARDWARE 0x00000004
#define DSBCAPS_LOCSOFTWARE 0x00000008
#define DSBCAPS_CTRL3D 0x00000010
#define DSBCAPS_CTRLFREQUENCY 0x00000020
#define DSBCAPS_CTRLPAN 0x00000040
#define DSBCAPS_CTRLVOLUME 0x00000080
#define DSBCAPS_CTRLPOSITIONNOTIFY 0x00000100
#define DSBCAPS_CTRLFX 0x00000200
#define DSBCAPS_STICKYFOCUS 0x00004000
#define DSBCAPS_GLOBALFOCUS 0x00008000
#define DSBCAPS_GETCURRENTPOSITION2 0x00010000
#define DSBCAPS_MUTE3DATMAXDISTANCE 0x00020000
#define DSBCAPS_LOCDEFER 0x00040000
#define DSBCAPS_TRUEPLAYPOSITION 0x00080000

#define DSBPLAY_LOOPING 0x00000001
#define DSBPLAY_LOCHARDWARE 0x00000002
#define DSBPLAY_LOCSOFTWARE 0x00000004
#define DSBPLAY_TERMINATEBY_TIME 0x00000008
#define DSBPLAY_TERMINATEBY_DISTANCE 0x000000010
#define DSBPLAY_TERMINATEBY_PRIORITY 0x000000020

#define DSBSTATUS_PLAYING 0x00000001
#define DSBSTATUS_BUFFERLOST 0x00000002
#define DSBSTATUS_LOOPING 0x00000004
#define DSBSTATUS_LOCHARDWARE 0x00000008
#define DSBSTATUS_LOCSOFTWARE 0x00000010
#define DSBSTATUS_TERMINATED 0x00000020

#define DSBLOCK_FROMWRITECURSOR 0x00000001
#define DSBLOCK_ENTIREBUFFER 0x00000002

#define DSBFREQUENCY_ORIGINAL 0
#define DSBFREQUENCY_MIN 100
#define DSBFREQUENCY_MAX 200000
#define DSBPAN_LEFT (-10000)
#define DSBPAN_CENTER 0
#define DSBPAN_RIGHT 10000
#define DSBVOLUME_MIN (-10000)
#define DSBVOLUME_MAX 0
#define DSBSIZE_MIN 4
#define DSBSIZE_MAX 0x0FFFFFFF
#define DSBSIZE_FX_MIN 150 /* ms; buffers with DSBCAPS_CTRLFX must be at least this long */
#define DSBPN_OFFSETSTOP 0xFFFFFFFF

#define DS3DMODE_NORMAL 0x00000000
#define DS3DMODE_HEADRELATIVE 0x00000001
#define DS3DMODE_DISABLE 0x00000002
#define DS3D_IMMEDIATE 0x00000000
#define DS3D_DEFERRED 0x00000001
#define DS3D_MINDISTANCEFACTOR 1.17549435e-38f
#define DS3D_MAXDISTANCEFACTOR 3.402823466e+38f
#define DS3D_DEFAULTDISTANCEFACTOR 1.0f
#define DS3D_MINROLLOFFFACTOR 0.0f
#define DS3D_MAXROLLOFFFACTOR 10.0f
#define DS3D_DEFAULTROLLOFFFACTOR 1.0f
#define DS3D_MINDOPPLERFACTOR 0.0f
#define DS3D_MAXDOPPLERFACTOR 10.0f
#define DS3D_DEFAULTDOPPLERFACTOR 1.0f
#define DS3D_DEFAULTMINDISTANCE 1.0f
#define DS3D_DEFAULTMAXDISTANCE 1000000000.0f
#define DS3D_MINCONEANGLE 0
#define DS3D_MAXCONEANGLE 360
#define DS3D_DEFAULTCONEANGLE 360
#define DS3D_DEFAULTCONEOUTSIDEVOLUME DSBVOLUME_MAX

#define DSFX_LOCHARDWARE 0x00000001
#define DSFX_LOCSOFTWARE 0x00000002
#define DSFXR_PRESENT 0
#define DSFXR_LOCHARDWARE 1
#define DSFXR_LOCSOFTWARE 2
#define DSFXR_UNALLOCATED 3
#define DSFXR_FAILED 4
#define DSFXR_UNKNOWN 5
#define DSFXR_SENDLOOP 6

/* ---- GUIDs (values of the DirectX SDK; the engine passes its own copies from the exe's .rdata) */
#define FNVNX_DS_GUID(name, l, w1, w2, ...) static const GUID name = {l, w1, w2, {__VA_ARGS__}}
FNVNX_DS_GUID(CLSID_DirectSound, 0x47d4d946, 0x62e8, 0x11cf, 0x93, 0xbc, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DS_GUID(CLSID_DirectSound8, 0x3901cc3f, 0x84b5, 0x4fa4, 0xba, 0x35, 0xaa, 0x81, 0x72, 0xb8, 0xa0, 0x9b);
FNVNX_DS_GUID(IID_IDirectSound, 0x279afa83, 0x4981, 0x11ce, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5, 0x60);
FNVNX_DS_GUID(IID_IDirectSound8, 0xc50a7e93, 0xf395, 0x4834, 0x9e, 0xf6, 0x7f, 0xa9, 0x9d, 0xe5, 0x09, 0x66);
FNVNX_DS_GUID(IID_IDirectSoundBuffer, 0x279afa85, 0x4981, 0x11ce, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5, 0x60);
FNVNX_DS_GUID(IID_IDirectSoundBuffer8, 0x6825a449, 0x7524, 0x4d82, 0x92, 0x0f, 0x50, 0xe3, 0x6a, 0xb3, 0xab, 0x1e);
FNVNX_DS_GUID(IID_IDirectSound3DListener, 0x279afa84, 0x4981, 0x11ce, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5, 0x60);
FNVNX_DS_GUID(IID_IDirectSound3DBuffer, 0x279afa86, 0x4981, 0x11ce, 0xa5, 0x21, 0x00, 0x20, 0xaf, 0x0b, 0xe5, 0x60);
FNVNX_DS_GUID(IID_IDirectSoundNotify, 0xb0210783, 0x89cd, 0x11d0, 0xaf, 0x08, 0x00, 0xa0, 0xc9, 0x25, 0xcd, 0x16);
FNVNX_DS_GUID(IID_IDirectSoundFXI3DL2Reverb8, 0x4b166a6a, 0x0d66, 0x43f3, 0x80, 0xe3, 0xee, 0x62, 0x80, 0xde, 0xe1, 0xa4);
FNVNX_DS_GUID(IID_IDirectSoundFXWavesReverb8, 0x46858c3a, 0x0dc6, 0x45e3, 0xb7, 0x60, 0xd4, 0xee, 0xd8, 0x1e, 0x32, 0x2d);
FNVNX_DS_GUID(GUID_DSFX_STANDARD_I3DL2REVERB, 0xef985e71, 0xd5c7, 0x42d4, 0xba, 0x4d, 0x2d, 0x07, 0x3e, 0x2e, 0x96, 0xf4);
FNVNX_DS_GUID(GUID_DSFX_WAVES_REVERB, 0x87fc0268, 0x9a55, 0x4360, 0x95, 0xaa, 0x00, 0x4a, 0x1d, 0x9d, 0xe2, 0x6c);
FNVNX_DS_GUID(GUID_All_Objects, 0xaa114de5, 0xc262, 0x4169, 0xa1, 0xc8, 0x23, 0xd6, 0x98, 0xcc, 0x73, 0xb5);
FNVNX_DS_GUID(DS3DALG_DEFAULT, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
FNVNX_DS_GUID(DS3DALG_NO_VIRTUALIZATION, 0xc241333f, 0x1c1b, 0x11d2, 0x94, 0xf5, 0x00, 0xc0, 0x4f, 0xc2, 0x8a, 0xca);
FNVNX_DS_GUID(DSDEVID_DefaultPlayback, 0xdef00000, 0x9c6d, 0x47ed, 0xaa, 0xf1, 0x4d, 0xda, 0x8f, 0x2b, 0x5c, 0x03);

/* ---- interfaces (slot numbers in brackets) */
struct IDirectSoundBuffer;
struct IDirectSound : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE CreateSoundBuffer(LPCDSBUFFERDESC pcDSBufferDesc,
                                                               IDirectSoundBuffer **ppDSBuffer, LPUNKNOWN pUnkOuter) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE GetCaps(LPDSCAPS pDSCaps) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE DuplicateSoundBuffer(IDirectSoundBuffer *pDSBufferOriginal,
                                                                  IDirectSoundBuffer **ppDSBufferDuplicate) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE SetCooperativeLevel(HWND hwnd, DWORD dwLevel) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE Compact() = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE GetSpeakerConfig(LPDWORD pdwSpeakerConfig) = 0;
    /* 9 */ virtual HRESULT STDMETHODCALLTYPE SetSpeakerConfig(DWORD dwSpeakerConfig) = 0;
    /* 10 */ virtual HRESULT STDMETHODCALLTYPE Initialize(LPCGUID pcGuidDevice) = 0;
};
struct IDirectSound8 : public IDirectSound
{
    /* 11 */ virtual HRESULT STDMETHODCALLTYPE VerifyCertification(LPDWORD pdwCertified) = 0;
};

struct IDirectSoundBuffer : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE GetCaps(LPDSBCAPS pDSBufferCaps) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE GetCurrentPosition(LPDWORD pdwCurrentPlayCursor,
                                                                LPDWORD pdwCurrentWriteCursor) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE GetFormat(LPWAVEFORMATEX pwfxFormat, DWORD dwSizeAllocated,
                                                       LPDWORD pdwSizeWritten) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE GetVolume(LPLONG plVolume) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE GetPan(LPLONG plPan) = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE GetFrequency(LPDWORD pdwFrequency) = 0;
    /* 9 */ virtual HRESULT STDMETHODCALLTYPE GetStatus(LPDWORD pdwStatus) = 0;
    /* 10 */ virtual HRESULT STDMETHODCALLTYPE Initialize(IDirectSound *pDirectSound, LPCDSBUFFERDESC pcDSBufferDesc) = 0;
    /* 11 */ virtual HRESULT STDMETHODCALLTYPE Lock(DWORD dwOffset, DWORD dwBytes, LPVOID *ppvAudioPtr1,
                                                   LPDWORD pdwAudioBytes1, LPVOID *ppvAudioPtr2, LPDWORD pdwAudioBytes2,
                                                   DWORD dwFlags) = 0;
    /* 12 */ virtual HRESULT STDMETHODCALLTYPE Play(DWORD dwReserved1, DWORD dwPriority, DWORD dwFlags) = 0;
    /* 13 */ virtual HRESULT STDMETHODCALLTYPE SetCurrentPosition(DWORD dwNewPosition) = 0;
    /* 14 */ virtual HRESULT STDMETHODCALLTYPE SetFormat(LPCWAVEFORMATEX pcfxFormat) = 0;
    /* 15 */ virtual HRESULT STDMETHODCALLTYPE SetVolume(LONG lVolume) = 0;
    /* 16 */ virtual HRESULT STDMETHODCALLTYPE SetPan(LONG lPan) = 0;
    /* 17 */ virtual HRESULT STDMETHODCALLTYPE SetFrequency(DWORD dwFrequency) = 0;
    /* 18 */ virtual HRESULT STDMETHODCALLTYPE Stop() = 0;
    /* 19 */ virtual HRESULT STDMETHODCALLTYPE Unlock(LPVOID pvAudioPtr1, DWORD dwAudioBytes1, LPVOID pvAudioPtr2,
                                                     DWORD dwAudioBytes2) = 0;
    /* 20 */ virtual HRESULT STDMETHODCALLTYPE Restore() = 0;
};
struct IDirectSoundBuffer8 : public IDirectSoundBuffer
{
    /* 21 */ virtual HRESULT STDMETHODCALLTYPE SetFX(DWORD dwEffectsCount, LPDSEFFECTDESC pDSFXDesc,
                                                    LPDWORD pdwResultCodes) = 0;
    /* 22 */ virtual HRESULT STDMETHODCALLTYPE AcquireResources(DWORD dwFlags, DWORD dwEffectsCount,
                                                               LPDWORD pdwResultCodes) = 0;
    /* 23 */ virtual HRESULT STDMETHODCALLTYPE GetObjectInPath(REFGUID rguidObject, DWORD dwIndex,
                                                              REFGUID rguidInterface, LPVOID *ppObject) = 0;
};

struct IDirectSound3DListener : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE GetAllParameters(LPDS3DLISTENER pListener) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE GetDistanceFactor(D3DVALUE *pflDistanceFactor) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE GetDopplerFactor(D3DVALUE *pflDopplerFactor) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE GetOrientation(D3DVECTOR *pvOrientFront, D3DVECTOR *pvOrientTop) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE GetPosition(D3DVECTOR *pvPosition) = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE GetRolloffFactor(D3DVALUE *pflRolloffFactor) = 0;
    /* 9 */ virtual HRESULT STDMETHODCALLTYPE GetVelocity(D3DVECTOR *pvVelocity) = 0;
    /* 10 */ virtual HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDS3DLISTENER pcListener, DWORD dwApply) = 0;
    /* 11 */ virtual HRESULT STDMETHODCALLTYPE SetDistanceFactor(D3DVALUE flDistanceFactor, DWORD dwApply) = 0;
    /* 12 */ virtual HRESULT STDMETHODCALLTYPE SetDopplerFactor(D3DVALUE flDopplerFactor, DWORD dwApply) = 0;
    /* 13 */ virtual HRESULT STDMETHODCALLTYPE SetOrientation(D3DVALUE xFront, D3DVALUE yFront, D3DVALUE zFront,
                                                            D3DVALUE xTop, D3DVALUE yTop, D3DVALUE zTop,
                                                            DWORD dwApply) = 0;
    /* 14 */ virtual HRESULT STDMETHODCALLTYPE SetPosition(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD dwApply) = 0;
    /* 15 */ virtual HRESULT STDMETHODCALLTYPE SetRolloffFactor(D3DVALUE flRolloffFactor, DWORD dwApply) = 0;
    /* 16 */ virtual HRESULT STDMETHODCALLTYPE SetVelocity(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD dwApply) = 0;
    /* 17 */ virtual HRESULT STDMETHODCALLTYPE CommitDeferredSettings() = 0;
};

struct IDirectSound3DBuffer : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE GetAllParameters(LPDS3DBUFFER pDs3dBuffer) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE GetConeAngles(LPDWORD pdwInsideConeAngle, LPDWORD pdwOutsideConeAngle) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE GetConeOrientation(D3DVECTOR *pvOrientation) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE GetConeOutsideVolume(LPLONG plConeOutsideVolume) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE GetMaxDistance(D3DVALUE *pflMaxDistance) = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE GetMinDistance(D3DVALUE *pflMinDistance) = 0;
    /* 9 */ virtual HRESULT STDMETHODCALLTYPE GetMode(LPDWORD pdwMode) = 0;
    /* 10 */ virtual HRESULT STDMETHODCALLTYPE GetPosition(D3DVECTOR *pvPosition) = 0;
    /* 11 */ virtual HRESULT STDMETHODCALLTYPE GetVelocity(D3DVECTOR *pvVelocity) = 0;
    /* 12 */ virtual HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDS3DBUFFER pcDs3dBuffer, DWORD dwApply) = 0;
    /* 13 */ virtual HRESULT STDMETHODCALLTYPE SetConeAngles(DWORD dwInsideConeAngle, DWORD dwOutsideConeAngle,
                                                           DWORD dwApply) = 0;
    /* 14 */ virtual HRESULT STDMETHODCALLTYPE SetConeOrientation(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD dwApply) = 0;
    /* 15 */ virtual HRESULT STDMETHODCALLTYPE SetConeOutsideVolume(LONG lConeOutsideVolume, DWORD dwApply) = 0;
    /* 16 */ virtual HRESULT STDMETHODCALLTYPE SetMaxDistance(D3DVALUE flMaxDistance, DWORD dwApply) = 0;
    /* 17 */ virtual HRESULT STDMETHODCALLTYPE SetMinDistance(D3DVALUE flMinDistance, DWORD dwApply) = 0;
    /* 18 */ virtual HRESULT STDMETHODCALLTYPE SetMode(DWORD dwMode, DWORD dwApply) = 0;
    /* 19 */ virtual HRESULT STDMETHODCALLTYPE SetPosition(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD dwApply) = 0;
    /* 20 */ virtual HRESULT STDMETHODCALLTYPE SetVelocity(D3DVALUE x, D3DVALUE y, D3DVALUE z, DWORD dwApply) = 0;
};

struct IDirectSoundNotify : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE SetNotificationPositions(DWORD dwPositionNotifies,
                                                                      LPCDSBPOSITIONNOTIFY pcPositionNotifies) = 0;
};

struct IDirectSoundFXI3DL2Reverb : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDSFXI3DL2Reverb pcDsFxI3DL2Reverb) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE GetAllParameters(LPDSFXI3DL2Reverb pDsFxI3DL2Reverb) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE SetPreset(DWORD dwPreset) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE GetPreset(LPDWORD pdwPreset) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE SetQuality(LONG lQuality) = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE GetQuality(LONG *plQuality) = 0;
};
typedef IDirectSoundFXI3DL2Reverb IDirectSoundFXI3DL2Reverb8;

struct IDirectSoundFXWavesReverb : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE SetAllParameters(LPCDSFXWavesReverb pcDsFxWavesReverb) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE GetAllParameters(LPDSFXWavesReverb pDsFxWavesReverb) = 0;
};
typedef IDirectSoundFXWavesReverb IDirectSoundFXWavesReverb8;

typedef IDirectSound *LPDIRECTSOUND;
typedef IDirectSound8 *LPDIRECTSOUND8;
typedef IDirectSoundBuffer *LPDIRECTSOUNDBUFFER;
typedef IDirectSoundBuffer8 *LPDIRECTSOUNDBUFFER8;
typedef IDirectSound3DListener *LPDIRECTSOUND3DLISTENER;
typedef IDirectSound3DBuffer *LPDIRECTSOUND3DBUFFER;
typedef IDirectSoundNotify *LPDIRECTSOUNDNOTIFY;

typedef BOOL (*LPDSENUMCALLBACKA)(LPGUID, LPCSTR, LPCSTR, LPVOID);

extern "C" {
HRESULT WINAPI DirectSoundCreate(LPCGUID pcGuidDevice, LPDIRECTSOUND *ppDS, LPUNKNOWN pUnkOuter);
HRESULT WINAPI DirectSoundCreate8(LPCGUID pcGuidDevice, LPDIRECTSOUND8 *ppDS8, LPUNKNOWN pUnkOuter);
HRESULT WINAPI DirectSoundEnumerateA(LPDSENUMCALLBACKA pDSEnumCallback, LPVOID pContext);
}

#endif
