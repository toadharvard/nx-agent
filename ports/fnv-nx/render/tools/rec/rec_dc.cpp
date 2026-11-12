/* rec_dc.cpp: d9port_dc_pixels for Windows: GetDIBits of the DC's selected bitmap, rows packed (rowbytes = n / h). */
#include "d9wrap_port.h"
static unsigned bpp(uint32_t fmt) {   /* D3DFORMAT of GetDC-able surfaces: R5G6B5 23, X1R5G5B5 24, A1R5G5B5 25, R8G8B8 20, A8R8G8B8 21, X8R8G8B8 22 */
    switch (fmt) { case 20: return 24; case 21: case 22: return 32; case 23: case 24: case 25: return 16; default: return 0; }
}
void d9port_dc_pixels(void *hdc, uint32_t fmt, uint32_t w, uint32_t h, void *dst, size_t n) {
    memset(dst, 0, n);
    unsigned b = bpp(fmt); HBITMAP bm = (HBITMAP)GetCurrentObject((HDC)hdc, OBJ_BITMAP);
    if (!b || !bm || !w || !h || n % h) return;
    size_t rb = n / h, stride = ((size_t)w * b + 31) / 32 * 4;
    struct { BITMAPINFOHEADER h; DWORD m[3]; } bi; memset(&bi, 0, sizeof bi);
    bi.h.biSize = sizeof bi.h; bi.h.biWidth = (LONG)w; bi.h.biHeight = -(LONG)h; bi.h.biPlanes = 1; bi.h.biBitCount = (WORD)b; bi.h.biCompression = BI_RGB;
    if (fmt == 23) { bi.h.biCompression = BI_BITFIELDS; bi.m[0] = 0xF800; bi.m[1] = 0x07E0; bi.m[2] = 0x001F; }
    unsigned char *t = (unsigned char *)malloc(stride * h); if (!t) return;
    if (GetDIBits((HDC)hdc, bm, 0, h, t, (BITMAPINFO *)&bi, DIB_RGB_COLORS))
        for (uint32_t y = 0; y < h; y++) memcpy((unsigned char *)dst + y * rb, t + y * stride, rb < stride ? rb : stride);
    free(t);
}
