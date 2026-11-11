#!/usr/bin/env python3
"""Synthetic check of link/ld.py: the ELF32 symbol reader, the undefined-symbol rule and the command line it builds."""
import os, struct, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import ld

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def elf(syms, cls=1, machine=183):
    """A minimal ELF32 little-endian file with .symtab/.strtab; syms = [(name, shndx, bind, type)]."""
    strtab = b'\0'
    recs = [struct.pack('<IIIBBH', 0, 0, 0, 0, 0, 0)]
    for name, shndx, bind, typ in syms:
        recs.append(struct.pack('<IIIBBH', len(strtab), 0, 0, (bind << 4) | typ, 0, shndx))
        strtab += name.encode() + b'\0'
    symtab = b''.join(recs)
    shstr = b'\0.symtab\0.strtab\0.shstrtab\0'
    body = symtab + strtab + shstr
    o_sym, o_str, o_shs = 52, 52 + len(symtab), 52 + len(symtab) + len(strtab)
    shoff = 52 + len(body)

    def sh(name, typ, off, size, link=0, ent=0):
        return struct.pack('<IIIIIIIIII', name, typ, 0, 0, off, size, link, 0, 4, ent)
    shdrs = sh(0, 0, 0, 0) + sh(1, 2, o_sym, len(symtab), 2, 16) + sh(9, 3, o_str, len(strtab)) + sh(17, 3, o_shs, len(shstr))
    hdr = b'\x7fELF' + bytes([cls, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 3, machine, 1, 0, 0, shoff, 0, 52, 0, 0, 40, 4, 3)
    return hdr + body + shdrs


d = tempfile.mkdtemp()
p = os.path.join(d, 'a.elf')
open(p, 'wb').write(elf([('local', 1, 0, 2), ('defined', 1, 1, 2), ('missing', 0, 1, 0), ('weak_missing', 0, 2, 0), ('abs', 0xfff1, 1, 1)]))
s = ld.symbols(p)
check('symbols', s, [('abs', True), ('defined', True), ('missing', False), ('weak_missing', False)])
check('undefined', ld.undefined({'missing', 'weak_missing', 'script_sym', 'defined'}, [('defined', True), ('script_sym', True), ('missing', False)]),
      ['missing', 'weak_missing'])
check('no dynamic segment: not pie', ld.is_pie(p), False)
for flags, want in ((0x08000000, True), (1, False)):
    ph = struct.pack('<IIIIIIII', 2, 52 + 32, 0, 0, 16, 16, 6, 4)
    dyn = struct.pack('<IIII', 0x6ffffffb, flags, 0, 0)
    hdr = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 2, 183, 1, 0, 52, 0, 0, 52, 32, 1, 40, 0, 0)
    open(os.path.join(d, 'pie.elf'), 'wb').write(hdr + ph + dyn)
    check('pie flag %x' % flags, ld.is_pie(os.path.join(d, 'pie.elf')), want)
# hidden symbols become local in a link: still a definition; a plain local is not
hid = elf([('h', 1, 0, 2)])
i = hid.index(struct.pack('<BBH', 2, 0, 1), 52 + 16)
open(p, 'wb').write(hid[:i + 1] + b'\x02' + hid[i + 2:])
check('hidden local', ld.symbols(p), [('h', True)])
# archive: members are read, the index is skipped; a name defined in one member is defined
m1, m2 = elf([('f', 0, 1, 0), ('k', 0, 1, 0)]), elf([('f', 1, 1, 2)])
ar = b'!<arch>\n'
for nm, body in ((b'/', b'idx'), (b'm1.o/', m1), (b'm2.o/', m2)):
    ar += nm.ljust(16) + b'0'.ljust(12) + b'0'.ljust(6) + b'0'.ljust(6) + b'644'.ljust(8) + str(len(body)).encode().ljust(10) + b'`\n' + body + (b'\n' if len(body) & 1 else b'')
open(os.path.join(d, 'l.a'), 'wb').write(ar)
check('archive', ld.symbols(os.path.join(d, 'l.a')), [('f', True), ('k', False)])
open(p, 'wb').write(elf([('local', 1, 0, 2)]))
for bad, why in ((elf([], cls=2), 'ELF32'), (elf([], machine=3), 'AArch64'), (b'MZ' + bytes(100), 'ELF')):
    open(p, 'wb').write(bad)
    try:
        ld.symbols(p)
        fails.append('accepted a file that is not %s' % why)
    except ValueError:
        pass

# inputs: every *.o and *.a below the given directories, sorted by path relative to its directory; a name twice is an error
os.makedirs(os.path.join(d, 'x', 'sub'))
os.makedirs(os.path.join(d, 'y'))
for f in ('x/b.o', 'x/sub/a.o', 'x/lib.a', 'x/notes.txt', 'y/c.o'):
    open(os.path.join(d, f), 'w').close()
ins = ld.inputs([os.path.join(d, 'x'), os.path.join(d, 'y')])
check('inputs', [os.path.relpath(i, d) for i in ins], ['x/b.o', 'x/lib.a', 'x/sub/a.o', 'y/c.o'])
open(os.path.join(d, 'y', 'b.o'), 'w').close()
try:
    ld.inputs([os.path.join(d, 'x'), os.path.join(d, 'y')])
    fails.append('two inputs named b.o accepted')
except ValueError:
    pass
cmd = ld.command(['/t/ld', '-m', 'aarch64elf32'], '/s/guest.ld', '/o/guest.elf', '/o/inputs.rsp')
check('command', cmd, ['/t/ld', '-m', 'aarch64elf32', '-pie', '--no-dynamic-linker', '-z', 'text', '-z', 'max-page-size=0x1000',
                       '-z', 'common-page-size=0x1000', '--unresolved-symbols=ignore-all', '--noinhibit-exec', '--whole-archive', '-T', '/s/guest.ld', '-o', '/o/guest.elf', '@/o/inputs.rsp'])

for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
