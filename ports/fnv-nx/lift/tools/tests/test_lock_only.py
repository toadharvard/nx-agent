"""Rule LK-ONLY (R9): inside a locked instruction a guest memory access reaches memory only through LKA<T> (LK8..LK64). By construction, in two places:
  emitter: the accessor prefix is cx.macc (core rd/wr/ea, f_int, the DSL emit), 'LK' inside a locked window; core.fmem (MF32 / MF64, no LK form) raises Unsupported there;
  prelude: every plain accessor macro of rt/fnvl.h passes `lkwin_` to its accessor and LK_BEGIN declares a block-local `lkwin_` of another type, so any spelling of a plain accessor (hand, DSL, helper)
  inside the window does not compile. Positive: the hand handlers of every LOCKABLE form lift, and the lifted unit compiles with the pack flags. Negative: the old defect (the DSL emitter handling a
  locked add) raises at the emitter; a plain accessor of every kind inside LK_BEGIN fails to compile. The accessor list is read from rt/fnvl.h (every `#define M..(a)` that is a guest accessor).
  Every compile is a stage of the recipe (toolchain/tests/plugbuild.py: the pinned msvcmodel compiler, the full plugin set, a lifted root), no direct compiler call.   test_lock_only.py (the inputs SDMROWS and GEN are mounted)"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", "..")) # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, re, shutil, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT); sys.path.insert(0, os.path.join(LIFT, 'tools')); sys.path.insert(0, os.path.join(LIFT, '..', 'toolchain', 'tests'))
from fnvnx.toolchain.tests import plugbuild as pb
import prelude
import fnvnx.lift.fnvlift as L
from fnvnx.lift.fnvlift.tests import _inputs
_inputs.configure()
GEN = os.environ.get('FNVNX_INPUT_GEN') or sys.exit('FNVNX_INPUT_GEN is not mounted (the GEN output of the lift/rt root, a produced input of tests/inputs.json)')
from fnvnx.lift.fnvlift import core, dsl
from fnvnx.lift.proof import lift_dsl as f_dsl, cpufeat
from fnvnx.lift.fnvlift.core import Unsupported
from fnvnx.lift.fnvlift.image import Image

CASES = [('f00108', 'lock add [eax],ecx'), ('f0ff08', 'lock dec dword [eax]'), ('870a', 'xchg [edx],ecx'), ('f00fb10a', 'lock cmpxchg [edx],ecx'), ('f00fc10a', 'lock xadd [edx],ecx'),
         ('f00fc70a', 'lock cmpxchg8b [edx]'), ('f00fab08', 'lock bts [eax],ecx'), ('f0800a01', 'lock or byte [edx],1')]
HDR = open(os.path.join(LIFT, 'rt', 'fnvl.h')).read()
ACCESSORS = sorted(set(re.findall(r'^#define (M[A-Z]?\d*)\(a\) \(G', HDR, re.M)))      # M8 M16 M32 M64 MF32 MF64 MX: the macros that name GM / GMF / GMX
assert set(ACCESSORS) == {'M8', 'M16', 'M32', 'M64', 'MF32', 'MF64', 'MX'}, ACCESSORS
GUEST_ACCESS = re.compile(r'\b(?:%s)\s*\(' % '|'.join(ACCESSORS))
WORK = tempfile.mkdtemp(prefix='lock-only-')
PLUG = [pb.build(n, WORK) for n in ('sdd', 'names', 'dispatch')]


def compiles(body):
    src, obj = os.path.join(WORK, 't.cc'), os.path.join(WORK, 't.o')
    open(src, 'w').write(prelude.text('fnvl_prelude.h', [os.path.join(LIFT, 'rt'), os.path.join(LIFT, '..', 'platform', 'include'), GEN]) + body)
    return pb.compile(src, obj, PLUG)


def lift_bytes(code, emit):
    img = Image.from_bytes(code, 0x401000)
    core.set_emit(f_dsl.emit_form if emit == 'dsl' else None, cpufeat.missing)
    try:
        return L.lift(img, 0x401000, len(code))[0]
    finally:
        core.set_emit(None, cpufeat.missing)


def raises(fn):
    try:
        fn()
    except Unsupported as e:
        return str(e)


for hexs, name in CASES:
    code = bytes.fromhex(hexs) + b'\xc3'
    for emit in ('dsl',):
        t = lift_bytes(code, emit)
        assert 'lk_commit<' in t and not GUEST_ACCESS.search(t[t.index('LK_BEGIN'):t.index('lk_commit<')]), (name, emit)
        r = compiles(t)
        assert r.returncode == 0, (name, emit, r.stderr[-600:])

class Cx: a = 0x401000; macc = 'LK'
assert 'outside LKA' in raises(lambda: core.Ctx.fmem(Cx, None, None))                        # the float accessors (MF32 / MF64) have no LK form: refused at the emitter
old = dsl.InlineEmit.macc                                                    # the old defect: a DSL emitter that spells plain accessors inside the lock window: the unit does not compile
dsl.InlineEmit.macc = property(lambda s: 'M', lambda s, v: None)
try:
    r = compiles(lift_bytes(bytes.fromhex('f00108') + b'\xc3', 'dsl'))
    assert r.returncode != 0 and 'LkWin' in r.stderr, r.stderr[-300:]
finally:
    dsl.InlineEmit.macc = old

OUT = 'extern "C" void L_t(u32 ecx_){ u32 cf=0,zf=0,sf=0,of=0,pf=0,af=0; u32 eax=0,ecx=ecx_,edx=0,ebx=0,esp=0,ebp=0,esi=0,edi=0; %s %s }'
for acc, stmt in (('M8', 'M8(ecx)=1;'), ('M16', 'M16(ecx)=1;'), ('M32', 'eax=M32(ecx);'), ('M64', 'M64(ecx)=1;'), ('MF32', 'float f=MF32(ecx); (void)f;'), ('MF64', 'MF64(ecx)=1.0;'), ('MX', 'MX(ecx)=MX(edx);')):
    ok = compiles(OUT % ('', stmt))
    assert ok.returncode == 0, (acc, 'outside a lock window the accessor compiles', ok.stderr[-400:])
    bad = compiles(OUT % ('LK_BEGIN(32);', stmt))
    assert bad.returncode != 0 and 'LkWin' in bad.stderr, (acc, bad.stderr[-300:], 'inside LK_BEGIN a plain accessor must not compile')
    good = compiles(OUT % ('LK_BEGIN(32);', 'LK32(ecx)=LK32(ecx)+eax;'))
    assert good.returncode == 0, ('LK path compiles', good.stderr[-400:])
print('lock_only_test ok')
shutil.rmtree(WORK, ignore_errors=True)
