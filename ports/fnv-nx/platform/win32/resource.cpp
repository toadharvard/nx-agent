/* resource.cpp - PE resources of a module's mapped image (kernel32 FindResourceA/ExA, LoadResource, ..., EnumResourceNamesA),
 * the user32 icon/cursor loaders built on them, and the comctl32 image list loader.
 *
 * Rule. A resource is looked up in the resource directory (data directory 2) of the module's mapped PE32 image, as
 * Windows does: three levels type / name / language, every offset bounds-checked against the image; a malformed
 * directory fails with ERROR_BAD_EXE_FORMAT (193), absence of image or directory with 1812, then 1813 / 1814 / 1815
 * for type / name / language. Names are integers or UTF-16 strings compared case-insensitively; "#n" is the integer n.
 * Language: the requested LANGID, else 0x0409 (the user default), else neutral, else any English, else the first.
 * The image of a module comes from `fnvnx_module_image` (weak: 0 = no image); tests install one with
 * fnvnx_resource_set_image_for_test. HRSRC is the pointer to the IMAGE_RESOURCE_DATA_ENTRY inside the image,
 * HGLOBAL the pointer to the data (the image is mapped: RVA == offset), so FreeResource has nothing to free.
 *
 * Icons/cursors: group directory -> best image (closest size to SM_CXICON/SM_CXCURSOR, then most colours <= 32 bpp)
 * -> decoded 32bpp 0xAARRGGBB pixels (alpha: the 32bpp alpha if any is non-zero, else the AND mask). Mono cursor
 * pixels with AND=1 and XOR=1 (screen invert) are drawn opaque white: the compositor has no invert.
 * Images: 1/4/8/24/32 bpp DIBs, BI_RGB, RLE8, RLE4, bottom-up and top-down. Not supported (error set, NULL):
 * PNG-compressed icons, 16 bpp and BI_BITFIELDS, BITMAPCOREHEADER. */
#include "w32.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define EXE_MODULE ((HMODULE)(uintptr_t)0x00400000u)
#define E_INVALID_DATA 13
#define E_BAD_EXE 193

extern "C" __attribute__((weak)) int fnvnx_module_image(HMODULE, const void **, size_t *) { return 0; }

namespace {
os_lock_t g_lock;
struct TI { HMODULE m; const uint8_t *b; size_t n; } g_ti[8];
struct Icon { Icon *next; HMODULE m; const void *ent; int cur, w, h, hx, hy; uint32_t *px; };
struct IList { IList *next; int count, cx, cy; uint32_t *px; };
Icon *g_icons;
IList *g_lists;
uint32_t g_icc;
struct Lk { Lk() { os_lock(&g_lock); } ~Lk() { os_unlock(&g_lock); } };

struct Img { const uint8_t *b; size_t n; uint32_t root; };
struct Key { int is_id; uint32_t id; const char *s; };
struct Dib { uint32_t *px; int w, h; };

inline unsigned r16(const uint8_t *p) { return p[0] | p[1] << 8; }
inline uint32_t r32(const uint8_t *p) { return r16(p) | (uint32_t)r16(p + 2) << 16; }
inline bool ok(const Img &im, uint64_t off, uint64_t len) { return off <= im.n && len <= im.n - off; }
bool fail(DWORD e) { SetLastError(e); return false; }

bool open_image(HMODULE m, Img *im)
{
    if (!m) m = EXE_MODULE;
    const void *b = NULL; size_t n = 0;
    { Lk l; for (TI &t : g_ti) if (t.b && t.m == m) { b = t.b; n = t.n; } }
    if (!b && (!fnvnx_module_image(m, &b, &n) || !b)) return fail(1812 /* ERROR_RESOURCE_DATA_NOT_FOUND */);
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

Key mkkey(LPCSTR p)
{
    Key k = { 0, 0, p };
    uintptr_t v = (uintptr_t)p;
    if (v <= 0xFFFF) { k.is_id = 1; k.id = (uint32_t)v; }
    else if (p[0] == '#' && p[1] >= '0' && p[1] <= '9') {
        char *e; unsigned long x = strtoul(p + 1, &e, 10);
        if (!*e) { k.is_id = 1; k.id = (uint32_t)x; }
    }
    return k;
}
Key idkey(uint32_t id) { Key k = { 1, id, NULL }; return k; }

struct Dir { uint64_t ents; unsigned n; };
bool open_dir(const Img &im, uint32_t rel, Dir *d)
{
    uint64_t a = (uint64_t)im.root + rel;
    if (!ok(im, a, 16)) return fail(E_BAD_EXE);
    d->n = r16(im.b + a + 12) + r16(im.b + a + 14);
    d->ents = a + 16;
    return ok(im, d->ents, 8ull * d->n) || fail(E_BAD_EXE);
}
/* 1 = entry offset in *off, 0 = no such entry, -1 = malformed */
int lookup(const Img &im, const Dir &d, const Key &k, uint32_t *off)
{
    for (unsigned i = 0; i < d.n; i++) {
        uint32_t nm = r32(im.b + d.ents + 8 * i);
        if (k.is_id) { if (!(nm & 0x80000000u) && nm == k.id) { *off = r32(im.b + d.ents + 8 * i + 4); return 1; } continue; }
        if (!(nm & 0x80000000u)) continue;
        uint64_t a = (uint64_t)im.root + (nm & 0x7FFFFFFFu);
        if (!ok(im, a, 2) || !ok(im, a + 2, 2ull * r16(im.b + a))) { fail(E_BAD_EXE); return -1; }
        size_t len = r16(im.b + a);
        if (strlen(k.s) != len) continue;
        size_t j = 0;
        for (; j < len; j++) {
            unsigned c = r16(im.b + a + 2 + 2 * j), q = (uint8_t)k.s[j];
            if (c >= 'a' && c <= 'z') c -= 32;
            if (q >= 'a' && q <= 'z') q -= 32;
            if (c != q) break;
        }
        if (j == len) { *off = r32(im.b + d.ents + 8 * i + 4); return 1; }
    }
    return 0;
}
/* the IMAGE_RESOURCE_DATA_ENTRY of (type, name, language) or NULL with the error set */
const uint8_t *find(const Img &im, Key type, Key name, unsigned lang)
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

/* ---- DIB decoding to 0xAARRGGBB. doubled: icon/cursor (XOR + AND mask, height doubled) */
uint32_t sys_color(uint32_t rgb)   /* LR_LOADMAP3DCOLORS: the layer's fixed Windows 7 Aero Basic scheme */
{
    return rgb == 0x808080 ? 0xA0A0A0 : rgb == 0xC0C0C0 ? 0xF0F0F0 : rgb == 0xDFDFDF ? 0xE3E3E3 : rgb;
}
bool dib_decode(const uint8_t *d, size_t n, bool doubled, bool cursor, uint32_t flags, Dib *o)
{
    if (n >= 8 && r32(d) == 0x474E5089u) return fail(ERROR_NOT_SUPPORTED);   /* PNG-compressed image */
    if (n < 40 || r32(d) < 40 || r32(d) > n) return fail(r32(d) == 12 ? ERROR_NOT_SUPPORTED : E_INVALID_DATA);
    uint32_t hs = r32(d);
    int32_t w = (int32_t)r32(d + 4), h = (int32_t)r32(d + 8);
    unsigned bpp = r16(d + 14), comp = r32(d + 16), used = r32(d + 32);
    bool top = h < 0;
    h = top ? -h : h;
    if (doubled) h /= 2;
    if (w <= 0 || h <= 0 || w > 8192 || h > 8192) return fail(E_INVALID_DATA);
    if ((bpp != 1 && bpp != 4 && bpp != 8 && bpp != 24 && bpp != 32) || comp > 2 || (comp == 1 && bpp != 8) || (comp == 2 && bpp != 4))
        return fail(ERROR_NOT_SUPPORTED);
    unsigned np = bpp <= 8 ? (used && used < (1u << bpp) ? used : 1u << bpp) : 0;
    if (hs + 4ull * np > n) return fail(E_INVALID_DATA);
    uint32_t pal[256];
    for (unsigned i = 0; i < 256; i++) pal[i] = 0xFF000000u;
    for (unsigned i = 0; i < np; i++) { const uint8_t *q = d + hs + 4 * i; pal[i] = 0xFF000000u | q[2] << 16 | q[1] << 8 | q[0]; }
    size_t ds = hs + 4 * (size_t)np, stride = ((size_t)w * bpp + 31) / 32 * 4;
    uint32_t *px = (uint32_t *)calloc((size_t)w * h, 4);
    uint8_t *idx = (uint8_t *)calloc((size_t)w * h, 1);
    bool good = px && idx;
    if (good && comp == 0) {
        good = ds + stride * h <= n;
        for (int r = 0; good && r < h; r++) {
            const uint8_t *row = d + ds + stride * r;
            int y = top ? r : h - 1 - r;
            for (int x = 0; x < w; x++) {
                if (bpp == 1) idx[y * w + x] = row[x >> 3] >> (7 - (x & 7)) & 1;
                else if (bpp == 4) idx[y * w + x] = (x & 1) ? row[x >> 1] & 15 : row[x >> 1] >> 4;
                else if (bpp == 8) idx[y * w + x] = row[x];
                else px[y * w + x] = bpp == 24 ? 0xFF000000u | row[3 * x + 2] << 16 | row[3 * x + 1] << 8 | row[3 * x] : r32(row + 4 * x);
            }
        }
    } else if (good) {   /* RLE: rows from the bottom; encoded mode, absolute mode, escapes EOL / EOB / delta */
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
    free(idx);
    if (good && doubled) {
        size_t as = ((size_t)w + 31) / 32 * 4, ao = ds + stride * h;
        bool alpha = false;
        if (bpp == 32) for (size_t i = 0; i < (size_t)w * h; i++) alpha |= (px[i] >> 24) != 0;
        else if (ao + as * h > n) good = false;
        for (int y = 0; good && y < h && !alpha; y++)
            for (int x = 0; x < w; x++) {
                if (!(d[ao + as * (h - 1 - y) + (x >> 3)] >> (7 - (x & 7)) & 1)) continue;
                uint32_t &q = px[y * w + x];
                q = (cursor && bpp == 1 && (q & 0xFFFFFF)) ? 0xFFFFFFFFu : 0;
            }
    } else if (good && bpp == 32)
        for (size_t i = 0; i < (size_t)w * h; i++) px[i] |= 0xFF000000u;
    if (!good) { free(px); SetLastError(E_INVALID_DATA); return false; }
    o->px = px; o->w = w; o->h = h;
    return true;
}

/* LookupIconIdFromDirectoryEx: closest size, then the most colours (<= 32 bpp) */
bool pick_image(const uint8_t *g, size_t n, int cur, int cx, int cy, uint32_t *id)
{
    if (n < 6 || r16(g + 2) != (unsigned)(cur ? 2 : 1) || 6 + 14ull * r16(g + 4) > n || !r16(g + 4)) return fail(E_INVALID_DATA);
    int best = -1, bd = 0, bb = 0;
    for (unsigned i = 0; i < r16(g + 4); i++) {
        const uint8_t *e = g + 6 + 14 * i;
        int w = cur ? (int)r16(e) : e[0] ? e[0] : 256, h = cur ? (int)r16(e + 2) / 2 : e[1] ? e[1] : 256;
        int bits = (int)r16(e + 6), dist = abs(w - cx) + abs(h - cy);
        if (!cur && !bits) bits = e[2] == 0 ? 8 : e[2] <= 2 ? 1 : e[2] <= 16 ? 4 : 8;
        if (bits > 32) continue;
        if (best < 0 || dist < bd || (dist == bd && bits > bb)) { best = (int)i; bd = dist; bb = bits; }
    }
    if (best < 0) return fail(E_INVALID_DATA);
    *id = r16(g + 6 + 14 * best + 12);
    return true;
}
bool load_icon_dib(const Img &im, const uint8_t *gent, int cur, int cx, int cy, Dib *o, int *hx, int *hy)
{
    const uint8_t *g, *d; size_t gn, dn; uint32_t id;
    if (!entry_data(im, gent, &g, &gn) || !pick_image(g, gn, cur, cx, cy, &id)) return false;
    const uint8_t *e = find(im, idkey(cur ? 1 : 3), idkey(id), 0);
    if (!e || !entry_data(im, e, &d, &dn)) return false;
    *hx = *hy = 0;
    if (cur) { if (dn < 4) return fail(E_INVALID_DATA); *hx = (int)r16(d); *hy = (int)r16(d + 2); d += 4; dn -= 4; }
    if (!dib_decode(d, dn, true, cur != 0, 0, o)) return false;
    if (!cur) { *hx = o->w / 2; *hy = o->h / 2; }
    return true;
}
}   // namespace

/* ---- kernel32 */
extern "C" void fnvnx_resource_set_image_for_test(HMODULE m, const void *base, size_t size)
{
    Lk l;
    for (Icon *i = g_icons; i; i = i->next) if (i->m == m) i->ent = NULL;   /* cached handles of the replaced image */
    TI *slot = NULL;
    for (TI &t : g_ti) if (t.b && t.m == m) slot = &t;
    if (!slot) for (TI &t : g_ti) if (!t.b) { slot = &t; break; }
    if (slot) { slot->m = m; slot->b = (const uint8_t *)base; slot->n = size; }
}
extern "C" void *FindResourceExA(HMODULE m, LPCSTR type, LPCSTR name, WORD lang)
{
    Img im;
    if (!open_image(m, &im)) return NULL;
    return (void *)find(im, mkkey(type), mkkey(name), lang & 0xFFFF);
}
extern "C" void *FindResourceA(HMODULE m, LPCSTR name, LPCSTR type) { return FindResourceExA(m, type, name, 0); }
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
extern "C" BOOL FreeResource(void *) { return FALSE; }   /* 16-bit contract: FALSE = freed */
extern "C" BOOL EnumResourceNamesA(HMODULE m, LPCSTR type, BOOL (*cb)(HMODULE, LPCSTR, LPSTR, LONG_PTR), LONG_PTR param)
{
    Img im; Dir d; uint32_t off;
    if (!cb || !open_image(m, &im) || !open_dir(im, 0, &d)) return FALSE;
    int r = lookup(im, d, mkkey(type), &off);
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
    Img im; Dib dib; int hx, hy;
    HMODULE m = hi ? (HMODULE)hi : EXE_MODULE;
    if (!open_image(m, &im)) return NULL;
    const uint8_t *g = find(im, idkey(is_cursor ? 12 : 14), mkkey(name), 0);
    if (!g) return NULL;
    { Lk l; for (Icon *i = g_icons; i; i = i->next) if (i->m == m && i->ent == g && i->cur == is_cursor) return i; }
    int sz = GetSystemMetrics(is_cursor ? 13 /* SM_CXCURSOR */ : 11 /* SM_CXICON */), sy = GetSystemMetrics(is_cursor ? 14 : 12);
    if (!load_icon_dib(im, g, is_cursor, sz, sy, &dib, &hx, &hy)) return NULL;
    Icon *ic = (Icon *)calloc(1, sizeof *ic);
    if (!ic) { free(dib.px); SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
    ic->m = m; ic->ent = g; ic->cur = is_cursor; ic->w = dib.w; ic->h = dib.h; ic->hx = hx; ic->hy = hy; ic->px = dib.px;
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
    Dib d = { NULL, 0, 0 };
    uint32_t mask = W32_DW(crMask), fl = flags;
    uint8_t *file = NULL;
    Img im;
    W32_UNUSED(grow);
    if (cx <= 0 || type > 2) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (type != 0 && (fl & 0x10)) { SetLastError(ERROR_NOT_SUPPORTED); return NULL; }   /* .ico/.cur files: not read */
    if (fl & 0x10) {
        size_t n;
        if (!read_file(name, &file, &n)) return NULL;
        if (n < 14 + 40 || file[0] != 'B' || file[1] != 'M') { free(file); SetLastError(E_INVALID_DATA); return NULL; }
        bool g = dib_decode(file + 14, n - 14, false, false, fl, &d);
        free(file);
        if (!g) return NULL;
    } else {
        if (!open_image((HMODULE)hi, &im)) return NULL;
        const uint8_t *src, *e;
        size_t n;
        int hx, hy;
        if (type == 0) {
            if (!(e = find(im, idkey(2), mkkey(name), 0)) || !entry_data(im, e, &src, &n) || !dib_decode(src, n, false, false, fl, &d)) return NULL;
        } else {
            if (!(e = find(im, idkey(type == 1 ? 14 : 12), mkkey(name), 0)) || !load_icon_dib(im, e, type == 2, cx, cx, &d, &hx, &hy)) return NULL;
            cx = d.w; mask = 0xFFFFFFFFu;   /* an icon carries its own mask */
        }
    }
    int count = d.w / cx;
    IList *l = count ? (IList *)calloc(1, sizeof *l) : NULL;
    if (l) l->px = (uint32_t *)malloc((size_t)count * cx * d.h * 4);
    if (!l || !l->px) { free(l); free(d.px); SetLastError(count ? ERROR_NOT_ENOUGH_MEMORY : ERROR_INVALID_PARAMETER); return NULL; }
    uint32_t key = mask == 0xFF000000u ? d.px[0] & 0xFFFFFF : (mask & 0xFF) << 16 | (mask & 0xFF00) | (mask >> 16 & 0xFF);   /* CLR_DEFAULT: top-left */
    for (int i = 0; i < count; i++)
        for (int y = 0; y < d.h; y++)
            for (int x = 0; x < cx; x++) {
                uint32_t p = d.px[y * d.w + i * cx + x];
                l->px[((size_t)i * d.h + y) * cx + x] = (mask != 0xFFFFFFFFu && (p >> 24) && (p & 0xFFFFFF) == key) ? 0 : p;
            }
    free(d.px);
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
