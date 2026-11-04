#!/usr/bin/env python3
"""msvc_check.py - S11 equivalence check of the S1b patches (stream S1b-patches, docs/PATCHES.md).

    msvc_check.py DECOMP WORK [--pristine-objs DIR] [--only SRC,...] [--renames OUT.tsv] [--jobs N]

Applies every patch in patches/*.s1b to the PRISTINE decomp text (no other rule), compiles each patched unit with its
recipe compiler and flags under MSVC 2008 (tools/equiv setup: FNV_MSVC9_ROOT, FNV_MSVC9_RTM_ROOT, FNV_MSVC9_PREFIX),
and compares every function with the pristine object (compare_builds fingerprint: code bytes, padding, every
relocation resolved to its original address). A declaration fix must leave the code identical; renamed definitions
and references are followed: a recipe function whose decorated name is gone is looked up under the new name with the
same identifier, and a relocation that names another symbol at the same offset is bound to the address of the old
one. Those pairs are the units.json renames (`--renames`, the format of patches/units_renames.tsv).

The FTOL1 patches are compiled with tests/s1b/msvc/s1b_check_fi.h force-included: `fnvnx_ftol2(x)` means the
compiler's own float -> __int64 conversion there (MSVC calls `_ftol2_sse` with x in ST0), so the check also proves
which expression the original code had on the x87 stack.

Objects and reports stay in WORK (decomp-derived, never committed).
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PORT = HERE.parent.parent
sys.path.insert(0, str(PORT / 'tools' / 'rules'))
sys.path.insert(0, str(PORT / 'tools'))
sys.path.insert(0, str(PORT / 'tools' / 'equiv'))
import equiv  # noqa: E402
import s1b_patches  # noqa: E402

CHECK_FI = HERE / 'msvc' / 's1b_check_fi.h'
HELPER = re.compile(r'^(?:__(?:ftol2(?:_sse)?|CI\w+)|\$SG\d+)$')    # compiler helpers and string literals


def overlay(decomp, work, files):
    """WORK/tree: symlink copy of decomp src/ and include/ with the patched files written in."""
    tree = work / 'tree'
    if not tree.exists():
        tree.mkdir(parents=True)
        for d in ('src', 'include'):
            subprocess.run(['cp', '-rs', str(Path(decomp, d).resolve()), str(tree / d)], check=True)
    report = []
    for rel in files:
        src = Path(decomp, rel)
        text = src.read_text(encoding='utf-8', errors='surrogateescape')
        stats = __import__('collections').Counter()
        for rule in ('XINC1', 'FTOL1', 'DBUG1'):
            text = s1b_patches.apply_sites(str(src), text, stats, rule, report)
        dst = tree / rel
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        dst.write_text(text, encoding='utf-8', errors='surrogateescape')
    return tree, report


def compile_unit(decomp, tree, unit, obj):
    args = [sys.executable, str(Path(decomp, 'tools/msvc.py').resolve()), '--compiler', unit['compiler'], '/c',
            '/nologo'] + unit['flags'] + ['/I' + str(equiv.SHIM), '/FI', str(equiv.FORCE_INCLUDE), '/FI', str(CHECK_FI),
                                          '/I' + str(tree / 'include'), str(tree / unit['source']), '/Fo' + str(obj)]
    r = subprocess.run(args, cwd=tree, capture_output=True, text=True, errors='replace')
    return r.returncode == 0 and obj.exists(), r.stdout + r.stderr


def ident(name):
    """Identifier of an MSVC decorated name: ?name@... -> name; ??0X@ / ??1X@ -> ctor/dtor of X; _f / _f@N -> f."""
    m = re.match(r'^\?\?([01_][A-Z]?)(\w+)@', name)
    if m:
        return '%s:%s' % (m.group(1), m.group(2))
    m = re.match(r'^\?(\w+)@', name)
    if m:
        return m.group(1)
    m = re.match(r'^[_@]?(\w+?)(?:@\d+)?$', name)
    return m.group(1) if m else name


def func_relocs(coff, sym):
    s = coff.named(sym)
    sec = coff.sections[s['section']]
    start = s['value']
    later = [x['value'] for x in coff.symbols.values() if x['section'] == s['section'] and x['type'] & 0x20
             and x['value'] > start and not x['name'].startswith('__catch$')]
    end = min(later) if later else len(sec['data'])
    return {o - start: coff.symbols[ix]['name'] for o, ix, _ in sec['relocs'] if start <= o < end}


def check_unit(fpr, Coff, i, unit, pristine_obj, new_obj, want):
    old, new = Coff(str(pristine_obj)), Coff(str(new_obj))
    old_defs = {s['name'] for s in old.symbols.values() if s['section'] > 0}
    new_defs = {s['name']: s for s in new.symbols.values() if s['section'] > 0 and s['type'] & 0x20}
    names = dict(fpr.names)
    local = {k: int(v, 16) for k, v in unit['symbols'].items()}
    for fn in unit['functions']:
        local[fn['symbol']] = int(fn['address'], 16)
    renames, mod = [], json.loads(json.dumps(unit))
    for fn in mod['functions']:
        if fn['symbol'] in new_defs:
            continue
        cand = [n for n in new_defs if n not in old_defs and ident(n) == ident(fn['symbol'])]
        if len(cand) == 1:
            renames.append((unit['source'], 'def', fn['address'], fn['symbol'], cand[0]))
            fn['symbol'] = cand[0]
    # references: same offset, another name -> the old name's address
    for fn_old, fn in zip(unit['functions'], mod['functions']):
        try:
            ro, rn = func_relocs(old, fn_old['symbol']), func_relocs(new, fn['symbol'])
        except ValueError:
            continue
        for off, nn in rn.items():
            on = ro.get(off)
            if on is None or on == nn or nn in local or nn in mod['symbols']:
                continue
            a = local.get(on, names.get(on))
            if a is None:
                continue
            if nn in new_defs:      # a definition of this unit (renamed): already a function of the recipe
                continue
            mod['symbols'][nn] = '0x%08x' % a
            if not HELPER.match(nn):    # compiler helpers MSVC calls by itself: no GCC symbol to bind
                renames.append((unit['source'], 'ref', '0x%08x' % a, on, nn))
    # S11's bindings on both sides (equiv.Resolver): string literals by content - MSVC numbers $SGnnnn with a counter
    # that any declaration a patch adds may advance - and recipe names of the whole tree
    ea, _ = fpr.resolver.extend(old, unit)
    eb, _ = fpr.resolver.extend(new, mod)
    fa = fpr.cb.fingerprint(str(pristine_obj), ea, fpr.names_nolit)
    fb = fpr.cb.fingerprint(str(new_obj), eb, fpr.names_nolit)
    res = {}
    for fn in unit['functions']:
        a = fn['address']
        if want and a not in want:
            continue
        res[a] = 'identical' if fa.get(a, [None])[0] == fb.get(a, [0])[0] else \
            ('missing' if fb.get(a, [None])[0] == 'missing' else 'differs')
    return res, renames, fa.get('eh') == fb.get('eh')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('decomp'); ap.add_argument('work')
    ap.add_argument('--pristine-objs', default=os.environ.get('S11', '') + '/obj/pristine')
    ap.add_argument('--only', help='comma-separated source paths')
    ap.add_argument('--renames', help='write the measured units.json renames here')
    ap.add_argument('--all-functions', action='store_true', help='report every function of the units, not only sites')
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--reuse', action='store_true', help='compare objects already in WORK/obj (no compile)')
    a = ap.parse_args()
    decomp, work = Path(a.decomp).resolve(), Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    sites = s1b_patches.load_sites()
    files = sorted({s.path for s in sites})
    if a.only:
        files = [f for f in files if f in a.only.split(',')]
    tree, report = overlay(decomp, work, files)
    for m in report:
        print('MISS', m)
    fpr = equiv.Fingerprinter(str(decomp))
    from compare import Coff
    units = fpr.units
    jobs = [(i, u) for i, u in enumerate(units) if u['source'] in files]
    want_by_src = {}
    for s in sites:
        if s.addr is not None:
            want_by_src.setdefault(s.path, set()).add('0x%08x' % s.addr)
    (work / 'obj').mkdir(exist_ok=True)

    def one(job):
        i, u = job
        obj = work / 'obj' / ('u%d.obj' % i)
        ok, out = (True, '') if a.reuse and obj.exists() else compile_unit(decomp, tree, u, obj)
        if not ok:
            return i, u, None, out
        want = None if a.all_functions else want_by_src.get(u['source'])
        return i, u, check_unit(fpr, Coff, i, u, Path(a.pristine_objs, 'u%d.obj' % i), obj, want), out

    all_renames, summary = [], {}
    with ThreadPoolExecutor(a.jobs) as pool:
        for i, u, res, out in pool.map(one, jobs):
            if res is None:
                print('COMPILE-FAILED u%d %s\n%s' % (i, u['source'], out[-2500:]))
                summary[i] = 'compile-failed'
                continue
            fns, ren, eh = res
            all_renames += ren
            for addr, st in sorted(fns.items()):
                print('%-9s u%-5d %s %s%s' % (st, i, addr, u['source'], '' if eh else ' (eh differs)'))
                summary.setdefault(st, 0)
                summary[st] += 1
    seen, uniq = set(), []
    for r in all_renames:
        if r not in seen:
            seen.add(r); uniq.append(r)
    if a.renames:
        with open(a.renames, 'w') as f:
            f.write('# source\tkind\taddress\told MSVC name\tnew MSVC name   (tests/s1b/msvc_check.py --renames)\n')
            for r in sorted(uniq):
                f.write('\t'.join(r) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if isinstance(v, int)}), 'renames', len(uniq))


if __name__ == '__main__':
    main()
