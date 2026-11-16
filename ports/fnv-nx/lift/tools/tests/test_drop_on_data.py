#!/usr/bin/env python3
"""The drop rule (a pointer-only entry on a byte that no instruction covers and a relocation names is data, not an entry), on a synthetic PE built in the test with the one emitter
(manifest/pefixture.build_pe; no game bytes): .text holds `ret` at its start and a table of two relocated dwords at +0x100 whose first bytes (0f 04) no decoder accepts; a relocated word in
.data points at the table start. R3 makes the table start an entry; the byte is class data-reloc; the rule must remove the entry (modlift: refused at its fixed point, with the proof id of
the relocation that names the byte) so that entries_on_data is 0. Runs the real producer `universe.py --module` as a subprocess."""
import os, sys, json, hashlib, subprocess, tempfile, struct
HERE = os.path.dirname(os.path.abspath(__file__)); TOOLS = os.path.dirname(HERE); LIFT = os.path.dirname(TOOLS); sys.path.insert(0, os.path.join(os.path.dirname(LIFT), 'manifest'))
import pefixture
BASE = 0x10000000; TEXT = BASE + 0x1000; TABLE = TEXT + 0x100; DATA = BASE + 0x2000
img = bytearray(0x3000)
img[0x1000] = 0xc3
struct.pack_into('<II', img, 0x1100, 0x1000040f, TEXT)         # word 0 starts 0f 04 (undecodable) and lies outside .text; word 1 is the entry
struct.pack_into('<I', img, 0x2000, TABLE)                      # a data word that holds the table's address (R3: a value site, not a displacement)
pe = pefixture.build_pe(BASE, bytes(img), [TABLE, TABLE + 4, DATA], [], [(TEXT, 0x1000)])
with tempfile.TemporaryDirectory() as td:
    open(td + '/syn.dll', 'wb').write(pe)
    row = ['syn.dll', hashlib.sha256(pe).hexdigest(), str(len(pe)), '%08x' % BASE, '%08x' % BASE, '%08x' % 0x3000, '0', '%08x' % 0x1000, '0']
    open(td + '/modules.tsv', 'w').write('# synthetic\n' + '\t'.join(row) + '\n')
    open(td + '/keys.tsv', 'w').write('# table_id 0\n')
    open(td + '/udsail.tsv', 'w').write('# fnvnx-udsail/2\n')
    r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'universe.py'), '--out', td + '/out', '--module', 'syn.dll', '--modules', td + '/modules.tsv', '--moddirs', td, '--keys', td + '/keys.tsv', '--lib', LIFT, '--baddecode', td + '/bad.tsv', '--shapes', td + '/shapes.tsv', '--udsail', td + '/udsail.tsv'],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    d = td + '/out/syn'; g = json.load(open(d + '/gate_codemap.json'))
    uni = {l.split('\t')[0] for l in open(d + '/universe.tsv') if l[0] != '#' and l.strip()}
    dr = [l.split('\t') for l in open(d + '/dropped_on_data.tsv') if l[0] != '#' and l.strip()]
    assert g['entries_on_data'] == 0, g
    assert '%08x' % TABLE not in uni, 'the table start is still an entry'
    assert dr and dr[0][0] == '%08x' % TABLE and dr[0][1].strip() == 'data-reloc:%08x' % TABLE, dr
print('ok: entry on proven data dropped with proof', dr[0][1].strip())
