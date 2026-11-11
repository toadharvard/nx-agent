/* Host harness of test_dkpass.py: dk_pass.c + nxgpu4_check.c + dk_fmt.c (the real product files) against a recording double of the deko3d calls.
 * The double fixes only the NAMES and signatures of the library calls. Every call is logged as one text event "@<cmdbuf> ..." joined by '|'; the
 * expected logs are built here from dk_fmt() and the pinned view fields, so a wrong view field, channel order, clear mask or call order is visible.
 * Doubles of other files: nxdk_img, nxdk_img_view_id, nxdk_chk_env (swapImage is a sentinel that is no image id), nxchk_err (= nxdk_err), nxdk, nxdk_s. */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"
#include "dk_fmt.h"
#include "nxgpu4_check.h"

/* ---- log ----------------------------------------------------------------------------------------------------------------------------------- */
static char lg[8192]; static int nerr, nchk; static char lastErr[256];
static int objs[2];
#define CMDA ((DkCmdBuf)&objs[0])
#define CMDB ((DkCmdBuf)&objs[1])
static void ev(DkCmdBuf c, const char* fmt, ...) {
    size_t n = strlen(lg);
    va_list ap;
    if (n) lg[n++] = '|';
    n += (size_t)snprintf(lg + n, sizeof lg - n, "@%c ", c == CMDA ? 'A' : c == CMDB ? 'B' : '?');
    va_start(ap, fmt); vsnprintf(lg + n, sizeof lg - n, fmt, ap); va_end(ap);
}
static void clear_log(void) { lg[0] = 0; nerr = 0; lastErr[0] = 0; }
#define CHK(name, cond) do { printf("CHK %s %d\n", name, (cond) ? 1 : 0); nchk++; } while (0)
static void eq(const char* name, const char* want) {
    const int ok = !strcmp(lg, want);
    if (!ok) fprintf(stderr, "%s\n  got  %s\n  want %s\n", name, lg, want);
    CHK(name, ok);
}
static void rej(const char* name, const char* msg) {   /* exactly one nxdk_err with this text, nothing recorded */
    const int ok = nerr == 1 && !lg[0] && !strcmp(lastErr, msg);
    if (!ok) fprintf(stderr, "%s: errs %d log '%s' err '%s' want '%s'\n", name, nerr, lg, lastErr, msg);
    CHK(name, ok);
}
void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(lastErr, sizeof lastErr, fmt, ap); va_end(ap); nerr++; }

/* ---- images ------------------------------------------------------------------------------------------------------------------------------ */
enum { SWAPID = 50, NIMG = 24 };
NxDk nxdk; NxDkStream nxdk_s;
static NxDkImage tab[NIMG];
static NxChkEnv env;
static void mk(uint32_t id, uint32_t w, uint32_t h, uint32_t mips, uint32_t samples, uint32_t type, uint32_t fmt, uint32_t flags) {
    NxDkImage* im = &tab[id];
    im->w = w; im->h = h; im->d = type == NxImgType_3D ? 4 : 1; im->mips = mips; im->samples = samples; im->type = type; im->fmt = fmt; im->flags = flags;
    im->layers = type == NxImgType_Cube ? 6 : 1; im->used = 1;
}
const NxDkImage* nxdk_img(uint32_t id) { return id == 0 ? &nxdk.front : id < NIMG && tab[id].used ? &tab[id] : NULL; }
uint32_t nxdk_img_view_id(uint32_t id, int srgb) { return srgb && id && id + 1 < NIMG && tab[id + 1].used && tab[id + 1].isView && tab[id + 1].base == id ? id + 1 : id; }
static int chk_image(void* ctx, uint32_t id, NxChkImage* o) {
    const NxDkImage* im = id == SWAPID ? &nxdk.front : nxdk_img(id);
    (void)ctx;
    if (id == 0 || !im) return 0;
    o->w = im->w; o->h = im->h; o->d = im->d; o->mips = im->mips; o->samples = im->samples; o->type = (uint8_t)im->type; o->isView = (uint8_t)im->isView;
    o->isDepth = (im->flags & NxImg_Depth) != 0; o->compressed = (dk_fmt(im->fmt).flags & DKFMT_COMPRESSED) != 0;
    return 1;
}
const NxChkEnv* nxdk_chk_env(void) { return &env; }

/* ---- deko3d double ------------------------------------------------------------------------------------------------------------------- */
static int idof(const DkImage* p) { if (p == &nxdk.front.dk) return 0; for (int i = 1; i < NIMG; i++) if (p == &tab[i].dk) return i; return -1; }
static void vs(char* o, size_t n, const DkImageView* v) {
    snprintf(o, n, "i%d/t%d/f%d/s%d%d%d%d/ds%d/l%u:%u/m%u:%u", idof(v->pImage), (int)v->type, (int)v->format, (int)v->swizzle[0], (int)v->swizzle[1],
             (int)v->swizzle[2], (int)v->swizzle[3], (int)v->dsSource, v->layerOffset, v->layerCount, v->mipLevelOffset, v->mipLevelCount);
}
void dkCmdBufBindRenderTargets(DkCmdBuf c, DkImageView const* const col[], uint32_t n, DkImageView const* d) {
    char b[512] = "BIND", t[128];
    snprintf(b + 4, sizeof b - 4, " n%u", n);
    for (uint32_t i = 0; i < n; i++) { vs(t, sizeof t, col[i]); strncat(b, " C(", sizeof b - strlen(b) - 1); strncat(b, t, sizeof b - strlen(b) - 1); strncat(b, ")", sizeof b - strlen(b) - 1); }
    if (d) { vs(t, sizeof t, d); strncat(b, " D(", sizeof b - strlen(b) - 1); strncat(b, t, sizeof b - strlen(b) - 1); strncat(b, ")", sizeof b - strlen(b) - 1); }
    else strncat(b, " D-", sizeof b - strlen(b) - 1);
    ev(c, "%s", b);
}
void dkCmdBufClearColor(DkCmdBuf c, uint32_t id, uint32_t mask, const void* data) {
    float f[4]; memcpy(f, data, 16);
    ev(c, "CLR %u m%u %.9g %.9g %.9g %.9g", id, mask, f[0], f[1], f[2], f[3]);
}
void dkCmdBufClearDepthStencil(DkCmdBuf c, bool cd, float z, uint8_t sm, uint8_t sv) { ev(c, "CDS %d %.9g %u %u", cd ? 1 : 0, z, sm, sv); }
void dkCmdBufSetViewports(DkCmdBuf c, uint32_t first, DkViewport const v[], uint32_t n) {
    ev(c, "VP f%u n%u %.9g %.9g %.9g %.9g %.9g %.9g", first, n, v[0].x, v[0].y, v[0].width, v[0].height, v[0].near, v[0].far);
}
void dkCmdBufSetScissors(DkCmdBuf c, uint32_t first, DkScissor const s[], uint32_t n) { ev(c, "SC f%u n%u %u %u %u %u", first, n, s[0].x, s[0].y, s[0].width, s[0].height); }
void dkCmdBufSetBlendConst(DkCmdBuf c, float r, float g, float b, float a) { ev(c, "BC %.9g %.9g %.9g %.9g", r, g, b, a); }
void dkCmdBufSetDepthBias(DkCmdBuf c, float k, float cl, float s) { ev(c, "DB %.9g %.9g %.9g", k, cl, s); }

/* ---- expected-string builders ------------------------------------------------------------------------------------------------------------ */
static char E[8192];
static void E0(void) { E[0] = 0; }
static void Ea(const char* fmt, ...) { size_t n = strlen(E); va_list ap; if (n) E[n++] = '|'; va_start(ap, fmt); vsnprintf(E + n, sizeof E - n, fmt, ap); va_end(ap); }
/* a view string with the fields dk_pass.c sets: type (0 none, 2 = DkImageType_2D), format (-1 = none), mip, layer */
static const char* V(uint32_t id, int type, int fmt, uint32_t mip, uint32_t layer) {
    static char b[8][128]; static int k;
    char* o = b[k++ & 7];
    snprintf(o, 128, "i%u/t%d/f%d/s%d%d%d%d/ds%d/l%u:0/m%u:1", id, type, fmt < 0 ? (int)DkImageFormat_None : fmt, (int)DkImageSwizzle_Red, (int)DkImageSwizzle_Green,
             (int)DkImageSwizzle_Blue, (int)DkImageSwizzle_Alpha, (int)DkDsSource_Depth, layer, mip);
    return o;
}
static const char* BIND(char cmd, const char* c0, const char* c1, const char* c2, const char* c3, const char* d) {
    static char b[8][700]; static int k;
    char* o = b[k++ & 7]; const char* c[4] = { c0, c1, c2, c3 }; int n = 0;
    for (int i = 0; i < 4; i++) n += c[i] != NULL;
    size_t m = (size_t)snprintf(o, 700, "@%c BIND n%d", cmd, n);
    for (int i = 0; i < 4; i++) if (c[i]) m += (size_t)snprintf(o + m, 700 - m, " C(%s)", c[i]);
    if (d) snprintf(o + m, 700 - m, " D(%s)", d); else snprintf(o + m, 700 - m, " D-");
    return o;
}
static const char* VP(char cmd, float x, float y, float w, float h) {
    static char b[8][100]; static int k; char* o = b[k++ & 7];
    snprintf(o, 100, "@%c VP f0 n1 %.9g %.9g %.9g %.9g %.9g %.9g", cmd, x, y, w, h, 0.0, 1.0); return o;
}
static const char* SC(char cmd, uint32_t x, uint32_t y, uint32_t w, uint32_t h) {
    static char b[8][100]; static int k; char* o = b[k++ & 7];
    snprintf(o, 100, "@%c SC f0 n1 %u %u %u %u", cmd, x, y, w, h); return o;
}
static const char* CLR(uint32_t id, uint32_t rgba) {
    static char b[8][120]; static int k; char* o = b[k++ & 7];
    snprintf(o, 120, "@A CLR %u m15 %.9g %.9g %.9g %.9g", id, (rgba & 255) / 255.0f, (rgba >> 8 & 255) / 255.0f, (rgba >> 16 & 255) / 255.0f, (rgba >> 24) / 255.0f);
    return o;
}
static const char* CDS(int z, float zv, int sm, int sv) {
    static char b[8][80]; static int k; char* o = b[k++ & 7];
    snprintf(o, 80, "@A CDS %d %.9g %d %d", z, zv, sm, sv); return o;
}
static uint32_t bits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
#define BEGIN_TAIL(w, h) Ea("%s", VP('A', 0.5f, 0.5f, (float)(w), (float)(h))); Ea("%s", SC('A', 0, 0, (w), (h))); Ea("@A BC 1 1 1 1"); Ea("@A DB 0 0 0")
#define PLAIN(id) V(id, 0, -1, 0, 0)

static void reset(void) {
    memset(&nxdk_s, 0, sizeof nxdk_s); nxdk_s.cmd = CMDA; env.swapImage = SWAPID;
    clear_log();
}
static void p4(const uint32_t* a, uint32_t n) { nxdk_op_begin_pass4(a, n); }
static void sub_is(const char* name, const NxDkSub* s, const NxDkImage* im, uint32_t id, uint32_t lv, uint32_t ly, uint32_t w, uint32_t h) {
    const int ok = s->im == im && s->id == id && s->level == lv && s->layer == ly && s->x == 0 && s->y == 0 && s->w == w && s->h == h;
    if (!ok) fprintf(stderr, "%s: im %p id %u lv %u ly %u %ux%u\n", name, (const void*)s->im, s->id, s->level, s->layer, s->w, s->h);
    CHK(name, ok);
}
static void pass_is(const char* name, uint32_t w, uint32_t h, uint32_t samples, uint32_t colors, uint32_t srgb) {
    const int ok = nxdk_s.inPass == 1 && nxdk_s.passW == w && nxdk_s.passH == h && nxdk_s.passSamples == samples && nxdk_s.passColors == colors &&
                   nxdk_s.passSrgb == srgb && nxdk_s.boundState == 0 &&
                   nxdk_s.scissor[0] == 0 && nxdk_s.scissor[1] == 0 && nxdk_s.scissor[2] == (int32_t)w && nxdk_s.scissor[3] == (int32_t)h;
    if (!ok) fprintf(stderr, "%s: in %u %ux%u s%u c%u srgb %u bs %u\n", name, nxdk_s.inPass, nxdk_s.passW, nxdk_s.passH, nxdk_s.passSamples, nxdk_s.passColors,
                     nxdk_s.passSrgb, nxdk_s.boundState);
    CHK(name, ok);
}
static int untouched(const NxDkStream* s0) { return !memcmp(s0, &nxdk_s, sizeof nxdk_s); }

int main(void) {
    env.image = chk_image; env.maxVtxStride = 2048; env.maxAttrOffset = 2047;
    nxdk.front.w = 1280; nxdk.front.h = 720; nxdk.front.mips = 1; nxdk.front.samples = 1; nxdk.front.fmt = NxFmt_RGBA8; nxdk.front.used = 1; nxdk.front.d = 1;
    mk(1, 64, 64, 7, 1, NxImgType_2D, NxFmt_RGBA8, NxImg_RenderTarget);
    mk(2, 64, 64, 1, 1, NxImgType_2D, NxFmt_BGRA8, NxImg_RenderTarget);
    mk(3, 64, 64, 1, 1, NxImgType_2D, NxFmt_BGRA8, 0); tab[3].isView = 1; tab[3].base = 2;   /* the sRGB view slot of image 2 */
    mk(4, 64, 64, 1, 1, NxImgType_2D, NxFmt_RGBA8, NxImg_RenderTarget);
    mk(5, 32, 32, 1, 1, NxImgType_Cube, NxFmt_RGBA8, NxImg_RenderTarget | NxImg_Cube);
    mk(6, 64, 64, 1, 1, NxImgType_3D, NxFmt_RGBA8, NxImg_RenderTarget);
    mk(7, 64, 64, 1, 1, NxImgType_2D, NxFmt_Z24S8, NxImg_Depth);
    mk(8, 64, 64, 1, 1, NxImgType_2D, NxFmt_Z16, NxImg_Depth);
    mk(9, 64, 64, 1, 1, NxImgType_2D, NxFmt_BC1, 0);
    mk(10, 64, 64, 1, 1, NxImgType_2D, NxFmt_A4R4G4B4, NxImg_RenderTarget);
    mk(11, 64, 64, 1, 4, NxImgType_2D, NxFmt_RGBA8, NxImg_RenderTarget);
    mk(12, 32, 32, 1, 1, NxImgType_2D, NxFmt_RGBA8, NxImg_RenderTarget);
    mk(13, 32, 32, 1, 1, NxImgType_2D, NxFmt_Z24S8, NxImg_Depth);
    mk(14, 64, 64, 1, 4, NxImgType_2D, NxFmt_Z24S8, NxImg_Depth);
    mk(15, 64, 64, 1, 1, NxImgType_2D, NxFmt_RGBA8, NxImg_RenderTarget);
    mk(16, 64, 64, 1, 1, NxImgType_2D, NxFmt_RGBA8, NxImg_RenderTarget);
    mk(17, 64, 64, 1, 1, NxImgType_2D, NxFmt_Z32F, NxImg_Depth);
    const int SRGB2 = (int)dk_fmt(NxFmt_BGRA8).srgb;
    NxDkStream s0;
    char nr[96];
    snprintf(nr, sizeof nr, "pass: target 0: format %u is not a render format of deko3d", (unsigned)NxFmt_A4R4G4B4);

    /* ---- BeginPass4: colour counts, with and without depth ---------------------------------------------------------------------------- */
    reset(); nxdk_s.boundState = 5;
    { const uint32_t w[] = { 1, 1, 0, 0, 0 }; p4(w, 5); }
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); BEGIN_TAIL(64, 64); eq("p4_one_colour", E);
    pass_is("p4_one_pass", 64, 64, 1, 1, 0);
    sub_is("p4_one_c0", &nxdk_s.passColor[0], &tab[1], 1, 0, 0, 64, 64);
    CHK("p4_one_nodepth", nxdk_s.passDepth.im == NULL && nerr == 0);

    reset();
    { const uint32_t w[] = { 2, 1, 0, 4, 0, 7, 0 }; p4(w, 7); }
    E0(); Ea("%s", BIND('A', PLAIN(1), PLAIN(4), 0, 0, PLAIN(7))); BEGIN_TAIL(64, 64); eq("p4_two_depth", E);
    pass_is("p4_two_pass", 64, 64, 1, 2, 0);
    sub_is("p4_two_c1", &nxdk_s.passColor[1], &tab[4], 4, 0, 0, 64, 64);
    sub_is("p4_two_depth_sub", &nxdk_s.passDepth, &tab[7], 7, 0, 0, 64, 64);

    reset();
    { const uint32_t w[] = { 3, 1, 0, 4, 0, 15, 0, 17, 0 }; p4(w, 9); }
    E0(); Ea("%s", BIND('A', PLAIN(1), PLAIN(4), PLAIN(15), 0, PLAIN(17))); BEGIN_TAIL(64, 64); eq("p4_three_depth", E);
    pass_is("p4_three_pass", 64, 64, 1, 3, 0);
    sub_is("p4_three_c2", &nxdk_s.passColor[2], &tab[15], 15, 0, 0, 64, 64);

    reset();
    { const uint32_t w[] = { 4, 1, 0, 4, 0, 15, 0, 16, 0, 0, 0 }; p4(w, 11); }
    E0(); Ea("%s", BIND('A', PLAIN(1), PLAIN(4), PLAIN(15), PLAIN(16), 0)); BEGIN_TAIL(64, 64); eq("p4_four_nodepth", E);
    pass_is("p4_four_pass", 64, 64, 1, 4, 0);
    sub_is("p4_four_c3", &nxdk_s.passColor[3], &tab[16], 16, 0, 0, 64, 64);

    reset();
    { const uint32_t w[] = { 4, 1, 0, 4, 0, 15, 0, 16, 0, 7, 0 }; p4(w, 11); }
    E0(); Ea("%s", BIND('A', PLAIN(1), PLAIN(4), PLAIN(15), PLAIN(16), PLAIN(7))); BEGIN_TAIL(64, 64); eq("p4_four_depth", E);

    reset();
    { const uint32_t w[] = { 0, 7, 0 }; p4(w, 3); }
    E0(); Ea("%s", BIND('A', 0, 0, 0, 0, PLAIN(7))); BEGIN_TAIL(64, 64); eq("p4_depth_only", E);
    CHK("p4_depth_only_fields", nxdk_s.passColors == 0 && nxdk_s.passColor[0].im == NULL && nxdk_s.passDepth.im == &tab[7] && nxdk_s.inPass == 1);

    reset();
    { const uint32_t w[] = { 1, 11, 0, 14, 0 }; p4(w, 5); }
    E0(); Ea("%s", BIND('A', PLAIN(11), 0, 0, 0, PLAIN(14))); BEGIN_TAIL(64, 64); eq("p4_multisample", E);
    pass_is("p4_ms_pass", 64, 64, 4, 1, 0);

    /* ---- level and layer subresources, swap image 0 --------------------------------------------------------------------------------- */
    reset();
    { const uint32_t w[] = { 1, 1, 3, 0, 0 }; p4(w, 5); }
    E0(); Ea("%s", BIND('A', V(1, 0, -1, 3, 0), 0, 0, 0, 0)); BEGIN_TAIL(8, 8); eq("p4_level3", E);
    sub_is("p4_level3_sub", &nxdk_s.passColor[0], &tab[1], 1, 3, 0, 8, 8);
    reset();
    { const uint32_t w[] = { 1, 1, 6, 0, 0 }; p4(w, 5); }
    E0(); Ea("%s", BIND('A', V(1, 0, -1, 6, 0), 0, 0, 0, 0)); BEGIN_TAIL(1, 1); eq("p4_level_last_1x1", E);
    reset();
    { const uint32_t w[] = { 1, 5, 4u << 16, 0, 0 }; p4(w, 5); }
    E0(); Ea("%s", BIND('A', V(5, (int)DkImageType_2D, -1, 0, 4), 0, 0, 0, 0)); BEGIN_TAIL(32, 32); eq("p4_cube_face4", E);
    sub_is("p4_cube_face_sub", &nxdk_s.passColor[0], &tab[5], 5, 0, 4, 32, 32);
    reset();
    { const uint32_t w[] = { 1, 1, 1, 13, 0 }; p4(w, 5); }
    E0(); Ea("%s", BIND('A', V(1, 0, -1, 1, 0), 0, 0, 0, PLAIN(13))); BEGIN_TAIL(32, 32); eq("p4_level_matches_depth", E);
    reset();
    { const uint32_t w[] = { 1, 0, 0, 0, 0 }; p4(w, 5); }
    E0(); Ea("%s", BIND('A', PLAIN(0), 0, 0, 0, 0)); BEGIN_TAIL(1280, 720); eq("p4_swap_image0", E);
    sub_is("p4_swap_sub", &nxdk_s.passColor[0], &nxdk.front, 0, 0, 0, 1280, 720);
    pass_is("p4_swap_pass", 1280, 720, 1, 1, 0);
    clear_log(); nxdk_pass_srgb(1);
    E0(); Ea("%s", BIND('A', PLAIN(0), 0, 0, 0, 0)); eq("swap_has_no_srgb_view", E);

    /* ---- BeginPass v3: every load action ------------------------------------------------------------------------------------------------ */
#define BP3(c, d, fl, rgba, z, s, w, h) do { const uint32_t w_[8] = { c, d, fl, rgba, z, s, w, h }; nxdk_op_begin_pass(w_, 8); } while (0)
    reset(); nxdk_s.boundState = 3;
    BP3(1, 0, 0, 0, 0, 0, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); BEGIN_TAIL(64, 64); eq("v3_no_clear", E);
    pass_is("v3_pass", 64, 64, 1, 1, 0);
    reset();
    BP3(1, 0, NxClear_Color, 0x80402010u, 0, 0, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CLR(0, 0x80402010u)); BEGIN_TAIL(64, 64); eq("v3_clear_colour", E);
    reset();
    BP3(1, 0, NxClear_Color | NxClear_Srgb, 0x80402010u, 0, 0, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CLR(0, 0x80402010u)); BEGIN_TAIL(64, 64);
    eq("v3_load_clear_ignores_srgb", E);
    reset();
    BP3(2, 7, NxClear_Depth, 0, bits(0.5f), 0x1AB, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(2), 0, 0, 0, PLAIN(7))); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CDS(1, 0.5f, 0, 0xAB)); BEGIN_TAIL(64, 64);
    eq("v3_clear_depth", E);
    reset();
    BP3(1, 7, NxClear_Stencil, 0, bits(0.25f), 0x1AB, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, PLAIN(7))); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CDS(0, 0.25f, 255, 0xAB)); BEGIN_TAIL(64, 64);
    eq("v3_clear_stencil", E);
    reset();
    BP3(1, 7, NxClear_Depth | NxClear_Stencil, 0, bits(0.75f), 7, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, PLAIN(7))); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CDS(1, 0.75f, 255, 7)); BEGIN_TAIL(64, 64);
    eq("v3_clear_depth_stencil", E);
    reset();
    BP3(1, 7, NxClear_Color | NxClear_Depth | NxClear_Stencil, 0xFF00FF00u, bits(1.0f), 0, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, PLAIN(7))); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CLR(0, 0xFF00FF00u)); Ea("%s", CDS(1, 1.0f, 255, 0));
    BEGIN_TAIL(64, 64); eq("v3_clear_all", E);
    reset();
    BP3(1, 8, NxClear_Stencil, 0, bits(1.0f), 5, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, PLAIN(8))); Ea("%s", SC('A', 0, 0, 64, 64)); BEGIN_TAIL(64, 64); eq("v3_stencil_without_stencil_format", E);
    reset();
    BP3(1, 8, NxClear_Depth | NxClear_Stencil, 0, bits(1.0f), 5, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, PLAIN(8))); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CDS(1, 1.0f, 0, 5)); BEGIN_TAIL(64, 64);
    eq("v3_depth_on_z16", E);
    reset();
    BP3(1, 0, NxClear_Depth | NxClear_Stencil, 0, bits(1.0f), 5, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); Ea("%s", SC('A', 0, 0, 64, 64)); BEGIN_TAIL(64, 64); eq("v3_depth_clear_without_depth_buffer", E);
    reset();
    BP3(1, 0, NxClear_Color | NxClear_Depth, 0x01020304u, 0, 0, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CLR(0, 0x01020304u)); BEGIN_TAIL(64, 64);
    eq("v3_colour_clear_without_depth_buffer", E);
    reset();
    BP3(0, 0, 0, 0, 0, 0, 1280, 720);
    E0(); Ea("%s", BIND('A', PLAIN(0), 0, 0, 0, 0)); BEGIN_TAIL(1280, 720); eq("v3_swap_image0", E);
    sub_is("v3_swap_sub", &nxdk_s.passColor[0], &nxdk.front, 0, 0, 0, 1280, 720);
    reset();
    BP3(1, 0, 0, 0, 0, 0, 32, 16);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); BEGIN_TAIL(32, 16); eq("v3_viewport_from_words", E);
    pass_is("v3_size_words_pass", 32, 16, 1, 1, 0);
    reset(); nxdk_s.passSrgb = 1;
    BP3(2, 0, 0, 0, 0, 0, 64, 64);
    E0(); Ea("%s", BIND('A', PLAIN(2), 0, 0, 0, 0)); BEGIN_TAIL(64, 64); eq("v3_starts_srgb_off", E);
    CHK("v3_starts_srgb_off_field", nxdk_s.passSrgb == 0);
    reset(); nxdk_s.passSrgb = 1;
    { const uint32_t w[] = { 1, 2, 0, 0, 0 }; p4(w, 5); }
    CHK("p4_starts_srgb_off", nxdk_s.passSrgb == 0);

    /* ---- sRGB on / off / no change -------------------------------------------------------------------------------------------------- */
    reset();
    { const uint32_t w[] = { 2, 2, 0, 1, 0, 7, 0 }; p4(w, 7); }
    clear_log(); nxdk_pass_srgb(1);
    E0(); Ea("%s", BIND('A', V(2, 0, SRGB2, 0, 0), PLAIN(1), 0, 0, PLAIN(7))); eq("srgb_on", E);
    CHK("srgb_on_field", nxdk_s.passSrgb == 1);
    clear_log(); nxdk_pass_srgb(1); eq("srgb_no_change_on", "");
    clear_log(); nxdk_pass_srgb(9); eq("srgb_nonzero_is_on", "");
    clear_log(); nxdk_pass_srgb(0);
    E0(); Ea("%s", BIND('A', PLAIN(2), PLAIN(1), 0, 0, PLAIN(7))); eq("srgb_off", E);
    CHK("srgb_off_field", nxdk_s.passSrgb == 0);
    clear_log(); nxdk_pass_srgb(0); eq("srgb_no_change_off", "");
    clear_log(); nxdk_pass_srgb(7);
    E0(); Ea("%s", BIND('A', V(2, 0, SRGB2, 0, 0), PLAIN(1), 0, 0, PLAIN(7))); eq("srgb_nonzero_binds_on", E);
    reset();
    { const uint32_t w[] = { 1, 1, 0, 0, 0 }; p4(w, 5); }
    clear_log(); nxdk_pass_srgb(1);
    E0(); Ea("%s", BIND('A', PLAIN(1), 0, 0, 0, 0)); eq("srgb_without_view_slot_plain", E);
    CHK("srgb_without_view_field", nxdk_s.passSrgb == 1);
    reset(); nxdk_pass_srgb(1); rej("srgb_outside_pass", "pass_srgb: no open pass");

    /* ---- every nxchk rejection class: one nxdk_err, nothing recorded, no state change ------------------------------------------------------ */
#define REJ4(name, n, msg, ...) do { const uint32_t w_[] = { __VA_ARGS__ }; reset(); nxdk_s.inPass = 1; nxdk_s.passColors = 77; nxdk_s.passW = 5; nxdk_s.boundState = 9; \
        s0 = nxdk_s; p4(w_, n); rej(name, msg); CHK(name "_state", untouched(&s0)); } while (0)
    REJ4("rej_argc0", 0, "BeginPass4: 0 words for 0 colour targets", 9);
    REJ4("rej_count5", 13, "BeginPass4: 13 words for 5 colour targets", 5, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0);
    REJ4("rej_short", 4, "BeginPass4: 4 words for 2 colour targets", 2, 1, 0, 4);
    REJ4("rej_no_target", 3, "pass: no target", 0, 0, 0);
    REJ4("rej_dead_image", 5, "pass: target 0 (image 30) is not a colour image", 1, 30, 0, 0, 0);
    REJ4("rej_view_image", 5, "pass: target 0 (image 3) is not a colour image", 1, 3, 0, 0, 0);
    REJ4("rej_depth_as_colour", 5, "pass: target 0 (image 7) is not a colour image", 1, 7, 0, 0, 0);
    REJ4("rej_colour_as_depth", 5, "pass: target 1 (image 1) is not a depth-stencil image", 1, 4, 0, 1, 0);
    REJ4("rej_compressed", 5, "pass: target 0 (image 9) is not a colour image", 1, 9, 0, 0, 0);
    REJ4("rej_level_range", 5, "pass: target 0: level 7 layer 0 outside image 1", 1, 1, 7, 0, 0);
    REJ4("rej_layer_2d", 5, "pass: target 0: level 0 layer 1 outside image 1", 1, 1, 1u << 16, 0, 0);
    REJ4("rej_layer_cube", 5, "pass: target 0: level 0 layer 6 outside image 5", 1, 5, 6u << 16, 0, 0);
    REJ4("rej_size_mismatch", 7, "pass: target 1 is 32x32 x1 samples, the pass is 64x64 x1", 2, 1, 0, 12, 0, 0, 0);
    REJ4("rej_sample_mismatch", 7, "pass: target 1 is 64x64 x4 samples, the pass is 64x64 x1", 2, 1, 0, 11, 0, 0, 0);
    REJ4("rej_depth_size_mismatch", 5, "pass: target 1 is 32x32 x1 samples, the pass is 64x64 x1", 1, 1, 0, 13, 0);
    REJ4("rej_depth_sample_mismatch", 5, "pass: target 1 is 64x64 x4 samples, the pass is 64x64 x1", 1, 1, 0, 14, 0);
    REJ4("rej_depth_level_range", 5, "pass: target 1: level 1 layer 0 outside image 7", 1, 4, 0, 7, 1);
    REJ4("rej_3d_target", 5, "pass: target 0 is a 3D image, deko3d has no single-slice view of one", 1, 6, 0, 0, 0);
    REJ4("rej_3d_second_target", 7, "pass: target 1 is a 3D image, deko3d has no single-slice view of one", 2, 1, 0, 6, 0, 0, 0);
    REJ4("rej_non_render_format", 5, nr, 1, 10, 0, 0, 0);
    reset(); env.swapImage = 0; nxdk_s.inPass = 1; nxdk_s.passColors = 77; s0 = nxdk_s;
    { const uint32_t w[] = { 1, 0, 0, 0, 0 }; p4(w, 5); }
    rej("rej_swap_missing", "BeginPass4: colour target 0 is the swapchain, which does not exist"); CHK("rej_swap_missing_state", untouched(&s0));
    reset(); env.swapImage = 0; BP3(0, 0, 0, 0, 0, 0, 8, 8);
    rej("rej_swap_missing_v3", "pass: target 0 (image 0) is not a colour image");
    reset();
    BP3(1, 1, 0, 0, 0, 0, 64, 64); rej("rej_v3_depth_is_colour", "pass: target 1 (image 1) is not a depth-stencil image");
    reset(); { const uint32_t w[7] = { 1, 1, 0, 0, 0, 0, 64 }; nxdk_op_begin_pass(w, 7); } rej("rej_v3_words", "BeginPass: 7 words");
    reset(); BP3(6, 0, 0, 0, 0, 0, 64, 64); rej("rej_v3_3d", "pass: target 0 is a 3D image, deko3d has no single-slice view of one");
    reset(); BP3(10, 0, 0, 0, 0, 0, 64, 64); rej("rej_v3_non_render", nr);
    reset(); BP3(1, 0, NxClear_Color, 0xFFFFFFFFu, 0, 0, 64, 64); nxdk_s.inPass = 0; clear_log();
    BP3(30, 0, NxClear_Color, 0, 0, 0, 64, 64); rej("rej_v3_dead_image_no_clear", "pass: target 0 (image 30) is not a colour image");

    /* ---- Clear: exact arguments --------------------------------------------------------------------------------------------------------- */
#define CLEARV(fl, rgba, z, s, x, y, w, h) do { const uint32_t w_[8] = { fl, rgba, z, s, x, y, w, h }; nxdk_op_clear(w_, 8); } while (0)
    reset();
    { const uint32_t w[] = { 2, 2, 0, 1, 0, 7, 0 }; p4(w, 7); }
    clear_log(); CLEARV(NxClear_Color, 0x80402010u, 0, 0, 3, 4, 20, 10);
    E0(); Ea("%s", SC('A', 3, 4, 20, 10)); Ea("%s", CLR(0, 0x80402010u)); Ea("%s", CLR(1, 0x80402010u)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_colour_all_targets", E);
    clear_log(); CLEARV(NxClear_Color | NxClear_Srgb, 0x80402010u, 0, 0, 1, 2, 3, 4);
    E0(); Ea("%s", SC('A', 1, 2, 3, 4)); Ea("%s", BIND('A', V(2, 0, SRGB2, 0, 0), PLAIN(1), 0, 0, PLAIN(7))); Ea("%s", CLR(0, 0x80402010u)); Ea("%s", CLR(1, 0x80402010u));
    Ea("%s", BIND('A', PLAIN(2), PLAIN(1), 0, 0, PLAIN(7))); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_srgb_rebinds_and_restores", E);
    CHK("clear_srgb_field_unchanged", nxdk_s.passSrgb == 0);
    nxdk_pass_srgb(1); clear_log(); CLEARV(NxClear_Color | NxClear_Srgb, 0x11223344u, 0, 0, 0, 0, 64, 64);
    E0(); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CLR(0, 0x11223344u)); Ea("%s", CLR(1, 0x11223344u)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_srgb_when_pass_srgb", E);
    clear_log(); CLEARV(NxClear_Color, 0x11223344u, 0, 0, 5, 6, 7, 8);
    E0(); Ea("%s", SC('A', 5, 6, 7, 8)); Ea("%s", BIND('A', PLAIN(2), PLAIN(1), 0, 0, PLAIN(7))); Ea("%s", CLR(0, 0x11223344u)); Ea("%s", CLR(1, 0x11223344u));
    Ea("%s", BIND('A', V(2, 0, SRGB2, 0, 0), PLAIN(1), 0, 0, PLAIN(7))); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_linear_when_pass_srgb", E);
    nxdk_pass_srgb(0);
    clear_log(); CLEARV(NxClear_Depth, 0, bits(0.5f), 0x1CD, 8, 9, 10, 11);
    E0(); Ea("%s", SC('A', 8, 9, 10, 11)); Ea("%s", CDS(1, 0.5f, 0, 0xCD)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_depth", E);
    clear_log(); CLEARV(NxClear_Stencil, 0, bits(0.5f), 0x1CD, 8, 9, 10, 11);
    E0(); Ea("%s", SC('A', 8, 9, 10, 11)); Ea("%s", CDS(0, 0.5f, 255, 0xCD)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_stencil", E);
    clear_log(); CLEARV(NxClear_Color | NxClear_Depth | NxClear_Stencil, 0xAABBCCDDu, bits(0.125f), 3, 0, 0, 64, 64);
    E0(); Ea("%s", SC('A', 0, 0, 64, 64)); Ea("%s", CLR(0, 0xAABBCCDDu)); Ea("%s", CLR(1, 0xAABBCCDDu)); Ea("%s", CDS(1, 0.125f, 255, 3)); Ea("%s", SC('A', 0, 0, 64, 64));
    eq("clear_all", E);
    clear_log(); CLEARV(0, 0xAABBCCDDu, bits(0.125f), 3, 1, 1, 2, 2);
    E0(); Ea("%s", SC('A', 1, 1, 2, 2)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_no_flags", E);
    reset();
    { const uint32_t w[] = { 1, 2, 0, 8, 0 }; p4(w, 5); }
    clear_log(); CLEARV(NxClear_Depth | NxClear_Stencil, 0, bits(0.5f), 9, 1, 2, 3, 4);
    E0(); Ea("%s", SC('A', 1, 2, 3, 4)); Ea("%s", CDS(1, 0.5f, 0, 9)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_z16_no_stencil", E);
    clear_log(); CLEARV(NxClear_Stencil, 0, 0, 9, 1, 2, 3, 4);
    E0(); Ea("%s", SC('A', 1, 2, 3, 4)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_z16_stencil_only_nothing", E);
    reset();
    { const uint32_t w[] = { 1, 4, 0, 0, 0 }; p4(w, 5); }
    clear_log(); CLEARV(NxClear_Depth | NxClear_Stencil, 0, 0, 0, 0, 0, 4, 4);
    E0(); Ea("%s", SC('A', 0, 0, 4, 4)); Ea("%s", SC('A', 0, 0, 64, 64)); eq("clear_without_depth_buffer", E);
    reset(); BP3(1, 7, 0, 0, 0, 0, 32, 16); clear_log();
    nxdk_s.boundState = 1; nxdk_s.scissor[0] = 3; nxdk_s.scissor[1] = 4; nxdk_s.scissor[2] = 5; nxdk_s.scissor[3] = 6;
    nxdk_s.st[1].rs.w[2] = 1ull << NXRS_R_SCISSOR;
    CLEARV(NxClear_Depth, 0, bits(1.0f), 0, 0, 0, 8, 8);
    E0(); Ea("%s", SC('A', 0, 0, 8, 8)); Ea("%s", CDS(1, 1.0f, 0, 0)); Ea("%s", SC('A', 3, 4, 5, 6)); eq("clear_restores_state_scissor", E);
    CHK("clear_keeps_bound_state", nxdk_s.boundState == 1);
    clear_log(); nxdk_s.st[1].rs.w[2] = 0;
    CLEARV(NxClear_Depth, 0, bits(1.0f), 0, 0, 0, 8, 8);
    E0(); Ea("%s", SC('A', 0, 0, 8, 8)); Ea("%s", CDS(1, 1.0f, 0, 0)); Ea("%s", SC('A', 0, 0, 32, 16)); eq("clear_restores_whole_pass", E);
    clear_log(); nxdk_s.boundState = 0; nxdk_s.st[0].rs.w[2] = ~0ull; nxdk_s.st[1].rs.w[2] = ~0ull;
    CLEARV(NxClear_Depth, 0, bits(1.0f), 0, 0, 0, 8, 8);
    E0(); Ea("%s", SC('A', 0, 0, 8, 8)); Ea("%s", CDS(1, 1.0f, 0, 0)); Ea("%s", SC('A', 0, 0, 32, 16)); eq("clear_no_state_whole_pass", E);
    reset(); CLEARV(NxClear_Color, 0, 0, 0, 0, 0, 8, 8); rej("rej_clear_no_pass", "Clear: no open pass");
    reset(); { const uint32_t w[7] = { 1, 0, 0, 0, 0, 0, 0 }; nxdk_op_clear(w, 7); } rej("rej_clear_words", "Clear: 7 words");
    reset(); nxdk_s.inPass = 1; { const uint32_t w[7] = { 1, 0, 0, 0, 0, 0, 0 }; nxdk_op_clear(w, 7); } rej("rej_clear_words_in_pass", "Clear: 7 words");

    /* ---- EndPass ---------------------------------------------------------------------------------------------------------------------- */
    reset();
    { const uint32_t w[] = { 1, 1, 0, 0, 0 }; p4(w, 5); }
    nxdk_s.boundState = 4; clear_log();
    nxdk_op_end_pass(NULL, 0);
    CHK("end_pass_resets", nxdk_s.inPass == 0 && nxdk_s.boundState == 0 && lg[0] == 0 && nerr == 0);
    CLEARV(NxClear_Color, 0, 0, 0, 0, 0, 8, 8); rej("clear_after_end_rejected", "Clear: no open pass");
    clear_log(); nxdk_pass_srgb(1); rej("srgb_after_end_rejected", "pass_srgb: no open pass");
    reset(); nxdk_op_end_pass(NULL, 0); CHK("end_pass_without_pass", nxdk_s.inPass == 0 && lg[0] == 0 && nerr == 0);

    /* ---- nxdk_target_bind ------------------------------------------------------------------------------------------------------------------------- */
    reset();
    { const NxDkSub d = { &tab[1], 1, 2, 0, 0, 0, 3, 3 }; nxdk_target_bind(CMDB, &d); }
    E0(); Ea("%s", BIND('B', V(1, 0, -1, 2, 0), 0, 0, 0, 0)); Ea("%s", VP('B', 0, 0, 16, 16)); Ea("%s", SC('B', 0, 0, 16, 16));
    eq("target_bind_level_full_size", E);
    CHK("target_bind_pass_untouched", nxdk_s.inPass == 0 && nxdk_s.passColors == 0 && nxdk_s.boundState == 0);
    reset();
    { const NxDkSub d = { &tab[5], 5, 0, 3, 0, 0, 32, 32 }; nxdk_target_bind(CMDB, &d); }
    E0(); Ea("%s", BIND('B', V(5, (int)DkImageType_2D, -1, 0, 3), 0, 0, 0, 0)); Ea("%s", VP('B', 0, 0, 32, 32)); Ea("%s", SC('B', 0, 0, 32, 32));
    eq("target_bind_cube_face", E);
    reset();
    { const NxDkSub d = { &tab[2], 2, 0, 0, 0, 0, 64, 64 }; nxdk_target_bind(CMDB, &d); }
    E0(); Ea("%s", BIND('B', PLAIN(2), 0, 0, 0, 0)); Ea("%s", VP('B', 0, 0, 64, 64)); Ea("%s", SC('B', 0, 0, 64, 64)); eq("target_bind_never_srgb", E);
    reset();
    { const NxDkSub d = { &nxdk.front, 0, 0, 0, 0, 0, 1280, 720 }; nxdk_target_bind(CMDB, &d); }
    E0(); Ea("%s", BIND('B', PLAIN(0), 0, 0, 0, 0)); Ea("%s", VP('B', 0, 0, 1280, 720)); Ea("%s", SC('B', 0, 0, 1280, 720)); eq("target_bind_front", E);
    reset();
    { const uint32_t w[] = { 1, 1, 0, 0, 0 }; p4(w, 5); }
    clear_log(); s0 = nxdk_s;
    { const NxDkSub d = { &tab[4], 4, 0, 0, 0, 0, 64, 64 }; nxdk_target_bind(CMDB, &d); }
    rej("rej_target_bind_in_pass", "target_bind: inside an open pass"); CHK("rej_target_bind_in_pass_state", untouched(&s0));
    reset();
    { const NxDkSub d = { &tab[6], 6, 0, 0, 0, 0, 64, 64 }; nxdk_target_bind(CMDB, &d); }
    rej("rej_target_bind_3d", "pass: target 0 is a 3D image, deko3d has no single-slice view of one");
    reset();
    { const NxDkSub d = { &tab[10], 10, 0, 0, 0, 0, 64, 64 }; nxdk_target_bind(CMDB, &d); }
    rej("rej_target_bind_non_render", nr);
    printf("NCHK %d\n", nchk);
    return 0;
}
