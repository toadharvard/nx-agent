/* TEST FIXTURE of link/tests/real_reloc.py (--eh): the guest's startup and C++ exceptions work with the strict link, in the emulator.
 *   ctor      a static object's constructor ran before main (.init_array through __libc_init_array)
 *   throw     a thrown int is caught by type, through two frames with destructors (the unwinder finds the .eh_frame FDEs:
 *             crtbegin's frame_dummy registered them with __register_frame_info, which libgcc's unwind-dw2-fde.o defines)
 *   std       a std::runtime_error (libstdc++ cow-stdexcept.o, which names the _ITM_* entry points) keeps its message
 *   dtor      the destructors of the unwound frames ran, in order
 *   atexit    a static object's destructor is registered to run at exit (__libc_fini marker): checked by the host's exit path, not here */
#include <cstdio>
#include <stdexcept>

static int g_ctor;
struct Init { Init() { g_ctor = 1; } } g_init;
static int g_order = 1;
struct Guard { int want; explicit Guard(int w) : want(w) {} ~Guard() { if (g_order == want - 1) g_order = want; } };

static void inner() { Guard g(2); throw 42; }
static void outer() { Guard g(3); inner(); }
static void thrower() { throw std::runtime_error("fnvnx-eh"); }

int main() {
    int caught = 0, stdok = 0;
    try { outer(); } catch (int v) { caught = v; }
    try { thrower(); } catch (const std::exception &e) { stdok = std::string(e.what()) == "fnvnx-eh"; }
    bool ok = g_ctor == 1 && caught == 42 && g_order == 3 && stdok;
    printf("eh: ctor %s, throw %s, dtor order %s, std %s\n", g_ctor == 1 ? "ok" : "WRONG", caught == 42 ? "ok" : "WRONG", g_order == 3 ? "ok" : "WRONG", stdok ? "ok" : "WRONG");
    return ok ? 0 : 1;
}
