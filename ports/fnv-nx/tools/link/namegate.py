#!/usr/bin/env python3
"""namegate.py - NAMES1 gate: the MSVC names the fnvnx_names plugin computed against cl's own symbol tables.

    python3 tools/link/namegate.py <compile.sh out dir> <tree>/build/units.json <cl object dir> [--out gate.json]
                                   [--examples N]

<cl object dir> holds u<N>.obj, the COFF object MSVC 2008 wrote for unit N of the SAME prepared tree (tools/equiv's
prepared side). A unit takes part when both compilers built it; the others are counted, not judged.

What is compared, per unit (rows of .fnvnx.sympair / .fnvnx.vtpair, toolchain/names/fnvnx_names.cc):
  definitions   a function or object GCC's object defines outside a COMDAT is defined by the unit's own text, so cl's
                object defines it too (every unit is built /Gy: cl puts each function in a COMDAT of its own, so
                `defined` is cl's def or comdat). Its name must be a definition of cl's object. This set is the gate:
                a wrong name here is a wrong rule.
  the rest      an inline function, a vftable or a reference is in an object only if its compiler chose to emit or
                use it there (cl writes a class's vftable and virtual functions only where a constructor or
                destructor is compiled; GCC wherever the class is used), so a name cl's object lacks proves
                nothing. They are counted by where cl knows the name - same unit, another unit, no cl object -
                as coverage of the same naming rules; `no cl object` splits into the names with C linkage (the
                target's own shim and runtime functions, no twin in the exe) and the C++ ones.
  unnamed rows  counted by reason (no rule names them); the gate wants 0.

Left out by class: cl's string literals (??_C@), RTTI records (??_R), vbtables (??_8), floating constants (__real@,
__xmm@), CRT / helper symbols (__, $, ??_L ??_M ??_H ??_I), names in an unnamed namespace (?A0x: internal for GCC).

The gate file is fnvnx-gate/1: status pass only with 0 unnamed rows and 0 definitions whose name is not cl's.
"""
import argparse
import collections
import datetime
import json
import os
import struct
import subprocess
import sys

OBJCOPY = os.environ.get('OBJCOPY', 'aarch64-linux-gnu-objcopy')
NM = os.environ.get('NM', 'aarch64-linux-gnu-nm')


def coff_symbols(path):
    """{name: 'def' | 'comdat' | 'undef' | 'common' | 'weak'} of a COFF object's external symbols"""
    b = open(path, 'rb').read()
    _, nsec, _, symoff, nsym, opt, _ = struct.unpack_from('<HHIIIHH', b, 0)
    stroff = symoff + 18 * nsym
    chars = [struct.unpack_from('<I', b, 20 + opt + 40 * i + 36)[0] for i in range(nsec)]
    out, i = {}, 0
    while i < nsym:
        o = symoff + 18 * i
        raw = b[o:o + 8]
        val, sec, _, cls, naux = struct.unpack_from('<IhHBB', b, o + 8)
        if raw[:4] == b'\0\0\0\0':
            so = stroff + struct.unpack_from('<I', raw, 4)[0]
            name = b[so:b.index(b'\0', so)].decode('latin-1')
        else:
            name = raw.rstrip(b'\0').decode('latin-1')
        if cls == 2:
            kind = ('undef' if val == 0 else 'common') if sec == 0 else 'abs' if sec < 0 else \
                'comdat' if chars[sec - 1] & 0x1000 else 'def'
            if kind != 'undef' or name not in out:
                out[name] = kind
        elif cls == 105:
            out.setdefault(name, 'weak')
        i += 1 + naux
    return out


def cl_left_out(n):
    return n.startswith(('??_C@', '??_R', '??_8', '__', '$', '@__', '??_L@', '??_M@', '??_H@', '??_I@')) or '?A0x' in n


def section(obj, name):
    """the rows of a plugin section; none when the object's section table has no such section (a unit with nothing
    for that plugin to write). Dumped to a file of its own (a dump to /dev/stdout is empty where stdout is a pipe
    objcopy cannot reopen); a section that is there and cannot be read is an error, never an empty list."""
    import tempfile
    h = subprocess.run([OBJCOPY.replace('objcopy', 'readelf'), '-SW', obj], capture_output=True, text=True)
    if h.returncode != 0:
        sys.exit('%s: cannot read the section table of %s' % (os.path.basename(sys.argv[0]), obj))
    if not any(name in l.split() for l in h.stdout.splitlines()):
        return []
    with tempfile.TemporaryDirectory() as d:
        sec = os.path.join(d, 's')
        r = subprocess.run([OBJCOPY, '--dump-section', '%s=%s' % (name, sec), obj, os.path.join(d, 'o')], capture_output=True)
        if r.returncode != 0 or not os.path.isfile(sec):
            sys.exit('%s: cannot read section %s of %s: %s' % (os.path.basename(sys.argv[0]), name, obj, r.stderr.decode('latin-1').strip()[:200]))
        return [l.split('\t') for l in open(sec, 'rb').read().decode('latin-1').splitlines()]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('objdir'); ap.add_argument('units'); ap.add_argument('cldir')
    ap.add_argument('--out'); ap.add_argument('--examples', type=int, default=8)
    a = ap.parse_args(argv)
    units = [u['source'] for u in json.load(open(a.units))['units']]
    index = {s: i for i, s in enumerate(units)}
    tot = collections.Counter(); ex = collections.defaultdict(list)
    every = set()
    cl = {}
    for f in os.listdir(a.cldir):
        if f.startswith('u') and f.endswith('.obj'):
            cl[int(f[1:-4])] = coff_symbols(os.path.join(a.cldir, f))
            every.update(cl[int(f[1:-4])])
    for line in open(os.path.join(a.objdir, 'objects.tsv')):
        src, obj, status = line.rstrip('\n').split('\t')[:3]
        if status != 'ok':
            tot['units GCC did not build'] += 1; continue
        if index.get(src) not in cl:
            tot['units without a cl object of this tree'] += 1; continue
        tot['units compared'] += 1
        M = cl[index[src]]
        strong = set()
        for l in subprocess.run([NM, '-g', '--defined-only', obj], capture_output=True, text=True).stdout.splitlines():
            p = l.split()
            if p[-2] in 'TDBR': strong.add(p[-1])
        got = set()
        for sym, ms, kind, df in section(obj, '.fnvnx.sympair'):
            tot['rows'] += 1
            if kind.startswith('unnamed'):
                tot['unnamed rows'] += 1; tot[kind] += 1; ex[kind].append((src, sym)); continue
            if kind in ('gcc', 'vtable', 'thunk', 'internal'):
                tot['rows without a cl twin by rule (%s)' % kind] += 1; continue
            got.add(ms)
            if sym in strong:
                if M.get(ms) in ('def', 'comdat', 'common'): tot['definitions: name equal to cl\'s'] += 1
                else:
                    tot['definitions: name is not a definition of cl\'s object'] += 1
                    ex['definitions: name not a definition of cl\'s object'].append((src, sym, ms, M.get(ms, '-')))
            else:
                where = 'same unit' if ms in M else 'another unit' if ms in every else \
                    'no cl object (C++ name)' if ms.startswith('?') else 'no cl object (C linkage: shim, runtime)'
                tot['inline / referenced names, cl has it in: ' + where] += 1
                if where == 'no cl object (C++ name)': ex['inline / referenced: C++ name in no cl object'].append((src, sym, ms))
        defined = {l.split()[-1] for l in subprocess.run([NM, '-g', '--defined-only', obj], capture_output=True, text=True).stdout.splitlines()}
        for sym, apo, ms, off in section(obj, '.fnvnx.vtpair'):
            if '%s.fnvnx_ap%s' % (sym, apo) not in defined and sym not in defined: continue
            if ms == '': tot['vftables: table of a class without virtual functions (no cl twin)'] += 1
            elif ms.startswith('unnamed'): tot['unnamed rows'] += 1; tot['vftables: ' + ms] += 1; ex['vftables: ' + ms].append((src, sym, apo))
            else: tot['vftables, cl has it in: ' + ('same unit' if ms in M else 'another unit' if ms in every else 'no cl object')] += 1
    bad = tot['unnamed rows'] + tot['definitions: name is not a definition of cl\'s object']
    status = 'pass' if tot['units compared'] and not bad else 'fail'
    for k, v in sorted(tot.items()): print('%8d  %s' % (v, k))
    for k, v in sorted(ex.items()):
        print('--- %s (%d)' % (k, len(v)))
        for e in v[:a.examples]: print('    ' + '  '.join(str(x)[:150] for x in e))
    print('namegate: %s' % status)
    if a.out:
        with open(a.out, 'w') as f:
            json.dump({'format': 'fnvnx-gate/1', 'gate': 'L2-names', 'status': status,
                       'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                       'counts': dict(sorted(tot.items())),
                       'examples': {k: v[:50] for k, v in sorted(ex.items())}}, f, indent=1)
            f.write('\n')
    return 0 if status == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
