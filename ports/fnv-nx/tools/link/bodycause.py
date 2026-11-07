"""bodycause.py - why a `missing` name of bodies.py has no body (stream S2-bodies; docs/LINK.md, "Declared-only
functions").

    python3 -I tools/link/bodycause.py OUT TREE OBJDIR [--traps TRAPS.txt] [--closed NAMES ...] [--counts NOTE.md]
                                       [-o OUT/bodies/causes.tsv]

OUT is a link's out/ (reads out/bodies/bodies.tsv), TREE the prepared tree it was linked from (build/units.json,
src/, include/), OBJDIR the linked objects (`fixed/*.o` of a link publish: every definition the link had). TRAPS is
an optional list of names the guest image binds to trap stubs (one per line); it adds a `trap` column so a class is
weighed by what reaches the image.

Every `missing` row gets exactly one cause, first that applies:

1. `crt-import` - the C/C++ runtime or an import: `std::`, `stdext::` (also a unit's copy `fnv_<a>::std::`), the
   MSVC STL's `_X*` throwers, `operator new/delete`, CRT C names. The runtime (S4) provides these, not the decomp.
2. `data` - not a function (bodies.py note): a static data member declared only. S2c's address map binds data.
3. `name-mismatch` - an object of the link defines the same function under another decorated name: the same
   member of the same class (class shells and unit namespaces relaxed) with the same number of parameters, but
   other parameter spellings, cv, namespace or return type. The decomp has the body; the declaration the
   referencing unit sees does not match it.
4. `not-compiled` - the tree has a body for it (an out-of-line `C::f(` definition or an in-class body of C::f),
   but no object defines it: in a source that is not a unit, under `#if 0`, or in a class copy the referencing
   unit does not include.
5. `no-body` - the decomp has no body anywhere. Subclasses (the `detail` column): `template-special` (a special
   member of a class template: refcount / free logic, never `= default`), `msvc-direct` (the pristine MSVC
   objects call it out of line, so the original had it out of line too, at an exe address no placed caller
   reaches), `exe-shape` (the exe calls an address whose definition has another shape), `other`.

With --traps, trap stubs bodies.py never classifies are listed too, from out/refs.tsv: `extern-text` (an exe
address the native build references but no unit compiles), `thunk` (a this-adjusting thunk of a function defined
nowhere), `runtime` / `import` / other refs classes as they are. --closed: files of names (one per line, or a JSON
list) that rules not yet in this link close (measured offline); they are left out.

`fun-addr`: an unresolved C placeholder named after an exe address (`FUN_<addr>`). Every row gets an owner
(D23): `recon` (no-body, extern-text, fun-addr: the fleet reconstructs them against the exe) or
`s2-bodies` (every other class), and a shard: sha1(unit) mod 10 of the unit the body belongs in (extern-text: the
unit units.json places the address in, else the unit of the placed function before it; otherwise the first
referencing unit). --counts writes a note with the
counts per cause, owner and shard, so each recon worker checks its share.

Output: causes.tsv (name, demangled, cause, detail, evidence, trap, units, owner, shard, home unit), and a summary
on stdout. Derived from
the decomp: it stays in the container or the private repository, never in nx-agent.
"""
import argparse
import bisect
import collections
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sigs                                   # noqa: E402
from bodies import norm, UNIT_NS              # noqa: E402

CRT_C = {'memcpy', 'memset', 'memmove', 'memcmp', 'strlen', 'strcpy', 'strncpy', 'strcmp', 'strncmp', 'strcat',
         'sprintf', 'vsprintf', '_snprintf', '_vsnprintf', 'malloc', 'free', 'realloc', 'calloc', '_purecall',
         'atexit', '_onexit', 'abort', 'exit', 'qsort', 'bsearch', 'rand', 'srand', '_ftol', '_ftol2', '_CIpow',
         'sqrt', 'sin', 'cos', 'atan2', 'floor', 'ceil', 'fmod', 'pow', 'log', 'exp'}
CRT_SCOPE = re.compile(r'^(?:fnv_[0-9a-f]{8}::)?(?:std|stdext)::')
CRT_MEMBER = re.compile(r'^_X(?:ran|len|invalid_argument|out_of_range|length_error)$')
CAUSES = ('crt-import', 'data', 'name-mismatch', 'not-compiled', 'no-body')


def demangle(names):
    if not names:
        return []
    r = subprocess.run(['c++filt'], input='\n'.join(names) + '\n', capture_output=True, text=True,
                       errors='surrogateescape')
    out = r.stdout.split('\n')
    return out[:len(names)]


def strip_targs(s):
    """'NiTArray<A, B>' -> 'NiTArray'."""
    i = s.find('<')
    return s[:i] if i > 0 else s


def key_of(sig):
    """(class, member, arity) with shells, unit namespaces and template arguments relaxed; None for non-members
    (then (None, name, arity))."""
    if sig is None or sig.kind != 'func' or not sig.qual or sig.params is None:
        return None
    qual = [q for q in sig.qual if not UNIT_NS.match(q)]
    if not qual:
        return None
    last = strip_targs(qual[-1]) if not qual[-1].startswith('operator') else qual[-1]
    cls = norm(strip_targs(qual[-2])) if len(qual) >= 2 else None
    return (cls, last, len(sig.params))


def is_crt(sig, dem):
    if CRT_SCOPE.match(dem):
        return 'std scope'
    if sig is not None and sig.qual:
        last = sig.qual[-1]
        if CRT_MEMBER.match(last):
            return 'MSVC STL thrower %s' % last
        if last.startswith(('operator new', 'operator delete')) and len(sig.qual) <= 2:
            return last
        if len(sig.qual) == 1 and last in CRT_C:
            return 'CRT %s' % last
    return None


# ------------------------------------------------------------------------------------------- definitions

def object_defs(objdir):
    """{key: set(mangled)} of every code symbol the objects define."""
    names = set()
    for f in sorted(os.listdir(objdir)):
        if not f.endswith('.o'):
            continue
        r = subprocess.run(['nm', '--defined-only', '-P', os.path.join(objdir, f)], capture_output=True, text=True,
                           errors='surrogateescape')
        for line in r.stdout.splitlines():
            p = line.split()
            if len(p) >= 2 and p[1] in 'TtWw' and p[0].startswith('_Z'):
                names.add(p[0])
    names = sorted(names)
    idx = collections.defaultdict(set)
    for n, d in zip(names, demangle(names)):
        k = key_of(sigs.parse_itanium(n, d))
        if k:
            idx[k].add(n)
    return idx


IF0 = re.compile(r'^\s*#\s*if\s+0\b')
IFX = re.compile(r'^\s*#\s*if')
ENDIF = re.compile(r'^\s*#\s*endif')


def masked_if0(text):
    """The text with `#if 0` ... `#endif` blocks blanked (lines kept, so offsets stay line-true)."""
    out, depth = [], 0
    for line in text.split('\n'):
        if depth:
            if IFX.match(line):
                depth += 1
            elif ENDIF.match(line):
                depth -= 1
            out.append('')
            continue
        if IF0.match(line):
            depth = 1
            out.append('')
            continue
        out.append(line)
    return '\n'.join(out)


OOL = re.compile(r'\b(\w+)\s*(?:<[^;{}()]*?>)?\s*::\s*(~?\w+|operator\s*[^\s(]+(?:\s*\[\])?)\s*\(')
CLASS = re.compile(r'\b(?:class|struct|union)\s+(?:__declspec\([^)]*\)\s*)?(\w+)[^;{(]*\{')
INCLASS = re.compile(r'(?<![\w:.>])(~?\w+|operator\s*[^\s(]+)\s*\(([^;{}]*?)\)\s*(?:const\s*)?(?:throw\s*\([^)]*\)\s*)?'
                     r'(?::[^;{]*)?\{')


def body_end(text, i):
    """Index after the brace block opening at text[i] == '{'."""
    d = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == '{':
            d += 1
        elif c == '}':
            d -= 1
            if d == 0:
                return j + 1
    return len(text)


def tree_bodies(tree, wanted):
    """{(cls, member): [(file, where)]} for the (class, member) pairs in `wanted`: out-of-line definitions
    `C::m(...) {` and in-class bodies `class C { ... m(...) { ... }`. where is 'if0' when it lies in `#if 0`."""
    found = collections.defaultdict(list)
    members = {m for _, m in wanted}
    for base in ('src', 'include'):
        root = os.path.join(tree, base)
        for dp, _, fs in os.walk(root):
            for f in fs:
                if not f.endswith(('.cpp', '.h', '.inl', '.hpp', '.c')):
                    continue
                path = os.path.join(dp, f)
                rel = os.path.relpath(path, tree)
                raw = open(path, encoding='utf-8', errors='replace').read()
                if not any(m.lstrip('~') in raw for m in members if not m.startswith('operator')) and \
                        'operator' not in raw:
                    continue
                live = masked_if0(raw)
                for text, where in ((live, 'live'), (raw, 'if0')):
                    for m in OOL.finditer(text):
                        cls, mem = norm(m.group(1)), re.sub(r'\s+', '', m.group(2))
                        mem = 'operator' + mem[8:] if mem.startswith('operator') else mem
                        if (cls, mem) not in wanted:
                            continue
                        close = sigs.matching_close(text, m.end() - 1)
                        rest = text[close + 1:close + 200] if close > 0 else ''
                        if re.match(r'\s*(?:const\s*)?(?:throw\s*\([^)]*\)\s*)?(?::[^;{]*)?\{', rest):
                            found[(cls, mem)].append((rel, where))
                    for c in CLASS.finditer(text):
                        cls = norm(c.group(1))
                        if not any(w[0] == cls for w in wanted):
                            continue
                        start, end = c.end() - 1, body_end(text, c.end() - 1)
                        inner = text[start + 1:end - 1]
                        for m in INCLASS.finditer(inner):
                            mem = re.sub(r'\s+', '', m.group(1))
                            mem = 'operator' + mem[8:] if mem.startswith('operator') else mem
                            if (cls, mem) in wanted:
                                found[(cls, mem)].append((rel, where + '-inclass'))
                    if where == 'live' and live == raw:
                        break
    return found


# ------------------------------------------------------------------------------------------- classify

def classify(rows, objidx, tbodies, units):
    out = []
    for r in rows:
        name, dem, note, sig = r['name'], r['dem'], r['note'], r['sig']
        k = key_of(sig)
        cause, detail, ev = None, '', ''
        crt = is_crt(sig, dem)
        if crt:
            cause, detail = 'crt-import', crt
        elif note.startswith('not a function') or (sig is not None and sig.kind == 'data'):
            cause, detail = 'data', 'static member or global declared only'
        if cause is None and k:
            other = sorted(n for n in objidx.get(k, ()) if n != name)
            if other:
                cause, detail, ev = 'name-mismatch', 'defined as another decoration', ' '.join(other[:3])
        if cause is None and k and k[0]:
            hits = tbodies.get((k[0], k[1]), [])
            if hits:
                live = [h for h in hits if h[1].startswith('live')]
                if not live:
                    detail = 'under #if 0'
                elif all(h[0].endswith(('.cpp', '.c')) and h[0] not in units for h in live):
                    detail = 'in a source that is not a unit'
                else:
                    detail = 'in a class copy or overload the referencing unit does not emit'
                cause, ev = 'not-compiled', ' '.join(sorted({'%s(%s)' % h for h in hits})[:3])
        if cause is None:
            cause = 'no-body'
            if 'template or unnamed' in note:
                detail = 'template-special'
            elif 'MSVC calls it directly' in note:
                detail = 'msvc-direct'
            elif 'signature shape differs' in note or 'exe calls' in note:
                detail = 'exe-shape'
            else:
                detail = 'other'
        out.append(dict(r, cause=cause, detail=detail, evidence=ev))
    return out


def shard_of(unit):
    return int(hashlib.sha1(unit.encode('utf-8', 'surrogateescape')).hexdigest(), 16) % 10 if unit else ''


def load_closed(paths):
    names = set()
    for p in paths or ():
        text = open(p, encoding='utf-8', errors='surrogateescape').read()
        if text.lstrip().startswith('['):
            names |= set(json.loads(text))
        elif text.lstrip().startswith('{'):
            names |= set(json.loads(text))
        else:
            names |= {l.split('\t')[0].strip() for l in text.splitlines() if l.strip() and not l.startswith('#')}
    return names


def other_traps(out, traps, have, tree):
    """Trap names bodies.py never classified: their refs.tsv class (first row), the address, the unit placing it."""
    place = {}
    uj = os.path.join(tree, 'build', 'units.json')
    if os.path.exists(uj):
        for u in json.load(open(uj))['units']:
            for f in u.get('functions', ()):
                place.setdefault(int(f['address'], 16), u['source'])
    starts = sorted(place)
    rows, seen = [], set()
    refs = os.path.join(out, 'refs.tsv')
    want = traps - have
    if os.path.exists(refs):
        with open(refs, encoding='utf-8', errors='surrogateescape') as fh:
            for line in fh:
                f = line.rstrip('\n').split('\t')
                if len(f) < 5 or f[1] not in want or f[1] in seen:
                    continue
                seen.add(f[1])
                cls = f[2]
                cause = ('extern-text' if cls == 'extern-text' else
                         'thunk' if cls.startswith('special-unresolved') and 'thunk' in cls else cls)
                a = int(f[4], 16) if f[4] else None
                m = re.match(r'^(?:FUN|sub|thunk_FUN)_([0-9a-fA-F]{8})$', f[1])
                if a is None and m and cls == 'unresolved':   # a decomp placeholder named after its exe address
                    a, cause = int(m.group(1), 16), 'fun-addr'
                home = ''
                if a is not None:   # not placed by any unit: the unit of the placed function before it
                    j = bisect.bisect_right(starts, a) - 1
                    home = place.get(a) or (place[starts[j]] if j >= 0 else '')
                rows.append({'name': f[1], 'units': f[0].split('#')[0], 'cause': cause,
                             'detail': cls + (' %s' % f[3] if len(f) > 3 and f[3] else ''),
                             'evidence': '%08x' % a if a is not None else '', 'home': home})
    for n in sorted(want - seen):
        rows.append({'name': n, 'units': '', 'cause': 'unreferenced', 'detail': 'no refs.tsv row (synthesized '
                     'object or platform)', 'evidence': '', 'home': ''})
    for r, d in zip(rows, demangle([r['name'] for r in rows])):
        r['dem'] = d
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('out')
    ap.add_argument('tree')
    ap.add_argument('objdir')
    ap.add_argument('--traps')
    ap.add_argument('--closed', nargs='*')
    ap.add_argument('--counts')
    ap.add_argument('-o', '--output')
    a = ap.parse_args(argv)
    closed = load_closed(a.closed)
    rows = []
    with open(os.path.join(a.out, 'bodies', 'bodies.tsv'), encoding='utf-8', errors='surrogateescape') as fh:
        for line in fh:
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t') + [''] * 7
            if f[1] == 'missing':
                rows.append({'name': f[0], 'units': f[4], 'note': f[5]})
    for r, d in zip(rows, demangle([r['name'] for r in rows])):
        r['dem'] = d
        r['sig'] = sigs.parse_itanium(r['name'], d) if r['name'].startswith('_Z') else None
    traps = set()
    if a.traps:
        with open(a.traps, encoding='utf-8', errors='surrogateescape') as fh:
            traps = {l.strip() for l in fh if l.strip()}
    units = set()
    uj = os.path.join(a.tree, 'build', 'units.json')
    if os.path.exists(uj):
        with open(uj) as fh:
            units = {u['source'] for u in json.load(fh)['units']}
    wanted = {(k[0], k[1]) for k in (key_of(r['sig']) for r in rows) if k and k[0]}
    objidx = object_defs(a.objdir)
    tbodies = tree_bodies(a.tree, wanted)
    res = classify(rows, objidx, tbodies, units)
    for r in res:
        r['home'] = sorted(r['units'].split(','))[0] if r['units'] else ''
    if traps:
        res += other_traps(a.out, traps, {r['name'] for r in res}, a.tree)
    res = [r for r in res if r['name'] not in closed]
    for r in res:
        r['owner'] = 'recon' if r['cause'] in ('no-body', 'extern-text', 'fun-addr') else 's2-bodies'
        r['shard'] = shard_of(r['home'])
    dst = a.output or os.path.join(a.out, 'bodies', 'causes.tsv')
    with open(dst, 'w', encoding='utf-8', errors='surrogateescape') as fh:
        fh.write('#name\tdemangled\tcause\tdetail\tevidence\ttrap\tunits\towner\tshard\thome unit\n')
        for r in res:
            fh.write('\t'.join([r['name'], r['dem'], r['cause'], r['detail'], r['evidence'],
                                '1' if r['name'] in traps else ('0' if traps else ''), r['units'], r['owner'],
                                str(r['shard']), r['home']]) + '\n')
    tot = collections.Counter((r['cause'], r['detail'] if r['cause'] in CAUSES else '') for r in res)
    trp = collections.Counter((r['cause'], r['detail'] if r['cause'] in CAUSES else '') for r in res
                              if r['name'] in traps)
    lines = ['names: %d (trap stubs %s); closed offline and left out: %d' % (
        len(res), sum(trp.values()) if traps else '-', len(closed))]
    for c in sorted({k for k, _ in tot}, key=lambda c: (c not in CAUSES, CAUSES.index(c) if c in CAUSES else 0, c)):
        n = sum(v for (k, _), v in tot.items() if k == c)
        lines.append('%-14s %5d  trap %5s' % (c, n, sum(v for (k, _), v in trp.items() if k == c) if traps else '-'))
        for (k, d), v in sorted(tot.items(), key=lambda x: -x[1]):
            if k == c and d:
                lines.append('  %-60s %5d  trap %5s' % (d, v, trp.get((k, d), 0) if traps else '-'))
    print('\n'.join(lines))
    if a.counts:
        own = collections.Counter(r['owner'] for r in res if not traps or r['name'] in traps)
        sh = collections.Counter((r['shard'], r['cause']) for r in res if r['owner'] == 'recon'
                                 and (not traps or r['name'] in traps))
        with open(a.counts, 'w') as fh:
            fh.write('# Trap-stub list counts\n\n```\n%s\n```\n\n' % '\n'.join(lines))
            fh.write('| owner | trap stubs |\n|---|---|\n')
            for k, v in sorted(own.items()):
                fh.write('| %s | %d |\n' % (k, v))
            fh.write('\n| recon shard (sha1(home unit) mod 10) | no-body | extern-text + fun-addr | total |\n|---|---|---|---|\n')
            for k in range(10):
                nb, et = sh.get((k, 'no-body'), 0), sh.get((k, 'extern-text'), 0) + sh.get((k, 'fun-addr'), 0)
                fh.write('| R%d (shard %d) | %d | %d | %d |\n' % (k + 1, k, nb, et, nb + et))
            nh = sum(v for (k, _), v in sh.items() if k == '')
            if nh:
                fh.write('| no home unit | %d | | |\n' % nh)
    print('wrote', dst)


if __name__ == '__main__':
    main()
