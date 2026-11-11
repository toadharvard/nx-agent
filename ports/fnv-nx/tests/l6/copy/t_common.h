// harness of tests/l6/copy: counted checks per part, the device, the RPC to the GL replay process, helpers.
#pragma once
#include "d9impl.gen.h"
#include "res_tex.h"
#include "copy_util.h"
#include "glue_buf.h"
#include "dev_core.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
extern int g_checks[8], g_fails[8], g_pi; extern const char* g_data;
void t_part(char p);
bool t_check(bool ok, const char* what, const char* file, int line);
#define CHECK(c) t_check((c), #c, __FILE__, __LINE__)
#define CHECK_EQ(a, b) t_check((uint64_t)(a) == (uint64_t)(b), #a " == " #b, __FILE__, __LINE__)
#define CHECK_HR(a, hr) t_check((a) == (hr), #a " == " #hr, __FILE__, __LINE__)
FILE* t_open(const char* name);
struct TDev { NxDirect3D9* d3d; NxDevice9* dev; NxDevice9Ex* devex; NxDeviceCore* core; NxObj* obj; IDirect3DDevice9* i9; };
TDev t_dev(bool ex = false, uint32_t w = 64, uint32_t h = 64);
void t_scene(TDev& d, bool in);   // sets the lifecycle state's in_scene (a minimal NxDevLife)
extern int g_calls[64]; extern int g_profile;   // backend calls by function number
// image helpers (plain surfaces in any pool); the pixel bytes are the D3D layout of the format
IDirect3DSurface9* t_surf(TDev& d, int what /*0 RT 1 DS 2 plain*/, uint32_t w, uint32_t h, uint32_t fmt, D3DPOOL pool = D3DPOOL_DEFAULT, uint32_t ms = 0, bool lockable = true);
void t_put(IDirect3DSurface9* s, const uint8_t* bytes, uint32_t len);   // lock + write rows of the whole surface (bytes = tight rows)
bool t_get(IDirect3DSurface9* s, uint8_t* out, uint32_t len);            // lock read; false when the lock fails
uint32_t t_rowbytes(uint32_t fmt, uint32_t w);
uint32_t t_lcg(uint32_t* s);
inline uint32_t t_fcc(char a, char b, char c, char d) { return (uint32_t)(uint8_t)a | (uint32_t)(uint8_t)b << 8 | (uint32_t)(uint8_t)c << 16 | (uint32_t)(uint8_t)d << 24; }
void t_a(); void t_b(); void t_c(); void t_d();
