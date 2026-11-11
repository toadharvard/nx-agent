/* L6 GL4 backend tests: main, the oracle format table, pattern generators, call helpers. */
#define _GNU_SOURCE
#include "t_common.h"

int g_checks, g_fail;
uint32_t g_mem;
uint8_t* g_scratch;
static uint32_t g_frame;
NxFrameStats g_stats;

uint32_t g_present_count, g_present_last = 0xFFFFFFFFu;
void nxgl4_on_present(uint32_t frame) { g_present_count++; g_present_last = frame; }   /* the embedder's hook */
void* nxgl4_guest_ptr(uint64_t addr, uint64_t size) { (void)size; return (void*)(uintptr_t)addr; }   /* identity embedder */

#define F(f, n, b, c, d, k, s) {f, n, b, c, d, k, s}
const TFmt g_fmts[] = {
    F(NxFmt_RGBA8, "RGBA8", 4, 0, 0, K_RAW, 1), F(NxFmt_BGRA8, "BGRA8", 4, 0, 0, K_RAW, 1),
    F(NxFmt_BC1, "BC1", 8, 1, 0, K_RAW, 1), F(NxFmt_BC2, "BC2", 16, 1, 0, K_RAW, 1), F(NxFmt_BC3, "BC3", 16, 1, 0, K_RAW, 1),
    F(NxFmt_R8, "R8", 1, 0, 0, K_RAW, 0), F(NxFmt_RG8, "RG8", 2, 0, 0, K_RAW, 0), F(NxFmt_R5G6B5, "R5G6B5", 2, 0, 0, K_RAW, 0),
    F(NxFmt_RGBA16F, "RGBA16F", 8, 0, 0, K_F16, 0), F(NxFmt_R32F, "R32F", 4, 0, 0, K_F32, 0),
    F(NxFmt_Z24S8, "Z24S8", 4, 0, 1, K_RAW, 0), F(NxFmt_Z32F, "Z32F", 4, 0, 1, K_Z32F, 0),
    F(NxFmt_BGRX8, "BGRX8", 4, 0, 0, K_X8, 1), F(NxFmt_RGBX8, "RGBX8", 4, 0, 0, K_X8, 1),
    F(NxFmt_A1R5G5B5, "A1R5G5B5", 2, 0, 0, K_RAW, 0), F(NxFmt_X1R5G5B5, "X1R5G5B5", 2, 0, 0, K_RAW, 0),
    F(NxFmt_A4R4G4B4, "A4R4G4B4", 2, 0, 0, K_RAW, 0), F(NxFmt_RGB10A2, "RGB10A2", 4, 0, 0, K_RAW, 0),
    F(NxFmt_RG16, "RG16", 4, 0, 0, K_RAW, 0), F(NxFmt_RGBA16, "RGBA16", 8, 0, 0, K_RAW, 0), F(NxFmt_R16, "R16", 2, 0, 0, K_RAW, 0),
    F(NxFmt_RG8S, "RG8S", 2, 0, 0, K_S8, 0), F(NxFmt_RGBA8S, "RGBA8S", 4, 0, 0, K_S8, 0),
    F(NxFmt_RG16S, "RG16S", 4, 0, 0, K_S16, 0), F(NxFmt_RGBA16S, "RGBA16S", 8, 0, 0, K_S16, 0),
    F(NxFmt_R16F, "R16F", 2, 0, 0, K_F16, 0), F(NxFmt_RG16F, "RG16F", 4, 0, 0, K_F16, 0),
    F(NxFmt_RG32F, "RG32F", 8, 0, 0, K_F32, 0), F(NxFmt_RGBA32F, "RGBA32F", 16, 0, 0, K_F32, 0),
    F(NxFmt_BC4, "BC4", 8, 1, 0, K_RAW, 0), F(NxFmt_BC5, "BC5", 16, 1, 0, K_RAW, 0),
    F(NxFmt_Z16, "Z16", 2, 0, 1, K_RAW, 0), F(NxFmt_Z24X8, "Z24X8", 4, 0, 1, K_RAW, 0),
    F(NxFmt_Z32FS8, "Z32FS8", 8, 0, 1, K_Z32FS8, 0),
};
const int g_nfmts = sizeof g_fmts / sizeof g_fmts[0];
_Static_assert(sizeof g_fmts / sizeof g_fmts[0] == NxFmt_Count - 1, "the oracle table covers every NxFormat");

uint32_t t_hash(uint32_t a) {
    a ^= a >> 16; a *= 0x7feb352dU; a ^= a >> 15; a *= 0x846ca68bU; a ^= a >> 16;
    return a;
}

void t_pattern(const TFmt* f, uint8_t* p, size_t n, uint32_t seed) {
    size_t total = n * f->bpp;
    for (size_t i = 0; i < total; i++) p[i] = (uint8_t)(t_hash(seed * 2654435761u + (uint32_t)i * 40503u) >> 11);
    for (size_t t = 0; t < n; t++) {
        uint8_t* q = p + t * f->bpp;
        switch ((enum Kind)f->kind) {
        case K_RAW: break;
        case K_F16:
            for (int i = 0; i < f->bpp / 2; i++) { uint16_t h; memcpy(&h, q + 2 * i, 2); uint32_t e = 1 + (h >> 10 & 31) % 30; h = (uint16_t)((h & 0x83FF) | e << 10); memcpy(q + 2 * i, &h, 2); }
            break;
        case K_F32:
            for (int i = 0; i < f->bpp / 4; i++) { uint32_t h; memcpy(&h, q + 4 * i, 4); uint32_t e = 1 + (h >> 23 & 255) % 254; h = (h & 0x807FFFFF) | e << 23; memcpy(q + 4 * i, &h, 4); }
            break;
        case K_S8: for (int i = 0; i < f->bpp; i++) if (q[i] == 0x80) q[i] = 0x81; break;
        case K_S16:
            for (int i = 0; i < f->bpp / 2; i++) { uint16_t h; memcpy(&h, q + 2 * i, 2); if (h == 0x8000) h = 0x8001; memcpy(q + 2 * i, &h, 2); }
            break;
        case K_X8: q[3] = 0xFF; break;   /* GL_RGB8 storage: the x byte reads back as 0xFF (documented) */
        case K_Z32F: { uint32_t h; memcpy(&h, q, 4); h = (h & 0x007FFFFF) | (100u + (h >> 23 & 255) % 27) << 23; memcpy(q, &h, 4); break; }
        case K_Z32FS8: { uint32_t h; memcpy(&h, q, 4); h = (h & 0x007FFFFF) | (100u + (h >> 23 & 255) % 27) << 23; memcpy(q, &h, 4);
                         q[5] = q[6] = q[7] = 0; break; }
        }
    }
}

uint64_t t_ext(uint32_t fn, int n, ...) {
    uint64_t a[8];
    va_list ap;
    va_start(ap, n);
    for (int i = 0; i < n; i++) a[i] = va_arg(ap, uint64_t);
    va_end(ap);
    return nxgl4_ext(fn, a, (uint32_t)n);
}

void t_op(Stream* s, uint32_t opcode, int argc, ...) {
    va_list ap;
    va_start(ap, argc);
    s->w[s->n++] = opcode | (uint32_t)argc << 16;
    for (int i = 0; i < argc; i++) s->w[s->n++] = va_arg(ap, uint32_t);
    va_end(ap);
}

uint64_t t_submit(Stream* s) {
    s->w[s->n++] = NxOp_End;
    NxSubmit sub = {(uint64_t)(uintptr_t)s->w, s->n, g_frame++, (uint64_t)(uintptr_t)&g_stats, 0, 0};
    uint64_t r = t_ext(NXGPU_FN_SUBMIT, 1, (uint64_t)(uintptr_t)&sub);
    s->n = 0;
    return r;
}

int t_setup(uint32_t w, uint32_t h) {
    char err[256];
    if (!nxgl4_create(w, h, err, sizeof err)) { printf("nxgl4_create: %s\n", err); return 1; }
    if (!g_scratch) g_scratch = (uint8_t*)aligned_alloc(4096, SCRATCH_SIZE);
    memset(g_scratch, 0, SCRATCH_SIZE);
    NxMemBlockDesc d = {(uint64_t)(uintptr_t)g_scratch, SCRATCH_SIZE, NxMem_Generic};
    g_mem = (uint32_t)t_ext(NXGPU_FN_MEM_CREATE, 1, (uint64_t)(uintptr_t)&d);
    return g_mem ? 0 : 1;
}
void t_teardown(void) { nxgl4_destroy(); }

uint32_t t_image(uint32_t type, uint32_t fmt, uint32_t w, uint32_t h, uint32_t d, uint32_t mips, uint32_t samples) {
    NxImageDesc4 id;
    memset(&id, 0, sizeof id);
    id.width = (uint16_t)w; id.height = (uint16_t)h; id.depth = (uint16_t)d; id.type = (uint8_t)type; id.format = (uint8_t)fmt;
    id.mips = (uint8_t)mips; id.samples = (uint8_t)samples;
    id.flags = samples > 1 ? NxImg_RenderTarget : 0;
    return (uint32_t)t_ext(NXGPU_FN_IMAGE_CREATE4, 1, (uint64_t)(uintptr_t)&id);
}

uint64_t t_update(uint32_t img, uint32_t level, uint32_t layer, const uint32_t b[6], const void* data, size_t dataSize,
                  size_t rowPitch, size_t off) {
    memcpy(g_scratch + off, data, dataSize);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_ImageUpdate, 12, img, level | layer << 16, b[0], b[1], b[2], b[3], b[4], b[5], g_mem, (uint32_t)off,
         (uint32_t)dataSize, (uint32_t)rowPitch);
    uint64_t r = t_submit(s);
    free(s);
    return r;
}

uint64_t t_read(uint32_t img, uint32_t level, uint32_t layer, const uint32_t b[6], void* dst, size_t dstSize, size_t rowPitch) {
    NxImageRead r;
    memset(&r, 0, sizeof r);
    r.src.image = img; r.src.level = (uint16_t)level; r.src.layer = (uint16_t)layer;
    r.x = b[0]; r.y = b[1]; r.z = b[2]; r.w = b[3]; r.h = b[4]; r.d = b[5];
    r.dst = (uint64_t)(uintptr_t)dst; r.dst_size = (uint32_t)dstSize; r.rowPitch = (uint32_t)rowPitch;
    return t_ext(NXGPU_FN_IMAGE_READ, 1, (uint64_t)(uintptr_t)&r);
}

uint8_t* t_read_all(uint32_t img, uint32_t level, uint32_t layer, uint32_t w, uint32_t h, uint32_t d, const TFmt* f) {
    size_t rb = t_rowbytes(f, w), n = rb * t_rows(f, h) * d;
    uint8_t* buf = (uint8_t*)calloc(1, n ? n : 1);
    uint32_t box[6] = {0, 0, 0, w, h, d};
    if (t_read(img, level, layer, box, buf, n, rb)) { free(buf); return NULL; }
    return buf;
}

float t_half(uint16_t h) {
    int e = h >> 10 & 31, m = h & 1023;
    float v = e == 0 ? ldexpf((float)m, -24) : e == 31 ? (m ? NAN : INFINITY) : ldexpf((float)(m + 1024), e - 25);
    return h & 0x8000 ? -v : v;
}
uint16_t t_tohalf(float f) {   /* nearest, for values in the normal range */
    uint16_t best = 0; float bd = 1e30f;
    for (uint32_t h = 0; h < 0x7C00; h++) { float d = fabsf(t_half((uint16_t)h) - fabsf(f)); if (d < bd) { bd = d; best = (uint16_t)h; } }
    return (uint16_t)(best | (f < 0 ? 0x8000 : 0));
}

const char T_VS_FULL[] = "#version 460\nvoid main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 4.0 - 2.0, 0.5, 1.0); }\n";
const char T_FS_WHITE[] = "#version 460\nlayout (location = 0) out vec4 o;\nvoid main() { o = vec4(1.0); }\n";

void t_viewport(Stream* s, float x, float y, float w, float h) {
    uint32_t b[6], one = 0x3F800000u;
    memcpy(&b[0], &x, 4); memcpy(&b[1], &y, 4); memcpy(&b[2], &w, 4); memcpy(&b[3], &h, 4); b[4] = 0; b[5] = one;
    t_op(s, NxOp_Viewport, 6, b[0], b[1], b[2], b[3], b[4], b[5]);
}
const TFmt* t_fmt(uint32_t nxfmt) { for (int i = 0; i < g_nfmts; i++) if (g_fmts[i].fmt == nxfmt) return &g_fmts[i]; return NULL; }

uint32_t t_shader(uint32_t stage, const char* src) {
    NxShaderDesc d = {stage, 0, 0, (uint64_t)(uintptr_t)src, (uint32_t)strlen(src), 0};
    return (uint32_t)t_ext(NXGPU_FN_SHADER_CREATE, 1, (uint64_t)(uintptr_t)&d);
}
void t_state(Stream* s, uint32_t id, uint32_t vs, uint32_t ps, uint32_t layout, uint64_t w0, uint64_t w1, uint64_t w2, uint64_t w3) {
    t_op(s, NxOp_DefineState, 12, id, vs, ps, layout, (uint32_t)w0, (uint32_t)(w0 >> 32), (uint32_t)w1, (uint32_t)(w1 >> 32),
         (uint32_t)w2, (uint32_t)(w2 >> 32), (uint32_t)w3, (uint32_t)(w3 >> 32));
}
int t_readback(uint32_t img, uint32_t fmt, uint32_t w, uint32_t h, void* dst, size_t size) {
    NxReadback rd = {img, 0, 0, w, h, fmt, (uint64_t)(uintptr_t)dst, (uint32_t)size, 0};
    return t_ext(NXGPU_FN_READBACK, 1, (uint64_t)(uintptr_t)&rd) == 0;
}
void t_zero(uint32_t img, uint32_t type, uint32_t fmt, uint32_t w, uint32_t h, uint32_t d, uint32_t mips) {
    const TFmt* f = t_fmt(fmt);
    uint32_t layers = type == NxImgType_Cube ? 6 : 1;
    for (uint32_t l = 0; l < mips; l++)
        for (uint32_t ly = 0; ly < layers; ly++) {
            uint32_t lw = lvl(w, l), lh = lvl(h, l), ld = type == NxImgType_3D ? lvl(d, l) : 1, box[6] = {0, 0, 0, lw, lh, ld};
            size_t rb = t_rowbytes(f, lw), n = rb * t_rows(f, lh) * ld;
            uint8_t* z = (uint8_t*)calloc(1, n);
            t_update(img, l, ly, box, z, n, rb, 0);
            free(z);
        }
}
uint32_t t_sampler(const NxSamplerState* s) { return (uint32_t)t_ext(NXGPU_FN_SAMPLER_CREATE, 1, (uint64_t)(uintptr_t)s); }
void t_layout4(Stream* s, uint32_t id, const NxVtxLayout4* l) {
    uint32_t w[1 + sizeof *l / 4];
    w[0] = id;
    memcpy(w + 1, l, sizeof *l);
    s->w[s->n++] = NxOp_DefineLayout4 | (uint32_t)(1 + sizeof *l / 4) << 16;
    memcpy(s->w + s->n, w, sizeof w);
    s->n += (uint32_t)(sizeof w / 4);
}

int main(int argc, char** argv) {
    const char* which = argc > 1 ? argv[1] : "abcdefijklmn";
    int bad = 0;
    for (const char* c = which; *c; c++) {
        g_checks = g_fail = 0;
        if (t_setup(16, 16)) { printf("PART %c: setup failed\n", *c); bad = 1; continue; }
        if (*c == 'a' || *c == 'b') part_ab(*c);
        else if (*c == 'c' || *c == 'd') part_cd(*c);
        else if (*c == 'e' || *c == 'f') part_ef(*c);
        else if (*c == 'i') part_i();
        else if (*c == 'j') part_j();
        else if (*c == 'k') part_k();
        else if (*c == 'l') part_l();
        else if (*c == 'm') part_m();
        else part_n();
        t_teardown();
        printf("PART %c: %d checks, %d failed\n", *c, g_checks, g_fail);
        if (g_fail) bad = 1;
    }
    return bad;
}
