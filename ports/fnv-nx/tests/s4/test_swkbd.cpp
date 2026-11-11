/* test_swkbd.cpp - text entry through the system software keyboard (platform/input/swkbd.cpp): the combination on the pad,
 * the entered text as key strokes through every keyboard channel the game imports (DirectInput buffered and immediate,
 * the message queue with TranslateMessage, GetAsyncKeyState), cancel, and a character no key makes. */
#include "s4_test.h"

#include <dinput.h>
#include <xinput.h>
#include "../../platform/input/input.h"
#include "../../platform/os/os.h"
#include "fnvnx/hostapi.h"

#include <stdint.h>
#include <stdio.h>
#include <string>
#include <vector>

extern "C" int fnvnx_win_vk_char(unsigned vk, int sh, int ctl, int caps, int num);

namespace {
LRESULT wp(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }
std::vector<uint16_t> u16(const char *s) { std::vector<uint16_t> v; while (*s) v.push_back((uint8_t)*s++); return v; }
void say(const char *s) { auto v = u16(s); os_text_script_push(v.data(), v.size()); }
void pad(uint64_t b) { fnvnx_pad_raw r = {}; r.connected = 1; r.buttons = b; fnvnx_pad_inject(0, &r); }
WORD xbuttons(void) { XINPUT_STATE st = {}; XInputGetState(0, &st); return st.Gamepad.wButtons; }
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
std::vector<Ev> g_ev;    /* what the game's loop saw: the messages it retrieved */
std::vector<int> g_di;   /* what its buffered DirectInput reads returned: scan code * 2 + down */
IDirectInputDevice8A *g_k;
void msgs(void) { for (const Ev &e : drain()) g_ev.push_back(e); }
void di_read(bool state, DWORD fl = 0)   /* the game's keyboard read: immediate state, or buffered data (consumed unless fl is DIGDD_PEEK) */
{
    BYTE ks[256];
    DIDEVICEOBJECTDATA d[64];
    DWORD n = 64;
    if (state) { CHECK_EQ(g_k->GetDeviceState(256, ks), DI_OK); return; }
    CHECK_EQ(g_k->GetDeviceData(sizeof d[0], d, &n, fl), DI_OK);   /* DI_BUFFEROVERFLOW would be a lost edge */
    if (!fl) for (DWORD i = 0; i < n; i++) g_di.push_back((int)d[i].dwOfs * 2 + ((d[i].dwData & 0x80) ? 1 : 0));
}
bool step(void)   /* one frame of the simplest game: one buffered read, then the message loop */
{
    unsigned n = fnvnx_swkbd_pending();
    di_read(false);
    msgs();
    return fnvnx_swkbd_pending() < n;
}
struct Rig {   /* a window with the focus and an acquired keyboard with a buffer of bufsz events */
    HWND w;
    IDirectInput8A *in = nullptr;
    explicit Rig(unsigned bufsz)
    {
        WNDCLASSA wc = {};
        wc.lpfnWndProc = wp; wc.lpszClassName = "SwK";
        RegisterClassA(&wc);
        w = CreateWindowExA(0, "SwK", "t", 0x90000000u, 0, 0, 100, 100, NULL, NULL, NULL, NULL);
        SetForegroundWindow(w);
        drain();
        CHECK_EQ(DirectInput8Create(nullptr, 0x0800, IID_IDirectInput8A, (void **)&in, nullptr), DI_OK);
        CHECK_EQ(in->CreateDevice(GUID_SysKeyboard, &g_k, nullptr), DI_OK);
        static DIOBJECTDATAFORMAT objs[256];
        for (int i = 0; i < 256; i++) objs[i] = {&GUID_Key, (DWORD)i, (DWORD)(0x80000000u | DIDFT_BUTTON | DIDFT_MAKEINSTANCE(i)), 0};
        DIDATAFORMAT f = {sizeof(DIDATAFORMAT), sizeof(DIOBJECTDATAFORMAT), DIDF_RELAXIS, 256, 256, objs};
        CHECK_EQ(g_k->SetDataFormat(&f), DI_OK);
        DIPROPDWORD bs = {{sizeof(DIPROPDWORD), sizeof(DIPROPHEADER), 0, DIPH_DEVICE}, bufsz};
        CHECK_EQ(g_k->SetProperty(DIPROP_BUFFERSIZE, &bs.diph), DI_OK);
        CHECK_EQ(g_k->Acquire(), DI_OK);
        di_read(false);
        g_di.clear(); g_ev.clear();
        os_text_script_reset();
    }
    ~Rig() { os_text_script_reset(); g_k->Release(); in->Release(); DestroyWindow(w); }
};
std::string sig(void)
{
    std::string r;
    char b[32];
    for (int d : g_di) { snprintf(b, sizeof b, "d%02x%d ", d >> 1, d & 1); r += b; }
    for (const Ev &e : g_ev) { snprintf(b, sizeof b, "m%x:%x ", e.m, (unsigned)e.w); r += b; }
    return r;
}
/* one entry of text with a game that does `reads` DI reads per frame (alternating buffered and immediate), the message loop
 * before them (mode 1), after them (0) or after each of them (2); what it saw is returned as a string */
std::string play(const char *text, int mode, int reads)
{
    g_di.clear(); g_ev.clear();
    say(text);
    press_combo();
    for (int f = 0; f < 2000 && fnvnx_swkbd_pending(); f++) {
        if (mode == 1) msgs();
        for (int i = 0; i < reads; i++) { di_read(i & 1); if (mode == 2) msgs(); }
        if (mode == 0) msgs();
    }
    CHECK_EQ(fnvnx_swkbd_pending(), 0u);
    di_read(false);   /* the last edge is observed by the next read */
    msgs();
    return sig();
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
    Rig rig(64);
    IDirectInputDevice8A *k = g_k;

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
    xbuttons();   /* still held: the applet does not open again; the next poll puts Shift down out (nothing was unobserved) */
    CHECK_EQ(fnvnx_swkbd_pending(), 19u);
    pad(FNVNX_NPAD_STICKR);   /* one click released: the other is still withheld until the combination is over */
    CHECK_EQ(xbuttons() & XINPUT_GAMEPAD_RIGHT_THUMB, 0);
    pad(0); xbuttons();

    CHECK(!(GetAsyncKeyState('C') & 0x8000));
    CHECK(GetAsyncKeyState(0x10) & 0x8000);   /* Shift down */
    unsigned left = fnvnx_swkbd_pending();
    for (int i = 0; i < 5; i++) fnvnx_swkbd_pump();   /* no DI read since Shift went in: nothing more */
    CHECK_EQ(fnvnx_swkbd_pending(), left);
    di_read(false);   /* read, but the message is not retrieved yet: still nothing */
    for (int i = 0; i < 5; i++) fnvnx_swkbd_pump();
    CHECK_EQ(fnvnx_swkbd_pending(), left);
    msgs();           /* both observed: C down is released, and only it */
    for (int i = 0; i < 5; i++) fnvnx_swkbd_pump();
    CHECK_EQ(fnvnx_swkbd_pending(), left - 1);
    CHECK(GetAsyncKeyState('C') & 0x8000);
    BYTE ks[256];
    CHECK_EQ(k->GetDeviceState(256, ks), DI_OK);   /* a state read of the unretrieved C down: it holds the next edge back */
    CHECK_EQ(fnvnx_swkbd_pending(), left - 1);
    CHECK(ks[0x2E] & 0x80); CHECK(ks[0x2A] & 0x80);
    msgs();
    for (int f = 0; f < 100 && fnvnx_swkbd_pending(); f++) step();
    CHECK_EQ(fnvnx_swkbd_pending(), 0u);
    di_read(false); msgs();
    CHECK(!(GetAsyncKeyState('C') & 0x8000)); CHECK(!(GetAsyncKeyState(0x10) & 0x8000));
    CHECK_EQ(k->GetDeviceState(256, ks), DI_OK);
    CHECK(!(ks[0x2E] & 0x80)); CHECK(!(ks[0x2A] & 0x80));

    /* DirectInput buffered: exact scan-code sequence, down then up */
    static const struct { uint8_t sc, down; } want[] = { {0x2A,1},{0x2E,1},{0x2E,0},{0x2A,0}, {0x18,1},{0x18,0}, {0x16,1},{0x16,0}, {0x13,1},{0x13,0},
        {0x17,1},{0x17,0}, {0x12,1},{0x12,0}, {0x13,1},{0x13,0}, {0x39,1},{0x39,0}, {0x07,1},{0x07,0} };
    CHECK_EQ(g_di.size(), 20u);
    for (size_t i = 0; i < g_di.size() && i < 20; i++) CHECK_EQ(g_di[i], want[i].sc * 2 + want[i].down);

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
    g_di.clear(); di_read(false); CHECK(g_di.empty());

    /* the first round has no initial text; a character no key makes is refused, never dropped: the applet is called again with the
     * refused text as its initial text, until a text has strokes */
    os_text_script_reset();
    say("Caf\xE9"); say("Cafe");
    press_combo();
    CHECK_EQ(os_text_script_calls(), 2u);
    uint16_t in[8];
    CHECK_EQ(os_text_script_init(in, 8), 4u);
    CHECK(in[0] == 'C' && in[1] == 'a' && in[2] == 'f' && in[3] == 0xE9);
    CHECK_EQ(fnvnx_swkbd_pending(), 9u);   /* "Cafe": the capital's 4 edges and 3 pairs */
    for (int f = 0; f < 100 && fnvnx_swkbd_pending(); f++) step();
    di_read(false); msgs();
    text.clear();
    for (const Ev &e : g_ev) if (e.m == WM_CHAR) text += (char)e.w;
    g_ev.clear();
    CHECK_STR(text.c_str(), "Cafe");
    os_text_script_reset();
    say("ab");
    press_combo();
    CHECK_EQ(os_text_script_calls(), 1u);
    CHECK_EQ(os_text_script_init(in, 8), (size_t)-1);
    for (int f = 0; f < 100 && fnvnx_swkbd_pending(); f++) step();
    di_read(false); msgs(); g_ev.clear(); g_di.clear();

    /* every refusal calls again with the text just refused, however many there are; the player cancelling ends it with nothing typed */
    os_text_script_reset();
    say("\xE9"); say("\xE8"); say("\xE0");
    press_combo();
    CHECK_EQ(os_text_script_calls(), 4u);   /* the empty script after the third refusal is a closed applet */
    CHECK_EQ(os_text_script_init(in, 8), 1u);
    CHECK_EQ(in[0], 0xE0);
    CHECK_EQ(fnvnx_swkbd_pending(), 0u);

    /* the applet's field takes no more than the 64 units of the entry */
    os_text_script_reset();
    std::string big(FNVNX_SWKBD_MAX_LEN + 1, 'a');
    say(big.c_str());
    press_combo();
    CHECK_EQ(os_text_script_calls(), 1u);
    for (int f = 0; f < 1000 && fnvnx_swkbd_pending(); f++) step();
    di_read(false); msgs();
    CHECK_EQ(g_di.size(), (size_t)(2 * FNVNX_SWKBD_MAX_LEN));
    g_ev.clear(); g_di.clear();
}

/* the contract of os_text_entry (hostapi.h TEXT_ENTRY): bounds, result codes, what a cancel leaves alone */
TEST(swkbd_text_entry_contract)
{
    static uint16_t out[FNVNX_TEXT_ENTRY_MAX + 1];
    size_t n = 7;
    const int bad = -(int)FNVNX_HE_BADINPUT;
    os_text_script_reset();
    CHECK_EQ(os_text_entry(0, NULL, 0, out, &n), bad);
    CHECK_EQ(os_text_entry(FNVNX_TEXT_ENTRY_MAX + 1, NULL, 0, out, &n), bad);
    CHECK_EQ(os_text_entry(4, NULL, 0, NULL, &n), bad);
    CHECK_EQ(os_text_entry(4, NULL, 0, out, NULL), bad);
    CHECK_EQ(os_text_script_calls(), 0u);   /* nothing was shown */
    CHECK_EQ(os_text_entry(FNVNX_TEXT_ENTRY_MAX, NULL, 0, out, &n), 0);   /* an empty script: the player closes the applet */
    CHECK_EQ(n, 7u);                                                      /* cancelled: *n and out untouched */
    os_text_script_cancel();
    out[0] = 0x55;
    CHECK_EQ(os_text_entry(4, NULL, 0, out, &n), 0);
    CHECK(n == 7u && out[0] == 0x55);
    say("abcdef");   /* the field holds 4 units: entered, terminated, no more than max_len */
    CHECK_EQ(os_text_entry(4, NULL, 0, out, &n), 1);
    CHECK_EQ(n, 4u);
    CHECK(out[3] == 'd' && out[4] == 0);
    say("");   /* an empty text is entered too */
    static const uint16_t init[3] = {'x', 'y', 'z'};
    CHECK_EQ(os_text_entry(4, init, 3, out, &n), 1);
    CHECK_EQ(n, 0u);
    CHECK_EQ(out[0], 0);
    uint16_t back[4];
    CHECK_EQ(os_text_script_init(back, 4), 3u);
    CHECK(back[0] == 'x' && back[2] == 'z');
    os_text_script_reset();
}

/* the stream is the same whatever the game's polling rate and loop order: progress is made by reads, not by time */
TEST(swkbd_polling_rate_independent)
{
    Rig rig(64);
    std::string ref = play("Courier 6", 0, 1);
    CHECK(!ref.empty());
    static const struct { int mode, reads; } way[] = { {0,10}, {1,1}, {1,10}, {2,1}, {2,10}, {2,3}, {0,2} };
    for (const auto &w : way) { std::string got = play("Courier 6", w.mode, w.reads); CHECK_STR(got.c_str(), ref.c_str()); }
}

/* a game that does not keep up loses nothing: no read or no acquired device holds the edges, a full buffer holds them too */
TEST(swkbd_holds_until_observed)
{
    Rig rig(2);   /* a buffer of two events */
    say("abc"); press_combo();   /* 6 edges */
    CHECK_EQ(fnvnx_swkbd_pending(), 5u);   /* the first edge went in at once */
    for (int i = 0; i < 50; i++) fnvnx_swkbd_pump();   /* the game never reads: no other */
    CHECK_EQ(fnvnx_swkbd_pending(), 5u);
    for (int i = 0; i < 50; i++) { di_read(false, DIGDD_PEEK); msgs(); }   /* it reads without emptying the buffer: two edges fit, no overflow */
    CHECK_EQ(fnvnx_swkbd_pending(), 4u);
    for (int f = 0; f < 100 && fnvnx_swkbd_pending(); f++) step();
    di_read(false); msgs();
    static const int want[] = { 0x1E*2+1, 0x1E*2, 0x30*2+1, 0x30*2, 0x2E*2+1, 0x2E*2 };
    CHECK_EQ(g_di.size(), 6u);
    for (size_t i = 0; i < g_di.size() && i < 6; i++) CHECK_EQ(g_di[i], want[i]);
    g_di.clear(); g_ev.clear();
    g_k->Unacquire();   /* no acquired keyboard: edges stay queued */
    say("a"); press_combo();
    for (int i = 0; i < 20; i++) { fnvnx_swkbd_pump(); msgs(); }
    CHECK_EQ(fnvnx_swkbd_pending(), 2u);
    CHECK_EQ(g_k->Acquire(), DI_OK);
    di_read(false);   /* acquired and reading again: it resumes */
    for (int f = 0; f < 100 && fnvnx_swkbd_pending(); f++) step();
    di_read(false); msgs();
    CHECK_EQ(g_di.size(), 2u);
}
