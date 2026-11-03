/* input.h - the input layer under XInput and DirectInput (docs/AUDIO_INPUT.md "Input").
 *
 * Producers (input_horizon.cpp on the Switch, the tests on a host) feed neutral events here; the API
 * implementations (xinput.cpp, dinput.cpp) consume them. Fixed-width C only, so the Horizon side can be
 * LP64-clean.
 */
#ifndef FNVNX_INPUT_H
#define FNVNX_INPUT_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- gamepads: libnx HidNpadButton bit positions (copied so the mapping is host-testable) */
#define FNVNX_NPAD_A (1ull << 0)        /* right face button */
#define FNVNX_NPAD_B (1ull << 1)        /* bottom */
#define FNVNX_NPAD_X (1ull << 2)        /* top */
#define FNVNX_NPAD_Y (1ull << 3)        /* left */
#define FNVNX_NPAD_STICKL (1ull << 4)
#define FNVNX_NPAD_STICKR (1ull << 5)
#define FNVNX_NPAD_L (1ull << 6)
#define FNVNX_NPAD_R (1ull << 7)
#define FNVNX_NPAD_ZL (1ull << 8)
#define FNVNX_NPAD_ZR (1ull << 9)
#define FNVNX_NPAD_PLUS (1ull << 10)
#define FNVNX_NPAD_MINUS (1ull << 11)
#define FNVNX_NPAD_LEFT (1ull << 12)
#define FNVNX_NPAD_UP (1ull << 13)
#define FNVNX_NPAD_RIGHT (1ull << 14)
#define FNVNX_NPAD_DOWN (1ull << 15)
#define FNVNX_NPAD_LEFT_SL (1ull << 24)
#define FNVNX_NPAD_LEFT_SR (1ull << 25)
#define FNVNX_NPAD_RIGHT_SL (1ull << 26)
#define FNVNX_NPAD_RIGHT_SR (1ull << 27)

typedef struct fnvnx_pad_raw {
    uint32_t connected;
    uint64_t buttons;                /* FNVNX_NPAD_* */
    int32_t lx, ly, rx, ry;          /* libnx HidAnalogStickState: -32767..32767, y up */
} fnvnx_pad_raw;

/* face-button layout: LABEL maps Nintendo A->XINPUT A (confirm on the right, the Switch convention);
 * POSITIONAL maps by position (Nintendo B, the bottom button -> XINPUT A, as on an Xbox pad). */
#define FNVNX_PAD_LAYOUT_LABEL 0
#define FNVNX_PAD_LAYOUT_POSITIONAL 1
void fnvnx_pad_set_layout(int layout);   /* default LABEL; $FNVNX_PAD_LAYOUT=positional */
int fnvnx_pad_get_layout(void);

/* pure mapping (tests): raw -> XINPUT_GAMEPAD fields */
typedef struct fnvnx_xpad {
    uint16_t buttons;
    uint8_t lt, rt;
    int16_t lx, ly, rx, ry;
} fnvnx_xpad;
void fnvnx_pad_map(const fnvnx_pad_raw *raw, int layout, fnvnx_xpad *out);

/* pad source: reads player `index` (0..3); returns 0 when disconnected. vibrate: amplitudes 0..1. */
typedef struct fnvnx_pad_source {
    int (*read)(unsigned index, fnvnx_pad_raw *out);
    void (*vibrate)(unsigned index, float low, float high);
} fnvnx_pad_source;
void fnvnx_pad_set_source(const fnvnx_pad_source *src);   /* NULL: built-in (hid on Horizon, injected on host) */
void fnvnx_pad_inject(unsigned index, const fnvnx_pad_raw *raw);   /* host source */
void fnvnx_pad_last_vibration(unsigned index, float *low, float *high);   /* host source */

/* ---- system keyboard and mouse for DirectInput (any thread) */
void fnvnx_input_key(uint8_t dik, int down);                 /* DIK_* scan code */
void fnvnx_input_mouse_move(int32_t dx, int32_t dy, int32_t dwheel);
void fnvnx_input_mouse_button(unsigned button, int down);    /* 0 left, 1 right, 2 middle, 3.. */
uint8_t fnvnx_hid_usage_to_dik(uint8_t usage);               /* USB HID keyboard usage -> DIK, 0 if none */

/* ---- touch screen as mouse (pure state machine, driven by input_horizon.cpp or tests)
 * one finger: moves the mouse (relative, scaled); a short tap clicks left; a two-finger tap clicks right. */
typedef struct fnvnx_touch_point { int32_t x, y; uint32_t id; } fnvnx_touch_point;
void fnvnx_touch_reset(void);
void fnvnx_touch_frame(const fnvnx_touch_point *pts, unsigned count, uint64_t time_ms);

/* pumps the platform sources (Horizon hid); called by the APIs before they read state; throttled */
void fnvnx_input_pump(void);

/* registers dinput8 / xinput1_3 (and the audio modules) with the runtime; see register.cpp */
void fnvnx_audio_input_register(void);

#ifdef __cplusplus
}
#endif
#endif
