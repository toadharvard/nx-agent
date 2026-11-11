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
#include "gdi_int.h"
#include "window_int.h"

#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

extern "C" {
int fnvnx_window_surface(HWND, void **pixels, int *w, int *h, int *pitch);
void fnvnx_window_surface_dirty(HWND, const RECT *);
}

/* ---- the window surfaces: one 32bpp buffer per window */
namespace {
struct wbuf { HWND h; int used, w, ht; uint8_t *p; RECT dirty; };
wbuf g_wb[64];
os_lock_t g_wb_lock;
}
extern "C" int fnvnx_window_surface(HWND h, void **px, int *w, int *ht, int *pitch)
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
extern "C" void fnvnx_window_surface_dirty(HWND h, const RECT *r)
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
enum { K_MEM, K_WIN, K_SCR, K_PAINT, K_DISP };   /* K_DISP: CreateDC("DISPLAY"), the desktop surface like K_SCR */
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
    int32_t ts[GDI_TS_COUNT];                /* DC text state, GDI_TS_* (gdi_int.h) */
    void *fdata;                             /* font: LOGFONTW copy (gdifont.cpp) */
    bool ext;                                /* bitmap: bits are the caller's memory (memdc_over_bits), never freed here */
    uint32_t xbmp;                           /* DC made by memdc_over_bits: the bitmap it made */
    void *view;                              /* a DIB section made over a file mapping: the base of its view (UnmapViewOfFile on delete) */
    RECT *clip; int nclip;                   /* DC: the clip region as disjoint rectangles in DC coordinates (a paint DC: the update region) */
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
    if (o->view) UnmapViewOfFile(o->view);   /* the view holds the section until the bitmap goes */
    else if (!o->ext) free(o->bits);          /* external bits (memdc_over_bits) are the caller's */
    free(o->fdata);
    free(o->clip);
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
    const RECT *clip; int nclip;   /* the DC's clip region: nothing outside it is written (nclip 0: no clip) */
};
const uint32_t M16[3] = { 0x7C00, 0x03E0, 0x001F }, M32[3] = { 0xFF0000, 0xFF00, 0xFF };

bool bm_surf(const obj *b, surf &s)
{
    s.p = b->bits; s.w = b->w; s.h = b->h; s.bpp = b->bpp; s.pitch = b->pitch; s.nct = b->nct; s.td = b->td; s.ddb = !b->dib;
    s.ct = b->ct; memcpy(s.m, b->mask, sizeof s.m); s.win = NULL; s.onwin = false; s.clip = NULL; s.nclip = 0;
    return true;
}
bool dc_surf(obj *dc, surf &s)
{
    if (dc->kind == K_MEM) { obj *b = get((void *)(uintptr_t)dc->bmp, T_BMP); return b && bm_surf(b, s); }
    void *px = NULL; int w = 0, h = 0, pitch = 0;
    if (!fnvnx_window_surface(dc->hwnd, &px, &w, &h, &pitch) || !px || w <= 0 || h <= 0) return false;
    s.p = (uint8_t *)px; s.w = w; s.h = h; s.bpp = 32; s.pitch = pitch; s.nct = 0; s.td = true; s.ddb = true; s.ct = NULL;
    memcpy(s.m, M32, sizeof s.m); s.win = dc->hwnd; s.onwin = true;
    s.clip = dc->clip; s.nclip = dc->nclip;
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
bool in_clip(const surf &s, int x, int y)
{
    if (!s.nclip) return true;
    for (int i = 0; i < s.nclip; i++)
        if (x >= s.clip[i].left && x < s.clip[i].right && y >= s.clip[i].top && y < s.clip[i].bottom) return true;
    return false;
}
void raw_put(const surf &s, int x, int y, uint32_t v)
{
    if (!in_clip(s, x, y)) return;
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
/* the format a BITMAPINFOHEADER / BITMAPCOREHEADER asks for; false = not a valid DIB description */
struct fmt { int w, h, bpp, comp, planes; bool td, core; uint32_t m[3]; int nct; uint32_t hsz; };
bool read_fmt(const uint8_t *b, fmt &f, bool need_wh)
{
    f.hsz = u32(b);
    f.core = f.hsz == 12;   /* BITMAPCOREHEADER: 16-bit fields, RGBTRIPLE colour table, always BI_RGB */
    int32_t w, h;
    uint32_t used = 0;
    if (f.core) {
        w = u16(b + 4); h = (int16_t)u16(b + 6); f.planes = u16(b + 8); f.bpp = u16(b + 10); f.comp = 0;
    } else if (f.hsz >= 40) {
        w = (int32_t)u32(b + 4); h = (int32_t)u32(b + 8); f.planes = u16(b + 12); f.bpp = u16(b + 14); f.comp = (int)u32(b + 16); used = u32(b + 32);
    } else return false;
    f.td = h < 0; f.w = w; f.h = h < 0 ? -h : h;
    if (need_wh && (w <= 0 || h == 0 || h == INT32_MIN)) return false;
    if (f.bpp != 1 && f.bpp != 4 && f.bpp != 8 && f.bpp != 16 && f.bpp != 24 && f.bpp != 32) return false;
    if (f.comp == 0) { memcpy(f.m, f.bpp == 16 ? M16 : M32, sizeof f.m); }
    else if (f.comp == 3 && (f.bpp == 16 || f.bpp == 32)) { for (int i = 0; i < 3; i++) f.m[i] = u32(b + 40 + 4 * i); if (!masks_ok(f.m, f.bpp)) return false; }
    else return false;
    if (f.comp == 0 && (f.bpp == 24 || f.bpp <= 8)) memset(f.m, 0, sizeof f.m);
    f.nct = 0;
    if (f.bpp <= 8) { if (used > (1u << f.bpp)) return false; f.nct = used ? (int)used : 1 << f.bpp; }
    return true;
}
/* the default colour table of a depth (ref get_default_color_table): 1 bpp black and white, 4 bpp the 16 VGA colours in
 * the reference's order, 8 bpp the 256-colour table: the 20 system colours at both ends, between them the 8 x 8 x 4
 * (red, green, blue) lattice. 0x00RRGGBB */
uint32_t std_pal(int bpp, int i)
{
    static const uint32_t vga[16] = { 0, 0x800000, 0x008000, 0x808000, 0x000080, 0x800080, 0x008080, 0x808080, 0xC0C0C0, 0xFF0000,
                                      0x00FF00, 0xFFFF00, 0x0000FF, 0xFF00FF, 0x00FFFF, 0xFFFFFF };
    if (bpp == 1) return i ? 0xFFFFFF : 0;
    if (bpp == 4) return vga[i & 15];
    if (i < 10) return SYSPAL[i];
    if (i >= 246) return SYSPAL[10 + i - 246];
    return (uint32_t)(((i & 7) * 0x20) << 16 | (((i >> 3) & 7) * 0x20) << 8 | (i >> 6) * 0x40);
}
uint32_t new_bitmap(int w, int h, int bpp, bool td, bool dib, obj **out, uint8_t *ext = NULL, void *view = NULL)
{
    if (w <= 0 || h <= 0 || stride_of(w, bpp) > (1 << 30)) return 0;
    int64_t sz = stride_of(w, bpp) * h;
    if (sz > (1 << 30)) return 0;
    uint8_t *bits = ext ? ext : (uint8_t *)calloc((size_t)sz, 1);   /* ext: the bits live in a mapped view */
    if (!bits) return 0;
    obj *o = NULL;
    uint32_t hd = alloc(T_BMP, &o);
    if (!hd) { if (!ext) free(bits); return 0; }
    o->w = w; o->h = h; o->bpp = bpp; o->pitch = (int)stride_of(w, bpp); o->td = td; o->dib = dib; o->bits = bits; o->view = view;
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
    d->ts[GDI_TS_BKCOLOR] = 0xFFFFFF; d->ts[GDI_TS_BKMODE] = 2; d->ts[GDI_TS_MAPMODE] = 1;   /* white, OPAQUE, MM_TEXT */
    d->ts[GDI_TS_WEXTX] = d->ts[GDI_TS_WEXTY] = d->ts[GDI_TS_VEXTX] = d->ts[GDI_TS_VEXTY] = 1;
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
    if (!d || (d->kind != K_MEM && d->kind != K_DISP)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
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
/* CreateDIBSection (MSDN): the bits are system memory, or, with hSection, a view of that file-mapping object starting at
 * dwOffset (a multiple of 4; the view itself starts at the allocation granularity below it, and the bits are at the rest).
 * The view keeps the section referenced until the bitmap is deleted. The section must be writable (PAGE_READWRITE or
 * PAGE_WRITECOPY); a view that cannot be made fails the call. The header is a BITMAPINFOHEADER (or larger) or a
 * BITMAPCOREHEADER; BI_RGB, and BI_BITFIELDS for 16 and 32 bits. */
extern "C" HBITMAP CreateDIBSection(HDC hd, const void *bmi, UINT usage, void **ppv, HANDLE section, DWORD offset)
{
    Lk l;
    init_stock();
    if (ppv) *ppv = NULL;
    fmt f;
    const uint8_t *b = (const uint8_t *)bmi;
    offset = W32_DW(offset);
    if (!bmi || !ppv || usage > 1 || !read_fmt(b, f, true) || (usage == 1 && !get(hd, T_DC)) || f.planes != 1 || (section && (offset & 3))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    uint8_t *ext = NULL;
    void *view = NULL;
    if (section) {
        int64_t need = stride_of(f.w, f.bpp) * f.h;
        DWORD base = offset & ~0xFFFFu;   /* views start at the allocation granularity */
        view = MapViewOfFile(section, 0x2 /* FILE_MAP_WRITE */, 0, base, (SIZE_T)(offset - base + need));
        if (!view) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
        ext = (uint8_t *)view + (offset - base);
    }
    obj *o = NULL;
    uint32_t bh = new_bitmap(f.w, f.h, f.bpp, f.td, true, &o, ext, view);
    if (!bh) {
        if (view) UnmapViewOfFile(view);
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    o->nct = f.nct;
    memcpy(o->mask, f.m, sizeof o->mask);
    const uint8_t *tab = b + f.hsz;
    for (int i = 0; i < f.nct; i++) {
        if (usage == 1) { uint32_t ix = u16(tab + 2 * i); o->ct[i] = ix < 20 ? SYSPAL[ix] : 0; }   /* DIB_PAL_COLORS: the DC's palette entries */
        else if (f.core) o->ct[i] = (uint32_t)tab[3 * i + 2] << 16 | (uint32_t)tab[3 * i + 1] << 8 | tab[3 * i];   /* RGBTRIPLE */
        else o->ct[i] = u32(tab + 4 * i) & 0xFFFFFF;                                                              /* RGBQUAD */
    }
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

/* GetDIBits (MSDN; the reference's NtGdiGetDIBitsInternal is the data for what the text leaves open).
 * lpbi may be a BITMAPCOREHEADER or a BITMAPINFOHEADER (or larger): the header says what format the caller wants. The bitmap
 * may be selected into a DC. With no bits and biBitCount 0 the call describes the bitmap: width, height, planes, bit count
 * (BI_BITFIELDS for 16 and 32 bits), size; no colour table. Otherwise the bitmap is converted to the requested depth:
 * bottom-up (positive height) or top-down; scan lines counted from the bottom of the bitmap, `cScanLines` of them from
 * `uStartScan`, clamped to the header's height; lines the bitmap does not have are zero. The colour table is the
 * bitmap's own when the depths agree (DIB_RGB_COLORS), else the default table of the depth, or with DIB_PAL_COLORS the
 * DC's palette and the table written back is the indices 0.. . The return value is the number of scan lines copied
 * (bits), nonzero (no bits), or 0. Compressed (RLE) bits cannot be retrieved: asked for with a buffer the call fails; JPEG
 * and PNG never. */
extern "C" int GetDIBits(HDC hd, HBITMAP hb, UINT start, UINT lines, void *bits, void *bmi, UINT usage)
{
    Lk l;
    uint8_t *b = (uint8_t *)bmi;
    if (!b) return 0;
    start = W32_DW(start); lines = W32_DW(lines);
    uint32_t hsz = u32(b);
    bool core = hsz == 12;
    int32_t dw_, dh_;
    int dplanes, dbpp;
    uint32_t dcomp;
    if (core) { dw_ = u16(b + 4); dh_ = (int16_t)u16(b + 6); dplanes = u16(b + 8); dbpp = u16(b + 10); dcomp = 0; }
    else if (hsz >= 40) { dw_ = (int32_t)u32(b + 4); dh_ = (int32_t)u32(b + 8); dplanes = u16(b + 12); dbpp = u16(b + 14); dcomp = u32(b + 16); }
    else return 0;
    if (usage > 1) return 0;                                  /* DIB_RGB_COLORS, DIB_PAL_COLORS */
    if (bits && (dcomp == 4 || dcomp == 5)) return 0;         /* BI_JPEG, BI_PNG */
    obj *d = get(hd, T_DC), *bm = get(hb, T_BMP);
    if (!d) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    if (!bm) return 0;
    if (dh_ == INT32_MIN) return 0;
    int Hd = dh_ < 0 ? -dh_ : dh_, Hs = bm->h, Ws = bm->w;
    if (lines == 0 || start >= (UINT)Hd) bits = NULL;
    if (!bits && dbpp == 0) {   /* describe the bitmap */
        uint32_t scomp = (bm->bpp == 16 || bm->bpp == 32) ? 3 : 0;
        uint32_t simg = (uint32_t)((int64_t)bm->pitch * bm->h);
        if (core) { b[4] = (uint8_t)Ws; b[5] = (uint8_t)(Ws >> 8); b[6] = (uint8_t)Hs; b[7] = (uint8_t)(Hs >> 8); b[8] = 1; b[9] = 0; b[10] = (uint8_t)bm->bpp; b[11] = 0; }
        else {
            w32p(b + 4, (uint32_t)Ws); w32p(b + 8, (uint32_t)Hs); b[12] = 1; b[13] = 0; b[14] = (uint8_t)bm->bpp; b[15] = 0;
            w32p(b + 16, scomp); w32p(b + 20, simg); w32p(b + 24, 0); w32p(b + 28, 0); w32p(b + 32, 0); w32p(b + 36, 0);
        }
        return Hs;
    }
    if (dw_ <= 0 || dh_ == 0) return 0;
    switch (dcomp) {
    case 2: if (dbpp != 4 || dh_ < 0 || bits) return 0; break;            /* BI_RLE4, BI_RLE8: described, never retrieved */
    case 1: if (dbpp != 8 || dh_ < 0 || bits) return 0; break;
    case 3: if (dbpp != 16 && dbpp != 32) return 0; /* fall through */
    case 0:
        if (lines && !dplanes) return 0;
        if (dbpp != 1 && dbpp != 4 && dbpp != 8 && dbpp != 16 && dbpp != 24 && dbpp != 32) return 0;
        break;
    default: return 0;
    }
    /* the destination's format: depth, masks, colour table */
    bool same = dbpp == bm->bpp && usage == 0;
    uint32_t dm[3] = { 0, 0, 0 };
    int n = dbpp <= 8 ? 1 << dbpp : 0;
    uint32_t tab[256];
    int tabn = 0;                                             /* entries to copy back */
    if (dbpp == 16 || dbpp == 32) {
        if (same) memcpy(dm, bm->mask, sizeof dm);              /* the bitmap's own masks, whatever the caller asked for */
        else if (dcomp == 3) { for (int i = 0; i < 3; i++) dm[i] = u32(b + 40 + 4 * i); if (!masks_ok(dm, dbpp)) return 0; }
        else memcpy(dm, dbpp == 16 ? M16 : M32, sizeof dm);
    }
    if (same && dbpp <= 8) {
        tabn = bm->nct > 0 ? bm->nct : n;
        for (int i = 0; i < tabn; i++) tab[i] = bm->nct > 0 ? bm->ct[i] : std_pal(dbpp, i);
        for (int i = tabn; i < n; i++) tab[i] = 0;
    } else if (dbpp <= 8) {
        tabn = n;
        for (int i = 0; i < n; i++) tab[i] = usage == 1 ? (i < 20 ? SYSPAL[i] : 0) : std_pal(dbpp, i);
    }
    /* scan lines: the block of `lines` rows that ends `start` rows above the bottom, clamped to the requested height */
    int copied = 0;
    UINT cnt = lines;
    if (bits) {
        if (cnt > (UINT)Hd - start) cnt = (UINT)Hd - start;
        int y0 = Hd - (int)start - (int)cnt;                    /* bitmap row of the block's visually top row */
        int64_t pitch64 = stride_of(dw_, dbpp);
        if (pitch64 > (1 << 30)) return 0;
        int pitch = (int)pitch64, cw = dw_ < Ws ? dw_ : Ws;
        surf S;
        bm_surf(bm, S);
        surf T = S;
        T.bpp = dbpp; T.pitch = pitch; T.td = true; T.h = 1; T.w = dw_; T.nct = n; T.ct = tab; T.ddb = false; T.win = NULL; T.onwin = false; T.clip = NULL; T.nclip = 0;
        memcpy(T.m, dm, sizeof T.m);
        bool raw = same;                                        /* same depth, masks and table: the pixel values are copied */
        for (UINT r = 0; r < cnt; r++) {
            int y = y0 + (int)r;
            UINT m = dh_ > 0 ? cnt - 1 - r : r;                 /* memory order: bottom-up buffers hold the bottom row first */
            uint8_t *row = (uint8_t *)bits + (size_t)m * pitch;
            T.p = row;
            if (y < 0 || y >= Hs) { memset(row, 0, (size_t)pitch); continue; }   /* a line the bitmap does not have */
            for (int x = 0; x < cw; x++) {
                if (raw) raw_put(T, x, 0, raw_get(S, x, y));
                else px_put(T, x, 0, px_get(S, x, y));
            }
            copied++;
        }
    }
    /* write the format back: colour table or masks, biClrUsed 0, biSizeImage */
    if (core) {
        uint8_t *t = b + 12;
        if (usage == 1) for (int i = 0; i < tabn; i++) { t[2 * i] = (uint8_t)i; t[2 * i + 1] = (uint8_t)(i >> 8); }
        else for (int i = 0; i < tabn; i++) { t[3 * i] = (uint8_t)tab[i]; t[3 * i + 1] = (uint8_t)(tab[i] >> 8); t[3 * i + 2] = (uint8_t)(tab[i] >> 16); }
    } else {
        if (same && (dbpp == 16 || dbpp == 32)) for (int i = 0; i < 3; i++) w32p(b + 40 + 4 * i, dm[i]);   /* the masks are returned */
        uint8_t *t = b + hsz;
        if (dbpp <= 8 && usage == 1) for (int i = 0; i < n; i++) { t[2 * i] = (uint8_t)i; t[2 * i + 1] = (uint8_t)(i >> 8); }
        else if (dbpp <= 8) for (int i = 0; i < tabn; i++) w32p(t + 4 * i, tab[i] & 0xFFFFFF);
        w32p(b + 32, 0);
        w32p(b + 20, (uint32_t)(stride_of(dw_, dbpp) * Hd));
    }
    if (bits) return copied;
    return 1;
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
/* the DC's clip: nothing is written outside the rectangles (a DC without a clip writes everywhere) */
extern "C" void gdi_dc_set_clip(void *hdv, const RECT *r, int n)
{
    uint32_t hd = (uint32_t)(uintptr_t)hdv;
    Lk l;
    obj *d = get((void *)(uintptr_t)hd, T_DC);
    if (!d) return;
    free(d->clip);
    d->clip = NULL; d->nclip = 0;
    if (n > 0 && (d->clip = (RECT *)malloc((size_t)n * sizeof(RECT)))) { memcpy(d->clip, r, (size_t)n * sizeof(RECT)); d->nclip = n; }
}
/* GetClipBox of a window or paint DC: the bounding box of the clip, else the surface (client area) */
extern "C" int gdi_dc_clipbox(void *hd, RECT *out)
{
    Lk l;
    obj *d = get(hd, T_DC);
    surf s;
    if (!d || !out || !dc_surf(d, s)) return 0;
    RECT b = { 0, 0, s.w, s.h };
    if (s.nclip) {
        RECT u = s.clip[0];
        for (int i = 1; i < s.nclip; i++) {
            if (s.clip[i].left < u.left) u.left = s.clip[i].left;
            if (s.clip[i].top < u.top) u.top = s.clip[i].top;
            if (s.clip[i].right > u.right) u.right = s.clip[i].right;
            if (s.clip[i].bottom > u.bottom) u.bottom = s.clip[i].bottom;
        }
        if (u.left > b.left) b.left = u.left;
        if (u.top > b.top) b.top = u.top;
        if (u.right < b.right) b.right = u.right;
        if (u.bottom < b.bottom) b.bottom = u.bottom;
        if (b.right < b.left) b.right = b.left;
        if (b.bottom < b.top) b.bottom = b.top;
    }
    *out = b;
    return 1;
}
/* the system colours of the reference environment (win32u sysparams system_colors), COLORREF, index COLOR_SCROLLBAR..COLOR_MENUBAR */
static const uint32_t k_syscolor[31] = {
    0xC8D0D4, 0xA56E3A, 0x6A240A, 0x808080, 0xC8D0D4, 0xFFFFFF, 0x000000, 0x000000, 0x000000, 0xFFFFFF, 0xC8D0D4, 0xC8D0D4,
    0x808080, 0x6A240A, 0xFFFFFF, 0xC8D0D4, 0x808080, 0x808080, 0x000000, 0xC8D0D4, 0xFFFFFF, 0x404040, 0xC8D0D4, 0x000000,
    0xE1FFFF, 0xB5B5B5, 0xC80000, 0xF0CAA6, 0xC0C0C0, 0x6A240A, 0xC8D0D4 };
/* the system colours of the reference environment, and the shared brush of each (GetSysColor, GetSysColorBrush) */
extern "C" COLORREF gdi_syscolor(int i) { return i >= 0 && i < 31 ? (COLORREF)k_syscolor[i] : 0; }
extern "C" HBRUSH gdi_sys_brush(int i)
{
    static uint32_t brush[31];
    if (i < 0 || i >= 31) return NULL;
    Lk l;
    init_stock();
    if (!brush[i]) {
        obj *o = NULL;
        brush[i] = alloc(T_BRUSH, &o);
        if (!brush[i]) return NULL;
        o->stock = 1;   /* shared: never deleted */
        o->color = k_syscolor[i];
    }
    return (HBRUSH)(uintptr_t)brush[i];
}
/* FillRect for the platform's own drawing: hbr is a brush object or a system colour index + 1 (a class background) */
extern "C" int gdi_fill_rect(void *hd, const RECT *rc, void *hbr)
{
    Lk l;
    obj *d = get(hd, T_DC);
    surf s;
    if (!d || !rc || !dc_surf(d, s)) return 0;
    uint32_t col;
    uintptr_t v = (uintptr_t)hbr;
    if (v >= 1 && v <= 31) col = k_syscolor[v - 1];
    else {
        obj *b = get(hbr, T_BRUSH);
        if (!b) return 0;
        if (b->hollow) return 1;
        col = b->color;
    }
    int x0 = rc->left > 0 ? (int)rc->left : 0, y0 = rc->top > 0 ? (int)rc->top : 0;
    int x1 = rc->right < s.w ? (int)rc->right : s.w, y1 = rc->bottom < s.h ? (int)rc->bottom : s.h;
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++) px_put(s, x, y, swp(col & 0xFFFFFF));
    if (x1 > x0 && y1 > y0) dirty(s, x0, y0, x1, y1);
    return 1;
}
/* BeginPaint (MSDN, reference user32): WM_NCPAINT when the frame is pending, rcPaint = the update region clipped to the
 * client area (less the clipped children), the region is validated, the DC is clipped to it, and WM_ERASEBKGND is sent
 * when the region was marked for erasing: fErase is TRUE when the procedure did not erase. */
extern "C" HDC BeginPaint(HWND hw, void *psv)
{
    fnvnx_paintstruct *ps = (fnvnx_paintstruct *)psv;
    if (!ps) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (!hw || !IsWindow(hw)) { SetLastError(1400); return NULL; }
    if (fnwin_take_frame_paint(hw))
        SendMessageA(hw, 0x0085, 1, 0);   /* WM_NCPAINT, the whole frame */
    HDC hd;
    {
        Lk l;
        hd = (HDC)(uintptr_t)new_dc(K_PAINT, hw);
    }
    if (!hd) return NULL;
    int n = 0, erase = 0;
    RECT bb = { 0, 0, 0, 0 };
    RECT *rects = fnwin_begin_paint(hw, &n, &bb, &erase);   /* malloc'd; the update region is validated */
    memset(ps, 0, sizeof *ps);
    ps->hdc = hd; ps->rcPaint = bb;
    {   /* an empty update region clips everything away: one empty rectangle */
        RECT none = { 0, 0, 0, 0 };
        gdi_dc_set_clip((void *)hd, n ? rects : &none, n ? n : 1);
    }
    free(rects);
    if (erase)
        ps->fErase = SendMessageA(hw, 0x0014, (WPARAM)(uintptr_t)hd, 0) == 0;   /* WM_ERASEBKGND */
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

/* ---- interface for gdifont.cpp (gdi_int.h): object queries and font objects */
extern "C" int gdi_obj_info(void *h, struct gdi_objinfo *oi)
{
    Lk l;
    init_stock();
    obj *o = get(h, 0);
    memset(oi, 0, sizeof *oi);
    oi->stock = -1;
    if (!o) return 0;
    oi->kind = o->type;
    if (o->stock) oi->stock = (int)((uint32_t)(uintptr_t)h & 0xFFFF) - 1;
    oi->w = o->w; oi->h = o->h; oi->bpp = o->bpp; oi->pitch = o->pitch; oi->nct = o->nct;
    oi->dib = o->dib; oi->topdown = o->td; oi->bits = o->bits;
    oi->color = o->color; memcpy(oi->mask, o->mask, sizeof oi->mask); oi->hollow = o->hollow;
    oi->payload = o->fdata;
    return 1;
}
extern "C" uint32_t gdi_dc_font(void *hd)
{
    Lk l;
    init_stock();
    obj *d = get(hd, T_DC);
    return d ? d->font : 0;
}
extern "C" void *gdi_font_create(const void *lf92)
{
    Lk l;
    init_stock();
    obj *o = NULL;
    uint32_t h = alloc(T_FONT, &o);
    if (!h) return NULL;
    o->fdata = malloc(92);
    if (!o->fdata) { release(h); return NULL; }
    memcpy(o->fdata, lf92, 92);
    return (void *)(uintptr_t)h;
}
extern "C" int gdi_dc_state(void *hd, int field, int set, int32_t value, int32_t *prev)
{
    Lk l;
    init_stock();
    obj *d = get(hd, T_DC);
    if (!d || field < 0 || field >= GDI_TS_COUNT) return 0;
    if (prev) *prev = d->ts[field];
    if (set) d->ts[field] = value;
    return 1;
}
extern "C" uint32_t gdi_dc_object(void *hd, int kind)
{
    Lk l;
    init_stock();
    obj *d = get(hd, T_DC);
    if (!d) return 0;
    return kind == GDIK_BITMAP ? d->bmp : kind == GDIK_BRUSH ? d->brush : kind == GDIK_PEN ? d->pen : kind == GDIK_FONT ? d->font : kind == GDIK_PALETTE ? d->pal : 0;
}

/* ---- CreateDC / ExtEscape: the set of devices is exactly the display of ENV_TRUTH D1 */
static bool ieq(const char *a, const char *b) { for (; *a && *b; a++, b++) if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return false; return !*a && !*b; }
extern "C" HDC CreateDCA(LPCSTR driver, LPCSTR device, LPCSTR output, const void *devmode)
{
    W32_UNUSED(output); W32_UNUSED(devmode);   /* a display DC has no output port and always shows the desktop mode */
    bool disp = driver ? ieq(driver, "DISPLAY") : device != NULL;
    if (!disp || (device && !ieq(device, "\\\\.\\DISPLAY1"))) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    Lk l;
    HDC hd = (HDC)(uintptr_t)new_dc(K_DISP, NULL);
    if (!hd) SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return hd;
}
/* The display driver of the environment is not a vendor driver: it implements no escape. QUERYESCSUPPORT therefore
 * answers 0 ("not supported") for every code and every other escape is "not implemented" (0). Errors are < 0. */
extern "C" int ExtEscape(HDC hd, int esc, int cb_in, LPCSTR in, int cb_out, LPSTR out)
{
    {
        Lk l;
        if (!get(hd, T_DC)) { SetLastError(ERROR_INVALID_HANDLE); return -1; }
    }
    if (cb_in < 0 || cb_out < 0 || (cb_in > 0 && !in) || (cb_out > 0 && !out) || (esc == 8 /* QUERYESCSUPPORT */ && (!in || cb_in < 2))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return -1;
    }
    return 0;
}

/* 1: hd is a DC of the display (screen, window, paint, CreateDC("DISPLAY")); 0: a memory DC; -1: not a DC (d3dkmt.cpp) */
extern "C" int gdi_dc_display(void *hd)
{
    Lk l;
    obj *d = get(hd, T_DC);
    return !d ? -1 : d->kind == K_MEM ? 0 : 1;
}

/* ---- text drawing side (gdi_int.h) */
extern "C" int gdi_dc_depth(void *hd)
{
    Lk l;
    obj *d = get(hd, T_DC);
    surf s;
    return d && dc_surf(d, s) ? s.bpp : 0;
}
extern "C" int gdi_dc_fill(void *hd, int l, int t, int r, int b, uint32_t c)
{
    Lk lk;
    obj *d = get(hd, T_DC);
    surf s;
    if (!d || !dc_surf(d, s)) return 0;
    if (l < 0) l = 0;
    if (t < 0) t = 0;
    if (r > s.w) r = s.w;
    if (b > s.h) b = s.h;
    for (int y = t; y < b; y++) for (int x = l; x < r; x++) px_put(s, x, y, swp(c & 0xFFFFFF));
    if (r > l && b > t) dirty(s, l, t, r, b);
    return 1;
}
namespace {
/* the 17 glyph levels: intensity of a glyph level drawn with component 0xff on black */
const uint8_t RAMP[17] = { 0, 0x4d, 0x68, 0x7c, 0x8c, 0x9a, 0xa7, 0xb2, 0xbd, 0xc7, 0xd0, 0xd9, 0xe1, 0xe9, 0xf0, 0xf8, 0xff };
uint8_t aa_comp(uint8_t dst, uint8_t text, int lvl)
{
    if (dst == text) return dst;
    int mn = RAMP[lvl] * text / 255, mx = RAMP[16 - lvl] + (255 - RAMP[16 - lvl]) * text / 255;
    if (dst > text) return (uint8_t)(text + (dst - text) * (mx - text) / (255 - text));
    return (uint8_t)(text - (text - dst) * (text - mn) / text);
}
}
extern "C" int gdi_dc_glyph(void *hd, int x, int y, int w, int h, const uint8_t *cov, int pitch, uint32_t c, const int *clip)
{
    Lk lk;
    obj *d = get(hd, T_DC);
    surf s;
    if (!d || !dc_surf(d, s)) return 0;
    int l = x, t = y, r = x + w, b = y + h;
    if (clip) { l = l > clip[0] ? l : clip[0]; t = t > clip[1] ? t : clip[1]; r = r < clip[2] ? r : clip[2]; b = b < clip[3] ? b : clip[3]; }
    l = l > 0 ? l : 0; t = t > 0 ? t : 0; r = r < s.w ? r : s.w; b = b < s.h ? b : s.h;
    uint32_t text = swp(c & 0xFFFFFF);
    for (int py = t; py < b; py++)
        for (int px = l; px < r; px++) {
            int v = cov[(size_t)(py - y) * pitch + (px - x)];
            if (v <= 1) continue;
            if (v >= 16 || s.bpp <= 8) { if (v >= 16) px_put(s, px, py, text); continue; }   /* <= 8 bpp: no antialiasing, 0 or solid */
            uint32_t o = px_get(s, px, py);
            px_put(s, px, py, (uint32_t)aa_comp((uint8_t)(o >> 16), (uint8_t)(text >> 16), v) << 16 | (uint32_t)aa_comp((uint8_t)(o >> 8), (uint8_t)(text >> 8), v) << 8 | aa_comp((uint8_t)o, (uint8_t)text, v));
        }
    if (r > l && b > t) dirty(s, l, t, r, b);
    return 1;
}
extern "C" int gdi_dc_poly(void *hd, const int32_t *xy, int n, uint32_t c)
{
    Lk lk;
    obj *d = get(hd, T_DC);
    surf s;
    if (!d || !dc_surf(d, s)) return 0;
    if (n < 3) return 1;
    int ymin = xy[1], ymax = xy[1];
    for (int i = 1; i < n; i++) { if (xy[2 * i + 1] < ymin) ymin = xy[2 * i + 1]; if (xy[2 * i + 1] > ymax) ymax = xy[2 * i + 1]; }
    if (ymin < 0) ymin = 0;
    if (ymax > s.h) ymax = s.h;
    for (int py = ymin; py < ymax; py++) {
        double yc = py + 0.5, xs[64];
        int k = 0;
        for (int i = 0; i < n && k < 63; i++) {   /* edge i -> i+1, half-open in y so a vertex is counted once */
            double x0 = xy[2 * i], y0 = xy[2 * i + 1], x1 = xy[2 * ((i + 1) % n)], y1 = xy[2 * ((i + 1) % n) + 1];
            if ((y0 <= yc) == (y1 <= yc)) continue;
            xs[k++] = x0 + (yc - y0) * (x1 - x0) / (y1 - y0);
        }
        for (int i = 1; i < k; i++) for (int j = i; j > 0 && xs[j] < xs[j - 1]; j--) { double q = xs[j]; xs[j] = xs[j - 1]; xs[j - 1] = q; }
        for (int i = 0; i + 1 < k; i += 2) {
            int x0 = (int)ceil(xs[i] - 0.5), x1 = (int)ceil(xs[i + 1] - 0.5);
            if (x0 < 0) x0 = 0;
            if (x1 > s.w) x1 = s.w;
            for (int px = x0; px < x1; px++) px_put(s, px, py, swp(c & 0xFFFFFF));
            if (x1 > x0) dirty(s, x0, py, x1, py + 1);
        }
    }
    return 1;
}

/* ---- a memory DC whose bitmap IS the caller's pixels (nxd3d_dc_create, render_hooks.cpp): a top-down DIB of
 * bpp 16 / 24 / 32 with the given channel masks (zero for 24), rows `pitch` bytes apart (every primitive reads the
 * bitmap's stored pitch). The memory stays the caller's: it is never freed here and outlives the DC. 0 = bad
 * arguments (bpp, pitch below one row, size over 1 GiB). */
extern "C" HDC fnvnx_gdi_memdc_over_bits(void *mem, int w, int h, int bpp, const uint32_t *masks, size_t pitch)
{
    Lk l;
    init_stock();
    if (!mem || w <= 0 || h <= 0 || (bpp != 16 && bpp != 24 && bpp != 32) || !masks) return NULL;
    if (pitch < (size_t)(((int64_t)w * bpp + 7) / 8) || pitch > (1u << 30) || (int64_t)pitch * h > (1 << 30)) return NULL;
    obj *b = NULL;
    uint32_t bh = alloc(T_BMP, &b);
    if (!bh) return NULL;
    b->w = w; b->h = h; b->bpp = bpp; b->pitch = (int)pitch; b->td = true; b->dib = true; b->bits = (uint8_t *)mem; b->ext = true;
    memcpy(b->mask, masks, sizeof b->mask);
    uint32_t dh = new_dc(K_MEM, NULL);
    if (!dh) { release(bh); return NULL; }
    obj *d = get((void *)(uintptr_t)dh, T_DC);
    if (obj *p = get((void *)(uintptr_t)d->bmp, T_BMP)) deselect(p);
    b->selin = dh;
    d->bmp = d->xbmp = bh;
    return (HDC)(uintptr_t)dh;
}
/* ends a DC of memdc_over_bits together with its bitmap; FALSE (nothing done) for any other handle */
extern "C" BOOL fnvnx_gdi_memdc_over_bits_release(HDC hd)
{
    Lk l;
    obj *d = get(hd, T_DC);
    if (!d || d->kind != K_MEM || !d->xbmp) return FALSE;
    uint32_t bh = d->xbmp;
    free_dc((uint32_t)(uintptr_t)hd, d);
    obj *b = get((void *)(uintptr_t)bh, T_BMP);
    if (b && !b->selin) release(bh);
    return TRUE;
}
