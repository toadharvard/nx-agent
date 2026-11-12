#!/usr/bin/env python3
"""iat - the value the loader writes in every import slot of an image (L1 CONTRACT, loader contract with L3 and L5).

    iat.py --facts facts.json --bridge bridge.tsv [--module name.dll=exports.tsv ...] --out iat.tsv

Image code pointers are not rewritten and `call [slot]` is an ordinary indirect call, so a slot must hold a 32-bit
value the dispatcher of the lifted code maps to an entry.  Rule, total over the image's import directory:

  module    the dll is a lifted PE module: the slot holds the address of the module's export (exports.tsv of
            modules.py: name or #ordinal, address at the module's assigned base, forwarder, ordinal), as the Windows
            loader does; a forwarder export holds the value of its target (resolve_exports).
  key       the dll is implemented by the platform (a bridge.tsv row of L5): the slot holds the function's key,
            IMPORT_BASE + STEP * (index of (dll, name) among the non-data rows of bridge.tsv sorted by dll, name).
            One function has one key in every image, so pointer equality behaves as on Windows.
  data      bridge.tsv conv `data`: the slot holds the address of the native object; no key (bound by the loader
            from the ELF symbol).

Keys lie in [IMPORT_BASE, LIMIT), and SENTINEL (the return address under the first guest frame) below them.
Assumption, checked by the loader at start: no mapping of the process intersects [SENTINEL, LIMIT) - the range
is below the lowest address the 32-bit address space hands out (0x00200000), so a key is never a readable or
executable address and never the address of an image, module, heap or stack.

A slot without a provider, an export the module does not have, a dll that is both a module and a bridge dll: the
stage fails and lists them (gate `iat`: universe = slots, certified = slots with a value).  Output rows:
slot VA, dll, name (an import by ordinal is written with the name bridge.tsv gives that ordinal), kind, value
(empty for data).  Exe-derived: container and fnv-nx-build only.

One key table, three consumers (the loader binding slots, the lifter's import stubs, the platform's GetProcAddress):

  --keys-out keys.tsv   key (8 hex), dll, name, ordinal, conv, ret, args, native_symbol; one row per non-data row of
                        bridge.tsv, sorted by key; first line `# table_id <sha256 of bridge.tsv>`.  Input of the stubs.
  --c-out import_keys.c the C table of loader/src/importkey.c (`fnvnx_import_rows`, sorted by (dll, name) for binary
                        search, `fnvnx_import_by_key_idx`, `fnvnx_import_table_id` = the same sha256).  No timestamps.

Rules fixed here (all three consumers use them through this file and importkey.c, nowhere else):
  dll name   as the Windows loader compares module names: ASCII case-insensitive, and a name with no '.' gets ".dll"
             appended (LoadLibrary / the import directory: "KERNEL32" and "kernel32.dll" are one module).  A name with
             another extension keeps it.  norm_dll() here and the same function in importkey.c.  Function names are
             compared exactly (GetProcAddress is case-sensitive).
  same name  in two dlls: two functions, two keys (the key is a function of (dll, name), never of name alone).
  ordinal    an import by ordinal (facts name `#N`) is the row of bridge.tsv with that (dll, ordinal), as the export
             table of the real dll maps ordinal to export; no row, no provider.  Two rows with one (dll, ordinal), or
             one (dll, name) twice, are a stage error.  Rows without an ordinal are reachable by name only.
  bridge.tsv gains or loses a row: keys of all later rows shift.  Keys, stubs and the loader table are generated from
             the same bridge.tsv in one link; the stage key of all three includes the SHA-256 of bridge.tsv, and
             keys.tsv and import_keys.c each carry it (`table_id`) so the gate can check they come from one input.
  data row   a bridge row of conv `data` whose native_symbol is a C identifier is a table row of kind data (value =
             address of the native object).  A data row with native_symbol `-` has no native object: it is left out of
             the table (the lookup returns 0 = the platform does not provide it) and counted in the stdout line.
"""
import argparse, hashlib, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatefile, store

SENTINEL, IMPORT_BASE, STEP, LIMIT = 0x000ffff0, 0x00100000, 16, 0x00200000


def norm_dll(dll):
    """Windows module-name rule: ASCII lowercase; no '.' in the name -> ".dll" appended."""
    d = ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in dll)
    return d if '.' in d else d + '.dll'


def system_keys(bridge):
    """bridge: [(dll, name, conv[, ordinal])] -> {(dll, name): key} for the rows that are functions."""
    names = sorted((norm_dll(b[0]), b[1]) for b in bridge if b[2] != 'data')
    if len(set(names)) != len(names):
        raise ValueError('bridge.tsv names one function twice')
    if IMPORT_BASE + STEP * len(names) > LIMIT:
        raise ValueError('more platform functions than the key range holds')
    return {k: IMPORT_BASE + STEP * i for i, k in enumerate(names)}


def table(imports, bridge, modules):
    """imports {slot: (dll, name)}, modules {dll: {name: address}} -> (rows, bad)."""
    keys = system_keys(bridge)
    data = {(norm_dll(b[0]), b[1]) for b in bridge if b[2] == 'data'}
    by_ord = {}      # an import by ordinal binds to the row of that ordinal, as the export table of the dll would
    for b in bridge:
        if len(b) > 3 and b[3] != '':
            if by_ord.setdefault((norm_dll(b[0]), '#%d' % int(b[3])), b[1]) != b[1]:
                raise ValueError('bridge.tsv gives ordinal %s of %s to two names' % (b[3], b[0]))
    both = sorted({d for d, _ in list(keys) + sorted(data)} & set(modules))
    if both:
        raise ValueError('both a lifted module and a platform dll: %s' % ', '.join(both))
    rows, bad = [], []
    for slot, (dll, name) in sorted(imports.items()):
        k = (norm_dll(dll), name)
        if k[0] not in modules and k in by_ord:
            k = (k[0], by_ord[k])
        if k[0] in modules:
            if name in modules[k[0]]:
                rows.append((slot,) + k + ('module', modules[k[0]][name]))
            else:
                bad.append((slot,) + k + ('not an export of the module',))
        elif k in keys:
            rows.append((slot,) + k + ('key', keys[k]))
        elif k in data:
            rows.append((slot,) + k + ('data', None))
        else:
            bad.append((slot,) + k + ('no provider',))
    return rows, bad


def read_tsv(path, n):
    out = []
    with open(path) as fh:
        for i, ln in enumerate(fh):
            p = ln.rstrip('\n').split('\t')
            if ln[:1] == '#' or (i == 0 and p[0] == 'dll'):
                continue
            if len(p) < n:
                raise ValueError('%s:%d: %d columns, expected %d' % (path, i + 1, len(p), n))
            out.append(p)
    return out


COLS = ('dll', 'name', 'ordinal', 'conv', 'ret', 'args', 'native_symbol', 'bind')
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
NO_ORD = 0xffffffff


def bridge_rows(path):
    """-> (rows, table_id): rows are dicts of the bridge.tsv columns with dll normalised; checked for ASCII names, one
    (dll, name) once, one (dll, ordinal) once.  Any violation is a ValueError (stage error)."""
    with open(path, 'rb') as fh:
        raw = fh.read()
    rows = []
    for p in read_tsv(path, 7):
        r = dict(zip(COLS, p + [''] * (8 - len(p))))
        for c in COLS:
            if not r[c].isascii() or any(ord(ch) < 32 or ord(ch) > 126 for ch in r[c]):
                raise ValueError('bridge.tsv: non-printable or non-ASCII %s %r' % (c, r[c]))
        r['dll'] = norm_dll(r['dll'])
        rows.append(r)
    names = [(r['dll'], r['name']) for r in rows]
    if len(set(names)) != len(names):
        raise ValueError('bridge.tsv names one function twice')
    ords = [(r['dll'], int(r['ordinal'])) for r in rows if r['ordinal'] != '']
    if len(set(ords)) != len(ords):
        raise ValueError('bridge.tsv gives one ordinal of one dll to two rows')
    return rows, hashlib.sha256(raw).hexdigest()


def keys_tsv(rows, table_id):
    """Text of keys.tsv."""
    keys = system_keys([(r['dll'], r['name'], r['conv'], r['ordinal']) for r in rows])
    out = ['# table_id %s\n' % table_id]
    for r in sorted((r for r in rows if r['conv'] != 'data'), key=lambda r: keys[(r['dll'], r['name'])]):
        out.append('%08x\t%s\n' % (keys[(r['dll'], r['name'])], '\t'.join(r[c] for c in COLS[:7] if c != 'native_symbol') + '\t' + r['native_symbol']))
    return ''.join(out)


def cstr(s):
    return '"' + ''.join('\\' + c if c in '"\\?' else c for c in s) + '"'


def c_source(rows, table_id):
    """-> (text of import_keys.c, number of data rows without a native object)."""
    keys = system_keys([(r['dll'], r['name'], r['conv'], r['ordinal']) for r in rows])
    ent, objs, skipped = [], [], 0
    for r in sorted(rows, key=lambda r: (r['dll'], r['name'])):
        o = r['ordinal'] and '%du' % int(r['ordinal']) or '0xffffffffu'
        if r['conv'] == 'data':
            if not IDENT.match(r['native_symbol']):
                raise ValueError('data row %s!%s: native_symbol %r is not a C identifier' % (r['dll'], r['name'], r['native_symbol']))
            objs.append(r['native_symbol'])
            ent.append((r, '{%s, %s, %s, FNVNX_IMPORT_DATA, (uintptr_t)&%s}' % (cstr(r['dll']), cstr(r['name']), o, r['native_symbol'])))
        else:
            ent.append((r, '{%s, %s, %s, FNVNX_IMPORT_KEY, 0x%08xu}' % (cstr(r['dll']), cstr(r['name']), o, keys[(r['dll'], r['name'])])))
    by_key = sorted((keys[(r['dll'], r['name'])], i) for i, (r, _) in enumerate(ent) if r['conv'] != 'data')
    assert [k for k, _ in by_key] == [IMPORT_BASE + STEP * i for i in range(len(by_key))]
    t = ['/* Generated by contract/iat.py from bridge.tsv: do not edit.  Exe-derived name set: not for the public tree. */\n',
         '#include <stddef.h>\n#include <stdint.h>\n#include "fnvnx/import_key.h"\n\n']
    t += ['extern char %s[];\n' % o for o in sorted(set(objs))]
    t += ['\nconst char fnvnx_import_table_id[65] = "%s";\n\n' % table_id,
          'const fnvnx_import_row fnvnx_import_rows[] = {\n'] + ['    %s,\n' % e for _, e in ent] + ['};\n',
          'const size_t fnvnx_import_row_count = %d;\n\n' % len(ent),
          'const uint16_t fnvnx_import_by_key_idx[] = {\n'] + ['    %d,\n' % i for _, i in by_key] + ['    0\n};\n',
          'const size_t fnvnx_import_key_count = %d;\n' % len(by_key)]
    return ''.join(t), skipped


def split_forwarder(fwd):
    """PE export forwarder "DLL.Name" or "DLL.#N" -> (normalised dll, "Name" | "#N"), or None when it has no '.'.  The
    dll part carries no extension in a forwarder (norm_dll appends ".dll"); the split is at the FIRST '.': function names
    contain none (assumption of the PE format's "DLLNAME.Function" string)."""
    i = fwd.find('.')
    if i <= 0 or i + 1 >= len(fwd):
        return None
    return norm_dll(fwd[:i]), fwd[i + 1:]


def resolve_exports(raw, bridge):
    """raw {dll: [(key, address | None, forwarder | None, ordinal)]} (key = export name or "#ordinal"; a named export is
    also reachable as "#ordinal") -> {dll: {key: value}}: the value an import of that export gets, as the loader's resolver
    computes it.  A forwarder is resolved through the same rule: the forwarded (dll, name | #N) is a module export
    (recursively; a cycle, or a target that is not there, leaves the export out = no provider) or a platform function
    (its key).  A forwarder to a platform DATA row is left out too: its value is the address of a native object bound by
    the loader, which this table cannot hold."""
    keys = system_keys(bridge)
    by_ord = {}
    for b in bridge:
        if len(b) > 3 and b[3] != '':
            by_ord[(norm_dll(b[0]), '#%d' % int(b[3]))] = b[1]
    index = {}
    for dll, ents in raw.items():
        d = index.setdefault(norm_dll(dll), {})
        for key, addr, fwd, ordinal in ents:
            d[key] = (addr, fwd)
            d['#%d' % ordinal] = (addr, fwd)

    def value(dll, key, hops):
        if dll in index:
            e = index[dll].get(key)
            if e is None:
                return None
            if e[1] is None:
                return e[0]
            t = split_forwarder(e[1])
            return None if t is None or hops >= len(index) else value(t[0], t[1], hops + 1)
        return keys.get((dll, by_ord.get((dll, key), key)))

    return {dll: {k: v for k in sorted(index[dll]) for v in [value(dll, k, 0)] if v is not None} for dll in sorted(index)}


def read_exports(path):
    """exports.tsv (modules.py): key, address (hex, empty for a forwarder), forwarder (empty for a code/data export),
    ordinal (decimal) -> [(key, address | None, forwarder | None, ordinal)]"""
    return [(p[0], int(p[1], 16) if p[1] else None, p[2] or None, int(p[3])) for p in read_tsv(path, 4)]


def write_table(path, rows):
    with open(path, 'w', newline='\n') as fh:
        for slot, dll, name, kind, v in rows:
            fh.write('%08x\t%s\t%s\t%s\t%s\n' % (slot, dll, name, kind, '' if v is None else '%08x' % v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--bridge', required=True, help="L5's bridge.tsv: dll, name, ordinal, conv, ...")
    ap.add_argument('--module', action='append', default=[], help='name.dll=exports.tsv (modules.py: key, address, forwarder, ordinal)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--keys-out', help='keys.tsv for the lifter stubs')
    ap.add_argument('--c-out', help='import_keys.c for loader/src/importkey.c')
    a = ap.parse_args()
    try:
        brows, table_id = bridge_rows(a.bridge)
        bridge = [(r['dll'], r['name'], r['conv'], r['ordinal']) for r in brows]
        ktxt = keys_tsv(brows, table_id)
        ctxt, nodata = c_source(brows, table_id)
        raw = {}
        for m in a.module:
            name, path = m.split('=', 1)
            raw[norm_dll(name)] = read_exports(path)
        modules = resolve_exports(raw, bridge)
        rows, bad = table(store.load(a.facts, 'facts')['imports'], bridge, modules)
    except ValueError as e:
        sys.exit('iat: %s' % e)
    for path, txt in ((a.keys_out, ktxt), (a.c_out, ctxt)):
        if path:
            with open(path, 'w', newline='\n') as fh:
                fh.write(txt)
    write_table(a.out, rows)
    gatefile.write('iat', len(rows) + len(bad), len(rows), ['%08x %s!%s: %s' % b for b in bad], '%d slots, %d without a value' % (len(rows) + len(bad), len(bad)))
    print('%d slots with a value, %d without; %d data rows without a native object' % (len(rows), len(bad), nodata))
    for b in bad:
        print('%08x\t%s!%s\t%s' % b)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
