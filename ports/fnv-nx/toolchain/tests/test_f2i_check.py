#!/usr/bin/env python3
"""Test of toolchain/f2i_check/fnvnx_f2i_check.cc (gate fpexact, check 3), built from the tracked source as a recipe plugin stage on the
guest compiler (plugbuild.py), loaded by the product flags of a guest root (which include -Werror=attributes). Cases:
  - a fixture using __attribute__((fnvnx_f2i)) on a conversion helper builds with the plugin, and the plugin's JSON (written next to the
    object by the CC stage, out=/out/f2i.json) holds the helper, one conversion site, no finding;
  - the same fixture without the plugin does not build: -Werror=attributes (the attribute is unknown to the compiler);
  - a negative: a float -> integer conversion outside a helper (cast, builtin lrintf, vector convert) is a finding, kind and function named;
  - a plugin without its out argument is an error.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_f2i_check')
FLAGS = pb.PRODUCT_FLAGS + ['-Werror=attributes']
OK = '''#define F2I __attribute__((fnvnx_f2i))
F2I int helper(float f) { return (int)f; }
int user(float f) { return helper(f) + 1; }
'''
BAD = OK + '''int bad_cast(float f) { return (int)f; }
int bad_builtin(float f) { return __builtin_lrintf(f); }
typedef float v4f __attribute__((vector_size(16)));
typedef int v4i __attribute__((vector_size(16)));
v4i bad_vec(v4f v) { return __builtin_convertvector(v, v4i); }
'''


def run(td, name, text, plugins, **kw):
    src = os.path.join(td, name + '.cpp')
    with open(src, 'w') as f:
        f.write(text)
    files = {}
    r = pb.compile(src, os.path.join(td, name + '.o'), plugins, flags=FLAGS, files=files, **kw)
    return r, (json.load(open(files['f2i.json'])) if 'f2i.json' in files else None)


def main():
    td = tempfile.mkdtemp(prefix='test-f2i-')
    try:
        so = pb.build('f2i_check', td)
        check('the plugin builds as a recipe plugin stage against msvcmodel\'s headers', os.path.basename(so) == 'fnvnx_f2i_check.so')
        r, j = run(td, 'ok', OK, [so])
        check('a fixture using the attribute builds with the plugin', r.returncode == 0, r.stderr[-1500:])
        check('the plugin writes its result next to the object', j is not None)
        if j:
            check('the helper is named, one conversion site, no finding',
                  j['format'] == 'fnvnx-f2i/1' and j['helpers'] == ['int helper(float)'] and j['sites'] == 1 and j['findings'] == [], j)
        r, _ = run(td, 'noplug', OK, [])
        check('without the plugin the build fails on -Werror=attributes',
              r.returncode != 0 and 'fnvnx_f2i' in r.stderr and 'attributes' in r.stderr, r.stderr[-1500:])
        r, j = run(td, 'bad', BAD, [so])
        check('the negative fixture builds (the check reports, the gate decides)', r.returncode == 0, r.stderr[-1500:])
        if j:
            got = sorted((f['function'], f['kind']) for f in j['findings'])
            check('a conversion outside a helper is a finding: cast (conv), lrintf and vector convert (builtin: IFN_VEC_CONVERT)',
                  got == [('int bad_builtin(float)', 'builtin'), ('int bad_cast(float)', 'conv'), ('v4i bad_vec(v4f)', 'builtin')], got)
            check('the helper is still not a finding', all(f['function'] != 'int helper(float)' for f in j['findings']), j['findings'])
        else:
            check('the negative fixture has a result', False)
        # no out argument: the plugin refuses
        pa = pb.REQUIRED_ARGS.pop('f2i_check')
        try:
            res, _ = pb._recipe(td, [('f2i_check', {})], {'t.cpp': 'int t();\n'}, FLAGS)
            check('a plugin without its out argument is an error', res['status'] != 'green' and 'out=FILE' in json.dumps(res.get('failure')), res.get('failure'))
        finally:
            pb.REQUIRED_ARGS['f2i_check'] = pa
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
