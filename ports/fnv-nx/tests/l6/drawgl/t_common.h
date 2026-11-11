// harness of tests/l6/drawgl: the product D3D9 device (-m32) over the GL backend (libnxgl4 sources in an LP64 replay process over llvmpipe, RPC + shared arena like tests/l6/copy).
// A case file (written by cases.py) is a script of D3D9 API calls; the player executes it and writes the render targets it is told to; the python side compares them with its CPU references.
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
extern int g_checks, g_fails; extern const char* g_data; extern int g_calls[64];
bool t_check(bool ok, const char* what, const char* file, int line);
#define CHECK(c) t_check((c), #c, __FILE__, __LINE__)
#define CHECK_HR(a, hr) t_check((a) == (hr), #a " == " #hr, __FILE__, __LINE__)
struct TDev { NxDirect3D9* d3d; NxDevice9* dev; NxDeviceCore* core; IDirect3DDevice9* i9; };
TDev t_dev(uint32_t w, uint32_t h);
void t_target(TDev& d, uint32_t w, uint32_t h);
void t_play(const char* script);   // t_play.cpp
