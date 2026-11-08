/* crt_kernel32.cpp - the Win32 imports only FalloutNV.exe's statically linked MSVC 2008 CRT calls: code pages,
 * locale (NLS) queries, the standard handles, the environment block, startup info, the top-level exception filter.
 *
 * The system is a single-byte en-US Windows: LCID 0x0409, ANSI and OEM code page both 1252 (one table; a real en-US
 * system has OEM 437, which only console and ".OCP" locale code would notice), no lead bytes. Conversions serve
 * CP_ACP/CP_OEMCP/CP_THREAD_ACP/1252 and CP_UTF8 with the Windows size conventions: a length of -1 includes the
 * terminating NUL, a destination size of 0 asks for the required size, a short buffer fails with
 * ERROR_INSUFFICIENT_BUFFER. 1252's five undefined bytes (0x81 0x8d 0x8f 0x90 0x9d) map to U+0081.. as on Windows;
 * WideCharToMultiByte uses no best-fit table (an unmappable character becomes the default character).
 * Case mapping and character types cover ASCII, Latin-1, Latin Extended-A, Greek, Cyrillic and full-width Latin;
 * CompareString orders by case-folded code unit, lower case before upper case on a tie (as Windows' "a" < "A"),
 * and LCMAP_SORTKEY produces keys whose memcmp order is that of CompareString.
 *
 * The standard handles are three pseudo values outside the handle table (not multiples of 4): output and error
 * go to the debug log line by line, input reads as end of file. They are character devices without a console
 * (GetFileType FILE_TYPE_CHAR, GetConsoleMode fails), so the CRT writes them with WriteFile. A Windows GUI process
 * without a console would have NULL standard handles and lose stdout; here printf reaches the log. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- code page 1252 */
static const uint16_t k1252_hi[32] = {
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D,
    0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A,
    0x0153, 0x009D, 0x017E, 0x0178,
};

static inline unsigned w1252(unsigned char b) { return b >= 0x80 && b < 0xA0 ? k1252_hi[b - 0x80] : b; }

/* -1: not in 1252 */
static int to1252(unsigned c)
{
    if (c < 0x80 || (c >= 0xA0 && c <= 0xFF))
        return (int)c;
    for (int i = 0; i < 32; i++)
        if (k1252_hi[i] == c)
            return 0x80 + i;
    return -1;
}

static size_t wlen(const WCHAR *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}

/* 0 unsupported, else 1252 or CP_UTF8 */
static UINT real_cp(UINT cp)
{
    switch (cp) {
    case CP_ACP: case CP_OEMCP: case CP_THREAD_ACP: case 1252: return 1252;
    case CP_UTF8: return CP_UTF8;
    default: return 0;
    }
}

/* counted output into an optional buffer */
template <typename T> struct sink {
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

extern "C" int MultiByteToWideChar(UINT cp, DWORD flags, LPCSTR src, int srclen, LPWSTR dst, int dstlen)
{
    flags = W32_DW(flags);
    UINT rcp = real_cp(cp);
    if (!src || !srclen || dstlen < 0 || (dstlen && !dst) || !rcp) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    const unsigned char *s = (const unsigned char *)src;
    size_t n = srclen < 0 ? strlen(src) + 1 : (size_t)srclen;
    sink<WCHAR> o = { dstlen ? dst : NULL, dstlen, 0, false };
    if (rcp == 1252) {
        for (size_t i = 0; i < n; i++)
            o.put((WCHAR)w1252(s[i]));
    } else {
        for (size_t i = 0; i < n;) {
            unsigned c = s[i], need, min;
            if (c < 0x80) { o.put((WCHAR)c); i++; continue; }
            if (c >= 0xC2 && c <= 0xDF) { need = 1; min = 0x80; c &= 0x1F; }
            else if (c >= 0xE0 && c <= 0xEF) { need = 2; min = 0x800; c &= 0x0F; }
            else if (c >= 0xF0 && c <= 0xF4) { need = 3; min = 0x10000; c &= 0x07; }
            else need = 0, min = 0;
            size_t j = i + 1;
            unsigned k = 0;
            for (; need && k < need && j < n && (s[j] & 0xC0) == 0x80; k++, j++)
                c = (c << 6) | (s[j] & 0x3F);
            if (!need || k < need || c < min || c > 0x10FFFF || (c >= 0xD800 && c <= 0xDFFF)) {
                if (flags & MB_ERR_INVALID_CHARS) {
                    SetLastError(ERROR_NO_UNICODE_TRANSLATION);
                    return 0;
                }
                o.put(0xFFFD);
                i = need && k ? j : i + 1;   /* one U+FFFD per maximal invalid subsequence */
                continue;
            }
            if (c >= 0x10000) {
                o.put((WCHAR)(0xD800 + ((c - 0x10000) >> 10)));
                o.put((WCHAR)(0xDC00 + ((c - 0x10000) & 0x3FF)));
            } else {
                o.put((WCHAR)c);
            }
            i = j;
        }
    }
    if (o.over) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    return o.n;
}

extern "C" int WideCharToMultiByte(UINT cp, DWORD flags, LPCWSTR src, int srclen, LPSTR dst, int dstlen,
                                   LPCSTR defchar, LPBOOL useddef)
{
    flags = W32_DW(flags);
    UINT rcp = real_cp(cp);
    if (!src || !srclen || dstlen < 0 || (dstlen && !dst) || !rcp ||
        (rcp == CP_UTF8 && (defchar || useddef))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    size_t n = srclen < 0 ? wlen(src) + 1 : (size_t)srclen;
    sink<char> o = { dstlen ? dst : NULL, dstlen, 0, false };
    BOOL used = FALSE;
    for (size_t i = 0; i < n; i++) {
        unsigned c = (uint16_t)src[i];
        bool pair = c >= 0xD800 && c <= 0xDBFF && i + 1 < n && (uint16_t)src[i + 1] >= 0xDC00 &&
                    (uint16_t)src[i + 1] <= 0xDFFF;
        if (rcp == 1252) {
            int b = to1252(c);
            if (b < 0) {
                b = (unsigned char)(defchar ? *defchar : '?');
                used = TRUE;
                if (pair)
                    i++;
            }
            o.put((char)b);
            continue;
        }
        if (pair) {
            c = 0x10000 + ((c - 0xD800) << 10) + ((uint16_t)src[++i] - 0xDC00);
        } else if (c >= 0xD800 && c <= 0xDFFF) {
            if (flags & WC_ERR_INVALID_CHARS) {
                SetLastError(ERROR_NO_UNICODE_TRANSLATION);
                return 0;
            }
            c = 0xFFFD;
        }
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
    if (useddef)
        *useddef = used;
    if (o.over) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    return o.n;
}

extern "C" UINT GetACP(void) { return 1252; }
extern "C" UINT GetOEMCP(void) { return 1252; }
extern "C" UINT GetConsoleCP(void) { return 1252; }
extern "C" UINT GetConsoleOutputCP(void) { return 1252; }
extern "C" BOOL IsValidCodePage(UINT cp) { return cp == 1252 || cp == CP_UTF8; }

extern "C" BOOL GetCPInfo(UINT cp, LPCPINFO info)
{
    UINT rcp = real_cp(cp);
    if (!info || !rcp) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    memset(info, 0, sizeof *info);
    info->MaxCharSize = rcp == CP_UTF8 ? 4 : 1;
    info->DefaultChar[0] = '?';
    return TRUE;
}

/* ---- case and character types (UTF-16 code units) */
static unsigned wupper(unsigned c)
{
    if (c < 0x80)
        return c >= 'a' && c <= 'z' ? c - 32 : c;
    if (c < 0x100) {
        if (c >= 0xE0 && c <= 0xFE && c != 0xF7)
            return c - 32;
        if (c == 0xFF)
            return 0x178;
        if (c == 0xB5)
            return 0x39C;
        return c;
    }
    if (c < 0x180) {
        if (c == 0x131)
            return 'I';
        if ((c >= 0x100 && c <= 0x12F) || (c >= 0x132 && c <= 0x137) || (c >= 0x14A && c <= 0x177))
            return c & 1 ? c - 1 : c;
        if ((c >= 0x139 && c <= 0x148) || (c >= 0x179 && c <= 0x17E))
            return c & 1 ? c : c - 1;
        return c;
    }
    if (c == 0x192)
        return 0x191;
    if (c >= 0x3B1 && c <= 0x3CB && c != 0x3C2)
        return c - 32;
    if (c == 0x3C2)
        return 0x3A3;
    if (c == 0x3AC)
        return 0x386;
    if (c >= 0x3AD && c <= 0x3AF)
        return c - 37;
    if (c == 0x3CC)
        return 0x38C;
    if (c == 0x3CD || c == 0x3CE)
        return c - 63;
    if (c >= 0x430 && c <= 0x44F)
        return c - 32;
    if (c >= 0x450 && c <= 0x45F)
        return c - 80;
    if (c >= 0xFF41 && c <= 0xFF5A)
        return c - 32;
    return c;
}

static unsigned wlower(unsigned c)
{
    if (c < 0x80)
        return c >= 'A' && c <= 'Z' ? c + 32 : c;
    if (c < 0x100)
        return c >= 0xC0 && c <= 0xDE && c != 0xD7 ? c + 32 : c;
    if (c < 0x180) {
        if (c == 0x130)
            return 'i';
        if (c == 0x178)
            return 0xFF;
        if ((c >= 0x100 && c <= 0x12F) || (c >= 0x132 && c <= 0x137) || (c >= 0x14A && c <= 0x177))
            return c & 1 ? c : c + 1;
        if ((c >= 0x139 && c <= 0x148) || (c >= 0x179 && c <= 0x17E))
            return c & 1 ? c + 1 : c;
        return c;
    }
    if (c == 0x191)
        return 0x192;
    if (c >= 0x391 && c <= 0x3AB && c != 0x3A2)
        return c + 32;
    if (c == 0x386)
        return 0x3AC;
    if (c >= 0x388 && c <= 0x38A)
        return c + 37;
    if (c == 0x38C)
        return 0x3CC;
    if (c == 0x38E || c == 0x38F)
        return c + 63;
    if (c >= 0x410 && c <= 0x42F)
        return c + 32;
    if (c >= 0x400 && c <= 0x40F)
        return c + 80;
    if (c >= 0xFF21 && c <= 0xFF3A)
        return c + 32;
    return c;
}

static WORD ctype1(unsigned c)
{
    WORD t;
    if (c < 0x80) {
        if (c < 0x20 || c == 0x7F) {
            t = C1_CNTRL;
            if (c >= 0x09 && c <= 0x0D)
                t |= C1_SPACE;
            if (c == 0x09)
                t |= C1_BLANK;
        } else if (c == ' ') {
            t = C1_SPACE | C1_BLANK;
        } else if (c >= '0' && c <= '9') {
            t = C1_DIGIT | C1_XDIGIT;
        } else if ((c | 32) >= 'a' && (c | 32) <= 'z') {
            t = C1_ALPHA | (c <= 'Z' ? C1_UPPER : C1_LOWER);
            if ((c | 32) <= 'f')
                t |= C1_XDIGIT;
        } else {
            t = C1_PUNCT;
        }
        return t | C1_DEFINED;
    }
    if (c < 0x100) {
        if (c < 0xA0)
            t = C1_CNTRL;
        else if (c == 0xA0)
            t = C1_SPACE | C1_BLANK;
        else if (c == 0xAA || c == 0xBA || c == 0xB5 || c == 0xDF)
            t = C1_ALPHA | C1_LOWER;
        else if (c == 0xB2 || c == 0xB3 || c == 0xB9)
            t = C1_DIGIT | C1_PUNCT;
        else if (c < 0xC0 || c == 0xD7 || c == 0xF7)
            t = C1_PUNCT;
        else
            t = C1_ALPHA | (c < 0xDF ? C1_UPPER : C1_LOWER);
        return t | C1_DEFINED;
    }
    if ((c >= 0xD800 && c <= 0xDFFF) || c >= 0xFFFE)
        return 0;
    if (wupper(c) != c)
        return C1_ALPHA | C1_LOWER | C1_DEFINED;
    if (wlower(c) != c)
        return C1_ALPHA | C1_UPPER | C1_DEFINED;
    if ((c >= 0x2000 && c <= 0x200B) || c == 0x202F || c == 0x205F || c == 0x3000)
        return C1_SPACE | C1_BLANK | C1_DEFINED;
    if (c == 0x2028 || c == 0x2029)
        return C1_SPACE | C1_DEFINED;
    if ((c >= 0x2C2 && c <= 0x2C5) || (c >= 0x2D2 && c <= 0x2DF) || (c >= 0x2E5 && c <= 0x2EB) ||
        (c >= 0x2EF && c <= 0x36F) || (c >= 0x2010 && c <= 0x2027) || (c >= 0x2030 && c <= 0x205E) ||
        (c >= 0x20A0 && c <= 0x20CF) || (c >= 0x2100 && c <= 0x214F) || (c >= 0x2190 && c <= 0x2BFF) ||
        (c >= 0x3001 && c <= 0x3003) || (c >= 0xFF01 && c <= 0xFF0F))
        return C1_PUNCT | C1_DEFINED;
    return C1_ALPHA | C1_DEFINED;
}

/* LCIDs this system knows: en-US and the default/neutral aliases */
static bool known_lcid(LCID lcid)
{
    unsigned l = (unsigned)W32_DW(lcid) & 0xFFFFu;
    return l == 0x0409 || l == LOCALE_USER_DEFAULT || l == LOCALE_SYSTEM_DEFAULT || l == 0 || l == 0x007F;
}

/* the mapping of LCMapString: 0 copy, 1 lower, 2 upper, 3 sort key; -1 bad flags */
static int map_kind(DWORD flags)
{
    flags = W32_DW(flags);
    int lo = (flags & LCMAP_LOWERCASE) != 0, up = (flags & LCMAP_UPPERCASE) != 0, key = (flags & LCMAP_SORTKEY) != 0;
    if (lo + up + key > 1)
        return -1;
    return key ? 3 : up ? 2 : lo;
}

/* sort key of n code units (NULs skipped): primary weights (folded, 2 bytes big-endian), 00 00, one case byte per
 * unit (1 lower/uncased, 2 upper), 00 */
static int sort_key(const uint16_t *s, size_t n, BYTE *out, int cap)
{
    sink<BYTE> o = { cap ? out : NULL, cap, 0, false };
    for (size_t i = 0; i < n; i++)
        if (s[i]) {
            unsigned f = wlower(s[i]);
            o.put((BYTE)(f >> 8));
            o.put((BYTE)f);
        }
    o.put(0);
    o.put(0);
    for (size_t i = 0; i < n; i++)
        if (s[i])
            o.put(wlower(s[i]) != s[i] ? 2 : 1);
    o.put(0);
    if (o.over) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    return o.n;
}

extern "C" int LCMapStringW(LCID lcid, DWORD flags, LPCWSTR src, int srclen, LPWSTR dst, int dstlen)
{
    int kind = map_kind(flags);
    if (kind < 0) {
        SetLastError(ERROR_INVALID_FLAGS);
        return 0;
    }
    if (!src || !srclen || dstlen < 0 || (dstlen && !dst)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    W32_UNUSED(lcid);
    size_t n = srclen < 0 ? wlen(src) + 1 : (size_t)srclen;
    if (kind == 3)
        return sort_key((const uint16_t *)src, srclen < 0 ? n - 1 : n, (BYTE *)dst, dstlen);
    if (!dstlen)
        return (int)n;
    if ((size_t)dstlen < n) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        unsigned c = (uint16_t)src[i];
        dst[i] = (WCHAR)(kind == 1 ? wlower(c) : kind == 2 ? wupper(c) : c);
    }
    return (int)n;
}

extern "C" int LCMapStringA(LCID lcid, DWORD flags, LPCSTR src, int srclen, LPSTR dst, int dstlen)
{
    int kind = map_kind(flags);
    if (kind < 0) {
        SetLastError(ERROR_INVALID_FLAGS);
        return 0;
    }
    if (!src || !srclen || dstlen < 0 || (dstlen && !dst)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    W32_UNUSED(lcid);
    const unsigned char *s = (const unsigned char *)src;
    size_t n = srclen < 0 ? strlen(src) + 1 : (size_t)srclen;
    if (kind == 3) {
        size_t m = srclen < 0 ? n - 1 : n;
        uint16_t *w = (uint16_t *)malloc((m ? m : 1) * sizeof *w);
        if (!w) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return 0;
        }
        for (size_t i = 0; i < m; i++)
            w[i] = (uint16_t)w1252(s[i]);
        int r = sort_key(w, m, (BYTE *)dst, dstlen);
        free(w);
        return r;
    }
    if (!dstlen)
        return (int)n;
    if ((size_t)dstlen < n) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        unsigned w = w1252(s[i]);
        int b = to1252(kind == 1 ? wlower(w) : kind == 2 ? wupper(w) : w);
        dst[i] = (char)(b < 0 ? s[i] : b);   /* the other case is not in 1252 (y diaeresis is; mu is not) */
    }
    return (int)n;
}

static int compare_units(DWORD flags, const uint16_t *a, size_t na, const uint16_t *b, size_t nb)
{
    size_t n = na < nb ? na : nb;
    for (size_t i = 0; i < n; i++) {
        unsigned x = wlower(a[i]), y = wlower(b[i]);
        if (x != y)
            return x < y ? CSTR_LESS_THAN : CSTR_GREATER_THAN;
    }
    if (na != nb)
        return na < nb ? CSTR_LESS_THAN : CSTR_GREATER_THAN;
    if (!(W32_DW(flags) & NORM_IGNORECASE))
        for (size_t i = 0; i < n; i++)
            if (a[i] != b[i])
                return wlower(a[i]) != a[i] ? CSTR_GREATER_THAN : CSTR_LESS_THAN;   /* upper after lower */
    return CSTR_EQUAL;
}

extern "C" int CompareStringW(LCID lcid, DWORD flags, LPCWSTR a, int na, LPCWSTR b, int nb)
{
    W32_UNUSED(lcid);
    if (!a || !b) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    return compare_units(flags, (const uint16_t *)a, na < 0 ? wlen(a) : (size_t)na, (const uint16_t *)b,
                         nb < 0 ? wlen(b) : (size_t)nb);
}

extern "C" int CompareStringA(LCID lcid, DWORD flags, LPCSTR a, int na, LPCSTR b, int nb)
{
    W32_UNUSED(lcid);
    if (!a || !b) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    size_t la = na < 0 ? strlen(a) : (size_t)na, lb = nb < 0 ? strlen(b) : (size_t)nb;
    uint16_t *w = (uint16_t *)malloc((la + lb + 1) * sizeof *w);
    if (!w) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return 0;
    }
    for (size_t i = 0; i < la; i++)
        w[i] = (uint16_t)w1252((unsigned char)a[i]);
    for (size_t i = 0; i < lb; i++)
        w[la + i] = (uint16_t)w1252((unsigned char)b[i]);
    int r = compare_units(flags, w, la, w + la, lb);
    free(w);
    return r;
}

extern "C" BOOL GetStringTypeW(DWORD info, LPCWSTR src, int srclen, LPWORD out)
{
    info = W32_DW(info);
    if (info != CT_CTYPE1 && info != CT_CTYPE2 && info != CT_CTYPE3) {
        SetLastError(ERROR_INVALID_FLAGS);
        return FALSE;
    }
    if (!src || !out || !srclen) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    size_t n = srclen < 0 ? wlen(src) + 1 : (size_t)srclen;   /* -1 includes the NUL (as Wine) */
    for (size_t i = 0; i < n; i++)
        out[i] = info == CT_CTYPE1 ? ctype1((uint16_t)src[i]) : 0;   /* CTYPE2/3 (bidi, misc): not classified */
    return TRUE;
}

extern "C" BOOL GetStringTypeA(LCID lcid, DWORD info, LPCSTR src, int srclen, LPWORD out)
{
    W32_UNUSED(lcid);
    info = W32_DW(info);
    if (info != CT_CTYPE1 && info != CT_CTYPE2 && info != CT_CTYPE3) {
        SetLastError(ERROR_INVALID_FLAGS);
        return FALSE;
    }
    if (!src || !out || !srclen) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    size_t n = srclen < 0 ? strlen(src) + 1 : (size_t)srclen;
    for (size_t i = 0; i < n; i++)
        out[i] = info == CT_CTYPE1 ? ctype1(w1252((unsigned char)src[i])) : 0;
    return TRUE;
}

/* ---- locale: en-US as Windows 7 reports it (the items the MSVC 2008 CRT's setlocale, localeconv and strftime
 * read, and the rest of the common LOCALE_* set). LOCALE_IDEFAULTCODEPAGE is 1252: see the top of the file. */
struct locale_item { LCTYPE type; const char *value; };
static const locale_item k_locale[] = {
    {0x0001, "0409"}, {0x0002, "English (United States)"}, {0x1001, "English"}, {0x0003, "ENU"},
    {0x0004, "English"}, {0x0005, "1"}, {0x0006, "United States"}, {0x1002, "United States"}, {0x0007, "USA"},
    {0x0008, "United States"}, {0x0009, "0409"}, {0x000A, "1"}, {0x000B, "1252"}, {0x1004, "1252"},
    {0x1011, "10000"}, {0x000C, ","}, {0x000D, "1"}, {0x000E, "."}, {0x000F, ","}, {0x0010, "3;0"},
    {0x0011, "2"}, {0x0012, "1"}, {0x1010, "1"}, {0x0013, "0123456789"}, {0x0014, "$"}, {0x0015, "USD"},
    {0x0016, "."}, {0x0017, ","}, {0x0018, "3;0"}, {0x0019, "2"}, {0x001A, "2"}, {0x001B, "0"}, {0x001C, "0"},
    {0x1007, "US Dollar"}, {0x1008, "US Dollar"}, {0x001D, "/"}, {0x001E, ":"}, {0x001F, "M/d/yyyy"},
    {0x0020, "dddd, MMMM dd, yyyy"}, {0x1003, "h:mm:ss tt"}, {0x1006, "MMMM, yyyy"}, {0x0021, "0"},
    {0x0022, "0"}, {0x0023, "0"}, {0x1005, "0"}, {0x0024, "1"}, {0x0025, "0"}, {0x0026, "0"}, {0x0027, "0"},
    {0x0028, "AM"}, {0x0029, "PM"}, {0x1009, "1"}, {0x100B, "0"}, {0x100C, "6"}, {0x100D, "0"},
    {0x002A, "Monday"}, {0x002B, "Tuesday"}, {0x002C, "Wednesday"}, {0x002D, "Thursday"}, {0x002E, "Friday"},
    {0x002F, "Saturday"}, {0x0030, "Sunday"}, {0x0031, "Mon"}, {0x0032, "Tue"}, {0x0033, "Wed"},
    {0x0034, "Thu"}, {0x0035, "Fri"}, {0x0036, "Sat"}, {0x0037, "Sun"},
    {0x0038, "January"}, {0x0039, "February"}, {0x003A, "March"}, {0x003B, "April"}, {0x003C, "May"},
    {0x003D, "June"}, {0x003E, "July"}, {0x003F, "August"}, {0x0040, "September"}, {0x0041, "October"},
    {0x0042, "November"}, {0x0043, "December"}, {0x100E, ""},
    {0x0044, "Jan"}, {0x0045, "Feb"}, {0x0046, "Mar"}, {0x0047, "Apr"}, {0x0048, "May"}, {0x0049, "Jun"},
    {0x004A, "Jul"}, {0x004B, "Aug"}, {0x004C, "Sep"}, {0x004D, "Oct"}, {0x004E, "Nov"}, {0x004F, "Dec"},
    {0x100F, ""}, {0x0050, ""}, {0x0051, "-"}, {0x0052, "3"}, {0x0053, "0"}, {0x0054, "1"}, {0x0055, "0"},
    {0x0056, "1"}, {0x0057, "0"}, {0x0059, "en"}, {0x005A, "US"}, {0x005C, "en-US"},
};

/* the value, or NULL with the error set */
static const char *locale_value(LCID lcid, LCTYPE type)
{
    if (!known_lcid(lcid)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    unsigned t = (unsigned)W32_DW(type) & ~(LOCALE_NOUSEROVERRIDE | LOCALE_USE_CP_ACP | LOCALE_RETURN_NUMBER);
    for (const locale_item &i : k_locale)
        if (i.type == t)
            return i.value;
    SetLastError(ERROR_INVALID_FLAGS);
    return NULL;
}

/* LOCALE_RETURN_NUMBER: the value as a DWORD in place of the string; cch counts characters of the caller's width */
static int locale_number(LCTYPE type, const char *v, void *out, int cch, int unit)
{
    unsigned t = (unsigned)W32_DW(type) & 0xFFFFu;
    int need = 4 / unit;
    if (!cch)
        return need;
    if (cch < need) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    char *end;
    uint32_t n = (uint32_t)strtoul(v, &end, t == 0x0001 || t == 0x0009 ? 16 : 10);   /* the LANGIDs are hex */
    if (end == v || *end) {
        SetLastError(ERROR_INVALID_FLAGS);
        return 0;
    }
    memcpy(out, &n, 4);
    return need;
}

extern "C" int GetLocaleInfoA(LCID lcid, LCTYPE type, LPSTR out, int cch)
{
    if (cch < 0 || (cch && !out)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    const char *v = locale_value(lcid, type);
    if (!v)
        return 0;
    if (W32_DW(type) & LOCALE_RETURN_NUMBER)
        return locale_number(type, v, out, cch, 1);
    int len = (int)strlen(v) + 1;
    if (!cch)
        return len;
    if (cch < len) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    memcpy(out, v, (size_t)len);
    return len;
}

extern "C" int GetLocaleInfoW(LCID lcid, LCTYPE type, LPWSTR out, int cch)
{
    if (cch < 0 || (cch && !out)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    const char *v = locale_value(lcid, type);
    if (!v)
        return 0;
    if (W32_DW(type) & LOCALE_RETURN_NUMBER)
        return locale_number(type, v, out, cch, 2);
    int len = (int)strlen(v) + 1;
    if (!cch)
        return len;
    if (cch < len) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    for (int i = 0; i < len; i++)
        out[i] = (WCHAR)(unsigned char)v[i];   /* the values are ASCII */
    return len;
}

extern "C" BOOL IsValidLocale(LCID lcid, DWORD flags)
{
    W32_UNUSED(flags);
    return known_lcid(lcid) && (W32_DW(lcid) & 0xFFFFu) != 0;
}

extern "C" BOOL EnumSystemLocalesA(LOCALE_ENUMPROCA proc, DWORD flags)
{
    W32_UNUSED(flags);
    if (!proc) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    char id[] = "00000409";
    proc(id);
    return TRUE;
}

extern "C" LCID GetUserDefaultLCID(void) { return 0x0409; }

extern "C" DWORD GetTimeZoneInformation(LPTIME_ZONE_INFORMATION tz)
{
    if (!tz) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0xFFFFFFFFu;   /* TIME_ZONE_ID_INVALID */
    }
    /* one zone without daylight rules, at the offset GetLocalTime applies (UTC unless the OS reports one) */
    memset(tz, 0, sizeof *tz);
    tz->Bias = -(LONG)os_tz_offset_minutes();
    return TIME_ZONE_ID_UNKNOWN;
}

/* ---- standard handles */
#define STD_IN ((HANDLE)(uintptr_t)0x3)
#define STD_OUT ((HANDLE)(uintptr_t)0x7)
#define STD_ERR ((HANDLE)(uintptr_t)0xB)
static HANDLE g_std[3] = { STD_IN, STD_OUT, STD_ERR };
struct std_line { char buf[512]; size_t n; };
static std_line g_lines[2];
static os_lock_t g_std_lock;

int w32_is_std_handle(HANDLE h) { return h == STD_IN || h == STD_OUT || h == STD_ERR; }

static int std_index(DWORD which)
{
    switch ((uint32_t)W32_DW(which)) {
    case 0xFFFFFFF6u: return 0;
    case 0xFFFFFFF5u: return 1;
    case 0xFFFFFFF4u: return 2;
    default: return -1;
    }
}

static void std_emit(std_line *l)
{
    if (l->n && l->buf[l->n - 1] == '\r')
        l->n--;
    l->buf[l->n] = 0;
    os_debug_print(l->buf);
    l->n = 0;
}

static void std_write(HANDLE h, const char *p, size_t n)
{
    std_line *l = &g_lines[h == STD_ERR];
    os_lock(&g_std_lock);
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '\n') {
            std_emit(l);
            continue;
        }
        l->buf[l->n++] = p[i];
        if (l->n == sizeof l->buf - 1)
            std_emit(l);
    }
    os_unlock(&g_std_lock);
}

int w32_std_io(HANDLE h, int write, void *buf, DWORD n, LPDWORD done)
{
    if (!w32_is_std_handle(h))
        return -1;
    if (done)
        *done = 0;
    if (h == STD_IN) {
        if (write) {
            SetLastError(ERROR_ACCESS_DENIED);
            return FALSE;
        }
        return TRUE;   /* end of file */
    }
    if (!write) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    std_write(h, (const char *)buf, W32_DW(n));
    if (done)
        *done = W32_DW(n);
    return TRUE;
}

extern "C" HANDLE GetStdHandle(DWORD which)
{
    int i = std_index(which);
    if (i < 0) {
        SetLastError(ERROR_INVALID_HANDLE);
        return INVALID_HANDLE_VALUE;
    }
    return g_std[i];
}

extern "C" BOOL SetStdHandle(DWORD which, HANDLE h)
{
    int i = std_index(which);
    if (i < 0) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    g_std[i] = h;
    return TRUE;
}

extern "C" DWORD GetFileType(HANDLE h)
{
    if (w32_is_std_handle(h))
        return FILE_TYPE_CHAR;
    struct w32_obj *o = w32_obj_get(h, 0);   /* ERROR_INVALID_HANDLE when bad */
    if (!o)
        return FILE_TYPE_UNKNOWN;
    if (o->type == W32_FILE)
        return FILE_TYPE_DISK;
    SetLastError(ERROR_SUCCESS);
    return FILE_TYPE_UNKNOWN;
}

extern "C" BOOL GetConsoleMode(HANDLE h, LPDWORD mode)
{
    W32_UNUSED(mode);
    W32_UNUSED(h);
    SetLastError(ERROR_INVALID_HANDLE);   /* no console: the standard handles are plain character devices */
    return FALSE;
}

extern "C" BOOL WriteConsoleA(HANDLE h, const void *buf, DWORD n, LPDWORD put, LPVOID reserved)
{
    W32_UNUSED(reserved);
    if (put)
        *put = 0;
    if (h != STD_OUT && h != STD_ERR) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    std_write(h, (const char *)buf, W32_DW(n));
    if (put)
        *put = W32_DW(n);
    return TRUE;
}

extern "C" BOOL WriteConsoleW(HANDLE h, const void *buf, DWORD n, LPDWORD put, LPVOID reserved)
{
    W32_UNUSED(reserved);
    if (put)
        *put = 0;
    if (h != STD_OUT && h != STD_ERR) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    const WCHAR *w = (const WCHAR *)buf;
    for (DWORD i = 0, cnt = W32_DW(n); i < cnt;) {   /* in chunks, as the log's code page (1252) */
        char tmp[256];
        int k = (int)(cnt - i < sizeof tmp ? cnt - i : sizeof tmp);
        WideCharToMultiByte(CP_ACP, 0, w + i, k, tmp, k, NULL, NULL);
        std_write(h, tmp, (size_t)k);
        i += (DWORD)k;
    }
    if (put)
        *put = W32_DW(n);
    return TRUE;
}

extern "C" UINT SetHandleCount(UINT n) { return n; }

/* ---- files and modules by UTF-16 name: as the A functions, with the name in the ANSI code page */
static bool narrow_name(LPCWSTR w, char *out, size_t n)
{
    BOOL used = FALSE;
    int r = WideCharToMultiByte(CP_ACP, 0, w, -1, out, (int)n, NULL, &used);
    return r > 0 && !used;
}

extern "C" HANDLE CreateFileW(LPCWSTR name, DWORD access, DWORD share, void *sa, DWORD disp, DWORD flags, HANDLE tmpl)
{
    char a[W32_PATH_MAX];
    if (!name) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return INVALID_HANDLE_VALUE;
    }
    if (!narrow_name(name, a, sizeof a)) {   /* a character outside 1252: no file the A API can name */
        SetLastError(ERROR_INVALID_NAME);
        return INVALID_HANDLE_VALUE;
    }
    return CreateFileA(a, access, share, sa, disp, flags, tmpl);
}

extern "C" HMODULE GetModuleHandleW(LPCWSTR name)
{
    if (!name)
        return GetModuleHandleA(NULL);
    char a[256];
    if (!narrow_name(name, a, sizeof a)) {
        SetLastError(ERROR_MOD_NOT_FOUND);
        return NULL;
    }
    return GetModuleHandleA(a);
}

/* kernel32.dll and user32.dll as modules: the CRT's _mtinit fails without GetModuleHandleW(L"KERNEL32.DLL") (it
 * then asks it for FlsAlloc/EncodePointer, absent here: it falls back to TLS), and __crtMessageBoxA reaches
 * MessageBoxA through LoadLibraryA("USER32.DLL"). GetProcAddress serves the export table; the guard stops the
 * table's fallback to registered modules from asking this resolver again. */
static __thread int g_sys_depth;
static FARPROC sys_export(const char *dll, const char *proc)
{
    if (g_sys_depth)
        return NULL;
    g_sys_depth++;
    void *p = fnvnx_win32_lookup_export(dll, proc);
    g_sys_depth--;
    return (FARPROC)p;
}
static FARPROC kernel32_export(const char *proc) { return sys_export("kernel32", proc); }
static FARPROC user32_export(const char *proc) { return sys_export("user32", proc); }
__attribute__((constructor(FNVNX_INIT_MODULES))) static void sys_modules_register(void)
{
    fnvnx_register_module("kernel32.dll", kernel32_export);
    fnvnx_register_module("user32.dll", user32_export);
}

/* ---- process */
extern "C" BOOL TerminateProcess(HANDLE h, UINT code)
{
    if (h == W32_PSEUDO_PROCESS) {
        w32_log("TerminateProcess(%u)", (unsigned)code);
        os_exit_process((int)code);
    }
    SetLastError(ERROR_INVALID_HANDLE);   /* no other processes */
    return FALSE;
}

static LPTOP_LEVEL_EXCEPTION_FILTER g_top_filter;

extern "C" LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER f)
{
    return __atomic_exchange_n(&g_top_filter, f, __ATOMIC_SEQ_CST);
}

/* as on Windows: the top-level filter decides; without one (or when it continues the search) the process is to
 * be terminated by the caller (EXCEPTION_EXECUTE_HANDLER) - there is no error reporting dialog */
extern "C" LONG UnhandledExceptionFilter(struct _EXCEPTION_POINTERS *ep)
{
    LPTOP_LEVEL_EXCEPTION_FILTER f = __atomic_load_n(&g_top_filter, __ATOMIC_SEQ_CST);
    w32_log("UnhandledExceptionFilter(%p)%s", (void *)ep, f ? ": calling the top-level filter" : "");
    if (f && ep) {
        LONG r = f(ep);
        if (r != EXCEPTION_CONTINUE_SEARCH)
            return r;
    }
    return EXCEPTION_EXECUTE_HANDLER;
}

extern "C" void GetStartupInfoA(LPSTARTUPINFOA si)
{
    if (!si)
        return;
    memset(si, 0, sizeof *si);
    si->cb = sizeof *si;
}

extern "C" UINT GetDriveTypeA(LPCSTR root)
{
    /* NULL, "\" and relative: the current drive (C:) */
    if (!root || !((root[0] | 32) >= 'a' && (root[0] | 32) <= 'z' && root[1] == ':'))
        return root && root[0] && root[0] != '\\' && root[0] != '/' ? DRIVE_NO_ROOT_DIR : DRIVE_FIXED;
    if (root[2] && !((root[2] == '\\' || root[2] == '/') && !root[3]))
        return DRIVE_NO_ROOT_DIR;   /* not a drive root */
    if ((root[0] | 32) == 'c')
        return DRIVE_FIXED;
    char host[W32_PATH_MAX];
    char win[4] = { root[0], ':', '\\', 0 };
    os_stat_t st;
    return !w32_map_path(win, host, sizeof host) && !os_stat(host, &st) && st.is_dir ? DRIVE_FIXED : DRIVE_NO_ROOT_DIR;
}

/* ---- environment: a process-local block (the CRT builds its environ from it), seeded with the variables a
 * Windows session always has (PATH, TEMP, SystemRoot, USERPROFILE, APPDATA...) on first use */
static char **g_env;   /* "NAME=value", NULL-terminated */
static size_t g_nenv;
static os_lock_t g_env_lock;
static bool g_env_seeded;

static const char *const ENV_DEFAULTS[] = {
    "ALLUSERSPROFILE=C:\\ProgramData", "APPDATA=C:\\Users\\Player\\AppData\\Roaming",
    "COMPUTERNAME=FNVNX", "ComSpec=C:\\Windows\\System32\\cmd.exe",
    "HOMEDRIVE=C:", "HOMEPATH=\\Users\\Player",
    "LOCALAPPDATA=C:\\Users\\Player\\AppData\\Local", "NUMBER_OF_PROCESSORS=4", "OS=Windows_NT",
    "PATH=C:\\Windows\\System32;C:\\Windows", "PATHEXT=.COM;.EXE;.BAT;.CMD",
    "PROCESSOR_ARCHITECTURE=x86", "ProgramData=C:\\ProgramData", "ProgramFiles=C:\\Program Files",
    "SystemDrive=C:", "SystemRoot=C:\\Windows", "TEMP=C:\\Users\\Player\\AppData\\Local\\Temp",
    "TMP=C:\\Users\\Player\\AppData\\Local\\Temp", "USERNAME=Player", "USERPROFILE=C:\\Users\\Player",
    "windir=C:\\Windows",
};

/* caller holds the lock */
static void env_seed(void)
{
    if (g_env_seeded)
        return;
    g_env_seeded = true;
    size_t n = sizeof ENV_DEFAULTS / sizeof *ENV_DEFAULTS;
    char **v = (char **)malloc((n + 1) * sizeof *v);
    if (!v)
        return;
    g_env = v;
    for (size_t i = 0; i < n; i++)
        if ((v[g_nenv] = strdup(ENV_DEFAULTS[i])))
            g_nenv++;
}

/* index of name, or -1; caller holds the lock */
static long env_find(const char *name)
{
    size_t ln = strlen(name);
    for (size_t i = 0; i < g_nenv; i++)
        if (!w32_strnicmp(g_env[i], name, ln) && g_env[i][ln] == '=')
            return (long)i;
    return -1;
}

static bool env_name_ok(const char *name) { return name && *name && !strchr(name + 1, '='); }

extern "C" BOOL SetEnvironmentVariableA(LPCSTR name, LPCSTR value)
{
    if (!env_name_ok(name)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    char *e = NULL;
    if (value) {
        size_t ln = strlen(name), lv = strlen(value);
        e = (char *)malloc(ln + lv + 2);
        if (!e) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return FALSE;
        }
        memcpy(e, name, ln);
        e[ln] = '=';
        memcpy(e + ln + 1, value, lv + 1);
    }
    os_lock(&g_env_lock);
    env_seed();
    long i = env_find(name);
    if (i < 0 && !e) {   /* deleting a variable that is not there */
        os_unlock(&g_env_lock);
        SetLastError(ERROR_ENVVAR_NOT_FOUND);
        return FALSE;
    }
    if (i >= 0) {
        free(g_env[i]);
        if (e)
            g_env[i] = e;
        else
            g_env[i] = g_env[--g_nenv];
    } else if (e) {
        char **n = (char **)realloc(g_env, (g_nenv + 2) * sizeof *n);
        if (!n) {
            os_unlock(&g_env_lock);
            free(e);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return FALSE;
        }
        g_env = n;
        g_env[g_nenv++] = e;
    }
    os_unlock(&g_env_lock);
    return TRUE;
}

extern "C" DWORD GetEnvironmentVariableA(LPCSTR name, LPSTR buf, DWORD n)
{
    n = W32_DW(n);
    if (!env_name_ok(name)) {
        SetLastError(ERROR_ENVVAR_NOT_FOUND);
        return 0;
    }
    os_lock(&g_env_lock);
    env_seed();
    long i = env_find(name);
    DWORD r = 0;
    if (i < 0) {
        SetLastError(ERROR_ENVVAR_NOT_FOUND);
    } else {
        const char *v = g_env[i] + strlen(name) + 1;
        DWORD len = (DWORD)strlen(v);
        if (len < n && buf) {
            memcpy(buf, v, len + 1);
            r = len;
            if (!len)
                SetLastError(ERROR_SUCCESS);   /* an empty value: 0 with no error */
        } else {
            r = len + 1;   /* the size needed, NUL included */
        }
    }
    os_unlock(&g_env_lock);
    return r;
}

extern "C" char *GetEnvironmentStrings(void)
{
    os_lock(&g_env_lock);
    env_seed();
    size_t total = 2;
    for (size_t i = 0; i < g_nenv; i++)
        total += strlen(g_env[i]) + 1;
    char *b = (char *)malloc(total), *p = b;
    if (b) {
        for (size_t i = 0; i < g_nenv; i++) {
            size_t l = strlen(g_env[i]) + 1;
            memcpy(p, g_env[i], l);
            p += l;
        }
        p[0] = p[1] = 0;   /* an empty block is two NULs */
    }
    os_unlock(&g_env_lock);
    if (!b)
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return b;
}

extern "C" WCHAR *GetEnvironmentStringsW(void)
{
    char *a = GetEnvironmentStrings();
    if (!a)
        return NULL;
    size_t n = 0;
    while (a[n] || a[n + 1])
        n++;
    n += 2;
    WCHAR *w = (WCHAR *)malloc(n * sizeof *w);
    if (w)
        for (size_t i = 0; i < n; i++)
            w[i] = (WCHAR)w1252((unsigned char)a[i]);
    else
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    free(a);
    return w;
}

extern "C" BOOL FreeEnvironmentStringsA(char *b)
{
    free(b);
    return TRUE;
}

extern "C" BOOL FreeEnvironmentStringsW(WCHAR *b)
{
    free(b);
    return TRUE;
}

/* ---- advapi32 / user32 */
extern "C" BOOL GetUserNameA(LPSTR buf, LPDWORD size)
{
    static const char name[] = "Player";   /* the profile directory's name (path.cpp: C:\Users\Player) */
    if (!size) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    if (W32_DW(*size) < sizeof name || !buf) {
        *size = sizeof name;
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }
    memcpy(buf, name, sizeof name);
    *size = sizeof name;   /* NUL included */
    return TRUE;
}

/* window classes keep only their name and procedure (window.cpp): every class long reads 0 */
extern "C" DWORD GetClassLongA(HWND h, int index)
{
    W32_UNUSED(h);
    W32_UNUSED(index);
    return 0;
}
