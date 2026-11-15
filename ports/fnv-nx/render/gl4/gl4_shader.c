/* fnv-nx L6 GL 4.5 backend v4: shaders and programs. The GLSL rewrite for the driver is nxgl4_shim_glsl (render/gl4shim, nx_gl4shim.h). */
#include "gl4.h"
#include "fnvnx_sha256.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- shaders --------------------------------------------------------------------------------------------------- */
static GLenum stage_enum(uint32_t stage) {
    switch ((enum NxStage)stage) {
    case NxStage_Vertex: return GL_VERTEX_SHADER;
    case NxStage_Fragment: return GL_FRAGMENT_SHADER;
    }
    return 0;
}

/* GLSL (deko style, "#version 460") -> a registered GL shader of the stage. 0 and a recorded error when the host allocator fails or the compile fails: nothing is registered then. */
uint32_t gl4_shader_from_glsl(const char* glsl, size_t n, uint32_t stage) {
    if (!glsl || !n || !stage_enum(stage)) return 0;
    uint32_t id = 1;
    while (id < g4.nSh && g4.sh[id].used) id++;
    if (id >= g4.nSh && !gl4_table_grow((void**)&g4.sh, &g4.nSh, sizeof *g4.sh)) { gl4_err("shader: out of memory"); return 0; }
    size_t cap = NX_GL4SHIM_OUT_CAP(n);
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
    const char* src = (const char*)nxgl4_guest_ptr(d->glsl, d->glsl_size);
    if (!src) return 0;
    uint8_t k[FNVNX_SHA256_DIGEST_SIZE]; fnvnx_sha256(src, d->glsl_size, k);
    if (memcmp(k, d->key, sizeof k)) { gl4_err("shader_create: key is not the sha256 of the glsl"); return 0; }
    return gl4_shader_from_glsl(src, d->glsl_size, d->stage);
}

/* Programs are cached by (vs, ps). Destroying a shader deletes the programs built from it and bumps progEpoch: a
 * state that cached a program index re-resolves at its next bind (ids are reused, a stale hit would be wrong). */
uint64_t gl4_api_shader_destroy(uint64_t id) {
    if (id >= g4.nSh || !id || !g4.sh[id].used) { gl4_err("shader_destroy: %llu is not a live shader", (unsigned long long)id); return NXGPU_BAD_CALL; }
    if (g4.sh[id].d3d && --g4.sh[id].d3d->refs) return 0;   /* made by SHADER_CREATE_D3D: one create taken back, others still hold the id */
    for (uint32_t i = 1; i < g4.nProg; i++) {
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

uint32_t gl4_program_for(uint32_t vs, uint32_t ps) {
    uint32_t freeSlot = 0;
    for (uint32_t i = 1; i < g4.nProg; i++) {
        if (!g4.prog[i].used) { if (!freeSlot) freeSlot = i; continue; }
        if (g4.prog[i].vs == vs && g4.prog[i].ps == ps) return i;
    }
    if (!vs || vs >= g4.nSh || !g4.sh[vs].used || !ps || ps >= g4.nSh || !g4.sh[ps].used) {
        gl4_err("program: shaders %u/%u not available", vs, ps);
        return 0;
    }
    if (!freeSlot) {
        freeSlot = g4.nProg ? g4.nProg : 1;
        if (!gl4_table_grow((void**)&g4.prog, &g4.nProg, sizeof *g4.prog)) { gl4_err("program: out of memory"); return 0; }
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
    return freeSlot;
}

/* Internal programs (scaling, present): GLSL 4.50 core, compiled as given. 0 and a recorded error on failure. */
GLuint gl4_compile_program(enum nx_gl4shim_prog_id id) {
    GLuint sh[2] = {p_glCreateShader(GL_VERTEX_SHADER), p_glCreateShader(GL_FRAGMENT_SHADER)}, p = p_glCreateProgram();
    const char* src[2] = {nx_gl4shim_progs[id].vs, nx_gl4shim_progs[id].fs};
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
