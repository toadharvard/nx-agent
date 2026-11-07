/* V6n TARGET: v6n_target.h on desktop GL 4.5 core (llvmpipe, EGL surfaceless).
 *
 * The D3D9 render/sampler states go through S6n's translator (render/native/front/d3d9_state.h:
 * nx_d3d9_translate, nx_d3d9_sampler, nx_d3d9_format, nx_d3d9_default_*) and the encoded result is applied to GL
 * following the backend contract of render/native/include/nx/state.h, written by V6n from that header alone:
 *   y-down window space, origin top-left, row 0 = top; depth [0,1] (ZERO_TO_ONE). On Mesa GL the edge tie rule
 *   follows the clip-control origin (st_atom_rasterizer.c flips bottom_edge_rule for UPPER_LEFT), so both
 *   glClipControl(UPPER_LEFT) and "lower-left + flip on read-back" drop pixel centres on the image-TOP edge that
 *   D3D9's top-left rule fills. V6n renders "upside down" instead: LOWER_LEFT origin, gl_Position.y negated in the
 *   VS, viewport/scissor y used as is, GL row 0 = image row 0 (mutant upper_left_origin, trap smoke.scene);
 *   NxViewport is a +0.5-centre viewport (glViewportIndexedf); CW/CCW = on-screen winding in y-down space;
 *   "front" stencil = CW; blend factors with GL meaning; masks bit0 R .. bit3 A; flat shading = first vertex;
 *   depthBiasUnits in GL 'units', depthBiasSlope in GL 'factor'; shader bits = alpha test + fog epilogue.
 * The fixed-function vertex/pixel pipeline (pretransformed or clip-space positions, stage 0/1 combiners, specular
 * add, vertex/table fog) is V6n's model of Nine's nine_ff.c, because S6n has no FF front end yet.
 *
 * Trap mutants: V6N_MUTANT=<name> breaks exactly one rule (list: v6n_gl -m). Each has a trap scene under
 * tests/v6n/traps/ that the comparison must flag. */
#define GL_GLEXT_PROTOTYPES 1
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <GL/glext.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "v6n_target.h"
#include "d3d9_state.h"

/* ---- mutants ------------------------------------------------------------------------------------------------ */
static const char* MUTANTS[] = {
    "cull_winding",        /* NxCull CW/CCW swapped when applied */
    "zwrite_ignored",      /* depth write mask always on */
    "zfunc_equal_lequal",  /* ZFUNC EQUAL applied as LEQUAL with writes (a common GL-port hack) */
    "bothsrcalpha",        /* BOTHSRCALPHA fix-up undone: dst = the D3D DESTBLEND */
    "separate_alpha",      /* alpha factors/op = colour factors/op */
    "no_half_z",           /* clip control NEGATIVE_ONE_TO_ONE */
    "mipnone_lod",         /* MIPFILTER NONE sampled with nearest mips */
    "mirroronce",          /* MirrorOnce applied as Mirror (repeat) */
    "border_argb",         /* border colour bytes taken as ARGB */
    "blendfactor_argb",    /* blend constant channels rotated (A,R,G,B) */
    "d24_bias_scale",      /* depth bias units halved (x2^23 for D24) */
    "srgb_write_blend",    /* sRGB write: encode without blending in linear (no FRAMEBUFFER_SRGB) */
    "fog_exp_exp2",        /* table fog EXP and EXP2 swapped */
    "fog_linear_scale",    /* linear fog factor = (end - z) * scale, unclamped */
    "alpharef_256",        /* alpha ref / 256 */
    "ati2_swizzle",        /* ATI2 sampled as R,G instead of G,R */
    "a4r4g4b4_order",      /* A4R4G4B4 channels read as R,G,B,A from the low nibble up */
    "l8_swizzle",          /* L8 as R,0,0,1 */
    "a8_swizzle",          /* A8 as 1,1,1,A */
    "a8l8_swizzle",        /* A8L8 as R,G,0,1 */
    "provoking_last",      /* flat shading takes the last vertex */
    "twosided_front",      /* CCW stencil ops applied to the CW faces */
    "colormask_order",     /* colour write mask bits reversed (A,B,G,R) */
    "no_half_pixel",       /* viewport without the +0.5 */
    "clear_ignores_scissor",
    "clear_honors_mask",   /* colour clear masked by COLORWRITEENABLE */
    "x8_dst_alpha",        /* X8R8G8B8 target stored with alpha (DESTALPHA not 1) */
    "stencil_ref_unmasked",/* stencil compare without the read mask */
    "no_texture_black",    /* unbound texture samples (0,0,0,1) instead of Nine's (1,1,1,1) */
    "rhw_viewport_z",      /* pretransformed z mapped through MinZ/MaxZ */
    "fog_vertex_specular", /* vertex fog for RHW from diffuse alpha instead of specular alpha */
    "specular_alpha",      /* specular add also adds alpha */
    "upper_left_origin",   /* y-down via glClipControl(UPPER_LEFT): GL's tie rule then drops image-top edges */
    "vs_posfix_halfpixel", /* S6b VS posfix half-pixel shift applied on top of the +0.5 viewport (double offset) */
    0};
static const char* g_mut = "";
static int M(const char* n) { return g_mut[0] && !strcmp(g_mut, n); }

/* ---- GL context ------------------------------------------------------------------------------------------------ */
static EGLDisplay g_dpy; static EGLContext g_ctx; static GLuint g_prog, g_vao, g_vbo, g_white, g_black;
static GLuint compile(GLenum type, const char* src) {
    GLuint s = glCreateShader(type); glShaderSource(s, 1, &src, NULL); glCompileShader(s);
    GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[4096]; glGetShaderInfoLog(s, sizeof log, NULL, log); fprintf(stderr, "GLSL: %s\n", log); exit(3); }
    return s;
}
static const char* VS =
    "#version 450 core\n"
    "layout(location=0) in vec4 aPos; layout(location=1) in vec4 aDiff; layout(location=2) in vec4 aSpec;\n"
    "layout(location=3) in vec2 aUV;\n"
    "uniform int uRhw; uniform vec4 uVp; uniform mat4 uProj; uniform int uVFog; uniform vec3 uFogP;\n"
    "uniform int uFogEnable; uniform int uFogDiffA; uniform float uYSign;\n"
    "out vec4 vDiff; flat out vec4 vDiffF; out vec4 vSpec; flat out vec4 vSpecF; out vec2 vUV; out float vFog;\n"
    "void main() {\n"
    "  if (uRhw != 0) {\n"
    "    float w = 1.0 / aPos.w;\n"
    "    vec2 ndc = vec2((aPos.x - uVp.x) * 2.0 / uVp.z - 1.0, 1.0 - (aPos.y - uVp.y) * 2.0 / uVp.w);\n"
    "    gl_Position = vec4(ndc * w, aPos.z * w, w);\n"
    "    vFog = uFogDiffA != 0 ? aDiff.a : aSpec.a;\n"
    "  } else {\n"
    "    gl_Position = uProj * vec4(aPos.xyz, 1.0);\n"
    "    float z = abs(aPos.z);\n"            /* eye z = position (world = view = identity) */
    "    if (uVFog == 1) vFog = exp2(-(z * uFogP.z) * 1.442695);\n"
    "    else if (uVFog == 2) { float d = z * uFogP.z; vFog = exp2(-(d * d) * 1.442695); }\n"
    "    else if (uVFog == 3) vFog = clamp((uFogP.x - z) * uFogP.y, 0.0, 1.0);\n"
    "    else vFog = aSpec.a;\n"
    "  }\n"
    "  gl_Position.y *= uYSign;\n"
    "  vDiff = aDiff; vDiffF = aDiff; vSpec = aSpec; vSpecF = aSpec; vUV = aUV;\n"
    "}\n";
/* VS for programmable pixel shaders: S6b's layout contract (fnvnx_dk_varying_location): COLOR0/1 at 0/1,
 * TEXCOORD0 at 2 as (u, v, 0, 1), fog factor at 18 */
static const char* VS2 =
    "#version 450 core\n"
    "layout(location=0) in vec4 aPos; layout(location=1) in vec4 aDiff; layout(location=2) in vec4 aSpec;\n"
    "layout(location=3) in vec2 aUV;\n"
    "uniform int uRhw; uniform vec4 uVp; uniform mat4 uProj; uniform int uVFog; uniform vec3 uFogP; uniform float uYSign;\n"
    "layout(location=0) out vec4 oD0; layout(location=1) out vec4 oD1; layout(location=2) out vec4 oT0;\n"
    "layout(location=18) out vec4 oFog;\n"
    "void main() {\n"
    "  float f;\n"
    "  if (uRhw != 0) {\n"
    "    float w = 1.0 / aPos.w;\n"
    "    vec2 ndc = vec2((aPos.x - uVp.x) * 2.0 / uVp.z - 1.0, 1.0 - (aPos.y - uVp.y) * 2.0 / uVp.w);\n"
    "    gl_Position = vec4(ndc * w, aPos.z * w, w); f = aSpec.a;\n"
    "  } else {\n"
    "    gl_Position = uProj * vec4(aPos.xyz, 1.0); float z = abs(aPos.z);\n"
    "    if (uVFog == 1) f = exp2(-(z * uFogP.z) * 1.442695);\n"
    "    else if (uVFog == 2) { float d = z * uFogP.z; f = exp2(-(d * d) * 1.442695); }\n"
    "    else if (uVFog == 3) f = clamp((uFogP.x - z) * uFogP.y, 0.0, 1.0); else f = aSpec.a;\n"
    "  }\n"
    "  gl_Position.y *= uYSign;\n"
    "  oD0 = aDiff; oD1 = aSpec; oT0 = vec4(aUV, 0.0, 1.0); oFog = vec4(f);\n"
    "}\n";
static const char* FS =
    "#version 450 core\n"
    "in vec4 vDiff; flat in vec4 vDiffF; in vec4 vSpec; flat in vec4 vSpecF; in vec2 vUV; in float vFog;\n"
    "layout(location=0) out vec4 oCol;\n"
    "uniform sampler2D uTex0; uniform sampler2D uTex1; uniform int uFlat; uniform ivec2 uTci;\n"
    "uniform ivec4 uStage[2]; uniform ivec4 uStageB[2];\n"   /* colorop, carg1, carg2, carg0 | alphaop, aarg1, aarg2, aarg0 */
    "uniform vec4 uTFactor; uniform vec4 uConst[2]; uniform int uSpecular; uniform int uSpecA;\n"
    "uniform int uFogMode; uniform vec4 uFogColor; uniform vec3 uFogP; uniform int uFogW; uniform int uFogLinNoClamp;\n"
    "uniform int uAlphaTest; uniform int uAlphaFunc; uniform float uAlphaRef;\n"
    "vec4 D, S, T[2], CUR, TMP;\n"
    "vec4 arg(int a, int s) {\n"
    "  int src = a & 15; vec4 v;\n"
    "  if (src == 0) v = D; else if (src == 1) v = CUR; else if (src == 2) v = T[s]; else if (src == 3) v = uTFactor;\n"
    "  else if (src == 4) v = S; else if (src == 5) v = TMP; else v = uConst[s];\n"
    "  if ((a & 0x10) != 0) v = 1.0 - v;\n"
    "  if ((a & 0x20) != 0) v = v.aaaa;\n"
    "  return v;\n"
    "}\n"
    "vec4 op(int o, vec4 a1, vec4 a2, vec4 a0, int s) {\n"
    "  if (o == 2) return a1; if (o == 3) return a2; if (o == 4) return a1 * a2; if (o == 5) return 2.0 * a1 * a2;\n"
    "  if (o == 6) return 4.0 * a1 * a2; if (o == 7) return a1 + a2; if (o == 8) return a1 + a2 - 0.5;\n"
    "  if (o == 9) return 2.0 * (a1 + a2 - 0.5); if (o == 10) return a1 - a2; if (o == 11) return a1 + a2 - a1 * a2;\n"
    "  if (o == 12) return mix(a2, a1, D.a); if (o == 13) return mix(a2, a1, T[s].a);\n"
    "  if (o == 14) return mix(a2, a1, uTFactor.a); if (o == 16) return mix(a2, a1, CUR.a);\n"
    "  if (o == 24) return vec4(clamp(4.0 * dot(a1.rgb - 0.5, a2.rgb - 0.5), 0.0, 1.0));\n"
    "  if (o == 25) return a0 + a1 * a2; if (o == 26) return mix(a2, a1, a0);\n"
    "  return a1;\n"
    "}\n"
    /* nine_ff.c ps_do_ts_op: saturate except SELECTARG1/2, MODULATE, PREMODULATE, BLEND*ALPHA, BUMPENVMAP*, LERP */
    "vec4 sat(int o, vec4 v) {\n"
    "  if (o == 2 || o == 3 || o == 4 || o == 17 || o == 12 || o == 13 || o == 14 || o == 16 || o == 22 || o == 23 || o == 26) return v;\n"
    "  return clamp(v, 0.0, 1.0);\n"
    "}\n"
    "bool cmpf(int f, float a, float r) {\n"
    "  if (f == 0) return false; if (f == 1) return a < r; if (f == 2) return a == r; if (f == 3) return a <= r;\n"
    "  if (f == 4) return a > r; if (f == 5) return a != r; if (f == 6) return a >= r; return true;\n"
    "}\n"
    "void main() {\n"
    "  D = uFlat != 0 ? vDiffF : vDiff; S = uFlat != 0 ? vSpecF : vSpec;\n"
    "  T[0] = texture(uTex0, uTci.x == 0 ? vUV : vec2(0.0)); T[1] = texture(uTex1, uTci.y == 0 ? vUV : vec2(0.0));\n"
    "  CUR = D; TMP = vec4(0.0);\n"
    "  for (int s = 0; s < 2; s++) {\n"
    "    if (uStage[s].x == 1) break;\n"
    "    vec3 c = sat(uStage[s].x, op(uStage[s].x, arg(uStage[s].y, s), arg(uStage[s].z, s), arg(uStage[s].w, s), s)).rgb;\n"
    "    float a = uStage[s].x == 24 ? c.r : uStageB[s].x == 1 ? CUR.a :\n"
    "      sat(uStageB[s].x, op(uStageB[s].x, arg(uStageB[s].y, s), arg(uStageB[s].z, s), arg(uStageB[s].w, s), s)).a;\n"
    "    CUR = vec4(c, a);\n"
    "  }\n"
    "  if (uSpecular != 0) { CUR.rgb += S.rgb; if (uSpecA != 0) CUR.a += S.a; }\n"
    "  if (uFogMode != 0) {\n"
    "    float f;\n"
    "    if (uFogMode == 4) f = vFog;\n"
    "    else {\n"
    "      float z = uFogW != 0 ? 1.0 / gl_FragCoord.w : gl_FragCoord.z;\n"
    "      if (uFogMode == 1) f = uFogLinNoClamp != 0 ? (uFogP.x - z) * uFogP.y : clamp((uFogP.x - z) * uFogP.y, 0.0, 1.0);\n"
    "      else if (uFogMode == 2) f = exp2(-(z * uFogP.z) * 1.442695);\n"
    "      else { float d = z * uFogP.z; f = exp2(-(d * d) * 1.442695); }\n"
    "    }\n"
    "    CUR.rgb = mix(uFogColor.rgb, CUR.rgb, f);\n"
    "  }\n"
    "  if (uAlphaTest != 0 && !cmpf(uAlphaFunc, CUR.a, uAlphaRef)) discard;\n"
    "  oCol = CUR;\n"
    "}\n";

static int gl_init(char* err, int errlen) {
    if (g_ctx) return 1;
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    g_dpy = gpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    EGLint ma, mi;
    if (!eglInitialize(g_dpy, &ma, &mi)) { snprintf(err, errlen, "eglInitialize"); return 0; }
    eglBindAPI(EGL_OPENGL_API);
    EGLint ca[] = {EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 5, EGL_CONTEXT_OPENGL_PROFILE_MASK,
                   EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
    g_ctx = eglCreateContext(g_dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (!g_ctx || !eglMakeCurrent(g_dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, g_ctx)) { snprintf(err, errlen, "GL 4.5 context"); return 0; }
    GLuint vs = compile(GL_VERTEX_SHADER, VS), fs = compile(GL_FRAGMENT_SHADER, FS);
    g_prog = glCreateProgram(); glAttachShader(g_prog, vs); glAttachShader(g_prog, fs); glLinkProgram(g_prog);
    GLint ok; glGetProgramiv(g_prog, GL_LINK_STATUS, &ok);
    if (!ok) { snprintf(err, errlen, "link"); return 0; }
    glGenVertexArrays(1, &g_vao); glGenBuffers(1, &g_vbo);
    glBindVertexArray(g_vao); glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, sizeof(V6nVertex), (void*)0);
    glVertexAttribPointer(1, GL_BGRA, GL_UNSIGNED_BYTE, GL_TRUE, sizeof(V6nVertex), (void*)16);   /* D3DCOLOR */
    glVertexAttribPointer(2, GL_BGRA, GL_UNSIGNED_BYTE, GL_TRUE, sizeof(V6nVertex), (void*)20);
    glVertexAttribPointer(3, 2, GL_FLOAT, GL_FALSE, sizeof(V6nVertex), (void*)24);
    for (int i = 0; i < 4; i++) glEnableVertexAttribArray(i);
    uint8_t black[4] = {0, 0, 0, 255}, white[4] = {255, 255, 255, 255};
    glGenTextures(1, &g_black); glBindTexture(GL_TEXTURE_2D, g_black);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, black);
    glGenTextures(1, &g_white); glBindTexture(GL_TEXTURE_2D, g_white);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, white);
    return 1;
}

/* ---- target state --------------------------------------------------------------------------------------------------- */
typedef struct { GLuint tex; uint32_t fmt; NxFormatInfo fi; GLenum ifmt, ifmt_srgb; GLuint view_srgb; int levels; GLenum target; } Tex;
typedef struct { GLuint prog; uint32_t hash; } PsProg;
static PsProg g_ps[64]; static int g_nps; static GLuint g_vs2, g_ubo[2];
static uint32_t fnv1a(const char* s) { uint32_t h = 2166136261u; while (*s) h = (h ^ (uint8_t)*s++) * 16777619u; return h; }
struct V6nTarget {
    int w, h; uint32_t rtf, dsf;
    GLuint fbo, rt, rt_srgb, ds; GLenum rt_ifmt; int rt_has_srgb;
    uint32_t rs[NXD3D_RS_COUNT];
    uint32_t samp[8][NXD3D_SAMP_COUNT];   /* 8 sampler stages for pixel shaders; the FF model uses 2 */
    uint32_t tss[2][33];
    Tex tex[8]; int has_tex[8];
    GLuint sampler[8];
    NxD3D9Viewport vp; NxD3D9Rect sc;
    float proj[16];
    V6nReadback rb;
    GLuint ps; uint32_t ps_version; float psc[256][4];
    GLuint vs; float vsc[256][4];          /* S6b VS (translated GLSL, UBO bindings moved to 8 + n) */
    const char* ps_glsl; const char* vs_glsl;
};
static GLuint g_vao2, g_ubo2[2];
typedef struct { GLuint prog; uint32_t vh, ph; } VsProg;
static VsProg g_vsp[64]; static int g_nvsp;
static char g_vs_text[64][1 << 16]; static int g_nvs_text;
/* VS UBO bindings move to 8 + n so they do not collide with the PS's 0 / 2 in GL's shared binding space */
static char* rebind_vs(const char* src) {
    static char out[1 << 17]; size_t o = 0;
    for (const char* p = src; *p && o < sizeof out - 16; ) {
        if (!strncmp(p, "binding = ", 10)) { int n = atoi(p + 10); o += snprintf(out + o, sizeof out - o, "binding = %d", 8 + n); p += 10; while (*p >= '0' && *p <= '9') p++; }
        else out[o++] = *p++;
    }
    out[o] = 0;
    return out;
}

/* Add to a VS every output location the PS reads and the VS does not write, written as vec4(0) (Nine's value for a
 * texture coordinate set an RHW / FF vertex does not carry, and for D3D9's unwritten varyings; RENDER_VERIFY §7 V1) */
static void stub_vs(const char* vsrc, const char* ps, char* vfull, size_t cap) {
    char decl[4096] = "", init[4096] = "";
    for (const char* q = ps; (q = strstr(q, "layout(location = ")); q++) {
        int loc = atoi(q + 18); const char* e = strchr(q, ';'); if (!e) break;
        if (!strstr(q, ") in ") || strstr(q, ") in ") > e) continue;
        char pat[64], pat2[64]; snprintf(pat, sizeof pat, "layout(location = %d) out", loc); snprintf(pat2, sizeof pat2, "layout(location=%d) out", loc);
        if (strstr(vsrc, pat) || strstr(vsrc, pat2)) continue;
        snprintf(decl + strlen(decl), sizeof decl - strlen(decl), "layout(location = %d) out highp vec4 v6n_stub_%d;\n", loc, loc);
        snprintf(init + strlen(init), sizeof init - strlen(init), "v6n_stub_%d = vec4(0.0);\n", loc);
    }
    const char* mainp = strstr(vsrc, "void main()");
    const char* brace = mainp ? strchr(mainp, '{') : NULL;
    const char* nl = strchr(vsrc, '\n');   /* after the #version line */
    if (brace && nl && nl < brace)
        snprintf(vfull, cap, "%.*s%s%.*s%s%s", (int)(nl - vsrc + 1), vsrc, decl, (int)(brace - nl), nl + 1, init, brace + 1);
    else snprintf(vfull, cap, "%s", vsrc);
}
static GLenum gl_cmp(uint32_t c) { return GL_NEVER + c; }   /* NxCompare = GL order */
static GLenum gl_sop(uint32_t o) {
    static const GLenum t[8] = {GL_KEEP, GL_ZERO, GL_REPLACE, GL_INCR, GL_DECR, GL_INVERT, GL_INCR_WRAP, GL_DECR_WRAP};
    return t[o & 7];
}
static GLenum gl_bf(uint32_t f) {
    static const GLenum t[NxBf_Count] = {GL_ZERO, GL_ONE, GL_SRC_COLOR, GL_ONE_MINUS_SRC_COLOR, GL_SRC_ALPHA,
        GL_ONE_MINUS_SRC_ALPHA, GL_DST_ALPHA, GL_ONE_MINUS_DST_ALPHA, GL_DST_COLOR, GL_ONE_MINUS_DST_COLOR,
        GL_SRC_ALPHA_SATURATE, GL_CONSTANT_COLOR, GL_ONE_MINUS_CONSTANT_COLOR, GL_SRC1_COLOR, GL_ONE_MINUS_SRC1_COLOR};
    return f < NxBf_Count ? t[f] : GL_ZERO;
}
static GLenum gl_bop(uint32_t o) {
    static const GLenum t[5] = {GL_FUNC_ADD, GL_FUNC_SUBTRACT, GL_FUNC_REVERSE_SUBTRACT, GL_MIN, GL_MAX};
    return o < 5 ? t[o] : GL_FUNC_ADD;
}
static GLenum gl_wrap(uint32_t w) {
    static const GLenum t[5] = {GL_REPEAT, GL_MIRRORED_REPEAT, GL_CLAMP_TO_EDGE, GL_CLAMP_TO_BORDER, GL_MIRROR_CLAMP_TO_EDGE};
    if (M("mirroronce") && w == NxWrap_MirrorOnce) return GL_MIRRORED_REPEAT;
    return w < 5 ? t[w] : GL_CLAMP_TO_EDGE;
}
static GLenum gl_swz(uint8_t s) {
    static const GLenum t[6] = {GL_RED, GL_GREEN, GL_BLUE, GL_ALPHA, GL_ZERO, GL_ONE};
    return s < 6 ? t[s] : GL_RED;
}

/* NxFormat -> GL internal format / upload format / type (V6n's reading of the contract) */
typedef struct { GLenum ifmt, srgb, fmt, type; int compressed; } GlFmt;
static GlFmt gl_fmt(int nx) {
    GlFmt g = {0, 0, 0, 0, 0};
    switch (nx) {
    case NxFmt_BGRA8: case NxFmt_BGRX8: g = (GlFmt){GL_RGBA8, GL_SRGB8_ALPHA8, GL_BGRA, GL_UNSIGNED_BYTE, 0}; break;
    case NxFmt_RGBA8: case NxFmt_RGBX8: g = (GlFmt){GL_RGBA8, GL_SRGB8_ALPHA8, GL_RGBA, GL_UNSIGNED_BYTE, 0}; break;
    case NxFmt_R5G6B5: g = (GlFmt){GL_RGB565, 0, GL_RGB, GL_UNSIGNED_SHORT_5_6_5, 0}; break;
    case NxFmt_A1R5G5B5: case NxFmt_X1R5G5B5: g = (GlFmt){GL_RGB5_A1, 0, GL_BGRA, GL_UNSIGNED_SHORT_1_5_5_5_REV, 0}; break;
    case NxFmt_A4R4G4B4: g = (GlFmt){GL_RGBA4, 0, GL_BGRA, GL_UNSIGNED_SHORT_4_4_4_4_REV, 0}; break;
    case NxFmt_RGB10A2: g = (GlFmt){GL_RGB10_A2, 0, GL_RGBA, GL_UNSIGNED_INT_2_10_10_10_REV, 0}; break;
    case NxFmt_R8: g = (GlFmt){GL_R8, 0, GL_RED, GL_UNSIGNED_BYTE, 0}; break;
    case NxFmt_RG8: g = (GlFmt){GL_RG8, 0, GL_RG, GL_UNSIGNED_BYTE, 0}; break;
    case NxFmt_R16: g = (GlFmt){GL_R16, 0, GL_RED, GL_UNSIGNED_SHORT, 0}; break;
    case NxFmt_RG16: g = (GlFmt){GL_RG16, 0, GL_RG, GL_UNSIGNED_SHORT, 0}; break;
    case NxFmt_RGBA16: g = (GlFmt){GL_RGBA16, 0, GL_RGBA, GL_UNSIGNED_SHORT, 0}; break;
    case NxFmt_RG8S: g = (GlFmt){GL_RG8_SNORM, 0, GL_RG, GL_BYTE, 0}; break;
    case NxFmt_RGBA8S: g = (GlFmt){GL_RGBA8_SNORM, 0, GL_RGBA, GL_BYTE, 0}; break;
    case NxFmt_RG16S: g = (GlFmt){GL_RG16_SNORM, 0, GL_RG, GL_SHORT, 0}; break;
    case NxFmt_RGBA16S: g = (GlFmt){GL_RGBA16_SNORM, 0, GL_RGBA, GL_SHORT, 0}; break;
    case NxFmt_R16F: g = (GlFmt){GL_R16F, 0, GL_RED, GL_HALF_FLOAT, 0}; break;
    case NxFmt_RG16F: g = (GlFmt){GL_RG16F, 0, GL_RG, GL_HALF_FLOAT, 0}; break;
    case NxFmt_RGBA16F: g = (GlFmt){GL_RGBA16F, 0, GL_RGBA, GL_HALF_FLOAT, 0}; break;
    case NxFmt_R32F: g = (GlFmt){GL_R32F, 0, GL_RED, GL_FLOAT, 0}; break;
    case NxFmt_RG32F: g = (GlFmt){GL_RG32F, 0, GL_RG, GL_FLOAT, 0}; break;
    case NxFmt_RGBA32F: g = (GlFmt){GL_RGBA32F, 0, GL_RGBA, GL_FLOAT, 0}; break;
    case NxFmt_BC1: g = (GlFmt){GL_COMPRESSED_RGBA_S3TC_DXT1_EXT, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT, 0, 0, 1}; break;
    case NxFmt_BC2: g = (GlFmt){GL_COMPRESSED_RGBA_S3TC_DXT3_EXT, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT, 0, 0, 1}; break;
    case NxFmt_BC3: g = (GlFmt){GL_COMPRESSED_RGBA_S3TC_DXT5_EXT, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT, 0, 0, 1}; break;
    case NxFmt_BC4: g = (GlFmt){GL_COMPRESSED_RED_RGTC1, 0, 0, 0, 1}; break;
    case NxFmt_BC5: g = (GlFmt){GL_COMPRESSED_RG_RGTC2, 0, 0, 0, 1}; break;
    }
    return g;
}

static V6nTarget* g_begin(int w, int h, uint32_t rtf, uint32_t dsf, char* err, int errlen) {
    const char* m = getenv("V6N_MUTANT"); g_mut = m ? m : "";
    if (!gl_init(err, errlen)) return NULL;
    V6nTarget* t = calloc(1, sizeof *t);
    t->w = w; t->h = h; t->rtf = rtf; t->dsf = dsf;
    nx_d3d9_default_rs(t->rs);
    for (int s = 0; s < 2; s++) {
        nx_d3d9_default_samp(t->samp[s]);
        t->tss[s][1] = s == 0 ? 4 : 1; t->tss[s][2] = 2; t->tss[s][3] = 1;      /* COLOROP MODULATE / DISABLE */
        t->tss[s][4] = s == 0 ? 2 : 1; t->tss[s][5] = 2; t->tss[s][6] = 1;      /* ALPHAOP SELECTARG1 / DISABLE */
        t->tss[s][26] = 1; t->tss[s][27] = 1; t->tss[s][11] = s;
    }
    for (int s = 0; s < 8; s++) { if (s >= 2) nx_d3d9_default_samp(t->samp[s]); glGenSamplers(1, &t->sampler[s]); }
    t->vp = (NxD3D9Viewport){0, 0, (uint32_t)w, (uint32_t)h, 0.0f, 1.0f};
    t->sc = (NxD3D9Rect){0, 0, w, h};
    for (int i = 0; i < 16; i++) t->proj[i] = (i % 5 == 0) ? 1.0f : 0.0f;
    NxFormatInfo fi = nx_d3d9_format(rtf);
    if (!fi.format || !(fi.flags & NXFMT_RENDER)) { snprintf(err, errlen, "rt format %u: no NXFMT_RENDER format", rtf); free(t); return NULL; }
    GlFmt g = gl_fmt(fi.format);
    t->rt_ifmt = g.ifmt;
    if ((fi.format == NxFmt_BGRX8 || fi.format == NxFmt_RGBX8) && !M("x8_dst_alpha")) { t->rt_ifmt = GL_RGB8; g.srgb = GL_SRGB8; }
    glGenTextures(1, &t->rt); glBindTexture(GL_TEXTURE_2D, t->rt);
    glTexStorage2D(GL_TEXTURE_2D, 1, t->rt_ifmt, w, h);
    if (g.srgb) { glGenTextures(1, &t->rt_srgb); glTextureView(t->rt_srgb, GL_TEXTURE_2D, t->rt, g.srgb, 0, 1, 0, 1); t->rt_has_srgb = 1; }
    glGenFramebuffers(1, &t->fbo); glBindFramebuffer(GL_FRAMEBUFFER, t->fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, t->rt, 0);
    if (dsf) {
        NxFormatInfo di = nx_d3d9_format(dsf);
        GLenum dif = di.format == NxFmt_Z24S8 ? GL_DEPTH24_STENCIL8 : di.format == NxFmt_Z24X8 ? GL_DEPTH_COMPONENT24 :
                     di.format == NxFmt_Z16 ? GL_DEPTH_COMPONENT16 : di.format == NxFmt_Z32F ? GL_DEPTH_COMPONENT32F :
                     di.format == NxFmt_Z32FS8 ? GL_DEPTH32F_STENCIL8 : 0;
        if (!dif) { snprintf(err, errlen, "ds format %u unsupported (NxFormat %d)", dsf, di.format); free(t); return NULL; }
        glGenRenderbuffers(1, &t->ds); glBindRenderbuffer(GL_RENDERBUFFER, t->ds);
        glRenderbufferStorage(GL_RENDERBUFFER, dif, w, h);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, (di.flags & NXFMT_STENCIL) ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, t->ds);
    }
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { snprintf(err, errlen, "fbo incomplete"); free(t); return NULL; }
    glClipControl(M("upper_left_origin") ? GL_UPPER_LEFT : GL_LOWER_LEFT, M("no_half_z") ? GL_NEGATIVE_ONE_TO_ONE : GL_ZERO_TO_ONE);
    glUseProgram(g_prog); glUniform1f(glGetUniformLocation(g_prog, "uYSign"), M("upper_left_origin") ? 1.0f : -1.0f);
    return t;
}
static void g_end(V6nTarget* t) {
    for (int s = 0; s < 8; s++) { if (t->has_tex[s]) { glDeleteTextures(1, &t->tex[s].tex); if (t->tex[s].view_srgb) glDeleteTextures(1, &t->tex[s].view_srgb); } glDeleteSamplers(1, &t->sampler[s]); }
    glDeleteFramebuffers(1, &t->fbo); glDeleteTextures(1, &t->rt); if (t->rt_srgb) glDeleteTextures(1, &t->rt_srgb);
    if (t->ds) glDeleteRenderbuffers(1, &t->ds);
    free(t->rb.color); free(t->rb.depth); free(t->rb.stencil); free(t);
}

static void bind_rt(V6nTarget* t, int srgb) {
    glBindFramebuffer(GL_FRAMEBUFFER, t->fbo);
    int use = srgb && t->rt_has_srgb && !M("srgb_write_blend");
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, use ? t->rt_srgb : t->rt, 0);
    if (use) glEnable(GL_FRAMEBUFFER_SRGB); else glDisable(GL_FRAMEBUFFER_SRGB);
}
static void g_clear(V6nTarget* t, uint32_t flags, uint32_t color, float z, uint32_t s) {
    /* D3D9 (Nine device9.c user_assert): ZBUFFER without a depth surface, or STENCIL without a stencil format,
     * make the whole Clear D3DERR_INVALIDCALL: nothing is cleared */
    if ((flags & 2) && !t->dsf) return;
    if ((flags & 4) && !(t->dsf && (nx_d3d9_format(t->dsf).flags & NXFMT_STENCIL))) return;
    /* Nine's clear: rect = viewport (intersected with the scissor when enabled); masks and write enables ignored;
     * the colour goes through the sRGB view when SRGBWRITEENABLE. */
    bind_rt(t, t->rs[194] != 0);
    if (M("srgb_write_blend") && t->rs[194] && t->rt_has_srgb) {   /* mutant: encode by hand, no linear blend */
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, t->rt_srgb, 0);
        glEnable(GL_FRAMEBUFFER_SRGB);
    }
    int x1 = t->vp.X, y1 = t->vp.Y, x2 = t->vp.X + t->vp.Width, y2 = t->vp.Y + t->vp.Height;
    if (t->rs[174] && !M("clear_ignores_scissor")) {
        if (t->sc.left > x1) x1 = t->sc.left; if (t->sc.top > y1) y1 = t->sc.top;
        if (t->sc.right < x2) x2 = t->sc.right; if (t->sc.bottom < y2) y2 = t->sc.bottom;
    }
    if (x2 <= x1 || y2 <= y1) return;
    glEnable(GL_SCISSOR_TEST); glScissor(x1, y1, x2 - x1, y2 - y1);
    GLbitfield b = 0;
    if (flags & 1) {
        float c[4]; nx_d3d9_color(color, c);
        uint32_t m = M("clear_honors_mask") ? t->rs[168] & 15 : 15;
        glColorMaski(0, m & 1, (m >> 1) & 1, (m >> 2) & 1, (m >> 3) & 1);
        glClearColor(c[0], c[1], c[2], c[3]); b |= GL_COLOR_BUFFER_BIT;
    }
    if ((flags & 2) && t->ds) { glDepthMask(GL_TRUE); glClearDepth(z); b |= GL_DEPTH_BUFFER_BIT; }
    if ((flags & 4) && t->ds) { glStencilMask(0xFF); glClearStencil((GLint)(s & 0xFF)); b |= GL_STENCIL_BUFFER_BIT; }
    glClear(b);
    glDisable(GL_SCISSOR_TEST);
}
static void g_rs(V6nTarget* t, uint32_t st, uint32_t v) { if (st < NXD3D_RS_COUNT) t->rs[st] = v; }
static void g_samp(V6nTarget* t, uint32_t s, uint32_t ty, uint32_t v) { if (s < 8 && ty < NXD3D_SAMP_COUNT) t->samp[s][ty] = v; }
static void g_tss(V6nTarget* t, uint32_t s, uint32_t ty, uint32_t v) { if (s < 2 && ty < 33) t->tss[s][ty] = v; }
int v6n_level_bytes(uint32_t fmt, int w, int h);

static int g_tex(V6nTarget* t, uint32_t stage, uint32_t fmt, int w, int h, int levels, const uint8_t* const* data) {
    if (stage > 7) return 0;
    Tex* x = &t->tex[stage];
    if (t->has_tex[stage]) { glDeleteTextures(1, &x->tex); if (x->view_srgb) glDeleteTextures(1, &x->view_srgb); }
    memset(x, 0, sizeof *x);
    x->fmt = fmt; x->fi = nx_d3d9_format(fmt); x->levels = levels;
    if (!x->fi.format) return 0;
    if (M("ati2_swizzle") && x->fi.format == NxFmt_BC5) { x->fi.swizzle[0] = NxSwz_R; x->fi.swizzle[1] = NxSwz_G; }
    if (M("l8_swizzle") && fmt == 50) { x->fi.swizzle[1] = NxSwz_0; x->fi.swizzle[2] = NxSwz_0; }
    if (M("a8_swizzle") && fmt == 28) { x->fi.swizzle[0] = x->fi.swizzle[1] = x->fi.swizzle[2] = NxSwz_1; }
    if (M("a8l8_swizzle") && fmt == 51) { x->fi.swizzle[1] = NxSwz_G; x->fi.swizzle[2] = NxSwz_0; x->fi.swizzle[3] = NxSwz_1; }
    /* contract (nx/state.h NXFMT_CONVERT): such texels go through nx_d3d9_convert() before the upload */
    const uint8_t* lev[16]; uint8_t* conv = NULL;
    for (int l = 0; l < levels && l < 16; l++) lev[l] = data[l];
    if (x->fi.flags & NXFMT_CONVERT) {
        uint32_t srcn = 0;
        for (int l = 0; l < levels; l++) srcn += v6n_level_bytes(fmt, w >> l > 0 ? w >> l : 1, h >> l > 0 ? h >> l : 1);
        uint8_t* src = malloc(srcn), *p = src;
        for (int l = 0; l < levels; l++) { int n = v6n_level_bytes(fmt, w >> l > 0 ? w >> l : 1, h >> l > 0 ? h >> l : 1); memcpy(p, data[l], n); p += n; }
        uint32_t cap = nx_d3d9_converted_size(fmt, w, h, levels, 1);
        conv = malloc(cap ? cap : 1);
        uint32_t got = nx_d3d9_convert(fmt, w, h, levels, 1, src, srcn, conv, cap);
        free(src);
        if (!got) { free(conv); return 0; }
        uint32_t bpp = got / 1;  (void)bpp;
        uint8_t* q = conv;   /* converted levels are tightly packed in the native format */
        int nbpp = x->fi.bytes;
        for (int l = 0; l < levels; l++) { lev[l] = q; q += (size_t)(w >> l > 0 ? w >> l : 1) * (h >> l > 0 ? h >> l : 1) * nbpp; }
    }
    GlFmt g = gl_fmt(x->fi.format);
    if (M("a4r4g4b4_order") && x->fi.format == NxFmt_A4R4G4B4) g.fmt = GL_RGBA, g.type = GL_UNSIGNED_SHORT_4_4_4_4_REV;
    if (!g.ifmt) return 0;
    x->ifmt = g.ifmt; x->ifmt_srgb = (x->fi.flags & NXFMT_SRGB) ? g.srgb : 0;
    glGenTextures(1, &x->tex); glBindTexture(GL_TEXTURE_2D, x->tex);
    glTexStorage2D(GL_TEXTURE_2D, levels, g.ifmt, w, h);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    for (int l = 0; l < levels; l++) {
        int lw = w >> l > 0 ? w >> l : 1, lh = h >> l > 0 ? h >> l : 1;
        if (g.compressed) glCompressedTexSubImage2D(GL_TEXTURE_2D, l, 0, 0, lw, lh, g.ifmt, v6n_level_bytes(fmt, lw, lh), lev[l]);
        else glTexSubImage2D(GL_TEXTURE_2D, l, 0, 0, lw, lh, g.fmt, g.type, lev[l]);
    }
    free(conv);
    GLint sw[4]; for (int i = 0; i < 4; i++) sw[i] = gl_swz(x->fi.swizzle[i]);
    glTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_SWIZZLE_RGBA, sw);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, levels - 1);
    if (x->ifmt_srgb) {
        glGenTextures(1, &x->view_srgb); glTextureView(x->view_srgb, GL_TEXTURE_2D, x->tex, x->ifmt_srgb, 0, levels, 0, 1);
        glBindTexture(GL_TEXTURE_2D, x->view_srgb);
        glTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_SWIZZLE_RGBA, sw);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, levels - 1);
    }
    x->target = GL_TEXTURE_2D;
    t->has_tex[stage] = 1;
    return glGetError() == GL_NO_ERROR;
}
/* cube texture: plain formats only (no NXFMT_CONVERT, no sRGB view). D3D9 face order and texel layout equal GL's
 * (GL_TEXTURE_CUBE_MAP_POSITIVE_X + f, row 0 first), as wined3d uploads them. */
static int g_texcube(V6nTarget* t, uint32_t stage, uint32_t fmt, int size, const uint8_t* const* faces) {
    if (stage > 7) return 0;
    Tex* x = &t->tex[stage];
    if (t->has_tex[stage]) { glDeleteTextures(1, &x->tex); if (x->view_srgb) glDeleteTextures(1, &x->view_srgb); }
    memset(x, 0, sizeof *x); t->has_tex[stage] = 0;
    x->fmt = fmt; x->fi = nx_d3d9_format(fmt); x->levels = 1;
    if (!x->fi.format || (x->fi.flags & NXFMT_CONVERT)) return 0;
    GlFmt g = gl_fmt(x->fi.format);
    if (!g.ifmt || g.compressed) return 0;
    x->ifmt = g.ifmt;
    glGenTextures(1, &x->tex); glBindTexture(GL_TEXTURE_CUBE_MAP, x->tex);
    glTexStorage2D(GL_TEXTURE_CUBE_MAP, 1, g.ifmt, size, size);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    for (int f = 0; f < 6; f++) glTexSubImage2D(GL_TEXTURE_CUBE_MAP_POSITIVE_X + f, 0, 0, 0, size, size, g.fmt, g.type, faces[f]);
    GLint sw[4]; for (int i = 0; i < 4; i++) sw[i] = gl_swz(x->fi.swizzle[i]);
    glTexParameteriv(GL_TEXTURE_CUBE_MAP, GL_TEXTURE_SWIZZLE_RGBA, sw);
    glTexParameteri(GL_TEXTURE_CUBE_MAP, GL_TEXTURE_MAX_LEVEL, 0);
    x->target = GL_TEXTURE_CUBE_MAP;
    t->has_tex[stage] = 1;
    return glGetError() == GL_NO_ERROR;
}
static void g_notex(V6nTarget* t, uint32_t stage) {
    if (stage > 7 || !t->has_tex[stage]) return;
    glDeleteTextures(1, &t->tex[stage].tex); if (t->tex[stage].view_srgb) glDeleteTextures(1, &t->tex[stage].view_srgb);
    t->has_tex[stage] = 0;
}
static void g_vp(V6nTarget* t, uint32_t x, uint32_t y, uint32_t w, uint32_t h, float mn, float mx) { t->vp = (NxD3D9Viewport){x, y, w, h, mn, mx}; }
static void g_scissor(V6nTarget* t, int32_t l, int32_t tp, int32_t r, int32_t b) { t->sc = (NxD3D9Rect){l, tp, r, b}; }
static void g_proj(V6nTarget* t, const float m[16]) { memcpy(t->proj, m, sizeof t->proj); }

static void apply_sampler(V6nTarget* t, int s) {
    GLuint smp = t->sampler[s];
    glActiveTexture(GL_TEXTURE0 + s);
    if (!t->has_tex[s]) { glBindTexture(GL_TEXTURE_2D, M("no_texture_black") ? g_black : g_white); glBindSampler(s, 0); return; }
    Tex* x = &t->tex[s];
    NxD3D9Texture ti = {x->fmt, 0, 0, 0, 0};
    NxSamplerState ss; nx_d3d9_sampler(t->samp[s], &ti, &ss);
    int mip = (ss.filter >> NXSMP_MIP_SHIFT) & 3;
    if (M("mipnone_lod") && mip == NxMip_None) mip = NxMip_Nearest;
    int minl = ss.filter & NXSMP_MIN_LINEAR, magl = ss.filter & NXSMP_MAG_LINEAR;
    GLenum minf = mip == NxMip_None ? (minl ? GL_LINEAR : GL_NEAREST) :
                  mip == NxMip_Nearest ? (minl ? GL_LINEAR_MIPMAP_NEAREST : GL_NEAREST_MIPMAP_NEAREST) :
                  (minl ? GL_LINEAR_MIPMAP_LINEAR : GL_NEAREST_MIPMAP_LINEAR);
    glSamplerParameteri(smp, GL_TEXTURE_MIN_FILTER, minf);
    glSamplerParameteri(smp, GL_TEXTURE_MAG_FILTER, magl ? GL_LINEAR : GL_NEAREST);
    glSamplerParameteri(smp, GL_TEXTURE_WRAP_S, gl_wrap(ss.wrap & 7));
    glSamplerParameteri(smp, GL_TEXTURE_WRAP_T, gl_wrap((ss.wrap >> 3) & 7));
    glSamplerParameteri(smp, GL_TEXTURE_WRAP_R, gl_wrap((ss.wrap >> 6) & 7));
    glSamplerParameterf(smp, GL_TEXTURE_MIN_LOD, ss.minLod);
    glSamplerParameterf(smp, GL_TEXTURE_MAX_LOD, ss.maxLod);
    glSamplerParameterf(smp, GL_TEXTURE_LOD_BIAS, ss.lodBias);
    glSamplerParameterf(smp, GL_TEXTURE_MAX_ANISOTROPY, ss.maxAniso > 1 ? ss.maxAniso : 1.0f);
    uint32_t b = ss.border;
    if (M("border_argb")) b = (b >> 8) | (b << 24);
    float bc[4] = {(b & 0xFF) / 255.0f, ((b >> 8) & 0xFF) / 255.0f, ((b >> 16) & 0xFF) / 255.0f, ((b >> 24) & 0xFF) / 255.0f};
    glSamplerParameterfv(smp, GL_TEXTURE_BORDER_COLOR, bc);
    glBindTexture(x->target, (ss.filter & NXSMP_SRGB) && x->view_srgb ? x->view_srgb : x->tex);
    glBindSampler(s, smp);
}

static GLenum gl_prim(uint32_t p) {
    switch (p) { case 1: return GL_POINTS; case 2: return GL_LINES; case 3: return GL_LINE_STRIP; case 4: return GL_TRIANGLES;
                 case 5: return GL_TRIANGLE_STRIP; case 6: return GL_TRIANGLE_FAN; }
    return GL_TRIANGLES;
}
static uint32_t vcount(uint32_t p, uint32_t n) {
    switch (p) { case 1: return n; case 2: return 2 * n; case 3: return n + 1; case 4: return 3 * n; default: return n + 2; }
}
static GLuint g_cur;
#define U(n) glGetUniformLocation(g_cur, n)

static const uint16_t* g_idx;   /* set by g_drawidx around g_draw */
static struct { int ne, nv; const uint8_t* us; const uint8_t* ix; const float* data; } g_gen;   /* g_drawg */
static int dk_attrib_location(int usage, int index) {   /* = fnvnx_dk_attrib_location (render/shaders/include) */
    switch (usage) {
    case 0: return index == 0 ? 0 : -1;  case 1: return index == 0 ? 1 : -1;  case 2: return index == 0 ? 2 : -1;
    case 3: return index == 0 ? 3 : -1;  case 10: return index < 2 ? 4 + index : -1; case 5: return index < 8 ? 6 + index : -1;
    case 6: return index == 0 ? 14 : -1; case 7: return index == 0 ? 15 : -1;
    }
    return -1;
}
static int g_draw(V6nTarget* t, uint32_t prim, uint32_t count, int rhw, const V6nVertex* v, int nv) {
    uint32_t nvert = vcount(prim, count);
    if (!g_idx && (int)nvert > nv && !(g_gen.ne && (int)nvert <= g_gen.nv)) return 0;
    if (t->vs && rhw) { fprintf(stderr, "v6n_gl: vertex shaders take XYZ vertices\n"); return 0; }
    NxD3D9Input in; memset(&in, 0, sizeof in);
    in.rs = t->rs; in.ps_version = t->ps ? t->ps_version : 0; in.depth_format = t->dsf; in.viewport = t->vp; in.scissor = t->sc;
    NxRenderState st; NxDynamicState dyn; uint32_t sb;
    nx_d3d9_translate(&in, &st, &dyn, &sb);
    uint64_t w0 = st.w[0], w1 = st.w[1], w2 = st.w[2];
    bind_rt(t, NXRS_GET(w2, NXRS_R_SRGB, 1));
    GLuint prog = t->ps ? t->ps : g_prog;
    if (t->vs) {   /* S6b VS + S6b PS */
        if (!t->ps) { fprintf(stderr, "v6n_gl: a vertex shader needs a pixel shader here\n"); return 0; }
        uint32_t vh = fnv1a(t->vs_glsl), ph = fnv1a(t->ps_glsl); prog = 0;
        for (int i = 0; i < g_nvsp; i++) if (g_vsp[i].vh == vh && g_vsp[i].ph == ph) prog = g_vsp[i].prog;
        if (!prog) {
            /* D3D9 lets a PS read a varying the VS does not write (Nine writes oFog = 0 for vs < 3 and leaves the
             * rest undefined); GL refuses to link. Add the missing VS outputs, written as 0. */
            static char vfull[1 << 17]; stub_vs(rebind_vs(t->vs_glsl), t->ps_glsl, vfull, sizeof vfull);
            GLuint vs = compile(GL_VERTEX_SHADER, vfull), fs = compile(GL_FRAGMENT_SHADER, t->ps_glsl);
            prog = glCreateProgram(); glAttachShader(prog, vs); glAttachShader(prog, fs); glLinkProgram(prog);
            GLint ok; glGetProgramiv(prog, GL_LINK_STATUS, &ok);
            if (!ok) { char log[2048]; glGetProgramInfoLog(prog, sizeof log, NULL, log); fprintf(stderr, "link: %s\n", log); return 0; }
            if (g_nvsp < 64) { g_vsp[g_nvsp].prog = prog; g_vsp[g_nvsp].vh = vh; g_vsp[g_nvsp].ph = ph; g_nvsp++; }
        }
        if (!g_vao2) {   /* fnvnx_dk_attrib_location: POSITION0 0, COLOR0/1 4/5, TEXCOORD0 6 */
            glGenVertexArrays(1, &g_vao2); glBindVertexArray(g_vao2); glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
            glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(V6nVertex), (void*)0);
            glVertexAttribPointer(4, GL_BGRA, GL_UNSIGNED_BYTE, GL_TRUE, sizeof(V6nVertex), (void*)16);
            glVertexAttribPointer(5, GL_BGRA, GL_UNSIGNED_BYTE, GL_TRUE, sizeof(V6nVertex), (void*)20);
            glVertexAttribPointer(6, 2, GL_FLOAT, GL_FALSE, sizeof(V6nVertex), (void*)24);
            glEnableVertexAttribArray(0); glEnableVertexAttribArray(4); glEnableVertexAttribArray(5); glEnableVertexAttribArray(6);
            glGenBuffers(2, g_ubo2);
        }
        /* VS driver block: posfix = (0, 0, -1): no half-pixel shift (the +0.5 viewport does it), y negated as in
         * every V6n GL draw (LOWER_LEFT origin, image row = GL row) */
        float drv[16] = {0, 0, M("upper_left_origin") ? 1.0f : -1.0f, 0};
        if (M("vs_posfix_halfpixel")) { drv[0] = -1.0f / (float)t->vp.Width; drv[1] = 1.0f / (float)t->vp.Height; }
        glBindBuffer(GL_UNIFORM_BUFFER, g_ubo2[0]); glBufferData(GL_UNIFORM_BUFFER, sizeof t->vsc, t->vsc, GL_STREAM_DRAW);
        glBindBufferBase(GL_UNIFORM_BUFFER, 8, g_ubo2[0]);
        glBindBuffer(GL_UNIFORM_BUFFER, g_ubo2[1]); glBufferData(GL_UNIFORM_BUFFER, sizeof drv, drv, GL_STREAM_DRAW);
        glBindBufferBase(GL_UNIFORM_BUFFER, 10, g_ubo2[1]);
    }
    glUseProgram(prog); g_cur = prog;
    if (g_gen.ne) {   /* generic FLOAT4 elements at fnvnx_dk_attrib_location(usage, index) */
        static GLuint vao3; if (!vao3) glGenVertexArrays(1, &vao3);
        glBindVertexArray(vao3); glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
        glBufferData(GL_ARRAY_BUFFER, 16 * g_gen.ne * (size_t)g_gen.nv, g_gen.data, GL_STREAM_DRAW);
        for (int l = 0; l < 16; l++) glDisableVertexAttribArray(l);
        for (int i = 0; i < g_gen.ne; i++) {
            int l = dk_attrib_location(g_gen.us[i], g_gen.ix[i]); if (l < 0) continue;
            glVertexAttribPointer(l, 4, GL_FLOAT, GL_FALSE, 16 * g_gen.ne, (void*)(uintptr_t)(16 * i)); glEnableVertexAttribArray(l);
        }
    } else {
    glBindVertexArray(t->vs ? g_vao2 : g_vao); glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(V6nVertex) * (g_idx ? (uint32_t)nv : nvert), v, GL_STREAM_DRAW);
    }
    /* viewport + depth range */
    NxViewport vp = dyn.viewport;
    if (M("no_half_pixel")) { vp.x -= 0.5f; vp.y -= 0.5f; }
    /* pretransformed (RHW) vertices: Nine's window-space position path applies no viewport transform and no
     * viewport clip, so the FF model maps them over the whole target (RENDER_VERIFY.md §5, ambiguity A1) */
    if (rhw) { vp.x = 0.5f; vp.y = 0.5f; vp.w = (float)t->w; vp.h = (float)t->h;
               if (M("no_half_pixel")) { vp.x -= 0.5f; vp.y -= 0.5f; } }
    glViewportIndexedf(0, vp.x, vp.y, vp.w, vp.h);   /* GL row y = image row y (VS negates y) */
    if (rhw && !M("rhw_viewport_z")) glDepthRangeIndexed(0, 0.0, 1.0); else glDepthRangeIndexed(0, vp.minZ, vp.maxZ);
    /* blend */
    if (NXRS_GET(w0, NXRS_B_ENABLE, 1)) {
        uint32_t src = NXRS_GET(w0, NXRS_B_SRC, 5), dst = NXRS_GET(w0, NXRS_B_DST, 5), op = NXRS_GET(w0, NXRS_B_OP, 3);
        uint32_t srca = NXRS_GET(w0, NXRS_B_SRCA, 5), dsta = NXRS_GET(w0, NXRS_B_DSTA, 5), opa = NXRS_GET(w0, NXRS_B_OPA, 3);
        if (M("bothsrcalpha") && (t->rs[19] == 12 || t->rs[19] == 13)) { dst = NxBf_InvSrcAlpha == dst ? NxBf_One : NxBf_Zero; dsta = dst; }
        if (M("separate_alpha")) { srca = src; dsta = dst; opa = op; }
        glEnablei(GL_BLEND, 0);
        glBlendFuncSeparatei(0, gl_bf(src), gl_bf(dst), gl_bf(srca), gl_bf(dsta));
        glBlendEquationSeparatei(0, gl_bop(op), gl_bop(opa));
    } else glDisablei(GL_BLEND, 0);
    float* bcst = dyn.blendConstant;
    if (M("blendfactor_argb")) glBlendColor(bcst[3], bcst[0], bcst[1], bcst[2]); else glBlendColor(bcst[0], bcst[1], bcst[2], bcst[3]);
    uint32_t m = NXRS_GET(w0, NXRS_B_MASK(0), 4);
    if (M("colormask_order")) m = ((m & 1) << 3) | ((m & 2) << 1) | ((m & 4) >> 1) | ((m & 8) >> 3);
    glColorMaski(0, m & 1, (m >> 1) & 1, (m >> 2) & 1, (m >> 3) & 1);
    /* depth */
    if (NXRS_GET(w1, NXRS_D_TEST, 1) && t->ds) {
        glEnable(GL_DEPTH_TEST);
        uint32_t f = NXRS_GET(w1, NXRS_D_FUNC, 3);
        if (M("zfunc_equal_lequal") && f == NxCmp_Equal) f = NxCmp_LEqual;
        glDepthFunc(gl_cmp(f));
        glDepthMask(NXRS_GET(w1, NXRS_D_WRITE, 1) || M("zwrite_ignored") || (M("zfunc_equal_lequal") && t->rs[14]));
    } else glDisable(GL_DEPTH_TEST);
    /* stencil: front = CW on screen */
    if (NXRS_GET(w1, NXRS_S_ENABLE, 1) && t->ds) {
        glEnable(GL_STENCIL_TEST);
        uint32_t rm = M("stencil_ref_unmasked") ? 0xFF : (uint32_t)NXRS_GET(w1, NXRS_S_READMASK, 8);
        GLenum cw = GL_FRONT, ccw = GL_BACK;          /* glFrontFace(GL_CW) below makes GL_FRONT = CW on screen */
        if (M("twosided_front")) { cw = GL_BACK; ccw = GL_FRONT; }
        glStencilFuncSeparate(cw, gl_cmp(NXRS_GET(w1, NXRS_S_FUNC, 3)), NXRS_GET(w1, NXRS_S_REF, 8), rm);
        glStencilOpSeparate(cw, gl_sop(NXRS_GET(w1, NXRS_S_FAIL, 3)), gl_sop(NXRS_GET(w1, NXRS_S_ZFAIL, 3)), gl_sop(NXRS_GET(w1, NXRS_S_PASS, 3)));
        glStencilFuncSeparate(ccw, gl_cmp(NXRS_GET(w1, NXRS_S_BFUNC, 3)), NXRS_GET(w1, NXRS_S_REF, 8), rm);
        glStencilOpSeparate(ccw, gl_sop(NXRS_GET(w1, NXRS_S_BFAIL, 3)), gl_sop(NXRS_GET(w1, NXRS_S_BZFAIL, 3)), gl_sop(NXRS_GET(w1, NXRS_S_BPASS, 3)));
        glStencilMask(NXRS_GET(w1, NXRS_S_WRITEMASK, 8));
    } else glDisable(GL_STENCIL_TEST);
    /* rasterizer */
    glFrontFace(GL_CCW);   /* image row = GL y: an on-screen CW triangle has GL-positive area, so GL_FRONT = CW */
    uint32_t cull = NXRS_GET(w2, NXRS_R_CULL, 2);
    if (M("cull_winding") && cull) cull = 3 - cull;
    if (cull == NxCull_None) glDisable(GL_CULL_FACE);
    else { glEnable(GL_CULL_FACE); glCullFace(cull == NxCull_CW ? GL_FRONT : GL_BACK); }
    uint32_t fill = NXRS_GET(w2, NXRS_R_FILL, 2);
    glPolygonMode(GL_FRONT_AND_BACK, fill == NxFill_Point ? GL_POINT : fill == NxFill_Wireframe ? GL_LINE : GL_FILL);
    if (NXRS_GET(w2, NXRS_R_BIAS, 1)) {
        glEnable(GL_POLYGON_OFFSET_FILL); glEnable(GL_POLYGON_OFFSET_LINE);
        glPolygonOffset(dyn.depthBiasSlope, M("d24_bias_scale") ? dyn.depthBiasUnits * 0.5f : dyn.depthBiasUnits);
    } else { glDisable(GL_POLYGON_OFFSET_FILL); glDisable(GL_POLYGON_OFFSET_LINE); }
    if (NXRS_GET(w2, NXRS_R_SCISSOR, 1)) {
        glEnable(GL_SCISSOR_TEST);
        glScissor(dyn.scissor.x, dyn.scissor.y, dyn.scissor.w, dyn.scissor.h);
    } else glDisable(GL_SCISSOR_TEST);
    glProvokingVertex(M("provoking_last") ? GL_LAST_VERTEX_CONVENTION : GL_FIRST_VERTEX_CONVENTION);
    /* shader uniforms */
    glUniform1i(U("uRhw"), rhw);
    if (rhw) glUniform4f(U("uVp"), 0.0f, 0.0f, (float)t->w, (float)t->h);
    else glUniform4f(U("uVp"), (float)t->vp.X, (float)t->vp.Y, (float)t->vp.Width, (float)t->vp.Height);
    glUniformMatrix4fv(U("uProj"), 1, GL_FALSE, t->proj);
    glUniform1i(U("uFlat"), NXRS_GET(w2, NXRS_R_FLAT, 1));
    int fogmode = (sb >> NXSB_FOG) & 7;
    glUniform1i(U("uFogEnable"), t->rs[28] != 0);
    glUniform1i(U("uVFog"), (!rhw && t->rs[28] && fogmode == NxFog_Vertex) ? (int)(t->rs[140] <= 3 ? t->rs[140] : 0) : 0);
    glUniform1i(U("uFogDiffA"), M("fog_vertex_specular"));
    int fm = fogmode == NxFog_Linear ? 1 : fogmode == NxFog_Exp ? 2 : fogmode == NxFog_Exp2 ? 3 : fogmode == NxFog_Vertex ? 4 : 0;
    if (M("fog_exp_exp2") && (fm == 2 || fm == 3)) fm = 5 - fm;
    glUniform1i(U("uFogMode"), fm);
    glUniform1i(U("uFogLinNoClamp"), M("fog_linear_scale"));
    glUniform4fv(U("uFogColor"), 1, dyn.fogColor);
    glUniform3f(U("uFogP"), dyn.fogEnd, dyn.fogScale, dyn.fogDensity);
    /* Nine nine_ff.c: pixel fog source is W unless the projection is orthogonal (_34 == 0 && _44 == 1) */
    glUniform1i(U("uFogW"), !(t->proj[11] == 0.0f && t->proj[15] == 1.0f));
    glUniform1i(U("uAlphaTest"), (sb >> NXSB_ALPHATEST) & 1);
    glUniform1i(U("uAlphaFunc"), (sb >> NXSB_ALPHAFUNC) & 7);
    glUniform1f(U("uAlphaRef"), M("alpharef_256") ? (float)t->rs[24] / 256.0f : dyn.alphaRef);
    float tf[4]; nx_d3d9_color(t->rs[60], tf); glUniform4fv(U("uTFactor"), 1, tf);
    glUniform1i(U("uSpecular"), t->rs[29] != 0); glUniform1i(U("uSpecA"), M("specular_alpha"));
    int stage[2][4], stageb[2][4]; float cst[2][4];
    for (int s = 0; s < 2; s++) {
        stage[s][0] = t->tss[s][1]; stage[s][1] = t->tss[s][2]; stage[s][2] = t->tss[s][3]; stage[s][3] = t->tss[s][26];
        stageb[s][0] = t->tss[s][4]; stageb[s][1] = t->tss[s][5]; stageb[s][2] = t->tss[s][6]; stageb[s][3] = t->tss[s][27];
        nx_d3d9_color(t->tss[s][32], cst[s]);
        apply_sampler(t, s);
    }
    if (t->ps) for (int s = 2; s < 8; s++) apply_sampler(t, s);   /* ps_sN: binding N */
    glUniform4iv(U("uStage"), 2, &stage[0][0]); glUniform4iv(U("uStageB"), 2, &stageb[0][0]);
    glUniform4fv(U("uConst"), 2, &cst[0][0]);
    glUniform1i(U("uTex0"), 0); glUniform1i(U("uTex1"), 1);
    /* TEXCOORDINDEX naming a set the FVF lacks (only TEX1 here): Nine feeds (0,0) -- test of the model */
    glUniform2i(U("uTci"), (t->tss[0][11] & 0xFFFF) != 0, (t->tss[1][11] & 0xFFFF) != 0);
    if (t->ps) {   /* layout contract UBO 0 (c#) and UBO 2 (driver: fog, alpha test) */
        glUniform1f(U("uYSign"), M("upper_left_origin") ? 1.0f : -1.0f);
        if (!g_ubo[0]) glGenBuffers(2, g_ubo);
        glBindBuffer(GL_UNIFORM_BUFFER, g_ubo[0]); glBufferData(GL_UNIFORM_BUFFER, sizeof t->psc, t->psc, GL_STREAM_DRAW);
        glBindBufferBase(GL_UNIFORM_BUFFER, 0, g_ubo[0]);
        struct { float fogcolor[4], fog[4]; int32_t alpha[4]; float alpharef[4], texbem[16][4]; } drv;
        memset(&drv, 0, sizeof drv);
        memcpy(drv.fogcolor, dyn.fogColor, 16);
        int fm = (sb >> NXSB_FOG) & 7;   /* NxFog -> layout: 0 off, 1 vertex, 2 exp, 3 exp2, 4 linear */
        drv.fog[0] = fm == NxFog_Vertex ? 1.0f : fm == NxFog_Exp ? 2.0f : fm == NxFog_Exp2 ? 3.0f : fm == NxFog_Linear ? 4.0f : 0.0f;
        memcpy(&drv.fog[1], &t->rs[36], 4); memcpy(&drv.fog[2], &t->rs[37], 4); memcpy(&drv.fog[3], &t->rs[38], 4);
        drv.alpha[0] = ((sb >> NXSB_ALPHATEST) & 1) ? (int)((sb >> NXSB_ALPHAFUNC) & 7) + 1 : 0;
        drv.alpharef[0] = dyn.alphaRef;
        glBindBuffer(GL_UNIFORM_BUFFER, g_ubo[1]); glBufferData(GL_UNIFORM_BUFFER, sizeof drv, &drv, GL_STREAM_DRAW);
        glBindBufferBase(GL_UNIFORM_BUFFER, 2, g_ubo[1]);
    }
    if (g_idx) {
        GLuint ib; glGenBuffers(1, &ib); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ib);
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, 2 * nvert, g_idx, GL_STREAM_DRAW);
        glDrawElements(gl_prim(prim), nvert, GL_UNSIGNED_SHORT, 0);
        glDeleteBuffers(1, &ib);
    } else glDrawArrays(gl_prim(prim), 0, nvert);
    return glGetError() == GL_NO_ERROR;
}

static int g_readback(V6nTarget* t, V6nReadback* out) {
    int w = t->w, h = t->h;
    V6nReadback* r = &t->rb;
    r->w = w; r->h = h; r->color = calloc((size_t)w * h * 4, sizeof(float)); r->color_channels = 4;
    glBindFramebuffer(GL_FRAMEBUFFER, t->fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, t->rt, 0);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    NxFormatInfo fi = nx_d3d9_format(t->rtf);
    if (fi.format == NxFmt_RGBA16F || fi.format == NxFmt_RGBA32F) {
        glReadPixels(0, 0, w, h, GL_RGBA, GL_FLOAT, r->color); r->color_is_float = 1;
    } else if (fi.format == NxFmt_R5G6B5) {
        uint16_t* p = malloc((size_t)w * h * 2);
        glReadPixels(0, 0, w, h, GL_RGB, GL_UNSIGNED_SHORT_5_6_5, p);
        for (int i = 0; i < w * h; i++) { r->color[4 * i] = p[i] >> 11; r->color[4 * i + 1] = (p[i] >> 5) & 63; r->color[4 * i + 2] = p[i] & 31; }
        free(p); r->color_channels = 3;
    } else {
        uint8_t* p = malloc((size_t)w * h * 4);
        glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, p);
        for (int i = 0; i < w * h * 4; i++) r->color[i] = p[i];
        free(p);
        if (fi.format == NxFmt_BGRX8 || fi.format == NxFmt_RGBX8) { r->color_channels = 3; for (int i = 0; i < w * h; i++) r->color[4 * i + 3] = 0; }
    }
    if (t->ds) {
        NxFormatInfo di = nx_d3d9_format(t->dsf);
        r->depth = calloc((size_t)w * h, 4);
        if (di.format == NxFmt_Z24S8) {
            uint32_t* p = malloc((size_t)w * h * 4);
            glReadPixels(0, 0, w, h, GL_DEPTH_STENCIL, GL_UNSIGNED_INT_24_8, p);
            r->stencil = calloc((size_t)w * h, 1);
            for (int i = 0; i < w * h; i++) { r->depth[i] = p[i] >> 8; r->stencil[i] = p[i] & 0xFF; }
            free(p);
        } else if (di.format == NxFmt_Z32F) {
            glReadPixels(0, 0, w, h, GL_DEPTH_COMPONENT, GL_FLOAT, r->depth);
        } else {
            glReadPixels(0, 0, w, h, GL_DEPTH_COMPONENT, GL_UNSIGNED_INT, r->depth);
            int bits = di.format == NxFmt_Z16 ? 16 : 24;
            for (int i = 0; i < w * h; i++) r->depth[i] >>= 32 - bits;
        }
    }
    if (0) {
        for (int y = 0; y < h / 2; y++) {
            int y2 = h - 1 - y;
            for (int x = 0; x < w * 4; x++) { float c = r->color[y * w * 4 + x]; r->color[y * w * 4 + x] = r->color[y2 * w * 4 + x]; r->color[y2 * w * 4 + x] = c; }
            if (r->depth) for (int x = 0; x < w; x++) { uint32_t d = r->depth[y * w + x]; r->depth[y * w + x] = r->depth[y2 * w + x]; r->depth[y2 * w + x] = d; }
            if (r->stencil) for (int x = 0; x < w; x++) { uint8_t d = r->stencil[y * w + x]; r->stencil[y * w + x] = r->stencil[y2 * w + x]; r->stencil[y2 * w + x] = d; }
        }
    }
    *out = *r;
    return glGetError() == GL_NO_ERROR;
}
static int g_psh(V6nTarget* t, const uint32_t* bc, int n, const char* glsl) {
    if (!bc) { t->ps = 0; return 1; }
    if (!glsl || n < 1) return 0;
    t->ps_version = bc[0] & 0xFFFF;
    uint32_t h = fnv1a(glsl);
    { static char keep[1 << 17]; snprintf(keep, sizeof keep, "%s", glsl); t->ps_glsl = keep; }
    for (int i = 0; i < g_nps; i++) if (g_ps[i].hash == h) { t->ps = g_ps[i].prog; return 1; }
    static char vfull[1 << 16]; stub_vs(VS2, glsl, vfull, sizeof vfull);   /* t1..t7 etc. the RHW vertex lacks: 0 */
    GLuint vs2 = compile(GL_VERTEX_SHADER, vfull);
    GLuint fs = compile(GL_FRAGMENT_SHADER, glsl);
    GLuint p = glCreateProgram(); glAttachShader(p, vs2); glAttachShader(p, fs); glLinkProgram(p);
    GLint ok; glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) { char log[2048]; glGetProgramInfoLog(p, sizeof log, NULL, log); fprintf(stderr, "link: %s\n", log); return 0; }
    if (g_nps < 64) { g_ps[g_nps].prog = p; g_ps[g_nps].hash = h; g_nps++; }
    t->ps = p;
    return 1;
}
static void g_psc(V6nTarget* t, uint32_t reg, const float v[4]) { if (reg < 256) memcpy(t->psc[reg], v, 16); }
static int g_vsh(V6nTarget* t, const uint32_t* bc, int n, const char* glsl) {
    if (!bc) { t->vs = 0; return 1; }
    if (!glsl || n < 1 || g_nvs_text >= 64) return 0;
    snprintf(g_vs_text[g_nvs_text], sizeof g_vs_text[0], "%s", glsl);
    t->vs_glsl = g_vs_text[g_nvs_text++]; t->vs = 1;
    return 1;
}
static void g_vsc(V6nTarget* t, uint32_t reg, const float v[4]) { if (reg < 256) memcpy(t->vsc[reg], v, 16); }
static int g_drawidx(V6nTarget* t, uint32_t prim, uint32_t count, int rhw, const V6nVertex* v, int nv, const uint16_t* idx, int ni) {
    if ((int)vcount(prim, count) > ni) return 0;
    for (int i = 0; i < ni; i++) if (idx[i] >= nv) return 0;
    g_idx = idx; int r = g_draw(t, prim, count, rhw, v, nv); g_idx = NULL;
    return r;
}
static int g_drawg(V6nTarget* t, uint32_t prim, uint32_t count, int ne, const uint8_t* us, const uint8_t* ix, const float* d, int nv) {
    if (!t->vs) return 0;
    g_gen.ne = ne; g_gen.nv = nv; g_gen.us = us; g_gen.ix = ix; g_gen.data = d;
    int r = g_draw(t, prim, count, 0, NULL, 0);
    g_gen.ne = 0;
    return r;
}
const V6nOps v6n_target_ops = {"gl45-llvmpipe+s6n", g_begin, g_end, g_clear, g_rs, g_samp, g_tss, g_tex, g_notex, g_vp,
                               g_scissor, g_proj, g_draw, g_readback, g_psh, g_psc, g_vsh, g_vsc, g_drawidx, g_drawg, g_texcube};
const char* const* v6n_gl_mutants(void) { return MUTANTS; }
