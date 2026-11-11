/* translate_dksh.c - DKSH step of the shader API: fnvnx_shader_translate = translate_glsl + uam + validate. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_shaders.h"
#include "dk_api.h"

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
    if (!fnvnx_shader_translate_glsl(bc, n, opts, r)) return 0;
    r->ok = 0;
    if (!(opts->flags & FNVNX_SHADER_NO_DKSH)) {
        fnvnx_dk_out dk;
        if (fnvnx_dk_compile(r->stage, r->glsl, &dk, r->error, sizeof r->error)) goto done;
        r->dksh = dk.dksh; r->dksh_size = dk.size;
        r->stats.dk_instructions = dk.instr; r->stats.dk_gprs = dk.gprs;
        r->stats.dk_code_size = dk.code; r->stats.dk_constbuf1_size = dk.cb1;
        if (fnvnx_dksh_validate(r->dksh, r->dksh_size, r->stage, r->error, sizeof r->error)) goto done;
    }
    r->ok = 1;
done:
    if (!(opts->flags & FNVNX_SHADER_KEEP_GLSL)) { free(r->glsl); r->glsl = NULL; }
    return r->ok;
}
