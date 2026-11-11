// D4: textures, volumes, surfaces of our D3D9 (storage, locks, dirty regions, backend hooks). Rules and sources: res_fmt.cpp / res_tex.cpp / res_surf.cpp / res_create.cpp headers.
// Link note: the classes are the NX_BASES_<Class> of res_hooks.h. Backend hooks (nxd3d_image_*) are declared here and never defined in the product objects (tests define them).
#pragma once
#include "res_core.h"
#include "nxgpu4.h"
#include "d3d9_state.h"   // nx_d3d9_format / nx_d3d9_convert: the one D3DFORMAT -> NxFormat table
#define NX_MAX_TEX_BYTES 0x40000000u   // largest single image we allocate (1 GiB, as NX_MAX_BUFFER_BYTES)

// ---- format layout (res_fmt.cpp): wined3d format table (utils.c formats[], format_block_info[], format_base_flags[]) over the D3DFORMAT -> wined3d id map of d3d9/device.c
enum { NXTF_BLOCKS = 1, NXTF_NOVERIFY = 2, NXTF_BROKEN = 4, NXTF_MAPPABLE = 8, NXTF_COMPRESSED = 16 };
struct NxTexFmt { uint8_t bytes, bw, bh, bb, flags, hn, hd; };   // bytes per pixel (bb per block), block size, height scale hn/hd (YV12, NV12)
bool nx_texfmt(uint32_t d3dfmt, NxTexFmt* f);                      // false: no layout (UNKNOWN, unmapped id, unknown FOURCC): wined3d size 0
void nx_tex_pitch(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t* row, uint32_t* slice);   // wined3d_format_calculate_pitch, alignment 4 (d3d9 device.c)
uint64_t nx_tex_size(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t d);                    // wined3d_format_calculate_size

// ---- backend hooks (thin, 1:1 onto nxgpu4.h; `res` is the owning texture / surface object). Declared here, DEFINED BY THE TESTS / the platform.
struct NxBox3 { uint32_t l, t, f, r, b, k; };                      // left, top, front, right, bottom, back (D3DBOX order)
uint32_t nxd3d_image_create(NxObj* res, const NxImageDesc4* d);   // image_create4; 0 = refused (a backend defect: the env table says the format exists)
void nxd3d_image_destroy(NxObj* res, uint32_t image);
// NxOp_ImageUpdate: native-format texels (BCn: 4x4 blocks) of `box` of (level, layer), rows row_pitch / slices slice_pitch bytes apart, `data` = first texel of the box
void nxd3d_image_update(NxObj* res, uint32_t image, uint32_t level, uint32_t layer, const NxBox3* box, const void* data, uint32_t row_pitch, uint32_t slice_pitch);
void nxd3d_image_read(NxObj* res, uint32_t image, uint32_t level, uint32_t layer, void* dst, uint32_t row_pitch, uint32_t slice_pitch);   // image_read of the whole sub-resource (native bytes)
void nxd3d_gen_mips(NxObj* res, uint32_t image, uint32_t filter);  // NxOp_GenMips, filter 0 point / 1 linear
int nx_env_profile_of(NxObj* device);                              // the ENV profile of a device (d3d9_adapter.cpp: prof of the owning IDirect3D9); tests define it

// ---- storage
struct NxSub {                       // one sub-resource: level x face (volume: level)
    uint8_t* mem; uint32_t w, h, d, row, slice, size;      // mem: CPU copy (null: GPU-only); row / slice: LockRect / LockBox pitches (wined3d sub_resource_get_map_pitch)
    uint8_t locked, lock_w, gpu_newer, dirty;                // lock_w: the lock may write; gpu_newer: the GPU wrote since the CPU copy was last read back; dirty: dbox is valid
    NxBox3 dbox;                     // dirty region (union), consumed by upload
};
enum { NXK_TEXTURE = 0, NXK_CUBE = 1, NXK_VOLUME = 2, NXK_SURFACE = 3 };
struct NxImg {
    uint32_t kind, fmt, usage, w, h, d, levels, nlev, faces, ms, msq, pool, access, gpu, flags;   // levels: exposed (subs per face), nlev: GPU chain (AUTOGENMIPMAP: full chain, levels = 1)
    NxTexFmt tf; NxFormatInfo ni;    // layout; ni.format == NxFmt_None: no native representation (the image stays CPU-only)
    NxSub* sub; uint8_t* raw; NxObj* owner;
    bool rec_dirty, autogen, mip_dirty, ext;                 // dirty regions recorded (MANAGED, SYSTEMMEM); AUTOGENMIPMAP; levels changed since the last generation; CPU memory supplied by the application
    uint32_t filter, lod;
    ~NxImg();
    HRESULT init(NxObj* own, uint32_t kind, uint32_t w, uint32_t h, uint32_t d, uint32_t levels, uint32_t faces, uint32_t fmt, uint32_t usage, uint32_t pool, uint32_t access, bool autogen_gpu, void* user_mem);
    NxSub* at(uint32_t level, uint32_t face) { return &sub[face * levels + level]; }
    void add_dirty(uint32_t level, uint32_t face, const NxBox3* b);   // union into the sub-resource's region (null = whole)
    void upload();                                                    // flush: image_update per dirty sub-resource (face-major, level order), then clear
    void sync(bool gen);                                              // upload(), and (gen) the AUTOGENMIPMAP generation when the levels changed
};
enum { NXI_DISCARD = 1 };            // NxImg::flags: Discard flag of CreateDepthStencilSurface
// ---- locks (res_surf.cpp): `box` null = the whole sub-resource
HRESULT nx_img_lock(NxImg* img, uint32_t level, uint32_t face, const D3DBOX* box, DWORD flags, void** bits, uint32_t* row, uint32_t* slice);
HRESULT nx_img_unlock(NxImg* img, uint32_t level, uint32_t face, bool* was_locked);
bool nx_box_valid(const NxImg* img, uint32_t level, const D3DBOX* b);   // wined3d_resource_check_box_dimensions

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
    ~NxSurfCore();
    HRESULT desc(D3DSURFACE_DESC* d); HRESULT lockRect(D3DLOCKED_RECT* lr, const RECT* r, DWORD f); HRESULT unlockRect();
};
struct NxVolCore : NxObj { NxImg* img = nullptr; uint32_t level = 0; HRESULT desc(D3DVOLUME_DESC* d); HRESULT lockBox(D3DLOCKED_BOX* lb, const D3DBOX* b, DWORD f); HRESULT unlockBox(); };
void nx_tex_preload(NxObj* resource);   // for the product's nxd3d_preload: MANAGED texture / surface -> upload the dirty regions (Wine: wined3d_resource_preload loads the draw texture)
NxImg* nx_img_of(NxObj* resource);      // image of a texture / surface / volume object of ours, null otherwise (for the draw path / UpdateTexture)
void nx_img_gpu_wrote(NxImg* img, uint32_t level, uint32_t face);   // the draw path rendered into the sub-resource: the CPU copy is stale until the next read lock
