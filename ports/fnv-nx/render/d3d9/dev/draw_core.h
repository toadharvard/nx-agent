// draw_core.h: shared declarations of the draw path (D5a): draw_api (the four Draw* methods, validation), draw_exec (one draw), draw_prim (primitive formulas), draw_prog (shader pairs),
// draw_layout (vertex layouts), draw_state (pass, render-state words, state / layout ids), draw_const (constant blocks, driver blocks), draw_tex (samplers, textures), draw_bind (streams, indices, UP data).
// Rules, sources and decisions: the headers of those files; the README of the device (dev/README.md "Draw path").
#pragma once
#include "res_tex.h"
#include "copy_util.h"
#include "glue_stream.h"
#include "glue_buf.h"
#include "draw_ffp.h"
#include "nxgpu4_shader.h"
#include "../front/d3d9_state.h"
#include "fnvnx_dk_layout.h"
#include <string.h>

enum : uint32_t { NXD_STREAM_INDEXED = D3DSTREAMSOURCE_INDEXEDDATA, NXD_STREAM_INSTANCE = D3DSTREAMSOURCE_INSTANCEDATA, NXD_FREQ_MASK = 0x7FFFFFu };
enum { NXD_VS_SAMPLERS = 4, NXD_SAMP_VS0 = 17 };   // sampler slots (state model): 0-15 pixel, 16 = D3DDMAPSAMPLER, 17-20 = D3DVERTEXTEXTURESAMPLER0-3

// ---- one draw call after validation
struct NxUpData { const void* vtx; uint32_t vbytes, vstride; const void* idx; uint32_t ibytes, itype; };   // DrawPrimitiveUP / DrawIndexedPrimitiveUP data (idx null: not indexed); itype 0 = 16 bit, 1 = 32 bit
struct NxDrawReq {
    uint32_t prim, count;     // D3DPRIMITIVETYPE (valid), primitive count (> 0)
    bool indexed;
    int32_t base;             // indexed: added to every index (BaseVertexIndex; UP: -MinVertexIndex)
    uint32_t first;           // not indexed: first vertex; indexed: first index (in indices)
    const NxUpData* up;       // null = the device's streams / index buffer
};
HRESULT nx_draw_exec(NxDeviceCore* d, const NxDrawReq& r);

// ---- draw_prim.cpp
bool nx_prim_valid(uint32_t t);
uint32_t nx_prim_vertices(uint32_t t, uint32_t n);
uint32_t nx_prim_nx(uint32_t t);
uint32_t nx_instances(const NxDeviceState& s);
uint32_t nx_attr_type(uint32_t d3ddecltype);

// ---- draw_prog.cpp: the program pair of a draw
struct NxVsSig { uint32_t n; uint8_t pair[16], loc[16]; };   // vertex inputs: FNVNX_DK_PAIR and attribute location of input k
struct NxProg {
    uint32_t vs, ps;                  // backend shader ids
    const NxSmIface* vi; const NxSmIface* pi;   // null = fixed function
    NxFfpVsBind fv; NxFfpPsBind fp;
    uint32_t ps_version;              // low 16 bits of the pixel shader version token, 0 = fixed function
    NxVsSig sig;
};
bool nx_vs_sig(const NxSmIface& i, NxVsSig* out);                                        // false: not a legal declaration set (duplicate pair / more than 16)
int nx_link_set(const uint8_t* vs_out, uint32_t nv, const uint8_t* ps_in, uint32_t np, uint8_t* l, uint32_t* nl);   // shcache_link_set (render/shaders/cache/shcache_key.c) over FNVNX_DK_PAIR codes
HRESULT nx_prog_resolve(NxDeviceCore* d, NxProg* p);

// ---- draw_layout.cpp
struct NxLayout { NxVtxLayout4 v; uint32_t streams; int32_t zero_stream; };   // streams: mask of the streams the attributes read (without the constant stream); zero_stream: the constant stream of unbound inputs, -1 = none
// false: an attribute reads a stream in `bound`-less state (a draw that reads an unbound stream has no vertex data)
bool nx_layout_build(const NxResDecl* decl, const NxVsSig& sig, const uint32_t* freq, uint32_t bound, NxLayout* out);

// ---- the global state of the one command stream (draw_state.cpp). Valid for the pass epoch `epoch` and the device `owner`; anything else = everything unbound.
struct NxUboCache { uint8_t* data; uint32_t cap, size; bool ok; };
struct NxBound {
    NxDeviceCore* owner; uint32_t epoch;
    uint32_t state_id, state_serial; bool state_ok;
    uint32_t vtx[NX4_MAX_STREAMS][4]; uint8_t vtx_ok[NX4_MAX_STREAMS];   // block, offset, size, stride
    uint32_t idx[4]; uint8_t idx_ok;                                       // block, offset, size, type
    uint32_t tex[2][24]; uint8_t tex_ok[2][24];
    NxUboCache ubo[2][8];                                                  // [stage][slot]
    NxViewport vp; NxScissor sc; float blend[4], bias[2]; uint8_t vp_ok, sc_ok, blend_ok, bias_ok;
    uint32_t pass_epoch; bool pass_ok; NxTarget col[4]; uint32_t nc; NxTarget dep; bool has_dep;   // the pass this module began
    bool fx_lastpixel;                                                     // NXW_wined3d_device_apply_stateblock was logged (Wine: `static bool warned`)
};
#include "const_fixme.h"   // fnvnx_wine_fixme and the NXW_ ids of verify/wine_fixme.tsv
extern NxBound g_nxb;
void nx_draw_shutdown();   // returns the process-wide memory of the draw path (the constant-block caches of g_nxb, the zero block of draw_bind.cpp) and forgets what the stream had bound: platform teardown, tests (with nxs_shutdown)
struct NxDrawDev : NxDevLifeBase { NxD3D9Latch latch; NxDrawDev() { nx_d3d9_latch_init(&latch); } };
NxDrawDev* nx_draw_dev(NxDeviceCore* d);

struct NxPassSet { uint32_t nc; NxTarget col[4]; NxSurfCore* csc[4]; NxTarget dep; bool has_dep; NxSurfCore* dsc; uint32_t depth_fmt; };
HRESULT nx_pass_resolve(NxDeviceCore* d, NxPassSet* ps);                       // the target set of the device state by the NULL-format rules (draw_state.cpp)
void nx_pass_ensure(NxDeviceCore* d, const NxPassSet& ps);                  // a pass of these targets is open (begins one if not); may flush
void nx_state_flush(NxDeviceCore* d, const NxDrawReq& r, const NxProg& pr, const NxLayout& lay, const NxPassSet& ps, uint64_t groups);   // viewport, scissor, dynamics, layout / state ids, BindState
uint32_t nx_layout_id(const NxVtxLayout4& v, uint32_t* serial);   // draw_ids.cpp: ids of the process-global tables; both emit their Define op when the content is new
uint32_t nx_state_id(uint32_t vs, uint32_t ps, uint32_t lay, uint32_t lserial, const NxRenderState& rs, uint32_t* serial);
void nx_state_forget_shader(uint32_t id);

// ---- draw_const.cpp
bool nx_ubo_reserve(uint32_t stage, uint32_t slot, uint32_t bytes);                               // capacity of the comparison copy of a block (the only allocation of the flush: made before anything is written; false = no memory)
void nx_ubo_emit(uint32_t stage, uint32_t slot, const void* data, uint32_t bytes, bool full);   // BindUbo of `data` unless the same bytes are bound (nx_ubo_reserve'd)
void nx_const_flush(NxDeviceCore* d, const NxProg& pr, const NxPassSet& ps, bool full, uint64_t groups);   // UBO 0 / 1 / 2 of both stages and the fixed-function blocks
void nx_driver_blocks(NxDeviceCore* d, const NxProg& pr, uint8_t* vs64, uint8_t* ps352);          // the contents of the two driver blocks (also for tests)

// ---- draw_tex.cpp: samplers and textures
struct NxTexPlan { uint32_t h[2][24]; uint32_t lo[2], hi[2]; uint8_t stype[8]; };   // texture handles per stage and slot (0 = none), used slot span [lo, hi) per stage, ps_1_x / fixed function type word per stage
HRESULT nx_tex_plan(NxDeviceCore* d, const NxProg& pr, NxTexPlan* plan);              // syncs images, resolves sampler ids (may flush the stream: call BEFORE the pass is ensured)
void nx_tex_flush(const NxTexPlan& plan, bool full);

// ---- draw_bind.cpp: vertex streams, index buffer, UP data
struct NxBindPlan { uint32_t vtx[NX4_MAX_STREAMS][4]; uint32_t nvtx_mask; uint32_t idx[4]; bool has_idx; uint32_t zero_blk; void* big_mem[2]; uint32_t big_blk[2]; uint32_t up_v[2], up_i[2]; };   // big_*: dedicated blocks of oversize UP data (draw_bind.cpp B5)
HRESULT nx_bind_plan(NxDeviceCore* d, const NxDrawReq& r, const NxLayout& lay, NxBindPlan* bp);   // mirrors of the buffers (flushes when the hazard rule says so)
void nx_bind_flush(const NxDrawReq& r, const NxLayout& lay, const NxBindPlan& bp, bool full);  // BindVtxN / BindIdx (UP: payload binds)
void nx_bind_done(NxBindPlan* bp);                                                              // after the draw op: oversize UP blocks are released (the stream is finished first)
