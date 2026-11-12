#!/usr/bin/env python3
"""Synthetic check of link/gate_disp.py: ELF fixtures built with struct (no toolchain, no private data) around a real perfect hash (found here
by search, K1 K2 NBITS TBITS g as rt.c uses them): the green case, a universe address without entry, a slot that points at the wrong symbol, an
extra key, a duplicate key, a key that sits in a slot other than the one the hash computes (hash-miss), a table whose stored parameters do not
match its slots (red), wrong symbol sizes, --keys, entries.tsv kinds, table_id and --missing."""
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
import gate_disp
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
TABLE = 0x2000      # fnvl_disp_table
BODY = 0x1000       # first body; body i at BODY + 0x40 * i
M = 0xffffffff
K1, K2, NB, TB = 0x9e3779b1, 0x85ebca6b, 4, 4       # 16 buckets, 16 slots
GOFF, POFF = TABLE + (8 << TB), TABLE + (8 << TB) + (2 << NB)   # fnvl_g and fnvl_phf_params after the table




def hslot(a, g, k1=K1, k2=K2, nb=NB, tb=TB):
    return ((((a ^ g[((a * k1) & M) >> (32 - nb)]) & M) * k2) & M) >> (32 - tb)


def phf(keys):
    """g (a u16 per bucket) with which the keys hash to distinct slots of 1 << TB: buckets by size, g by search."""
    g, used = [0] * (1 << NB), set()
    buckets = {}
    for k in sorted(keys):
        buckets.setdefault(((k * K1) & M) >> (32 - NB), []).append(k)
    for b in sorted(buckets, key=lambda b: (-len(buckets[b]), b)):
        for v in range(1 << 16):
            g[b] = v
            sl = [hslot(k, g) for k in buckets[b]]
            if len(set(sl)) == len(sl) and not set(sl) & used:
                used |= set(sl)
                break
        else:
            raise SystemExit('no g for bucket %d' % b)
    return g


def elf(slots, syms, g, params=(K1, K2, NB, TB), sizes=(8 << TB, 2 << NB, 16), with_params=True):
    """ELF32 LE AArch64 with one PT_LOAD [0, 0x3000) that holds the table at TABLE, fnvl_g and fnvl_phf_params after it, and a .symtab.
    slots [(key, offset)]; syms [(name, address)]; sizes the st_size of table, g, params."""
    img = bytearray(0x3000)
    for i, (k, off) in enumerate(slots):
        struct.pack_into('<Ii', img, TABLE + 8 * i, k, off)
    struct.pack_into('<%dH' % len(g), img, GOFF, *g)
    struct.pack_into('<IIII', img, POFF, *params)
    allsyms = list(syms) + [('fnvl_disp_table', TABLE, sizes[0]), ('fnvl_g', GOFF, sizes[1])] + ([('fnvl_phf_params', POFF, sizes[2])] if with_params else [])
    strtab = b'\0'
    recs = [struct.pack('<IIIBBH', 0, 0, 0, 0, 0, 0)]
    for t in allsyms:
        n, v, size = t if len(t) == 3 else (t + (0,))
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
    return bytes(img) + bytes(52) + symtab + strtab + shstr + shdrs


UNIVERSE = [0x00401000, 0x00401020, 0x0040a000, 0x00ff0000]
NAMES = {a: 'L_%08x' % a for a in UNIVERSE}
BODIES = {a: BODY + 0x40 * i for i, a in enumerate(UNIVERSE)}
IMPORTS = {0xf0000001: ('imp_a', 0x1800), 0xf0000002: ('imp_b', 0x1840)}
G_ALL = phf(UNIVERSE + sorted(IMPORTS) + [0x12345678])      # a hash that also holds the import keys and one extra key


def table(overrides=None, extra=(), place=None, drop=(), g=None):
    """slots of the 1 << TB-slot table: every key of the universe and `extra` at the slot the hash gives (g), offset to its body;
    overrides {key: symbol address}; place {key: slot} puts a key at another slot; drop: keys left out; empty slots hold an arbitrary key, offset 0."""
    g = g or G_ALL
    ov, place = overrides or {}, place or {}
    slots = [(0xfeed0000 + i, 0) for i in range(1 << TB)]
    for k in [a for a in UNIVERSE if a not in drop] + list(extra):
        tgt = ov.get(k, BODIES.get(k) or IMPORTS[k][1] if k in IMPORTS else BODIES.get(k, BODY))
        slots[place.get(k, hslot(k, g))] = (k, tgt - TABLE)
    return slots


d = tempfile.mkdtemp()


def write(name, text):
    p = os.path.join(d, name)
    with open(p, 'w', newline='\n') as fh:
        fh.write(text)
    return p


def run(slots, entries=None, keys=None, extra_syms=(), entries_extra='', keys_header='', missing=None, **elfkw):
    syms = [(NAMES[a], BODIES[a]) for a in UNIVERSE] + list(extra_syms)
    elfkw.setdefault('g', G_ALL)
    with open(os.path.join(d, 'g.elf'), 'wb') as fh:
        fh.write(elf(slots, syms, **elfkw))
    u = write('u.tsv', '# base\n' + ''.join('%08x\tdecomp\t10\tcall\n' % a for a in UNIVERSE))
    ents = NAMES if entries is None else entries
    e = write('e.tsv', ''.join('%08x\t%s\n' % (a, ents[a]) for a in sorted(ents)) + entries_extra)
    go = os.path.join(d, 'gate')
    os.makedirs(go, exist_ok=True)
    cmd = [sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf'), '--universe', u, '--entries', e]
    if keys is not None:
        cmd += ['--keys', write('k.tsv', keys_header + ''.join('%08x\t%s\n' % kv for kv in keys))]
    if missing is not None:
        cmd += ['--missing', write('m.tsv', ''.join('%08x\n' % a for a in missing))]
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, GATE_OUT=go))
    if not os.path.exists(os.path.join(go, 'disp.json')):
        sys.exit('gate_disp failed: ' + r.stderr[-600:])
    with open(os.path.join(go, 'disp.json')) as fh:
        j = json.load(fh)
    with open(os.path.join(go, 'disp.ids')) as fh:
        j['ids'] = fh.read().split('\n')[:-1]
    j['stdout'] = r.stdout
    return r.returncode, j


rc, g = run(table())
case('green', got=(rc, g['status'], g['universe'], g['certified'], g['uncertified'], g['ids']), want=(0, 'green', 4, 4, [], []))
# the lookup is the one of rt.c: every universe key hashes to its slot
case('hash', got=all(table()[hslot(a, G_ALL)][0] == a for a in UNIVERSE), want=True)
# a universe address without entry (and --missing is reporting only)
ents = dict(NAMES)
del ents[0x0040a000]
rc, g = run(table(), entries=ents)
case('no entry', got=(rc, g['status'], g['universe'], g['certified'], g['uncertified']), want=(1, 'red', 4, 3, ['0040a000:no-entry']))
rc, g = run(table(), entries=ents, missing=[0x0040a000, 0x00401000])
case('--missing reports and certifies nothing', got=(rc, g['uncertified'], 'missing list 2: 1 without entry, 1 with an entry; without entry and not listed 0' in g['stdout']),
      want=(1, ['0040a000:no-entry'], True))
rc, g = run(table(), entries=ents, missing=[])
case('--missing: a gap not listed is reported', got='without entry and not listed 1' in g['stdout'], want=True)
# an entry whose symbol the ELF does not define
ents = dict(NAMES)
ents[0x00401020] = 'L_deadbeef'
rc, g = run(table(), entries=ents)
case('undefined symbol', got=(rc, g['uncertified']), want=(1, ['00401020:symbol-undefined']))
# a slot that points at the wrong symbol (the body of another entry)
rc, g = run(table({0x00401020: BODIES[0x00ff0000]}))
case('wrong target', got=(rc, g['status'], g['certified'], g['uncertified'], g['ids']), want=(1, 'red', 3, ['00401020:wrong-target'], ['00401020:wrong-target']))
# one byte off (a stored offset that is right modulo 2^32 only)
slots = table()
i = hslot(0x00401000, G_ALL)
slots[i] = (slots[i][0], slots[i][1] + 1)
rc, g = run(slots)
case('off by one', got=(rc, g['uncertified']), want=(1, ['00401000:wrong-target']))
# an extra key (an occupied slot whose key is not an address of the universe)
rc, g = run(table(extra=[0x12345678]))
case('extra key', got=(rc, g['status'], g['universe'], g['certified'], g['uncertified']), want=(1, 'red', 5, 4, ['12345678:extra-key']))
# an empty slot (offset 0) is no key whatever its key says
slots = table()
j = next(i for i, (k, o) in enumerate(slots) if not o)
slots[j] = (0x12345678, 0)
rc, g = run(slots)
case('empty slot with a key', got=(rc, g['status']), want=(0, 'green'))
# a duplicate key: the key of 0x00401000 also sits in an empty slot
j = next(i for i, (k, o) in enumerate(table()) if not o)
rc, g = run([(s if i != j else (0x00401000, BODIES[0x00401000] - TABLE)) for i, s in enumerate(table())])
case('duplicate key', got=(rc, g['uncertified']), want=(1, ['00401000:duplicate-key']))
# a key whose slot is not the one the hash computes (the dispatcher would not find it)
rc, g = run(table(place={0x00401020: j}))
case('hash-miss', got=(rc, g['uncertified']), want=(1, ['00401020:hash-miss']))
# a key in the universe with no slot at all
rc, g = run(table(drop=[0x00ff0000]))
case('no slot', got=(rc, g['uncertified']), want=(1, ['00ff0000:no-slot']))
# the stored parameters are not those the slots were placed with: the proof of the lookup fails for the keys it misplaces
rc, g = run(table(), params=(K1, K2 ^ 0x10000, NB, TB))
case('parameters do not match the table', got=(rc, g['status'], g['certified'] < 4, all(u.endswith(':hash-miss') for u in g['uncertified'])), want=(1, 'red', True, True))
rc, g = run(table(), params=(K1 ^ 0x5a5a5a5a, K2, NB, TB))
case('K1 differs', got=(rc, g['status']), want=(1, 'red'))
# sizes and parameters outside the table
for name, kw in (('table size', dict(sizes=((8 << TB) - 8, 2 << NB, 16))), ('g size', dict(sizes=(8 << TB, (2 << NB) - 2, 16))),
                 ('params size', dict(sizes=(8 << TB, 2 << NB, 12))), ('NBITS 0', dict(params=(K1, K2, 0, TB))), ('TBITS 32', dict(params=(K1, K2, NB, 32))),
                 ('no params', dict(with_params=False))):
    rc, g = run(table(), **kw)
    case('red: %s', got=(rc, g['status'], g['certified'], g['uncertified'][:1]), want=(1, 'red', 0, ['00401000:no-table']), key=name)
# --keys: the import key set; the symbols of the bodies are named by entries.tsv rows of kind import
imp = [(k, v[0]) for k, v in sorted(IMPORTS.items())]
xs = [('imp_a', 0x1800), ('imp_b', 0x1840)]
irows = lambda names: ''.join('%08x\t%s\timport\n' % (k, n) for k, n in names)
rc, g = run(table(extra=sorted(IMPORTS)), keys=imp, extra_syms=xs, entries_extra=irows(imp))
case('keys green', got=(rc, g['status'], g['universe'], g['certified']), want=(0, 'green', 6, 6))
rc, g = run(table(extra=sorted(IMPORTS)), keys=imp, extra_syms=xs, entries_extra=irows(imp[:1]))
case('a key of --keys that no entries row names', got=(rc, g['uncertified']), want=(1, ['f0000002:no-entry']))
rc, g = run(table(extra=sorted(IMPORTS)), keys=imp, extra_syms=xs, entries_extra=irows([imp[0], (0xf0000002, 'imp_a')]))
case('key names the wrong symbol', got=(rc, g['uncertified']), want=(1, ['f0000002:wrong-target']))
rc, g = run(table(), extra_syms=[('imp_a', 0x1800)], entries_extra='00401000\timp_a\tcode\n')
case('entry conflict: two rows name two symbols', got=(rc, g['uncertified']), want=(1, ['00401000:entry-conflict']))
# entries.tsv third column: kind import makes the row a key that is not a universe address
ents = dict(NAMES)
ents.update({0xf0000001: 'imp_a', 0xf0000002: 'imp_b'})
rc, g = run(table(extra=sorted(IMPORTS)), entries={a: s for a, s in ents.items()}, extra_syms=xs)
case('import rows without the kind column are not keys: the slots are extra keys', got=(rc, g['uncertified']), want=(1, ['f0000001:extra-key', 'f0000002:extra-key']))
e = write('e2.tsv', ''.join('%08x\t%s\tcode\n' % (a, NAMES[a]) for a in sorted(NAMES)) + ''.join('%08x\t%s\timport\n' % (k, IMPORTS[k][0]) for k in sorted(IMPORTS)) +
          '# table_id abc123\n')
# (run() writes its own entries file, so call the gate directly for the kind column)
with open(os.path.join(d, 'g.elf'), 'wb') as fh:
    fh.write(elf(table(extra=sorted(IMPORTS)), [(NAMES[a], BODIES[a]) for a in UNIVERSE] + xs, g=G_ALL))
u = write('u.tsv', ''.join('%08x\tdecomp\t10\tcall\n' % a for a in UNIVERSE))
go = os.path.join(d, 'gate2')
os.makedirs(go, exist_ok=True)


def direct(*extra):
    r = subprocess.run([sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf'), '--universe', u, '--entries', e] + list(extra), capture_output=True, text=True,
                       env=dict(os.environ, GATE_OUT=go))
    with open(os.path.join(go, 'disp.json')) as fh:
        return r.returncode, json.load(fh)


rc, g = direct()
case('kind import', got=(rc, g['status'], g['universe'], g['certified']), want=(0, 'green', 6, 6))
# table_id: equal in entries.tsv and keys.tsv is green and counted, different is red
k_same = write('k_same.tsv', '# table_id abc123\n')
k_diff = write('k_diff.tsv', '# table_id fff999\n')
rc, g = direct('--keys', k_same)
case('table_id equal', got=(rc, g['status'], g['universe'], g['certified']), want=(0, 'green', 7, 7))
rc, g = direct('--keys', k_diff)
case('table_id differs', got=(rc, g['status'], g['uncertified']), want=(1, 'red', ['table_id']))

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
