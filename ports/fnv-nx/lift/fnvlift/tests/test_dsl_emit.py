#!/usr/bin/env python3
"""f_dsl.emit_form (the C emitted from the proven DSL forms) over every instruction shape of FalloutNV.exe whose mnemonic the forms cover: each shape is
decoded with zeroed immediate / displacement bytes (plus, for branches, a second copy with a non-zero relative target that lies outside the function),
emitted as a one-instruction function and compiled (-fsyntax-only, the flags of test/instest.py (liftgen.resolved_flags)).  Prints `shapes N emitted N compiled N`, and
`undef-assigned P`: the number of (mnemonic, flag) pairs where a form declares the flag undefined (no assignment is emitted) and the hand handler
assigns it.  Input: the shape census table shapes.tsv of the exe (exe-derived, first column = shape key; '#' lines ignored: /home/user/l3work/publish-shapes/FalloutNV/shapes.tsv, made by proof/census.py).
    dsl_emit_test.py [--shapes P] [--jobs 2] [--work DIR] [--pairs]"""
import argparse, multiprocessing as mp, os, re, subprocess, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(os.path.dirname(HERE)); sys.path.insert(0, LIFT); sys.path.insert(0, os.path.join(LIFT, 'test'))   # instest: the shared harness of lift/test
import capstone
from capstone import x86 as X
import fnvlift
from fnvlift import core, f_dsl
import instest

BASE, STEP = 0x400000, 0x100


def shapes(pk):
    cnt = {l.split()[0] for l in open(pk) if l.strip() and not l.startswith('#')}
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    for s in sorted(cnt):
        raw = bytes.fromhex(s.split('/')[0]); i = 0
        yield s, raw, md


def mkctx(ins, a):
    cx = core.Ctx(a, ins.size, {a: ins}, {}, [])
    m, pfx = ins.mnemonic, ''
    if m.startswith(('rep ', 'repe ', 'repne ')): pfx, m = m.split(' ', 1)
    if m.startswith('lock '): m = m.split(' ', 1)[1]
    cx.start, cx.a, cx.nxt, cx.m, cx.pfx, cx.ops, cx.term, cx.xm, cx.xdp = a, a, a + ins.size, m, pfx, ins.operands, False, None, 0
    cx.uent = {ins.operands[0].imm & 0xffffffff} if ins.operands and ins.operands[0].type == X.X86_OP_IMM else set()      # a branch target is an entry of the universe (jump_out), else it goes to the dispatcher
    cx.b0, cx.modrm, cx.s = ins.opcode[0], ins.modrm, ins.operands[0].size * 8 if ins.operands else 32
    return cx


def func(cx, a):
    decl = ''.join('extern "C" void L_%08x(void);\n' % t for t in sorted(cx.calls | cx.tails))      # the callees, declared as fnvlift/core.py does for a lifted function
    return '%sextern "C" void L_%08x(void)\n{ DECL; SYNC_IN; u32 fsb=c->fs; u32 E=esp;\nL_%08x: ;\n%s\nafter_tail: ; nonlocal: ; return;\n}\n' % (decl, a, a, '\n'.join(cx.L))


def collect(pk):
    out, bad, undef = [], [], collections.defaultdict(set)
    n = 0
    for s, raw, md in shapes(pk):
        ins = next(md.disasm(raw, BASE), None)
        if ins is None or ins.size != len(raw) or not f_dsl.supported(ins): continue
        vars_ = [(ins, raw)]
        if ins.mnemonic.split()[-1][0] in 'jlc' and ins.operands and ins.operands[0].type == X.X86_OP_IMM and ins.mnemonic.split()[-1] != 'cmp':
            q = bytearray(raw); q[-1] = 0x40                      # rel8 / the low byte of rel32: a target at +0x40 (outside the one-instruction function)
            j = next(md.disasm(bytes(q), BASE), None)
            if j is not None and j.size == len(raw) and j.operands[0].imm != ins.operands[0].imm: vars_.append((j, bytes(q)))
        for vi, (ins, raw) in enumerate(vars_):
            a = BASE + STEP * n; n += 1
            ins = next(md.disasm(raw, a), None); cx = mkctx(ins, a)
            if vi: cx.uent = set()                                # the outside-target copy: not an entry, so the transfer goes to the dispatcher
            try:
                if f_dsl.emit_form(cx, ins) is core.SKIP: raise core.Unsupported('SKIP')
            except (core.Unsupported, AssertionError) as e:       # the emitter's stated reason, or the builder's precondition (shapeprove.py class `no-form`); any other build error propagates
                bad.append((s, repr(e)[:100])); continue
            out.append((a, s + ('+' if vi else ''), func(cx, a)))
            F = f_dsl.forms.build(ins, a)
            hx = mkctx(ins, a)
            try: core.dispatch(hx, ins) if core.EMIT == '' else None
            except core.Unsupported: hx.L = []                    # no hand handler for this instruction: nothing assigned
            t = '\n'.join(hx.L)
            for f in F.undef:
                if re.search(r'(?<![\w])%s\s*=(?!=)' % f, t): undef[ins.mnemonic.split()[-1]].add(f)
    return out, bad, undef


def compile_tu(arg):
    k, prel, fs, work = arg
    p = '%s/tu%d.cpp' % (work, k); open(p, 'w').write(prel + '\n' + ''.join(f[2] for f in fs))
    r = subprocess.run(['aarch64-linux-gnu-g++'] + [x for x in instest.FLAGS if x != '-c'] + ['-fsyntax-only', p], capture_output=True, text=True)
    if r.returncode == 0: return len(fs), []
    names = set(re.findall(r"In function '[^']*L_([0-9a-f]{8})", r.stderr)) or {'%08x' % f[0] for f in fs}
    return len(fs) - len(names), [(n, [l for l in r.stderr.splitlines() if 'error' in l][:1]) for n in sorted(names)]


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--shapes', default='/home/user/l3work/publish-shapes/FalloutNV/shapes.tsv'); A('--jobs', type=int, default=2); A('--work', default='/home/user/l3work/dsl_emit_test'); A('--pairs', action='store_true')
    a = ap.parse_args(); os.makedirs(a.work, exist_ok=True)
    out, bad, undef = collect(a.shapes)
    chunks = [out[i:i + 300] for i in range(0, len(out), 300)]
    with mp.Pool(a.jobs) as p: res = p.map(compile_tu, [(k, fnvlift.prelude(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'rt')), c, a.work) for k, c in enumerate(chunks)])
    ok = sum(r[0] for r in res); fails = [f for r in res for f in r[1]]
    nshapes = len(out) + len(bad)
    print('(%d of them the second, outside-target copy of a branch)' % sum(1 for f in out if f[1].endswith('+')))
    for s, e in bad[:10]: print('emit error', s, e)
    for n, e in fails[:10]: print('compile error', n, e)
    print('shapes %d emitted %d compiled %d' % (nshapes, len(out), ok))
    pairs = sorted((m, f) for m, fs in undef.items() for f in fs)
    print('undef-assigned %d' % len(pairs))
    if a.pairs: print(' '.join('%s:%s' % p for p in pairs))
    sys.exit(0 if nshapes == len(out) == ok else 1)


if __name__ == '__main__': main()
