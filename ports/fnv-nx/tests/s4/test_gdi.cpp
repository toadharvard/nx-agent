/* test_gdi.cpp - platform/win32/gdi32.cpp: handle table, stock objects, DCs, bitmaps and DIB sections, pixels,
 * StretchBlt (all 256 ROP3 + modes), GetDIBits, GetDeviceCaps, window DCs. Structures are written with fixed 32-bit
 * layouts (the guest's), so the tests hold on LP64 hosts where LONG/DWORD are 64-bit. */
#include "s4_test.h"

#include <stdint.h>
#include <stdlib.h>

extern "C" {
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
HBITMAP CreateCompatibleBitmap(HDC, int, int);
HBITMAP CreateDIBSection(HDC, const void *, UINT, void **, HANDLE, DWORD);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
int GetDIBits(HDC, HBITMAP, UINT, UINT, void *, void *, UINT);
COLORREF GetPixel(HDC, int, int);
COLORREF SetPixel(HDC, int, int, COLORREF);
BOOL StretchBlt(HDC, int, int, int, int, HDC, int, int, int, int, DWORD);
int SetStretchBltMode(HDC, int);
int GetDeviceCaps(HDC, int);
HDC GetDC(HWND);
int ReleaseDC(HWND, HDC);
HDC BeginPaint(HWND, void *);
BOOL fnwin_invalidate(HWND, const RECT *, int);
BOOL EndPaint(HWND, const void *);
int fnvnx_window_surface(HWND, void **pixels, int *w, int *h, int *pitch);
}

struct tbih { uint32_t size; int32_t w, h; uint16_t planes, bc; uint32_t comp, simg; int32_t xp, yp; uint32_t used, imp; };
struct tbmi { tbih h; uint32_t t[256]; };
struct tps { HDC hdc; int fErase; RECT rc; int fRestore, fInc; uint8_t rsv[32]; };

static bool inv(COLORREF c) { return (c & 0xFFFFFFFFu) == 0xFFFFFFFFu; }
static uint32_t swp(uint32_t x) { return ((x & 0xFF) << 16) | (x & 0xFF00) | ((x >> 16) & 0xFF); } /* xrgb <-> COLORREF */
static HGDIOBJ SO(int i) { return GetStockObject(i); }

static void mkbmi(tbmi &b, int w, int h, int bpp, uint32_t comp = 0)
{
    memset(&b, 0, sizeof b);
    b.h.size = 40; b.h.w = w; b.h.h = h; b.h.planes = 1; b.h.bc = (uint16_t)bpp; b.h.comp = comp;
}
struct dib { HBITMAP bm; HDC dc; HGDIOBJ prev; uint8_t *bits; int w, h, bpp, stride; bool td; };
static dib mkdib(int w, int h, int bpp, bool td, const uint32_t *tab = NULL, int n = 0)
{
    tbmi b; mkbmi(b, w, td ? -h : h, bpp);
    for (int i = 0; i < n; i++) b.t[i] = tab[i];
    dib d = { NULL, NULL, NULL, NULL, w, h, bpp, ((w * bpp + 31) / 32) * 4, td };
    void *bits = NULL;
    d.bm = CreateDIBSection(NULL, &b, 0, &bits, NULL, 0);
    d.bits = (uint8_t *)bits;
    d.dc = CreateCompatibleDC(NULL);
    d.prev = SelectObject(d.dc, d.bm);
    return d;
}
static void freedib(dib &d) { SelectObject(d.dc, d.prev); DeleteDC(d.dc); DeleteObject(d.bm); }
static uint32_t rawget(const dib &d, int x, int y)
{
    const uint8_t *r = d.bits + (size_t)(d.td ? y : d.h - 1 - y) * d.stride;
    switch (d.bpp) {
    case 1: return (r[x >> 3] >> (7 - (x & 7))) & 1;
    case 4: return (x & 1) ? r[x >> 1] & 15 : r[x >> 1] >> 4;
    case 8: return r[x];
    case 16: { uint16_t v; memcpy(&v, r + 2 * x, 2); return v; }
    case 24: return r[3 * x] | (uint32_t)r[3 * x + 1] << 8 | (uint32_t)r[3 * x + 2] << 16;
    default: { uint32_t v; memcpy(&v, r + 4 * x, 4); return v; }
    }
}
static void rawset(dib &d, int x, int y, uint32_t v)
{
    uint8_t *r = d.bits + (size_t)(d.td ? y : d.h - 1 - y) * d.stride;
    switch (d.bpp) {
    case 1: r[x >> 3] = (uint8_t)((r[x >> 3] & ~(0x80 >> (x & 7))) | (v << (7 - (x & 7)))); break;
    case 4: r[x >> 1] = (uint8_t)((x & 1) ? (r[x >> 1] & 0xF0) | v : (r[x >> 1] & 0x0F) | (v << 4)); break;
    case 8: r[x] = (uint8_t)v; break;
    case 16: { uint16_t s = (uint16_t)v; memcpy(r + 2 * x, &s, 2); break; }
    case 24: r[3 * x] = (uint8_t)v; r[3 * x + 1] = (uint8_t)(v >> 8); r[3 * x + 2] = (uint8_t)(v >> 16); break;
    default: memcpy(r + 4 * x, &v, 4);
    }
}
static void put(dib &d, int x, int y, uint32_t xrgb) { SetPixel(d.dc, x, y, swp(xrgb)); }
static uint32_t get(dib &d, int x, int y) { return swp((uint32_t)GetPixel(d.dc, x, y) & 0xFFFFFF); }

TEST(gdi_stock_objects)
{
    HGDIOBJ seen[20];
    for (int i = 0; i < 20; i++) {
        seen[i] = GetStockObject(i);
        if (i == 9) { CHECK(seen[i] == NULL); continue; }   /* 9 is not a stock index */
        CHECK(seen[i] != NULL);
        CHECK(((uintptr_t)seen[i] >> 32) == 0);  /* opaque 32-bit value, not a pointer */
        CHECK(GetStockObject(i) == seen[i]);
        for (int j = 0; j < i; j++) CHECK(seen[j] != seen[i]);
        CHECK(DeleteObject(seen[i]));
        CHECK(DeleteObject(seen[i]));
        CHECK(GetStockObject(i) == seen[i]);
    }
    CHECK(GetStockObject(-1) == NULL && GetStockObject(20) == NULL && GetStockObject(1000) == NULL);
    /* documented properties: brush colours through a 1x1 pattern blit (PATCOPY) */
    static const uint32_t col[] = { 0xFFFFFF, 0xC0C0C0, 0x808080, 0x404040, 0x000000 };
    dib d = mkdib(1, 1, 32, true);
    for (int i = 0; i < 5; i++) {
        SelectObject(d.dc, SO(i));
        CHECK(StretchBlt(d.dc, 0, 0, 1, 1, NULL, 0, 0, 0, 0, 0x00F00021));
        CHECK_EQ(get(d, 0, 0), col[i]);
    }
    put(d, 0, 0, 0x123456);
    SelectObject(d.dc, SO(5)); /* NULL_BRUSH draws nothing */
    CHECK(StretchBlt(d.dc, 0, 0, 1, 1, NULL, 0, 0, 0, 0, 0x00F00021));
    CHECK_EQ(get(d, 0, 0), 0x123456);
    SelectObject(d.dc, SO(18)); /* DC_BRUSH default white */
    CHECK(StretchBlt(d.dc, 0, 0, 1, 1, NULL, 0, 0, 0, 0, 0x00F00021));
    CHECK_EQ(get(d, 0, 0), 0xFFFFFF);
    freedib(d);
}

TEST(gdi_handle_validation)
{
    HDC dc = CreateCompatibleDC(NULL);
    CHECK(dc != NULL && ((uintptr_t)dc >> 32) == 0);
    CHECK(!DeleteObject(dc));                  /* a DC is not a GDI object */
    CHECK(!DeleteDC((HDC)SO(0)));              /* wrong type */
    CHECK(!DeleteDC(NULL) && !DeleteDC((HDC)(uintptr_t)0x1234));
    CHECK(!DeleteObject(NULL) && !DeleteObject((HGDIOBJ)(uintptr_t)0x1234));
    CHECK(CreateCompatibleDC((HDC)SO(0)) == NULL);
    CHECK(CreateCompatibleDC((HDC)(uintptr_t)0x777) == NULL);
    HDC dc2 = CreateCompatibleDC(dc);
    CHECK(dc2 != NULL && dc2 != dc);
    CHECK(DeleteDC(dc2));
    CHECK(!DeleteDC(dc2));
    CHECK(SelectObject(dc2, SO(0)) == NULL);
    CHECK(inv(GetPixel(dc2, 0, 0)) && inv(SetPixel(dc2, 0, 0, 0)));
    CHECK_EQ(GetDeviceCaps(dc2, 12), 0);
    HDC dc3 = CreateCompatibleDC(dc);            /* a reused slot never revives the dead handle */
    CHECK(dc3 != dc2);
    CHECK(!DeleteDC(dc2));
    CHECK(DeleteDC(dc3));
    CHECK(SelectObject(dc, (HGDIOBJ)dc) == NULL);
    CHECK(SelectObject(dc, NULL) == NULL);
    CHECK(CreateCompatibleBitmap((HDC)SO(0), 4, 4) == NULL);
    CHECK(DeleteDC(dc));
}

TEST(gdi_compatible_bitmap)
{
    HDC dc = CreateCompatibleDC(NULL);
    HBITMAP b = CreateCompatibleBitmap(dc, 10, 5);        /* fresh memory DC: 1x1 monochrome surface */
    CHECK(b != NULL);
    HGDIOBJ stock = SelectObject(dc, b);
    CHECK(stock != NULL && DeleteObject(stock));          /* the stock 1x1 bitmap: TRUE, nothing happens */
    CHECK_EQ(GetDeviceCaps(dc, 12), 1);
    CHECK_EQ(GetDeviceCaps(dc, 8), 10);
    CHECK_EQ(GetDeviceCaps(dc, 10), 5);
    CHECK_EQ(SetPixel(dc, 0, 0, 0xFFFFFF), 0xFFFFFF);
    CHECK_EQ(GetPixel(dc, 0, 0), 0xFFFFFF);
    CHECK_EQ(SetPixel(dc, 1, 1, 0x808080), 0xFFFFFF);     /* nearest of black / white */
    CHECK_EQ(SetPixel(dc, 2, 2, 0x7F7F7F), 0);
    CHECK_EQ(GetPixel(dc, 9, 4), 0);
    CHECK(inv(GetPixel(dc, 10, 0)) && inv(GetPixel(dc, 0, 5)) && inv(GetPixel(dc, -1, 0)) && inv(SetPixel(dc, 0, -1, 0)));
    CHECK(SelectObject(dc, stock) == b);
    CHECK(DeleteObject(b));
    CHECK(!DeleteObject(b));
    HBITMAP z = CreateCompatibleBitmap(dc, 0, 7);         /* zero size: 1x1 monochrome */
    CHECK(z != NULL);
    SelectObject(dc, z);
    CHECK_EQ(GetDeviceCaps(dc, 8), 1);
    CHECK(inv(GetPixel(dc, 1, 0)));
    SelectObject(dc, stock);
    DeleteObject(z);
    CHECK(CreateCompatibleBitmap(dc, -1, 5) == NULL && CreateCompatibleBitmap(dc, 5, -1) == NULL);
    CHECK(CreateCompatibleBitmap(dc, 0x7FFFFFFF, 0x7FFFFFFF) == NULL);
    CHECK(CreateCompatibleBitmap(NULL, 4, 4) == NULL);
    /* a bitmap compatible with a colour surface is colour */
    dib d = mkdib(3, 3, 24, false);
    HBITMAP c = CreateCompatibleBitmap(d.dc, 4, 4);
    HDC m = CreateCompatibleDC(d.dc);
    SelectObject(m, c);
    CHECK_EQ(GetDeviceCaps(m, 12), 24);
    CHECK_EQ(SetPixel(m, 3, 3, 0x102030), 0x102030);
    CHECK_EQ(GetPixel(m, 3, 3), 0x102030);
    CHECK_EQ(GetPixel(m, 0, 0), 0);
    DeleteDC(m); DeleteObject(c); freedib(d);
}

TEST(gdi_select_rules)
{
    HDC a = CreateCompatibleDC(NULL), b = CreateCompatibleDC(NULL);
    HBITMAP bm = CreateCompatibleBitmap(a, 4, 4);
    HGDIOBJ sa = SelectObject(a, bm);
    CHECK(sa != NULL);
    CHECK(SelectObject(b, bm) == NULL);                   /* one DC at a time */
    CHECK(SelectObject(a, bm) == bm);                     /* same DC again */
    CHECK(!DeleteObject(bm));                             /* selected */
    HGDIOBJ sb = SelectObject(b, sa);
    CHECK(sb != NULL && sb != sa);                        /* each DC has its own stock bitmap */
    CHECK(SelectObject(a, sb) == bm);
    CHECK(SelectObject(b, bm) != NULL);
    CHECK(SelectObject(a, bm) == NULL);                   /* now in b */
    CHECK(DeleteDC(b));                                   /* releases it */
    CHECK(SelectObject(a, bm) != NULL);
    SelectObject(a, sa);
    CHECK(DeleteObject(bm));
    CHECK(SelectObject(a, bm) == NULL);                   /* dead handle */
    /* brush, pen, font: previous object of the type */
    CHECK(SelectObject(a, SO(4)) == SO(0));               /* WHITE_BRUSH default */
    CHECK(SelectObject(a, SO(1)) == SO(4));
    CHECK(SelectObject(a, SO(7)) == SO(7));               /* BLACK_PEN default */
    CHECK(SelectObject(a, SO(6)) == SO(7));
    CHECK(SelectObject(a, SO(17)) == SO(13));             /* SYSTEM_FONT default */
    CHECK(SelectObject(a, SO(10)) == SO(17));
    CHECK(SelectObject(a, SO(15)) == NULL);               /* palettes go through SelectPalette */
    HDC scr = GetDC(NULL);
    HBITMAP sbm = CreateCompatibleBitmap(scr, 2, 2);
    CHECK(SelectObject(scr, sbm) == NULL);                /* only memory DCs take bitmaps */
    CHECK(!DeleteDC(scr));
    CHECK_EQ(ReleaseDC(NULL, scr), 1);
    CHECK(DeleteObject(sbm));
    CHECK(DeleteDC(a));
}

static void depth_case(int bpp, bool td)
{
    uint32_t tab[256];
    int n = bpp <= 8 ? 1 << bpp : 0;
    for (int i = 0; i < n; i++)
        tab[i] = bpp == 1 ? (i ? 0xFFFFFF : 0) : bpp == 4 ? (uint32_t)((i * 17) << 16 | (255 - i * 17) << 8 | ((i * 53) & 255))
                                                         : (uint32_t)(i << 16 | ((i * 7) & 255) << 8 | ((i * 13) & 255));
    const int w = 7, h = 5;
    dib d = mkdib(w, h, bpp, td, tab, n);
    CHECK(d.bm != NULL && d.bits != NULL);
    if (!d.bits) return;
    CHECK_EQ(GetDeviceCaps(d.dc, 12), bpp);
    CHECK_EQ(GetDeviceCaps(d.dc, 8), w);
    CHECK_EQ(GetDeviceCaps(d.dc, 10), h);
    auto gen = [&](int x, int y, uint32_t &col, uint32_t &raw) {
        if (n) { raw = (uint32_t)((x * 5 + y * 3 + 1) % n); col = swp(tab[raw]); }
        else if (bpp == 16) {
            uint32_t r5 = (x * 5 + y) & 31, g5 = (x * 3 + y * 7) & 31, b5 = (x + y * 11) & 31;
            raw = r5 << 10 | g5 << 5 | b5;
            col = ((r5 * 255 + 15) / 31) | ((g5 * 255 + 15) / 31) << 8 | ((b5 * 255 + 15) / 31) << 16;
        } else { uint32_t r = x * 30 + 1, g = y * 50 + 2, b = x * y + 3; col = r | g << 8 | b << 16; raw = swp(col); }
    };
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            uint32_t col, raw; gen(x, y, col, raw);
            CHECK_EQ(SetPixel(d.dc, x, y, col), col);
            CHECK_EQ(rawget(d, x, y), raw);
            CHECK_EQ(GetPixel(d.dc, x, y), col);
        }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            uint32_t col, raw; gen(w - 1 - x, h - 1 - y, col, raw);
            rawset(d, x, y, raw);
        }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            uint32_t col, raw; gen(w - 1 - x, h - 1 - y, col, raw);
            CHECK_EQ(GetPixel(d.dc, x, y), col);
        }
    CHECK(inv(GetPixel(d.dc, w, 0)) && inv(GetPixel(d.dc, 0, h)) && inv(GetPixel(d.dc, -1, 2)));
    uint32_t before = rawget(d, 0, 0);
    CHECK(inv(SetPixel(d.dc, w, h, 0xFFFFFF)));
    CHECK_EQ(rawget(d, 0, 0), before);
    freedib(d);
}

TEST(gdi_dib_depths_orientations)
{
    static const int bpps[] = { 1, 4, 8, 16, 24, 32 };
    for (int i = 0; i < 6; i++) { depth_case(bpps[i], false); depth_case(bpps[i], true); }
}

TEST(gdi_dib_bitfields_and_palcolors)
{
    tbmi b; mkbmi(b, 4, -2, 16, 3); b.t[0] = 0xF800; b.t[1] = 0x07E0; b.t[2] = 0x001F;
    void *bits = NULL;
    HBITMAP bm = CreateDIBSection(NULL, &b, 0, &bits, NULL, 0);
    CHECK(bm != NULL && bits);
    HDC dc = CreateCompatibleDC(NULL);
    SelectObject(dc, bm);
    CHECK_EQ(SetPixel(dc, 1, 1, swp(0xFF8040)), swp(0xFF8242));   /* the 5/6/5-bit approximation: 31, 32, 8 -> 255, 130, 66 */
    uint16_t v; memcpy(&v, (uint8_t *)bits + 1 * 8 + 2, 2);   /* top-down, stride 8 */
    CHECK_EQ(v, (31 << 11) | (32 << 5) | 8);
    DeleteDC(dc); DeleteObject(bm);
    mkbmi(b, 2, 2, 32, 3); b.t[0] = 0x3FF00000; b.t[1] = 0x000FFC00; b.t[2] = 0x000003FF;
    bm = CreateDIBSection(NULL, &b, 0, &bits, NULL, 0);
    CHECK(bm != NULL);
    dc = CreateCompatibleDC(NULL);
    SelectObject(dc, bm);
    CHECK_EQ(SetPixel(dc, 0, 1, swp(0xFF8040)), swp(0xFF8040));
    uint32_t v32; memcpy(&v32, bits, 4);                      /* bottom-up: y=1 is memory row 0 */
    CHECK_EQ(v32, (1023u << 20) | (514u << 10) | 257u);
    uint32_t sv = (200u << 20) | (100u << 10) | 50u; memcpy((uint8_t *)bits + 4, &sv, 4);
    CHECK_EQ(GetPixel(dc, 1, 1), swp((uint32_t)((200 * 255 + 511) / 1023) << 16 | ((100 * 255 + 511) / 1023) << 8 | ((50 * 255 + 511) / 1023)));
    DeleteDC(dc); DeleteObject(bm);
    /* DIB_PAL_COLORS: indices into the default palette (the 20 static colours) */
    mkbmi(b, 2, 1, 4);
    uint16_t *pi = (uint16_t *)b.t; pi[0] = 9; pi[1] = 19; pi[2] = 1;
    dc = CreateCompatibleDC(NULL);
    CHECK(CreateDIBSection(NULL, &b, 1, &bits, NULL, 0) == NULL);   /* needs a DC */
    bm = CreateDIBSection(dc, &b, 1, &bits, NULL, 0);
    CHECK(bm != NULL);
    SelectObject(dc, bm);
    ((uint8_t *)bits)[0] = 0x01;                                    /* pixels 0 and 1 */
    CHECK_EQ(GetPixel(dc, 0, 0), swp(0xA6CAF0));
    CHECK_EQ(GetPixel(dc, 1, 0), swp(0xFFFFFF));
    ((uint8_t *)bits)[0] = 0x20;
    CHECK_EQ(GetPixel(dc, 0, 0), swp(0x800000));
    DeleteDC(dc); DeleteObject(bm);
}

TEST(gdi_dib_invalid_arguments)
{
    tbmi b; void *bits = NULL;
    mkbmi(b, 4, 4, 32);
    CHECK(CreateDIBSection(NULL, &b, 0, &bits, NULL, 0) != NULL);
    CHECK(CreateDIBSection(NULL, &b, 0, &bits, (HANDLE)(uintptr_t)8, 0) == NULL);   /* hSection must be NULL */
    CHECK(CreateDIBSection(NULL, NULL, 0, &bits, NULL, 0) == NULL);
    CHECK(CreateDIBSection(NULL, &b, 0, NULL, NULL, 0) == NULL);
    CHECK(CreateDIBSection(NULL, &b, 5, &bits, NULL, 0) == NULL);
    CHECK(CreateDIBSection((HDC)(uintptr_t)3, &b, 1, &bits, NULL, 0) == NULL);
    int bad[][4] = { { 4, 4, 3, 0 }, { 4, 4, 0, 0 }, { 0, 4, 32, 0 }, { -4, 4, 32, 0 }, { 4, 0, 32, 0 }, { 4, 4, 8, 1 }, { 4, 4, 4, 2 },
                     { 4, 4, 24, 3 }, { 4, 4, 8, 3 }, { 4, 4, 32, 4 }, { 4, 4, 32, 5 }, { 4, 4, 32, 9 }, { 1 << 20, 1 << 20, 32, 0 } };
    for (auto &r : bad) { mkbmi(b, r[0], r[1], r[2], (uint32_t)r[3]); CHECK(CreateDIBSection(NULL, &b, 0, &bits, NULL, 0) == NULL); }
    mkbmi(b, 4, 4, 32); b.h.planes = 2;
    CHECK(CreateDIBSection(NULL, &b, 0, &bits, NULL, 0) == NULL);
    mkbmi(b, 4, 4, 8); b.h.used = 300;
    CHECK(CreateDIBSection(NULL, &b, 0, &bits, NULL, 0) == NULL);
    mkbmi(b, 4, 4, 32); b.h.size = 12;
    CHECK(CreateDIBSection(NULL, &b, 0, &bits, NULL, 0) == NULL);
    mkbmi(b, 4, 4, 16, 3); b.t[0] = 0xFF00; b.t[1] = 0x0FF0; b.t[2] = 0x1;      /* overlapping masks */
    CHECK(CreateDIBSection(NULL, &b, 0, &bits, NULL, 0) == NULL);
    b.t[0] = 0x5; b.t[1] = 0x2; b.t[2] = 0x10;                                   /* not contiguous */
    CHECK(CreateDIBSection(NULL, &b, 0, &bits, NULL, 0) == NULL);
}

static int rop_dep(int idx, int bit) /* does the ROP3 index depend on operand bit (4=P 2=S 1=D) */
{
    for (int k = 0; k < 8; k++) if (((idx >> k) & 1) != ((idx >> (k ^ bit)) & 1)) return 1;
    return 0;
}

TEST(gdi_rop3_all_256)
{
    dib d = mkdib(1, 1, 32, true), s = mkdib(1, 1, 32, true);
    for (int idx = 0; idx < 256; idx++) {
        DWORD rop = (DWORD)idx << 16 | 0x0020;
        for (int k = 0; k < 8; k++) {
            int P = k >> 2, S = (k >> 1) & 1, D = k & 1;
            SelectObject(d.dc, SO(P ? 0 : 4));
            put(d, 0, 0, D ? 0xFFFFFF : 0);
            put(s, 0, 0, S ? 0xFFFFFF : 0);
            CHECK(StretchBlt(d.dc, 0, 0, 1, 1, s.dc, 0, 0, 1, 1, rop));
            CHECK_EQ(get(d, 0, 0), ((idx >> k) & 1) ? 0xFFFFFF : 0);
        }
        /* source-less ROPs take no source DC; the others fail without one and leave the destination alone */
        put(d, 0, 0, 0x555555);
        BOOL r = StretchBlt(d.dc, 0, 0, 1, 1, NULL, 0, 0, 0, 0, rop);
        CHECK_EQ(r, rop_dep(idx, 2) ? 0 : 1);
        if (rop_dep(idx, 2)) CHECK_EQ(get(d, 0, 0), 0x555555);
        /* multi-bit: P = LTGRAY (0xC0C0C0) */
        SelectObject(d.dc, SO(1));
        uint32_t S = 0xA0B0C0, D = 0x0F5533, want = 0;
        for (int b = 0; b < 24; b++) {
            int k = ((0xC0C0C0 >> b) & 1) << 2 | ((S >> b) & 1) << 1 | ((D >> b) & 1);
            want |= (uint32_t)((idx >> k) & 1) << b;
        }
        put(d, 0, 0, D); put(s, 0, 0, S);
        CHECK(StretchBlt(d.dc, 0, 0, 1, 1, s.dc, 0, 0, 1, 1, rop));
        CHECK_EQ(get(d, 0, 0), want);
    }
    freedib(d); freedib(s);
}

static uint32_t sp(int x, int y) { return (uint32_t)(x * 8) << 16 | (uint32_t)(y * 8) << 8 | (uint32_t)((x + y) * 4); }

TEST(gdi_stretch_modes_mirror_clip)
{
    dib s = mkdib(4, 4, 32, true), d = mkdib(8, 8, 32, false);
    for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) put(s, x, y, sp(x, y));
    HDC dd = d.dc;
    CHECK_EQ(SetStretchBltMode(dd, 3), 1);               /* default BLACKONWHITE */
    CHECK_EQ(SetStretchBltMode(dd, 4), 3);
    CHECK_EQ(SetStretchBltMode(dd, 0), 0);
    CHECK_EQ(SetStretchBltMode(dd, 5), 0);
    CHECK_EQ(SetStretchBltMode(dd, -1), 0);
    CHECK_EQ(SetStretchBltMode((HDC)(uintptr_t)9, 1), 0);
    CHECK_EQ(SetStretchBltMode((HDC)SO(0), 1), 0);
    for (int mode = 1; mode <= 4; mode++) {              /* 2x up: every mode copies */
        SetStretchBltMode(dd, mode);
        CHECK(StretchBlt(dd, 0, 0, 8, 8, s.dc, 0, 0, 4, 4, 0x00CC0020));
        for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) CHECK_EQ(get(d, x, y), sp(x / 2, y / 2));
    }
    for (int mode = 1; mode <= 4; mode++) {              /* 2x down */
        SetStretchBltMode(dd, mode);
        CHECK(StretchBlt(dd, 0, 0, 2, 2, s.dc, 0, 0, 4, 4, 0x00CC0020));
        for (int y = 0; y < 2; y++) for (int x = 0; x < 2; x++) {
            uint32_t a = ~0u, o = 0, sum[3] = { 0, 0, 0 };
            for (int j = 0; j < 4; j++) {
                uint32_t p = sp(x * 2 + (j & 1), y * 2 + (j >> 1));
                a &= p; o |= p;
                for (int c = 0; c < 3; c++) sum[c] += (p >> (8 * c)) & 255;
            }
            uint32_t avg = ((sum[2] + 2) / 4) << 16 | ((sum[1] + 2) / 4) << 8 | ((sum[0] + 2) / 4);
            uint32_t want = mode == 1 ? (a & 0xFFFFFF) : mode == 2 ? o : mode == 3 ? sp(x * 2, y * 2) : avg;
            CHECK_EQ(get(d, x, y), want);
        }
    }
    SetStretchBltMode(dd, 3);
    CHECK(StretchBlt(dd, 4, 0, -4, 4, s.dc, 0, 0, 4, 4, 0x00CC0020));      /* mirror x */
    for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) CHECK_EQ(get(d, x, y), sp(3 - x, y));
    CHECK(StretchBlt(dd, 0, 0, 4, 4, s.dc, 0, 4, 4, -4, 0x00CC0020));      /* mirror y through the source */
    for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) CHECK_EQ(get(d, x, y), sp(x, 3 - y));
    CHECK(StretchBlt(dd, 4, 4, -4, -4, s.dc, 4, 4, -4, -4, 0x00CC0020));   /* both negative: no mirror */
    for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) CHECK_EQ(get(d, x, y), sp(x, y));
    /* clipping: destination */
    for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) put(d, x, y, 0x010203);
    CHECK(StretchBlt(dd, -2, -2, 4, 4, s.dc, 0, 0, 4, 4, 0x00CC0020));
    CHECK_EQ(get(d, 0, 0), sp(2, 2)); CHECK_EQ(get(d, 1, 1), sp(3, 3));
    CHECK_EQ(get(d, 2, 0), 0x010203); CHECK_EQ(get(d, 0, 2), 0x010203);
    CHECK(StretchBlt(dd, 6, 6, 4, 4, s.dc, 0, 0, 4, 4, 0x00CC0020));
    CHECK_EQ(get(d, 7, 7), sp(1, 1)); CHECK_EQ(get(d, 5, 5), 0x010203);
    CHECK(StretchBlt(dd, 100, 100, 4, 4, s.dc, 0, 0, 4, 4, 0x00CC0020));    /* wholly outside */
    CHECK(StretchBlt(dd, 0, 0, 2, 2, s.dc, 2, 2, 4, 4, 0x00CC0020));       /* source clipped: missing pixels untouched */
    CHECK_EQ(get(d, 0, 0), sp(2, 2)); CHECK_EQ(get(d, 1, 1), sp(3, 3));   /* (1,1) has no source: untouched */
    for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) put(d, x, y, 0x010203);
    CHECK(StretchBlt(dd, 0, 0, 4, 4, s.dc, 2, 2, 4, 4, 0x00CC0020));
    CHECK_EQ(get(d, 0, 0), sp(2, 2)); CHECK_EQ(get(d, 1, 1), sp(3, 3));
    CHECK_EQ(get(d, 2, 2), 0x010203); CHECK_EQ(get(d, 3, 0), 0x010203);
    /* invalid handles */
    CHECK(!StretchBlt((HDC)(uintptr_t)5, 0, 0, 1, 1, s.dc, 0, 0, 1, 1, 0x00CC0020));
    CHECK(!StretchBlt(dd, 0, 0, 1, 1, (HDC)(uintptr_t)5, 0, 0, 1, 1, 0x00CC0020));
    CHECK(!StretchBlt(dd, 0, 0, 1, 1, (HDC)SO(0), 0, 0, 1, 1, 0x00CC0020));
    /* overlapping copy inside one surface behaves like memmove */
    for (int x = 0; x < 4; x++) put(s, x, 0, 0x100 + x);
    CHECK(StretchBlt(s.dc, 1, 0, 3, 1, s.dc, 0, 0, 3, 1, 0x00CC0020));
    CHECK_EQ(get(s, 0, 0), 0x100); CHECK_EQ(get(s, 1, 0), 0x100); CHECK_EQ(get(s, 2, 0), 0x101); CHECK_EQ(get(s, 3, 0), 0x102);
    freedib(s); freedib(d);
}

TEST(gdi_mono_conversions)
{
    HDC m = CreateCompatibleDC(NULL);
    HBITMAP mb = CreateCompatibleBitmap(m, 2, 1);
    SelectObject(m, mb);
    SetPixel(m, 0, 0, 0); SetPixel(m, 1, 0, 0xFFFFFF);
    dib d = mkdib(2, 1, 32, true);
    CHECK(StretchBlt(d.dc, 0, 0, 2, 1, m, 0, 0, 2, 1, 0x00CC0020));
    CHECK_EQ(get(d, 0, 0), 0);                           /* 0 -> text colour (black) */
    CHECK_EQ(get(d, 1, 0), 0xFFFFFF);                    /* 1 -> background colour (white) */
    put(d, 0, 0, 0xFFFFFF); put(d, 1, 0, 0xFF0000);
    CHECK(StretchBlt(m, 0, 0, 2, 1, d.dc, 0, 0, 2, 1, 0x00CC0020));
    CHECK_EQ(GetPixel(m, 0, 0), 0xFFFFFF);               /* equal to the background colour -> 1 */
    CHECK_EQ(GetPixel(m, 1, 0), 0);
    freedib(d); DeleteDC(m); DeleteObject(mb);
}

TEST(gdi_getdibits_conversions)
{
    dib d = mkdib(5, 3, 32, false);
    static const uint32_t C[3][5] = { { 0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0x000000 },
                                      { 0xFFFFFF, 0xFF00FF, 0x00FFFF, 0xFF0000, 0x00FF00 },
                                      { 0x0000FF, 0x000000, 0xFFFFFF, 0xFFFF00, 0xFF00FF } };
    for (int y = 0; y < 3; y++) for (int x = 0; x < 5; x++) put(d, x, y, C[y][x]);
    tbmi b; mkbmi(b, 0, 0, 0);
    uint8_t buf[256];
    CHECK(GetDIBits(d.dc, d.bm, 0, 3, NULL, &b, 0) != 0);          /* selected into a DC: the reference answers (MSDN says "must not be", Windows does) */
    SelectObject(d.dc, d.prev);
    CHECK(GetDIBits(d.dc, d.bm, 0, 3, NULL, &b, 0) != 0);
    CHECK_EQ(b.h.w, 5); CHECK_EQ(b.h.h, 3); CHECK_EQ(b.h.bc, 32); CHECK_EQ(b.h.planes, 1); CHECK_EQ(b.h.comp, 3);   /* the description of a 16 or 32-bit bitmap is BI_BITFIELDS (the reference); masks follow once the depth is asked for */
    CHECK_EQ(b.h.simg, 5 * 4 * 3);
    mkbmi(b, 5, 3, 24);
    memset(buf, 0xAA, sizeof buf);
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 0), 3);          /* bottom-up: line 0 is the bottom row */
    for (int ln = 0; ln < 3; ln++) for (int x = 0; x < 5; x++) {
        uint32_t c = C[2 - ln][x]; const uint8_t *p = buf + ln * 16 + x * 3;
        CHECK(p[0] == (c & 255) && p[1] == ((c >> 8) & 255) && p[2] == ((c >> 16) & 255));
    }
    mkbmi(b, 5, -3, 24);                                           /* top-down */
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 0), 3);
    CHECK_EQ(b.h.h, -3);
    CHECK(buf[0] == 0 && buf[1] == 0 && buf[2] == 0xFF);           /* C[0][0] = 0xFF0000 (xrgb red) */
    CHECK_EQ(GetDIBits(d.dc, d.bm, 1, 1, buf, &b, 0), 1);          /* one line: row 1 */
    CHECK(buf[0] == 0xFF && buf[1] == 0xFF && buf[2] == 0xFF);
    CHECK_EQ(GetDIBits(d.dc, d.bm, 2, 5, buf, &b, 0), 1);          /* clamped to the bitmap */
    CHECK_EQ(GetDIBits(d.dc, d.bm, 3, 1, buf, &b, 0), 1);          /* no such line: no bits, the header is described (the reference's answer is 1) */
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 0, buf, &b, 0), 1);          /* no lines: the same */
    mkbmi(b, 5, -3, 8);                                            /* 32 -> 8: colour table filled, nearest entries */
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 0), 3);
    for (int y = 0; y < 3; y++) for (int x = 0; x < 5; x++) CHECK_EQ(b.t[buf[y * 8 + x]] & 0xFFFFFF, C[y][x]);
    mkbmi(b, 5, -3, 1);
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 0), 3);
    CHECK_EQ(b.t[0] & 0xFFFFFF, 0); CHECK_EQ(b.t[1] & 0xFFFFFF, 0xFFFFFF);
    CHECK_EQ(buf[0] >> 7, 0); /* red (0xFF0000) is nearer black than white: 255^2 < 2*255^2 */
    CHECK_EQ(buf[4] >> 7, 1);       /* row 1 pixel 0 is white */
    mkbmi(b, 5, 3, 16);
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 0), 3);
    uint16_t w16; memcpy(&w16, buf, 2);                                          /* bottom line, pixel 0: blue */
    CHECK_EQ(w16, 0x001F);
    /* failures */
    mkbmi(b, 5, 3, 24);
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 2), 0);          /* usage other than DIB_RGB_COLORS, DIB_PAL_COLORS */
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 1), 3);          /* DIB_PAL_COLORS with a 24-bit target: no table to build */
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, NULL, 0), 0);
    CHECK_EQ(GetDIBits(d.dc, (HBITMAP)SO(0), 0, 3, buf, &b, 0), 0);
    CHECK_EQ(GetDIBits((HDC)(uintptr_t)4, d.bm, 0, 3, buf, &b, 0), 0);
    mkbmi(b, 5, 3, 7);
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 0), 0);
    mkbmi(b, 5, 3, 24, 1);
    CHECK_EQ(GetDIBits(d.dc, d.bm, 0, 3, buf, &b, 0), 0);
    /* a monochrome DDB converts too */
    HDC m = CreateCompatibleDC(NULL);
    HBITMAP mb = CreateCompatibleBitmap(m, 8, 2);
    HGDIOBJ ms = SelectObject(m, mb);
    SetPixel(m, 1, 0, 0xFFFFFF);
    SelectObject(m, ms);
    mkbmi(b, 8, -2, 24);
    CHECK_EQ(GetDIBits(m, mb, 0, 2, buf, &b, 0), 2);
    CHECK(buf[3] == 255 && buf[4] == 255 && buf[5] == 255 && buf[0] == 0 && buf[6] == 0);
    DeleteDC(m); DeleteObject(mb);
    DeleteObject(d.bm); DeleteDC(d.dc);
}

TEST(gdi_device_caps)
{
    HDC s = GetDC(NULL);
    int W = GetSystemMetrics(SM_CXSCREEN), H = GetSystemMetrics(SM_CYSCREEN);
    struct { int idx, v; } t[] = {
        { 0, 0x0400 }, { 2, 1 }, { 4, (W * 254 + 480) / 960 }, { 6, (H * 254 + 480) / 960 }, { 8, W }, { 10, H }, { 12, 32 },
        { 14, 1 }, { 16, -1 }, { 18, -1 }, { 20, 0 }, { 22, 0 }, { 24, -1 }, { 26, 0 }, { 28, 0x1FF }, { 30, 0xFE },
        { 32, 0xFF }, { 34, 0x6177 }, { 36, 1 }, { 38, 0x7E99 }, { 40, 36 }, { 42, 36 }, { 44, 51 }, { 88, 96 }, { 90, 96 },
        { 104, 0 }, { 106, 20 }, { 108, 24 }, { 110, 0 }, { 111, 0 }, { 112, 0 }, { 113, 0 }, { 114, 0 }, { 115, 0 },
        { 116, 60 }, { 117, H }, { 118, W }, { 119, 0 }, { 120, 0 }, { 121, 0 }, { 9999, 0 }, { -5, 0 }, { 3, 0 }, { 122, 0 },
    };
    for (auto &e : t) CHECK_EQ(GetDeviceCaps(s, e.idx), e.v);
    CHECK_EQ(GetDeviceCaps((HDC)(uintptr_t)8, 12), 0);
    CHECK_EQ(GetDeviceCaps((HDC)SO(0), 12), 0);
    ReleaseDC(NULL, s);
    dib d = mkdib(100, 50, 8, false);
    CHECK_EQ(GetDeviceCaps(d.dc, 8), 100); CHECK_EQ(GetDeviceCaps(d.dc, 10), 50); CHECK_EQ(GetDeviceCaps(d.dc, 12), 8);
    CHECK_EQ(GetDeviceCaps(d.dc, 24), 256); CHECK_EQ(GetDeviceCaps(d.dc, 14), 1); CHECK_EQ(GetDeviceCaps(d.dc, 88), 96);
    CHECK_EQ(GetDeviceCaps(d.dc, 4), (100 * 254 + 480) / 960);
    CHECK_EQ(GetDeviceCaps(d.dc, 118), W); CHECK_EQ(GetDeviceCaps(d.dc, 9999), 0);
    freedib(d);
}

TEST(gdi_window_dcs)
{
    WNDCLASSA wc; memset(&wc, 0, sizeof wc);
    wc.lpszClassName = "GdiTestCls";
    CHECK(RegisterClassA(&wc) != 0);
    HWND hw = CreateWindowExA(0, "GdiTestCls", "gdi", 0, 0, 0, 64, 48, NULL, NULL, NULL, NULL);
    CHECK(hw != NULL);
    RECT rc; CHECK(GetClientRect(hw, &rc));
    int cw = (int)rc.right, ch = (int)rc.bottom;
    HDC dc = GetDC(hw);
    CHECK(dc != NULL);
    CHECK_EQ(GetDeviceCaps(dc, 12), 32);
    CHECK_EQ(SetPixel(dc, 0, 0, 0x00112233), 0x00112233);
    CHECK_EQ(GetPixel(dc, 0, 0), 0x00112233);
    CHECK(inv(GetPixel(dc, cw, 0)) && inv(GetPixel(dc, 0, ch)) && inv(GetPixel(dc, -1, 0)));
    void *px = NULL; int w = 0, h = 0, pitch = 0;
    CHECK_EQ(fnvnx_window_surface(hw, &px, &w, &h, &pitch), 1);
    CHECK_EQ(w, cw); CHECK_EQ(h, ch); CHECK(pitch >= cw * 4 && px);
    if (px) { uint32_t v; memcpy(&v, px, 4); CHECK_EQ(v & 0xFFFFFF, 0x00332211); }
    dib d = mkdib(4, 4, 32, true);
    for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) put(d, x, y, sp(x, y));
    CHECK(StretchBlt(dc, 10, 12, 4, 4, d.dc, 0, 0, 4, 4, 0x00CC0020));
    CHECK_EQ(GetPixel(dc, 13, 15), swp(sp(3, 3)));
    CHECK(StretchBlt(d.dc, 0, 0, 4, 4, dc, 10, 12, 4, 4, 0x00CC0020));    /* and back */
    CHECK_EQ(get(d, 2, 1), sp(2, 1));
    CHECK_EQ(ReleaseDC(hw, dc), 1);
    CHECK_EQ(ReleaseDC(hw, dc), 0);
    CHECK(inv(GetPixel(dc, 0, 0)));
    HDC dc2 = GetDC(hw);
    CHECK_EQ(GetPixel(dc2, 0, 0), 0x00112233);                             /* the window's surface persists */
    CHECK(!DeleteDC(dc2));
    CHECK_EQ(ReleaseDC(NULL, dc2), 0);                                     /* wrong window */
    CHECK_EQ(ReleaseDC(hw, dc2), 1);
    tps ps; memset(&ps, 0xEE, sizeof ps);
    HDC pd = BeginPaint(hw, &ps);                                          /* a hidden window has no update region: rcPaint is empty */
    CHECK(pd != NULL && ps.hdc == pd);
    CHECK_EQ(ps.fErase, 0);
    CHECK(ps.rc.left == 0 && ps.rc.top == 0 && ps.rc.right == 0 && ps.rc.bottom == 0);
    CHECK(EndPaint(hw, &ps));
    fnwin_invalidate(hw, NULL, 0);                                         /* the client area is to be painted */
    memset(&ps, 0xEE, sizeof ps);
    pd = BeginPaint(hw, &ps);
    CHECK(pd != NULL && ps.hdc == pd);
    CHECK_EQ(ps.fErase, 0);
    CHECK(ps.rc.left == 0 && ps.rc.top == 0 && ps.rc.right == cw && ps.rc.bottom == ch);
    CHECK_EQ(GetPixel(pd, 0, 0), 0x00112233);
    CHECK_EQ(ReleaseDC(hw, pd), 0);                                        /* not from GetDC */
    CHECK(!DeleteDC(pd));
    CHECK(EndPaint(hw, &ps));
    CHECK(!EndPaint(hw, &ps));
    CHECK(inv(GetPixel(pd, 0, 0)));
    CHECK(BeginPaint((HWND)(uintptr_t)0x99, &ps) == NULL && BeginPaint(hw, NULL) == NULL);
    CHECK(GetDC((HWND)(uintptr_t)0x99) == NULL);
    CHECK_EQ(ReleaseDC(hw, (HDC)SO(0)), 0);
    /* the screen DC covers the desktop */
    HDC scr = GetDC(NULL);
    int W = GetSystemMetrics(SM_CXSCREEN), H = GetSystemMetrics(SM_CYSCREEN);
    CHECK(scr != NULL);
    CHECK_EQ(SetPixel(scr, W - 1, H - 1, 0x0A0B0C), 0x0A0B0C);
    CHECK_EQ(GetPixel(scr, W - 1, H - 1), 0x0A0B0C);
    CHECK(inv(GetPixel(scr, W, 0)) && inv(GetPixel(scr, 0, H)));
    CHECK_EQ(ReleaseDC(NULL, scr), 1);
    CHECK_EQ(GetDeviceCaps(dc2, 8), 0);                                    /* dead */
    freedib(d);
    DestroyWindow(hw);
}
