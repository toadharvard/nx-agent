#!/usr/bin/env python3
"""The drop rule in exe mode (universe.py --exe), by reference on a synthetic PE (manifest/pefixture.build_pe, no game bytes). .text: `jmp [eax*4 + TABLE]` (the table is marked by the jump-table
rule), a `ret` body, and the table at +0x100: word 0 = the body, word 1 starts 0f 04 (undecodable, relocated); a relocated word in .data holds TABLE+4 (R3 makes it an entry). The bytes at TABLE+4 are
class data-reloc (no instruction covers them, a relocation names them): the entry must be dropped with proof data-reloc:<site>, so that entries_on_data is 0."""
import os, sys, json, subprocess, tempfile, struct
HERE = os.path.dirname(os.path.abspath(__file__)); TOOLS = os.path.dirname(HERE); LIFT = os.path.dirname(TOOLS); sys.path.insert(0, os.path.join(os.path.dirname(LIFT), 'manifest'))
import pefixture
BASE = 0x400000; TEXT = BASE + 0x10000; TABLE = TEXT + 0x100; BODY = TEXT + 0x20; BODY2 = TEXT + 0x40f; DATA = BASE + 0x11000      # BODY2's low half is 0x040f: the table word that holds it starts 0f 04
img = bytearray(0x12000); T = 0x10000
img[T:T + 14] = b'\x83\xf8\x01\x77\x07' + b'\xff\x24\x85' + struct.pack('<I', TABLE) + b'\xc3'     # cmp eax,1; ja ret; jmp [eax*4 + TABLE]; ret: the bound makes the table two words
img[T + 0x20] = 0xc3; img[T + 0x40f] = 0xc3
struct.pack_into('<II', img, T + 0x100, BODY, BODY2)
struct.pack_into('<I', img, 0x11000, TABLE + 4)                         # R3: a value site, the entry TABLE+4 (the bytes 0f 04 41 00 are not an instruction)
pe = pefixture.build_pe(BASE, bytes(img), [TEXT + 8, TABLE, TABLE + 4, DATA], [], [(TEXT, 0x1000)])
with tempfile.TemporaryDirectory() as td:
    open(td + '/syn.exe', 'wb').write(pe)
    open(td + '/funcs.tsv', 'w').write('# decomp functions: address\tsize\n')
    open(td + '/keys.tsv', 'w').write('# table_id 0\n')
    open(td + '/udsail.tsv', 'w').write('# fnvnx-udsail/2\n')
    r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'universe.py'), '--out', td + '/out', '--exe', td + '/syn.exe', '--funcs', td + '/funcs.tsv', '--keys', td + '/keys.tsv', '--lib', LIFT,
                        '--baddecode', td + '/bad.tsv', '--shapes', td + '/shapes.tsv', '--udsail', td + '/udsail.tsv'], capture_output=True, text=True)
    out = td + '/out'
    assert os.path.exists(out + '/dropped_on_data.tsv'), r.stdout[-2000:] + r.stderr[-2000:]
    uni = {l.split('\t')[0] for l in open(out + '/universe.tsv') if l[0] != '#' and l.strip()}
    dr = [l.split('\t') for l in open(out + '/dropped_on_data.tsv') if l[0] != '#' and l.strip()]
    assert '%08x' % (TABLE + 4) not in uni, 'the entry on data is still in the universe'
    assert dr and dr[0][0] == '%08x' % (TABLE + 4) and dr[0][1].strip().startswith('data-reloc:'), (dr, r.stdout[-1500:])
print('ok: exe-mode entry on proven data dropped with proof', dr[0][1].strip())
