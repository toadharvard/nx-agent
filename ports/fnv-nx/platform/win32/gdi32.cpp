/* gdi32.cpp - the GDI subset the game and the lifted video DLL import: stock objects, memory DCs, device-dependent
 * bitmaps and DIB sections, Get/SetPixel, StretchBlt (all 256 ROP3 by one truth-table rule), GetDIBits,
 * GetDeviceCaps, and the DC side of user32 (GetDC, ReleaseDC, BeginPaint, EndPaint).
 *
 * Model. One private handle table; a handle is (type<<28 | generation<<16 | slot), a 32-bit opaque value, checked on
 * every call (dead slot, stale generation or wrong type -> the documented failure return). Pixel values are carried
 * as 0x00RRGGBB; COLORREFs (0x00BBGGRR) are converted at the API edge. Structures read from callers use the fixed
 * 32-bit Win32 layout (byte offsets), so they hold on the LP64 host tests too.
 *
 * Window surfaces come from one hook the renderer supplies (weak defaults below back each window by a private
 * 32bpp buffer of its client size, the screen DC by one of SM_CXSCREEN x SM_CYSCREEN):
 *   int  fnvnx_window_surface(HWND, void **pixels, int *w, int *h, int *pitch);   -- 1 = ok; pixels are 32bpp BGRX,
 *        top-down, pitch in bytes; HWND NULL = the desktop. Called with the GDI lock held; must not call GDI.
 *   void fnvnx_window_surface_dirty(HWND, const RECT *);   -- client-space rectangle that was written.
 *
 * StretchBlt rule: dest pixel i of a |w| run covers source pixels [floor(i*|sw|/|w|), floor((i+1)*|sw|/|w|)) (at
 * least one); mirrored when the signs of w and sw differ; the group is reduced by the DC's stretch mode (AND / OR /
 * first / box average per channel); the result is rop3(idx, P, S, D) per bit with idx = bits 16-23 of the rop and
 * the bit number P<<2|S<<1|D. Dest pixels clipped away, or whose source group lies wholly outside the source
 * surface, are left untouched. Mono (1bpp DDB) source: 0 -> text colour, 1 -> background colour (black, white: the
 * DC defaults); colour -> mono DDB: pixels equal to the source background colour become 1. */
#include "w32.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

extern "C" {
int fnvnx_window_surface(HWND, void **pixels, int *w, int *h, int *pitch);
void fnvnx_window_surface_dirty(HWND, const RECT *);
}

/* ---- weak default surfaces */
namespace {
struct wbuf { HWND h; int used, w, ht; uint8_t *p; RECT dirty; };
wbuf g_wb[64];
os_lock_t g_wb_lock;
}
extern "C" __attribute__((weak)) int fnvnx_window_surface(HWND h, void **px, int *w, int *ht, int *pitch)
{
    int cw, ch;
    RECT r;
    if (h) {
        if (!IsWindow(h) || !GetClientRect(h, &r)) return 0;
        cw = (int)r.right; ch = (int)r.bottom;
    } else {
        cw = GetSystemMetrics(SM_CXSCREEN); ch = GetSystemMetrics(SM_CYSCREEN);
    }
    if (cw <= 0 || ch <= 0 || (int64_t)cw * ch > (1 << 27) || !px || !w || !ht || !pitch) return 0;
    int ok = 0;
    os_lock(&g_wb_lock);
    wbuf *e = NULL, *fr = NULL;
    for (int i = 0; i < 64; i++) {
        if (g_wb[i].used && g_wb[i].h == h) e = &g_wb[i];
        else if (g_wb[i].used && g_wb[i].h && !IsWindow(g_wb[i].h)) { free(g_wb[i].p); memset(&g_wb[i], 0, sizeof g_wb[i]); }
        if (!g_wb[i].used && !fr) fr = &g_wb[i];
    }
    if (!e) e = fr;
    if (e) {
        if (!e->used || e->w != cw || e->ht != ch) {
            free(e->p);
            memset(e, 0, sizeof *e);
            e->p = (uint8_t *)calloc((size_t)cw * ch, 4);
            if (e->p) { e->used = 1; e->h = h; e->w = cw; e->ht = ch; }
        }
        if (e->used) { *px = e->p; *w = cw; *ht = ch; *pitch = cw * 4; ok = 1; }
    }
    os_unlock(&g_wb_lock);
    return ok;
}
extern "C" __attribute__((weak)) void fnvnx_window_surface_dirty(HWND h, const RECT *r)
{
    if (!r) return;
    os_lock(&g_wb_lock);   /* the default surface keeps the union of what was written, per window */
    for (int i = 0; i < 64; i++)
        if (g_wb[i].used && g_wb[i].h == h) {
            RECT &d = g_wb[i].dirty;
            bool empty = d.right <= d.left || d.bottom <= d.top;
            if (empty || r->left < d.left) d.left = r->left;
            if (empty || r->top < d.top) d.top = r->top;
            if (empty || r->right > d.right) d.right = r->right;
            if (empty || r->bottom > d.bottom) d.bottom = r->bottom;
        }
    os_unlock(&g_wb_lock);
}

namespace {
enum { T_DC = 1, T_BMP, T_BRUSH, T_PEN, T_FONT, T_PAL };
enum { K_MEM, K_WIN, K_SCR, K_PAINT };
const uint32_t CLRINV = 0xFFFFFFFFu;
const uint32_t SYSPAL[20] = { 0x000000, 0x800000, 0x008000, 0x808000, 0x000080, 0x800080, 0x008080, 0xC0C0C0, 0xC0DCC0, 0xA6CAF0,
                              0xFFFBF0, 0xA0A0A4, 0x808080, 0xFF0000, 0x00FF00, 0xFFFF00, 0x0000FF, 0xFF00FF, 0x00FFFF, 0xFFFFFF };

struct obj {
    uint8_t type, stock;
    int sel;                                 /* DCs holding it (brush/pen/font) */
    int w, h, bpp, pitch, nct;               /* bitmap */
    bool td, dib;
    uint8_t *bits;
    uint32_t ct[256], mask[3], selin, owner; /* selin: DC it is selected into; owner: DC whose stock bitmap it is */
    uint32_t color; int hollow;              /* brush / pen */
    int kind, smode;                         /* DC */
    HWND hwnd;
    uint32_t bmp, brush, pen, font, pal;
    uint32_t own;
};
struct slot { obj *o; uint32_t gen; };
slot g_slots[65536];
os_lock_t g_lock;
bool g_init;
struct Lk { Lk() { os_lock(&g_lock); } ~Lk() { os_unlock(&g_lock); } };

uint32_t mkh(int type, uint32_t gen, uint32_t idx) { return (uint32_t)type << 28 | gen << 16 | idx; }
obj *get(const void *hp, int type)
{
    uintptr_t v = (uintptr_t)hp;
    if (!v || (v >> 32)) return NULL;
    uint32_t h = (uint32_t)v, idx = h & 0xFFFF, gen = (h >> 16) & 0xFFF, t = h >> 28;
    obj *o = g_slots[idx].o;
    if (!o || g_slots[idx].gen != gen || o->type != t || (type && t != (uint32_t)type)) return NULL;
    return o;
}
uint32_t handle_of(obj *o) { for (uint32_t i = 0; i < 65536; i++) if (g_slots[i].o == o) return mkh(o->type, g_slots[i].gen, i); return 0; }
uint32_t alloc(int type, obj **out)
{
    for (uint32_t i = 32; i < 65536; i++)
        if (!g_slots[i].o) {
            obj *o = (obj *)calloc(1, sizeof(obj));
            if (!o) return 0;
            o->type = (uint8_t)type;
            if (!g_slots[i].gen) g_slots[i].gen = 1;
            g_slots[i].o = o;
            *out = o;
            return mkh(type, g_slots[i].gen, i);
        }
    return 0;
}
void release(uint32_t h)
{
    uint32_t idx = h & 0xFFFF;
    obj *o = g_slots[idx].o;
    if (!o) return;
    free(o->bits);
    free(o);
    g_slots[idx].o = NULL;
    g_slots[idx].gen = (g_slots[idx].gen % 4095) + 1;
}

void init_stock()
{
    if (g_init) return;
    g_init = true;
    static const uint32_t bc[6] = { 0xFFFFFF, 0xC0C0C0, 0x808080, 0x404040, 0x000000, 0 };
    for (int i = 0; i < 20; i++) {
        if (i == 9) continue;
        obj *o = (obj *)calloc(1, sizeof(obj));
        o->stock = 1;
        o->type = i < 6 || i == 18 ? T_BRUSH : i < 9 || i == 19 ? T_PEN : i == 15 ? T_PAL : T_FONT;
        if (i < 6) { o->color = bc[i]; o->hollow = i == 5; }
        if (i == 6) o->color = 0xFFFFFF;
        if (i == 8) o->hollow = 1;
        if (i == 18) o->color = 0xFFFFFF;
        if (i == 15) { o->nct = 20; memcpy(o->ct, SYSPAL, sizeof SYSPAL); }
        g_slots[1 + i].o = o;     /* generation 0: dynamic slots start at 32 with generation >= 1 */
    }
}
uint32_t stock_h(int i) { return mkh(g_slots[1 + i].o->type, 0, 1 + i); }

/* ---- surfaces and pixels */
struct surf {
    uint8_t *p; int w, h, bpp, pitch, nct; bool td, ddb; const uint32_t *ct; uint32_t m[3]; HWND win; bool onwin;
};
const uint32_t M16[3] = { 0x7C00, 0x03E0, 0x001F }, M32[3] = { 0xFF0000, 0xFF00, 0xFF };

bool bm_surf(const obj *b, surf &s)
{
    s.p = b->bits; s.w = b->w; s.h = b->h; s.bpp = b->bpp; s.pitch = b->pitch; s.nct = b->nct; s.td = b->td; s.ddb = !b->dib;
    s.ct = b->ct; memcpy(s.m, b->mask, sizeof s.m); s.win = NULL; s.onwin = false;
    return true;
}
bool dc_surf(obj *dc, surf &s)
{
    if (dc->kind == K_MEM) { obj *b = get((void *)(uintptr_t)dc->bmp, T_BMP); return b && bm_surf(b, s); }
    void *px = NULL; int w = 0, h = 0, pitch = 0;
    if (!fnvnx_window_surface(dc->hwnd, &px, &w, &h, &pitch) || !px || w <= 0 || h <= 0) return false;
    s.p = (uint8_t *)px; s.w = w; s.h = h; s.bpp = 32; s.pitch = pitch; s.nct = 0; s.td = true; s.ddb = true; s.ct = NULL;
    memcpy(s.m, M32, sizeof s.m); s.win = dc->hwnd; s.onwin = true;
    return true;
}
inline uint8_t *rowp(const surf &s, int y) { return s.p + (size_t)(s.td ? y : s.h - 1 - y) * s.pitch; }
inline int ctz32(uint32_t v) { return __builtin_ctz(v); }
uint32_t chan_dec(uint32_t v, uint32_t mask)
{
    if (!mask) return 0;
    int sh = ctz32(mask);
    uint64_t mx = mask >> sh;
    return (uint32_t)(((uint64_t)((v & mask) >> sh) * 255 + mx / 2) / mx);
}
uint32_t chan_enc(uint32_t c, uint32_t mask)
{
    if (!mask) return 0;
    int sh = ctz32(mask);
    uint64_t mx = mask >> sh;
    return (uint32_t)(((uint64_t)c * mx + 127) / 255) << sh;
}
uint32_t nearest(const surf &s, uint32_t c)
{
    int r = (c >> 16) & 255, g = (c >> 8) & 255, b = c & 255, best = -1;
    uint32_t bi = 0;
    for (int i = 0; i < s.nct; i++) {
        uint32_t e = s.ct[i];
        int dr = (int)((e >> 16) & 255) - r, dg = (int)((e >> 8) & 255) - g, db = (int)(e & 255) - b, d = dr * dr + dg * dg + db * db;
        if (best < 0 || d < best) { best = d; bi = (uint32_t)i; if (!d) break; }
    }
    return bi;
}
uint32_t raw_get(const surf &s, int x, int y)
{
    const uint8_t *r = rowp(s, y);
    switch (s.bpp) {
    case 1: return (r[x >> 3] >> (7 - (x & 7))) & 1;
    case 4: return (x & 1) ? r[x >> 1] & 15 : r[x >> 1] >> 4;
    case 8: return r[x];
    case 16: { uint16_t v; memcpy(&v, r + 2 * x, 2); return v; }
    case 24: return r[3 * x] | (uint32_t)r[3 * x + 1] << 8 | (uint32_t)r[3 * x + 2] << 16;
    default: { uint32_t v; memcpy(&v, r + 4 * x, 4); return v; }
    }
}
void raw_put(const surf &s, int x, int y, uint32_t v)
{
    uint8_t *r = rowp(s, y);
    switch (s.bpp) {
    case 1: r[x >> 3] = (uint8_t)((r[x >> 3] & ~(0x80 >> (x & 7))) | ((v & 1) << (7 - (x & 7)))); break;
    case 4: r[x >> 1] = (uint8_t)((x & 1) ? (r[x >> 1] & 0xF0) | (v & 15) : (r[x >> 1] & 0x0F) | ((v & 15) << 4)); break;
    case 8: r[x] = (uint8_t)v; break;
    case 16: { uint16_t t = (uint16_t)v; memcpy(r + 2 * x, &t, 2); break; }
    case 24: r[3 * x] = (uint8_t)v; r[3 * x + 1] = (uint8_t)(v >> 8); r[3 * x + 2] = (uint8_t)(v >> 16); break;
    default: memcpy(r + 4 * x, &v, 4);
    }
}
uint32_t px_get(const surf &s, int x, int y)   /* -> 0x00RRGGBB */
{
    uint32_t v = raw_get(s, x, y);
    if (s.bpp <= 8) return (int)v < s.nct ? s.ct[v] & 0xFFFFFF : 0;
    if (s.bpp == 24) return v;
    if (s.bpp == 32 && s.m[0] == M32[0] && s.m[1] == M32[1] && s.m[2] == M32[2]) return v & 0xFFFFFF;
    return chan_dec(v, s.m[0]) << 16 | chan_dec(v, s.m[1]) << 8 | chan_dec(v, s.m[2]);
}
void px_put(const surf &s, int x, int y, uint32_t c)
{
    c &= 0xFFFFFF;
    if (s.bpp <= 8) raw_put(s, x, y, nearest(s, c));
    else if (s.bpp == 24 || (s.bpp == 32 && s.m[0] == M32[0] && s.m[1] == M32[1] && s.m[2] == M32[2])) raw_put(s, x, y, c);
    else raw_put(s, x, y, chan_enc(c >> 16, s.m[0]) | chan_enc((c >> 8) & 255, s.m[1]) | chan_enc(c & 255, s.m[2]));
}
uint32_t swp(uint32_t x) { return ((x & 0xFF) << 16) | (x & 0xFF00) | ((x >> 16) & 0xFF); }
void dirty(const surf &s, int l, int t, int r, int b)
{
    if (!s.onwin) return;
    RECT rc; rc.left = l; rc.top = t; rc.right = r; rc.bottom = b;
    fnvnx_window_surface_dirty(s.win, &rc);
}

/* ---- raw structure access */
uint32_t u32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
uint16_t u16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
void w32p(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
int64_t stride_of(int w, int bpp) { return ((int64_t)w * bpp + 31) / 32 * 4; }
bool masks_ok(const uint32_t *m, int bpp)
{
    for (int i = 0; i < 3; i++) {
        if (bpp == 16 && m[i] > 0xFFFF) return false;
        if (m[i]) { uint32_t q = m[i] >> ctz32(m[i]); if (q & (q + 1)) return false; }
    }
    return !((m[0] & m[1]) || (m[0] & m[2]) || (m[1] & m[2]));
}
/* the format a BITMAPINFOHEADER asks for; false = not a valid DIB description */
struct fmt { int w, h, bpp, comp; bool td; uint32_t m[3]; int nct; };
bool read_fmt(const uint8_t *b, fmt &f, bool need_wh)
{
    if (u32(b) < 40) return false;
    int32_t w = (int32_t)u32(b + 4), h = (int32_t)u32(b + 8);
    f.bpp = u16(b + 14); f.comp = (int)u32(b + 16); f.td = h < 0; f.w = w; f.h = h < 0 ? -h : h;
    if (need_wh && (w <= 0 || h == 0 || h == INT32_MIN)) return false;
    if (f.bpp != 1 && f.bpp != 4 && f.bpp != 8 && f.bpp != 16 && f.bpp != 24 && f.bpp != 32) return false;
    if (f.comp == 0) { memcpy(f.m, f.bpp == 16 ? M16 : M32, sizeof f.m); }
    else if (f.comp == 3 && (f.bpp == 16 || f.bpp == 32)) { for (int i = 0; i < 3; i++) f.m[i] = u32(b + 40 + 4 * i); if (!masks_ok(f.m, f.bpp)) return false; }
    else return false;
    if (f.comp == 0 && (f.bpp == 24 || f.bpp <= 8)) memset(f.m, 0, sizeof f.m);
    uint32_t used = u32(b + 32);
    f.nct = 0;
    if (f.bpp <= 8) { if (used > (1u << f.bpp)) return false; f.nct = used ? (int)used : 1 << f.bpp; }
    return true;
}
uint32_t std_pal(int bpp, int i)
{
    static const uint32_t vga[16] = { 0, 0x800000, 0x008000, 0x808000, 0x000080, 0x800080, 0x008080, 0xC0C0C0, 0x808080, 0xFF0000,
                                      0x00FF00, 0xFFFF00, 0x0000FF, 0xFF00FF, 0x00FFFF, 0xFFFFFF };
    if (bpp == 1) return i ? 0xFFFFFF : 0;
    if (bpp == 4) return vga[i & 15];
    return (uint32_t)(((i >> 5) * 255 / 7) << 16 | (((i >> 2) & 7) * 255 / 7) << 8 | ((i & 3) * 255 / 3));
}
uint32_t new_bitmap(int w, int h, int bpp, bool td, bool dib, obj **out)
{
    if (w <= 0 || h <= 0 || stride_of(w, bpp) > (1 << 30)) return 0;
    int64_t sz = stride_of(w, bpp) * h;
    if (sz > (1 << 30)) return 0;
    uint8_t *bits = (uint8_t *)calloc((size_t)sz, 1);
    if (!bits) return 0;
    obj *o = NULL;
    uint32_t hd = alloc(T_BMP, &o);
    if (!hd) { free(bits); return 0; }
    o->w = w; o->h = h; o->bpp = bpp; o->pitch = (int)stride_of(w, bpp); o->td = td; o->dib = dib; o->bits = bits;
    *out = o;
    return hd;
}
void deselect(obj *b)
{
    b->selin = 0;
    if (b->stock && !b->owner) release(handle_of(b));   /* an orphaned stock bitmap of a deleted DC */
}
uint32_t new_dc(int kind, HWND hw)
{
    init_stock();
    obj *d = NULL;
    uint32_t hd = alloc(T_DC, &d);
    if (!hd) return 0;
    d->kind = kind; d->hwnd = hw; d->smode = 1;
    d->brush = stock_h(0); d->pen = stock_h(7); d->font = stock_h(13); d->pal = stock_h(15);
    if (kind == K_MEM) {
        obj *b = NULL;
        uint32_t bh = new_bitmap(1, 1, 1, true, false, &b);
        if (!bh) { release(hd); return 0; }
        b->nct = 2; b->ct[1] = 0xFFFFFF; b->stock = 1; b->owner = hd; b->selin = hd;
        d->bmp = d->own = bh;
    }
    return hd;
}
void free_dc(uint32_t hd, obj *d)
{
    if (d->kind == K_MEM) {
        obj *b = get((void *)(uintptr_t)d->bmp, T_BMP);
        obj *own = get((void *)(uintptr_t)d->own, T_BMP);
        if (b) b->selin = 0;
        if (own) { own->owner = 0; if (!own->selin) release(d->own); }
        if (b && b != own && b->stock && !b->owner) release(d->bmp);   /* another dead DC's stock bitmap */
    }
    obj *o[3] = { get((void *)(uintptr_t)d->brush, T_BRUSH), get((void *)(uintptr_t)d->pen, T_PEN), get((void *)(uintptr_t)d->font, T_FONT) };
    for (obj *x : o) if (x && !x->stock) x->sel--;
    release(hd);
}
int rop_dep(int idx, int bit)
{
    for (int k = 0; k < 8; k++) if (((idx >> k) & 1) != ((idx >> (k ^ bit)) & 1)) return 1;
    return 0;
}
uint32_t rop3(int idx, uint32_t p, uint32_t s, uint32_t d)
{
    uint32_t r = 0;
    for (int k = 0; k < 8; k++)
        if ((idx >> k) & 1) r |= ((k & 4) ? p : ~p) & ((k & 2) ? s : ~s) & ((k & 1) ? d : ~d);
    return r & 0xFFFFFF;
}
} // namespace

/* ---- objects and DCs */
extern "C" HGDIOBJ GetStockObject(int i)
{
    if (i < 0 || i > 19 || i == 9) return NULL;
    Lk l;
    init_stock();
    return (HGDIOBJ)(uintptr_t)stock_h(i);
}
extern "C" BOOL DeleteObject(HGDIOBJ h)
{
    Lk l;
    init_stock();
    obj *o = get(h, 0);
    if (!o || o->type == T_DC) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    if (o->stock) return TRUE;
    if (o->selin || o->sel > 0) return FALSE;
    release((uint32_t)(uintptr_t)h);
    return TRUE;
}
extern "C" HDC CreateCompatibleDC(HDC src)
{
    Lk l;
    init_stock();
    if (src && !get(src, T_DC)) { SetLastError(ERROR_INVALID_HANDLE); return NULL; }
    return (HDC)(uintptr_t)new_dc(K_MEM, NULL);
}
extern "C" BOOL DeleteDC(HDC hd)
{
    Lk l;
    obj *d = get(hd, T_DC);
    if (!d || d->kind != K_MEM) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    free_dc((uint32_t)(uintptr_t)hd, d);
    return TRUE;
}
extern "C" HBITMAP CreateCompatibleBitmap(HDC hd, int w, int h)
{
    Lk l;
    obj *d = get(hd, T_DC), *b = NULL;
    surf s;
    if (!d || !dc_surf(d, s) || w < 0 || h < 0) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (!w || !h) {   /* documented: a 1x1 monochrome bitmap */
        uint32_t bh = new_bitmap(1, 1, 1, true, false, &b);
        if (bh) { b->nct = 2; b->ct[1] = 0xFFFFFF; }
        return (HBITMAP)(uintptr_t)bh;
    }
    uint32_t bh = new_bitmap(w, h, s.bpp, true, false, &b);
    if (!bh) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
    b->nct = s.nct;
    if (s.nct) memcpy(b->ct, s.ct, sizeof(uint32_t) * (size_t)s.nct);
    memcpy(b->mask, s.m, sizeof b->mask);
    return (HBITMAP)(uintptr_t)bh;
}
extern "C" HBITMAP CreateDIBSection(HDC hd, const void *bmi, UINT usage, void **ppv, HANDLE section, DWORD offset)
{
    W32_UNUSED(offset);
    Lk l;
    init_stock();
    fmt f;
    const uint8_t *b = (const uint8_t *)bmi;
    if (!bmi || !ppv || section || usage > 1 || !read_fmt(b, f, true) || (usage == 1 && !get(hd, T_DC)) || u16(b + 12) != 1) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    obj *o = NULL;
    uint32_t bh = new_bitmap(f.w, f.h, f.bpp, f.td, true, &o);
    if (!bh) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    o->nct = f.nct;
    memcpy(o->mask, f.m, sizeof o->mask);
    const uint8_t *tab = b + u32(b);
    for (int i = 0; i < f.nct; i++)
        o->ct[i] = usage == 0 ? u32(tab + 4 * i) & 0xFFFFFF : (u16(tab + 2 * i) < 20 ? SYSPAL[u16(tab + 2 * i)] : 0);
    *ppv = o->bits;
    return (HBITMAP)(uintptr_t)bh;
}
extern "C" HGDIOBJ SelectObject(HDC hd, HGDIOBJ ho)
{
    Lk l;
    init_stock();
    obj *d = get(hd, T_DC), *o = get(ho, 0);
    if (!d || !o) { SetLastError(ERROR_INVALID_HANDLE); return NULL; }
    uint32_t nh = (uint32_t)(uintptr_t)ho, dh = (uint32_t)(uintptr_t)hd, prev;
    uint32_t *fld;
    switch (o->type) {
    case T_BMP: {
        if (d->kind != K_MEM || (o->selin && o->selin != dh)) return NULL;
        if (o->selin == dh) return ho;
        prev = d->bmp;
        if (obj *p = get((void *)(uintptr_t)prev, T_BMP)) deselect(p);
        o->selin = dh;
        d->bmp = nh;
        return (HGDIOBJ)(uintptr_t)prev;
    }
    case T_BRUSH: fld = &d->brush; break;
    case T_PEN: fld = &d->pen; break;
    case T_FONT: fld = &d->font; break;
    default: return NULL;
    }
    prev = *fld;
    if (obj *p = get((void *)(uintptr_t)prev, 0)) if (!p->stock) p->sel--;
    if (!o->stock) o->sel++;
    *fld = nh;
    return (HGDIOBJ)(uintptr_t)prev;
}

/* ---- pixels */
extern "C" COLORREF GetPixel(HDC hd, int x, int y)
{
    Lk l;
    obj *d = get(hd, T_DC);
    surf s;
    if (!d || !dc_surf(d, s) || x < 0 || y < 0 || x >= s.w || y >= s.h) return (COLORREF)CLRINV;
    return (COLORREF)swp(px_get(s, x, y));
}
extern "C" COLORREF SetPixel(HDC hd, int x, int y, COLORREF c)
{
    Lk l;
    obj *d = get(hd, T_DC);
    surf s;
    if (!d || !dc_surf(d, s) || x < 0 || y < 0 || x >= s.w || y >= s.h) return (COLORREF)CLRINV;
    px_put(s, x, y, swp((uint32_t)c & 0xFFFFFF));
    dirty(s, x, y, x + 1, y + 1);
    return (COLORREF)swp(px_get(s, x, y));
}
extern "C" int SetStretchBltMode(HDC hd, int mode)
{
    Lk l;
    obj *d = get(hd, T_DC);
    if (!d || mode < 1 || mode > 4) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    int p = d->smode;
    d->smode = mode;
    return p;
}

extern "C" BOOL StretchBlt(HDC hd, int x, int y, int w, int h, HDC hs, int sx, int sy, int sw, int sh, DWORD rop)
{
    Lk l;
    obj *d = get(hd, T_DC), *so = NULL;
    surf D, S = {};
    int idx = (int)((rop >> 16) & 255);
    bool needS = rop_dep(idx, 2), needP = rop_dep(idx, 4);
    if (!d || !dc_surf(d, D)) return FALSE;
    if (needS && (!(so = get(hs, T_DC)) || !dc_surf(so, S) || !sw || !sh)) return FALSE;
    if (!w || !h) return TRUE;
    uint32_t P = 0;
    if (needP) {
        obj *b = get((void *)(uintptr_t)d->brush, T_BRUSH);
        if (b && b->hollow) return TRUE;   /* a hollow brush paints nothing */
        P = b ? b->color : 0xFFFFFF;
    }
    int aw = w < 0 ? -w : w, ah = h < 0 ? -h : h, asw = sw < 0 ? -sw : sw, ash = sh < 0 ? -sh : sh;
    int dx0 = w < 0 ? x + w : x, dy0 = h < 0 ? y + h : y, sx0 = sw < 0 ? sx + sw : sx, sy0 = sh < 0 ? sy + sh : sy;
    bool mx = (w < 0) != (sw < 0), my = (h < 0) != (sh < 0);
    int64_t cx0 = dx0 < 0 ? 0 : dx0, cy0 = dy0 < 0 ? 0 : dy0;
    int64_t cx1 = (int64_t)dx0 + aw > D.w ? D.w : (int64_t)dx0 + aw, cy1 = (int64_t)dy0 + ah > D.h ? D.h : (int64_t)dy0 + ah;
    if (cx0 >= cx1 || cy0 >= cy1) return TRUE;
    uint8_t *snap = NULL;
    if (needS && S.p < D.p + (size_t)D.pitch * D.h && D.p < S.p + (size_t)S.pitch * S.h) {   /* same memory: read the source first */
        size_t n = (size_t)S.pitch * S.h;
        snap = (uint8_t *)malloc(n);
        if (!snap) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return FALSE; }
        memcpy(snap, S.p, n);
        S.p = snap;
    }
    bool toMono = D.bpp == 1 && D.ddb && needS && S.bpp != 1;
    int mode = d->smode;
    for (int64_t py = cy0; py < cy1; py++) {
        int iy = (int)(py - dy0);
        int ya = 0, yb = 0;
        if (needS) {
            ya = (int)((int64_t)iy * ash / ah); yb = (int)((int64_t)(iy + 1) * ash / ah);
            if (yb <= ya) yb = ya + 1;
            if (my) { int t = ash - yb; yb = ash - ya; ya = t; }
            ya += sy0; yb += sy0;
        }
        for (int64_t px = cx0; px < cx1; px++) {
            uint32_t sv = 0;
            if (needS) {
                int ix = (int)(px - dx0);
                int xa = (int)((int64_t)ix * asw / aw), xb = (int)((int64_t)(ix + 1) * asw / aw);
                if (xb <= xa) xb = xa + 1;
                int pickx = xa, picky = (int)((int64_t)iy * ash / ah);
                if (mx) { int t = asw - xb; xb = asw - xa; xa = t; pickx = asw - 1 - pickx; }
                if (my) picky = ash - 1 - picky;
                xa += sx0; xb += sx0; pickx += sx0; picky += sy0;
                int x1 = xa < 0 ? 0 : xa, x2 = xb > S.w ? S.w : xb, y1 = ya < 0 ? 0 : ya, y2 = yb > S.h ? S.h : yb;
                if (x1 >= x2 || y1 >= y2) continue;
                if (mode == 3 || (xb - xa == 1 && yb - ya == 1)) {
                    if (pickx < 0 || pickx >= S.w || picky < 0 || picky >= S.h) continue;
                    sv = px_get(S, pickx, picky);
                } else {
                    uint32_t a = 0xFFFFFF, o = 0, n = 0, sum[3] = { 0, 0, 0 };
                    for (int j = y1; j < y2; j++)
                        for (int i = x1; i < x2; i++) {
                            uint32_t v = px_get(S, i, j);
                            a &= v; o |= v; n++;
                            sum[0] += v & 255; sum[1] += (v >> 8) & 255; sum[2] += (v >> 16) & 255;
                        }
                    sv = mode == 1 ? a : mode == 2 ? o : ((sum[2] + n / 2) / n) << 16 | ((sum[1] + n / 2) / n) << 8 | ((sum[0] + n / 2) / n);
                }
                if (toMono) sv = sv == 0xFFFFFF ? 0xFFFFFF : 0;
            }
            uint32_t dv = idx == 0xCC ? 0 : px_get(D, (int)px, (int)py);
            px_put(D, (int)px, (int)py, idx == 0xCC ? sv : rop3(idx, P, sv, dv));
        }
    }
    free(snap);
    dirty(D, (int)cx0, (int)cy0, (int)cx1, (int)cy1);
    return TRUE;
}

extern "C" int GetDIBits(HDC hd, HBITMAP hb, UINT start, UINT lines, void *bits, void *bmi, UINT usage)
{
    Lk l;
    obj *d = get(hd, T_DC), *bm = get(hb, T_BMP);
    if (!d || !bm || bm->selin || !bmi || usage != 0) return 0;
    uint8_t *b = (uint8_t *)bmi;
    surf S;
    bm_surf(bm, S);
    fmt f;
    int bc = u16(b + 14);
    if (u32(b) < 40) return 0;
    bool stdm = bm->bpp == 16 ? !memcmp(bm->mask, M16, 12) : bm->bpp == 32 ? !memcmp(bm->mask, M32, 12) : true;
    if (!bits && bc == 0) {   /* describe the bitmap itself */
        w32p(b + 4, (uint32_t)bm->w); w32p(b + 8, (uint32_t)bm->h);
        b[12] = 1; b[13] = 0; b[14] = (uint8_t)bm->bpp; b[15] = 0;
        w32p(b + 16, stdm ? 0 : 3); w32p(b + 20, (uint32_t)bm->pitch * bm->h);
        if (!stdm) for (int i = 0; i < 3; i++) w32p(b + 40 + 4 * i, bm->mask[i]);
        if (bm->bpp <= 8) { w32p(b + 32, (uint32_t)bm->nct); for (int i = 0; i < bm->nct; i++) w32p(b + u32(b) + 4 * i, bm->ct[i]); }
        return bm->h;
    }
    if (!read_fmt(b, f, false)) return 0;
    uint32_t tab[256];
    int n = f.bpp <= 8 ? 1 << f.bpp : 0;
    bool same = f.bpp <= 8 && bm->bpp == f.bpp && bm->nct > 0;
    for (int i = 0; i < n; i++) tab[i] = same ? (i < bm->nct ? bm->ct[i] : 0) : std_pal(f.bpp, i);
    int64_t pitch64 = stride_of(bm->w, f.bpp);
    if (pitch64 > (1 << 30)) return 0;
    int pitch = (int)pitch64;
    w32p(b + 4, (uint32_t)bm->w); w32p(b + 8, (uint32_t)(f.td ? -bm->h : bm->h));
    b[12] = 1; b[13] = 0;
    w32p(b + 20, (uint32_t)pitch * bm->h); w32p(b + 32, (uint32_t)n);
    for (int i = 0; i < n; i++) w32p(b + u32(b) + 4 * i, tab[i]);
    if (!bits) return bm->h;
    if (start >= (UINT)bm->h || !lines) return 0;
    int cnt = (UINT)bm->h - start < lines ? bm->h - (int)start : (int)lines;
    surf T = S;
    T.p = (uint8_t *)bits; T.bpp = f.bpp; T.pitch = pitch; T.td = true; T.nct = n; T.ct = tab; T.ddb = false;
    memcpy(T.m, f.m, sizeof T.m);
    for (int k = 0; k < cnt; k++) {
        int line = (int)start + k, vy = f.td ? line : bm->h - 1 - line;
        memset(T.p + (size_t)k * pitch, 0, (size_t)pitch);
        for (int x = 0; x < bm->w; x++) {
            if (same) raw_put(T, x, k, raw_get(S, x, vy));
            else px_put(T, x, k, px_get(S, x, vy));
        }
    }
    return cnt;
}

extern "C" int GetDeviceCaps(HDC hd, int idx)
{
    Lk l;
    obj *d = get(hd, T_DC);
    if (!d) return 0;
    int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN), rw = sw, rh = sh, bpp = 32, nct = 0;
    if (d->kind == K_MEM) {
        surf s;
        if (!dc_surf(d, s)) return 0;
        rw = s.w; rh = s.h; bpp = s.bpp; nct = s.nct;
    }
    switch (idx) {
    case 0: return 0x0400;                  /* DRIVERVERSION */
    case 2: return 1;                       /* TECHNOLOGY = DT_RASDISPLAY */
    case 4: return (int)(((int64_t)rw * 254 + 480) / 960);    /* HORZSIZE, mm at 96 dpi */
    case 6: return (int)(((int64_t)rh * 254 + 480) / 960);
    case 8: return rw;
    case 10: return rh;
    case 12: return bpp;
    case 14: return 1;
    case 16: case 18: return -1;            /* NUMBRUSHES, NUMPENS */
    case 20: case 22: return 0;             /* NUMMARKERS, NUMFONTS */
    case 24: return bpp <= 8 ? nct : -1;    /* NUMCOLORS */
    case 26: return 0;
    case 28: return 0x1FF;                  /* CURVECAPS */
    case 30: return 0xFE;                   /* LINECAPS */
    case 32: return 0xFF;                   /* POLYGONALCAPS */
    case 34: return 0x6177;                 /* TEXTCAPS */
    case 36: return 1;                      /* CLIPCAPS = CP_REGION */
    case 38: return 0x7E99 | (bpp <= 8 ? 0x100 : 0);   /* RASTERCAPS (+ RC_PALETTE) */
    case 40: case 42: return 36;
    case 44: return 51;
    case 88: case 90: return 96;
    case 104: return 0;
    case 106: return 20;
    case 108: return 24;
    case 110: case 111: case 112: case 113: case 114: case 115: return 0;
    case 116: return 60;
    case 117: return sh;
    case 118: return sw;
    case 119: return 0;
    case 120: case 121: return 0;           /* SHADEBLENDCAPS, COLORMGMTCAPS: none */
    default: return 0;
    }
}

/* ---- user32, DC side */
struct fnvnx_paintstruct { HDC hdc; int fErase; RECT rcPaint; int fRestore, fIncUpdate; uint8_t rgb[32]; };

extern "C" HDC GetDC(HWND hw)
{
    Lk l;
    if (hw && !IsWindow(hw)) { SetLastError(ERROR_INVALID_HANDLE); return NULL; }
    return (HDC)(uintptr_t)new_dc(hw ? K_WIN : K_SCR, hw);
}
extern "C" int ReleaseDC(HWND hw, HDC hd)
{
    Lk l;
    obj *d = get(hd, T_DC);
    if (!d || (d->kind != K_WIN && d->kind != K_SCR) || d->hwnd != hw) return 0;
    free_dc((uint32_t)(uintptr_t)hd, d);
    return 1;
}
extern "C" HDC BeginPaint(HWND hw, void *psv)
{
    fnvnx_paintstruct *ps = (fnvnx_paintstruct *)psv;
    RECT rc;
    if (!ps || !hw || !IsWindow(hw) || !GetClientRect(hw, &rc)) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    Lk l;
    HDC hd = (HDC)(uintptr_t)new_dc(K_PAINT, hw);
    if (!hd) return NULL;
    memset(ps, 0, sizeof *ps);
    ps->hdc = hd; ps->fErase = 0; ps->rcPaint = rc;
    return hd;
}
extern "C" BOOL EndPaint(HWND hw, const void *psv)
{
    const fnvnx_paintstruct *ps = (const fnvnx_paintstruct *)psv;
    if (!ps) return FALSE;
    Lk l;
    obj *d = get(ps->hdc, T_DC);
    if (!d || d->kind != K_PAINT || d->hwnd != hw) return FALSE;
    free_dc((uint32_t)(uintptr_t)ps->hdc, d);
    return TRUE;
}
