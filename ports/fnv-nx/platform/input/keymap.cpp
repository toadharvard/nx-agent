/* keymap.cpp - USB HID keyboard usages (what libnx hidGetKeyboardStates reports) -> DirectInput scan codes,
 * and the touch-as-mouse state machine. Both pure; see input.h. */
#include <dinput.h>
#include "input.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* the platform lane (platform/win32/platstat.cpp; fnvnx_win32_ext.h is not included in C here) */
extern "C" volatile int fnvnx_plat_on;
extern "C" void fnvnx_plat_note(int cls, const char *name, unsigned caller, const char *detail);
#define PLAT_RA ((unsigned)(unsigned long)__builtin_return_address(0))

extern "C" uint8_t fnvnx_hid_usage_to_dik(uint8_t u)
{
    /* letters a..z (usages 4..29) */
    static const uint8_t letters[26] = {DIK_A, DIK_B, DIK_C, DIK_D, DIK_E, DIK_F, DIK_G, DIK_H, DIK_I,
                                        DIK_J, DIK_K, DIK_L, DIK_M, DIK_N, DIK_O, DIK_P, DIK_Q, DIK_R,
                                        DIK_S, DIK_T, DIK_U, DIK_V, DIK_W, DIK_X, DIK_Y, DIK_Z};
    if (u >= 4 && u <= 29) return letters[u - 4];
    if (u >= 30 && u <= 39) return (uint8_t)(DIK_1 + (u - 30));  /* 1..9, 0: DIK_1..DIK_0 are consecutive */
    if (u >= 58 && u <= 67) return (uint8_t)(DIK_F1 + (u - 58)); /* F1..F10 */
    switch (u) {
    case 40: return DIK_RETURN;
    case 41: return DIK_ESCAPE;
    case 42: return DIK_BACK;
    case 43: return DIK_TAB;
    case 44: return DIK_SPACE;
    case 45: return DIK_MINUS;
    case 46: return DIK_EQUALS;
    case 47: return DIK_LBRACKET;
    case 48: return DIK_RBRACKET;
    case 49: return DIK_BACKSLASH;
    case 50: return DIK_BACKSLASH;   /* non-US # */
    case 51: return DIK_SEMICOLON;
    case 52: return DIK_APOSTROPHE;
    case 53: return DIK_GRAVE;
    case 54: return DIK_COMMA;
    case 55: return DIK_PERIOD;
    case 56: return DIK_SLASH;
    case 57: return DIK_CAPITAL;
    case 68: return DIK_F11;
    case 69: return DIK_F12;
    case 70: return DIK_SYSRQ;
    case 71: return DIK_SCROLL;
    case 72: return DIK_PAUSE;
    case 73: return DIK_INSERT;
    case 74: return DIK_HOME;
    case 75: return DIK_PRIOR;
    case 76: return DIK_DELETE;
    case 77: return DIK_END;
    case 78: return DIK_NEXT;
    case 79: return DIK_RIGHT;
    case 80: return DIK_LEFT;
    case 81: return DIK_DOWN;
    case 82: return DIK_UP;
    case 83: return DIK_NUMLOCK;
    case 84: return DIK_DIVIDE;
    case 85: return DIK_MULTIPLY;
    case 86: return DIK_SUBTRACT;
    case 87: return DIK_ADD;
    case 88: return DIK_NUMPADENTER;
    case 89: return DIK_NUMPAD1;
    case 90: return DIK_NUMPAD2;
    case 91: return DIK_NUMPAD3;
    case 92: return DIK_NUMPAD4;
    case 93: return DIK_NUMPAD5;
    case 94: return DIK_NUMPAD6;
    case 95: return DIK_NUMPAD7;
    case 96: return DIK_NUMPAD8;
    case 97: return DIK_NUMPAD9;
    case 98: return DIK_NUMPAD0;
    case 99: return DIK_DECIMAL;
    case 100: return DIK_OEM_102;
    case 101: return DIK_APPS;
    case 102: return 0xDE;   /* DIK_POWER */
    case 103: return 0x8D;   /* DIK_NUMPADEQUALS */
    case 104: return 0x64;   /* DIK_F13 */
    case 105: return 0x65;   /* DIK_F14 */
    case 106: return 0x66;   /* DIK_F15 */
    case 127: return 0xA0;   /* DIK_MUTE */
    case 128: return 0xB0;   /* DIK_VOLUMEUP */
    case 129: return 0xAE;   /* DIK_VOLUMEDOWN */
    case 135: return 0x73;   /* DIK_ABNT_C1 (international 1, "ro") */
    case 136: return 0x70;   /* DIK_KANA */
    case 137: return 0x7D;   /* DIK_YEN */
    case 138: return 0x79;   /* DIK_CONVERT */
    case 139: return 0x7B;   /* DIK_NOCONVERT */
    case 224: return DIK_LCONTROL;
    case 225: return DIK_LSHIFT;
    case 226: return DIK_LMENU;
    case 227: return DIK_LWIN;
    case 228: return DIK_RCONTROL;
    case 229: return DIK_RSHIFT;
    case 230: return DIK_RMENU;
    case 231: return DIK_RWIN;
    }
    if (fnvnx_plat_on && u > 3) {   /* 0..3: no key / rollover errors. The platform lane: 4 = input (platstat.cpp) */
        char d[24];
        snprintf(d, sizeof d, "hid usage %u", (unsigned)u);
        fnvnx_plat_note(4, "fnvnx_hid_usage_to_dik", 0, d);
    }
    return 0;
}

/* ---- touch as mouse
 * Touch panel coordinates are 1280x720. Motion is relative: 2 mouse counts per touch pixel. A tap is a contact
 * shorter than TAP_MS that moved less than TAP_PX: one finger -> left click, two fingers -> right click. */
#define TAP_MS 220
#define TAP_PX 12
#define SCALE 2

static struct {
    unsigned prev_count, max_count;
    uint32_t id;
    int32_t x, y, sx, sy;
    uint64_t t0;
    int moved;
} g_t;

extern "C" void fnvnx_touch_reset(void) { memset(&g_t, 0, sizeof g_t); }

extern "C" void fnvnx_touch_frame(const fnvnx_touch_point *p, unsigned n, uint64_t t)
{
    if (n && !g_t.prev_count) {           /* contact starts */
        g_t.id = p[0].id;
        g_t.x = g_t.sx = p[0].x;
        g_t.y = g_t.sy = p[0].y;
        g_t.t0 = t;
        g_t.moved = 0;
        g_t.max_count = n;
    } else if (n) {
        if (n > g_t.max_count) g_t.max_count = n;
        /* follow the first finger while it stays down; re-anchor when it changes */
        const fnvnx_touch_point *f = &p[0];
        for (unsigned i = 0; i < n; i++) if (p[i].id == g_t.id) f = &p[i];
        if (f->id != g_t.id) { g_t.id = f->id; g_t.x = f->x; g_t.y = f->y; }
        int32_t dx = f->x - g_t.x, dy = f->y - g_t.y;
        if (abs(f->x - g_t.sx) >= TAP_PX || abs(f->y - g_t.sy) >= TAP_PX) g_t.moved = 1;
        if (g_t.max_count == 1 && (dx || dy)) fnvnx_input_mouse_move(dx * SCALE, dy * SCALE, 0);
        if (g_t.max_count == 2 && dy) fnvnx_input_mouse_move(0, 0, -dy * 4);   /* two-finger drag: wheel */
        g_t.x = f->x;
        g_t.y = f->y;
    } else if (g_t.prev_count) {          /* contact ends */
        if (!g_t.moved && t - g_t.t0 < TAP_MS) {
            unsigned b = g_t.max_count >= 2 ? 1 : 0;
            fnvnx_input_mouse_button(b, 1);
            fnvnx_input_mouse_button(b, 0);
        }
        g_t.max_count = 0;
    }
    g_t.prev_count = n;
}
