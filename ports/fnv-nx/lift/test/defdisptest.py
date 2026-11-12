#!/usr/bin/env python3
"""defdisptest - .fnvnx.defdisp: one row `<asm name> <descriptor>` per emitted function definition (toolchain/dispatch, the same shape() as the call sites).
(1) a C++ unit with a method, a virtual method, a __stdcall-style free function, a __fastcall one and a constructor with its two clones (virtual base:
    C1 and C2 differ) has one row per definition, each with the descriptor its own type gives;
(2) the section is ASCII, sorted, unique per section, ends in LF (L1's descs parser reads the same form);
(3) for every aggregate type of descclasstest.TYPES the definitions a_<n> (argument) and r_<n> (result) get a descriptor whose register class is the one the
    callee's own compiled body uses (descclasstest.ref_arg / ref_ret: the compiler as reference).
Run: python3 lift/test/defdisptest.py [--cc <dir of aarch64-linux-gnu-g++>] [--work dir]   exit 0 on pass."""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import descclasstest as dc

UNIT = r'''
struct VB { int v; };
struct C : virtual VB { C(int x); int a; int m(int y); virtual int vf(); };
C::C(int x) : a(x) { v = x; }
int C::m(int y) { return a + y; }
int C::vf() { return a; }
struct F2 { float a, b; };
int __attribute__((fnvnx_cc("stack"))) sc(float x, F2 p) { return (int)x + (int)p.b; }
int __attribute__((fnvnx_cc("fast"))) fc(int x, int y) { return x + y; }
'''
WANT = {
    '_Z2fcii': 'fast:i:i,i',
    '_Z2scf2F2': 'stack:i:f,s8hf',
    '_ZN1C1mEi': 'this:i:i,i',
    '_ZN1C2vfEv': 'this:i:i',
    '_ZN1CC1Ei': 'this:v:i,i',          # complete-object constructor
    '_ZN1CC2Ei': 'this:v:i,i,i',        # base-object constructor: the clone's own type has the VTT parameter
}


def rows(obj, objcopy, out):
    r = subprocess.run([objcopy, '--dump-section', '.fnvnx.defdisp=' + out, obj, '/dev/null'], capture_output=True, text=True)
    if r.returncode: return None
    return open(out, 'rb').read()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cc', default=''); ap.add_argument('--work', default='/tmp/defdisp'); a = ap.parse_args()
    w = a.work; os.makedirs(w, exist_ok=True)
    cc = lambda t: (a.cc + '/' if a.cc else '') + 'aarch64-linux-gnu-' + t
    objcopy = 'aarch64-linux-gnu-objcopy'
    pdir = dc.run([cc('g++'), '-print-file-name=plugin']).strip() + '/include'
    plug = []
    for n, src in (('fnvnx_names', 'names/fnvnx_names.cc'), ('fnvnx_dispatch', 'dispatch/fnvnx_dispatch.cc')):
        so = '%s/%s.so' % (w, n)
        dc.run(['x86_64-linux-gnu-g++-13', '-shared', '-fPIC', '-fno-rtti', '-fno-exceptions', '-O2', '-I' + pdir, '%s/toolchain/%s' % (dc.FNV, src), '-o', so])
        plug.append('-fplugin=' + so)
    F = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-ffreestanding', '-fno-exceptions', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-Wno-attributes']
    bad = 0
    def check(name, ok, info=''):
        nonlocal bad
        print('%-44s %s %s' % (name, 'ok' if ok else 'FAIL', info)); bad += not ok
    def build(src, stem):
        open('%s/%s.cc' % (w, stem), 'w').write(src)
        dc.run([cc('g++')] + F + plug + ['-c', '%s/%s.cc' % (w, stem), '-o', '%s/%s.o' % (w, stem)])
        data = rows('%s/%s.o' % (w, stem), objcopy, '%s/%s.defdisp' % (w, stem))
        return data
    # (1) + (2)
    data = build(UNIT, 'unit')
    check('section present', data is not None)
    if data is not None:
        txt = data.decode('ascii')
        lines = txt.split('\n')
        check('final LF', txt.endswith('\n') and lines[-1] == '')
        lines = lines[:-1]
        check('sorted and unique', lines == sorted(set(lines)))
        got = dict(l.split(' ', 1) for l in lines)
        check('one row per definition', set(got) == set(WANT), str(sorted(got)))
        for k, v in WANT.items(): check('descriptor of ' + k, got.get(k) == v, 'got %s, want %s' % (got.get(k), v))
    # (3)
    decls = '\n'.join(t[1] for t in dc.TYPES)
    ref = ''.join('%s g_%s;\nextern "C" void __attribute__((noinline)) a_%s(%s x) { g_%s = x; }\nextern "C" %s __attribute__((noinline)) r_%s(void) { return g_%s; }\n' % (T, n, n, T, n, T, n, n) for n, d, T in dc.TYPES)
    src = decls + '\n' + ref
    data = build(src, 'ref')
    got = dict(l.split(' ', 1) for l in data.decode('ascii').split('\n')[:-1]) if data is not None else {}
    dc.run([cc('g++')] + F + ['-S', w + '/ref.cc', '-o', w + '/ref.s'])
    asm = open(w + '/ref.s').read()
    for n, d, T in dc.TYPES:
        for kind, fn in (('argument', 'a_' + n), ('result', 'r_' + n)):
            d1 = got.get(fn)
            if d1 is None: check('%s %s: a row' % (kind, n), False, str(sorted(got))); continue
            conv, rt, args = d1.split(':')
            g = args if kind == 'argument' else rt
            h = re.search(r'(h(?:f|d|q|h|v8|v16))$', g)
            cls = ('m',) if g[:1] == 'm' else ('h',) if h else ('g',)
            want = dc.ref_arg(asm, fn) if kind == 'argument' else dc.ref_ret(asm, fn)
            ok = cls == want or (cls == ('m',) and want == ('g',) and kind == 'result') or (g.endswith('r') and kind == 'argument' and want == ('g',))
            if kind == 'result' and g[:1] == 'a': ok = ok and g.endswith('x') == (n in ('ct', 'in', 'vf'))
            check('%s %s' % (kind, n), ok, 'row %s %s, the compiler uses %s' % (fn, d1, want))
    print('defdisptest: %d failed' % bad); sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
