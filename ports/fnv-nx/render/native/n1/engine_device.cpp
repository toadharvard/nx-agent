// fnv-nx native renderer: the engine's D3D9 device as the sink of the translated path (translated.h).
// The engine creates its shader programs and writes many shader constants straight through IDirect3DDevice9 (BSShader
// code outside the replaced pipeline, e.g. light and fog arrays). HookD3DDevice swaps the device object's method table
// for a copy whose CreateVertexShader / CreatePixelShader record each program's bytecode hash (the key of the NXSP
// package) and whose Set{Vertex,Pixel}ShaderConstant{F,I,B} keep a copy of the register files; every call still
// reaches the device behind (the guest's null device until S6 replaces it). Then, per translated draw, the engine
// pass's constant maps are evaluated over that copy (engine_stubs.cpp).
#include "engine_stubs.h"
#include <string.h>

namespace nx {
namespace engine {

enum { kSlots = 120, kVsCreate = 91, kVsF = 94, kVsI = 96, kVsB = 98, kPsCreate = 106, kPsF = 109, kPsI = 111, kPsB = 113 };
static uintptr_t g_table[kSlots];
static uintptr_t g_orig[kSlots];
static void* g_device = nullptr;
static TranslatedConstants g_regs;
struct Prog { const void* obj; uint64_t hash; };
enum { kProgs = 8192 };
static Prog g_progs[kProgs];
static uint32_t g_nProgs = 0;

TranslatedConstants& DeviceConstants() { return g_regs; }

void RegisterShaderObject(const void* obj, uint64_t hash) {
    if (!obj) return;
    for (uint32_t i = 0; i < g_nProgs; i++) if (g_progs[i].obj == obj) { g_progs[i].hash = hash; return; }
    if (g_nProgs < kProgs) g_progs[g_nProgs++] = {obj, hash};
}
bool ShaderObjectHash(const void* obj, uint64_t* hash) {
    for (uint32_t i = 0; obj && i < g_nProgs; i++) if (g_progs[i].obj == obj) { *hash = g_progs[i].hash; return true; }
    return false;
}

typedef int32_t (*CreateFn)(void*, const uint32_t*, void**);
typedef int32_t (*ConstFn)(void*, uint32_t, const void*, uint32_t);

static int32_t Create(void* self, const uint32_t* code, void** out, int slot) {
    const int32_t hr = ((CreateFn)g_orig[slot])(self, code, out);
    if (hr >= 0 && out && *out) {
        const uint32_t bytes = TranslatedStore::BytecodeBytes(code, 1u << 20);
        if (bytes) RegisterShaderObject(*out, TranslatedStore::Hash(code, bytes));
    }
    return hr;
}
static int32_t CreateVs(void* self, const uint32_t* code, void** out) { return Create(self, code, out, kVsCreate); }
static int32_t CreatePs(void* self, const uint32_t* code, void** out) { return Create(self, code, out, kPsCreate); }

static void Store(void* dst, uint32_t regs, uint32_t elem, uint32_t start, const void* src, uint32_t count) {
    if (!src || start >= regs) return;
    if (count > regs - start) count = regs - start;
    memcpy((uint8_t*)dst + (size_t)start * elem, src, (size_t)count * elem);
}
static int32_t VsF(void* s, uint32_t r, const void* d, uint32_t n) { Store(g_regs.vsc, 256, 16, r, d, n); return ((ConstFn)g_orig[kVsF])(s, r, d, n); }
static int32_t VsI(void* s, uint32_t r, const void* d, uint32_t n) { Store(g_regs.vsi, 16, 16, r, d, n); return ((ConstFn)g_orig[kVsI])(s, r, d, n); }
static int32_t VsB(void* s, uint32_t r, const void* d, uint32_t n) { Store(g_regs.vsb, 16, 4, r, d, n); return ((ConstFn)g_orig[kVsB])(s, r, d, n); }
static int32_t PsF(void* s, uint32_t r, const void* d, uint32_t n) { Store(g_regs.psc, 256, 16, r, d, n); return ((ConstFn)g_orig[kPsF])(s, r, d, n); }
static int32_t PsI(void* s, uint32_t r, const void* d, uint32_t n) { Store(g_regs.psi, 16, 16, r, d, n); return ((ConstFn)g_orig[kPsI])(s, r, d, n); }
static int32_t PsB(void* s, uint32_t r, const void* d, uint32_t n) { Store(g_regs.psb, 16, 4, r, d, n); return ((ConstFn)g_orig[kPsB])(s, r, d, n); }

bool HookD3DDevice(void* device) {
    if (!device) return false;
    uintptr_t* vptr = *(uintptr_t**)device;
    if (vptr == g_table) return true;   // hooked already
    memcpy(g_orig, vptr, sizeof g_orig);
    memcpy(g_table, vptr, sizeof g_table);
    g_table[kVsCreate] = (uintptr_t)&CreateVs; g_table[kPsCreate] = (uintptr_t)&CreatePs;
    g_table[kVsF] = (uintptr_t)&VsF; g_table[kVsI] = (uintptr_t)&VsI; g_table[kVsB] = (uintptr_t)&VsB;
    g_table[kPsF] = (uintptr_t)&PsF; g_table[kPsI] = (uintptr_t)&PsI; g_table[kPsB] = (uintptr_t)&PsB;
    *(uintptr_t**)device = g_table;
    g_device = device;
    return true;
}
void* HookedD3DDevice() { return g_device; }

} // namespace engine
} // namespace nx
