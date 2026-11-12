/* test_render_hooks.cpp - the platform side of the Direct3D 9 device layer's hook table (platform/win32/render_hooks.cpp,
 * shim/include/fnvnx_render_hooks.h; contract: render/d3d9/dev/platform_hooks.tsv). Cases rh_*. */
#include "s4_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <string>
#include <vector>

#include "fnvnx_render_hooks.h"
#include "../../platform/win32/gdi_int.h"

extern "C" {
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
COLORREF GetPixel(HDC, int, int);
COLORREF SetPixel(HDC, int, int, COLORREF);
HBITMAP CreateDIBSection(HDC, const void *, UINT, void **, HANDLE, DWORD);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
BOOL DeleteObject(HGDIOBJ);
BOOL StretchBlt(HDC, int, int, int, int, HDC, int, int, int, int, DWORD);
COLORREF SetTextColor(HDC, COLORREF);
int SetBkMode(HDC, int);
BOOL ExtTextOutA(HDC, int, int, UINT, const RECT *, const char *, UINT, const int *);
BOOL SetWindowPos(HWND, HWND, int, int, int, int, UINT);
}

static MEMORY_BASIC_INFORMATION Q(const void *p)
{
    MEMORY_BASIC_INFORMATION m;
    memset(&m, 0xEE, sizeof m);
    if (VirtualQuery(p, &m, sizeof m) != sizeof m) memset(&m, 0, sizeof m);
    return m;
}

/* ---- nxd3d_gpu_alloc / nxd3d_gpu_free */
TEST(rh_gpu_alloc_shape)
{
    struct { size_t ask, got; } t[] = { { 1, 4096 }, { 4095, 4096 }, { 4096, 4096 }, { 4097, 8192 }, { 65536, 65536 }, { 70000, 73728 }, { 0, 4096 } };
    for (auto &c : t) {
        void *p = nxd3d_gpu_alloc(c.ask);
        CHECK(p != NULL);
        if (!p) continue;
        CHECK_EQ((uintptr_t)p & 4095, 0);
        CHECK_EQ((uintptr_t)p & 65535, 0);   /* the page map's reservations are 64 KiB aligned */
        MEMORY_BASIC_INFORMATION m = Q(p);
        CHECK(m.BaseAddress == p && m.AllocationBase == p);
        CHECK_EQ(m.RegionSize, c.got);
        CHECK_EQ(m.State, MEM_COMMIT);
        CHECK_EQ(m.Type, MEM_PRIVATE);
        CHECK_EQ(m.Protect, PAGE_READWRITE);
        CHECK_EQ(m.AllocationProtect, PAGE_READWRITE);
        memset(p, 0x5A, c.got);                       /* all of it is usable */
        CHECK_EQ(((uint8_t *)p)[c.got - 1], 0x5A);
        nxd3d_gpu_free(p);
        CHECK_EQ(Q(p).State, MEM_FREE);
    }
}

TEST(rh_gpu_alloc_exhaustion_and_reuse)
{
    CHECK(nxd3d_gpu_alloc((size_t)0x80000000u) == NULL);   /* above the 2 GiB user range */
    CHECK(nxd3d_gpu_alloc((size_t)0x7FFF0000u) == NULL);   /* the whole range cannot be free */
    CHECK(nxd3d_gpu_alloc((size_t)-1) == NULL);
    CHECK(nxd3d_gpu_alloc((size_t)-1 - 4000) == NULL);
    void *a = nxd3d_gpu_alloc(8192), *b = nxd3d_gpu_alloc(8192);
    CHECK(a && b && a != b);
    if (!a || !b) return;
    CHECK((uintptr_t)a + 8192 <= (uintptr_t)b || (uintptr_t)b + 8192 <= (uintptr_t)a);
    memset(a, 1, 8192); memset(b, 2, 8192);
    CHECK_EQ(((uint8_t *)a)[8191], 1);
    nxd3d_gpu_free(a);
    CHECK_EQ(Q(a).State, MEM_FREE);
    CHECK_EQ(Q(b).State, MEM_COMMIT);
    CHECK_EQ(((uint8_t *)b)[0], 2);
    /* a pointer alloc did not return (interior, freed, null, not a heap block): no crash, memory untouched */
    nxd3d_gpu_free((uint8_t *)b + 4096);
    CHECK_EQ(Q(b).State, MEM_COMMIT);
    CHECK_EQ(Q(b).RegionSize, 8192);
    CHECK_EQ(((uint8_t *)b)[8191], 2);
    nxd3d_gpu_free(a);          /* double free */
    nxd3d_gpu_free(NULL);
    int local = 0;
    nxd3d_gpu_free(&local);
    CHECK_EQ(Q(b).State, MEM_COMMIT);
    nxd3d_gpu_free(b);
    CHECK_EQ(Q(b).State, MEM_FREE);
    void *c = nxd3d_gpu_alloc(4096);   /* the space can be used again */
    CHECK(c != NULL);
    nxd3d_gpu_free(c);
}

/* ---- device contexts over the caller's memory */
struct Fmt { uint32_t d3d; int bpp; uint32_t red, green, blue; };   /* raw pixel values of red, green, blue */
static const Fmt FMTS[] = {
    { 21, 4, 0x00FF0000, 0x0000FF00, 0x000000FF },   /* A8R8G8B8 */
    { 22, 4, 0x00FF0000, 0x0000FF00, 0x000000FF },   /* X8R8G8B8 */
    { 23, 2, 0xF800, 0x07E0, 0x001F },               /* R5G6B5 */
    { 24, 2, 0x7C00, 0x03E0, 0x001F },               /* X1R5G5B5 */
    { 25, 2, 0x7C00, 0x03E0, 0x001F },               /* A1R5G5B5 */
    { 20, 3, 0xFF0000, 0x00FF00, 0x0000FF },         /* R8G8B8 */
};
static uint32_t rawpx(const uint8_t *buf, size_t pitch, int bpp, int x, int y)
{
    const uint8_t *p = buf + (size_t)y * pitch + (size_t)x * bpp;
    uint32_t v = 0;
    for (int i = 0; i < bpp; i++) v |= (uint32_t)p[i] << (8 * i);
    return bpp == 4 ? v & 0xFFFFFF : v;   /* the alpha / unused byte of 32 bpp is not specified */
}
static bool pad_intact(const std::vector<uint8_t> &buf, size_t pitch, int bpp, int W, int H)
{
    for (int y = 0; y < H; y++)
        for (size_t i = (size_t)W * bpp; i < pitch; i++)
            if (buf[(size_t)y * pitch + i] != 0xAB) return false;
    return true;
}

TEST(rh_dc_formats_pitch)
{
    const int W = 9, H = 7;
    for (const Fmt &f : FMTS) {
        const size_t pitch = (size_t)W * f.bpp + 20;      /* larger than a row: rows are not packed */
        std::vector<uint8_t> buf(pitch * H, 0xAB);
        HDC dc = (HDC)nxd3d_dc_create(buf.data(), f.d3d, W, H, (uint32_t)pitch);
        CHECK(dc != NULL);
        if (!dc) continue;
        /* SetPixel: COLORREF is 0x00BBGGRR */
        SetPixel(dc, 1, 2, 0x000000FF);   /* red */
        SetPixel(dc, 8, 6, 0x0000FF00);   /* green, last pixel of the last row */
        SetPixel(dc, 0, 5, 0x00FF0000);   /* blue */
        CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 1, 2), f.red);
        CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 8, 6), f.green);
        CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 0, 5), f.blue);
        CHECK_EQ(GetPixel(dc, 1, 2), 0x000000FF);
        CHECK_EQ(GetPixel(dc, 8, 6), 0x0000FF00);
        CHECK_EQ(GetPixel(dc, 0, 5), 0x00FF0000);
        /* a fill of [2,7) x [1,5) */
        gdi_dc_fill(dc, 2, 1, 7, 5, 0x000000FF);
        bool fill_ok = true, out_ok = true;
        for (int y = 0; y < H; y++)
            for (int x = 0; x < W; x++) {
                bool in = x >= 2 && x < 7 && y >= 1 && y < 5;
                if (in && rawpx(buf.data(), pitch, f.bpp, x, y) != f.red) fill_ok = false;
                if (!in && !(x == 1 && y == 2) && !(x == 8 && y == 6) && !(x == 0 && y == 5))
                    for (int i = 0; i < f.bpp; i++)
                        if (buf[(size_t)y * pitch + (size_t)x * f.bpp + i] != 0xAB) out_ok = false;
            }
        CHECK(fill_ok);
        CHECK(out_ok);
        CHECK(pad_intact(buf, pitch, f.bpp, W, H));
        /* drawing outside the bitmap is clipped */
        gdi_dc_fill(dc, -5, -5, 50, 50, 0x00FFFFFF);
        CHECK(pad_intact(buf, pitch, f.bpp, W, H));
        CHECK_EQ(GetPixel(dc, 3, 3), 0x00FFFFFF);
        CHECK(GetPixel(dc, W, 0) == 0xFFFFFFFFu && GetPixel(dc, 0, H) == 0xFFFFFFFFu);
        /* a blit from another DC lands in the caller's memory at its pitch */
        HDC src = CreateCompatibleDC(NULL);
        uint32_t bmi[12] = { 40, 2, (uint32_t)-2, 0x00200001, 0, 0, 0, 0, 0, 0, 0, 0 };   /* 2x2 top-down 32 bpp */
        void *sbits = NULL;
        HBITMAP sb = CreateDIBSection(NULL, bmi, 0, &sbits, NULL, 0);
        CHECK(sb != NULL && sbits != NULL);
        if (sb && sbits) {
            HGDIOBJ prev = SelectObject(src, sb);
            SetPixel(src, 0, 0, 0x000000FF); SetPixel(src, 1, 0, 0x0000FF00); SetPixel(src, 0, 1, 0x00FF0000); SetPixel(src, 1, 1, 0x000000FF);
            CHECK(StretchBlt(dc, 3, 4, 2, 2, src, 0, 0, 2, 2, 0x00CC0020 /* SRCCOPY */));
            CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 3, 4), f.red);
            CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 4, 4), f.green);
            CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 3, 5), f.blue);
            CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 4, 5), f.red);
            SelectObject(src, prev);
            DeleteObject(sb);
        }
        DeleteDC(src);
        CHECK(pad_intact(buf, pitch, f.bpp, W, H));
        /* release: the pixels stay, the DC handle is dead */
        uint32_t keep = rawpx(buf.data(), pitch, f.bpp, 3, 5);
        nxd3d_dc_release(dc);
        CHECK_EQ(rawpx(buf.data(), pitch, f.bpp, 3, 5), keep);
        CHECK(SetPixel(dc, 0, 0, 0) == 0xFFFFFFFFu && GetPixel(dc, 0, 0) == 0xFFFFFFFFu);
        CHECK(!DeleteDC(dc));
        nxd3d_dc_release(dc);   /* a second release: nothing */
    }
}

TEST(rh_dc_text)
{
    const int W = 40, H = 24;
    for (const Fmt &f : FMTS) {
        const size_t pitch = (size_t)W * f.bpp + 12;
        std::vector<uint8_t> buf(pitch * H, 0xAB);
        HDC dc = (HDC)nxd3d_dc_create(buf.data(), f.d3d, W, H, (uint32_t)pitch);
        CHECK(dc != NULL);
        if (!dc) continue;
        gdi_dc_fill(dc, 0, 0, W, H, 0x00FFFFFF);
        std::vector<uint8_t> white = buf;
        SetTextColor(dc, 0);
        SetBkMode(dc, 1);
        CHECK(ExtTextOutA(dc, 4, 2, 0, NULL, "W", 1, NULL));
        int dark = 0;
        for (int y = 0; y < H; y++)
            for (int x = 0; x < W; x++)
                if (rawpx(buf.data(), pitch, f.bpp, x, y) != rawpx(white.data(), pitch, f.bpp, x, y)) dark++;
        CHECK(dark > 8);
        CHECK(pad_intact(buf, pitch, f.bpp, W, H));
        nxd3d_dc_release(dc);
    }
}

TEST(rh_dc_rejects)
{
    std::vector<uint8_t> buf(4096, 0);
    const uint32_t bad[] = { 0, 19, 26, 31, 50, 75, 0xFFFFFFFFu };
    for (uint32_t d : bad) CHECK(nxd3d_dc_create(buf.data(), d, 4, 4, 16) == NULL);
    CHECK(nxd3d_dc_create(NULL, 22, 4, 4, 16) == NULL);
    CHECK(nxd3d_dc_create(buf.data(), 22, 0, 4, 16) == NULL);
    CHECK(nxd3d_dc_create(buf.data(), 22, 4, 0, 16) == NULL);
    CHECK(nxd3d_dc_create(buf.data(), 22, 4, 4, 15) == NULL);   /* a pitch below one row */
    CHECK(nxd3d_dc_create(buf.data(), 20, 4, 4, 11) == NULL);
    CHECK(nxd3d_dc_create(buf.data(), 23, 0x80000000u, 4, 16) == NULL);
    /* release of a handle that is not one of ours changes nothing */
    HDC other = CreateCompatibleDC(NULL);
    nxd3d_dc_release(other);
    CHECK(DeleteDC(other));
    nxd3d_dc_release(NULL);
    HDC dc = (HDC)nxd3d_dc_create(buf.data(), 22, 4, 4, 16);
    CHECK(dc != NULL);
    nxd3d_dc_release(dc);
}

/* ---- window_client / perf_counter / cursor */
static LRESULT rh_proc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }

TEST(rh_window_client)
{
    WNDCLASSA wc; memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = rh_proc; wc.lpszClassName = "RhCls";
    CHECK(RegisterClassA(&wc) != 0);
    HWND pw = CreateWindowExA(0, "RhCls", "p", 0x90000000u /* WS_POPUP | WS_VISIBLE */, 37, 53, 200, 100, NULL, NULL, NULL, NULL);
    CHECK(pw != NULL);
    RECT r = { 1, 2, 3, 4 };
    CHECK(nxd3d_window_client(pw, &r));
    CHECK(r.left == 37 && r.top == 53 && r.right == 237 && r.bottom == 153);
    CHECK(nxd3d_window_client(pw, NULL));   /* r may be null */
    HWND ow = CreateWindowExA(0, "RhCls", "o", 0x10CF0000u /* WS_OVERLAPPEDWINDOW | WS_VISIBLE */, 10, 10, 320, 240, NULL, NULL, NULL, NULL);
    CHECK(ow != NULL);
    CHECK(SetWindowPos(ow, NULL, 120, 90, 0, 0, 0x15 /* NOSIZE | NOZORDER | NOACTIVATE */));
    RECT cl, wr, got = { 0, 0, 0, 0 };
    POINT o = { 0, 0 };
    CHECK(GetClientRect(ow, &cl) && ClientToScreen(ow, &o) && GetWindowRect(ow, &wr));
    CHECK(nxd3d_window_client(ow, &got));
    CHECK(got.left == o.x && got.top == o.y && got.right - got.left == cl.right && got.bottom - got.top == cl.bottom);
    CHECK(got.left >= wr.left && got.top >= wr.top && got.right <= wr.right && got.bottom <= wr.bottom);
    CHECK(wr.left == 120 && wr.top == 90);
    /* not a window */
    CHECK(!nxd3d_window_client(NULL, &r));
    CHECK(!nxd3d_window_client((void *)(uintptr_t)0x1234, &r));
    CHECK(!nxd3d_window_client((void *)(uintptr_t)0xFFFFFFFFu, NULL));
    DestroyWindow(pw);
    CHECK(!nxd3d_window_client(pw, &r));
    DestroyWindow(ow);
}

TEST(rh_perf_counter)
{
    LARGE_INTEGER f0, q0, q1;
    CHECK(QueryPerformanceFrequency(&f0));
    uint64_t freq = 0, prev = 0;
    CHECK(QueryPerformanceCounter(&q0));
    uint64_t a = nxd3d_perf_counter(&freq);
    CHECK(QueryPerformanceCounter(&q1));
    CHECK(freq > 0 && freq == (uint64_t)f0.QuadPart);
    CHECK(a >= (uint64_t)q0.QuadPart && a <= (uint64_t)q1.QuadPart);
    prev = a;
    bool mono = true;
    for (int i = 0; i < 2000; i++) {
        uint64_t v = nxd3d_perf_counter(NULL);   /* freq may be null */
        if (v < prev) mono = false;
        prev = v;
    }
    CHECK(mono);
    uint64_t fr2 = 7;
    nxd3d_perf_counter(&fr2);
    CHECK_EQ(fr2, freq);
}

TEST(rh_cursor_pos)
{
    const int pos[][2] = { { 123, 45 }, { 0, 0 }, { 640, 360 } };
    for (auto &p : pos) {
        CHECK(SetCursorPos(p[0], p[1]));
        POINT w = { -1, -1 };
        CHECK(GetCursorPos(&w));
        int32_t x = -7, y = -7;
        nxd3d_os_cursor_pos(&x, &y);
        CHECK(x == (int32_t)w.x && y == (int32_t)w.y);
        CHECK(x == p[0] && y == p[1]);
    }
}
