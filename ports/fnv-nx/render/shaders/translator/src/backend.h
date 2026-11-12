/*
 * backend.h - the seam between the D3D9 front end and GLSL emission.
 *
 * The front end (frontend.c) owns: bytecode validation, the shader model/stage, reflection
 * (fnvnx_reflection, serialised to JSON), hashing. A backend only turns bytecode into GLSL 4.50
 * that obeys fnvnx_dk_layout.h: UBO 0/1/2, sampler binding = register, fixed attribute and
 * varying locations, the VS position epilogue, the PS alpha-test/fog epilogue. It must not
 * change register meaning, so reflection stays valid whatever backend runs.
 *
 * Today: "mojoshader-glsldk" (MojoShader + mojoshader.patch). Planned: an optimising backend
 * (own IR from the token stream: SSA, constant folding, DCE, register-array scalarisation,
 * swizzle simplification; or SPIR-V + spirv-opt -> GLSL) registered next to it.
 */
#ifndef FNVNX_SHADERS_BACKEND_H
#define FNVNX_SHADERS_BACKEND_H
#include <stddef.h>
#include <stdint.h>
#include "../include/fnvnx_shaders.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct fnvnx_reflection {
    fnvnx_shader_stage stage;
    int major, minor;
    uint32_t d3d_instructions;
    /* the parsed form the front end used; opaque to backends that lift their own IR */
    const void *frontend_parse;
    /* location rule result (fnvnx_dk_assign) indexed by FNVNX_DK_PAIR; -1 = not declared; copy: VS writes a varying copy of POSITION0/PSIZE0 */
    signed char attrib_loc[224], varying_loc[224];
    unsigned char copy[224];
    int vs_complete;                      /* FNVNX_SHADER_VS_COMPLETE (vertex shaders) */
} fnvnx_reflection;

typedef struct fnvnx_glsl_backend {
    const char *name;
    /* Returns malloc'd GLSL or NULL with err filled. */
    char *(*emit)(const void *bytecode, size_t n, const fnvnx_reflection *refl, char *err, size_t errlen);
} fnvnx_glsl_backend;

extern const fnvnx_glsl_backend fnvnx_backend_mojoshader_glsldk;

/* frontend.c */
int fnvnx_frontend_parse(const void *bc, size_t n, const unsigned char *link, uint32_t nlink, fnvnx_reflection *refl, char **json, char *err, size_t errlen);
void fnvnx_frontend_release(fnvnx_reflection *refl);

#ifdef __cplusplus
}
#endif
#endif
