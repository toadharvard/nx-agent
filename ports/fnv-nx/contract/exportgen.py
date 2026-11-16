#!/usr/bin/env python3
"""exportgen - exports.tsv of a directory tree of objects (L1 CONTRACT; the recipe stage that reads the `.fnvnx.export` records).

    exportgen.py --objects <dir of <root>/<unit>.o> --out exports.tsv

Every object may carry the section `.fnvnx.export` (contract/include/fnvnx_export.h): lines `module TAB import name TAB symbol TAB func|data`.  The tool
writes the union, sorted, as the same four columns.  RULE (total, nothing is skipped or guessed):
  - a line that is not exactly four fields, with a module that is not a lower-case `*.dll` name, a symbol that is not a C identifier or a kind other than
    func|data is an error;
  - a (module, name) given twice (in one object or two) is an error;
  - the symbol must be defined (global, in a section) by the object that carries the record, and by no other object of the tree: exactly one definer.
The output depends on the objects' bytes and relative paths only."""
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l2sections

SECTION = '.fnvnx.export'
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


def scan(path):
    """-> (export records, set of the symbols the object defines globally)"""
    from elftools.elf.elffile import ELFFile
    with open(path, 'rb') as fh:
        e = ELFFile(fh)
        s = e.get_section_by_name(SECTION)
        recs = parse_export(s.data(), os.path.basename(path) + ':' + SECTION) if s is not None else []
        st = e.get_section_by_name('.symtab')
        defs = set()
        if st is not None:
            for sy in st.iter_symbols():
                if sy['st_shndx'] != 'SHN_UNDEF' and sy['st_info']['bind'] in ('STB_GLOBAL', 'STB_WEAK') and sy['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT', 'STT_NOTYPE', 'STT_TLS'):
                    defs.add(sy.name)
        return recs, defs


def collect(root):
    """-> sorted rows (module, name, symbol, kind); ValueError listing every violation"""
    rows, definers, errs, seen = [], {}, [], {}
    per = []
    for d, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            if f.endswith('.o'):
                rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, '/')
                recs, defs = scan(os.path.join(d, f))
                for s in defs:
                    definers.setdefault(s, []).append(rel)
                per.append((rel, recs, defs))
    for rel, recs, defs in per:
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
            else:
                rows.append((mod, name, sym, kind))
    if errs:
        raise ValueError('%d export records are wrong:\n%s' % (len(errs), '\n'.join(errs)))
    return sorted(rows)


def read(text):
    """{(module, name): (symbol, kind)} from exports.tsv text"""
    out = {}
    for r in parse_export(text.encode() if text.endswith('\n') or not text else (text + '\n').encode(), 'exports.tsv'):
        out[(r[0], r[1])] = (r[2], r[3])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--objects', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    try:
        rows = collect(a.objects)
    except ValueError as e:
        sys.stderr.write('exportgen: %s\n' % e)
        sys.exit(1)
    with open(a.out, 'w') as fh:
        fh.write(''.join('\t'.join(r) + '\n' for r in rows))
    print('%d exports' % len(rows))


if __name__ == '__main__':
    main()
