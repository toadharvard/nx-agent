#!/usr/bin/env python3
"""callcensus - every call site of chosen functions over the image set, with reach from the exe entry and the value set of one argument (L1 CONTRACT).

    callcensus.py --exe-facts facts.json --exe-entry 0xVA --images-file images.tsv --bridge bridge.tsv --modules-dir M
                  --fn dll!name:argc:srcarg ... [--gpa gpa_names.tsv] --out DIR

For each --fn (an export of an image of the set, for example d3dx9_38.dll!D3DXAssembleShader:7:0): the uses of its slots in every image
(argflow.uses_of_slot_ref: call [slot], thunk, register-loaded call; an import by name or by the ordinal the module's export table gives
it), the direct calls to its entry inside the providing image (argflow Image.callers, entry = preferred base + export rva), and the
GetProcAddress rows of --gpa that name it.  Per site: image, va, the function containing it, REACH and the SOURCE argument.
reach  (the exe's own sites) `yes` iff the containing function is in the forward closure from the exe entry over the exe facts: direct
       calls, tail jumps, relocated code pointers of the function, the slots of the RTTI vftables it references, and the pointer
       tables it references (every code pointer of the same contiguous run of the pointer table).  This over-approximates the call
       graph (an indirect call can only reach a function whose address is taken somewhere in the facts), so `no` is a proof of
       unreachability and `yes` means "not excluded".  A site in another image: `inner` (reach decided by the calls into that image).
source the derived value set of argument srcarg (argflow.derive_arg): `const-string "text"`, `imm 0x..`, `pointer-from-flow` (a call result
       or a value of the caller's parameters: the leaf kinds are listed), `open` (a break: the reason).
Output callcensus.tsv: image, va, call, function, reach, kind, source, note.  Exe-derived: container and fnv-nx-build only."""
import argparse
import bisect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argflow
import gpa_names
import iat
import modules


def closure(facts, entry, img=None):
    """The functions of the exe facts reachable from `entry` (see the module docstring); {function entry}.  With IMG (the exe's
    argflow.Image) every direct call and jump found by decoding the ranges of the facts is an edge too (a chunk reached by a jmp into
    the body of another function is part of its entry's ranges), so the closure is over the entries of the ranges."""
    fn = {int(k): v for k, v in facts['fn'].items()}
    vft = {int(k): v[2] for k, v in facts['vft'].items()}
    runs, ordered = {}, sorted((int(a), int(t)) for a, t in facts['ptrs'])
    start = None
    for i, (a, t) in enumerate(ordered):
        if start is None or a != ordered[i - 1][0] + 4:
            start = a
        runs.setdefault(start, []).append(t)
    run_of = {}
    for s, ts in sorted(runs.items()):
        for k in range(len(ts)):
            run_of[s + 4 * k] = s
    tgt = {int(a): int(t) for a, t in facts['ptrs']}
    starts = sorted(fn)
    entry = starts[bisect.bisect_right(starts, entry) - 1]          # the function containing the exe's entry point
    out = {}
    if img is not None:
        img.callers(0)
        for target, sites in sorted(img._callers.items()):
            for site, how in sorted(sites):
                for e in img.entries_containing(site):
                    out.setdefault(e, set()).add(target)
    seen, work = set(), [entry]
    while work:
        f = work.pop()
        if f in seen or (f not in fn and f not in out):
            continue
        seen.add(f)
        v = fn.get(f) or dict(calls=[], tails=[], codeptrs=[], jt=[], vrefs=[], data=[])
        nxt = sorted(out.get(f, ())) + [c[1] for c in v['calls']] + [t[1] for t in v['tails']] + [c[1] for c in v['codeptrs']]
        for site, table, count in v['jt']:
            nxt += [tgt.get(table + 4 * k) for k in range(count) if tgt.get(table + 4 * k) is not None]
        for site, kind, vt in v['vrefs']:
            nxt += vft.get(vt, [])
        for site, addr in v['data']:
            if addr in run_of:
                nxt += runs[run_of[addr]]
        work += [x for x in sorted(set(nxt)) if x not in seen]
    return seen


def function_of(fn_starts, va):
    """The function entry containing va (largest entry <= va), by bisect over the sorted entries."""
    i = bisect.bisect_right(fn_starts, va) - 1
    return fn_starts[i] if i >= 0 else None


def describe(res):
    """derive_arg result -> (kind, text)."""
    leaves = res.sorted_leaves()
    brk = sorted('%x %s' % b if isinstance(b, tuple) else str(b) for b in res.breaks)
    parts, kinds = [], []
    for kind, val, _ in leaves:
        if kind == 'str':
            parts.append('const-string %r' % (val[0] if val[0] is not None else val[1]))
            kinds.append('const-string')
        elif kind == 'imm':
            parts.append('imm 0x%x' % val)
            kinds.append('imm')
        else:
            parts.append('%s %r' % (kind, val))
            kinds.append('pointer-from-flow')
    if brk:
        return 'open', '; '.join(parts + ['open: ' + b for b in brk])
    return ','.join(sorted(dict.fromkeys(kinds))) or 'open', '; '.join(parts) or 'open: no value'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe-facts', required=True)
    ap.add_argument('--exe-entry', required=True)
    ap.add_argument('--images-file', required=True)
    ap.add_argument('--bridge', required=True)
    ap.add_argument('--module', action='append', default=[], help='name.dll=path of the module file (export table)')
    ap.add_argument('--fn', action='append', required=True, help='dll!name:argc:srcarg')
    ap.add_argument('--gpa')
    ap.add_argument('--escape', action='append', default=[])
    ap.add_argument('--writes')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    with open(a.exe_facts) as fh:
        facts = json.load(fh)['data']
    imgs = gpa_names.load_images(gpa_names.specs_of(a.images_file), a.bridge, a.escape, a.writes)
    starts = sorted(int(k) for k in facts['fn'])
    reached = closure(facts, int(a.exe_entry, 16), imgs[[n for n in sorted(imgs) if n.endswith('.exe')][0]])
    files = dict(m.split('=', 1) for m in a.module)
    me = [n for n in sorted(imgs) if n.endswith('.exe')][0]
    rows = []
    for spec in a.fn:
        target, argc, src = spec.rsplit(':', 2)
        dll, name = target.split('!')
        dll = iat.norm_dll(dll)
        ordinal, entry_rva = '', None
        if dll in files:
            with open(files[dll], 'rb') as fh:
                pe = modules.PE(fh.read(), dll)
            for key, rva, fwd, ordv in pe.exports():
                if key == name:
                    ordinal, entry_rva = str(ordv), rva
        for nm in sorted(imgs):
            img = imgs[nm]
            slots = sorted(s for s, (d, n) in img.imports.items() if iat.norm_dll(d) == dll and (n == name or (ordinal and n == '#' + ordinal)))
            sites = []
            for ref in sorted(r for r in img.relocs if img.word(r) in slots):
                calls, o = argflow.uses_of_slot_ref(img, ref)
                sites += [(c, 'import') for c in sorted(set(calls))] + [(x[0], 'open: ' + x[1]) for x in o]
            if nm == dll and entry_rva is not None:
                sites += [(s, 'direct-' + h) for s, h in img.callers(img.base + entry_rva)]
            for va, how in sorted(sites):
                if how.startswith('open'):
                    rows.append((nm, va, name, '-', 'unknown', 'open', how, ''))
                    continue
                fe = function_of(starts, va) if nm == me else (sorted(img.entries_containing(va)) or [0])[0]
                reach = ('yes' if fe in reached or any(e in reached for e in img.entries_containing(va)) else 'no') if nm == me else 'inner'
                kind, text = describe(argflow.derive_arg(img, va, int(src)))
                rows.append((nm, va, name, '%x' % (fe or 0), reach, kind, text, how))
    if a.gpa:
        with open(a.gpa) as fh:
            for line in fh.read().splitlines()[1:]:
                f = line.split('\t')
                for spec in a.fn:
                    dll, name = spec.rsplit(':', 2)[0].split('!')
                    if len(f) > 8 and any(p.split('!')[-1] == name and iat.norm_dll(p.split('!')[0]) in (iat.norm_dll(dll), '*') for p in f[8].split(';') if p):
                        rows.append((f[0], int(f[1], 16), name, '-', 'unknown', 'open', 'GetProcAddress pair', f[8]))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'callcensus.tsv'), 'w', newline='\n') as fh:
        fh.write('image\tva\tcall\tfunction\treach\tkind\tsource\tnote\n')
        for r in sorted(rows, key=lambda r: (r[0], r[1], r[2])):
            fh.write('%s\t0x%x\t%s\t%s\t%s\t%s\t%s\t%s\n' % (r[0], r[1], r[2], r[3], r[4], r[5], r[6] or '-', r[7] or '-'))
    print('callcensus: %d sites (%d reached from the exe entry, %d not)' % (len(rows), sum(1 for r in rows if r[4] == 'yes'), sum(1 for r in rows if r[4] == 'no')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
