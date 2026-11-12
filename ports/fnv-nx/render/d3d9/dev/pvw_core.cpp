// pvw_core.cpp: wined3d_device_process_vertices (device.c) after its declaration FIXME, and process_vertices_strided, for the fixed-function state of the device.
// Order of events as Wine: (1) the stream info is built from the CURRENT vertex declaration with the vertex shader hidden (a bound vertex shader is ignored, no message); (2) every used element's buffer is mapped for reading at
// src_start_idx * stride (a failed map = ERR "Failed to map resource" + WINED3DERR_INVALIDCALL: d3d9 gives no MAP_R to a WRITEONLY buffer of D3DPOOL_DEFAULT, so that map is refused, before the position check); (3) no position element = ERR "Source has no position mask" + WINED3DERR_INVALIDCALL; (4) RS_CLIPPING set = FIXME "Clipping is broken and disabled for now" (the clipped
// branch is dead code in 9.0: do_clip is always FALSE, so it is not implemented); (5) the destination vertex size comes from the FVF the destination buffer was CREATED with (an FVF with no position field: FIXME "Unexpected position mask",
// size contribution 0); (6) per vertex: position (x y z w=1, world*view*projection, viewport transform, written as 4 floats whatever the FVF: XYZ advances 12, XYZRHW 16, any other position field writes nothing and advances nothing), 4 bytes
// skipped for PSIZE, the NORMAL copy (FIXME "Didn't expect the destination to have a normal"), lighting, diffuse, specular (with fog in its alpha), then per destination texture set the raw source bytes (size from the destination FVF; a missing
// source set = ERR and the bytes are skipped, not written). D3DPV_DONOTCOPYDATA (flags) is not read anywhere in Wine 9.0. The destination pointer only advances by what was written: a destination FVF whose written size differs from its vertex size
// (XYZB*, XYZW) packs vertices denser than its size, exactly as Wine.
// OUR closing rules where Wine is memory-unsafe (all return D3DERR_INVALIDCALL BEFORE any byte is written; Wine would read or write outside the buffer / use an uninitialised stream-info element):
//  M1 every byte Wine would read or write must lie inside its buffer: sources at (u32)(start*stride) + stream offset + element offset + (u32)(i*stride) (Wine's unsigned arithmetic), sizes = what Wine reads (position 12, normal 12, colours by format, texcoords
//     size*4); destination from (u32)(dst_idx*vertex_size). The one exception is the 4th float of an XYZ destination position, which Wine writes 4 bytes past the vertex (into the next vertex / padding): it is stored if it fits in the buffer, else dropped.
//  M2 the destination FVF has NORMAL but the source has no normal element: Wine copies from an uninitialised stream-info element; here INVALIDCALL.
// The 32 bit guest assumption: no address arithmetic of Wine wraps around 4 GiB inside a buffer of at most 1 GiB (the u32 products above are the only wraps modelled).
#pragma GCC optimize("fp-contract=off")
#include "pvw.h"
#include <math.h>

namespace {
struct Ctx {
    NxDeviceCore* d; const PvInfo* si; NxBufCore* dst; uint32_t count, dvf; uint64_t left; bool dry, bad; uint64_t maxend; bool fx_fmt;
    uint8_t zeros[16];
};
inline uint32_t tex_size(uint32_t fvf, uint32_t n) { return ((((fvf >> (16 + 2 * n)) + 1) & 3) + 1); }   // GET_TEXCOORD_SIZE_FROM_FVF
// bytes of vertex i of element slot `s`: a dry run only checks the range and returns zeros
const uint8_t* rd(Ctx& c, uint32_t s, uint32_t i, uint32_t n) {
    const PvElem& e = c.si->e[s]; uint64_t off = e.base + (uint64_t)(uint32_t)(i * e.stride);
    if (off + n > e.buf->length) { c.bad = true; return c.zeros; }
    return c.dry ? c.zeros : e.buf->mem + off;
}
void wr(Ctx& c, uint64_t off, const void* p, uint32_t n, bool spill = false) {
    if (off + n > c.dst->length) { if (!spill) c.bad = true; return; }
    if (c.dry) { if (off + n > c.maxend) c.maxend = off + n; } else memmove(c.dst->mem + off, p, n);
}
uint32_t q8(float v) { float t = v * 255.0f + 0.5f; return t >= 0.0f && t < 4294967296.0f ? (uint32_t)t : 0u; }   // wined3d_format_convert_from_float, B8G8R8A8_UNORM: (value * 255 + 0.5) truncated (NaN: 0)
uint32_t pack_bgra(PvCol c) { c.r = pvw_clamp(c.r, 0.0f, 1.0f); c.g = pvw_clamp(c.g, 0.0f, 1.0f); c.b = pvw_clamp(c.b, 0.0f, 1.0f); c.a = pvw_clamp(c.a, 0.0f, 1.0f); return (q8(c.r) << 16) | (q8(c.g) << 8) | q8(c.b) | (q8(c.a) << 24); }
uint32_t rd32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }
// wined3d_format_get_colour for the declaration types Wine maps to B8G8R8A8 / R8G8B8A8 UNORM, R16G16 / R16G16B16A16 UNORM, R32.._FLOAT; every other type: (0,0,0,1) + FIXME
void get_colour(Ctx& c, uint32_t slot, uint32_t i, PvCol* o) {
    uint32_t t = c.si->e[slot].type; PvCol def = {0.0f, 0.0f, 0.0f, 1.0f};
    if (t == D3DDECLTYPE_D3DCOLOR) { uint32_t v = rd32(rd(c, slot, i, 4)); o->r = ((v >> 16) & 0xff) / 255.0f; o->g = ((v >> 8) & 0xff) / 255.0f; o->b = (v & 0xff) / 255.0f; o->a = ((v >> 24) & 0xff) / 255.0f; }
    else if (t == D3DDECLTYPE_UBYTE4N) { uint32_t v = rd32(rd(c, slot, i, 4)); o->r = (v & 0xff) / 255.0f; o->g = ((v >> 8) & 0xff) / 255.0f; o->b = ((v >> 16) & 0xff) / 255.0f; o->a = ((v >> 24) & 0xff) / 255.0f; }
    else if (t == D3DDECLTYPE_USHORT2N || t == D3DDECLTYPE_USHORT4N || t <= D3DDECLTYPE_FLOAT4) {
        bool f = t <= D3DDECLTYPE_FLOAT4; uint32_t n = f ? t + 1 : t == D3DDECLTYPE_USHORT2N ? 2 : 4, sz = f ? 4 : 2; const uint8_t* p = rd(c, slot, i, n * sz); float v[4] = {def.r, def.g, def.b, def.a};
        for (uint32_t k = 0; k < n; k++) { if (f) memcpy(&v[k], p + 4 * k, 4); else { uint16_t u; memcpy(&u, p + 2 * k, 2); v[k] = u / 65535.0f; } }
        o->r = v[0]; o->g = v[1]; o->b = v[2]; o->a = v[3];
    } else { *o = def; if (!c.dry && !c.fx_fmt) { c.fx_fmt = true; fnvnx_wine_fixme(NXW_wined3d_format_get_colour); } }
}
// wined3d_colour_from_mcs: mcs 0 material, 1 COLOR1 (diffuse element, absent = white), 2 COLOR2 (specular element, absent = 0)
void from_mcs(Ctx& c, uint32_t mcs, const D3DCOLORVALUE* mat, uint32_t i, PvCol* o) {
    if (mcs == 0) { o->r = mat->r; o->g = mat->g; o->b = mat->b; o->a = mat->a; return; }
    uint32_t slot = mcs == 1 ? PVW_FFP_DIFFUSE : PVW_FFP_SPECULAR;
    if (!(c.si->use_map >> slot & 1)) { o->r = o->g = o->b = o->a = mcs == 1 ? 1.0f : 0.0f; return; }
    get_colour(c, slot, i, o);
}
uint32_t mcs_valid(uint32_t use_map, uint32_t src) { return (src == 1 && (use_map >> PVW_FFP_DIFFUSE & 1)) || (src == 2 && (use_map >> PVW_FFP_SPECULAR & 1)) ? src : 0; }   // validate_material_colour_source
bool pass(Ctx& c) {
    NxDeviceCore* d = c.d; const NxDeviceState& s = d->cur; const PvInfo& si = *c.si; uint32_t fvf = c.dvf, pos = fvf & 0x400e, ntex = (fvf & 0x0f00) >> 8;
    PvMat view, proj, world, mat; memcpy(&view, &s.xf[nx_dxf(D3DTS_VIEW)], sizeof view); memcpy(&proj, &s.xf[nx_dxf(D3DTS_PROJECTION)], sizeof proj); memcpy(&world, &s.xf[nx_dxf(D3DTS_WORLD)], sizeof world);
    const D3DVIEWPORT9& v = s.viewport[0]; float vx = (float)v.X, vy = (float)v.Y, vw = (float)v.Width, vh = (float)v.Height, min_z = v.MinZ, max_z = v.MaxZ > v.MinZ + 0.001f ? v.MaxZ : v.MinZ + 0.001f;   // wined3d_viewport_get_z_range
    pvw_mul(&mat, &view, &world); pvw_mul(&mat, &proj, &mat);
    bool lighting = s.rs[D3DRS_LIGHTING] && (fvf & (D3DFVF_DIFFUSE | D3DFVF_SPECULAR)); PvLights L; pvw_lights_init(&L, d, lighting, !c.dry);
    uint32_t dmcs, emcs, amcs, smcs;                                                           // wined3d_get_material_colour_source
    if (!s.rs[D3DRS_LIGHTING]) { dmcs = 1; smcs = 2; emcs = amcs = 0; }
    else if (!s.rs[D3DRS_COLORVERTEX]) dmcs = emcs = amcs = smcs = 0;
    else { dmcs = mcs_valid(si.use_map, s.rs[D3DRS_DIFFUSEMATERIALSOURCE]); emcs = mcs_valid(si.use_map, s.rs[D3DRS_EMISSIVEMATERIALSOURCE]); amcs = mcs_valid(si.use_map, s.rs[D3DRS_AMBIENTMATERIALSOURCE]); smcs = mcs_valid(si.use_map, s.rs[D3DRS_SPECULARMATERIALSOURCE]); }
    const D3DMATERIAL9& M = s.material[0]; static const D3DCOLORVALUE black = {0, 0, 0, 0}; const D3DCOLORVALUE* mspec = s.rs[D3DRS_SPECULARENABLE] ? &M.Specular : &black;
    uint64_t dp = c.left; bool fx_normal = false;
    for (uint32_t i = 0; i < c.count; i++) {
        if (c.dry && c.bad) break;                                                              // the validation pass has its verdict (INVALIDCALL): the rest of the range cannot change it (count may be 2^32 - 1)
        float p[3]; memcpy(p, rd(c, PVW_FFP_POSITION, i, 12), 12); PvVec4 position = {p[0], p[1], p[2], 1.0f};
        pvw_light_vertex(&L, &position);
        if ((pos == D3DFVF_XYZ) || (pos == D3DFVF_XYZRHW)) {
            float x = (p[0] * mat.m[0][0]) + (p[1] * mat.m[1][0]) + (p[2] * mat.m[2][0]) + mat.m[3][0];
            float y = (p[0] * mat.m[0][1]) + (p[1] * mat.m[1][1]) + (p[2] * mat.m[2][1]) + mat.m[3][1];
            float z = (p[0] * mat.m[0][2]) + (p[1] * mat.m[1][2]) + (p[2] * mat.m[2][2]) + mat.m[3][2];
            float rhw = (p[0] * mat.m[0][3]) + (p[1] * mat.m[1][3]) + (p[2] * mat.m[2][3]) + mat.m[3][3];
            x /= rhw; y /= rhw; z /= rhw; y *= -1; x *= vw / 2; y *= vh / 2; z *= max_z - min_z; x += vw / 2 + vx; y += vh / 2 + vy; z += min_z; rhw = 1 / rhw;   // no clipping: the viewport transform for every vertex
            float o[4] = {x, y, z, rhw}; wr(c, dp, o, 12); wr(c, dp + 12, &o[3], 4, pos == D3DFVF_XYZ);   // the 4th float is written for XYZ too (Wine writes 4 floats)
            dp += pos == D3DFVF_XYZRHW ? 16 : 12;
        }
        if (fvf & D3DFVF_PSIZE) dp += 4;
        if (fvf & D3DFVF_NORMAL) {
            if (!(si.use_map >> PVW_FFP_NORMAL & 1)) { c.bad = true; return false; }          // M2
            if (!c.dry && !fx_normal) { fx_normal = true; fnvnx_wine_fixme(NXW_process_vertices_strided); }
            wr(c, dp, rd(c, PVW_FFP_NORMAL, i, 12), 12); dp += 12;
        }
        PvCol amb, dif, spc;
        if (lighting) {
            PvVec3 nv, *np = nullptr;
            if (si.use_map >> PVW_FFP_NORMAL & 1) { memcpy(&nv, rd(c, PVW_FFP_NORMAL, i, 12), 12); np = &nv; }
            float shin; if (s.rs[D3DRS_SPECULARENABLE]) shin = M.Power; else shin = 0.0f;
            pvw_compute_light(&amb, &dif, &spc, &L, np, shin);
        }
        if (fvf & D3DFVF_DIFFUSE) {
            PvCol md, mam, mem, dc; from_mcs(c, dmcs, &M.Diffuse, i, &md);
            if (lighting) {
                from_mcs(c, amcs, &M.Ambient, i, &mam); from_mcs(c, emcs, &M.Emissive, i, &mem);
                dc.r = amb.r * mam.r + dif.r * md.r + mem.r; dc.g = amb.g * mam.g + dif.g * md.g + mem.g; dc.b = amb.b * mam.b + dif.b * md.b + mem.b; dc.a = md.a;
            } else dc = md;
            uint32_t w = pack_bgra(dc); wr(c, dp, &w, 4); dp += 4;
        }
        if (fvf & D3DFVF_SPECULAR) {
            PvCol ms, sc; from_mcs(c, smcs, mspec, i, &ms);
            if (lighting) { sc.r = spc.r * ms.r; sc.g = spc.g * ms.g; sc.b = spc.b * ms.b; sc.a = ms.a; } else sc = ms;   // legacy lighting is off (d3d9): alpha = material alpha
            sc.a = pvw_fog_factor(&L, sc.a);
            uint32_t w = pack_bgra(sc); wr(c, dp, &w, 4); dp += 4;
        }
        for (uint32_t t = 0; t < ntex; t++) {
            uint32_t n = tex_size(fvf, t) * 4;
            if (si.use_map >> (PVW_FFP_TEXCOORD0 + t) & 1) wr(c, dp, rd(c, PVW_FFP_TEXCOORD0 + t, i, n), n);   // else: Wine's ERR "No source texture" and the bytes are skipped
            dp += n;
        }
    }
    return !c.bad;
}
}   // namespace

HRESULT pvw_process(NxDeviceCore* d, UINT start, UINT dst_idx, UINT count, NxBufCore* dst) {
    PvInfo si; pvw_stream_info(&si, d, start);
    for (uint32_t k = 0; k < PVW_FFP_COUNT; k++)                                           // wined3d_device_context_map(READ) of each source: d3d9 gives MAP_R except for WRITEONLY buffers of D3DPOOL_DEFAULT -> E_INVALIDARG, ERR, WINED3DERR_INVALIDCALL
        if ((si.use_map >> k & 1) && si.e[k].buf && (si.e[k].buf->usage & D3DUSAGE_WRITEONLY) && si.e[k].buf->pool == (uint8_t)D3DPOOL_DEFAULT) return D3DERR_INVALIDCALL;
    if (!(si.use_map >> PVW_FFP_POSITION & 1)) return D3DERR_INVALIDCALL;                      // ERR "Source has no position mask"
    if (d->cur.rs[D3DRS_CLIPPING]) fnvnx_wine_fixme(NXW_wined3d_device_process_vertices);
    uint32_t fvf = dst->fmt, ntex = (fvf & 0x0f00) >> 8, vs = 0, pm = fvf & 0x400e;            // wined3d_get_flexible_vertex_size
    vs += (fvf & D3DFVF_NORMAL ? 12 : 0) + (fvf & D3DFVF_DIFFUSE ? 4 : 0) + (fvf & D3DFVF_SPECULAR ? 4 : 0) + (fvf & D3DFVF_PSIZE ? 4 : 0);
    switch (pm) { case D3DFVF_XYZ: vs += 12; break; case D3DFVF_XYZRHW: case D3DFVF_XYZB1: case D3DFVF_XYZW: vs += 16; break; case D3DFVF_XYZB2: vs += 20; break; case D3DFVF_XYZB3: vs += 24; break;
              case D3DFVF_XYZB4: vs += 28; break; case D3DFVF_XYZB5: vs += 32; break; default: fnvnx_wine_fixme(NXW_wined3d_get_flexible_vertex_size); }
    for (uint32_t i = 0; i < ntex; i++) vs += tex_size(fvf, i) * 4;
    Ctx c; memset((void*)&c, 0, sizeof c); c.d = d; c.si = &si; c.dst = dst; c.count = count; c.dvf = fvf; c.left = (uint32_t)(dst_idx * vs); c.dry = true; c.maxend = c.left;
    if (!pass(c)) return D3DERR_INVALIDCALL;
    uint64_t end = c.maxend; c.dry = false; c.maxend = 0; c.bad = false;
    pass(c);
    if (end > c.left) { void* p; HRESULT hr = dst->lock((uint32_t)c.left, (uint32_t)(end - c.left), &p, 0); if (hr == D3D_OK) dst->unlock(); }   // the write map of the destination (dirty range for the GPU mirror)
    return D3D_OK;
}
