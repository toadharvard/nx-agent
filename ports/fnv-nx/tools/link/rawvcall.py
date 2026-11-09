#!/usr/bin/env python3
"""rawvcall.py - the raw-vcall check (S1-rules, orchestrator 2026-11-09): every indirect call whose callee is loaded
from (the first word of an object X) + a constant, with X passed as the first argument, is a call through X's
vtable by a raw slot - whatever the source spelling ((*(int**)x)[k], (char*)*(void**)x + off, x->vtbl[k], ...). It
is found in GCC's SSA dump of the unit (-O1 -fdump-tree-ssa-lineno), not in the text. A declared virtual call is an
OBJ_TYPE_REF there and is not counted. Such a call is covered when its slot offset is computed at run time (the m1
test of fnvnx_rawvt.h: FNVNX_RAWVT_DYN / FNVNX_RAWVT_DYNOFF) or when its source line carries a prepare rule's decision
(FNVNX_D26_SLOT, FNVNX_D22_CALL, `fnv-nx RAWVT1-KEEP <reason>`: COM, exe-order, slot <= K). Anything else is a raw
vcall not under the m1 test: the layout gate's rawvcall rows.

    python3 tools/link/rawvcall.py --tree <prepared tree> <unit> ...
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layout   # noqa: E402

_STMT = re.compile(r'^\s*\[([^\]]+):(\d+):\d+\]\s*(.*?);?\s*$')
_FUNC = re.compile(r'^;; Function (.+?) \(')
COVERED = re.compile(r'FNVNX_(?:D26_SLOT|D22_CALL|RAWVT_DYN|RAWVT_DYNOFF|RAWVT_DYNK|RAWVT_DEL0|VSLOT)|fnv-nx RAWVT1-KEEP|fnv-nx VTM1')


def _strip_loc(s):
    return re.sub(r'\[[^\]]*:\d+:\d+\]\s*', '', s)


def scan_dump(path):
    """[(function, source file, line, offset or None (run time), first arg)] of the raw vcalls in an SSA dump"""
    out, fn, defs = [], None, {}
    for raw in open(path, errors='replace'):
        fm = _FUNC.match(raw)
        if fm:
            fn, defs = fm.group(1), {}
            continue
        m = _STMT.match(raw)
        if not m or fn is None:
            continue
        src, line, st = m.group(1), int(m.group(2)), _strip_loc(m.group(3))
        a = re.match(r'(_\d+|\w+_\d+)\s*=\s*(.+)$', st)
        if a:
            defs[a.group(1)] = a.group(2).strip()
            continue
        c = re.match(r'(?:\w+\s*=\s*)?(_\d+)\s*\((.*)\)$', st)          # an indirect call through an SSA name
        if not c:
            continue
        callee, args = c.group(1), [x.strip() for x in c.group(2).split(',') if x.strip()]
        if not args:
            continue
        hit = _vslot(defs, callee)
        if hit is None:
            continue
        obj, off = hit
        if _canon(defs, args[0]) != _canon(defs, obj):
            continue
        out.append((fn, src, line, off, args[0]))
    return out


def _base(defs, v):
    """v through casts and copies"""
    for _ in range(8):
        d = defs.get(v)
        if d is None:
            return v
        m = re.fullmatch(r'\(.*\)\s*(\w+(?:\(D\))?)', d) or re.fullmatch(r'(\w+(?:\(D\))?)', d)
        if not m:
            return v
        v = m.group(1)
    return v


def _canon(defs, v, depth=0):
    """v as an expression of the function's parameters (two loads of the same place compare equal)"""
    v = _base(defs, v)
    d = defs.get(v)
    if d is None or depth > 8:
        return re.sub(r'_\d+(\(D\))?$', '', v) if re.match(r'\w+_\d+\(D\)$', v) else v
    m = re.fullmatch(r'MEM\[\([^)]*\)\s*(\w+(?:\(D\))?)(?:\s*\+\s*(\d+)B)?\]', d)
    if m:
        return '*(%s+%s)' % (_canon(defs, m.group(1), depth + 1), m.group(2) or 0)
    m = re.fullmatch(r'\*(\w+(?:\(D\))?)', d)
    if m:
        return '*(%s+0)' % _canon(defs, m.group(1), depth + 1)
    m = re.fullmatch(r'(\w+(?:\(D\))?)\s*\+\s*(\d+)', d)
    if m:
        return '%s+%s' % (_canon(defs, m.group(1), depth + 1), m.group(2))
    return d


def _first_word(defs, v):
    """the object X when v = *X / MEM[(T *)X] (the first word of X), else None"""
    d = defs.get(_base(defs, v))
    if d is None:
        return None
    m = re.fullmatch(r'MEM\[\([^)]*\)\s*(\w+(?:\(D\))?)\]', d) or re.fullmatch(r'\*(\w+(?:\(D\))?)', d) or \
        re.fullmatch(r'(\w+(?:\(D\))?)->(?:m_ppVirtualMethodTable|vtable|vtbl|m_vtbl|m_pVtbl|vptr|_vtable|vt|vftable|methods)', d) or \
        re.fullmatch(r'MEM\[\([^)]*\)\s*(\w+(?:\(D\))?)\]\.(?:m_ppVirtualMethodTable|vtable|vtbl|m_vtbl|m_pVtbl|vptr|_vtable|vt|vftable|methods)', d)
    return m.group(1) if m else None


def _vslot(defs, callee):
    """(X, byte offset or None for a run-time offset) when callee = *(*(X) + off), else None"""
    d = defs.get(_base(defs, callee))
    if d is None:
        return None
    m = re.fullmatch(r'(\w+)->\w+', d)                         # a field of a vftable-model struct (vt->f2)
    if m:
        x = _first_word(defs, m.group(1))
        return (x, 'field') if x else None
    m = re.fullmatch(r'MEM\[\([^)]*\)\s*(\w+)\s*\+\s*(\d+)B\]', d)
    if m:
        x = _first_word(defs, m.group(1))
        return (x, int(m.group(2))) if x else None
    m = re.fullmatch(r'\*(\w+)', d) or re.fullmatch(r'MEM\[\([^)]*\)\s*(\w+)\]', d)
    if not m:
        return None
    p = defs.get(_base(defs, m.group(1)), '')       # through a cast: (void (**)(void *)) _2, _2 = _1 + 188
    q = re.fullmatch(r'\(.*\)\s*(\w+)', p)
    while q and q.group(1) in defs:
        p = defs[q.group(1)]
        q = re.fullmatch(r'\(.*\)\s*(\w+)', p)
    x0 = _first_word(defs, m.group(1))
    if x0:
        return (x0, 0)
    q = re.fullmatch(r'(\w+)\s*\+\s*(\d+)', p)
    if q:
        x = _first_word(defs, q.group(1)) or _first_word(defs, _base(defs, q.group(1)))
        return (x, int(q.group(2))) if x else None
    q = re.fullmatch(r'(\w+)\s*\+\s*(\w+)', p)
    if q:
        x = _first_word(defs, q.group(1))
        return (x, None) if x else None
    return None


def check_unit(tree, unit, workdir):
    """[(unit, function, line, offset, status)] for one unit; status raw / covered"""
    d = tempfile.mkdtemp(dir=workdir)
    fl = [f for f in layout.flags(tree) if f not in ('-O2', '-O3')] + ['-O1', '-fdump-tree-ssa-lineno', '-w']
    r = subprocess.run([layout.CXX, *fl, '-dumpdir', d + '/', '-c', '-o', os.path.join(d, 'u.o'),
                        os.path.join(tree, unit)], cwd=tree, capture_output=True, text=True, errors='replace')
    dumps = [os.path.join(d, f) for f in os.listdir(d) if f.endswith('.ssa')]
    if r.returncode or not dumps:
        return [(unit, '-', 0, '-', 'compile-failed')]
    lines = {}
    rows = []
    for fn, src, line0, off, _ in scan_dump(dumps[0]):
        rel = os.path.relpath(src, tree) if os.path.isabs(src) else src
        line_id = line0 if rel == unit else '%s:%d' % (rel, line0)     # a call in a header: its file and line
        line = line0
        if off is None or os.path.basename(src) == 'fnvnx_rawvt.h':
            # a run-time offset, or the m1 helper's own call (fnvnx_rawvt_del0's D0 / vp[0](o, flag))
            rows.append((unit, fn, line_id, 'dyn' if off is None else off, 'covered')); continue
        if off == 0:
            # slot 0 (the exe's deleting destructor ??_G(flags)): covered by FNVNX_RAWVT_DEL0 (flag 1 -> D0); else raw
            pass
        if src not in lines:
            try:
                lines[src] = open(src if os.path.isabs(src) else os.path.join(tree, src), errors='replace').read().splitlines()
            except OSError:
                lines[src] = []
        text = '\n'.join(lines[src][max(0, line - 3):line + 1])
        if off == 'field':
            # a vftable-model struct's field: VTM1 padded the struct (the unit carries FNVNX_VTM1_PAD); a COM-shaped
            # model (QueryInterface / AddRef / Release / __stdcall members, a D3D call) keeps its slots: ok-com
            whole = '\n'.join(lines[src])
            call = lines[src][line - 1] if 0 < line <= len(lines[src]) else ''
            fm = re.search(r'->\s*(\w+)\s*\(', call)
            if fm:
                sd = re.search(r'struct\s+\w+\s*\{[^{}]*\b%s\b[^{}]*\}' % re.escape(fm.group(1)), whole)
                if fm.group(1) in ('QueryInterface', 'AddRef', 'Release') or \
                        (sd and re.search(r'QueryInterface|AddRef|Release|__stdcall|STDMETHOD|HRESULT', sd.group(0))):
                    rows.append((unit, fn, line_id, off, 'ok-com')); continue
            rows.append((unit, fn, line_id, off, 'covered' if 'FNVNX_VTM1_PAD' in whole or COVERED.search(text) else 'raw'))
            continue
        rows.append((unit, fn, line_id, off, 'covered' if COVERED.search(text) else 'raw'))
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    os.rmdir(d)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', required=True)
    ap.add_argument('--sites', help='write the raw rows as a rawvt_ssa.tsv site list (RAWVT1-SSA) here')
    ap.add_argument('units', nargs='+')
    a = ap.parse_args()
    sites = []
    wd = tempfile.mkdtemp()
    n = 0
    for u in a.units:
        for r in check_unit(os.path.abspath(a.tree), u, wd):
            print('\t'.join(str(x) for x in r))
            n += r[4] == 'raw'
            if r[4] == 'raw':
                f, ln = (str(r[2]).rsplit(':', 1) + [None])[:2] if ':' in str(r[2]) else (u, r[2])
                try:
                    t = open(os.path.join(a.tree, f), errors='replace').read().splitlines()[int(ln) - 1]
                    sites.append((f, ' '.join(t.split()), r[3]))
                except (OSError, IndexError, ValueError):
                    pass
    if a.sites:
        with open(a.sites, 'w') as fh:
            fh.write('# fnvnx-rawvt-ssa/1\tunit\tsource line (whitespace collapsed)\tbyte offset or field\n')
            for s in sorted(set(sites)):
                fh.write('%s\t%s\t%s\n' % s)
    print('raw vcalls not under the m1 test: %d' % n, file=sys.stderr)


if __name__ == '__main__':
    main()
