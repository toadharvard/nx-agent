/* host_textentry.inc - TEXT_ENTRY (hostapi.h 36): the system software keyboard (libnx swkbd), modal. Standalone: needs <switch.h> and
 * host_textconv.h. The contract is the one in hostapi.h; this file adds nothing to it.
 *   max_len outside 1..FNVNX_TEXT_ENTRY_MAX, out or n null -> -FNVNX_HE_BADINPUT, nothing shown
 *   the keyboard starts with init cut to max_len units (te_cut16); the applet returns UTF-8 (te_utf8_to_16)
 *   swkbdShow LibnxError_LibAppletBadExit -> 0 (cancelled, out and *n untouched); any other failure -> -(Result)
 *   a text of more than max_len units (characters above U+FFFF, which swkbd counts as one) is not returned: the keyboard opens again with
 *   that text cut to max_len units, until the text fits or the player cancels
 * One keyboard exists: concurrent callers are served one after the other. */
#include "host_textconv.h"
#define TE_BYTES (4 * FNVNX_TEXT_ENTRY_MAX + 1)
static Mutex g_te_lock;
static unsigned char g_te_utf8[TE_BYTES], g_te_init[3 * FNVNX_TEXT_ENTRY_MAX + 1];
static uint16_t g_te_units[TE_BYTES], g_te_cur[FNVNX_TEXT_ENTRY_MAX];
static u64 host_text_entry(const u64 *a) {
    u64 max_len = a[0];
    const uint16_t *init = (const uint16_t *)a[1];
    uint16_t *out = (uint16_t *)a[3];
    uint32_t *n = (uint32_t *)a[4];
    if (max_len == 0 || max_len > FNVNX_TEXT_ENTRY_MAX || !out || !n) return (u64)-(s64)FNVNX_HE_BADINPUT;
    mutexLock(&g_te_lock);
    size_t cur = init ? te_cut16(init, a[2], max_len) : 0;
    if (cur) memcpy(g_te_cur, init, cur * 2);
    u64 r;
    for (;;) {
        g_te_init[te_utf16_to_8(g_te_cur, cur, g_te_init)] = 0;
        SwkbdConfig kbd;
        Result rc = swkbdCreate(&kbd, 0);
        if (R_SUCCEEDED(rc)) {
            swkbdConfigMakePresetDefault(&kbd);
            swkbdConfigSetStringLenMax(&kbd, (u32)max_len);
            swkbdConfigSetInitialText(&kbd, (const char *)g_te_init);
            rc = swkbdShow(&kbd, (char *)g_te_utf8, sizeof g_te_utf8);
            swkbdClose(&kbd);
        }
        if (R_FAILED(rc)) {
            r = rc == LibnxError_LibAppletBadExit ? 0 : (u64)-(s64)rc;
            break;
        }
        size_t units = te_utf8_to_16(g_te_utf8, strnlen((const char *)g_te_utf8, sizeof g_te_utf8), g_te_units);
        if (units <= max_len) {
            memcpy(out, g_te_units, units * 2);
            out[units] = 0;
            *n = (uint32_t)units;
            r = 1;
            break;
        }
        cur = te_cut16(g_te_units, units, max_len);
        memcpy(g_te_cur, g_te_units, cur * 2);
    }
    mutexUnlock(&g_te_lock);
    return r;
}
