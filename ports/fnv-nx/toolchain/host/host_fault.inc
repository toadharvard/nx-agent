/* host_fault.inc - FNVNX_PLAT_ACCESS_FAULT_HANDLER (hostapi.h service 10): the delivery of a guest data / instruction access fault
 * to a guest handler, and the resume from the handler's frame. Proofs: journal/inbox/l1-contract-w1-001.md (the Mesosphere
 * sources, commit 4e3f37038d9e82dde7054897213ab32b2ecf651f, "kern" = libraries/libmesosphere) and the kernel double
 * tests/s4/backends/kdouble.h (model of the same lines).
 *
 * THE KERNEL MECHANISM. A process whose NPDM enables user exception handlers has a per-thread "PLR" in its TLS page. On a user
 * fault the kernel (kern_k_thread.cpp: KThread::SetUserException... / kern_k_exception_context / HandleException in
 * kern_exception_handler... "ExceptionInfo" at PLR) saves {x0..x8, lr, sp, pc, pstate, afsr0, afsr1, esr, far} into the PLR's
 * ExceptionInfo (120 bytes = ThreadExceptionFrameA64) and sends the thread to the PROCESS ENTRY point with x0 = exception type,
 * x1 = &ExceptionInfo, sp = the top of the PLR data, psr = 0. x9..x29 and v0..v31 are still the faulting code's. The thread is
 * then in the EXCEPTION STATE: the kernel allows only svcReturnFromException (0x28) and svcGetInfo, and takes no second user
 * exception of the process until svcReturnFromException(0) (one exception slot per process). svcReturnFromException(0) loads
 * x0..x8, lr, sp, pc and NZCV from the ExceptionInfo (the possibly edited copy) and leaves x9..x29 / v0..v31 as they are at
 * the svc; a non-zero argument ends the process.
 *   => fnvnx_fault_entry (below, the overridden process entry) saves x9..x29, fpcr, fpsr, v0..v31 into the frame, lets C fill
 *      the rest from the ExceptionInfo, then REDIRECTS the ExceptionInfo to a trampoline on the handler's stack (pc, sp, x0) and
 *      returns: the thread leaves the exception state and runs the guest handler in the normal state (every svc allowed).
 *   => RESUME. A user-mode "load 31 registers and branch" has no scratch register, so the resume re-enters the exception
 *      state: fnvnx_fault_resume loads x9..x29 / v / fpcr / fpsr from the frame and executes an INVALID svc (0xFF >= 0xC0 =
 *      AMS_KERN_NUM_SUPERVISOR_CALLS: kern_svc_handlers_asm.s sends it to the invalid-svc path, ExceptionType_InvalidSystemCall
 *      0x301); the entry sees state RESUMING, writes the frame's x0..x8, x30, sp, pc, NZCV into that ExceptionInfo and executes
 *      svcReturnFromException(0). Every register of the frame is then the thread's.
 * PER THREAD: the registration, the state and the frame live in a thread-local record (IDLE -> IN_HANDLER -> RESUMING -> IDLE).
 * THE RULE THAT CLOSES "which faults are delivered", total over every exception the kernel can deliver:
 *   deliver  <=>  type in 0x100..0x103  and  ESR.EC in {0x20 instruction abort, 0x22 pc alignment, 0x24 data abort}  and the
 *                 fault status is a translation / address-size / access-flag (unmapped), permission (protection) or alignment
 *                 fault  and  the thread has a handler  and  its state is IDLE  and  (kind is execute, or the faulting pc is
 *                 inside the guest image: the fault is GUEST code's access, not the host's)
 *   anything else ends the process loudly through the crash writer (g_crash_note says which clause failed): an exception that
 *   is no access fault (undefined instruction, SP alignment, SError / hardware error status, svc, fpu), a fault of a thread
 *   without a handler, a fault inside the handler (IN_HANDLER, which masks every kind), a fault of the host's own code (a bad
 *   guest pointer dereferenced by a host service: its pc is outside the guest image), a fault while resuming.
 *   Assumption of the "host" clause: the host branches to a guest-supplied code pointer only at thread start (a new thread has
 *   no handler) and at the image entry (no handler yet), so an execute fault always comes from guest code.
 * ASSUMPTIONS TO VERIFY FIRST ON THE CONSOLE (the kernel double models them; hardware is the proof): A1 the NPDM / kernel
 *   configuration enables user exception handlers and the PLR is aarch64; A2 crt0 jumps to the overridden __libnx_exception_entry
 *   with x0 = type, x1 = &info; A3 svc #0xFF yields ExceptionType 0x301 on this firmware; A4 ExceptionInfo.esr / far are the raw
 *   ESR_EL1 / FAR_EL1; A5 the registered stack is mapped read-write; A6 one kernel exception slot per process, so the shared exception-state buffers are never reentered; A7 the host branches to guest pointers only at thread start and image entry; A8 handler == 0 skips stack validation.
 * Needs: host_device.inc's g_crash_guest_base / g_crash_guest_size / g_crash_note / __nx_exception_stack / __libnx_exception_handler
 * (the tests define them), h_exit (HOST_FAULT_EXIT). Hooks for the tests: HOST_FAULT_EXIT(code), HOST_FAULT_CRASH(dump),
 * HOST_FAULT_TERMINATE(), HOST_FAULT_DOUBLE (the kernel double on Linux: tpidr_el0 and brk instead of the Switch's tp and svc). */
#ifndef HOST_FAULT_EXIT
#define HOST_FAULT_EXIT(code) h_exit(code)
#endif
#ifndef HOST_FAULT_CRASH
#define HOST_FAULT_CRASH(dump) __libnx_exception_handler(dump)
#endif
#ifndef HOST_FAULT_TERMINATE
#define HOST_FAULT_TERMINATE() svcExitProcess()
#endif

typedef struct { u64 handler, stack_top; u32 state; u32 pad; u64 frame, active_handler; } HostFaultTls;   /* offsets 0, 8, 16, 24, 32 (the asm) */
enum { HF_IDLE = 0, HF_IN_HANDLER = 1, HF_RESUMING = 2 };
enum { HF_EC_IABT = 0x20, HF_EC_PCALIGN = 0x22, HF_EC_DABT = 0x24 };
/* the frame's byte offsets (the asm); hostapi.h's struct is the contract */
#define HF_OFF_KIND 0
#define HF_OFF_REASON 4
#define HF_OFF_ADDR 8
#define HF_OFF_PC 16
#define HF_OFF_SP 24
#define HF_OFF_PSTATE 32
#define HF_OFF_X 40
#define HF_OFF_FPCR 288
#define HF_OFF_V 296
_Static_assert(offsetof(FnvnxFaultFrame, kind) == HF_OFF_KIND && offsetof(FnvnxFaultFrame, reason) == HF_OFF_REASON &&
               offsetof(FnvnxFaultFrame, addr) == HF_OFF_ADDR && offsetof(FnvnxFaultFrame, pc) == HF_OFF_PC &&
               offsetof(FnvnxFaultFrame, sp) == HF_OFF_SP && offsetof(FnvnxFaultFrame, pstate) == HF_OFF_PSTATE &&
               offsetof(FnvnxFaultFrame, x) == HF_OFF_X && offsetof(FnvnxFaultFrame, fpcr) == HF_OFF_FPCR &&
               offsetof(FnvnxFaultFrame, v) == HF_OFF_V && sizeof(FnvnxFaultFrame) == 816, "frame offsets used by host_fault.inc's asm");
_Static_assert(offsetof(HostFaultTls, state) == 16 && offsetof(HostFaultTls, frame) == 24, "HostFaultTls offsets used by the asm");
_Static_assert(sizeof(ThreadExceptionFrameA64) == 120 && offsetof(ThreadExceptionFrameA64, lr) == 72 && offsetof(ThreadExceptionFrameA64, sp) == 80 &&
               offsetof(ThreadExceptionFrameA64, elr_el1) == 88 && offsetof(ThreadExceptionFrameA64, pstate) == 96, "ExceptionInfo offsets used by the asm");

__thread HostFaultTls fnvnx_hf_tls;
FnvnxFaultFrame fnvnx_hf_dieframe;           /* x9..x29 / v of a fault that is not delivered (the process ends: one exception at a time) */
static ThreadExceptionDump g_hf_dump;
static int g_hf_dying;
alignas(16) u8 fnvnx_hf_cstack[0x1000];      /* the stack the entry's C code runs on (exception state: one thread at a time) */
#define HF_CSTACK_SIZE 0x1000

/* ---- the registration (service 10) -------------------------------------------------------------------------- */
static u64 host_fault_register(u64 handler, u64 stack_top) {
    HostFaultTls *t = &fnvnx_hf_tls;
    if (!handler) { t->handler = 0; t->stack_top = 0; return 0; }   /* unregisters; the stack is not looked at */
    if ((stack_top & 15) || stack_top < sizeof(FnvnxFaultFrame) || stack_top >= (1ull << 32)) return FNVNX_HE_ADDR;
    t->handler = handler;
    t->stack_top = stack_top;
    return 0;
}

/* ---- decode: ESR -> {kind, reason}; 0 when the exception is an access fault, else a reason text --------------- */
static const char *hf_decode(u64 type, const ThreadExceptionFrameA64 *in, u32 *kind, u32 *reason) {
    u32 ec = in->esr >> 26 & 0x3F, fsc = in->esr & 0x3F;
    if (type < 0x100 || type > 0x103) return "exception is not an access fault (kernel exception type)";
    if (ec == HF_EC_PCALIGN) { *kind = 2; *reason = 2; return 0; }
    if (ec != HF_EC_IABT && ec != HF_EC_DABT) return "exception is not an access fault (ESR.EC)";
    *kind = ec == HF_EC_IABT ? 2 : (in->esr >> 6 & 1);
    if (fsc <= 0x0B) *reason = 0;            /* address size, translation, access flag: no mapping */
    else if (fsc <= 0x0F) *reason = 1;       /* permission */
    else if (fsc == 0x21) *reason = 2;       /* alignment */
    else return "fault status is not a memory access fault (external / parity / TLB conflict)";
    return 0;
}

/* the crash writer's dump (libnx's ThreadExceptionDump) of the fault, then the thread is redirected to a trampoline that runs
 * __libnx_exception_handler in the normal state (it needs IPC); a fault in there ends the process at once */
static void fnvnx_fault_die_tramp(void) { HOST_FAULT_CRASH(&g_hf_dump); HOST_FAULT_TERMINATE(); __builtin_unreachable(); }
static void fnvnx_fault_exit_now(void) { HOST_FAULT_TERMINATE(); __builtin_unreachable(); }
extern u8 __nx_exception_stack[];
static void hf_die(u64 type, ThreadExceptionFrameA64 *in, const FnvnxFaultFrame *saved, const char *why) {
    if (g_hf_dying++) {                       /* a fault while dying: no writer again */
        in->elr_el1 = (u64)(uintptr_t)fnvnx_fault_exit_now;
        in->sp = ((u64)(uintptr_t)fnvnx_hf_cstack + HF_CSTACK_SIZE) & ~15ull;
        in->pstate = 0;
        return;
    }
    g_crash_note = why;
    memset(&g_hf_dump, 0, sizeof g_hf_dump);
    g_hf_dump.error_desc = type == 0x100 || type == 0x102 ? (u32)type : type == 0x103 && (in->esr >> 26) == 0x26 ? 0x103 :
                           type == 0x104 || type == 0x301 ? (u32)type : 0x101;
    for (int i = 0; i < 9; i++) g_hf_dump.cpu_gprs[i].x = in->cpu_gprs[i];
    for (int i = 9; i < 29; i++) g_hf_dump.cpu_gprs[i].x = saved->x[i];
    g_hf_dump.fp.x = saved->x[29];
    g_hf_dump.lr.x = in->lr;
    g_hf_dump.sp.x = in->sp;
    g_hf_dump.pc.x = in->elr_el1;
    for (int i = 0; i < 32; i++) g_hf_dump.fpu_gprs[i].v = (u128)saved->v[i][0] | (u128)saved->v[i][1] << 64;
    g_hf_dump.pstate = in->pstate; g_hf_dump.afsr0 = in->afsr0; g_hf_dump.afsr1 = in->afsr1; g_hf_dump.esr = in->esr;
    g_hf_dump.far.x = in->far;
    in->elr_el1 = (u64)(uintptr_t)fnvnx_fault_die_tramp;
    in->sp = ((u64)(uintptr_t)__nx_exception_stack + 0x4000) & ~15ull;   /* the same size host_device.inc defines */
    in->pstate = 0;
}

/* The handler's trampoline: the normal state, sp = the frame (the handler's stack grows down from there). */
void fnvnx_fault_resume(FnvnxFaultFrame *frame) __attribute__((noreturn));
static void fnvnx_fault_tramp(FnvnxFaultFrame *fr) {
    u64 (*h)(FnvnxFaultFrame *) = (u64 (*)(FnvnxFaultFrame *))(uintptr_t)fnvnx_hf_tls.active_handler;
    u64 rc = h(fr);
    if (rc) { HOST_FAULT_EXIT(rc); __builtin_unreachable(); }
    fnvnx_hf_tls.state = HF_RESUMING;
    fnvnx_fault_resume(fr);
}

/* Called by the entry in the exception state with the live x9..x29 / v already saved in *fr (the thread's frame, or the die
 * frame): decides by the rule above and edits the ExceptionInfo so that svcReturnFromException continues at the trampoline. */
void fnvnx_fault_deliver(u64 type, ThreadExceptionFrameA64 *in, FnvnxFaultFrame *fr) {
    HostFaultTls *t = &fnvnx_hf_tls;
    if (g_hf_dying) { hf_die(type, in, fr, 0); return; }
    u32 kind = 0, reason = 0;
    const char *why = hf_decode(type, in, &kind, &reason);
    if (!why && kind != 2 && !(g_crash_guest_size && in->elr_el1 - g_crash_guest_base < g_crash_guest_size))
        why = "access fault taken by host code (a bad guest pointer dereferenced by the host): not delivered";
    if (!why && !t->handler) why = "access fault in a thread without a registered handler";
    if (!why && t->state == HF_IN_HANDLER) why = "access fault inside the access-fault handler";
    if (!why && t->state != HF_IDLE) why = "access fault while resuming from the access-fault handler";
    if (why) { hf_die(type, in, fr, why); return; }
    fr->kind = kind;
    fr->reason = reason;
    fr->addr = kind == 2 ? in->elr_el1 : in->far;
    fr->pc = in->elr_el1;
    fr->sp = in->sp;
    fr->pstate = in->pstate & 0xF0000000u;
    for (int i = 0; i < 9; i++) fr->x[i] = in->cpu_gprs[i];
    fr->x[30] = in->lr;
    t->state = HF_IN_HANDLER;
    t->active_handler = t->handler;
    t->frame = (u64)(uintptr_t)fr;
    in->elr_el1 = (u64)(uintptr_t)fnvnx_fault_tramp;
    in->sp = (u64)(uintptr_t)fr;
    in->cpu_gprs[0] = (u64)(uintptr_t)fr;
    in->pstate = 0;
}

/* Called by the entry in state RESUMING when the exception is the resume svc: nothing to decide (asm below copies the frame). */

#if defined(__aarch64__) && !defined(HOST_FAULT_LOGIC_ONLY)
#ifdef HOST_FAULT_DOUBLE
#define HF_TP(r) "mrs " #r ", tpidr_el0\n"
#define HF_SVC(n) "brk #" #n "\n"
#else
#define HF_TP(r) "mrs " #r ", tpidrro_el0\nldr " #r ", [" #r ", #0x1F8]\n"
#define HF_SVC(n) "svc #" #n "\n"
#endif
/* x0 = type, x1 = &ExceptionInfo; x2..x8 are scratch (x0..x8 are in the ExceptionInfo); x9..x29 and v0..v31 are the faulting code's. */
__asm__(
".text\n.balign 16\n.global fnvnx_fault_entry\n.type fnvnx_fault_entry,%function\nfnvnx_fault_entry:\n"
HF_TP(x2)
"add x2, x2, #:tprel_hi12:fnvnx_hf_tls, lsl #12\nadd x2, x2, #:tprel_lo12_nc:fnvnx_hf_tls\n"
"ldr w3, [x2, #16]\n"
"cmp w3, #2\nb.ne 3f\n"
/* RESUMING: is this exception our own resume svc? type 0x301 and pc at / after the svc */
"mov x4, #0x301\ncmp x0, x4\nb.ne 3f\n"
"ldr x3, [x1, #88]\nadrp x4, fnvnx_fault_resume_svc\nadd x4, x4, :lo12:fnvnx_fault_resume_svc\nsub x3, x3, x4\ncmp x3, #4\nb.hi 3f\n"
"ldr x7, [x2, #24]\nstr wzr, [x2, #16]\n"
"ldp x3, x4, [x7, #40]\nstp x3, x4, [x1, #0]\nldp x3, x4, [x7, #56]\nstp x3, x4, [x1, #16]\n"
"ldp x3, x4, [x7, #72]\nstp x3, x4, [x1, #32]\nldp x3, x4, [x7, #88]\nstp x3, x4, [x1, #48]\n"
"ldr x3, [x7, #104]\nstr x3, [x1, #64]\nldr x3, [x7, #280]\nstr x3, [x1, #72]\n"
"ldr x3, [x7, #24]\nstr x3, [x1, #80]\nldr x3, [x7, #16]\nstr x3, [x1, #88]\n"
"ldr x3, [x7, #32]\nand x3, x3, #0xF0000000\nstr w3, [x1, #96]\n"
"mov x0, #0\n"
HF_SVC(0x28)
/* a fault: the frame is the thread's (handler registered, IDLE) or the die frame */
"3:\n"
"ldr x4, [x2, #0]\nldr w3, [x2, #16]\n"
"cbz x4, 4f\ncbnz w3, 4f\n"
"ldr x5, [x2, #8]\nsub x7, x5, #816\nb 5f\n"
"4:\nadrp x7, fnvnx_hf_dieframe\nadd x7, x7, :lo12:fnvnx_hf_dieframe\n"
"5:\n"
"stp x9, x10, [x7, #112]\nstp x11, x12, [x7, #128]\nstp x13, x14, [x7, #144]\nstp x15, x16, [x7, #160]\n"
"stp x17, x18, [x7, #176]\nstp x19, x20, [x7, #192]\nstp x21, x22, [x7, #208]\nstp x23, x24, [x7, #224]\n"
"stp x25, x26, [x7, #240]\nstp x27, x28, [x7, #256]\nstr x29, [x7, #272]\n"
"mrs x2, fpcr\nmrs x3, fpsr\nstr w2, [x7, #288]\nstr w3, [x7, #292]\n"
"add x8, x7, #296\n"
"stp q0, q1, [x8], #32\nstp q2, q3, [x8], #32\nstp q4, q5, [x8], #32\nstp q6, q7, [x8], #32\n"
"stp q8, q9, [x8], #32\nstp q10, q11, [x8], #32\nstp q12, q13, [x8], #32\nstp q14, q15, [x8], #32\n"
"stp q16, q17, [x8], #32\nstp q18, q19, [x8], #32\nstp q20, q21, [x8], #32\nstp q22, q23, [x8], #32\n"
"stp q24, q25, [x8], #32\nstp q26, q27, [x8], #32\nstp q28, q29, [x8], #32\nstp q30, q31, [x8], #32\n"
"adrp x6, fnvnx_hf_cstack\nadd x6, x6, :lo12:fnvnx_hf_cstack\nadd x6, x6, #0x1000\nmov sp, x6\n"
"mov x2, x7\nbl fnvnx_fault_deliver\n"
"mov x0, #0\n"
HF_SVC(0x28)
"brk #0xF001\n"
".size fnvnx_fault_entry, .-fnvnx_fault_entry\n.global __libnx_exception_entry\n.type __libnx_exception_entry,%function\n.set __libnx_exception_entry, fnvnx_fault_entry\n"
/* the resume: x0 = the frame; loads fpcr, fpsr, v0..v31, x9..x29 and enters the exception state again (invalid svc 0xFF) */
".balign 16\n.global fnvnx_fault_resume\n.type fnvnx_fault_resume,%function\nfnvnx_fault_resume:\n"
"mov x8, x0\nldr w2, [x8, #288]\nldr w3, [x8, #292]\nmsr fpcr, x2\nmsr fpsr, x3\nadd x2, x8, #296\n"
"ldp q0, q1, [x2], #32\nldp q2, q3, [x2], #32\nldp q4, q5, [x2], #32\nldp q6, q7, [x2], #32\n"
"ldp q8, q9, [x2], #32\nldp q10, q11, [x2], #32\nldp q12, q13, [x2], #32\nldp q14, q15, [x2], #32\n"
"ldp q16, q17, [x2], #32\nldp q18, q19, [x2], #32\nldp q20, q21, [x2], #32\nldp q22, q23, [x2], #32\n"
"ldp q24, q25, [x2], #32\nldp q26, q27, [x2], #32\nldp q28, q29, [x2], #32\nldp q30, q31, [x2], #32\n"
"ldp x9, x10, [x8, #112]\nldp x11, x12, [x8, #128]\nldp x13, x14, [x8, #144]\nldp x15, x16, [x8, #160]\n"
"ldp x17, x18, [x8, #176]\nldp x19, x20, [x8, #192]\nldp x21, x22, [x8, #208]\nldp x23, x24, [x8, #224]\n"
"ldp x25, x26, [x8, #240]\nldp x27, x28, [x8, #256]\nldr x29, [x8, #272]\n"
".global fnvnx_fault_resume_svc\nfnvnx_fault_resume_svc:\n"
HF_SVC(0xFF)
"brk #0xF002\n"
".size fnvnx_fault_resume, .-fnvnx_fault_resume\n"
);
void __libnx_exception_entry(void);   /* = fnvnx_fault_entry (asm .set above): the process entry for a user exception, overriding libnx's weak one */
#endif
