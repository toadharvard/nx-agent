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
check('command', cmd, ['/t/ld', '-m', 'aarch64elf32', '-pie', '--no-dynamic-linker', '-z', 'text', '-z', 'now', '-z', 'pack-relative-relocs',
                       '-z', 'max-page-size=0x1000', '-z', 'common-page-size=0x1000', '--build-id=sha1', '--unresolved-symbols=ignore-all', '--noinhibit-exec',
                       '-T', '/s/guest.ld', '-o', '/o/guest.elf', '--whole-archive', '@/o/inputs.rsp', '--no-whole-archive'])
cmd = ld.command(['/t/ld'], '/s/guest.ld', '/o/guest.elf', '/o/inputs.rsp', ['/l1', '/l2'], ['gcc', 'c'], ['/l1/crti.o', '/l1/crtbegin.o'], ['/l1/crtend.o', '/l1/crtn.o'], '/o/m.map')
check('command with libraries: the driver order, ours whole, libraries searched, no defsym, no gc-sections',
      cmd[cmd.index('-Map'):], ['-Map', '/o/m.map', '/l1/crti.o', '/l1/crtbegin.o', '-L/l1', '-L/l2', '--whole-archive', '@/o/inputs.rsp', '--no-whole-archive',
                                '--start-group', '-lgcc', '-lc', '--end-group', '/l1/crtend.o', '/l1/crtn.o'])
check('no defsym, no gc-sections', [c for c in cmd if 'defsym' in c or 'gc-sections' in c], [])


def guest_elf(rela=(), relr=(), magic=True, pie=True, entry=0, extra_dyn=()):
    """A minimal guest image: LOAD text (vaddr 0, the crt0 header words) and LOAD data (vaddr 0x1000: .dynamic, RELA, RELR; bss to 0x2300, so the span is 0x3000)."""
    text = struct.pack('<IIIIIIII', 0, 0, 0x47584E46 if magic else 0, 3, 0, 0, 0, 0)
    dyn_n = 6 + len(extra_dyn)
    rela_b = b''.join(struct.pack('<IIi', *r) for r in rela)
    relr_b = b''.join(struct.pack('<I', e) for e in relr)
    dv = 0x1000
    rv, rrv = dv + 8 * dyn_n, dv + 8 * dyn_n + len(rela_b)
    dyn = struct.pack('<II', 0x6ffffffb, 0x08000000 if pie else 0) + struct.pack('<II', 7, rv) + struct.pack('<II', 8, len(rela_b)) + \
        struct.pack('<II', 36, rrv) + struct.pack('<II', 35, len(relr_b)) + b''.join(struct.pack('<II', *t) for t in extra_dyn) + struct.pack('<II', 0, 0)
    data = dyn + rela_b + relr_b
    t_off, d_off = 0x100, 0x100 + len(text)
    phs = [struct.pack('<IIIIIIII', 1, t_off, 0, 0, len(text), len(text), 5, 0x1000),
           struct.pack('<IIIIIIII', 1, d_off, dv, dv, len(data), 0x2300 - 0x1000, 6, 0x1000),
           struct.pack('<IIIIIIII', 2, d_off, dv, dv, len(dyn), len(dyn), 6, 4)]
    hdr = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 2, 183, 1, entry, 52, 0, 0, 52, 32, 3, 40, 0, 0)
    return (hdr + b''.join(phs)).ljust(t_off, b'\0') + text + data


G = os.path.join(d, 'g.elf')
R = ld.R_AARCH64_P32_RELATIVE
# H3b: the dynamic relocations are P32_RELATIVE entries (RELA) and RELR words, nothing else
open(G, 'wb').write(guest_elf(rela=[(0x1100, R, 8), (0x1104, R, 0x20)], relr=[0x1200, 0b1011 << 1 | 1, 0x1300]))
check('dyn relocs good', ld.dyn_relocs(G), ({'P32_RELATIVE': 2, 'RELR': 1 + 3 + 1}, []))
open(G, 'wb').write(guest_elf(rela=[(0x1100, R, 8), (0x1104, 0x101 << 8 | R, 0), (0x1108, 1027 & 0xff, 0)]))
check('dyn relocs: a symbol, another type', ld.dyn_relocs(G), ({'P32_RELATIVE': 1, 'RELR': 0}, ['dynreloc:00001104:type183+sym', 'dynreloc:00001108:type3']))
open(G, 'wb').write(guest_elf(extra_dyn=[(23, 0x1100), (2, 12), (22, 0)]))
check('dyn relocs: JMPREL and TEXTREL', ld.dyn_relocs(G)[1], ['dynreloc:table:JMPREL', 'dynreloc:table:TEXTREL'])
# H2: the header words are written (link address of the first segment, page aligned span, relocatable bit) and the magic is checked
open(G, 'wb').write(guest_elf())
check('header written', ld.write_header(G), (0, 0x3000, 1))
with open(G, 'rb') as fh:
    img = fh.read()
check('header words in the file', struct.unpack_from('<IIIII', img, 0x100 + 8), (0x47584E46, 3, 0, 0x3000, 1))
open(G, 'wb').write(guest_elf(pie=False))
check('header: not relocatable', ld.write_header(G)[2], 0)
for why, img in (('no magic', guest_elf(magic=False)), ('entry not the first segment', guest_elf(entry=4))):
    open(G, 'wb').write(img)
    try:
        ld.write_header(G)
        fails.append('header: accepted %s' % why)
    except ValueError:
        pass
# ar names: GNU long-name table; the map file's pulled members
lm = b'a_member_with_a_long_name.o/\n'
ar = b'!<arch>\n'
for nm, body in ((b'/', b'idx'), (b'//', lm), (b'/0', m1), (b's.o/', m2)):
    ar += nm.ljust(16) + b'0'.ljust(12) + b'0'.ljust(6) + b'0'.ljust(6) + b'644'.ljust(8) + str(len(body)).encode().ljust(10) + b'`\n' + body + (b'\n' if len(body) & 1 else b'')
open(os.path.join(d, 'n.a'), 'wb').write(ar)
check('ar names', [n for n, _, _ in ld.ar_members(os.path.join(d, 'n.a'))], ['a_member_with_a_long_name.o', 's.o'])
check('member symbols', ld.member_symbols(os.path.join(d, 'n.a'), 'a_member_with_a_long_name.o'), {'f': False, 'k': False})
open(os.path.join(d, 'x.map'), 'w').write('Discarded input sections\n\nArchive member included to satisfy reference by file (symbol)\n\n'
                                          '/t/libc.a(lib_a-printf.o)\n                              hello.o (printf)\n'
                                          '/t/very/long/path/libgcc.a(_aeabi.o)\n'
                                          '                              /t/libc.a(lib_a-printf.o) (__aeabi)\n\nAllocating common symbols\n/t/x.a(no.o)\n')
check('map members', ld.map_members(os.path.join(d, 'x.map')), [('/t/libc.a', 'lib_a-printf.o'), ('/t/very/long/path/libgcc.a', '_aeabi.o')])

for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
