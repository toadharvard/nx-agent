// D3: GPU-independent resource objects of our D3D9 (buffers, declarations + FVF, shaders, queries). Rules and sources: res_*.cpp headers.
// Link note: these classes are the NX_BASES_<Class> of res_hooks.h (the one-line change of d9impl_hooks.h).
#pragma once
#include "state_rt.h"
#include "res_sm.h"
#define NX_MAX_BUFFER_BYTES 0x40000000u  // largest buffer we allocate (1 GiB: Switch memory bound; above = D3DERR_OUTOFVIDEOMEMORY)

// backend hooks of the GPU mirrors of buffers and queries (glue_buf.cpp / glue_query.cpp; the tests define their own)
struct NxBufCore; struct NxQueryCore;
void nxd3d_buf_destroy(NxBufCore* b);                      // the object dies: its GPU mirror (if any) goes
bool nxd3d_query_create(NxQueryCore* q);                   // the query object exists: reserve what the backend needs for its type; false = out of backend query ids or no memory for the stream ring (glue_stream.h E1; CreateQuery: D3DERR_OUTOFVIDEOMEMORY)
void nxd3d_query_destroy(NxQueryCore* q);                  // the object dies: release what nxd3d_query_create reserved

// ---- vertex / index buffers. CPU memory owned by the object, 16-byte aligned, zero-filled at creation (RD7: and again on an effective DISCARD).
struct NxBufCore : NxObj {
    uint8_t* mem = nullptr; uint8_t* raw = nullptr;
    uint32_t length = 0, usage = 0, locks = 0, rev = 0;     // rev: +1 per lock that may write (the draw path re-uploads when it changed)
    uint32_t dirty_lo = ~0u, dirty_hi = 0;                  // dirty byte range [lo, hi), empty iff lo >= hi; accumulated by write locks, cleared by the consumer
    uint32_t fmt = 0;                                       // FVF (vertex buffer) / D3DFORMAT (index buffer)
    void* gmem = nullptr; uint32_t gsize = 0, gblock = 0;   // GPU mirror (glue_buf.cpp nx_buf_gpu_sync): 4 KiB aligned copy, backend memory block id
    ~NxBufCore() { nxd3d_free(raw); nxd3d_buf_destroy(this); }
    NX_MUST_USE HRESULT init(uint32_t len, uint32_t use, uint32_t fmt_, uint32_t pool);   // validation is in the Create* functions; this allocates (E_OUTOFMEMORY: nothing allocated)
    HRESULT lock(uint32_t off, uint32_t size, void** pp, DWORD flags);
    HRESULT unlock();
    bool take_dirty(uint32_t* lo, uint32_t* hi);            // false = nothing dirty; else the range, now cleared
};

// ---- vertex declarations. `el` holds the elements incl. the END element; n counts the END (GetDeclaration's count).
struct NxFfpVsDecl;
struct NxResDecl : NxDeclCore {                             // NxDeclCore::fvf = nx_decl_to_fvf(el) for element declarations, the source FVF for implicit ones
    D3DVERTEXELEMENT9* el = nullptr; uint32_t n = 0; uint32_t stream_mask = 0; uint32_t stride[16] = {};   // stride: max(offset + size) per stream < 16 (a lower bound, the SetStreamSource stride rules)
    ~NxResDecl() { nxd3d_free(el); }
};
struct NxFvfLayout { D3DVERTEXELEMENT9 el[16]; uint32_t n; uint32_t stride; };   // n elements WITHOUT END; el[n] is END
bool nx_fvf_to_decl(DWORD fvf, NxFvfLayout* out);          // false = fvf is no FVF of the documented grammar (out untouched)
DWORD nx_decl_to_fvf(const D3DVERTEXELEMENT9* e);          // END terminated; 0 = the declaration "is" no FVF
uint32_t nx_decltype_bytes(uint32_t type);                 // 0 for type >= D3DDECLTYPE_UNUSED
uint32_t nx_decltype_comps(uint32_t type);                 // components as the fixed-function pipeline reads them; 0 for type >= UNUSED
HRESULT nx_decl_validate(const D3DVERTEXELEMENT9* e, uint32_t* n_incl_end);   // Wine d3d9 convert_to_wined3d_declaration + wined3d vertexdeclaration_init
void nx_ffp_decl_from_elements(const D3DVERTEXELEMENT9* e, NxFfpVsDecl* out); // pure and total (ffp_vs_state.h)
bool nx_env_decltype_supported(uint32_t type);             // ENV (caps DeclTypes): declared here, defined by the environment module

// ---- shaders: bytecode copy, content key (SHA-256 of the bytecode, fnvnx_sha256 of lib/sha256), parsed interface (res_sm.h)
struct NxShaderCore;
void nxd3d_shader_ids_destroy(NxShaderCore* s);            // draw_prog.cpp: the object dies: the backend shader ids made for it (NXGPU_FN_SHADER_DESTROY after a flush of the stream that may use them); only called when `nprogs` > 0
struct NxShaderProg { uint8_t variant, n_l, l[31]; uint32_t id; };   // a backend shader of this shader object: (variant bit0 = VS complete, bit1 = D3DSHADE_FLAT colours, link set L = FNVNX_DK_PAIR codes ascending) -> id (>= 1), made at the first draw that needs it
struct NxShaderCore : NxObj {
    uint32_t* code = nullptr; uint32_t nbytes = 0; uint8_t key[32] = {}; NxSmIface iface;
    NxShaderProg* progs = nullptr; uint32_t nprogs = 0, cprogs = 0;
    ~NxShaderCore() { if (nprogs) nxd3d_shader_ids_destroy(this); nxd3d_free(progs); nxd3d_free(code); }
    HRESULT getFunction(void* pData, UINT* pSize);
};

// ---- queries (object part). Backend hooks are declared, never defined here.
enum { NXQ_CREATED = 0, NXQ_BUILDING = 1, NXQ_SIGNALED = 2 };   // Wine wined3d QUERY_CREATED / QUERY_BUILDING / QUERY_SIGNALLED
struct NxQueryCore : NxObj {
    uint32_t type = 0, data_size = 0, state = NXQ_CREATED;
    uint32_t gid = 0; uint8_t gbegun = 0, gready = 0; uint64_t gvalue = 0;   // glue_query.cpp: backend query id (0 = none), a Begin is open, the result arrived (cached in gvalue)
    ~NxQueryCore() { nxd3d_query_destroy(this); }
};
bool nx_env_query_supported(uint32_t type);                // ENV: which D3DQUERYTYPEs exist on this device
uint32_t nx_query_data_size(uint32_t type);                // documented size of GetData's result; 0 = not a D3DQUERYTYPE of this header
void nxd3d_query_issue(NxQueryCore* q, DWORD flags);       // backend: the Issue reached the stream (flags as given by the application)
bool nxd3d_query_ready(NxQueryCore* q);                    // backend: has the result arrived?
void nxd3d_query_flush(NxQueryCore* q);                    // backend: D3DGETDATA_FLUSH while pending
void nxd3d_query_result(NxQueryCore* q, void* out, uint32_t size);   // backend: copy min(size, data_size) result bytes (only after ready)
