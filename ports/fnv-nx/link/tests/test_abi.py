#!/usr/bin/env python3
"""Check of link/abi.py: the contract model on pairs of MSVC names (equal, equivalent, and every differing component), the gate over sympair
rows (universe, canonical definition, unnamed rows, kind mismatch, undecodable names) and the CLI."""
import os, subprocess, sys, tempfile
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi
import abi_gaterun
import store
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
def part(what, ref, dfn, text):
    got = abi.compare(ref, dfn)
    case('part', got=(got is not None and text in got) if text else got, want=True if text else None, key=what)


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
case('hidden result pointer after this', got=[x[0] for x in c.x86], want=['P', 'P', 'I32'])
case('this in ecx, the rest on the stack', got=[x[1] for x in c.x86][:2], want=['ecx', ('stk', (0, ()))])
case('a64: x registers', got=[x[1] for x in abi.contract('?f@@YAXHMN_J@Z').a64], want=[('x', 0), ('v', 0), ('v', 1), ('x', 1)])
case('x86 stack words', got=[x[1] for x in abi.contract('?f@@YAXH_JN@Z').x86], want=[('stk', (0, ())), ('stk', (4, ())), ('stk', (12, ()))])
case('callee pop only for callee-pop conventions', got=(abi.contract('?f@@YGXH@Z').pop, abi.contract('?f@@YAXH@Z').pop), want=(('I32',), ()))

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
case('green: both refs of f, the ref of g; h has no definition and is out', got=(rc, r['universe'], r['certified'], r['status']), want=(0, 3, 3, 'green'))
rc, r = run({'a.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')], 'b.o': [row('_Z1fi', '?f@@YAXH@Z')], 'c.o': [row('_Z1fi', '?f@@YGHH@Z')]})
case('red items are named', got=(rc, [u.split(' [')[0] for u in r['uncertified']]), want=(1, ['b.o:_Z1fi', 'c.o:_Z1fi']))
case('reasons', got=('result class VOID vs I32' in r['uncertified'][0], 'callee pops differ' in r['uncertified'][1]), want=(True, True))
rc, r = run({'a.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')], 'b.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')]})
case('a second definition is compared with the first', got=(rc, r['universe']), want=(0, 1))
rc, r = run({'a.o': [row('_Z1fi', '', 'unnamed:local', df='def')], 'b.o': [row('_Z1fi', '?f@@YAHH@Z')]})
case('definition without a name', got=(rc, r['uncertified'][0].startswith('b.o:_Z1fi [definition in a.o has no MSVC name')), want=(1, True))
rc, r = run({'a.o': [row('_Z1fi', '?f@@YAHH@Z', df='def')], 'b.o': [row('_Z1fi', '', 'unnamed:why')]})
case('reference without a name', got=(rc, 'the row has no MSVC name' in r['uncertified'][0]), want=(1, True))
rc, r = run({'a.o': [row('_ZN1AC1Ev', '??0A@@QAE@XZ', 'ctor.c1', 'def')], 'b.o': [row('_ZN1AC1Ev', '?f@A@@QAEXXZ', 'func')]})
case('kind mismatch', got=(rc, 'kind func vs the definition' in r['uncertified'][0]), want=(1, True))
rc, r = run({})
case('empty is green with universe 0', got=(rc, r['universe'], r['status']), want=(0, 0, 'green'))
d = tempfile.mkdtemp()
bad = os.path.join(d, 'bad.json')
open(bad, 'w').write('[]')
case('malformed input is an error', got=abi.main(['--obj', bad]), want=2)
exe = [sys.executable, '-I', os.path.join(HERE, '..', 'abi.py')]
case('cli equal', got=subprocess.run(exe + ['--decl', '?f@@YAHH@Z', '?f@@YAHI@Z'], capture_output=True).returncode, want=0)
r = subprocess.run(exe + ['--decl', '?f@@YAHH@Z', '?f@@YAXH@Z'], capture_output=True, text=True)
case('cli differ', got=(r.returncode, 'result class' in r.stdout), want=(1, True))

for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
