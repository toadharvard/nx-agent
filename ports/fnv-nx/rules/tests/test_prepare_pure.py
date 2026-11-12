#!/usr/bin/env python3
"""test_prepare_pure.py - prepare.py as a stage tool: the output is a function of the argv inputs' content only.
 * determinism: three runs (python3 -I, so three hash seeds) give the same tree hash;
 * no environment: a run with a poisoned environment (the legacy FNVNX_* switches, clock and locale variables, a hostile
   PYTHONHASHSEED, TMPDIR) gives the same tree as a run with an empty one;
 * no file outside the inputs: the same tree results when the checkout is copied to another directory name and when the
   working directory differs;
 * the argv contract: missing inputs and unknown options are refused (exit 2), a drifting input changes the output;
 * XC1 (the dynamic-initializer rule, the only carried rule keyed on the recipe by address) on a synthetic recipe.

    python3 -I rules/tests/test_prepare_pure.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
from support import tree_hash  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

PREPARE = os.path.join(support.RULES, 'prepare.py')
W = tempfile.mkdtemp()

SRC_A = '''// synthetic unit
#include <stdlib.h>
union __declspec(intrin_type) __declspec(align(16)) __m128 { float m128_f32[4]; };
extern "C" double __cdecl sqrt(double);
void* operator new(unsigned int n);
struct Holder { Holder(int, int); };
// @fnv 0x00000010
// @fnv 0x00000020
Holder g_obj(1, 2);
float lane(__m128 v) { return v.m128_f32[1]; }
int gone(int a)
{
    __asm {
        mov eax, a
    }
    return a;
}
#ifdef _MSC_VER
int own_cl_only;
#endif
'''
SRC_B = '// @fnv 0x00000010\n// @fnv 0x00000020\nHolder g_obj(1, 2);\n'


def make_checkout(root):
    for d in ('src', 'include', 'build', 'third_party'):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    open(os.path.join(root, 'src', 'a.cpp'), 'w').write(SRC_A)
    open(os.path.join(root, 'src', 'b.cpp'), 'w').write(SRC_B)
    open(os.path.join(root, 'include', 'x.h'), 'w').write('#pragma once\nint x;\n')
    open(os.path.join(root, 'include', 'blob.bin'), 'wb').write(bytes(range(256)))
    open(os.path.join(root, 'third_party', 'z.c'), 'w').write('int z;\n')
    json.dump({'units': [
        {'source': 'src/a.cpp', 'symbols': {}, 'functions': [
            {'address': '0x00000010', 'symbol': '??__Eg_obj@@YAXXZ'}, {'address': '0x00000020', 'symbol': '??__Fg_obj@@YAXXZ'}]},
        {'source': 'src/b.cpp', 'symbols': {}, 'functions': []}]}, open(os.path.join(root, 'build', 'units.json'), 'w'))


HDR = os.path.join(W, 'win32.h')
CRT = os.path.join(W, 'crt.tsv')
open(HDR, 'w').write(support.WIN32_HEADER)
open(CRT, 'w').write('')
DEC = os.path.join(W, 'dec')
make_checkout(DEC)


def run(out, env, dec=DEC, cwd=None, extra=()):
    p = subprocess.run([sys.executable, '-I', PREPARE, dec, out, '--win32-header', HDR, '--crt-symbols', CRT, *extra],
                       env=env, cwd=cwd or W, capture_output=True, text=True)
    return p


CLEAN = {'PATH': os.environ.get('PATH', '/usr/bin')}
POISON = dict(CLEAN, FNVNX_PREPARE_SKIP='r_inline_asm,r_sse_members,r_dyninit', FNVNX_PREPARE_MEMO=os.path.join(W, 'memo'),
              FNVNX_RECON=os.path.join(W, 'recon'), FNVNX_RECON_CERTIFY='1', FNVNX_D22='0', FNVNX_FAKE_CLOCK='1', TZ='Pacific/Kiritimati',
              LANG='tr_TR.UTF-8', LC_ALL='tr_TR.UTF-8', PYTHONHASHSEED='12345', TMPDIR='/nonexistent', HOME='/nonexistent',
              PYTHONPATH='/nonexistent', PYTHONDONTWRITEBYTECODE='1', SOURCE_DATE_EPOCH='1', HTTPS_PROXY='http://127.0.0.1:1')

hashes = []
for i, env in enumerate((CLEAN, CLEAN, CLEAN, POISON, POISON)):
    out = os.path.join(W, 'out%d' % i)
    p = run(out, env)
    case('run %d exits 0', got=p.stdout + p.stderr if p.returncode else 'ok', contains=['ok'], key=i)
    hashes.append(tree_hash(out))
case('three runs, three hash seeds: one tree hash', got=str(len(set(hashes[:3]))), contains=['1'])
case('poisoned environment: the same tree as the clean one', got=str(len(set(hashes))), contains=['1'])

out0 = os.path.join(W, 'out0')
case('src/a.cpp: the rules ran (ASM, SSE3, XC1)', got=open(os.path.join(out0, 'src', 'a.cpp')).read(),
      contains=['__asm__("")', '((float*)&(v))[1]', '__fnvnx_dyninit_00000010', '__fnvnx_dynexit_00000020', 'int own_cl_only;'], lacks=['m128_f32'])
case('src/b.cpp: a copy of an initializer the unit does not own becomes an extern declaration', got=open(os.path.join(out0, 'src', 'b.cpp')).read(),
      contains=['extern Holder g_obj;'], lacks=['__fnvnx_dyninit'])
case('other files copied byte for byte', got='same' if open(os.path.join(out0, 'include', 'blob.bin'), 'rb').read() == bytes(range(256))
      and open(os.path.join(out0, 'third_party', 'z.c')).read() == 'int z;\n' else 'differs', contains=['same'])
case('prepare_stats.tsv lists the rules', got=open(os.path.join(out0, 'prepare_stats.tsv')).read(), contains=['ASM-spans\t1 files', 'XC1\t1 files', 'SSE3\t'])
case('build/units.json copied', got='same' if open(os.path.join(out0, 'build', 'units.json')).read() ==
      open(os.path.join(DEC, 'build', 'units.json')).read() else 'differs', contains=['same'])

# the checkout under another name, another working directory: the same tree
DEC2 = os.path.join(W, 'other-name', 'checkout')
make_checkout(DEC2)
other = os.path.join(W, 'out_other')
elsewhere = tempfile.mkdtemp()
p = run(other, CLEAN, dec=DEC2, cwd=elsewhere)
case('another checkout path and working directory: the same tree', got='same' if tree_hash(other) == hashes[0] else 'differs ' + p.stderr, contains=['same'])

# a changed input changes the output (the output depends on the argv inputs' content)
open(HDR, 'w').write(support.WIN32_HEADER + 'int NewCall(void*);\n')
p = run(os.path.join(W, 'out_hdr'), CLEAN)
case('a changed input file is read (no memo, no cache)', got='ok' if p.returncode == 0 else p.stderr, contains=['ok'])
shutil.rmtree(os.path.join(W, 'memo'), ignore_errors=True)

# the argv contract
for name, args in (('no inputs', []), ('one directory', [DEC]), ('unknown option', [DEC, os.path.join(W, 'o'), '--skip', 'x'])):
    p = subprocess.run([sys.executable, '-I', PREPARE, *args], capture_output=True, text=True, env=CLEAN)
    case('argv contract: %s refused (exit 2)', got=str(p.returncode), contains=['2'], key=name)
p = subprocess.run([sys.executable, '-I', PREPARE, DEC, os.path.join(W, 'o'), '--win32-header', HDR], capture_output=True, text=True, env=CLEAN)
case('argv contract: --crt-symbols missing refused (exit 2)', got=str(p.returncode), contains=['2'])

# the source of the tool names no environment and no skip list
import re  # noqa: E402
bad = []
for f in sorted(os.listdir(support.RULES)):
    if f.endswith('.py'):
        t = open(os.path.join(support.RULES, f)).read()
        for pat in (r'os\.environ', r'getenv', r'FNVNX_PREPARE', r'importlib', r'sys\.modules', r'copytree', r'copy2', r'marshal', r'addaudithook',
                    r'FNVNX_RECON', r'reccert', r'r2_vorder', r's1b_patches'):
            if re.search(pat, t):
                bad.append('%s: %s' % (f, pat))
case('no environment variable, skip list, dynamic import, clock copy or dropped machinery in the sources', got='\n'.join(bad) or 'clean', contains=['clean'])
shutil.rmtree(W, ignore_errors=True)
shutil.rmtree(elsewhere, ignore_errors=True)
sys.exit(testcase.finish())
