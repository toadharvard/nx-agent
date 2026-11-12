#!/usr/bin/env python3
"""check_doubles.py - the census of test doubles (CLAUDE.md, owner order of 2026-11-11 night: no doubles in tests).

The rule, per test binary:  (symbols defined by the test's own files) ∩ (symbols defined by the product) = ∅,
and a test links the real product files of its root.

Product  = the units platform/build_guest.sh compiles for the guest: depth-1 *.c / *.cpp of platform/{win32,crt,audio,
           input,boundary,com,os} platform/clock/clock_rt.cpp, clock_real.cpp (clock_virtual.cpp is
           the verification clock, also a product choice: both clocks are product units), platform/guest/os_guest.c and
           platform_glue.cpp.  The set is read from platform/build_guest.sh itself, so it cannot drift from the build.
Test file = every .o of a test binary's link line that is not a product unit (the product's third-party FreeType objects and
           archives are not test files and are not read).

A pure function of its arguments: it reads the link maps, the objects (through `readelf -sW`), build_guest.sh and the optional
test logs; no clock, no environment.

Findings (each is a rule violation; exit 1 if any count is nonzero):
  double          test file F of binary B strongly defines symbol S that a product unit defines.
  replaced unit   product unit U is not linked in binary B while a test file of B defines >= 1 symbol of U.
  unlinked unit   product unit U is linked in no test binary.
  boundary double test file F (not a double file) strongly defines a symbol that product units reference and no product unit
                  defines: F stands in for what lies below the product (deko3d, libnx, the kernel, the host side).
Weak/weak overlap (inline functions, template instantiations) is not a replacement and is only counted (informational).

Inputs:
  --root R            the fnv-nx directory
  --linkmaps F        TSV: binary <TAB> entry <TAB> entry ...   (one line per test binary; an entry is an object path, or
                      <root-relative source>=<object path> when the object is not named after its source; non-.o entries are ignored)
  --guest-objs D      directory with the guest-build objects, named like build_guest.sh names them (rel path, '/' -> '_', + .o);
                      supplies the symbols of units that no host binary links
  --test-log B=F      the log of binary B (or of the make run that ran it); the count is the first "<N> tests" / "s7: <N> tests"
  --tsv OUT           write the findings as TSV
"""
import argparse
import os
import re
import subprocess
import sys

DIRS_RE = re.compile(r'for\s+sub\s+in\s+([a-z0-9_ ]+);\s*do')
GUESTFILES_RE = re.compile(r'for\s+n\s+in\s+(os_guest\.c\s+platform_glue\.cpp)\s*;')


def product_units(root):
    txt = open(os.path.join(root, 'platform', 'build_guest.sh')).read()
    dirs = DIRS_RE.search(txt).group(1).split()
    units = []
    for d in dirs:
        p = os.path.join(root, 'platform', d)
        if not os.path.isdir(p):
            continue
        for f in sorted(os.listdir(p)):
            if f.endswith('.c') or f.endswith('.cpp'):
                units.append('platform/%s/%s' % (d, f))
    units += ['platform/clock/clock_rt.cpp', 'platform/clock/clock_real.cpp', 'platform/clock/clock_virtual.cpp']
    for n in GUESTFILES_RE.search(txt).group(1).split():
        units.append('platform/guest/' + n)
    return sorted(set(units))


_cache = {}


def symbols(path, tool, filt='c++filt'):
    """(strong defined, weak defined, undefined) of an object, from `readelf -sW`.
    A definition is a GLOBAL/WEAK/UNIQUE symbol of default or protected visibility; hidden/internal symbols are the compiler's
    own linkonce helpers (__x86.get_pc_thunk.*), not interfaces."""
    if path in _cache:
        return _cache[path]
    if not os.path.exists(path):
        _cache[path] = None
        return None
    out = subprocess.run([tool, '-sW', path], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit('check_doubles: readelf failed on %s: %s' % (path, out.stderr.strip()))
    strong, weak, und = set(), set(), set()
    for ln in out.stdout.splitlines():
        p = ln.split()
        if len(p) < 8 or not p[0].endswith(':'):
            continue
        typ, bind, vis, ndx, name = p[3], p[4], p[5], p[6], p[7].split('@')[0]
        if typ in ('SECTION', 'FILE') or bind == 'LOCAL' or ndx == 'ABS':
            continue
        if ndx == 'UND':
            und.add(name)
        elif vis in ('HIDDEN', 'INTERNAL'):
            continue
        elif bind == 'GLOBAL':
            strong.add(name)
        else:                       # WEAK, UNIQUE
            weak.add(name)
    strong, weak, und = (_keys(x, filt) for x in (strong, weak, und))
    _cache[path] = (strong, weak, und)
    return _cache[path]


def _strip_params(d):
    """demangled 'ns::f<T>(int, char)' -> 'ns::f<T>'; the parameter list is dropped because the host (LP64) and the guest
    (ILP32) mangle the same declaration differently (uint64_t is unsigned long vs unsigned long long)"""
    depth = 0
    for i, c in enumerate(d):
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
        elif c == '(' and depth == 0 and not d[:i].endswith('operator'):
            return d[:i]
    return d


def _keys(names, filt):
    """the census key of each symbol: C symbols as they are, C++ symbols by their qualified name (no parameter list)"""
    mangled = sorted(n for n in names if n.startswith('_Z'))
    keys = {n for n in names if not n.startswith('_Z')}
    if mangled:
        out = subprocess.run([filt], input='\n'.join(mangled) + '\n', capture_output=True, text=True).stdout.split('\n')
        keys |= {_strip_params(d) for d in out[:len(mangled)]}
    return keys


SRC = {}    # object -> root-relative source, for link-map entries written "<source>=<object>" (objects not named after their source)


def unit_of(obj, units):
    if obj in SRC:
        return SRC[obj] if SRC[obj] in units else None
    for u in units:
        if obj.endswith('/' + u + '.o') or obj == u + '.o':
            return u
    return None


def read_linkmaps(path):
    maps = []
    for ln in open(path):
        ln = ln.rstrip('\n')
        if not ln:
            continue
        f = ln.split('\t')
        objs = []
        for e in f[1:]:
            src, eq, o = e.partition('=')
            if not eq:
                src, o = None, e
            if o.endswith('.o'):
                objs.append(o)
                if src:
                    SRC[o] = src
        maps.append((f[0], objs))
    return maps


def count_tests(path):
    m = None
    for ln in open(path, errors='replace'):
        m2 = re.search(r'(?:^|\s)(\d+) tests\b', ln)
        if m2:
            m = int(m2.group(1))
    return m


def run(a):
    units = product_units(a.root)
    maps = read_linkmaps(a.linkmaps)
    # the product's symbols: every object of a product unit, from the host binaries and from the guest build
    pdef, pweak, pund = {}, {}, set()
    pobjs = {}
    for _, objs in maps:
        for o in objs:
            u = unit_of(o, units)
            if u:
                pobjs.setdefault(u, set()).add(o)
    if a.guest_objs:
        for u in units:
            g = os.path.join(a.guest_objs, u.replace('/', '_') + '.o')
            if os.path.exists(g):
                pobjs.setdefault(u, set()).add(g)
    for u in units:
        pdef[u], pweak[u] = set(), set()
        for o in pobjs.get(u, ()):
            sy = symbols(o, a.readelf)
            if sy is None:
                continue
            s, w, un = sy
            pdef[u] |= s
            pweak[u] |= w
            pund |= un
    owner = {}
    for u in units:
        for s in pdef[u] | pweak[u]:
            owner.setdefault(s, set()).add(u)
    alldef = set(owner)
    boundary_syms = pund - alldef

    rows = []           # (kind, binary, file, symbol-or-unit, detail)
    linked_any = set()
    bin_find = {}
    weakov = 0
    for b, objs in maps:
        linked = {unit_of(o, units) for o in objs} - {None}
        linked_any |= linked
        defined_by_test = {}
        for o in objs:
            if unit_of(o, units):
                continue
            rel = SRC.get(o) or os.path.relpath(o, a.root)
            m = re.search(r'(?:^|/)build[^/]*/(.*)$', rel)
            if m:
                rel = m.group(1)
            if rel.endswith('.o'):
                rel = rel[:-2]
            if rel.startswith('render/'):
                continue        # another layer's product (L6 D3D9 front end, linked into the m32 tests): not a test file
            sy = symbols(o, a.readelf)
            if sy is None:
                rows.append(('unbuilt object', b, rel, '', 'the object does not exist: the census of this file is not made'))
                continue
            s, w, _ = sy
            defined_by_test[rel] = s
            weakov += len(w & alldef)
            for sym in sorted(s & alldef):
                rows.append(('double', b, rel, sym, ','.join(sorted(owner[sym]))))
        for u in units:
            if u in linked:
                continue
            for rel, s in defined_by_test.items():
                hit = s & pdef[u]
                if hit:
                    rows.append(('replaced unit', b, rel, u, '%d symbols' % len(hit)))
        for rel, s in defined_by_test.items():
            bs = s & boundary_syms
            if bs:
                rows.append(('boundary double', b, rel, ','.join(sorted(bs)[:6]) + ('...' if len(bs) > 6 else ''), '%d symbols' % len(bs)))
        bin_find[b] = any(r[1] == b for r in rows)
    for u in units:
        if not any(os.path.exists(o) for o in pobjs.get(u, ())):
            rows.append(('unbuilt object', '', u, '', 'no object of this product unit exists: its symbols are not known'))
    for u in units:
        if u not in linked_any:
            known = 'symbols known' if pobjs.get(u) else 'symbols UNKNOWN (no object)'
            rows.append(('unlinked unit', '', u, '', known))
    return units, maps, rows, weakov, bin_find


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--linkmaps')
    ap.add_argument('--guest-objs')
    ap.add_argument('--readelf', default='readelf')
    ap.add_argument('--test-log', action='append', default=[])
    ap.add_argument('--tsv')
    ap.add_argument('--list-units', action='store_true', help='print the product units (root-relative) and exit')
    a = ap.parse_args()
    if a.list_units:
        print('\n'.join(product_units(a.root)))
        return
    if not a.linkmaps:
        ap.error('--linkmaps is required')
    units, maps, rows, weakov, bin_find = run(a)

    out = ['kind\tbinary\tfile\tsymbol_or_unit\tdetail'] + ['\t'.join(r) for r in rows]
    if a.tsv:
        open(a.tsv, 'w').write('\n'.join(out) + '\n')
    for ln in out:
        print(ln)
    dfiles = {(r[2]) for r in rows if r[0] == 'double'}
    dsyms = {(r[2], r[3]) for r in rows if r[0] == 'double'}
    per_file = {}
    for f, s in dsyms:
        per_file[f] = per_file.get(f, 0) + 1
    repl = {r[3] for r in rows if r[0] == 'replaced unit'}
    unl = [r[2] for r in rows if r[0] == 'unlinked unit']
    bfiles = {r[2] for r in rows if r[0] == 'boundary double' and r[2] not in dfiles}
    tests = {}
    for t in a.test_log:
        k, _, p = t.partition('=')
        tests[k] = count_tests(p)
    print()
    print('# product units: %d' % len(units))
    print('# binaries: %d total, %d with findings' % (len(maps), sum(1 for v in bin_find.values() if v)))
    tw = sum(n or 0 for b, n in tests.items() if bin_find.get(b))
    print('# tests in binaries with findings: %d (of %d counted: %s)' % (
        tw, sum(n or 0 for n in tests.values()), ' '.join('%s=%s' % kv for kv in sorted(tests.items()))))
    print('# double files: %d, double symbols: %d (%s)' % (
        len(dfiles), len(dsyms), ', '.join('%s %d' % (f, n) for f, n in sorted(per_file.items(), key=lambda x: -x[1]))))
    print('# replaced units: %d (%s)' % (len(repl), ' '.join(sorted(repl))))
    print('# unlinked units: %d (%s)' % (len(unl), ' '.join(unl)))
    print('# boundary-double files (not already double files): %d (%s)' % (len(bfiles), ' '.join(sorted(bfiles))))
    print('# weak/weak overlap symbol-instances (informational, not a finding): %d' % weakov)
    unb = {(r[1] or '-', r[2]) for r in rows if r[0] == 'unbuilt object'}
    print('# unbuilt objects (census incomplete): %d (%s)' % (len(unb), ' '.join('%s:%s' % x for x in sorted(unb))))
    sys.exit(1 if (dsyms or repl or unl or bfiles or unb) else 0)


if __name__ == '__main__':
    main()
