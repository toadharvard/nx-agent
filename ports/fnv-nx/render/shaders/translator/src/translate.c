/* translate.c - GLSL half of the shader API: front end -> GLSL backend. No DKSH, no uam: the result always holds the GLSL in r->glsl. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../include/fnvnx_shaders.h"
#include "backend.h"
#include "fnvnx_sha256.h"

static const fnvnx_glsl_backend *const g_backends[] = { &fnvnx_backend_mojoshader_glsldk, NULL };
const char *const fnvnx_shader_backend_names[] = { "mojoshader-glsldk", NULL };


static void glsl_stats(const char *g, fnvnx_shader_stats *st)
{
    int in_main = 0, depth = 0;
    for (const char *c = g; *c; c++) {
        if (*c == '\n') st->glsl_lines++;
        if (!in_main && !strncmp(c, "void main()", 11)) in_main = 1;
        if (in_main) {
            if (*c == '{') depth++;
            else if (*c == '}') depth--;
            else if (*c == ';' && depth > 0) st->glsl_statements++;
        }
    }
}

int fnvnx_shader_translate_glsl(const void *bc, size_t n, const fnvnx_shader_opts *opts, fnvnx_shader_result *r)
{
    static const fnvnx_shader_opts defaults = { 0, NULL, NULL, 0 };
    if (!opts) opts = &defaults;
    memset(r, 0, sizeof(*r));
    r->stage = FNVNX_STAGE_UNKNOWN;
    if (!bc) { snprintf(r->error, sizeof r->error, "null bytecode"); return 0; }
    fnvnx_sha256(bc, n, r->key);

    const fnvnx_glsl_backend *be = NULL;
    for (int i = 0; g_backends[i]; i++)
        if (!opts->backend || !strcmp(opts->backend, g_backends[i]->name)) { be = g_backends[i]; break; }
    if (!be) { snprintf(r->error, sizeof r->error, "unknown backend '%s'", opts->backend); return 0; }

    fnvnx_reflection refl;
    if (fnvnx_frontend_parse(bc, n, opts->link_pairs, opts->n_link_pairs, &refl, &r->reflection_json, r->error, sizeof r->error)) return 0;
    r->stage = refl.stage; r->major = refl.major; r->minor = refl.minor;
    refl.vs_complete = refl.stage == FNVNX_STAGE_VERTEX && (opts->flags & FNVNX_SHADER_VS_COMPLETE) != 0;
    r->stats.d3d_instructions = refl.d3d_instructions;

    char *glsl = be->emit(bc, n, &refl, r->error, sizeof r->error);
    fnvnx_frontend_release(&refl);
    if (!glsl) return 0;
    {   /* build-time defines go right after the #version line: the static epilogue variant, the numerics */
        char defs[160]; int k = 0;
        if ((opts->flags & FNVNX_SHADER_EPI_STATIC) && refl.stage == FNVNX_STAGE_PIXEL && strstr(glsl, "DK_EPI_ALPHA"))
            k += snprintf(defs + k, sizeof defs - (size_t)k, "#define DK_EPI_ALPHA %d\n#define DK_EPI_FOG %d\n",
                          (opts->flags & FNVNX_SHADER_EPI_ALPHA) ? 1 : 0, (opts->flags & FNVNX_SHADER_EPI_FOG) ? 1 : 0);
        if ((opts->flags & FNVNX_SHADER_NUMERICS_NINE) && strstr(glsl, "DK_NUMERICS_NINE"))
            k += snprintf(defs + k, sizeof defs - (size_t)k, "#define DK_NUMERICS_NINE 1\n");
        if (k > 0) {
            char *nl = strchr(glsl, '\n');
            size_t head = nl ? (size_t)(nl - glsl) + 1 : 0, len = strlen(glsl);
            char *v = (char *)malloc(len + (size_t)k + 1);
            if (!v) { free(glsl); snprintf(r->error, sizeof r->error, "out of memory"); return 0; }
            memcpy(v, glsl, head); memcpy(v + head, defs, (size_t)k); memcpy(v + head + k, glsl + head, len - head + 1);
            free(glsl); glsl = v;
        }
    }
    glsl_stats(glsl, &r->stats);

    r->glsl = glsl;
    r->ok = 1;
    return 1;
}

void fnvnx_shader_result_free(fnvnx_shader_result *r)
{
    free(r->dksh); free(r->glsl); free(r->reflection_json);
    r->dksh = NULL; r->glsl = NULL; r->reflection_json = NULL;
}
