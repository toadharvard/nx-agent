// D7: device lifecycle objects (swap chains, implicit objects, presentation state). Light header (no backend / format includes): the classes are NX_BASES of res_hooks.h.
// Rules and sources: dev_params.cpp (CreateDevice parameters), dev_chain.cpp (chains), dev_reset.cpp (Reset), dev_misc.cpp (everything else). Reference: Wine 9.0 d3d9 device.c / swapchain.c / wined3d swapchain.c, device.c.
#pragma once
#include "state_rt.h"
#ifndef D3DPRESENT_FORCEIMMEDIATE
#define D3DPRESENT_FORCEIMMEDIATE 0x00000100L   // d3d9.h: not in the shim
#endif

// ---- environment answers of the presentation parameters (dev_params.cpp), the one resolver of CreateDevice / CreateAdditionalSwapChain / Reset
struct NxPPEff { uint32_t w, h, fmt, count; void* win; uint32_t samples, ds_samples; };   // effective size / format / back buffer count, the window of the chain (device window, else focus window), samples handed to the backend (colour / depth)
enum { NX_PP_CREATE = 0, NX_PP_RESET = 1 };
// Wine d3d9 wined3d_swapchain_desc_from_d3d9 (INVALIDCALL rules), wined3d_swapchain_state_init / resize_buffers (size, format), swapchain_create_texture + wined3d_texture_create + resource_init (front buffer,
// back buffers, auto depth stencil, multisample), wined3d_output_set_display_mode (full screen). `focus` = the creation focus window (null on a chain that has no focus window of its own).
HRESULT nx_pp_resolve(int prof, bool ex, int how, const D3DPRESENT_PARAMETERS* pp, void* focus, NxPPEff* eff);
HRESULT nx_pp_check(bool ex, const D3DPRESENT_PARAMETERS* pp);   // wined3d_swapchain_desc_from_d3d9: the d3d9-level INVALIDCALL rules (also the first stage of nx_pp_resolve)
void nx_pp_write_back(D3DPRESENT_PARAMETERS* pp, const NxPPEff& e, bool reset);   // device_init (all fields) / d3d9_device_reset (width, height, format, count) write-back
D3DFORMAT nx_pp_fmt_out(uint32_t fmt);   // wined3dformat_from_d3dformat -> d3dformat_from_wined3dformat: printable FOURCCs and the 48 enum values pass, everything else is UNKNOWN
uint32_t nx_pp_interval(DWORD d3d_interval);   // NxPresent.interval (0 immediate, 1-4) of a D3DPRESENT_INTERVAL_* value of a validated parameter set

// ---- swap chain (NX_BASES_NxSwapChain9 / NxSwapChain9Ex)
struct NxDevLife;
struct NxChainCore : NxObj {
    NxDeviceCore* d = nullptr; uint32_t index = 0;
    D3DPRESENT_PARAMETERS pp = {};                    // what GetPresentParameters reports: effective size / format / count, window = the chain's window, interval as given
    NxObj** bb = nullptr; uint32_t nbb = 0;           // back buffer objects (NxSurface9, implicit: public 0, owned); identity is fixed, the IMAGE contents rotate (dev_chain.cpp rotate)
    D3DGAMMARAMP ramp = {}; bool ramp_set = false; uint32_t pcount = 0, pres_vb = 0;   // pcount: successful presents (GetLastPresentCount, PresentCount); pres_vb: vblank counter when the last present returned (m1_stats.cpp)
    NxObj* destroy_bbs();                             // destroys the back buffers that have no public reference, detaches the others (Ex Reset): they become ordinary objects
    ~NxChainCore();
    HRESULT present(const RECT* src, const RECT* dst, void* override_win, DWORD flags);   // flags: D3DPRESENT_FORCEIMMEDIATE = interval 0
    HRESULT backbuffer(UINT i, IDirect3DSurface9** out);
    HRESULT frontbuffer_data(IDirect3DSurface9* dst);
    HRESULT raster(D3DRASTER_STATUS* r);
    HRESULT display(D3DDISPLAYMODE* m); HRESULT display_ex(D3DDISPLAYMODEEX* m, D3DDISPLAYROTATION* rot);
    HRESULT present_params(D3DPRESENT_PARAMETERS* out);
    HRESULT last_present_count(UINT* c);
    void presented();                                 // a present succeeded: pcount++, stamp pres_vb (m1_stats.cpp)
    HRESULT present_stats(D3DPRESENTSTATS* s);        // IDirect3DSwapChain9Ex::GetPresentStats (m1_stats.cpp)
    // creation of the back buffers (also Reset): a surface per buffer, GPU image, lockable per D3DPRESENTFLAG_LOCKABLE_BACKBUFFER
    NX_MUST_USE HRESULT make_buffers(const NxPPEff& e, const D3DPRESENT_PARAMETERS& p);
};

// ---- device lifecycle state (NxDeviceCore::life)
enum { NX_DEV_OK = 0, NX_DEV_LOST = 1, NX_DEV_NOTRESET = 2 };   // Wine d3d9_device_state
struct NxCursorState { uint8_t* bgra = nullptr; uint32_t w = 0, h = 0, hx = 0, hy = 0; int32_t x = 0, y = 0; bool visible = false; ~NxCursorState() { nxd3d_free(bgra); } };
struct NxDevLife : NxDevLifeBase {
    NxChainCore** chains = nullptr; uint32_t nchains = 0, implicit_chains = 0;   // implicit_chains = Wine wined3d device->swapchain_count (the implicit chains; set by nx_chain_create); [0] = the implicit chain, then the additional ones (not owned: each chain removes itself when destroyed)
    NxObj* ds = nullptr;                                    // auto depth stencil surface (implicit), null without EnableAutoDepthStencil
    D3DDEVICE_CREATION_PARAMETERS cp = {};
    uint32_t state = NX_DEV_OK; bool in_scene = false, ex = false, dialog = false; int32_t gpu_prio = 0; uint32_t latency = 3, frame = 0; uint32_t prof = 0;
    NxCursorState cur;
    ~NxDevLife();
    NX_MUST_USE bool add_chain(NxChainCore* c); void del_chain(NxChainCore* c);
};
inline NxDevLife* nx_life(NxObj* dev) { return static_cast<NxDevLife*>(static_cast<NxDeviceCore*>(dev)->life); }
NX_MUST_USE NxDevLife* nx_life_make(NxDeviceCore* d, bool ex, int prof);   // nullptr = out of memory; allocates and attaches the (empty) lifecycle state
HRESULT nx_make_auto_ds(NxDeviceCore* d, const NxPPEff& e, const D3DPRESENT_PARAMETERS& p, NxObj** out);   // implicit depth stencil surface for (eff size / samples, AutoDepthStencilFormat)
HRESULT nx_chain_create(NxDeviceCore* d, const D3DPRESENT_PARAMETERS& p, const NxPPEff& e, bool implicit, NxChainCore** out);
void nx_dev_bind_defaults(NxDeviceCore* d);   // CreateDevice / Reset: state defaults, RT 0 = back buffer 0 of chain 0, depth stencil = the auto depth stencil
uint64_t nx_dev_mem_accounted(NxObj* dev);    // bytes of the accounted resources (GetAvailableTextureMem); also usable by the creation functions
uint64_t nx_dev_mem_available(NxObj* dev);    // video memory minus accounted resources (clamped at 0)
HRESULT nx_dev_evict(NxObj* dev);
bool nx_dev_default_pool_blocks(NxObj* dev);   // d3d9 reset_enum_callback over the registry: a DEFAULT pool resource that blocks Reset
