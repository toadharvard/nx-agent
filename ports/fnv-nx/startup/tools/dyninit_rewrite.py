#!/usr/bin/env python3
"""dyninit_rewrite.py - one native function per original dynamic initializer (stream S10, docs/STARTUP.md).

MSVC compiled every file-scope object with a dynamic initializer to its own function (??__E, in .text$yc,
called from the CRT table __xc_a..__xc_z) and, for a non-trivial destructor, an atexit thunk (??__F, in
.text$yd) that ??__E registers. The decomp's generated units spell these as plain definitions, tagged with
the original addresses:

    // @fnv 0x00f35430          <- ??__E (dynamic initializer)
    // @fnv 0x00fc7a70          <- ??__F (atexit destructor), when the type has one
    GlobalObject_0040c150 g_011c3264(g_011c3264_arg0, g_011c3264_arg1);

GCC folds all of a unit's initializers into one _GLOBAL__sub_I function in .init_array, so their order
relative to other units is lost (the six generated units interleave in 1,187 runs), and nothing native sits
at the ??__E address. This rewrite gives each one its own extern "C" function named by the address, which
startup/tools/mkxctab.py collects into the initializer table and the guest runner calls in original order:

    extern GlobalObject_0040c150 g_011c3264;
    // @fnv 0x00fc7a70
    extern "C" void __fnvnx_dynexit_00fc7a70(void) { fnvnx_dyn_destroy(g_011c3264); }
    // @fnv 0x00f35430
    extern "C" void __fnvnx_dyninit_00f35430(void) { ::new ((fnvnx_dyn_tag *)(void *)__builtin_addressof(g_011c3264))
        GlobalObject_0040c150(g_011c3264_arg0, g_011c3264_arg1); atexit(__fnvnx_dynexit_00fc7a70); }

The object itself is no longer defined natively: it lives in the data image (D3) and every reference,
this unit's included, binds to its address (S2, data class). Copies of such definitions in flag-variant
units that do not own the address become plain extern declarations (no second construction).

Use as a prepare.py rule (`r_dyninit(path, text, stats)`, finds build/units.json above the source tree) or
standalone, in place on a prepared tree:

    dyninit_rewrite.py --units <tree>/build/units.json --tree <tree> [--report rewrite.tsv] [--dry-run]

Everything it writes is derived from the decomp: local output only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

MARKER = '// fnvnx S10 dyninit_rewrite: '
TAG = re.compile(r'^[ \t]*//[ \t]*@fnv[ \t]+(0x[0-9a-fA-F]+)[ \t]*$')
NAME_AT_END = re.compile(r'([A-Za-z_]\w*)\s*$')
# A placement form of our own (tag type) instead of <new>: some generated headers define the standard
# placement operator new themselves, and <new> would define it a second time.
PROLOGUE = (MARKER + 'one extern "C" function per original initializer (startup/tools/dyninit_rewrite.py)\n'
            'struct fnvnx_dyn_tag;\n'
            'inline void *operator new(decltype(sizeof 0), fnvnx_dyn_tag *p) noexcept { return p; }\n'
            'inline void operator delete(void *, fnvnx_dyn_tag *) noexcept {}\n'
            '#include <stdlib.h>\n'
            'template <class T> static inline void fnvnx_dyn_destroy(T &o) { o.~T(); }\n')


class Index:
    """Original addresses of ??__E / ??__F functions and the unit that defines each."""

    def __init__(self, units: dict):
        self.kind: dict[int, str] = {}
        self.owner: dict[int, str] = {}
        self.owners_of_e: set[str] = set()
        for u in units['units']:
            for f in u['functions']:
                a = int(f['address'], 16)
                s = f['symbol']
                k = 'E' if s.startswith('??__E') else 'F' if s.startswith('??__F') else 'O'
                self.kind.setdefault(a, k)
                self.owner.setdefault(a, u['source'])
                if k == 'E':
                    self.owners_of_e.add(u['source'])

    @classmethod
    def load(cls, path: str) -> 'Index':
        with open(path, encoding='utf-8') as f:
            return cls(json.load(f))


def split_decl(text: str):
    """'T x;' -> (T, x, None); 'T x(args);' -> (T, x, args); None if it is not a plain object definition."""
    t = text.strip()
    if t.startswith('FNVNX_KEEP '):
        t = t[len('FNVNX_KEEP '):].strip()
    if not t.endswith(';') or '{' in t or '=' in t or '[' in t:
        return None
    body = t[:-1].rstrip()
    args = None
    if body.endswith(')'):
        depth = 0
        for i in range(len(body) - 1, -1, -1):
            c = body[i]
            if c == ')':
                depth += 1
            elif c == '(':
                depth -= 1
                if depth == 0:
                    args = body[i + 1:-1].strip()
                    body = body[:i].rstrip()
                    break
        else:
            return None
        if not args:
            return None  # 'T x()' declares a function, never an object with an initializer
    m = NAME_AT_END.search(body)
    if not m:
        return None
    name, typ = m.group(1), body[:m.start()].strip()
    if not typ or typ.split()[0] in ('static', 'extern', 'typedef', 'thread_local', 'inline', 'constexpr'):
        return None
    if typ.endswith(('::', ',')) or '(' in typ:
        return None
    return typ, name, args


def rewrite(rel: str, text: str, index: Index, stats=None, report=None) -> str:
    """Rewrite the tagged initializer definitions of one source file (rel: path relative to the tree)."""
    if MARKER in text or '@fnv' not in text:
        return text
    lines = text.split('\n')
    out: list[str] = []
    changed = 0
    i = 0
    while i < len(lines):
        m = TAG.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        tags = []
        j = i
        while j < len(lines) and TAG.match(lines[j]):
            tags.append(int(TAG.match(lines[j]).group(1), 16))
            j += 1
        if index.kind.get(tags[0]) != 'E' or len(tags) > 2:
            out.extend(lines[i:j])
            i = j
            continue
        # the declaration: up to the first line ending with ';' (generated units use one line)
        k = j
        while k < len(lines) and k - j < 8 and not lines[k].rstrip().endswith(';'):
            k += 1
        decl_lines = lines[j:k + 1]
        parsed = split_decl(' '.join(decl_lines)) if k < len(lines) else None
        e = tags[0]
        f = tags[1] if len(tags) == 2 else None
        if f is not None and index.kind.get(f, 'F') != 'F':
            f = None
        if not parsed:
            if report is not None:
                report.append((e, f, rel, '', '', 'skipped: not a plain object definition'))
            out.extend(lines[i:k + 1])
            i = k + 1
            continue
        typ, name, args = parsed
        indent = re.match(r'[ \t]*', decl_lines[0]).group(0)
        owned = index.owner.get(e) == rel
        if not owned:
            # a copy (flag-variant unit): keep the line count, no second definition or construction
            for t in tags:
                out.append('%s// fnvnx S10: initializer 0x%08x of %s is defined by %s' % (
                    indent, t, name, index.owner.get(e, '?')))
            out.append('%sextern %s %s;' % (indent, typ, name))
            out.extend([''] * (len(decl_lines) - 1))
            if stats is not None:
                stats['XC1x'] += 1
            if report is not None:
                report.append((e, f, rel, name, typ, 'extern (owned by %s)' % index.owner.get(e, '?')))
        else:
            out.append('%sextern %s %s;' % (indent, typ, name))
            ctor = '%s(%s)' % (typ, args) if args is not None else typ
            reg = ''
            if f is not None:
                out.append('%s// @fnv 0x%08x' % (indent, f))
                out.append('%sextern "C" void __fnvnx_dynexit_%08x(void) { fnvnx_dyn_destroy(%s); }' % (
                    indent, f, name))
                reg = ' atexit(__fnvnx_dynexit_%08x);' % f
            out.append('%s// @fnv 0x%08x' % (indent, e))
            out.append('%sextern "C" void __fnvnx_dyninit_%08x(void) { ::new ((fnvnx_dyn_tag *)(void *)__builtin_addressof(%s)) %s;%s }'
                       % (indent, e, name, ctor, reg))
            if stats is not None:
                stats['XC1'] += 1
            if report is not None:
                report.append((e, f, rel, name, typ, 'init' + (' + exit' if f is not None else '')))
        changed += 1
        i = k + 1
    if not changed:
        return text
    return PROLOGUE + '\n'.join(out)


# ---- prepare.py rule ------------------------------------------------------------------------------------

_INDEX_CACHE: dict[str, Index] = {}


def _find_units(path: str):
    d = os.path.dirname(os.path.abspath(path))
    while True:
        cand = os.path.join(d, 'build', 'units.json')
        if os.path.exists(cand):
            return cand, d
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def r_dyninit(path, text, stats):
    """XC1: per-initializer extern "C" functions for the generated ??__E units (docs/STARTUP.md)."""
    if '@fnv' not in text or 'generated' not in path:
        return text
    units, root = _find_units(path)
    if not units:
        return text
    if units not in _INDEX_CACHE:
        _INDEX_CACHE[units] = Index.load(units)
    rel = os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/')
    return rewrite(rel, text, _INDEX_CACHE[units], stats)


RULES = [r_dyninit]


# ---- CLI ------------------------------------------------------------------------------------------------

def candidate_files(tree: str, index: Index):
    """The units that own ??__E functions, and the other sources in their directories (flag variants)."""
    dirs = sorted({os.path.dirname(s) for s in index.owners_of_e})
    for d in dirs:
        full = os.path.join(tree, d)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            if name.endswith(('.cpp', '.cc', '.cxx')):
                yield (d + '/' + name) if d else name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--units', required=True, help='build/units.json of the tree')
    ap.add_argument('--tree', required=True, help='prepared tree (rewritten in place)')
    ap.add_argument('--report', help='write one TSV line per initializer: e, f, unit, object, type, action')
    ap.add_argument('--dry-run', action='store_true', help='do not write the sources')
    a = ap.parse_args(argv)
    index = Index.load(a.units)
    report: list = []
    from collections import Counter
    stats: Counter = Counter()
    files = 0
    for rel in candidate_files(a.tree, index):
        p = os.path.join(a.tree, rel)
        with open(p, encoding='utf-8', errors='surrogateescape') as f:
            text = f.read()
        new = rewrite(rel, text, index, stats, report)
        if new != text:
            files += 1
            if not a.dry_run:
                with open(p, 'w', encoding='utf-8', errors='surrogateescape') as f:
                    f.write(new)
    if a.report:
        with open(a.report, 'w', encoding='utf-8') as f:
            f.write('# e\tf\tunit\tobject\ttype\taction\n')
            for e, fa, rel, name, typ, action in sorted(report):
                f.write('0x%08x\t%s\t%s\t%s\t%s\t%s\n' % (e, '0x%08x' % fa if fa is not None else '-', rel, name,
                                                          typ, action))
    skipped = sum(1 for r in report if r[5].startswith('skipped'))
    e_total = sum(1 for k in index.kind.values() if k == 'E')
    print('dyninit_rewrite: %d files, %d initializers (%d with exit), %d extern copies, %d skipped; units.json '
          'defines %d ??__E' % (files, stats['XC1'], sum(1 for r in report if r[5] == 'init + exit'),
                                stats['XC1x'], skipped, e_total))
    return 0


if __name__ == '__main__':
    sys.exit(main())
