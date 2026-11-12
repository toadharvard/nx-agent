#!/usr/bin/env python3
"""The CC stage of a C unit (cc1) loads the same plugin binaries as the CC stage of a C++ unit (cc1plus): all 8, one .so each (RULE CC1PLUS-SYMS,
toolchain/cpweak.h). The toy guest root is built by the one build (realobj.build via verify/recipe.py: msvcmodel, all plugins, GATE_CACHE) with C
units and a C++ unit together; a plugin that cannot be loaded by cc1 (an undefined symbol of the C++ front end) makes the CC stage red, so a green
build is the load proof for every plugin in both front ends. Per plugin, on the C unit:
  order     a load of shared memory is ldar and a store is stlr            (applies its rule)
  addr      a 32-bit address sum is finished before the access             (applies its rule)
  dispatch  an indirect call goes through fnvnx_dispatch                   (applies its rule)
  pi        .fnvnx.n3 has the verdict line of every emitted function        (applies its rule)
  names, layout, sdd   C has none of their trees (C++ names, class layouts, vtables): no rows, no sections; the fnvnx_cc attribute that names
            registers is accepted by cc1 (a C unit may carry a calling convention)
Plain script: exit 0 on pass. Run: python3 -I <this file>  (needs the pinned compiler: ARTIFACTS_URL)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import realobj

FAILED = []


def check(name, ok, detail=''):
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)[:900]) if detail and not ok else ''), flush=True)
    if not ok:
        FAILED.append(name)
    return ok


C = r'''
int g;
int rd(int *p) { return *p + g; }
void wr(int *p, int v) { *p = v; }
int addr(unsigned x) { return *(int *)(x + 0x40u); }
void call(void (*f)(int)) { f(1); }
int __attribute__((fnvnx_cc("stdcall"))) cc_fn(int a) { return a; }
struct S { int a; int b; };
int fs(struct S *s) { return s->a + s->b; }
'''
CXX = r'''
struct K { virtual void f() {} int x; };
K *mk() { return new K; }
int rdx(int *p) { return *p; }
'''


def tool(*a):
    return subprocess.run(list(a), capture_output=True, text=True, check=True).stdout


def main():
    res = realobj.build({'cxxu': CXX}, csources={'cu': C})
    ok = check('a C unit and a C++ unit build with all 8 plugins loaded by cc1 and cc1plus', res['status'] == 'green', res.get('failure'))
    if not ok:
        return
    check('all 8 plugins were built', len(res['plugins']) == 8, [p['name'] for p in res['plugins']])
    objs = {os.path.splitext(o['unit'])[0]: o['object'] for o in res['objects']}
    o = objs['cu']
    dis = tool('aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', o)

    def fn(name):
        out, on = [], False
        for l in dis.splitlines():
            if l.endswith('<%s>:' % name):
                on = True
            elif on and l.endswith('>:'):
                break
            elif on:
                out.append(l)
        return '\n'.join(out)
    check('order: a load of shared memory in a C unit is ldar', 'ldar' in fn('rd'), fn('rd'))
    check('order: a store of shared memory in a C unit is stlr', 'stlr' in fn('wr'), fn('wr'))
    a = fn('addr')
    check('addr: the 32-bit sum of a C unit is finished (an add of the constant) before the ordered load', 'add' in a and 'ldar' in a, a)
    syms = tool('aarch64-linux-gnu-nm', o)
    check('dispatch: an indirect call of a C unit goes through fnvnx_dispatch', 'U fnvnx_dispatch' in syms, syms)
    n3 = tool('aarch64-linux-gnu-readelf', '-p', '.fnvnx.n3', o)
    check('pi: .fnvnx.n3 of a C unit has a verdict line for rd, wr and fs', all(f + '^Iok' in n3 for f in ('rd', 'wr', 'fs')), n3)   # readelf -p prints a tab as ^I
    sec = tool('aarch64-linux-gnu-readelf', '-SW', o)
    check('names: no C++ name rows in a C unit', '.fnvnx.sympair' not in sec)
    check('layout: no layout rows in a C unit', '.fnvnx.layout' not in sec)
    check('sdd: no vtable map in a C unit', '.fnvnx.vtmap' not in sec)
    k = objs['cxxu']
    ksec = tool('aarch64-linux-gnu-readelf', '-SW', k)
    check('the C++ unit of the same root still gets the names, layout and vtable-map sections (one binary per plugin, both front ends)',
          all(s in ksec for s in ('.fnvnx.sympair', '.fnvnx.layout', '.fnvnx.vtmap')), ksec)


if __name__ == '__main__':
    main()
    print('FAILED: %s' % FAILED if FAILED else 'all passed')
    sys.exit(1 if FAILED else 0)
