#!/usr/bin/env python3
"""T1: tools/pipeline/traced.py - a traced run writes what a plain `python3 -I` run writes, records the files it read
(its imports too) and the directories it listed, never its outputs; check() is True only while every input is
unchanged."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.abspath(os.path.join(HERE, '..', '..', 'tools', 'pipeline'))
sys.path.insert(0, PIPE)
import traced  # noqa: E402

fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


SCRIPT = r'''
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
import helper
src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
names = sorted(os.listdir(os.path.join(src, 'rules')))
text = open(os.path.join(src, 'input.txt')).read()
open(os.path.join(out, 'result.txt'), 'w').write(helper.f(text) + '|' + ','.join(names) + '\n')
open(os.path.join(out, 'result.txt')).read()     # reading its own output: not an input
'''


def main():
    t = tempfile.mkdtemp(prefix='traced-')
    try:
        tool, src = os.path.join(t, 'tool'), os.path.join(t, 'src')
        os.makedirs(os.path.join(tool, 'lib')); os.makedirs(os.path.join(src, 'rules'))
        open(os.path.join(tool, 'prep.py'), 'w').write(SCRIPT)
        open(os.path.join(tool, 'lib', 'helper.py'), 'w').write('def f(t):\n    return t.upper().strip()\n')
        open(os.path.join(src, 'input.txt'), 'w').write('hello\n')
        open(os.path.join(src, 'rules', 'A1.py'), 'w').write('x')
        plain, tr = os.path.join(t, 'plain'), os.path.join(t, 'tr')
        subprocess.run([sys.executable, '-I', os.path.join(tool, 'prep.py'), src, plain], check=True)
        deps = os.path.join(t, 'deps.json')
        argv = [os.path.join(tool, 'prep.py'), src, tr]
        r = subprocess.run([sys.executable, '-I', os.path.join(PIPE, 'traced.py'), deps, '--exclude', tr, '--'] + argv)
        ok('traced run: exit 0 and the same output as a plain -I run', r.returncode == 0 and
           open(os.path.join(plain, 'result.txt')).read() == open(os.path.join(tr, 'result.txt')).read())
        d = json.load(open(deps))
        ok('records the input, the imported module and the script', all(p in d['files'] for p in (
            os.path.join(src, 'input.txt'), os.path.join(tool, 'lib', 'helper.py'), os.path.join(tool, 'prep.py'))))
        ok('records the listed directory, never the outputs', os.path.join(src, 'rules') in d['dirs'] and
           not any(p.startswith(tr) for p in list(d['files']) + list(d['dirs'])))
        ok('check: unchanged inputs', traced.check(deps, argv))
        ok('check: another argv is not the same run', not traced.check(deps, argv[:-1] + [plain]))
        open(os.path.join(src, 'input.txt'), 'w').write('hello!\n')
        ok('check: a changed input', not traced.check(deps, argv))
        open(os.path.join(src, 'input.txt'), 'w').write('hello\n')
        ok('check: back as it was', traced.check(deps, argv))
        open(os.path.join(src, 'rules', 'B2.py'), 'w').write('y')
        ok('check: a file added to a listed directory', not traced.check(deps, argv))
        os.remove(os.path.join(src, 'rules', 'B2.py'))
        open(os.path.join(tool, 'lib', 'helper.py'), 'a').write('# edit\n')
        ok('check: an imported module changed', not traced.check(deps, argv))
        open(os.path.join(tool, 'lib', 'helper.py'), 'w').write('def f(t):\n    return t.upper().strip()\n')
        os.environ['FNVNX_PREPARE_SKIP'] = 'X1'
        ok('check: another FNVNX_* environment', not traced.check(deps, argv))
        os.environ.pop('FNVNX_PREPARE_SKIP')
        ok('check: all as recorded again', traced.check(deps, argv))
        r = subprocess.run([sys.executable, '-I', os.path.join(PIPE, 'traced.py'), os.path.join(t, 'd2.json'), '--',
                            os.path.join(tool, 'prep.py'), os.path.join(t, 'nosuch'), tr], capture_output=True)
        ok('a failing script: its exit status, no record', r.returncode != 0 and not os.path.exists(os.path.join(t, 'd2.json')))
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('test_traced: all ok' if not fails else 'test_traced: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
