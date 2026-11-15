/* bootrt: the symbols the boot machine gives the host-target objects it loads (the renderer library libnxgl4.so, translator@host objects):
 * the embedder callbacks nxgl4_guest_ptr / nxgl4_on_present and the proven-unreachable assertion fnvnx_reach_violation of hostabi/include/fnvnx/reach.h
 * (fnvnx_assume_violation is not defined here: no gate certifies its facts).
 * Freestanding: no libc, no system call. Protocol: the embedder (the boot machine) registers all three callbacks with nxgl4_shim_set before it loads the
 * renderer. The exit callback is the host exit slot: it receives the full 32-bit exit code and ends the process (the boot machine writes OUT.json,
 * which carries the code and its fact id, first). fnvnx_reach_violation exits only through that slot. */
#include <stdint.h>

#include "fnvnx/reach.h"

static void *(*g_ptr)(uint64_t, uint64_t);
static void (*g_present)(uint32_t);
static void (*g_exit)(uint32_t);

void nxgl4_shim_set(void *(*ptr)(uint64_t, uint64_t), void (*present)(uint32_t), void (*exit_cb)(uint32_t)) {
    g_ptr = ptr;
    g_present = present;
    g_exit = exit_cb;
}

void *nxgl4_guest_ptr(uint64_t addr, uint64_t size) { return g_ptr(addr, size); }

void nxgl4_on_present(uint32_t frame) {
    g_present(frame);
}

void fnvnx_reach_violation(uint32_t fact_id) {
    g_exit(FNVNX_REACH_EXIT | (fact_id & 0xFFFFFFu));   /* the slot ends the process (protocol) */
    __builtin_unreachable();
}
