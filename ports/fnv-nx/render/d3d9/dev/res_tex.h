// D4: textures, volumes, surfaces of our D3D9 (storage, locks, dirty regions, backend hooks). Rules and sources: res_fmt.cpp / res_tex.cpp / res_surf.cpp / res_create.cpp headers.
// Link note: the classes are the NX_BASES_<Class> of res_hooks.h. Backend hooks (nxd3d_image_*) are declared here and never defined in the product objects (tests define them).
#pragma once
#include "res_texcore.h"
#include "nxgpu4.h"
#include "../front/d3d9_state.h"   // nx_d3d9_format / nx_d3d9_convert: the one D3DFORMAT -> NxFormat table
#define NX_MAX_TEX_BYTES 0x40000000u   // largest single image we allocate (1 GiB, as NX_MAX_BUFFER_BYTES)

// ---- format layout (res_fmt.cpp): wined3d format table (utils.c formats[], format_block_info[], format_base_flags[]) over the D3DFORMAT -> wined3d id map of d3d9/device.c
enum { NXTF_BLOCKS = 1, NXTF_NOVERIFY = 2, NXTF_BROKEN = 4, NXTF_MAPPABLE = 8, NXTF_COMPRESSED = 16 };
struct NxTexFmt { uint8_t bytes, bw, bh, bb, flags, hn, hd; };   // bytes per pixel (bb per block), block size, height scale hn/hd (YV12, NV12)
bool nx_texfmt(uint32_t d3dfmt, NxTexFmt* f);                      // false: no layout (UNKNOWN, unmapped id, unknown FOURCC): wined3d size 0
void nx_tex_pitch(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t* row, uint32_t* slice);   // wined3d_format_calculate_pitch, alignment 4 (d3d9 device.c)
uint64_t nx_tex_store(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t d);   // storage bytes of a sub-resource (>= nx_tex_size)
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
    uint8_t* mem; uint32_t w, h, d, row, slice, size, store;      // size: the D3D size; store: bytes kept at mem (nx_tex_store, >= size); mem: CPU copy (null: GPU-only); row / slice: LockRect / LockBox pitches (wined3d sub_resource_get_map_pitch)
    uint8_t locked, lock_w, gpu_newer, dirty;                // lock_w: the lock may write; gpu_newer: the GPU wrote since the CPU copy was last read back; dirty: dbox is valid
    NxBox3 dbox;                     // dirty region (union), consumed by upload
};
enum { NXK_TEXTURE = 0, NXK_CUBE = 1, NXK_VOLUME = 2, NXK_SURFACE = 3 };
struct NxImg {
    uint32_t kind, fmt, usage, w, h, d, levels, nlev, faces, ms, msq, samples, pool, access, gpu, flags;   // levels: exposed (subs per face), nlev: GPU chain (AUTOGENMIPMAP: full chain, levels = 1)
    NxTexFmt tf; NxFormatInfo ni;    // layout; ni.format == NxFmt_None: no native representation (the image stays CPU-only)
    NxSub* sub; uint8_t* raw; NxObj* owner;
    bool rec_dirty, autogen, mip_dirty, ext;                 // dirty regions recorded (MANAGED, SYSTEMMEM); AUTOGENMIPMAP; levels changed since the last generation; CPU memory supplied by the application
    uint32_t filter, lod;
    ~NxImg();
    void* operator new(size_t n) noexcept NX_MUST_USE NX_USE_MAKE { return nxd3d_alloc(n); } void operator delete(void* p) { nxd3d_free(p); }   // made by nx_make_plain<NxImg>() (value-initialised); nullptr on exhaustion
    NX_MUST_USE HRESULT init(NxObj* own, uint32_t kind, uint32_t w, uint32_t h, uint32_t d, uint32_t levels, uint32_t faces, uint32_t fmt, uint32_t usage, uint32_t pool, uint32_t access, bool autogen_gpu, void* user_mem, uint32_t ms = 0, uint32_t msq = 0, uint32_t samples = 1, uint32_t bind = 0);
    NxSub* at(uint32_t level, uint32_t face) { return &sub[face * levels + level]; }
    void add_dirty(uint32_t level, uint32_t face, const NxBox3* b);   // union into the sub-resource's region (null = whole)
    NX_MUST_USE bool upload();                                        // flush: image_update per dirty sub-resource (face-major, level order), then clear; false = out of memory, the sub-resource that failed (and every later one) stays dirty
    NX_MUST_USE bool sync(bool gen);                                  // upload(), and (gen) the AUTOGENMIPMAP generation when the levels changed; false = upload failed (nothing generated)
};
NX_MUST_USE bool nx_img_push(NxImg*, uint32_t level, uint32_t face, const NxSub* s, const NxBox3* b);   // image_update of a box of a D3D-layout sub-resource (converts formats with NXFMT_CONVERT)
NX_MUST_USE bool nx_img_push_at(NxImg*, uint32_t level, uint32_t face, const NxBox3* b, const uint8_t* texels, uint32_t row, uint32_t slice);   // the same for texels (D3D layout) given by the pointer to the first one of the box
enum { NXI_DISCARD = 1 };            // NxImg::flags: Discard flag of CreateDepthStencilSurface
// ---- locks (res_surf.cpp): `box` null = the whole sub-resource
HRESULT nx_img_lock(NxImg* img, uint32_t level, uint32_t face, const D3DBOX* box, DWORD flags, void** bits, uint32_t* row, uint32_t* slice);
HRESULT nx_img_unlock(NxImg* img, uint32_t level, uint32_t face, bool* was_locked);
bool nx_box_valid(const NxImg* img, uint32_t level, const D3DBOX* b);   // wined3d_resource_check_box_dimensions

void nx_tex_preload(NxObj* resource);   // for the product's nxd3d_preload: MANAGED texture / surface -> upload the dirty regions (Wine: wined3d_resource_preload loads the draw texture)
NxImg* nx_img_of(NxObj* resource);      // image of a texture / surface / volume object of ours, null otherwise (for the draw path / UpdateTexture)
void nx_img_gpu_wrote(NxImg* img, uint32_t level, uint32_t face);   // the draw path rendered into the sub-resource: the CPU copy is stale until the next read lock
