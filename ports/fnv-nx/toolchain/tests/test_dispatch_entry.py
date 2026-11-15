#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/hostabi/product/build.json ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/link
# reads: ports/fnv-nx/loader/build.json ports/fnv-nx/platform/build.json ports/fnv-nx/platform/include ports/fnv-nx/render/d3d9/build.json
# reads: ports/fnv-nx/render/gl4/build.json ports/fnv-nx/render/nxgpu/build.json ports/fnv-nx/render/pair/build.json
# reads: ports/fnv-nx/render/shaders/translator/build.json ports/fnv-nx/render/smscan/build.json ports/fnv-nx/rt/guest/build.json
# reads: ports/fnv-nx/rt/guest_main/build.json ports/fnv-nx/shim/build.json ports/fnv-nx/shim/include ports/fnv-nx/toolchain/addr/fnvnx_addr.cc
# reads: ports/fnv-nx/toolchain/dispatch/fnvnx_dispatch.cc ports/fnv-nx/toolchain/ftoi/f2i_builtins.inc ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc
# reads: ports/fnv-nx/toolchain/gcc ports/fnv-nx/toolchain/layout/crossing_terms.inc ports/fnv-nx/toolchain/layout/decltype_spell.inc
# reads: ports/fnv-nx/toolchain/layout/fnvnx_layout.cc ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json ports/fnv-nx/toolchain/msvcmodel/build.sh
# reads: ports/fnv-nx/toolchain/msvcmodel/msvcmodel.patch ports/fnv-nx/toolchain/names/fnvnx_names.cc ports/fnv-nx/toolchain/order/atomic_order.inc
# reads: ports/fnv-nx/toolchain/order/fnvnx_frame.h ports/fnv-nx/toolchain/order/fnvnx_order.cc ports/fnv-nx/toolchain/pi/fnvnx_pi.cc
# reads: ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc ports/fnv-nx/toolchain/tests ports/fnv-nx/verify
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


def headers(td, so):
    """a header of platform/include is included from C and from C++ units: each one compiles as both languages under the recipe's flags and plugins
    (the C++ unit with the C++ keywords only: g++ rejects `_Static_assert` and `_Alignof`)"""
    inc = os.path.join(HERE, '..', '..', 'platform', 'include')
    pb.EXTRA['copy'].append(('ports/fnv-nx/platform/include', 'inc'))
    pb.EXTRA['copy'].append(('ports/fnv-nx/shim/include', 'shim'))
    pb.EXTRA['include'] += [pb.ROOT + '/inc', pb.ROOT + '/shim']
    try:
        # the universe: every header of the directory whose includes stay inside it and the shim (a header that reaches a sibling tree by `../` needs that tree's
        # own product root as its home; it is covered where that root is built)
        for h in sorted(f for f in os.listdir(inc) if f.endswith('.h') and '#include "../' not in open(os.path.join(inc, f)).read()):
            for ext, lang in (('c', 'C'), ('cpp', 'C++')):
                src = os.path.join(td, 'hdr_%s.%s' % (h[:-2], ext))
                open(src, 'w').write('#include "%s"\n' % h)
                r = pb.compile(src, os.path.join(td, 'hdr_%s_%s.o' % (h[:-2], ext)), so)
                check('platform/include/%s compiles as %s' % (h, lang), r.returncode == 0, r.stderr[:900])
    finally:
        pb.EXTRA['copy'].clear(); pb.EXTRA['include'].clear()


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
        r = pb.compile(os.path.join(FIX, 'ok_paths.cpp'), os.path.join(td, 'p.o'), so)
        check('static initialisers (scalar, array, aggregate), an aggregate copy and a same-type builtin copy are accepted', r.returncode == 0, r.stderr[:1500])
        for n, tag in (('static_dyn', 'ENTRY1-CONT'), ('static_const', 'a constant is no native entry'), ('static_ctor', 'a constant is no native entry'),
                       ('union', 'ENTRY1-UNION'), ('union_nested', 'ENTRY1-UNION'), ('memcpy_other', 'ENTRY1-CONT'), ('memset', 'ENTRY1-CONT'), ('memmove_cast', 'ENTRY1-CONT')):
            r = pb.compile(os.path.join(FIX, 'bad_%s.cpp' % n), os.path.join(td, n + '.o'), so)
            check('%s is an error (%s)' % (n, tag), r.returncode != 0 and tag in r.stderr, r.stderr[:900])
        headers(td, so)
        r = pb.compile(os.path.join(FIX, 'bad_attr.cpp'), os.path.join(td, 'a.o'), so)
        check('the attribute on a function type (not a pointer) is an error', r.returncode != 0 and 'applies to a pointer-to-function type' in r.stderr, r.stderr[:900])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
