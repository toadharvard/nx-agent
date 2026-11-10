// test harness: counted checks per part (b defaults/roundtrip, c validation, d state blocks, e refcounts); output lines `PART x: n checks, f failed`
#pragma once
#include "d9impl.gen.h"
#include <stdio.h>
extern const char* g_part; extern int g_checks[8], g_fails[8]; extern int g_part_i;
void t_part(const char* name);
bool t_check(bool ok, const char* what, const char* file, int line);
#define CHECK(c) t_check((c), #c, __FILE__, __LINE__)
#define CHECK_EQ(a, b) t_check((uint64_t)(a) == (uint64_t)(b), #a " == " #b, __FILE__, __LINE__)
#define CHECK_HR(a, hr) t_check((a) == (hr), #a " == " #hr, __FILE__, __LINE__)
void t_defaults(); void t_valid(); void t_blocks(); void t_refs();
// ---- test-only factory: the generated classes with the minimal definitions this test provides (t_factory.cpp)
struct TEnv { NxDirect3D9* d3d; NxDevice9* dev; };
TEnv t_env(uint32_t auto_depth = 1, uint32_t behavior = D3DCREATE_MIXED_VERTEXPROCESSING, uint32_t w = 640, uint32_t h = 480);
template <class T> T* t_child(const TEnv& e, uint32_t pool = D3DPOOL_DEFAULT) { T* o = new T; o->asChild(e.dev, pool); return o; }
NxSurface9* t_surface(const TEnv& e, uint32_t usage, uint32_t w, uint32_t h);   // device child with a GetDesc
NxVertexDeclaration9* t_decl(const TEnv& e);
struct TTracker : IUnknown {  // private-data IUnknown: `alive()` = the tracked object has not been destroyed
    int refs = 1;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void**) override { return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { return --refs; }
    void watch(IUnknown* o);
    bool alive() const { return refs > 1; }
};
inline NxObj* O(IUnknown* p) { return nx_obj_of(p); }
extern int g_preloads;
