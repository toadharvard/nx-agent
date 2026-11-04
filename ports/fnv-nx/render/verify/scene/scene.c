/* V6n scene runner: parses a scene (text, one command per line) and executes it on the V6nOps implementation the
 * executable was linked with (v6n_target.h), then writes the read-back as <out>/<scene>.v6nd.
 *
 *   v6n_nine|v6n_gl [-o OUTDIR] [-q] SCENE...
 *
 * Commands (values: decimal, 0x hex, or f:<float> for a float-in-DWORD; names are the D3D9 names without prefix):
 *   size W H                 render target size (default 32 32)
 *   rt FMT                   colour target format (A8R8G8B8 X8R8G8B8 A16B16G16R16F A32B32G32R32F R5G6B5 ...)
 *   ds FMT|NONE              depth-stencil format (D24S8 D24X8 D16 D32F_LOCKABLE NONE)
 *   clear FLAGS COLOR Z S    IDirect3DDevice9::Clear (FLAGS: 1 target, 2 zbuffer, 4 stencil)
 *   rs NAME VALUE            SetRenderState            samp S NAME VALUE   SetSamplerState
 *   tss S NAME VALUE         SetTextureStageState      notex S             SetTexture(S, NULL)
 *   tex S FMT W H LEVELS hash SEED | solid HEXBYTES    texture from a deterministic generator (D3D memory layout)
 *   texcube S FMT SIZE hash SEED                        cube texture, one level; face f is generated with SEED + f
 *   vp X Y W H MINZ MAXZ     SetViewport               scissor L T R B     SetScissorRect
 *   proj 16 floats           SetTransform(PROJECTION) (row-major)
 *   mode rhw|xyz             vertex position kind for the following vertices
 *   v X Y Z RHW DIFFUSE SPECULAR U V                    one vertex (RHW ignored in xyz mode)
 *   draw PRIM COUNT          DrawPrimitiveUP with the pending vertices (PRIM: TRIANGLELIST TRIANGLESTRIP
 *                            TRIANGLEFAN LINELIST LINESTRIP POINTLIST), then clears them
 *   # ...                    comment
 * The harness sets LIGHTING = 0 after begin (vertices carry no normals). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdint.h>
#include <math.h>
#include "v6n_target.h"
#include "names.h"
#include <unistd.h>
#include <sys/wait.h>

static uint32_t parse_val(const char* s, int* ok) {
    char* e; *ok = 1;
    if (!strncmp(s, "f:", 2)) { float f = strtof(s + 2, &e); uint32_t u; memcpy(&u, &f, 4); if (*e) *ok = 0; return u; }
    unsigned long long v = strtoull(s, &e, 0);
    if (*e || e == s) { long long sv = strtoll(s, &e, 0); if (*e || e == s) { *ok = 0; return 0; } return (uint32_t)sv; }
    return (uint32_t)v;
}
static int lookup(const V6nName* t, const char* s, uint32_t* out) {
    for (; t->name; t++) if (!strcasecmp(t->name, s)) { *out = t->value; return 1; }
    int ok; *out = parse_val(s, &ok); return ok;
}

/* ---- texture generator ------------------------------------------------------------------------------------- */
static uint64_t mix64(uint64_t x) { x ^= x >> 33; x *= 0xff51afd7ed558ccdull; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ull; x ^= x >> 33; return x; }
static uint16_t f2h(float f) {   /* round-to-nearest-even float -> half for values in [0, 65504] */
    uint32_t u; memcpy(&u, &f, 4);
    uint32_t sign = (u >> 16) & 0x8000; int e = (int)((u >> 23) & 0xff) - 127 + 15; uint32_t m = u & 0x7fffff;
    if (e <= 0) { if (e < -10) return (uint16_t)sign; m |= 0x800000; int sh = 14 - e; uint32_t h = m >> sh;
        if ((m >> (sh - 1)) & 1 && ((m & ((1u << (sh - 1)) - 1)) || (h & 1))) h++; return (uint16_t)(sign | h); }
    if (e >= 31) return (uint16_t)(sign | 0x7c00);
    uint32_t h = sign | (uint32_t)e << 10 | m >> 13;
    if ((m >> 12) & 1 && ((m & 0xfff) || (h & 1))) h++;
    return (uint16_t)h;
}
int v6n_level_bytes(uint32_t fmt, int w, int h) {
    const V6nFmt* f = v6n_fmt(fmt);
    if (!f) return -1;
    if (f->block) return ((w + 3) / 4) * ((h + 3) / 4) * f->bytes;
    return w * h * f->bytes;
}
static uint8_t* gen_level(uint32_t fmt, int w, int h, int level, const char* gen, uint64_t seed, const char* solid, int* n) {
    const V6nFmt* f = v6n_fmt(fmt);
    int bytes = v6n_level_bytes(fmt, w, h);
    uint8_t* d = malloc(bytes > 0 ? bytes : 1);
    *n = bytes;
    if (!strcmp(gen, "solid")) {
        uint8_t pat[64]; int np = 0;
        for (const char* p = solid; p[0] && p[1] && np < 64; p += 2) { unsigned b; sscanf(p, "%2x", &b); pat[np++] = (uint8_t)b; }
        for (int i = 0; i < bytes; i++) d[i] = np ? pat[i % np] : 0;
        return d;
    }
    for (int i = 0; i < bytes; i++) d[i] = (uint8_t)mix64(seed * 1000003u + (uint64_t)level * 7919u + (uint64_t)i);
    if (f->kind == 'h') {          /* half floats in [0, 1] */
        for (int i = 0; i < bytes / 2; i++) { float v = (float)(mix64(seed + (uint64_t)i * 31 + level) % 4097) / 4096.0f; uint16_t hh = f2h(v); memcpy(d + 2 * i, &hh, 2); }
    } else if (f->kind == 'f') {   /* floats in [0, 1] */
        for (int i = 0; i < bytes / 4; i++) { float v = (float)(mix64(seed + (uint64_t)i * 31 + level) % 65537) / 65536.0f; memcpy(d + 4 * i, &v, 4); }
    }
    return d;
}

/* ---- dump ------------------------------------------------------------------------------------------------- */
static int write_dump(const char* path, const V6nReadback* r, uint32_t rt, uint32_t ds) {
    FILE* f = fopen(path, "wb");
    if (!f) return 0;
    fprintf(f, "V6ND %d %d %d %d %d %d %u %u\n", r->w, r->h, r->color_channels, r->color_is_float, r->depth != 0,
            r->stencil != 0, rt, ds);
    fwrite(r->color, sizeof(float), (size_t)r->w * r->h * 4, f);
    if (r->depth) fwrite(r->depth, 4, (size_t)r->w * r->h, f);
    if (r->stencil) fwrite(r->stencil, 1, (size_t)r->w * r->h, f);
    fclose(f);
    return 1;
}

/* ---- run one scene ------------------------------------------------------------------------------------------ */
#define MAXV 4096
static int run_scene(const V6nOps* ops, const char* path, const char* outdir, int quiet) {
    FILE* f = fopen(path, "r");
    if (!f) { fprintf(stderr, "%s: cannot open\n", path); return 0; }
    int w = 32, h = 32; uint32_t rt = 21 /* A8R8G8B8 */, ds = 75 /* D24S8 */;
    V6nTarget* t = NULL;
    static V6nVertex verts[MAXV]; int nv = 0, rhw = 1;
    static float gdata[MAXV * 64]; static uint8_t gus[16], gix[16]; int gne = 0, gnv = 0;   /* vdecl / vg */
    char line[4096], err[256] = "";
    int lineno = 0, ok = 1;
#define BEGIN() do { if (!t) { t = ops->begin(w, h, rt, ds, err, sizeof err); if (!t) { fprintf(stderr, "%s: begin: %s\n", path, err); ok = 0; goto done; } ops->render_state(t, 137 /* LIGHTING */, 0); } } while (0)
#define FAIL(...) do { fprintf(stderr, "%s:%d: ", path, lineno); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); ok = 0; goto done; } while (0)
    while (fgets(line, sizeof line, f)) {
        lineno++;
        char* hash = strchr(line, '#'); if (hash) *hash = 0;
        char* a[80]; int n = 0;
        for (char* tok = strtok(line, " \t\r\n"); tok && n < 80; tok = strtok(NULL, " \t\r\n")) a[n++] = tok;
        if (!n) continue;
        uint32_t x, y, z; int vok;
        if (!strcmp(a[0], "size") && n == 3) { if (t) FAIL("size after begin"); w = atoi(a[1]); h = atoi(a[2]); }
        else if (!strcmp(a[0], "rt") && n == 2) { if (t || !lookup(V6N_FMT, a[1], &rt)) FAIL("bad rt"); }
        else if (!strcmp(a[0], "ds") && n == 2) { if (t) FAIL("ds after begin"); if (!strcasecmp(a[1], "NONE")) ds = 0; else if (!lookup(V6N_FMT, a[1], &ds)) FAIL("bad ds"); }
        else if (!strcmp(a[0], "clear") && n == 5) {
            BEGIN(); x = parse_val(a[1], &vok); y = parse_val(a[2], &vok);
            ops->clear(t, x, y, strtof(a[3], NULL), parse_val(a[4], &vok));
        } else if (!strcmp(a[0], "rs") && n == 3) {
            BEGIN(); if (!lookup(V6N_RS, a[1], &x)) FAIL("unknown render state %s", a[1]);
            if (!lookup(V6N_VALUE, a[2], &y)) FAIL("bad value %s", a[2]);
            ops->render_state(t, x, y);
        } else if (!strcmp(a[0], "samp") && n == 4) {
            BEGIN(); if (!lookup(V6N_SAMP, a[2], &x)) FAIL("unknown sampler state %s", a[2]);
            if (!lookup(V6N_VALUE, a[3], &y)) FAIL("bad value %s", a[3]);
            ops->sampler_state(t, (uint32_t)atoi(a[1]), x, y);
        } else if (!strcmp(a[0], "tss") && n == 4) {
            BEGIN(); if (!lookup(V6N_TSS, a[2], &x)) FAIL("unknown stage state %s", a[2]);
            if (!lookup(V6N_VALUE, a[3], &y)) FAIL("bad value %s", a[3]);
            ops->stage_state(t, (uint32_t)atoi(a[1]), x, y);
        } else if (!strcmp(a[0], "notex") && n == 2) { BEGIN(); ops->no_texture(t, (uint32_t)atoi(a[1])); }
        else if (!strcmp(a[0], "tex") && n >= 8) {
            BEGIN();
            uint32_t fmt; if (!lookup(V6N_FMT, a[2], &fmt) || !v6n_fmt(fmt)) FAIL("unknown texture format %s", a[2]);
            int tw = atoi(a[3]), th = atoi(a[4]), lv = atoi(a[5]);
            if (lv < 1 || lv > 14 || tw < 1 || th < 1) FAIL("bad texture size");
            uint8_t* lev[14]; int bytes;
            for (int i = 0; i < lv; i++) {
                int lw = tw >> i > 0 ? tw >> i : 1, lh = th >> i > 0 ? th >> i : 1;
                lev[i] = gen_level(fmt, lw, lh, i, a[6], strtoull(a[7], NULL, 0), a[7], &bytes);
            }
            int r = ops->texture(t, (uint32_t)atoi(a[1]), fmt, tw, th, lv, (const uint8_t* const*)lev);
            for (int i = 0; i < lv; i++) free(lev[i]);
            if (!r) FAIL("texture %s not supported by %s", a[2], ops->name);
        } else if (!strcmp(a[0], "texcube") && n == 6) {
            BEGIN();
            uint32_t fmt; if (!lookup(V6N_FMT, a[2], &fmt) || !v6n_fmt(fmt)) FAIL("unknown texture format %s", a[2]);
            int sz = atoi(a[3]); if (sz < 1 || sz > 4096) FAIL("bad cube size");
            if (!ops->texture_cube) FAIL("cube textures not supported by %s", ops->name);
            uint8_t* f[6]; int bytes;
            for (int i = 0; i < 6; i++) f[i] = gen_level(fmt, sz, sz, 0, a[4], strtoull(a[5], NULL, 0) + (uint64_t)i, a[5], &bytes);
            int r = ops->texture_cube(t, (uint32_t)atoi(a[1]), fmt, sz, (const uint8_t* const*)f);
            for (int i = 0; i < 6; i++) free(f[i]);
            if (!r) FAIL("cube texture %s not supported by %s", a[2], ops->name);
        } else if (!strcmp(a[0], "vp") && n == 7) {
            BEGIN(); ops->viewport(t, (uint32_t)atoi(a[1]), (uint32_t)atoi(a[2]), (uint32_t)atoi(a[3]), (uint32_t)atoi(a[4]), strtof(a[5], NULL), strtof(a[6], NULL));
        } else if (!strcmp(a[0], "scissor") && n == 5) { BEGIN(); ops->scissor(t, atoi(a[1]), atoi(a[2]), atoi(a[3]), atoi(a[4])); }
        else if (!strcmp(a[0], "proj") && n == 17) { BEGIN(); float m[16]; for (int i = 0; i < 16; i++) m[i] = strtof(a[1 + i], NULL); ops->projection(t, m); }
        else if ((!strcmp(a[0], "ps") || !strcmp(a[0], "vs")) && (n == 2 || n == 3)) {
            int isvs = a[0][0] == 'v';
            /* ps BYTECODE_FILE [GLSL_FILE] | ps NONE ; relative paths are relative to the scene file */
            BEGIN();
            int (*setsh)(V6nTarget*, const uint32_t*, int, const char*) = isvs ? ops->vertex_shader : ops->pixel_shader;
            if (!strcmp(a[1], "NONE")) { if (!setsh || !setsh(t, NULL, 0, NULL)) FAIL("%s NONE", a[0]); continue; }
            char p1[1024], p2[1024]; const char* sl = strrchr(path, '/');
            int dl = sl ? (int)(sl - path + 1) : 0;
            snprintf(p1, sizeof p1, "%s%.*s%s", a[1][0] == '/' ? "" : "", a[1][0] == '/' ? 0 : dl, path, a[1]);
            FILE* bf = fopen(p1, "rb"); if (!bf) FAIL("cannot open %s", p1);
            static uint32_t tok[65536]; int nt = (int)fread(tok, 4, 65536, bf); fclose(bf);
            static char glsl[1 << 20]; glsl[0] = 0;
            if (n == 3) {
                snprintf(p2, sizeof p2, "%.*s%s", a[2][0] == '/' ? 0 : dl, path, a[2]);
                FILE* gf = fopen(p2, "rb"); if (!gf) FAIL("cannot open %s", p2);
                size_t k = fread(glsl, 1, sizeof glsl - 1, gf); glsl[k] = 0; fclose(gf);
            }
            if (!setsh || !setsh(t, tok, nt, n == 3 ? glsl : NULL)) FAIL("%s not supported by %s", a[0], ops->name);
        } else if ((!strcmp(a[0], "psc") || !strcmp(a[0], "vsc")) && n == 6) {
            BEGIN(); float v[4]; for (int k = 0; k < 4; k++) v[k] = strtof(a[2 + k], NULL);
            void (*setc)(V6nTarget*, uint32_t, const float*) = a[0][0] == 'p' ? ops->ps_const : ops->vs_const;
            if (setc) setc(t, (uint32_t)atoi(a[1]), v);
        } else if (!strcmp(a[0], "drawidx") && n >= 4) {
            /* drawidx PRIM COUNT i0 i1 ...: DrawIndexedPrimitiveUP over the pending vertices (16-bit indices) */
            BEGIN(); if (!lookup(V6N_PRIM, a[1], &x)) FAIL("unknown primitive %s", a[1]);
            uint16_t idx[64]; int ni = 0;
            for (int k = 3; k < n && ni < 64; k++) idx[ni++] = (uint16_t)atoi(a[k]);
            if (!ops->draw_indexed || !ops->draw_indexed(t, x, (uint32_t)atoi(a[2]), rhw, verts, nv, idx, ni)) FAIL("drawidx failed on %s", ops->name);
            nv = 0;
        }
        else if (!strcmp(a[0], "mode") && n == 2) rhw = !strcmp(a[1], "rhw");
        else if (!strcmp(a[0], "vdecl") && n >= 2 && n <= 17) {
            /* vdecl USAGE:INDEX ... (D3DDECLUSAGE numbers, FLOAT4 each) for vg / drawg (vertex-shader parity) */
            gne = n - 1; gnv = 0;
            for (int k = 0; k < gne; k++) { gus[k] = (uint8_t)atoi(a[1 + k]); const char* c = strchr(a[1 + k], ':'); gix[k] = c ? (uint8_t)atoi(c + 1) : 0; }
        } else if (!strcmp(a[0], "vg") && gne && n == 1 + 4 * gne) {
            if (gnv == MAXV) FAIL("too many vertices");
            for (int k = 0; k < 4 * gne; k++) gdata[gnv * 64 + k] = strtof(a[1 + k], NULL);
            gnv++;
        } else if (!strcmp(a[0], "drawg") && n == 3) {
            BEGIN(); if (!lookup(V6N_PRIM, a[1], &x)) FAIL("unknown primitive %s", a[1]);
            /* repack to stride 16 * gne */
            static float pk[MAXV * 64];
            for (int i = 0; i < gnv; i++) memcpy(pk + i * 4 * gne, gdata + i * 64, 16 * gne);
            if (!ops->draw_generic || !ops->draw_generic(t, x, (uint32_t)atoi(a[2]), gne, gus, gix, pk, gnv)) FAIL("drawg failed on %s", ops->name);
            gnv = 0;
        }
        else if (!strcmp(a[0], "v") && n == 9) {
            if (nv == MAXV) FAIL("too many vertices");
            V6nVertex* v = &verts[nv++];
            v->x = strtof(a[1], NULL); v->y = strtof(a[2], NULL); v->z = strtof(a[3], NULL); v->rhw = strtof(a[4], NULL);
            v->diffuse = parse_val(a[5], &vok); v->specular = parse_val(a[6], &vok);
            v->u = strtof(a[7], NULL); v->v = strtof(a[8], NULL);
        } else if (!strcmp(a[0], "draw") && n == 3) {
            BEGIN(); if (!lookup(V6N_PRIM, a[1], &x)) FAIL("unknown primitive %s", a[1]);
            z = (uint32_t)atoi(a[2]);
            if (!ops->draw(t, x, z, rhw, verts, nv)) FAIL("draw failed on %s", ops->name);
            nv = 0;
        } else FAIL("cannot parse: %s (%d args)", a[0], n);
    }
    BEGIN();
    {
        V6nReadback r; memset(&r, 0, sizeof r);
        if (!ops->readback(t, &r)) FAIL("readback failed");
        const char* base = strrchr(path, '/'); base = base ? base + 1 : path;
        char out[1024]; snprintf(out, sizeof out, "%s/%s.v6nd", outdir, base);
        char* dot = strstr(out, ".scene.v6nd"); if (dot) strcpy(dot, ".v6nd");
        if (!write_dump(out, &r, rt, ds)) FAIL("cannot write %s", out);
        if (!quiet) printf("%s -> %s\n", path, out);
    }
done:
    if (t) ops->end(t);
    fclose(f);
    return ok;
}

int main(int argc, char** argv) {
    const char* outdir = ".";
    int quiet = 0, bad = 0, n = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-o") && i + 1 < argc) { outdir = argv[++i]; continue; }
        if (!strcmp(argv[i], "-q")) { quiet = 1; continue; }
        n++;
        /* one child per scene: a crash in a driver (softpipe has a few) costs that scene only */
        fflush(stdout); fflush(stderr);
        pid_t pid = fork();
        if (pid == 0) _exit(run_scene(&v6n_target_ops, argv[i], outdir, quiet) ? 0 : 1);
        int st = 0;
        waitpid(pid, &st, 0);
        if (WIFSIGNALED(st)) { fprintf(stderr, "%s: CRASH signal %d in %s\n", argv[i], WTERMSIG(st), v6n_target_ops.name); bad++; }
        else if (!WIFEXITED(st) || WEXITSTATUS(st)) bad++;
    }
    if (!n) { fprintf(stderr, "usage: %s [-o OUTDIR] [-q] SCENE...\n", argv[0]); return 2; }
    return bad ? 1 : 0;
}
