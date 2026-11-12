#!/usr/bin/env python3
"""fnvnx_order rules on the game's compiler (msvcmodel, all 8 plugins, the one build via realobj.build): ORDER-CONT (resumption-point fences, no error
for a block-ending statement without fall-through edge), HARDREG (a hard-register object is no memory), LOCAL (a non-escaped decl is not ordered, a
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
struct NF { void *jb[5]; };
struct C { NF *nf; int v; };
void lj(C *c) { if (c->nf) __builtin_longjmp((void **)c->nf->jb, 1); }
void thrower();
int sj(void **b, int *p) { if (__builtin_setjmp(b)) return *p; thrower(); return 0; }
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

    def fence_before_ldar(ls):
        f = [i for i, x in enumerate(ls) if x.startswith('dmb') and 'ishld' in x]
        l = [i for i, x in enumerate(ls) if x.startswith('ldar')]
        return bool(f and l and f[0] < max(l))

    ops = lambda ls: [x.split()[0] for x in ls]  # noqa: E731
    # ORDER-CONT
    lj = lines('cont', '2ljP1C')
    br = [i for i, x in enumerate(lj) if x.split()[0] == 'br']
    check('(i) noreturn longjmp after a load: compiles (no error); the noreturn path ends in br, nothing after it but padding',
          bool(br) and all(x.split()[0] in ('nop', 'br') for x in lj[br[-1]:]), lj)
    s2 = lines('cont', '2sjPPvPi')
    check('(ii) __builtin_setjmp: the receiver begins with dmb ishld, before the load', fence_before_ldar(s2), s2)
    check('(ii) no two adjacent identical dmb ishld (FENCE-MERGE)', not any(a == b and a.startswith('dmb') and 'ishld' in a for a, b in zip(s2, s2[1:])), s2)
    tc = lines('cont', '2tcPi')
    check('(iii) catch landing pad begins with dmb ishld, before the load', fence_before_ldar(tc), tc)
    s3 = lines('cont', '3sj2P')
    i3 = [i for i, x in enumerate(s3) if 'setjmp' in x]
    check('(iv) setjmp(): dmb ishld right after the call', bool(i3) and any(x.startswith('dmb') and 'ishld' in x for x in s3[i3[0] + 1:i3[0] + 3]), s3)
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


if __name__ == '__main__':
    main()
    print('FAILED: %s' % FAILED if FAILED else 'all passed')
    sys.exit(1 if FAILED else 0)
