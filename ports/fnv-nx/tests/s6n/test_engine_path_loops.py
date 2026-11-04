#!/usr/bin/env python3
"""test_engine_path_loops.py - render/native/perf/engine_path.py loop counting on hand-written AArch64 (P6n).

Each case is a function whose dynamic instruction count is known; the counter's `exp` (and `max`, where the path
is unique) must match it exactly with the loop trips given in the spec. Covers V1's refutation L1 (nested loops
did not multiply: a 4 x 4 nest counted 39 of 66) plus deeper and sibling nests, a triangular nest (inner trips
depend on the outer index: the static model takes the mean trip count, which is exact for the total), and a nest
with a conditional block inside the inner body (probability from the spec).

    python3 -I tests/s6n/test_engine_path_loops.py      (needs aarch64-linux-gnu-as or llvm-mc, and llvm-objdump)
No decomp or game content: the objects are assembled here from the text below.
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'native', 'perf'))

ASM = r"""
    .text
    .globl nest44
    .type nest44, %function
nest44:                         // V1 L1: outer 4 x inner 4
    mov w1, #0
1:  mov w2, #0
2:  add w2, w2, #1
    cmp w2, #4
    b.ne 2b
    add w1, w1, #1
    cmp w1, #4
    b.ne 1b
    ret
    .size nest44, .-nest44

    .globl single4
    .type single4, %function
single4:                        // one loop, 4 trips
    mov w2, #0
3:  add w2, w2, #1
    cmp w2, #4
    b.ne 3b
    ret
    .size single4, .-single4

    .globl nest234
    .type nest234, %function
nest234:                        // three deep: 2 x 3 x 4
    mov w1, #0
4:  mov w2, #0
5:  mov w3, #0
6:  add w3, w3, #1
    cmp w3, #4
    b.ne 6b
    add w2, w2, #1
    cmp w2, #3
    b.ne 5b
    add w1, w1, #1
    cmp w1, #2
    b.ne 4b
    ret
    .size nest234, .-nest234

    .globl siblings
    .type siblings, %function
siblings:                       // outer 3 holding two inner loops of 2 and 5 trips
    mov w1, #0
7:  mov w2, #0
8:  add w2, w2, #1
    cmp w2, #2
    b.ne 8b
    mov w3, #0
9:  add w3, w3, #1
    nop
    cmp w3, #5
    b.ne 9b
    add w1, w1, #1
    cmp w1, #3
    b.ne 7b
    ret
    .size siblings, .-siblings

    .globl tri
    .type tri, %function
tri:                            // triangular: for i in 0..3: for j in 0..i  (1+2+3+4 = 10 inner trips)
    mov w1, #0
10: mov w2, #0
11: add w2, w2, #1
    cmp w2, w1
    b.ls 11b
    add w1, w1, #1
    cmp w1, #4
    b.ne 10b
    ret
    .size tri, .-tri

    .globl condnest
    .type condnest, %function
condnest:                       // outer 2 x inner 4, the inner body has a 2-word block taken half the time
    mov w1, #0
12: mov w2, #0
13: tbz w2, #0, 14f
    add w4, w4, #1
    add w5, w5, #1
14: add w2, w2, #1
    cmp w2, #4
    b.ne 13b
    add w1, w1, #1
    cmp w1, #2
    b.ne 12b
    ret
    .size condnest, .-condnest
"""

# name: (address, loops {header offset: trips}, branches {offset: P(taken)}, dynamic count, max is exact too)
CASES = {
    'nest44':   ('00401000', {'4': 4, '8': 4}, {}, 1 + 4 * (1 + 4 * 3 + 3) + 1, True),             # 66
    'single4':  ('00402000', {'4': 4}, {}, 1 + 4 * 3 + 1, True),                                  # 14
    'nest234':  ('00403000', {'4': 2, '8': 3, 'c': 4}, {}, 1 + 2 * (1 + 3 * (1 + 4 * 3 + 3) + 3) + 1, True),
    'siblings': ('00404000', {'4': 3, '8': 2, '18': 5}, {}, 1 + 3 * (1 + 2 * 3 + 1 + 5 * 4 + 3) + 1, True),
    'tri':      ('00405000', {'4': 4, '8': 2.5}, {}, 1 + 4 * (1 + 3) + 10 * 3 + 1, True),            # 48
    # inner body: tbz (1) + taken half -> 2 words on half the trips + 3; tbz at +8 taken (skip) P = 0.5
    'condnest': ('00406000', {'4': 2, '8': 4}, {'8': 0.5}, 1 + 2 * (1 + 4 * 1 + 4 * 3 + 4 + 3) + 1, False),
}


def assemble(src, obj):
    if shutil.which('aarch64-linux-gnu-as'):
        subprocess.run(['aarch64-linux-gnu-as', '-o', obj, src], check=True)
    elif shutil.which('llvm-mc'):
        subprocess.run(['llvm-mc', '-triple=aarch64-linux-gnu', '-filetype=obj', '-o', obj, src], check=True)
    else:
        return False
    return True


def main():
    if not shutil.which(os.environ.get('OBJDUMP', 'llvm-objdump')):
        print('skip: llvm-objdump missing'); return 0
    import engine_path
    work = tempfile.mkdtemp(prefix='s6n-engine-path-')
    bad = 0
    try:
        src = os.path.join(work, 't.s'); open(src, 'w').write(ASM)
        od = os.path.join(work, 'obj'); os.makedirs(od)
        if not assemble(src, os.path.join(od, 't.o')):
            print('skip: no AArch64 assembler'); return 0
        lo = os.path.join(work, 'link'); os.makedirs(lo)
        open(os.path.join(lo, 'addrmap.tsv'), 'w').write(
            ''.join('%s\tF\t-\t%s\tt.o\n' % (v[0], k) for k, v in CASES.items()))
        open(os.path.join(lo, 'refs.tsv'), 'w').write('')
        ov = os.path.join(work, 'overrides.tsv'); open(ov, 'w').write('')
        spec = {'loops': {v[0]: v[1] for v in CASES.values()},
                'branches': {v[0]: v[2] for v in CASES.values() if v[2]}}
        C = engine_path.Counter(od, lo, spec, ov)
        for name, (addr, loops, br, truth, max_exact) in CASES.items():
            mn, ex, mx, _ = C.count(('t.o', name))
            ok = abs(ex - truth) < 1e-6 and (not max_exact or abs(mx - truth) < 1e-6) and mx >= ex - 1e-6
            bad += not ok
            print('%-9s %s: dynamic %g, exp %g, max %g, min %g' % (name, 'ok' if ok else 'FAIL', truth, ex, mx, mn))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('%d failed' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
