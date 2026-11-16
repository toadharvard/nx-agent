"""The suspension poll (fnvlift/flaglive.py poll_set, rt/fnvl.h FNVL_POLL; FLAGLIVE.md P2'): P = back-edge targets of the function's CFG and its entries.
  (1) backedge_targets on hand-made graphs: a loop, a self-loop, nested loops, a diamond and a chain (no back edge), a cycle only through a back edge from a later block;
  (2) the solver: every point of P keeps all flags live (the flags written before a loop header are not dropped; the same write before a straight-line block is);
  (3) lifted buffers: the entry and each loop header carry exactly one FNVL_POLLP at the first emission of the instruction, a straight-line body only the entry's; a handler loop (`loop`) and a jump-table-free
      conditional loop are found by the one rule; a poll names the address of its instruction; the macro and the struct field exist in rt/fnvl.h.
    test_poll.py --sdmrows PATH"""
import os, re, sys, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
from fnvlift import flaglive as fl, inputs
from fnvlift.image import Image
import fnvlift as L
FAILS = []


def check(n, ok):
    print('%-78s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


# (1) back edges
bt = fl.backedge_targets
check('chain: no back edge', bt({1: [2], 2: [3], 3: []}) == frozenset())
check('diamond: no back edge', bt({1: [2, 3], 2: [4], 3: [4], 4: []}) == frozenset())
check('loop 1->2->3->2: header 2', bt({1: [2], 2: [3], 3: [2, 4], 4: []}) == frozenset({2}))
check('self loop: the node', bt({1: [1]}) == frozenset({1}))
check('nested loops: both headers', bt({1: [2], 2: [3], 3: [4], 4: [3, 5], 5: [2, 6], 6: []}) == frozenset({2, 3}))
check('a cycle entered from a later block (edge to a lower address): found', bt({5: [1], 1: [2], 2: [5, 3], 3: []}) in (frozenset({5}), frozenset({1})))
check('every cycle has a header: removing the headers leaves an acyclic graph',
      all(bt({a: [t for t in ts if t not in h and a not in h] for a, ts in g.items()}) == frozenset()
          for g in ({1: [2], 2: [3], 3: [2, 4], 4: []}, {1: [2], 2: [3], 3: [4], 4: [3, 5], 5: [2, 6], 6: []}) for h in [bt(g)]))
check('edges to addresses that are not nodes are ignored', bt({1: [9, 2], 2: [1]}) == frozenset({1}))
check('poll_set adds the entries that are nodes', fl.poll_set({1: [2], 2: []}, [1, 7]) == frozenset({1}))
# (2) solver
E = fl.EXIT
rec = lambda **k: dict(dict(reads=frozenset(), kills=frozenset('cf pf af zf sf of'.split()), esc=False, succs=(), writes=frozenset('cf pf af zf sf of'.split())), **k)
g = {1: rec(succs=(2,)), 2: rec(succs=(3,)), 3: rec(succs=(E,))}
check('straight line: the first write is dead (overwritten by the next)', fl.dropped(g) and fl.dropped(g).get(1) == frozenset('cf pf af zf sf of'.split()))
check('the same line with a poll at the second instruction: nothing is dropped before it', 1 not in fl.dropped(g, frozenset({2})))
check('a poll point on the writer itself: its own write stays live after only if live_out says so (live_in is ALL, live_out unchanged)', fl.dropped(g, frozenset({1})).get(1) == frozenset('cf pf af zf sf of'.split()))

# (3) lifted buffers
d = tempfile.mkdtemp(prefix='poll-')
open(d + '/universe.tsv', 'w').write('')
open(d + '/depth.tsv', 'w').close(); open(d + '/udsail.tsv', 'w').write('# fnvnx-udsail/2\n')
json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': n == 'gs'} for n in ('es', 'ds', 'fs', 'gs')}}}, open(d + '/segwrites.json', 'w'))
inputs.reset(); _inputs.configure(); inputs.reset()
inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=_inputs.X86SYS, sdmrows=_inputs.sdmrows_arg(), cpumodel=_inputs.CPUMODEL, rcptab=_inputs.RCPTAB, rcpcert=_inputs.RCPCERT,
                 udsail=d + '/udsail.tsv', segwrites=d + '/segwrites.json', image='buf')
B = 0x401000


def lift(h):
    code = bytes.fromhex(h)
    t, m = L.lift(Image.from_bytes(code, B), B, len(code))[:2]
    return t, m


def polls(t): return re.findall(r'FNVL_POLLP\((0x[0-9a-f]+)u\)', t)


t, m = lift('01d801d1c3')
check('straight line: one poll, at the entry', polls(t) == ['0x401000'] and m['poll'] == [B])
t, m = lift('4975fdc3')                  # top: dec ecx; jne top; ret
check('dec;jne top: the entry is the header: one poll', polls(t) == ['0x401000'] and m['poll'] == [B])
t, m = lift('31c049' '75fdc3')           # xor eax,eax; top: dec ecx; jne top; ret
check('xor;dec;jne: polls at the entry and the header (+2)', polls(t) == ['0x401000', '0x401002'] and m['poll'] == [B, B + 2])
t, m = lift('01d849' '75fdc3')           # add; top: dec ecx; jne top; ret : the add's flags are live (the poll at top)
check('a flag write before a loop header is kept: no flagdrop for the add', B not in m['flagdrop'] and 'cf = ' in t)
t, m = lift('b905000000' 'e2fe' 'c3')    # mov ecx,5; top: loop top; ret : the loop is a hand handler (jump_to), the poll is at its own address
check('loop (hand handler) back edge found by the one rule: poll at the loop instruction', polls(t) == ['0x401000', '0x401005'] and m['poll'] == [B, B + 5])
def first_emission(t, a):
    L = t.split('\n'); i = next(k for k, l in enumerate(L) if l.startswith('FNVL_POLLP(0x%xu)' % a))
    return L[i - 1] in ('L_%08x: ;' % a, 'TRACE(0x%xu);' % a) or 'u32 E=esp;' in L[i - 1]
check('every poll is the first emission of its instruction (after its label / TRACE line)', all(first_emission(t, a) for a in m['poll']))
check('the poll macro and the susp field are in rt/fnvl.h', all(s in open(os.path.join(_inputs.LIFT, 'rt', 'fnvl.h')).read() for s in ('#define FNVL_POLL(SO,SI,h)', 'u32 susp; }', 'fnvnx_safepoint(void *c, u32 pc)', '__builtin_offsetof(struct cpu, susp) == 504')))
# x87 function: the poll re-enters with the pre-state of its instruction
t, m = lift('d9e8' 'dd d8'.replace(' ', '') + 'c3')    # fld1; fstp st(0); ret (static x87 stack)
check('x87 function: the poll has the x87 stack store before SYNC_OUT and the load after SYNC_IN', bool(re.search(r'FNVL_POLL\([^\n]*c->ftop=[^\n]*SYNC_OUT;,SYNC_IN;[^\n]*ft0=', t)))
print('FAIL: %s' % FAILS if FAILS else 'all ok')
sys.exit(1 if FAILS else 0)
