
## Pinned IR 7c307464: hunks

New pin: x86_proof.ir sha256 7c3074644653d2976cbb101ae993e94f2a09c9b7a3b574d3c74f6a80278d079c (BUMP now defaults to 1 in farm/sail_build.sh; BUMP=0 rebuilds the superseded IR).
All hunks are transcriptions of ACL2 master 3ccb342c88bd3b815856f4bbc336382287934405 (ASM-009, OWNER 9) over the ACL2 8.5 translation; each patch file cites ACL2 file:line in its own header.

| patch (sail_patches/bump/) | ACL2 master source | change |
|---|---|---|
| ror_cf.sail | rotates-spec.lisp:662-665, :709-717 | ror: masked count 0 affects/undefines no flag; CF = MSB of the result |
| rol_count0.sail | rotates-spec.lisp:285-288 | rol: masked count 0 affects/undefines no flag (was: OF undefined) |
| sar_bigcount.sail | shifts-spec.lisp:797-852 | sar, count >= size: CF = MSB of the destination, not undefined |
| cmps.sail | string.lisp:312-560 (:383-386, :397-433, :443, :465-471) | x86_cmps operand order (src1=[rSI], src2=[rDI]) and rep termination |
| cpuid.sail + cpuid_dispatch.py | cpuid.lisp:28-106; inst-listing.lisp:9985-9987 | 0F A2 implemented; eax..edx are declared outputs (rt/cpumodel.c) |
| bit_bsf_bsr.sail + bsf_bsr_dispatch.py | bit.lisp:715-723, 755-843, 870-954; inst-listing.lisp:10314-10325 | 0F BC/0F BD implemented (closed forms z3-checked against the ACL2 recursions: tests/test_bsfbsr_closed_form.py); source 0 leaves the destination, ZF=(src=0), other flags undefined |
| bsf_bsr_dispatch.py (F3 arms) | Intel SDM Vol. 2B, TZCNT and LZCNT pages ("executed as BSF/BSR") | F3 0F BC / F3 0F BD: BMI1 (CPUID.07H.0:EBX[3]) and LZCNT (CPUID.80000001H:ECX[5]) are READ from the declared CPU (lift/rt/cpumodel.c, leaf above the max leaf = absent); both clear, so the arms go to x86_bsf / x86_bsr (a set bit leaves the arm unimplemented) |

Proved on the new IR (shapeprove, SAIL_IR): 0fbc, 0fbd, f30fbc (reg, mem), f30fbd (reg, mem), 66f30fbc: 7 of 7 proved. DSL: forms_alu.py handler bs also covers tzcnt/lzcnt (same F3 rule).
Changed-closure counts follow from certs_build once the v2 farm records land.

Published IR: fnv-nx-build branch tools/sail-ir-7c3074644653 (ir/x86_proof.ir, MANIFEST.json with ir_sha256 and tree_sha256).
