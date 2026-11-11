# l1-contract-w1-001: service 10 ACCESS_FAULT_HANDLER - kernel proof and construction

Sources: Atmosphere 4e3f37038d9e82dde7054897213ab32b2ecf651f (libraries/libmesosphere/source, "kex" = arch/arm64/kern_exception_handlers.cpp),
libnx (nx/source/runtime/exception.c, nx/source/crt0.s style entry). Hardware is not tested; see "Verify first on the console".

## Step 1: the kernel mechanism
a. Delivery. A synchronous EL0 exception reaches HandleUserException (kex:112). With KTargetSystem::IsUserExceptionHandlersEnabled()
   (kex:135) and not a breakpoint (kex:145-148) and process.EnterUserException() true (kex:149) the kernel delivers it to the process.
   Data/instruction aborts: type DataAbort / InstructionAbort (kex:202,225); a data abort whose IFSC is "alignment fault" and an SP
   alignment fault -> ExceptionType_UnalignedData (kex:208,221-223); pc alignment -> UnalignedInstruction (kex:205). A far that is
   a kernel address sets ESR.IFSC = 4 (kex:128-130). So reason is decodable from ESR: alignment (IFSC 0x21 / the two types), translation
   fault (IFSC 4..7, 0x4 mask) = unmapped, permission fault (IFSC 0xC..0xF) = protection.
b. ExceptionInfo (ProcessLocalRegion.exception_info, user-writable) gets r[0..8] = x0..x8, lr, sp, pc, pstate (NZCV etc.), afsr0, afsr1,
   esr, far (kex:156-166). x9..x29 and the FP/SIMD registers are NOT stored: they stay live in the CPU because the kernel
   redirects the thread to the process exception entry with x0 = type, x1 = &info and sp = top of PLR exception stack, without touching
   x9..x29 (SvcHandler/exception return paths save only what they use) - so a handler stub can spill them itself before it clobbers them.
c. svcReturnFromException(0): ReturnFromException (kex:395) reads the ExceptionInfo from the PLR (kex:408-413), LeaveUserException()
   (kex:420), then copies r[0..8], lr, sp, pc and pstate & El0Aarch64PsrMask into the kernel context (kex:425-433). x9..x29 and v regs are those
   live at the svc (saved by CallReturnFromException64, svc/kern_svc_exception_asm.s:18-37), so ALL GPRs are restorable: x0..x8/lr/sp/pc/NZCV from
   ExceptionInfo, x9..x29 from live registers. A result other than 0 makes the thread terminate the process (kex:434+ non-zero path).
d. One slot per process: KProcess::EnterUserException (kern_k_process.cpp:798-830): if m_exception_thread == cur_thread (nested fault, same thread)
   it returns false -> the fault is handled as unhandled (process exit); another thread waits (kern_k_process.cpp:803-826) until LeaveUserException
   (:843-848). In exception state only svcReturnFromException and svcGetInfo are allowed (kern_k_thread.cpp:80-148, svc_access_flags).
e. Separate stack: the exception entry runs on the PLR exception stack (sp in info), not on the faulting stack; but in exception state nothing but
   ReturnFromException/GetInfo can be called, so a guest handler cannot run there. Mechanism: the stub in exception state redirects pc/sp
   in ExceptionInfo to a trampoline and returns (kernel leaves exception state, thread continues in NORMAL state on the registered stack).

## Step 2: the class-closing rule for "return 0 -> resume from the frame"
Pure user-mode "load 31 GPRs and branch to pc" is impossible (a branch needs a register), so the final resume goes through
svcReturnFromException, the only operation that sets pc and x0..x8, lr, sp, NZCV atomically while x9..x29 are live.
Per-thread state in TLS (fnvnx_hf_tls: handler, stack_top, state, frame, active_handler), so threads are independent; the kernel's single slot
only serialises the short exception windows.
  IDLE       --fault--> entry (kernel exception state) saves x9..x29, v0..v31, fpcr/fpsr; fnvnx_fault_deliver decides, fills the frame at
                        stack_top-816, sets IN_HANDLER, rewrites ExceptionInfo {pc = trampoline, sp = frame, x0 = frame}, svc 0x28 (0).
                        The thread runs the trampoline, and so the handler, in NORMAL state on the registered stack.
  IN_HANDLER a fault now (state != IDLE) -> loud process end through the crash writer with g_crash_note.
  RESUMING   handler returned 0: state RESUMING, load the frame's registers, execute the invalid `svc #0xFF` (number >= 0xC0, outside every
             valid SVC id; ec Svc64 -> type 0x301, kex:212). The entry sees RESUMING, copies the frame into ExceptionInfo, x9..x29 and v
             regs are live from the load, svc 0x28; state := IDLE. Handler returns non-zero: process ends with that exit code.
Delivery rule (total; every other case ends the process loudly): type in 0x100..0x103; ESR.EC in {0x20, 0x22, 0x24}; FSC 0x00..0x0B -> reason 0
(unmapped), 0x0C..0x0F -> 1 (protection), 0x21 -> 2 (alignment); the thread has a handler and state IDLE; kind 2 (execute) is always a guest
fault; kinds 0/1 need the faulting pc inside the guest image [g_crash_guest_base, +g_crash_guest_size), else it is a host fault (ends the process).
Registration: stack_top not 16-aligned, < 816 or >= 4 GiB -> 0xCC01, nothing registered; handler == 0 unregisters (no stack check).

## Assumptions (stated; hardware cannot be tested here)
A1 user exception handlers are enabled in the kernel and the process is aarch64 (PLR layout aarch64).
A2 crt0 calls the overridden __libnx_exception_entry with x0 = type, x1 = &ExceptionInfo.
A3 `svc #0xFF` yields ExceptionType 0x301 (InvalidSystemCall), not a process kill.
A4 ExceptionInfo.esr / far are the raw ESR_EL1 / FAR_EL1 values.
A5 the guest stack registered by the handler is mapped RW whenever a fault can occur.
A6 the kernel has one exception slot per process (kern_k_process.cpp:798-830), so only one thread is in exception state at a time; the shared
   fnvnx_hf_cstack / dieframe used in exception state are therefore not reentered.
A7 the host branches to guest pointers only at thread start and image entry, so a fault with pc outside the guest image is a host fault.
A8 handler == 0 skips stack validation (nothing is registered, so nothing can be misused).

## Verify first on the console
1. A fault in a thread reaches the entry (A1/A2); x1 != -1; esr/far for unmapped, read-only write, misaligned atomic (A4).
2. Resume with a modified frame (pc, x0, x9, sp, NZCV) continues exactly there; v registers and fpcr/fpsr survive.
3. The invalid-svc re-entry (A3) returns to the entry with type 0x301.
4. Nested fault inside the handler ends the process with the host's note line; a fault in thread B while A is in a handler waits, then is served (A6).
5. A host-side bad pointer ends the process with no delivery (A7).

## Test doubles and gate results (this container)
- kdouble.h KD_EXCEPTION models the kernel with Linux signals under qemu-aarch64. Handlers run on a per-thread sigaltstack (the Linux signal frame
  is ~4.5 KB, larger than the 4 KB exception stack). qemu rounds fault addresses, so tests use 16-aligned addresses. brk #0x28 models svc 0x28,
  brk #0xFF models the invalid svc; the double reads tpidr_el0 for TLS where the Switch uses tpidrro_el0 + 0x1F8.
- Before: hostapi_total.py red (service 10 unserved). After: hostapi_total ok, 35 = 35.
- run_qemu.sh: all tests ok, incl. host_fault, host_fault_logic (exhaustive oracle sweep of the delivery rule), host_total.
- run_host_sanitized.sh (ASan+UBSan, no recover): host_fault_logic ok, 1432 delivered, 686696 ended.
- build_native.sh NOT runnable here (no devkitA64 aarch64-none-elf-gcc). A syntax-only aarch64-linux-gnu-gcc pass of host.c shows no error in
  host_fault.inc; the only errors are in host_sock.inc (sin_len/sa_len, Linux headers), unrelated to this change.
