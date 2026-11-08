#!/usr/bin/env python3
"""Test of seamlayout.py (the static LAYOUT seam check) on hand-written classes and hand-written x86 (no game content).

    python3 tools/link/test_seamlayout.py

A tiny PE32 (.text at 0x00401000, .data at 0x00403000) holds
  * a fake operator new at 0x00401000 (`ret`, an allocator address of seamlayout.DEFAULT_ALLOCATORS) and constructors,
  * a caller doing `push 0x20; call new; add esp,4; mov ecx,eax; call Foo::Foo` (class Foo: native 8, exe 0x20),
  * the same through an ebp slot as /Od does it, for ns::Qux (native 0x10, exe 0x10: ok), with a decoy `new` whose
    result goes to a function that is not a constructor,
  * a global `mov ecx, <data>; call Bar::Bar` (Bar: native 0x10, extent 0x10; ok) and Gar (native 4, extent 0x20, the
    tail referenced: mismatch; Har: native 4, extent 0x20, tail not referenced: suspect),
  * a member function of Baz reading [ecx+0x40] (native 0x20: mismatch) and of Fine reading [ecx+4] (native 8: ok).
The native sizes come from DWARF of a small object compiled by aarch64-linux-gnu-g++ -g (Foo, ns::Qux, Baz), from a
layouts.tsv (Bar, Gar, Har) and, for Fine, from nothing (verdict unknown without a tree). Also: the class split of
demangled names, the DWARF reader on nested namespaces, and the CLI's TSV.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import seamlayout  # noqa: E402

BASE = 0x00400000
TEXT_VA, DATA_VA, RDATA_VA = 0x00401000, 0x00403000, 0x00404000
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def make_pe(code, data_size=0x100):
    """code: {address: bytes} in .text (int3 elsewhere)."""
    text = bytearray(b'\xcc' * 0x400)
    for a, bs in code.items():
        text[a - TEXT_VA:a - TEXT_VA + len(bs)] = bs
    data = bytes(data_size)
    hdr = 0x400
    mz = bytearray(0x40)
    mz[0:2] = b'MZ'
    mz[0x3c:0x40] = struct.pack('<I', 0x40)
    opt = bytearray(224)
    struct.pack_into('<H', opt, 0, 0x10b)
    struct.pack_into('<I', opt, 28, BASE)
    struct.pack_into('<I', opt, 32, 0x1000)
    struct.pack_into('<I', opt, 36, 0x200)
    struct.pack_into('<I', opt, 56, 0x4000)
    struct.pack_into('<I', opt, 60, hdr)
    struct.pack_into('<I', opt, 92, 16)
    coff = struct.pack('<HHIIIHH', 0x14c, 3, 0, 0, 0, len(opt), 0x102)
    secs = struct.pack('<8sIIIIIIHHI', b'.text', len(text), TEXT_VA - BASE, len(text), hdr, 0, 0, 0, 0, 0x60000020)
    secs += struct.pack('<8sIIIIIIHHI', b'.data', data_size, DATA_VA - BASE, len(data), hdr + len(text), 0, 0, 0, 0,
                        0xc0000040)
    secs += struct.pack('<8sIIIIIIHHI', b'.rdata', 0x100, RDATA_VA - BASE, 0x100, hdr + len(text) + len(data), 0, 0, 0, 0,
                        0x40000040)
    head = bytes(mz) + b'PE\0\0' + coff + bytes(opt) + secs
    return head.ljust(hdr, b'\0') + bytes(text) + data + bytes(0x100)


def call(src, dst):
    return b'\xe8' + struct.pack('<i', dst - (src + 5))


def build_code():
    """-> ({address: bytes}, {name: address})."""
    A = {'new': 0x401000, 'foo': 0x401010, 'qux': 0x401020, 'bar': 0x401030, 'gar': 0x401040, 'har': 0x401050,
         'baz': 0x401060, 'fine': 0x401070, 'other': 0x401080, 'c1': 0x401100, 'c2': 0x401140, 'c3': 0x401180,
         'bazf': 0x401200, 'finef': 0x401220, 'c4': 0x401300}
    code = {A['new']: b'\xc3'}
    for k in ('foo', 'qux', 'bar', 'gar', 'har', 'baz', 'fine', 'other'):
        code[A[k]] = b'\x8b\xc1\xc3'                       # mov eax, ecx; ret
    # Foo: push 0x20; call new; add esp,4; mov ecx,eax; call Foo::Foo; ret
    c = b'\x68\x20\0\0\0'
    c += call(A['c1'] + len(c), A['new']) + b'\x83\xc4\x04\x8b\xc8'
    c += call(A['c1'] + len(c), A['foo']) + b'\xc3'
    code[A['c1']] = c
    # /Od shape for ns::Qux, after a decoy: new(0x10) handed to a plain function, then new(0x10) through [ebp-4]
    c = b'\x6a\x10'
    c += call(A['c2'] + len(c), A['new']) + b'\x83\xc4\x04\x8b\xc8'
    c += call(A['c2'] + len(c), A['other'])                 # not a constructor: no evidence, and ends the track
    c += b'\x6a\x10'
    c += call(A['c2'] + len(c), A['new']) + b'\x83\xc4\x04\x89\x45\xfc'          # mov [ebp-4], eax
    c += b'\x83\x7d\xfc\x00\x74\x08'                                              # cmp [ebp-4], 0; je +8
    c += b'\x8b\x4d\xfc'                                                          # mov ecx, [ebp-4]
    c += call(A['c2'] + len(c), A['qux']) + b'\xc3'
    code[A['c2']] = c
    # globals: mov ecx, <addr>; call ctor, and an access to the tail of Gar's object ([addr+0x1c]) and to Har's head
    c = b''
    for ctor, addr in (('bar', DATA_VA + 0x00), ('gar', DATA_VA + 0x20), ('har', DATA_VA + 0x60)):
        c += b'\xb9' + struct.pack('<I', addr)
        c += call(A['c3'] + len(c), A[ctor])
    c += b'\xa1' + struct.pack('<I', DATA_VA + 0x5c)                              # mov eax, [Gar+0x3c]
    c += b'\xa1' + struct.pack('<I', DATA_VA + 0x60)                              # mov eax, [Har]
    c += b'\xc3'
    code[A['c3']] = c
    # the base-class constructor inside an inlined derived constructor: a vftable address is stored at offset 0
    c = b'\x68\x30\0\0\0'
    c += call(A['c4'] + len(c), A['new']) + b'\x83\xc4\x04\x8b\xf0\x8b\xce'    # mov esi,eax; mov ecx,esi
    c += call(A['c4'] + len(c), A['foo'])
    c += b'\xc7\x06' + struct.pack('<I', RDATA_VA) + b'\xc3'                    # mov dword ptr [esi], vftable; ret
    code[A['c4']] = c
    code[A['bazf']] = b'\x8b\x41\x40\xc3'                   # mov eax, [ecx+0x40]; ret
    code[A['finef']] = b'\x8b\x41\x04\xc3'                  # mov eax, [ecx+4]; ret
    return code, A


NATIVE_SRC = r'''
struct Foo { int a, b; Foo(); virtual ~Foo(); };
namespace ns { struct Qux { int v[4]; Qux(); }; }
struct Baz { char buf[0x20]; Baz(); void f(); };
Foo::Foo() : a(0), b(0) {}
Foo::~Foo() {}
ns::Qux::Qux() {}
Baz::Baz() {}
void Baz::f() {}
'''


def make_link(root, A):
    os.makedirs(os.path.join(root, 'out'))
    os.makedirs(os.path.join(root, 'fixed'))
    src = os.path.join(root, 'u.cpp')
    open(src, 'w').write(NATIVE_SRC)
    r = subprocess.run([CXX, '-g', '-c', '-O0', src, '-o', os.path.join(root, 'fixed', 'u.cpp.o')],
                       capture_output=True, text=True)
    check(r.returncode == 0, 'compiled the DWARF fixture: %s' % r.stderr[:200])
    rows = [('foo', 'F', '_ZN3FooC1Ev'), ('qux', 'F', '_ZN2ns3QuxC1Ev'), ('bar', 'C', '_ZN3BarC1Ev'),
            ('gar', 'F', '_ZN3GarC1Ev'), ('har', 'F', '_ZN3HarC1Ev'), ('baz', 'F', '_ZN3BazC1Ev'),
            ('fine', 'F', '_ZN4FineC1Ev'), ('other', 'F', '_Z5otherv'), ('bazf', 'F', '_ZN3Baz1fEv'),
            ('finef', 'F', '_ZN4Fine1fEv'), ('new', 'F', '_Znwj')]
    with open(os.path.join(root, 'out', 'addrmap.tsv'), 'w') as f:
        f.write('# fnvnx-addrmap 1\n# address\tkind\tlink_symbol\tnative_symbol\tobject\tnote\n')
        for k, kind, sym in rows:
            f.write('%08x\t%s\t__fnv_%08x\t%s\tu.cpp.o\t\t\t\t\n' % (A[k], kind, A[k], sym))
    with open(os.path.join(root, 'out', 'refs.tsv'), 'w') as f:
        f.write('#object\tundefined\tclass\ttier\taddress\ttarget\tnote\n')
        for off in (0x00, 0x20, 0x60, 0x80):      # the data symbols (neighbours): Bar 0x20, Gar 0x40, Har 0x20
            f.write('src/u.cpp\tDAT_%08x\tdata\tunit/cname\t%08x\t__fnv_%08x\t?x@@3HA\n' % (
                DATA_VA + off, DATA_VA + off, DATA_VA + off))
        # a constructor no object defines, reached only through refs.tsv (the StandardLibrarySingletonObject case)
        f.write('src/u.cpp\t_ZN4FineC2Ev\tcode\tunit/exact\t%08x\t__fnv_%08x\t?x\n' % (A['fine'], A['fine']))
    with open(os.path.join(root, 'out', 'layouts.tsv'), 'w') as f:
        f.write('#unit\ttype\tsize\talign\ttrivial\tempty\tclass\tkey\tpod\trule\n')
        for t, sz in (('Bar', 32), ('Gar', 4), ('Har', 4)):
            f.write('u.cpp\t%s\t%d\t4\t1\t0\tG1\tk\t0\tpod2\n' % (t, sz))
        f.write('other.cpp\tBar\t4\t4\t1\t0\tG1\tk\t0\tpod2\n')   # another unit's own `Bar`: not this class


def test_unit():
    s = seamlayout.split_member
    check(s('Foo::Foo()') == ('Foo', 'Foo'), 'split_member: constructor')
    check(s('ns::Qux<int, char>::Qux(int)') == ('ns::Qux<int,char>', 'Qux'), 'split_member: template class')
    check(s('virtual Foo::~Foo()') == ('Foo', '~Foo'), 'split_member: virtual destructor')
    check(s('A::operator()(int)') == ('A', s('A::operator()(int)')[1]), 'split_member: operator() does not cut early')
    check(s('free_function(int)') is None, 'split_member: free function')
    check(seamlayout.is_ctor('ns::Qux<int,char>', 'Qux') and not seamlayout.is_ctor('Foo', 'f'), 'is_ctor')


def main():
    test_unit()
    tmp = tempfile.mkdtemp(prefix='seamlayout_')
    try:
        code, A = build_code()
        exe = os.path.join(tmp, 'x.exe')
        open(exe, 'wb').write(make_pe(code))
        root = os.path.join(tmp, 'link')
        make_link(root, A)
        d = seamlayout.dwarf_sizes(os.path.join(root, 'fixed', 'u.cpp.o'))
        check(d.get('Foo') and d.get('ns::Qux') == 16 and d.get('Baz') == 0x20, 'dwarf_sizes: %s' % sorted(d.items())[:6])
        rows = seamlayout.check_layout(root, exe)
        by = {(r[0], r[4]): r for r in rows}
        for r in rows:
            print('   ', '\t'.join(r))
        r = by.get(('Foo', 'new'))
        check(r and r[3] == '32' and r[1] == str(d['Foo']) and r[2] == 'dwarf' and r[7] == 'mismatch',
              'Foo: new 0x20 against native %s: mismatch' % (r and r[1]))
        check(by[('Foo', 'new')][5] == '1', 'Foo: the call inside an inlined derived constructor is not evidence')
        r = by.get(('ns::Qux', 'new'))
        check(r and r[3] == '16' and r[1] == '16' and r[7] == 'ok' and r[5] == '1',
              'ns::Qux: new through an ebp slot, 16 = 16: ok (the decoy new is no evidence)')
        r = by.get(('Bar', 'static'))
        check(r and r[3] == '32' and r[7] == 'ok', 'Bar: static extent 0x20 = native 0x20: ok (%s)' % (r and r[3]))
        r = by.get(('Gar', 'static'))
        check(r and r[3] == '64' and r[1] == '4' and r[7] == 'mismatch',
              'Gar: static extent 0x40, tail referenced, native 4: mismatch (%s)' % (r and r[7:]))
        r = by.get(('Har', 'static'))
        check(r and r[3] == '32' and r[7] == 'suspect', 'Har: tail not referenced: suspect (%s)' % (r and r[7:]))
        r = by.get(('Baz', 'maxoff'))
        check(r and r[3] == '68' and r[1] == '32' and r[7] == 'mismatch', 'Baz: [ecx+0x40] is 0x44 bytes, native 0x20: mismatch')
        r = by.get(('Fine', 'maxoff'))
        check(r and r[3] == '8' and r[1] == '' and r[7] == 'unknown',
              'Fine: constructor known only from refs.tsv, max offset 8, native size unknown (%s)' % (r and r[7]))
        check(rows[0][7] == 'mismatch', 'mismatches sort first')
        out = os.path.join(tmp, 'seams_layout.tsv')
        rc = subprocess.run([sys.executable, os.path.join(HERE, 'seamlayout.py'), root, exe, '-o', out, '--no-probe'],
                            capture_output=True, text=True)
        lines = open(out).read().split('\n') if rc.returncode == 0 else []
        check(rc.returncode == 0 and lines[0] == '#' + '\t'.join(seamlayout.COLUMNS) and len(lines) > len(rows),
              'CLI writes the TSV (%s)' % rc.stderr[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
