/* swkbd.cpp - text entry through the Switch system software keyboard (the owner's rule: it handles all text entry).
 *
 * The game gets keyboard text from DirectInput (buffered and immediate keyboard state), the message queue
 * (WM_KEYDOWN/WM_KEYUP; TranslateMessage makes WM_CHAR from them) and GetAsyncKeyState; it imports no ToAscii, MapVirtualKey
 * or GetKeyState (the manifest's imports.tsv). All of these are fed from one key edge, fnvnx_swkbd_inject, exactly as a
 * real key would feed them: fnvnx_win_key (message queue, key state) and fnvnx_input_key (DirectInput, DIK = set-1 scan
 * code of the key).
 *
 * The rule: a character is typed by the key and Shift state under which the platform's own layout table
 * (fnvnx_win_vk_char, the table TranslateMessage uses) gives that character. The inverse is computed from that table
 * over every main-block virtual key and both Shift states, so a layout change cannot make the two disagree. A character
 * no key makes (outside printable ASCII on the en-US layout) is refused at entry: the applet is kept open on it with the
 * index of the refused unit, and it is never dropped. Strokes: Shift down, key down, key up, Shift up, one edge per
 * FNVNX_SWKBD_EDGE_NS, so a game that polls once per frame at 30 fps or more sees every edge. */
#include "input.h"
#include "../os/os.h"

#include <string.h>
#include <vector>

extern "C" {
int fnvnx_win_vk_char(unsigned vk, int sh, int ctl, int caps, int num);
unsigned fnvnx_win_vk_scan(unsigned vk, unsigned *ext);
void fnvnx_win_key(unsigned vk, unsigned scan, int down, unsigned flags);
}

#define VK_SHIFT_ 0x10
#define SCAN_LSHIFT 0x2A

struct Stroke { uint8_t vk, scan; bool shift; };
static Stroke g_tab[128];   /* by character; vk 0: none */
static bool g_tab_built;
static os_lock_t g_lock;
static std::vector<uint8_t> g_edges;   /* vk, scan, down: three bytes per edge */
static size_t g_head;
static uint64_t g_last_ns;
static bool g_combo_held;

static void build(void)
{
    if (g_tab_built) return;
    for (int sh = 0; sh < 2; sh++)   /* unshifted first: a character with both keeps the plain one */
        for (unsigned vk = 1; vk < 255; vk++) {
            if ((vk >= 0x10 && vk <= 0x12) || (vk >= 0x60 && vk <= 0x6F)) continue;   /* modifiers; the keypad: the main block types text */
            unsigned ext = 0, scan = fnvnx_win_vk_scan(vk, &ext);
            if (!scan || ext) continue;   /* DIK = scan only for the keys without the extended prefix */
            int c = fnvnx_win_vk_char(vk, sh, 0, 0, 0);
            if (c < 0x20 || c > 0x7E || g_tab[c].vk) continue;   /* controls are keys of their own, not text */
            g_tab[c] = { (uint8_t)vk, (uint8_t)scan, sh != 0 };
        }
    g_tab_built = true;
}

extern "C" int fnvnx_swkbd_stroke(uint16_t ch, unsigned *vk, unsigned *scan, int *shift)
{
    os_lock(&g_lock);
    build();
    os_unlock(&g_lock);
    if (ch >= 128 || !g_tab[ch].vk) return 0;
    *vk = g_tab[ch].vk; *scan = g_tab[ch].scan; *shift = g_tab[ch].shift;
    return 1;
}

extern "C" void fnvnx_swkbd_inject(unsigned vk, unsigned scan, int down)
{
    fnvnx_win_key(vk, scan, down, 0);                      /* queue, WM_CHAR source, GetAsyncKeyState */
    fnvnx_input_key((uint8_t)scan, down);                  /* DirectInput: state and buffered events */
}

extern "C" unsigned fnvnx_swkbd_pending(void)
{
    os_lock(&g_lock);
    unsigned n = (unsigned)((g_edges.size() - g_head) / 3);
    os_unlock(&g_lock);
    return n;
}

extern "C" void fnvnx_swkbd_pump(uint64_t now)
{
    uint8_t e[3];
    os_lock(&g_lock);
    bool due = g_head < g_edges.size() && (!g_last_ns || now - g_last_ns >= FNVNX_SWKBD_EDGE_NS);
    if (due) {
        memcpy(e, &g_edges[g_head], 3);
        g_head += 3;
        g_last_ns = now ? now : 1;
        if (g_head == g_edges.size()) { g_edges.clear(); g_head = 0; }
    }
    os_unlock(&g_lock);
    if (due) fnvnx_swkbd_inject(e[0], e[1], e[2]);   /* outside the lock: the game's window procedure may run from here */
}

static size_t check(const uint16_t *t, size_t n, void *)
{
    unsigned vk, sc;
    int sh;
    if (n > FNVNX_SWKBD_MAX_LEN) return FNVNX_SWKBD_MAX_LEN + 1;
    for (size_t i = 0; i < n; i++)
        if (!fnvnx_swkbd_stroke(t[i], &vk, &sc, &sh)) return i + 1;
    return 0;
}

extern "C" int fnvnx_swkbd_enter(void)
{
    uint16_t text[FNVNX_SWKBD_MAX_LEN];
    size_t n = 0;
    int r = os_text_entry(FNVNX_SWKBD_MAX_LEN, check, NULL, text, &n);
    if (r <= 0) return r;
    std::vector<uint8_t> e;   /* built outside the lock: fnvnx_swkbd_stroke takes it */
    for (size_t i = 0; i < n; i++) {
        unsigned vk, sc;
        int sh;
        fnvnx_swkbd_stroke(text[i], &vk, &sc, &sh);   /* every unit passed check */
        auto put = [&e](unsigned v, unsigned s, unsigned d) { e.push_back((uint8_t)v); e.push_back((uint8_t)s); e.push_back((uint8_t)d); };
        if (sh) put(VK_SHIFT_, SCAN_LSHIFT, 1);
        put(vk, sc, 1);
        put(vk, sc, 0);
        if (sh) put(VK_SHIFT_, SCAN_LSHIFT, 0);
    }
    os_lock(&g_lock);
    g_edges.insert(g_edges.end(), e.begin(), e.end());
    os_unlock(&g_lock);
    return 1;
}

/* the combination is withheld from the game from its first sample until both clicks are up, and opens the applet once */
extern "C" void fnvnx_swkbd_filter(uint64_t *b)
{
    bool both = (*b & FNVNX_SWKBD_COMBO) == FNVNX_SWKBD_COMBO, open = false;
    os_lock(&g_lock);
    if (both && !g_combo_held) { g_combo_held = true; open = true; }
    if (g_combo_held) {
        if (!(*b & FNVNX_SWKBD_COMBO)) g_combo_held = false;
        else *b &= ~(uint64_t)FNVNX_SWKBD_COMBO;
    }
    os_unlock(&g_lock);
    if (open) fnvnx_swkbd_enter();   /* modal, like the system's own applet: no platform lock is held */
}
