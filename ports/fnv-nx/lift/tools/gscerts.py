#!/usr/bin/env python3
"""gscerts: the /GS failure-path certificates (format fnvnx-gsfail/1) of the images, by rule from each image's load-config SecurityCookie and the facts' runs.

    gscerts.py --out gscerts.json --facts FACTS --modules MODULES_TSV --exe EXE --moddirs D1:D2

RULE (per image with a load-config directory that carries a SecurityCookie):
  cookie_va  the load-config SecurityCookie (relocated to the image's assigned BASE; BASE '-' = the stored image);
  compare    every decoded instruction `cmp` with a memory operand that is exactly the absolute address cookie_va, whose next instruction is a Jcc rel (jne/jnz or je/jz): cmp_va = the cmp;
  fail_va    the failure edge of that Jcc: the taken target of jne/jnz, the fall-through of je/jz;
  sites      every instruction that only the failure edge dominates: the instructions reachable from fail_va in the control-flow graph of the decoded runs that are NOT reachable from the roots
             when that one edge is cut. Graph: fall-through (not after jmp / ret / int3 / ud2 / hlt), direct jump / branch / call targets, a call falls through, a `jmp [table]` has the
             targets of the jump-table facts. Roots: every universe entry whose kind is not `call` or `tail` (those two arise from direct edges of the graph; every other kind is an entry
             that code can enter without such an edge: entry, export, address-taken, handlers).
An image without a load-config SecurityCookie has no certificate (nothing is guessed from code patterns). Nothing here is a list of addresses."""
import argparse, collections, json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = sys.argv[sys.argv.index('--lift') + 1] if '--lift' in sys.argv else os.path.dirname(HERE)      # lift/ (fnvlift/, proof/): a recipe stage mounts it as a declared input
sys.path[:0] = [HERE, LIFT]
import capstone as CS
from capstone import x86 as X
from fnvlift.image import Image
from proof import census, shape

JCC = {'jne': 'fail-taken', 'jnz': 'fail-taken', 'je': 'fail-fall', 'jz': 'fail-fall'}
STOP = ('ret', 'retn', 'int3', 'ud2', 'hlt')


def cookie(path):
    """-> SecurityCookie VA at the stored image's preferred base, or None (no load-config directory, or one that is too short to hold the field)"""
    d = open(path, 'rb').read(); pe = struct.unpack_from('<I', d, 0x3c)[0]
    rva, sz = struct.unpack_from('<II', d, pe + 24 + 96 + 10 * 8)
    if not rva: return None
    im = Image(path); o = im.off(im.base + rva)
    if o is None or struct.unpack_from('<I', d, o)[0] < 0x40: return None
    return struct.unpack_from('<I', d, o + 0x3c)[0]


def universe(path):
    out = {}
    for l in open(path):
        if l.startswith('#') or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); out[int(f[0], 16)] = f[1]
    return out


def decode(img, ranges):
    base, rngs = census.read_ranges(ranges); md = shape.decoder(); ins = {}
    for lo, hi in rngs:
        for g in md.disasm(img.bytes(lo, hi - lo), lo):
            ins.setdefault(g.address, g)
    return ins


def graph(ins, tables):
    succ = {}
    for a, g in ins.items():
        m = g.mnemonic.split(' ')[-1]; nxt = a + g.size; o = g.operands; s = []
        imm = o[0].imm & 0xffffffff if o and o[0].type == X.X86_OP_IMM else None
        if m == 'jmp':
            if imm is not None: s.append(imm)
            else: s += tables.get(a, [])
        elif m in STOP: pass
        elif m == 'call':
            if imm is not None: s.append(imm)
            s.append(nxt)
        elif (m.startswith('j') or m in ('loop', 'loope', 'loopne')) and imm is not None: s += [imm, nxt]
        else: s.append(nxt)
        succ[a] = [x for x in s if x in ins]
    return succ


def reach(succ, starts, cut=None):
    seen = set(); st = [x for x in starts if x in succ]
    while st:
        a = st.pop()
        if a in seen: continue
        seen.add(a)
        for b in succ[a]:
            if cut and (a, b) == cut: continue
            if b not in seen: st.append(b)
    return seen


def certs_in(name, ins, tables, uni, ck):
    """-> ([cert], number of compares against the cookie) over the decoded instructions INS {va: instruction}"""
    succ = graph(ins, tables); roots = [a for a, k in uni.items() if k not in ('call', 'tail')]
    out = []; ncmp = 0
    for a in sorted(ins):
        g = ins[a]
        if g.mnemonic != 'cmp': continue
        if not any(o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and not o.mem.segment and o.mem.disp & 0xffffffff == ck for o in g.operands): continue
        ncmp += 1
        j = ins.get(a + g.size)
        if j is None or j.mnemonic not in JCC or not j.operands or j.operands[0].type != X.X86_OP_IMM: continue
        fail = (j.operands[0].imm & 0xffffffff) if JCC[j.mnemonic] == 'fail-taken' else j.address + j.size
        if fail not in ins: continue
        cut = (j.address, fail)
        sites = sorted(reach(succ, [fail]) - reach(succ, roots, cut))
        out.append(dict(kind='gs-fail', image=name, cookie_va='%08x' % ck, cmp_va='%08x' % a, fail_va='%08x' % fail, sites=['%08x' % x for x in sites], assumption='ASM-006'))
    return out, ncmp


def certs_of(name, path, base, uni, rng, jtab):
    img = Image(path, base); pref = Image(path)
    ck = cookie(path)
    if ck is None: return [], dict(image=name, cookie=None)
    ck = (ck + (img.base - pref.base)) & 0xffffffff
    ins = decode(img, rng); tables = {}
    if jtab:
        for t in json.load(open(jtab)).get('jtab_tables', []): tables[int(t['site'], 16)] = [int(x, 16) for x in t['targets']]
    out, ncmp = certs_in(name, ins, tables, uni, ck)
    return out, dict(image=name, cookie='%08x' % ck, compares=ncmp, certs=len(out), sites=sum(len(c['sites']) for c in out))


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--out', required=True); A('--facts', required=True, help='the FACTS tree (lift/facts): exe files at the top, one directory per module row'); A('--modules', required=True, help="the loader's modules.tsv")
    A('--lift'); A('--exe', required=True); A('--moddirs', required=True, help='DIR[:DIR...] that hold the module files')
    a = ap.parse_args(); import pemod
    imgs = [('exe', a.exe, None, a.facts)]
    for n, row in sorted(pemod.modules(a.modules).items()):      # every row of the loader's table, by file name; the exe is the image of its own
        path, _ = pemod.locate(n, a.modules, a.moddirs.split(':'))
        imgs.append((os.path.splitext(n)[0], path, row.base, os.path.join(a.facts, os.path.splitext(n)[0])))
    certs, stats = [], []
    for name, path, base, d in imgs:
        jt = os.path.join(d, 'ehtables.json')
        c, s = certs_of(name, path, base, universe(os.path.join(d, 'universe.tsv')), os.path.join(d, 'ranges.tsv'), jt if os.path.isfile(jt) else None)
        certs += c; stats.append(s); print(s)
    with open(a.out, 'w') as f: json.dump({'format': 'fnvnx-gsfail/1', 'certs': certs}, f, indent=1, sort_keys=True); f.write('\n')


if __name__ == '__main__': main()
