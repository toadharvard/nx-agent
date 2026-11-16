"""Flags liveness (fnvlift/flaglive.py, proof/FLAGLIVE.md): the lifter omits the flag writes that no path reads.
  (1) the solver on hand-made CFGs (records straight from flaglive.info shapes): a loop, a jcc consumer, an escape (fault), a call / ret exit, a handler (not a Form instruction);
  (2) lifted buffers: the dropped set (meta['flagdrop'], addr -> mask) is exactly the dead writes, and the C text has no store to a dropped flag at that address;
  (3) every flag stays live where it must: before a ret, a call, a div (fault), and for a flag read by a later jcc/setcc/adc.
    test_flaglive.py"""
import os, re, sys, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
from fnvlift import flaglive as fl, inputs
from fnvlift.image import Image
import fnvlift as L
FAILS = []


def check(n, ok):
    print('%-78s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


def rec(reads=(), kills=(), esc=False, succs=(), writes=None):
    sp = lambda x: frozenset(x.split() if isinstance(x, str) else x)
    k = sp(kills)
    return dict(reads=sp(reads), kills=k, esc=esc, succs=tuple(succs), writes=k if writes is None else sp(writes))


E = fl.EXIT
# (1) solver
g = {1: rec(kills='cf zf sf', succs=[2]), 2: rec(reads='zf', succs=[3, 4]), 3: rec(kills='cf', succs=[E]), 4: rec(kills='zf sf of', succs=[E])}
d = fl.dropped(g)
check('jcc consumer: nothing the cmp writes is dead (every path reaches an exit or the reader)', d == {} or 1 not in d)
lin, lout = fl.solve(g)
check('live_out of an instruction before an exit is ALL', lout[3] == fl.ALL and lout[4] == fl.ALL)
check('live_in of the jcc = {zf} U live_out minus nothing', lin[2] == frozenset({'zf'}) | lout[2])
loop = {1: rec(reads='cf', kills='cf zf', succs=[1, 2]), 2: rec(kills='cf pf af zf sf of df', succs=[E])}
lin, lout = fl.solve(loop)
check('loop: the loop instruction keeps cf live (it reads it), live_out of 1 includes cf', 'cf' in lout[1] and 'cf' in lin[1])
check('loop: nothing is dropped at the exit instruction (ALL live after)', 2 not in fl.dropped(loop))
ov = {1: rec(kills='cf pf af zf sf of', succs=[2]), 2: rec(kills='cf pf af zf sf of', succs=[3]), 3: rec(reads='zf', succs=[E])}
d = fl.dropped(ov)
check('overwritten before any read: exactly {cf pf af zf sf of}', d.get(1) == frozenset({'cf', 'pf', 'af', 'zf', 'sf', 'of'}))
rd = {1: rec(kills='cf pf af zf sf of', succs=[2]), 2: rec(reads='zf', succs=[3]), 3: rec(kills='cf pf af zf sf of df', succs=[E])}
dd = fl.dropped(rd)
check('writer, zf reader, full overwrite: the writer keeps zf and drops the other five', dd.get(1) == frozenset({'cf', 'pf', 'af', 'sf', 'of'}))
check('the reader before a full overwrite exits-live nothing: instruction 3 (exit) drops nothing', 3 not in dd)
esc = {1: rec(kills='cf zf', succs=[2]), 2: rec(esc=True, succs=[3]), 3: rec(kills='cf pf af zf sf of', succs=[E])}
check('a faulting instruction after a writer keeps every flag live', fl.dropped(esc).get(1) is None)
hand = {1: rec(kills='cf zf', succs=[9])}
check('a successor that is not a Form instruction is an exit: ALL live', fl.dropped(hand) == {})
check('no successor at all is an exit', fl.dropped({1: rec(kills='cf', succs=[])}) == {})
check('mask is the FL bit set', fl.mask({'cf', 'df'}) == 1 | (1 << 6))

# (2) lifted buffers
d = tempfile.mkdtemp(prefix='flaglive-')
open(d + '/universe.tsv', 'w').write('')
open(d + '/depth.tsv', 'w').close(); open(d + '/udsail.tsv', 'w').write('# fnvnx-udsail/2\n')
json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': n == 'gs'} for n in ('es', 'ds', 'fs', 'gs')}}}, open(d + '/segwrites.json', 'w'))
inputs.reset(); _inputs.configure(); inputs.reset()
inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=_inputs.X86SYS, cpumodel=_inputs.CPUMODEL, rcptab=_inputs.RCPTAB, rcpcert=_inputs.RCPCERT,
                 udsail=d + '/udsail.tsv', segwrites=d + '/segwrites.json', image='buf')
B = 0x401000
M = lambda *f: sum(1 << fl.FL.index(x) for x in f)


def lift(h):
    code = bytes.fromhex(h)
    t, meta = L.lift(Image.from_bytes(code, B), B, len(code))[:2]
    return t, meta


def stores(t, a, f):
    return bool(re.search(r'\b%s = ' % f, t))


# add eax,ebx ; add ecx,edx ; ret   -> the first add's flags are all dead (the second overwrites them), the second's are live (ret exits)
t, m = lift('01d801d1c3')
fd = m['flagdrop']
check('add;add;ret: first add drops all six flags', fd.get(B) == M('cf', 'pf', 'af', 'zf', 'sf', 'of'))
check('add;add;ret: second add (before ret) drops nothing', B + 2 not in fd)
# cmp eax,ebx ; je L ; add ecx,edx ; L: ret   (je reads zf; the add overwrites; ret exits)
t, m = lift('39d8740201d1c3')
fd = m['flagdrop']
check('cmp;je;add;ret: the je reads only zf, the add overwrites the rest on one path and ret exits on the other: the cmp drops nothing', B not in fd)
check('cmp;je;add;ret: the cmp keeps zf', M('zf') & fd.get(B, 0) == 0)
# loop: dec ecx ; jne top ; ret
t, m = lift('49 75fd c3'.replace(' ', ''))
check('dec;jne top;ret: dec keeps zf (the jne reads it) and everything exit-live', m['flagdrop'].get(B, 0) == 0)
# a div between: add ; div ecx ; ret -> the add precedes a faulting instruction: ALL live
t, m = lift('01d8f7f1c3')
check('add;div;ret: the add before a fault drops nothing', B not in m['flagdrop'])
# add ; call ; ret
t, m = lift('01d8e800000000c3')
check('add;call;ret: the add before a call drops nothing', B not in m['flagdrop'])
# add ; adc ; ret: add's cf is read by adc, its other five are overwritten by adc
t, m = lift('01d811d8c3')
check('add;adc;ret: add drops pf af zf sf of, keeps cf', m['flagdrop'].get(B) == M('pf', 'af', 'zf', 'sf', 'of'))
# setcc after: cmp ; sete al ; xor eax,eax ; ret
t, m = lift('39d80f94c031c0c3')
check('cmp;sete;xor;ret: cmp keeps zf', not (m['flagdrop'].get(B, 0) & M('zf')))
check('cmp;sete;xor;ret: cmp drops cf pf af sf of', m['flagdrop'].get(B) == M('cf', 'pf', 'af', 'sf', 'of'))
# the dropped flag has no store at the address: lift with and without
t0 = lift('01d801d1c3')[0]
check('the first add emits no store to cf (dropped) but the second does', t0.count('cf = ') == 1)
print('FAIL: %s' % FAILS if FAILS else 'all ok')
sys.exit(1 if FAILS else 0)
