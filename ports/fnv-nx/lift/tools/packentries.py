#!/usr/bin/env python3
"""The `entries` stage of the product root lift/pack: entries.tsv of the pack, an output (ENTRIES) of the same build as the image.
    packentries.py PACK_ENTRIES IMP_ENTRIES BRIDGE_TSV MODULES_TSV OUT
PACK_ENTRIES = the external's entries.tsv (rows `addr<TAB>symbol<TAB>kind`, kinds code and runtime: what the lifter derives from the exe and modules alone, no import row, no
trailer); IMP_ENTRIES = the output of the impgen stage (first line `# table_id <sha256 of BRIDGE_TSV>`, rows `key<TAB>symbol<TAB>dll<TAB>name<TAB>bind`); BRIDGE_TSV = the
loader root's output of that name (contract/bridgegen.py), the one table the import keys index. The output holds the pack's rows, the import rows as kind `import`, sorted by
address, and the trailers `# table_id <sha256 of BRIDGE_TSV's bytes>` and `# module_table sha256 <hex of MODULES_TSV>`.
RULE one module table: PACK_ENTRIES ends with `# module_table sha256 <hex>` (tools/liftgen.py finish): the sha256 of the modules.tsv the lifter took its module set and bases from. The stage
is red when that stamp is missing, repeated, not the last line, or not the sha256 of MODULES_TSV (the loader root's output): a pack lifted against another module table than
the loader's cannot enter the build.
table_id has ONE computation, here, over the bytes of the table the build uses. The impgen output's header must equal it (the two stages read one table), and an address
must not be a row of both inputs: a mismatch or a collision is a red stage, never a silent merge."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


STAMP = '# module_table sha256 '


def stamp(entries, modules):
    """the module table stamp of the external's entries.tsv against the loader's MODULES_TSV; the stamp's hash, else SystemExit naming both hashes"""
    want = hashlib.sha256(open(modules, 'rb').read()).hexdigest()
    lines = [l for l in open(entries).read().split('\n') if l]
    st = [l[len(STAMP):] for l in lines if l.startswith(STAMP)]
    if len(st) != 1: sys.exit('packentries: entries.tsv has %d module_table stamps (exactly one needed): the pack was not lifted against a module table' % len(st))
    if lines[-1] != STAMP + st[0]: sys.exit('packentries: the module_table stamp is not the last line of entries.tsv')
    if st[0] != want: sys.exit('packentries: the pack was lifted against module table %s, the loader\'s is %s: stale pack' % (st[0], want))
    return want


def main(entries, imp, bridge, modules, out):
    mid = stamp(entries, modules)
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
    open(out, 'w').write(''.join('%08x\t%s\t%s\n' % (a, s, k) for a, (s, k) in sorted(rows.items())) + '# table_id %s\n# module_table sha256 %s\n' % (tid, mid))


if __name__ == '__main__':
    import fnvnx.lift.tools.cli as cli
    (a, b, c, m, d), _ = cli.parse('usage: packentries.py PACK_ENTRIES IMP_ENTRIES BRIDGE_TSV MODULES_TSV OUT\n  entries.tsv of the pack: the external\'s rows + the import rows, table_id = sha256 of BRIDGE_TSV, module_table = sha256 of MODULES_TSV (checked against the pack\'s stamp)', sys.argv[1:], (5, 5))
    main(a, b, c, m, d)
