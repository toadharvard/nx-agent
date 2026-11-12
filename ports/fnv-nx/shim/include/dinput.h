/* fnv-nx shim: <dinput.h> - DirectInput 8, ANSI interfaces (stream S7-audio-input, docs/AUDIO_INPUT.md).
 *
 * Interfaces in SDK vtable order, slot numbers in brackets. The engine calls them through stand-in classes
 * (IDirectInputDevice8A/IDirectInput8A re-declared per unit with Q/A/R placeholders, PollingDevice, IDIDevice8),
 * so the slot index is the contract. 32-bit layouts are checked under ILP32. Implementation:
 * platform/input/dinput.cpp (system keyboard and mouse; no joysticks - the gamepad goes through XInput).
 */
#ifndef FNVNX_SHIM_DINPUT_H
#define FNVNX_SHIM_DINPUT_H
#include "fnvnx_win32.h"
/* outside the game build (host tests) fnvnx_compat.h is not force-included */
#ifndef STDMETHODCALLTYPE
#define STDMETHODCALLTYPE
#endif
#ifndef WINAPI
#define WINAPI
#endif

#ifndef DIRECTINPUT_VERSION
#define DIRECTINPUT_VERSION 0x0800
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
#define E_POINTER ((HRESULT)(int)0x80004003u)
#endif
#ifndef E_HANDLE
#define E_HANDLE ((HRESULT)(int)0x80070006u)
#endif
#ifndef S_FALSE
#define S_FALSE ((HRESULT)1)
#endif
#ifndef MAX_PATH
#define MAX_PATH 260
#endif
#ifndef CLASS_E_NOAGGREGATION
#define CLASS_E_NOAGGREGATION ((HRESULT)(int)0x80040110u)
#endif
typedef GUID *LPGUID;

/* ---- structures */
typedef struct _DIOBJECTDATAFORMAT {
    const GUID *pguid;
    DWORD dwOfs;
    DWORD dwType;
    DWORD dwFlags;
} DIOBJECTDATAFORMAT, *LPDIOBJECTDATAFORMAT;
typedef const DIOBJECTDATAFORMAT *LPCDIOBJECTDATAFORMAT;

typedef struct _DIDATAFORMAT {
    DWORD dwSize;
    DWORD dwObjSize;
    DWORD dwFlags;
    DWORD dwDataSize;
    DWORD dwNumObjs;
    LPDIOBJECTDATAFORMAT rgodf;
} DIDATAFORMAT, *LPDIDATAFORMAT;
typedef const DIDATAFORMAT *LPCDIDATAFORMAT;

typedef struct DIDEVCAPS {
    DWORD dwSize, dwFlags, dwDevType, dwAxes, dwButtons, dwPOVs, dwFFSamplePeriod, dwFFMinTimeResolution,
        dwFirmwareRevision, dwHardwareRevision, dwFFDriverVersion;
} DIDEVCAPS, *LPDIDEVCAPS;

typedef struct DIDEVICEOBJECTDATA {
    DWORD dwOfs;
    DWORD dwData;
    DWORD dwTimeStamp;
    DWORD dwSequence;
    UINT_PTR uAppData;
} DIDEVICEOBJECTDATA, *LPDIDEVICEOBJECTDATA;
typedef const DIDEVICEOBJECTDATA *LPCDIDEVICEOBJECTDATA;

typedef struct DIPROPHEADER {
    DWORD dwSize;
    DWORD dwHeaderSize;
    DWORD dwObj;
    DWORD dwHow;
} DIPROPHEADER, *LPDIPROPHEADER;
typedef const DIPROPHEADER *LPCDIPROPHEADER;
typedef struct DIPROPDWORD { DIPROPHEADER diph; DWORD dwData; } DIPROPDWORD, *LPDIPROPDWORD;
typedef const DIPROPDWORD *LPCDIPROPDWORD;
typedef struct DIPROPRANGE { DIPROPHEADER diph; LONG lMin, lMax; } DIPROPRANGE, *LPDIPROPRANGE;
typedef const DIPROPRANGE *LPCDIPROPRANGE;

typedef struct DIDEVICEINSTANCEA {
    DWORD dwSize;
    GUID guidInstance;
    GUID guidProduct;
    DWORD dwDevType;
    CHAR tszInstanceName[MAX_PATH];
    CHAR tszProductName[MAX_PATH];
    GUID guidFFDriver;
    WORD wUsagePage;
    WORD wUsage;
} DIDEVICEINSTANCEA, *LPDIDEVICEINSTANCEA;
typedef const DIDEVICEINSTANCEA *LPCDIDEVICEINSTANCEA;

typedef struct DIDEVICEOBJECTINSTANCEA {
    DWORD dwSize;
    GUID guidType;
    DWORD dwOfs;
    DWORD dwType;
    DWORD dwFlags;
    CHAR tszName[MAX_PATH];
    DWORD dwFFMaxForce;
    DWORD dwFFForceResolution;
    WORD wCollectionNumber;
    WORD wDesignatorIndex;
    WORD wUsagePage;
    WORD wUsage;
    DWORD dwDimension;
    WORD wExponent;
    WORD wReportId;
} DIDEVICEOBJECTINSTANCEA, *LPDIDEVICEOBJECTINSTANCEA;
typedef const DIDEVICEOBJECTINSTANCEA *LPCDIDEVICEOBJECTINSTANCEA;

typedef struct _DIMOUSESTATE { LONG lX, lY, lZ; BYTE rgbButtons[4]; } DIMOUSESTATE, *LPDIMOUSESTATE;
typedef struct _DIMOUSESTATE2 { LONG lX, lY, lZ; BYTE rgbButtons[8]; } DIMOUSESTATE2, *LPDIMOUSESTATE2;
typedef struct DIJOYSTATE {
    LONG lX, lY, lZ, lRx, lRy, lRz;
    LONG rglSlider[2];
    DWORD rgdwPOV[4];
    BYTE rgbButtons[32];
} DIJOYSTATE, *LPDIJOYSTATE;
typedef struct DIJOYSTATE2 {
    LONG lX, lY, lZ, lRx, lRy, lRz;
    LONG rglSlider[2];
    DWORD rgdwPOV[4];
    BYTE rgbButtons[128];
    LONG lVX, lVY, lVZ, lVRx, lVRy, lVRz;
    LONG rglVSlider[2];
    LONG lAX, lAY, lAZ, lARx, lARy, lARz;
    LONG rglASlider[2];
    LONG lFX, lFY, lFZ, lFRx, lFRy, lFRz;
    LONG rglFSlider[2];
} DIJOYSTATE2, *LPDIJOYSTATE2;

#if defined(__ILP32__) || defined(_WIN32)
static_assert(sizeof(DIOBJECTDATAFORMAT) == 16, "DIOBJECTDATAFORMAT");
static_assert(sizeof(DIDATAFORMAT) == 24, "DIDATAFORMAT");
static_assert(sizeof(DIDEVCAPS) == 44, "DIDEVCAPS");
static_assert(sizeof(DIDEVICEOBJECTDATA) == 20, "DIDEVICEOBJECTDATA");
static_assert(sizeof(DIPROPHEADER) == 16, "DIPROPHEADER");
static_assert(sizeof(DIPROPDWORD) == 20, "DIPROPDWORD");
static_assert(sizeof(DIDEVICEINSTANCEA) == 580, "DIDEVICEINSTANCEA");
static_assert(sizeof(DIDEVICEOBJECTINSTANCEA) == 316, "DIDEVICEOBJECTINSTANCEA");
static_assert(sizeof(DIMOUSESTATE2) == 20, "DIMOUSESTATE2");
static_assert(sizeof(DIJOYSTATE) == 80, "DIJOYSTATE");
static_assert(sizeof(DIJOYSTATE2) == 272, "DIJOYSTATE2");
#endif

FNVNX_CALLBACK_T(LPDIENUMDEVICESCALLBACKA, BOOL, (LPCDIDEVICEINSTANCEA, LPVOID));
FNVNX_CALLBACK_T(LPDIENUMDEVICEOBJECTSCALLBACKA, BOOL, (LPCDIDEVICEOBJECTINSTANCEA, LPVOID));

/* ---- constants */
#define DI_OK S_OK
#define DI_NOTATTACHED S_FALSE
#define DI_BUFFEROVERFLOW S_FALSE
#define DI_PROPNOEFFECT S_FALSE
#define DI_NOEFFECT S_FALSE
#define DI_POLLEDDEVICE ((HRESULT)0x00000002L)
#define DIERR_OLDDIRECTINPUTVERSION ((HRESULT)(int)0x8007047Eu)
#define DIERR_BETADIRECTINPUTVERSION ((HRESULT)(int)0x80070481u)
#define DIERR_BADDRIVERVER ((HRESULT)(int)0x80070077u)
#define DIERR_DEVICENOTREG REGDB_E_CLASSNOTREG
#define DIERR_NOTFOUND ((HRESULT)(int)0x80070002u)
#define DIERR_OBJECTNOTFOUND ((HRESULT)(int)0x80070002u)
#define DIERR_INVALIDPARAM E_INVALIDARG
#define DIERR_NOINTERFACE E_NOINTERFACE
#define DIERR_GENERIC E_FAIL
#define DIERR_OUTOFMEMORY E_OUTOFMEMORY
#define DIERR_UNSUPPORTED E_NOTIMPL
#define DIERR_NOTINITIALIZED ((HRESULT)(int)0x80070015u)
#define DIERR_ALREADYINITIALIZED ((HRESULT)(int)0x800704DFu)
#define DIERR_NOAGGREGATION CLASS_E_NOAGGREGATION
#define DIERR_OTHERAPPHASPRIO ((HRESULT)(int)0x80070005u)
#define DIERR_INPUTLOST ((HRESULT)(int)0x8007001Eu)
#define DIERR_ACQUIRED ((HRESULT)(int)0x800700AAu)
#define DIERR_NOTACQUIRED ((HRESULT)(int)0x8007000Cu)
#define DIERR_READONLY ((HRESULT)(int)0x80070005u)
#define DIERR_HANDLEEXISTS ((HRESULT)(int)0x80070005u)
#define DIERR_NOTBUFFERED ((HRESULT)(int)0x80040207u)

#define DI8DEVCLASS_ALL 0
#define DI8DEVCLASS_DEVICE 1
#define DI8DEVCLASS_POINTER 2
#define DI8DEVCLASS_KEYBOARD 3
#define DI8DEVCLASS_GAMECTRL 4
#define DIEDFL_ALLDEVICES 0x00000000
#define DIEDFL_ATTACHEDONLY 0x00000001
#define DIEDFL_FORCEFEEDBACK 0x00000100

#define DI8DEVTYPE_DEVICE 0x11
#define DI8DEVTYPE_MOUSE 0x12
#define DI8DEVTYPE_KEYBOARD 0x13
#define DI8DEVTYPE_JOYSTICK 0x14
#define DI8DEVTYPE_GAMEPAD 0x15
#define DI8DEVTYPEMOUSE_UNKNOWN 1
#define DI8DEVTYPEMOUSE_TRADITIONAL 2
#define DI8DEVTYPEKEYBOARD_PCENH 4
#define DIDC_ATTACHED 0x00000001
#define DIDC_POLLEDDEVICE 0x00000002
#define DIDC_EMULATED 0x00000004

#define DISCL_EXCLUSIVE 0x00000001
#define DISCL_NONEXCLUSIVE 0x00000002
#define DISCL_FOREGROUND 0x00000004
#define DISCL_BACKGROUND 0x00000008
#define DISCL_NOWINKEY 0x00000010

#define DIDF_ABSAXIS 0x00000001
#define DIDF_RELAXIS 0x00000002

#define DIDFT_ALL 0x00000000
#define DIDFT_RELAXIS 0x00000001
#define DIDFT_ABSAXIS 0x00000002
#define DIDFT_AXIS 0x00000003
#define DIDFT_PSHBUTTON 0x00000004
#define DIDFT_TGLBUTTON 0x00000008
#define DIDFT_BUTTON 0x0000000C
#define DIDFT_POV 0x00000010
#define DIDFT_COLLECTION 0x00000040
#define DIDFT_NODATA 0x00000080
#define DIDFT_ANYINSTANCE 0x00FFFF00
#define DIDFT_INSTANCEMASK DIDFT_ANYINSTANCE
#define DIDFT_MAKEINSTANCE(n) ((WORD)(n) << 8)
#define DIDFT_GETTYPE(n) LOBYTE(n)
#define DIDFT_GETINSTANCE(n) LOWORD((n) >> 8)
#define DIDFT_FFACTUATOR 0x01000000
#define DIDFT_FFEFFECTTRIGGER 0x02000000
#define DIDFT_OUTPUT 0x10000000
#define DIDFT_NOCOLLECTION 0x00FFFF00
#define DIDFT_OPTIONAL 0x80000000
#define DIDOI_ASPECTPOSITION 0x00000100

#define DIPH_DEVICE 0
#define DIPH_BYOFFSET 1
#define DIPH_BYID 2
#define DIPH_BYUSAGE 3
/* property GUIDs are small integers cast to REFGUID */
#define MAKEDIPROP(prop) (*(const GUID *)(prop))
#define DIPROP_BUFFERSIZE MAKEDIPROP(1)
#define DIPROP_AXISMODE MAKEDIPROP(2)
#define DIPROPAXISMODE_ABS 0
#define DIPROPAXISMODE_REL 1
#define DIPROP_GRANULARITY MAKEDIPROP(3)
#define DIPROP_RANGE MAKEDIPROP(4)
#define DIPROP_DEADZONE MAKEDIPROP(5)
#define DIPROP_SATURATION MAKEDIPROP(6)
#define DIPROP_FFGAIN MAKEDIPROP(7)
#define DIPROP_FFLOAD MAKEDIPROP(8)
#define DIPROP_AUTOCENTER MAKEDIPROP(9)
#define DIPROP_CALIBRATIONMODE MAKEDIPROP(10)
#define DIPROP_KEYNAME MAKEDIPROP(20)
#define DIPROP_SCANCODE MAKEDIPROP(23)
#define DIGDD_PEEK 0x00000001
#define DIENUM_STOP 0
#define DIENUM_CONTINUE 1

/* DIK_* scan codes (set 1, extended keys | 0x80) */
#define DIK_ESCAPE 0x01
#define DIK_1 0x02
#define DIK_2 0x03
#define DIK_3 0x04
#define DIK_4 0x05
#define DIK_5 0x06
#define DIK_6 0x07
#define DIK_7 0x08
#define DIK_8 0x09
#define DIK_9 0x0A
#define DIK_0 0x0B
#define DIK_MINUS 0x0C
#define DIK_EQUALS 0x0D
#define DIK_BACK 0x0E
#define DIK_TAB 0x0F
#define DIK_Q 0x10
#define DIK_W 0x11
#define DIK_E 0x12
#define DIK_R 0x13
#define DIK_T 0x14
#define DIK_Y 0x15
#define DIK_U 0x16
#define DIK_I 0x17
#define DIK_O 0x18
#define DIK_P 0x19
#define DIK_LBRACKET 0x1A
#define DIK_RBRACKET 0x1B
#define DIK_RETURN 0x1C
#define DIK_LCONTROL 0x1D
#define DIK_A 0x1E
#define DIK_S 0x1F
#define DIK_D 0x20
#define DIK_F 0x21
#define DIK_G 0x22
#define DIK_H 0x23
#define DIK_J 0x24
#define DIK_K 0x25
#define DIK_L 0x26
#define DIK_SEMICOLON 0x27
#define DIK_APOSTROPHE 0x28
#define DIK_GRAVE 0x29
#define DIK_LSHIFT 0x2A
#define DIK_BACKSLASH 0x2B
#define DIK_Z 0x2C
#define DIK_X 0x2D
#define DIK_C 0x2E
#define DIK_V 0x2F
#define DIK_B 0x30
#define DIK_N 0x31
#define DIK_M 0x32
#define DIK_COMMA 0x33
#define DIK_PERIOD 0x34
#define DIK_SLASH 0x35
#define DIK_RSHIFT 0x36
#define DIK_MULTIPLY 0x37
#define DIK_LMENU 0x38
#define DIK_SPACE 0x39
#define DIK_CAPITAL 0x3A
#define DIK_F1 0x3B
#define DIK_F2 0x3C
#define DIK_F3 0x3D
#define DIK_F4 0x3E
#define DIK_F5 0x3F
#define DIK_F6 0x40
#define DIK_F7 0x41
#define DIK_F8 0x42
#define DIK_F9 0x43
#define DIK_F10 0x44
#define DIK_NUMLOCK 0x45
#define DIK_SCROLL 0x46
#define DIK_NUMPAD7 0x47
#define DIK_NUMPAD8 0x48
#define DIK_NUMPAD9 0x49
#define DIK_SUBTRACT 0x4A
#define DIK_NUMPAD4 0x4B
#define DIK_NUMPAD5 0x4C
#define DIK_NUMPAD6 0x4D
#define DIK_ADD 0x4E
#define DIK_NUMPAD1 0x4F
#define DIK_NUMPAD2 0x50
#define DIK_NUMPAD3 0x51
#define DIK_NUMPAD0 0x52
#define DIK_DECIMAL 0x53
#define DIK_OEM_102 0x56
#define DIK_F11 0x57
#define DIK_F12 0x58
#define DIK_NUMPADENTER 0x9C
#define DIK_RCONTROL 0x9D
#define DIK_DIVIDE 0xB5
#define DIK_SYSRQ 0xB7
#define DIK_RMENU 0xB8
#define DIK_PAUSE 0xC5
#define DIK_HOME 0xC7
#define DIK_UP 0xC8
#define DIK_PRIOR 0xC9
#define DIK_LEFT 0xCB
#define DIK_RIGHT 0xCD
#define DIK_END 0xCF
#define DIK_DOWN 0xD0
#define DIK_NEXT 0xD1
#define DIK_INSERT 0xD2
#define DIK_DELETE 0xD3
#define DIK_LWIN 0xDB
#define DIK_RWIN 0xDC
#define DIK_APPS 0xDD

/* ---- GUIDs (DirectX SDK values; the engine passes its copies from the exe's .rdata) */
#define FNVNX_DI_GUID(name, l, w1, w2, ...) static const GUID name = {l, w1, w2, {__VA_ARGS__}}
FNVNX_DI_GUID(CLSID_DirectInput8, 0x25e609e4, 0xb259, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(CLSID_DirectInputDevice8, 0x25e609e5, 0xb259, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(IID_IDirectInput8A, 0xbf798030, 0x483a, 0x4da2, 0xaa, 0x99, 0x5d, 0x64, 0xed, 0x36, 0x97, 0x00);
FNVNX_DI_GUID(IID_IDirectInputDevice8A, 0x54d41080, 0xdc15, 0x4833, 0xa4, 0x1b, 0x74, 0x8f, 0x73, 0xa3, 0x81, 0x79);
FNVNX_DI_GUID(GUID_SysMouse, 0x6f1d2b60, 0xd5a0, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_SysKeyboard, 0x6f1d2b61, 0xd5a0, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_Joystick, 0x6f1d2b70, 0xd5a0, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_XAxis, 0xa36d02e0, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_YAxis, 0xa36d02e1, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_ZAxis, 0xa36d02e2, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_RxAxis, 0xa36d02f4, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_RyAxis, 0xa36d02f5, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_RzAxis, 0xa36d02e3, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_Slider, 0xa36d02e4, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_Button, 0xa36d02f0, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_Key, 0x55728220, 0xd33c, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
FNVNX_DI_GUID(GUID_POV, 0xa36d02f2, 0xc9f3, 0x11cf, 0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);

/* ---- interfaces */
struct IDirectInputDevice8A;
/* A force-feedback effect object. Neither device has force feedback (DIDC_FORCEFEEDBACK clear), so CreateEffect never
 * returns one; the interface is declared completely because CreateEffect's result type names it. */
struct IDirectInputEffect : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE hinst, DWORD dwVersion, REFGUID rguid) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE GetEffectGuid(LPGUID pguid) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE GetParameters(void *peff, DWORD dwFlags) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE SetParameters(const void *peff, DWORD dwFlags) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE Start(DWORD dwIterations, DWORD dwFlags) = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE Stop() = 0;
    /* 9 */ virtual HRESULT STDMETHODCALLTYPE GetEffectStatus(LPDWORD pdwFlags) = 0;
    /* 10 */ virtual HRESULT STDMETHODCALLTYPE Download() = 0;
    /* 11 */ virtual HRESULT STDMETHODCALLTYPE Unload() = 0;
    /* 12 */ virtual HRESULT STDMETHODCALLTYPE Escape(void *pesc) = 0;
};
struct IDirectInputDevice8A : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE GetCapabilities(LPDIDEVCAPS lpDIDevCaps) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE EnumObjects(LPDIENUMDEVICEOBJECTSCALLBACKA lpCallback, LPVOID pvRef,
                                                         DWORD dwFlags) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE GetProperty(REFGUID rguidProp, LPDIPROPHEADER pdiph) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE SetProperty(REFGUID rguidProp, LPCDIPROPHEADER pdiph) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE Acquire() = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE Unacquire() = 0;
    /* 9 */ virtual HRESULT STDMETHODCALLTYPE GetDeviceState(DWORD cbData, LPVOID lpvData) = 0;
    /* 10 */ virtual HRESULT STDMETHODCALLTYPE GetDeviceData(DWORD cbObjectData, LPDIDEVICEOBJECTDATA rgdod,
                                                            LPDWORD pdwInOut, DWORD dwFlags) = 0;
    /* 11 */ virtual HRESULT STDMETHODCALLTYPE SetDataFormat(LPCDIDATAFORMAT lpdf) = 0;
    /* 12 */ virtual HRESULT STDMETHODCALLTYPE SetEventNotification(HANDLE hEvent) = 0;
    /* 13 */ virtual HRESULT STDMETHODCALLTYPE SetCooperativeLevel(HWND hwnd, DWORD dwFlags) = 0;
    /* 14 */ virtual HRESULT STDMETHODCALLTYPE GetObjectInfo(LPDIDEVICEOBJECTINSTANCEA pdidoi, DWORD dwObj,
                                                            DWORD dwHow) = 0;
    /* 15 */ virtual HRESULT STDMETHODCALLTYPE GetDeviceInfo(LPDIDEVICEINSTANCEA pdidi) = 0;
    /* 16 */ virtual HRESULT STDMETHODCALLTYPE RunControlPanel(HWND hwndOwner, DWORD dwFlags) = 0;
    /* 17 */ virtual HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE hinst, DWORD dwVersion, REFGUID rguid) = 0;
    /* 18 */ virtual HRESULT STDMETHODCALLTYPE CreateEffect(REFGUID rguid, const void *lpeff,
                                                           IDirectInputEffect **ppdeff, LPUNKNOWN punkOuter) = 0;
    /* 19 */ virtual HRESULT STDMETHODCALLTYPE EnumEffects(void *lpCallback, LPVOID pvRef, DWORD dwEffType) = 0;
    /* 20 */ virtual HRESULT STDMETHODCALLTYPE GetEffectInfo(void *pdei, REFGUID rguid) = 0;
    /* 21 */ virtual HRESULT STDMETHODCALLTYPE GetForceFeedbackState(LPDWORD pdwOut) = 0;
    /* 22 */ virtual HRESULT STDMETHODCALLTYPE SendForceFeedbackCommand(DWORD dwFlags) = 0;
    /* 23 */ virtual HRESULT STDMETHODCALLTYPE EnumCreatedEffectObjects(void *lpCallback, LPVOID pvRef,
                                                                       DWORD fl) = 0;
    /* 24 */ virtual HRESULT STDMETHODCALLTYPE Escape(void *pesc) = 0;
    /* 25 */ virtual HRESULT STDMETHODCALLTYPE Poll() = 0;
    /* 26 */ virtual HRESULT STDMETHODCALLTYPE SendDeviceData(DWORD cbObjectData, LPCDIDEVICEOBJECTDATA rgdod,
                                                             LPDWORD pdwInOut, DWORD fl) = 0;
    /* 27 */ virtual HRESULT STDMETHODCALLTYPE EnumEffectsInFile(LPCSTR lpszFileName, void *pec, LPVOID pvRef,
                                                                DWORD dwFlags) = 0;
    /* 28 */ virtual HRESULT STDMETHODCALLTYPE WriteEffectToFile(LPCSTR lpszFileName, DWORD dwEntries, void *rgDiFileEft,
                                                                DWORD dwFlags) = 0;
    /* 29 */ virtual HRESULT STDMETHODCALLTYPE BuildActionMap(void *lpdiaf, LPCSTR lpszUserName, DWORD dwFlags) = 0;
    /* 30 */ virtual HRESULT STDMETHODCALLTYPE SetActionMap(void *lpdiaf, LPCSTR lpszUserName, DWORD dwFlags) = 0;
    /* 31 */ virtual HRESULT STDMETHODCALLTYPE GetImageInfo(void *lpdiDevImageInfoHeader) = 0;
};

struct IDirectInput8A : public IUnknown
{
    /* 3 */ virtual HRESULT STDMETHODCALLTYPE CreateDevice(REFGUID rguid, IDirectInputDevice8A **lplpDirectInputDevice,
                                                          LPUNKNOWN pUnkOuter) = 0;
    /* 4 */ virtual HRESULT STDMETHODCALLTYPE EnumDevices(DWORD dwDevType, LPDIENUMDEVICESCALLBACKA lpCallback,
                                                         LPVOID pvRef, DWORD dwFlags) = 0;
    /* 5 */ virtual HRESULT STDMETHODCALLTYPE GetDeviceStatus(REFGUID rguidInstance) = 0;
    /* 6 */ virtual HRESULT STDMETHODCALLTYPE RunControlPanel(HWND hwndOwner, DWORD dwFlags) = 0;
    /* 7 */ virtual HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE hinst, DWORD dwVersion) = 0;
    /* 8 */ virtual HRESULT STDMETHODCALLTYPE FindDevice(REFGUID rguidClass, LPCSTR ptszName, LPGUID pguidInstance) = 0;
    /* 9 */ virtual HRESULT STDMETHODCALLTYPE EnumDevicesBySemantics(LPCSTR ptszUserName, void *lpdiActionFormat,
                                                                    void *lpCallback, LPVOID pvRef, DWORD dwFlags) = 0;
    /* 10 */ virtual HRESULT STDMETHODCALLTYPE ConfigureDevices(void *lpdiCallback, void *lpdiCDParams, DWORD dwFlags,
                                                               LPVOID pvRefData) = 0;
};
typedef IDirectInput8A IDirectInput8;
typedef IDirectInputDevice8A IDirectInputDevice8;
typedef IDirectInput8A *LPDIRECTINPUT8A, *LPDIRECTINPUT8;
typedef IDirectInputDevice8A *LPDIRECTINPUTDEVICE8A, *LPDIRECTINPUTDEVICE8;
#define IID_IDirectInput8 IID_IDirectInput8A
#define IID_IDirectInputDevice8 IID_IDirectInputDevice8A

extern "C" HRESULT WINAPI DirectInput8Create(HINSTANCE hinst, DWORD dwVersion, REFIID riidltf, LPVOID *ppvOut,
                                             LPUNKNOWN punkOuter);

#endif
