# l3a-w7: SIMD FP flag priority and the 17 no-form mnemonics (branch claude/fnv-l3a-lift-w7-simd, base int3 ffd74aee)

1. One function for the pre-computation flags: `dsl._pre` (z3 model) and `fx_pre` (C twin, rt/fnvl.h), in the priority order of SDM Vol 1 11.5.2.1: SNaN #I, QNaN operand (nothing below), other #I, #Z, #D; #O #U #P are the callers'. `_fp_smt` (arithmetic, min/max, the eight cmp predicates, comis/ucomis), the C helpers `fx_dsl_fp_t` / `fx_dsl_fcmp` and the new `_cvt_smt` / `fx_dsl_cvt` all take their IE / ZE / DE from it. Defect (a) (QNaN + denormal: DE) and (b) (denormal / 0: DE with ZE) are closed by the order, not by a case.
2. New DSL nodes: `dsl.cvt` (int32 <-> binary32 / binary64 lane, rounding by MXCSR.RC, integer indefinite + IE out of range, PE when inexact) and `dsl.tab` (the 12-bit entry of the pinned rt/rcptab.c table; z3 reads the table from the file only when sha256(rcptab.c) = rcptab.cert.json source_sha256 with 0 mismatches; C: `fnvl_tab_rcp` / `fnvl_tab_rsqrt` read the same file's own functions). C twins `fx_dsl_cvt`, `fnvl_tab_*` in rt/fnvl.h, software rounding (no FPCR).
3. Forms (proof/forms_ssefp.py): cvtsi2ss/sd, cvtdq2ps/pd, cvtpi2ps (the MMX rule), cvt(t)ss2si, cvt(t)sd2si, cvt(t)ps2dq, cvt(t)pd2dq, rcpss/ps, rsqrtss/ps, ldmxcsr (#GP(0) = fault kind 7 on bits outside `mxcsr_mask` of verify/cpuid_model.json), stmxcsr, movntps/pd/dq (16-byte aligned store, align fault), prefetchnta/t0/t1/t2 (no effect, no fault), pause (no effect), emms (all tags empty, Form.mmx(emms=True)). lift_dsl.supported() routes them to the DSL emitter ahead of the hand handlers of f_sse.py; those hand handlers are now reachable only with emit='' (the legacy mode of tools/lifted.py) and were not deleted.
4. Tests: proof/tests/fpc_host.py (C helpers vs the z3 model: fp, cvt, tab; 307,420 cases at d90205d3 with 0 disagreements before the Forms), proof/tests/test_forms_simdclass.py (98 checks, 0 fails: SDM-derived directed cases through forms.build and to_c_inline).
5. L3b spec side: prefetchnta/t0/t1/t2 has no row in simdspec.py (and simdcheck.py indexes `m` by `mem_width` even when it is None / 8 bits). In my scratch overlay a row `row('', 0x18, 'prefetchh', [], form='m', mw=None, al=False)(lambda e, d, m, a, g: Res(dict(d)))` (SDM 2B:PREFETCHh, hint, no architectural effect) and `m = None if w is None else (Select(M0, a) if w == 8 else Concat(...))` made prefetch/emms/pause prove. L3b owns both files; the rerun needs them.

## One path (lead correction 1, rule 3) and the numbers (correction 2)

- Hand handlers of every mnemonic a Form covers are deleted from all fnvlift modules (f_sse, f_int, f_flow, f_sys, f_simd_int; f_string is gone: its five mnemonics are Forms).
  Global: hand-handler mnemonics that were also Form mnemonics were 335 (f_sse 121, f_int 89, f_simd_int 67, f_sys 23, f_flow 20, f_string 15); now the set intersection is {call, jmp}.
  test_forms_simdclass.py asserts it, read from the registries (99 checks, 0 fail).
- call / jmp: FF /3 and FF /5 (far CALL / JMP m16:32) are a shape the Forms do not model. lift_dsl.supported() refused none of them before, so a far call through memory
  was built as a near call (a latent defect, closed here: supported() now refuses FF /3 /5, f_sys.far_mem emits them). The two mnemonics keep their handler for that shape.
  The clean fix is a far-transfer Form (selector check + CS push); an owner decision for L3a/L3b, reported to the lead.
- tzcnt / lzcnt: the alias to BSF / BSR (no BMI1 / ABM in CPUREF) moved from the hand handlers into the Form (forms_alu `bs`).
- `emit=''` is gone: tools/lifted.install accepts only 'dsl'; liftgen --emit default 'dsl'. Dead runtime helpers removed from rt/fnvl.h (sse_arith, sse_sqrt, sse_min/max, sse_cmpb, sse_comi).
- The 62 / "representative shapes" figure was a local check (41 proved shapes), not a result. The result is L3b's rerun over all 4,290 shapes at the head commit; a divss/divsd timeout is L3b's timeout class.
- Debt (owner L3a): proof/tests/fpc_host.py compiles fnvl.h with the host compiler, outside the recipe. It moves to a recipe test root (host flags variant) when the L1 `run` stage lands.

## Far-transfer Form (lead decision after 46357156)

- One Form for CALL / JMP far: FF /3, FF /5 (m16:32; m16:16 with 66), 9A, EA (proof/forms_flow.py `far_transfer`, SDM Vol 2 CALL/JMP far, protected mode, same privilege).
  The selector is checked against platform/declared/x86sys.json selectors.cs (0x1b) with the RPL stripped; any other selector is fault 7 (the AV row, as every selector fault of f_sys), raised before any effect.
  Conforming and non-conforming code segments of the same privilege give the same transfer, so no undecided descriptor field is read; the record has no call gate or TSS, so those selectors are the same fault.
  CALL pushes CS (zero-extended to the operand size) then the return EIP. No x86sys field is "undecided" for this Form: nothing to report.
- Deleted: f_sys far_target / h_ljmp / far_call / h_lcall / far_mem / far_call_mem / is_far_ff / the call+jmp wrappers / h_ljmp_any; f_flow h_call / h_jmp. lift_dsl.supported() no longer refuses FF /3, /5; transfer() treats lcall / ljmp like call / jmp.
  Intersection of hand handlers and Forms: {} (test_forms_simdclass), 335 -> 0.
- Form key: the far FF /3, /5 shapes had the key of the near FF /2, /4 (same mnemonic, same m32 class, same opcode): they now end `ff/far` (fnvlift/formkey.py and proof/forms.py). L3b: the new far shapes are `call|m32|ff/far`, `jmp|m48|ff/far`, `lcall|m48|ff/far`, `ljmp|m48|ff/far`(as capstone names them), `lcall|imm,imm|9a`, `ljmp|imm,imm|ea`.
- test: proof/tests/test_forms_far.py, 43 checks, 0 fail (selectors x call/jmp, pushes, 16-bit forms, 9A/EA, keys, emitter).
- f_cpufeat read the CPUID model from committed paths that no longer exist (fnvl_abi.h defines, cpumodel.c E1): it now reads the generated fnvl_cpuref.h and cpumodel_tab.inc through fnvlift/rtgen (the recipe's own stages).
