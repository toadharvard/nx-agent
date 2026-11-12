#!/usr/bin/env python3
"""The `generated` stage of the product root lift/test/dslemit (build.json): the C++ that f_dsl.emit_form emits for every instruction shape of the
declared shapes external, as fixed-name bucket units.
    gen_dslemit.py --shapes DIR --lifter DIR --emit {,dsl} --out DIR
DIR/<image>/shapes.tsv   the shape census of every image (proof/census.py; column 1 = the shape key `hexbytes/...`); the union of the keys is the universe.
--lifter DIR             the directory holding the packages fnvlift/ and proof/ and rt/fnvl.h, rt/fnvl_x87.h (declared inputs of the stage, tracked files)
--emit                   the lifter's emission mode (fnvlift.core.EMIT): '' = the hand handlers are the baseline for undef.tsv, 'dsl' = no baseline
Outputs under --out (the declared list, fixed, outputs()): u/000.cpp .. u/fff.cpp, inc/fnvl_prelude.h, emitted.tsv, bad.tsv, undef.tsv.
RULE units: a shape key lives in bucket sha256(key)[0:8 hex digits] mod 4096 (4096 fixed names, an empty bucket is the include alone). Inside a bucket the shapes are
sorted by key and the n-th function sits at the fake address 0x400000 + 0x100*n, so a changed or added shape changes exactly its bucket's unit and no other.
RULE every shape is in exactly one of emitted.tsv / bad.tsv: bad.tsv gives the class `uncovered` (no DSL form covers the mnemonic, or the bytes do not decode to
one instruction of their own length) or `emit` (the emitter's stated reason, or the form builder's precondition). Nothing is dropped.
The instruction is decoded with zeroed immediates / displacement as the census wrote it; a branch gets a second copy with a target at +0x40 outside the one-instruction function.
Deterministic: sorted iteration, no clocks, no environment."""
import argparse
import collections
import hashlib
import os
import re
import sys

NB = 4096     # the generator's own unit split: one unit per 1/4096 of the shapes (by sha256 of the key); about 6 shapes per unit
BASE, STEP = 0x400000, 0x100


def bucket(key):
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % NB


def unit_names():
    return ['u/%03x.cpp' % i for i in range(NB)]


def outputs():
    return unit_names() + ['inc/fnvl_prelude.h', 'emitted.tsv', 'bad.tsv', 'undef.tsv']


def load_shapes(root):
    keys = set()
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d, 'shapes.tsv')
        if os.path.isfile(p):
            with open(p) as f:
                keys.update(l.split('\t')[0] for l in f if l.strip() and not l.startswith('#'))
    return sorted(keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shapes', required=True)
    ap.add_argument('--lifter', required=True)
    ap.add_argument('--emit', choices=['', 'dsl'], required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.lifter)
    # the lifter of L3a 8ec81459 reads the emit mode from FNVL_EMIT when core is imported: it is set here, from the argument, before the import
    os.environ['FNVL_EMIT'] = a.emit
    import capstone
    from capstone import x86 as X
    import fnvlift
    from fnvlift import core, f_dsl
    assert core.EMIT == a.emit

    def mkctx(ins, ad):
        cx = core.Ctx(ad, ins.size, {ad: ins}, {}, [])
        m, pfx = ins.mnemonic, ''
        if m.startswith(('rep ', 'repe ', 'repne ')):
            pfx, m = m.split(' ', 1)
        if m.startswith('lock '):
            m = m.split(' ', 1)[1]
        cx.start, cx.a, cx.nxt, cx.m, cx.pfx, cx.ops, cx.term, cx.xm, cx.xdp = ad, ad, ad + ins.size, m, pfx, ins.operands, False, None, 0
        cx.uent = {ins.operands[0].imm & 0xffffffff} if ins.operands and ins.operands[0].type == X.X86_OP_IMM else set()
        cx.b0, cx.modrm, cx.s = ins.opcode[0], ins.modrm, ins.operands[0].size * 8 if ins.operands else 32
        return cx

    def func(cx, ad):
        decl = ''.join('extern "C" void L_%08x(void);\n' % t for t in sorted(cx.calls | cx.tails))      # the callees, declared as fnvlift/core.py does for a lifted function
        return '%sextern "C" void L_%08x(void)\n{ DECL; SYNC_IN; u32 fsb=c->fs; u32 E=esp;\nL_%08x: ;\n%s\nafter_tail: ; nonlocal: ; return;\n}\n' % (decl, ad, ad, '\n'.join(cx.L))

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    keys = load_shapes(a.shapes)
    per = collections.defaultdict(list)
    for k in keys:
        per[bucket(k)].append(k)
    units = {i: [] for i in range(NB)}
    emitted, bad, undef = [], [], collections.defaultdict(set)
    for b in range(NB):
        n = 0
        for s in per.get(b, ()):
            raw = bytes.fromhex(s.split('/')[0])
            ins = next(md.disasm(raw, BASE), None)
            if ins is None or ins.size != len(raw) or not f_dsl.supported(ins):
                bad.append((s, 'uncovered', 'no DSL form covers it or the bytes are not one instruction' if ins is None or ins.size != len(raw) else 'no DSL form for ' + ins.mnemonic.split()[-1]))
                continue
            vars_ = [raw]
            if ins.mnemonic.split()[-1][0] in 'jlc' and ins.operands and ins.operands[0].type == X.X86_OP_IMM and ins.mnemonic.split()[-1] != 'cmp':
                q = bytearray(raw)
                q[-1] = 0x40       # rel8 / the low byte of rel32: a target at +0x40, outside the one-instruction function
                j = next(md.disasm(bytes(q), BASE), None)
                if j is not None and j.size == len(raw) and j.operands[0].imm != ins.operands[0].imm:
                    vars_.append(bytes(q))
            texts, err = [], None
            for vi, r in enumerate(vars_):
                ad = BASE + STEP * (n + vi)
                i2 = next(md.disasm(r, ad), None)
                cx = mkctx(i2, ad)
                if vi:
                    cx.uent = set()   # the outside-target copy is not an entry, so the transfer goes to the dispatcher
                try:
                    if f_dsl.emit_form(cx, i2) is core.SKIP:
                        raise core.Unsupported('SKIP')
                except (core.Unsupported, AssertionError) as e:     # the emitter's stated reason, or the builder's precondition (shapeprove.py class `no-form`)
                    err = repr(e)[:100]
                    break
                F = f_dsl.forms.build(i2, ad)
                hx = mkctx(i2, ad)
                try:
                    if core.EMIT == '':
                        core.dispatch(hx, i2)
                except core.Unsupported:
                    hx.L = []
                t = '\n'.join(hx.L)
                for f in F.undef:
                    if re.search(r'(?<![\w])%s\s*=(?!=)' % f, t):
                        undef[i2.mnemonic.split()[-1]].add(f)
                texts.append((ad, vi, func(cx, ad)))
            if err is not None:
                bad.append((s, 'emit', err))
                continue
            for ad, vi, t in texts:
                units[b].append(t)
                emitted.append((s, vi, '%02x' % b, '%08x' % ad))
            n += len(vars_)
    out = a.out
    os.makedirs(out + '/u')
    os.makedirs(out + '/inc')
    with open(out + '/inc/fnvl_prelude.h', 'w') as f:
        f.write(fnvlift.PRELUDE)
    for i in range(NB):
        with open(out + '/u/%02x.cpp' % i, 'w') as f:
            f.write('#include "fnvl_prelude.h"\n' + ''.join(units[i]))
    with open(out + '/emitted.tsv', 'w') as f:
        f.write('# shape\tvariant\tbucket\taddress\n' + ''.join('%s\t%d\t%s\t%s\n' % r for r in sorted(emitted)))
    with open(out + '/bad.tsv', 'w') as f:
        f.write('# shape\tclass\treason\n' + ''.join('%s\t%s\t%s\n' % r for r in sorted(bad)))
    with open(out + '/undef.tsv', 'w') as f:
        f.write('# mnemonic\tflag (the form declares it undefined, the hand handler assigns it)\n' + ''.join('%s\t%s\n' % (m, fl) for m in sorted(undef) for fl in sorted(undef[m])))
    print('shapes %d emitted %d (functions %d) bad %d' % (len(keys), len({e[0] for e in emitted}), len(emitted), len(bad)))


if __name__ == '__main__':
    main()
