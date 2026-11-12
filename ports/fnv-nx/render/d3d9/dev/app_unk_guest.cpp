// app_unk_guest.cpp: the application's IUnknown objects when the application is the lifted guest. The object is guest memory: [u] = address of its
// vtable of guest code addresses; slot 1 = AddRef, slot 2 = Release, both __stdcall with `this` as the only stack argument, result in eax (COM ABI on x86).
// The call goes through the lifter runtime's one primitive (lift/rt/imp.h, L3a): args are the x86 stack words in order, kind 0 = integer result.
#include "obj.h"
extern "C" {
struct fnvnx_guest_ret { uint32_t eax, edx; double st0; };
void fnvnx_guest_invoke(uint32_t addr, uint32_t ecx, uint32_t nargs, const uint32_t* args, uint32_t kind, struct fnvnx_guest_ret* out);
}
static ULONG slot_call(IUnknown* u, uint32_t slot) {
    uint32_t self = (uint32_t)(uintptr_t)u; const uint32_t* vt = (const uint32_t*)(uintptr_t)*(const uint32_t*)u;
    fnvnx_guest_ret r = {0, 0, 0.0}; fnvnx_guest_invoke(vt[slot], 0, 1, &self, 0, &r); return r.eax;
}
ULONG nxd3d_app_addref(IUnknown* u) { return slot_call(u, 1); }
ULONG nxd3d_app_release(IUnknown* u) { return slot_call(u, 2); }
