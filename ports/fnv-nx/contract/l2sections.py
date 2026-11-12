#!/usr/bin/env python3
"""l2sections - the three non-loaded ELF sections that L2's compiler plugins put into every game object (L1 CONTRACT).

    parse_sympair(data, where) -> [(symbol, msvc_name, kind, 'def'|'ref')]            .fnvnx.sympair (plugin NAMES1)
    parse_vtpair(data, where)  -> [(ztv symbol, address point, ??_7 name, vfptr offset)]   .fnvnx.vtpair (NAMES1)
    parse_n3(data, where)      -> [(symbol, verdict, (inlined symbols), kind, (types))]   .fnvnx.n3 (plugin pi, sdd)
    read(path)                 -> {'sympair': [...], 'vtpair': [...], 'n3': [...]}; an absent section gives []

Format: plain text, LF-terminated lines, TAB-separated.  sympair: exactly 4 fields, kind one of KINDS or
`unnamed:<reason>` (reason not empty); the name may be empty only for the kinds in NAMELESS.  vtpair: exactly 4
fields, offsets decimal.  n3: 2..5 fields (the producer omits trailing empty ones), verdict not empty, inlined a
`,`-separated list of symbols, kind one of N3_KINDS or a clone suffix (not empty, no blank), types `,`-separated
Itanium type strings (the producer's own separator; the task brief said `;`).  Any other line is an error
`<where>: line N: <what>`; no line is skipped, a final line without LF is an error, an empty section is [].
Pure: bytes in, tuples out (rows keep the section's order).
"""
import os, sys

KINDS = frozenset(('func', 'data', 'ctor.c1', 'ctor.c2', 'dtor.d2', 'dtor.d1', 'dtor.d0', 'dtor.sdd', 'dtor.sdd.thunk',
                   'thunk', 'vtable', 'gcc', 'internal'))     # `vtable`: NAMES1 writes it for the table groups
NAMELESS = frozenset(('thunk', 'vtable', 'gcc', 'internal'))
N3_KINDS = frozenset(('', 'thunk', 'inline', 'sdd', 'alias'))


def _lines(data, where):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError('%s: line 1: not UTF-8 text (%s)' % (where, e))
    if text and text[-1] != '\n':
        raise ValueError('%s: line %d: no final LF' % (where, text.count('\n') + 1))
    return text.split('\n')[:-1]


def _int(s, where, n, what):
    if not s or not (s[1:] if s[0] == '-' else s).isdigit() or not s.isascii():
        raise ValueError('%s: line %d: %s is not a decimal number: %r' % (where, n, what, s))
    return int(s)


def _fields(ln, where, n, lo, hi):
    f = ln.split('\t')
    if not lo <= len(f) <= hi:
        raise ValueError('%s: line %d: %d fields, expected %s' % (where, n, len(f), str(lo) if lo == hi else '%d..%d' % (lo, hi)))
    return f


def _need(s, where, n, what):
    if not s or ' ' in s or '\r' in s:
        raise ValueError('%s: line %d: %s is empty or holds a blank: %r' % (where, n, what, s))


def parse_sympair(data, where):
    out = []
    for n, ln in enumerate(_lines(data, where), 1):
        sym, ms, kind, df = _fields(ln, where, n, 4, 4)
        _need(sym, where, n, 'symbol')
        if kind not in KINDS and not (kind.startswith('unnamed:') and len(kind) > 8):
            raise ValueError('%s: line %d: unknown kind %r' % (where, n, kind))
        if df not in ('def', 'ref'):
            raise ValueError('%s: line %d: %r is neither def nor ref' % (where, n, df))
        if not ms and kind not in NAMELESS and kind[:8] != 'unnamed:':
            raise ValueError('%s: line %d: kind %s without an MSVC name' % (where, n, kind))
        out.append((sym, ms, kind, df))
    return out


def parse_vtpair(data, where):
    out = []
    for n, ln in enumerate(_lines(data, where), 1):
        z, ap, ms, off = _fields(ln, where, n, 4, 4)
        _need(z, where, n, '_ZTV symbol')
        out.append((z, _int(ap, where, n, 'address point'), ms, _int(off, where, n, 'vfptr offset')))
    return out


def parse_n3(data, where):
    out = []
    for n, ln in enumerate(_lines(data, where), 1):
        f = _fields(ln, where, n, 2, 5) + [''] * 3
        sym, verdict, inl, kind, types = f[:5]
        _need(sym, where, n, 'symbol')
        _need(verdict, where, n, 'verdict')
        if kind not in N3_KINDS:
            _need(kind, where, n, 'clone suffix')
        inlined = tuple(inl.split(',')) if inl else ()
        for s in inlined:
            _need(s, where, n, 'inlined symbol')
        tys = tuple(types.split(',')) if types else ()
        for t in tys:
            _need(t, where, n, 'type')
        out.append((sym, verdict, inlined, kind, tys))
    return out


SECTIONS = (('sympair', '.fnvnx.sympair', parse_sympair), ('vtpair', '.fnvnx.vtpair', parse_vtpair), ('n3', '.fnvnx.n3', parse_n3))


def read(path):
    from elftools.elf.elffile import ELFFile
    where = os.path.basename(path)
    out = {}
    with open(path, 'rb') as fh:
        e = ELFFile(fh)
        for key, name, parse in SECTIONS:
            s = e.get_section_by_name(name)
            out[key] = parse(s.data(), where + ':' + name) if s is not None else []
    return out


if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(p, {k: len(v) for k, v in sorted(read(p).items())})
