/* E2E FIXTURE, NOT PART OF ANY BUILD OF THE GAME. It stands where the lifter runtime and the module loader will be
 * linked: it defines the two symbols the boot glue (boot.c) takes from them, so that loader/guest/e2e.sh can run
 * the start sequence to its end in the emulator.
 *
 *   fnvnx_lift_enter    records the call, checks what the start sequence promised the lifter (return address at
 *                       [esp], TEB words, stack bounds, TLS vector) and returns 0.
 *   fnvnx_module_import without E2E_ALL_PROVIDERS: no lifted module is loaded, so nothing is provided (0): the start
 *                       is refused, which is the correct outcome of this build. With -DE2E_ALL_PROVIDERS: a TEST
 *                       value for every import of a lifted PE module, only to prove the path after S4. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "fnvnx_start.h"

#define E2E_EXIT_BAD 0xE2E0u
#define E2E_MODULE_VALUE 0x001ffff0u   /* test value; the real one is an export address of the loaded module */

static unsigned g_module_slots;

uint32_t fnvnx_module_import(const char *dll, const char *name, uint32_t ordinal) {
    static const char *const mods[] = {"d3dx9_38", "binkw32", "libvorbisfile"};
    unsigned i;
    (void)name; (void)ordinal;
#ifdef E2E_ALL_PROVIDERS
    for (i = 0; i < sizeof mods / sizeof mods[0]; i++)
        if (!strncasecmp(dll, mods[i], strlen(mods[i])) && (dll[strlen(mods[i])] == '.' || !dll[strlen(mods[i])])) {
            g_module_slots++;
            return E2E_MODULE_VALUE;
        }
#else
    (void)dll; (void)mods; (void)i;
#endif
    return 0;
}

static uint32_t rd(uint32_t va) { return *(const uint32_t *)(uintptr_t)va; }

uint32_t fnvnx_lift_enter(uint32_t eip, uint32_t esp, uint32_t teb) {
    const fnvnx_proc *p = fnvnx_start_proc();
    int ok = rd(esp) == FNVNX_START_SENTINEL && rd(teb) == 0xffffffffu && rd(teb + 0x18) == teb &&
             rd(teb + 4) == p->main.stack_base && rd(teb + 8) == p->main.stack_limit && teb == p->main.teb_va &&
             esp >= p->main.stack_limit && esp < p->main.stack_base && !(esp & 15) &&
             rd(teb + 0x2c) == p->main.tls_vec_va && rd(p->main.tls_vec_va) == p->main.tls_block_va &&
             p->main.tls_block_va != 0 && eip == p->entry_va;
    printf("e2e: lift_enter %08x %08x %08x\n", (unsigned)eip, (unsigned)esp, (unsigned)teb);
    printf("e2e: module slots provided %u, imports bound %u\n", g_module_slots, (unsigned)p->imports_bound);
    if (!ok) { printf("e2e: FAIL: [esp]/TEB words/stack bounds/TLS vector differ from the start sequence's contract\n"); return E2E_EXIT_BAD; }
    printf("e2e: contract ok\n");
    return 0;
}
