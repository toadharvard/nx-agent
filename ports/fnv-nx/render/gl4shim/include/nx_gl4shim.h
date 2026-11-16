/* The GLSL shim of the GL 4.5 backend (render/gl4shim): rewrites the translator's GLSL for the driver. Returns the length written to OUT (NUL-terminated),
 * 0 when CAP is too small. STAGE: 0 vertex, 1 fragment. */
#ifndef NX_GL4SHIM_H
#define NX_GL4SHIM_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
size_t nxgl4_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap);

/* The output buffer size that nxgl4_shim_glsl needs for an input of N bytes (the one place of this number). */
#define NX_GL4SHIM_OUT_CAP(n) ((size_t)(n) * 2 + 256)

/* The internal programs of the GL 4.5 backend: the only GLSL gl4 compiles besides the shim's output (render/gl4shim, one table).
 * GLSL 4.50 core, compiled as given; point-filtered scaling by rule (see gl4_scale.c): P_* = colour 2D / 2D array / 3D, depth, stencil. */
#define NX_GL4SHIM_VS "#version 450 core\nvoid main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); " \
                      "gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }\n"
#define NX_GL4SHIM_HEAD "#version 450 core\nlayout(location = 0) uniform ivec4 src;\nlayout(location = 1) uniform ivec4 dst;\n" \
                        "layout(location = 2) uniform ivec2 lz;\nlayout(location = 3) uniform int mode;\n" \
                        "layout(binding = 63) uniform usampler2D ramp;\n"
#define NX_GL4SHIM_TEX "ivec2 t = src.xy + ((2 * (ivec2(gl_FragCoord.xy) - dst.xy) + 1) * src.zw) / (2 * dst.zw);\n"
#define NX_GL4SHIM_GAMMA "if (mode == 1) { ivec3 i = ivec3(round(clamp(c.rgb, 0.0, 1.0) * 255.0));\n" \
                         "  c.r = float(texelFetch(ramp, ivec2(i.r, 0), 0).r >> 8) / 255.0; c.g = float(texelFetch(ramp, ivec2(i.g, 1), 0).r >> 8) / 255.0;\n" \
                         "  c.b = float(texelFetch(ramp, ivec2(i.b, 2), 0).r >> 8) / 255.0; }\n"
#define NX_GL4SHIM_COLOUR(smp, fetch) NX_GL4SHIM_HEAD "layout(binding = 62) uniform " smp " s;\n" \
    "layout(location = 0) out vec4 o;\nvoid main() {\n" NX_GL4SHIM_TEX " vec4 c = " fetch ";\n" NX_GL4SHIM_GAMMA " o = c; }\n"
/* Row kinds. P(id, vs, fs): a linked program (both stages); S(id, VS|FS, text): one stage, made per stage because D3D9 pairs a fixed-function stage with a
 * game stage. The FFP texts are generated (gen_glsl.py from the ffp directory) into gen/gl4shim_ffp.gen.h and exist only in the table's unit. */
#define NX_GL4SHIM_PROG_LIST(P, S) \
    P(SCALE_C2D, NX_GL4SHIM_VS, NX_GL4SHIM_COLOUR("sampler2D", "texelFetch(s, t, 0)")) \
    P(SCALE_CARR, NX_GL4SHIM_VS, NX_GL4SHIM_COLOUR("sampler2DArray", "texelFetch(s, ivec3(t, lz.y), 0)")) \
    P(SCALE_C3D, NX_GL4SHIM_VS, NX_GL4SHIM_COLOUR("sampler3D", "texelFetch(s, ivec3(t, lz.y), 0)")) \
    P(SCALE_DEPTH, NX_GL4SHIM_VS, NX_GL4SHIM_HEAD "layout(binding = 62) uniform sampler2D s;\n" \
      "void main() {\n" NX_GL4SHIM_TEX " gl_FragDepth = texelFetch(s, t, 0).r; }\n") \
    P(SCALE_STENCIL, NX_GL4SHIM_VS, NX_GL4SHIM_HEAD "layout(binding = 62) uniform usampler2D s;\n" \
      "void main() {\n" NX_GL4SHIM_TEX " if (((texelFetch(s, t, 0).r >> uint(mode)) & 1u) == 0u) discard; }\n") \
    S(FFP_VS_SMOOTH, VS, nx_gl4shim_ffp_vs_smooth) S(FFP_VS_FLAT, VS, nx_gl4shim_ffp_vs_flat) \
    S(FFP_PS_SMOOTH, FS, nx_gl4shim_ffp_ps_smooth) S(FFP_PS_FLAT, FS, nx_gl4shim_ffp_ps_flat)
/* From the one list: every row is its own const data symbol nx_gl4shim_<NAME> (declared here, defined in src/gl4shim_progs.c), a pair-row enum (the slot of its linked
 * program in gl4) and the reserved backend shader ids of the stage rows (NX_GL4SHIM_SHADER_<NAME>, 1..; 0 = none): the front end names a fixed-function stage shader
 * by that id and never sends GLSL. */
enum nx_gl4shim_pair_id {
#define NX_GL4SHIM_ENUM_P(id, vs, fs) NX_GL4SHIM_PAIR_##id,
#define NX_GL4SHIM_ENUM_S(id, st, text)
    NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ENUM_P, NX_GL4SHIM_ENUM_S)
#undef NX_GL4SHIM_ENUM_P
#undef NX_GL4SHIM_ENUM_S
    NX_GL4SHIM_PAIR_COUNT
};
enum nx_gl4shim_shader_id {
    NX_GL4SHIM_SHADER_NONE,
#define NX_GL4SHIM_ENUM_P(id, vs, fs)
#define NX_GL4SHIM_ENUM_S(id, st, text) NX_GL4SHIM_SHADER_##id,
    NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ENUM_P, NX_GL4SHIM_ENUM_S)
#undef NX_GL4SHIM_ENUM_P
#undef NX_GL4SHIM_ENUM_S
    NX_GL4SHIM_SHADER_COUNT
};
#define NX_GL4SHIM_STG_VS 0u   /* = NxStage_Vertex (nx/gpu_abi.h) */
#define NX_GL4SHIM_STG_FS 1u   /* = NxStage_Fragment */
struct nx_gl4shim_pair { const char* vs; const char* fs; };
struct nx_gl4shim_stage { uint32_t stage; const char* text; };   /* stage: NX_GL4SHIM_STG_*, a fact of the row */
#define NX_GL4SHIM_DECL_P(id, vs, fs) extern const struct nx_gl4shim_pair nx_gl4shim_##id;
#define NX_GL4SHIM_DECL_S(id, st, text) extern const struct nx_gl4shim_stage nx_gl4shim_##id;
NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_DECL_P, NX_GL4SHIM_DECL_S)
#undef NX_GL4SHIM_DECL_P
#undef NX_GL4SHIM_DECL_S
#ifdef __cplusplus
}
#endif
#endif
