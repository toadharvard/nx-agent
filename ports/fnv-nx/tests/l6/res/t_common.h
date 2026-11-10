// test harness of tests/l6/res: counted checks per part (a create validation, b locks, c FVF / declarations, d shaders, e queries); `PART x: n checks, f failed`
#pragma once
#include "d9impl.gen.h"
#include <stdio.h>
#include <initializer_list>
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
void t_buf(); void t_decl(); void t_fvf(); void t_shader(); void t_query();
// test backend of the query hooks (t_env.cpp)
extern bool g_q_ready; extern int g_q_issues, g_q_flushes; extern DWORD g_q_last_flags;
// oracle files written by test_res.py: one line per case
FILE* t_open(const char* name);
