#!/usr/bin/env python3
"""repro_cname_abi.py - V1-link defect reproducer (C2/C3): resolve never compares a reference with a definition when
either declaration is an undecorated C name (`@f@N` fastcall, `_f@N` stdcall, `_f` cdecl: extern "C" in the decomp),
so a pair that needs a bridge (or is x86-inconsistent) is bound directly.

    repro_cname_abi.py <work dir>          # exit 1 while the defect is present

Hand-made two-unit tree (no decomp content), run through the code under test (`tools/link/fnvlink.py resolve`):

  def.cpp   extern "C" void __fastcall FUN_00c0ffee(T *self, void *edx_dummy, int a, int b)    -> '@FUN_00c0ffee@16'
            void __fastcall FUN_00c0fff0(T *self, void *edx_dummy, int a, int b)  (C++ decorated control)
  ref.cpp   struct H { void FUN_00c0ffee(int, int); void FUN_00c0fff0(int, int); };   thiscall members

x86: ecx = this, stack a, b -> the definition gets self = this, a, b (edx is the dummy). AArch64 without a bridge:
x0 = this, w1 = a, w2 = b -> the definition reads a from w2 (= b) and b from w3 (junk). The control pair (same
shapes, C++ names) gets an edx-dummy bridge; the C-named pair must get one too. The script also runs the direct
binding under qemu ILP32 (bridge_harness) to show what the definition receives.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import qemu_ilp32            # noqa: E402

LINK = os.path.join(HERE, '..', '..', 'link', 'fnvlink.py')
DEF_CPP = r'''
#define __fastcall
struct T { int v; };
extern "C" void __fastcall FUN_00c0ffee(T *self, void *edx_dummy, int a, int b) { self->v = a * 7 + b; }
void __fastcall FUN_00c0fff0(T *self, void *edx_dummy, int a, int b) { self->v = a * 5 + b; }
'''
REF_CPP = r'''
struct H { void FUN_00c0ffee(int, int); void FUN_00c0fff0(int, int); };
void call_both(H *h) { h->FUN_00c0ffee(1, 2); h->FUN_00c0fff0(3, 4); }
'''


def main():
    work = os.path.abspath(sys.argv[1])
    if os.path.isdir(work):
        shutil.rmtree(work)
    tree = os.path.join(work, 'tree')
    os.makedirs(os.path.join(tree, 'src'))
    os.makedirs(os.path.join(tree, 'build'))
    objs = os.path.join(work, 'obj')
    os.makedirs(objs)
    rows = []
    for name, text in (('def.cpp', DEF_CPP), ('ref.cpp', REF_CPP)):
        p = os.path.join(tree, 'src', name)
        open(p, 'w').write(text)
        o = os.path.join(objs, 'src_' + name + '.o')
        subprocess.run([qemu_ilp32.CXX, '-mabi=ilp32', '-O2', '-fPIC', '-c', p, '-o', o], check=True)
        rows.append('src/%s\t%s\tok' % (name, o))
    units = {'units': [
        {'source': 'src/def.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
            {'address': '0x00c0ffee', 'symbol': '@FUN_00c0ffee@16', 'size': 16},
            {'address': '0x00c0fff0', 'symbol': '?FUN_00c0fff0@@YIXPAUT@@PAXHH@Z', 'size': 16}], 'symbols': {}},
        {'source': 'src/ref.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
            {'address': '0x00c10000', 'symbol': '?call_both@@YAXPAUH@@@Z', 'size': 16}],
         'symbols': {'?FUN_00c0ffee@H@@QAEXHH@Z': '0x00c0ffee', '?FUN_00c0fff0@H@@QAEXHH@Z': '0x00c0fff0'}}]}
    uj = os.path.join(tree, 'build', 'units.json')
    json.dump(units, open(uj, 'w'))
    otsv = os.path.join(objs, 'objects.tsv')
    open(otsv, 'w').write('\n'.join(rows) + '\n')
    out = os.path.join(work, 'out')
    r = subprocess.run([sys.executable, LINK, 'resolve', uj, otsv, out, '--crt-symbols', 'none', '--imports', 'none',
                        '--units-renames', 'none'], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-2000:])
        return 2
    got = {}
    for line in open(os.path.join(out, 'refs.tsv')):
        c = line.rstrip('\n').split('\t')
        if len(c) > 5 and c[0] == 'src/ref.cpp' and 'FUN_00c0f' in c[1]:
            got[c[4]] = (c[2], c[5])
    print('control  (C++ names): 0x00c0fff0 ->', got.get('00c0fff0'))
    print('C-named definition  : 0x00c0ffee ->', got.get('00c0ffee'))
    ctl_ok = got.get('00c0fff0', ('',))[0] == 'code-bridge'
    defect = got.get('00c0ffee', ('',))[0] == 'code'
    # what the direct binding delivers (same declaration shapes, run under qemu ILP32)
    dem = bh.undname(['?FUN_00c0ffee@H@@QAEXHH@Z', '?FUN_00c0ffee@@YIXPAUT@@PAXHH@Z'])
    st, notes = bh.run_case('?FUN_00c0ffee@H@@QAEXHH@Z', '?FUN_00c0ffee@@YIXPAUT@@PAXHH@Z', {}, {}, None,
                            os.path.join(work, 'direct'), seed=5, dem=dem)
    print('direct binding under qemu vs x86:', st, notes)
    if not ctl_ok:
        print('control did not get a bridge: harness or tool changed')
        return 2
    if defect:
        print('DEFECT: the C-named definition is bound without an ABI comparison (no bridge, not reported)')
        return 1
    print('fixed: the C-named pair is compared')
    return 0


if __name__ == '__main__':
    sys.exit(main())
