/* fnv-nx L6 GL 4.5 backend v4: shaders and programs. The GLSL shim is nxgl v3's (same rewriting rules, see
 * render/native/gl/nxgl.h): "#version 460..." -> "#version 450 core", "binding = n" -> "binding = stage * 16 + n",
 * uniform block names get a per-stage suffix, and the vertex stage's main is wrapped to negate gl_Position.y. */
#include "gl4.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- the GLSL shim ---------------------------------------------------------------------------------------------- */
size_t nxgl4_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap) {
    size_t o = 0, i = 0;
#define PUT(c) do { if (o + 1 >= cap) return 0; out[o++] = (c); } while (0)
    while (i < n) {
        if ((i == 0 || src[i - 1] == '\n') && n - i >= 8 && !memcmp(src + i, "#version", 8)) {
            const char* v = "#version 450 core";
            for (const char* p = v; *p; p++) PUT(*p);
            while (i < n && src[i] != '\n') i++;
            continue;
        }
        if (n - i >= 7 && !memcmp(src + i, "binding", 7) && (i == 0 || !(src[i - 1] == '_' ||
            (src[i - 1] >= 'a' && src[i - 1] <= 'z') || (src[i - 1] >= 'A' && src[i - 1] <= 'Z') ||
            (src[i - 1] >= '0' && src[i - 1] <= '9')))) {
            size_t j = i + 7;
            while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
            if (j < n && src[j] == '=') {
                j++;
                while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
                if (j < n && src[j] >= '0' && src[j] <= '9') {
                    unsigned v = 0;
                    while (j < n && src[j] >= '0' && src[j] <= '9') v = v * 10 + (unsigned)(src[j++] - '0');
                    char num[32];
                    int k = snprintf(num, sizeof num, "binding = %u", v + stage * 16);
                    for (int q = 0; q < k; q++) PUT(num[q]);
                    i = j;
                    continue;
                }
            }
        }
        /* "uniform Name {": a block of that name in two stages is one GL block and must have one binding; the
         * stages bind their own buffers (binding = stage * 16 + n), so each stage gets its own block name */
        if (n - i >= 8 && !memcmp(src + i, "uniform", 7) && (src[i + 7] == ' ' || src[i + 7] == '\t') &&
            (i == 0 || src[i - 1] == ' ' || src[i - 1] == '\t' || src[i - 1] == ')' || src[i - 1] == '\n')) {
            size_t j = i + 7;
            while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
            size_t id0 = j;
            while (j < n && (src[j] == '_' || (src[j] >= 'a' && src[j] <= 'z') || (src[j] >= 'A' && src[j] <= 'Z') ||
                             (src[j] >= '0' && src[j] <= '9'))) j++;
            size_t id1 = j;
            while (j < n && (src[j] == ' ' || src[j] == '\t' || src[j] == '\n')) j++;
            if (id1 > id0 && j < n && src[j] == '{') {
                for (size_t q = i; q < id1; q++) PUT(src[q]);
                const char* sfx = stage ? "_nxfs" : "_nxvs";
                for (const char* q = sfx; *q; q++) PUT(*q);
                i = id1;
                continue;
            }
        }
        /* vertex stage: "void main(" -> "void nx_main_(", wrapper appended below */
        if (stage == 0 && n - i >= 9 && !memcmp(src + i, "void main", 9) && (i == 0 || src[i - 1] == '\n' ||
            src[i - 1] == ' ') && (i + 9 >= n || src[i + 9] == '(' || src[i + 9] == ' ')) {
            const char* r = "void nx_main_";
            for (const char* q = r; *q; q++) PUT(*q);
            i += 9;
            continue;
        }
        PUT(src[i]);
        i++;
    }
    if (stage == 0) {
        const char* w = "\nvoid main() { nx_main_(); gl_Position.y = -gl_Position.y; }\n";
        for (const char* q = w; *q; q++) PUT(*q);
    }
    out[o] = 0;
    return o;
#undef PUT
}

/* ---- shaders --------------------------------------------------------------------------------------------------- */
static GLenum stage_enum(uint32_t stage) {
    switch ((enum NxStage)stage) {
    case NxStage_Vertex: return GL_VERTEX_SHADER;
    case NxStage_Fragment: return GL_FRAGMENT_SHADER;
    }
    return 0;
}

/* GLSL (deko style, "#version 460") -> a registered GL shader of the stage. 0 and a recorded error when the table is full or the compile fails: nothing is registered then. */
uint32_t gl4_shader_from_glsl(const char* glsl, size_t n, uint32_t stage) {
    if (!glsl || !n || !stage_enum(stage)) return 0;
    uint32_t id = 1;
    while (id <= GL4_MAX_SHADERS && g4.sh[id].used) id++;
    if (id > GL4_MAX_SHADERS) { gl4_err("shader: the shader table is full"); return 0; }
    size_t cap = n * 2 + 256;
    char* src = (char*)malloc(cap);
    if (!src) return 0;
    if (!nxgl4_shim_glsl(glsl, n, stage, src, cap)) { free(src); gl4_err("shader: the GLSL shim failed"); return 0; }
    GLuint s = p_glCreateShader(stage_enum(stage));
    const char* srcs[1] = {src};
    p_glShaderSource(s, 1, srcs, NULL);
    p_glCompileShader(s);
    free(src);
    GLint ok = 0;
    p_glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1500];
        p_glGetShaderInfoLog(s, sizeof log, NULL, log);
        gl4_err("shader compile failed: %s", log);
        p_glDeleteShader(s);
        return 0;
    }
    g4.sh[id].sh = s; g4.sh[id].stage = (uint8_t)stage; g4.sh[id].used = 1;
    return id;
}

uint64_t gl4_api_shader_create(uint64_t a) {
    const NxShaderDesc* d = (const NxShaderDesc*)nxgl4_guest_ptr(a, sizeof *d);
    if (!d || !d->glsl || !d->glsl_size) return 0;
    return gl4_shader_from_glsl((const char*)nxgl4_guest_ptr(d->glsl, d->glsl_size), d->glsl_size, d->stage);
}

/* Programs are cached by (vs, ps). Destroying a shader deletes the programs built from it and bumps progEpoch: a
 * state that cached a program index re-resolves at its next bind (ids are reused, a stale hit would be wrong). */
uint64_t gl4_api_shader_destroy(uint64_t id) {
    if (id > GL4_MAX_SHADERS || !id || !g4.sh[id].used) { gl4_err("shader_destroy: %llu is not a live shader", (unsigned long long)id); return NXGPU_BAD_CALL; }
    if (g4.sh[id].d3d && --g4.sh[id].d3d->refs) return 0;   /* made by SHADER_CREATE_D3D: one create taken back, others still hold the id */
    for (uint32_t i = 1; i <= GL4_MAX_PROGRAMS; i++) {
        Gl4Program* p = &g4.prog[i];
        if (!p->used || (p->vs != id && p->ps != id)) continue;
        p_glDeleteProgram(p->prog);
        memset(p, 0, sizeof *p);
    }
    g4.progEpoch++;
    p_glDeleteShader(g4.sh[id].sh);
    gl4_d3d_free(g4.sh[id].d3d);
    memset(&g4.sh[id], 0, sizeof g4.sh[id]);
    return 0;
}

uint16_t gl4_program_for(uint16_t vs, uint16_t ps) {
    uint32_t freeSlot = 0;
    for (uint32_t i = 1; i <= GL4_MAX_PROGRAMS; i++) {
        if (!g4.prog[i].used) { if (!freeSlot) freeSlot = i; continue; }
        if (g4.prog[i].vs == vs && g4.prog[i].ps == ps) return (uint16_t)i;
    }
    if (!freeSlot || !vs || vs > GL4_MAX_SHADERS || !g4.sh[vs].used || !ps || ps > GL4_MAX_SHADERS || !g4.sh[ps].used) {
        gl4_err("program: shaders %u/%u not available", vs, ps);
        return 0;
    }
    GLuint p = p_glCreateProgram();
    p_glAttachShader(p, g4.sh[vs].sh);
    p_glAttachShader(p, g4.sh[ps].sh);
    p_glLinkProgram(p);
    GLint ok = 0;
    p_glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[1500];
        p_glGetProgramInfoLog(p, sizeof log, NULL, log);
        gl4_err("program link failed: %s", log);
        p_glDeleteProgram(p);
        return 0;
    }
    g4.prog[freeSlot].vs = vs; g4.prog[freeSlot].ps = ps; g4.prog[freeSlot].prog = p; g4.prog[freeSlot].used = 1;
    return (uint16_t)freeSlot;
}

/* Internal programs (scaling, present): GLSL 4.50 core, compiled as given. 0 and a recorded error on failure. */
GLuint gl4_compile_program(const char* vs, const char* fs) {
    GLuint sh[2] = {p_glCreateShader(GL_VERTEX_SHADER), p_glCreateShader(GL_FRAGMENT_SHADER)}, p = p_glCreateProgram();
    const char* src[2] = {vs, fs};
    GLint ok = 1;
    char log[1500];
    for (int i = 0; i < 2; i++) {
        GLint c = 0;
        p_glShaderSource(sh[i], 1, &src[i], NULL);
        p_glCompileShader(sh[i]);
        p_glGetShaderiv(sh[i], GL_COMPILE_STATUS, &c);
        if (!c) { p_glGetShaderInfoLog(sh[i], sizeof log, NULL, log); gl4_err("internal shader %d: %s", i, log); ok = 0; }
        p_glAttachShader(p, sh[i]);
    }
    if (ok) {
        p_glLinkProgram(p);
        p_glGetProgramiv(p, GL_LINK_STATUS, &ok);
        if (!ok) { p_glGetProgramInfoLog(p, sizeof log, NULL, log); gl4_err("internal program: %s", log); }
    }
    p_glDeleteShader(sh[0]); p_glDeleteShader(sh[1]);
    if (!ok) { p_glDeleteProgram(p); return 0; }
    return p;
}
