"""addrsec - the non-loaded ELF section `.fnvnx.addr` that the compiler plugin fnvnx_addr (toolchain/addr, rule ADDR32)
puts into every game object, in the strict style of contract/l2sections.py.

    parse_addr(data, where)  ->  [(symbol, seen, routed)]      rows keep the section's order
    read(path)               ->  [(symbol, seen, routed)] or None when the object has no such section

Format: plain text, LF-terminated lines, TAB-separated, exactly 3 fields per line: the assembler symbol (not empty, no
blank), then the number of integer-to-pointer conversions the function holds at the last GIMPLE point (`seen`) and the
number of those routed through the barrier (`routed`), both plain decimal numbers (digits only, no sign).  Any other
line is an error `<where>: line N: <what>`; no line is skipped, a final line without LF is an error, an empty section
is [].  Pure: bytes in, tuples out.
"""
import os

SECTION = '.fnvnx.addr'


def parse_addr(data, where):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError('%s: line 1: not UTF-8 text (%s)' % (where, e))
    if text and text[-1] != '\n':
        raise ValueError('%s: line %d: no final LF' % (where, text.count('\n') + 1))
    out = []
    for n, ln in enumerate(text.split('\n')[:-1], 1):
        f = ln.split('\t')
        if len(f) != 3:
            raise ValueError('%s: line %d: %d fields, expected 3' % (where, n, len(f)))
        sym, seen, routed = f
        if not sym or ' ' in sym or '\r' in sym:
            raise ValueError('%s: line %d: symbol is empty or holds a blank: %r' % (where, n, sym))
        for what, s in (('seen', seen), ('routed', routed)):
            if not s or not s.isascii() or not s.isdigit():
                raise ValueError('%s: line %d: %s is not a decimal number: %r' % (where, n, what, s))
        out.append((sym, int(seen), int(routed)))
    return out


def from_sections(sections, where):
    """sections: [Sec] of a64obj.  -> rows, or None when there is no section of that name; two sections of that name are an error."""
    hit = [s for s in sections if s.name == SECTION]
    if len(hit) > 1:
        raise ValueError('%s: %d sections named %s' % (where, len(hit), SECTION))
    return parse_addr(hit[0].data, where + ':' + SECTION) if hit else None


def read(path):
    import a64obj
    return from_sections(a64obj.read(path).sections, os.path.basename(path))
