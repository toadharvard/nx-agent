// part e of test_bridge.py: private data D3DSPD_IUNKNOWN with app_unk_guest.cpp. The "guest object" is a raw x86-shaped COM object (a table of 32-bit code
// addresses); the test's fnvnx_guest_invoke plays the lifter runtime: it calls the address with the stack words as cdecl arguments and records the call.
#include <stdio.h>
#include "../t_keep.h"
#include "d9impl.gen.h"
extern "C" { struct fnvnx_guest_ret { uint32_t eax, edx; double st0; };
static uint32_t g_calls, g_last_addr, g_bad;
void fnvnx_guest_invoke(uint32_t addr, uint32_t ecx, uint32_t nargs, const uint32_t* args, uint32_t kind, fnvnx_guest_ret* out) {
    g_calls++; g_last_addr = addr; if (nargs != 1 || kind != 0 || ecx != 0) g_bad++;
    out->eax = ((uint32_t (*)(uint32_t))(uintptr_t)addr)(args[0]); } }
struct GObj { uint32_t vt; int refs; };
static uint32_t g_qi(uint32_t) { return 0x80004002u; }
static uint32_t g_addref(uint32_t self) { return (uint32_t)++((GObj*)(uintptr_t)self)->refs; }
static uint32_t g_release(uint32_t self) { return (uint32_t)--((GObj*)(uintptr_t)self)->refs; }
static int fails, checks;
#define CHECK(...) do { checks++; if (!(__VA_ARGS__)) { fails++; printf("FAIL e: %s (line %d)\n", #__VA_ARGS__, __LINE__); } } while (0)
int main() {
    static uint32_t vt[3] = {(uint32_t)(uintptr_t)g_qi, (uint32_t)(uintptr_t)g_addref, (uint32_t)(uintptr_t)g_release};
    GObj g = {(uint32_t)(uintptr_t)vt, 1}; void* gp = &g; static const GUID k = {7, 7, 7, {7}};
    NxDirect3D9* d = t_new<NxDirect3D9>(); NxObj* o = d; o->asRoot(1);
    CHECK(o->setPrivateData(k, &gp, 4, D3DSPD_IUNKNOWN) == D3D_OK); CHECK(g.refs == 2 && g_calls == 1 && g_last_addr == vt[1]);
    void* q = nullptr; DWORD sz = 4; CHECK(o->getPrivateData(k, &q, &sz) == D3D_OK && q == gp && g.refs == 3 && g_calls == 2);
    CHECK(o->setPrivateData(k, &gp, 4, D3DSPD_IUNKNOWN) == D3D_OK); CHECK(g.refs == 3 && g_calls == 4 && g_last_addr == vt[2]);   // AddRef new, then Release old
    CHECK(o->freePrivateData(k) == D3D_OK); CHECK(g.refs == 2 && g_calls == 5 && g_last_addr == vt[2]);
    CHECK(o->setPrivateData(k, &gp, 4, D3DSPD_IUNKNOWN) == D3D_OK); CHECK(d->Release() == 0); CHECK(g.refs == 2 && g_calls == 7);   // destruction releases the entry
    CHECK(g_bad == 0);
    printf("PART e: %d checks, %d failed\n", checks, fails); return fails != 0;
}
