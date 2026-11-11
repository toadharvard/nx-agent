/* Part j: vertex layouts and streams. Every NxAttrType4 through a pass-through vertex shader into an RGBA32F target
 * against the CPU expansion (D3D9's rules, written here independently of the backend); the same bytes re-read
 * through another layout (the UDEC3 / DEC3N conversion must not touch them); instancing with divisors and a
 * stride-0 stream; 16 streams of different blocks, offsets and strides; layouts / binds that must be refused. */
#include "t_ref.h"

enum { N_TYPES = NxAttr4_Count };
static const uint8_t k_bytes[N_TYPES] = {4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8};
static const char k_vs_attr[] = "#version 460\nlayout (location = 0) in vec4 a;\nlayout (location = 0) flat out vec4 v;\n"
    "void main() { gl_PointSize = 1.0; gl_Position = vec4(float(gl_VertexID) * 0.5 - 1.0, 1.0, 0.0, 1.0); v = a; }\n";
static const char k_fs_pass[] = "#version 460\nlayout (location = 0) flat in vec4 v;\nlayout (location = 0) out vec4 o;\nvoid main() { o = v; }\n";

/* the D3D9 expansion of one element (the oracle) */
static void expand(uint32_t t, const uint8_t* p, double o[4]) {
    float f[4]; int16_t s[4]; uint16_t u[4], h[4]; uint32_t w;
    o[0] = o[1] = o[2] = 0; o[3] = 1;
    memcpy(f, p, 16 > k_bytes[t] ? k_bytes[t] : 16); memcpy(s, p, 8); memcpy(u, p, 8); memcpy(h, p, 8); memcpy(&w, p, 4);
    switch ((enum NxAttrType4)t) {
    case NxAttr4_Float1: case NxAttr4_Float2: case NxAttr4_Float3: case NxAttr4_Float4:
        for (uint32_t i = 0; i <= t; i++) o[i] = f[i];
        break;
    case NxAttr4_D3DColor: o[0] = p[2] / 255.0; o[1] = p[1] / 255.0; o[2] = p[0] / 255.0; o[3] = p[3] / 255.0; break;
    case NxAttr4_UByte4: for (int i = 0; i < 4; i++) o[i] = p[i]; break;
    case NxAttr4_Short2: o[0] = s[0]; o[1] = s[1]; break;
    case NxAttr4_Short4: for (int i = 0; i < 4; i++) o[i] = s[i]; break;
    case NxAttr4_UByte4N: for (int i = 0; i < 4; i++) o[i] = p[i] / 255.0; break;
    case NxAttr4_Short2N: for (int i = 0; i < 2; i++) o[i] = fmax(s[i] / 32767.0, -1.0); break;
    case NxAttr4_Short4N: for (int i = 0; i < 4; i++) o[i] = fmax(s[i] / 32767.0, -1.0); break;
    case NxAttr4_UShort2N: for (int i = 0; i < 2; i++) o[i] = u[i] / 65535.0; break;
    case NxAttr4_UShort4N: for (int i = 0; i < 4; i++) o[i] = u[i] / 65535.0; break;
    case NxAttr4_UDec3: o[0] = w & 1023; o[1] = w >> 10 & 1023; o[2] = w >> 20 & 1023; break;   /* w = 1: bits 30-31 ignored */
    case NxAttr4_Dec3N:
        o[0] = fmax(((int32_t)(w << 22) >> 22) / 511.0, -1.0); o[1] = fmax(((int32_t)(w << 12) >> 22) / 511.0, -1.0);
        o[2] = fmax(((int32_t)(w << 2) >> 22) / 511.0, -1.0);
        break;
    case NxAttr4_Float16_2: o[0] = t_half(h[0]); o[1] = t_half(h[1]); break;
    case NxAttr4_Float16_4: for (int i = 0; i < 4; i++) o[i] = t_half(h[i]); break;
    case NxAttr4_Count: break;
    }
}

static void element(uint32_t t, uint8_t* p, uint32_t seed, int v) {   /* a representable element; vertex 3 = the extremes */
    for (int i = 0; i < k_bytes[t]; i++) p[i] = (uint8_t)(t_hash(seed * 977u + (uint32_t)i * 31u) >> 9);
    if (t <= NxAttr4_Float4) for (uint32_t i = 0; i <= t; i++) { float f = ((int)(t_hash(seed + i) % 2001) - 1000) / 16.0f; memcpy(p + 4 * i, &f, 4); }
    if (t == NxAttr4_Float16_2 || t == NxAttr4_Float16_4)
        for (int i = 0; i < k_bytes[t] / 2; i++) { uint16_t h; memcpy(&h, p + 2 * i, 2); h = (uint16_t)((h & 0x83FF) | (1 + (h >> 10 & 31) % 30) << 10); memcpy(p + 2 * i, &h, 2); }
    if (v != 3) return;
    if (t == NxAttr4_Short2N || t == NxAttr4_Short4N) for (int i = 0; i < k_bytes[t] / 2; i++) { int16_t m = i & 1 ? 32767 : -32768; memcpy(p + 2 * i, &m, 2); }
    if (t == NxAttr4_UDec3) { uint32_t w = 0xFFFFFFFFu; memcpy(p, &w, 4); }
    if (t == NxAttr4_Dec3N) { uint32_t w = 0xC0000000u | 0x3FFu << 20 | 0x1FFu << 10 | 0x200u; memcpy(p, &w, 4); }   /* x = -512, y = 511, z = -1, w bits 11 */
    if (t == NxAttr4_UByte4N || t == NxAttr4_D3DColor || t == NxAttr4_UByte4) { p[0] = 0; p[1] = 255; p[2] = 1; p[3] = 254; }
}

static void layout1(Stream* s, uint32_t id, uint32_t type, uint32_t offset) {
    NxVtxLayout4 l;
    memset(&l, 0, sizeof l);
    l.attrCount = 1; l.attr[0].offset = (uint16_t)offset; l.attr[0].type = (uint8_t)type;
    t_layout4(s, id, &l);
}

static int near_d(double a, double b) { return fabs(a - b) <= 1e-6 * fmax(1.0, fabs(b)); }

/* draws the 4 elements bound as stream 0 through layout `lay` into target T (4x1 RGBA32F) */
static uint64_t draw4(Stream* s, uint32_t T, uint32_t state, uint32_t base, uint32_t size, uint32_t stride) {
    const uint32_t tg[2] = {T, 0};
    s->w[s->n++] = NxOp_BeginPass4 | 5u << 16; s->w[s->n++] = 1; s->w[s->n++] = tg[0]; s->w[s->n++] = tg[1]; s->w[s->n++] = 0; s->w[s->n++] = 0;
    t_op(s, NxOp_BindVtxN, 5, 0, g_mem, base, size, stride);
    t_op(s, NxOp_BindState, 1, state);
    t_op(s, NxOp_Draw, 5, NxPrim_Points, 4, 1, 0, 0);
    t_op(s, NxOp_EndPass, 0);
    return t_submit(s);
}

static void types(void) {
    uint32_t vs = t_shader(NxStage_Vertex, k_vs_attr), fs = t_shader(NxStage_Fragment, k_fs_pass);
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA32F, 4, 1, 1, 1, 1), T2 = t_image(NxImgType_2D, NxFmt_RGBA32F, 4, 1, 1, 1, 1);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    for (uint32_t t = 0; t < N_TYPES; t++) {
        uint32_t stride = 4 + k_bytes[t] + 4, size = 4 * stride;
        uint8_t* buf = g_scratch + 4096 * (1 + t);
        memset(buf, 0xA5, size);
        for (int v = 0; v < 4; v++) element(t, buf + v * stride + 4, 100 + t * 7 + (uint32_t)v, v);
        t_state(s, 1, vs, fs, 1, T_W0_ALL, 0, 0, 0);
        layout1(s, 1, t, 4);   /* redefined for every type: the next BindState rebuilds the layout */
        CHECKM(draw4(s, T, 1, 4096 * (1 + t), size, stride) == 0, "attribute type %u draws", t);
        float got[16];
        uint32_t b[6] = {0, 0, 0, 4, 1, 1};
        CHECK(t_read(T, 0, 0, b, got, sizeof got, 64) == 0);
        int ok = 1;
        for (int v = 0; v < 4; v++) {
            double e[4];
            expand(t, buf + v * stride + 4, e);
            for (int c = 0; c < 4; c++) ok &= near_d(got[v * 4 + c], e[c]);
            if (!ok && g_fail < 8) printf("  type %u vertex %d: got %g %g %g %g want %g %g %g %g\n", t, v, got[v * 4], got[v * 4 + 1], got[v * 4 + 2], got[v * 4 + 3], e[0], e[1], e[2], e[3]);
            if (!ok) break;
        }
        CHECKM(ok, "attribute type %u equals the D3D9 expansion", t);
        if (t == NxAttr4_UDec3 || t == NxAttr4_Dec3N) {   /* the same bytes through a UByte4 layout: untouched by the conversion */
            t_state(s, 2, vs, fs, 2, T_W0_ALL, 0, 0, 0);
            layout1(s, 2, NxAttr4_UByte4, 4);
            CHECK(draw4(s, T2, 2, 4096 * (1 + t), size, stride) == 0);
            CHECK(t_read(T2, 0, 0, b, got, sizeof got, 64) == 0);
            int raw = 1;
            for (int v = 0; v < 4; v++) for (int c = 0; c < 4; c++) raw &= got[v * 4 + c] == (float)buf[v * stride + 4 + c];
            CHECKM(raw, "type %u bytes read through UByte4 are the unconverted bytes", t);
        }
    }
    free(s);
}

static void instancing(void) {
    static const char vs_src[] = "#version 460\nlayout (location = 0) in vec2 pos;\nlayout (location = 1) in vec2 off;\n"
        "layout (location = 2) in vec4 col;\nlayout (location = 0) flat out vec4 c;\n"
        "void main() { vec2 px = pos + off; gl_PointSize = 1.0; gl_Position = vec4(2.0 * px.x / 16.0 - 1.0, 1.0 - 2.0 * px.y / 16.0, 0.0, 1.0); c = col; }\n";
    uint32_t vs = t_shader(NxStage_Vertex, vs_src), fs = t_shader(NxStage_Fragment, k_fs_pass);
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 1);
    t_zero(T, NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1);
    float pos[4] = {1, 2, 5, 3}, off[6] = {0, 0, 8, 0, 0, 8}, col[4] = {0, 1, 0, 1};
    memcpy(g_scratch + 4096, pos, 16); memcpy(g_scratch + 8192, off, 24); memcpy(g_scratch + 12288, col, 16);
    NxVtxLayout4 l;
    memset(&l, 0, sizeof l);
    l.attrCount = 3; l.instanceStreams = 2; l.divisor[1] = 2;
    l.attr[0] = (NxVtxAttr4){0, 0, NxAttr4_Float2, 0, {0}};
    l.attr[1] = (NxVtxAttr4){0, 1, NxAttr4_Float2, 1, {0}};
    l.attr[2] = (NxVtxAttr4){0, 2, NxAttr4_Float4, 2, {0}};
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_layout4(s, 3, &l);
    t_state(s, 1, vs, fs, 3, T_W0_ALL, 0, 0, 0);
    uint32_t tg[2] = {T, 0};
    s->w[s->n++] = NxOp_BeginPass4 | 5u << 16; s->w[s->n++] = 1; s->w[s->n++] = tg[0]; s->w[s->n++] = tg[1]; s->w[s->n++] = 0; s->w[s->n++] = 0;
    t_op(s, NxOp_BindVtxN, 5, 0, g_mem, 4096, 16, 8);
    t_op(s, NxOp_BindVtxN, 5, 1, g_mem, 8192, 24, 8);
    t_op(s, NxOp_BindVtxN, 5, 2, g_mem, 12288, 16, 0);   /* stride 0: one element for every vertex and instance */
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, NxPrim_Points, 2, 4, 0, 0);   /* 4 instances, divisor 2: elements 0, 0, 1, 1 of the offsets */
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    uint8_t model[16 * 16 * 4] = {0}, *got = t_read_all(T, 0, 0, 16, 16, 1, t_fmt(NxFmt_RGBA8));
    for (int e = 0; e < 2; e++) for (int v = 0; v < 2; v++) {
        int x = (int)(pos[2 * v] + off[2 * e]), y = (int)(pos[2 * v + 1] + off[2 * e + 1]);
        model[(y * 16 + x) * 4 + 1] = 255; model[(y * 16 + x) * 4 + 3] = 255;
    }
    CHECKM(got && !memcmp(got, model, sizeof model), "4 instances, divisor 2 on stream 1 (offset element 2 is never used), stride-0 colour stream");
    free(got); free(s);
}

static void sixteen(void) {
    static char vs_src[2000];
    int n = snprintf(vs_src, sizeof vs_src, "#version 460\n");
    for (int i = 0; i < 16; i++) n += snprintf(vs_src + n, sizeof vs_src - (size_t)n, "layout (location = %d) in float a%d;\n", i, i);
    for (int k = 0; k < 4; k++) n += snprintf(vs_src + n, sizeof vs_src - (size_t)n, "layout (location = %d) flat out vec4 g%d;\n", k, k);
    n += snprintf(vs_src + n, sizeof vs_src - (size_t)n, "void main() { gl_PointSize = 1.0; gl_Position = vec4(float(gl_VertexID) * (2.0 / 3.0) - 1.0, 1.0, 0.0, 1.0);\n");
    for (int k = 0; k < 4; k++) n += snprintf(vs_src + n, sizeof vs_src - (size_t)n, " g%d = vec4(a%d, a%d, a%d, a%d);\n", k, 4 * k, 4 * k + 1, 4 * k + 2, 4 * k + 3);
    snprintf(vs_src + n, sizeof vs_src - (size_t)n, "}\n");
    static const char fs_src[] = "#version 460\nlayout (location = 0) flat in vec4 g0;\nlayout (location = 1) flat in vec4 g1;\n"
        "layout (location = 2) flat in vec4 g2;\nlayout (location = 3) flat in vec4 g3;\n"
        "layout (location = 0) out vec4 o0;\nlayout (location = 1) out vec4 o1;\nlayout (location = 2) out vec4 o2;\nlayout (location = 3) out vec4 o3;\n"
        "void main() { o0 = g0; o1 = g1; o2 = g2; o3 = g3; }\n";
    uint32_t vs = t_shader(NxStage_Vertex, vs_src), fs = t_shader(NxStage_Fragment, fs_src), T[4];
    CHECK(vs && fs);
    for (int k = 0; k < 4; k++) T[k] = t_image(NxImgType_2D, NxFmt_RGBA32F, 3, 1, 1, 1, 1);
    static uint8_t blk2[8192] __attribute__((aligned(4096)));
    NxMemBlockDesc md = {(uint64_t)(uintptr_t)blk2, sizeof blk2, NxMem_Generic};
    uint32_t m2 = (uint32_t)t_ext(NXGPU_FN_MEM_CREATE, 1, (uint64_t)(uintptr_t)&md);
    NxVtxLayout4 l;
    memset(&l, 0, sizeof l);
    l.attrCount = 16;
    for (int i = 0; i < 16; i++) l.attr[i] = (NxVtxAttr4){(uint16_t)((i % 4) * 4), (uint8_t)i, NxAttr4_Float1, (uint8_t)i, {0}};
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_layout4(s, 4, &l);
    t_state(s, 1, vs, fs, 4, T_W0_ALL, 0, 0, 0);
    s->w[s->n++] = NxOp_BeginPass4 | 11u << 16; s->w[s->n++] = 4;
    for (int k = 0; k < 4; k++) { s->w[s->n++] = T[k]; s->w[s->n++] = 0; }
    s->w[s->n++] = 0; s->w[s->n++] = 0;
    for (int i = 0; i < 16; i++) {   /* even streams in the scratch block, odd streams in blk2; strides 32 + 8 * (i % 3) */
        uint32_t stride = 32 + 8 * (uint32_t)(i % 3);
        uint8_t* base = (i & 1 ? blk2 : g_scratch) + 256 * (uint32_t)i + (i & 1 ? 0 : 65536);
        memset(base, 0xEE, 3 * stride);
        for (int v = 0; v < 3; v++) { float f = (float)((i + 1) * 10 + v); memcpy(base + v * stride + (i % 4) * 4, &f, 4); }
        t_op(s, NxOp_BindVtxN, 5, (uint32_t)i, i & 1 ? m2 : g_mem, 256 * (uint32_t)i + (i & 1 ? 0 : 65536), 3 * stride, stride);
    }
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, NxPrim_Points, 3, 1, 0, 0);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    int ok = 1;
    for (int k = 0; k < 4; k++) {
        float got[12];
        uint32_t b[6] = {0, 0, 0, 3, 1, 1};
        ok &= t_read(T[k], 0, 0, b, got, sizeof got, 48) == 0;
        for (int v = 0; v < 3; v++) for (int c = 0; c < 4; c++) ok &= got[v * 4 + c] == (float)((4 * k + c + 1) * 10 + v);
    }
    CHECKM(ok, "16 streams (two blocks, offsets, strides) -> 16 attributes");
    free(s);
}

static void refused(void) {
    NxVtxLayout4 base;
    memset(&base, 0, sizeof base);
    base.attrCount = 1; base.attr[0].type = NxAttr4_Float4;
    typedef struct { const char* name; NxVtxLayout4 l; uint32_t id; } Case;
    Case cs[10];
    for (int i = 0; i < 10; i++) { cs[i].l = base; cs[i].id = 20 + (uint32_t)i; }
    cs[0].name = "type = Count"; cs[0].l.attr[0].type = NxAttr4_Count;
    cs[1].name = "stream 16"; cs[1].l.attr[0].stream = 16;
    cs[2].name = "location 16"; cs[2].l.attr[0].location = 16;
    cs[3].name = "duplicate location"; cs[3].l.attrCount = 2; cs[3].l.attr[1] = cs[3].l.attr[0];
    cs[4].name = "instance stream, divisor 0"; cs[4].l.instanceStreams = 1;
    cs[5].name = "17 attributes"; cs[5].l.attrCount = 17;
    cs[6].name = "instance stream bit 16"; cs[6].l.instanceStreams = 1u << 16;
    cs[7].name = "offset beyond the GL limit"; cs[7].l.attr[0].offset = 65535;
    cs[8].name = "layout id 0"; cs[8].id = 0;
    cs[9].name = "layout id 257"; cs[9].id = 257;
    Stream* s = (Stream*)calloc(1, sizeof *s);
    for (int i = 0; i < 10; i++) {
        if (i == 7 && g4.maxAttrOffset >= 65535) continue;   /* a GL that takes any u16 offset has no limit to test */
        NxVtxLayout4 good = base;
        t_layout4(s, 40 + (uint32_t)i, &good);   /* executes only if the stream is valid: it must not */
        t_layout4(s, cs[i].id, &cs[i].l);
        CHECKM(t_submit(s) == NXGPU_BAD_CALL, "layout refused: %s", cs[i].name);
        CHECKM(!g4.lay[40 + i].defined, "nothing before the bad layout (%s) executed", cs[i].name);
    }
    t_op(s, NxOp_BindVtxN, 5, 16, g_mem, 0, 16, 16);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    t_op(s, NxOp_BindVtxN, 5, 0, g_mem, 0, 16, 1u << 20);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    t_op(s, NxOp_BindVtxN, 5, 0, g_mem, SCRATCH_SIZE, 16, 16);   /* range outside the block: refused when it runs */
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    /* a draw whose layout reads a stream that has no BindVtxN (stream 7 was never bound; stream 0 unbound explicitly) */
    uint32_t vs = t_shader(NxStage_Vertex, k_vs_attr), fs = t_shader(NxStage_Fragment, k_fs_pass), T = t_image(NxImgType_2D, NxFmt_RGBA32F, 4, 1, 1, 1, 1);
    NxVtxLayout4 l7 = base;
    l7.attr[0].stream = 7;
    t_layout4(s, 5, &l7);
    t_state(s, 1, vs, fs, 5, T_W0_ALL, 0, 0, 0);
    uint32_t tg[2] = {T, 0};
    s->w[s->n++] = NxOp_BeginPass4 | 5u << 16; s->w[s->n++] = 1; s->w[s->n++] = tg[0]; s->w[s->n++] = tg[1]; s->w[s->n++] = 0; s->w[s->n++] = 0;
    t_op(s, NxOp_BindVtxN, 5, 7, 0, 0, 0, 0);   /* block 0, size 0: unbinds */
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, NxPrim_Points, 4, 1, 0, 0);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    free(s);
}

void part_j(void) { types(); instancing(); sixteen(); refused(); }
