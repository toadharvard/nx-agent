/* Host harness of test_dkblit.py: dk_blit.c (the real product file) against a recording double of the deko3d image calls and of the contract
 * helpers (nxdk_img, nxdk_box_view, nxdk_scale_point, nxdk_query_pause/resume, dk_fmt). The double prints every recorded call with the sub-resources
 * it names, so the exact primitive, views, rects, flags and barrier bracketing of every blit class are checked, and every rejection and its message. */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

static char evs[512][176]; static int nev;
static void ev(const char* fmt, ...) { va_list ap; va_start(ap, fmt); if (nev < 512) vsnprintf(evs[nev++], 176, fmt, ap); va_end(ap); }
static int is(int i, const char* s) { return i >= 0 && i < nev && !strcmp(evs[i], s); }
static int nchk;
#define CHK(name, cond) do { printf("CHK %s %d\n", name, (cond) ? 1 : 0); nchk++; } while (0)
static char msg[200]; static int nerr;
void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap); nerr++; }
NxDkStream nxdk_s;
static uint8_t cmdobj; static int badcmd;

/* ---- the image table and formats: 1 RGBA8, 2 BGRA8, 3 Z24S8, 4 ZF32, 5 BC1, 6 RGBA32F (no 2D engine) --------------------------------------------- */
static NxDkImage tbl[32];
DkFmt dk_fmt(uint32_t f) {
    switch (f) {
    case 1: return (DkFmt){ .format = DkImageFormat_RGBA8_Unorm, .flags = DKFMT_RENDER };
    case 2: return (DkFmt){ .format = DkImageFormat_BGRA8_Unorm, .flags = DKFMT_RENDER };
    case 3: return (DkFmt){ .format = DkImageFormat_Z24S8, .flags = DKFMT_DEPTH | DKFMT_STENCIL };
    case 4: return (DkFmt){ .format = DkImageFormat_ZF32, .flags = DKFMT_DEPTH };
    case 5: return (DkFmt){ .format = DkImageFormat_RGBA_BC1, .flags = DKFMT_COMPRESSED };
    default: return (DkFmt){ .format = DkImageFormat_RGBA32_Float, .flags = DKFMT_RENDER };
    }
}
uint32_t dkImageFormatGetFlags(DkImageFormat f) { return f == DkImageFormat_RGBA32_Float ? 0 : DkImageFlags_Usage2DEngine; }
const NxDkImage* nxdk_img(uint32_t id) { return id < 32 && tbl[id].used ? &tbl[id] : NULL; }
static void mk(uint32_t id, uint32_t w, uint32_t h, uint32_t d, uint32_t fmt, uint32_t type, uint32_t mips, uint32_t samples) {
    memset(&tbl[id], 0, sizeof tbl[id]);
    tbl[id] = (NxDkImage){ .w = w, .h = h, .d = d, .fmt = fmt, .type = type, .mips = mips, .samples = samples, .used = 1, .layers = type == NxImgType_Cube ? 6 : 1 };
}
static void setup(void) {
    memset(tbl, 0, sizeof tbl);
    mk(0, 64, 64, 1, 2, 0, 1, 1); mk(1, 64, 64, 1, 1, 0, 7, 1); mk(2, 64, 64, 1, 1, 0, 7, 1); mk(3, 32, 32, 1, 1, 0, 6, 1); mk(4, 64, 64, 1, 2, 0, 7, 1);
    mk(5, 64, 64, 1, 1, 0, 1, 4); mk(6, 64, 64, 1, 3, 0, 1, 1); mk(7, 64, 64, 1, 3, 0, 1, 1); mk(8, 32, 32, 1, 3, 0, 1, 1); mk(9, 64, 64, 1, 4, 0, 1, 1);
    mk(10, 64, 64, 1, 5, 0, 1, 1); mk(11, 64, 64, 1, 5, 0, 1, 1); mk(12, 32, 32, 1, 5, 0, 1, 1); mk(13, 64, 64, 1, 6, 0, 3, 1); mk(14, 32, 32, 1, 6, 0, 1, 1);
    mk(15, 16, 16, 1, 1, NxImgType_Cube, 5, 1); mk(16, 16, 16, 16, 1, NxImgType_3D, 5, 1);
    tbl[17] = tbl[1]; tbl[17].isView = 1; tbl[17].base = 1;
    mk(18, 64, 64, 1, 3, 0, 1, 4); mk(19, 32, 32, 1, 1, 0, 1, 4); mk(20, 64, 64, 1, 6, 0, 1, 4); mk(21, 16, 16, 1, 1, 0, 1, 4); mk(22, 8, 2, 1, 1, 0, 4, 1);
    mk(23, 2, 2, 1, 1, 0, 4, 1); mk(24, 6, 6, 6, 1, NxImgType_3D, 3, 1); mk(25, 7, 7, 5, 1, NxImgType_3D, 2, 1); mk(26, 8, 8, 1, 1, 0, 1, 1); mk(27, 64, 32, 1, 1, 0, 1, 1); mk(28, 128, 64, 1, 1, 0, 1, 1); mk(29, 64, 128, 1, 1, 0, 1, 1); mk(31, 32, 64, 1, 1, 0, 1, 1);
}

/* ---- recorded calls ---------------------------------------------------------------------------------------------------------------------------- */
static struct { const DkImageView* v; DkImageRect r; } vmap[64]; static int nvm, badbox;
void nxdk_box_view(const NxDkImage* im, uint32_t level, uint32_t layer, const uint32_t box[6], DkImageView* v, DkImageRect* r) {
    memset(v, 0, sizeof *v); v->pImage = &im->dk; v->mipLevelOffset = (uint8_t)level; v->layerOffset = (uint16_t)layer;
    *r = (DkImageRect){ box[0], box[1], layer, box[3], box[4], 1 };
    if (box[2] != layer || box[5] != 1) badbox = 1;
    vmap[nvm % 64].v = v; vmap[nvm % 64].r = *r; nvm++;
}
static const char* vs(const DkImageView* v) {
    static char b[4][80]; static int k; char* o = b[k++ & 3];
    const DkImageRect* r = NULL;
    for (int i = nvm - 1; i >= 0 && i >= nvm - 64; i--) if (vmap[i % 64].v == v) { r = &vmap[i % 64].r; break; }
    snprintf(o, 80, "%d:l%uz%u %u,%u,%ux%u", (int)((const NxDkImage*)v->pImage - tbl), v->mipLevelOffset, v->layerOffset, r ? r->x : 9999, r ? r->y : 9999,
             r ? r->width : 0, r ? r->height : 0);
    return o;
}
void dkCmdBufBarrier(DkCmdBuf c, DkBarrier b, uint32_t f) {
    badcmd |= (c != (DkCmdBuf)&cmdobj);
    if (b == DkBarrier_Full && f == (DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache)) ev("bar"); else ev("barBAD%u/%x", (unsigned)b, f);
}
void dkCmdBufCopyImage(DkCmdBuf c, const DkImageView* s, const DkImageRect* sr, const DkImageView* d, const DkImageRect* dr, uint32_t fl) {
    badcmd |= (c != (DkCmdBuf)&cmdobj);
    char a[80]; snprintf(a, sizeof a, "%s", vs(s)); (void)sr; (void)dr;
    ev("copy %s > %s f%u", a, vs(d), fl);
}
void dkCmdBufBlitImage(DkCmdBuf c, const DkImageView* s, const DkImageRect* sr, const DkImageView* d, const DkImageRect* dr, uint32_t fl, uint32_t fac) {
    badcmd |= (c != (DkCmdBuf)&cmdobj);
    char a[80]; snprintf(a, sizeof a, "%s", vs(s)); (void)sr; (void)dr;
    ev("blit %s > %s f%u a%u", a, vs(d), fl, fac);
}
void dkCmdBufResolveImage(DkCmdBuf c, const DkImageView* s, const DkImageView* d) {
    badcmd |= (c != (DkCmdBuf)&cmdobj);
    char a[80]; snprintf(a, sizeof a, "%s", vs(s)); ev("resolve %s > %s", a, vs(d));
}
void nxdk_scale_point(DkCmdBuf c, const NxDkSub* s, const NxDkSub* d, const uint16_t* ramp) {
    badcmd |= (c != (DkCmdBuf)&cmdobj);
    ev("scale %d(id%u):l%uz%u %u,%u,%ux%u > %d(id%u):l%uz%u %u,%u,%ux%u r%d", (int)(s->im - tbl), s->id, s->level, s->layer, s->x, s->y, s->w, s->h,
       (int)(d->im - tbl), d->id, d->level, d->layer, d->x, d->y, d->w, d->h, ramp != NULL);
}
void nxdk_query_pause(void) { ev("pause"); }
void nxdk_query_resume(void) { ev("resume"); }

/* ---- drivers --------------------------------------------------------------------------------------------------------------------------------- */
static void reset(void) { nev = 0; nerr = 0; msg[0] = 0; setup(); nxdk_s.cmd = (DkCmdBuf)&cmdobj; nvm = 0; }
static void BLN(uint32_t n, uint32_t s, uint32_t sl, uint32_t sx, uint32_t sy, uint32_t sw, uint32_t sh, uint32_t d, uint32_t dl, uint32_t dx, uint32_t dy,
                uint32_t dw, uint32_t dh, uint32_t f) {
    const uint32_t a[13] = { s, sl, sx, sy, sw, sh, d, dl, dx, dy, dw, dh, f };
    reset(); nxdk_op_blit(a, n);
}
static void BL(uint32_t s, uint32_t sl, uint32_t sx, uint32_t sy, uint32_t sw, uint32_t sh, uint32_t d, uint32_t dl, uint32_t dx, uint32_t dy, uint32_t dw,
               uint32_t dh, uint32_t f) { BLN(13, s, sl, sx, sy, sw, sh, d, dl, dx, dy, dw, dh, f); }
static void GM(uint32_t id, uint32_t f, uint32_t n) { const uint32_t a[2] = { id, f }; reset(); nxdk_op_gen_mips(a, n); }
#define EVS(...) (nev == (int)(sizeof((const char*[]){ __VA_ARGS__ }) / sizeof(char*)) && evseq(__VA_ARGS__, NULL))
static int evseq(const char* first, ...) {
    va_list ap; va_start(ap, first); int i = 0, ok = 1;
    for (const char* s = first; s; s = va_arg(ap, const char*), i++) if (!is(i, s)) { ok = 0; break; }
    va_end(ap); return ok;
}
#define ERR(name, text) CHK(name, nerr == 1 && nev == 0 && !strcmp(msg, text))
#define ERRP(name, text) CHK(name, nerr == 1 && nev == 0 && !strncmp(msg, text, strlen(text)))
#define W64 0, 0, 64, 64

int main(void) {
    /* B2: copy */
    BL(1, 0, W64, 2, 0, W64, 0);
    CHK("copy_events", EVS("bar", "copy 1:l0z0 0,0,64x64 > 2:l0z0 0,0,64x64 f0", "bar") && nerr == 0);
    BL(1, 1, 8, 4, 16, 8, 2, 2, 0, 4, 16, 8, 1);
    CHK("copy_sub_events", EVS("bar", "copy 1:l1z0 8,4,16x8 > 2:l2z0 0,4,16x8 f0", "bar") && nerr == 0);
    BL(15, 3 << 16, 0, 0, 16, 16, 15, 1 | (5 << 16), 0, 0, 8, 8, 0);
    CHK("cube_scale_layers", is(2, "scale 15(id15):l0z3 0,0,16x16 > 15(id15):l1z5 0,0,8x8 r0") && nerr == 0);
    BL(15, 1 | (3 << 16), 0, 0, 8, 8, 15, 1 | (5 << 16), 0, 0, 8, 8, 1);
    CHK("cube_copy_events", EVS("bar", "copy 15:l1z3 0,0,8x8 > 15:l1z5 0,0,8x8 f0", "bar") && nerr == 0);
    BL(10, 0, W64, 11, 0, W64, 0);
    CHK("copy_compressed_events", EVS("bar", "copy 10:l0z0 0,0,64x64 > 11:l0z0 0,0,64x64 f0", "bar"));
    BL(6, 0, W64, 7, 0, W64, 1);
    CHK("copy_depth_events", EVS("bar", "copy 6:l0z0 0,0,64x64 > 7:l0z0 0,0,64x64 f0", "bar"));
    BL(1, 0, W64, 2, 0, W64, 1);
    CHK("copy_ignores_filter", EVS("bar", "copy 1:l0z0 0,0,64x64 > 2:l0z0 0,0,64x64 f0", "bar"));
    BL(17, 0, W64, 2, 0, W64, 0);
    CHK("view_src_is_base", EVS("bar", "copy 1:l0z0 0,0,64x64 > 2:l0z0 0,0,64x64 f0", "bar"));
    BL(2, 0, W64, 17, 0, W64, 0);
    CHK("view_dst_is_base", EVS("bar", "copy 2:l0z0 0,0,64x64 > 1:l0z0 0,0,64x64 f0", "bar"));
    BL(1, 0, W64, 0, 0, W64, 1);
    CHK("blit_front_dst", EVS("bar", "blit 1:l0z0 0,0,64x64 > 0:l0z0 0,0,64x64 f16 a0", "bar"));

    /* B6: 2D engine blit */
    BL(1, 0, W64, 3, 0, 0, 0, 32, 32, 1);
    CHK("blit_linear_events", EVS("bar", "blit 1:l0z0 0,0,64x64 > 3:l0z0 0,0,32x32 f16 a0", "bar") && !badcmd);
    BL(1, 0, W64, 4, 0, W64, 0);
    CHK("blit_nearest_convert", EVS("bar", "blit 1:l0z0 0,0,64x64 > 4:l0z0 0,0,64x64 f0 a0", "bar"));
    BL(1, 0, W64, 4, 0, W64, 1);
    CHK("blit_linear_convert", EVS("bar", "blit 1:l0z0 0,0,64x64 > 4:l0z0 0,0,64x64 f16 a0", "bar"));
    BL(1, 0, 16, 8, 32, 16, 4, 0, 4, 2, 24, 20, 1);
    CHK("blit_rects_exact", EVS("bar", "blit 1:l0z0 16,8,32x16 > 4:l0z0 4,2,24x20 f16 a0", "bar"));
    BL(13, 0, 0, 0, 64, 64, 14, 0, 0, 0, 32, 32, 1);
    ERRP("err_engine_format", "Blit: format 6 -> 6 (format) needs the 2D engine");
    BL(16, 0, 0, 0, 16, 16, 16, 1, 0, 0, 8, 8, 1);
    ERRP("err_engine_3d", "Blit: format 1 -> 1 (3D image) needs the 2D engine");

    BL(1, 0, W64, 13, 0, W64, 1);
    ERRP("err_engine_dst", "Blit: format 1 -> 6 (format) needs the 2D engine");
    BL(13, 0, W64, 1, 0, W64, 1);
    ERRP("err_engine_src", "Blit: format 6 -> 1 (format) needs the 2D engine");
    BL(1, 0, 0, 0, 64, 32, 2, 0, W64, 0);
    CHK("scale_height_only", EVS("bar", "pause", "scale 1(id1):l0z0 0,0,64x32 > 2(id2):l0z0 0,0,64x64 r0", "resume", "bar"));
    BL(3, 0, 0, 0, 32, 32, 17, 0, W64, 0);
    CHK("scale_dst_view_id", is(2, "scale 3(id3):l0z0 0,0,32x32 > 1(id1):l0z0 0,0,64x64 r0"));
    /* B5: point scale */
    BL(1, 0, W64, 3, 0, 0, 0, 32, 32, 0);
    CHK("scale_events", EVS("bar", "pause", "scale 1(id1):l0z0 0,0,64x64 > 3(id3):l0z0 0,0,32x32 r0", "resume", "bar") && !badcmd);
    BL(17, 0, W64, 3, 0, 0, 0, 32, 32, 0);
    CHK("scale_view_id", is(2, "scale 1(id1):l0z0 0,0,64x64 > 3(id3):l0z0 0,0,32x32 r0"));
    BL(6, 0, W64, 8, 0, 0, 0, 32, 32, 1);
    CHK("depth_scale_events", EVS("bar", "pause", "scale 6(id6):l0z0 0,0,64x64 > 8(id8):l0z0 0,0,32x32 r0", "resume", "bar"));
    BL(1, 0, W64, 26, 0, 0, 0, 8, 8, 0);
    CHK("scale_down_to_8", is(2, "scale 1(id1):l0z0 0,0,64x64 > 26(id26):l0z0 0,0,8x8 r0"));

    /* B1, B3 */
    BL(1, 0, W64, 5, 0, W64, 0);
    ERR("err_ms_dst", "Blit: multisampled destination");
    BL(5, 0, W64, 5, 0, W64, 1);
    ERR("err_ms_dst_same", "Blit: multisampled destination");
    BL(10, 0, W64, 12, 0, 0, 0, 32, 32, 0);
    ERR("err_compressed_size", "Blit: compressed formats only copy (same format and size)");
    BL(10, 0, W64, 1, 0, W64, 0);
    ERR("err_compressed_format", "Blit: compressed formats only copy (same format and size)");
    BL(1, 0, W64, 10, 0, W64, 0);
    ERR("err_compressed_dst", "Blit: compressed formats only copy (same format and size)");
    BL(6, 0, W64, 9, 0, W64, 0);
    ERR("err_depth_formats", "Blit: depth-stencil blit needs identical formats (3 -> 4)");
    BL(6, 0, W64, 1, 0, W64, 0);
    ERR("err_depth_to_colour", "Blit: depth-stencil blit needs identical formats (3 -> 1)");
    BL(1, 0, W64, 6, 0, W64, 0);
    ERR("err_colour_to_depth", "Blit: depth-stencil blit needs identical formats (1 -> 3)");

    /* B4: multisampled source */
    BL(5, 0, W64, 2, 0, W64, 0);
    CHK("resolve_events", EVS("bar", "resolve 5:l0z0 0,0,64x64 > 2:l0z0 0,0,64x64", "bar") && !badcmd);
    BL(5, 0, W64, 2, 0, W64, 1);
    CHK("resolve_ignores_filter", EVS("bar", "resolve 5:l0z0 0,0,64x64 > 2:l0z0 0,0,64x64", "bar"));
    BL(19, 0, 0, 0, 32, 32, 2, 1, 0, 0, 32, 32, 0);
    CHK("resolve_level_events", EVS("bar", "resolve 19:l0z0 0,0,32x32 > 2:l1z0 0,0,32x32", "bar"));
    BL(5, 0, 0, 0, 32, 32, 2, 0, 0, 0, 32, 32, 0);
    ERRP("err_resolve_subrect", "Blit: multisampled source 32x32");
    BL(5, 0, W64, 2, 0, 8, 8, 32, 32, 0);
    ERRP("err_resolve_offset", "Blit: multisampled source 64x64");
    BL(5, 0, W64, 3, 0, 0, 0, 32, 32, 0);
    ERRP("err_resolve_scale", "Blit: multisampled source 64x64");
    BL(5, 0, W64, 4, 0, W64, 0);
    ERRP("err_resolve_format", "Blit: multisampled source 64x64");
    BL(5, 0, W64, 26, 0, 0, 0, 8, 8, 0);
    ERRP("err_resolve_dst_partial", "Blit: multisampled source 64x64");
    BL(5, 0, 0, 0, 32, 32, 3, 0, 0, 0, 32, 32, 0);
    ERRP("err_resolve_subsrc", "Blit: multisampled source 32x32");
    BL(5, 0, 0, 0, 32, 64, 31, 0, 0, 0, 32, 64, 0);
    ERRP("err_resolve_subsrc_w", "Blit: multisampled source 32x64");
    BL(5, 0, 0, 0, 64, 32, 27, 0, 0, 0, 64, 32, 0);
    ERRP("err_resolve_subsrc_h", "Blit: multisampled source 64x32");
    BL(5, 0, W64, 28, 0, W64, 0);
    ERRP("err_resolve_dstw", "Blit: multisampled source 64x64");
    BL(5, 0, W64, 29, 0, W64, 0);
    ERRP("err_resolve_dsth", "Blit: multisampled source 64x64");
    BL(18, 0, W64, 7, 0, W64, 0);
    ERRP("err_resolve_depth", "Blit: resolve of a multisampled depth source");
    BL(20, 0, W64, 13, 0, W64, 0);
    ERRP("err_resolve_engine", "Blit: multisampled source 64x64");
    BL(21, 0, 0, 0, 16, 16, 15, 0, 0, 0, 16, 16, 0);
    ERRP("err_resolve_cube_dst", "Blit: multisampled source 16x16");
    BL(21, 0, 0, 0, 16, 16, 16, 0, 0, 0, 16, 16, 0);
    ERRP("err_resolve_3d_dst", "Blit: multisampled source 16x16");

    /* validation */
    BLN(12, 1, 0, W64, 2, 0, W64, 0);
    ERR("err_argc", "Blit: 12 words, need 13");
    BLN(0, 1, 0, W64, 2, 0, W64, 0);
    ERR("err_argc0", "Blit: 0 words, need 13");
    BL(30, 0, W64, 2, 0, W64, 0);
    ERR("err_src_missing", "Blit: bad source 30 or destination 2");
    BL(1, 0, W64, 31, 0, W64, 0);
    ERR("err_dst_missing", "Blit: bad source 1 or destination 31");
    BL(1, 0, W64, 40, 0, W64, 0);
    ERR("err_dst_oob_id", "Blit: bad source 1 or destination 40");
    BL(1, 0, W64, 2, 0, W64, 2);
    ERR("err_filter", "Blit: bad source 1 or destination 2");
    BL(1, 7, W64, 2, 0, W64, 0);
    ERR("err_src_level", "Blit: bad source 1 or destination 2");
    BL(1, 0, W64, 2, 7, W64, 0);
    ERR("err_dst_level", "Blit: bad source 1 or destination 2");
    BL(1, 1 << 16, W64, 2, 0, W64, 0);
    ERR("err_src_layer_2d", "Blit: bad source 1 or destination 2");
    BL(1, 0, W64, 2, 1 << 16, W64, 0);
    ERR("err_dst_layer_2d", "Blit: bad source 1 or destination 2");
    BL(15, 6 << 16, 0, 0, 16, 16, 15, 0, 0, 0, 16, 16, 0);
    ERR("err_cube_layer", "Blit: bad source 15 or destination 15");
    BL(16, 16 << 16, 0, 0, 16, 16, 16, 0, 0, 0, 16, 16, 0);
    ERR("err_3d_layer", "Blit: bad source 16 or destination 16");
    BL(16, 1 | (8 << 16), 0, 0, 8, 8, 16, 1 | (7 << 16), 0, 0, 8, 8, 0);
    CHK("ok_3d_last_slice", nerr == 1);   /* level 1 of a 16-deep 3D image has 8 slices: slice 8 is outside */
    BL(16, 1 | (7 << 16), 0, 0, 8, 8, 16, 1 | (7 << 16), 0, 0, 8, 8, 0);
    CHK("ok_3d_last_slice", EVS("bar", "copy 16:l1z7 0,0,8x8 > 16:l1z7 0,0,8x8 f0", "bar"));
    BL(1, 0, 0, 0, 0, 64, 2, 0, W64, 0);
    ERR("err_zero_sw", "Blit: bad source 1 or destination 2");
    BL(1, 0, 0, 0, 64, 0, 2, 0, W64, 0);
    ERR("err_zero_sh", "Blit: bad source 1 or destination 2");
    BL(1, 0, W64, 2, 0, 0, 0, 0, 64, 0);
    ERR("err_zero_dw", "Blit: bad source 1 or destination 2");
    BL(1, 0, W64, 2, 0, 0, 0, 64, 0, 0);
    ERR("err_zero_dh", "Blit: bad source 1 or destination 2");
    BL(1, 0, 1, 0, 64, 64, 2, 0, W64, 0);
    ERR("err_src_x", "Blit: bad source 1 or destination 2");
    BL(1, 0, 0, 1, 64, 64, 2, 0, W64, 0);
    ERR("err_src_y", "Blit: bad source 1 or destination 2");
    BL(1, 0, W64, 2, 0, 1, 0, 64, 64, 0);
    ERR("err_dst_x", "Blit: bad source 1 or destination 2");
    BL(1, 0, W64, 2, 0, 0, 1, 64, 64, 0);
    ERR("err_dst_y", "Blit: bad source 1 or destination 2");
    BL(1, 0, 0xFFFFFFF0u, 0, 32, 8, 2, 0, 0, 0, 32, 8, 0);
    ERR("err_src_overflow", "Blit: bad source 1 or destination 2");
    BL(1, 0, 0, 0, 32, 8, 2, 0, 0xFFFFFFF0u, 0, 32, 8, 0);
    ERR("err_dst_overflow", "Blit: bad source 1 or destination 2");
    BL(1, 2, 0, 0, 16, 16, 2, 2, 0, 0, 17, 16, 0);
    ERR("err_level_extent", "Blit: bad source 1 or destination 2");
    BL(1, 7, 0, 0, 1, 1, 2, 0, 0, 0, 1, 1, 0);
    ERR("err_src_level_eq_count", "Blit: bad source 1 or destination 2");
    BL(1, 0, 0, 0xFFFFFFF0u, 32, 32, 2, 0, 0, 0, 32, 32, 0);
    ERR("err_src_overflow_y", "Blit: bad source 1 or destination 2");
    BL(1, 2, 0, 0, 16, 17, 2, 2, 0, 0, 16, 16, 0);
    ERR("err_level_extent_y", "Blit: bad source 1 or destination 2");
    BL(26, 0, W64, 2, 0, W64, 0);
    ERR("err_extent_small_src", "Blit: bad source 26 or destination 2");

    /* GenMips */
    GM(1, 0, 2);
    CHK("gm_nearest_events", nev == 30 && nerr == 0 && is(0, "bar") && is(1, "pause") &&
        is(2, "scale 1(id1):l0z0 0,0,64x64 > 1(id1):l1z0 0,0,32x32 r0") && is(3, "resume") && is(4, "bar") &&
        is(27, "scale 1(id1):l5z0 0,0,2x2 > 1(id1):l6z0 0,0,1x1 r0") && is(29, "bar") && !badcmd);
    GM(1, 1, 2);
    CHK("gm_linear_events", nev == 18 && nerr == 0 && is(1, "blit 1:l0z0 0,0,64x64 > 1:l1z0 0,0,32x32 f16 a0") &&
        is(16, "blit 1:l5z0 0,0,2x2 > 1:l6z0 0,0,1x1 f16 a0") && is(17, "bar"));
    GM(17, 1, 2);
    CHK("gm_view_base", nev == 18 && is(1, "blit 1:l0z0 0,0,64x64 > 1:l1z0 0,0,32x32 f16 a0"));
    GM(17, 0, 2);
    CHK("gm_view_id", is(2, "scale 1(id1):l0z0 0,0,64x64 > 1(id1):l1z0 0,0,32x32 r0"));
    GM(22, 1, 2);
    CHK("gm_npot_events", EVS("bar", "blit 22:l0z0 0,0,8x2 > 22:l1z0 0,0,4x1 f16 a0", "bar", "bar", "blit 22:l1z0 0,0,4x1 > 22:l2z0 0,0,2x1 f16 a0", "bar",
        "bar", "blit 22:l2z0 0,0,2x1 > 22:l3z0 0,0,1x1 f16 a0", "bar"));
    GM(23, 1, 2);
    CHK("gm_clamped_copy", EVS("bar", "blit 23:l0z0 0,0,2x2 > 23:l1z0 0,0,1x1 f16 a0", "bar", "bar", "copy 23:l1z0 0,0,1x1 > 23:l2z0 0,0,1x1 f0", "bar",
        "bar", "copy 23:l2z0 0,0,1x1 > 23:l3z0 0,0,1x1 f0", "bar"));
    GM(15, 1, 2);
    CHK("gm_cube_layers", nev == 72 && is(1, "blit 15:l0z0 0,0,16x16 > 15:l1z0 0,0,8x8 f16 a0") && is(16, "blit 15:l0z5 0,0,16x16 > 15:l1z5 0,0,8x8 f16 a0") &&
        is(19, "blit 15:l1z0 0,0,8x8 > 15:l2z0 0,0,4x4 f16 a0") && is(70, "blit 15:l3z5 0,0,2x2 > 15:l4z5 0,0,1x1 f16 a0"));
    GM(16, 0, 2);
    CHK("gm_3d_slices", nev == 5 * (8 + 4 + 2 + 1) && is(2, "scale 16(id16):l0z1 0,0,16x16 > 16(id16):l1z0 0,0,8x8 r0") &&
        is(37, "scale 16(id16):l0z15 0,0,16x16 > 16(id16):l1z7 0,0,8x8 r0") && is(42, "scale 16(id16):l1z1 0,0,8x8 > 16(id16):l2z0 0,0,4x4 r0") &&
        is(72, "scale 16(id16):l3z1 0,0,2x2 > 16(id16):l4z0 0,0,1x1 r0"));
    GM(24, 0, 2);
    CHK("gm_3d_odd_slices", nev == 5 * 4 && is(2, "scale 24(id24):l0z1 0,0,6x6 > 24(id24):l1z0 0,0,3x3 r0") &&
        is(7, "scale 24(id24):l0z3 0,0,6x6 > 24(id24):l1z1 0,0,3x3 r0") && is(12, "scale 24(id24):l0z5 0,0,6x6 > 24(id24):l1z2 0,0,3x3 r0") &&
        is(17, "scale 24(id24):l1z1 0,0,3x3 > 24(id24):l2z0 0,0,1x1 r0"));
    GM(25, 0, 2);
    CHK("gm_3d_ratio_slices", nev == 10 && is(2, "scale 25(id25):l0z1 0,0,7x7 > 25(id25):l1z0 0,0,3x3 r0") &&
        is(7, "scale 25(id25):l0z3 0,0,7x7 > 25(id25):l1z1 0,0,3x3 r0"));
    GM(16, 1, 2);
    ERRP("gm_3d_linear_rejected", "GenMips: the linear filter on a 3D image has no deko3d expression");
    GM(26, 1, 2);
    CHK("gm_single_level", nev == 0 && nerr == 0);
    GM(0, 1, 2);
    CHK("gm_front_single_level", nev == 0 && nerr == 0);
    tbl[26].type = NxImgType_3D;
    { const uint32_t a[2] = { 26, 1 }; nxdk_op_gen_mips(a, 2); }
    CHK("gm_3d_single_level_linear", nev == 0 && nerr == 0);
    GM(13, 1, 2);
    ERRP("gm_stops_on_error", "Blit: format 6 -> 6 (format) needs the 2D engine");
    GM(13, 0, 2);
    CHK("gm_no_engine_nearest", nev == 10 && nerr == 0 && is(2, "scale 13(id13):l0z0 0,0,64x64 > 13(id13):l1z0 0,0,32x32 r0"));
    GM(1, 0, 1);
    ERR("err_gm_argc", "GenMips: 1 words, need 2");
    GM(1, 0, 0);
    ERR("err_gm_argc0", "GenMips: 0 words, need 2");
    GM(30, 0, 2);
    ERR("err_gm_missing", "GenMips: bad image 30 / filter 0");
    GM(1, 2, 2);
    ERR("err_gm_filter", "GenMips: bad image 1 / filter 2");
    GM(5, 0, 2);
    ERR("err_gm_ms", "GenMips: bad image 5 / filter 0");
    GM(99, 0, 2);
    ERR("err_gm_oob_id", "GenMips: bad image 99 / filter 0");
    CHK("box_args_ok", !badbox);
    CHK("cmd_everywhere", !badcmd);
    printf("NCHK %d\n", nchk);
    return 0;
}
