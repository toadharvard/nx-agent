/* translate.c - libfnvnx_shaders public API: front end -> GLSL backend -> uam -> DKSH. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_shaders.h"
#include "backend.h"

static const fnvnx_glsl_backend *const g_backends[] = { &fnvnx_backend_mojoshader_glsldk, NULL };
static const char *const g_backend_names[] = { "mojoshader-glsldk", NULL };

const char *const *fnvnx_shader_backends(void) { return g_backend_names; }

uint64_t fnvnx_shader_hash(const void *bytecode, size_t n)
{
    const uint8_t *p = (const uint8_t *)bytecode;
    uint64_t h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 0x100000001b3ull; }
    return h;
}

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

/* DkshHeader: magic, header_sz, control_sz, code_sz, programs_off, num_programs; then DkshProgramHeader.type */
int fnvnx_dksh_validate(const void *dksh, size_t n, fnvnx_shader_stage stage, char *err, size_t errlen)
{
    const uint8_t *b = (const uint8_t *)dksh;
    uint32_t h[8];
    if (n < 32) { snprintf(err, errlen, "dksh too small"); return -1; }
    for (int i = 0; i < 8; i++) h[i] = b[4*i] | (b[4*i+1] << 8) | (b[4*i+2] << 16) | ((uint32_t)b[4*i+3] << 24);
    if (h[0] != 0x48534B44u) { snprintf(err, errlen, "bad magic %08x", h[0]); return -1; }
    if (h[1] != 24) { snprintf(err, errlen, "header_sz %u", h[1]); return -1; }
    if ((h[2] & 255) || (h[3] & 255)) { snprintf(err, errlen, "sections not 256-aligned (%u, %u)", h[2], h[3]); return -1; }
    if ((size_t)h[2] + h[3] != n) { snprintf(err, errlen, "control %u + code %u != size %zu", h[2], h[3], n); return -1; }
    if (h[4] != 24 || h[5] != 1) { snprintf(err, errlen, "programs_off %u num_programs %u", h[4], h[5]); return -1; }
    uint32_t want = stage == FNVNX_STAGE_VERTEX ? 0 : 1;
    if (h[6] != want) { snprintf(err, errlen, "program type %u, expected %u", h[6], want); return -1; }
    if (h[3] == 0) { snprintf(err, errlen, "empty code section"); return -1; }
    return 0;
}

int fnvnx_shader_translate(const void *bc, size_t n, const fnvnx_shader_opts *opts, fnvnx_shader_result *r)
{
    static const fnvnx_shader_opts defaults = { 0, NULL, NULL, 0 };
    if (!opts) opts = &defaults;
    memset(r, 0, sizeof(*r));
    r->stage = FNVNX_STAGE_UNKNOWN;
    if (!bc) { snprintf(r->error, sizeof r->error, "null bytecode"); return 0; }
    r->hash = fnvnx_shader_hash(bc, n);

    const fnvnx_glsl_backend *be = NULL;
    for (int i = 0; g_backends[i]; i++)
        if (!opts->backend || !strcmp(opts->backend, g_backends[i]->name)) { be = g_backends[i]; break; }
    if (!be) { snprintf(r->error, sizeof r->error, "unknown backend '%s'", opts->backend); return 0; }

    fnvnx_reflection refl;
    if (fnvnx_frontend_parse(bc, n, opts->link_pairs, opts->n_link_pairs, &refl, &r->reflection_json, r->error, sizeof r->error)) return 0;
    r->stage = refl.stage; r->major = refl.major; r->minor = refl.minor;
    refl.vs_complete = refl.stage == FNVNX_STAGE_VERTEX && (opts->flags & FNVNX_SHADER_VS_COMPLETE) != 0;
    refl.flat_color = (opts->flags & FNVNX_SHADER_FLAT_COLOR) != 0;
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

    if (!(opts->flags & FNVNX_SHADER_NO_DKSH)) {
        fnvnx_dk_out dk;
        if (fnvnx_dk_compile(r->stage, glsl, &dk, r->error, sizeof r->error)) goto done;
        r->dksh = dk.dksh; r->dksh_size = dk.size;
        r->stats.dk_instructions = dk.instr; r->stats.dk_gprs = dk.gprs;
        r->stats.dk_code_size = dk.code; r->stats.dk_constbuf1_size = dk.cb1;
        if (fnvnx_dksh_validate(r->dksh, r->dksh_size, r->stage, r->error, sizeof r->error)) goto done;
    }
    r->ok = 1;
done:
    if (opts->flags & FNVNX_SHADER_KEEP_GLSL) r->glsl = glsl;
    else free(glsl);
    return r->ok;
}

void fnvnx_shader_result_free(fnvnx_shader_result *r)
{
    free(r->dksh); free(r->glsl); free(r->reflection_json);
    r->dksh = NULL; r->glsl = NULL; r->reflection_json = NULL;
}
