#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <atomic>
#define NS 8192
static std::atomic<uintptr_t> g_k[NS];
static std::atomic<unsigned long> g_c[NS];
extern "C" __attribute__((no_instrument_function)) void __cyg_profile_func_enter(void *fn, void *)
{
    uintptr_t k = (uintptr_t)fn;
    unsigned h = (unsigned)((k >> 3) * 2654435761u) % NS;
    for (unsigned n = 0; n < NS; n++, h = (h + 1) % NS) {
        uintptr_t c = g_k[h].load(std::memory_order_relaxed);
        if (c == k || (c == 0 && g_k[h].compare_exchange_strong(c, k)) || c == k) { g_c[h].fetch_add(1, std::memory_order_relaxed); return; }
    }
}
extern "C" __attribute__((no_instrument_function)) void __cyg_profile_func_exit(void *, void *) {}
__attribute__((destructor, no_instrument_function)) static void dump()
{
    const char *p = getenv("INSTR_OUT");
    if (!p) return;
    FILE *f = fopen(p, "w");
    for (unsigned h = 0; h < NS; h++) if (g_k[h]) { Dl_info i; if (dladdr((void *)g_k[h].load(), &i) && i.dli_sname) fprintf(f, "%s\t%lu\n", i.dli_sname, g_c[h].load()); }
    fclose(f);
}
