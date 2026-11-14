#!/usr/bin/env python3
"""Test of toolchain/pi/fnvnx_pi.cc (N3 verdict, inline tree, touched types), built from the tracked source with the argv of the
recipe's plugin stage (plugbuild.py). Two fixtures, both compiled with the product flags and read in-process from the object:
  pi/pi.cpp      one function per verdict, the inline-tree cases: every line of section .fnvnx.n3 and .fnvnx.argorder, exactly
  pi/effect.cpp  the EFFECT rule of `argorder`: a call with two or more arguments that have an effect (a store, a volatile
                 access, a throwing or non-returning construct, a call not known side-effect-free by the compiler's own facts:
                 ECF_CONST / ECF_PURE, built-ins by their flags, always_inline bodies judged by the same rule); NEON wrappers and
                 const functions in two arguments give `ok`, two calls of an unknown function give `argorder`
The fixture carries its own declarations of the lifted forms (fnvnx_lifted attribute). An unknown plugin argument is an error
(there was a `fpfast` mode: it is not a product value and is removed). No network, no game data.
Run: python3 -I <this file> from the checkout root."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_pi')


def n3_rows(obj):
    rows = {}
    for l in pb.section(obj, '.fnvnx.n3').splitlines():
        f = l.split('\t')
        rows[(f[0], f[3])] = f
    return rows


def legacy(obj):
    rows = n3_rows(obj)
    want = {
        ('_Z3topP1R', ''): ['ok', '_ZL3midP1R,_ZL4leafP1R', '1R'],     # the inline tree, transitively
        ('_ZL3midP1R', 'inline'): ['ok', '', '1R'],                    # a body seen but not emitted on its own
        ('_ZL4leafP1R', 'inline'): ['ok', '', '1R'],
        ('_Z2dvii', ''): ['div', '', ''],
        ('_Z2dci', ''): ['ok', '', ''],                                # division by a constant is inside
        ('_Z2shjj', ''): ['shift', '', ''],
        ('_Z2vaiz', ''): ['varargs', '', ''],
        ('_Z3volPVi', ''): ['volatile', '', ''],
        ('_Z2uni', ''): ['uninit', '', ''],                            # read early: the optimizer deletes the use
        ('_Z2asi', ''): ['asm', '', ''],
        ('_ZL2cpii', 'constprop.0'): ['ok', '', ''],                   # a clone, under the function's own name
        ('_Z5usecpi', ''): ['ok', '', ''],
        ('_Z2m1v', ''): ['ok', '', '1Q'],
        ('_Z2m2v', ''): ['uninit-mem', '', '1Q'],
        ('_Z2m3v', ''): ['uninit-mem', '', '1Q'],
        ('_Z2m4i', ''): ['uninit-mem', '_Z2uni', '1Q'],        # ICF made its body one with un's: GCC inlined un (the scope blocks never showed it)
        ('_Z2m5v', ''): ['ok', '', '1Q'],
        ('_Z2o1v', ''): ['argorder', '', ''],
        ('_Z2o2v', ''): ['argorder', '', ''],
        ('_Z2o3i', ''): ['argorder', '', ''],
        ('_Z2o4v', ''): ['ok', '', ''],
        ('_Z2o5R1W', ''): ['argorder', '', '1W'],
        ('_Z3fo1i', ''): ['falloff', '', ''],                          # FALL2
        ('_Z3fo2i', ''): ['ok', '_Z2uni', ''],                         # the same: ICF
        ('_Z3fo3i', ''): ['ok', '', ''],
        ('_Z3fo4i', ''): ['ok', '', ''],
        ('_Z3fo5i', ''): ['falloff', '', ''],
        ('_ZN1KC2Ev', ''): ['ok', '', '1K'],
        ('_ZN2KDD2Ev', ''): ['argorder', '', '2KD'],
        ('_ZN2KDD1Ev', 'alias'): ['argorder', '', '2KD'],
        ('_ZN2KED2Ev', ''): ['argorder', '', '2KE'],
        ('_ZN2KED1Ev', 'alias'): ['argorder', '', '2KE'],
        ('_ZN1KD2Ev', ''): ['ok', '', '1K'],
        ('_Z3sx1i', ''): ['seh', '', ''],                              # SEH-PI: the callee's attribute, not its name
        ('_Z3sx2i', ''): ['seh', '', ''],
        ('_Z3sx3i', ''): ['ok', '', ''],
        ('_ZL3sh1i', 'inline'): ['seh', '', ''],
        ('_Z3sx4i', ''): ['ok', '_ZL3sh1i', ''],
        ('_Z2dbi', ''): ['int3', '', ''],
        ('_Z3ao1i', ''): ['argorder', '', '2S1'],
        ('_ZN1KC1Ev', 'alias'): ['ok', '', '1K'],                      # the same body under its other symbol
        ('_ZN1KD1Ev', 'alias'): ['ok', '', '1K'],
        # INL2: the inlined set from GCC's inlining bookkeeping (removal hook, inline-clone edges), not only the scope blocks
        ('_Z2f2i', ''): ['ok', '_ZL2h2i', ''],
        ('_Z3f2bi', ''): ['ok', '_ZL2i2i', ''],
        ('_Z2f3i', ''): ['ok', '_ZL2g3i,_ZL2h3i', ''],
        ('_Z2f4i', ''): ['ok', '_ZL2g4i,_ZL2h4i', ''],
        ('_Z2e5i', ''): ['ok', '_ZL2h5i', ''],
        ('_Z2f5i', ''): ['ok', '_ZL2h5i', ''],
        ('_Z2f6i', ''): ['ok', '', ''],
        ('_Z2f7i', ''): ['ok', '_ZL2r7i', ''],
        ('_Z3f7bi', ''): ['ok', '_ZL2r7i', ''],
        ('_Z2f8i', ''): ['ok', '_ZL2z8i', ''],
        ('_Z2f9i', ''): ['ok', '_ZL2t9i,_ZL3ip9PFiiEi', ''],
        ('_ZL2h5i', ''): ['ok', '', ''],
        ('_Z3f10i', ''): ['ok', '_ZL3g10i,_ZL3h10i', ''],         # INL3
        ('_ZL3g10i', 'inline'): ['ok', '', ''],
        ('_Z3f11i', ''): ['uninit-mem', '_ZL3i11i,_ZN1ZC4Ei', '1Z'],           # INL3: the clone's own inlining is in the caller's list
        ('_ZL3i11i', 'inline'): ['ok', '', ''],
        ('_ZN1ZC2Ei', 'inline'): ['ok', '', '1Z'],
        ('_ZL3h10i', 'inline'): ['ok', '', ''],
        ('_ZL3cp6ii', 'constprop.0'): ['ok', '_ZL2g6i', ''],     # into a clone: the line is the origin's
        ('_ZL2g3i', 'inline'): ['ok', '', ''],
        ('_ZL2g4i', 'inline'): ['ok', '', ''],
        ('_ZL2g6i', 'inline'): ['ok', '', ''],
        ('_ZL2h2i', 'inline'): ['ok', '', ''],
        ('_ZL2h3i', 'inline'): ['ok', '', ''],
        ('_ZL2h4i', 'inline'): ['ok', '', ''],
        ('_ZL2i2i', 'inline'): ['ok', '', ''],
        ('_ZL2t9i', 'inline'): ['ok', '', ''],
        ('_ZL2z8i', 'inline'): ['ok', '', ''],
        ('_ZL3ip9PFiiEi', 'inline'): ['ok', '', ''],
        ('_ZL2r7i', 'inline'): ['ok', '', ''],
        # INL4: records under bodies inside removed code are consumed by the callgraph's own removal facts
        ('_Z4inl4P2Ta', ''): ['uninit-mem', '_ZN2TaC4Ev,_ZN2VaC4Ev', '2Ta,2Va'],   # -fno-lifetime-dse (a product flag): the empty Ma / Ra constructors are never made bodies
        ('_ZN2TaC2Ev', 'inline'): ['ok', '', '2Ta'],
        ('_ZN2MaC2Ev', 'inline'): ['ok', '', '2Ma'],
        ('_ZN2RaC2Ev', 'inline'): ['ok', '', '2Ra'],
        ('_ZN2VaC2Ev', 'inline'): ['ok', '', '2Va'],
        # INL5: a record whose receiver's body is not output here (extern template member): no error, the body has a row
        ('_Z2f5PKc', ''): ['ok', '', '3Ex5IcE'],
        ('_ZN4Ex5iIcEC2Ev', 'inline'): ['ok', '', '4Ex5iIcE'],
        ('_ZN3Ex5IcEC2EPKc', 'inline'): ['ok', '', '3Ex5IcE'],
    }
    for k, w in want.items():
        got = rows.get(k)
        check('row %s' % (k,), got and [got[1], got[2], got[4]] == w, 'want %s got %s' % (w, got))
    check('no other row', set(rows) == set(want), sorted(set(rows) ^ set(want)))
    ao = pb.section(obj, '.fnvnx.argorder').splitlines()
    # the last field: the call's number among the function's calls of that callee / how many there are
    wantao = ['_Z2o1v\\t_Z3twoii\\t_Z2gav|_Z2gbv\\t0/1', '_Z2o5R1W\\t_ZN1W1wEii\\t_ZN1W4selfEv|_Z2gav|\\t0/1',
              '_ZN2KDD2Ev\\t_Z3twoii\\t_Z2gav|_Z2gbv\\t0/1']
    check('argorder rows', [l.replace('\t', '\\t') for l in ao] == wantao, ao)
    und = pb.undefined(obj)
    for w in ('fnvnx_seh_try', 'fnvnx_seh_except', 'fnvnx_seh_finally', '__debugbreak'):
        check('undefined reference to ' + w, w in und)


# EFFECT: verdict of each function of effect.cpp, by symbol, and the argorder rows (written for readable sites only)
EFFECT = {
    '_Z2x1i': 'ok',          # two const calls
    '_Z2x2i': 'ok',          # a pure and a const call
    '_Z2x3i': 'ok',          # always_inline bodies with locals and a result only, and a summary of summaries
    '_Z2x4i': 'ok',          # an always_inline body that calls const functions
    '_Z2x5Pii': 'argorder',  # a body that stores through a pointer; an unknown call
    '_Z2x6i': 'argorder',    # a body that calls an unknown function
    '_Z2x7i': 'argorder',    # a body with a loop: termination is not judged
    '_Z2x8i': 'ok',          # ... against a const call: one effect only
    '_Z2x9i': 'argorder',    # a pure function whose argument itself has an effect (i++)
    '_Z3x9bi': 'ok',         # ... and the other argument is const
    '_Z3x10i': 'argorder',   # pure functions of unknown calls
    '_Z3x11i': 'argorder',   # a call that does not return
    '_Z3x12i': 'ok',         # built-ins with the const flag
    '_Z3x13i': 'ok',         # one effect only
    '_Z3x14i': 'ok',         # no calls
    '_Z3x15i': 'ok',         # an indirect call and a const call: one effect only
    '_Z3x16i': 'argorder',   # two indirect calls
    '_Z2n111__Int32x4_tS_S_': 'ok',   # NEON intrinsics (always_inline wrappers of const built-ins)
    '_Z2n211__Int32x4_tS_S_': 'ok',
    '_Z2n311__Int32x4_tPi': 'ok',     # a load and an unknown call: one effect only
    '_Z2n411__Int32x4_tPiS0_': 'ok',  # a store wrapper as the outer call, a load inside
    '_Z2n511__Int32x4_t': 'argorder', # wrappers of two unknown calls
}
EFFECT_ROWS = ['_Z2x5Pii\\t_Z3twoii\\t_ZL3wstPi|_Z2gav\\t0/1', '_Z2x6i\\t_Z3twoii\\t_ZL3wcxi|_Z2gbv\\t0/1',
               '_Z2x7i\\t_Z3twoii\\t_ZL3wlpi|_Z2gav\\t0/1', '_Z3x11i\\t_Z3twoii\\t_Z2nri|_Z2gav\\t0/1']


def effect(obj):
    rows = n3_rows(obj)
    for sym, v in sorted(EFFECT.items()):
        got = rows.get((sym, ''))
        check('effect %s: %s' % (sym, v), got and got[1] == v, got)
    own = {k[0] for k in rows if k[1] == ''}
    check('effect: every emitted function is a case', own == set(EFFECT), sorted(own ^ set(EFFECT)))
    ao = [l.replace('\t', '\\t') for l in pb.section(obj, '.fnvnx.argorder').splitlines()]
    check('effect: argorder rows', ao == EFFECT_ROWS, ao)


def main():
    td = tempfile.mkdtemp(prefix='test-pi-')
    try:
        so = pb.build('pi', td)
        obj = os.path.join(td, 'pi.o')
        r = pb.compile(os.path.join(HERE, 'pi', 'pi.cpp'), obj, [so], args=['-w'])
        check('pi.cpp compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            legacy(obj)
        obj = os.path.join(td, 'effect.o')
        r = pb.compile(os.path.join(HERE, 'pi', 'effect.cpp'), obj, [so], args=['-w'])
        check('effect.cpp compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            effect(obj)
        for arg in ('mode=1', 'fpfast'):
            r = pb.compile(os.path.join(HERE, 'pi', 'pi.cpp'), os.path.join(td, 'x.o'), [so], args=['-w', '-fplugin-arg-fnvnx_pi-' + arg])
            check('unknown plugin argument %s is an error' % arg, r.returncode != 0 and 'unknown plugin argument' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
