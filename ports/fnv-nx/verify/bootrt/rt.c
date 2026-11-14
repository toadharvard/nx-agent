/* bootrt: the symbols the boot machine gives the host-target objects it loads (the renderer library libnxgl4.so, translator@host objects):
 * the embedder callbacks nxgl4_guest_ptr / nxgl4_on_present and the two proven-unreachable assertions of hostabi/include/fnvnx/reach.h.
 * Freestanding: no libc, the process's only calls are the write and exit_group system calls of Linux x86-64.
 * nxgl4_shim_set registers (guest ptr, present, exit) callbacks; the exit callback receives the full 32-bit exit code and ends the process (the boot
 * machine writes OUT.json first). A violation with no exit callback ends the process with status 1 after its log line: a library loaded outside the
 * boot machine has no other exit. */
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

void *nxgl4_guest_ptr(uint64_t addr, uint64_t size) { return g_ptr ? g_ptr(addr, size) : (void *)0; }

void nxgl4_on_present(uint32_t frame) {
    if (g_present)
        g_present(frame);
}

static long sys3(long n, long a, long b, long c) {
    long r;
    __asm__ volatile("syscall" : "=a"(r) : "0"(n), "D"(a), "S"(b), "d"(c) : "rcx", "r11", "memory");
    return r;
}

static char *hex(char *p, uint64_t v, int digits) {
    for (int i = digits - 1; i >= 0; i--)
        *p++ = "0123456789abcdef"[v >> (4 * i) & 15];
    return p;
}

static char *str(char *p, const char *s) {
    while (*s)
        *p++ = *s++;
    return p;
}

static void log_line(const char *line, int n) { sys3(1, 2, (long)line, n); }   /* write(2, line, n) */

static void __attribute__((noreturn)) leave(uint32_t code) {
    if (g_exit)
        g_exit(code);   /* the machine ends the process itself; if it returns, the code is still the process's end */
    for (;;)
        sys3(231, g_exit ? (long)(code & 0xFF) : 1, 0, 0);   /* exit_group(status): 1 with no callback, else the code's low byte */
}

void fnvnx_reach_violation(uint32_t fact_id) {
    char b[64], *p = b;
    p = str(p, "fnvnx-reach fact=0x");
    p = hex(p, fact_id & 0xFFFFFFu, 6);
    *p++ = '\n';
    log_line(b, (int)(p - b));
    leave(FNVNX_REACH_EXIT | (fact_id & 0xFFFFFFu));
}

void fnvnx_assume_violation(uint32_t asm_id, uint64_t value) {
    char b[96], *p = b;
    p = str(p, "fnvnx-assume id=");
    p = hex(p, asm_id, 8);
    p = str(p, " value=0x");
    p = hex(p, value, 16);
    *p++ = '\n';
    log_line(b, (int)(p - b));
    leave(FNVNX_ASSUME_EXIT | (asm_id & 0xFFu) << 16 | (uint32_t)(value & 0xFFFF));
}
