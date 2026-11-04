/* V6n ORACLE: v6n_target.h on Gallium Nine + softpipe (S6c's host Nine: render/nine/host/ + gate/gate_winsys.c).
 * Every call is a plain IDirect3DDevice9 call; the colour target is read back with GetRenderTargetData + LockRect.
 * Depth/stencil have no lockable D3D9 format with stencil, so they are read from the surface's pipe resource
 * after Nine's CSMT queue has drained and the softpipe context has been flushed (host tool only). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nine_host.h"
#include "device9.h"
#include "surface9.h"
#include "nine_state.h"
#include "pipe/p_context.h"
#include "util/u_inlines.h"
#include "util/format/u_format.h"
#include "v6n_target.h"

struct V6nTarget {
    IDirect3D9* d3d; IDirect3DDevice9* dev; IDirect3DSurface9* rt; IDirect3DSurface9* ds;
    IDirect3DTexture9* tex[8];
    int w, h; uint32_t rtf, dsf;
    V6nReadback rb;
};

static V6nTarget* n_begin(int w, int h, uint32_t rtf, uint32_t dsf, char* err, int errlen) {
    V6nTarget* t = calloc(1, sizeof *t);
    t->w = w; t->h = h; t->rtf = rtf; t->dsf = dsf;
    t->d3d = nx_direct3d_create9(32);
    if (!t->d3d) { snprintf(err, errlen, "nx_direct3d_create9 failed"); free(t); return NULL; }
    D3DPRESENT_PARAMETERS pp; memset(&pp, 0, sizeof pp);
    pp.BackBufferWidth = w; pp.BackBufferHeight = h; pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.Windowed = TRUE; pp.hDeviceWindow = (HWND)(uintptr_t)0x1000;
    HRESULT hr = IDirect3D9_CreateDevice(t->d3d, 0, D3DDEVTYPE_HAL, pp.hDeviceWindow,
                                         D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, &t->dev);
    if (FAILED(hr)) { snprintf(err, errlen, "CreateDevice 0x%x", (unsigned)hr); return NULL; }
    hr = IDirect3DDevice9_CreateRenderTarget(t->dev, w, h, (D3DFORMAT)rtf, D3DMULTISAMPLE_NONE, 0, FALSE, &t->rt, NULL);
    if (FAILED(hr)) { snprintf(err, errlen, "CreateRenderTarget fmt %u: 0x%x", rtf, (unsigned)hr); return NULL; }
    IDirect3DDevice9_SetRenderTarget(t->dev, 0, t->rt);
    if (dsf) {
        hr = IDirect3DDevice9_CreateDepthStencilSurface(t->dev, w, h, (D3DFORMAT)dsf, D3DMULTISAMPLE_NONE, 0, FALSE, &t->ds, NULL);
        if (FAILED(hr)) { snprintf(err, errlen, "CreateDepthStencilSurface fmt %u: 0x%x", dsf, (unsigned)hr); return NULL; }
        IDirect3DDevice9_SetDepthStencilSurface(t->dev, t->ds);
    } else IDirect3DDevice9_SetDepthStencilSurface(t->dev, NULL);
    D3DVIEWPORT9 vp = {0, 0, (DWORD)w, (DWORD)h, 0.0f, 1.0f};
    IDirect3DDevice9_SetViewport(t->dev, &vp);
    IDirect3DDevice9_SetFVF(t->dev, D3DFVF_XYZRHW | D3DFVF_DIFFUSE | D3DFVF_SPECULAR | D3DFVF_TEX1);
    IDirect3DDevice9_BeginScene(t->dev);
    return t;
}
static void n_end(V6nTarget* t) {
    IDirect3DDevice9_EndScene(t->dev);
    for (int i = 0; i < 8; i++) if (t->tex[i]) IDirect3DTexture9_Release(t->tex[i]);
    if (t->ds) IDirect3DSurface9_Release(t->ds);
    if (t->rt) IDirect3DSurface9_Release(t->rt);
    IDirect3DDevice9_Release(t->dev);
    IDirect3D9_Release(t->d3d);
    free(t->rb.color); free(t->rb.depth); free(t->rb.stencil);
    free(t);
}
static void n_clear(V6nTarget* t, uint32_t flags, uint32_t color, float z, uint32_t s) {
    IDirect3DDevice9_Clear(t->dev, 0, NULL, flags, color, z, s);
}
static void n_rs(V6nTarget* t, uint32_t st, uint32_t v) { IDirect3DDevice9_SetRenderState(t->dev, (D3DRENDERSTATETYPE)st, v); }
static void n_samp(V6nTarget* t, uint32_t s, uint32_t ty, uint32_t v) { IDirect3DDevice9_SetSamplerState(t->dev, s, (D3DSAMPLERSTATETYPE)ty, v); }
static void n_tss(V6nTarget* t, uint32_t s, uint32_t ty, uint32_t v) { IDirect3DDevice9_SetTextureStageState(t->dev, s, (D3DTEXTURESTAGESTATETYPE)ty, v); }
int v6n_level_bytes(uint32_t fmt, int w, int h);
static int is_block(uint32_t f) { return f > 0xFFFF; }
static int n_tex(V6nTarget* t, uint32_t stage, uint32_t fmt, int w, int h, int levels, const uint8_t* const* data) {
    IDirect3DTexture9* tex = NULL;
    HRESULT hr = IDirect3DDevice9_CreateTexture(t->dev, w, h, levels, 0, (D3DFORMAT)fmt, D3DPOOL_MANAGED, &tex, NULL);
    if (FAILED(hr)) return 0;
    for (int l = 0; l < levels; l++) {
        int lw = w >> l > 0 ? w >> l : 1, lh = h >> l > 0 ? h >> l : 1;
        int bytes = v6n_level_bytes(fmt, lw, lh);
        int rows = is_block(fmt) ? (lh + 3) / 4 : lh;
        int rowbytes = bytes / rows;
        D3DLOCKED_RECT lr;
        if (FAILED(IDirect3DTexture9_LockRect(tex, l, &lr, NULL, 0))) { IDirect3DTexture9_Release(tex); return 0; }
        for (int r = 0; r < rows; r++) memcpy((uint8_t*)lr.pBits + (size_t)r * lr.Pitch, data[l] + (size_t)r * rowbytes, rowbytes);
        IDirect3DTexture9_UnlockRect(tex, l);
    }
    if (t->tex[stage & 7]) IDirect3DTexture9_Release(t->tex[stage & 7]);
    t->tex[stage & 7] = tex;
    IDirect3DDevice9_SetTexture(t->dev, stage, (IDirect3DBaseTexture9*)tex);
    return 1;
}
static void n_notex(V6nTarget* t, uint32_t stage) { IDirect3DDevice9_SetTexture(t->dev, stage, NULL); }
static void n_vp(V6nTarget* t, uint32_t x, uint32_t y, uint32_t w, uint32_t h, float mn, float mx) {
    D3DVIEWPORT9 vp = {x, y, w, h, mn, mx}; IDirect3DDevice9_SetViewport(t->dev, &vp);
}
static void n_scissor(V6nTarget* t, int32_t l, int32_t tp, int32_t r, int32_t b) {
    RECT rc = {l, tp, r, b}; IDirect3DDevice9_SetScissorRect(t->dev, &rc);
}
static void n_proj(V6nTarget* t, const float m[16]) {
    D3DMATRIX mm; memcpy(&mm, m, sizeof mm); IDirect3DDevice9_SetTransform(t->dev, D3DTS_PROJECTION, &mm);
}
static int n_draw(V6nTarget* t, uint32_t prim, uint32_t count, int rhw, const V6nVertex* v, int nv) {
    HRESULT hr;
    if (rhw) {
        IDirect3DDevice9_SetFVF(t->dev, D3DFVF_XYZRHW | D3DFVF_DIFFUSE | D3DFVF_SPECULAR | D3DFVF_TEX1);
        hr = IDirect3DDevice9_DrawPrimitiveUP(t->dev, (D3DPRIMITIVETYPE)prim, count, v, sizeof *v);
    } else {
        struct { float x, y, z; uint32_t d, s; float u, v; } *p = malloc(sizeof *p * (nv ? nv : 1));
        for (int i = 0; i < nv; i++) { p[i].x = v[i].x; p[i].y = v[i].y; p[i].z = v[i].z; p[i].d = v[i].diffuse; p[i].s = v[i].specular; p[i].u = v[i].u; p[i].v = v[i].v; }
        IDirect3DDevice9_SetFVF(t->dev, D3DFVF_XYZ | D3DFVF_DIFFUSE | D3DFVF_SPECULAR | D3DFVF_TEX1);
        hr = IDirect3DDevice9_DrawPrimitiveUP(t->dev, (D3DPRIMITIVETYPE)prim, count, p, sizeof *p);
        free(p);
    }
    return SUCCEEDED(hr);
}
static float h2f(uint16_t h) {
    uint32_t s = (h >> 15) & 1, e = (h >> 10) & 31, m = h & 1023, u;
    if (e == 0) { float f = (float)m / 1024.0f / 16384.0f; return s ? -f : f; }
    if (e == 31) u = s << 31 | 0x7f800000 | m << 13; else u = s << 31 | (e + 112) << 23 | m << 13;
    float f; memcpy(&f, &u, 4); return f;
}
static int n_readback(V6nTarget* t, V6nReadback* out) {
    int w = t->w, h = t->h;
    IDirect3DSurface9* sys = NULL;
    if (FAILED(IDirect3DDevice9_CreateOffscreenPlainSurface(t->dev, w, h, (D3DFORMAT)t->rtf, D3DPOOL_SYSTEMMEM, &sys, NULL))) return 0;
    if (FAILED(IDirect3DDevice9_GetRenderTargetData(t->dev, t->rt, sys))) return 0;
    D3DLOCKED_RECT lr;
    if (FAILED(IDirect3DSurface9_LockRect(sys, &lr, NULL, D3DLOCK_READONLY))) return 0;
    V6nReadback* r = &t->rb;
    r->w = w; r->h = h; r->color = calloc((size_t)w * h * 4, sizeof(float)); r->color_channels = 4; r->color_is_float = 0;
    for (int y = 0; y < h; y++) {
        const uint8_t* row = (const uint8_t*)lr.pBits + (size_t)y * lr.Pitch;
        for (int x = 0; x < w; x++) {
            float* c = r->color + ((size_t)y * w + x) * 4;
            switch (t->rtf) {
            case D3DFMT_A8R8G8B8: case D3DFMT_X8R8G8B8:
                c[0] = row[4 * x + 2]; c[1] = row[4 * x + 1]; c[2] = row[4 * x + 0]; c[3] = row[4 * x + 3];
                if (t->rtf == D3DFMT_X8R8G8B8) { c[3] = 0; r->color_channels = 3; }
                break;
            case D3DFMT_A16B16G16R16F: {
                const uint16_t* p = (const uint16_t*)row + 4 * x;
                for (int k = 0; k < 4; k++) c[k] = h2f(p[k]);
                r->color_is_float = 1; break; }
            case D3DFMT_A32B32G32R32F:
                memcpy(c, (const float*)row + 4 * x, 16); r->color_is_float = 1; break;
            case D3DFMT_R5G6B5: {
                uint16_t p; memcpy(&p, row + 2 * x, 2);
                c[0] = p >> 11; c[1] = (p >> 5) & 63; c[2] = p & 31; c[3] = 0; r->color_channels = 3; break; }
            default:
                IDirect3DSurface9_UnlockRect(sys); IDirect3DSurface9_Release(sys); return 0;
            }
        }
    }
    IDirect3DSurface9_UnlockRect(sys);
    IDirect3DSurface9_Release(sys);
    if (t->ds) {
        struct NineDevice9* dev = (struct NineDevice9*)t->dev;
        struct pipe_resource* res = ((struct NineSurface9*)t->ds)->base.resource;
        struct pipe_context* pipe = nine_context_get_pipe_acquire(dev);
        pipe->flush(pipe, NULL, 0);
        struct pipe_transfer* xf = NULL;
        const uint8_t* map = pipe_transfer_map(pipe, res, 0, 0, PIPE_TRANSFER_READ, 0, 0, w, h, &xf);
        if (map) {
            const struct util_format_description* d = util_format_description(res->format);
            r->depth = calloc((size_t)w * h, 4);
            int has_s = util_format_has_stencil(d);
            if (has_s) r->stencil = calloc((size_t)w * h, 1);
            for (int y = 0; y < h; y++) {
                const uint8_t* row = map + (size_t)y * xf->stride;
                if (res->format == PIPE_FORMAT_Z32_FLOAT || res->format == PIPE_FORMAT_Z32_FLOAT_S8X24_UINT) {
                    float zf[4096]; util_format_unpack_z_float(res->format, zf, row, w);
                    memcpy(r->depth + (size_t)y * w, zf, 4 * w);
                } else {
                    uint32_t z32[4096]; util_format_unpack_z_32unorm(res->format, z32, row, w);
                    int bits = d->block.bits == 16 ? 16 : 24;
                    for (int x = 0; x < w; x++) r->depth[(size_t)y * w + x] = z32[x] >> (32 - bits);
                }
                if (has_s) util_format_unpack_s_8uint(res->format, r->stencil + (size_t)y * w, row, w);
            }
            pipe->transfer_unmap(pipe, xf);
        }
        nine_context_get_pipe_release(dev);
    }
    *out = *r;
    return 1;
}
static int n_ps(V6nTarget* t, const uint32_t* bc, int n, const char* glsl) {
    (void)n; (void)glsl;
    IDirect3DPixelShader9* ps = NULL;
    if (bc && FAILED(IDirect3DDevice9_CreatePixelShader(t->dev, (const DWORD*)bc, &ps))) return 0;
    IDirect3DDevice9_SetPixelShader(t->dev, ps);
    if (ps) IDirect3DPixelShader9_Release(ps);   /* the device keeps its reference */
    return 1;
}
static void n_psc(V6nTarget* t, uint32_t reg, const float v[4]) { IDirect3DDevice9_SetPixelShaderConstantF(t->dev, reg, v, 1); }
static int n_vsh(V6nTarget* t, const uint32_t* bc, int n, const char* glsl) {
    (void)n; (void)glsl;
    IDirect3DVertexShader9* vs = NULL;
    if (bc && FAILED(IDirect3DDevice9_CreateVertexShader(t->dev, (const DWORD*)bc, &vs))) return 0;
    IDirect3DDevice9_SetVertexShader(t->dev, vs);
    if (vs) IDirect3DVertexShader9_Release(vs);
    return 1;
}
static void n_vsc(V6nTarget* t, uint32_t reg, const float v[4]) { IDirect3DDevice9_SetVertexShaderConstantF(t->dev, reg, v, 1); }
static int n_drawidx(V6nTarget* t, uint32_t prim, uint32_t count, int rhw, const V6nVertex* v, int nv, const uint16_t* idx, int ni) {
    (void)ni;
    HRESULT hr;
    if (rhw) {
        IDirect3DDevice9_SetFVF(t->dev, D3DFVF_XYZRHW | D3DFVF_DIFFUSE | D3DFVF_SPECULAR | D3DFVF_TEX1);
        hr = IDirect3DDevice9_DrawIndexedPrimitiveUP(t->dev, (D3DPRIMITIVETYPE)prim, 0, nv, count, idx, D3DFMT_INDEX16, v, sizeof *v);
    } else {
        struct { float x, y, z; uint32_t d, s; float u, v; } *p = malloc(sizeof *p * (nv ? nv : 1));
        for (int i = 0; i < nv; i++) { p[i].x = v[i].x; p[i].y = v[i].y; p[i].z = v[i].z; p[i].d = v[i].diffuse; p[i].s = v[i].specular; p[i].u = v[i].u; p[i].v = v[i].v; }
        IDirect3DDevice9_SetFVF(t->dev, D3DFVF_XYZ | D3DFVF_DIFFUSE | D3DFVF_SPECULAR | D3DFVF_TEX1);
        hr = IDirect3DDevice9_DrawIndexedPrimitiveUP(t->dev, (D3DPRIMITIVETYPE)prim, 0, nv, count, idx, D3DFMT_INDEX16, p, sizeof *p);
        free(p);
    }
    return SUCCEEDED(hr);
}
static int n_drawg(V6nTarget* t, uint32_t prim, uint32_t count, int ne, const uint8_t* us, const uint8_t* ix, const float* d, int nv) {
    (void)nv;
    D3DVERTEXELEMENT9 el[17];
    for (int i = 0; i < ne; i++) el[i] = (D3DVERTEXELEMENT9){0, (WORD)(16 * i), D3DDECLTYPE_FLOAT4, D3DDECLMETHOD_DEFAULT, us[i], ix[i]};
    el[ne] = (D3DVERTEXELEMENT9)D3DDECL_END();
    IDirect3DVertexDeclaration9* decl = NULL;
    if (FAILED(IDirect3DDevice9_CreateVertexDeclaration(t->dev, el, &decl))) return 0;
    IDirect3DDevice9_SetVertexDeclaration(t->dev, decl);
    HRESULT hr = IDirect3DDevice9_DrawPrimitiveUP(t->dev, (D3DPRIMITIVETYPE)prim, count, d, 16 * ne);
    IDirect3DVertexDeclaration9_Release(decl);
    return SUCCEEDED(hr);
}
const V6nOps v6n_target_ops = {"nine-softpipe", n_begin, n_end, n_clear, n_rs, n_samp, n_tss, n_tex, n_notex, n_vp,
                               n_scissor, n_proj, n_draw, n_readback, n_ps, n_psc, n_vsh, n_vsc, n_drawidx, n_drawg};
