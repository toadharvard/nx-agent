#!/usr/bin/env python3
"""c2_bridges.py - V1-link claim C2: the ABI bridges a link run generated deliver what x86 would.

    c2_bridges.py <link out dir> <units.json> <work dir> [per class (default 6)] [seeds (default 3)]

Picks bridged (reference, definition) pairs from <out>/abi.tsv stratified by bridge class (seeded), takes each one's
bridge exactly as the run generated it (<out>/bridges.S, by the symbol refs.tsv binds the reference to), and runs it
under qemu ILP32 with bridge_harness: a caller compiled from the reference declaration, the bridge, a callee compiled
from the definition declaration; every parameter the callee reads and the result the caller reads are compared with
the MSVC x86 placement of bridge_harness's x86 model (oracle-checked by selftest_harness.py), several random seeds.
Aggregate layouts come from the run's probe (layouts.tsv, GCC-measured); where MSVC's choice between a register
result and a hidden buffer depends on C++03 POD-ness (free functions returning 1/2/4/8-byte aggregates) the case is
run both ways and reported 'pod-dependent' when the outcomes differ.
"""
import collections
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import v1pairs               # noqa: E402

CLASS_ORDER = ['edx-dummy', 'ref-passes-fewer', 'narrow-extend', 'sret-explicit', 'reg-shift', 'int-float-param',
               'layout', 'result-moves', 'ret-regfile', 'thiscall-member->free-cdecl', 'static-member-cdecl->free-cdecl',
               'arity', 'free-fastcall->free-cdecl', 'fastcall->thiscall', 'thiscall-member->free-stdcall',
               'thiscall-member->free-fastcall']


def load_bridges(path):
    out, cur, name = {}, [], None
    for line in open(path):
        m = re.match(r'^(__fnvnx_br_[0-9a-f]{8}_[0-9a-f]{8}):', line)
        if m:
            name, cur = m.group(1), [line]
            continue
        if name:
            if line.strip().startswith('.size'):
                out[name] = '    .globl %s\n' % name + ''.join(cur)
                name = None
            else:
                cur.append(line)
    return out


def load_bridge_obj(path, work):
    """{bridge symbol: {'obj', 'sym', 'target'}} from an assembled bridges object and, per bridge, the __fnv_
    symbol its b/bl relocations name (its target)."""
    import elf32
    os.makedirs(work, exist_ok=True)
    text = path                     # linked whole; qemu_ilp32's script discards its TLS block
    o = elf32.Obj(path)
    out = {}
    # every other symbol the object needs lands on the harness's v1_trap (a near address: no branch out of range)
    provide = os.path.join(work, 'zz_bridges_provide.ld')
    with open(provide, 'w') as f:
        for name in sorted({s.name for s in o.syms if s.name and s.shndx == 'SHN_UNDEF'}):
            f.write('PROVIDE(%s = v1_trap);\n' % name)
    syms = sorted((s for s in o.syms if s.name.startswith('__fnvnx_br_') and isinstance(s.shndx, int)),
                  key=lambda s: (s.shndx, s.value))
    rels = sorted((r for r in o.rels if r.type in ('JUMP26', 'CALL26')), key=lambda r: (r.sec, r.offset))
    import bisect
    keys = [(r.sec, r.offset) for r in rels]
    for s in syms:
        k = bisect.bisect_left(keys, (s.shndx, s.value))
        tgt = None
        while k < len(rels) and rels[k].sec == s.shndx and rels[k].offset < s.value + max(s.size, 4):
            if rels[k].sym.name.startswith('__fnv_'):
                tgt = rels[k].sym.name
            k += 1
        if tgt:
            out[s.name] = {'obj': text, 'sym': s.name, 'target': tgt, 'provide': provide}
    o.close()
    return out


def load_layouts(path):
    lay = collections.defaultdict(dict)
    if not os.path.exists(path):          # a link without the probe step: no aggregate layouts
        return lay
    for line in open(path):
        if line.startswith('#'):
            continue
        c = line.rstrip('\n').split('\t')
        if len(c) < 7 or c[2] == '-1':
            continue
        pod = (c[8] == '1') if len(c) > 8 and c[8] in ('0', '1') else None
        lay[c[0]][c[1]] = (int(c[2]), int(c[3]), int(c[4]), c[6], pod)
    return lay


def main():
    out_dir, units_json, work = sys.argv[1:4]
    per = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    nseeds = int(sys.argv[5]) if len(sys.argv) > 5 else 3
    os.makedirs(work, exist_ok=True)
    abi = v1pairs.load_abi(out_dir)
    targets = {}
    for c in v1pairs.load_refs(out_dir, classes=('code-bridge',)):
        targets[(c[0], c[1])] = c[5]
    if os.path.exists(os.path.join(out_dir, 'bridges.S')):
        bridges = load_bridges(os.path.join(out_dir, 'bridges.S'))
    else:   # a farm link publishes the assembled bridges only (fixed/zz_bridges.o): run those bytes
        bridges = load_bridge_obj(os.path.join(os.path.dirname(os.path.abspath(out_dir)), 'fixed', 'zz_bridges.o'),
                                  work)
    lays = load_layouts(os.path.join(out_dir, 'layouts.tsv'))
    dba = v1pairs.defs_by_addr(units_json)
    rows = [c for c in abi.values() if c[5] == 'bridge' and (c[0], c[1]) in targets]
    # unique (ref, def) pairs; a pair's classes as a set
    uniq = {}
    for c in rows:
        uniq.setdefault((c[3], c[4]), c)
    rng = random.Random(1104)
    chosen = {}
    by_class = collections.defaultdict(list)
    for k, c in sorted(uniq.items()):
        for cl in c[6].split(','):
            cl = re.sub(r'\(.*', '', cl)
            by_class[cl].append(k)
    if os.environ.get('V1_SELECT') == 'fastcall-agg':
        # every bridged pair with a __fastcall side returning an aggregate by value (adversarial shape B)
        dall = bh.undname(sorted({n for k in uniq for n in k}))
        for k in uniq:
            for n in k:
                t = dall.get(n) or ''
                m = re.match(r'^(?:(?:public|protected|private): )?(?:static )?(?:virtual )?(.*?) __fastcall ', t)
                if m and re.match(r'^(class|struct|union) [^*&]*$', m.group(1).replace(' const', '')):
                    chosen[k] = 'fastcall-aggregate-result'
    else:
        for cl in CLASS_ORDER + sorted(set(by_class) - set(CLASS_ORDER)):
            ks = [k for k in by_class.get(cl, []) if k not in chosen]
            rng.shuffle(ks)
            for k in ks[:per]:
                chosen[k] = cl
    names = sorted({n for k in chosen for n in k})
    dem = bh.undname(names)
    res = collections.Counter()
    per_class = collections.defaultdict(collections.Counter)
    lines = []
    for k, cl in sorted(chosen.items(), key=lambda x: (CLASS_ORDER.index(x[1]) if x[1] in CLASS_ORDER else 99, x[0])):
        c = uniq[k]
        obj, undef, addr = c[0], c[1], int(c[2], 16)
        bname = targets[(obj, undef)]
        asm = bridges.get(bname)
        def_units = [u for u, s in dba.get(addr, []) if s == c[4]]
        lref = {t: v for t, v in lays.get(obj, {}).items()}
        ldef = {t: v for t, v in lays.get(def_units[0], {}).items()} if def_units else {}
        outcomes = set()
        notes = []
        detail = ''
        pod_known = all(v[4] is not None for v in list(lref.values()) + list(ldef.values()))
        for pod in ((None,) if pod_known else (True, False)):
            hr = {t: bh.Lay(v[0], v[1], v[2], v[3], v[4] if v[4] is not None else pod) for t, v in lref.items()}
            hd = {t: bh.Lay(v[0], v[1], v[2], v[3], v[4] if v[4] is not None else pod) for t, v in ldef.items()}
            sts = []
            for seed in range(nseeds):
                try:
                    st, nt = bh.run_case(c[3], c[4], hr, hd, asm, os.path.join(work, '%08x_%s' % (addr, bname[-8:])),
                                         seed=seed + 1, dem=dem)
                except bh.Skip as e:
                    st, nt = 'skip ' + str(e), []
                except Exception as e:      # build errors are harness failures, reported as such
                    st, nt = 'error ' + str(e).split('\n')[0][:120], []
                sts.append(st)
                notes = nt
            worst = next((s for s in sts if s.startswith('FAIL')), None) or next(
                (s for s in sts if not s.startswith('pass')), 'pass')
            outcomes.add(worst.split(' ')[0])
            detail = detail or (worst if worst != 'pass' else '')
            if not any(t in hr or t in hd for t in ()):
                pass
            # POD-ness only matters for free functions returning small aggregates
            if not re.search(r'struct|class|union', (dem.get(c[3]) or '') + (dem.get(c[4]) or '')):
                break
        if len(outcomes) > 1:
            st = 'pod-dependent'
        else:
            st = outcomes.pop()
        res[st] += 1
        per_class[cl][st] += 1
        lines.append('%-14s %-32s %08x %s\n      ref %s\n      def %s%s%s' % (
            st, cl, addr, bname, dem.get(c[3]), dem.get(c[4]), ('\n      ' + detail[:300]) if detail else '',
            ('\n      notes: ' + '; '.join(notes)) if notes else ''))
    with open(os.path.join(work, 'c2_results.txt'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('bridged pairs in the run: %d unique (ref, def); chosen %d' % (len(uniq), len(chosen)))
    for cl in sorted(per_class, key=lambda x: CLASS_ORDER.index(x) if x in CLASS_ORDER else 99):
        print('  %-34s %s' % (cl, dict(per_class[cl])))
    print('total', dict(res))
    for l in lines:
        if not l.startswith('pass'):
            print(l)
    return 1 if res.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
