/* fnv-nx shim: <xinput.h> - XInput 1.3 (stream S7-audio-input, docs/AUDIO_INPUT.md).
 * The exe imports only XInputGetState (xinput1_3.dll); the rest is provided for GetProcAddress users.
 * Implementation: platform/input/xinput.c (libnx hid on Horizon, an injectable source on the host). */
#ifndef FNVNX_SHIM_XINPUT_H
#define FNVNX_SHIM_XINPUT_H
#include "fnvnx_win32.h"
/* outside the game build (host tests) fnvnx_compat.h is not force-included */
#ifndef STDMETHODCALLTYPE
#define STDMETHODCALLTYPE
#endif
#ifndef WINAPI
#define WINAPI
#endif

#define XINPUT_DEVTYPE_GAMEPAD 0x01
#define XINPUT_DEVSUBTYPE_GAMEPAD 0x01
#define XINPUT_FLAG_GAMEPAD 0x00000001
#define XUSER_MAX_COUNT 4
#define XUSER_INDEX_ANY 0x000000FF

#define XINPUT_GAMEPAD_DPAD_UP 0x0001
#define XINPUT_GAMEPAD_DPAD_DOWN 0x0002
#define XINPUT_GAMEPAD_DPAD_LEFT 0x0004
#define XINPUT_GAMEPAD_DPAD_RIGHT 0x0008
#define XINPUT_GAMEPAD_START 0x0010
#define XINPUT_GAMEPAD_BACK 0x0020
#define XINPUT_GAMEPAD_LEFT_THUMB 0x0040
#define XINPUT_GAMEPAD_RIGHT_THUMB 0x0080
#define XINPUT_GAMEPAD_LEFT_SHOULDER 0x0100
#define XINPUT_GAMEPAD_RIGHT_SHOULDER 0x0200
#define XINPUT_GAMEPAD_A 0x1000
#define XINPUT_GAMEPAD_B 0x2000
#define XINPUT_GAMEPAD_X 0x4000
#define XINPUT_GAMEPAD_Y 0x8000

#define XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE 7849
#define XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE 8689
#define XINPUT_GAMEPAD_TRIGGER_THRESHOLD 30

#ifndef ERROR_SUCCESS
#define ERROR_SUCCESS 0L
#endif
#ifndef ERROR_BAD_ARGUMENTS
#define ERROR_BAD_ARGUMENTS 160L
#endif
#ifndef ERROR_DEVICE_NOT_CONNECTED
#define ERROR_DEVICE_NOT_CONNECTED 1167L
#endif

typedef struct _XINPUT_GAMEPAD {
    WORD wButtons;
    BYTE bLeftTrigger;
    BYTE bRightTrigger;
    SHORT sThumbLX;
    SHORT sThumbLY;
    SHORT sThumbRX;
    SHORT sThumbRY;
} XINPUT_GAMEPAD, *PXINPUT_GAMEPAD;

typedef struct _XINPUT_STATE {
    DWORD dwPacketNumber;
    XINPUT_GAMEPAD Gamepad;
} XINPUT_STATE, *PXINPUT_STATE;

typedef struct _XINPUT_VIBRATION {
    WORD wLeftMotorSpeed;
    WORD wRightMotorSpeed;
} XINPUT_VIBRATION, *PXINPUT_VIBRATION;

typedef struct _XINPUT_CAPABILITIES {
    BYTE Type;
    BYTE SubType;
    WORD Flags;
    XINPUT_GAMEPAD Gamepad;
    XINPUT_VIBRATION Vibration;
} XINPUT_CAPABILITIES, *PXINPUT_CAPABILITIES;

#if defined(__ILP32__) || defined(_WIN32)
static_assert(sizeof(XINPUT_GAMEPAD) == 12, "XINPUT_GAMEPAD");
static_assert(sizeof(XINPUT_STATE) == 16, "XINPUT_STATE");
static_assert(sizeof(XINPUT_CAPABILITIES) == 20, "XINPUT_CAPABILITIES");
#endif

#ifdef __cplusplus
extern "C" {
#endif
DWORD WINAPI XInputGetState(DWORD dwUserIndex, XINPUT_STATE *pState);
DWORD WINAPI XInputSetState(DWORD dwUserIndex, XINPUT_VIBRATION *pVibration);
DWORD WINAPI XInputGetCapabilities(DWORD dwUserIndex, DWORD dwFlags, XINPUT_CAPABILITIES *pCapabilities);
void WINAPI XInputEnable(BOOL enable);
#ifdef __cplusplus
}
#endif
#endif
