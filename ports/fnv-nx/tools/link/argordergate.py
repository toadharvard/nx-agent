#!/usr/bin/env python3
"""argordergate.py - the lemma of rule ORDER-ARGS against cl's own code.

    python3 tools/link/argordergate.py <compile.sh out dir> <tree>/build/units.json <cl object dir> [--out gate.json]
                                       [--examples N]

MSVC 2008 x86 evaluates the arguments of a call from the last to the first and the object of a member call after
them; the fnvnx_order plugin makes GCC do the same, and the fnvnx_pi plugin writes every call whose arguments have
effects in two or more of them to section .fnvnx.argorder of the object:

    function <TAB> callee <TAB> a0|a1|... <TAB> k/n     a_i: the callees of the calls inside argument i (a member
                                                         call's object is a_0); the call is number k of the
                                                         function's n calls of that callee, in source order

This gate reads the same calls out of cl's object of the same unit (u<N>.obj of the same prepared tree, as
tools/link/namegate.py): the function's COMDAT section, its call relocations in address order. When cl's code has
the same n calls of the callee, it takes call number k and the calls before it back to the previous call of the
callee, and checks that the callees of a later argument all come before the callees of an earlier one. Names are
paired by .fnvnx.names (every declaration the unit named, also those GCC inlined).

  confirmed      cl's order is the rule's
  contradicted   cl calls an earlier argument's callee before a later argument's: the rule is wrong for this shape
  not readable   cl's object does not show the site as written (it inlined a callee, the function has no code of its
                 own in the object, a name has no cl twin, the k-th call is missing): proves nothing either way

The gate file (fnvnx-gate/1) passes with 0 contradicted and at least one confirmed site. The rule decides the order
for every site; this run only checks it where an object can show it.
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


class Coff:
    """sections, external symbols and relocations of an i386 COFF object"""
    def __init__(self, path):
        b = self.b = open(path, 'rb').read()
        _, nsec, _, symoff, nsym, opt, _ = struct.unpack_from('<HHIIIHH', b, 0)
        stroff = symoff + 18 * nsym
        self.names, i = {}, 0
        self.defs = {}                          # name -> (section number, value)
        while i < nsym:
            o = symoff + 18 * i
            raw = b[o:o + 8]
            val, sec, _, cls, naux = struct.unpack_from('<IhHBB', b, o + 8)
            if raw[:4] == b'\0\0\0\0':
                so = stroff + struct.unpack_from('<I', raw, 4)[0]
                name = b[so:b.index(b'\0', so)].decode('latin-1')
            else:
                name = raw.rstrip(b'\0').decode('latin-1')
            self.names[i] = name
            if cls in (2, 3) and sec > 0 and not name.startswith('.'):
                self.defs.setdefault(name, (sec, val))
            i += 1 + naux
        self.relocs = {}                        # section number -> [(offset, symbol name, type)]
        for k in range(nsec):
            o = 20 + opt + 40 * k
            relptr, _, nrel = struct.unpack_from('<IIH', b, o + 24)
            rs = []
            for r in range(nrel):
                va, si, ty = struct.unpack_from('<IIH', b, relptr + 10 * r)
                rs.append((va, self.names.get(si, '?'), ty))
            self.relocs[k + 1] = sorted(rs)

    def calls(self, fn):
        """the symbols the function's own section refers to by a relative relocation (calls and jumps), in address
        order from the function's start; None when the object has no code of its own for it"""
        if fn not in self.defs: return None
        sec, val = self.defs[fn]
        return [n for off, n, ty in self.relocs.get(sec, []) if ty == 0x14 and off >= val]


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


def judge(seq, k, n, callee, args):
    """seq: cl's calls of the function; call number k of its n calls of callee and the calls before it"""
    at = [i for i, x in enumerate(seq) if x == callee]
    if len(at) != n: return 'not readable', 'cl\'s code has another number of calls of the callee than the source'
    lo = at[k - 1] + 1 if k else 0
    window = seq[lo:at[k]]
    pos = []
    for a in args:
        ps = []
        for c in a:
            if window.count(c) != 1: return 'not readable', 'a callee of an argument is not once before the call'
            ps.append(window.index(c))
        pos.append(ps)
    # a later argument's calls all come before an earlier argument's
    for i in range(len(pos)):
        for j in range(i + 1, len(pos)):
            if pos[i] and pos[j] and max(pos[j]) > min(pos[i]):
                return 'contradicted', 'argument %d is evaluated before argument %d' % (i, j)
    return 'confirmed', ''


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('objdir'); ap.add_argument('units'); ap.add_argument('cldir')
    ap.add_argument('--out'); ap.add_argument('--examples', type=int, default=8)
    a = ap.parse_args(argv)
    index = {u['source']: i for i, u in enumerate(json.load(open(a.units))['units'])}
    tot = collections.Counter(); ex = collections.defaultdict(list)
    for line in open(os.path.join(a.objdir, 'objects.tsv')):
        src, obj, status = line.rstrip('\n').split('\t')[:3]
        clo = os.path.join(a.cldir, 'u%s.obj' % index.get(src))
        if status != 'ok' or not os.path.exists(clo): continue
        rows = [r for r in section(obj, '.fnvnx.argorder') if len(r) == 4]
        if not rows: continue
        pair = {r[0]: r[1] for r in section(obj, '.fnvnx.sympair') if len(r) == 4 and r[1]}
        pair.update({r[0]: r[1] for r in section(obj, '.fnvnx.names') if len(r) == 2 and r[1]})
        coff = Coff(clo)
        tot['units with sites'] += 1
        for fn, callee, argtext, kn in rows:
            k, n = (int(x) for x in kn.split('/'))
            tot['sites'] += 1
            args = [[c for c in x.split(',') if c] for x in argtext.split('|')]
            if fn not in pair:
                tot['not readable: the function has no cl twin (internal, or the shim\'s own)'] += 1; continue
            names = [callee] + [c for x in args for c in x]
            if any(n not in pair for n in names):
                tot['not readable: a callee has no cl twin'] += 1; continue
            seq = coff.calls(pair[fn])
            if seq is None:
                tot['not readable: cl\'s object has no code of the function'] += 1; continue
            res, why = judge(seq, k, n, pair[callee], [[pair[c] for c in x] for x in args])
            if res == 'not readable': tot['not readable: ' + why.split(',')[0]] += 1
            else: tot[res] += 1
            if res == 'contradicted': ex['contradicted'].append((src, fn, callee, argtext, why))
    status = 'pass' if tot['confirmed'] and not tot['contradicted'] else 'fail'
    for k, v in sorted(tot.items()): print('%8d  %s' % (v, k))
    for k, v in sorted(ex.items()):
        print('--- %s (%d)' % (k, len(v)))
        for e in v[:a.examples]: print('    ' + '  '.join(str(x)[:140] for x in e))
    print('argordergate: %s' % status)
    if a.out:
        with open(a.out, 'w') as f:
            json.dump({'format': 'fnvnx-gate/1', 'gate': 'L2-argorder', 'status': status,
                       'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                       'counts': dict(sorted(tot.items())), 'examples': {k: v[:50] for k, v in ex.items()}}, f, indent=1)
            f.write('\n')
    return 0 if status == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
