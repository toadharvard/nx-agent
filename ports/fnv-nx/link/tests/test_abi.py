#!/usr/bin/env python3
"""Check of link/abi.py: the contract model on pairs of MSVC names (equal, equivalent, and every differing component), the gate over sympair
rows (universe, canonical definition, unnamed rows, kind mismatch, undecodable names) and the CLI."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi
import abi_gaterun
import store

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def part(what, ref, dfn, text):
    got = abi.compare(ref, dfn)
    check(what, got is not None and text in got, True) if text else check(what, got, None)


part('identical', '?f@A@@QAEHH@Z', '?f@A@@QAEHH@Z', '')
part('int vs unsigned: same class', '?f@A@@QAEHH@Z', '?f@A@@QAEIH@Z', '')
part('char* vs unsigned char*', '?f@@YAHPAD@Z', '?f@@YAHPAE@Z', '')
part('pointer to class vs void*', '?f@@YAXPAVA@@@Z', '?f@@YAXPAX@Z', '')
part('enum vs int', '?f@@YAXW4C@@@Z', '?f@@YAXH@Z', '')
part('cdecl vs stdcall without arguments', '?f@@YAXXZ', '?f@@YGXXZ', '')
part('result void vs int', '?f@A@@QAEXH@Z', '?f@A@@QAEHH@Z', 'result class VOID vs I32')
part('result float vs int', '?f@@YAMH@Z', '?f@@YAHH@Z', 'result class F32 vs I32')
part('bool vs char result', '?f@@YA_NXZ', '?f@@YADXZ', 'result class B8 vs I8')
part('narrow vs wide parameter', '?f@@YAXD@Z', '?f@@YAXH@Z', 'parameter 0 class I8 vs I32')
part('float vs double parameter', '?f@@YAXM@Z', '?f@@YAXN@Z', 'parameter 0 class F32 vs F64')
part('extra parameter', '?f@@YAXH@Z', '?f@@YAXHH@Z', 'parameter count 1 vs 2')
part('member vs free', '?f@A@@QAEXH@Z', '?f@@YAXH@Z', 'member on one side')
part('cdecl vs stdcall with arguments', '?f@@YAXH@Z', '?f@@YGXH@Z', 'callee pops differ')
part('cdecl vs fastcall', '?f@@YAXHH@Z', '?f@@YIXHH@Z', 'x86 parameter locations differ')
part('aggregate by value, other type', '?f@@YAXVA@@@Z', '?f@@YAXVB@@@Z', 'aggregate parameter 0 differs')
part('aggregate by value, same type', '?f@@YAXVA@@@Z', '?f@@YAXVA@@@Z', '')
part('aggregate result, other type', '?f@@YA?AVA@@XZ', '?f@@YA?AVB@@XZ', 'result class AGG:A vs AGG:B')
part('variadic on one side', '?f@@YAXHZZ', '?f@@YAXH@Z', 'variadic')
part('variadic both', '?f@@YAXHZZ', '?f@@YAXHZZ', '')
part('undecoded', '?f@@YAXV?$A@$1?x@@@@Z', '?f@@YAXXZ', 'undecoded name')
part('long double vs double', '?f@@YAXO@Z', '?f@@YAXN@Z', 'parameter 0 class F80 vs F64')
part('aggregate result adds a hidden pointer', '?f@A@@QAE?AVB@@XZ', '?f@A@@QAE?AVB@@XZ', '')
c = abi.contract('?f@A@@QAE?AVB@@H@Z')
check('hidden result pointer after this', [x[0] for x in c.x86], ['P', 'P', 'I32'])
check('this in ecx, the rest on the stack', [x[1] for x in c.x86][:2], ['ecx', ('stk', (0, ()))])
check('a64: x registers', [x[1] for x in abi.contract('?f@@YAXHMN_J@Z').a64], [('x', 0), ('v', 0), ('v', 1), ('x', 1)])
check('x86 stack words', [x[1] for x in abi.contract('?f@@YAXH_JN@Z').x86], [('stk', (0, ())), ('stk', (4, ())), ('stk', (12, ()))])
check('callee pop only for callee-pop conventions', (abi.contract('?f@@YGXH@Z').pop, abi.contract('?f@@YAXH@Z').pop), (('I32',), ()))

# the gate
def row(sym, ms, kind='func', df='ref'):
    return (sym, ms, kind, df)


def run(objs):
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'obj.json')
    store.save(p, 'obj', {(ob, 'sympair'): rows for ob, rows in objs.items()})
    rc, r = abi_gaterun.run(abi, ['--obj', p], 'abi')
    return rc, r


rc, r = run({'a.o': [row('_Z1fi', '?f@@YAHH@Z', df='def'), row('_Z1gi', '?g@@YAXH@Z', df='ref')],
             'b.o': [row('_Z1fi', '?f@@YAHH@Z'), row('_Z1hv', '?h@@YAXXZ'), row('_ZTV1A', '', 'vtable', 'def')],
             'c.o': [row('_Z1fi', '?f@@YAHI@Z'), row('_Z1gi', '?g@@YAXH@Z', df='def')]})
check('green: both refs of f, the ref of g; h has no definition and is out', (rc, r['universe'], r['certified'], r['status']), (0, 3, 3, 'green'))
rc, r = run({'a.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')], 'b.o': [row('_Z1fi', '?f@@YAXH@Z')], 'c.o': [row('_Z1fi', '?f@@YGHH@Z')]})
check('red items are named', (rc, [u.split(' [')[0] for u in r['uncertified']]), (1, ['b.o:_Z1fi', 'c.o:_Z1fi']))
check('reasons', ('result class VOID vs I32' in r['uncertified'][0], 'callee pops differ' in r['uncertified'][1]), (True, True))
rc, r = run({'a.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')], 'b.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')]})
check('a second definition is compared with the first', (rc, r['universe']), (0, 1))
rc, r = run({'a.o': [row('_Z1fi', '', 'unnamed:local', df='def')], 'b.o': [row('_Z1fi', '?f@@YAHH@Z')]})
check('definition without a name', (rc, r['uncertified'][0].startswith('b.o:_Z1fi [definition in a.o has no MSVC name')), (1, True))
rc, r = run({'a.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')], 'b.o': [row('_Z1fi', '', 'unnamed:why')]})
check('reference without a name', (rc, 'the row has no MSVC name' in r['uncertified'][0]), (1, True))
rc, r = run({'a.o': [row('_ZN1AC1Ev', '??0A@@QAE@XZ', 'ctor.c1', 'def')], 'b.o': [row('_ZN1AC1Ev', '?f@A@@QAEXXZ', 'func')]})
check('kind mismatch', (rc, 'kind func vs the definition' in r['uncertified'][0]), (1, True))
rc, r = run({})
check('empty is green with universe 0', (rc, r['universe'], r['status']), (0, 0, 'green'))
d = tempfile.mkdtemp()
bad = os.path.join(d, 'bad.json')
open(bad, 'w').write('[]')
check('malformed input is an error', abi.main(['--obj', bad]), 2)
exe = [sys.executable, '-I', os.path.join(HERE, '..', 'abi.py')]
check('cli equal', subprocess.run(exe + ['--decl', '?f@@YAHH@Z', '?f@@YAHI@Z'], capture_output=True).returncode, 0)
r = subprocess.run(exe + ['--decl', '?f@@YAHH@Z', '?f@@YAXH@Z'], capture_output=True, text=True)
check('cli differ', (r.returncode, 'result class' in r.stdout), (1, True))

for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
