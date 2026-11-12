/* render_hooks.cpp - the platform side of the Direct3D 9 device layer's hook table (render/d3d9/dev/platform_hooks.tsv;
 * declarations: shim/include/fnvnx_render_hooks.h). nxd3d_gpu_call is not here: its definition is
 * platform/guest/platform_glue.cpp. */
#include "w32.h"
#include "../include/fnvnx_render_hooks.h"

extern "C" {
HDC fnvnx_gdi_memdc_over_bits(void *mem, int w, int h, int bpp, const uint32_t *masks, size_t pitch);
BOOL fnvnx_gdi_memdc_over_bits_release(HDC hd);
}

/* ---- GPU memory: ordinary guest memory of the page map. One VirtualAlloc(NULL, n, MEM_RESERVE | MEM_COMMIT) is one
 * allocation: memory.cpp's VirtualAlloc places it with exactly one successful place() = one os_vm_reserve of the whole
 * (granule rounded) range (a failed attempt maps nothing), then commits inside that reservation (commit_pages is no
 * reservation). The base is 64 KiB aligned. A size of 0 is one page. */
void *nxd3d_gpu_alloc(size_t bytes)
{
    const size_t PG = 4096;
    if (bytes > (size_t)-1 - (PG - 1)) return NULL;
    size_t n = (bytes + PG - 1) & ~(PG - 1);
    if (!n) n = PG;
    return VirtualAlloc(NULL, n, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
}
/* a pointer alloc did not return is a caller contract violation: VirtualFree's documented failure (the last error),
 * nothing else changes */
void nxd3d_gpu_free(void *p)
{
    VirtualFree(p, 0, MEM_RELEASE);
}

/* ---- device contexts over the caller's pixels. D3DFMT values (d3d9types.h) and their GDI layout */
void *nxd3d_dc_create(void *mem, uint32_t d3dfmt, uint32_t width, uint32_t height, uint32_t pitch)
{
    static const uint32_t M888[3] = { 0xFF0000, 0x00FF00, 0x0000FF }, M565[3] = { 0xF800, 0x07E0, 0x001F },
                          M555[3] = { 0x7C00, 0x03E0, 0x001F }, M0[3] = { 0, 0, 0 };
    int bpp;
    const uint32_t *m;
    switch (d3dfmt) {
    case 21: /* D3DFMT_A8R8G8B8 */
    case 22: /* D3DFMT_X8R8G8B8 */ bpp = 32; m = M888; break;
    case 23: /* D3DFMT_R5G6B5 */ bpp = 16; m = M565; break;
    case 24: /* D3DFMT_X1R5G5B5 */
    case 25: /* D3DFMT_A1R5G5B5 */ bpp = 16; m = M555; break;
    case 20: /* D3DFMT_R8G8B8 */ bpp = 24; m = M0; break;
    default: return NULL;
    }
    if (width > 0x7FFFFFFFu || height > 0x7FFFFFFFu) return NULL;
    return (void *)fnvnx_gdi_memdc_over_bits(mem, (int)width, (int)height, bpp, m, pitch);
}
void nxd3d_dc_release(void *dc)
{
    fnvnx_gdi_memdc_over_bits_release((HDC)dc);
}

/* ---- Win32 facts */
bool nxd3d_window_client(void *hwnd, RECT *r)
{
    HWND w = (HWND)hwnd;
    RECT c;
    POINT o = { 0, 0 };
    if (!IsWindow(w) || !GetClientRect(w, &c) || !ClientToScreen(w, &o)) return false;
    if (r) {
        r->left = o.x + c.left; r->top = o.y + c.top; r->right = o.x + c.right; r->bottom = o.y + c.bottom;
    }
    return true;
}
/* the counter and the frequency of QueryPerformanceCounter / QueryPerformanceFrequency (time.cpp): clk_read_ticks / clk_tick_freq */
uint64_t nxd3d_perf_counter(uint64_t *freq)
{
    if (freq) *freq = clk_tick_freq();
    return clk_read_ticks();
}
/* GetCursorPos's state; both pointers are required (platform_hooks.tsv gives no null case) */
void nxd3d_os_cursor_pos(int32_t *x, int32_t *y)
{
    POINT p = { 0, 0 };
    GetCursorPos(&p);
    *x = (int32_t)p.x;
    *y = (int32_t)p.y;
}
