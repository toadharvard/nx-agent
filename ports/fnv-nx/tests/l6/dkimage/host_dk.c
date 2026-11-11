/* host_dk.c: host harness of the image files of the deko3d backend (dk_image.c, dk_xfer.c, dk_sampler.c, dk_read.c, dk_fmt.c) over the functional double of
 * dk_double.h. Two modes:
 *   host_dk selftest   prints "CHK name 0|1" and "NCHK n": recorded call arguments, round trips through the double, error cases, cleanup, the X cases
 *   host_dk script     reads commands from stdin (the same script is run against libnxgl4.so by test_dkimage.py) and prints one result line per command:
 *     f                                          F fmt flags bpb usage2d srgb          (the format table of the backend)
 *     c idx w h d type fmt mips samples swz      create4 then destroy: R idx id / D idx rc
 *     k idx w h d type fmt mips samples          create4 and keep: R idx id
 *     v idx fmt cube w h mips seed               v3 create with a pattern blob, read every face and level: R idx id, H idx level face rc hash
 *     vb idx fmt w h seed                        v3 create (2D), readback in the four formats: R idx id, B idx format rc hash
 *     d id | r id level layer x y z w h d size pitch nodst | b id x y w h format size      E rc
 *     s filter aniso wrap minLod maxLod bias border | u id filter ...                       S id | E rc
 * and at the end "LIVE n" (memory blocks still alive after nxdk_img_shutdown: 0). */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk_double.h"
#include "nxgpu4_check.h"

NxDk nxdk;
void nxchk_err(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap);
    va_end(ap);
    nxdk.errCount++;
}
void* nxdk_guest_ptr(uint64_t addr, uint64_t len) { (void)len; return (void*)(uintptr_t)addr; }

static int nchk;
static void chk(const char* name, int ok) { printf("CHK %s %d\n", name, ok ? 1 : 0); nchk++; }
#define P(x) ((uint64_t)(uintptr_t)(x))

static void setup(void) {
    nxdk.dev = (DkDevice)(uintptr_t)1;
    nxdk.q = (DkQueue)(uintptr_t)1;
    if (nxdk_img_init()) die("img_init failed");
}
static uint8_t g_flags;
static uint32_t mk(uint32_t w, uint32_t h, uint32_t d, uint32_t type, uint32_t fmt, uint32_t mips, uint32_t samples, uint32_t swz) {
    const NxImageDesc4 e = { .width = (uint16_t)w, .height = (uint16_t)h, .depth = (uint16_t)d, .type = (uint8_t)type, .format = (uint8_t)fmt,
                             .mips = (uint8_t)mips, .samples = (uint8_t)samples, .flags = g_flags, .swizzle = (uint16_t)swz };
    return (uint32_t)nxdk_image_create4(P(&e));
}
static uint64_t rd(uint32_t id, uint32_t lv, uint32_t ly, const uint32_t b[6], void* dst, uint32_t size, uint32_t pitch) {
    NxImageRead r;
    memset(&r, 0, sizeof r);
    r.src.image = id; r.src.level = (uint16_t)lv; r.src.layer = (uint16_t)ly;
    r.x = b[0]; r.y = b[1]; r.z = b[2]; r.w = b[3]; r.h = b[4]; r.d = b[5];
    r.dst = P(dst); r.dst_size = size; r.rowPitch = pitch;
    return nxdk_image_read(P(&r));
}
static uint64_t rb_(uint32_t id, uint32_t x, uint32_t y, uint32_t w, uint32_t h, uint32_t fmt, void* dst, uint32_t size) {
    const NxReadback r = { id, x, y, w, h, fmt, P(dst), size, 0 };
    return nxdk_readback(P(&r));
}
static uint32_t fnv(const uint8_t* p, uint64_t n) { uint32_t h = 2166136261u; while (n--) h = (h ^ *p++) * 16777619u; return h; }
static void pattern(uint8_t* p, uint64_t n, uint32_t seed) { for (uint64_t i = 0; i < n; i++) p[i] = (uint8_t)(i * 131 + seed * 17 + 7 + (i >> 8) * 29); }
/* a float depth texel is a depth value (exponent 0x70..0x7E, sign clear); the word after a Z32FS8 depth keeps only the stencil byte: same rule as pattern() in test_dkimage.py */
static void depth_fix(uint32_t fmt, uint8_t* p, uint64_t n) {
    if (fmt != 12 && fmt != 34) return;
    for (uint64_t w = 0; w + 4 <= n; w += 4) {
        uint32_t v; memcpy(&v, p + w, 4);
        if (fmt == 34 && ((w >> 2) & 1)) v &= 0xFF; else v = (v & 0x007FFFFFu) | ((0x70u + ((v >> 23) % 15u)) << 23);
        memcpy(p + w, &v, 4);
    }
}
static uint64_t blob_bytes(const DkFmt* f, uint32_t w, uint32_t h, uint32_t mips, uint32_t faces) {
    uint64_t n = 0;
    for (uint32_t l = 0; l < mips; l++) n += (uint64_t)nxdk_row_bytes(f, nxchk_lvl(w, l)) * nxdk_rows(f, nxchk_lvl(h, l));
    return n * faces;
}
static uint32_t mk3(uint32_t fmt, uint32_t cube, uint32_t w, uint32_t h, uint32_t mips, const uint8_t* data, uint64_t size) {
    const NxImageDesc d = { .width = (uint16_t)w, .height = (uint16_t)h, .format = (uint8_t)fmt, .mips = (uint8_t)mips, .flags = (uint8_t)(cube ? NxImg_Cube : 0),
                            .data = P(data), .data_size = (uint32_t)size };
    return (uint32_t)nxdk_image_create(P(&d));
}
static int has(const char* tag) { return strstr(nxdk.lastErr, tag) != NULL; }
static uint32_t srgb_fmt(void) { for (uint32_t i = 1; i < NxFmt_Count; i++) if (nx_format_has_srgb(i) && dk_fmt(i).srgb != DkImageFormat_None) return i; return 0; }

/* ---- the script mode ------------------------------------------------------------------------------------------------------------------------- */
static void script_v(uint64_t* a, int reads) {
    const uint32_t idx = (uint32_t)a[0], fmt = (uint32_t)a[1], cube = (uint32_t)a[2], w = (uint32_t)a[3], h = (uint32_t)a[4], mips = a[5] ? (uint32_t)a[5] : 1;
    const DkFmt f = dk_fmt(fmt);
    const uint32_t faces = cube ? 6 : 1;
    const uint64_t sz = f.format ? blob_bytes(&f, w, h, mips, faces) : 16;
    uint8_t* blob = (uint8_t*)malloc(sz + 16);
    pattern(blob, sz + 16, (uint32_t)a[6]); depth_fix(fmt, blob, sz + 16);
    const uint32_t id = mk3(fmt, cube, w, h, mips, blob, sz);
    printf("R %u %u\n", idx, id);
    if (!reads) { free(blob); return; }
    for (uint32_t face = 0; id && face < faces; face++)
        for (uint32_t l = 0; l < mips; l++) {
            const uint32_t b[6] = { 0, 0, 0, nxchk_lvl(w, l), nxchk_lvl(h, l), 1 };
            const uint32_t rb = nxdk_row_bytes(&f, b[3]), size = rb * nxdk_rows(&f, b[4]);
            uint8_t* o = (uint8_t*)malloc(size);
            memset(o, 0xCD, size);
            const uint64_t rc = rd(id, l, face, b, o, size, rb);
            printf("H %u %u %u %llu %u\n", idx, l, face, (unsigned long long)rc, fnv(o, size));
            free(o);
        }
    if (id) printf("D %u %llu\n", idx, (unsigned long long)nxdk_image_destroy(id));
    free(blob);
}
static void script_vb(uint64_t* a) {
    const uint32_t idx = (uint32_t)a[0], fmt = (uint32_t)a[1], w = (uint32_t)a[2], h = (uint32_t)a[3];
    const DkFmt f = dk_fmt(fmt);
    const uint64_t sz = blob_bytes(&f, w, h, 1, 1);
    uint8_t* blob = (uint8_t*)malloc(sz + 16);
    pattern(blob, sz + 16, (uint32_t)a[4]); depth_fix(fmt, blob, sz + 16);
    const uint32_t id = mk3(fmt, 0, w, h, 1, blob, sz);
    printf("R %u %u\n", idx, id);
    for (uint32_t k = 0; id && k < 4; k++) {
        const uint32_t size = w * h * nx_readback_bpp(k);
        uint8_t* o = (uint8_t*)malloc(size + 1);
        memset(o, 0xCD, size);
        const uint64_t rc = rb_(id, 0, 0, w, h, k, o, size);
        printf("B %u %u %llu ", idx, k, (unsigned long long)rc);
        if (k < 3) { putchar("GFD"[k]); for (uint32_t b = 0; b < size; b++) printf("%02x", o[b]); putchar('\n'); }
        else printf("%u\n", fnv(o, size));
        free(o);
    }
    if (id) nxdk_image_destroy(id);
    free(blob);
}
static NxSamplerState smp_of(const uint64_t* a) {
    NxSamplerState s;
    memset(&s, 0, sizeof s);
    s.filter = (uint8_t)a[0]; s.maxAniso = (uint8_t)a[1]; s.wrap = (uint16_t)a[2]; s.minLod = (uint8_t)a[3]; s.maxLod = (uint8_t)a[4];
    s.lodBias = (float)((int64_t)a[5]) / 8.0f; s.border = (uint32_t)a[6];
    return s;
}
static int script(void) {
    char line[512];
    while (fgets(line, sizeof line, stdin)) {
        if (line[0] == '#') { fputs(line, stdout); continue; }   /* a block marker: echoed, so the output can be cut into blocks */
        uint64_t a[16] = { 0 };
        int n = 0;
        char* tok = strtok(line + 1, " \n");
        while (tok && n < 16) { a[n++] = (uint64_t)strtoll(tok, NULL, 0); tok = strtok(NULL, " \n"); }
        switch (line[0]) {
        case 'f':
            for (uint32_t i = 1; i < NxFmt_Count; i++) {
                const DkFmt f = dk_fmt(i);
                printf("F %u %d %d %d %u %d %d\n", i, (f.flags & DKFMT_RENDER) != 0, (f.flags & DKFMT_DEPTH) != 0, (f.flags & DKFMT_COMPRESSED) != 0, f.bpb, (dkImageFormatGetFlags(f.format) & DkImageFlags_Usage2DEngine) != 0, nx_format_has_srgb(i) && f.srgb != DkImageFormat_None);
            }
            break;
        case 'c': case 'k': {
            g_flags = (uint8_t)a[9];
            const uint32_t id = mk((uint32_t)a[1], (uint32_t)a[2], (uint32_t)a[3], (uint32_t)a[4], (uint32_t)a[5], (uint32_t)a[6], (uint32_t)a[7], (uint32_t)a[8]);
            printf("R %u %u\n", (uint32_t)a[0], id);
            if (id && line[0] == 'c') printf("D %u %llu\n", (uint32_t)a[0], (unsigned long long)nxdk_image_destroy(id));
            break; }
        case 'v': script_v(a, 1); break;
        case 'm': script_v(a, 0); break;
        case 'w': script_vb(a); break;
        case 'd': printf("E %llu\n", (unsigned long long)nxdk_image_destroy(a[0])); break;
        case 'r': {
            const uint32_t b[6] = { (uint32_t)a[3], (uint32_t)a[4], (uint32_t)a[5], (uint32_t)a[6], (uint32_t)a[7], (uint32_t)a[8] };
            uint8_t* o = (uint8_t*)calloc(1, a[9] + 16);
            printf("E %llu\n", (unsigned long long)rd((uint32_t)a[0], (uint32_t)a[1], (uint32_t)a[2], b, a[11] ? NULL : o, (uint32_t)a[9], (uint32_t)a[10]));
            free(o);
            break; }
        case 'b': {
            uint8_t* o = (uint8_t*)calloc(1, a[6] + 16);
            printf("E %llu\n", (unsigned long long)rb_((uint32_t)a[0], (uint32_t)a[1], (uint32_t)a[2], (uint32_t)a[3], (uint32_t)a[4], (uint32_t)a[5], o, (uint32_t)a[6]));
            free(o);
            break; }
        case 's': { const NxSamplerState s = smp_of(a); printf("S %llu\n", (unsigned long long)nxdk_sampler_create(P(&s))); break; }
        case 'u': { const NxSamplerState s = smp_of(a + 1); printf("E %llu\n", (unsigned long long)nxdk_sampler_update(a[0], P(&s))); break; }
        default: die("script: unknown command %c", line[0]);
        }
    }
    return 0;
}

/* ---- the selftest ------------------------------------------------------------------------------------------------------------------------------ */
static const DkImageLayoutMaker* last_lay(void) { return &lay_log[(n_lay - 1) & 63]; }

static void t_layouts(void) {
    const DkFmt rgba = dk_fmt(NxFmt_RGBA8);
    uint32_t id = mk(64, 32, 1, NxImgType_2D, NxFmt_RGBA8, 3, 1, 0);
    const DkImageLayoutMaker* m = last_lay();
    chk("lay_2d", id == 1 && m->type == DkImageType_2D && m->dimensions[0] == 64 && m->dimensions[1] == 32 && m->dimensions[2] == 1 && m->mipLevels == 3 && m->format == rgba.format && m->msMode == DkMsMode_1x);
    chk("lay_2d_usage", (m->flags & DkImageFlags_UsageRender) && (m->flags & DkImageFlags_Usage2DEngine));
    chk("blk_flags_size", last_init.blk_flags == (DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image) && last_init.blk_size >= last_init.lay_size && !(last_init.blk_size & 4095));
    chk("info_layers_mips", nxdk.img[id].layers == 1 && nxdk.img[id].mips == 3 && nxdk.img[id].used && !nxdk.img[id].isView);
    nxdk_image_destroy(id);
    id = mk(16, 16, 1, NxImgType_Cube, NxFmt_RGBA8, 5, 1, 0);
    m = last_lay();
    chk("lay_cube", id && m->type == DkImageType_Cubemap && m->dimensions[2] == 6 && m->dimensions[0] == 16 && m->mipLevels == 5 && nxdk.img[id].layers == 6 && (nxdk.img[id].flags & NxImg_Cube));
    nxdk_image_destroy(id);
    id = mk(8, 8, 8, NxImgType_3D, NxFmt_RGBA8, 4, 1, 0);
    m = last_lay();
    chk("lay_3d", id && m->type == DkImageType_3D && m->dimensions[2] == 8 && m->mipLevels == 4 && !(m->flags & DkImageFlags_Usage2DEngine));
    nxdk_image_destroy(id);
    id = mk(16, 16, 1, NxImgType_2D, NxFmt_RGBA8, 1, 4, 0);
    m = last_lay();
    chk("lay_ms4", id && m->type == DkImageType_2DMS && m->msMode == DkMsMode_4x && (m->flags & DkImageFlags_UsageRender));
    nxdk_image_destroy(id);
    id = mk(16, 16, 1, NxImgType_2D, NxFmt_RGBA8, 1, 2, 0);
    chk("lay_ms2", id && last_lay()->msMode == DkMsMode_2x);
    nxdk_image_destroy(id);
    id = mk(16, 16, 1, NxImgType_2D, NxFmt_RGBA8, 1, 8, 0);
    chk("lay_ms8", id && last_lay()->msMode == DkMsMode_8x);
    nxdk_image_destroy(id);
    id = mk(16, 16, 1, NxImgType_2D, NxFmt_BC1, 1, 1, 0);
    chk("lay_bc_no_render", id && !(last_lay()->flags & DkImageFlags_UsageRender));
    nxdk_image_destroy(id);
    id = mk(16, 16, 1, NxImgType_2D, NxFmt_Z24S8, 1, 1, 0);
    chk("lay_depth_render", id && (last_lay()->flags & DkImageFlags_UsageRender) && (nxdk.img[id].flags & NxImg_Depth));
    nxdk_image_destroy(id);
}

static void t_ids(void) {
    const uint32_t sf = srgb_fmt();
    chk("srgb_fmt_exists", sf != 0);
    const DkFmt f = dk_fmt(sf);
    const uint32_t a = mk(8, 8, 1, NxImgType_2D, sf, 1, 1, 0);
    chk("srgb_pair", a == 1 && nxdk.img[2].used && nxdk.img[2].isView && nxdk.img[2].base == a && !nxdk.img[2].blk);
    chk("srgb_desc", hdesc(1).format == DkImageFormat_None && hdesc(2).format == f.srgb && nxdk_img_view_id(1, 1) == 2 && nxdk_img_view_id(1, 0) == 1);
    const uint32_t b = mk(8, 8, 1, NxImgType_2D, NxFmt_R8, 1, 1, 0);
    chk("next_id", b == 3 && nxdk_img_view_id(b, 1) == b);
    chk("view_destroy_bad", nxdk_image_destroy(2) == NXGPU_BAD_CALL);
    const int idle = n_idle;
    chk("destroy_ok", nxdk_image_destroy(a) == 0 && n_idle == idle + 1 && !nxdk.img[1].used && !nxdk.img[2].used);
    chk("destroy_twice_bad", nxdk_image_destroy(a) == NXGPU_BAD_CALL);
    chk("destroy_range_bad", nxdk_image_destroy(0) == NXGPU_BAD_CALL && nxdk_image_destroy(NX_MAX_IMAGES + 1) == NXGPU_BAD_CALL && nxdk_image_destroy(NX_MAX_IMAGES) == NXGPU_BAD_CALL);
    chk("id_reuse_pair", mk(8, 8, 1, NxImgType_2D, sf, 1, 1, 0) == 1 && nxdk.img[2].isView);
    nxdk_image_destroy(1);
    const uint32_t hp = mk(8, 8, 1, NxImgType_2D, NxFmt_R8, 1, 1, 0);
    const uint32_t hq = mk(8, 8, 1, NxImgType_2D, sf, 1, 1, 0);
    chk("srgb_pair_no_clobber", hp == 1 && hq == 4 && nxdk.img[3].used && !nxdk.img[3].isView && nxdk.img[5].isView && nxdk.img[5].base == hq);
    nxdk_image_destroy(hq); nxdk_image_destroy(hp);
    mk(8, 8, 1, NxImgType_2D, sf, 1, 1, 0);
    nxdk_image_destroy(1); nxdk_image_destroy(b);
    for (uint32_t i = 0; i < NX_MAX_IMAGES; i++) if (!mk(1, 1, 1, NxImgType_2D, NxFmt_R8, 1, 1, 0)) { chk("capacity_early", 0); break; }
    chk("capacity", mk(1, 1, 1, NxImgType_2D, NxFmt_R8, 1, 1, 0) == 0 && has("ids"));
    for (uint32_t i = 1; i <= NX_MAX_IMAGES; i++) nxdk_image_destroy(i);
    chk("capacity_freed", n_live == 1);
    const uint32_t sw = NXIMG_SWIZZLE(NxSwz_B, NxSwz_G, NxSwz_R, NxSwz_1);
    const uint32_t c = mk(8, 8, 1, NxImgType_2D, NxFmt_RGBA8, 1, 1, sw);
    const uint32_t n[4] = { NxSwz_B, NxSwz_G, NxSwz_R, NxSwz_1 };
    DkImageView v;
    dkImageViewDefaults(&v, &nxdk.img[c].dk);
    const DkFmt rf = dk_fmt(NxFmt_RGBA8);
    dk_fmt_view_swizzle(&rf, (const uint8_t[4]){ (uint8_t)n[0], (uint8_t)n[1], (uint8_t)n[2], (uint8_t)n[3] }, v.swizzle);
    chk("swizzle_desc", hdesc(c).sw[0] == (uint8_t)v.swizzle[0] && hdesc(c).sw[2] == (uint8_t)v.swizzle[2] && hdesc(c).sw[3] == (uint8_t)v.swizzle[3]);
    nxdk_image_destroy(c);
    const uint32_t z = mk(8, 8, 1, NxImgType_2D, NxFmt_Z32F, 1, 1, sw);
    dkImageViewDefaults(&v, &nxdk.img[z].dk);
    chk("swizzle_depth_none", hdesc(z).sw[0] == (uint8_t)v.swizzle[0] && hdesc(z).sw[2] == (uint8_t)v.swizzle[2]);
    nxdk_image_destroy(z);
    chk("swizzle_range_bad", mk(8, 8, 1, NxImgType_2D, NxFmt_RGBA8, 1, 1, 6) == 0);
}

static void t_rules(void) {
    chk("zero_extent_bad", !mk(0, 8, 1, 0, NxFmt_RGBA8, 1, 1, 0) && !mk(8, 0, 1, 0, NxFmt_RGBA8, 1, 1, 0) && !mk(8, 8, 0, 2, NxFmt_RGBA8, 1, 1, 0));
    chk("mips_range_bad", !mk(8, 8, 1, 0, NxFmt_RGBA8, 0, 1, 0) && !mk(8, 8, 1, 0, NxFmt_RGBA8, 5, 1, 0) && mk(8, 8, 1, 0, NxFmt_RGBA8, 4, 1, 0));
    chk("depth_nonthreed_bad", !mk(8, 8, 2, 0, NxFmt_RGBA8, 1, 1, 0) && !mk(8, 8, 2, 1, NxFmt_RGBA8, 1, 1, 0));
    chk("cube_square_bad", !mk(8, 4, 1, 1, NxFmt_RGBA8, 1, 1, 0));
    chk("samples_3_bad", !mk(8, 8, 1, 0, NxFmt_RGBA8, 1, 3, 0) && !mk(8, 8, 1, 0, NxFmt_RGBA8, 1, 0, 0) && !mk(8, 8, 1, 0, NxFmt_RGBA8, 1, 16, 0));
    chk("ms_shape_bad", !mk(8, 8, 1, 1, NxFmt_RGBA8, 1, 4, 0) && !mk(8, 8, 8, 2, NxFmt_RGBA8, 1, 4, 0) && !mk(8, 8, 1, 0, NxFmt_RGBA8, 2, 4, 0) && !mk(8, 8, 1, 0, NxFmt_BC1, 1, 4, 0));
    chk("3d_depth_bc45_bad", !mk(8, 8, 8, 2, NxFmt_Z32F, 1, 1, 0) && !mk(8, 8, 8, 2, NxFmt_BC4, 1, 1, 0) && !mk(8, 8, 8, 2, NxFmt_BC5, 1, 1, 0) && mk(8, 8, 8, 2, NxFmt_BC1, 1, 1, 0));
    chk("format_type_bad", !mk(8, 8, 1, 0, 0, 1, 1, 0) && !mk(8, 8, 1, 0, NxFmt_Count, 1, 1, 0) && !mk(8, 8, 1, 3, NxFmt_RGBA8, 1, 1, 0));
    chk("3d_mips_use_depth", mk(2, 2, 16, 2, NxFmt_R8, 5, 1, 0) != 0 && !mk(2, 2, 16, 2, NxFmt_R8, 6, 1, 0));
    for (uint32_t i = 1; i <= NX_MAX_IMAGES; i++) nxdk_image_destroy(i);
    int ok = 1;
    for (uint32_t f = 1; f < NxFmt_Count; f++) {
        const DkFmt d = dk_fmt(f);
        const uint32_t id = mk(16, 16, 1, 0, f, 1, 4, 0);
        const int want = (d.flags & (DKFMT_RENDER | DKFMT_DEPTH)) && !(d.flags & DKFMT_COMPRESSED);
        ok &= (id != 0) == want && (id || d.flags & DKFMT_COMPRESSED || has("X1"));
        if (id) nxdk_image_destroy(id);
    }
    chk("x1_rule_all_formats", ok);
    const uint32_t big = mk(NXDK_MAX_DIM, 1, 1, 0, NxFmt_RGBA8, 1, 1, 0);
    chk("x2_limit_ok", big != 0 && last_lay()->dimensions[0] == NXDK_MAX_DIM);
    if (big) nxdk_image_destroy(big);
    chk("x2_extent_bad", mk(NXDK_MAX_DIM + 1, 1, 1, 0, NxFmt_RGBA8, 1, 1, 0) == 0 && has("X2"));
    const int live = n_live;
    chk("x3_block_size", mk(NXDK_MAX_DIM, NXDK_MAX_DIM, 1, 0, NxFmt_RGBA32F, 1, 1, 0) == 0 && has("X3") && n_live == live && !nxdk.img[1].used);
}

static void t_cleanup(void) {
    const int live = n_live;
    int ok = 1, any_ok = 0;
    const uint32_t sf = srgb_fmt();
    for (int k = 0; k < 8; k++) {
        fail_after = k;
        const uint32_t id = mk(8, 8, 1, 0, sf, 2, 1, 0);
        fail_after = -1;
        if (id) { any_ok = 1; nxdk_image_destroy(id); } else ok &= !nxdk.img[1].used && !nxdk.img[2].used;
        ok &= n_live == live;
    }
    chk("create_fail_clean", ok && any_ok);
    uint8_t blob[8 * 8 * 4 * 2];
    pattern(blob, sizeof blob, 1);
    ok = 1; any_ok = 0;
    for (int k = 0; k < 8; k++) {
        fail_after = k;
        const uint32_t id = mk3(NxFmt_RGBA8, 0, 8, 8, 2, blob, 8 * 8 * 4 + 4 * 4 * 4);
        fail_after = -1;
        if (id) { any_ok = 1; nxdk_image_destroy(id); } else ok &= !nxdk.img[1].used;
        ok &= n_live == live && !nxdk.stage;
    }
    chk("upload_fail_clean", ok && any_ok);
    chk("v3_data_short_bad", mk3(NxFmt_RGBA8, 0, 8, 8, 2, blob, 8 * 8 * 4 + 4 * 4 * 4 - 1) == 0 && has("data") && n_live == live);
    chk("v3_data_exact", mk3(NxFmt_RGBA8, 0, 8, 8, 2, blob, 8 * 8 * 4 + 4 * 4 * 4) == 1);
    nxdk_image_destroy(1);
    chk("v3_bad_pointer", nxdk_image_create(0) == 0 && nxdk_image_create4(0) == 0);
}

static void t_xfer(void) {
    uint8_t blob[6 * (8 * 8 + 4 * 4) * 4], out[8 * 8 * 4];
    pattern(blob, sizeof blob, 3);
    int c0 = n_copies;
    uint32_t id = mk3(NxFmt_RGBA8, 1, 8, 8, 2, blob, sizeof blob);
    chk("upload_copy_count", id && n_copies == c0 + 12);
    chk("upload_cube_args", last_copy.v.type == DkImageType_2DArray && last_copy.r.z == 5 && last_copy.r.depth == 1 && last_copy.v.mipLevelOffset == 1 && last_copy.r.width == 4 && !last_copy.cb.rowLength);
    int ok = 1;
    for (uint32_t face = 0; face < 6; face++) {
        const uint32_t b[6] = { 0, 0, 0, 8, 8, 1 };
        ok &= rd(id, 0, face, b, out, sizeof out, 32) == 0 && !memcmp(out, blob + face * (8 * 8 + 4 * 4) * 4, 8 * 8 * 4);
    }
    chk("roundtrip_cube_faces", ok);
    const uint32_t b1[6] = { 0, 0, 0, 4, 4, 1 };
    chk("roundtrip_cube_mip1", rd(id, 1, 2, b1, out, 64, 16) == 0 && !memcmp(out, blob + (2 * (8 * 8 + 4 * 4) + 8 * 8) * 4, 64));
    const uint32_t sub[6] = { 2, 4, 0, 3, 2, 1 };
    memset(out, 0xCD, sizeof out);
    chk("read_box_pitch", rd(id, 0, 1, sub, out, sizeof out, 20) == 0 && !memcmp(out, blob + (1 * 80 + 4 * 8 + 2) * 4, 12) && !memcmp(out + 20, blob + (1 * 80 + 5 * 8 + 2) * 4, 12) && out[12] == 0xCD && out[32] == 0xCD);
    nxdk_image_destroy(id);
    /* 3D box upload through the upload primitive, read back by a different box */
    id = mk(4, 4, 4, NxImgType_3D, NxFmt_RGBA8, 2, 1, 0);
    uint8_t slab[4 * 4 * 3 * 4], rdb[4 * 4 * 3 * 4];
    pattern(slab, sizeof slab, 9);
    NxDkSync s;
    const uint32_t box[6] = { 0, 0, 1, 4, 4, 3 };
    ok = !nxdk_sync_begin(&s) && !nxdk_image_upload(s.cmd, id, 0, 0, box, slab, 16) && !nxdk_sync_end(&s);
    chk("3d_upload_args", ok && last_copy.r.z == 1 && last_copy.r.depth == 3 && last_copy.v.type != DkImageType_2DArray);
    chk("roundtrip_3d", rd(id, 0, 0, box, rdb, sizeof rdb, 16) == 0 && !memcmp(rdb, slab, sizeof slab));
    const uint32_t one[6] = { 0, 0, 2, 4, 4, 1 };
    chk("roundtrip_3d_slice", rd(id, 0, 0, one, rdb, 64, 16) == 0 && !memcmp(rdb, slab + 16 * 4, 64));
    const uint32_t over[6] = { 0, 0, 2, 4, 4, 3 };
    chk("3d_depth_overflow_bad", rd(id, 0, 0, over, rdb, sizeof rdb, 16) == NXGPU_BAD_CALL && has("image_read"));
    chk("3d_upload_pitch_bad", (nxdk_sync_begin(&s), nxdk_image_upload(s.cmd, id, 0, 0, box, slab, 15) == 1) && (nxdk_sync_end(&s), 1));
    nxdk_image_destroy(id);
    /* compressed: 4x4 blocks, the box is block aligned */
    uint8_t bc[8 * 8 * 2], bo[8 * 8 * 2];
    pattern(bc, sizeof bc, 5);
    id = mk3(NxFmt_BC1, 0, 8, 8, 1, bc, 32);
    const uint32_t full[6] = { 0, 0, 0, 8, 8, 1 };
    chk("roundtrip_bc1", id && rd(id, 0, 0, full, bo, 32, 16) == 0 && !memcmp(bo, bc, 32));
    const uint32_t blk[6] = { 4, 4, 0, 4, 4, 1 };
    chk("bc1_block_box", rd(id, 0, 0, blk, bo, 8, 8) == 0 && !memcmp(bo, bc + 24, 8));
    const uint32_t un[6] = { 2, 0, 0, 4, 4, 1 };
    chk("bc1_unaligned_bad", rd(id, 0, 0, un, bo, 32, 16) == NXGPU_BAD_CALL);
    nxdk_image_destroy(id);
    /* ROT8: Z24S8 is stored rotated in deko3d, read back unrotated */
    uint8_t z[4 * 4 * 4], zo[4 * 4 * 4];
    pattern(z, sizeof z, 7);
    id = mk3(NxFmt_Z24S8, 0, 4, 4, 1, z, sizeof z);
    uint32_t w0, st;
    memcpy(&w0, z, 4);
    memcpy(&st, dkMemBlockGetCpuAddr(nxdk.img[id].blk), 4);
    chk("rot8_stored_rotated", id && st == ((w0 >> 8) | (w0 << 24)));
    const uint32_t z4[6] = { 0, 0, 0, 4, 4, 1 };
    chk("rot8_roundtrip", rd(id, 0, 0, z4, zo, sizeof zo, 16) == 0 && !memcmp(z, zo, sizeof z));
    nxdk_image_destroy(id);
    /* x byte of BGRX8 / RGBX8 reads back 0xFF */
    uint8_t x[16], xo[16];
    pattern(x, 16, 2);
    id = mk3(NxFmt_BGRX8, 0, 2, 2, 1, x, 16);
    const uint32_t x2[6] = { 0, 0, 0, 2, 2, 1 };
    chk("bgrx_x_byte", id && rd(id, 0, 0, x2, xo, 16, 8) == 0 && xo[3] == 0xFF && xo[7] == 0xFF && xo[0] == x[0] && xo[12] == x[12] && xo[15] == 0xFF);
    nxdk_image_destroy(id);
}

static void t_read(void) {
    uint8_t out[256];
    const uint32_t b[6] = { 0, 0, 0, 4, 4, 1 };
    chk("front_absent_bad", rd(0, 0, 0, b, out, sizeof out, 16) == NXGPU_BAD_CALL);
    nxdk.front.w = 4; nxdk.front.h = 4; nxdk.front.d = 1; nxdk.front.fmt = NxFmt_RGBA8; nxdk.front.type = NxImgType_2D;
    nxdk.front.mips = 1; nxdk.front.samples = 1; nxdk.front.layers = 1; nxdk.front.used = 1;
    chk("front_storage", nxdk_img_storage(&nxdk.front) == 0);
    memset(out, 0xCD, sizeof out);
    chk("front_read_zero", rd(0, 0, 0, b, out, 64, 16) == 0 && out[0] == 0 && out[63] == 0 && out[64] == 0xCD);
    nxdk_img_release(&nxdk.front);
    const uint32_t sf = srgb_fmt();
    uint8_t blob[8 * 8 * 4];
    pattern(blob, sizeof blob, 4);
    const uint32_t id = mk3(sf, 0, 8, 8, 1, blob, sizeof blob);
    const uint32_t full[6] = { 0, 0, 0, 8, 8, 1 };
    uint8_t o2[8 * 8 * 4];
    chk("view_reads_base", id && rd(id + 1, 0, 0, full, o2, sizeof o2, 32) == 0 && rd(id, 0, 0, full, o2, sizeof o2, 32) == 0);
    int ok = 1;
    const uint32_t big[6] = { 0, 0, 0, 9, 8, 1 }, yb[6] = { 0, 1, 0, 8, 8, 1 }, zb[6] = { 0, 0, 1, 8, 8, 1 }, zero[6] = { 0, 0, 0, 0, 8, 1 };
    ok &= rd(id, 0, 0, big, o2, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 0, 0, yb, o2, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 0, 0, zb, o2, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 0, 0, zero, o2, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 1, 0, full, o2, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 0, 1, full, o2, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 0, 0, full, NULL, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 0, 0, full, o2, sizeof o2 - 1, 32) == NXGPU_BAD_CALL;
    ok &= rd(id, 0, 0, full, o2, sizeof o2, 31) == NXGPU_BAD_CALL;
    ok &= rd(9, 0, 0, full, o2, sizeof o2, 32) == NXGPU_BAD_CALL;
    ok &= nxdk_image_read(0) == NXGPU_BAD_CALL;
    chk("read_errors", ok);
    uint8_t wide[7 * 40 + 32];
    chk("read_last_row_tight", rd(id, 0, 0, full, wide, sizeof wide, 40) == 0);
    chk("readback_rgba8", rb_(id, 0, 0, 8, 8, NxRb_RGBA8, o2, sizeof o2) == 0 && !memcmp(o2, blob, sizeof o2));
    float fo[8 * 8 * 4];
    chk("readback_rgba32f", rb_(id, 0, 0, 8, 8, NxRb_RGBA32F, fo, sizeof fo) == 0 && fo[0] == (float)blob[0] / 255.0f && fo[255] == (float)blob[255] / 255.0f);
    chk("readback_empty_rect", rb_(id, 0, 0, 0, 0, NxRb_RGBA8, fo, 0) == 0);
    chk("readback_errors", rb_(id, 0, 0, 8, 8, NxRb_Depth32F, fo, sizeof fo) == NXGPU_BAD_CALL && rb_(id, 0, 0, 8, 8, 4, fo, sizeof fo) == NXGPU_BAD_CALL &&
        rb_(id, 1, 0, 8, 8, NxRb_RGBA8, fo, sizeof fo) == NXGPU_BAD_CALL && rb_(id, 0, 0, 8, 8, NxRb_RGBA8, fo, 255) == NXGPU_BAD_CALL && rb_(id, 0, 0, 8, 8, NxRb_RGBA8, NULL, 256) == NXGPU_BAD_CALL &&
        nxdk_readback(0) == NXGPU_BAD_CALL);
    nxdk_image_destroy(id);
    const uint32_t rc = mk(4, 4, 1, 1, NxFmt_RGBA8, 1, 1, 0), c3 = mk(4, 4, 4, 2, NxFmt_RGBA8, 1, 1, 0);
    chk("readback_nonplain_bad", rb_(rc, 0, 0, 4, 4, NxRb_RGBA8, fo, 64) == NXGPU_BAD_CALL && rb_(c3, 0, 0, 4, 4, NxRb_RGBA8, fo, 64) == NXGPU_BAD_CALL);
    nxdk_image_destroy(rc); nxdk_image_destroy(c3);
    const uint32_t bc = mk(8, 8, 1, 0, NxFmt_BC1, 1, 1, 0);
    chk("readback_compressed_bad", rb_(bc, 0, 0, 8, 8, NxRb_RGBA8, fo, 256) == NXGPU_BAD_CALL);
    nxdk_image_destroy(bc);
    uint8_t zb4[16 * 4];
    pattern(zb4, sizeof zb4, 6);
    const uint32_t zi = mk3(NxFmt_Z32F, 0, 4, 4, 1, zb4, sizeof zb4);
    chk("readback_depth32f", zi && rb_(zi, 0, 0, 4, 4, NxRb_Depth32F, fo, 64) == 0 && !memcmp(fo, zb4, 64) && rb_(zi, 0, 0, 4, 4, NxRb_Stencil8, fo, 16) == NXGPU_BAD_CALL);
    nxdk_image_destroy(zi);
    uint8_t zs[16 * 4];
    pattern(zs, sizeof zs, 8);
    const uint32_t zs_id = mk3(NxFmt_Z24S8, 0, 4, 4, 1, zs, sizeof zs);
    uint8_t st[16];
    chk("readback_stencil", zs_id && rb_(zs_id, 0, 0, 4, 4, NxRb_Stencil8, st, 16) == 0 && st[0] == zs[0] && st[5] == zs[20]);
    nxdk_image_destroy(zs_id);
}

static void ms_fill(uint32_t id, uint8_t v) { memset(dkMemBlockGetCpuAddr(nxdk.img[id].blk), v, nxdk.img[id].blk ? last_init.blk_size : 0); }

static void t_ms(void) {
    uint8_t out[16];
    const uint32_t one[6] = { 0, 0, 0, 1, 1, 1 };
    uint32_t id = mk(4, 4, 1, 0, NxFmt_RGBA8, 1, 4, 0);
    ms_fill(id, 80);
    uint8_t* mem = (uint8_t*)dkMemBlockGetCpuAddr(nxdk.img[id].blk);
    memset(mem + 4, 240, 4);   /* sample (1, 0) of pixel (0, 0): grid 2x2, rows of 4 * 2 * 4 bytes */
    const int live = n_live, trace0 = n_trace;
    chk("ms4_resolve_read", rd(id, 0, 0, one, out, 4, 4) == 0 && out[0] == 120 && out[3] == 120 && n_live == live);
    chk("ms4_resolve_recorded", memchr(trace + trace0, 'R', (size_t)(n_trace - trace0)) != NULL && last_lay()->msMode == DkMsMode_1x && last_lay()->type == DkImageType_2D);
    nxdk_image_destroy(id);
    id = mk(4, 4, 1, 0, NxFmt_RGBA8, 1, 2, 0);
    ms_fill(id, 100);
    chk("ms2_resolve_read", rd(id, 0, 0, one, out, 4, 4) == 0 && out[0] == 100);
    nxdk_image_destroy(id);
    id = mk(4, 4, 1, 0, NxFmt_RGBA8, 1, 8, 0);
    chk("ms8_read_bad", rd(id, 0, 0, one, out, 4, 4) == NXGPU_BAD_CALL && has("X5"));
    nxdk_image_destroy(id);
    id = mk(4, 4, 1, 0, NxFmt_Z32F, 1, 4, 0);
    chk("ms_depth_read_bad", rd(id, 0, 0, one, out, 4, 4) == NXGPU_BAD_CALL && has("X4") && rb_(id, 0, 0, 1, 1, NxRb_Depth32F, out, 4) == NXGPU_BAD_CALL && has("X4"));
    nxdk_image_destroy(id);
    chk("ms_blocks_balanced", n_live == live - 1);
}

static void t_sampler(void) {
    const NxSamplerState s0 = { .filter = NXSMP_MIN_LINEAR | (NxMip_Linear << NXSMP_MIP_SHIFT), .maxAniso = 8, .wrap = (uint16_t)(NxWrap_Mirror | NxWrap_Clamp << 3 | NxWrap_Border << 6),
                                .minLod = 1, .maxLod = 5, .lodBias = -0.5f, .border = 0x80402010u };
    const uint32_t a = (uint32_t)nxdk_sampler_create(P(&s0));
    const DkSampler* d = &smp_seen[a];
    chk("smp_id_dense", a == 1 && nxdk.nSmp == 1);
    chk("smp_filters", d->minFilter == DkFilter_Linear && d->magFilter == DkFilter_Nearest && d->mipFilter == DkMipFilter_Linear);
    chk("smp_wrap", d->wrapMode[0] == DkWrapMode_MirroredRepeat && d->wrapMode[1] == DkWrapMode_ClampToEdge && d->wrapMode[2] == DkWrapMode_ClampToBorder);
    chk("smp_lod", d->lodClampMin == 1.0f && d->lodClampMax == 5.0f && d->lodBias == -0.5f && d->maxAnisotropy == 8.0f && !d->compareEnable);
    chk("smp_border", d->borderColor[0].value_f == 16.0f / 255.0f && d->borderColor[1].value_f == 32.0f / 255.0f && d->borderColor[2].value_f == 64.0f / 255.0f && d->borderColor[3].value_f == 128.0f / 255.0f);
    chk("smp_state_kept", nxdk_smp(a) && !memcmp(nxdk_smp(a), &s0, sizeof s0) && !nxdk_smp(0) && !nxdk_smp(2));
    NxSamplerState s1;
    memset(&s1, 0, sizeof s1);
    s1.filter = NXSMP_MAG_LINEAR | NXSMP_SHADOW | NXSMP_SRGB | (NxMip_Nearest << NXSMP_MIP_SHIFT);
    s1.wrap = (uint16_t)(NxWrap_MirrorOnce | NxWrap_Repeat << 3);
    const uint32_t b = (uint32_t)nxdk_sampler_create(P(&s1));
    d = &smp_seen[b];
    chk("smp_filters2", b == 2 && d->minFilter == DkFilter_Nearest && d->magFilter == DkFilter_Linear && d->mipFilter == DkMipFilter_Nearest && d->compareEnable && d->compareOp == DkCompareOp_Lequal);
    chk("smp_wrap2", d->wrapMode[0] == DkWrapMode_MirrorClampToEdge && d->wrapMode[1] == DkWrapMode_Repeat && d->wrapMode[2] == DkWrapMode_Repeat);
    chk("smp_aniso_off", d->maxAnisotropy == 1.0f && nxdk_smp(b)->filter & NXSMP_SRGB);
    s1.maxAniso = 1;
    nxdk_sampler_update(b, P(&s1) == 0 ? 0 : P(&s1));
    chk("smp_aniso_1", smp_seen[b].maxAnisotropy == 1.0f);
    s1.maxAniso = 16; s1.minLod = 2; s1.maxLod = 3;
    chk("smp_update", nxdk_sampler_update(b, P(&s1)) == 0 && smp_seen[b].maxAnisotropy == 16.0f && smp_seen[b].lodClampMin == 2.0f && smp_seen[b].lodClampMax == 3.0f && nxdk_smp(b)->maxAniso == 16);
    chk("smp_update_bad_id", nxdk_sampler_update(0, P(&s1)) == NXGPU_BAD_CALL && nxdk_sampler_update(3, P(&s1)) == NXGPU_BAD_CALL && nxdk_sampler_update(b, 0) == NXGPU_BAD_CALL);
    NxSamplerState bad = s1;
    const DkSampler before = smp_seen[b];
    bad.wrap = 5;
    chk("smp_update_wrap5_bad", nxdk_sampler_update(b, P(&bad)) == NXGPU_BAD_CALL && has("wrap") && !memcmp(&before, &smp_seen[b], sizeof before) && nxdk_smp(b)->wrap == s1.wrap);
    bad = s1; bad.filter = (uint8_t)(3 << NXSMP_MIP_SHIFT);
    chk("smp_update_mip3_bad", nxdk_sampler_update(b, P(&bad)) == NXGPU_BAD_CALL && has("mip"));
    bad = s1; bad.wrap = 7 << 6;
    chk("smp_create_wrap7_bad", nxdk_sampler_create(P(&bad)) == 0 && nxdk.nSmp == 2 && has("wrap"));
    chk("smp_create_null", nxdk_sampler_create(0) == 0 && nxdk.nSmp == 2);
    int ok = 1;
    for (uint32_t w = 0; w < 5; w++) {
        NxSamplerState s = s1;
        s.wrap = (uint16_t)(w | w << 3 | w << 6);
        const uint32_t id = (uint32_t)nxdk_sampler_create(P(&s));
        static const DkWrapMode e[5] = { DkWrapMode_Repeat, DkWrapMode_MirroredRepeat, DkWrapMode_ClampToEdge, DkWrapMode_ClampToBorder, DkWrapMode_MirrorClampToEdge };
        ok &= id && smp_seen[id].wrapMode[0] == e[w] && smp_seen[id].wrapMode[1] == e[w] && smp_seen[id].wrapMode[2] == e[w];
    }
    chk("smp_wrap_table", ok && nxdk.nSmp == 7);
    while (nxdk.nSmp + 1 < NX_MAX_SAMPLERS) nxdk_sampler_create(P(&s1));
    chk("smp_capacity", nxdk_sampler_create(P(&s1)) == 0 && nxdk.nSmp == NX_MAX_SAMPLERS - 1 && has("ids"));
    DkCmdBuf cb = (DkCmdBuf)calloc(1, sizeof(HCmd));
    nxdk_bind_descriptors(cb);
    free(cb);
    chk("bind_sets", last_bind.n == 2 && last_bind.nimg == NXDK_IMG_SLOTS && last_bind.nsmp == NX_MAX_SAMPLERS && last_bind.img == nxdk.imgDescAddr && last_bind.smp == nxdk.imgDescAddr + NXDK_IMG_SLOTS * 32);
    chk("tex_handle", NX_TEX_HANDLE(5, 3) == dkMakeTextureHandle(5, 3) && NX_TEX_HANDLE(NXDK_IMG_SLOTS - 1, NX_MAX_SAMPLERS - 1) == dkMakeTextureHandle(NXDK_IMG_SLOTS - 1, NX_MAX_SAMPLERS - 1));
}

static int selftest(void) {
    setup();
    t_layouts(); nxdk_img_shutdown(); chk("balanced_layouts", n_live == 0); setup();
    t_ids(); nxdk_img_shutdown(); chk("balanced_ids", n_live == 0); setup();
    t_rules(); nxdk_img_shutdown(); chk("balanced_rules", n_live == 0); setup();
    t_cleanup(); nxdk_img_shutdown(); chk("balanced_cleanup", n_live == 0); setup();
    t_xfer(); nxdk_img_shutdown(); chk("balanced_xfer", n_live == 0); setup();
    t_read(); nxdk_img_shutdown(); chk("balanced_read", n_live == 0); setup();
    t_ms(); nxdk_img_shutdown(); chk("balanced_ms", n_live == 0); setup();
    t_sampler(); nxdk_img_shutdown(); chk("balanced_sampler", n_live == 0);
    printf("NCHK %d\n", nchk);
    return 0;
}

int main(int argc, char** argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc > 1 && !strcmp(argv[1], "selftest")) return selftest();
    setup();
    script();
    nxdk_img_shutdown();
    printf("LIVE %d\n", n_live);
    return 0;
}
