#!/usr/bin/env python3
"""lemma.py - rule N2 (neutral lemma) gate: per function, is the prepared unit's MSVC code the pristine unit's code?

  lemma.py PRISTINE_OBJ_DIR PREPARED_OBJ_DIR UNITS.json --out DIR [--jobs N<=3]
  objects are <dir>/u<N>.obj or <dir>/obj/u<N>.obj, N = index in UNITS.json (one list for both sides).

Writes DIR/lemma.tsv (unit, source, function key, verdict, detail) and DIR/summary.json. Exit 0 whenever it ran;
non-zero only on unreadable input (units.json, or an object that is not a readable i386 COFF).

Own strict COFF reader (equiv.py reads objects through the decomp checkout's compare.Coff/compare_builds.fingerprint,
which compare 24-hex digests of resolved addresses and drop retargets of equal bytes: not used here).
A function is one COMDAT code section (key: its lowest-valued defined symbol, the decorated name) plus the sections
associated with it (xdata, sxdata; not .debug*); a non-COMDAT code section is split at its function symbols.
equal iff section flags/alignment, code bytes, and the relocation list (offset, type, target) are identical, for the
section and for each associated section (by name). A relocation target is: an undefined or external symbol -> its
name; a static or section symbol -> (content digest of the target section, symbol value), the digest covering its
bytes, flags and relocations recursively (a back edge to a section on the walk stack counts as its depth); an
absolute symbol -> its value. Not compared, because they hold no code: timestamp, symbol table order, section order,
.drectve, .debug$S/.debug$F, file-level non-code sections (plain .sxdata, .tls$, .data, .bss). No path
normalisation: a path in a compared section is a difference.
"""
import argparse
import hashlib
import json
import os
import struct
import sys
import time
from multiprocessing import Pool

CODE, COMDAT, ALIGN = 0x20, 0x1000, 0xF00000


class Coff:
    def __init__(self, path):
        b = open(path, 'rb').read()
        if len(b) < 20:
            raise ValueError('short file: ' + path)
        mach, nsec, _, symptr, nsym, opt, _ = struct.unpack_from('<HHIIIHH', b, 0)
        if mach != 0x14c:
            raise ValueError('not an i386 COFF object: ' + path)
        strtab = symptr + 18 * nsym

        def string(off):
            e = b.index(b'\0', strtab + off)
            return b[strtab + off:e].decode('latin-1')
        self.secs = []                       # dict(name, flags, data, relocs=[(off, symidx, type)])
        for i in range(nsec):
            o = 20 + opt + 40 * i
            raw = b[o:o + 8].rstrip(b'\0').decode('latin-1')
            name = string(int(raw[1:])) if raw.startswith('/') and raw[1:].isdigit() else raw
            _, _, size, ptr, relptr, _, nrel, _, flags = struct.unpack_from('<IIIIIIHHI', b, o + 8)
            data = None if flags & 0x80 else b[ptr:ptr + size]      # BSS: size only
            rel = [struct.unpack_from('<IIH', b, relptr + 10 * k) for k in range(nrel)]
            self.secs.append(dict(name=name, flags=flags, size=size, data=data,
                                  relocs=sorted((r[0], r[1], r[2]) for r in rel), assoc=None, sel=None))
        self.syms = []                       # (name, value, section, type, class) or None for aux records
        i = 0
        while i < nsym:
            o = symptr + 18 * i
            raw = b[o:o + 8]
            name = string(struct.unpack_from('<I', raw, 4)[0]) if raw[:4] == b'\0\0\0\0' else raw.rstrip(b'\0').decode('latin-1')
            value, secno, typ, cls, naux = struct.unpack_from('<IhHBB', b, o + 8)
            self.syms.append((name, value, secno, typ, cls))
            for k in range(naux):
                self.syms.append(None)
                if cls == 3 and typ == 0 and secno > 0 and k == 0 and self.secs[secno - 1]['sel'] is None:
                    num, sel = struct.unpack_from('<IHHIHB', b, o + 18)[4:]
                    if self.secs[secno - 1]['flags'] & COMDAT:
                        self.secs[secno - 1]['sel'] = sel
                        if sel == 5:
                            self.secs[secno - 1]['assoc'] = num - 1
            i += 1 + naux


class Digest:
    """Target-section content digests of one object (memoised when no back edge was involved)."""

    def __init__(self, coff):
        self.c, self.memo = coff, {}

    def target(self, symidx, stack, here):
        n, value, secno, typ, cls = self.c.syms[symidx]
        if secno == 0 or cls == 2 and secno <= 0:
            return ('N', n), 1 << 30
        if secno == -1:
            return ('A', value), 1 << 30
        if cls == 2 and self.c.secs[secno - 1]['flags'] & COMDAT and secno - 1 != here:
            return ('N', n), 1 << 30          # a defined external: by name (its own bytes are its own verdict)
        d, back = self.section(secno - 1, stack)
        return ('S', d, value), back

    def section(self, si, stack):
        if si in stack:
            return 'back%d' % stack.index(si), stack.index(si)
        if si in self.memo:
            return self.memo[si], 1 << 30
        s = self.c.secs[si]
        stack = stack + [si]
        back, rows = 1 << 30, []
        for off, ix, typ in s['relocs']:
            if self.c.syms[ix] is None:
                raise ValueError('relocation to an aux record')
            t, b = self.target(ix, stack, si)
            back = min(back, b)
            rows.append((off, typ, t))
        d = hashlib.sha1(repr((s['flags'], s['size'], s['data'], rows)).encode()).hexdigest()[:16]
        if back >= len(stack) - 1:
            self.memo[si] = d
            back = 1 << 30
        return d, back


def functions(path):
    """{key: ('canonical tuple', parts)} for the code of one object; parts = [(label, flags, data, relocs)] for diffing."""
    c = Coff(path)
    dg = Digest(c)
    defs = {}
    for ix, s in enumerate(c.syms):
        if s and s[2] > 0 and s[4] in (2, 3) and not (s[4] == 3 and s[3] == 0 and s[0].startswith('.')):
            defs.setdefault(s[2] - 1, []).append((s[1], s[0]))

    def part(si, lo, hi):
        s = c.secs[si]
        rows = []
        for off, ix, typ in s['relocs']:
            if lo <= off < hi:
                rows.append((off - lo, typ, dg.target(ix, [si], si)[0]))
        data = s['data'][lo:hi] if s['data'] is not None else s['size']
        return (s['name'], s['flags'] & ~0x0, data, rows)
    out = {}
    kids = {}
    for si, s in enumerate(c.secs):
        if s['assoc'] is not None:
            kids.setdefault(s['assoc'], []).append(si)

    def assoc(si):
        r = []
        for k in kids.get(si, []):
            if not c.secs[k]['name'].startswith('.debug'):
                r.append(part(k, 0, c.secs[k]['size']))
                r += assoc(k)
        return sorted(r, key=repr)
    for si, s in enumerate(c.secs):
        if not s['flags'] & CODE:
            continue
        syms = sorted(defs.get(si, []))
        if s['flags'] & COMDAT:
            key = syms[0][1] if syms else '%s#%d' % (s['name'], si)
            ranges = [(key, 0, s['size'])]
        else:       # plain code section: one function per symbol, up to the next one
            cuts = sorted({v for v, _ in syms} | {0})
            ranges = []
            for a, e in zip(cuts, cuts[1:] + [s['size']]):
                ranges.append(('%s+%s' % (s['name'], next((n for v, n in syms if v == a), '0x%x' % a)), a, e))
        for key, lo, hi in ranges:
            k, n = key, 1
            while k in out:
                n += 1
                k = '%s#%d' % (key, n)
            out[k] = [part(si, lo, hi)] + (assoc(si) if s['flags'] & COMDAT else [])
    return out


def first_diff(a, b):
    """('bytes'|'reloc'|'flags', offset, label) of the first difference of two part lists, or None."""
    if [p[0] for p in a] != [p[0] for p in b]:
        la, lb = [p[0] for p in a], [p[0] for p in b]
        return ('assoc', 0, 'sections pristine %s prepared %s' % (','.join(la[1:]) or '-', ','.join(lb[1:]) or '-'))
    for i, (p, q) in enumerate(zip(a, b)):
        tag = 'code' if i == 0 else 'assoc ' + p[0]
        if p[0] != q[0] or p[1] != q[1]:
            return ('flags', 0, tag)
        if p[2] != q[2]:
            if isinstance(p[2], int) or isinstance(q[2], int):
                return ('bytes', 0, tag + ' size')
            n = next((k for k in range(min(len(p[2]), len(q[2]))) if p[2][k] != q[2][k]), min(len(p[2]), len(q[2])))
            return ('bytes', n, tag)
        if p[3] != q[3]:
            n = next((x[0] for x, y in zip(p[3], q[3]) if x != y), None)
            if n is None:
                n = (p[3] if len(p[3]) > len(q[3]) else q[3])[min(len(p[3]), len(q[3]))][0]
            return ('reloc', n, tag)
    return None


def one(job):
    i, src, pa, pb = job
    rows, ua = [], None
    ok = lambda p: p is not None and os.path.exists(p)
    if not ok(pa) and not ok(pb):
        return i, 'neither', [], None
    if not ok(pa):
        return i, 'pristine-missing', [], None
    if not ok(pb):
        return i, 'prepared-missing', [], None
    try:
        fa, fb = functions(pa), functions(pb)
    except (ValueError, struct.error, IndexError) as e:
        return i, 'unreadable', [], '%s: %s' % (type(e).__name__, e)
    for k in sorted(set(fa) | set(fb)):
        if k not in fb:
            rows.append((k, 'only-pristine', ''))
        elif k not in fa:
            rows.append((k, 'only-prepared', ''))
        else:
            d = first_diff(fa[k], fb[k])
            rows.append((k, 'equal', '') if d is None else (k, 'differs', '%s@0x%x %s' % d))
    return i, 'both', rows, None


def objpath(d, i):
    for p in (os.path.join(d, 'u%d.obj' % i), os.path.join(d, 'obj', 'u%d.obj' % i)):
        if os.path.exists(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pristine')
    ap.add_argument('prepared')
    ap.add_argument('units')
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=3)
    a = ap.parse_args()
    t0 = time.time()
    try:
        u = json.load(open(a.units))
        u = u['units'] if isinstance(u, dict) else u
    except (OSError, ValueError, KeyError) as e:
        sys.exit('lemma: unreadable units.json: %s' % e)
    os.makedirs(a.out, exist_ok=True)
    jobs = [(i, x['source'], objpath(a.pristine, i), objpath(a.prepared, i)) for i, x in enumerate(u)]
    units = dict(both=0, **{'prepared-missing': 0, 'pristine-missing': 0, 'neither': 0})
    fn = {k: 0 for k in ('equal', 'differs', 'only-pristine', 'only-prepared')}
    per, bad = {}, []
    with Pool(min(a.jobs, 3)) as pool, open(os.path.join(a.out, 'lemma.tsv'), 'w') as tsv:
        tsv.write('unit\tsource\tfunction\tverdict\tdetail\n')
        for i, st, rows, err in pool.imap(one, jobs, chunksize=4):      # imap: input order
            if st == 'unreadable':
                bad.append((i, err))
                continue
            units[st] = units.get(st, 0) + 1
            if st != 'both':
                tsv.write('%d\t%s\t-\t%s\t\n' % (i, jobs[i][1], st))
                continue
            c = {k: 0 for k in fn}
            for k, v, d in rows:
                tsv.write('%d\t%s\t%s\t%s\t%s\n' % (i, jobs[i][1], k, v, d))
                c[v] += 1
                fn[v] += 1
            per[i] = c
    kinds = {}
    for line in open(os.path.join(a.out, 'lemma.tsv')):
        p = line.rstrip('\n').split('\t')
        if len(p) > 4 and p[3] == 'differs':
            kinds[p[4].split('@')[0]] = kinds.get(p[4].split('@')[0], 0) + 1
    json.dump(dict(units=units, functions=fn, differs_by_kind=dict(sorted(kinds.items())), per_unit=per,
                   unreadable=bad, seconds=round(time.time() - t0, 1)),
              open(os.path.join(a.out, 'summary.json'), 'w'), indent=0, sort_keys=True)
    if bad:
        sys.exit('lemma: unreadable objects: %s' % bad[:5])


if __name__ == '__main__':
    main()
