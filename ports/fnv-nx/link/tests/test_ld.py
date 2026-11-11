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
                       '-z', 'max-page-size=0x1000', '-z', 'common-page-size=0x1000', '--build-id=sha1',
                       '-T', '/s/guest.ld', '-o', '/o/guest.elf', '--whole-archive', '@/o/inputs.rsp', '--no-whole-archive'])
cmd = ld.command(['/t/ld'], '/s/guest.ld', '/o/guest.elf', '/o/inputs.rsp', ['/l1', '/l2'], ['gcc', 'c'], ['/l1/crti.o', '/l1/crtbegin.o'], ['/l1/crtend.o', '/l1/crtn.o'])
check('command with libraries: the driver order, ours whole, libraries searched', cmd[cmd.index('-o'):],
      ['-o', '/o/guest.elf', '/l1/crti.o', '/l1/crtbegin.o', '-L/l1', '-L/l2', '--whole-archive', '@/o/inputs.rsp', '--no-whole-archive',
       '--start-group', '-lgcc', '-lc', '--end-group', '/l1/crtend.o', '/l1/crtn.o'])
check('strict command: no option beyond the specs', [c for c in cmd if c.startswith('--') and c not in
                                                    ('--no-dynamic-linker', '--build-id=sha1', '--whole-archive', '--no-whole-archive', '--start-group', '--end-group')], [])
# the stage's --ld-arg: the emulation, -EL, -X; nothing else
for ok in (['-m', 'aarch64linux32'], ['-maarch64elf32', '-EL', '-X']):
    ld.check_ld_args(ok)
for bad in (['-z', 'x'], ['-m'], ['--gc-sections'], ['-T', 'x.ld']):
    try:
        ld.check_ld_args(bad)
        fails.append('ld-arg accepted: %r' % bad)
    except ValueError:
        pass


def arch(members, armap=True, index=None):
    """GNU ar bytes with a symbol index of the global definitions of `members` [(name, elf bytes)] (index overrides [(symbol, member #)])."""
    body, offs, o = b'', [], 8
    parts = []
    for nm, b in members:
        parts.append((nm, b))
    hdr = lambda nm, b: nm.ljust(16).encode() + b'0'.ljust(12) + b'0'.ljust(6) + b'0'.ljust(6) + b'644'.ljust(8) + str(len(b)).encode().ljust(10) + b'`\n'
    ent = []
    for i, (nm, b) in enumerate(parts):
        ent.append([(n, i) for n, df in sorted(ld.elf_symbols('m', b).items()) if df])
    flat = [e for l in ent for e in l] if index is None else index
    strs = b''.join(n.encode() + b'\0' for n, _ in flat)
    mlen = [len(hdr(nm + '/', b)) + len(b) + (len(b) & 1) for nm, b in parts]
    idx_len = 4 + 4 * len(flat) + len(strs)
    start = 8 + (60 + idx_len + (idx_len & 1) if armap else 0)
    offsets = [start + sum(mlen[:i]) for i in range(len(parts))]
    out = b'!<arch>\n'
    if armap:
        ib = struct.pack('>I', len(flat)) + b''.join(struct.pack('>I', offsets[i]) for _, i in flat) + strs
        out += hdr('/', ib) + ib + (b'\n' if len(ib) & 1 else b'')
    for (nm, b) in parts:
        out += hdr(nm + '/', b) + b + (b'\n' if len(b) & 1 else b'')
    return out


def info(syms):
    return ld.symtab_info(elf(syms))


# symbol tables: definitions, strong and weak references, COMMON, unmodelled symbols
check('symtab_info', info([('f', 1, 1, 2), ('w', 1, 2, 2), ('r', 0, 1, 0), ('wr', 0, 2, 0), ('c', 0xfff2, 1, 1), ('loc', 1, 0, 2), ('ifn', 1, 1, 10), ('v@V1', 1, 1, 2)]),
      ({'f', 'w', 'c', 'ifn', 'v@V1'}, {'r'}, {'wr'}, {'c'}, ['ifn', 'v@V1']))
# the pull-in closure, as the linker does it: archives in order, rescanned (group), a strong reference pulls, a weak one does not
base = [info([('main', 1, 1, 2), ('a', 0, 1, 0), ('w', 0, 2, 0), ('s', 0, 1, 0)])]
liba = [('a1.o', elf([('a', 1, 1, 2), ('b', 0, 1, 0)])), ('a2.o', elf([('w', 1, 1, 2)])), ('a3.o', elf([('unused', 1, 1, 2)]))]
libb = [('b1.o', elf([('b', 1, 1, 2), ('c', 0, 1, 0)])), ('b2.o', elf([('c', 1, 1, 2), ('a', 1, 1, 2)]))]
LA, LB = os.path.join(d, 'liba.a'), os.path.join(d, 'libb.a')
open(LA, 'wb').write(arch(liba))
open(LB, 'wb').write(arch(libb))
archives = []
for pth in (LA, LB):
    ix, mem = ld.armap(pth)
    check('index matches members %s' % os.path.basename(pth), ld.index_matches(ix, mem), True)
    archives.append((pth, ix, mem))
taken, defs, strong, weak, problems = ld.closure(base, archives, {'s'})
check('closure: a1 (a), b1 (b, rescan of libb after liba), b2 (c); a2 not (weak reference); a3 not', [m for _, m, _ in taken], ['a1.o', 'b1.o', 'b2.o'])
check('closure: definitions', sorted(defs), ['a', 'b', 'c', 'main'])
check('closure: what stays undefined (weak w, script s excluded by the caller)', sorted(strong - defs - {'s'}), [])
check('closure: weak only', sorted(weak - strong - defs), ['w'])
check('closure: no problem', problems, [])
# both libraries define `a`: the first archive of the link line wins (libb first: b2 takes `a`, and nothing needs `b`)
taken, *_ = ld.closure(base, archives[::-1], {'s'})
check('closure: order of the link line', [m for _, m, _ in taken], ['b2.o'])
# unmodelled: a member with a COMMON symbol, with an indirect function; an index that does not match; no index; the script defines a listed name
open(LA, 'wb').write(arch([('x.o', elf([('a', 0xfff2, 1, 1)]))]))
ix, mem = ld.armap(LA)
check('closure: library COMMON is a problem', ld.closure(base, [(LA, ix, mem)], set())[4], ['%s(x.o): common a' % LA])
open(LA, 'wb').write(arch([('x.o', elf([('a', 1, 1, 10)]))]))
ix, mem = ld.armap(LA)
check('closure: library indirect function is a problem', ld.closure(base, [(LA, ix, mem)], set())[4], ['%s(x.o): unmodelled symbol a' % LA])
open(LA, 'wb').write(arch([('x.o', elf([('a', 1, 1, 2)]))], index=[]))
ix, mem = ld.armap(LA)
check('index that lacks a definition', ld.index_matches(ix, mem), False)
open(LA, 'wb').write(arch([('x.o', elf([('a', 1, 1, 2)]))], armap=False))
try:
    ld.armap(LA)
    fails.append('an archive without index accepted')
except ValueError:
    pass
open(LA, 'wb').write(arch([('x.o', elf([('s', 1, 1, 2)]))]))
ix, mem = ld.armap(LA)
check('closure: the script defines a name the index lists', ld.closure([info([('s', 0, 1, 0)])], [(LA, ix, mem)], {'s'})[4], ['%s: the script defines s, which the library index lists' % LA])
check('find_lib', ld.find_lib(['/nonexistent', d], 'a'), LA)
# the empty object the script probe links is a well-formed object
open(p, 'wb').write(ld.empty_object())
check('empty object', ld.symbols(p), [])
check('empty object symtab_info', ld.symtab_info(ld.empty_object()), (set(), set(), set(), set(), []))

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

for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
