#!/usr/bin/env python3
"""Test of ENTRY1 (toolchain/dispatch/fnvnx_dispatch.cc): `fnvnx_entry` on a pointer-to-function type marks native entry addresses. A call through a value of
that type is not rewritten and has no .fnvnx.disp row (a plain fnptr call is still rewritten); the address of a function converts to the entry type; every other
conversion with an entry type on one side (int, plain fnptr, void*, C cast, reinterpret_cast, implicit) is the error ENTRY1-CONT naming both types; the
attribute on anything but a pointer-to-function type is an error. Built through plugbuild (the recipe's flags and plugins).
Run: python3 -I <this file> from the checkout root."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_dispatch_entry')
FIX = os.path.join(HERE, 'dispatch_entry')


def body(dis, frag):
    sym = next((k for k in dis if frag in k), None)
    return '\n'.join(re.sub(r'\s+', ' ', l.strip()) for l in dis.get(sym, []))


def disp_rows(obj):
    from elftools.elf.elffile import ELFFile
    with open(obj, 'rb') as fh:
        s = ELFFile(fh).get_section_by_name('.fnvnx.disp')
        return s.data().decode().split('\n')[:-1] if s is not None else []


def main():
    td = tempfile.mkdtemp(prefix='test-dispatch-entry-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names', 'dispatch')]
        obj = os.path.join(td, 'ok.o')
        r = pb.compile(os.path.join(FIX, 'ok.cpp'), obj, so)
        check('&func converts to the entry type without error (and the entry calls compile)', r.returncode == 0, r.stderr[:1500])
        if r.returncode:
            return check.done()
        dis = pb.objdump(obj)
        for f in ('10call_entry', '11call_loaded', '12call_via_get'):
            b = body(dis, f)
            check('%s: an entry call is not rewritten (no fnvnx_dispatch call)' % f, bool(b) and 'fnvnx_dispatch' not in b and re.search(r'\b(?:blr|br) x\d+', b), b)
        b = body(dis, '10call_plain')
        check('a plain fnptr call is still rewritten (a call to fnvnx_dispatch)', 'fnvnx_dispatch' in b, b)
        check('the .fnvnx.disp section has the descriptor of the plain call only (no entry descriptor)', disp_rows(obj) == ['stack:i:i'], disp_rows(obj))
        for n, want in (('int2entry', 'conversion between'), ('entry2plain', 'conversion between'), ('plain2entry', 'conversion between'),
                        ('plain2entry_reint', 'conversion between'), ('entry2void', 'conversion between'), ('implicit', 'conversion between'), ('return', 'conversion between'), ('arg', 'conversion between')):
            r = pb.compile(os.path.join(FIX, 'bad_%s.cpp' % n), os.path.join(td, n + '.o'), so)
            check('%s is the error ENTRY1-CONT' % n, r.returncode != 0 and 'ENTRY1-CONT' in r.stderr and want in r.stderr, r.stderr[:900])
        r = pb.compile(os.path.join(FIX, 'bad_attr.cpp'), os.path.join(td, 'a.o'), so)
        check('the attribute on a function type (not a pointer) is an error', r.returncode != 0 and 'applies to a pointer-to-function type' in r.stderr, r.stderr[:900])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
