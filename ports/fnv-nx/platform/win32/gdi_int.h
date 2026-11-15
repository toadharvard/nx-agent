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
/* DC text state: one 32-bit value per field; set != 0 stores value; *prev receives the old one. 0 = bad DC. */
enum { GDI_TS_TEXTCOLOR, GDI_TS_BKCOLOR, GDI_TS_BKMODE, GDI_TS_ALIGN, GDI_TS_MAPMODE, GDI_TS_POSX, GDI_TS_POSY,
       GDI_TS_WEXTX, GDI_TS_WEXTY, GDI_TS_VEXTX, GDI_TS_VEXTY,   /* window / viewport extents of the mapping mode */
       GDI_TS_COUNT };
int gdi_dc_state(void *hdc, int field, int set, int32_t value, int32_t *prev);
uint32_t gdi_dc_object(void *hdc, int gdik);              /* the DC's selected object of that kind, 0 = none / bad DC */
void *gdi_font_create(const void *logfontw92);            /* new font object holding a copy, NULL on failure */
/* Text drawing side, device coordinates, everything clipped to the DC's surface (gdi32.cpp). */
int gdi_dc_depth(void *hdc);                                                   /* bits per pixel of the DC's surface, 0 = bad DC / no surface */
int gdi_dc_fill(void *hdc, int l, int t, int r, int b, uint32_t colorref);     /* solid rectangle [l,r) x [t,b) */
/* A glyph coverage bitmap (8 bits per pixel, levels 0..16; 0 and 1 draw nothing, >= 16 is solid, between is the
 * 17-level antialiasing ramp on surfaces deeper than 8 bpp). clip = l,t,r,b or NULL. */
int gdi_dc_glyph(void *hdc, int x, int y, int w, int h, const uint8_t *cov, int pitch, uint32_t colorref, const int *clip);
int gdi_dc_poly(void *hdc, const int32_t *xy, int n, uint32_t colorref);       /* even-odd polygon, pixel centres */
}
#endif
