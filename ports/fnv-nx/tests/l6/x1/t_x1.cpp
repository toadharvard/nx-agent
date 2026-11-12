// driver of test_x1.py: DebugSetMute / Direct3DShaderValidatorCreate9 / IDirect3DShaderValidator9 = what Wine 9.0 d3d9_main.c does (+ one fixme line per stub body)
#include <stdio.h>
#include <string.h>
#include <vector>
#include "d9impl.gen.h"
#include "face.h"
#include "d3d9_adapter.h"
extern "C" { void WINAPI DebugSetMute(void); IDirect3DShaderValidator9* WINAPI Direct3DShaderValidatorCreate9(void); void fnvnx_wine_fixme(uint32_t id); }
static std::vector<uint32_t> g_fix;
extern "C" void fnvnx_wine_fixme(uint32_t id) { g_fix.push_back(id); }
ULONG nxd3d_app_addref(IUnknown*) { return 0; } ULONG nxd3d_app_release(IUnknown*) { return 0; }
uint32_t nx_bridge_keytab(const NxClassInfo*) { return 0x1234; }
static const GUID IID_IUnknown = {0, 0, 0, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
static int fails, checks; static const char* part = "b";
#define CHECK(...) do { checks++; if (!(__VA_ARGS__)) { fails++; printf("FAIL %s: %s (line %d)\n", part, #__VA_ARGS__, __LINE__); } } while (0)
static void done(const char* next) { printf("PART %s: %d checks, %d failed\n", part, checks, fails); part = next; checks = 0; fails = 0; }
static int g_cb; static HRESULT cb(const char*, int, DWORD_PTR, DWORD_PTR, const char*, void*) { g_cb++; return 0; }
int main() {
    // b: Create, Begin / Instruction / End, DebugSetMute
    DebugSetMute(); CHECK(g_fix.size() == 1 && g_fix[0] == 52); g_fix.clear();      // Wine: nothing to do; one log line, id 52
    IDirect3DShaderValidator9* v = Direct3DShaderValidatorCreate9(); CHECK(v != nullptr); CHECK(g_fix.size() == 1 && g_fix[0] == 20);
    CHECK(Direct3DShaderValidatorCreate9() == v); CHECK(nx_Direct3DShaderValidatorCreate9() == v);   // the ONE static object
    g_fix.clear(); static const DWORD tok[] = {0xFFFE0101, 0x00000001, 0x800F0000, 0x90E40000, 0x0000FFFF}, bad[] = {0xdeadbeef};
    CHECK(v->Begin(cb, (void*)0x77, 1) == S_OK); CHECK(v->Begin(nullptr, nullptr, 0) == S_OK);
    CHECK(v->Instruction("f.vsh", 3, tok, 5) == S_OK); CHECK(v->Instruction(nullptr, -1, bad, 1) == S_OK); CHECK(v->Instruction(nullptr, 0, nullptr, 0) == S_OK);   // the arguments are not looked at
    CHECK(v->End() == S_OK); CHECK(v->End() == S_OK);                           // no state: End without Begin is S_OK too
    CHECK(g_fix.size() == 7); CHECK(g_fix[0] == 22 && g_fix[1] == 22 && g_fix[2] == 23 && g_fix[3] == 23 && g_fix[4] == 23 && g_fix[5] == 24 && g_fix[6] == 24);
    done("c");
    // c: the callback is never called (Wine's Begin stores nothing)
    CHECK(g_cb == 0); done("d");
    // d: IUnknown lattice of the stub: AddRef 2, Release 1 always; QueryInterface answers E_NOINTERFACE with *out = NULL for EVERY iid, IUnknown included (Wine)
    g_fix.clear(); for (int i = 0; i < 5; i++) { CHECK(v->AddRef() == 2); CHECK(v->Release() == 1); } CHECK(g_fix.size() == 10 && g_fix[0] == 53 && g_fix[1] == 54);   // one line per call, ids 53 / 54
    g_fix.clear(); void* q = (void*)1; CHECK(v->QueryInterface(IID_IUnknown, &q) == E_NOINTERFACE); CHECK(q == nullptr);
    q = (void*)1; CHECK(v->QueryInterface(IID_IDirect3DDevice9, &q) == E_NOINTERFACE); CHECK(q == nullptr);
    static const GUID zero = {0, 0, 0, {0}}; q = (void*)1; CHECK(v->QueryInterface(zero, &q) == E_NOINTERFACE); CHECK(q == nullptr);
    CHECK(g_fix.size() == 0);   /* a real QueryInterface body: no log line */ CHECK(v->QueryInterface(IID_IUnknown, nullptr) == E_POINTER);
    q = nullptr; CHECK(v->QueryInterface(IID_NxObj, &q) == S_OK && q == static_cast<NxObj*>(static_cast<NxShaderValidator9*>(v)));   // the layer's private identity query
    CHECK(v->AddRef() == 2);                                                                                                         // ... took no reference
    void* f = nx_face(v); CHECK((uint8_t*)f + NX_FACE_SIZE == (uint8_t*)static_cast<NxShaderValidator9*>(v) && *(uint32_t*)f == 0x1234); CHECK(nx_unface(f) == (void*)static_cast<NxShaderValidator9*>(v));
    done("-"); return 0;
}
