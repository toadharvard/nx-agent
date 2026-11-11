/* E2E FIXTURE, NOT PART OF ANY BUILD OF THE GAME. It stands where the lifter runtime will be linked: it defines the one
 * symbol the boot glue takes from it, so that loader/guest/e2e.sh can run the start sequence to its end in the emulator.
 *
 *   fnvnx_lift_enter  records the call, checks what the start sequence promised the lifter (return address at [esp], TEB
 *                     words, stack bounds, TLS vector). A module's DllMain (an address that is not the exe's entry) returns
 *                     1, every TLS callback and the exe entry return 0.
 *   with -DE2E_ALL_PROVIDERS (a TEST resolver, only here): fnvnx_import_key is wrapped (-Wl,--wrap) so that a platform
 *                     import the generated table lacks gets a test value, only to prove the path after S4 when the platform
 *                     does not provide every function the lifted modules import yet. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "fnvnx_importkey.h"
#include "fnvnx_start.h"

#define E2E_EXIT_BAD 0xE2E0u
#define E2E_TEST_VALUE 0x001ffff0u

static unsigned g_test_values, g_dllmain;

#ifdef E2E_ALL_PROVIDERS
uint32_t __real_fnvnx_import_key(const char *dll, const char *name, uint32_t ordinal);
uint32_t __wrap_fnvnx_import_key(const char *dll, const char *name, uint32_t ordinal) {
    uint32_t v = __real_fnvnx_import_key(dll, name, ordinal);
    if (v) return v;
    g_test_values++;
    return E2E_TEST_VALUE;
}
#endif

static uint32_t rd(uint32_t va) { return *(const uint32_t *)(uintptr_t)va; }

uint32_t fnvnx_lift_enter(uint32_t eip, uint32_t esp, uint32_t teb) {
    const fnvnx_proc *p = fnvnx_start_proc();
    int is_exe = eip == p->exe.entry_va;
    int ok = rd(esp) == FNVNX_START_SENTINEL && rd(teb) == 0xffffffffu && rd(teb + 0x18) == teb &&
             rd(teb + 4) == p->main.stack_base && rd(teb + 8) == p->main.stack_limit && teb == p->main.teb_va &&
             esp >= p->main.stack_limit && esp < p->main.stack_base && !(esp & 15) &&
             rd(teb + 0x2c) == p->main.tls_vec_va && rd(p->main.tls_vec_va) == p->main.tls_block_va &&
             p->main.tls_block_va != 0;
    if (!is_exe) { g_dllmain++; printf("e2e: guest call %08x\n", (unsigned)eip); }
    if (!is_exe) return ok ? 1u : E2E_EXIT_BAD;
    printf("e2e: lift_enter %08x %08x %08x\n", (unsigned)eip, (unsigned)esp, (unsigned)teb);
    printf("e2e: test values %u, module calls %u, imports bound %u\n", g_test_values, g_dllmain, (unsigned)p->exe.imports_bound);
    if (!ok) { printf("e2e: FAIL: [esp]/TEB words/stack bounds/TLS vector differ from the start sequence's contract\n"); return E2E_EXIT_BAD; }
    printf("e2e: contract ok\n");
    return 0;
}
