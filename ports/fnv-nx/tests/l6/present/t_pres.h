// tests/l6/present: shared declarations. The swapchain (= the client area of the window) is SWW x SWH; the harness is tests/l6/copy/t_rpc.cpp.
#pragma once
#include "t_common.h"   // tests/l6/copy: CHECK macros, parts, g_calls
#include "dev_hooks.h"
#include "d3d9_adapter.h"
#include "glue_stream.h"
#include "env_table.gen.h"
enum { SWW = 48, SWH = 40, WIN0 = 0x100, WIN1 = 0x200 };   // WIN0: client (0,0)-(48,40); WIN1: client (10,20)-(58,60) on the screen
struct PSpy { NxPresent p; uint32_t prev_fn; bool has_ramp; uint16_t ramp[768]; };
extern PSpy g_ps[1024]; extern int g_nps, g_nvbl; extern bool g_fail_alloc; extern int32_t g_ospos[2];
// helpers (t_parts.cpp)
struct Model { uint8_t img[SWW * SWH * 4]; };      // the swapchain image as RGBA8, row 0 = top
IDirect3DDevice9* mk_dev(bool ex, void* win, uint32_t w, uint32_t h, uint32_t fmt, uint32_t count, uint32_t swap, uint32_t interval, D3DPRESENT_PARAMETERS* pp_out = nullptr, HRESULT* hr = nullptr);
uint32_t pix(uint32_t seed, uint32_t x, uint32_t y);
bool fill_bb(IDirect3DDevice9* d, IDirect3DSurface9* bb, uint32_t w, uint32_t h, uint32_t fmt, uint32_t seed);   // back buffer := pix(seed, x, y), through UpdateSurface
bool read_bb(IDirect3DDevice9* d, IDirect3DSurface9* bb, uint32_t w, uint32_t h, uint32_t fmt, uint32_t seed);   // true iff the back buffer equals pix(seed, ...) (alpha ignored for X8R8G8B8)
void model_present(Model& m, uint32_t seed, bool alpha, uint32_t bw, uint32_t bh, const RECT& s, const RECT& d, const D3DGAMMARAMP* ramp);
void model_cursor(Model& m, const uint32_t* cur, int cw, int ch, int cx, int cy, const D3DGAMMARAMP* ramp);
bool front_equals(IDirect3DDevice9* d, const Model& m);        // GetFrontBufferData == the model
bool frame_file_equals(uint32_t frame, const Model& m);
extern const char* g_frames_dir;
