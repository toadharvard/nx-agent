#!/usr/bin/env python3
"""exportgen - exports.tsv of a directory tree of objects (L1 CONTRACT; the recipe stage that reads the `.fnvnx.export` records).

    exportgen.py [--objects <dir of <root>/<unit>.o>] [--out exports.tsv] [--section NAME]   defaults /objs, /out/exports.tsv and .fnvnx.export (the generate stage's mounts and the
                                                                              section its recipe names: contract/exports/build.json, `objects_of` guest + section: the objects of
                                                                              every guest product root outside the consumers of the table, the link set)

Every object may carry the section `.fnvnx.export` (contract/include/fnvnx_export.h): lines `module TAB import name TAB symbol TAB func|data`.  The tool
writes the union, sorted, as five columns: the four fields and `ret`, the return class of a function export read from the definer's own `.fnvnx.decltype` record (`-` for data).  RULE (total, nothing is skipped or guessed):
  - a line that is not exactly four fields, with a module that is not a lower-case `*.dll` name, a symbol that is not a C identifier or a kind other than
    func|data is an error;
  - a (module, name) given twice (in one object or two) is an error;
  - the symbol must be defined (global, in a section) by the object that carries the record, and by no other object of the tree: exactly one definer;
  - a function export's definer has a type row for the symbol in its `.fnvnx.decltype` (link/ctypes_compat.py grammar): no row, or a reason instead of a type, is an error; the type must be a
    function type, and its return type has a class (ret_class): a struct, union, vector, complex or reference return is an error (the bridge table's `ret` is one of v i q f d).
The output depends on the objects' bytes and relative paths only."""
import argparse, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # ports/fnv-nx: the siblings are imported by their qualified name (contract.X)
from contract import l2sections
from link import ctypes_compat

SECTION = '.fnvnx.export'   # the default; the recipe names the section (`objects_of.section`, the one place) and passes --section
DECLTYPE = '.fnvnx.decltype'
LONGLONG = ('long_long_int', 'long_long_unsigned_int')   # the integer words wider than a register of the ILP32 data model (DATAMODEL, OWNER 7): everything else integer is <= 4 bytes
FLOATS = {'float': 'f', 'double': 'd', 'long_double': 'd'}   # DATAMODEL: long double is binary64 (it returns as a double)
MODULE = re.compile(r'[a-z0-9_.\-]+\.dll\Z')
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


def parse_export(data, where):
    out = []
    for n, ln in enumerate(l2sections._lines(data, where), 1):
        mod, name, sym, kind = l2sections._fields(ln, where, n, 4, 4)
        if not MODULE.match(mod):
            raise ValueError('%s: line %d: module %r is not a lower-case *.dll name' % (where, n, mod))
        l2sections._need(name, where, n, 'import name')
        if not IDENT.match(sym):
            raise ValueError('%s: line %d: symbol %r is not a C identifier' % (where, n, sym))
        if kind not in ('func', 'data'):
            raise ValueError('%s: line %d: kind %r is neither func nor data' % (where, n, kind))
        out.append((mod, name, sym, kind))
    return out


def ret_class(t, sym='?'):
    """The bridge table's `ret` letter (v i q f d) of a function type T (a parsed link/ctypes_compat type) by the return type: void v; float f; double and long double d; the 64-bit integers
    q; a pointer, an entry pointer, an enumeration (its integer type) and every other scalar word i.  A struct, union, vector, complex or reference return has no letter: ValueError."""
    if t[0] != 'fn':
        raise ValueError('%s: the type of a function export is not a function type' % sym)
    r = t[3]
    while r[0] in ('q', 'enum', 'host'):
        r = ('sc', r[1]) if r[0] == 'host' else r[2]
    if r[0] in ('ptr', 'entry'):
        return 'i'
    if r[0] != 'sc':
        raise ValueError('%s: a %s return has no class of the bridge table (v i q f d)' % (sym, r[0]))
    w = r[1]
    return 'v' if w == 'void' else FLOATS.get(w) or ('q' if w in LONGLONG else 'i')


def records(path, section=SECTION):
    """-> the export records [(module, name, symbol, kind)] of the object at PATH (no decltype)"""
    from elftools.elf.elffile import ELFFile
    with open(path, 'rb') as fh:
        s = ELFFile(fh).get_section_by_name(section)
        return parse_export(s.data(), os.path.basename(path) + ':' + section) if s is not None else []


def scan(path, reasons, section=SECTION):
    """-> (export records, set of the symbols the object defines globally, {symbol: declared type}, {symbol: no-row reason}) of the object at PATH"""
    from elftools.elf.elffile import ELFFile
    with open(path, 'rb') as fh:
        e = ELFFile(fh)
        s = e.get_section_by_name(section)
        recs = parse_export(s.data(), os.path.basename(path) + ':' + section) if s is not None else []
        dt = e.get_section_by_name(DECLTYPE)
        types, why = ctypes_compat.parse_decltype_lines(dt.data(), os.path.basename(path) + ':' + DECLTYPE, reasons) if dt is not None else ({}, {})
        st = e.get_section_by_name('.symtab')
        defs = set()
        if st is not None:
            for sy in st.iter_symbols():
                if sy['st_shndx'] != 'SHN_UNDEF' and sy['st_info']['bind'] in ('STB_GLOBAL', 'STB_WEAK') and sy['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT', 'STT_NOTYPE', 'STT_TLS'):
                    defs.add(sy.name)
        return recs, defs, types, why


def collect(root, reasons, section=SECTION):
    """-> sorted rows (module, name, symbol, kind, ret); ValueError listing every violation"""
    rows, definers, errs, seen = [], {}, [], {}
    per = []
    for d, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            if f.endswith('.o'):
                rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, '/')
                recs, defs, types, why = scan(os.path.join(d, f), reasons, section)
                for s in defs:
                    definers.setdefault(s, []).append(rel)
                per.append((rel, recs, defs, types, why))
    for rel, recs, defs, types, why in per:
        for mod, name, sym, kind in recs:
            k = (mod, name)
            if k in seen:
                errs.append('%s!%s: given twice (%s, %s)' % (mod, name, seen[k], rel))
                continue
            seen[k] = rel
            if sym not in defs:
                errs.append('%s!%s: %s is not defined by %s, which carries the record' % (mod, name, sym, rel))
            elif len(definers[sym]) != 1:
                errs.append('%s!%s: %s has %d definers (%s)' % (mod, name, sym, len(definers[sym]), ' '.join(sorted(definers[sym]))))
            elif kind == 'data':
                rows.append((mod, name, sym, kind, '-'))
            elif sym not in types:
                errs.append('%s!%s: %s has no type row in the .fnvnx.decltype of %s (%s)' % (mod, name, sym, rel, 'reason ' + why[sym] if sym in why else 'no record'))
            else:
                try:
                    rows.append((mod, name, sym, kind, ret_class(ctypes_compat.parse(types[sym]), sym)))
                except ValueError as e:
                    errs.append('%s!%s: %s' % (mod, name, e))
    if errs:
        raise ValueError('%d export records are wrong:\n%s' % (len(errs), '\n'.join(errs)))
    return sorted(rows)


RETS = ('v', 'i', 'q', 'f', 'd')


def read(text):
    """{(module, name): (symbol, kind, ret)} from exports.tsv text: five columns, `ret` one of v i q f d for a function and `-` for data"""
    out = {}
    for n, ln in enumerate(text.split('\n'), 1):
        if not ln:
            continue
        f = ln.split('\t')
        if len(f) != 5:
            raise ValueError('exports.tsv line %d: need module TAB name TAB symbol TAB func|data TAB ret' % n)
        m, name, sym, kind, ret = f
        parse_export(('%s\t%s\t%s\t%s\n' % (m, name, sym, kind)).encode(), 'exports.tsv:%d' % n)
        if ret != ('-' if kind == 'data' else ret) or (kind == 'func' and ret not in RETS) or (m, name) in out:
            raise ValueError('exports.tsv line %d: bad ret %r or %s!%s twice' % (n, ret, m, name))
        out[(m, name)] = (sym, kind, ret)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--objects', default='/objs')
    ap.add_argument('--out', default='/out/exports.tsv')
    ap.add_argument('--section', default=SECTION)
    ap.add_argument('--reasons', required=True, help='toolchain/layout/decltype_spell.inc, the plugin\'s closed set of no-row reasons (a declared input of the stage)')
    a = ap.parse_args()
    try:
        with open(a.reasons) as fh:
            reasons = ctypes_compat.decltype_reasons(fh.read())
        rows = collect(a.objects, reasons, a.section)
    except ValueError as e:
        sys.stderr.write('exportgen: %s\n' % e)
        sys.exit(1)
    with open(a.out, 'w') as fh:
        fh.write(''.join('\t'.join(r) + '\n' for r in rows))
    print('%d exports' % len(rows))


if __name__ == '__main__':
    main()
