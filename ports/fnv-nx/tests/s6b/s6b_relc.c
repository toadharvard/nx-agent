/* s6b_relc - relative constant reads through a CTAB array that leave it onto a def'd register, on handwritten
 * bytecode tokens (no assembler, no game content). D3D9 (and Nine, WineD3D) load the def'd literals into the constant
 * file, so c[base + a0] outside the array returns the literal: past its end (S16 R1) or before its start with a
 * negative a0 (V6n vs_reladdr_def). The translation keeps every relative read a plain indexed UBO load and lists the
 * float defs as "// fnvnx-def" lines; the runtime writes them over UBO 0 at bind (fnvnx_dk_overlay_defs). Checked
 * here: the GLSL (plain reads, no per-read def check, the def lines) and the overlay itself, by reading c[] as the
 * translated shader would after the runtime's overlay.
 *
 * Token layout as in s6b_centroid.c; the CTAB comment is built by ctab() below (D3DXSHADER_CONSTANTTABLE: 28-byte
 * header, 20-byte constant info, 16-byte type info, strings; offsets from the byte after the 'CTAB' fourcc). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_shaders.h"
#include "fnvnx_dk_layout.h"

static int fails, total;
#define CHECK(c) do { total++; if (!(c)) { fails++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); } } while (0)

enum { T_TEMP = 0, T_INPUT = 1, T_CONST = 2, T_ADDR = 3, T_RASTOUT = 4, T_TEXCRDOUT = 6 };
#define REGT(t)           ((((uint32_t)(t) & 7) << 28) | ((((uint32_t)(t) >> 3) & 3) << 11))
#define DST(t, n, m)      (0x80000000u | REGT(t) | (n) | ((uint32_t)(m) << 16))
#define SRCS(t, n, s)     (0x80000000u | REGT(t) | (n) | ((uint32_t)(s) << 16))
#define SRC(t, n)         SRCS(t, n, 0xE4)
#define REL               0x2000u                       /* D3DSHADER_ADDRMODE_RELATIVE */
#define USAGE(u, i)       (0x80000000u | (u) | ((uint32_t)(i) << 16))
#define F(x)              fbits(x)

static uint32_t fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

typedef struct { uint32_t t[256]; int n; } prog;
static void put(prog *p, uint32_t v) { p->t[p->n++] = v; }

/* CTAB comment with one float vector array NAME at c[reg], count registers */
static void ctab(prog *p, uint32_t version, const char *name, int reg, int count)
{
    uint8_t d[128]; memset(d, 0, sizeof d);
    const char *target = (version >> 16) == 0xFFFE ? "vs" : "ps";
    uint32_t h[7] = { 28, 0, version, 1, 28, 0, 0 };
    int s = 64;                                           /* strings after header + cinfo + typeinfo */
    size_t ln = strlen(name) + 1;
    memcpy(d + s, name, ln); uint32_t name_off = (uint32_t)s; s += (int)ln;
    memcpy(d + s, target, 3); h[6] = (uint32_t)s; s += 3;
    memcpy(d + s, "s6b", 4); h[1] = (uint32_t)s; s += 4;
    memcpy(d, h, sizeof h);
    uint16_t ci[4] = { 2 /* float */, (uint16_t)reg, (uint16_t)count, 0 };
    uint32_t ti_off = 48, defv = 0;
    memcpy(d + 28, &name_off, 4); memcpy(d + 32, ci, 8); memcpy(d + 40, &ti_off, 4); memcpy(d + 44, &defv, 4);
    uint16_t ti[6] = { 1 /* vector */, 3 /* float */, 1, 4, (uint16_t)count, 0 };
    memcpy(d + 48, ti, 12);
    int words = (s + 3) / 4;
    put(p, 0xFFFE | ((uint32_t)(words + 1) << 16));       /* comment: fourcc + data */
    put(p, 0x42415443);                                   /* 'CTAB' */
    for (int i = 0; i < words; i++) { uint32_t w; memcpy(&w, d + 4 * i, 4); put(p, w); }
}

static char *glsl_of(const prog *p, int *ok)
{
    fnvnx_shader_opts o = { FNVNX_SHADER_KEEP_GLSL, NULL };
    fnvnx_shader_result r;
    fnvnx_shader_translate(p->t, (size_t)p->n * 4, &o, &r);
    *ok = r.ok && r.dksh && fnvnx_dksh_validate(r.dksh, r.dksh_size, r.stage, NULL, 0) == 0;
    if (!*ok) printf("  translate: %s\n", r.error);
    char *g = r.glsl ? r.glsl : strdup(""); r.glsl = NULL;
    if (getenv("S6B_DUMP")) fputs(g, stdout);             /* S6B_DUMP=1: print the GLSL */
    fnvnx_shader_result_free(&r);
    return g;
}

static int has(const char *g, const char *s) { return strstr(g, s) != NULL; }

/* mode 0: no def, 1: defs past the array (the S16 cases), 2: defs only below the array base (negative a0) */

/* vs_1_1 (DISTTREE pattern): Inst c9..c10, def c11 (mode 2: c5), oT0 = c[a0.x + 10] (a0 = 1 reaches c11) */
static void vs11(prog *p, int with_def)
{
    p->n = 0;
    put(p, 0xFFFE0101);
    ctab(p, 0xFFFE0101, "Inst", 9, 2);
    if (with_def) { put(p, 0x51); put(p, DST(T_CONST, with_def == 2 ? 5 : 11, 0xF)); put(p, F(1.5f)); put(p, F(-2)); put(p, F(0.25f)); put(p, F(8)); }
    put(p, 0x1F); put(p, USAGE(0, 0)); put(p, DST(T_INPUT, 0, 0xF));                      /* dcl_position v0 */
    put(p, 0x01); put(p, DST(T_ADDR, 0, 0x1)); put(p, SRCS(T_CONST, 0, 0x00));              /* mov a0.x, c0.x */
    put(p, 0x01); put(p, DST(T_RASTOUT, 0, 0xF)); put(p, SRC(T_INPUT, 0));                  /* mov oPos, v0 */
    put(p, 0x01); put(p, DST(T_TEXCRDOUT, 0, 0xF)); put(p, SRC(T_CONST, 10) | REL);         /* mov oT0, c[a0.x + 10] */
    put(p, 0x0000FFFF);
}

/* vs_2_0 (PARTICLE pattern): Inst c18..c19, def c21 and c22 (mode 2: c16, c17), reads c[a0.x + 18] and c[a0.x + 19] (a0 up to 3) */
static void vs20(prog *p, int with_def)
{
    const uint32_t a0x = 0x80000000u | REGT(T_ADDR);       /* relative token: a0, component x */
    p->n = 0;
    put(p, 0xFFFE0200);
    ctab(p, 0xFFFE0200, "Inst", 18, 2);
    if (with_def) {
        put(p, 0x05000051); put(p, DST(T_CONST, with_def == 2 ? 16 : 21, 0xF)); put(p, F(-1.55e-4f)); put(p, F(-2.17e-3f)); put(p, F(2.6e-2f)); put(p, F(0.26f));
        put(p, 0x05000051); put(p, DST(T_CONST, with_def == 2 ? 17 : 22, 0xF)); put(p, F(-0.02f)); put(p, F(-0.5f)); put(p, F(1)); put(p, F(0.5f));
    }
    put(p, 0x0200001F); put(p, USAGE(0, 0)); put(p, DST(T_INPUT, 0, 0xF));                 /* dcl_position v0 */
    put(p, 0x0200002E); put(p, DST(T_ADDR, 0, 0x1)); put(p, SRCS(T_CONST, 0, 0x00));        /* mova a0.x, c0.x */
    put(p, 0x02000001); put(p, DST(T_RASTOUT, 0, 0xF)); put(p, SRC(T_INPUT, 0));            /* mov oPos, v0 */
    put(p, 0x03000001); put(p, DST(T_TEXCRDOUT, 0, 0xF)); put(p, SRC(T_CONST, 18) | REL); put(p, a0x);  /* mov oT0, c[a0.x + 18] */
    put(p, 0x03000001); put(p, DST(T_TEXCRDOUT, 1, 0xF)); put(p, SRC(T_CONST, 19) | REL); put(p, a0x);  /* mov oT1, c[a0.x + 19] */
    put(p, 0x0000FFFF);
}

/* reg/val: the def'd register and its bits ("0x.. 0x.. 0x.. 0x.."); arr: the read; idx: the register the read hits */
static void test_def(const char *what, void (*build)(prog *, int), int mode, int reg, const char *val, const char *arr,
                     int idx)
{
    prog p; int ok; char want[128];
    int f0 = fails;
    build(&p, mode);
    char *g = glsl_of(&p, &ok);
    CHECK(ok);
    CHECK(has(g, arr));                                   /* a plain indexed UBO load */
    CHECK(!has(g, "if (i ==") && !has(g, "dk_defv") && !has(g, "dk_rela"));   /* no per-read def check */
    snprintf(want, sizeof want, FNVNX_DK_DEF_TAG "%d %s\n", reg, val); CHECK(has(g, want));
    /* the runtime's overlay: app values everywhere, then the defs; the read at idx sees the literal */
    float c[256][4], lit[4]; unsigned u[4];
    for (int r = 0; r < 256; r++) for (int k = 0; k < 4; k++) c[r][k] = 100.0f + r;
    CHECK(fnvnx_dk_overlay_defs(g, c) >= 1);
    sscanf(val, "%x %x %x %x", &u[0], &u[1], &u[2], &u[3]); memcpy(lit, u, 16);
    CHECK(!memcmp(c[reg], lit, 16) && reg == idx);
    CHECK(c[0][0] == 100.0f && c[9][0] == 109.0f && c[18][0] == 118.0f);   /* the rest keeps the app values */
    if (fails != f0) printf("  (%s, mode %d)\n", what, mode);
    free(g);
}

/* no def: no def lines, the read stays a plain UBO read */
static void test_plain(void (*build)(prog *, int), const char *arr)
{
    prog p; int ok;
    build(&p, 0);
    char *g = glsl_of(&p, &ok);
    CHECK(ok);
    CHECK(has(g, arr));
    CHECK(!has(g, FNVNX_DK_DEF_TAG));
    float c[256][4]; memset(c, 0, sizeof c);
    CHECK(fnvnx_dk_overlay_defs(g, c) == 0);
    free(g);
}

int main(void)
{
    const char *v11 = "0x3fc00000 0xc0000000 0x3e800000 0x41000000";
    const char *v20 = "0xbca3d70a 0xbf000000 0x3f800000 0x3f000000";
    const char *a11 = "vs_uniforms_vec4[(ARRAYBASE_9 + 1) + vs_a0.x]", *a20 = "vs_uniforms_vec4[ARRAYBASE_18 + vs_a0.x]";
    /* DISTTREE: c[10 + a0], a0 = 1 -> c11; PARTICLE: c[19 + a0], a0 = 3 -> c22; negative a0: c[10 + a0], a0 = -5 -> c5,
     * c[19 + a0], a0 = -2 -> c17 */
    test_def("vs_1_1", vs11, 1, 11, v11, a11, 11);
    test_def("vs_1_1", vs11, 2, 5, v11, a11, 5);
    test_def("vs_2_0", vs20, 1, 22, v20, a20, 22);
    test_def("vs_2_0", vs20, 2, 17, v20, a20, 17);
    test_plain(vs11, a11);
    test_plain(vs20, a20);
    printf("s6b_relc: %d/%d checks passed\n", total - fails, total);
    return fails != 0;
}
