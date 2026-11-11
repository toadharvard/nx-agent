#!/usr/bin/env python3
"""Synthetic check of link/gate_disp.py: ELF fixtures built with struct (no toolchain, no private data) for the green case, a
universe address without entry, a slot that points at the wrong symbol, an extra key and a duplicate key, plus --keys."""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, '..', 'gate_disp.py')
sys.path.insert(0, os.path.join(HERE, '..'))
import gate_disp

fails = []
TABLE = 0x2000      # fnvl_disp_table
BODY = 0x1000       # first body; body i at BODY + 0x40 * i
EMPTY = 0xffffffff


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def elf(slots, syms, with_size=False, with_g=True):
    """ELF32 LE AArch64 with one PT_LOAD [0, 0x3000) that holds the table at TABLE, and a .symtab.
    slots [(key, offset)]; syms [(name, address)]; the table symbol gets st_size only when with_size."""
    img = bytearray(0x3000)
    for i, (k, off) in enumerate(slots):
        struct.pack_into('<Ii', img, TABLE + 8 * i, k, off)
    allsyms = list(syms) + [('fnvl_disp_table', TABLE)] + ([('fnvl_g', TABLE + 8 * len(slots))] if with_g else [])
    strtab = b'\0'
    recs = [struct.pack('<IIIBBH', 0, 0, 0, 0, 0, 0)]
    for n, v in allsyms:
        size = 8 * len(slots) if (with_size and n == 'fnvl_disp_table') else 0
        recs.append(struct.pack('<IIIBBH', len(strtab), v, size, (1 << 4) | 1, 0, 1))
        strtab += n.encode() + b'\0'
    symtab = b''.join(recs)
    shstr = b'\0.text\0.symtab\0.strtab\0.shstrtab\0'
    o_sym = 0x3000 + 52
    o_str = o_sym + len(symtab)
    o_shs = o_str + len(strtab)
    shoff = o_shs + len(shstr)

    def sh(name, typ, off, size, link=0, ent=0):
        return struct.pack('<IIIIIIIIII', name, typ, 0, 0, off, size, link, 0, 4, ent)
    shdrs = (sh(0, 0, 0, 0) + sh(1, 1, 0, 0x3000) + sh(7, 2, o_sym, len(symtab), 3, 16) + sh(15, 3, o_str, len(strtab)) +
             sh(23, 3, o_shs, len(shstr)))
    # file layout: [ELF header (52) | one program header (32) at 52] is inside the first 84 bytes of the image, which are zero
    ph = struct.pack('<IIIIIIII', 1, 0, 0, 0, 0x3000, 0x3000, 5, 0x1000)
    img[52:84] = ph
    hdr = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 3, 183, 1, 0, 52, shoff, 0, 52, 32, 1, 40, 5, 4)
    img[0:52] = hdr
    # section headers and tables follow the image; the symtab offsets above start after it (0x3000 + 52 is a fixed gap)
    return bytes(img) + bytes(52) + symtab + strtab + shstr + shdrs


UNIVERSE = [0x00401000, 0x00401020, 0x0040a000, 0x00ff0000]
NAMES = {a: 'L_%08x' % a for a in UNIVERSE}
BODIES = {a: BODY + 0x40 * i for i, a in enumerate(UNIVERSE)}


def table(overrides=None, extra=(), dup=None):
    """slots: the four keys in hash-free slot order with two empty slots; overrides {key: symbol address}."""
    ov = overrides or {}
    slots = []
    for a in UNIVERSE:
        slots.append((a, ov.get(a, BODIES[a]) - TABLE))
        if a == UNIVERSE[1]:
            slots.append((EMPTY, 0))
    for k in extra:
        slots.append((k, BODIES[UNIVERSE[0]] - TABLE))
    slots.append((EMPTY, 0))
    if dup is not None:
        slots[dup[0]] = (dup[1], BODIES[dup[1]] - TABLE)
    return slots


d = tempfile.mkdtemp()


def write(name, text):
    p = os.path.join(d, name)
    with open(p, 'w', newline='\n') as fh:
        fh.write(text)
    return p


def run(slots, entries=None, keys=None, with_size=False, with_g=True, extra_syms=()):
    syms = [(NAMES[a], BODIES[a]) for a in UNIVERSE] + list(extra_syms)
    with open(os.path.join(d, 'g.elf'), 'wb') as fh:
        fh.write(elf(slots, syms, with_size, with_g))
    u = write('u.tsv', '# base\n' + ''.join('%08x\tdecomp\t10\tcall\n' % a for a in UNIVERSE))
    ents = NAMES if entries is None else entries
    e = write('e.tsv', ''.join('%08x\t%s\n' % (a, ents[a]) for a in sorted(ents)))
    go = os.path.join(d, 'gate')
    os.makedirs(go, exist_ok=True)
    cmd = [sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf'), '--universe', u, '--entries', e]
    if keys is not None:
        cmd += ['--keys', write('k.tsv', ''.join('%08x\t%s\n' % kv for kv in keys))]
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, GATE_OUT=go))
    with open(os.path.join(go, 'disp.json')) as fh:
        return r.returncode, json.load(fh)


# green, with the table symbol sized and not sized
for sized in (False, True):
    rc, g = run(table(), with_size=sized)
    check('green rc %s' % sized, rc, 0)
    check('green %s' % sized, (g['status'], g['universe'], g['certified'], g['uncertified']), ('green', 4, 4, []))
rc, g = run(table(), with_size=True, with_g=False)
check('sized table needs no fnvl_g', (rc, g['status']), (0, 'green'))
rc, g = run(table(), with_g=False)
check('no size and no fnvl_g is red', (rc, g['status'], g['certified']), (1, 'red', 0))
# a universe address without entry
ents = dict(NAMES)
del ents[0x0040a000]
rc, g = run(table(), entries=ents)
check('no entry', (rc, g['status'], g['universe'], g['certified'], g['uncertified']), (1, 'red', 4, 3, ['0040a000:no-entry']))
# an entry whose symbol the ELF does not define
ents = dict(NAMES)
ents[0x00401020] = 'L_deadbeef'
rc, g = run(table(), entries=ents)
check('undefined symbol', (rc, g['uncertified']), (1, ['00401020:symbol-undefined']))
# a slot that points at the wrong symbol (the body of another entry)
rc, g = run(table({0x00401020: BODIES[0x00ff0000]}))
check('wrong target', (rc, g['status'], g['certified'], g['uncertified']), (1, 'red', 3, ['00401020:wrong-target']))
# one byte off (a stored offset that is right modulo 2^32 only)
slots = table()
slots[0] = (slots[0][0], slots[0][1] + 1)
rc, g = run(slots)
check('off by one', (rc, g['uncertified']), (1, ['00401000:wrong-target']))
# an extra key (a table key that is not an address of the universe)
rc, g = run(table(extra=[0x12345678]))
check('extra key', (rc, g['status'], g['universe'], g['certified'], g['uncertified']), (1, 'red', 5, 4, ['12345678:extra-key']))
# a duplicate key: two slots with the key 0x00401000 (the slot of 0x00401020 overwritten); both keys are then bad
rc, g = run(table(dup=(1, 0x00401000)))
check('duplicate key', (rc, g['status'], g['uncertified']), (1, 'red', ['00401000:duplicate-key', '00401020:no-slot']))
# a key in the universe with no slot at all
slots = [s for s in table() if s[0] != 0x00ff0000]
rc, g = run(slots)
check('no slot', (rc, g['uncertified']), (1, ['00ff0000:no-slot']))
# --keys: import keys outside the universe are certified, and are an extra key when they are not in --keys
imp = [(0xf0000001, 'imp_a'), (0xf0000002, 'imp_b')]
slots = table(extra=[0xf0000001, 0xf0000002])
slots[-3] = (0xf0000001, 0x1800 - TABLE)
slots[-2] = (0xf0000002, 0x1840 - TABLE)
rc, g = run(slots, keys=imp, extra_syms=[('imp_a', 0x1800), ('imp_b', 0x1840)])
check('keys green', (rc, g['status'], g['universe'], g['certified']), (0, 'green', 6, 6))
rc, g = run(slots, keys=imp[:1], extra_syms=[('imp_a', 0x1800), ('imp_b', 0x1840)])
check('key missing from --keys', (rc, g['uncertified']), (1, ['f0000002:extra-key']))
rc, g = run(slots, keys=[(0xf0000001, 'imp_a'), (0xf0000002, 'imp_a')], extra_syms=[('imp_a', 0x1800), ('imp_b', 0x1840)])
check('key names the wrong symbol', (rc, g['uncertified']), (1, ['f0000002:wrong-target']))
# a key that is in the universe and in --keys must name the same symbol in both files
rc, g = run(table(), keys=[(0x00401000, 'imp_a')], extra_syms=[('imp_a', 0x1800)])
check('entry conflict', (rc, g['uncertified']), (1, ['00401000:entry-conflict']))
# the stored offset is a signed 32-bit value: a body below the table address has a negative one
check('table slots', gate_disp.table_slots({'fnvl_disp_table': {TABLE}, 'fnvl_g': {TABLE + 16}}, {'fnvl_disp_table': 0}, [(0, 0, 0x3000)],
                                           elf([(1, -4), (EMPTY, 0)], [])), (TABLE, [(1, -4), (EMPTY, 0)]))

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
