"""framesec - the non-loaded ELF section `.fnvnx.frame` that the compiler plugin fnvnx_order (toolchain/order, rule R1 of class B)
puts into every native game object, in the strict style of contract/l2sections.py and link/addrsec.py.

    parse_frame(data, where)  ->  [Row(symbol, ranges)]   rows keep the section's order; ranges is a tuple of (offset, size) or the string 'dynamic'
                                                          (a Row is a 2-tuple with the attributes .args (int or None) and .unk (tuple of causes))
    from_sections(sections, where) -> rows or None        None when the object has no such section
    read(path)                ->  rows or None

Format: plain text, LF-terminated lines, TAB-separated, 3 fields per line (the third `args:N`, N = the size of the incoming stack arguments,
bytes [CFA, CFA+N), decimal without sign or leading zeros, 0 allowed; a 2-field line, the earlier form, has args None: no argument area is
known) and in the ranges field, after the pairs, zero or more `unk:CAUSE` tokens (CAUSE reg, mem or size; sorted, each once): an address-taken
variable whose slot is not a constant frame offset. A row with an `unk` token is `dynamic` for the gate (its ranges are not complete). Fields: the assembler symbol (not empty, no blank),
then either the empty string (no address-taken variable), the word `dynamic` (the frame has no constant layout: alloca, a
variable-length array, a variable whose slot is not a constant frame offset), or one or more `OFFSET:SIZE` or `OFFSET:SIZE:n` pairs (`:n` = a variable that does not escape, escapes=0: a plain access to it is certified by its range,
through sp/x29 or a register that holds the formed address; a `:n` pair never certifies bytes that overlap an escaping pair) separated by
one blank each, sorted ascending (by offset, then size). OFFSET is a decimal integer without sign, or with a leading `-`, with no
leading zeros (`0`, `-4`, `16`); SIZE is a decimal integer of at least 1 without leading zeros. OFFSET is relative to the CFA (the stack
pointer at function entry): a variable at [CFA-16, CFA-4) is `-16:12`. Any other line is an error `<where>: line N: <what>`; no
line is skipped, a final line without LF is an error, an empty section is []. Pure: bytes in, tuples out."""
import a64obj

SECTION = '.fnvnx.frame'
DYNAMIC = 'dynamic'


class Row(tuple):
    """(symbol, ranges) with .args (incoming stack-argument bytes or None) and .unk (causes)"""

    def __new__(cls, sym, ranges, args=None, unk=()):
        r = tuple.__new__(cls, (sym, ranges))
        r.args, r.unk = args, tuple(unk)
        return r


class Pair(tuple):
    """(offset, size) with .priv: the variable does not escape (`OFF:SIZE:n`, GCC's points-to solution of the final GIMPLE: its address stays in the thread).
    Equal only to a pair of the same offset, size and flag."""

    def __new__(cls, off, size, priv=False):
        r = tuple.__new__(cls, (off, size))
        r.priv = priv
        return r

    def __eq__(self, o):
        return tuple.__eq__(self, o) and getattr(o, 'priv', False) == self.priv

    def __ne__(self, o):
        return not self == o

    def __hash__(self):
        return hash((tuple(self), self.priv))


UNK_CAUSES = ('reg', 'mem', 'size')


def _canon(t, signed):
    """a decimal integer without leading zeros ('0', '-4', '16'; a size is at least 1 and unsigned)"""
    d = t[1:] if signed and t.startswith('-') else t
    return bool(d) and d.isascii() and d.isdigit() and (d == '0' or d[0] != '0') and (signed or d != '0') and t != '-0'


def parse_frame(data, where):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError('%s: line 1: not UTF-8 text (%s)' % (where, e))
    if text and text[-1] != '\n':
        raise ValueError('%s: line %d: no final LF' % (where, text.count('\n') + 1))
    out = []
    for n, ln in enumerate(text.split('\n')[:-1], 1):
        f = ln.split('\t')
        if len(f) not in (2, 3):
            raise ValueError('%s: line %d: %d fields, expected 2 or 3' % (where, n, len(f)))
        sym, rs = f[0], f[1]
        args = None
        if len(f) == 3:
            a = f[2][5:] if f[2].startswith('args:') else ''
            if not (a.isascii() and a.isdigit() and (a == '0' or a[0] != '0')):
                raise ValueError('%s: line %d: not args:N: %r' % (where, n, f[2]))
            args = int(a)
        if not sym or ' ' in sym or '\r' in sym:
            raise ValueError('%s: line %d: symbol is empty or holds a blank: %r' % (where, n, sym))
        if rs == DYNAMIC:
            out.append(Row(sym, DYNAMIC, args))
            continue
        pairs, unk = [], []
        for p in (rs.split(' ') if rs else []):
            if p.startswith('unk:'):
                if p[4:] not in UNK_CAUSES:
                    raise ValueError('%s: line %d: unknown cause: %r' % (where, n, p))
                unk.append(p[4:])
                continue
            if unk:
                raise ValueError('%s: line %d: a range after an unk token: %r' % (where, n, p))
            o, _, z = p.partition(':')
            z, _, pv = z.partition(':')
            if not (_canon(o, True) and _canon(z, False)) or pv not in ('', 'n'):
                raise ValueError('%s: line %d: not an OFFSET:SIZE[:n] pair: %r' % (where, n, p))
            pairs.append(Pair(int(o), int(z), pv == 'n'))
        if [tuple(q) for q in pairs] != sorted(tuple(q) for q in pairs):
            raise ValueError('%s: line %d: ranges are not sorted' % (where, n))
        if unk != sorted(set(unk)):
            raise ValueError('%s: line %d: unk causes are not sorted and distinct' % (where, n))
        out.append(Row(sym, DYNAMIC if unk else tuple(pairs), args, unk))
    return out


def from_sections(sections, where):
    found = [s for s in sections if s.name == SECTION]
    if not found:
        return None
    if len(found) > 1:
        raise ValueError('%s: %d %s sections' % (where, len(found), SECTION))
    return parse_frame(found[0].data, where)


def read(path):
    return from_sections(a64obj.read(path).sections, path)
