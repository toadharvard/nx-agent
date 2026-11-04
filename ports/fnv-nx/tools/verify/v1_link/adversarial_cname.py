#!/usr/bin/env python3
"""adversarial_cname.py - V1-link round 3: break J-0081's C-name comparison (fix 3: parameter types of `@f@N`,
`_f@N`, `_f` read from the declaring unit's source) and the cast rule, through the real `fnvlink.py resolve` on a
hand-made tree (v1tree), then run whatever it bound under qemu ILP32 against the x86 placement (bridge_harness).

    python3 adversarial_cname.py <work dir>      # exit 1 when a case is bound in a way x86 does not deliver

Cases (want: 'ok' = the binding must deliver what x86 delivers; 'flag' = must be listed x86-inconsistent/manual):
  c1  thiscall ref -> extern "C" __fastcall def with an edx dummy (the J-0074 reproducer)               ok
  c2  def's parameter list has a function-pointer parameter (nested parentheses), float result          ok
  c3  a comment with another signature precedes the definition (Ghidra-style note), int result         ok
  c4  cdecl C name declared only in a header: the unit's text holds `return FUN(v);` calls only        ok
  c5  stdcall: reference passes 12 bytes, the C-named definition pops 8 (x86 stack imbalance)           flag
  c6  C-named reference _f@12 against a C++ stdcall definition of 8 bytes                                flag
  c7  fastcall C name with a double parameter (@12: ecx int, 8 stack bytes)                              ok
  c8  stdcall C name whose result type is a typedef (float)                                              ok
  c9  extern "C" { } block, pointer result, const char * / unsigned parameters                          ok
  k1  a fastcall declaration called only through a cast to __thiscall (the cast decides)                 ok
  k2  the same name called directly and through a cast                                                   flag
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v1tree  # noqa: E402

CC = '#define __stdcall\n#define __fastcall\n#define __thiscall\n#define __cdecl\n'

DEF_CPP = CC + r'''
struct T { int v; };
extern "C" void __fastcall FUN_00c10010(T *self, void *edx_dummy, int a, int b) { self->v = a * 7 + b; }
extern "C" float __stdcall FUN_00c10020(void (*cb)(int), int k) { if (cb) cb(k); return k * 0.5f; }
// double __stdcall FUN_00c10030(int x);   (decompiler note)
extern "C" int __stdcall FUN_00c10030(int x) { return x + 1; }
extern "C" float FUN_00c10040(float a) { return a * 2.0f; }
extern "C" int __stdcall FUN_00c10050(int a, int b) { return a - b; }
int __stdcall FUN_00c10060(int a, int b) { return a + b; }
extern "C" int __fastcall FUN_00c10070(double d, int a) { return (int)d + a; }
typedef float real;
extern "C" real __stdcall FUN_00c10080(int k) { return k * 0.25f; }
extern "C" {
char * __stdcall FUN_00c10090(const char *s, unsigned n) { return (char *)s + n; }
}
struct M { int v; void FUN_00c100a0(const char *s, int n); void FUN_00c100b0(const char *s, int n); };
void M::FUN_00c100a0(const char *s, int n) { v = s[0] + n; }
void M::FUN_00c100b0(const char *s, int n) { v = s[0] - n; }
'''

HDR = 'extern "C" float FUN_00c10040(float);\n'

REF_CPP = CC + r'''
#include "h4.h"
struct H { void FUN_00c10010(int, int); };
float FUN_00c10020(void *, int);
int FUN_00c10030(int);
extern "C" int __stdcall FUN_00c10050(int a, int b, int c);
extern "C" int __stdcall FUN_00c10060(int, int, int);
struct T7 { int FUN_00c10070(double); };
float FUN_00c10080(int);
char *FUN_00c10090(const char *, unsigned);
void __fastcall FUN_00c100a0(void *a, const char *b, int *c);
void __fastcall FUN_00c100b0(void *a, const char *b, int *c);
float g4(float v) { return FUN_00c10040(v); }
float g4b(float v) { if (v > 1) return FUN_00c10040(v); else return FUN_00c10040(-v); }
void call_all(H *h, T7 *t7, void *m) {
    h->FUN_00c10010(1, 2);
    FUN_00c10020(0, 3);
    FUN_00c10030(4);
    FUN_00c10050(1, 2, 3);
    FUN_00c10060(1, 2, 3);
    t7->FUN_00c10070(2.5);
    FUN_00c10080(5);
    FUN_00c10090("abc", 1);
    static int k;
    ((void (__thiscall *)(void *, const char *, int *)) &FUN_00c100a0)(m, "x", &k);
    ((void (__thiscall *)(void *, const char *, int *)) &FUN_00c100b0)(m, "y", &k);
    FUN_00c100b0(m, "z", &k);
}
'''

# (id, address, want, the reference's decorated name in units.json, the definition's units.json name,
#  a C++ decoration of the definition's real declaration (what the harness compiles as the callee),
#  the reference as the harness calls it (None: the reference's name))
CASES = [
    ('c1', 0x00c10010, 'ok', '?FUN_00c10010@H@@QAEXHH@Z', '@FUN_00c10010@16', '?FUN_00c10010@@YIXPAUT@@PAXHH@Z', None),
    ('c2', 0x00c10020, 'ok', '?FUN_00c10020@@YGMPAXH@Z', '_FUN_00c10020@8', '?FUN_00c10020@@YGMP6AXH@ZH@Z', None),
    ('c3', 0x00c10030, 'ok', '?FUN_00c10030@@YGHH@Z', '_FUN_00c10030@4', '?FUN_00c10030@@YGHH@Z', None),
    ('c4', 0x00c10040, 'ok', '_FUN_00c10040', '_FUN_00c10040', '?FUN_00c10040@@YAMM@Z', '?FUN_00c10040@@YAMM@Z'),
    ('c5', 0x00c10050, 'flag', '?FUN_00c10050@@YGHHHH@Z', '_FUN_00c10050@8', '?FUN_00c10050@@YGHHH@Z', None),
    ('c6', 0x00c10060, 'flag', '_FUN_00c10060@12', '?FUN_00c10060@@YGHHH@Z', '?FUN_00c10060@@YGHHH@Z',
     '?FUN_00c10060@@YGHHHH@Z'),
    ('c7', 0x00c10070, 'ok', '?FUN_00c10070@T7@@QAEHN@Z', '@FUN_00c10070@12', '?FUN_00c10070@@YIHNH@Z', None),
    ('c8', 0x00c10080, 'ok', '?FUN_00c10080@@YGMH@Z', '_FUN_00c10080@4', '?FUN_00c10080@@YGMH@Z', None),
    ('c9', 0x00c10090, 'ok', '?FUN_00c10090@@YGPADPBDI@Z', '_FUN_00c10090@8', '?FUN_00c10090@@YGPADPBDI@Z', None),
    ('k1', 0x00c100a0, 'ok', '?FUN_00c100a0@@YIXPAXPBDPAH@Z', '?FUN_00c100a0@M@@QAEXPBDH@Z',
     '?FUN_00c100a0@M@@QAEXPBDH@Z', '?FUN_00c100a0@M@@QAEXPBDPAH@Z'),
    ('k2', 0x00c100b0, 'flag', '?FUN_00c100b0@@YIXPAXPBDPAH@Z', '?FUN_00c100b0@M@@QAEXPBDH@Z',
     '?FUN_00c100b0@M@@QAEXPBDH@Z', None),
]


def main():
    work = sys.argv[1]
    t = v1tree.Tree(work)
    t.header('src/h4.h', HDR)
    t.unit('src/def.cpp', DEF_CPP, functions=[(a, d) for _, a, _, _, d, _, _ in CASES])
    t.unit('src/ref.cpp', REF_CPP, functions=[(0x00c20000, '?call_all@@YAXPAUH@@PAUT7@@PAX@Z')],
           symbols={r: a for _, a, _, r, _, _, _ in CASES})
    t.resolve()
    bad = 0
    for cid, addr, want, ref, dfn, real, as_called in CASES:
        rows = [r for r in t.ref_rows(addr) if r[0] == 'src/ref.cpp']
        arows = [r for r in t.abi_rows(addr) if r[0] == 'src/ref.cpp']
        if not rows:
            print('%-4s no reference row (resolve did not bind it)' % cid)
            bad += 1
            continue
        cls, target = rows[0][2], rows[0][5]
        verdict = (arows[0][5] + ' ' + arows[0][6]) if arows else 'same'
        st, notes = ('not run', [])
        if target:
            st, notes = t.run_bridge(target, as_called or ref, real)
        flagged = bool(arows) and arows[0][5] in ('x86-inconsistent', 'manual')
        ok = (st == 'pass') if want == 'ok' else flagged
        bad += 0 if ok else 1
        print('%-6s %-4s want %-4s %-12s %-46s run %s %s' % ('ok' if ok else 'BROKEN', cid, want, cls, verdict[:46],
                                                            st[:120], notes if st != 'pass' else ''))
    print('broken: %d' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
