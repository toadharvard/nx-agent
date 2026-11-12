#!/usr/bin/env python3
"""defdisptest - .fnvnx.defdisp: one row `<asm name> <descriptor>` per function symbol the object defines (toolchain/dispatch, the same shape() as the call sites).
(1) a C++ unit at the product's -O2 with a method, a virtual method, a stdcall-style and a fastcall free function, a constructor with its C1/C2 clones
    (virtual base), a C1/C2 and D1/D2 alias pair (no virtual base), a this-adjusting thunk (_ZThn) and a covariant-return thunk (_ZTc): the rows are
    EXACTLY the defined function symbols of the object (nm, T/t/W/w), the difference empty in both directions, each with the descriptor its own type gives;
(2) the section is ASCII, sorted, unique, ends in LF (L1's descs parser reads the same form);
(3) for every aggregate type of descclasstest.TYPES the definitions a_<n> (argument) and r_<n> (result) get a descriptor whose register class is the one the
    callee's own compiled body uses (descclasstest.ref_arg / ref_ret: the compiler as reference);
(4) the hidden-pointer rule on results (msvc_pod): every case of podmatrix.py (9 features x depth 0/1/array x sizes 1,2,4,8,12) against the reference table
    cl_pod_table.tsv (the /FAs output of Microsoft cl 15.00.30729.01, Visual C++ 2008 SP1, toolchain/msvc9 of fnv-nx-build, commit in the table header; the rule itself
    is stated from the MSVC documentation and the VC 2008 ABI in fnvnx_dispatch.cc, the table verifies it and defines nothing); exit 3 (BLOCKED) while the table is absent.
Run: python3 -B lift/test/defdisptest.py [--cc <dir of aarch64-linux-gnu-g++>] [--work dir]   exit 0 on pass."""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import descclasstest as dc
import bridgegen
import podmatrix

UNIT = r'''
struct VB { int v; };
struct C : virtual VB { C(int x); int a; int m(int y); virtual int vf(); };
C::C(int x) : a(x) { v = x; }
int C::m(int y) { return a + y; }
int C::vf() { return a; }
struct F2 { float a, b; };
struct NV { NV(int x); ~NV(); int a; };                          // no virtual base: C1 is an alias of C2, D1 of D2 at -O2
NV::NV(int x) : a(x) {}
NV::~NV() {}
struct A1 { virtual int f(); int a; };  struct A2 { virtual int g(); int b; };
struct MI : A1, A2 { int f(); int g(); };                        // g is reached through a this-adjusting thunk (_ZThn)
int MI::f() { return 1; }
int MI::g() { return 2; }
struct CB { virtual CB *cl(); };  struct CD : A1, CB { CD *cl(); };   // covariant return with a base at an offset: a _ZTc thunk
CB *CB::cl() { return this; }
CD *CD::cl() { return this; }
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
    '_ZN2NVC1Ei': 'this:v:i,i', '_ZN2NVC2Ei': 'this:v:i,i', '_ZN2NVD1Ev': 'this:v:i', '_ZN2NVD2Ev': 'this:v:i',   # alias pairs
    '_ZThn8_N2MI1gEv': 'this:i:i',
}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cc', default=''); ap.add_argument('--work', default='/tmp/defdisp'); a = ap.parse_args()
    w = a.work; os.makedirs(w, exist_ok=True)
    cc = lambda t: (a.cc + '/' if a.cc else '') + 'aarch64-linux-gnu-' + t
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
        out = '%s/%s.defdisp' % (w, stem)
        r = subprocess.run(['aarch64-linux-gnu-objcopy', '--dump-section', '.fnvnx.defdisp=' + out, '%s/%s.o' % (w, stem), '/dev/null'], capture_output=True, text=True)
        return open(out, 'rb').read() if r.returncode == 0 else None
    def rows(data):
        return dict(l.split(' ', 1) for l in data.decode('ascii').split('\n')[:-1])
    # (1) + (2)
    data = build(UNIT, 'unit')
    check('section present', data is not None)
    if data is not None:
        txt = data.decode('ascii')
        lines = txt.split('\n')
        check('final LF', txt.endswith('\n') and lines[-1] == '')
        lines = lines[:-1]
        check('sorted and unique', lines == sorted(set(lines)))
        got = rows(data)
        nm = dc.run(['aarch64-linux-gnu-nm', '--defined-only', '%s/unit.o' % w])
        defined = {l.split()[2] for l in nm.split('\n') if len(l.split()) == 3 and l.split()[1] in ('T', 't', 'W', 'w')}
        check('defined function symbols == rows', defined == set(got), 'only in the object %s, only in the rows %s' % (sorted(defined - set(got)), sorted(set(got) - defined)))
        check('a covariant thunk is a defined symbol with a row', any(k.startswith('_ZTch') or k.startswith('_ZTc') for k in got), str(sorted(got)))
        for k, v in WANT.items(): check('descriptor of ' + k, got.get(k) == v, 'got %s, want %s' % (got.get(k), v))
    # (4) the hidden-pointer rule on results, every row of the reference table (podmatrix.py; cl_pod_table.tsv = the cl output, a committed fact)
    tp = os.path.join(HERE, 'cl_pod_table.tsv')
    if not os.path.exists(tp):
        print('defdisptest: BLOCKED: cl_pod_table.tsv is absent (the cl reference run of podmatrix.py --cl)'); sys.exit(3)
    table = dict(l.split('\t') for l in open(tp).read().split('\n') if l and not l.startswith('#'))
    cs = [c[0] for c in podmatrix.cases()]
    check('table rows == matrix cases', set(table) == set(cs), 'only in the table %s, only in the matrix %s' % (sorted(set(table) - set(cs))[:5], sorted(set(cs) - set(table))[:5]))
    src = podmatrix.decls() + ''.join('extern %s g_%s;\nextern "C" %s r_%s() { return g_%s; }\n' % (n, n, n, n, n) for n in cs)
    got = rows(build(src, 'pod') or b'')
    for n in cs:
        d1 = got.get('r_' + n)
        if d1 is None or n not in table: check('result %s' % n, False, 'row %s, table %s' % (d1, table.get(n))); continue
        rt = d1.split(':')[1]
        size = int(re.match(r'[am](\d+)', rt).group(1))
        hidden = rt[0] == 'm' or rt.endswith('x') or size not in (1, 2, 4, 8)   # sizes that are no x86 register size: bridgegen's rule
        check('result %s: cl %s' % (n, table[n]), hidden == (table[n] == 'hidden'), 'plugin row %s' % d1)
    # (3)
    decls = '\n'.join(t[1] for t in dc.TYPES)
    ref = ''.join('%s g_%s;\nextern "C" void __attribute__((noinline)) a_%s(%s x) { g_%s = x; }\nextern "C" %s __attribute__((noinline)) r_%s(void) { return g_%s; }\n' % (T, n, n, T, n, T, n, n) for n, d, T in dc.TYPES)
    data = build(decls + '\n' + ref, 'ref')
    got = rows(data) if data is not None else {}
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
