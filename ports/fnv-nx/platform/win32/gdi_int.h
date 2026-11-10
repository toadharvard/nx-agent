/* gdi_int.h - what gdifont.cpp needs from gdi32.cpp's handle table (implemented at the end of gdi32.cpp). */
#ifndef FNVNX_GDI_INT_H
#define FNVNX_GDI_INT_H
#include <stdint.h>

enum { GDIK_NONE = 0, GDIK_DC = 1, GDIK_BITMAP, GDIK_BRUSH, GDIK_PEN, GDIK_FONT, GDIK_PALETTE };   /* = obj type */

struct gdi_objinfo {
    int kind;            /* GDIK_*, 0 for a dead / wrong handle */
    int stock;           /* stock object index (GetStockObject argument), -1 for a created object */
    int w, h, bpp, pitch, nct;   /* bitmap */
    int dib, topdown;
    const uint8_t *bits;
    uint32_t color, mask[3];     /* brush / pen colour (0x00RRGGBB), DIB bit masks */
    int hollow;                  /* NULL brush / pen */
    void *payload;               /* font: the LOGFONTW (92 bytes, 32-bit layout) of a created font, NULL for stock */
};

extern "C" {
int gdi_obj_info(void *handle, struct gdi_objinfo *oi);   /* 1 = ok */
uint32_t gdi_dc_font(void *hdc);                          /* the font handle selected into the DC, 0 = bad DC */
void *gdi_font_create(const void *logfontw92);            /* new font object holding a copy, NULL on failure */
}
#endif
