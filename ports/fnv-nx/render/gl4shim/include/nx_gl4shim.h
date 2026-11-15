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
#define NX_GL4SHIM_PROG_LIST(X) \
    X(SCALE_C2D, NX_GL4SHIM_VS, NX_GL4SHIM_COLOUR("sampler2D", "texelFetch(s, t, 0)")) \
    X(SCALE_CARR, NX_GL4SHIM_VS, NX_GL4SHIM_COLOUR("sampler2DArray", "texelFetch(s, ivec3(t, lz.y), 0)")) \
    X(SCALE_C3D, NX_GL4SHIM_VS, NX_GL4SHIM_COLOUR("sampler3D", "texelFetch(s, ivec3(t, lz.y), 0)")) \
    X(SCALE_DEPTH, NX_GL4SHIM_VS, NX_GL4SHIM_HEAD "layout(binding = 62) uniform sampler2D s;\n" \
      "void main() {\n" NX_GL4SHIM_TEX " gl_FragDepth = texelFetch(s, t, 0).r; }\n") \
    X(SCALE_STENCIL, NX_GL4SHIM_VS, NX_GL4SHIM_HEAD "layout(binding = 62) uniform usampler2D s;\n" \
      "void main() {\n" NX_GL4SHIM_TEX " if (((texelFetch(s, t, 0).r >> uint(mode)) & 1u) == 0u) discard; }\n")
enum nx_gl4shim_prog_id {
#define NX_GL4SHIM_ENUM(id, vs, fs) NX_GL4SHIM_PROG_##id,
    NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ENUM)
#undef NX_GL4SHIM_ENUM
    NX_GL4SHIM_PROG_COUNT
};
struct nx_gl4shim_prog { const char* vs; const char* fs; };
extern const struct nx_gl4shim_prog nx_gl4shim_progs[NX_GL4SHIM_PROG_COUNT];
#ifdef __cplusplus
}
#endif
#endif
