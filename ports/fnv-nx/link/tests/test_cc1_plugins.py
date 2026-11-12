#!/usr/bin/env python3
"""The CC stage of a C unit (cc1) loads the same plugin binaries as the CC stage of a C++ unit (cc1plus): all 8, one .so each (RULE CC1PLUS-SYMS,
toolchain/cpweak.h). The toy guest root is built by the one build (realobj.build via verify/recipe.py: msvcmodel, all plugins, GATE_CACHE) with C
units and a C++ unit together; a plugin that cannot be loaded by cc1 (an undefined symbol of the C++ front end) makes the CC stage red, so a green
build is the load proof for every plugin in both front ends. Per plugin, on the C unit:
  order     a load of shared memory is ldar and a store is stlr            (applies its rule)
  addr      a 32-bit address sum is finished before the access             (applies its rule)
  dispatch  an indirect call goes through fnvnx_dispatch                   (applies its rule)
  pi        .fnvnx.n3 has the verdict line of every emitted function        (applies its rule)
  names     C names of the unit's external symbols in .fnvnx.sympair: _name, _name@N (stdcall), @name@N (fastcall)
  layout    .fnvnx.layout rows of C structs and unions: UP@@ (struct P), TU1@@ (union U1), fields, closure under members
  sdd       C has no vtables: no vtable map (the plugin loads and registers nothing)
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
struct P { char c; int i; short s[3]; };
struct P gp;
union U1 { int a; char b; };
union U1 gu;
struct Nest { struct P p; unsigned bits : 3; };
int use_nest(struct Nest *n) { return n->bits + n->p.i; }
int __attribute__((fnvnx_cc("std"))) std_fn(int a, struct P *p) { return a + p->i; }
int __attribute__((fnvnx_cc("fast"))) fast_fn(int a, int b, int c) { return a + b + c; }
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
    sp = tool('aarch64-linux-gnu-readelf', '-p', '.fnvnx.sympair', o)
    want = ['rd^I_rd^Ifunc^Idef', 'g^I_g^Idata^Idef', 'std_fn^I_std_fn@8^Ifunc^Idef', 'fast_fn^I@fast_fn@12^Ifunc^Idef', 'call^I_call^Ifunc^Idef']
    check('names: C names in .fnvnx.sympair: cdecl _rd, data _g, stdcall _std_fn@8, fastcall @fast_fn@12', all(w in sp for w in want), sp)
    check('names: a C unit has no vtable pairs', '.fnvnx.vtpair' not in sec or 'vtable' not in sp)
    ly = tool('aarch64-linux-gnu-readelf', '-p', '.fnvnx.layout', o)
    check('layout: struct P (char, int, short[3]) is UP@@ size 16 align 4 with its fields', 'UP@@^I16^I4^Ic:0:1,i:4:4,s:8:6^I^I^I1P' in ly, ly)
    check('layout: union U1 is TU1@@ size 4', 'TU1@@^I4^I4^Ia:0:4,b:0:1' in ly, ly)
    check('layout: struct Nest has the bit-field and the member struct (closure under members)', 'UNest@@^I' in ly and 'bits:' in ly and 'UP@@' in ly, ly)
    check('sdd: no vtable map in a C unit', '.fnvnx.vtmap' not in sec)
    k = objs['cxxu']
    ksec = tool('aarch64-linux-gnu-readelf', '-SW', k)
    check('the C++ unit of the same root still gets the names, layout and vtable-map sections (one binary per plugin, both front ends)',
          all(s in ksec for s in ('.fnvnx.sympair', '.fnvnx.layout', '.fnvnx.vtmap')), ksec)


if __name__ == '__main__':
    main()
    print('FAILED: %s' % FAILED if FAILED else 'all passed')
    sys.exit(1 if FAILED else 0)
