/* backend_mojoshader.c - GLSL backend: the GLSL text of the front end's one MojoShader parse (profile "glsldk", derivation/mojoshader_*.patch). */
#include <stdlib.h>
#include <string.h>
#include "mojoshader.h"
#include "backend.h"

static char *mojo_emit(const fnvnx_reflection *refl, char *err, size_t errlen)
{
    const MOJOSHADER_parseData *pd = (const MOJOSHADER_parseData *)refl->frontend_parse;
    char *out = (char *)malloc((size_t)pd->output_len + 1);
    if (!out) { memcpy(err, "out of memory", errlen < 14 ? errlen : 14); return NULL; }
    memcpy(out, pd->output, (size_t)pd->output_len);
    out[pd->output_len] = 0;
    return out;
}

const fnvnx_glsl_backend fnvnx_backend_mojoshader_glsldk = { "mojoshader-glsldk", mojo_emit };
