/* test_swkbd.cpp - text entry through the system software keyboard (platform/input/swkbd.cpp): the combination on the pad,
 * the entered text as key strokes through every keyboard channel the game imports (DirectInput buffered and immediate,
 * the message queue with TranslateMessage, GetAsyncKeyState), cancel, and a character no key makes. */
#include "s4_test.h"

#include <dinput.h>
#include <xinput.h>
#include "../../platform/input/input.h"
#include "../../platform/os/os.h"

#include <stdint.h>
#include <string>
#include <vector>

extern "C" int fnvnx_win_vk_char(unsigned vk, int sh, int ctl, int caps, int num);

namespace {
LRESULT wp(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }
std::vector<uint16_t> u16(const char *s) { std::vector<uint16_t> v; while (*s) v.push_back((uint8_t)*s++); return v; }
void say(const char *s) { auto v = u16(s); os_text_script_push(v.data(), v.size()); }
void pad(uint64_t b) { fnvnx_pad_raw r = {}; r.connected = 1; r.buttons = b; fnvnx_pad_inject(0, &r); }
WORD xbuttons(void) { XINPUT_STATE st = {}; XInputGetState(0, &st); return st.Gamepad.wButtons; }
uint64_t g_t = 1000000000ull;
void press_combo(void) { pad(0); xbuttons(); pad(FNVNX_SWKBD_COMBO); xbuttons(); pad(0); xbuttons(); }
struct Ev { UINT m; WPARAM w; };
std::vector<Ev> drain(void)   /* every message of the thread; key messages go through TranslateMessage as the game's loop does */
{
    std::vector<Ev> v;
    MSG m;
    while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) {
        if (m.message == WM_KEYDOWN || m.message == WM_KEYUP || m.message == WM_CHAR) v.push_back({m.message, m.wParam});
        if (m.message == WM_KEYDOWN) TranslateMessage(&m);
        else DispatchMessageA(&m);   /* the rest (WM_PAINT, activation) is the window's own business */
    }
    return v;
}
std::vector<Ev> g_ev;   /* what the game's loop saw, frame by frame */
bool step(void)   /* one frame: the pump releases an edge, the loop retrieves and translates what it finds */
{
    unsigned n = fnvnx_swkbd_pending();
    g_t += FNVNX_SWKBD_EDGE_NS;
    fnvnx_swkbd_pump(g_t);
    for (const Ev &e : drain()) g_ev.push_back(e);
    return fnvnx_swkbd_pending() < n;
}
}

TEST(swkbd_layout_inverse)
{
    unsigned vk, sc;
    int sh;
    for (unsigned c = 0x20; c < 0x7F; c++) {   /* every printable ASCII character has a stroke, and the platform's own table gives it back */
        CHECK(fnvnx_swkbd_stroke((uint16_t)c, &vk, &sc, &sh));
        CHECK_EQ(fnvnx_win_vk_char(vk, sh, 0, 0, 0), (int)c);
    }
    CHECK(!fnvnx_swkbd_stroke(0xE9, &vk, &sc, &sh));
    CHECK(!fnvnx_swkbd_stroke(0x0A, &vk, &sc, &sh));
    CHECK(!fnvnx_swkbd_stroke(0x20AC, &vk, &sc, &sh));
    CHECK(fnvnx_swkbd_stroke('C', &vk, &sc, &sh)); CHECK_EQ(sc, 0x2E); CHECK_EQ(sh, 1);
    CHECK(fnvnx_swkbd_stroke('c', &vk, &sc, &sh)); CHECK_EQ(sc, 0x2E); CHECK_EQ(sh, 0);
    CHECK(fnvnx_swkbd_stroke(' ', &vk, &sc, &sh)); CHECK_EQ(sc, 0x39);
}

TEST(swkbd_combo_name_entry)
{
    WNDCLASSA wc = {};
    wc.lpfnWndProc = wp; wc.lpszClassName = "SwK";
    RegisterClassA(&wc);
    HWND w = CreateWindowExA(0, "SwK", "t", 0x90000000u, 0, 0, 100, 100, NULL, NULL, NULL, NULL);
    SetForegroundWindow(w);
    drain();
    IDirectInput8A *in = nullptr;
    CHECK_EQ(DirectInput8Create(nullptr, 0x0800, IID_IDirectInput8A, (void **)&in, nullptr), DI_OK);
    IDirectInputDevice8A *k = nullptr;
    CHECK_EQ(in->CreateDevice(GUID_SysKeyboard, &k, nullptr), DI_OK);
    static DIOBJECTDATAFORMAT objs[256];
    for (int i = 0; i < 256; i++) objs[i] = {&GUID_Key, (DWORD)i, (DWORD)(0x80000000u | DIDFT_BUTTON | DIDFT_MAKEINSTANCE(i)), 0};
    DIDATAFORMAT f = {sizeof(DIDATAFORMAT), sizeof(DIOBJECTDATAFORMAT), DIDF_RELAXIS, 256, 256, objs};
    CHECK_EQ(k->SetDataFormat(&f), DI_OK);
    DIPROPDWORD bs = {{sizeof(DIPROPDWORD), sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, 64};
    CHECK_EQ(k->SetProperty(DIPROP_BUFFERSIZE, &bs.diph), DI_OK);
    CHECK_EQ(k->Acquire(), DI_OK);
    DIDEVICEOBJECTDATA d[64];
    DWORD n = 64;
    k->GetDeviceData(sizeof d[0], d, &n, 0);   /* flush */

    os_text_script_reset();
    say("Courier 6");
    pad(0);
    CHECK_EQ(xbuttons() & (XINPUT_GAMEPAD_LEFT_THUMB | XINPUT_GAMEPAD_RIGHT_THUMB), 0);
    pad(FNVNX_NPAD_STICKL);   /* one click is the game's own button */
    CHECK_EQ(xbuttons() & XINPUT_GAMEPAD_LEFT_THUMB, XINPUT_GAMEPAD_LEFT_THUMB);
    CHECK_EQ(fnvnx_swkbd_pending(), 0u);
    pad(FNVNX_SWKBD_COMBO | FNVNX_NPAD_A);   /* the combination is consumed, other buttons pass */
    WORD b = xbuttons();
    CHECK_EQ(b & (XINPUT_GAMEPAD_LEFT_THUMB | XINPUT_GAMEPAD_RIGHT_THUMB), 0);
    CHECK(b & XINPUT_GAMEPAD_A);
    CHECK_EQ(fnvnx_swkbd_pending(), 20u);   /* 9 characters: 2 edges each, and Shift down/up around the capital C */
    xbuttons();   /* still held: the applet does not open again */
    CHECK_EQ(fnvnx_swkbd_pending(), 20u);
    pad(FNVNX_NPAD_STICKR);   /* one click released: the other is still withheld until the combination is over */
    CHECK_EQ(xbuttons() & XINPUT_GAMEPAD_RIGHT_THUMB, 0);
    pad(0); xbuttons();

    CHECK(!(GetAsyncKeyState('C') & 0x8000));
    CHECK(step());                            /* Shift down: the first edge is due at once */
    CHECK(GetAsyncKeyState(0x10) & 0x8000);
    unsigned left = fnvnx_swkbd_pending();
    fnvnx_swkbd_pump(g_t + 1000);             /* inside the pacing interval: nothing is released */
    CHECK_EQ(fnvnx_swkbd_pending(), left);
    CHECK(step());                            /* C down */
    CHECK(GetAsyncKeyState('C') & 0x8000);
    BYTE ks[256];
    CHECK_EQ(k->GetDeviceState(256, ks), DI_OK);
    CHECK(ks[0x2E] & 0x80); CHECK(ks[0x2A] & 0x80);
    while (fnvnx_swkbd_pending()) CHECK(step());
    CHECK(!(GetAsyncKeyState('C') & 0x8000)); CHECK(!(GetAsyncKeyState(0x10) & 0x8000));
    CHECK_EQ(k->GetDeviceState(256, ks), DI_OK);
    CHECK(!(ks[0x2E] & 0x80)); CHECK(!(ks[0x2A] & 0x80));

    /* DirectInput buffered: exact scan-code sequence, down then up */
    n = 64;
    CHECK_EQ(k->GetDeviceData(sizeof d[0], d, &n, 0), DI_OK);
    static const struct { uint8_t sc, down; } want[] = { {0x2A,1},{0x2E,1},{0x2E,0},{0x2A,0}, {0x18,1},{0x18,0}, {0x16,1},{0x16,0}, {0x13,1},{0x13,0},
        {0x17,1},{0x17,0}, {0x12,1},{0x12,0}, {0x13,1},{0x13,0}, {0x39,1},{0x39,0}, {0x07,1},{0x07,0} };
    CHECK_EQ(n, 20u);
    for (DWORD i = 0; i < n && i < 20; i++) { CHECK_EQ(d[i].dwOfs, want[i].sc); CHECK_EQ((d[i].dwData & 0x80) != 0, want[i].down); }

    /* the message queue: WM_KEYDOWN/WM_KEYUP in the same order, and the WM_CHAR text TranslateMessage derives from them */
    auto ev = g_ev;
    g_ev.clear();
    std::string text;
    int keys = 0;
    for (const Ev &e : ev) { if (e.m == WM_KEYDOWN || e.m == WM_KEYUP) keys++; if (e.m == WM_CHAR) text += (char)e.w; }
    CHECK_EQ(keys, 20);
    CHECK_STR(text.c_str(), "Courier 6");
    CHECK(ev.size() >= 2 && ev[0].m == WM_KEYDOWN && ev[0].w == 0x10 && ev[1].m == WM_KEYDOWN && ev[1].w == 'C');

    /* cancel: no key at all */
    os_text_script_cancel();
    press_combo();
    CHECK_EQ(fnvnx_swkbd_pending(), 0u);
    CHECK(drain().empty() && g_ev.empty());
    n = 64; k->GetDeviceData(sizeof d[0], d, &n, 0); CHECK_EQ(n, 0u);

    /* a character no key makes is refused at entry, never dropped: the next entry of the applet is the one typed */
    os_text_script_reset();
    say("Caf\xE9"); say("Cafe");
    press_combo();
    CHECK_EQ(os_text_script_rejects(), 1u);
    CHECK_EQ(fnvnx_swkbd_pending(), 10u);   /* "Cafe": the capital's 4 edges and 3 pairs */
    while (fnvnx_swkbd_pending()) step();
    text.clear();
    for (const Ev &e : g_ev) if (e.m == WM_CHAR) text += (char)e.w;
    g_ev.clear();
    CHECK_STR(text.c_str(), "Cafe");

    /* longer than the field: refused too */
    os_text_script_reset();
    std::string big(FNVNX_SWKBD_MAX_LEN + 1, 'a');
    say(big.c_str());
    press_combo();
    CHECK_EQ(os_text_script_rejects(), 1u);
    CHECK_EQ(fnvnx_swkbd_pending(), 0u);

    os_text_script_reset();
    k->Release(); in->Release();
    DestroyWindow(w);
}
