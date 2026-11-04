#!/usr/bin/env python3
"""Differential test of abi2.emit_lean against the generic abi2.emit (stream S2c-abi, bridge cost): both bridges of
one plan are called with the same random registers, stack words and memory under qemu-aarch64, into a recording
definition, and everything the definition can read and everything the caller can see afterwards must agree.

    python3 tools/link/test_bridgefuzz.py                      # the synthetic plans below (no decomp content)
    python3 tools/link/test_bridgefuzz.py --link <out> --units <units.json>   # every abi2 bridge of a link

Per plan k: run_k loads x0-x8, q0-q7 and 128 bytes of stack arguments from a state block, calls the bridge, and
stores x0, x1, q0-q3 and sp. The definition stand-in def_k logs x0-x8, q0-q7 and 128 bytes of its incoming stack,
then acts as the definition: writes a pattern through x8 (indirect result) or through the parameter that receives
the reference's result buffer (returning it in x0), or loads its result registers from the pattern. Compared: the
definition's argument chunks (registers by width, stack bytes, the bytes behind by-reference parameters), the
caller's result registers by the reference's result chunks, the whole memory arena (results written through
pointers) and sp.
"""
import argparse
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi2          # noqa: E402
import fnvlink       # noqa: E402
import layout        # noqa: E402
import sigs          # noqa: E402
import test_bridges  # noqa: E402

ARENA = 65536

# synthetic plans: (reference MSVC, definition MSVC), with test_bridges' TYPES for the layouts
SYNTH = [
    ('?Get@Foo@@QAE?AUVec3@@M@Z', '?Get@Foo@@QAEPAUVec3@@PAU2@M@Z'),        # regs result vs explicit out pointer
    ('?Get@Foo@@QAEPAUBig@@PAU2@H@Z', '?Get@Foo@@QAE?AUBig@@H@Z'),          # explicit pointer vs indirect result
    ('?Get@Foo@@QAEPAUVec3@@PAU2@M@Z', '?Get@Foo@@QAE?AUVec3@@M@Z'),        # explicit pointer vs HFA result
    ('?b@Foo@@QAE?AUBig@@H@Z', '?b@Foo@@QAEPAUBig@@PAU2@H@Z'),              # indirect result vs explicit pointer
    ('?r@A@@QAE?AUI2@@XZ', '?r@B@@QAE?AUF2@@XZ'),                          # result in other registers
    ('?g@@YAXUQ@@@Z', '?g@@YAXHHHH@Z'),                                    # aggregate vs ints (generic only)
    ('?n@A@@QBE?AUQ@@XZ', '?n@B@@QAEXPIAH@Z'),                             # Q result vs out pointer
    ('?d@@YAHPAUFoo@@@Z', '?d@@YAHUHolder@@@Z'),                            # pointer vs by-reference holder
    ('?h@@YA?AUI2@@HH@Z', '?h@@YAPAUI2@@PAU1@HH@Z'),                        # G1 result vs explicit pointer
    ('?c@@YAXHHHH@Z', '?c@@YAXUQ@@@Z'),                                    # ints assembled into a G2 struct
    ('?e@@YAXHUI2@@H@Z', '?e@@YAXHHHH@Z'),                                 # a struct's words split into ints
]


def plan_cases(link=None, units=None):
    """[(name, Plan)] from the synthetic declarations, or every abi2 bridge of a link (bridgecost.bridges)."""
    out = []
    if link:
        import bridgecost
        for addr, ref, f, v in bridgecost.bridges(link, units):
            if v.plan is not None:
                out.append(('%08x %s' % (addr, ref), v.plan))
        return out
    names = sorted({n for pair in SYNTH for n in pair})
    dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    with tempfile.TemporaryDirectory() as tree:
        os.makedirs(os.path.join(tree, 'src'))
        open(os.path.join(tree, 'src', 'types.cpp'), 'w').write(test_bridges.TYPES)
        lay = layout.probe_unit(tree, tree, 'src/types.cpp', ['Vec3', 'Big', 'Rgba', 'Q', 'P2', 'NiPoint2', 'V2',
                                                              'I2', 'F2', 'Holder'])
    for rm, dm in SYNTH:
        p = abi2.compare(sigs.parse_msvc(rm, dem[rm]), sigs.parse_msvc(dm, dem[dm]), lay.get, lay.get)
        if p is not None and p.kind == 'bridge':
            out.append(('%s -> %s' % (rm, dm), p))
    return out


def checks(p):
    """What must agree: [('x'|'v', n, len) | ('stk', off, len) | ('mem', reg n, size)] on the definition's side and
    [('rx'|'rv', n, len)] on the caller's (the arena and sp always)."""
    d, r = [], []
    for v in p.dvals:
        ps = p.pieces.get(v.label)
        if ps is None or any(s == ('refsret',) for _, s, _, _ in ps):
            continue
        for _, cont, co, ln in p.dch[v.label]:
            if cont[0] in ('x', 'v'):
                d.append((cont[0], cont[1], ln))
            elif cont[0] == 'stk':
                d.append(('stk', cont[1] + co, ln))
            elif cont[0] == 'mem' and cont[1][0] == 'x':
                d.append(('mem', cont[1][1], v.size))
    if p.rret is not None and p.rret.kind != 'IND':
        for vo, cont, _, ln in abi2.a64_ret(p.rret):
            r.append(('r' + cont[0], cont[1], ln))
    elif p.def_sret_from is not None or (p.rret is not None and p.rret.kind == 'gpr'):
        r.append(('rx', 0, 4))
    return d, r


def act_asm(k, p):
    """The definition stand-in's behaviour after logging: its result convention."""
    a = []
    size = p.dret.size if p.dret is not None else 0
    sret_param = None
    for v in p.dvals:
        ps = p.pieces.get(v.label)
        if ps and any(s == ('refsret',) for _, s, _, _ in ps):
            sret_param = p.dch[v.label][0][1]
    rs = p.rret.size if p.rret is not None else 16

    def fill(reg, n):
        a.extend(['adrp x10, pat', 'add x10, x10, :lo12:pat', 'mov x11, #0', '1:', 'ldrb w12, [x10, x11]',
                  'strb w12, [%s, x11]' % reg, 'add x11, x11, #1', 'cmp x11, #%d' % n, 'b.lo 1b'])
    if p.dret is not None and p.dret.kind == 'IND':
        a.append('mov x13, x8')
        fill('x13', size)
    elif sret_param is not None and sret_param[0] == 'x':
        a.append('mov x13, x%d' % sret_param[1])
        fill('x13', rs)
        a.append('mov x0, x13')
    elif p.dret is not None:
        a += ['adrp x10, pat', 'add x10, x10, :lo12:pat', 'ldp x0, x1, [x10]', 'ldp q0, q1, [x10]',
              'ldp q2, q3, [x10, #32]']
    return a


def asm_for(k, p, gen, lean):
    lines = ['    .text', gen.replace('__G__', 'gen_%d' % k).replace('__T__', 'def_%d' % k),
             lean.replace('__L__', 'lean_%d' % k).replace('__T__', 'def_%d' % k)]
    body = ['adrp x9, dlog', 'add x9, x9, :lo12:dlog']
    for i in range(0, 8, 2):
        body.append('stp x%d, x%d, [x9, #%d]' % (i, i + 1, 8 * i))
    body.append('str x8, [x9, #64]')
    for i in range(0, 8, 2):
        body.append('stp q%d, q%d, [x9, #%d]' % (i, i + 1, 80 + 16 * i))
    body += ['mov x10, sp', 'mov x11, #0', '2:', 'ldr x12, [x10, x11]', 'add x13, x9, #208', 'str x12, [x13, x11]',
             'add x11, x11, #8', 'cmp x11, #128', 'b.lo 2b']
    body += act_asm(k, p) + ['ret']
    lines.append('    .p2align 2\n    .globl def_%d\ndef_%d:\n' % (k, k) + '\n'.join('    ' + b for b in body))
    return '\n'.join(lines)


RUN = r'''
    .text
    .p2align 2
    .globl run_bridge
run_bridge:                       // x0 = state: x[9] @0, q[8] @80, stk[128] @208, fn @336 | rx[2] @352, rq[4] @368, sp @432
    stp x29, x30, [sp, #-32]!
    stp x19, x20, [sp, #16]
    mov x19, x0
    mov x20, sp
    sub sp, sp, #128
    mov x9, #0
3:  add x10, x19, #208
    ldr x11, [x10, x9]
    str x11, [sp, x9]
    add x9, x9, #8
    cmp x9, #128
    b.lo 3b
    ldp q0, q1, [x19, #80]
    ldp q2, q3, [x19, #112]
    ldp q4, q5, [x19, #144]
    ldp q6, q7, [x19, #176]
    ldr x9, [x19, #336]
    ldp x0, x1, [x19, #0]
    ldp x2, x3, [x19, #16]
    ldp x4, x5, [x19, #32]
    ldp x6, x7, [x19, #48]
    ldr x8, [x19, #64]
    blr x9
    stp x0, x1, [x19, #352]
    stp q0, q1, [x19, #368]
    stp q2, q3, [x19, #400]
    mov x9, sp
    str x9, [x19, #432]
    mov sp, x20
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #32
    ret
'''

C = r'''
extern "C" { __attribute__((aligned(16))) unsigned char arena[%(ARENA)d], arena0[%(ARENA)d], arena1[%(ARENA)d];
__attribute__((aligned(16))) unsigned char pat[64]; __attribute__((aligned(16))) unsigned char dlog[336],
dlog0[336]; __attribute__((aligned(16))) unsigned char st[448], st0[448]; void run_bridge(void *); }
struct Chk { int kind, n, len; };
static unsigned rng;
static unsigned rnd() { rng = rng * 1103515245u + 12345u; return rng >> 8; }
static void cpy(void *d, const void *s, unsigned n) { unsigned char *a = (unsigned char *)d; const unsigned char *b =
  (const unsigned char *)s; while (n--) *a++ = *b++; }
static int eq(const void *x, const void *y, unsigned n) { const unsigned char *a = (const unsigned char *)x, *b =
  (const unsigned char *)y; while (n--) if (*a++ != *b++) return 0; return 1; }
static void setup(unsigned seed, void *fn) {
  rng = seed;
  for (unsigned i = 0; i < sizeof arena; i++) arena[i] = rnd();
  for (int i = 0; i < 64; i++) pat[i] = rnd() | 1;
  for (int i = 0; i < 9; i++) { unsigned long long v = (unsigned long)(arena + 1024 + (rnd() %% 60000 & ~15u));
    cpy(st + 8 * i, &v, 8); }
  for (int i = 0; i < 128; i++) st[80 + i] = rnd();
  for (int i = 0; i < 16; i++) { unsigned long long v = (unsigned long)(arena + 1024 + (rnd() %% 60000 & ~15u));
    cpy(st + 208 + 8 * i, &v, 8); }
  unsigned long long f = (unsigned long)fn; cpy(st + 336, &f, 8);
}
static int compare(const Chk *d, int nd, const Chk *r, int nr) {
  int bad = 0;
  for (int i = 0; i < nd; i++) {
    const Chk &c = d[i];
    if (c.kind == 0) bad += !eq(dlog0 + 8 * c.n, dlog + 8 * c.n, c.len);
    else if (c.kind == 1) bad += !eq(dlog0 + 80 + 16 * c.n, dlog + 80 + 16 * c.n, c.len);
    else if (c.kind == 2) bad += !eq(dlog0 + 208 + c.n, dlog + 208 + c.n, c.len);
    else if (c.kind == 3) { unsigned a0, a1; cpy(&a0, dlog0 + 8 * c.n, 4); cpy(&a1, dlog + 8 * c.n, 4);
      bad += !eq((void *)(unsigned long)a0, (void *)(unsigned long)a1, c.len); }
  }
  for (int i = 0; i < nr; i++) {
    const Chk &c = r[i];
    if (c.kind == 4) bad += !eq(st0 + 352 + 8 * c.n, st + 352 + 8 * c.n, c.len);
    else bad += !eq(st0 + 368 + 16 * c.n, st + 368 + 16 * c.n, c.len);
  }
  bad += !eq(st0 + 432, st + 432, 8);
  return bad;
}
%(CASES)s
extern "C" int main_() {
  int bad = 0;
  for (int k = 0; k < %(N)d; k++) for (unsigned s = 1; s <= 4; s++) {
    setup(s * 7919u + k, GEN[k]); run_bridge(st); cpy(dlog0, dlog, sizeof dlog); cpy(st0, st, sizeof st);
    cpy(arena0, arena, sizeof arena);
    setup(s * 7919u + k, LEAN[k]); run_bridge(st);
    int b = compare(DC[k], ND[k], RC[k], NR[k]) + !eq(arena0, arena, sizeof arena);
    if (b) { bad++; FAILED[k] = 1; }
  }
  return bad > 255 ? 255 : bad;
}
extern "C" { __attribute__((aligned(16))) char stack[262144]; }
asm(".globl stack_top\n.set stack_top, stack + 262144\n");
'''


def _run(t, src, asm):
    import subprocess
    open(os.path.join(t, 't.cpp'), 'w').write(test_bridges.RUNTIME.replace('bl main_', 'bl main_') + src)
    open(os.path.join(t, 'b.S'), 'w').write('\n'.join(asm))
    cxx = [layout.CXX, '-mabi=ilp32', '-O1', '-ffreestanding', '-fno-exceptions', '-fno-rtti', '-fno-builtin', '-c']
    test_bridges.run(cxx + [os.path.join(t, 't.cpp'), '-o', os.path.join(t, 't.o')])
    test_bridges.run([fnvlink.AS, '-mabi=ilp32', os.path.join(t, 'b.S'), '-o', os.path.join(t, 'b.o')])
    exe32 = os.path.join(t, 't32')
    test_bridges.run([fnvlink.LD, '-m', 'aarch64linux32', '-N', '-static', '--build-id=none', '-Ttext=0x400000', '-e',
                      '_start', os.path.join(t, 't.o'), os.path.join(t, 'b.o'), '-o', exe32])
    flat = os.path.join(t, 'flat.bin')
    test_bridges.run([fnvlink.OBJCOPY, '-O', 'binary', '--remove-section=.comment', exe32, flat])
    entry = int(next(l.split()[0] for l in test_bridges.run(['aarch64-linux-gnu-nm', exe32]).stdout.splitlines()
                     if l.endswith(' _start')), 16)
    open(os.path.join(t, 'w.S'), 'w').write('    .section .img, "awx"\n    .incbin "%s"\n    .space 0x80000\n' % flat)
    test_bridges.run([fnvlink.AS, os.path.join(t, 'w.S'), '-o', os.path.join(t, 'w.o')])
    exe64 = os.path.join(t, 't64')
    test_bridges.run([fnvlink.LD, '-m', 'aarch64linux', '-static', '-z', 'max-page-size=4096',
                      '--section-start=.img=0x400000', '-e', '0x%x' % entry, os.path.join(t, 'w.o'), '-o', exe64])
    r = subprocess.run(['qemu-aarch64', exe64], capture_output=True, text=True, timeout=600)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--link', help='a link out dir: every abi2 bridge of it (with --units)')
    ap.add_argument('--units')
    ap.add_argument('--batch', type=int, default=200)
    a = ap.parse_args()
    cases, skipped, dedup = [], 0, {}
    for k, (label, p) in enumerate(plan_cases(a.link, a.units)):
        gen = abi2.emit('__G__', '__T__', p, '')
        lean = abi2.emit_lean('__L__', '__T__', p, '')
        if gen is None or lean is None:
            skipped += 1
            continue
        if lean in dedup:        # the same lean code: one representative per shape
            continue
        dedup[lean] = label
        cases.append((len(cases), p, gen, lean, label))
    fails = []
    for i in range(0, len(cases), a.batch):
        chunk = cases[i:i + a.batch]
        rc = _run_chunk(chunk)
        if rc:
            # narrow down: each case alone
            for c in chunk:
                if _run_chunk([c]):
                    fails.append(c[4])
    print('plans: %d distinct lean shapes run (x4 seeds), %d without a lean bridge; %d failing' % (
        len(cases), skipped, len(fails)))
    for f in fails[:20]:
        print('  FAIL', f)
    print('ok' if not fails else 'FAILED')
    sys.exit(1 if fails else 0)


def _run_chunk(chunk):
    with tempfile.TemporaryDirectory() as t:
        kinds = {'x': 0, 'v': 1, 'stk': 2, 'mem': 3, 'rx': 4, 'rv': 5}
        decls, gens, leans, dcs, rcs, nds, nrs, asm = [], [], [], [], [], [], [], [RUN]
        for j, (_, p, gen, lean, _) in enumerate(chunk):
            d, r = checks(p)
            decls.append('extern "C" void gen_%d(); extern "C" void lean_%d();' % (j, j))
            decls.append('static const Chk dc_%d[] = {%s{-1,0,0}}; static const Chk rc_%d[] = {%s{-1,0,0}};' % (
                j, ''.join('{%d,%d,%d},' % (kinds[c[0]], c[1], c[2]) for c in d), j,
                ''.join('{%d,%d,%d},' % (kinds[c[0]], c[1], c[2]) for c in r)))
            gens.append('(void *)gen_%d' % j); leans.append('(void *)lean_%d' % j)
            dcs.append('dc_%d' % j); rcs.append('rc_%d' % j); nds.append(str(len(d))); nrs.append(str(len(r)))
            asm.append(asm_for(j, p, gen, lean))
        n = len(chunk)
        tables = ('static void *GEN[] = {%s}; static void *LEAN[] = {%s}; static const Chk *DC[] = {%s};'
                  ' static const Chk *RC[] = {%s}; static int ND[] = {%s}; static int NR[] = {%s};'
                  ' extern "C" { int FAILED[%d]; }' % (','.join(gens), ','.join(leans), ','.join(dcs), ','.join(rcs),
                                                        ','.join(nds), ','.join(nrs), n))
        src = C % {'ARENA': ARENA, 'CASES': '\n'.join(decls) + '\n' + tables, 'N': n}
        return _run(t, src, asm)


if __name__ == '__main__':
    main()
