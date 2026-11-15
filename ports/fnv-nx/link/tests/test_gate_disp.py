#!/usr/bin/env python3
"""Synthetic check of link/gate_disp.py (typed dispatch tables): ELF32 fixtures built with struct (no toolchain, no private data).
Cases: rows equal (green, with a RELR relocated image and with a fixed image); a slot differs; a row is missing (a slot without a row); a slot is
missing (a row without a slot); the rows file is missing; null slots; an unrelocated slot; an undefined symbol; two tables; the universe; conflicts."""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, '..', 'gate_disp.py')
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.append(os.path.join(HERE, '..', '..', 'verify'))
import scratch
scratch.install()

fails = []
TA, TB = 0x2000, 0x2100                  # tables T_a (4 slots), T_b (2 slots)
BODY = {'e0': 0x1000, 'e1': 0x1040, 'e2': 0x1080, 'e3': 0x10c0}
ROWS_A = [('T_a', 0, 0x401000, 'e0'), ('T_a', 1, 0x401020, 'e1'), ('T_a', 2, 0x401040, '-'), ('T_a', 3, 0x401060, 'e2')]
ROWS_B = [('T_b', 0, 0x402000, 'e3'), ('T_b', 1, 0x402020, 'e0')]


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def elf(tables, syms, relr=None):
    """tables {name: (address, [u32 words], size or None)}; syms [(name, addr)]; relr list of vaddrs (a SHT_RELR section) or None for a fixed image."""
    img = bytearray(0x3000)
    allsyms = list(syms)
    for n, (a, words, size) in tables.items():
        struct.pack_into('<%dI' % len(words), img, a, *words)
        allsyms.append((n, a, 4 * len(words) if size is None else size))
    strtab, recs = b'\0', [struct.pack('<IIIBBH', 0, 0, 0, 0, 0, 0)]
    for t in allsyms:
        n, v, size = t if len(t) == 3 else (t + (0,))
        recs.append(struct.pack('<IIIBBH', len(strtab), v, size, (1 << 4) | 1, 0, 1))
        strtab += n.encode() + b'\0'
    symtab = b''.join(recs)
    relrb = b''
    if relr is not None:
        relrb = b''.join(struct.pack('<I', a) for a in sorted(relr))     # address entries only (each its own entry)
    shstr = b'\0.text\0.symtab\0.strtab\0.shstrtab\0.relr.dyn\0'
    o_sym = 0x3000 + 52
    o_str = o_sym + len(symtab)
    o_shs = o_str + len(strtab)
    o_rel = o_shs + len(shstr)
    shoff = o_rel + len(relrb)

    def sh(name, typ, off, size, link=0, ent=0):
        return struct.pack('<IIIIIIIIII', name, typ, 0, 0, off, size, link, 0, 4, ent)
    shdrs = sh(0, 0, 0, 0) + sh(1, 1, 0, 0x3000) + sh(7, 2, o_sym, len(symtab), 3, 16) + sh(15, 3, o_str, len(strtab)) + sh(23, 3, o_shs, len(shstr))
    if relr is not None:
        shdrs += sh(33, 19, o_rel, len(relrb), 0, 4)
    ph = struct.pack('<IIIIIIII', 1, 0, 0, 0, 0x3000, 0x3000, 5, 0x1000)
    img[52:84] = ph
    hdr = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 3, 183, 1, 0, 52, shoff, 0, 52, 32, 1, 40, 6 if relr is not None else 5, 4)
    img[0:52] = hdr
    return bytes(img) + bytes(52) + symtab + strtab + shstr + relrb + shdrs


d = tempfile.mkdtemp()


def write(name, text):
    p = os.path.join(d, name)
    with open(p, 'w', newline='\n') as fh:
        fh.write(text)
    return p


def words(rows, base, ov=None):
    ov = ov or {}
    return [0 if r[3] == '-' else ov.get(r[1], BODY[r[3]]) for r in rows]


def run(tables=None, rows=None, relr='auto', universe=None, rows_file=True, extra_syms=(), syms=None, hdr='# table_id abc123\n'):
    tables = tables if tables is not None else {'T_a': (TA, words(ROWS_A, TA), None), 'T_b': (TB, words(ROWS_B, TB), None)}
    rows = ROWS_A + ROWS_B if rows is None else rows
    if relr == 'auto':
        relr = [TA, TA + 4, TA + 12, TB, TB + 4]          # every non-null slot
    syms = syms if syms is not None else list(BODY.items())
    with open(os.path.join(d, 'g.elf'), 'wb') as fh:
        fh.write(elf(tables, syms + list(extra_syms), relr))
    cmd = [sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf'), '--rows', os.path.join(d, 'rows.tsv')]
    if rows_file:
        write('rows.tsv', hdr + ''.join('%s\t%d\t%08x\t%s\n' % r for r in rows))
    elif os.path.exists(os.path.join(d, 'rows.tsv')):
        os.remove(os.path.join(d, 'rows.tsv'))
    if universe is not None:
        cmd += ['--universe', write('u.tsv', '# base\n' + ''.join('%08x\tdecomp\n' % a for a in universe))]
    go = os.path.join(d, 'gate')
    os.makedirs(go, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, GATE_OUT=go))
    if not os.path.exists(os.path.join(go, 'disp.json')):
        sys.exit('gate_disp failed: ' + r.stderr[-600:])
    with open(os.path.join(go, 'disp.json')) as fh:
        j = json.load(fh)
    with open(os.path.join(go, 'disp.ids')) as fh:
        j['ids'] = fh.read().split('\n')[:-1]
    os.remove(os.path.join(go, 'disp.json'))
    return r.returncode, j


rc, g = run()
check('rows equal', (rc, g['status'], g['universe'], g['certified'], g['uncertified'], g['ids']), (0, 'green', 8, 8, [], []))
rc, g = run(relr=None)
check('fixed image (no relocation section) is green', (rc, g['status']), (0, 'green'))
# a slot differs
w = words(ROWS_A, TA)
w[1] = BODY['e2']
rc, g = run({'T_a': (TA, w, None), 'T_b': (TB, words(ROWS_B, TB), None)})
check('slot differs', (rc, g['uncertified']), (1, ['T_a[1]:wrong-target']))
# a slot without a row (the table has one more word than rows) and a row without a slot (one word fewer)
rc, g = run({'T_a': (TA, words(ROWS_A, TA) + [BODY['e0']], None), 'T_b': (TB, words(ROWS_B, TB), None)})
check('slot without a row', (rc, g['uncertified'][:1], g['status']), (1, ['T_a:table-size'], 'red'))
rc, g = run({'T_a': (TA, words(ROWS_A, TA)[:3], None), 'T_b': (TB, words(ROWS_B, TB), None)})
check('row without a slot', (rc, g['uncertified'][:1]), (1, ['T_a:table-size']))
# a row is missing: the index space has a gap
rc, g = run(rows=[r for r in ROWS_A + ROWS_B if r[:2] != ('T_a', 1)])
check('row missing', (rc, g['uncertified'][:1]), (1, ['T_a:index-gap']))
rc, g = run(rows=ROWS_A + ROWS_B + [ROWS_B[0]])
check('duplicate row', (rc, g['uncertified'][:1]), (1, ['T_b:duplicate-row']))
# the rows file is missing / does not parse
rc, g = run(rows_file=False)
check('rows file missing', (rc, g['status'], g['uncertified']), (1, 'red', ['rows:no-rows']))
write('bad.tsv', 'T_a\tx\n')
rc = subprocess.run([sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf'), '--rows', os.path.join(d, 'bad.tsv')], capture_output=True,
                    env=dict(os.environ, GATE_OUT=os.path.join(d, 'gate'))).returncode
check('rows file malformed', rc, 1)
# no ELF
rc = subprocess.run([sys.executable, '-I', GATE, '--elf', os.path.join(d, 'none.elf'), '--rows', os.path.join(d, 'rows.tsv')], capture_output=True,
                    env=dict(os.environ, GATE_OUT=os.path.join(d, 'gate'))).returncode
check('no elf', rc, 1)
# null slots
w = words(ROWS_A, TA)
w[2] = BODY['e1']
rc, g = run({'T_a': (TA, w, None), 'T_b': (TB, words(ROWS_B, TB), None)})
check('null slot nonzero', (rc, g['uncertified']), (1, ['T_a[2]:null-slot-nonzero']))
rc, g = run(relr=[TA, TA + 4, TA + 8, TA + 12, TB, TB + 4])
check('null slot relocated', (rc, g['uncertified']), (1, ['T_a[2]:null-slot-relocated']))
# unrelocated slot
rc, g = run(relr=[TA, TA + 12, TB, TB + 4])
check('unrelocated', (rc, g['uncertified']), (1, ['T_a[1]:unrelocated']))
# symbols
rc, g = run(syms=[s for s in BODY.items() if s[0] != 'e2'])
check('undefined symbol', (rc, g['uncertified']), (1, ['T_a[3]:symbol-undefined']))
rc, g = run(extra_syms=[('e2', 0x9000)])
check('ambiguous symbol', (rc, g['uncertified']), (1, ['T_a[3]:symbol-ambiguous']))
rc, g = run(tables={'T_b': (TB, words(ROWS_B, TB), None)})
check('table undefined', (rc, g['uncertified'][:1], g['certified']), (1, ['T_a:table-undefined'], 3))
# conflict: one guest address, two symbols, in one table
rc, g = run(rows=[('T_a', 0, 0x401000, 'e0'), ('T_a', 1, 0x401000, 'e1'), ('T_a', 2, 0x401040, '-'), ('T_a', 3, 0x401060, 'e2')] + ROWS_B)
check('entry conflict', (rc, g['uncertified']), (1, ['T_a[1]:entry-conflict']))
# universe
rc, g = run(universe=[0x401000, 0x402020, 0x401060])
check('universe covered', (rc, g['status'], g['universe']), (0, 'green', 11))
rc, g = run(universe=[0x401000, 0x401040, 0x409999])
check('universe address without non-null row', (rc, g['uncertified']), (1, ['00401040:no-entry', '00409999:no-entry']))

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
