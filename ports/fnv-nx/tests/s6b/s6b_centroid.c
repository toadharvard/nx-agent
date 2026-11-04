/* s6b_centroid - D3D9 _centroid through the translator, on handwritten bytecode tokens (no assembler, no game
 * content). Checks the GLSL (interpolateAtCentroid on the centroid inputs only, locations matching the VS outputs
 * of the same usage/index) and that uam builds a DKSH from it.
 *
 * Token reference (d3d9types.h layout): instruction = opcode | (length << 24) (SM2+); dcl = 0x1F with a usage token
 * (bit 31, usage bits 0-4, index bits 16-19; sampler: type << 27) and a destination token; register tokens: bit 31,
 * number bits 0-10, type bits 28-30 + 11-12, write mask bits 16-19 (dst), result modifier bits 20-23 (dst: 1 sat,
 * 2 pp, 4 centroid), swizzle bits 16-23 (src). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_shaders.h"

static int fails, total;
#define CHECK(c) do { total++; if (!(c)) { fails++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); } } while (0)

enum { T_TEMP = 0, T_INPUT = 1, T_CONST = 2, T_TEXTURE = 3, T_OUTPUT = 6, T_COLOROUT = 8, T_SAMPLER = 10,
       T_LOOP = 15, T_MISC = 17 };
enum { U_POSITION = 0, U_NORMAL = 3, U_TEXCOORD = 5, U_COLOR = 10 };
enum { M_SAT = 1, M_PP = 2, M_CENTROID = 4 };

#define REGT(t)          ((((uint32_t)(t) & 7) << 28) | ((((uint32_t)(t) >> 3) & 3) << 11))
#define DST(t, n, m, mod) (0x80000000u | REGT(t) | (n) | ((uint32_t)(m) << 16) | ((uint32_t)(mod) << 20))
#define SRC(t, n)        (0x80000000u | REGT(t) | (n) | (0xE4u << 16))
#define OP(op, len)      ((uint32_t)(op) | ((uint32_t)(len) << 24))
#define DCL              OP(0x1F, 2)
#define USAGE(u, i)      (0x80000000u | (u) | ((uint32_t)(i) << 16))
#define SAMP2D           (0x80000000u | (2u << 27))
#define MOV OP(0x01, 2)
#define MAD OP(0x04, 4)
#define TEXLD OP(0x42, 3)
#define END 0x0000FFFFu

static char *glsl_of(const uint32_t *t, size_t n, int *ok, char *err, size_t errlen)
{
    fnvnx_shader_opts o = { FNVNX_SHADER_KEEP_GLSL, NULL };
    fnvnx_shader_result r;
    fnvnx_shader_translate(t, n * 4, &o, &r);
    *ok = r.ok && r.dksh && fnvnx_dksh_validate(r.dksh, r.dksh_size, r.stage, NULL, 0) == 0;
    snprintf(err, errlen, "%s", r.error);
    char *g = r.glsl; r.glsl = NULL;
    fnvnx_shader_result_free(&r);
    if (!*ok) printf("  translate: %s\n", err);
    if (getenv("S6B_DUMP") && g) fputs(g, stdout);                    /* S6B_DUMP=1: print the GLSL */
    return g ? g : strdup("");
}

static int has(const char *g, const char *s) { return strstr(g, s) != NULL; }

/* location of "in/out ... io_<usage>_<index>;" in the GLSL, -1 if absent */
static int loc_of(const char *g, const char *dir, int usage, int index)
{
    char want[64]; snprintf(want, sizeof want, "%s highp vec4 io_%d_%d;", dir, usage, index);
    const char *p = strstr(g, want);
    if (!p) return -1;
    while (p > g && p[-1] != '\n') p--;
    int loc = -1;
    return sscanf(p, "layout(location = %d)", &loc) == 1 ? loc : -1;
}

/* ps_3_0, the SM3 lighting PS pattern: dcl_texcoord0_centroid v0, dcl_color0_centroid v1, dcl_normal0 v2 (plain) */
static const uint32_t ps30[] = {
    0xFFFF0300,
    DCL, USAGE(U_TEXCOORD, 0), DST(T_INPUT, 0, 0xF, M_CENTROID),
    DCL, USAGE(U_COLOR, 0),    DST(T_INPUT, 1, 0xF, M_CENTROID),
    DCL, USAGE(U_NORMAL, 0),   DST(T_INPUT, 2, 0xF, 0),
    DCL, USAGE(U_TEXCOORD, 1), DST(T_INPUT, 3, 0x3, M_CENTROID | M_PP),   /* partial mask + _pp */
    DCL, SAMP2D, DST(T_SAMPLER, 0, 0xF, 0),
    TEXLD, DST(T_TEMP, 0, 0xF, 0), SRC(T_INPUT, 0), SRC(T_SAMPLER, 0),
    MAD, DST(T_TEMP, 0, 0xF, 0), SRC(T_TEMP, 0), SRC(T_INPUT, 1), SRC(T_INPUT, 2),
    MAD, DST(T_TEMP, 0, 0xF, 0), SRC(T_TEMP, 0), SRC(T_INPUT, 3), SRC(T_TEMP, 0),
    MOV, DST(T_COLOROUT, 0, 0xF, 0), SRC(T_TEMP, 0),
    END
};

/* ps_2_0: dcl_centroid t0, dcl t1, dcl_centroid v0 (ps_2_x input dcls carry no usage) */
static const uint32_t ps20[] = {
    0xFFFF0200,
    DCL, 0x80000000u, DST(T_TEXTURE, 0, 0xF, M_CENTROID),
    DCL, 0x80000000u, DST(T_TEXTURE, 1, 0xF, 0),
    DCL, 0x80000000u, DST(T_INPUT, 0, 0xF, M_CENTROID),
    DCL, SAMP2D, DST(T_SAMPLER, 0, 0xF, 0),
    TEXLD, DST(T_TEMP, 0, 0xF, 0), SRC(T_TEXTURE, 0), SRC(T_SAMPLER, 0),
    MAD, DST(T_TEMP, 0, 0xF, 0), SRC(T_TEMP, 0), SRC(T_INPUT, 0), SRC(T_TEXTURE, 1),
    MOV, DST(T_COLOROUT, 0, 0xF, 0), SRC(T_TEMP, 0),
    END
};

/* ps_2_0 centroid on a t# that is never read: the declaration alone must still translate */
static const uint32_t ps20_unread[] = {
    0xFFFF0200,
    DCL, 0x80000000u, DST(T_TEXTURE, 2, 0x3, M_CENTROID),
    DCL, 0x80000000u, DST(T_INPUT, 1, 0xF, 0),
    MOV, DST(T_COLOROUT, 0, 0xF, 0), SRC(T_INPUT, 1),
    END
};

/* vs_3_0 feeding ps30: outputs carry no centroid (D3D9 has none on VS outputs); same usage/index -> same location */
static const uint32_t vs30[] = {
    0xFFFE0300,
    DCL, USAGE(U_POSITION, 0), DST(T_INPUT, 0, 0xF, 0),
    DCL, USAGE(U_POSITION, 0), DST(T_OUTPUT, 0, 0xF, 0),
    DCL, USAGE(U_TEXCOORD, 0), DST(T_OUTPUT, 1, 0xF, 0),
    DCL, USAGE(U_COLOR, 0),    DST(T_OUTPUT, 2, 0xF, 0),
    DCL, USAGE(U_NORMAL, 0),   DST(T_OUTPUT, 3, 0xF, 0),
    DCL, USAGE(U_TEXCOORD, 1), DST(T_OUTPUT, 4, 0x3, 0),
    MOV, DST(T_OUTPUT, 0, 0xF, 0), SRC(T_INPUT, 0),
    MOV, DST(T_OUTPUT, 1, 0xF, 0), SRC(T_INPUT, 0),
    MOV, DST(T_OUTPUT, 2, 0xF, 0), SRC(T_INPUT, 0),
    MOV, DST(T_OUTPUT, 3, 0xF, 0), SRC(T_INPUT, 0),
    MOV, DST(T_OUTPUT, 4, 0x3, 0), SRC(T_INPUT, 0),
    END
};

/* ps_3_0 centroid input read through v[aL + n] (relative input addressing): the input array copy must see the
 * centroid sample too */
static const uint32_t ps30_rel[] = {
    0xFFFF0300,
    OP(0x30, 5), DST(7 /* CONSTINT */, 0, 0xF, 0), 2, 0, 1, 0,      /* defi i0, 2, 0, 1, 0 */
    DCL, USAGE(U_TEXCOORD, 0), DST(T_INPUT, 0, 0xF, M_CENTROID),
    DCL, USAGE(U_TEXCOORD, 1), DST(T_INPUT, 1, 0xF, M_CENTROID),
    MOV, DST(T_TEMP, 0, 0xF, 0), SRC(T_CONST, 0),                   /* mov r0, c0 */
    OP(0x1B, 2), SRC(T_LOOP, 0), SRC(7, 0),                         /* loop aL, i0 */
    OP(0x02, 4), DST(T_TEMP, 0, 0xF, 0), SRC(T_TEMP, 0),            /* add r0, r0, v[aL + 0] */
        SRC(T_INPUT, 0) | 0x2000u, SRC(T_LOOP, 0) & ~(0xFFu << 16), /* relative bit 13, then the aL token */
    OP(0x1D, 0),                                                    /* endloop */
    MOV, DST(T_COLOROUT, 0, 0xF, 0), SRC(T_TEMP, 0),
    END
};

static void test_ps30(void)
{
    char err[1024]; int ok;
    char *g = glsl_of(ps30, sizeof ps30 / 4, &ok, err, sizeof err);
    CHECK(ok);
    CHECK(!has(err, "centroid unsupported"));
    CHECK(has(g, "ps_v0 = interpolateAtCentroid(io_5_0);"));
    CHECK(has(g, "ps_v1 = interpolateAtCentroid(io_10_0);"));
    CHECK(has(g, "ps_v3 = interpolateAtCentroid(io_5_1);"));
    CHECK(has(g, "#define ps_v2 io_3_0"));                 /* plain input stays a plain read */
    CHECK(!has(g, "interpolateAtCentroid(io_3_0)"));
    char *v = glsl_of(vs30, sizeof vs30 / 4, &ok, err, sizeof err);
    CHECK(ok);
    CHECK(!has(v, "centroid"));                             /* no qualifier on VS outputs */
    static const int uu[][2] = { { U_TEXCOORD, 0 }, { U_COLOR, 0 }, { U_NORMAL, 0 }, { U_TEXCOORD, 1 } };
    for (int k = 0; k < 4; k++) {
        int pi = loc_of(g, "in", uu[k][0], uu[k][1]), vo = loc_of(v, "out", uu[k][0], uu[k][1]);
        CHECK(pi >= 0 && pi == vo);
        if (pi < 0 || pi != vo) printf("  usage %d/%d: ps in %d, vs out %d\n", uu[k][0], uu[k][1], pi, vo);
    }
    free(g); free(v);
}

static void test_ps20(void)
{
    char err[1024]; int ok;
    char *g = glsl_of(ps20, sizeof ps20 / 4, &ok, err, sizeof err);
    CHECK(ok);
    CHECK(has(g, "ps_t0 = interpolateAtCentroid(io_5_0);"));
    CHECK(has(g, "#define ps_t1 io_5_1"));
    CHECK(has(g, "ps_v0 = interpolateAtCentroid(io_10_0);"));
    free(g);
    g = glsl_of(ps20_unread, sizeof ps20_unread / 4, &ok, err, sizeof err);
    CHECK(ok);
    CHECK(!has(err, "centroid unsupported"));
    free(g);
}

static void test_ps30_rel(void)
{
    char err[1024]; int ok;
    char *g = glsl_of(ps30_rel, sizeof ps30_rel / 4, &ok, err, sizeof err);
    CHECK(ok);
    CHECK(has(g, "interpolateAtCentroid(io_5_0)"));
    CHECK(has(g, "interpolateAtCentroid(io_5_1)"));
    const char *arr = strstr(g, "(ps_v0, ps_v1,"), *ic = strstr(g, "ps_v1 = interpolateAtCentroid(io_5_1);");
    CHECK(arr && ic && ic < arr);                           /* the array is built from the centroid samples */
    free(g);
}

int main(void)
{
    test_ps30();
    test_ps20();
    test_ps30_rel();
    printf("s6b_centroid: %d/%d checks passed\n", total - fails, total);
    return fails != 0;
}
