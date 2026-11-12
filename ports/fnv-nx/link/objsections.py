#!/usr/bin/env python3
"""objsections - parsers of the L2 compiler-plugin sections that contract/l2sections.py does not parse (L2 SOURCE).

    parse_names(data, where)    -> [(symbol, msvc_name)]                                        .fnvnx.names (plugin NAMES1)
    parse_argorder(data, where) -> [(function, callee, ((callee, ...), ...), k, n)]              .fnvnx.argorder (plugin pi)
    parse_layout(data, where)   -> [Layout]                                                      .fnvnx.layout (plugin layout)
    section(elf, name)          -> bytes; an absent section is b'' (= no rows)

Same style as contract/l2sections.py: plain text, LF-terminated lines, TAB-separated; every line is parsed, any other line
is ValueError('<where>: line N: <what>'); a final line without LF is an error; an empty section is [] (rows keep the
section's order).  Pure: bytes in, tuples out.

  names     exactly 2 fields: symbol (not empty, no blank), MSVC name (not empty).
  argorder  exactly 4 fields: function, callee (symbols), `a0|a1|...` (at least two arguments: the producer writes a site only
            when two or more arguments have effects; each argument is a possibly empty `,`-list of symbols, no empty element
            inside a non-empty list), `k/n` (decimals, 0 <= k < n).
  layout    exactly 7 fields: key (not empty), size, align (decimals), fields, bases, flags, itanium.
            fields: `,`-list of `name:byteoffset:size` | `name:byteoffset:bitoffset:bitsize:size` (bitoffset < 8) |
                    `<vptr>:byteoffset:size`; a name starting with `#` is the producer's mark of an unnamed (bit-)field.
            bases:  `,`-list of `key:offset:flag` (offset decimal, may be negative), flag n (direct non-virtual), v (direct virtual), i (indirect virtual).
            flags:  `,`-list of `nokey:<reason>` (reason not empty), packed, useralign, altered; any other flag is an error.
"""
import collections

Layout = collections.namedtuple('Layout', 'key size align fields bases flags itanium')
# fields: (name, byte offset, bit offset or None, bit width or None, size); the vptr is ('<vptr>', offset, None, None, size)
# bases: (key, offset, flag)
FLAGS = frozenset(('packed', 'useralign', 'altered'))


def section(elf, name):
    d = elf.section(name)
    return b'' if d is None else d


def _lines(data, where):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError('%s: line 1: not UTF-8 text (%s)' % (where, e))
    if text and text[-1] != '\n':
        raise ValueError('%s: line %d: no final LF' % (where, text.count('\n') + 1))
    return text.split('\n')[:-1]


def _fields(ln, where, n, count):
    f = ln.split('\t')
    if len(f) != count:
        raise ValueError('%s: line %d: %d fields, expected %d' % (where, n, len(f), count))
    return f


def _need(s, where, n, what):
    if not s or ' ' in s or '\r' in s:
        raise ValueError('%s: line %d: %s is empty or holds a blank: %r' % (where, n, what, s))


def _nat(s, where, n, what):
    if not s or not s.isascii() or not s.isdigit():
        raise ValueError('%s: line %d: %s is not a decimal number: %r' % (where, n, what, s))
    return int(s)


def _int(s, where, n, what):
    if s[:1] == '-':
        return -_nat(s[1:], where, n, what)
    return _nat(s, where, n, what)


def _list(s, sep, where, n, what):
    if not s:
        return []
    items = s.split(sep)
    for x in items:
        if not x:
            raise ValueError('%s: line %d: empty element in %s' % (where, n, what))
    return items


def parse_names(data, where):
    out = []
    for n, ln in enumerate(_lines(data, where), 1):
        sym, ms = _fields(ln, where, n, 2)
        _need(sym, where, n, 'symbol')
        if not ms or '\r' in ms:
            raise ValueError('%s: line %d: MSVC name is empty' % (where, n))
        out.append((sym, ms))
    return out


def parse_argorder(data, where):
    out = []
    for n, ln in enumerate(_lines(data, where), 1):
        fn, callee, args, kn = _fields(ln, where, n, 4)
        _need(fn, where, n, 'function')
        _need(callee, where, n, 'callee')
        parts = args.split('|')
        if len(parts) < 2:
            raise ValueError('%s: line %d: fewer than two arguments: %r' % (where, n, args))
        groups = []
        for p in parts:
            g = tuple(_list(p, ',', where, n, 'an argument'))
            for c in g:
                _need(c, where, n, 'argument callee')
            groups.append(g)
        if kn.count('/') != 1:
            raise ValueError('%s: line %d: %r is not k/n' % (where, n, kn))
        ks, ns = kn.split('/')
        k, cnt = _nat(ks, where, n, 'k'), _nat(ns, where, n, 'n')
        if k >= cnt:
            raise ValueError('%s: line %d: call number %d of %d' % (where, n, k, cnt))
        out.append((fn, callee, tuple(groups), k, cnt))
    return out


def _field(f, where, n):
    p = f.split(':')
    if p[0] == '<vptr>':
        if len(p) != 3:
            raise ValueError('%s: line %d: vptr field %r is not <vptr>:offset:size' % (where, n, f))
        return ('<vptr>', _nat(p[1], where, n, 'vptr offset'), None, None, _nat(p[2], where, n, 'vptr size'))
    if not p[0] or len(p) not in (3, 5):
        raise ValueError('%s: line %d: field %r is neither name:off:size nor name:off:bit:width:size' % (where, n, f))
    if len(p) == 3:
        return (p[0], _nat(p[1], where, n, 'offset'), None, None, _nat(p[2], where, n, 'size'))
    bit = _nat(p[2], where, n, 'bit offset')
    if bit > 7:
        raise ValueError('%s: line %d: bit offset %d in field %r' % (where, n, bit, f))
    return (p[0], _nat(p[1], where, n, 'offset'), bit, _nat(p[3], where, n, 'bit width'), _nat(p[4], where, n, 'size'))


def parse_layout(data, where):
    out = []
    for n, ln in enumerate(_lines(data, where), 1):
        key, size, align, fields, bases, flags, itanium = _fields(ln, where, n, 7)
        if not key or ' ' in key or '\r' in key:
            raise ValueError('%s: line %d: key is empty or holds a blank: %r' % (where, n, key))
        fl = []
        for f in _list(flags, ',', where, n, 'flags'):
            if f not in FLAGS and not (f.startswith('nokey:') and len(f) > 6):
                raise ValueError('%s: line %d: unknown flag %r' % (where, n, f))
            fl.append(f)
        bs = []
        for b in _list(bases, ',', where, n, 'bases'):
            parts = b.rsplit(':', 2)
            if len(parts) != 3 or not parts[0] or parts[2] not in ('n', 'v', 'i'):
                raise ValueError('%s: line %d: base %r is not key:offset:flag' % (where, n, b))
            bs.append((parts[0], _int(parts[1], where, n, 'base offset'), parts[2]))
        out.append(Layout(key, _nat(size, where, n, 'size'), _nat(align, where, n, 'align'),
                          tuple(_field(f, where, n) for f in _list(fields, ',', where, n, 'fields')), tuple(bs), tuple(fl), itanium))
    return out
