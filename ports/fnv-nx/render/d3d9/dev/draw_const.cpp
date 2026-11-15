// draw_const.cpp: the uniform blocks of a draw, byte-exact per render/shaders/include/fnvnx_dk_layout.h (per stage: UBO 0 = vec4 c[256] by register, UBO 1 = ivec4 i[16] at 16 * n and one uint per bool at 256 + 16 * n,
// UBO 2 = the driver block) plus the blocks of the fixed-function module (draw_ffp.h, bound to the slots it names).
//  C1 UBO 0: the float registers Set*ShaderConstantF stored (immediate `def` constants are in the shader); the pixel block has 224 registers, c224..c255 are 0 ("0 outside"). UBO 1: the int registers, the bool registers as
//     0 / 1 (BOOL != 0 is true). Both are rebuilt when the VSCONST / PSCONST group is dirty or the stream is rebound in full, and bound when their bytes differ from what the stream holds.
//  C2 VS driver block (208 bytes): posfix = (0, 0, 1, 0): xy 0 because the viewport carries the half pixel (draw_state.cpp S4: nx/state.h NxViewport, docs/RENDER_NATIVE.md "VS posfix (0, 0, 1)"), z = 1 (backend window space is y-down
//     like D3D9), w = 0 (the opaque zero); byte 16 (vs_drv_pad.x) = 0.0 when the pair's pixel stage is ps_3_0, else 1.0 (ps_1_x, ps_2_x, fixed function; README "Varying completion"); bytes 64..191 = the 8 clip planes (SetClipPlane's, raw; zero unless CLIPPING and the plane's CLIPPLANEENABLE bit are set, and 6 and 7: a zero plane has distance 0 and never clips, so the shader needs no enable word), bytes 192.. = (POINTSIZE, min, max, 0) raw: the shader's min(max(size, min), max) is max when min > max, as WineD3D 9.0 utils.c get_pointsize_minmax; the rest 0.
//  C3 PS driver block (352 bytes): fogcolor = FOGCOLOR as RGBA; fog = (mode, FOGSTART, FOGEND, FOGDENSITY) with mode 0 off / 1 vertex / 2 exp / 3 exp2 / 4 linear = the front's NxFog (None, Vertex, Exp, Exp2, Linear) of
//     nx_d3d9_shader_bits(rs, ps_version) renumbered per the shaders README (FOGENABLE == 0 or ps_3_0 = off; FOGTABLEMODE NONE = vertex fog); alpha = (D3DCMPFUNC of ALPHAFUNC when ALPHATESTENABLE else 0, 0, 0, 0); alpharef.x =
//     ALPHAREF / 255; texbem[2n] = (BUMPENVMAT00, 01, 10, 11) and texbem[2n + 1] = (BUMPENVLSCALE, BUMPENVLOFFSET, 0, 0) of stage n < 8; stype word n = the dimension of the texture bound at stage n (0 2D, 1 cube, 2 volume)
//     for a ps_1_x shader and the fixed-function pixel stage, 0 for ps_2 and later.
//  NOT carried: point scale (attenuation) of programmable vertex shaders (WineD3D 9.0 get_pointsize applies it to the fixed-function pipeline only).
#include "draw_core.h"

static float fbits(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
bool nx_ubo_reserve(uint32_t stage, uint32_t slot, uint32_t bytes) {
    NxUboCache& c = g_nxb.ubo[stage][slot]; if (c.cap >= bytes) return true;
    uint8_t* m = (uint8_t*)nxd3d_alloc(bytes); if (!m) return false;
    nxd3d_free(c.data); c.data = m; c.cap = bytes; c.ok = false; return true;
}
void nx_ubo_emit(uint32_t stage, uint32_t slot, const void* data, uint32_t bytes, bool) {
    NxUboCache& c = g_nxb.ubo[stage][slot];
    if (c.ok && c.size == bytes && !memcmp(c.data, data, bytes)) return;
    memcpy(c.data, data, bytes); c.size = bytes; c.ok = true;
    uint32_t blk, off; nxs_payload(data, bytes, &blk, &off);
    uint32_t* w = nxs_op(NxOp_BindUbo, 5); w[0] = stage ? 2u : 1u; w[1] = slot; w[2] = blk; w[3] = off; w[4] = bytes;
}
void nx_driver_blocks(NxDeviceCore* d, const NxProg& pr, uint8_t* vs, uint8_t* ps) {
    const NxDeviceState& s = d->cur; memset(vs, 0, FNVNX_DK_VS_DRIVER_SIZE); memset(ps, 0, FNVNX_DK_PS_DRIVER_SIZE);
    float* v = (float*)vs; v[2] = 1.0f; v[FNVNX_DK_VS_PAD_OFFSET / 4] = pr.ps_version == 0x0300 ? 0.0f : 1.0f;                                  // C2
    NxD3D9Input in; memset(&in, 0, sizeof in); in.rs = s.rs; in.ps_version = pr.ps_version; NxDynamicState dyn; uint32_t sb; nx_d3d9_translate(&in, nullptr, &dyn, &sb);   // dynamics only: no latch needed
    static_assert(sizeof s.clip == 96, "six planes");
    if (s.rs[D3DRS_CLIPPING]) for (uint32_t i = 0; i < 6; i++) if (s.rs[D3DRS_CLIPPLANEENABLE] >> i & 1) memcpy(vs + FNVNX_DK_VS_CLIP_OFFSET + 16 * i, &s.clip[i], 16);   // a zero plane has distance 0, never clipped
    { float* q = (float*)(vs + FNVNX_DK_VS_POINT_OFFSET); q[0] = fbits(s.rs[D3DRS_POINTSIZE]); q[1] = fbits(s.rs[D3DRS_POINTSIZE_MIN]); q[2] = fbits(s.rs[D3DRS_POINTSIZE_MAX]); }
    float* p = (float*)ps; memcpy(p, dyn.fogColor, 16);                                                                                          // C3
    uint32_t fm = (sb >> NXSB_FOG) & 7, mode = fm == NxFog_Vertex ? 1 : fm == NxFog_Exp ? 2 : fm == NxFog_Exp2 ? 3 : fm == NxFog_Linear ? 4 : 0;
    p[4] = (float)mode; p[5] = fbits(s.rs[D3DRS_FOGSTART]); p[6] = dyn.fogEnd; p[7] = dyn.fogDensity;
    ((uint32_t*)ps)[8] = (sb >> NXSB_ALPHATEST & 1) ? ((sb >> NXSB_ALPHAFUNC & 7) + 1) : 0; p[12] = dyn.alphaRef;
    for (uint32_t n = 0; n < 8; n++) {
        const uint32_t* t = s.tss[n]; float* e = p + 16 + n * 8;
        e[0] = fbits(t[D3DTSS_BUMPENVMAT00]); e[1] = fbits(t[D3DTSS_BUMPENVMAT01]); e[2] = fbits(t[D3DTSS_BUMPENVMAT10]); e[3] = fbits(t[D3DTSS_BUMPENVMAT11]);
        e[4] = fbits(t[D3DTSS_BUMPENVLSCALE]); e[5] = fbits(t[D3DTSS_BUMPENVLOFFSET]);
    }
    if (!pr.pi || pr.pi->major < 2) {
        uint32_t* st = (uint32_t*)(ps + FNVNX_DK_PS_STYPE_OFFSET);
        for (uint32_t n = 0; n < 8; n++) { NxObj* o = s.tex[n] ? nx_obj_of((IUnknown*)s.tex[n]) : nullptr; NxImg* im = o ? nx_img_of(o) : nullptr; st[n] = im && im->kind == NXK_CUBE ? FNVNX_DK_STYPE_CUBE : im && im->kind == NXK_VOLUME ? FNVNX_DK_STYPE_VOLUME : FNVNX_DK_STYPE_2D; }
    }
}
void nx_const_flush(NxDeviceCore* d, const NxProg& pr, const NxPassSet&, bool full, uint64_t groups) {
    const NxDeviceState& s = d->cur; static uint8_t f[4096], ib[512];
    if (full || (groups & NX_G_VSCONST)) {                                                                                                       // C1
        memcpy(f, s.vsf, sizeof f); nx_ubo_emit(0, FNVNX_DK_UBO_FLOAT, f, sizeof f, full);
        memcpy(ib, s.vsi, 256); for (uint32_t k = 0; k < 16; k++) { memset(ib + 256 + 16 * k, 0, 16); ((uint32_t*)(ib + 256 + 16 * k))[0] = s.vsb[k] != 0; } nx_ubo_emit(0, FNVNX_DK_UBO_INTBOOL, ib, sizeof ib, full);
    }
    if (full || (groups & NX_G_PSCONST)) {
        memset(f, 0, sizeof f); memcpy(f, s.psf, sizeof s.psf); nx_ubo_emit(1, FNVNX_DK_UBO_FLOAT, f, sizeof f, full);
        memcpy(ib, s.psi, 256); for (uint32_t k = 0; k < 16; k++) { memset(ib + 256 + 16 * k, 0, 16); ((uint32_t*)(ib + 256 + 16 * k))[0] = s.psb[k] != 0; } nx_ubo_emit(1, FNVNX_DK_UBO_INTBOOL, ib, sizeof ib, full);
    }
    uint8_t vd[FNVNX_DK_VS_DRIVER_SIZE], pd[FNVNX_DK_PS_DRIVER_SIZE]; nx_driver_blocks(d, pr, vd, pd);
    nx_ubo_emit(0, FNVNX_DK_UBO_DRIVER, vd, sizeof vd, full); nx_ubo_emit(1, FNVNX_DK_UBO_DRIVER, pd, sizeof pd, full);
    if (!pr.vi) for (uint32_t i = 0; i < pr.fv.n_ubo; i++) nx_ubo_emit(0, pr.fv.ubo[i].slot, pr.fv.ubo[i].data, pr.fv.ubo[i].bytes, full);
    if (!pr.pi) for (uint32_t i = 0; i < pr.fp.n_ubo; i++) nx_ubo_emit(1, pr.fp.ubo[i].slot, pr.fp.ubo[i].data, pr.fp.ubo[i].bytes, full);
}
