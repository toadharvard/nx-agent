/* host_fault_logic_test.c - FNVNX_PLAT_ACCESS_FAULT_HANDLER's RULES, total over their domains, in plain C (host_fault.inc with
 * HOST_FAULT_LOGIC_ONLY: no asm), so it runs on the build machine under -fsanitize=address,undefined (run_host_sanitized.sh) and
 * under qemu-aarch64 (run_qemu.sh). Test name (hostapi.h table): fault_logic.
 *   (1) the registration rule over every stack_top class;
 *   (2) the delivery rule: every (kernel type, ESR.EC, FSC, WnR) of the domain against an independent statement of the rule,
 *       for each (handler, state, pc inside / outside the guest image) combination: the answer is "delivered with the frame
 *       {kind, reason, addr, pc, sp, pstate & NZCV, x0..x8, x30}" or "ends the process with the clause's note", never anything else. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <stdalign.h>
#include "fnvnx/hostapi.h"
#include <switch/types.h>
#include <switch/arm/thread_context.h>

static int g_fail;
#define T(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); g_fail++; } } while (0)

#define HOST_FAULT_LOGIC_ONLY
#define HOST_FAULT_EXIT(c) ((void)(c))
#define HOST_FAULT_CRASH(d) ((void)(d))
#define HOST_FAULT_TERMINATE() ((void)0)
static u64 g_crash_guest_base, g_crash_guest_size;
static const char *g_crash_note;
alignas(16) u8 __nx_exception_stack[0x4000];
void fnvnx_fault_resume(FnvnxFaultFrame *frame) { (void)frame; abort(); }   /* the asm resume: run by host_fault_test.c; the rule never reaches it here */
#include "../../../toolchain/host/host_fault.inc"

#define GBASE 0x10000000ull
#define GSIZE 0x1000ull

static void set_state(u64 handler, u32 state) { memset(&fnvnx_hf_tls, 0, sizeof fnvnx_hf_tls); fnvnx_hf_tls.handler = handler; fnvnx_hf_tls.stack_top = handler ? 0x40000 : 0; fnvnx_hf_tls.state = state; g_hf_dying = 0; g_crash_note = 0; }

static void registration(void) {
    static const u64 bad[] = {1, 8, 15, 17, 815, 800, 0, 4, 1ull << 32, (1ull << 32) + 16, ~0ull, ~15ull, 0x100000000ull - 8, 0x80000000ull + 4};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        u64 s = bad[i]; int ok = !(s & 15) && s >= 816 && s < (1ull << 32);
        set_state(0, 0);
        T((host_fault_register(5, s) == 0) == ok);
        T(ok ? fnvnx_hf_tls.handler == 5 && fnvnx_hf_tls.stack_top == s : fnvnx_hf_tls.handler == 0 && fnvnx_hf_tls.stack_top == 0);
        T(host_fault_register(0, s) == 0 && fnvnx_hf_tls.handler == 0);   /* handler 0 unregisters whatever the stack */
    }
    for (u64 s = 0; s < 4096; s++) {   /* every low stack_top, around 816 and the alignment boundary */
        set_state(0, 0);
        int ok = !(s & 15) && s >= 816;
        T((host_fault_register(7, s) == 0) == ok);
    }
    for (int k = 0; k < 64; k++) {     /* every bit position */
        u64 s = 1ull << k; set_state(0, 0);
        T((host_fault_register(7, s) == 0) == (k >= 4 && s >= 816 && s < (1ull << 32)));
    }
}

typedef struct { u64 type; u32 ec, fsc, wnr; } Case;
/* the rule, stated independently: 1 = delivered (kind, reason out), 0 = ends the process */
static int oracle(u64 type, u32 ec, u32 fsc, u32 wnr, u32 *kind, u32 *reason) {
    if (type < 0x100 || type > 0x103) return 0;
    if (ec == 0x22) { *kind = 2; *reason = 2; return 1; }
    if (ec != 0x20 && ec != 0x24) return 0;
    if (!(fsc <= 0x0F || fsc == 0x21)) return 0;
    if (fsc >= 0x0C && fsc <= 0x0F) *reason = 1; else if (fsc == 0x21) *reason = 2; else *reason = 0;
    *kind = ec == 0x20 ? 2 : wnr;
    return 1;
}

static void delivery(void) {
    int delivered = 0, ended = 0;
    static const u32 states[] = {HF_IDLE, HF_IN_HANDLER, HF_RESUMING};
    for (u64 type = 0xFF; type <= 0x105; type++)
      for (u32 ec = 0; ec < 64; ec++)
        for (u32 fsc = 0; fsc < 64; fsc++)
          for (u32 wnr = 0; wnr < 2; wnr++)
            for (int hand = 0; hand < 2; hand++)
              for (int si = 0; si < 3; si++)
                for (int inside = 0; inside < 2; inside++) {
                    static FnvnxFaultFrame fr; static ThreadExceptionFrameA64 in;
                    memset(&fr, 0xCC, sizeof fr); memset(&in, 0, sizeof in);
                    for (int i = 0; i < 9; i++) in.cpu_gprs[i] = 0x1000 + i;
                    in.lr = 0x2222; in.sp = 0x3330; in.far = 0x44444444; in.pstate = 0xF00003C5; in.afsr0 = 1; in.afsr1 = 2;
                    in.esr = ec << 26 | 1u << 25 | wnr << 6 | fsc;
                    in.elr_el1 = inside ? GBASE + 0x40 : GBASE + GSIZE + 0x40;
                    g_crash_guest_base = GBASE; g_crash_guest_size = GSIZE;
                    set_state(hand ? 0x7000 : 0, states[si]);
                    ThreadExceptionFrameA64 orig = in;
                    fnvnx_fault_deliver(type, &in, &fr);
                    u32 kind = 9, reason = 9;
                    int dec = oracle(type, ec, fsc, wnr, &kind, &reason);
                    int want = dec && hand && states[si] == HF_IDLE && (kind == 2 || inside);
                    if (want) {
                        delivered++;
                        T(!g_crash_note && !g_hf_dying);
                        T(fr.kind == kind && fr.reason == reason && fr.addr == (kind == 2 ? orig.elr_el1 : orig.far) && fr.pc == orig.elr_el1 && fr.sp == orig.sp);
                        T(fr.pstate == (orig.pstate & 0xF0000000u));
                        for (int i = 0; i < 9; i++) T(fr.x[i] == orig.cpu_gprs[i]);
                        T(fr.x[30] == orig.lr);
                        T(in.elr_el1 == (u64)(uintptr_t)fnvnx_fault_tramp && in.sp == (u64)(uintptr_t)&fr && in.cpu_gprs[0] == (u64)(uintptr_t)&fr && in.pstate == 0);
                        T(fnvnx_hf_tls.state == HF_IN_HANDLER && fnvnx_hf_tls.active_handler == 0x7000 && fnvnx_hf_tls.frame == (u64)(uintptr_t)&fr);
                    } else {
                        ended++;
                        T(g_crash_note && g_hf_dying == 1);
                        T(in.elr_el1 == (u64)(uintptr_t)fnvnx_fault_die_tramp && in.sp == (((u64)(uintptr_t)__nx_exception_stack + 0x4000) & ~15ull) && in.pstate == 0);
                        T(g_hf_dump.esr == (u32)(ec << 26 | 1u << 25 | wnr << 6 | fsc) && g_hf_dump.far.x == 0x44444444 && g_hf_dump.pc.x == orig.elr_el1);
                        T(fnvnx_hf_tls.state == states[si]);   /* an ended process leaves the record as it was */
                        /* the clause named is the first false one of the rule, in this order */
                        const char *w = g_crash_note;
                        if (!dec) T(strstr(w, "not an access fault") || strstr(w, "not a memory access fault"));
                        else if (kind != 2 && !inside) T(strstr(w, "host code"));
                        else if (!hand) T(strstr(w, "without a registered handler"));
                        else if (states[si] == HF_IN_HANDLER) T(strstr(w, "inside the access-fault handler"));
                        else T(strstr(w, "while resuming"));
                    }
                    /* a fault while the process is already ending: no second writer, the exit path */
                    g_hf_dying = 1; in = orig;
                    fnvnx_fault_deliver(type, &in, &fr);
                    T(in.elr_el1 == (u64)(uintptr_t)fnvnx_fault_exit_now && g_hf_dying == 2);
                }
    T(delivered > 0 && ended > 0);
    printf("host_fault_logic: %d delivered, %d ended\n", delivered, ended);
}

int main(void) {
    registration();
    delivery();
    printf("host_fault_logic: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
