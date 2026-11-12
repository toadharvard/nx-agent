# L3a w8: A-NATIVE (ASM-207), the FP state across lifted<->native transfers. Rule and proof argument, before any code.

Sources read: lift/rt/imp.h (fnvl_native_in/out), lift/rt/enter.cc (fp_park, fnvl_invoke_regs_main, fnvl_thread_main), lift/rt/fnvl.h sse_fpcr, lift/rt/fnvl_x87.h
(fnvl_fp_set_rc_x87, fnvl_fp_x87_flags, fnvl_fp_x87_clear), lift/ASSUMPTIONS.l3b.json (A-NATIVE).

## What the state is
- FPCR: the only bits lifted code ever changes are RMode [23:22], from fldcw (fnvl_fp_set_rc_x87), ldmxcsr (sse_fpcr) and the seh restore (seh.cc:103).
  FZ [24], DN [25], AHP [26] and the trap enables [12:8] are 0 in the guest by construction (nothing writes them; cpu_reset / fnvl_thread_main do fp_set(0, 0)).
- FPSR: the cumulative flags IOC[0] DZC[1] OFC[2] UFC[3] IXC[4] IDC[7]; the guest's x87 sticky flags are read from them (fnvl_fp_x87_flags) together with c->fex.
- Parking (imp.h, enter.cc): an import entry saves FPCR and FPSR in c->hfpcr / c->hfpsr, runs the body under FPCR = 0, and puts both back on return.
  A callback from an import (fnvl_invoke_regs_main) starts with fp_set(c->hfpcr, c->hfpsr), the parked guest values.

## The lead's view, checked
"A native body implements the exe function itself, so it runs under the guest FPCR and its flags are the function's own; parking applies only to imports."
- FPCR: HOLDS. The exe function's own FP depends on the current RMode (fistp, _controlfp users). The native body must see it, so it must not be parked, and it must be
  compiled to read the rounding mode (the game's own flags: -frounding-math, JOURNAL.md; a build flag of the native root, to be checked by the gate on the native root's flags).
  Differing bits between guest and parked state: only RMode (FPCR 0 vs guest value) and the sticky flags (FPSR parked vs live).
- FPSR: HOLDS AS A MECHANISM, NOT AS AN EQUALITY. The native body raises the flags ITS OWN operations raise (binary64/binary32 IEEE: IXC for an inexact double result,
  UFC/OFC at double range). The exe's x87 sequence raised the flags of the 80-bit operations (PE where the extended result is inexact, different underflow/overflow
  thresholds). So after a native body the guest's sticky flags are in general NOT the flags the exe body would have left. The owner's order (FP plain IEEE, no 80-bit bit-equality) accepts
  different VALUES; it says nothing about the flag words. Class-closing rule for the flags, which needs a static fact and is not proven here:
  the flag words are observable only through readers (fnstsw / fstsw / fnstenv / fnsave / stmxcsr / _statusfp / _controlfp-family callers in the exe closure). If on every path from a native
  body's exit to every reader a clearing instruction (fnclex / fninit / _clearfp / fldenv) comes first, or no reader is reachable, any flag state at the exit is equivalent.
  The gate `native_fpflags`: for each native body, the set of readers reachable from its exits without an intervening clear must be empty. Until it exists, the claim
  "native bodies leave the exe's flags" is an assumption and must stay OPEN in the registry (it is not one of the nine OWNER assumptions).

## Rule R-FP (what the code must do)
- G1. Guest code (lifted code and native bodies alike) runs under the guest FPCR and FPSR. A transfer between two pieces of guest code (lifted->native body,
  native body->lifted function through DISPATCH1's bridge and fnvnx_guest_invoke_regs) does not read, write or park FPCR/FPSR.
- H1. Host code that is not the exe (import bodies, rt/imp.h, platform) runs parked: FPCR = 0, guest FPSR kept aside, and both restored on return. This is the existing pair.
- Proof argument (induction over the transfers). Invariant I: whenever guest code executes, FPCR/FPSR hold the guest state. Base: fnvl_thread_main sets (0, 0).
  Steps: guest->guest (G1) preserves I by doing nothing; guest->import parks and restores (H1), so the guest state is unchanged on return; an import callback restores
  the parked guest state before guest code runs and re-parks what it leaves. Every transfer is one of these.

## Two defects that G1 shows in the current code (found by reading, not by a run)
1. Native body -> lifted function (bridge -> fnvnx_guest_invoke_regs -> fnvl_invoke_regs_main): enter.cc does `fp_set(c->hfpcr, c->hfpsr)` unconditionally. That restores the
   PARKED values, valid only when the caller is an import entry (something parked them). When the caller is a native body nothing was parked: c->hfpcr/c->hfpsr are
   stale (zero or an earlier park), and the callee runs under the wrong RMode and with its sticky flags overwritten. G1 requires: restore only if parked.
   Needed: a `parked` state in struct cpu, set by fnvl_native_in / fp_park, cleared by fnvl_native_out and by the restore; invoke_regs restores iff parked, and on return re-parks iff it restored.
2. Native body -> import: a native body calls a platform body as a plain C++ call, so H1 does not hold (the import runs under the guest FPCR and flags go into the guest FPSR).
   Either the platform body's FP is proven independent of RMode and its flags unobservable, or the call goes through an entry that parks. The class rule: every call from a native
   body to a symbol of the platform/import set is made through the park pair, emitted by a rule (a plugin or the recipe's native root), not by hand per site. This needs L2/L5; I do not
   know yet which of the two applies and will not guess.

## Removed_by (proposal for the registry)
"fact: guest code and native bodies run under the guest FPCR/FPSR (G1, I), imports parked (H1); gate native_fpflags (readers unreachable without a clear); native root built with the rounding-mode flag".
Open until native_fpflags exists and defects 1 and 2 are closed.

## Corrections after the implementation (37e3f069)
1. Defect 1 is fixed STATICALLY: rt/enter.cc has two entries by kind (fnvnx_guest_invoke = import callback, restores the parked state and re-parks; fnvnx_guest_invoke_regs = bridge from a native
   body, leaves FPCR/FPSR alone). No run-time `parked` flag. Tests: disptest cases fsw, frm (fail with the old unconditional restore, pass now).
2. The sticky flags are OWNED (rt/fnvl_x87.h): the live FPSR counts only while a family owns it; entering from outside, the owner is read from c->fex. So flags a native body raises matter only when
   x87 or SSE is the owner at the moment, which narrows the native_fpflags gate (readers reachable while a family owns the FPSR across the native body).
3. The lifted fistp reads c->fcw, not the host FPCR; only the lifted arithmetic uses the host rounding mode.
4. A native body that calls a bridge needs the caller's guest registers (esp) in c->r[] first. Imports get them from fnvl_native_in (which also parks). A native-body entry needs the same register
   save WITHOUT the park. That entry does not exist in the tree yet: it belongs to whoever emits native entries.
