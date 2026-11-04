#!/usr/bin/env python3
"""V2 adversarial cases for S11 a23478e (GOTO1-CONST): "a const integer literal declaration stays as written unless a
forward goto jumps past it into its scope". Each case runs the rule (tools/rules/s1_compile.py r_goto_scalar_inits)
on our own synthetic function, compiles the output with GCC (-fpermissive, as compile.sh) and runs it under
qemu-aarch64: the output must compile and give the value MSVC's code gives (n is a constant expression on both).

    python3 tests/v2/test_s11_goto1_const.py     # needs aarch64-linux-gnu-g++

  B1  backward goto from below the block into its scope, past `const int n = 4;`, which the rule keeps as written
      (it only looks for forward gotos). GCC -fpermissive warns ("skips initialization") and folds n: ok
  B2  the same with the goto in a later sibling block and n as an array bound
  C1  the forward goto of a23478e's own test (becomes static const)
Without -fpermissive B1/B2 are errors, so the verdict depends on compile.sh keeping -fpermissive.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
TOOLS = os.path.join(PORT, 'tools')
sys.path.insert(0, TOOLS)
spec = importlib.util.spec_from_file_location('s1_compile', os.path.join(TOOLS, 'rules', 's1_compile.py'))
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')

PRE = 'struct H { int n; };\nextern "C" int use(int x) { return x; }\n'
CASES = [
    ('B1', 'int g(H* h, int e)\n{\n    {\n        const int n = 4;\n    again:\n        h->n += use(n);\n    }\n'
           '    if (--e > 0) goto again;\n    return h->n;\n}\n'),
    ('B2', 'int g(H* h, int e)\n{\n    {\n        const int n = 4;\n        int a[n];\n        a[0] = e;\n    retry:\n'
           '        h->n = a[0] + n;\n    }\n    { if (h->n < 0) goto retry; }\n    return h->n;\n}\n'),
    ('C1', 'int g(H* h, int e)\n{\n    if (e) goto out;\n    const int slots = 4;\n    int a[slots];\n'
           '    a[0] = h->n;\nout:\n    return e ? 0 : slots;\n}\n'),
]


# (argument e, the value MSVC's code returns: n folded to 4)
want = {'B1': (3, 12), 'B2': (1, 5), 'C1': (0, 4)}


def main():
    work = tempfile.mkdtemp(prefix='v2-goto1-')   # raises when it cannot create the directory
    fails = 0
    try:
        for name, src in CASES:
            out = s1.r_goto_scalar_inits('t.cpp', src, Counter())
            p, exe = os.path.join(work, name + '.cpp'), os.path.join(work, name)
            open(p, 'w').write(PRE + out + 'int main() { H h = {0}; return g(&h, %d); }\n' % want[name][0])
            r = subprocess.run([CXX, '-static', '-O1', '-fpermissive', '-fms-extensions', '-w', p, '-o', exe],
                               capture_output=True, text=True)
            if r.returncode:
                err = next((l for l in r.stderr.splitlines() if 'error' in l), '')
                print('FAIL %s: %s' % (name, err.split(work + '/')[-1]))
                fails += 1
                continue
            got = subprocess.run(['qemu-aarch64', exe]).returncode
            ok = got == want[name][1]
            print('%s %s: compiles, returns %d (MSVC %d)' % ('ok' if ok else 'FAIL', name, got, want[name][1]))
            fails += not ok
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ok' if not fails else 'FAILED (%d)' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
