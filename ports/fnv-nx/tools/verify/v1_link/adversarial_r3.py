#!/usr/bin/env python3
"""adversarial_r3.py - V1-link round 3 (J-0081 fixes): new hand-made pairs against the merged abi/abi2, run under
qemu ILP32 with the bridge the code under test generates (tool_bridge mirrors resolve), judged by bridge_harness's
x86 model (MSVC 2008 oracle). No decomp content: all names are made up.

    python3 adversarial_r3.py <work dir>       # exit 1 when an 'expect pass' case fails or a verdict is unexpected

Fix 1 (__fastcall hidden result = first stack word): fastcall aggregates of 4/8/12/16/24 bytes, HFAs, {float},
{double}, a 64-bit first parameter, three register-class parameters, both directions, and the non-POD 8-byte result
(MSVC: hidden buffer in ecx, the arguments one place on; S1b a9fbc1b).
Fix 2 (float vs double result): through conventions with argument moves, aggregate parameters (abi2 path), NaN /
infinity / denormal edge values, and long double (binary128 under GCC: must not be 'same' with float or double).
ecx-not-passed moves: the stack arguments the reference does pass still reach the definition.
"""
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import qemu_ilp32            # noqa: E402
import tool_bridge           # noqa: E402

# layouts: (size, align, trivial-for-calls, AArch64 class, MSVC C++03 POD)
L = {
    'P4f': (4, 4, 1, 'H1x4', True), 'PD': (8, 8, 1, 'H1x8', True), 'P8': (8, 4, 1, 'G1', True),
    'P12': (12, 4, 1, 'G2', True), 'P16': (16, 4, 1, 'G2', True), 'P24': (24, 4, 1, 'IND', True),
    'H3': (12, 4, 1, 'H3x4', True), 'C8': (8, 4, 1, 'G1', False),
}

# (id, expectation, reference, definition) - expectation: 'pass' (a correct bridge or 'same'), 'inconsistent'
# (must not be bridged as consistent: x86-inconsistent or manual), 'manual', 'known' (older open defect)
CASES = [
    ('F1-24-member-edx', 'pass', '?b@T@@QAE?AUP24@@H@Z', '?b@@YI?AUP24@@PAUT@@HH@Z'),
    ('F1-16-member-edx', 'pass', '?c@T@@QAE?AUP16@@H@Z', '?c@@YI?AUP16@@PAUT@@HH@Z'),
    ('F1-12-member-edx', 'pass', '?d@T@@QAE?AUP12@@H@Z', '?d@@YI?AUP12@@PAUT@@HH@Z'),
    ('F1-4f-vs-int', 'pass', '?e@@YIHPAUT@@HH@Z', '?e@@YI?AUP4f@@PAUT@@HH@Z'),
    ('F1-PD-vs-int64', 'pass', '?f@@YI_JPAUT@@HH@Z', '?f@@YI?AUPD@@PAUT@@HH@Z'),
    ('F1-HFA3-member-edx', 'pass', '?g@T@@QAE?AUH3@@M@Z', '?g@@YI?AUH3@@PAUT@@HM@Z'),
    # the fastcall reference expects its callee to pop the hidden pointer and the float (8 bytes; oracle
    # call_fcf_p12), the cdecl definition pops nothing: a pop mismatch, bridged and listed (S2c, J-0102)
    ('F1-24-ref-fastcall-float', 'pass-inconsistent', '?h@@YI?AUP24@@MH@Z', '?h@@YAPAUP24@@PAU1@M@Z'),
    ('F1-12-int64-first', 'pass', '?j@T@@QAE?AUP12@@_J@Z', '?j@@YI?AUP12@@_JH@Z'),
    ('F1-12-four-ints', 'pass', '?m@@YI?AUP12@@HHHH@Z', '?m@@YIPAUP12@@HHPAU1@HH@Z'),
    ('F1-12-def-explicit', 'pass', '?n@@YIPAUP12@@HHPAU1@HH@Z', '?n@@YI?AUP12@@HHHH@Z'),
    ('F1-24-both-fastcall', 'pass', '?o@@YI?AUP24@@HH@Z', '?o@@YIXHHPAUP24@@@Z'),
    ('F1-8-member-vs-regs', 'inconsistent', '?l@T@@QAE?AUP8@@H@Z', '?l@@YI?AUP8@@PAUT@@HH@Z'),
    # MSVC 2008 puts a non-POD's fastcall result buffer in ecx and moves the arguments (S1b a9fbc1b, J-0119; V1
    # tests/v1/test_fastcall_nonpod.py). `void k2(C8 *, int, int)` passes the buffer where `C8 k2(int, int)` reads it.
    # `void k(int, int, C8 *)` hands its first int to the buffer pointer, as x86 would (sret-explicit routes it and the
    # run passes); open for S2c: sret-explicit takes a non-pointer parameter as the buffer (the mirror of
    # result-buffer-not-written)
    ('F1-8-nonpod-hidden', 'pass', '?k@@YIXHHPAUC8@@@Z', '?k@@YI?AUC8@@HH@Z'),
    ('F1-8-nonpod-ecx', 'pass', '?k2@@YIXPAUC8@@HH@Z', '?k2@@YI?AUC8@@HH@Z'),
    ('F2-member-fastcall', 'pass', '?n2@T@@QAENXZ', '?n2@@YIMPAUT@@@Z'),
    ('F2-moves-and-width', 'pass', '?o2@T@@QAENH@Z', '?o2@@YIMPAUT@@HH@Z'),
    ('F2-narrow-from-double', 'pass', '?o3@T@@QAEMH@Z', '?o3@@YINPAUT@@HH@Z'),
    ('F2-agg-param', 'pass', '?s2@T@@QAENUH3@@@Z', '?s2@T@@QAEMUH3@@@Z'),
    ('F2-agg-param-rev', 'pass', '?s3@@YAMUP12@@M@Z', '?s3@@YANUP12@@M@Z'),
    ('F2-longdouble-float', 'manual', '?p@@YAOXZ', '?p@@YAMXZ'),
    ('F2-float-longdouble', 'manual', '?q@@YAMXZ', '?q@@YAOXZ'),
    ('F2-longdouble-double', 'manual', '?r@@YAOXZ', '?r@@YANXZ'),
    # callee-pop conventions with another stack byte count: x86 returns with esp moved (cannot have worked)
    ('P-stdcall-pops-fewer', 'inconsistent', '?sp1@@YGHHHH@Z', '?sp1@@YGHHH@Z'),
    ('P-thiscall-pops-fewer', 'inconsistent', '?sp2@T@@QAEHHH@Z', '?sp2@T@@QAEHH@Z'),
    ('E-ecx-not-passed-moves', 'pass-inconsistent', '?e1@@YGXPAX@Z', '?e1@@YIXPAXHPAUB@@@Z'),
    ('E-ecx-not-passed-stack2', 'pass-inconsistent', '?e3@@YGXHH@Z', '?e3@@YIXHHHH@Z'),
]
EDGE_CASES = [('F2-edges-d<-f', '?w1@@YANM@Z', '?w1@@YAMM@Z'), ('F2-edges-f<-d', '?w2@@YAMN@Z', '?w2@@YANN@Z')]


def long_double_probe(work):
    """What a GCC AArch64 caller declaring `long double r(void)` reads from a callee returning double 3.5."""
    d = os.path.join(work, 'ld')
    os.makedirs(d, exist_ok=True)
    a, b = os.path.join(d, 'a.c'), os.path.join(d, 'b.c')
    open(a, 'w').write('void v1_hex(const void *, unsigned long); void v1_puts(const char *);\n'
                       'long double r_ld(void);\nint main(void) { long double v = r_ld(); v1_puts("LD ");'
                       ' v1_hex(&v, sizeof v); v1_puts("\\n"); return sizeof(long double); }\n')
    open(b, 'w').write('double r_ld(void) { return 3.5; }\n')
    code, out = qemu_ilp32.run(qemu_ilp32.build([a, b], os.path.join(d, 'ld')))
    return code, out.strip()


def main():
    work = sys.argv[1]
    os.makedirs(work, exist_ok=True)
    names = sorted({n for c in CASES for n in c[2:]} | {n for c in EDGE_CASES for n in c[1:]})
    dem = bh.undname(names)
    tl = {k: v[:5] for k, v in L.items()}
    hl = {k: bh.Lay(*v) for k, v in L.items()}
    bad = 0
    for cid, want, ref, dfn in CASES:
        kind, classes, asm = tool_bridge.verdict(ref, dfn, dem, tl, tl)
        st = 'not run'
        if want in ('pass', 'known', 'pass-inconsistent') or kind == 'bridge':
            try:
                sts = []
                for seed in (1, 2, 3):
                    s, notes = bh.run_case(ref, dfn, hl, hl, asm, os.path.join(work, cid), seed=seed, dem=dem)
                    sts.append(s)
                st = next((s for s in sts if s != 'pass'), 'pass')
            except bh.Skip as e:
                st = 'skip ' + str(e)
        ok = {'pass': st == 'pass' and kind in ('same', 'bridge'),
              'pass-inconsistent': st == 'pass' and kind == 'x86-inconsistent' and asm is not None,
              'inconsistent': kind in ('x86-inconsistent', 'manual'),
              'manual': kind in ('manual', 'x86-inconsistent'),
              'known': True}[want]
        if not ok:
            bad += 1
        print('%-6s %-26s want %-17s tool=%-16s %-44s run: %s' % ('ok' if ok else 'BROKEN', cid, want, kind,
                                                                  ','.join(classes)[:44], st[:110]))
        print('         ref: %s\n         def: %s' % (dem[ref], dem[dfn]))
    for cid, ref, dfn in EDGE_CASES:
        kind, classes, asm = tool_bridge.verdict(ref, dfn, dem, {}, {})
        res = []
        for edge in range(5):
            s, _ = bh.run_case(ref, dfn, {}, {}, asm, os.path.join(work, cid), seed=1, dem=dem, edge=edge)
            res.append(s)
        okk = all(r == 'pass' for r in res)
        bad += 0 if okk else 1
        print('%-6s %-26s tool=%-16s edge values (-0, denormal, inf, qNaN payload, 1.5): %s' % (
            'ok' if okk else 'BROKEN', cid, kind, ' | '.join(r[:60] for r in res)))
    # long double under GCC AArch64 ILP32: 16 bytes, binary128 in q0
    code, out = long_double_probe(work)
    want = struct.pack('<d', 3.5).hex()
    print('long double probe: sizeof %d, a caller declaring long double reads %s from a callee returning double '
          '3.5 (binary64 3.5 = %s)' % (code, out, want))
    print('broken: %d' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
