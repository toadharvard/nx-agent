#include "t_common.h"
#include "../t_keep.h"
TDev t_dev(bool ex, uint32_t w, uint32_t h) {
    TDev e = {}; NxCreateParams cp = {0, w, h, D3DCREATE_MIXED_VERTEXPROCESSING};
    if (ex) { NxDirect3D9Ex* d = t_new<NxDirect3D9Ex>(); d->asRoot(1); e.devex = t_new<NxDevice9Ex>(); e.devex->asDevice(d); e.devex->init(cp); e.core = e.devex; e.obj = e.devex; e.i9 = e.devex; }
    else { e.d3d = t_new<NxDirect3D9>(); e.d3d->asRoot(1); e.dev = t_new<NxDevice9>(); e.dev->asDevice(e.d3d); e.dev->init(cp); e.core = e.dev; e.obj = e.dev; e.i9 = e.dev; }
    return e;
}
void t_scene(TDev& d, bool in) { if (!d.core->life) d.core->life = t_new<NxDevLife>(); static_cast<NxDevLife*>(d.core->life)->in_scene = in; }
uint32_t t_lcg(uint32_t* s) { *s = *s * 1664525u + 1013904223u; return *s >> 8; }
uint32_t t_rowbytes(uint32_t fmt, uint32_t w) { NxTexFmt f; if (!nx_texfmt(fmt, &f)) return 0; return (f.flags & NXTF_BLOCKS) ? ((w + f.bw - 1) / f.bw) * f.bb : w * f.bytes; }
IDirect3DSurface9* t_surf(TDev& d, int what, uint32_t w, uint32_t h, uint32_t fmt, D3DPOOL pool, uint32_t ms, bool lockable) {
    IDirect3DSurface9* s = nullptr; HRESULT hr;
    if (what == 0) hr = d.i9->CreateRenderTarget(w, h, (D3DFORMAT)fmt, (D3DMULTISAMPLE_TYPE)ms, 0, lockable, &s, nullptr);
    else if (what == 1) hr = d.i9->CreateDepthStencilSurface(w, h, (D3DFORMAT)fmt, (D3DMULTISAMPLE_TYPE)ms, 0, FALSE, &s, nullptr);
    else hr = d.i9->CreateOffscreenPlainSurface(w, h, (D3DFORMAT)fmt, pool, &s, nullptr);
    return hr == D3D_OK ? s : nullptr;
}
void t_put(IDirect3DSurface9* s, const uint8_t* bytes, uint32_t len) {
    D3DSURFACE_DESC dd; s->GetDesc(&dd); D3DLOCKED_RECT lr; if (s->LockRect(&lr, nullptr, 0) != D3D_OK) { CHECK(false); return; }
    NxTexFmt f; nx_texfmt(dd.Format, &f); uint32_t rb = t_rowbytes(dd.Format, dd.Width), rows = (f.flags & NXTF_BLOCKS) ? (dd.Height + f.bh - 1) / f.bh : dd.Height;
    for (uint32_t y = 0; y < rows && (y + 1) * rb <= len; y++) memcpy((uint8_t*)lr.pBits + (size_t)y * lr.Pitch, bytes + (size_t)y * rb, rb);
    s->UnlockRect();
}
bool t_get(IDirect3DSurface9* s, uint8_t* out, uint32_t len) {
    D3DSURFACE_DESC dd; s->GetDesc(&dd); D3DLOCKED_RECT lr; if (s->LockRect(&lr, nullptr, D3DLOCK_READONLY) != D3D_OK) return false;
    NxTexFmt f; nx_texfmt(dd.Format, &f); uint32_t rb = t_rowbytes(dd.Format, dd.Width), rows = (f.flags & NXTF_BLOCKS) ? (dd.Height + f.bh - 1) / f.bh : dd.Height;
    for (uint32_t y = 0; y < rows && (y + 1) * rb <= len; y++) memcpy(out + (size_t)y * rb, (uint8_t*)lr.pBits + (size_t)y * lr.Pitch, rb);
    s->UnlockRect(); return true;
}
