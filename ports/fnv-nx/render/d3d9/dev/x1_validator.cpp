// x1_validator.cpp: DebugSetMute, Direct3DShaderValidatorCreate9 and IDirect3DShaderValidator9, exactly what Wine 9.0 does (owner decision 2026-11-11 "Wine 9.0 as an acceptable implementation").
// Source: wine-9.0 dlls/d3d9/d3d9_main.c (fetched, not from memory). Where Wine's body is a stub, the body does what Wine's does plus one log line (fnvnx_wine_fixme; ids = rows of verify/wine_fixme.tsv):
//   DebugSetMute              `/* nothing to do */` (d3d9_exports.cpp)      id 52
//   Direct3DShaderValidatorCreate9   FIXME("Returning stub validator %p."); returns the ONE static object (the same pointer every call, never freed)      id 20
//   QueryInterface            WARN("%s not implemented, returning E_NOINTERFACE."); *out = NULL; E_NOINTERFACE for EVERY iid (IUnknown included): a real QueryInterface body (COM contract for a foreign iid), no log line
//   AddRef / Release          TRACE only in Wine (that line is the log line here): return 2 / 1 (a static object has no count)      id 53 / 54
//   Begin / Instruction / End WARN("... stub!"); S_OK, the arguments are not looked at                                                                    id 22 / 23 / 24
// Layer-private exception: QueryInterface(IID_NxObj) (obj.h: the identity query nx_obj_of / nx_face use on every object of the layer, never an application iid) answers the object itself.
// Evidence for the callers (D3DX call sites, shim/include/d3d9.h comment): D3DX calls Create with no arguments, Begin(callback, context, arg3), Instruction(file, line, tokens, count), End() and Release(),
// all stdcall, and treats a NULL Create result or a failing Begin as "no validation"; it never calls QueryInterface.
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "../gen/d9impl.gen.h"
#include "face.h"
extern "C" void fnvnx_wine_fixme(uint32_t id);   // loader/include/fnvnx_reach.h: writes one line and returns
enum : uint32_t { NX_WINE_FIXME_ShaderValidatorCreate9 = 20, NX_WINE_FIXME_ShaderValidator_Begin = 22,
                  NX_WINE_FIXME_ShaderValidator_Instruction = 23, NX_WINE_FIXME_ShaderValidator_End = 24,
                  NX_WINE_FIXME_ShaderValidator_AddRef = 53, NX_WINE_FIXME_ShaderValidator_Release = 54 };   // verify/wine_fixme.tsv
HRESULT STDMETHODCALLTYPE NxShaderValidator9::QueryInterface(REFIID riid, void** out) {
    if (!out) return E_POINTER;   // Wine writes *out unconditionally (a fault); the defined error is the layer's closed choice (README "Decided with doubt")
    if (!memcmp(&riid, &IID_NxObj, sizeof(GUID))) { *out = static_cast<NxObj*>(this); return S_OK; }
    *out = nullptr; return E_NOINTERFACE;
}
ULONG STDMETHODCALLTYPE NxShaderValidator9::AddRef() { fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_AddRef); return 2; }
ULONG STDMETHODCALLTYPE NxShaderValidator9::Release() { fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_Release); return 1; }
HRESULT STDMETHODCALLTYPE NxShaderValidator9::Begin(D3DSHADERVALIDATORCALLBACK, void*, DWORD_PTR) { fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_Begin); return S_OK; }
HRESULT STDMETHODCALLTYPE NxShaderValidator9::Instruction(const char*, int, const DWORD*, unsigned int) { fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_Instruction); return S_OK; }
HRESULT STDMETHODCALLTYPE NxShaderValidator9::End() { fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_End); return S_OK; }

// the one static object behind its guest face (face.h), constant-initialised: no code runs to make it and the create function is "log line + address"
struct NxValidatorCell { uint32_t face[NX_FACE_SIZE / 4]; NxShaderValidator9 obj; constexpr NxValidatorCell() : face{}, obj() {} };
static_assert(offsetof(NxValidatorCell, obj) == NX_FACE_SIZE, "the object sits right behind its face");
static NxValidatorCell g_mem;   // static storage, constant initialisation (the constructor above is constexpr)
IDirect3DShaderValidator9* nx_Direct3DShaderValidatorCreate9(void) {
    fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidatorCreate9);
    return &g_mem.obj;
}
