#!/usr/bin/env python3
"""adversarial_abi.py - V1-link: hand-made (reference, definition) pairs run through the code under test
(tool_bridge.verdict -> abi/abi2 verdict and bridge) and then executed under qemu-aarch64 (bridge_harness) against
the MSVC x86 semantics the oracle established. No decomp content: all names are made up.

    python3 adversarial_abi.py [work dir]

Cases marked 'control' must pass; the 'bug' cases reproduce three defects of the code under test (exit 1 while any
of them still fails, 2 when a control fails):

  A  MSVC returns a free function's 1/2/4/8-byte aggregate in edx:eax only for a C++03 POD (oracle: a user
     constructor -> hidden buffer); abi2 decides by Itanium triviality, so `C4 f()` vs `void f(C4 *)` is 'same'.
  B  __fastcall with a hidden result: MSVC passes the buffer as the first stack word and keeps ecx/edx for the
     arguments (oracle); abi2's bridge puts it in place of the first register argument. Real in f12: all 25
     bridged references of 0x0056fa00 (a __fastcall function returning a 36-byte aggregate, referenced from
     thiscall members) store the result over *this and never into the caller's buffer (c2_bridges.py with
     V1_SELECT=fastcall-agg).
  C  float vs double results are 'same' (one register file): x86 converts exactly through st(0), AArch64 reads d0
     where the callee wrote s0. Real in f12: 130 pairs / 190 references bound directly (c2_shapes.py)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import tool_bridge           # noqa: E402

# layouts: (size, align, trivial-for-calls (Itanium), AArch64 class, MSVC C++03 POD)
C4 = (4, 4, 1, 'G1', False)      # struct C4 { int a; C4() {} };  (oracle: MSVC returns it through a hidden pointer)
P12 = (12, 4, 1, 'G2', True)     # struct P12 { int a, b, c; };
P8 = (8, 4, 1, 'G1', True)       # struct P8 { int a, b; };
H3 = (12, 4, 1, 'H3x4', True)    # struct V3 { float x, y, z; };

CASES = [
    # (id, role, ref, def, layouts)
    ('ctl-regshift', 'control', '?n@T@@QAEHH@Z', '?n@@YGHH@Z', {}),
    ('ctl-edxdummy', 'control', '?m@T@@QAEHHH@Z', '?m@@YIHPAUT@@HHH@Z', {}),
    ('ctl-intfloat', 'control', '?q@T@@QAEXH@Z', '?q@T@@QAEXM@Z', {}),
    ('ctl-sret-explicit', 'control', '?s@T@@QAEXPAUP12@@@Z', '?s@T@@QAE?AUP12@@XZ', {'P12': P12}),
    ('ctl-hfa-vs-floats', 'control', '?v@@YAXUV3@@@Z', '?v@@YAXMMM@Z', {'V3': H3}),
    ('ctl-i64-vs-2int', 'control', '?w@@YAX_J@Z', '?w@@YAXHH@Z', {}),
    ('A1 free C4 result vs out pointer', 'bug', '?f@@YAXPAUC4@@@Z', '?f@@YA?AUC4@@XZ', {'C4': C4}),
    ('A2 out pointer vs free C4 result', 'bug', '?f@@YA?AUC4@@XZ', '?f@@YAXPAUC4@@@Z', {'C4': C4}),
    ('B1 fastcall hidden result', 'bug', '?g@@YIXHHPAUP12@@@Z', '?g@@YI?AUP12@@HH@Z', {'P12': P12}),
    ('B2 fastcall hidden result, ref explicit', 'bug', '?g@@YI?AUP12@@HH@Z', '?g@@YIXHHPAUP12@@@Z', {'P12': P12}),
    ('C1 double read of a float result', 'bug', '?h@@YANXZ', '?h@@YAMXZ', {}),
    ('C2 float read of a double result', 'bug', '?h@@YAMXZ', '?h@@YANXZ', {}),
    ('C3 double member read of int fastcall', 'x86-chain', '?F@D@@QAENXZ', '?F@@YIHPAX@Z', {}),
]


def main():
    work = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='v1_adv_')
    names = sorted({c[2] for c in CASES} | {c[3] for c in CASES})
    dem = bh.undname(names)
    bad_controls = bugs = 0
    for cid, role, ref, dfn, lays in CASES:
        tl = {k: v[:4] for k, v in lays.items()}
        hl = {k: bh.Lay(v[0], v[1], v[2], v[3], v[4]) for k, v in lays.items()}
        kind, classes, asm = tool_bridge.verdict(ref, dfn, dem, tl, tl)
        try:
            st, notes = bh.run_case(ref, dfn, hl, hl, asm if kind == 'bridge' else None,
                                    os.path.join(work, cid.split()[0]), seed=7, dem=dem)
        except bh.Skip as e:
            st, notes = 'skip ' + str(e), []
        if role == 'control' and st != 'pass':
            bad_controls += 1
        if role == 'bug' and st != 'pass':
            bugs += 1
        print('%-40s %-9s tool=%-16s %-40s -> %s %s' % (cid, role, kind, ','.join(classes)[:40], st,
                                                      ('[' + '; '.join(notes) + ']') if notes else ''))
        print('      ref: %s\n      def: %s' % (dem[ref], dem[dfn]))
    print('controls failing: %d; defect reproducers failing (defect present): %d' % (bad_controls, bugs))
    return 2 if bad_controls else (1 if bugs else 0)


if __name__ == '__main__':
    sys.exit(main())
