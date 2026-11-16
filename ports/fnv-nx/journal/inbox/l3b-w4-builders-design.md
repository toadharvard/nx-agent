# Design note: certified builders for x87 (2,063 shapes), simd (4,291), sys (129)   [w4 → L3b lead, 2026-11-14]

## What the pinned reference actually contains (measured, not assumed)
Reference = ACL2 x86isa master 3ccb342c via sail-x86-from-acl2 @0c6c023d (ASM-009 / assumption 9). Its Sail translation includes only: integer ISA, string ops, syscall/sysret, cpuid (after the bump),
segment moves, `mov.sail` (movaps/movups/movdqu/movlps/movhps/movd/movq) and `shuffle_and_unpack.sail` (shufps/shufpd/unpck). `topfp.sail` is 2 lines (includes those two files).
The ACL2 source has MORE than the Sail translation: instructions/{logical,padd,psub,pcmp,pmul,pmadd,pshift,pshuf,punpck,pack,move-mmx,move-sse,emms}.lisp and instructions/fp/ (SSE FP);
for x87 it has ONLY fxsave/fxrstor (instructions/x87.lisp). There is no x87 arithmetic, stack or control word anywhere in the reference.

## Rule per family (each is a class rule; none selects by test)
1. sys (129): same mechanism as int. Forms over the integer state (+ selectors/MSR-less) proved against the IR. Present in the IR: syscall/sysret, push/pop sreg, cpuid (bump), lahf/sahf, etc.
   Instructions the model leaves out (in/out, hlt, cli/sti, rdtsc, int, …) are not provable against it; for them the lifter semantics is a declared platform call (owner: rdtsc = platform timer, cpuid = fixed reference CPU) -
   each needs an OWNER-registered assumption id or a different reference; I will list them by key from census (needs the 129 keys by mnemonic: next step, one run).
2. simd integer + SSE data movement (4,291): extend the DSL state with xmm0-7 (128-bit) and mm0-7 (aliasing the x87 significands, 64-bit) + mxcsr as Form regs; Form lane statements already exist in the lifter as C templates (f_simd_int/f_sse),
   the Form must be DSL nodes (lane = extract/op/concat) so z3 sees them. Reference: transcribe the ACL2 books above into Sail patches (same procedure as the bump: line-cited transcriptions, ASM-009), build into the IR,
   then the existing shape prover compares post-states (xmm/mm in the output set). New IR = new pin (the same bump as item 2; one IR sha for all).
3. SSE FP (inside the 4,291): ACL2 fp/ specs are bit-exact IEEE (binary32/64, rounding by MXCSR.RC, denormal/flags); DSL FP nodes map to z3 FPA (fp.add etc., RNE and the other three modes); the native C `float/double` op equals it by assumption 8 (IEEE), the lifted op is then proved equal to the ACL2 spec by z3 FPA. Flags in MXCSR (exceptions) are part of the state and are compared.
4. x87 (2,063): NO REFERENCE EXISTS in the pinned model, so a model-based certificate is impossible (model-gap). I see no class-closing option within the 9 accepted assumptions that keeps "proved against the IR":
   (a) owner decision: x87 semantics := IEEE binary64 on a stack (owner order of 2026-11-10 on FP) with the stack/tag/status bookkeeping as the Form's own spec. The proof is then Form-vs-Form (lifter C template vs a z3 FPA stack model I write): it proves
       the template equals the written spec, NOT that the spec equals the exe's CPU. That is a new OWNER assumption ("x87 stack/control-word model written from the SDM") - needs the owner's decision, I must not add it.
   (b) transcribe x87 from another formal model (e.g. isla-snapshots / sail-x86 do not have it; Intel XED has decode only). None found.
   (c) leave x87 as model-gap in certs.tsv: formgate then must treat x87 shapes as "uncertified form" = red, which blocks the pack root.
   → I need decision (a)/(c) from the lead/orchestrator before building x87 builders; the work is large either way (2,063 shapes, ~120 forms).

## Order of work I propose
IR bump (running) → sys keys by mnemonic (1 run) → simd-int Forms+Sail transcriptions (biggest closed class: 4,291 shapes, mostly int lanes) → SSE FP via z3 FPA → x87 after the decision.
