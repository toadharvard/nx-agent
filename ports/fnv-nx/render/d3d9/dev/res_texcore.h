// D4: the object classes of textures / surfaces / volumes (NX_BASES of res_hooks.h). Light header: no backend or format includes (res_tex.h has the storage NxImg).
#pragma once
#include "res_core.h"
struct NxImg;
// ---- objects
struct NxTexCore : NxObj {           // NxTexture9 / NxCubeTexture9 / NxVolumeTexture9 / NxBaseTexture9
    NxImg* img = nullptr; NxObj** kids = nullptr; uint32_t nkids = 0;   // kids: the level surfaces / volumes, created with the texture (identity), public count 0, refcount = ours
    ~NxTexCore();
    DWORD setLOD(DWORD l); DWORD getLOD(); DWORD getLevelCount();
    HRESULT setFilter(D3DTEXTUREFILTERTYPE t); D3DTEXTUREFILTERTYPE getFilter(); void genMips();
    HRESULT levelDesc2(UINT level, D3DSURFACE_DESC* d); HRESULT levelDesc3(UINT level, D3DVOLUME_DESC* d);
    HRESULT kid(uint32_t level, uint32_t face, void** pp);               // sub-resource object, AddRef'ed through R4
    HRESULT lock2(uint32_t level, uint32_t face, D3DLOCKED_RECT* lr, const RECT* r, DWORD f); HRESULT unlock2(uint32_t level, uint32_t face);
    HRESULT dirty2(uint32_t face, const RECT* r);
};
struct NxSurfCore : NxObj {          // NxSurface9: a level / face of a texture (tex != null) or a stand-alone surface (own)
    NxImg* img = nullptr; NxImg* own = nullptr; uint32_t level = 0, face = 0;
    void* dc = nullptr;   // the GDI DC handed out by GetDC (copy_dc.cpp), null = none
    ~NxSurfCore();
    HRESULT desc(D3DSURFACE_DESC* d); HRESULT lockRect(D3DLOCKED_RECT* lr, const RECT* r, DWORD f); HRESULT unlockRect();
};
struct NxVolCore : NxObj { NxImg* img = nullptr; uint32_t level = 0; HRESULT desc(D3DVOLUME_DESC* d); HRESULT lockBox(D3DLOCKED_BOX* lb, const D3DBOX* b, DWORD f); HRESULT unlockBox(); };
