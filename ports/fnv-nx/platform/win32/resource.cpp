/* resource.cpp - PE resources of a module's mapped image (kernel32 FindResourceA/ExA, LoadResource, ..., EnumResourceNamesA),
 * the user32 icon/cursor loaders built on them, and the comctl32 image list loader.
 *
 * Rule. A resource is looked up in the resource directory (data directory 2) of the module's mapped PE32 image, as
 * Windows does: three levels type / name / language, every offset bounds-checked against the image; a malformed
 * directory fails with ERROR_BAD_EXE_FORMAT (193), absence of image or directory with 1812, then 1813 / 1814 / 1815
 * for type / name / language. Names are integers or UTF-16 strings compared case-insensitively; "#n" is the integer n.
 * Language: the requested LANGID, else 0x0409 (the user default), else neutral, else any English, else the first.
 * The image of a module is the one registered with fnvnx_resource_register_image (the loader calls it when it maps
 * an image; module.cpp keeps no image base/size); a module without one has no resources. HRSRC is the pointer to the IMAGE_RESOURCE_DATA_ENTRY inside the image,
 * HGLOBAL the pointer to the data (the image is mapped: RVA == offset), so FreeResource has nothing to free.
 *
 * Icons/cursors: group directory -> best image (closest size to SM_CXICON/SM_CXCURSOR, then most colours <= 32 bpp)
 * -> decoded 32bpp 0xAARRGGBB pixels (alpha: the alpha channel if any is non-zero, else the AND mask). Mono cursor
 * pixels with AND=1 and XOR=1 (screen invert) are transparent in the pixels and set in the icon's invert mask
 * (fnvnx_icon_invert_mask). Images: PNG-compressed icons (all colour types, depths, Adam7), and every DIB encoding
 * (see dib_decode); .ico/.cur files through ImageList_LoadImageA. Malformed data: ERROR_INVALID_DATA, NULL. */
#include "w32.h"
#include "inflate.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

extern "C" HMODULE fnvnx_win32_exe_module(void);   // module.cpp: the exe image's base (0x00400000 until the loader registers it)
#define EXE_MODULE fnvnx_win32_exe_module()
#define E_INVALID_DATA 13
#define E_BAD_EXE 193

namespace {
os_lock_t g_lock;
struct TI { TI *next; HMODULE m; const uint8_t *b; size_t n; } *g_ti;
struct Icon { Icon *next; HMODULE m; const void *ent; int cur, w, h, hx, hy; uint32_t *px; uint8_t *inv; };
struct IList { IList *next; int count, cx, cy; uint32_t *px; };
Icon *g_icons;
IList *g_lists;
uint32_t g_icc;
struct Lk { Lk() { os_lock(&g_lock); } ~Lk() { os_unlock(&g_lock); } };

struct Img { const uint8_t *b; size_t n; uint32_t root; };
struct Key { int is_id; uint32_t id; const WCHAR *w; size_t wn; };   // a name is UTF-16, as the directory stores it
struct Dib { uint32_t *px; int w, h; uint8_t *inv; };

inline unsigned r16(const uint8_t *p) { return p[0] | p[1] << 8; }
inline uint32_t r32(const uint8_t *p) { return r16(p) | (uint32_t)r16(p + 2) << 16; }
inline bool ok(const Img &im, uint64_t off, uint64_t len) { return off <= im.n && len <= im.n - off; }
bool fail(DWORD e) { SetLastError(e); return false; }

bool open_image(HMODULE m, Img *im)
{
    if (!m) m = EXE_MODULE;
    const void *b = NULL; size_t n = 0;
    { Lk l; for (TI *t = g_ti; t; t = t->next) if (t->b && t->m == m) { b = t->b; n = t->n; } }
    if (!b) return fail(1812 /* ERROR_RESOURCE_DATA_NOT_FOUND */);
    im->b = (const uint8_t *)b; im->n = n;
    if (!ok(*im, 0, 0x40) || r16(im->b) != 0x5A4D) return fail(E_BAD_EXE);
    uint64_t pe = r32(im->b + 0x3C), opt = pe + 24;
    if (!ok(*im, pe, 24) || r32(im->b + pe) != 0x4550 || !ok(*im, opt, 96) || r16(im->b + opt) != 0x10B) return fail(E_BAD_EXE);
    if (r32(im->b + opt + 92) <= 2 || r16(im->b + pe + 20) < 96 + 24) return fail(1812);   /* no resource directory entry */
    if (!ok(*im, opt + 96 + 16, 8)) return fail(E_BAD_EXE);
    uint32_t rva = r32(im->b + opt + 96 + 16), size = r32(im->b + opt + 96 + 20);
    if (!rva || !size) return fail(1812);
    if (!ok(*im, rva, 16)) return fail(E_BAD_EXE);
    im->root = rva;
    return true;
}

/* a resource name or type argument: IS_INTRESOURCE gives an integer id, "#digits" the integer (modulo 2^32), any other
 * string a UTF-16 name (an ANSI name is converted in the ANSI code page; one rule for both entry points) */
Key mkkeyw(const WCHAR *p)
{
    Key k = { 0, 0, p, 0 };
    uintptr_t v = (uintptr_t)p;
    if (v <= 0xFFFF) { k.is_id = 1; k.id = (uint32_t)v; return k; }
    size_t n = 0;
    while (p[n]) n++;
    k.wn = n;
    if (p[0] == '#' && n > 1) {
        uint32_t x = 0; size_t i = 1;
        for (; i < n && p[i] >= '0' && p[i] <= '9'; i++) x = x * 10 + (uint32_t)(p[i] - '0');
        if (i == n) { k.is_id = 1; k.id = x; k.w = NULL; k.wn = 0; }
    }
    return k;
}
struct KeyA {
    Key k;
    WCHAR st[128];
    WCHAR *hp;
    explicit KeyA(LPCSTR p) : hp(NULL)
    {
        k.is_id = 1; k.id = (uint32_t)(uintptr_t)p; k.w = NULL; k.wn = 0;
        if ((uintptr_t)p <= 0xFFFF) return;
        int n = MultiByteToWideChar(CP_ACP, 0, p, -1, NULL, 0);
        WCHAR *w = st;
        if (n > (int)(sizeof st / sizeof st[0])) w = hp = (WCHAR *)malloc((size_t)n * sizeof(WCHAR));
        k.is_id = 0;
        if (n <= 0 || !w || MultiByteToWideChar(CP_ACP, 0, p, -1, w, n) != n) return;   /* k.w == NULL: no such name */
        k = mkkeyw(w);
    }
    ~KeyA() { free(hp); }
    KeyA(const KeyA &) = delete;
};
Key idkey(uint32_t id) { Key k = { 1, id, NULL, 0 }; return k; }

struct Dir { uint64_t ents; unsigned n; };
bool open_dir(const Img &im, uint32_t rel, Dir *d)
{
    uint64_t a = (uint64_t)im.root + rel;
    if (!ok(im, a, 16)) return fail(E_BAD_EXE);
    d->n = r16(im.b + a + 12) + r16(im.b + a + 14);
    d->ents = a + 16;
    return ok(im, d->ents, 8ull * d->n) || fail(E_BAD_EXE);
}
WCHAR upcase(WCHAR c)   /* names compare case-insensitively: the invariant locale's upper case of the UTF-16 unit */
{
    WCHAR u;
    return LCMapStringW(0x7F, LCMAP_UPPERCASE, &c, 1, &u, 1) == 1 ? u : c;
}
/* 1 = entry offset in *off, 0 = no such entry, -1 = malformed */
int lookup(const Img &im, const Dir &d, const Key &k, uint32_t *off)
{
    for (unsigned i = 0; i < d.n; i++) {
        uint32_t nm = r32(im.b + d.ents + 8 * i);
        if (k.is_id) { if (!(nm & 0x80000000u) && nm == k.id) { *off = r32(im.b + d.ents + 8 * i + 4); return 1; } continue; }
        if (!(nm & 0x80000000u)) continue;
        if (!k.w) { fail(ERROR_NOT_ENOUGH_MEMORY); return -1; }   /* the name could not be built */
        uint64_t a = (uint64_t)im.root + (nm & 0x7FFFFFFFu);
        if (!ok(im, a, 2) || !ok(im, a + 2, 2ull * r16(im.b + a))) { fail(E_BAD_EXE); return -1; }
        size_t len = r16(im.b + a);
        if (k.wn != len) continue;
        size_t j = 0;
        for (; j < len; j++) {
            WCHAR c = (WCHAR)r16(im.b + a + 2 + 2 * j), q = k.w[j];
            if (c != q && upcase(c) != upcase(q)) break;
        }
        if (j == len) { *off = r32(im.b + d.ents + 8 * i + 4); return 1; }
    }
    return 0;
}
/* the IMAGE_RESOURCE_DATA_ENTRY of (type, name, language) or NULL with the error set */
const uint8_t *find(const Img &im, Key type, Key name, unsigned lang, bool strict = false)   /* strict: only an entry of exactly this language */
{
    Dir d; uint32_t off;
    if (!open_dir(im, 0, &d)) return NULL;
    for (int lv = 0; lv < 2; lv++) {
        int r = lookup(im, d, lv ? name : type, &off);
        if (r < 0) return NULL;
        if (!r) { SetLastError(lv ? 1814 : 1813); return NULL; }
        if (!(off & 0x80000000u) || !open_dir(im, off & 0x7FFFFFFFu, &d)) { SetLastError(E_BAD_EXE); return NULL; }
    }
    int exact = -1, def = -1, neut = -1, eng = -1, first = -1;
    for (unsigned i = 0; i < d.n; i++) {
        uint32_t id = r32(im.b + d.ents + 8 * i);
        if (id & 0x80000000u) continue;
        if (first < 0) first = (int)i;
        if (id == lang && exact < 0) exact = (int)i;
        if (id == 0x409 && def < 0) def = (int)i;
        if (id == 0 && neut < 0) neut = (int)i;
        if ((id & 0x3FF) == 9 && eng < 0) eng = (int)i;
    }
    if (strict && exact < 0) { SetLastError(1815); return NULL; }   /* ERROR_RESOURCE_LANG_NOT_FOUND */
    int pick = exact >= 0 ? exact : def >= 0 ? def : neut >= 0 ? neut : eng >= 0 ? eng : first;
    if (pick < 0) { SetLastError(1815); return NULL; }
    off = r32(im.b + d.ents + 8 * pick + 4);
    if ((off & 0x80000000u) || !ok(im, (uint64_t)im.root + off, 16)) { SetLastError(E_BAD_EXE); return NULL; }
    return im.b + im.root + off;
}
bool entry_ptr(const Img &im, const void *h) { const uint8_t *p = (const uint8_t *)h; return p >= im.b && (size_t)(p - im.b) <= im.n && ok(im, (size_t)(p - im.b), 16); }
bool entry_data(const Img &im, const uint8_t *e, const uint8_t **d, size_t *n)
{
    uint32_t rva = r32(e), sz = r32(e + 4);
    if (!ok(im, rva, sz)) return fail(E_BAD_EXE);
    *d = im.b + rva; *n = sz;
    return true;
}

/* ---- PNG (ISO/IEC 15948) -> 0xAARRGGBB. Rule: signature, then chunks each CRC-32 checked; IHDR first (13 bytes, the valid
 * colour type / bit depth pairs only, compression 0, filter 0, interlace 0/1), PLTE (3n bytes, n <= 2^depth for type 3,
 * forbidden for types 0/4) and tRNS (type 0: one grey value, 2: one RGB triple, 3: <= n alphas, 4/6: forbidden) before the
 * IDAT run, IDAT chunks consecutive, IEND last; an unknown critical chunk (first letter upper case) is invalid, unknown
 * ancillary chunks and gAMA/sRGB are skipped (no gamma correction, as Windows). The zlib stream must inflate to exactly
 * the filtered scanlines of every Adam7 pass (or the one pass); filters 0-4 per scanline; samples below 8 bits scale to
 * 0..255, 16-bit samples keep the high byte; colour key and palette alpha give the alpha. Any violation: ERROR_INVALID_DATA. */
const struct CrcT { uint32_t t[256]; CrcT() { for (uint32_t i = 0; i < 256; i++) { uint32_t c = i; for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ c >> 1 : c >> 1; t[i] = c; } } } g_crc;
uint32_t crc32(const uint8_t *p, size_t n) { uint32_t c = 0xFFFFFFFFu; while (n--) c = g_crc.t[(c ^ *p++) & 255] ^ c >> 8; return ~c; }
inline uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
inline unsigned smp(const uint8_t *row, unsigned bits, size_t i)
{
    if (bits == 16) return row[2 * i] << 8 | row[2 * i + 1];
    if (bits == 8) return row[i];
    return row[i * bits / 8] >> (8 - bits - i * bits % 8) & ((1u << bits) - 1);
}
bool png_decode(const uint8_t *d, size_t n, Dib *o)
{
    static const uint8_t X0[7] = { 0, 4, 0, 2, 0, 1, 0 }, Y0[7] = { 0, 0, 4, 0, 2, 0, 1 }, DX[7] = { 8, 8, 4, 4, 2, 2, 1 }, DY[7] = { 8, 8, 8, 4, 4, 2, 2 };
    static const uint8_t CH[7] = { 1, 0, 3, 1, 2, 0, 4 };
    uint32_t w = 0, h = 0; unsigned bd = 0, ct = 0, il = 0, np = 0, nt = 0, ids = 0, key[3] = { 0, 0, 0 };
    uint8_t pal[768], trns[256]; bool good = n >= 20, ihdr = false, havep = false, havet = false, iend = false;
    uint8_t *z = NULL, *raw = NULL, *prev = NULL; size_t zn = 0, rn = 0, p = 8;
    uint32_t *px = NULL;
    while (good && !iend && p + 12 <= n) {
        uint32_t len = be32(d + p); const uint8_t *t = d + p + 4, *c = t + 4;
        good = len <= n - p - 12 && be32(c + len) == crc32(t, 4 + len);
        for (int i = 0; i < 4; i++) good = good && ((t[i] | 32) >= 'a' && (t[i] | 32) <= 'z');
        p += 12 + (size_t)len;
        if (!good) break;
        if (!ihdr) {
            good = !memcmp(t, "IHDR", 4) && len == 13; if (!good) break;
            w = be32(c); h = be32(c + 4); bd = c[8]; ct = c[9]; il = c[12];
            good = w && h && w <= 0x7FFFFFFFu && h <= 0x7FFFFFFFu && (uint64_t)w * h <= (1u << 26) && c[10] == 0 && c[11] == 0 && il <= 1 && ct <= 6 && CH[ct] &&
                   (bd == 1 || bd == 2 || bd == 4 || bd == 8 || bd == 16) && (ct == 3 ? bd <= 8 : ct == 0 || bd >= 8);
            ihdr = true;
        } else if (!memcmp(t, "IHDR", 4)) good = false;
        else if (!memcmp(t, "PLTE", 4)) {
            np = len / 3;
            good = !ids && !havep && ct != 0 && ct != 4 && len % 3 == 0 && np >= 1 && np <= (ct == 3 ? 1u << bd : 256u);
            if (good) { memcpy(pal, c, len); havep = true; }
        } else if (!memcmp(t, "tRNS", 4)) {
            good = !ids && !havet && (ct == 0 ? len == 2 : ct == 2 ? len == 6 : ct == 3 && havep && len <= np);
            if (good) {
                havet = true; nt = ct == 3 ? len : 0;
                if (ct == 3) memcpy(trns, c, len);
                else for (unsigned i = 0; i < len / 2; i++) key[i] = (c[2 * i] << 8 | c[2 * i + 1]) & (bd < 16 ? (1u << bd) - 1 : 0xFFFFu);
            }
        } else if (!memcmp(t, "IDAT", 4)) {
            good = ids != 2 && (ct != 3 || havep);
            uint8_t *nz = good ? (uint8_t *)realloc(z, zn + len + 1) : NULL;
            if (!nz) { good = false; break; }
            z = nz; memcpy(z + zn, c, len); zn += len; ids = 1;
            continue;
        } else if (!memcmp(t, "IEND", 4)) { good = len == 0 && ids; iend = true; }
        else good = t[0] & 32;   /* unknown critical chunk */
        if (ids == 1) ids = 2;
    }
    good = good && iend;
    unsigned bits = bd * CH[ct], bpp = (bits + 7) / 8;
    size_t need = 0;
    for (int ps = 0; good && ps < (il ? 7 : 1); ps++) {
        uint64_t pw = il ? (w + DX[ps] - 1 - X0[ps]) / DX[ps] : w, ph = il ? (h + DY[ps] - 1 - Y0[ps]) / DY[ps] : h;
        if (il && (w <= X0[ps] || h <= Y0[ps])) pw = ph = 0;
        if (pw && ph) need += (size_t)ph * ((pw * bits + 7) / 8 + 1);
    }
    if (good && !(fnvnx_inflate_zlib(z, zn, &raw, &rn) == 0 && rn == need)) good = false;
    px = good ? (uint32_t *)calloc((size_t)w * h, 4) : NULL;
    prev = good ? (uint8_t *)calloc(8 * (size_t)w + 8, 1) : NULL;
    if (!px || !prev) good = false;
    size_t pos = 0;
    for (int ps = 0; good && ps < (il ? 7 : 1); ps++) {
        unsigned x0 = il ? X0[ps] : 0, y0 = il ? Y0[ps] : 0, dx = il ? DX[ps] : 1, dy = il ? DY[ps] : 1;
        if (x0 >= w || y0 >= h) continue;
        unsigned pw = (w - x0 + dx - 1) / dx, ph = (h - y0 + dy - 1) / dy;
        size_t rb = ((size_t)pw * bits + 7) / 8;
        memset(prev, 0, rb);
        for (unsigned y = 0; good && y < ph; y++) {
            uint8_t *row = raw + pos + 1; unsigned ft = raw[pos];
            pos += rb + 1;
            for (size_t i = 0; i < rb; i++) {
                int a = i >= bpp ? row[i - bpp] : 0, b = prev[i], cc = i >= bpp ? prev[i - bpp] : 0, pr = 0;
                if (ft == 1) pr = a; else if (ft == 2) pr = b; else if (ft == 3) pr = (a + b) / 2;
                else if (ft == 4) { int q = a + b - cc, pa = abs(q - a), pb = abs(q - b), pc = abs(q - cc); pr = pa <= pb && pa <= pc ? a : pb <= pc ? b : cc; }
                else if (ft != 0) { good = false; break; }
                row[i] = (uint8_t)(row[i] + pr);
            }
            for (unsigned i = 0; good && i < pw; i++) {
                unsigned s[4] = { 0, 0, 0, 0 }, mx = (1u << (bd < 16 ? bd : 8)) - 1, r8, g8, b8, a8 = 255, k = CH[ct];
                for (unsigned c2 = 0; c2 < k; c2++) s[c2] = smp(row, bd, (size_t)i * k + c2);
                auto sc = [&](unsigned v) { return bd == 16 ? v >> 8 : v * 255 / mx; };
                if (ct == 3) { if (s[0] >= np) { good = false; break; } r8 = pal[3 * s[0]]; g8 = pal[3 * s[0] + 1]; b8 = pal[3 * s[0] + 2]; a8 = s[0] < nt ? trns[s[0]] : 255; }
                else if (ct == 0 || ct == 4) { r8 = g8 = b8 = sc(s[0]); if (ct == 4) a8 = sc(s[1]); else if (havet && s[0] == key[0]) a8 = 0; }
                else { r8 = sc(s[0]); g8 = sc(s[1]); b8 = sc(s[2]); if (ct == 6) a8 = sc(s[3]); else if (havet && s[0] == key[0] && s[1] == key[1] && s[2] == key[2]) a8 = 0; }
                px[(size_t)(y0 + y * dy) * w + x0 + i * dx] = a8 << 24 | r8 << 16 | g8 << 8 | b8;
            }
            if (good) memcpy(prev, row, rb);
        }
    }
    free(z); free(raw); free(prev);
    if (!good) { free(px); return fail(E_INVALID_DATA); }
    o->px = px; o->w = (int)w; o->h = (int)h;
    return true;
}

/* ---- DIB decoding to 0xAARRGGBB. doubled: icon/cursor (XOR + AND mask, height doubled).
 * Rule. Header sizes 12 (BITMAPCOREHEADER: 16-bit fields, RGBTRIPLE palette of 2^bpp entries), 40, 52, 56, 108, 124
 * (colour-space fields ignored), others invalid. bpp 1/4/8/24 (+16/32 with an info header). Compression: BI_RGB; BI_RLE8
 * (8 bpp) and BI_RLE4 (4 bpp), bottom-up only; BI_BITFIELDS (16/32 bpp; 3 masks after a 40-byte header, else inside the
 * header, alpha mask only with 56+): each mask one contiguous run of bits, a component of n bits expands to 8 by bit
 * replication; BI_JPEG/BI_PNG and everything else: invalid. BI_RGB 16 bpp is 5-5-5. biClrUsed (<= 2^bpp) sets the palette
 * size (entries beyond it read black), biClrImportant is a hint and ignored. Alpha: a mask/32 bpp channel that is non-zero
 * somewhere, else (icons) the AND mask, else opaque; a bitmap keeps only an alpha mask's channel (BI_RGB 32: dropped). */
uint32_t sys_color(uint32_t rgb)   /* LR_LOADMAP3DCOLORS: the layer's fixed Windows 7 Aero Basic scheme */
{
    return rgb == 0x808080 ? 0xA0A0A0 : rgb == 0xC0C0C0 ? 0xF0F0F0 : rgb == 0xDFDFDF ? 0xE3E3E3 : rgb;
}
unsigned expand8(uint32_t v, uint32_t m)   /* the bits of v under the contiguous mask m, as 8 bits */
{
    if (!m) return 0;
    int sh = __builtin_ctz(m), nb = __builtin_popcount(m), s;
    unsigned r = 0, c = (v & m) >> sh;
    for (s = 8 - nb; s > -nb; s -= nb) r |= s >= 0 ? c << s : c >> -s;
    return r & 255;
}
bool dib_decode(const uint8_t *d, size_t n, bool doubled, bool cursor, uint32_t flags, Dib *o)
{
    o->inv = NULL;
    if (n >= 8 && r32(d) == 0x474E5089u && r32(d + 4) == 0x0A1A0A0Du) return doubled ? png_decode(d, n, o) : fail(E_INVALID_DATA);
    if (n < 12) return fail(E_INVALID_DATA);
    uint32_t hs = r32(d), mk[4] = { 0, 0, 0, 0 };
    bool core = hs == 12, top = false;
    int32_t w, h;
    unsigned bpp, comp = 0, used = 0;
    if (core) { w = r16(d + 4); h = r16(d + 6); bpp = r16(d + 10); }
    else if (hs == 40 || hs == 52 || hs == 56 || hs == 108 || hs == 124) {
        if (hs > n) return fail(E_INVALID_DATA);
        w = (int32_t)r32(d + 4); h = (int32_t)r32(d + 8); bpp = r16(d + 14); comp = r32(d + 16); used = r32(d + 32);
        top = h < 0; h = top ? -h : h;
    } else return fail(E_INVALID_DATA);
    if (doubled) h /= 2;
    if (w <= 0 || h <= 0 || w > 8192 || h > 8192) return fail(E_INVALID_DATA);
    if (!(bpp == 1 || bpp == 4 || bpp == 8 || bpp == 24 || (!core && (bpp == 16 || bpp == 32))) || comp > 3 || (comp == 1 && (bpp != 8 || top)) ||
        (comp == 2 && (bpp != 4 || top)) || (comp == 3 && bpp != 16 && bpp != 32))
        return fail(E_INVALID_DATA);
    size_t ds = hs;
    if (comp == 3) {
        if (hs >= 52) { for (int i = 0; i < 3; i++) mk[i] = r32(d + 40 + 4 * i); if (hs >= 56) mk[3] = r32(d + 52); }
        else { if (ds + 12 > n) return fail(E_INVALID_DATA); for (int i = 0; i < 3; i++) mk[i] = r32(d + 40 + 4 * i); ds += 12; }
        for (uint32_t m : mk) { uint32_t q = m ? m >> __builtin_ctz(m) : 0; if ((q & (q + 1)) || (bpp == 16 && m >> 16)) return fail(E_INVALID_DATA); }   /* one run of bits */
    } else if (bpp == 16) { mk[0] = 0x7C00; mk[1] = 0x3E0; mk[2] = 0x1F; }
    else if (bpp == 32) { mk[0] = 0xFF0000; mk[1] = 0xFF00; mk[2] = 0xFF; if (doubled) mk[3] = 0xFF000000u; }
    unsigned np = 0;
    if (bpp <= 8) { if (used > 1u << bpp) return fail(E_INVALID_DATA); np = core || !used ? 1u << bpp : used; }
    else if (!core) np = used;
    ds += (core ? 3 : 4) * (size_t)np;
    if (ds > n) return fail(E_INVALID_DATA);
    uint32_t pal[256];
    for (unsigned i = 0; i < 256; i++) pal[i] = 0xFF000000u;
    for (unsigned i = 0; i < np && i < 256 && bpp <= 8; i++) {
        const uint8_t *q = d + hs + (core ? 3 : 4) * i;
        pal[i] = 0xFF000000u | q[2] << 16 | q[1] << 8 | q[0];
    }
    size_t stride = ((size_t)w * bpp + 31) / 32 * 4;
    uint32_t *px = (uint32_t *)calloc((size_t)w * h, 4);
    uint8_t *idx = (uint8_t *)calloc((size_t)w * h, 1);
    bool good = px && idx, achan = mk[3] != 0;
    if (good && comp != 1 && comp != 2) {
        good = ds + stride * h <= n;
        for (int r = 0; good && r < h; r++) {
            const uint8_t *row = d + ds + stride * r;
            int y = top ? r : h - 1 - r;
            for (int x = 0; x < w; x++) {
                if (bpp == 1) idx[y * w + x] = row[x >> 3] >> (7 - (x & 7)) & 1;
                else if (bpp == 4) idx[y * w + x] = (x & 1) ? row[x >> 1] & 15 : row[x >> 1] >> 4;
                else if (bpp == 8) idx[y * w + x] = row[x];
                else if (bpp == 24) px[y * w + x] = 0xFF000000u | row[3 * x + 2] << 16 | row[3 * x + 1] << 8 | row[3 * x];
                else {
                    uint32_t v = bpp == 16 ? r16(row + 2 * x) : r32(row + 4 * x);
                    px[y * w + x] = (achan ? expand8(v, mk[3]) : 255u) << 24 | expand8(v, mk[0]) << 16 | expand8(v, mk[1]) << 8 | expand8(v, mk[2]);
                }
            }
        }
    } else if (good) {   /* RLE: rows from the bottom; encoded mode, absolute mode, escapes EOL / EOB / delta; out-of-image pixels are clipped */
        size_t p = ds; int x = 0, r = 0;
        auto put = [&](unsigned v) { if (x < w && r < h) idx[(h - 1 - r) * w + x] = (uint8_t)v; x++; };
        while (p + 2 <= n) {
            unsigned c = d[p], v = d[p + 1]; p += 2;
            if (c) { for (unsigned i = 0; i < c; i++) put(comp == 1 ? v : (i & 1) ? v & 15 : v >> 4); }
            else if (v == 0) { x = 0; r++; }
            else if (v == 1) break;
            else if (v == 2) { if (p + 2 > n) { good = false; break; } x += d[p]; r += d[p + 1]; p += 2; }
            else {
                size_t bytes = comp == 1 ? v : (v + 1) / 2;
                if (p + bytes > n) { good = false; break; }
                for (unsigned i = 0; i < v; i++) put(comp == 1 ? d[p + i] : (i & 1) ? d[p + i / 2] & 15 : d[p + i / 2] >> 4);
                p += bytes + (bytes & 1);
            }
        }
    }
    if (good && bpp <= 8) {
        if (flags & 0x20) pal[idx[0]] = 0xFFFFFFFFu;   /* LR_LOADTRANSPARENT: top-left colour becomes COLOR_WINDOW */
        for (unsigned i = 0; i < 256; i++) if (flags & 0x1000) pal[i] = 0xFF000000u | sys_color(pal[i] & 0xFFFFFF);   /* LR_LOADMAP3DCOLORS */
        for (size_t i = 0; i < (size_t)w * h; i++) px[i] = pal[idx[i]];
    }
    if (good && doubled) {
        size_t as = ((size_t)w + 31) / 32 * 4, ao = ds + stride * h;
        bool alpha = false;
        if (achan) for (size_t i = 0; i < (size_t)w * h; i++) alpha |= (px[i] >> 24) != 0;
        if (!alpha) {
            if (ao + as * h > n) good = false;
            if (good && cursor && bpp == 1) good = (o->inv = (uint8_t *)calloc((size_t)w * h, 1)) != NULL;
            for (int y = 0; good && y < h; y++)
                for (int x = 0; x < w; x++) {
                    uint32_t &q = px[y * w + x];
                    q |= 0xFF000000u;
                    if (!(d[ao + as * (top ? y : h - 1 - y) + (x >> 3)] >> (7 - (x & 7)) & 1)) continue;
                    q = 0;
                    if (o->inv && idx[y * w + x]) o->inv[y * w + x] = 1;   /* AND=1, XOR=1: invert the screen */
                }
        }
    }
    free(idx);
    if (!good) { free(px); free(o->inv); o->inv = NULL; SetLastError(E_INVALID_DATA); return false; }
    o->px = px; o->w = w; o->h = h;
    return true;
}

/* LookupIconIdFromDirectoryEx: closest size, then the most colours (<= 32 bpp). The directory is a resource group (14-byte
 * entries ending in the RT_ICON/RT_CURSOR id; a cursor group has 16-bit width and doubled height) or an .ico/.cur file
 * (16-byte entries ending in the image offset; sizes in bytes, 0 = 256). Returns the chosen entry. */
const uint8_t *pick_image(const uint8_t *g, size_t n, int cur, bool file, int cx, int cy)
{
    size_t es = file ? 16 : 14;
    bool wide = cur && !file;
    if (n < 6 || r16(g + 2) != (unsigned)(cur ? 2 : 1) || 6 + es * r16(g + 4) > n || !r16(g + 4)) return fail(E_INVALID_DATA), (const uint8_t *)NULL;
    int best = -1, bd = 0, bb = 0;
    for (unsigned i = 0; i < r16(g + 4); i++) {
        const uint8_t *e = g + 6 + es * i;
        int w = wide ? (int)r16(e) : e[0] ? e[0] : 256, h = wide ? (int)r16(e + 2) / 2 : e[1] ? e[1] : 256;
        int bits = (int)r16(e + 6), dist = abs(w - cx) + abs(h - cy);
        if (file && cur) {   /* the entry holds the hotspot: the colour depth is in the image's own header */
            uint32_t off = r32(e + 12);
            bits = off <= n && n - off >= 16 ? (r32(g + off) == 12 ? (int)r16(g + off + 10) : r32(g + off) == 0x474E5089u ? 32 : (int)r16(g + off + 14)) : 0;
        } else if (!cur && !bits) bits = e[2] == 0 ? 8 : e[2] <= 2 ? 1 : e[2] <= 16 ? 4 : 8;
        if (bits > 32) continue;
        if (best < 0 || dist < bd || (dist == bd && bits > bb)) { best = (int)i; bd = dist; bb = bits; }
    }
    if (best < 0) return fail(E_INVALID_DATA), (const uint8_t *)NULL;
    return g + 6 + es * best;
}
bool load_icon_dib(const Img &im, const uint8_t *gent, int cur, int cx, int cy, Dib *o, int *hx, int *hy)
{
    const uint8_t *g, *d, *pe; size_t gn, dn;
    if (!entry_data(im, gent, &g, &gn) || !(pe = pick_image(g, gn, cur, false, cx, cy))) return false;
    const uint8_t *e = find(im, idkey(cur ? 1 : 3), idkey(r16(pe + 12)), 0);
    if (!e || !entry_data(im, e, &d, &dn)) return false;
    *hx = *hy = 0;
    if (cur) { if (dn < 4) return fail(E_INVALID_DATA); *hx = (int)r16(d); *hy = (int)r16(d + 2); d += 4; dn -= 4; }
    if (!dib_decode(d, dn, true, cur != 0, 0, o)) return false;
    if (!cur) { *hx = o->w / 2; *hy = o->h / 2; }
    return true;
}
/* an .ico/.cur file image: same selection, the cursor hotspot from the directory entry */
bool load_icon_file(const uint8_t *f, size_t n, int cur, int cx, Dib *o)
{
    const uint8_t *e = pick_image(f, n, cur, true, cx, cx);
    if (!e) return false;
    uint32_t sz = r32(e + 8), off = r32(e + 12);
    return (off <= n && sz <= n - off) ? dib_decode(f + off, sz, true, cur != 0, 0, o) : fail(E_INVALID_DATA);
}
}   // namespace

/* ---- kernel32 */
/* The loader calls this when it maps an image (base == NULL: unmapped); module.cpp keeps no image base/size. */
extern "C" void fnvnx_resource_register_image(HMODULE m, const void *base, size_t size)
{
    Lk l;
    for (Icon *i = g_icons; i; i = i->next) if (i->m == m) i->ent = NULL;   /* cached handles of the replaced image */
    TI *slot = NULL;
    for (TI *t = g_ti; t && !slot; t = t->next) if (t->m == m) slot = t;
    if (!slot && base && (slot = (TI *)calloc(1, sizeof *slot))) { slot->next = g_ti; g_ti = slot; slot->m = m; }
    if (slot) { slot->b = (const uint8_t *)base; slot->n = size; }
}
extern "C" void fnvnx_resource_set_image_for_test(HMODULE m, const void *base, size_t size) { fnvnx_resource_register_image(m, base, size); }
extern "C" void *FindResourceExA(HMODULE m, LPCSTR type, LPCSTR name, WORD lang)
{
    Img im;
    if (!open_image(m, &im)) return NULL;
    return (void *)find(im, KeyA(type).k, KeyA(name).k, lang & 0xFFFF);
}
/* FindResourceEx without the language fallback: the entry of exactly this language (message-table lookup, whose
 * language is specific; lang 0 keeps the ordinary neutral/default order) */
extern "C" void *fnvnx_resource_find_exact(HMODULE m, LPCSTR type, LPCSTR name, WORD lang)
{
    Img im;
    if (!open_image(m, &im)) return NULL;
    return (void *)find(im, KeyA(type).k, KeyA(name).k, lang & 0xFFFF, (lang & 0xFFFF) != 0);
}
extern "C" void *FindResourceA(HMODULE m, LPCSTR name, LPCSTR type) { return FindResourceExA(m, type, name, 0); }
extern "C" void *FindResourceExW(HMODULE m, LPCWSTR type, LPCWSTR name, WORD lang)
{
    Img im;
    if (!open_image(m, &im)) return NULL;
    return (void *)find(im, mkkeyw(type), mkkeyw(name), lang & 0xFFFF);
}
extern "C" void *FindResourceW(HMODULE m, LPCWSTR name, LPCWSTR type) { return FindResourceExW(m, type, name, 0); }
extern "C" void *LoadResource(HMODULE m, void *r)
{
    Img im; const uint8_t *d; size_t n;
    if (!r) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (!open_image(m, &im)) return NULL;
    if (!entry_ptr(im, r)) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    return entry_data(im, (const uint8_t *)r, &d, &n) ? (void *)d : NULL;
}
extern "C" void *LockResource(void *h) { return h; }
extern "C" DWORD SizeofResource(HMODULE m, void *r)
{
    Img im; const uint8_t *d; size_t n;
    if (!r || !open_image(m, &im)) return 0;
    if (!entry_ptr(im, r)) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    return entry_data(im, (const uint8_t *)r, &d, &n) ? (DWORD)n : 0;
}
extern "C" BOOL EnumResourceNamesA(HMODULE m, LPCSTR type, ENUMRESNAMEPROCA cb, LONG_PTR param)
{
    Img im; Dir d; uint32_t off;
    if (!cb || !open_image(m, &im) || !open_dir(im, 0, &d)) return FALSE;
    int r = lookup(im, d, KeyA(type).k, &off);
    if (r <= 0) { if (!r) SetLastError(1813); return FALSE; }
    if (!(off & 0x80000000u) || !open_dir(im, off & 0x7FFFFFFFu, &d)) { SetLastError(E_BAD_EXE); return FALSE; }
    for (int pass = 0; pass < 2; pass++)   /* the directory lists named entries first */
        for (unsigned i = 0; i < d.n; i++) {
            uint32_t nm = r32(im.b + d.ents + 8 * i);
            char buf[256], *name = (char *)(uintptr_t)(nm & 0xFFFF);
            if (!!(nm & 0x80000000u) != (pass == 0)) continue;
            if (pass == 0) {
                uint64_t a = (uint64_t)im.root + (nm & 0x7FFFFFFFu);
                if (!ok(im, a, 2) || !ok(im, a + 2, 2ull * r16(im.b + a))) { SetLastError(E_BAD_EXE); return FALSE; }
                size_t len = r16(im.b + a) < 255 ? r16(im.b + a) : 255;
                for (size_t j = 0; j < len; j++) buf[j] = r16(im.b + a + 2 + 2 * j) < 128 ? (char)r16(im.b + a + 2 + 2 * j) : '?';
                buf[len] = 0; name = buf;
            }
            if (!cb(m, type, name, param)) return FALSE;
        }
    return TRUE;
}

/* ---- user32 loaders (msgqueue.cpp: LoadIconA / LoadCursorA with a module) */
extern "C" HANDLE fnvnx_load_icon_resource(HINSTANCE hi, LPCSTR name, int is_cursor)
{
    Img im; Dib dib = { NULL, 0, 0, NULL }; int hx, hy;
    HMODULE m = hi ? (HMODULE)hi : EXE_MODULE;
    if (!open_image(m, &im)) return NULL;
    const uint8_t *g = find(im, idkey(is_cursor ? 12 : 14), KeyA(name).k, 0);
    if (!g) return NULL;
    { Lk l; for (Icon *i = g_icons; i; i = i->next) if (i->m == m && i->ent == g && i->cur == is_cursor) return i; }
    int sz = GetSystemMetrics(is_cursor ? 13 /* SM_CXCURSOR */ : 11 /* SM_CXICON */), sy = GetSystemMetrics(is_cursor ? 14 : 12);
    if (!load_icon_dib(im, g, is_cursor, sz, sy, &dib, &hx, &hy)) return NULL;
    Icon *ic = (Icon *)calloc(1, sizeof *ic);
    if (!ic) { free(dib.px); free(dib.inv); SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
    ic->m = m; ic->ent = g; ic->cur = is_cursor; ic->w = dib.w; ic->h = dib.h; ic->hx = hx; ic->hy = hy; ic->px = dib.px; ic->inv = dib.inv;
    Lk l;
    ic->next = g_icons; g_icons = ic;
    return ic;
}
extern "C" int fnvnx_icon_pixels(HANDLE h, const uint32_t **bgra, int *w, int *hh, int *hx, int *hy)
{
    Lk l;
    for (Icon *i = g_icons; i; i = i->next)
        if (i == h) { *bgra = i->px; *w = i->w; *hh = i->h; *hx = i->hx; *hy = i->hy; return 1; }
    return 0;
}

/* the per-pixel screen-invert mask (w*h bytes, 1 = invert) of a mono cursor: *mask is NULL when no pixel inverts; 0 = no such icon */
extern "C" int fnvnx_icon_invert_mask(HANDLE h, const uint8_t **mask)
{
    Lk l;
    for (Icon *i = g_icons; i; i = i->next) if (i == h) { *mask = i->inv; return 1; }
    return 0;
}

/* ---- comctl32 */
extern "C" BOOL InitCommonControlsEx(const void *icc)
{
    if (!icc || r32((const uint8_t *)icc) != 8) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    Lk l;
    g_icc |= r32((const uint8_t *)icc + 4);   /* the ICC_* classes asked for; the window layer owns the classes */
    return TRUE;
}
extern "C" uint32_t fnvnx_common_controls_requested(void) { Lk l; return g_icc; }

static bool read_file(LPCSTR path, uint8_t **buf, size_t *n)
{
    HANDLE f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return false;
    DWORD sz = GetFileSize(f, NULL), got = 0;
    bool good = sz != 0xFFFFFFFFu && sz < (64u << 20) && (*buf = (uint8_t *)malloc(sz ? sz : 1)) && ReadFile(f, *buf, sz, &got, NULL) && got == sz;
    CloseHandle(f);
    if (good) *n = sz; else { free(*buf); *buf = NULL; if (!GetLastError()) SetLastError(ERROR_NOT_ENOUGH_MEMORY); }
    return good;
}
extern "C" void *ImageList_LoadImageA(HINSTANCE hi, LPCSTR name, int cx, int grow, DWORD crMask, UINT type, UINT flags)
{
    Dib d = { NULL, 0, 0, NULL };
    uint32_t mask = W32_DW(crMask), fl = flags;
    uint8_t *file = NULL;
    Img im;
    W32_UNUSED(grow);
    if (cx <= 0 || type > 2) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (fl & 0x10) {
        size_t n;
        if (!read_file(name, &file, &n)) return NULL;
        bool g;
        if (type != 0) g = load_icon_file(file, n, type == 2, cx, &d), cx = d.w, mask = 0xFFFFFFFFu;   /* an icon carries its own mask */
        else g = n >= 14 + 12 && file[0] == 'B' && file[1] == 'M' ? dib_decode(file + 14, n - 14, false, false, fl, &d) : fail(E_INVALID_DATA);
        free(file);
        if (!g) return NULL;
    } else {
        if (!open_image((HMODULE)hi, &im)) return NULL;
        const uint8_t *src, *e;
        size_t n;
        int hx, hy;
        if (type == 0) {
            if (!(e = find(im, idkey(2), KeyA(name).k, 0)) || !entry_data(im, e, &src, &n) || !dib_decode(src, n, false, false, fl, &d)) return NULL;
        } else {
            if (!(e = find(im, idkey(type == 1 ? 14 : 12), KeyA(name).k, 0)) || !load_icon_dib(im, e, type == 2, cx, cx, &d, &hx, &hy)) return NULL;
            cx = d.w; mask = 0xFFFFFFFFu;   /* an icon carries its own mask */
        }
    }
    int count = d.w / cx;
    IList *l = count ? (IList *)calloc(1, sizeof *l) : NULL;
    if (l) l->px = (uint32_t *)malloc((size_t)count * cx * d.h * 4);
    if (!l || !l->px) { free(l); free(d.px); free(d.inv); SetLastError(count ? ERROR_NOT_ENOUGH_MEMORY : ERROR_INVALID_PARAMETER); return NULL; }
    uint32_t key = mask == 0xFF000000u ? d.px[0] & 0xFFFFFF : (mask & 0xFF) << 16 | (mask & 0xFF00) | (mask >> 16 & 0xFF);   /* CLR_DEFAULT: top-left */
    for (int i = 0; i < count; i++)
        for (int y = 0; y < d.h; y++)
            for (int x = 0; x < cx; x++) {
                uint32_t p = d.px[y * d.w + i * cx + x];
                l->px[((size_t)i * d.h + y) * cx + x] = (mask != 0xFFFFFFFFu && (p >> 24) && (p & 0xFFFFFF) == key) ? 0 : p;
            }
    free(d.px); free(d.inv);
    l->count = count; l->cx = cx; l->cy = d.h;
    Lk lk;
    l->next = g_lists; g_lists = l;
    return l;
}
static IList *il_find(void *h) { for (IList *l = g_lists; l; l = l->next) if (l == h) return l; return NULL; }   /* g_lock held */
extern "C" BOOL ImageList_Destroy(void *h)
{
    Lk lk;
    for (IList **p = &g_lists; *p; p = &(*p)->next)
        if (*p == h) { IList *l = *p; *p = l->next; free(l->px); free(l); return TRUE; }
    return FALSE;
}
extern "C" int ImageList_GetImageCount(void *h) { Lk lk; IList *l = il_find(h); return l ? l->count : 0; }
extern "C" BOOL ImageList_GetIconSize(void *h, int *cx, int *cy)
{
    Lk lk; IList *l = il_find(h);
    if (!l) return FALSE;
    if (cx) *cx = l->cx;
    if (cy) *cy = l->cy;
    return TRUE;
}
extern "C" int fnvnx_imagelist_pixels(void *h, int i, const uint32_t **bgra)
{
    Lk lk; IList *l = il_find(h);
    if (!l || i < 0 || i >= l->count) return 0;
    *bgra = l->px + (size_t)i * l->cx * l->cy;
    return 1;
}
