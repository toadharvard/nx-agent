#!/usr/bin/env python3
"""Test of toolchain/dispatch/fnvnx_dispatch.cc (DISPATCH1), built from the tracked source with the argv of the recipe's plugin
stage (plugbuild.py), loaded after the sdd and names plugins as in the product flag set. Every indirect call (function pointer,
virtual call, delete-expression, tail call, calls in a loop) checks its own site and calls fnvnx_dispatch on a miss; a direct
call is untouched; the call's shape is the descriptor string; an unknown plugin argument is an error. No network, no game data.
Run: python3 -I <this file> from the checkout root."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_dispatch')
FIX = os.path.join(HERE, 'dispatch', 'dispatch.cpp')


def body(dis, sym):
    """the instructions and relocation lines of function SYM of the object (objdump -dr), tabs squeezed to one space"""
    return '\n'.join(re.sub(r'\s+', ' ', l.strip()) for l in dis.get(sym, []))


def calls(b, target='fnvnx_dispatch'):
    return len(re.findall(r'R_AARCH64_P32_(?:CALL|JUMP)26 %s\b' % re.escape(target), b))


def checks(obj):
    dis = pb.objdump(obj)
    want = {'_Z2c1PFiiEi': 1, '_Z2c2P1Ai': 1, '_Z2c3P1A': 1, '_Z2c4PFiiEi': 1, '_Z2c5PFiiES0_i': 2, '_Z2c6i': 0}
    for f, n in want.items():
        b = body(dis, f)
        check('%s: %d relocation(s) against fnvnx_dispatch' % (f, n), calls(b) == n, b)
        check('%s: no indirect call without a site check' % f,
              len(re.findall(r'[0-9a-f]+: [0-9a-f]+ (?:blr|br) x\d+', b)) == n and len(re.findall(r'[0-9a-f]+: [0-9a-f]+ cmp w\d+, w\d+', b)) >= n, b)
    c1 = body(dis, '_Z2c1PFiiEi')
    check('hit path: ONE 64-bit load of the site, compare the low half of that register, branch, shift the register',
          re.search(r'ldr x(\d+), \[x\d+\] [0-9a-f]+: R_AARCH64_P32_LDST64_ABS_LO12_NC \.bss.*? cmp w0, w\1 [0-9a-f]+: [0-9a-f]+ b\.ne .*? lsr x\d+, x\1, #32', c1.replace('\n', ' '))
          and len(re.findall(r'R_AARCH64_P32_LDST64_ABS_LO12_NC \.bss', c1)) == 1, c1)
    c6 = body(dis, '_Z2c6i')
    check('a direct call is untouched', calls(c6, '_Z6directi') == 1 and 'fnvnx_dispatch' not in c6, c6)
    n = sum(calls(body(dis, f)) for f in dis)
    sz = pb.section_size(obj, '.bss')
    check('one 8-byte zero site per indirect call', sz == 8 * n and n == 11, '.bss %s bytes, %d dispatch relocations' % (sz, n))

    def desc(f):
        m = re.search(r'R_AARCH64_P32_ADR_PREL_PG_HI21 (\.rodata\.str1\.8)(?:\+0x([0-9a-f]+))?', body(dis, f))
        return m and pb.cstring(obj, m.group(1), int(m.group(2) or '0', 16))
    shapes = {'_Z2c1PFiiEi': 'stack:i:i', '_Z2c2P1Ai': 'this:i:i,i', '_Z2c3P1A': 'this:i:i,i',
              '_Z2d1PFviifE': 'fast:v:i,i,f', '_Z2d2PFxdxE': 'stack:q:d,q',
              '_Z2d3PF2V3S_2P23BigcES_S0_S1_': 'stack:a12:s12h,s8g,s32r,i', '_Z2d4PF3Big2NTERS0_': 'stack:m32:s4r',
              '_Z2d5P1BMS_FifdE': 'this:i:i,f,d'}
    for f, want_d in shapes.items():
        check('%s: descriptor %s' % (f, want_d), desc(f) == want_d, repr(desc(f)))


HOSTFIX = os.path.join(HERE, 'dispatch', 'host.cpp')
PRE = open(HOSTFIX).read().split('unsigned long long h1')[0]
# each error form of the containment rule: (id, the function that uses a host slot value in that form, the exact error)
FORMS = [
    ('store of the value', 'unsigned long long g;\nvoid f() { g = host_api->open; }', 'a host slot value is stored'),
    ('argument', 'void sink(unsigned long long);\nvoid f() { sink(host_api->open); }', 'a host slot value is passed as an argument'),
    ('return', 'unsigned long long f() { return host_api->open; }', 'a host slot value is returned'),
    ('comparison', 'int f(unsigned long long a) { return host_api->open == a; }', 'a host slot value is compared or switched on'),
    ('switch', 'int f() { switch (host_api->open) { case 1: return 5; case 7: return 9; default: return 0; } }', 'a host slot value is compared or switched on'),
    ('arithmetic', 'unsigned long long f(unsigned long long a) { return host_api->open + a; }',
     'a host slot value is used in arithmetic or a selection'),
    ('PHI', 'unsigned long long f(int c, unsigned long long a) { return ((hfn1)(unsigned long)(c ? host_api->open : host_api->close))(a); }',
     'a host slot value is merged by a PHI'),
    ('asm operand', 'void f() { asm volatile("" :: "r"(host_api->open)); }', 'a host slot value is an operand of an asm'),
    ('store into a slot', 'void f(Api *p, unsigned long long v) { p->open = v; }', 'a host slot is stored into'),
    ('address of a slot', 'hslot *f(Api *p) { return &p->open; }', 'the address of a host slot is taken'),
    ('copy of the aggregate', 'void f(Api *p, const Api *q) { *p = *q; }',
     'an aggregate with host slots is copied, loaded, stored, passed or returned by value'),
    ('the value also an argument of its own call', 'unsigned long long f() { return ((hfn1)(unsigned long)(host_api->open))((unsigned long long)host_api->open); }',
     'a host slot value is passed as an argument'),
    ('copy through __builtin_memcpy', 'void f(Api *p, const Api *q) { __builtin_memcpy(p, q, sizeof *p); }',
     'an aggregate with host slots is copied, loaded, stored, passed or returned by value'),
]


def host(td, so):
    obj = os.path.join(td, 'host.o')
    r = pb.compile(HOSTFIX, obj, so)
    check('host.cpp compiles', r.returncode == 0, r.stderr[:2000])
    if r.returncode == 0:
        dis = pb.objdump(obj)
        fns = {'_Z2h1y': 0, '_Z2h2yy': 0, '_Z2h3iy': 0, '_Z2h4y': 0, '_Z3ordPFyyEy': 1, '_Z5plainPK3Api': 0}
        for f, n in fns.items():
            b = body(dis, f)
            check('%s: %d call(s) of fnvnx_dispatch' % (f, n), calls(b) == n, b)
        check('a host call is a plain indirect call (blr), with no site', all(re.search(r'\b(?:blr|br) x\d+', body(dis, f)) for f in ('_Z2h1y', '_Z2h2yy', '_Z2h3iy', '_Z2h4y')), body(dis, '_Z2h1y'))
        check('the pointer call of ord is rewritten and its host call is not: one site, two indirect calls',
              len(re.findall(r'\b(?:blr|br) x\d+', body(dis, '_Z3ordPFyyEy'))) == 2 and pb.section_size(obj, '.bss') == 8, body(dis, '_Z3ordPFyyEy'))
        rec = {l.split('\t')[0]: tuple(int(x) for x in l.split('\t')[1:]) for l in pb.section(obj, '.fnvnx.dispatch').splitlines()}
        want = {'_Z2h1y': (0, 1), '_Z2h2yy': (0, 2), '_Z2h3iy': (0, 1), '_Z2h4y': (0, 1), '_Z3ordPFyyEy': (1, 1)}
        check('.fnvnx.dispatch: per function the rewritten sites and the host-ABI calls', rec == want, rec)
    for name, fn, err in FORMS:
        src = os.path.join(td, 'form.cpp')
        open(src, 'w').write(PRE + fn + '\n')
        r = pb.compile(src, os.path.join(td, 'form.o'), so)
        check('containment: %s is the error "HOST1: %s"' % (name, err), r.returncode != 0 and 'HOST1: ' + err in r.stderr, r.stderr[:800])
    # HOST1: the macro is defined by the plugin and by nothing else; a slot whose type is named through another typedef is still a host slot
    src = os.path.join(td, 'mac.cpp')
    open(src, 'w').write('#ifndef __FNVNX_HOST_ATTR__\n#error "no host attribute macro"\n#endif\nint mac_ok(void) { return __FNVNX_HOST_ATTR__; }\n')
    r = pb.compile(src, os.path.join(td, 'mac.o'), so)
    check('__FNVNX_HOST_ATTR__ is defined by the semantic flags of a guest root (guest_compiler.json: plugins load in the CC stage, after preprocessing)', r.returncode == 0, r.stderr[:500])
    src = os.path.join(td, 'alias.cpp')
    open(src, 'w').write(PRE.replace('hslot open;', 'hslot2 open;').replace('typedef unsigned long long (*hfn1)',
                         'typedef hslot hslot2;\ntypedef unsigned long long (*hfn1)') + 'unsigned long long a1(unsigned long long a) { return HCALL1(open, a); }\n')
    r = pb.compile(src, os.path.join(td, 'alias.o'), so)
    dis = pb.objdump(os.path.join(td, 'alias.o')) if r.returncode == 0 else ''
    check('a slot typed through a second typedef is a host slot (no dispatch, plain blr)',
          r.returncode == 0 and calls(body(dis, '_Z2a1y')) == 0 and re.search(r'\b(?:blr|br) x\d+', body(dis, '_Z2a1y')), r.stderr[:500] or body(dis, '_Z2a1y'))
    src = os.path.join(td, 'bad.cpp')
    open(src, 'w').write('typedef int __attribute__((fnvnx_host)) bad;\n')
    r = pb.compile(src, os.path.join(td, 'bad.o'), so)
    check('fnvnx_host applies to a 64-bit unsigned type only', r.returncode != 0 and 'applies to a 64-bit unsigned integer type' in r.stderr, r.stderr[:500])


def main():
    td = tempfile.mkdtemp(prefix='test-dispatch-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names', 'dispatch')]
        out = os.path.join(td, 't.o')
        r = pb.compile(FIX, out, so)
        check('compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            checks(out)
        host(td, so)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
