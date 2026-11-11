// IDirect3DSurface9::GetDC / ReleaseDC. Sources: Wine 9.0 d3d9/surface.c d3d9_surface_{GetDC, ReleaseDC}, wined3d/texture.c wined3d_texture_{get_dc, release_dc, create_dc} (+ d3d9/device.c
// is_gdi_compat_wined3dformat, d3d9/texture.c d3d9_texture_init: which surfaces get WINED3D_TEXTURE_GET_DC).
// RULES (INVALIDCALL unless stated): D1 the surface is a surface, a level of a 2D texture or a cube face (not a volume: wined3d type TEXTURE_2D) of one of the six GDI formats A8R8G8B8, X8R8G8B8, R5G6B5,
//  X1R5G5B5, A1R5G5B5, R8G8B8, and not multisampled (a multisampled image has no CPU view: MS lockable targets are refused at creation, the backend neither reads nor updates it). D2 no sub-resource of the resource is locked and no DC is out (wined3d: texture map_count != 0 refuses). D3 GetDC makes the CPU copy current (a GPU-only image gets a CPU
//  copy on first use; a read-back refreshes it), hands out the platform DC over it (nxd3d_dc_create; the platform cannot: INVALIDCALL), marks the whole level dirty (create_dc: dirty region of the whole
//  layer) and counts as a lock of the sub-resource. D4 ReleaseDC: a DC must be out, the handle must be that DC; the DC is destroyed (not OWNDC), the lock ends (AUTOGENMIPMAP: levels changed) and the pixels
//  the application drew are uploaded (the GPU copy was invalidated at GetDC).
#include "copy_util.h"
#include "../gen/d9impl.gen.h"
#include <string.h>

static bool gdi_format(uint32_t f) { return f == 20 || f == 21 || f == 22 || f == 23 || f == 24 || f == 25; }
static bool ensure_cpu(NxImg* im) {   // a CPU copy of every sub-resource of a GPU-only image (a GPU image may hold anything: the first read-back makes the copy current); false = out of memory, nothing changed
    if (im->raw || im->sub[0].mem) return true;
    uint64_t tot = 0; for (uint32_t i = 0; i < im->levels * im->faces; i++) tot += (im->sub[i].store + 15u) & ~15u;
    im->raw = (uint8_t*)nxd3d_alloc((size_t)tot + 15); if (!im->raw) return false;
    memset(im->raw, 0, (size_t)tot + 15); uint8_t* c = (uint8_t*)(((uintptr_t)im->raw + 15) & ~(uintptr_t)15);
    for (uint32_t i = 0; i < im->levels * im->faces; i++) { im->sub[i].mem = c; im->sub[i].gpu_newer = im->gpu != 0; c += (im->sub[i].store + 15u) & ~15u; }
    return true;
}
HRESULT STDMETHODCALLTYPE NxSurface9::GetDC(HDC* out) {
    if (!out) return D3DERR_INVALIDCALL;
    NxImg* im = img; if (im->kind == NXK_VOLUME || im->ms > 1 || !gdi_format(im->fmt)) return D3DERR_INVALIDCALL;                                           // D1
    if (dc) return D3DERR_INVALIDCALL;
    for (uint32_t i = 0; i < im->levels * im->faces; i++) if (im->sub[i].locked) return D3DERR_INVALIDCALL;                                      // D2
    if (!ensure_cpu(im)) return E_OUTOFMEMORY;   // wined3d_texture_get_dc: the CPU copy is the DC's bitmap; the map failure (E_OUTOFMEMORY, wined3d texture.c) is passed through
    NxSub* s = im->at(level, face);
    if (s->gpu_newer) { if (im->gpu) nxd3d_image_read(im->owner, im->gpu, level, face, s->mem, s->row, s->slice); s->gpu_newer = 0; }                // D3
    void* h = nxd3d_dc_create(s->mem, im->fmt, s->w, s->h, s->row); if (!h) return D3DERR_INVALIDCALL;
    im->add_dirty(level, face, nullptr); s->locked = 1; s->lock_w = 1; dc = h; *out = (HDC)h; return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxSurface9::ReleaseDC(HDC h) {
    if (!dc || (void*)h != dc) return D3DERR_INVALIDCALL;                                                                                         // D4
    nxd3d_dc_release(dc); dc = nullptr; bool was; nx_img_unlock(img, level, face, &was);
    nx_ignore(img->upload());   // eager flush only: GetDC marked the whole level dirty, so a failed upload leaves it dirty and the next flush (draw, PreLoad, Present) retries; ReleaseDC itself has nothing to undo
    return D3D_OK;
}
