#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/hostabi/product/build.json ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/link
# reads: ports/fnv-nx/loader/build.json ports/fnv-nx/platform/build.json ports/fnv-nx/render/d3d9/build.json ports/fnv-nx/render/gl4/build.json
# reads: ports/fnv-nx/render/nxgpu/build.json ports/fnv-nx/render/pair/build.json ports/fnv-nx/render/shaders/translator/build.json
# reads: ports/fnv-nx/render/smscan/build.json ports/fnv-nx/rt/guest/build.json ports/fnv-nx/rt/guest_main/build.json ports/fnv-nx/shim/build.json
# reads: ports/fnv-nx/shim/include ports/fnv-nx/toolchain/addr/fnvnx_addr.cc ports/fnv-nx/toolchain/dispatch/fnvnx_dispatch.cc
# reads: ports/fnv-nx/toolchain/ftoi/f2i_builtins.inc ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc ports/fnv-nx/toolchain/layout/crossing_terms.inc
# reads: ports/fnv-nx/toolchain/layout/decltype_spell.inc ports/fnv-nx/toolchain/layout/fnvnx_layout.cc ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json
# reads: ports/fnv-nx/toolchain/names/fnvnx_names.cc ports/fnv-nx/toolchain/order/atomic_order.inc ports/fnv-nx/toolchain/order/fnvnx_frame.h
# reads: ports/fnv-nx/toolchain/order/fnvnx_order.cc ports/fnv-nx/toolchain/pi/fnvnx_pi.cc ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc
# reads: ports/fnv-nx/toolchain/tests ports/fnv-nx/verify
"""Test of the four sse2neon float -> integer forms (_mm_cvt_ps2pi / _mm_cvtps_pi32, _mm_cvtps_pi16, _mm_cvtps_pi8, _mm_cvt_ss2si) through the real
shim/include/fnvnx_compat.h and the pinned sse2neon (fnv-nx-build tools/sse2neon), built by the recipe with the msvcmodel compiler and the full native
plugin set. Static: the unit builds, fnvnx_ftoi rewrote every NEON target builtin the forms reach (no finding), each form's function holds the rule's compare and
select, the generated value driver (ftoi_values/gen_values.py sse2neon: lanes, boundaries, NaN, each FPCR rounding mode) builds. The values themselves run on the
L4 boot machine (no run here). The forms read the run-time rounding mode (vrndiq_f32 = frinti reads FPCR), as x86 reads MXCSR.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import json
import os
import sys
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402
import test_ftoi as tf  # noqa: E402

check = pb.Checks('test_ftoi_sse2neon')
EXT = {'name': 'sse2neon', 'repo': 'fnv-nx-build', 'ref': 'tools/sse2neon', 'commit': '699510ccdd79460c19efa7b9e036f82cd8ad5930',
       'tree_sha256': 'c8e826605645f4ff49966520d9ab549f2ff74b40aaadb8f306cfc6851de56fa3', 'at': '/ext/sse2neon'}
SRC = open(os.path.join(HERE, 'ftoi_values', 'fixture_sse2neon.cpp')).read()


def main():
    pb.EXTRA['external'].append(EXT)
    pb.EXTRA['include'] += [pb.ROOT + '/shim', '/ext/sse2neon']
    pb.EXTRA['copy'].append(('ports/fnv-nx/shim/include', 'shim'))
    td = tempfile.mkdtemp(prefix='test-ftoi-s2n-')
    so = pb.build('ftoi', td)
    r, obj, j = tf.comp(td, 's2n', SRC, native=True, plugins=[so])
    check('the unit builds on the real fnvnx_compat.h with the sse2neon pin', r.returncode == 0, r.stderr[-2500:])
    if r.returncode:
        return check.done()
    check('the report has no finding', j is not None and not j.get('findings'), json.dumps(j and j.get('findings'))[:400])
    d = pb.objdump(obj)
    for fn in ('ps2pi', 'ps_pi32', 'ps_pi16', 'ps_pi8', 'ss2si', 'ss_si32'):
        check('%s: the object holds the rule\'s compare and select' % fn, pb.rule_form(d.get(fn, [])), '\n'.join(d.get(fn, []))[:600])
    # the pass runs before inlining: the sites sit in the always_inline NEON wrappers sse2neon calls (vcvtnq_s32_f32 ...), and each of them is rewritten
    check('the target builtins the forms reach are rewritten, none left (kinds: %s)' % sorted({f['kind'] for f in j['rewrites']}),
          j['rewritten'] >= 1 and not j['findings'] and all(f['kind'] == 'mdbuiltin' or f['kind'].startswith('conv') or f['kind'] == 'builtin' for f in j['rewrites']), json.dumps(j['rewrites'])[:400])
    drv = os.path.join(td, 'values_sse2neon.cpp')
    subprocess.run([sys.executable, '-I', os.path.join(HERE, 'ftoi_values', 'gen_values.py'), 'sse2neon', drv], check=True)
    r2, _, _ = tf.comp(td, 'values_s2n', open(drv).read(), native=True)
    check('the generated value driver builds against the same compat root', r2.returncode == 0, r2.stderr[-1500:])
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
