/* d9_validator_compat.h: IDirect3DShaderValidator9 and its callback type, absent from the mingw-w64 <d3d9.h> (11.0.1) and needed by the generated wrapper
 * (d9wrap.gen.h wraps every interface of d3d9_spec.json). Declaration identical to shim/include/d3d9.h (D3DX / Wine 9.0 dlls/d3d9/d3d9_main.c: names, slot order 3..5).
 * Force-included by build.sh after <d3d9.h> is reachable; no IID (the spec has `iid: null`). */
#ifndef D9_VALIDATOR_COMPAT_H
#define D9_VALIDATOR_COMPAT_H
#include <windows.h>
#include <d3d9.h>
#ifndef D3DSHADERVALIDATORCALLBACK
typedef HRESULT (STDMETHODCALLTYPE *D3DSHADERVALIDATORCALLBACK)(const char *file, int line, DWORD_PTR arg3, DWORD_PTR message_id, const char *message, void *context);
struct IDirect3DShaderValidator9 : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE Begin(D3DSHADERVALIDATORCALLBACK callback, void *context, DWORD_PTR arg3) = 0;
    virtual HRESULT STDMETHODCALLTYPE Instruction(const char *file, int line, const DWORD *tokens, unsigned int token_count) = 0;
    virtual HRESULT STDMETHODCALLTYPE End() = 0;
};
#endif
#endif
