// copy_util.h: shared helpers of the D6 methods (copy_*.cpp): surface objects, sub-resource views, rect rules, CPU rectangle copies. Rules and sources are stated at each function.
#pragma once
#include <fnvnx_render_hooks.h>
#include "res_tex.h"
#include "state_rt.h"
#include "glue_stream.h"

inline NxSurfCore* nx_surf_core(IDirect3DSurface9* p) { NxObj* o = nx_obj_of((IUnknown*)p); return o && o->ci->rtype == D3DRTYPE_SURFACE ? static_cast<NxSurfCore*>(o) : nullptr; }
inline const NxSub* nx_surf_sub(const NxSurfCore* s) { return s->img->at(s->level, s->face); }
inline bool nx_img_is_depth(const NxImg* im) { return (im->ni.flags & NXFMT_DEPTH) != 0; }
inline bool nx_img_null(const NxImg* im) { return im->ni.format == NxFmt_None && (im->ni.flags & NXFMT_NULL); }   // a target without storage
inline bool nx_img_plain(const NxImg* im) { return im->kind == NXK_SURFACE && !(im->usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)); }   // offscreen plain surface
inline NxTarget nx_target(const NxSurfCore* s) { NxTarget t; t.image = s->img->gpu; t.level = (uint16_t)s->level; t.layer = (uint16_t)s->face; return t; }
bool nx_device_in_scene(NxObj* device);   // BeginScene .. EndScene (the lifecycle state of the device; false without one)
int nx_dev_profile(NxObj* device);        // the env profile of the device
// StretchRect filter / format-conversion legality from the env table; plain rect math
struct NxRc { int32_t l, t, r, b; };
inline bool nx_rc_empty(const NxRc& a) { return a.l >= a.r || a.t >= a.b; }
inline NxRc nx_rc_and(const NxRc& a, const NxRc& b) { NxRc r = {a.l > b.l ? a.l : b.l, a.t > b.t ? a.t : b.t, a.r < b.r ? a.r : b.r, a.b < b.b ? a.b : b.b}; if (nx_rc_empty(r)) r = {0, 0, 0, 0}; return r; }
inline D3DBOX nx_box_of(const NxRc& r) { D3DBOX b = {(UINT)r.l, (UINT)r.t, (UINT)r.r, (UINT)r.b, 0, 1}; return b; }
// CPU copy of the texels of `src` rect to `dst` at (dx, dy) of two images of the SAME format (block formats: rect in texels, multiples of the block unless it ends at the level edge)
void nx_cpu_copy_rect(const NxImg* si, const NxSub* ss, const NxRc& sr, const NxImg* di, NxSub* ds, int dx, int dy);
// platform: a GDI memory device context over the pixels of a surface (Win32 D3DKMTCreateDCFromMemory with a compatible DC: the bitmap IS `mem`, rows `pitch` bytes apart, top row first,
// d3dfmt one of A8R8G8B8, X8R8G8B8, R5G6B5, X1R5G5B5, A1R5G5B5, R8G8B8, width x height pixels); null = the platform cannot make one. nxd3d_dc_release: DeleteDC. Writes through the DC land in `mem`.
