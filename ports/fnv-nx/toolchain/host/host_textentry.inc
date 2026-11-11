/* host_textentry.inc - TEXT_ENTRY (FNVNX_PLAT_TEXT_ENTRY, host_platform.inc): the system software keyboard (libnx swkbd). Contract and results:
 * include/fnvnx/hostapi.h. Standalone (only <switch.h>, <string.h>, <stdint.h>) so tests/s4/backends runs it under qemu with the swkbd
 * calls stubbed. Needs svcSleepThread (the lock below).
 * Design: ONE modal call; the check-and-reopen loop of a name dialog lives in the GUEST (it calls its own check on the returned text and
 * calls again with the text as `init`): the host has no synchronous guest-callback mechanism, and libnx's SwkbdTextCheckCb would need one
 * (a guest function running on the applet's callback with the host's stack). The only loop here is the length rule the applet cannot
 * state: swkbd counts characters, the contract counts UTF-16 units. */
#define FNVNX_TE_U8CAP 2048   /* bytes: 4 per character, at most FNVNX_TEXT_ENTRY_MAX characters, and the NUL */
static int g_te_busy;
static char g_te_init[FNVNX_TE_U8CAP], g_te_out[FNVNX_TE_U8CAP];

static int te_high(unsigned c) { return c >= 0xD800 && c <= 0xDBFF; }
static int te_low(unsigned c) { return c >= 0xDC00 && c <= 0xDFFF; }

/* UTF-16 -> UTF-8 (n units, a well-formed pair is one scalar, a lone surrogate is U+FFFD); the NUL-terminated result in dst */
static void te_utf16_to_utf8(const uint16_t *s, size_t n, char *dst) {
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        uint32_t c = s[i];
        if (te_high(c) && i + 1 < n && te_low(s[i + 1])) { c = 0x10000 + ((c - 0xD800) << 10) + (s[i + 1] - 0xDC00); i++; }
        else if (te_high(c) || te_low(c)) c = 0xFFFD;
        if (c < 0x80) dst[o++] = (char)c;
        else if (c < 0x800) { dst[o++] = (char)(0xC0 | c >> 6); dst[o++] = (char)(0x80 | (c & 0x3F)); }
        else if (c < 0x10000) { dst[o++] = (char)(0xE0 | c >> 12); dst[o++] = (char)(0x80 | (c >> 6 & 0x3F)); dst[o++] = (char)(0x80 | (c & 0x3F)); }
        else { dst[o++] = (char)(0xF0 | c >> 18); dst[o++] = (char)(0x80 | (c >> 12 & 0x3F)); dst[o++] = (char)(0x80 | (c >> 6 & 0x3F)); dst[o++] = (char)(0x80 | (c & 0x3F)); }
    }
    dst[o] = 0;
}

/* UTF-8 (Unicode 15 ch. 3, Table 3-7) -> UTF-16: the units written to dst when dst != NULL, always the count. A byte that does not start a
 * well-formed sequence (continuation byte, C0/C1/F5..FF, overlong, surrogate, > U+10FFFF, truncated sequence) is U+FFFD and consumes ONE byte. */
static size_t te_utf8_to_utf16(const unsigned char *s, uint16_t *dst) {
    size_t n = 0;
    while (*s) {
        uint32_t c = s[0];
        int len = 1;
        if (c >= 0xC2 && c <= 0xDF) { if ((s[1] & 0xC0) == 0x80) { c = (c & 0x1F) << 6 | (s[1] & 0x3F); len = 2; } else c = 0xFFFD; }
        else if (c >= 0xE0 && c <= 0xEF) {
            unsigned lo = c == 0xE0 ? 0xA0 : 0x80, hi = c == 0xED ? 0x9F : 0xBF;
            if (s[1] >= lo && s[1] <= hi && (s[2] & 0xC0) == 0x80) { c = (c & 0x0F) << 12 | (s[1] & 0x3F) << 6 | (s[2] & 0x3F); len = 3; } else c = 0xFFFD;
        } else if (c >= 0xF0 && c <= 0xF4) {
            unsigned lo = c == 0xF0 ? 0x90 : 0x80, hi = c == 0xF4 ? 0x8F : 0xBF;
            if (s[1] >= lo && s[1] <= hi && (s[2] & 0xC0) == 0x80 && (s[3] & 0xC0) == 0x80) { c = (c & 0x07) << 18 | (s[1] & 0x3F) << 12 | (s[2] & 0x3F) << 6 | (s[3] & 0x3F); len = 4; } else c = 0xFFFD;
        } else if (c >= 0x80) c = 0xFFFD;
        s += len;
        if (c >= 0x10000) { if (dst) { dst[n] = (uint16_t)(0xD800 + ((c - 0x10000) >> 10)); dst[n + 1] = (uint16_t)(0xDC00 + ((c - 0x10000) & 0x3FF)); } n += 2; }
        else { if (dst) dst[n] = (uint16_t)c; n++; }
    }
    return n;
}

static u64 host_text_entry(u64 max_len, const uint16_t *init, u64 init_n, uint16_t *out, uint32_t *n) {
    if (max_len == 0 || max_len > FNVNX_TEXT_ENTRY_MAX || !out || !n) return (u64)-(s64)FNVNX_HE_BADINPUT;
    while (__atomic_exchange_n(&g_te_busy, 1, __ATOMIC_ACQUIRE)) svcSleepThread(1000000);   /* one keyboard at a time: later callers wait */
    u64 res;
    if (!init) init_n = 0;
    if (init_n > max_len) {   /* the first max_len units; a cut through a surrogate pair drops the pair's high half */
        init_n = max_len;
        if (te_high(init[init_n - 1]) && te_low(init[init_n])) init_n--;
    }
    te_utf16_to_utf8(init, init_n, g_te_init);
    for (;;) {
        SwkbdConfig c;
        Result rc = swkbdCreate(&c, 0);
        if (R_FAILED(rc)) { res = (u64)-(s64)rc; break; }
        swkbdConfigMakePresetDefault(&c);
        swkbdConfigSetStringLenMax(&c, (u32)max_len);
        swkbdConfigSetInitialText(&c, g_te_init);
        g_te_out[0] = 0;
        rc = swkbdShow(&c, g_te_out, sizeof g_te_out);
        swkbdClose(&c);
        if (rc == MAKERESULT(Module_Libnx, LibnxError_LibAppletBadExit)) { res = 0; break; }   /* the player cancelled (libnx: the exit reason is not Normal) */
        if (R_FAILED(rc)) { res = (u64)-(s64)rc; break; }
        g_te_out[FNVNX_TE_U8CAP - 1] = 0;
        size_t units = te_utf8_to_utf16((const unsigned char *)g_te_out, NULL);
        if (units <= max_len) {
            te_utf8_to_utf16((const unsigned char *)g_te_out, out);
            out[units] = 0;
            *n = (uint32_t)units;
            res = 1;
            break;
        }
        memcpy(g_te_init, g_te_out, sizeof g_te_init);   /* more units than max_len (supplementary characters count 2): the applet opens again with this text */
    }
    __atomic_store_n(&g_te_busy, 0, __ATOMIC_RELEASE);
    return res;
}
