// harness of tests/l6/draw: counted checks per part, the product device over a RECORDING backend (nxd3d_gpu_call decodes every submitted stream with a decoder written from nx/gpu_abi.h + nxgpu4.h).
#pragma once
#include "d9impl.gen.h"
#include "res_tex.h"
#include "copy_util.h"
#include "draw_core.h"
#include "dev_core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
#include <functional>
#include <string>
extern int g_checks[8], g_fails[8], g_pi; extern const char* g_data;
void t_part(char p);
bool t_check(bool ok, const char* what, const char* file, int line);
#define CHECK(c) t_check((c), #c, __FILE__, __LINE__)
#define CHECK_EQ(a, b) t_check((uint64_t)(a) == (uint64_t)(b), #a " == " #b, __FILE__, __LINE__)
#define CHECK_HR(a, hr) t_check((a) == (hr), #a " == " #hr, __FILE__, __LINE__)
FILE* t_open(const char* name);
// ---- recording backend
struct Ev { uint32_t op, n, a[64]; std::vector<uint8_t> blob; uint32_t lay[50]; uint32_t state; };   // lay / state: for a draw, the layout words and the state id the replay would use           // a decoded op; blob = the bytes of BindUbo / BindVtxN / BindIdx at submit time
struct Call { uint32_t fn; uint64_t a[3]; NxShaderD3D sd; uint8_t l[32]; uint32_t ret; std::vector<uint8_t> code; };   // a backend call other than the stream plumbing (shader create / destroy, sampler, image)
extern std::vector<Ev> g_ev; extern std::vector<Call> g_calls; extern uint32_t g_shader_fail;   // g_shader_fail: the next SHADER_CREATE_D3D returns 0
void t_clear();                                  // flush the stream, forget the logs
void t_flush();                                  // submit the pending stream (the events appear)
int t_count(uint32_t op); const Ev* t_nth(uint32_t op, int n);
int t_ncalls(uint32_t fn);
// ---- device
struct TDev { NxDirect3D9* d3d; NxDevice9* dev; NxDevice9Ex* devex; NxDeviceCore* core; IDirect3DDevice9* i9; };
TDev t_dev(bool ex = false, uint32_t w = 64, uint32_t h = 64);
void t_target(TDev& d, uint32_t w = 64, uint32_t h = 64, bool depth = true);       // RT 0 (A8R8G8B8) and depth-stencil (D24S8) of the device
IDirect3DVertexBuffer9* t_vb(TDev& d, uint32_t bytes, uint8_t fill = 0);
IDirect3DIndexBuffer9* t_ib(TDev& d, uint32_t bytes, bool is32 = false);
IDirect3DVertexDeclaration9* t_decl(TDev& d, const D3DVERTEXELEMENT9* e);
IDirect3DVertexShader9* t_vs(TDev& d, const char* hex);                           // hex = the token stream (words, lower case hex, space separated)
IDirect3DPixelShader9* t_ps(TDev& d, const char* hex);
std::vector<uint32_t> t_hex(const char* s);
uint32_t t_lcg(uint32_t* s);
// FFP test doubles (nx_ffp_vs_program / nx_ffp_ps_program are D5b's): g_ffp_* select what they return
extern uint32_t g_ffp_vs_id, g_ffp_ps_id; extern uint8_t g_ffp_ubo_v[64], g_ffp_ubo_p[32];
void t_a(); void t_b(); void t_c(); void t_d(); void t_e(); void t_f();
