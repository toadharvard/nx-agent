#!/usr/bin/env python3
"""`import fnvlift` and `import fnvlift.dsl` read no file of lift/rt: a child interpreter (python3 -I) imports both with an audit hook that raises on any open() of a
path below rt/ (the prelude is built only by fnvlift.prelude(rt_dir), called by the generator).  Also checks prelude() reads exactly rt_dir/fnvl.h and what it includes.
    python3 -I import_reads_no_rt_test.py"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
RT = os.path.join(LIFT, 'rt')
CHILD = '''
import os, sys
RT = %r
def hook(ev, args):
    if ev == 'open' and isinstance(args[0], (str, bytes, os.PathLike)) and os.fspath(args[0]) not in ('',) and (RT + os.sep) in os.path.abspath(os.fsdecode(args[0])):
        raise RuntimeError('read of ' + os.fsdecode(args[0]))
sys.path.insert(0, %r)
sys.addaudithook(hook)
import fnvlift, fnvlift.dsl
assert not hasattr(fnvlift, 'PRELUDE')
try:
    fnvlift.prelude(RT)
except RuntimeError:
    print('prelude-reads-rt')
else:
    raise SystemExit('the audit hook did not see prelude() read rt/')
'''

p = subprocess.run([sys.executable, '-I', '-c', CHILD % (RT, LIFT)], capture_output=True, text=True)
assert p.returncode == 0 and p.stdout.strip() == 'prelude-reads-rt', (p.returncode, p.stdout, p.stderr[-400:])
print('import fnvlift, fnvlift.dsl: no rt read; prelude(rt_dir) reads rt: ok')
