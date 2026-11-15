#!/usr/bin/env python3
"""fnvnx_order rules on the game's compiler (msvcmodel, all 8 plugins, the one build via realobj.build): ONE FORM (no fence anywhere: an exception
landing pad and the setjmp() library call need none, ldar orders by itself; a builtin with no library function that GCC expands at RTL and that
touches shared memory is a compile error naming it), HARDREG (a hard-register object is no memory), LOCAL (a non-escaped decl is not ordered, a
by-reference result is a pointer value, never memory). Plain script: exit 0 on pass. Run: python3 -I <this file>  (needs the pinned compiler: ARTIFACTS_URL)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import realobj

FAILED = []


def check(name, ok, detail=''):
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)[:700]) if detail and not ok else ''), flush=True)
    if not ok:
        FAILED.append(name)
    return ok


SRC = {
    'cont': r'''
#include <setjmp.h>
void thrower();
int tc(int *p) { try { thrower(); } catch (...) { return *p; } return 0; }
int sj2(jmp_buf b, int *p) { if (setjmp(b)) return *p; thrower(); return 0; }
''',
    'hard': r'''
register unsigned eax asm("x19");
struct cpu { int r[4]; };
register cpu *cc asm("x27");
int hg(int v) { eax = v; eax += 1; return eax; }
int hl(int v) { register unsigned loc asm("x25") = v; loc += 2; return loc; }
int hp(int v) { cc->r[1] = v; return cc->r[1]; }
''',
    'local': r'''
extern int gg;
int loc(int v) { int a[2]; a[0] = v; a[1] = a[0] + 1; return a[1]; }
int glob() { return gg; }
int esc_use(int *);
int esc(int v) { int a = v; esc_use(&a); return a; }
struct W { virtual void f(); int m; };
W mk(W x) { return x; }
struct Big { int a[8]; };
Big mkb(int v) { Big r; for (int i = 0; i < 8; i++) r.a[i] = v + i; return r; }
''',
    'str': r'''
#include <string>
std::string cp(const std::string &s) { return s; }
std::string ctor(const char *s) { return std::string(s); }
std::string cp2(const std::string &s) { return std::string(s); }
std::string fold(const char *s) { std::string r(s); return r; }
void sink(std::string *);
void nosink(const char *s) { std::string r(s); sink(&r); }
''',
}


def main():
    res = realobj.build(SRC)
    if not check('all fixtures build on msvcmodel with the 8 plugins', res['status'] == 'green', res.get('failure')):
        return
    objs = {os.path.splitext(o['unit'])[0]: o['object'] for o in res['objects']}

    def lines(unit, frag):
        o = objs[unit]
        syms = [n.split()[-1] for n in subprocess.run(['aarch64-linux-gnu-nm', o], capture_output=True, text=True, check=True).stdout.splitlines()]
        sym = next(n for n in syms if n.startswith('_Z') and frag in n)
        txt = subprocess.run(['aarch64-linux-gnu-objdump', '-d', o], capture_output=True, text=True, check=True).stdout
        body = txt.split('<%s>:' % sym)[1].split('\n\n')[0]
        return [x.split('\t', 2)[-1].strip() for x in body.split('\n') if '\t' in x]

    ops = lambda ls: [x.split()[0] for x in ls]  # noqa: E731
    # ONE FORM: no fence in a landing pad or after setjmp()
    tc = lines('cont', '2tcPi')
    check('(i) catch landing pad: ldar, no dmb', 'ldar' in ops(tc) and 'dmb' not in ops(tc), tc)
    s3 = lines('cont', '3sj2P')
    check('(ii) setjmp(): a plain call, no dmb', 'dmb' not in ops(s3), s3)
    # a builtin with no library function that is expanded at RTL and touches shared memory through a pointer: a compile error naming it
    bad = realobj.build({'bad': '''
struct NF { void *jb[5]; };
void lj(NF *c) { __builtin_longjmp((void **)c->jb, 1); }
'''})
    check('__builtin_longjmp on a shared buffer is a compile error naming the builtin',
          bad['status'] != 'green' and '__builtin_longjmp' in str(bad.get('failure')), bad.get('failure'))
    # HARDREG
    for frag in ('2hgi', '2hli'):
        ls = lines('hard', frag)
        check('HARDREG %s: no ldar/stlr/dmb for a register object' % frag, not any(o in ('ldar', 'stlr', 'dmb') for o in ops(ls)), ls)
    hp = lines('hard', '2hpi')
    check('HARDREG: access through a register-held pointer stays ordered (stlr + ldar)', 'stlr' in ops(hp) and 'ldar' in ops(hp), hp)
    # LOCAL
    ll = lines('local', '3loci')
    check('LOCAL: a non-escaped local array has no ldar/stlr/dmb', not any(o in ('ldar', 'stlr', 'dmb') for o in ops(ll)), ll)
    check('LOCAL: a global read is ldar', 'ldar' in ops(lines('local', '4globv')))
    esc = lines('local', '3esci')
    check('LOCAL: an address-taken local is still ordered (stlr and ldar)', 'stlr' in ops(esc) and 'ldar' in ops(esc), esc)
    mk = lines('local', '2mk1W')
    check('LOCAL: a polymorphic by-value return builds (the by-reference result is a pointer value, no ICE)', bool(mk))
    mb = lines('local', '3mkbi')
    check('LOCAL: the store into a result returned through x8 is ordered (stlr through the pointer)', 'stlr' in ops(mb), mb)
    check('a std::string copy builds', bool(lines('str', '2cp')))
    for f in ('4ctor', '3cp2', '4fold', '6nosink'):   # w3's four cases (ini.cpp:124 shape)
        check('std::string %s builds' % f, bool(lines('str', f)))


if __name__ == '__main__':
    main()
    print('FAILED: %s' % FAILED if FAILED else 'all passed')
    sys.exit(1 if FAILED else 0)
