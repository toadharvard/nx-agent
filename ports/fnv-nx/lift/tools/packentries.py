#!/usr/bin/env python3
"""The `entries` stage of the product root lift/pack: entries.tsv of the pack, an output (ENTRIES) of the same build as the image.
    packentries.py PACK_ENTRIES IMP_ENTRIES BRIDGE_TSV OUT
PACK_ENTRIES = the external's entries.tsv (rows `addr<TAB>symbol<TAB>kind`, kinds code and runtime: what the lifter derives from the exe and modules alone, no import row, no
trailer); IMP_ENTRIES = the output of the impgen stage (first line `# table_id <sha256 of BRIDGE_TSV>`, rows `key<TAB>symbol<TAB>dll<TAB>name<TAB>bind`); BRIDGE_TSV = the
loader root's output of that name (contract/bridgegen.py), the one table the import keys index. The output holds the pack's rows, the import rows as kind `import`, sorted by
address, and the trailer `# table_id <sha256 of BRIDGE_TSV's bytes>`.
table_id has ONE computation, here, over the bytes of the table the build uses. The impgen output's header must equal it (the two stages read one table), and an address
must not be a row of both inputs: a mismatch or a collision is a red stage, never a silent merge."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(entries, imp, bridge, out):
    tid = hashlib.sha256(open(bridge, 'rb').read()).hexdigest()
    il = open(imp).read().split('\n')
    if il and il[-1] == '': il.pop()
    if not il or il[0] != '# table_id %s' % tid:
        sys.exit('packentries: the import rows were generated against another bridge table: header %r, BRIDGE_TSV table_id %s' % (il[:1], tid))
    rows = {}
    for l in open(entries).read().split('\n'):
        if not l or l.startswith('#'): continue
        a, s, k = l.split('\t')
        if k == 'import': sys.exit('packentries: %s: an import row in the external (import rows are the impgen stage)' % a)
        rows[int(a, 16)] = (s, k)
    for l in il[1:]:
        f = l.split('\t'); a = int(f[0], 16)
        if a in rows: sys.exit('packentries: %08x is a row of the external and an import row' % a)
        rows[a] = (f[1], 'import')
    open(out, 'w').write(''.join('%08x\t%s\t%s\n' % (a, s, k) for a, (s, k) in sorted(rows.items())) + '# table_id %s\n' % tid)


if __name__ == '__main__':
    import cli
    (a, b, c, d), _ = cli.parse('usage: packentries.py PACK_ENTRIES IMP_ENTRIES BRIDGE_TSV OUT\n  entries.tsv of the pack: the external\'s rows + the import rows, table_id = sha256 of BRIDGE_TSV', sys.argv[1:], (4, 4))
    main(a, b, c, d)
