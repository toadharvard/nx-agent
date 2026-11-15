# l3b-w9: SIMD reference spec (simdspec.py, ssefp.py, simdcheck.py)

Commit: see the push of branch claude/fnv-l3b-lift-w9 (tranche 2 = 5f46ce18, FP tranche = the commit after it).
Measured by `python3 -I lift/proof/simdcheck.py simd_keys.tsv` over the whole pack shape universe (4290 shape keys), at that commit:
- proved 2659, proved-by-cases 98 (the builder branches on the immediate: all 256 values checked), FAILED 0
- no-form 773: a spec row exists (add/sub/mul/div/min/max/sqrt, ps pd ss sd) but the symbolic Form does not (forms_simd.py has no FP arithmetic handler; the lifter emits
  it in C++, fnvlift/f_sse.py). Owner of the Form: L3a (symbolic Form) -> then these prove with no further spec work.
- no-row 760: shifts (psrl/psll/psra), pmul*/pmaddwd, pack*, pextrw/pinsrw, pavg/pmin/pmax/psadbw, cmpps family, comis, cvt*, rcp/rsqrt, ldmxcsr/stmxcsr, emms, psubq mm, movntps.
3DNow shapes are #UD on the declared CPU (L3a's certificates), not rows.

FP spec: z3 FPA with MXCSR (RC, DAZ, FTZ, six flags), ACL2 rtl/excps.lisp + arith-spec.lisp cases, masked branches only (ASM-204 as an environment fact `masks_set`).
Reference run: the host x86-64 CPU executes the row's own bytes between ldmxcsr/stmxcsr: 8400 cases, 0 differences (QEMU does not keep SSE flags and ignores FTZ).

Findings for the owners:
1. ACL2 sse-max/min sets DE for a denormal operand even with a NaN operand; the CPU and SDM 1:4.9.2 do not. The spec follows the CPU/SDM (recorded in CERTS.md).
2. forms_simd.py `_shufps` / `_shufpd` / pshuf* branch on the immediate (builder-branches-on-field): simdcheck proves them by 256 cases; a symbolic select would prove them in one query (L3a).
3. pinsrw/pextrw/pshufw immediate masking defect: already sent to L3a.

## SIMD remainder (final)

Universe: lift/proof simdcheck over simd_keys.tsv (4290 shapes), measured at the commit that carries this note, `python3 -I simdcheck.py simd_keys.tsv`:
proved 2828, proved-by-cases 122, no-form 1005, no-row 335, FAILED 0, timeout 0, uncertified 0 (FP shapes are no-form first: no Form side exists for them; once L3a gives one the
result is `uncertified: CPUREF has no mxcsr_mask` until lift/rt/cpumodel.c defines FNVL_CPUREF_MXCSR_MASK; ssefp.cpuref_mxcsr_mask reads that exact define).
- New rows: cmpps/pd/ss/sd, comis/ucomis, ldmxcsr/stmxcsr, cvt* (xmm and general-register forms), rcp/rsqrt (table of rt/rcptab.c), SDM integer rows (shifts, multiplies, packs, pavg, pmin/pmax, psadbw, pmovmskb, pextrw/pinsrw, ...).
- One name: ASM-204 = I-FPMASK everywhere; `x87-masked-sites` removed. Host-CPU runs are measurements (tests/test_simdspec.py FpRows 2880, CvtRows 1280 per run, 0 differences), outside the certificates.
- simdcheck now compares Res.flags (comis/ucomis EFLAGS) and certifies shapes whose immediate is part of the mnemonic (cmpeqps ...) with that fixed byte.
- no-row (335 shapes): 3DNow pf*/pi2fd/pswapd/femms (#UD under the declared CPU, not spec rows), cvtpi2ps (3 shapes: MMX-register form, not done), pause (2 sites), vpavgw (VEX decode of data, 1).
- Findings: ACL2 sse-max/min sets DE with a NaN operand (CPU and SDM 1:4.9.2 do not); ACL2 ldmxcsr has no reserved-bit #GP; forms_simd _shufps/_shufpd/pshuf branch on the imm (proved by 256 cases); pinsrw/pextrw/pshufw imm masking is with L3a.

## simdcheck on L3a's SSE FP class (subject)

Subject: L3b lead 4e41099f merged with L3a claude/fnv-l3a-lift-int3 47b68e51 (contains 0bfa4a518), prover files untouched; spec side: w9 eee4f6ba plus the --timeout/--progress options of simdcheck.py. Command: `python3 -I simdcheck.py simd_keys.tsv --cleared cleared.tsv --timeout 30000` (cleared.tsv: sdmfeat.py partition of the same 4290 shapes). 4290 shapes:
proved 3033, proved-by-cases 122, ud-class 331 (3DNow and VEX vpavgw: #UD by the sdmfeat partition, the opmap's class), FAILED 108, timeout 566 (30 s; 60 s for the first 1630), no-form 130, uncertified 0 (MXCSR_MASK is read from verify/cpuid_model.json).
- FAILED 108 (cmp 94, divss/sd 12, ucomis 2): two defect classes of the Form against ACL2 + SDM 1:4.9.2 (and the host CPU): (a) a NaN operand together with a denormal operand: the Form sets DE (MXCSR bit 1), the spec does not (QNaN handling precedes the denormal-operand exception); (b) a zero divisor with a denormal dividend: the Form sets DE together with ZE, ACL2 sse-binary-comp gives ZE only.
- timeout 566: add/sub/mul/div/sqrt ps pd ss sd: the FPA obligation of the generic builder does not finish in 30 s; each needs a lane split (the Form is per-lane identical) for L3a to expose or a certificate `split`.
- no-form 130 for L3a: cvtsi2ss 39, prefetchnta 28, rcpps 20, rsqrtss 10, cvtdq2ps 9, rsqrtps 6, cvtpi2ps 3, stmxcsr 2, ldmxcsr 2, rcpss 2, cvtsd2si 2, movntps 2, emms 1, cvtsi2sd 1, cvttsd2si 1, cvtdq2pd 1, pause 1.
