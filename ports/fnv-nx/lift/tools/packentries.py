#!/usr/bin/env python3
"""The `entries` stage of the product root lift/pack: entries.tsv of the pack, an output (ENTRIES) of the same build as the image.
    packentries.py PACK_ENTRIES BRIDGE_TSV OUT
PACK_ENTRIES = the pack's entries.tsv (rows `addr<TAB>symbol<TAB>kind`, last line `# table_id <sha256>`); BRIDGE_TSV = the loader root's output of that name
(contract/bridgegen.py), the one table the pack's import keys index. The output holds the pack's rows and the trailer `# table_id <sha256 of BRIDGE_TSV's bytes>`.
table_id has ONE computation, here, over the bytes of the table the build uses. The pack's own trailer (written when its imports were generated, tools/impgen.py)
must equal it: a pack whose import keys were generated against another table is a red stage, never a silent mismatch."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(entries, bridge, out):
    tid = hashlib.sha256(open(bridge, 'rb').read()).hexdigest()
    lines = open(entries).read().split('\n')
    if lines and lines[-1] == '': lines.pop()
    tr = [l for l in lines if l.startswith('#')]
    rows = [l for l in lines if not l.startswith('#')]
    if tr != ['# table_id %s' % tid]:
        sys.exit('packentries: the pack was generated against another bridge table: pack trailer %s, BRIDGE_TSV table_id %s' % (tr, tid))
    open(out, 'w').write(''.join(l + '\n' for l in rows) + '# table_id %s\n' % tid)


if __name__ == '__main__':
    import cli
    (a, b, c), _ = cli.parse('usage: packentries.py PACK_ENTRIES BRIDGE_TSV OUT\n  entries.tsv of the pack with table_id = sha256 of BRIDGE_TSV (the pack must agree)', sys.argv[1:], (3, 3))
    main(a, b, c)
