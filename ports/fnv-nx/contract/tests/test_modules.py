#!/usr/bin/env python3
"""Synthetic check of modules.py (rule M1): closure, system/module classification, init order, cycle, total base assignment,
errors (missing file, 64-bit dll), exports.tsv, modules.c, determinism.  PE files come from loader/synth/pebuild.py."""
import hashlib, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'loader', 'synth'))
import modules, pebuild

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def pe(base, imports, exports=(), dll=True, relocs=True, machine=None, entry=0x1010):
    img = pebuild.Img(base, dll=dll, entry_rva=entry)
    img.section('.text', 0x1000, 0x200, 0x60000020)
    img.section('.rdata', 0x2000, 0x800, 0x40000040)
    img.section('.data', 0x3000, 0x200, 0xC0000040)
    if imports:
        img.imports(0x2000, imports)
    if exports:
        img.exports(0x2400, 'x.dll', exports)
    if relocs:
        img.va_word(0x3100, 0x1010)
    data = bytearray(img.build(reloc_rva=0x5000 if relocs else None))
    if machine:
        data[0x84 + 0:0x84 + 2] = machine.to_bytes(2, 'little')
    return bytes(data)


def write(d, name, data):
    with open(os.path.join(d, name), 'wb') as fh:
        fh.write(data)


BRIDGE = 'dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\nkernel32.dll\tSleep\t\tstdcall\tv\ti\tSleep\tsym\n'


def world(tmp, extra=None):
    d1, d2 = os.path.join(tmp, 'd1'), os.path.join(tmp, 'd2')
    os.makedirs(d1); os.makedirs(d2)
    write(os.path.join(tmp, 'bridge.tsv'), BRIDGE) if False else open(os.path.join(tmp, 'bridge.tsv'), 'w').write(BRIDGE)
    write(tmp, 'exe.pe', pe(0x400000, [('KERNEL32.dll', ['Sleep']), ('A.dll', ['fa']), ('Zed', ['fz'])], dll=False, relocs=False))
    write(d1, 'Zed.DLL', pe(0x10000000, [('b.dll', ['fb'])], [(1, 'fz', 0x1020)]))               # Zed (no extension in the import) imports b
    write(d1, 'a.dll', pe(0x10000000, [('b.dll', ['fb', 3]), ('kernel32.dll', ['Sleep'])], [(1, 'fa', 0x1020), (2, None, 0x1030)]))
    write(d2, 'B.dll', pe(0x10000000, [('a.dll', ['fa'])], [(1, 'fb', 0x1040), (2, 'ga', 'A.fa'), (3, 'fc', 0x1050)]))   # cycle a <-> b
    write(d2, 'z_ondemand.dll', pe(0x10000000, [('kernel32.dll', ['Sleep'])], [(1, 'fo', 0x1060)]))
    write(d2, 'a.dll', pe(0x20000000, [], [(1, 'fa', 0x1020)]))                                   # shadowed by d1/a.dll
    write(d2, 'notes.txt', b'not a module')
    if extra:
        extra(d1, d2, tmp)
    return d1, d2


def run(tmp, dirs, out):
    return modules.run(os.path.join(tmp, 'exe.pe'), os.path.join(tmp, 'bridge.tsv'), dirs, out)


def rows(out):
    with open(os.path.join(out, 'modules.tsv')) as fh:
        return [l.rstrip('\n').split('\t') for l in fh if l[0] != '#']


def main():
    tmp = tempfile.mkdtemp()
    try:
        d1, d2 = world(tmp)
        out1, out2 = os.path.join(tmp, 'o1'), os.path.join(tmp, 'o2')
        text, bad = run(tmp, [d1, d2], out1)
        r = rows(out1)
        by = {x[0]: x for x in r}
        check('names sorted', [x[0] for x in r], ['a.dll', 'b.dll', 'z_ondemand.dll', 'zed.dll'])
        check('static flags', [x[8] for x in r], ['1', '1', '0', '1'])
        # exe imports a then zed; a imports b (b imports a: the edge back to a is not followed); zed imports b (seen)
        check('load indices (init order b, a, zed)', {n: by[n][9] for n in by}, {'a.dll': '1', 'b.dll': '0', 'z_ondemand.dll': '', 'zed.dll': '2'})
        exe_end = 0x400000 + 0x5000
        check('first base after the exe, 64 KiB aligned', int(by['a.dll'][4], 16), 0x410000)
        bases = [(int(x[4], 16), int(x[5], 16)) for x in r]
        check('bases in name order, no overlap', all(b + s <= n for (b, s), (n, _) in zip(bases, bases[1:])) and all(b % 0x10000 == 0 for b, _ in bases) and bases[0][0] >= exe_end, True)
        check('first match in --dir order (d1/a.dll preferred 0x10000000)', by['a.dll'][3], '10000000')
        check('sha256 of the file', by['a.dll'][1], hashlib.sha256(open(os.path.join(d1, 'a.dll'), 'rb').read()).hexdigest())
        check('exe slots: all have a value', 'exe slots with a value 3 of 3' in text, True)
        ex = open(os.path.join(out1, 'exports', 'a.dll.tsv')).read().split('\n')
        check('exports.tsv (named, unnamed)', ex[:2], ['fa\t%08x\t\t1' % (int(by['a.dll'][4], 16) + 0x1020), '#2\t%08x\t\t2' % (int(by['a.dll'][4], 16) + 0x1030)])
        check('forwarder row', open(os.path.join(out1, 'exports', 'b.dll.tsv')).read().split('\n')[1], 'ga\t\tA.fa\t2')
        c = open(os.path.join(out1, 'modules.c')).read()
        check('modules.c: count and table order', ('fnvnx_module_count = 4u' in c, c.index('"b.dll"') < c.index('"a.dll"') < c.index('"zed.dll"') < c.index('"z_ondemand.dll"')), (True, True))
        check('imports of a module through the other module', 'via modules 2' in text.split('module import slots a.dll')[1].split('\n')[0], True)
        # determinism
        run(tmp, [d1, d2], out2)
        same = all(open(os.path.join(out1, n), 'rb').read() == open(os.path.join(out2, n), 'rb').read() for n in ('modules.tsv', 'modules.c', 'iat.tsv'))
        check('deterministic', same, True)
        # a system dll is not followed, even when a file of that name exists
        write(d2, 'kernel32.dll', pe(0x10000000, [], [(1, 'Sleep', 0x1020)]))
        run(tmp, [d1, d2], os.path.join(tmp, 'o3'))
        check('system dll not a module', 'kernel32.dll' in [x[0] for x in rows(os.path.join(tmp, 'o3'))], False)
        # errors
        for name, extra, want in (('missing', lambda a, b, t: os.remove(os.path.join(b, 'B.dll')), 'module not found in any --dir: b.dll'),
                                  ('64-bit', lambda a, b, t: write(b, 'wide.dll', pe(0x10000000, [], machine=0x8664)), 'wide.dll: not an i386 image'),
                                  ('no relocs', lambda a, b, t: write(b, 'plain.dll', pe(0x10000000, [], relocs=False)), 'plain.dll: needs relocating but has no base relocations')):
            t2 = tempfile.mkdtemp()
            try:
                a, b = world(t2, extra)
                try:
                    run(t2, [a, b], os.path.join(t2, 'o'))
                    check('%s: error' % name, 'no error', want)
                except modules.StageError as e:
                    check('%s: message' % name, want in str(e), True)
            finally:
                shutil.rmtree(t2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


main()
for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
