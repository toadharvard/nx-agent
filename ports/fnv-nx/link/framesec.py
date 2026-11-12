"""framesec - the non-loaded ELF section `.fnvnx.frame` that the compiler plugin fnvnx_order (toolchain/order, rule R1 of class B)
puts into every native game object, in the strict style of contract/l2sections.py and link/addrsec.py.

    parse_frame(data, where)  ->  [(symbol, ranges)]      rows keep the section's order; ranges is a tuple of (offset, size) or the string 'dynamic'
    from_sections(sections, where) -> rows or None        None when the object has no such section
    read(path)                ->  rows or None

Format: plain text, LF-terminated lines, TAB-separated, exactly 2 fields per line: the assembler symbol (not empty, no blank),
then either the empty string (no address-taken variable), the word `dynamic` (the frame has no constant layout: alloca, a
variable-length array, a variable whose slot is not a constant frame offset), or one or more `OFFSET:SIZE` pairs separated by
one blank each, sorted ascending (by offset, then size). OFFSET is a decimal integer without sign, or with a leading `-`, with no
leading zeros (`0`, `-4`, `16`); SIZE is a decimal integer of at least 1 without leading zeros. OFFSET is relative to the CFA (the stack
pointer at function entry): a variable at [CFA-16, CFA-4) is `-16:12`. Any other line is an error `<where>: line N: <what>`; no
line is skipped, a final line without LF is an error, an empty section is []. Pure: bytes in, tuples out."""
import a64obj

SECTION = '.fnvnx.frame'
DYNAMIC = 'dynamic'


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
        if len(f) != 2:
            raise ValueError('%s: line %d: %d fields, expected 2' % (where, n, len(f)))
        sym, rs = f
        if not sym or ' ' in sym or '\r' in sym:
            raise ValueError('%s: line %d: symbol is empty or holds a blank: %r' % (where, n, sym))
        if rs == DYNAMIC:
            out.append((sym, DYNAMIC))
            continue
        pairs = []
        for p in (rs.split(' ') if rs else []):
            o, _, z = p.partition(':')
            if not (_canon(o, True) and _canon(z, False)):
                raise ValueError('%s: line %d: not an OFFSET:SIZE pair: %r' % (where, n, p))
            pairs.append((int(o), int(z)))
        if pairs != sorted(pairs):
            raise ValueError('%s: line %d: ranges are not sorted' % (where, n))
        out.append((sym, tuple(pairs)))
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
