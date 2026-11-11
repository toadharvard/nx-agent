/* fnv-nx L6 GL 4.5 backend v4.1: NXGPU_FN_SHADER_CREATE_D3D (nxgpu4_shader.h). D3D9 bytecode -> GLSL by libfnvnx_shaders (NO_DKSH | KEEP_GLSL, VS_COMPLETE per the variant bit, the link set L) ->
 * the path of the v3 shader_create (gl4_shader_from_glsl: the nxgl v3 shim, compile, register).
 *  D1 every byte the call reads comes from nxgl4_guest_ptr over exactly the ranges the descriptor names (descriptor 40 bytes, bytecode `size` bytes, link set n_link_pairs bytes); the bytecode is copied
 *     before anything looks at it, so the guest cannot change it under the translator. Nothing beyond `size` is read.
 *  D2 the call is total: every violation of the ABI text (stage, variant, reserved fields, size, hash, L, stage of the version token) and every failure of the translator or of the GL compile
 *     returns 0 with the reason in nxgl4_last_error and registers nothing.
 *  D3 memo: the key is the bytecode itself (memcmp, so a hash collision cannot alias two codes) + stage + variant + L. A repeated key returns the id again and counts one more owner; SHADER_DESTROY takes
 *     one owner back (gl4_shader.c), the last one releases the id. Distinct variants / link sets are distinct ids.
 *  D4 the translator's output is the GLSL; the shader cache of render/shaders/cache holds DKSH + reflection only (shcache.h payload) and is keyed without the VS_COMPLETE bit, so it cannot serve
 *     this call: no cache path exists here . */
#include "gl4.h"
#include "fnvnx_shaders.h"
#include <stdlib.h>
#include <string.h>

enum { MAX_CODE = 1u << 20, MAX_L = 31 };

void gl4_d3d_free(Gl4D3d* d) { if (d) { free(d->code); free(d); } }

static uint64_t refuse(const char* why) { gl4_err("shader_create_d3d: %s", why); return 0; }

static uint32_t memo_find(const Gl4D3d* k, const uint8_t* code) {
    for (uint32_t id = 1; id <= GL4_MAX_SHADERS; id++) {
        const Gl4D3d* e = g4.sh[id].d3d;
        if (e && e->size == k->size && e->stage == k->stage && e->variant == k->variant && e->nl == k->nl && !memcmp(e->l, k->l, k->nl) && !memcmp(e->code, code, k->size)) return id;
    }
    return 0;
}

uint64_t gl4_api_shader_create_d3d(uint64_t a) {
    const NxShaderD3D* dp = (const NxShaderD3D*)nxgl4_guest_ptr(a, sizeof *dp);
    if (!dp) return refuse("descriptor unreadable");
    const NxShaderD3D d = *dp;
    if (d.stage > 1) return refuse("stage is neither vertex (0) nor pixel (1)");
    if (d.variant > 3 || (d.stage == 1 && (d.variant & 1))) return refuse("variant: bit 0 is the vertex stage's, bit 1 either stage's, all other bits are 0");
    if (d.pad[0] || d.pad[1] || d.pad2) return refuse("reserved fields are not 0");
    if (!d.size || (d.size & 3) || d.size > MAX_CODE) return refuse("bytecode size is 0, not a multiple of 4 or above 1 MiB");
    if (d.n_link_pairs > MAX_L) return refuse("more than 31 link pairs");
    const uint8_t* bc = (const uint8_t*)nxgl4_guest_ptr(d.bytecode, d.size);
    const uint8_t* lp = d.n_link_pairs ? (const uint8_t*)nxgl4_guest_ptr(d.link_pairs, d.n_link_pairs) : NULL;
    if (!bc || (d.n_link_pairs && !lp)) return refuse("bytecode or link set unreadable");
    Gl4D3d key; memset(&key, 0, sizeof key);
    key.size = d.size; key.stage = d.stage; key.variant = d.variant; key.nl = (uint8_t)d.n_link_pairs;
    for (uint32_t i = 0; i < d.n_link_pairs; i++) {
        if (lp[i] / 16 > 13 || (i && lp[i] <= lp[i - 1])) return refuse("link set is not ascending, unique pairs of usage <= 13");
        key.l[i] = lp[i];
    }
    uint8_t* code = (uint8_t*)malloc(d.size);
    if (!code) return refuse("out of memory");
    memcpy(code, bc, d.size);
    if (fnvnx_shader_hash(code, d.size) != d.hash) { free(code); return refuse("hash is not fnvnx_shader_hash of the bytecode"); }
    uint32_t id = memo_find(&key, code);
    if (id) { free(code); g4.sh[id].d3d->refs++; return id; }
    fnvnx_shader_opts o; memset(&o, 0, sizeof o);
    o.flags = FNVNX_SHADER_NO_DKSH | FNVNX_SHADER_KEEP_GLSL | (d.stage == 0 && (d.variant & 1) ? FNVNX_SHADER_VS_COMPLETE : 0) | ((d.variant & 2) ? FNVNX_SHADER_FLAT_COLOR : 0);
    o.link_pairs = key.nl ? key.l : NULL; o.n_link_pairs = key.nl;
    fnvnx_shader_result r; memset(&r, 0, sizeof r);
    if (!fnvnx_shader_translate(code, d.size, &o, &r)) {
        gl4_err("shader_create_d3d: translation failed: %s", r.error);
        fnvnx_shader_result_free(&r); free(code); return 0;
    }
    if ((int)r.stage != (int)d.stage || !r.glsl) {
        fnvnx_shader_result_free(&r); free(code); return refuse("the version token of the bytecode names the other stage");
    }
    id = gl4_shader_from_glsl(r.glsl, strlen(r.glsl), d.stage);
    fnvnx_shader_result_free(&r);
    Gl4D3d* e = id ? (Gl4D3d*)malloc(sizeof *e) : NULL;
    if (!e) {
        if (id) { p_glDeleteShader(g4.sh[id].sh); memset(&g4.sh[id], 0, sizeof g4.sh[id]); gl4_err("shader_create_d3d: out of memory"); }
        free(code); return 0;
    }
    *e = key; e->code = code; e->refs = 1;
    g4.sh[id].d3d = e;
    return id;
}
