/* nls.cpp - national language support of the Win32 layer: code pages, MultiByteToWideChar/WideCharToMultiByte,
 * GetCPInfo, locale info, LCMapString, CompareString, GetStringType, locale enumeration. Total on the documented
 * MSDN domain: every argument combination returns the documented result and sets the documented last error.
 *
 * ENVIRONMENT (decided): ANSI code page 1252, OEM 437, user/system default locale en-US (0x0409). Installed code
 * pages are exactly 1252, 437, 65001, 65000, 20127, 28591, 10000 and the ids CP_ACP 0, CP_OEMCP 1, CP_MACCP 2
 * (= 10000), CP_THREAD_ACP 3 (= ACP), CP_SYMBOL 42; any other code page is not installed (IsValidCodePage FALSE,
 * conversions and GetCPInfo fail with ERROR_INVALID_PARAMETER). The only locale is en-US; LOCALE_USER_DEFAULT
 * 0x400, LOCALE_SYSTEM_DEFAULT 0x800, LOCALE_NEUTRAL 0, the custom-default ids 0xC00/0x1400 and the primary-language
 * id 0x9 resolve to it; LOCALE_INVARIANT 0x7F has the invariant data of kInv below; any other LCID (including a
 * sort id in the high word) is not installed: ERROR_INVALID_PARAMETER.
 *
 * ASSUMPTIONS (not proven against Windows 7):
 *  A1 best fit (WideCharToMultiByte without WC_NO_BEST_FIT_CHARS): Windows' bestfit<cp>.txt tables are replaced by
 *     the rule "the NFKD decomposition without nonspacing marks is one different character the code page can
 *     represent -> that character" (U+0100 -> 'A'). Also: a surrogate pair or lone surrogate becomes one default char.
 *  A2 the code page tables, case mapping, character types (CT1-3), width forms and decompositions are generated
 *     from the Unicode database / codecs of the generating Python (tools/gen_nls.py), not Windows 7's tables;
 *     cp 10000 byte 0xDB is U+00A4, 20127 bytes >= 0x80 are undefined (U+FFFD, or 1113 with MB_ERR_INVALID_CHARS),
 *     MB_USEGLYPHCHARS maps the 437 control bytes to the IBM PC glyphs.
 *  A3 collation (CompareString, LCMAP_SORTKEY, one function): per character a primary weight (symbols < digits <
 *     letters; ASCII symbols in the order of kSym, Latin letters by base letter via canonical decomposition, all
 *     other characters by code point after those), then a diacritic weight (sum of the combining marks), then a
 *     tertiary weight (case upper > lower, katakana > hiragana, wide/half form > normal). Word sort ignores hyphen
 *     and apostrophe at the first three levels and compares them at a fourth. This is a rule, not sortdefault.nls.
 *  A4 WC_DISCARDNS/SEPCHARS/DEFAULTCHAR without WC_COMPOSITECHECK, or two of them: ERROR_INVALID_FLAGS; the
 *     composite default is WC_SEPCHARS. SORT_DIGITSASNUMBERS (0x8) is ERROR_INVALID_FLAGS. LCMAP_SIMPLIFIED_CHINESE
 *     and LCMAP_TRADITIONAL_CHINESE are accepted and leave characters unchanged (no Unihan variant data).
 *  A5 UTF-7 encodes the letters, digits and ' ( ) , - . / : ? space tab CR LF NUL directly, '+' as "+-", every
 *     other character in base64, and ends each base64 run with '-'. UTF-7 decoding drops leftover bits and turns
 *     bytes >= 0x80 into U+FFFD. UTF-8 decoding replaces each maximal invalid subpart by one U+FFFD.
 *  A6 locale data are the values of Windows 7 en-US as remembered; LOCALE_FONTSIGNATURE and the invariant locale
 *     data (kInv) are best knowledge only; LOCALE_USE_CP_ACP changes nothing (the locale's ANSI page is the ACP). */
#include "w32.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "nls_tables.inc"

#define NELEM(a) (sizeof(a) / sizeof((a)[0]))
enum { MB_COMP = 2, MB_GLYPH = 4, MB_ERRCH = 8, WCF_ERR = 0x80, WCF_CC = 0x200, WCF_DNS = 0x10, WCF_SEP = 0x20,
       WCF_DEF = 0x40, WCF_NOBF = 0x400 };
enum { L_LOWER = 0x100, L_UPPER = 0x200, L_KEY = 0x400, L_REV = 0x800, L_HIRA = 0x100000, L_KATA = 0x200000,
       L_HALF = 0x400000, L_FULL = 0x800000, L_LING = 0x08000000, L_CHS = 0x02000000, L_CHT = 0x04000000,
       K_CASE = 1, K_NS = 2, K_SYM = 4, K_STR = 0x1000, K_KANA = 0x10000, K_WIDTH = 0x20000 };
enum { SB, U8, U7, SYM };

static int er(DWORD e)
{
    SetLastError(e);
    return 0;
}

static size_t wlen(const WCHAR *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}

template <typename T> struct sink {   /* counted output into an optional buffer */
    T *p;
    int cap, n;
    bool over;
    void put(T v)
    {
        if (p) {
            if (n >= cap) {
                over = true;
                return;
            }
            p[n] = v;
        }
        n++;
    }
};

template <class T, class F> static const T *bsrch(const T *t, size_t n, unsigned k, F key)
{
    size_t lo = 0, hi = n;
    while (lo < hi) {
        size_t m = (lo + hi) / 2;
        unsigned v = key(t[m]);
        if (v == k)
            return &t[m];
        if (v < k)
            lo = m + 1;
        else
            hi = m;
    }
    return NULL;
}

static int pair_get(const nls_pair *t, size_t n, unsigned c)
{
    const nls_pair *p = bsrch(t, n, c, [](const nls_pair &e) { return (unsigned)e.a; });
    return p ? p->b : -1;
}

static unsigned run_val(const uint32_t *t, size_t n, unsigned c)   /* the run starting at or before c */
{
    size_t lo = 0, hi = n;
    while (hi - lo > 1) {
        size_t m = (lo + hi) / 2;
        if ((t[m] >> 16) <= c)
            lo = m;
        else
            hi = m;
    }
    return t[lo] & 0xFFFF;
}

#define CT1(c) run_val(kct1, NELEM(kct1), (c))
#define CT2(c) run_val(kct2, NELEM(kct2), (c))
#define CT3(c) run_val(kct3, NELEM(kct3), (c))
static unsigned wupper(unsigned c)
{
    if (c < 0x80)
        return c >= 'a' && c <= 'z' ? c - 32 : c;
    int m = pair_get(kupper, NELEM(kupper), c);
    return m < 0 ? c : (unsigned)m;
}
static unsigned wlower(unsigned c)
{
    if (c < 0x80)
        return c >= 'A' && c <= 'Z' ? c + 32 : c;
    int m = pair_get(klower, NELEM(klower), c);
    return m < 0 ? c : (unsigned)m;
}

/* ---- code pages */
static bool cp_real(UINT cp, UINT *r)
{
    switch (cp) {
    case 0: case 3: *r = 1252; return true;
    case 1: *r = 437; return true;
    case 2: *r = 10000; return true;
    case 1252: case 437: case 10000: case 20127: case 28591: case 65001: case 65000: case 42: *r = cp; return true;
    }
    return false;
}
static int cp_kind(UINT cp) { return cp == 65001 ? U8 : cp == 65000 ? U7 : cp == 42 ? SYM : SB; }

static const uint16_t *cp_table(UINT cp) { return cp == 1252 ? kcp1252 : cp == 437 ? kcp437 : cp == 10000 ? kcp10000 : NULL; }

/* byte to UTF-16 unit, -1 undefined */
static int sb_w(UINT cp, unsigned b, bool glyph)
{
    if (cp == 42)
        return b < 0x20 ? (int)b : (int)(0xF000 + b);
    if (cp == 437 && glyph && (b >= 1 && b < 0x20))
        return kglyph437[b];
    if (cp == 437 && glyph && b == 0x7F)
        return kglyph437[32];
    if (b < 0x80)
        return (int)b;
    if (cp == 28591)
        return (int)b;
    const uint16_t *t = cp_table(cp);
    return t ? t[b - 0x80] : -1;
}

/* UTF-16 unit to byte, -1 not representable */
static int sb_b(UINT cp, unsigned c)
{
    if (cp == 42)
        return c < 0x20 ? (int)c : c >= 0xF020 && c <= 0xF0FF ? (int)(c - 0xF000) : -1;
    if (c < 0x80)
        return (int)c;
    if (cp == 28591)
        return c < 0x100 ? (int)c : -1;
    const uint16_t *t = cp_table(cp);
    for (unsigned i = 0; t && i < 128; i++)
        if (t[i] == c)
            return (int)(0x80 + i);
    return -1;
}

static int bestfit(UINT cp, unsigned c)
{
    int b = pair_get(kbestfit, NELEM(kbestfit), c);
    return b < 0 ? -1 : sb_b(cp, (unsigned)b);
}

static const nls_decomp *decomp_of(unsigned c)
{
    return bsrch(kdecomp, NELEM(kdecomp), c, [](const nls_decomp &e) { return (unsigned)e.c; });
}

/* UTF-8: length of the sequence (or maximal invalid subpart) at s[0]; *cp = code point, or ~0u when invalid */
static size_t u8dec(const unsigned char *s, size_t n, uint32_t *cp)
{
    unsigned c = s[0], need = 0, lo = 0x80, hi = 0xBF;
    *cp = c;
    if (c < 0x80)
        return 1;
    if (c >= 0xC2 && c <= 0xDF) {
        need = 1;
        *cp = c & 0x1F;
    } else if (c >= 0xE0 && c <= 0xEF) {
        need = 2;
        *cp = c & 0x0F;
        lo = c == 0xE0 ? 0xA0 : lo;
        hi = c == 0xED ? 0x9F : hi;
    } else if (c >= 0xF0 && c <= 0xF4) {
        need = 3;
        *cp = c & 7;
        lo = c == 0xF0 ? 0x90 : lo;
        hi = c == 0xF4 ? 0x8F : hi;
    } else {
        *cp = ~0u;
        return 1;
    }
    for (size_t k = 1; k <= need; k++, lo = 0x80, hi = 0xBF) {
        if (k >= n || s[k] < lo || s[k] > hi) {
            *cp = ~0u;
            return k;
        }
        *cp = (*cp << 6) | (s[k] & 0x3F);
    }
    return need + 1;
}

static void u8enc(sink<char> &o, unsigned c)
{
    if (c < 0x80) {
        o.put((char)c);
    } else if (c < 0x800) {
        o.put((char)(0xC0 | (c >> 6)));
        o.put((char)(0x80 | (c & 0x3F)));
    } else if (c < 0x10000) {
        o.put((char)(0xE0 | (c >> 12)));
        o.put((char)(0x80 | ((c >> 6) & 0x3F)));
        o.put((char)(0x80 | (c & 0x3F)));
    } else {
        o.put((char)(0xF0 | (c >> 18)));
        o.put((char)(0x80 | ((c >> 12) & 0x3F)));
        o.put((char)(0x80 | ((c >> 6) & 0x3F)));
        o.put((char)(0x80 | (c & 0x3F)));
    }
}

static const char kb64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static int b64val(unsigned c) { return c && c < 0x80 && strchr(kb64, (int)c) ? (int)(strchr(kb64, (int)c) - kb64) : -1; }
static bool u7direct(unsigned c)
{
    return c == 0 || (c < 0x80 && c != '+' && (b64val(c) >= 0 || strchr("'(),-./:? \t\r\n", (int)c)));
}

static void u7enc(sink<char> &o, const WCHAR *s, size_t n)
{
    bool in64 = false;
    unsigned acc = 0, bits = 0;
    for (size_t i = 0; i < n; i++) {
        unsigned c = (uint16_t)s[i];
        if (u7direct(c) || (c == '+' && !in64)) {
            if (in64) {
                if (bits)
                    o.put(kb64[(acc << (6 - bits)) & 63]);
                o.put('-');
                in64 = false;
            }
            o.put((char)c);
            if (c == '+')
                o.put('-');
            continue;
        }
        if (!in64) {
            o.put('+');
            in64 = true;
            acc = bits = 0;
        }
        acc = (acc << 16) | c;
        for (bits += 16; bits >= 6; bits -= 6)
            o.put(kb64[(acc >> (bits - 6)) & 63]);
        acc &= (1u << bits) - 1;
    }
    if (in64) {
        if (bits)
            o.put(kb64[(acc << (6 - bits)) & 63]);
        o.put('-');
    }
}

static void u7dec(sink<WCHAR> &o, const unsigned char *s, size_t n)
{
    for (size_t i = 0; i < n;) {
        unsigned c = s[i++];
        if (c != '+') {
            o.put(c < 0x80 ? (WCHAR)c : (WCHAR)0xFFFD);
            continue;
        }
        if (i < n && s[i] == '-') {
            o.put('+');
            i++;
            continue;
        }
        unsigned acc = 0, bits = 0;
        for (int v; i < n && (v = b64val(s[i])) >= 0; i++) {
            acc = (acc << 6) | (unsigned)v;
            if ((bits += 6) >= 16) {
                o.put((WCHAR)((acc >> (bits - 16)) & 0xFFFF));
                bits -= 16;
                acc &= (1u << bits) - 1;
            }
        }
        if (i < n && s[i] == '-')
            i++;
    }
}

static bool is_mn(unsigned c) { return (CT3(c) & 1) != 0; }

extern "C" int MultiByteToWideChar(UINT cp, DWORD flags, LPCSTR src, int srclen, LPWSTR dst, int dstlen)
{
    flags = W32_DW(flags);
    if (!src || !srclen || srclen < -1 || dstlen < 0 || (dstlen && (!dst || (const void *)src == (const void *)dst)))
        return er(ERROR_INVALID_PARAMETER);
    UINT rcp;
    if (!cp_real(cp, &rcp))
        return er(ERROR_INVALID_PARAMETER);
    int kind = cp_kind(rcp);
    DWORD ok = kind == SB ? 0xF : kind == U8 ? MB_ERRCH : 0;
    if ((flags & ~ok) || (flags & 3) == 3)
        return er(ERROR_INVALID_FLAGS);
    const unsigned char *s = (const unsigned char *)src;
    size_t n = srclen < 0 ? strlen(src) + 1 : (size_t)srclen;
    sink<WCHAR> o = { dstlen ? dst : NULL, dstlen, 0, false };
    if (kind == U7) {
        u7dec(o, s, n);
    } else if (kind == U8) {
        for (size_t i = 0; i < n;) {
            uint32_t c;
            i += u8dec(s + i, n - i, &c);
            if (c == ~0u) {
                if (flags & MB_ERRCH)
                    return er(ERROR_NO_UNICODE_TRANSLATION);
                o.put(0xFFFD);
            } else if (c >= 0x10000) {
                o.put((WCHAR)(0xD800 + ((c - 0x10000) >> 10)));
                o.put((WCHAR)(0xDC00 + ((c - 0x10000) & 0x3FF)));
            } else {
                o.put((WCHAR)c);
            }
        }
    } else {
        for (size_t i = 0; i < n; i++) {
            int c = sb_w(rcp, s[i], (flags & MB_GLYPH) != 0);
            if (c < 0) {
                if (flags & MB_ERRCH)
                    return er(ERROR_NO_UNICODE_TRANSLATION);
                c = 0xFFFD;
            }
            const nls_decomp *d = (flags & MB_COMP) ? decomp_of((unsigned)c) : NULL;
            for (int k = 0; d && k < 3 && (k == 0 || d->d[k]); k++)
                o.put((WCHAR)d->d[k]);
            if (!d)
                o.put((WCHAR)c);
        }
    }
    if (o.over)
        return er(ERROR_INSUFFICIENT_BUFFER);
    return o.n;
}

static int compose(unsigned base, unsigned mark)   /* precomposed character of base + one mark, -1 none */
{
    for (const nls_decomp &e : kdecomp)
        if (e.d[0] == base && e.d[1] == mark && !e.d[2])
            return e.c;
    return -1;
}

extern "C" int WideCharToMultiByte(UINT cp, DWORD flags, LPCWSTR src, int srclen, LPSTR dst, int dstlen,
                                   LPCSTR defchar, LPBOOL useddef)
{
    flags = W32_DW(flags);
    if (!src || !srclen || srclen < -1 || dstlen < 0 || (dstlen && (!dst || (const void *)src == (const void *)dst)))
        return er(ERROR_INVALID_PARAMETER);
    UINT rcp;
    if (!cp_real(cp, &rcp))
        return er(ERROR_INVALID_PARAMETER);
    int kind = cp_kind(rcp);
    DWORD ok = kind == SB ? (WCF_CC | WCF_DNS | WCF_SEP | WCF_DEF | WCF_NOBF) : kind == U8 ? WCF_ERR : 0;
    DWORD sub = flags & (WCF_DNS | WCF_SEP | WCF_DEF);
    if ((flags & ~ok) || (sub && !(flags & WCF_CC)) || (sub & (sub - 1)))
        return er(ERROR_INVALID_FLAGS);
    if ((kind == U8 || kind == U7 || kind == SYM) && (defchar || useddef))
        return er(ERROR_INVALID_PARAMETER);
    size_t n = srclen < 0 ? wlen(src) + 1 : (size_t)srclen;
    sink<char> o = { dstlen ? dst : NULL, dstlen, 0, false };
    BOOL used = FALSE;
    char dc = defchar ? *defchar : '?';
    if (kind == U7) {
        u7enc(o, src, n);
    } else if (kind == U8) {
        for (size_t i = 0; i < n; i++) {
            unsigned c = (uint16_t)src[i];
            if (c >= 0xD800 && c <= 0xDBFF && i + 1 < n && (uint16_t)src[i + 1] >= 0xDC00 && (uint16_t)src[i + 1] <= 0xDFFF) {
                c = 0x10000 + ((c - 0xD800) << 10) + ((uint16_t)src[i + 1] - 0xDC00);
                i++;
            } else if (c >= 0xD800 && c <= 0xDFFF) {
                if (flags & WCF_ERR)
                    return er(ERROR_NO_UNICODE_TRANSLATION);
                c = 0xFFFD;
            }
            u8enc(o, c);
        }
    } else {
        for (size_t i = 0; i < n; i++) {
            unsigned c = (uint16_t)src[i];
            bool pair = c >= 0xD800 && c <= 0xDBFF && i + 1 < n && (uint16_t)src[i + 1] >= 0xDC00 && (uint16_t)src[i + 1] <= 0xDFFF;
            bool mark = (flags & WCF_CC) && !pair && i + 1 < n && is_mn((uint16_t)src[i + 1]);
            if (mark) {
                int x = compose(c, (uint16_t)src[i + 1]), b = x < 0 ? -1 : sb_b(rcp, (unsigned)x);
                if (b >= 0 || (flags & WCF_DEF)) {
                    o.put((char)(b >= 0 ? b : dc));
                    used |= b < 0;
                    i++;
                    continue;
                }
            }
            int b = pair ? -1 : sb_b(rcp, c);
            if (b < 0 && !pair && !(flags & WCF_NOBF))
                b = bestfit(rcp, c);
            if (b < 0) {
                b = (unsigned char)dc;
                used = TRUE;
            }
            o.put((char)b);
            i += pair;
            if (mark && (flags & WCF_DNS))
                while (i + 1 < n && is_mn((uint16_t)src[i + 1]))
                    i++;
        }
    }
    if (useddef)
        *useddef = used;
    if (o.over)
        return er(ERROR_INSUFFICIENT_BUFFER);
    return o.n;
}

extern "C" UINT GetACP(void) { return 1252; }
extern "C" UINT GetOEMCP(void) { return 437; }
extern "C" BOOL IsValidCodePage(UINT cp)
{
    UINT r;
    return cp_real(cp, &r);
}

extern "C" BOOL GetCPInfo(UINT cp, LPCPINFO info)
{
    UINT r;
    if (!info || !cp_real(cp, &r))
        return er(ERROR_INVALID_PARAMETER);
    memset(info, 0, sizeof *info);
    info->MaxCharSize = r == 65001 ? 4 : r == 65000 ? 5 : 1;
    info->DefaultChar[0] = '?';
    return TRUE;
}

/* ---- locales */
static int lc_kind(LCID lcid)   /* 0 not installed, 1 en-US, 2 invariant */
{
    unsigned l = (unsigned)W32_DW(lcid);
    if (l == 0x7F)
        return 2;
    return l == 0x409 || l == 0x400 || l == 0x800 || l == 0 || l == 0x9 || l == 0xC00 || l == 0x1400 ? 1 : 0;
}

struct locale_item { uint16_t type; const char *value; };   /* Latin-1 */
static const locale_item kUS[] = {
    {0x01, "0409"}, {0x02, "English (United States)"}, {0x03, "ENU"}, {0x04, "English"}, {0x05, "1"},
    {0x06, "United States"}, {0x07, "USA"}, {0x08, "United States"}, {0x09, "0409"}, {0x0A, "1"}, {0x0B, "437"},
    {0x0C, ","}, {0x0D, "1"}, {0x0E, "."}, {0x0F, ","}, {0x10, "3;0"}, {0x11, "2"}, {0x12, "1"},
    {0x13, "0123456789"}, {0x14, "$"}, {0x15, "USD"}, {0x16, "."}, {0x17, ","}, {0x18, "3;0"}, {0x19, "2"},
    {0x1A, "2"}, {0x1B, "0"}, {0x1C, "0"}, {0x1D, "/"}, {0x1E, ":"}, {0x1F, "M/d/yyyy"},
    {0x20, "dddd, MMMM dd, yyyy"}, {0x21, "0"}, {0x22, "0"}, {0x23, "0"}, {0x24, "1"}, {0x25, "0"}, {0x26, "0"},
    {0x27, "0"}, {0x28, "AM"}, {0x29, "PM"},
    {0x2A, "Monday"}, {0x2B, "Tuesday"}, {0x2C, "Wednesday"}, {0x2D, "Thursday"}, {0x2E, "Friday"},
    {0x2F, "Saturday"}, {0x30, "Sunday"}, {0x31, "Mon"}, {0x32, "Tue"}, {0x33, "Wed"}, {0x34, "Thu"},
    {0x35, "Fri"}, {0x36, "Sat"}, {0x37, "Sun"},
    {0x38, "January"}, {0x39, "February"}, {0x3A, "March"}, {0x3B, "April"}, {0x3C, "May"}, {0x3D, "June"},
    {0x3E, "July"}, {0x3F, "August"}, {0x40, "September"}, {0x41, "October"}, {0x42, "November"},
    {0x43, "December"}, {0x44, "Jan"}, {0x45, "Feb"}, {0x46, "Mar"}, {0x47, "Apr"}, {0x48, "May"},
    {0x49, "Jun"}, {0x4A, "Jul"}, {0x4B, "Aug"}, {0x4C, "Sep"}, {0x4D, "Oct"}, {0x4E, "Nov"}, {0x4F, "Dec"},
    {0x50, ""}, {0x51, "-"}, {0x52, "3"}, {0x53, "0"}, {0x54, "1"}, {0x55, "0"}, {0x56, "1"}, {0x57, "0"},
    {0x59, "en"}, {0x5A, "US"}, {0x5B, "244"}, {0x5C, "en-US"}, {0x5D, "h:mm:ss"}, {0x5E, "00000409"},
    {0x60, "Mo"}, {0x61, "Tu"}, {0x62, "We"}, {0x63, "Th"}, {0x64, "Fr"}, {0x65, "Sa"}, {0x66, "Su"},
    {0x67, "eng"}, {0x68, "USA"}, {0x69, "NaN"}, {0x6A, "Infinity"}, {0x6B, "-Infinity"}, {0x6C, "Latn;"},
    {0x6D, "en"}, {0x6E, "en-US"}, {0x6F, "English"}, {0x70, "0"}, {0x71, "0"}, {0x72, "English (United States)"},
    {0x73, "English (United States)"}, {0x79, "h:mm tt"}, {0x7A, "ENG "},
    {0x1001, "English"}, {0x1002, "United States"}, {0x1003, "h:mm:ss tt"}, {0x1004, "1252"}, {0x1005, "0"},
    {0x1006, "MMMM, yyyy"}, {0x1007, "US Dollar"}, {0x1008, "US Dollar"}, {0x1009, "1"}, {0x100A, "1"},
    {0x100B, "0"}, {0x100C, "6"}, {0x100D, "0"}, {0x100E, ""}, {0x100F, ""}, {0x1010, "1"}, {0x1011, "10000"},
    {0x1012, "37"}, {0x1013, "Default"}, {0x1014, "1"},
};
static const locale_item kInv[] = {   /* the invariant locale: what differs from en-US */
    {0x01, "007F"}, {0x02, "Invariant Language (Invariant Country)"}, {0x03, "IVL"}, {0x04, "Invariant Language"},
    {0x06, "Invariant Country"}, {0x07, "IVC"}, {0x08, "Invariant Country"}, {0x09, "007F"}, {0x0D, "0"},
    {0x14, "\xA4"}, {0x15, "XDR"}, {0x1F, "MM/dd/yyyy"}, {0x20, "dddd, dd MMMM yyyy"}, {0x21, "0"}, {0x22, "1"},
    {0x23, "1"}, {0x25, "1"}, {0x26, "1"}, {0x27, "1"}, {0x59, "iv"}, {0x5A, "IV"}, {0x5C, ""}, {0x5D, "HH:mm:ss"},
    {0x5E, "00000409"}, {0x67, "ivl"}, {0x68, "IVC"}, {0x6D, ""}, {0x6E, "en-US"}, {0x6F, "Invariant Language"},
    {0x72, "Invariant Language (Invariant Country)"}, {0x73, "Invariant Language (Invariant Country)"},
    {0x79, "HH:mm"}, {0x1001, "Invariant Language"}, {0x1002, "Invariant Country"}, {0x1003, "HH:mm:ss"},
    {0x1006, "yyyy MMMM"}, {0x1007, "International Monetary Fund"}, {0x1008, "International Monetary Fund"},
};
static const uint16_t kfontsig[16] = { 3, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0 };   /* LOCALESIGNATURE */

static const char *li_find(const locale_item *t, size_t n, unsigned type)
{
    for (size_t i = 0; i < n; i++)
        if (t[i].type == type)
            return t[i].value;
    return NULL;
}

static int locale_info(LCID lcid, LCTYPE type, void *out, int cch, bool wide)
{
    DWORD ty = W32_DW(type);
    if (cch < 0 || (cch && !out))
        return er(ERROR_INVALID_PARAMETER);
    int kind = lc_kind(lcid);
    if (!kind)
        return er(ERROR_INVALID_PARAMETER);
    unsigned t = (unsigned)(ty & ~(LOCALE_NOUSEROVERRIDE | LOCALE_USE_CP_ACP | LOCALE_RETURN_NUMBER));
    bool num = (ty & LOCALE_RETURN_NUMBER) != 0;
    if (t == 0x58) {   /* LOCALE_FONTSIGNATURE: 16 WCHARs, 32 bytes for the A form */
        int units = wide ? 16 : 32;
        if (num)
            return er(ERROR_INVALID_FLAGS);
        if (cch && cch < units)
            return er(ERROR_INSUFFICIENT_BUFFER);
        if (cch)
            memcpy(out, kfontsig, 32);
        return units;
    }
    const char *v = kind == 2 ? li_find(kInv, NELEM(kInv), t) : NULL;
    if (!v)
        v = li_find(kUS, NELEM(kUS), t);
    if (!v)
        return er(ERROR_INVALID_FLAGS);
    if (num) {
        int need = wide ? 2 : 4;
        char *end;
        uint32_t x = (uint32_t)strtoul(v, &end, t == 1 || t == 9 ? 16 : 10);   /* the LANGIDs are hex */
        if (end == v || *end)
            return er(ERROR_INVALID_FLAGS);
        if (cch && cch < need)
            return er(ERROR_INSUFFICIENT_BUFFER);
        if (cch)
            memcpy(out, &x, 4);
        return need;
    }
    int len = (int)strlen(v) + 1;
    if (cch && cch < len)
        return er(ERROR_INSUFFICIENT_BUFFER);
    for (int i = 0; cch && i < len; i++) {
        if (wide)
            ((WCHAR *)out)[i] = (WCHAR)(unsigned char)v[i];
        else
            ((char *)out)[i] = v[i];
    }
    return len;
}

extern "C" int GetLocaleInfoA(LCID lcid, LCTYPE type, LPSTR out, int cch) { return locale_info(lcid, type, out, cch, false); }
extern "C" int GetLocaleInfoW(LCID lcid, LCTYPE type, LPWSTR out, int cch) { return locale_info(lcid, type, out, cch, true); }

extern "C" BOOL IsValidLocale(LCID lcid, DWORD flags)
{
    DWORD f = W32_DW(flags);
    if (f != 1 && f != 2)
        return er(ERROR_INVALID_FLAGS);
    return lc_kind(lcid) != 0;
}

extern "C" BOOL EnumSystemLocalesA(LOCALE_ENUMPROCA proc, DWORD flags)
{
    DWORD f = W32_DW(flags);
    if (!proc)
        return er(ERROR_INVALID_PARAMETER);
    if ((f & ~7u) || !(f & 7))
        return er(ERROR_INVALID_FLAGS);
    char id[] = "00000409";
    if (f & 3)
        proc(id);
    return TRUE;
}

extern "C" LCID GetUserDefaultLCID(void) { return 0x0409; }

/* ---- source text of the NLS functions: UTF-16 units, or ANSI bytes read through code page 1252 */
struct nsrc {
    const void *p;
    size_t n;
    bool a;
    unsigned at(size_t i) const
    {
        if (!a)
            return ((const uint16_t *)p)[i];
        unsigned b = ((const unsigned char *)p)[i];
        return b >= 0x80 && b < 0xA0 ? kcp1252[b - 0x80] : b;
    }
};
static nsrc mksrc(const void *p, int len, bool a)
{
    size_t n = len == -1 ? (a ? strlen((const char *)p) : wlen((const WCHAR *)p)) + 1 : len > 0 ? (size_t)len : 0;
    return nsrc{ p, n, a };
}

/* ---- character types */
static int string_type(DWORD info, const nsrc &s, int cch, LPWORD out)
{
    if (!out || !s.p || !cch || cch < -1)
        return er(ERROR_INVALID_PARAMETER);
    if (info != 1 && info != 2 && info != 4)
        return er(ERROR_INVALID_FLAGS);
    for (size_t i = 0; i < s.n; i++) {
        unsigned c = s.at(i);
        out[i] = (WORD)(info == 1 ? CT1(c) : info == 2 ? CT2(c) : CT3(c));
    }
    return TRUE;
}

extern "C" BOOL GetStringTypeW(DWORD info, LPCWSTR src, int cch, LPWORD out)
{
    return string_type(W32_DW(info), mksrc(src, src ? cch : 1, false), cch, out);
}

extern "C" BOOL GetStringTypeA(LCID lcid, DWORD info, LPCSTR src, int cch, LPWORD out)
{
    if (!lc_kind(lcid))
        return er(ERROR_INVALID_PARAMETER);
    return string_type(W32_DW(info), mksrc(src, src ? cch : 1, true), cch, out);
}

/* ---- collation: one element stream per string; CompareString compares it level by level, LCMAP_SORTKEY writes it.
 * kind 1 element: p primary, s diacritic, t tertiary; kind 2 (word sort hyphen/apostrophe): pos, p = weight */
struct elem { int kind; unsigned p, s, t, pos; };
struct eit { nsrc s; size_t i; DWORD fl; unsigned cnt; };
static const char kSym[] = "\t\n\v\f\r _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$";
static unsigned wmark(unsigned m) { return m >= 0x300 && m < 0x370 ? m - 0x2FF : m; }

static void eit_next(eit &it, elem &e)
{
    DWORD fl = it.fl;
    for (;;) {
        if (it.i >= it.s.n) {
            e.kind = 0;
            return;
        }
        unsigned c = it.s.at(it.i++), t = 0, sec = 0, k = CT1(c);
        if ((k & 0x20) && !(k & 8))
            continue;   /* controls and format characters are ignorable */
        if (is_mn(c) && (fl & K_NS))
            continue;
        if (const nls_width *w = bsrch(kwidth, NELEM(kwidth), c, [](const nls_width &x) { return (unsigned)x.form; })) {
            c = w->base;
            t |= (fl & K_WIDTH) ? 0 : 4;
        }
        if (c >= 0x3041 && c <= 0x3096)
            c += 0x60;
        else if (c >= 0x30A1 && c <= 0x30F6)
            t |= (fl & K_KANA) ? 0 : 2;
        if (const nls_decomp *d = decomp_of(c)) {
            c = d->d[0];
            sec += wmark(d->d[1]) + (d->d[2] ? wmark(d->d[2]) : 0);
        }
        while (it.i < it.s.n && is_mn(it.s.at(it.i)))
            sec += wmark(it.s.at(it.i++));
        if (fl & K_NS)
            sec = 0;
        unsigned lo = wlower(c);
        if (lo != c) {
            c = lo;
            t |= (fl & K_CASE) ? 0 : 1;
        }
        k = CT1(c);
        e.s = sec > 0xFFFF ? 0xFFFF : sec;
        e.t = t;
        e.pos = it.cnt;
        if ((k & 0x18) && (fl & K_SYM))
            continue;   /* NORM_IGNORESYMBOLS: punctuation, symbols and spaces */
        if (!(fl & K_STR) && (c == '-' || c == '\'')) {
            e.kind = 2;
            e.p = c == '-' ? 2 : 3;
            return;
        }
        if (k & 0x18) {
            const char *q = c < 0x80 ? strchr(kSym, (int)c) : NULL;
            e.p = (1u << 16) | (c < 0x80 ? (q ? (unsigned)(q - kSym) : 0u) : c);
        } else if (k & 4) {
            e.p = (2u << 16) | (c < 0x80 ? c - '0' : c);
        } else {
            e.p = (3u << 16) | (c >= 'a' && c <= 'z' ? c - 'a' : c);
        }
        e.kind = 1;
        it.cnt++;
        return;
    }
}

static void level_next(eit &it, elem &e, int lv)   /* the next element that exists at this level */
{
    do
        eit_next(it, e);
    while (e.kind && e.kind != (lv == 4 ? 2 : 1));
}
static unsigned level_val(const elem &e, int lv) { return lv == 1 ? e.p : lv == 2 ? e.s : lv == 3 ? e.t : (e.pos << 8) | e.p; }

static int collate(DWORD fl, const nsrc &a, const nsrc &b)   /* -1, 0, 1 */
{
    for (int lv = 1; lv <= 4; lv++) {
        eit x = { a, 0, fl, 0 }, y = { b, 0, fl, 0 };
        for (;;) {
            elem ex, ey;
            level_next(x, ex, lv);
            level_next(y, ey, lv);
            if (!ex.kind || !ey.kind) {
                if (ex.kind != ey.kind)
                    return ex.kind ? 1 : -1;
                break;
            }
            unsigned vx = level_val(ex, lv), vy = level_val(ey, lv);
            if (vx != vy)
                return vx < vy ? -1 : 1;
        }
    }
    return 0;
}

static void sortkey(DWORD fl, const nsrc &s, sink<BYTE> &o)
{
    for (int lv = 1; lv <= 4; lv++) {
        eit it = { s, 0, fl, 0 };
        elem e;
        for (level_next(it, e, lv); e.kind; level_next(it, e, lv)) {
            if (lv == 1) {
                o.put((BYTE)((e.p >> 16) + 2));
                o.put((BYTE)(e.p >> 8));
                o.put((BYTE)e.p);
            } else if (lv == 2) {
                o.put(2);
                o.put((BYTE)(e.s >> 8));
                o.put((BYTE)e.s);
            } else if (lv == 3) {
                o.put((BYTE)(e.t + 2));
            } else {
                o.put(2);
                o.put((BYTE)(e.pos >> 8));
                o.put((BYTE)e.pos);
                o.put((BYTE)(e.p + 2));
            }
        }
        o.put(lv == 4 ? 0 : 1);
    }
}

static int compare_string(LCID lcid, DWORD flags, const void *a, int na, const void *b, int nb, bool ansi)
{
    DWORD f = W32_DW(flags);
    if (!a || !b || na < -1 || nb < -1)
        return er(ERROR_INVALID_PARAMETER);
    if (f & ~(DWORD)(K_CASE | K_NS | K_SYM | 0x10 | 0x20 | K_STR | K_KANA | K_WIDTH | L_LING))
        return er(ERROR_INVALID_FLAGS);
    if (!lc_kind(lcid))
        return er(ERROR_INVALID_PARAMETER);
    DWORD nf = (f & (K_CASE | K_NS | K_SYM | K_STR | K_KANA | K_WIDTH)) | ((f & 0x10) ? K_CASE : 0) | ((f & 0x20) ? K_NS : 0);
    return collate(nf, mksrc(a, na, ansi), mksrc(b, nb, ansi)) + 2;
}

extern "C" int CompareStringW(LCID lcid, DWORD flags, LPCWSTR a, int na, LPCWSTR b, int nb)
{
    return compare_string(lcid, flags, a, na, b, nb, false);
}
extern "C" int CompareStringA(LCID lcid, DWORD flags, LPCSTR a, int na, LPCSTR b, int nb)
{
    return compare_string(lcid, flags, a, na, b, nb, true);
}

/* ---- LCMapString */
static unsigned map_unit(DWORD f, unsigned c)
{
    if ((f & L_KATA) && c >= 0x3041 && c <= 0x3096)
        c += 0x60;
    else if ((f & L_HIRA) && c >= 0x30A1 && c <= 0x30F6)
        c -= 0x60;
    if (f & (L_FULL | L_HALF)) {
        const nls_width *w = bsrch(kwidth, NELEM(kwidth), c, [](const nls_width &x) { return (unsigned)x.form; });
        if (w && w->kind == ((f & L_FULL) ? 2 : 1))
            c = w->base;   /* halfwidth form -> its (full) base; fullwidth form -> its (narrow) base */
        else if (!w)
            for (const nls_width &x : kwidth)
                if (x.base == c && x.kind == ((f & L_FULL) ? 1 : 2)) {
                    c = x.form;
                    break;
                }
    }
    return (f & L_UPPER) ? wupper(c) : (f & L_LOWER) ? wlower(c) : c;
}

static int lcmap(LCID lcid, DWORD flags, const void *src, int srclen, void *dst, int dstlen, bool a)
{
    DWORD f = W32_DW(flags);
    if (!src || !srclen || srclen < -1 || dstlen < 0 || (dstlen && !dst))
        return er(ERROR_INVALID_PARAMETER);
    if (!lc_kind(lcid))
        return er(ERROR_INVALID_PARAMETER);
    const DWORD cs = L_LOWER | L_UPPER, kn = L_HIRA | L_KATA, wd = L_HALF | L_FULL, nm = K_CASE | K_NS | K_SYM | K_STR | K_KANA | K_WIDTH;
    const DWORD all = cs | kn | wd | L_KEY | L_REV | L_LING | L_CHS | L_CHT | nm;
    bool key = (f & L_KEY) != 0;
    if ((f & ~all) || (f & cs) == cs || (f & kn) == kn || (f & wd) == wd || (f & (L_CHS | L_CHT)) == (L_CHS | L_CHT) ||
        !(f & (cs | kn | wd | L_KEY | L_REV | L_CHS | L_CHT)) || ((f & L_LING) && !(f & cs)) ||
        (key && (f & (cs | kn | wd | L_LING | L_CHS | L_CHT))) || (!key && (f & nm)) ||
        (dstlen && src == dst && (f & ~(cs | L_LING))))
        return er(ERROR_INVALID_FLAGS);
    nsrc s = mksrc(src, srclen, a);
    int n;
    bool over;
    if (key) {
        sink<BYTE> o = { dstlen ? (BYTE *)dst : NULL, dstlen, 0, false };
        sortkey(f & nm, s, o);
        n = o.n, over = o.over;
    } else if (a) {
        sink<char> o = { dstlen ? (char *)dst : NULL, dstlen, 0, false };
        for (size_t i = 0; i < s.n; i++) {
            int b = sb_b(1252, map_unit(f, s.at(i)));
            o.put(b < 0 ? ((const char *)src)[i] : (char)b);   /* a mapping outside the page keeps the byte */
        }
        n = o.n, over = o.over;
    } else {
        sink<WCHAR> o = { dstlen ? (WCHAR *)dst : NULL, dstlen, 0, false };
        for (size_t i = 0; i < s.n; i++)
            o.put((WCHAR)map_unit(f, s.at(i)));
        n = o.n, over = o.over;
    }
    if (over)
        return er(ERROR_INSUFFICIENT_BUFFER);
    if ((f & L_REV) && dstlen) {   /* LCMAP_BYTEREV: swap the bytes of each unit (pairs of bytes in the A and key forms) */
        unsigned char *d = (unsigned char *)dst;
        size_t units = (a || key) ? (size_t)n / 2 : (size_t)n;
        for (size_t i = 0; i < units; i++) {
            unsigned char t = d[2 * i];
            d[2 * i] = d[2 * i + 1];
            d[2 * i + 1] = t;
        }
    }
    return n;
}

extern "C" int LCMapStringW(LCID lcid, DWORD flags, LPCWSTR src, int srclen, LPWSTR dst, int dstlen)
{
    return lcmap(lcid, flags, src, srclen, dst, dstlen, false);
}
extern "C" int LCMapStringA(LCID lcid, DWORD flags, LPCSTR src, int srclen, LPSTR dst, int dstlen)
{
    return lcmap(lcid, flags, src, srclen, dst, dstlen, true);
}
