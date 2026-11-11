/* host_fault_test.c - FNVNX_PLAT_ACCESS_FAULT_HANDLER (hostapi.h service 10): toolchain/host/host_fault.inc, the REAL entry / resume asm and the
 * delivery rule, run under qemu-aarch64 against the user-exception model of kdouble.h (KD_EXCEPTION: Linux signals standing in for the
 * kernel's exception state, cited there). A handwritten probe loads a known pattern into x0..x30, v0..v31, fpcr, fpsr and NZCV, takes
 * the fault, and its epilogue dumps every register after the resume. Test names (hostapi.h table): fault_register fault_deliver
 * fault_resume fault_masked fault_die. The GUEST IMAGE of the host's rule is the probes' text: a fault elsewhere is a host fault. */
#define KD_EXCEPTION
#include "kdouble.h"
#include <stddef.h>
#include <stdalign.h>
#include "fnvnx/hostapi.h"

static u64 g_crash_guest_base, g_crash_guest_size;
static const char *g_crash_note;
alignas(16) u8 __nx_exception_stack[0x4000];
static const ThreadExceptionDump *g_dumped; static ThreadExceptionDump g_dump_copy;
#define HOST_FAULT_DOUBLE
#define HOST_FAULT_EXIT(c) kd_terminate(KD_EXIT, (c))
#define HOST_FAULT_CRASH(d) do { g_dump_copy = *(d); g_dumped = &g_dump_copy; kd_terminate(KD_CRASH, 0); } while (0)
#define HOST_FAULT_TERMINATE() kd_terminate(KD_TERMINATE, 0)
#include "../../../toolchain/host/host_fault.inc"

typedef struct { u64 x[31]; u64 nzcv; u32 fpcr, fpsr, pad[2]; u64 v[32][2]; } Regs;   /* x @0, nzcv @248, fpcr @256, fpsr @260, v @272 */
_Static_assert(offsetof(Regs, nzcv) == 248 && offsetof(Regs, v) == 272, "probe layout");
extern u64 probe_start[], probe_end[], probe_read_at[], probe_write_at[], probe_exec_at[], probe_align_at[], probe_udf_at[], probe_after[];
void probe_read(Regs *in), probe_write(Regs *in), probe_exec(Regs *in), probe_align(Regs *in), probe_udf(Regs *in);
u64 g_save[24];   /* sp, x19..x30, d8..d15 of the probe's caller */
__asm__(
".text\n.balign 16\n.global probe_start\nprobe_start:\n"
".macro PROBE name, insn\n.global \\name\n\\name:\n"
"adrp x1, g_save\nadd x1, x1, :lo12:g_save\nmov x2, sp\nstp x2, x19, [x1]\nstp x20, x21, [x1, #16]\nstp x22, x23, [x1, #32]\nstp x24, x25, [x1, #48]\n"
"stp x26, x27, [x1, #64]\nstp x28, x29, [x1, #80]\nstr x30, [x1, #96]\n"
"stp d8, d9, [x1, #104]\nstp d10, d11, [x1, #120]\nstp d12, d13, [x1, #136]\nstp d14, d15, [x1, #152]\n"
"mov x1, x0\nldr x2, [x1, #248]\nmsr nzcv, x2\nldr w2, [x1, #256]\nldr w3, [x1, #260]\nmsr fpcr, x2\nmsr fpsr, x3\nadd x2, x1, #272\n"
"ldp q0, q1, [x2], #32\nldp q2, q3, [x2], #32\nldp q4, q5, [x2], #32\nldp q6, q7, [x2], #32\nldp q8, q9, [x2], #32\nldp q10, q11, [x2], #32\n"
"ldp q12, q13, [x2], #32\nldp q14, q15, [x2], #32\nldp q16, q17, [x2], #32\nldp q18, q19, [x2], #32\nldp q20, q21, [x2], #32\nldp q22, q23, [x2], #32\n"
"ldp q24, q25, [x2], #32\nldp q26, q27, [x2], #32\nldp q28, q29, [x2], #32\nldp q30, q31, [x2], #32\n"
"ldp x2, x3, [x1, #16]\nldp x4, x5, [x1, #32]\nldp x6, x7, [x1, #48]\nldp x8, x9, [x1, #64]\nldp x10, x11, [x1, #80]\nldp x12, x13, [x1, #96]\n"
"ldp x14, x15, [x1, #112]\nldp x16, x17, [x1, #128]\nldp x18, x19, [x1, #144]\nldp x20, x21, [x1, #160]\nldp x22, x23, [x1, #176]\nldp x24, x25, [x1, #192]\n"
"ldp x26, x27, [x1, #208]\nldp x28, x29, [x1, #224]\nldr x30, [x1, #240]\nldp x0, x1, [x1]\n"
".global \\name\\()_at\n\\name\\()_at:\n\\insn\n"
"brk #0xA001\n.endm\n"
"PROBE probe_read, \"ldr xzr, [x9]\"\nPROBE probe_write, \"str xzr, [x9]\"\nPROBE probe_exec, \"br x9\"\nPROBE probe_align, \"ldxr xzr, [x9]\"\nPROBE probe_udf, \"udf #0\"\n"
/* after the resume: sp = the Regs to fill (set by the handler); stores need no scratch register */
".global probe_after\nprobe_after:\n"
"stp x0, x1, [sp]\nstp x2, x3, [sp, #16]\nstp x4, x5, [sp, #32]\nstp x6, x7, [sp, #48]\nstp x8, x9, [sp, #64]\nstp x10, x11, [sp, #80]\nstp x12, x13, [sp, #96]\n"
"stp x14, x15, [sp, #112]\nstp x16, x17, [sp, #128]\nstp x18, x19, [sp, #144]\nstp x20, x21, [sp, #160]\nstp x22, x23, [sp, #176]\nstp x24, x25, [sp, #192]\n"
"stp x26, x27, [sp, #208]\nstp x28, x29, [sp, #224]\nstr x30, [sp, #240]\n"
"mrs x0, nzcv\nstr x0, [sp, #248]\nmrs x0, fpcr\nmrs x1, fpsr\nstr w0, [sp, #256]\nstr w1, [sp, #260]\nadd x2, sp, #272\n"
"stp q0, q1, [x2], #32\nstp q2, q3, [x2], #32\nstp q4, q5, [x2], #32\nstp q6, q7, [x2], #32\nstp q8, q9, [x2], #32\nstp q10, q11, [x2], #32\n"
"stp q12, q13, [x2], #32\nstp q14, q15, [x2], #32\nstp q16, q17, [x2], #32\nstp q18, q19, [x2], #32\nstp q20, q21, [x2], #32\nstp q22, q23, [x2], #32\n"
"stp q24, q25, [x2], #32\nstp q26, q27, [x2], #32\nstp q28, q29, [x2], #32\nstp q30, q31, [x2], #32\n"
"adrp x1, g_save\nadd x1, x1, :lo12:g_save\nldp x2, x19, [x1]\nmov sp, x2\nldp x20, x21, [x1, #16]\nldp x22, x23, [x1, #32]\nldp x24, x25, [x1, #48]\n"
"ldp x26, x27, [x1, #64]\nldp x28, x29, [x1, #80]\nldr x30, [x1, #96]\n"
"ldp d8, d9, [x1, #104]\nldp d10, d11, [x1, #120]\nldp d12, d13, [x1, #136]\nldp d14, d15, [x1, #152]\nret\n"
".global probe_end\nprobe_end:\n"
);

#define STK_BASE 0x50000000ull
#define STK_SIZE 0x10000ull
static u64 g_stk_top;
static Regs g_in, g_out;
static FnvnxFaultFrame g_seen;
static FnvnxFaultFrame *g_frame_ptr;
static int g_calls, g_in_exc_seen; static u64 g_sp_in_handler;
static int g_mode;   /* handler behaviour */
enum { M_RESUME, M_EDIT, M_RC7, M_NEST, M_TWICE };
static void fill(Regs *r, u64 addr) {
    for (int i = 0; i < 31; i++) r->x[i] = 0x1111000000000000ull * 0 + 0xA5A50000ull + i * 0x01010101ull + ((u64)i << 40);
    r->x[9] = addr; r->nzcv = 0xA0000000ull; r->fpcr = 0x00400000; r->fpsr = 0x08000000;
    for (int i = 0; i < 32; i++) { r->v[i][0] = 0xF00D0000ull + i; r->v[i][1] = 0xBEEF000000000000ull + i * 3; }
}
static u64 handler(FnvnxFaultFrame *f) {
    g_calls++; g_frame_ptr = f; g_seen = *f; g_in_exc_seen = kd_in_exc; g_sp_in_handler = (u64)(uintptr_t)__builtin_frame_address(0);
    if (g_mode == M_RC7) return 7;
    if (g_mode == M_NEST) { Regs r; fill(&r, 0x30000000ull); probe_read(&r); return 0; }   /* faults inside the handler from guest code */
    f->pc = (u64)(uintptr_t)probe_after; f->sp = (u64)(uintptr_t)&g_out;
    if (g_mode == M_EDIT) { f->x[5] += 1; f->v[3][0] = 0x1234; f->x[30] ^= 0xFFFF; f->pstate = 0x60000000ull | 0xF | 0x3C0; f->fpcr = 0x00800000; f->fpsr = 0; }
    return 0;
}

#define UNMAPPED 0x30000000ull
#define PROTPG 0x31000000ull
static int g_registered;
static void on(u64 h) { T(host_fault_register(h, g_stk_top) == 0); g_registered = h != 0; }
static void reset(int mode) { g_mode = mode; g_calls = 0; memset(&g_seen, 0xEE, sizeof g_seen); memset(&g_out, 0, sizeof g_out); }
static void check_frame(u32 kind, u32 reason, u64 addr, u64 pc) {
    T(g_calls == 1 && g_seen.kind == kind && g_seen.reason == reason && g_seen.addr == addr && g_seen.pc == pc);
    T(g_seen.sp == g_save[0] && g_seen.pstate == (g_in.nzcv & 0xF0000000ull) && g_seen.fpcr == g_in.fpcr && g_seen.fpsr == g_in.fpsr);
    for (int i = 0; i < 31; i++) T(g_seen.x[i] == g_in.x[i]);
    for (int i = 0; i < 32; i++) T(g_seen.v[i][0] == g_in.v[i][0] && g_seen.v[i][1] == g_in.v[i][1]);
    T(!g_in_exc_seen);                                                    /* the handler runs in the NORMAL state */
    T((u64)(uintptr_t)g_frame_ptr == g_stk_top - sizeof(FnvnxFaultFrame) && g_sp_in_handler < (u64)(uintptr_t)g_frame_ptr && g_sp_in_handler > STK_BASE);
    T(!kd_in_exc);
}
static void check_resumed(int edited) {
    for (int i = 0; i < 31; i++) {
        u64 e = g_in.x[i]; if (edited && i == 5) e++; if (edited && i == 30) e ^= 0xFFFF;
        T(g_out.x[i] == e);
    }
    for (int i = 0; i < 32; i++) T(g_out.v[i][0] == (edited && i == 3 ? 0x1234 : g_in.v[i][0]) && g_out.v[i][1] == g_in.v[i][1]);
    T(g_out.nzcv == (edited ? 0x60000000ull : g_in.nzcv));
    T(g_out.fpcr == (edited ? 0x00800000u : g_in.fpcr) && g_out.fpsr == (edited ? 0u : g_in.fpsr));
}
static void sc_register(void) {
    T(host_fault_register(1, 0) == FNVNX_HE_ADDR && host_fault_register(1, 8) == FNVNX_HE_ADDR && host_fault_register(1, 800) == FNVNX_HE_ADDR);
    T(host_fault_register(1, g_stk_top + 8) == FNVNX_HE_ADDR && host_fault_register(1, 1ull << 32) == FNVNX_HE_ADDR && host_fault_register(1, 0x100000000ull + 16) == FNVNX_HE_ADDR);
    T(fnvnx_hf_tls.handler == 0);                                         /* nothing registered by a refusal */
    T(host_fault_register(1, 816) == 0 && fnvnx_hf_tls.handler == 1 && fnvnx_hf_tls.stack_top == 816);
    T(host_fault_register(2, 0xFFFFFFF0ull) == 0 && fnvnx_hf_tls.handler == 2);
    T(host_fault_register(0, 7) == 0 && fnvnx_hf_tls.handler == 0);       /* unregister: the stack is not looked at */
    T(host_fault_register(1, 1ull << 32) == FNVNX_HE_ADDR && fnvnx_hf_tls.handler == 0);
}
static void sc_read(void) {   /* read / unmapped, then write / protection, execute, alignment: each resumes with all registers */
    fill(&g_in, UNMAPPED + 0x120); on((u64)(uintptr_t)handler); reset(M_RESUME);
    probe_read(&g_in); check_frame(0, 0, UNMAPPED + 0x120, (u64)(uintptr_t)probe_read_at); check_resumed(0);
    fill(&g_in, PROTPG + 8); reset(M_RESUME); probe_write(&g_in); check_frame(1, 1, PROTPG + 8, (u64)(uintptr_t)probe_write_at); check_resumed(0);
    fill(&g_in, UNMAPPED); reset(M_RESUME); probe_exec(&g_in); check_frame(2, 0, UNMAPPED, UNMAPPED); check_resumed(0);
    fill(&g_in, g_stk_top - 0x2001); reset(M_RESUME); probe_align(&g_in); check_frame(0, 2, g_stk_top - 0x2001, (u64)(uintptr_t)probe_align_at); check_resumed(0);
}
static void sc_edit(void) {   /* the handler edits pc, sp, x, v, fpcr, fpsr and pstate (with non-NZCV bits): the resumed state is the frame */
    fill(&g_in, UNMAPPED); on((u64)(uintptr_t)handler); reset(M_EDIT); probe_read(&g_in); T(g_calls == 1); check_resumed(1);
}
static void sc_twice(void) {  /* the fault mask is gone after a resume: faults of every kind in a row, on the same thread */
    on((u64)(uintptr_t)handler);
    for (int i = 0; i < 3; i++) { fill(&g_in, UNMAPPED + 16 * i); reset(M_RESUME); probe_read(&g_in); check_frame(0, 0, UNMAPPED + 16 * i, (u64)(uintptr_t)probe_read_at); check_resumed(0);
        reset(M_RESUME); fill(&g_in, PROTPG); probe_write(&g_in); check_frame(1, 1, PROTPG, (u64)(uintptr_t)probe_write_at); check_resumed(0); }
    T(fnvnx_hf_tls.state == HF_IDLE);
}
static void sc_nohandler(void) { fill(&g_in, UNMAPPED); reset(M_RESUME); probe_read(&g_in); T(0); }
static void sc_nest(void) { fill(&g_in, UNMAPPED); on((u64)(uintptr_t)handler); reset(M_NEST); probe_read(&g_in); T(0); }
static void sc_rc7(void) { fill(&g_in, UNMAPPED); on((u64)(uintptr_t)handler); reset(M_RC7); probe_read(&g_in); T(0); }
static void sc_hostfault(void) { on((u64)(uintptr_t)handler); reset(M_RESUME); volatile u64 *p = (volatile u64 *)(uintptr_t)UNMAPPED; (void)*p; T(0); }
static void sc_udf(void) { fill(&g_in, UNMAPPED); on((u64)(uintptr_t)handler); reset(M_RESUME); probe_udf(&g_in); T(0); }
static int note_has(const char *s) { return g_crash_note && strstr(g_crash_note, s); }
static void *thread_main(void *arg) {
    (void)arg;
    KdEnd e = kd_run(sc_nohandler);                                       /* this thread has no registration, the main one has */
    T(e.how == KD_CRASH && note_has("without a registered handler") && g_calls == 0);
    g_crash_note = 0; g_hf_dying = 0; fnvnx_hf_tls.state = 0;
    e = kd_run(sc_twice); T(e.how == KD_RETURNED); return 0;
}
int main(void) {
    kdouble_init(); kd_exc_init();
    void *p = mmap((void *)STK_BASE, STK_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0); T(p != MAP_FAILED);
    g_stk_top = STK_BASE + STK_SIZE;
    p = mmap((void *)PROTPG, 0x1000, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0); T(p != MAP_FAILED);
    T(munmap((void *)UNMAPPED, 0x10000) == 0 || 1);
    g_crash_guest_base = (u64)(uintptr_t)probe_start; g_crash_guest_size = (u64)(uintptr_t)probe_end - (u64)(uintptr_t)probe_start;
    KdEnd e; setvbuf(stdout,0,_IONBF,0);
    e = kd_run(sc_register); T(e.how == KD_RETURNED);                                   /* test:fault_register */
    e = kd_run(sc_read); T(e.how == KD_RETURNED);                                       /* test:fault_deliver */
    e = kd_run(sc_edit); T(e.how == KD_RETURNED);                                       /* test:fault_resume */
    e = kd_run(sc_twice); T(e.how == KD_RETURNED);
    e = kd_run(sc_nest); T(e.how == KD_CRASH && note_has("inside the access-fault handler"));   /* test:fault_masked */
    T(g_calls == 1);
    g_crash_note = 0; host_fault_register(0, 0); g_hf_dying = 0; fnvnx_hf_tls.state = 0;
    e = kd_run(sc_nohandler); T(e.how == KD_CRASH && note_has("without a registered handler") && g_calls == 0);   /* test:fault_die */
    T(g_dumped && g_dumped->pc.x == (u64)(uintptr_t)probe_read_at && g_dumped->far.x == UNMAPPED && g_dumped->cpu_gprs[9].x == UNMAPPED &&
      g_dumped->cpu_gprs[19].x == g_in.x[19] && g_dumped->cpu_gprs[0].x == g_in.x[0] && g_dumped->fpu_gprs[7].v == ((u128)g_in.v[7][1] << 64 | g_in.v[7][0]) && g_dumped->error_desc == 0x101);
    g_crash_note = 0; g_hf_dying = 0; fnvnx_hf_tls.state = 0;
    e = kd_run(sc_hostfault); T(e.how == KD_CRASH && note_has("host code") && g_calls == 0);
    g_crash_note = 0; g_hf_dying = 0; fnvnx_hf_tls.state = 0;
    e = kd_run(sc_rc7); T(e.how == KD_EXIT && e.code == 7 && g_calls == 1);
    g_crash_note = 0; g_hf_dying = 0; fnvnx_hf_tls.state = 0;
    e = kd_run(sc_udf); T(e.how == KD_CRASH && note_has("kernel exception type") && g_calls == 0 && g_dumped->error_desc == 0x104);
    g_crash_note = 0; g_hf_dying = 0; fnvnx_hf_tls.state = 0; host_fault_register(0, 0);
    pthread_t th; T(!pthread_create(&th, 0, thread_main, 0)); pthread_join(th, 0);
    printf(g_fail ? "host_fault: FAILED (%d)\n" : "host_fault: ok\n", g_fail);
    return g_fail != 0;
}
