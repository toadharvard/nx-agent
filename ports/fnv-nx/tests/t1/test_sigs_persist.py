#!/usr/bin/env python3
"""T1: tools/link/sigs.py's persisted parses (sigs.persist): a later process gets Sig objects equal to fresh parses,
each call its own object; pruning keeps only the entries a process used; another parser (sigs.py's bytes) or
FNVNX_SIGS_PERSIST=0 means no store."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = os.path.abspath(os.path.join(HERE, '..', '..', 'tools', 'link'))
NAMES = [('m', '?Get@Foo@@QAEHH@Z', 'public: int __thiscall Foo::Get(int)'),
         ('m', '??0Foo@@QAE@XZ', 'public: __thiscall Foo::Foo(void)'),
         ('m', '_memset', None),
         ('i', '_ZN3Foo3GetEi', 'Foo::Get(int)'),
         ('i', '_ZNK3Foo4SizeEv', 'Foo::Size() const'),
         ('i', 'g_count', None)]
fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


CHILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import sigs
store, which, prune = sys.argv[2], json.loads(sys.argv[3]), sys.argv[4] == '1'
if os.environ.get('FAKE_ID'):
    sigs._code_id = lambda: 'other'
sigs.persist(store, prune=prune)
out = []
for kind, n, d in which:
    s = (sigs.parse_msvc if kind == 'm' else sigs.parse_itanium)(n, d)
    out.append({k: repr(getattr(s, k)) for k in sigs.Sig.__slots__})
a = sigs.parse_itanium('_ZN3Foo3GetEi', 'Foo::Get(int)')
a.qual.append('edited')
b = sigs.parse_itanium('_ZN3Foo3GetEi', 'Foo::Get(int)')
print(json.dumps({'sigs': out, 'hits_before': len(sigs._P['map'] or {}) - sigs._P['new'],
                  'isolated': 'edited' not in b.qual}))
'''


def child(store, which, prune=False, env=None):
    r = subprocess.run([sys.executable, '-I', '-c', CHILD, LINK, store, __import__('json').dumps(which),
                        '1' if prune else '0'], capture_output=True, text=True, env=dict(os.environ, **(env or {})))
    if r.returncode:
        raise SystemExit(r.stderr)
    return __import__('json').loads(r.stdout)


def entries(store):
    import marshal
    with open(store, 'rb') as f:
        return marshal.load(f)['map']


def main():
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, 'cache', 'sigs.marshal')
        fresh = child(os.path.join(t, 'none.pkl'), NAMES, env={'FNVNX_SIGS_PERSIST': '0'})
        ok('FNVNX_SIGS_PERSIST=0: no store written', not os.path.exists(os.path.join(t, 'none.pkl')))
        cold = child(store, NAMES, prune=True)
        ok('cold: the store is written with every parse', os.path.exists(store) and len(entries(store)) == len(NAMES))
        warm = child(store, NAMES, prune=True)
        ok('warm: the parses come from the store', warm['hits_before'] == len(NAMES))
        ok('cold, warm and unpersisted parses are equal', fresh['sigs'] == cold['sigs'] == warm['sigs'])
        ok('each call gets its own object (an edit does not reach the next caller)', warm['isolated'] and
           cold['isolated'])
        child(store, NAMES[:2] + NAMES[3:4], prune=True)
        ok('prune: only the entries this process used are kept', len(entries(store)) == 3)
        child(store, NAMES, prune=False)
        ok('no prune (bodies, vtables): entries are added, none dropped', len(entries(store)) == len(NAMES))
        other = child(store, NAMES, prune=True, env={'FAKE_ID': '1'})
        ok('another parser: the store is not used', other['hits_before'] == 0 and other['sigs'] == fresh['sigs'])
    print('test_sigs_persist: all ok' if not fails else 'test_sigs_persist: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
