#!/usr/bin/env python3
"""tests/s13/test_renames.py - S1b's units_renames rows in the harness (J-0141).

config.units_renames reads patches/units_renames.tsv for the units whose prepared source carries an S1b patch;
engine.apply_renames gives a `def` row's definition its new MSVC name (what places the arguments on both machines)
and returns the `ref` rows as the caller's declarations; fnkeys adds a unit's rows to its functions' keys, so only
those units are fuzzed again.
"""
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DF = os.path.normpath(os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
FAIL = []

OLD = '?FUN_00eacd00@@YG_NPAX@Z'
NEW = '?FUN_00eacd00@FUN_00eacd00_s1b@@QAE_NPAX@Z'
REF_OLD = '_MyColl_WriteToStream@4'
REF_NEW = '?FUN_00a81010@FUN_00eacd00_s1b@@QAEEPAX@Z'


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def fresh_config(tree, tsv):
    os.environ['FNVNX_TREE'] = tree
    os.environ['FNVNX_UNITS_RENAMES'] = tsv
    if DF not in sys.path:
        sys.path.insert(0, DF)
    import config
    return importlib.reload(config)


def main():
    with tempfile.TemporaryDirectory() as d:
        tree = os.path.join(d, 'tree')
        os.makedirs(os.path.join(tree, 'src', 'a'))
        os.makedirs(os.path.join(tree, 'build'))
        open(os.path.join(tree, 'src', 'a', 'patched.cpp'), 'w').write(
            'struct FUN_00eacd00_s1b {}; /* fnv-nx XINC1: 0x00a81010 */\n')
        open(os.path.join(tree, 'src', 'a', 'plain.cpp'), 'w').write('int f() { return 0; }\n')
        tsv = os.path.join(d, 'units_renames.tsv')
        open(tsv, 'w').write('# source\tkind\taddress\told\tnew\n'
                             'src/a/patched.cpp\tdef\t0x00eacd00\t%s\t%s\n' % (OLD, NEW) +
                             'src/a/patched.cpp\tref\t0x00a81010\t%s\t%s\n' % (REF_OLD, REF_NEW) +
                             'src/a/plain.cpp\tdef\t0x00401000\t?f@@YAHXZ\t?f@@YGHXZ\n')
        config = fresh_config(tree, tsv)
        rows = config.units_renames()
        check([r[0] for r in rows] == ['src/a/patched.cpp'] * 2,
              'only the unit whose prepared source carries the S1b patch (%s)' % rows)

        import engine
        tdef = {0x00eacd00: OLD}
        ref = engine.apply_renames(tdef, rows)
        check(tdef[0x00eacd00] == NEW, 'def: the pristine name becomes the patched one')
        check(ref == {('src/a/patched.cpp', 0x00a81010): REF_NEW}, 'ref: the caller declaration (%s)' % ref)
        tdef = {0x00eacd00: NEW}
        engine.apply_renames(tdef, rows)
        check(tdef[0x00eacd00] == NEW, 'idempotent on a units.json prepare already rewrote')
        tdef = {}
        engine.apply_renames(tdef, rows)
        check(tdef.get(0x00eacd00) == NEW, 'a tree without units.json takes the row')
        tdef = {0x00eacd00: '?other@@YAXXZ'}
        engine.apply_renames(tdef, rows)
        check(tdef[0x00eacd00] == '?other@@YAXXZ', 'a name the tree gives otherwise is kept')

        import conv
        cv = conv.Conv(NEW, 'src/a/patched.cpp')
        check(cv.describe().startswith('this:'), 'the new name is a member: this in ecx (%s)' % cv.describe())

        config = fresh_config(tree, 'none')
        check(config.units_renames() == [], 'FNVNX_UNITS_RENAMES=none: off')

        # fnkeys: the rows go into the keys of their unit only
        targets = os.path.join(d, 't.jsonl')
        cat = os.path.join(d, 'cat.jsonl')
        with open(targets, 'w') as f, open(cat, 'w') as g:
            for a, s, calls in (('00eacd00', 'src/a/patched.cpp', []), ('00401000', 'src/a/plain.cpp', []),
                                ('00405000', 'src/b/caller.cpp', ['00eacd00'])):
                f.write(json.dumps(dict(address=a, source=s)) + '\n')
                g.write(json.dumps(dict(address=a, source=s, ohash='o' + a, calls=calls)) + '\n')
        stub = os.path.join(d, 'stubkeys.py')
        open(stub, 'w').write(
            'import sys; sys.path.insert(0, %r)\n' % DF +
            'import fnkeys\n'
            'class N:\n'
            '    def __init__(self, *a): pass\n'
            '    def keys(self, source, refs_out=None): return {"00eacd00": "n1", "00401000": "n2", "00405000": "n3"}\n'
            'fnkeys.Native = N\n'
            'fnkeys.harness_tag = lambda: "H"\n'
            'sys.argv = ["fnkeys"] + sys.argv[1:]\n'
            'fnkeys.main()\n')

        def keys(rn):
            out = os.path.join(d, 'keys.json')
            env = dict(os.environ, FNVNX_TREE=tree, FNVNX_UNITS_RENAMES=rn, FNVNX_DFLINK=d)
            subprocess.run([sys.executable, stub, targets, cat, out], check=True, env=env, capture_output=True)
            return json.load(open(out))
        on, off = keys(tsv), keys('none')
        plain = hashlib.sha1(b'H|o00401000|n2').hexdigest()[:20]
        check(on['00401000'] == off['00401000'] == plain, 'a unit without rows keeps its key')
        check(on['00eacd00'] != off['00eacd00'], 'a unit with rows gets a new key (fuzzed again)')
        check(on['00405000'] != off['00405000'],
              'a caller in another unit of a renamed definition gets a new key too (V1 R1)')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main())
