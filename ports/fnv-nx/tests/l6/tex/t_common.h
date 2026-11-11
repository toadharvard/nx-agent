// test harness of tests/l6/tex: counted checks per part (a creation legality, b levels / descs / identity / refcounts, c locks / dirty / pitch, d autogen / filter / LOD, e volumes + cubes, h format table completeness)
#pragma once
#include "d9impl.gen.h"
#include "res_tex.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
extern int g_checks[8], g_fails[8], g_pi; extern const char* g_data;
void t_part(char p);
bool t_check(bool ok, const char* what, const char* file, int line);
#define CHECK(c) t_check((c), #c, __FILE__, __LINE__)
#define CHECK_EQ(a, b) t_check((uint64_t)(a) == (uint64_t)(b), #a " == " #b, __FILE__, __LINE__)
#define CHECK_HR(a, hr) t_check((a) == (hr), #a " == " #hr, __FILE__, __LINE__)
struct TEnv { NxDirect3D9* d3d; NxDevice9* dev; NxDevice9Ex* devex; NxDeviceCore* core; NxObj* obj; };
TEnv t_env(bool ex = false);
FILE* t_open(const char* name);
// recording backend
enum { R_CREATE = 1, R_DESTROY, R_UPDATE, R_READ, R_GEN };
struct Rec { int op; uint32_t image, level, layer; NxBox3 box; uint32_t row, slice; uint8_t b0; NxImageDesc4 desc; };
extern Rec g_rec[1 << 16]; extern int g_nrec, g_profile; extern bool g_refuse;
int t_count(int op);
void t_clear();
void t_a(); void t_b(); void t_c(); void t_d(); void t_e(); void t_h();
