// fnv-nx: the d3dx9_38.dll module of the game (stream S6c-nine, guest side, ILP32).
//
// The game's d3dx9_38.dll imports are served by this directory: the module is registered with the platform's module
// table (platform/win32/module.cpp), which is where the exe's import slots and GetProcAddress find it. The platform's
// own export table does not list it: this file is the provider and names what it provides.
#include <string.h>

extern "C" {
typedef void* (*fnvnx_module_resolver)(const char* proc);
void fnvnx_register_module(const char* dll_name, fnvnx_module_resolver resolve);   // platform/win32/module.cpp

#define FNVNX_D3DX_EXPORTS(X)                                                                                  \
    X(D3DXAssembleShader) X(D3DXAssembleShaderFromFileA) X(D3DXCompileShader) X(D3DXCompileShaderFromFileA)   \
    X(D3DXCreateBuffer) X(D3DXCreateCubeTextureFromFileInMemory) X(D3DXCreateCubeTextureFromFileInMemoryEx)    \
    X(D3DXCreateTextureFromFileInMemory) X(D3DXCreateTextureFromFileInMemoryEx)                               \
    X(D3DXCreateVolumeTextureFromFileInMemory) X(D3DXCreateVolumeTextureFromFileInMemoryEx)                   \
    X(D3DXFindShaderComment) X(D3DXGetImageInfoFromFileInMemory) X(D3DXGetPixelShaderProfile)                 \
    X(D3DXGetShaderConstantTable) X(D3DXGetShaderConstantTableEx) X(D3DXGetShaderSize) X(D3DXGetShaderVersion) \
    X(D3DXGetVertexShaderProfile) X(D3DXLoadSurfaceFromMemory) X(D3DXLoadSurfaceFromSurface) X(D3DXMatrixInverse) \
    X(D3DXMatrixLookAtLH) X(D3DXMatrixMultiply) X(D3DXMatrixMultiplyTranspose) X(D3DXMatrixOrthoOffCenterLH)   \
    X(D3DXMatrixPerspectiveFovLH) X(D3DXMatrixRotationYawPitchRoll) X(D3DXMatrixTranspose) X(D3DXPlaneNormalize) \
    X(D3DXPlaneTransform) X(D3DXSaveTextureToFileA) X(D3DXVec3Normalize) X(D3DXVec3TransformCoord)            \
    X(D3DXVec3TransformNormal) X(D3DXVec4Transform)
#define FNVNX_D3DX_DECL(f) void f(void);   // defined in this directory
FNVNX_D3DX_EXPORTS(FNVNX_D3DX_DECL)
#undef FNVNX_D3DX_DECL
}

namespace {
struct Export { const char* name; void* fn; };
const Export kExports[] = {
#define FNVNX_D3DX_ENTRY(f) {#f, (void*)&f},
    FNVNX_D3DX_EXPORTS(FNVNX_D3DX_ENTRY)
#undef FNVNX_D3DX_ENTRY
};
void* resolve(const char* proc) {
    if (!proc) return nullptr;
    for (const Export& e : kExports)
        if (!strcmp(e.name, proc)) return e.fn;
    return nullptr;
}
}  // namespace

// priority 103 = FNVNX_INIT_MODULES (platform/os/os.h): module registrations before the game boot
__attribute__((constructor(103))) static void d3dx_register_module() { fnvnx_register_module("d3dx9_38.dll", resolve); }
