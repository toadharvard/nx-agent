/* DKSH step: GLSL -> uam -> DKSH (dk.cpp). Not part of the GL translator root. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "fnvnx_shaders.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef struct fnvnx_dk_out { uint8_t *dksh; size_t size; uint32_t instr, gprs, code, cb1; } fnvnx_dk_out;
int fnvnx_dk_compile(fnvnx_shader_stage stage, const char *glsl, fnvnx_dk_out *out, char *err, size_t errlen);

#ifdef __cplusplus
}
#endif
