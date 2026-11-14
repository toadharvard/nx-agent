/* backend_mojoshader.c - GLSL backend: MojoShader with the "glsldk" profile (mojoshader.patch). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mojoshader.h"
#include "backend.h"

static char *mojo_emit(const void *bc, size_t n, const fnvnx_reflection *refl, char *err, size_t errlen)
{
    MOJOSHADER_glsldkSetLocations(refl->attrib_loc, refl->varying_loc, refl->copy);
    MOJOSHADER_glsldkSetComplete(refl->vs_complete);
    MOJOSHADER_glsldkSetFlat(refl->flat_color);
    const MOJOSHADER_parseData *pd = MOJOSHADER_parse(MOJOSHADER_PROFILE_GLSLDK, "main",
        (const unsigned char *)bc, (unsigned int)n, NULL, 0, NULL, 0, NULL, NULL, NULL);
    MOJOSHADER_glsldkSetLocations(NULL, NULL, NULL);
    MOJOSHADER_glsldkSetComplete(0);
    MOJOSHADER_glsldkSetFlat(0);
    if (!pd) { snprintf(err, errlen, "out of memory"); return NULL; }
    if (pd->error_count > 0) {
        size_t k = (size_t)snprintf(err, errlen, "mojoshader-glsldk:");
        for (int i = 0; i < pd->error_count && k < errlen; i++)
            k += (size_t)snprintf(err + k, errlen - k, " %s;", pd->errors[i].error);
        MOJOSHADER_freeParseData(pd);
        return NULL;
    }
    char *out = (char *)malloc((size_t)pd->output_len + 1);
    if (out) { memcpy(out, pd->output, (size_t)pd->output_len); out[pd->output_len] = 0; }
    MOJOSHADER_freeParseData(pd);
    return out;
}

const fnvnx_glsl_backend fnvnx_backend_mojoshader_glsldk = { "mojoshader-glsldk", mojo_emit };
