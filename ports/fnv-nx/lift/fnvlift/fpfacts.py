"""Certified invariants of the floating-point control state (tools/fpstate.py, task C4a). A lifter body that has no effect BECAUSE of an invariant asks this module; the answer is read from the
fact file of the module being lifted, never assumed.

    <facts>/fpstate/invariants.tsv     lines `I-FPMASK <holds|unproven> <per-module detail>` (written by `fpstate.py combine`: the PROGRAM invariant = AND over all modules)
    <facts> = $FNVNX_FPSTATE_DIR, else $FNVNX_FACTS/fpstate, else /home/user/l3facts/fpstate

I-FPMASK    no x87 or SSE exception is ever unmasked at any instruction of any module: every load of the x87 control word / MXCSR (fldcw, ldmxcsr, fninit, frstor, fldenv, fxrstor, the _controlfp family)
            is a constant or a transform that cannot clear a mask bit, starting from FCW 0x027F / MXCSR 0x1F80 (thread start, rt/enter.cc). Consequence: `fwait` never raises #MF, and x87/SSE arithmetic
            never raises: the runtime's masked-response semantics are the whole semantics.
I-FPSTICKY  no instruction of any module can observe the sticky exception flags (FSW bits 0-7, 15; MXCSR bits 0-5), so they need not be maintained.
A lifter body that needs an invariant calls require(); if the fact file says `unproven` (or is missing) the form is Unsupported with the reason: a missing semantics is a lift error, never a no-op."""
import os
from .core import Unsupported

# Recorded assumptions of the owner (not proofs). A-FPMASK, owner word of 2026-11-11 (CLAUDE.md of the integration branch, 0c6667f5): until the first frame,
# x87/SSE exceptions are taken to stay masked as in the thread start state FCW 0x027F / MXCSR 0x1F80 (ENV_TRUTH P6). Known risk, measured by tools/fpstate.py: the control-word
# loads whose value is not proven (exe 55, libvorbis 15, libvorbisfile 2, binkw32 1, d3dx9_31 43, d3dx9_38 51). The full semantics (thread flag + exact slow path raising #MF/#XM) is
# deferred work, not dropped: a body that relies on the assumption records 'A-FPMASK' in its kinds, so every such site is counted in the pack.
OWNER_ASSUMED = {'I-FPMASK': 'A-FPMASK'}
ASSUME = set()          # test harness only (test/instest.py): states drawn with every exception masked, so the invariant holds by construction of the test states
_CACHE = {}


def _path():
    d = os.environ.get('FNVNX_FPSTATE_DIR')
    if d is None:
        d = os.path.join(os.environ.get('FNVNX_FACTS', '/home/user/l3facts'), 'fpstate')
    return os.path.join(d, 'invariants.tsv')


def status(name):
    """-> (holds, detail)"""
    if name in ASSUME:
        return True, 'assumed by the test harness'
    p = _path()
    if p not in _CACHE:
        t = {}
        try:
            for ln in open(p):
                f = ln.rstrip('\n').split('\t')
                if len(f) >= 2 and not ln.startswith('#'):
                    t[f[0]] = (f[1] == 'holds', f[2] if len(f) > 2 else '')
        except OSError:
            t = None
        _CACHE[p] = t
    t = _CACHE[p]
    if t is None:
        return False, 'no fact file %s (run tools/fpstate.py)' % p
    return t.get(name, (False, 'invariant %s not in %s' % (name, p)))


def require(name, what):
    """the body of `what` has no effect under invariant `name`: returns the invariant id when it holds, the id of the owner's recorded assumption when
    the invariant is unproven but assumed (OWNER_ASSUMED), raises Unsupported otherwise"""
    ok, detail = status(name)
    if not ok and name in OWNER_ASSUMED:
        return OWNER_ASSUMED[name]
    if not ok:
        raise Unsupported('%s needs %s, which is not proven for this program (%s)' % (what, name, detail))
    return name
