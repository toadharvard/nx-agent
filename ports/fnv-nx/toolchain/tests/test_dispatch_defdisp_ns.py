#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/hostabi/product/build.json ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/lift/tools ports/fnv-nx/link
# reads: ports/fnv-nx/loader/build.json ports/fnv-nx/platform/build.json ports/fnv-nx/render/d3d9/build.json ports/fnv-nx/render/gl4/build.json
# reads: ports/fnv-nx/render/nxgpu/build.json ports/fnv-nx/render/pair/build.json ports/fnv-nx/render/shaders/translator/build.json
# reads: ports/fnv-nx/render/smscan/build.json ports/fnv-nx/rt/guest/build.json ports/fnv-nx/rt/guest_main/build.json ports/fnv-nx/shim/build.json
# reads: ports/fnv-nx/toolchain/addr/fnvnx_addr.cc ports/fnv-nx/toolchain/dispatch/fnvnx_dispatch.cc ports/fnv-nx/toolchain/ftoi/f2i_builtins.inc
# reads: ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc ports/fnv-nx/toolchain/gcc ports/fnv-nx/toolchain/layout/crossing_terms.inc
# reads: ports/fnv-nx/toolchain/layout/decltype_spell.inc ports/fnv-nx/toolchain/layout/fnvnx_layout.cc ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json
# reads: ports/fnv-nx/toolchain/msvcmodel/build.sh ports/fnv-nx/toolchain/msvcmodel/msvcmodel.patch ports/fnv-nx/toolchain/names/fnvnx_names.cc
# reads: ports/fnv-nx/toolchain/order/atomic_order.inc ports/fnv-nx/toolchain/order/fnvnx_frame.h ports/fnv-nx/toolchain/order/fnvnx_order.cc
# reads: ports/fnv-nx/toolchain/pi/fnvnx_pi.cc ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc ports/fnv-nx/toolchain/tests ports/fnv-nx/verify
"""Test of DEFDISP-NS (toolchain/dispatch/fnvnx_dispatch.cc + lift/tools/exitgen.py): a DEFINITION whose type has no x86 shape (_Float128, _Float16, __int128 as
return or parameter) gets the row `<asm> -<reason>` in .fnvnx.defdisp and compiles; the same types at an indirect call site stay errors (the site needs the
descriptor); exitgen is red for a JOINED definition whose row is `-<reason>` and names the symbol and the reason, and an unjoined `-` row is inert.
The objects are the recipe's (plugbuild.py, the product flags, the three plugins). Run: python3 -I <this file> from the checkout root."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'lift', 'tools'))
import plugbuild as pb  # noqa: E402
import exitgen  # noqa: E402

check = pb.Checks('test_dispatch_defdisp_ns')
FIX = os.path.join(HERE, 'dispatch_defdisp_ns')
EXITGEN = os.path.join(HERE, '..', '..', 'lift', 'tools', 'exitgen.py')


def main():
    td = tempfile.mkdtemp(prefix='test-defdisp-ns-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names', 'dispatch')]
        obj = os.path.join(td, 'd.o')
        r = pb.compile(os.path.join(FIX, 'defs.cpp'), obj, so)
        check('definitions of _Float128, _Float16 and __int128 types compile (no error)', r.returncode == 0, r.stderr[:1500])
        if r.returncode:
            return check.done()
        dd = exitgen.defdisp_table(exitgen.read_defdisp(obj))
        want = {'d_f128': '-f128', 'd_f16': '-f16', 'd_i128': '-i128', 'd_i128_arg': '-i128', 'd_ok': 'stack:i:i'}
        check('the rows are `-<reason>` for a type with no shape, the descriptor for the others', all(dd.get(n) == {d} for n, d in want.items()), repr(dd))
        for f, why in (('call_f128', 'floating type of 128 bits'), ('call_f16', 'floating type of 16 bits'), ('call_i128', '128-bit integer')):
            r = pb.compile(os.path.join(FIX, f + '.cpp'), os.path.join(td, f + '.o'), so)
            check('%s: the type at an indirect call site is still an error' % f, r.returncode != 0 and why in r.stderr, r.stderr[:800])
        rf = os.path.join(td, 'rows.tsv')
        out = os.path.join(td, 'exits.cc')

        def run(rows):
            open(rf, 'w').write(''.join(rows))
            if os.path.exists(out):
                os.remove(out)
            return subprocess.run([sys.executable, '-I', EXITGEN, '--rows', rf, '--out', out, '--obj', obj], capture_output=True, text=True, env={})
        r = run(['00401000\td_ok\tcaller\n'])
        check('exitgen: an unjoined `-` row is inert (only joined names are read)', r.returncode == 0 and os.path.exists(out), r.stderr)
        r = run(['00401000\td_ok\tcaller\n', '00402000\td_f128\tcaller\n', '00403000\td_i128_arg\tcaller\n'])
        check('exitgen: a joined `-` row is red, names the symbol and the reason, nothing generated',
              r.returncode != 0 and 'd_f128' in r.stderr and 'f128' in r.stderr and 'd_i128_arg' in r.stderr and 'i128' in r.stderr and 'no x86 shape' in r.stderr and not os.path.exists(out), r.stderr)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
