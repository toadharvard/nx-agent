// test harness of tests/l6/devlife: counted checks per part (a CreateDevice domain, b refcounts / identity, c Reset, d swap chains / Present, e gamma / cursor / raster / memory, f Ex methods)
#pragma once
#include "d9impl.gen.h"
#include "res_tex.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "env_table.gen.h"
#include "d3d9_adapter.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
extern int g_checks[8], g_fails[8], g_pi; extern const char* g_data;
void t_part(char p);
bool t_check(bool ok, const char* what, const char* file, int line);
#define CHECK(c) t_check((c), #c, __FILE__, __LINE__)
#define CHECK_EQ(a, b) t_check((uint64_t)(a) == (uint64_t)(b), #a " == " #b, __FILE__, __LINE__)
#define CHECK_HR(a, hr) t_check((uint32_t)(a) == (uint32_t)(hr), #a " == " #hr, __FILE__, __LINE__)
FILE* t_open(const char* name);
// windows of the platform hook (handle -> client rect on the screen): 0x100 800x600 at (10,20), 0x200 an empty client area, 0x300 the display size at (0,0), 0x400 100x50 at (5,5); anything else is no window
enum { W_A = 0x100, W_EMPTY = 0x200, W_SCREEN = 0x300, W_SMALL = 0x400, W_BAD = 0x999 };
extern int g_profile; void t_screen(uint32_t* w, uint32_t* h);
// recording backend
enum { R_CREATE = 1, R_DESTROY, R_UPDATE, R_READ };
struct Rec { int op; uint32_t image; NxImageDesc4 desc; };
extern Rec g_rec[1 << 16]; extern int g_nrec; int t_count(int op); void t_clear(); uint32_t t_created(int nth);   // image id of the nth R_CREATE record since t_clear extern bool g_refuse;
struct PRec { NxPresent p; uint16_t gamma[768]; bool has_gamma; int seq; };
extern PRec g_pres[256]; extern int g_npres, g_seq;
struct CRec { uint32_t w, h; int32_t x, y; uint8_t px0[4]; int seq; };
extern CRec g_cur[64]; extern int g_ncur, g_nidle, g_nfront, g_nvblank; extern uint64_t g_qpc, g_qpf; extern int32_t g_ospos[2]; extern int g_present_result;
void t_clear_hooks();
// a device made through the real CreateDevice path
D3DPRESENT_PARAMETERS t_pp(bool windowed = true, uint32_t swap = D3DSWAPEFFECT_DISCARD, uint32_t count = 1);
IDirect3D9* t_d3d(bool ex);
IDirect3DDevice9* t_dev(IDirect3D9* d3d, D3DPRESENT_PARAMETERS* pp, HRESULT* hr = nullptr, void* focus = (void*)W_A, DWORD beh = D3DCREATE_SOFTWARE_VERTEXPROCESSING);
inline NxObj* O(IUnknown* p) { return nx_obj_of(p); }
void t_a(); void t_b(); void t_c(); void t_d(); void t_e(); void t_f();
