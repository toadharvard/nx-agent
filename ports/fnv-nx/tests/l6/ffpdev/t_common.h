// harness of tests/l6/ffpdev: counted checks per part, the product device over the GL backend (libnxgl4 in an LP64 replay process, RPC like tests/l6/copy), hex word files written by the python oracle.
#pragma once
#include "d9impl.gen.h"
#include "res_tex.h"
#include "copy_util.h"
#include "draw_core.h"
#include "ffp_dev.h"
#include "dev_core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
#include <string>
extern int g_checks[8], g_fails[8], g_pi, g_calls[64]; extern const char* g_data;
void t_part(char p);
bool t_check(bool ok, const char* what, const char* file, int line);
#define CHECK(c) t_check((c), #c, __FILE__, __LINE__)
#define CHECK_EQ(a, b) t_check((uint64_t)(a) == (uint64_t)(b), #a " == " #b, __FILE__, __LINE__)
#define CHECK_HR(a, hr) t_check((a) == (hr), #a " == " #hr, __FILE__, __LINE__)
struct TDev { NxDirect3D9* d3d; NxDevice9* dev; NxDeviceCore* core; IDirect3DDevice9* i9; };
TDev t_dev(uint32_t w = 64, uint32_t h = 64);
void t_target(TDev& d, uint32_t w = 64, uint32_t h = 64);   // RT 0 (A8R8G8B8), no depth-stencil
// a case file: lines of whitespace separated tokens; numbers are hex words (floats are their bit patterns)
struct Lines { std::vector<std::vector<uint32_t>> w; std::vector<char> tag; size_t at = 0; bool open(const char* name);
               bool next(char* t, std::vector<uint32_t>** v) { if (at >= tag.size()) return false; *t = tag[at]; *v = &w[at]; at++; return true; } };
void t_a(); void t_b(); void t_c(); void t_d();
void t_apply(TDev& d, char tag, const std::vector<uint32_t>& v, IDirect3DBaseTexture9** tex);   // one state line of the case files (shared by parts a, b, c)
