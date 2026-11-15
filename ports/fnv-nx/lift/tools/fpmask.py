#!/usr/bin/env python3
"""Facts stage: the FP-exception-mask fact of CLAUDE.md "Hardware exceptions in lifted code" (owner decision 2026-11-14): every site that loads the x87 control word or MXCSR
(fldcw, fninit/finit, fldenv, frstor, ldmxcsr, fxrstor, and the CRT imports _controlfp/_control87/_fpreset) keeps all six exception-mask bits set.
    fpmask.py --images IMAGES.tsv --out fpmask.tsv [--jobs N]
IMAGES.tsv rows (tab separated, '#' comments): NAME  PE-FILE  FACTS-DIR   (FACTS-DIR holds universe.tsv and ranges.tsv; a module's PE-FILE is rebased to the `# base=` of its universe.tsv).
A pure function of the image bytes and the facts; rows are sorted, so the text is the same for every --jobs.

RULE (abstract value over the mask bits).  M = 0x3F for FCW (bits 0-5, IM DM ZM OM UM PM), M = 0x1F80 for MXCSR (bits 7-12).  P(w) = (w & M) == M.
A site is PROVED when P holds for every value its operand can take, and each value is one of
  const        a constant of the image (fpmodes.val: an immediate store, traced through the frame / registers; fninit: 0x037F, Intel SDM vol.1 8.1.5);  P read off the constant.
  saved(A,O)   (cur & A) | O, where cur was stored earlier by fnstcw / stmxcsr (the word in force when it was saved) and A, O are the and/or masks applied to it in between.
               P(cur) is the induction hypothesis; the result keeps P iff (A | O) & M == M  (per mask bit: A=1 passes cur's 1, or O=1 sets it).
  controlfp    a CRT import call _controlfp/_control87(new, mask) with constant arguments: the same (A,O) form (fpstate.crt_to_cw / crt_to_mx).
Induction base: every guest thread starts with FCW 0x027F and MXCSR 0x1F80 (Windows: the documented initial control state; Wine 9.0 dlls/ntdll/unix/signal_i386.c init_thread_context sets
FloatSave.ControlWord 0x27F and MxCsr 0x1F80; both have P); rt/enter.cc loads exactly these.  The induction holds program-wide iff NO site of any image is undecided: a `saved` verdict is
`proved` only under that hypothesis, so the summary line says whether the program-level statement holds.
Sites the rule cannot decide get `undecided:<reason>` (reasons are the value-walk's: ptr alias arg call global uninit espebp env-load budget, and entry-undecodable for sites in an entry whose decode failed). Never guessed.
SITE UNIVERSE: every instruction of the listed mnemonics in the instruction ranges of ranges.tsv (a sweep), plus the CRT import calls found in decoded entries; a site no decoded entry analysed is undecided:entry-undecodable."""
import os
import sys
import struct
import tempfile
from collections import Counter, defaultdict
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import cli
import x86flow as W
import pemod
import ctlloads
import capstone

USAGE = 'usage: fpmask.py --images IMAGES.tsv --out fpmask.tsv [--jobs N]   the FCW/MXCSR exception-mask proof of every control-word load site (tools/fpmask.py docstring)'
MFCW, MMX = 0x3F, 0x1F80
SWEEP = {'fldcw': 'fcw', 'fninit': 'fcw', 'finit': 'fcw', 'fldenv': 'fcw', 'frstor': 'fcw', 'ldmxcsr': 'mx', 'fxrstor': 'both'}
HEADER = '# fpmask/1 image\taddr\tinstruction\tverdict\tproof\tsource'
SRC_INIT = 'MS: initial FCW 0x027F / MXCSR 0x1F80; Wine 9.0 ntdll/unix/signal_i386.c init_thread_context'


def read_images(path):
    out = []
    for ln in open(path).read().splitlines():
        if ln and ln[0] != '#':
            f = ln.split('\t')
            out.append((f[0], f[1], f[2]))
    return out


def rebased(path, facts):
    base = None
    for ln in open(os.path.join(facts, 'universe.tsv')):
        if ln.startswith('# base='):
            base = int(ln.split('base=')[1].split(':')[0], 16)
        break
    if base is None:
        return path, None
    M = pemod.load(path, base)
    d = bytearray(M.d)
    struct.pack_into('<I', d, M.pe + 52, base)
    fd, tmp = tempfile.mkstemp(suffix='.pe')
    os.write(fd, bytes(d)); os.close(fd)
    return tmp, tmp


def sweep(img, facts):
    """-> {addr: mnemonic} of the control-word load instructions in the ranges of ranges.tsv"""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    seen, out = set(), {}
    for ln in open(os.path.join(facts, 'ranges.tsv')):
        if ln[0] == '#' or not ln.strip():
            continue
        for r in ln.split('\t')[1].replace(',', ' ').split():
            lo, hi = [int(x, 16) for x in r.split('-')]
            if (lo, hi) in seen:
                continue
            seen.add((lo, hi))
            code = img.bytes(lo, hi - lo)
            for i in md.disasm(code, lo):
                m = i.mnemonic.split()[-1]
                if m in SWEEP:
                    out[i.address] = m
    return out


def judge(eff, mask):
    """eff: list of ('c', v) | ('t', A, O) | ('u', reason) -> (verdict, proof, detail)"""
    und = sorted({e[1] for e in eff if e[0] == 'u'})
    if und:
        return 'undecided:' + ','.join(und), '-', '-'
    bad, kinds, det = [], set(), []
    for e in eff:
        if e[0] == 'c':
            kinds.add('const'); det.append('c=%x' % e[1])
            if e[1] & mask != mask:
                bad.append(e)
        else:
            kinds.add('saved'); det.append('and=%x,or=%x' % (e[1] & 0xffffffff, e[2] & 0xffffffff))
            if (e[1] | e[2]) & mask != mask:
                bad.append(e)
    proof = '+'.join(sorted(kinds)) + ' ' + ' '.join(det)
    if bad:
        return 'CLEARS-MASK', proof, 'a value with a mask bit clear: ' + str(bad)
    return 'proved', proof, ''


def scan_entry(args):
    a, ext, kind = args
    F = W.Fn(a, ext, kind)
    fc, mx = [], []
    if F.ok:
        for x in sorted(F.ins):
            f_, m_ = ctlloads.control_loads(F, x)
            fc += f_; mx += m_
    return fc, mx


def analyse_image(name, pe, facts, jobs):
    path, tmp = rebased(pe, facts)
    for d in (W.KINDS, W.FLAGS, W.RETN, W.THUNK, W.IATADDR):
        d.clear()
    del W.ALLOCA[:]
    W.configure(facts)
    raw, rows = W.load_universe(path)
    with Pool(jobs) as p:
        ret, thunk, alloca = W.compute(p, rows)
    W.RETN.update(ret); W.THUNK.update(thunk); W.ALLOCA.extend(alloca); W.parse_imports()
    swept = sweep(W.IMG, facts)
    got = {}                                    # (addr, mnemonic, tag) -> effects
    with Pool(jobs) as p:
        for fc, mx in p.imap(scan_entry, [(a, e, k) for a, e, k in rows], chunksize=100):
            for tag, lst in (('fcw', fc), ('mx', mx)):
                for x, m, eff in lst:
                    got.setdefault((x, m, tag), eff)
    rowsout = []
    done = set()
    for (x, m, tag), eff in sorted(got.items()):
        mask = MMX if tag == 'mx' else MFCW
        v, proof, det = judge(eff, mask)
        ins = m + (' [mxcsr]' if tag == 'mx' and m == 'fxrstor' else ' [fcw]' if m == 'fxrstor' else '')
        rowsout.append((name, x, ins, v, proof, SRC_INIT if 'saved' in proof else 'image bytes (x86flow value trace)' if proof != '-' else det or '-'))
        done.add(x)
    for x, m in sorted(swept.items()):
        if x not in done:
            for tag in (('fcw', 'mx') if m == 'fxrstor' else (SWEEP[m],)):
                rowsout.append((name, x, m + (' [%s]' % tag if m == 'fxrstor' else ''), 'undecided:entry-undecodable', '-', 'no decoded entry analysed this site'))
    if tmp:
        os.unlink(tmp)
    return rowsout


def main():
    _, o = cli.parse(USAGE, sys.argv[1:], (0, 0), {'--images': True, '--out': True, '--jobs': True})
    if '--images' not in o or '--out' not in o:
        sys.exit(USAGE)
    jobs = int(o.get('--jobs', os.cpu_count() or 1))
    allrows = []
    for name, pe, facts in read_images(o['--images']):
        allrows += analyse_image(name, pe, facts, jobs)
    allrows.sort(key=lambda r: (r[0], r[1], r[2]))
    os.makedirs(os.path.dirname(os.path.abspath(o['--out'])), exist_ok=True)
    tot = Counter(); per = defaultdict(Counter)
    for r in allrows:
        k = 'proved' if r[3] == 'proved' else r[3]
        tot[k] += 1; per[r[0]][k] += 1
    undecided = sum(v for k, v in tot.items() if k != 'proved')
    with open(o['--out'], 'w') as f:
        f.write(HEADER + '\n')
        f.write('# program statement (induction over all sites): %s; sites %d, proved %d, other %d\n' % ('HOLDS' if not undecided else 'UNPROVEN', len(allrows), tot['proved'], undecided))
        for r in allrows:
            f.write('%s\t%08x\t%s\t%s\t%s\t%s\n' % r)
    print('fpmask: sites %d proved %d; ' % (len(allrows), tot['proved']) + ' '.join('%s=%d' % kv for kv in sorted(tot.items()) if kv[0] != 'proved'))
    for n in sorted(per):
        print('  %s: ' % n + ' '.join('%s=%d' % kv for kv in sorted(per[n].items())))


if __name__ == '__main__':
    main()
