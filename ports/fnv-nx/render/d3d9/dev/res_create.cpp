// Device Create* of textures, volume textures, cube textures, render targets, depth-stencil and offscreen plain surfaces. Sources: Wine 9.0 d3d9/device.c (d3d9_device_Create{Texture,VolumeTexture,CubeTexture,
// RenderTarget,DepthStencilSurface,OffscreenPlainSurface}{,Ex}, d3d9_device_create_surface: WINED3DERR_NOTAVAILABLE -> D3DERR_INVALIDCALL), d3d9/texture.c d3d9_texture_init, wined3d/texture.c wined3d_texture_create /
// wined3d_texture_init, wined3d/resource.c resource_init. Format and multisample legality come ONLY from the generated env table (env_table.gen.h): the wined3d format-cap loop of resource_init is
//  texture bind (SHADER_RESOURCE) = CheckDeviceFormat(usage 0, TEXTURE / CUBETEXTURE / VOLUMETEXTURE), render target = (usage RT), depth = (usage DS) of the same resource class; SURFACE class for surfaces.
// Order of checks = Wine's. Pool SCRATCH skips the cap loop (resource_init). AUTOGENMIPMAP: levels exposed 1; the GPU chain is the full chain iff CheckDeviceFormat(GENMIPMAP) == D3D_OK (d3d9_texture_init).
// DECISIONS (doubt): levels > 32 = D3DERR_INVALIDCALL (no chain is longer; Wine accepts any); an image above NX_MAX_TEX_BYTES = OUT-OF-MEMORY (GPU pools D3DERR_OUTOFVIDEOMEMORY, others E_OUTOFMEMORY); a single GPU-pool resource
// above the env VideoMemorySize = OUTOFVIDEOMEMORY (non-Ex; no cross-resource accounting, the device has none); Lockable RT with a multisample type = INVALIDCALL (MS CreateRenderTarget: only non-multisampled targets are lockable);
// CreateOffscreenPlainSurfaceEx is NOT implemented (Wine returns E_NOTIMPL, no source for the Windows result). Null output pointer = INVALIDCALL (Windows faults).
#include "res_tex.h"
#include "d9impl.gen.h"
#include "env_table.gen.h"
#include <string.h>
#define MAXU64 ((uint64_t)NX_MAX_TEX_BYTES)
static bool is_ex(NxObj* dev) { return strcmp(dev->ci->name, "IDirect3DDevice9Ex") == 0; }
static const uint32_t X8 = 22, HRNOMIP = 0x0876086Fu;
static bool cdf(int p, uint32_t usage, uint32_t rtype, uint32_t fmt) { return nxenv_check_device_format(p, 0, 1, X8, usage, rtype, fmt) == 0; }
static uint32_t dimu(uint32_t v, uint32_t l) { v = l > 31 ? 0 : v >> l; return v ? v : 1; }
static bool fits(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t d, uint32_t lv, uint32_t faces) {
    uint64_t tot = 0;
    for (uint32_t l = 0; l < lv; l++) {
        uint64_t lw = dimu(w, l), lh = dimu(h, l), ld = dimu(d, l), row = (f.flags & NXTF_BLOCKS) ? (lw + f.bw - 1) / f.bw * f.bb : lw * f.bytes, rows = (f.flags & NXTF_BLOCKS) ? (lh + f.bh - 1) / f.bh : lh;
        if (row > MAXU64 || rows > MAXU64 || row * rows > MAXU64 || row * rows * ld > MAXU64) return false;
        tot += (row * rows * ld * (f.hn ? f.hn : 1) / (f.hd ? f.hd : 1) + 15) & ~15ull; if (tot * faces > MAXU64) return false;
    }
    return true;
}
// multisample legality (wined3d_texture_create against the env table: nxenv_check_msaa); samples = the sample count handed to the backend
static bool ms_ok(int p, uint32_t fmt, uint32_t type, uint32_t q, uint32_t* samples) {
    *samples = 1; if (type == 0) return true;
    uint32_t lv = 0;
    if (type == 1) {                                                   // NONMASKABLE: quality indexes the supported types
        if (nxenv_check_msaa(p, 0, 1, fmt, 0, 1, &lv) != 0 || q >= lv) return false;
        for (uint32_t t = 2, n = 0; t <= 16; t++) if (nxenv_check_msaa(p, 0, 1, fmt, 0, t, nullptr) == 0 && n++ == q) { *samples = t; return true; }
        return false;
    }
    if (type > 16 || nxenv_check_msaa(p, 0, 1, fmt, 0, type, nullptr) != 0 || (q && q != 0xFFFFFFFFu)) return false;
    *samples = type; return true;
}
template <class T, class K> static T* build(NxObj* dev, NxImg* img, uint32_t pool, uint32_t n) {
    T* t = new T; t->img = img; t->asChild(dev, pool); NxTexCore* c = t; c->kids = (NxObj**)nxd3d_alloc(sizeof(NxObj*) * n); c->nkids = n; img->owner = t;
    for (uint32_t i = 0; i < n; i++) { K* k = new K; k->img = img; k->level = i % img->levels; NxObj* ko = k; ko->asSubResource(dev, t); c->kids[i] = k; }
    return t;
}
static void fix_faces(NxTexCore* t) { if (t->img->kind != NXK_VOLUME) for (uint32_t i = 0; i < t->nkids; i++) static_cast<NxSurfCore*>(t->kids[i])->face = i / t->img->levels; }
static HRESULT mk_tex(NxObj* dev, uint32_t kind, UINT w, UINT h, UINT d, UINT levels, DWORD usage, D3DFORMAT fmt, D3DPOOL pool, void** out, HANDLE* sh) {
    if (!out) return D3DERR_INVALIDCALL;
    *out = nullptr; void* um = nullptr; bool ex = is_ex(dev);
    if (sh) {
        if (!ex) return E_NOTIMPL;
        if (kind == NXK_TEXTURE && pool == D3DPOOL_SYSTEMMEM) { if (levels != 1) return D3DERR_INVALIDCALL; um = *sh; }
        else if (pool != D3DPOOL_DEFAULT) return D3DERR_INVALIDCALL;
    }
    if ((uint32_t)pool > (uint32_t)D3DPOOL_SCRATCH || (pool == D3DPOOL_MANAGED && ex) || (usage & D3DUSAGE_WRITEONLY)) return D3DERR_INVALIDCALL;
    if (kind == NXK_VOLUME && (usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL))) return D3DERR_INVALIDCALL;
    int p = nx_env_profile_of(dev); uint32_t rt = kind == NXK_TEXTURE ? 3 : kind == NXK_CUBE ? 5 : 4; bool ag = false;
    if (usage & D3DUSAGE_AUTOGENMIPMAP) {
        if (kind == NXK_VOLUME || pool == D3DPOOL_SYSTEMMEM || levels > 1) return D3DERR_INVALIDCALL;
        ag = nxenv_check_device_format(p, 0, 1, 21, 0x400, 3, fmt) == 0; levels = 1;                 // WINED3DUSAGE_QUERY_GENMIPMAP on B8G8R8A8 adapter format; D3D_OK only (not NOAUTOGEN)
    }
    if (!w || !h || !d || fmt == D3DFMT_UNKNOWN) return D3DERR_INVALIDCALL;
    if ((usage & D3DUSAGE_DYNAMIC) && (pool == D3DPOOL_MANAGED || pool == D3DPOOL_SCRATCH)) return D3DERR_INVALIDCALL;
    D3DCAPS9 caps; nxenv_caps(p, 0, 1, &caps);
    if ((pool == D3DPOOL_DEFAULT || pool == D3DPOOL_MANAGED) && (w > caps.MaxTextureWidth || h > caps.MaxTextureHeight)) return D3DERR_NOTAVAILABLE;
    NxTexFmt f; if (!nx_texfmt(fmt, &f) || levels > 32) return D3DERR_INVALIDCALL;
    uint32_t bind = usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL);
    if (bind && (pool != D3DPOOL_DEFAULT || (usage & D3DUSAGE_DYNAMIC))) return D3DERR_INVALIDCALL;
    if (pool != D3DPOOL_SCRATCH && (!cdf(p, 0, rt, fmt) || ((bind & 1) && !cdf(p, 1, rt, fmt)) || ((bind & 2) && !cdf(p, 2, rt, fmt)))) return D3DERR_INVALIDCALL;
    if ((f.flags & (NXTF_BLOCKS | NXTF_NOVERIFY)) == NXTF_BLOCKS && ((w & (f.bw - 1)) || (h & (f.bh - 1)))) return D3DERR_INVALIDCALL;
    uint32_t chain = 1, mx = w > h ? w : h; if (d > mx) mx = d; while (mx >>= 1) chain++;
    uint32_t lv = ag ? 1 : (levels ? levels : chain), faces = kind == NXK_CUBE ? 6 : 1;
    bool gpu = pool == D3DPOOL_DEFAULT || pool == D3DPOOL_MANAGED;
    if (!fits(f, w, h, d, lv, faces)) return gpu ? D3DERR_OUTOFVIDEOMEMORY : E_OUTOFMEMORY;
    if (gpu && !ex && nx_tex_size(f, w, h, d) > nxenv_video_memory(p)) return D3DERR_OUTOFVIDEOMEMORY;
    uint32_t acc = (pool != D3DPOOL_DEFAULT || (usage & D3DUSAGE_DYNAMIC) || (f.flags & NXTF_MAPPABLE)) ? 12 : 0;
    NxImg* img = new NxImg; memset((void*)img, 0, 0); NxTexCore* t;
    if (kind == NXK_VOLUME) { NxVolumeTexture9* v = build<NxVolumeTexture9, NxVolume9>(dev, img, pool, lv); t = v; *out = v; }
    else if (kind == NXK_CUBE) { NxCubeTexture9* v = build<NxCubeTexture9, NxSurface9>(dev, img, pool, lv * 6); t = v; *out = v; }
    else { NxTexture9* v = build<NxTexture9, NxSurface9>(dev, img, pool, lv); t = v; *out = v; }
    img->init(t, kind, w, h, d, lv, faces, fmt, usage, pool, acc, ag, um); fix_faces(t); return D3D_OK;
}
