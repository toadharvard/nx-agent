#!/usr/bin/env python3
"""Check of link/gate_abs.py (H3a): a relocation to an absolute symbol is red, in a loose object and in an archive member; so are an absolute
definition and a reference to a name the linked image defines as absolute; clean inputs are green.  Needs the AArch64 cross toolchain
(as test_link_synth.py)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import gate_abs
import mutate
import realobj
import relocs

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


d = tempfile.mkdtemp()


def put(name, data):
    p = os.path.join(d, name)
    with open(p, 'wb') as fh:
        fh.write(data)
    return p


def build(src, **kw):
    res = realobj.build({os.path.splitext(src)[0]: open(os.path.join(realobj.FIXDIR, src)).read()}, **kw)
    if res['status'] != 'green':
        print('FAIL real-object build %s: %s' % (src, res.get('failure')))
        sys.exit(1)
    return res


def ar(members):
    """a deterministic GNU `ar` archive (the format of `ar Dcr`: no symbol table, short names, member data padded to 2) of [(name, bytes)]"""
    out = b'!<arch>\n'
    for n, data in members:
        out += (n + '/').ljust(16).encode() + b'0'.ljust(12) + b'0'.ljust(6) + b'0'.ljust(6) + b'644'.ljust(8) + str(len(data)).ljust(10).encode() + b'`\n'
        out += data + (b'\n' if len(data) % 2 else b'')
    return out


CLEAN = open(build('abs_clean.cc')['objects'][0]['object'], 'rb').read()
clean = put('clean.o', CLEAN)
# an absolute symbol: the undefined global `ext` (value 0) turned SHN_ABS in the symbol table of the real object; a relocation without a symbol: entry 0 of the
# relocation table (the assembler's folded absolute value)
absref = put('absref.o', mutate.set_symbol_shndx(CLEAN, 'ext', 0xfff1))
nosym = put('nosym.o', mutate.zero_reloc_symbol(CLEAN, 0))
LINK = 'SECTIONS { scr = 0x1000; . = 0x10000; .text : { *(.text*) } .data : { *(.data*) *(.bss*) } /DISCARD/ : { *(.comment) *(.note*) *(.fnvnx*) } }\n'
img = build('abs_ref.cc', link_script=LINK)
scriptref = img['objects'][0]['object']
elf = img['elf']['path']

uni, red = gate_abs.check([clean], [])
check('clean is green', red, [])
nrel = len(relocs.scan('clean.o', CLEAN)[0])
nsym = len([s for s in relocs.scan('clean.o', CLEAN)[1] if s[0] and s[2] not in (relocs.STT_SECTION, relocs.STT_FILE) and s[3] != 0])
check('clean universe = relocations + defined symbols (counted by relocs.scan)', (uni, uni > 0), (nrel + nsym, True))
uni, red = gate_abs.check([absref], [])
rel_ids = [r for r in red if ':def:' not in r]
check('absolute reference and definition', (sorted(r for r in red if ':def:' in r), len(rel_ids) >= 1, all(r.endswith(':ext') for r in rel_ids)),
      (['absref.o:def:ext'], True, True))
check('relocation without a symbol', [r.endswith(':*ABS*') for r in gate_abs.check([nosym], [])[1]], [True])
# an archive member: the id names archive(member)
lib = put('lib.a', ar([('clean.o', CLEAN), ('absref.o', open(absref, 'rb').read())]))
check('archive member', gate_abs.check([lib], [])[1], ['lib.a(absref.o):' + r.split(':', 1)[1] for r in gate_abs.check([absref], [])[1]])
# the linked image of the real build: its linker script defines `scr` as an absolute symbol; the object that refers to it
names = relocs.abs_names(elf)
check('abs names of the linked image', 'scr' in names, True)
check('reference to a name the image defines as absolute', [r.split(':')[-1] for r in gate_abs.check([scriptref], names)[1]], ['scr', 'scr'])
check('same input, nothing absolute in the image', gate_abs.check([scriptref], [])[1], [])
check('the real object defines nothing absolute', gate_abs.check([scriptref], [])[0] > 0, True)
# the gate file: format, name and the full id list
go = os.path.join(d, 'go')
os.makedirs(go)
rsp = os.path.join(d, 'in.rsp')
with open(rsp, 'w') as fh:
    fh.write('"%s"\n"%s"\n' % (clean, lib))
r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_abs.py'), '--inputs', rsp], env=dict(os.environ, GATE_OUT=go), capture_output=True, text=True)
check('exit status red', r.returncode, 1)
with open(os.path.join(go, 'abs.json')) as fh:
    j = json.load(fh)
check('gate file', (j['name'], j['status'], j['format'], j['universe'] - j['certified'], len(j['uncertified'])), ('abs', 'red', 'fnvnx-gate/1', len(red), len(red)))
check('gate file ids are those of the archive member', j['uncertified'], sorted(r for r in ['lib.a(absref.o):' + x.split(':', 1)[1] for x in red]))
with open(os.path.join(go, 'abs.ids')) as fh:
    check('ids file', fh.read().split('\n')[:-1], sorted(j['uncertified']))
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
