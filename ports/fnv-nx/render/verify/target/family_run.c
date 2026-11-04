/* V6n: runs one of S6n's family shader pairs (render/native/shaders, expanded by build.py) on S6n's GL backend
 * (render/native/gl/nxgl.c, NxGpuApi + command stream) and writes the colour as a .v6nd dump (RGBA32F target, no
 * depth), so that it can be compared with the original D3D9 program run by v6n_nine on equivalent inputs
 * (tests/v6n/claims/c69_families.py). Synthetic inputs only.
 *
 *   v6n_family FILE.fam OUT.v6nd
 * FILE.fam (one command per line):
 *   size W H                      target size (default 16 16)
 *   vs PATH.glsl | ps PATH.glsl   family stages (GLSL 4.60, deko3d flavour, as build.py writes them)
 *   ubo F F F ...                 the family block, slot 1, both stages (floats, std140 order of nx/families.h)
 *   epi F*12                      optional NxEpilogue (fragment slot 2): fogColor[4], fogParams[4], modes[4] (as ints)
 *   tex SLOT RRGGBBAA [x3]        a 1x1 (or 2x2: four texels, row-major) RGBA8 texture, point sampled, wrap, at SLOT
 *   layout C C ...                float vertex attributes: attribute i = location i with C components
 *   v F F ...                     one vertex (sum of C floats)
 *   clear RRGGBBAA                clear colour of the target (default 0)
 *   blend SRC DST                 D3DBLEND factors for colour + alpha (default none)
 *   draw N                        N triangles from the vertices so far
 * The state is D3D9's defaults through S6n's translator (no depth, no cull). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "d3d9_state.h"
#include "nx/gpu_abi.h"
#include "nxgl.h"

#define CALL1(f, a) ((uint64_t (*)(uint64_t))(uintptr_t)(f))((uint64_t)(uintptr_t)(a))
static uint32_t cmd[1 << 16]; static uint32_t nw;
static void emit(uint32_t op, const uint32_t* a, uint32_t n) { cmd[nw++] = op | n << 16; memcpy(cmd + nw, a, n * 4); nw += n; }
static uint32_t fb(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static char* slurp(const char* p) {
    FILE* f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char* s = malloc(n + 1); if (fread(s, 1, n, f) != (size_t)n) { fclose(f); free(s); return NULL; }
    s[n] = 0; fclose(f); return s;
}

int main(int argc, char** argv) {
    if (argc != 3) { fprintf(stderr, "usage: v6n_family FILE.fam OUT.v6nd\n"); return 2; }
    FILE* f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    int W = 16, H = 16; uint32_t vs = 0, ps = 0, color = 0;
    static uint8_t block[1 << 20] __attribute__((aligned(4096))); uint32_t used = 0;
    NxMemBlockDesc m = {(uint64_t)(uintptr_t)block, sizeof block, NxMem_Generic};
    uint32_t mem = (uint32_t)CALL1(api->mem_create, &m);
    float ubo[256]; int nubo = 0; float epi[12]; int has_epi = 0;
    uint32_t tex[8] = {0}; int ntex = 0;
    uint8_t comps[8]; int nattr = 0; float verts[4096]; int nf = 0;
    uint32_t bsrc = 0, bdst = 0; int blend = 0; uint32_t clearc = 0;
    char line[8192];
    while (fgets(line, sizeof line, f)) {
        char* a[600]; int n = 0;
        char* h = strchr(line, '#'); if (h) *h = 0;
        for (char* t = strtok(line, " \t\r\n"); t && n < 600; t = strtok(NULL, " \t\r\n")) a[n++] = t;
        if (!n) continue;
        if (!strcmp(a[0], "size") && n == 3) { W = atoi(a[1]); H = atoi(a[2]); }
        else if ((!strcmp(a[0], "vs") || !strcmp(a[0], "ps")) && n == 2) {
            char* src = slurp(a[1]); if (!src) { fprintf(stderr, "cannot read %s\n", a[1]); return 2; }
            NxShaderDesc d; memset(&d, 0, sizeof d);
            d.stage = a[0][0] == 'v' ? NxStage_Vertex : NxStage_Fragment; d.glsl = (uint64_t)(uintptr_t)src; d.glsl_size = (uint32_t)strlen(src);
            uint32_t id = (uint32_t)CALL1(api->shader_create, &d);
            if (!id) { fprintf(stderr, "shader %s: %s\n", a[1], nxgl_last_error()); return 1; }
            if (a[0][0] == 'v') vs = id; else ps = id;
        } else if (!strcmp(a[0], "ubo")) { for (int i = 1; i < n && nubo < 256; i++) ubo[nubo++] = strtof(a[i], NULL); }
        else if (!strcmp(a[0], "epi") && n == 13) { for (int i = 0; i < 12; i++) epi[i] = strtof(a[1 + i], NULL); has_epi = 1; }
        else if (!strcmp(a[0], "tex") && (n == 3 || n == 6)) {   /* 1x1, or 2x2 (four texels, row-major) */
            int s = atoi(a[1]), nt = n - 2; uint8_t px[16];
            for (int k = 0; k < nt; k++) { unsigned v = (unsigned)strtoul(a[2 + k], NULL, 16);
                px[4 * k] = (uint8_t)(v >> 24); px[4 * k + 1] = (uint8_t)(v >> 16); px[4 * k + 2] = (uint8_t)(v >> 8); px[4 * k + 3] = (uint8_t)v; }
            NxImageDesc d; memset(&d, 0, sizeof d); d.width = nt == 4 ? 2 : 1; d.height = nt == 4 ? 2 : 1; d.format = NxFmt_RGBA8;
            d.data = (uint64_t)(uintptr_t)px; d.data_size = 4 * nt;
            if (s >= 0 && s < 8) { tex[s] = (uint32_t)CALL1(api->image_create, &d); if (s + 1 > ntex) ntex = s + 1; }
        } else if (!strcmp(a[0], "layout")) { nattr = 0; for (int i = 1; i < n && nattr < 8; i++) comps[nattr++] = (uint8_t)atoi(a[i]); }
        else if (!strcmp(a[0], "v")) { for (int i = 1; i < n && nf < 4096; i++) verts[nf++] = strtof(a[i], NULL); }
        else if (!strcmp(a[0], "clear") && n == 2) clearc = (uint32_t)strtoul(a[1], NULL, 16);
        else if (!strcmp(a[0], "blend") && n == 3) { bsrc = (uint32_t)atoi(a[1]); bdst = (uint32_t)atoi(a[2]); blend = 1; }
        else if (!strcmp(a[0], "draw") && n == 2) {
            if (!color) {
                NxImageDesc d; memset(&d, 0, sizeof d); d.width = (uint16_t)W; d.height = (uint16_t)H; d.format = NxFmt_RGBA32F; d.flags = NxImg_RenderTarget;
                color = (uint32_t)CALL1(api->image_create, &d);
                uint32_t rgba = (clearc >> 24) | (clearc >> 8 & 0xFF00) | (clearc << 8 & 0xFF0000) | (clearc << 24);   /* R low */
                uint32_t bp[8] = {color, 0, 1, rgba, fb(1.0f), 0, (uint32_t)W, (uint32_t)H};
                emit(NxOp_BeginPass, bp, 8);
            }
            NxVtxLayout L; memset(&L, 0, sizeof L); int stride = 0;
            for (int i = 0; i < nattr; i++) { L.attr[i].offset = (uint8_t)(stride * 4); L.attr[i].type = NxAttr_Float32; L.attr[i].components = comps[i]; stride += comps[i]; }
            L.count = (uint8_t)nattr; L.stride = (uint8_t)(stride * 4);
            uint32_t la[10]; la[0] = 1; memcpy(la + 1, &L, sizeof L); emit(NxOp_DefineLayout, la, 10);
            uint32_t rs[NXD3D_RS_COUNT]; nx_d3d9_default_rs(rs);
            rs[22] = 1;   /* CULLMODE NONE */ rs[7] = 0;   /* ZENABLE FALSE */
            if (blend) { rs[27] = 1; rs[19] = bsrc; rs[20] = bdst; }   /* ALPHABLENDENABLE, SRCBLEND, DESTBLEND (D3DBLEND) */
            NxD3D9Input in; memset(&in, 0, sizeof in); in.rs = rs; in.ps_version = 0x300;
            in.viewport = (NxD3D9Viewport){0, 0, (uint32_t)W, (uint32_t)H, 0.0f, 1.0f}; in.scissor = (NxD3D9Rect){0, 0, W, H};
            NxRenderState st; NxDynamicState dyn; uint32_t sb; nx_d3d9_translate(&in, &st, &dyn, &sb);
            uint32_t ds[12] = {1, vs, ps, 1};
            for (int i = 0; i < 4; i++) { ds[4 + 2 * i] = (uint32_t)st.w[i]; ds[5 + 2 * i] = (uint32_t)(st.w[i] >> 32); }
            emit(NxOp_DefineState, ds, 12); uint32_t id = 1; emit(NxOp_BindState, &id, 1);
            uint32_t off = (used + 255) & ~255u; memcpy(block + off, ubo, sizeof ubo); used = off + sizeof ubo;
            { uint32_t a5[5] = {3, 1, mem, off, sizeof ubo}; emit(NxOp_BindUbo, a5, 5); }
            if (has_epi) {
                struct { float c[4], p[4]; uint32_t m[4]; } e;
                memcpy(e.c, epi, 16); memcpy(e.p, epi + 4, 16); for (int i = 0; i < 4; i++) e.m[i] = (uint32_t)epi[8 + i];
                off = (used + 255) & ~255u; memcpy(block + off, &e, sizeof e); used = off + sizeof e;
                uint32_t a5[5] = {2, 2, mem, off, sizeof e}; emit(NxOp_BindUbo, a5, 5);
            }
            off = (used + 255) & ~255u; memcpy(block + off, verts, nf * 4); used = off + nf * 4;
            { uint32_t a3[3] = {mem, off, (uint32_t)nf * 4}; emit(NxOp_BindVtx, a3, 3); }
            if (ntex) {
                uint32_t th[11] = {NxStage_Fragment, 0, (uint32_t)ntex};
                NxSamplerState ss; uint32_t samp[NXD3D_SAMP_COUNT]; nx_d3d9_default_samp(samp);
                NxD3D9Texture ti = {21, 0, 0, 0, 0}; nx_d3d9_sampler(samp, &ti, &ss);
                uint32_t smp = (uint32_t)CALL1(api->sampler_create, &ss);
                for (int s = 0; s < ntex; s++) th[3 + s] = NX_TEX_HANDLE(tex[s], smp);
                emit(NxOp_BindTextures, th, 3 + ntex);
            }
            uint32_t dr[5] = {NxPrim_Triangles, (uint32_t)atoi(a[1]) * 3, 1, 0, 0}; emit(NxOp_Draw, dr, 5);
            nf = 0;
        } else { fprintf(stderr, "%s: cannot parse %s\n", argv[1], a[0]); return 2; }
    }
    emit(NxOp_EndPass, NULL, 0); emit(NxOp_End, NULL, 0);
    NxSubmit sub; memset(&sub, 0, sizeof sub); sub.stream = (uint64_t)(uintptr_t)cmd; sub.words = nw; sub.frame = 1;
    if (CALL1(api->submit, &sub)) { fprintf(stderr, "submit failed: %s\n", nxgl_last_error()); return 1; }
    float* px = calloc((size_t)W * H * 4, 4);
    NxReadback r; memset(&r, 0, sizeof r); r.image = color; r.w = W; r.h = H; r.format = NxRb_RGBA32F;
    r.dst = (uint64_t)(uintptr_t)px; r.dst_size = W * H * 16;
    if (CALL1(api->readback, &r)) { fprintf(stderr, "readback failed\n"); return 1; }
    FILE* o = fopen(argv[2], "wb"); if (!o) { perror(argv[2]); return 2; }
    fprintf(o, "V6ND %d %d 4 1 0 0 116 0\n", W, H);   /* A32B32G32R32F, no depth */
    fwrite(px, 4, (size_t)W * H * 4, o); fclose(o);
    nxgl_destroy();
    return 0;
}
