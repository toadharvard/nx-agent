#!/usr/bin/env python3
"""descclasstest - the DISPATCH1 descriptor's argument and result classes against the pinned compiler's own code (L3a, item 26).

The dispatch plugin (toolchain/dispatch/fnvnx_dispatch.cc) takes the class of every argument and result from the backend (pass_by_reference, function_arg,
function_value). This test is the reference check: for every type of the list below the real plugin compiles an indirect call, and the class it wrote into
.fnvnx.disp is compared with the registers the same compiler uses in the prologue of a callee of the same signature, compiled WITHOUT the plugin
(argument: which register file the first use reads; result: which register file the epilogue writes, x8 for a result in memory). Only the register FILE is compared: the compiler
reads or packs the pieces of an aggregate at widths of its own choosing (fmov x0, d0 for {float, float}), so the element width is the backend's answer, which bridgegen
checks for consistency with the size (an element code that does not divide the size into 1..4 parts is a build error). It verifies the rule; it selects nothing.

    descclasstest.py [--cc DIR] [--work DIR]
"""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE); FNV = os.path.dirname(LIFT)
sys.path.insert(0, LIFT + '/tools')
import bridgegen

# name -> declaration text; every type is used as an argument and as a result
TYPES = [
    ('u_f2', 'union U1 { float a[2]; };', 'U1'),
    ('u_f1', 'union U2 { float x, y; };', 'U2'),
    ('u_fd', 'union U3 { float x; double y; };', 'U3'),
    ('v4', 'typedef float v16f __attribute__((vector_size(16))); struct HV { v16f v; };', 'HV'),
    ('v8', 'typedef float v8f __attribute__((vector_size(8))); struct HW { v8f v; v8f w; };', 'HW'),
    ('d4', 'struct D4 { double a, b, c, d; };', 'D4'),
    ('d5', 'struct D5 { double a, b, c, d, e; };', 'D5'),
    ('f4', 'struct F4 { float a, b, c, d; };', 'F4'),
    ('f2', 'struct F2 { float a, b; };', 'F2'),
    ('al', 'struct AL { alignas(16) float a; float b; };', 'AL'),
    ('bf', 'struct BF { float a; int : 0; float b; };', 'BF'),
    ('ld', 'struct LD { long double a; };', 'LD'),
    ('h2', 'struct H2 { _Float16 a, b; };', 'H2'),
    ('mix', 'struct MX { float a; int b; };', 'MX'),
    ('i16', 'struct I16 { int a, b, c, d; };', 'I16'),
    ('i20', 'struct I20 { int a, b, c, d, e; };', 'I20'),
]
CC = '__attribute__((fnvnx_cc("stack")))'


def run(cmd, **k):
    r = subprocess.run(cmd, capture_output=True, text=True, **k)
    if r.returncode: sys.exit('%s: %s' % (' '.join(cmd[:3]), r.stderr[:800]))
    return r.stdout


WIDTH = {'b': 1, 'h': 2, 's': 4, 'd': 8, 'q': 16}
VTOK = re.compile(r'\b(?:([sdqh])(\d+)|v(\d+)\.(?:\d*)([bhsd]))\b')


def vregs(ln):
    """[(register number, element width)] of the vector-file operands of an instruction line"""
    return [(int(m.group(2) or m.group(3)), WIDTH[m.group(1) or m.group(4)]) for m in VTOK.finditer(ln)]


def ref_arg(asm, fn):
    """what the callee's body reads first of the registers an argument arrives in: ('h', element width) | ('g',).  Writes of x0/w0 (adrp, ldr, mov ...) come before
    the argument is read in some bodies, so operand 0 of a non-store instruction is a write, not a use."""
    killed = False
    for ln in func(asm, fn):
        ln = ln.split('//')[0]
        m = re.match(r'\s*([a-z0-9.]+)\s+(.*)', ln)
        if not m or m.group(1).startswith('.'): continue
        op, rest = m.group(1), m.group(2)
        store = op.startswith(('st', 'cmp', 'tst'))
        v = [x for x in vregs(rest) if x[0] == 0]
        if v and (store or ',' in rest and vregs(rest)[0][0] != 0 or any(x[0] == 0 for x in vregs(rest.split(',', 1)[-1]))): return ('h',)
        ops = rest.split(',')
        srcs = ','.join(ops if store else ops[1:])
        if not killed and re.search(r'\b[xw]0\b', srcs): return ('g',)
        if not store and re.match(r'[xw]0\b', ops[0].strip()): killed = True
    return None


def ref_ret(asm, fn):
    """what the callee's body writes of the result registers: ('m',) with x8 used as a base, ('h', element width) for a vector-file register 0 written, else ('g',)"""
    body = func(asm, fn)
    if any(re.search(r'\[x8', ln) for ln in body): return ('m',)
    for ln in body:
        ln = ln.split('//')[0]
        m = re.match(r'\s*([a-z0-9.]+)\s+([^,]+)', ln)
        if m and not m.group(1).startswith('.') and not m.group(1).startswith(('st', 'ret')):
            v = vregs(m.group(2))
            if v and v[0][0] == 0: return ('h',)
    return ('g',) if any(re.match(r'\s*(ldr|ldp|ldur|mov|lsr|and)\s+[xw][01]\b', ln) for ln in body) else None


def func(asm, fn):
    out, on = [], False
    for ln in asm.split('\n'):
        if re.match(r'^%s:' % re.escape(fn), ln): on = True; continue
        if on and re.match(r'\s*\.size', ln): break
        if on: out.append(ln)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cc', default=''); ap.add_argument('--work', default='/tmp/descclass'); a = ap.parse_args()
    w = a.work; os.makedirs(w, exist_ok=True)
    cc = lambda t: (a.cc + '/' if a.cc else '') + 'aarch64-linux-gnu-' + t
    pdir = run([cc('g++'), '-print-file-name=plugin']).strip() + '/include'
    plug = []
    for n, src in (('fnvnx_names', 'names/fnvnx_names.cc'), ('fnvnx_dispatch', 'dispatch/fnvnx_dispatch.cc')):
        so = '%s/%s.so' % (w, n)
        run(['x86_64-linux-gnu-g++-13', '-shared', '-fPIC', '-fno-rtti', '-fno-exceptions', '-O2', '-w', '-I' + pdir, '%s/toolchain/%s' % (FNV, src), '-o', so])
        plug.append('-fplugin=' + so)
    decls = '\n'.join(t[1] for t in TYPES)
    call = ''.join('void c_a_%s(unsigned long p, %s x) { ((void (%s *)(%s))p)(x); }\n%s c_r_%s(unsigned long p) { return ((%s (%s *)(void))p)(); }\n'
                   % (n, T, CC, T, T, n, T, CC) for n, d, T in TYPES)
    ref = ''.join('%s g_%s;\nextern "C" void __attribute__((noinline)) a_%s(%s x) { g_%s = x; }\nextern "C" %s __attribute__((noinline)) r_%s(void) { return g_%s; }\n' % (T, n, n, T, n, T, n, n) for n, d, T in TYPES)
    F = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-ffreestanding', '-fno-exceptions', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-Wno-attributes']
    open(w + '/ref.cc', 'w').write(decls + '\n' + ref)
    run([cc('g++')] + F + ['-w', '-S', w + '/ref.cc', '-o', w + '/ref.s'])
    asm = open(w + '/ref.s').read()
    bad = 0
    def check(name, ok, info=''):
        nonlocal bad
        print('%-44s %s %s' % (name, 'ok' if ok else 'FAIL', info)); bad += not ok
    # per-function check: compile each call alone so its descriptor is the unit's only one
    for n, d, T in TYPES:
        for kind in ('a', 'r'):
            src = '#define CC\n' + decls + '\n' + [c for c in call.split('\n') if c.startswith(('void c_a_%s(' % n, '%s c_r_%s(' % (T, n)))][0 if kind == 'a' else 1] + '\n'
            open(w + '/one.cc', 'w').write(src)
            r = subprocess.run([cc('g++')] + F + plug + ['-c', w + '/one.cc', '-o', w + '/one.o'], capture_output=True, text=True)
            if r.returncode: check('%s %s: the plugin compiles' % (kind, n), False, r.stderr[:200]); continue
            ds = bridgegen.read_obj(w + '/one.o')
            d1 = ds[0] if len(ds) == 1 else None
            want = ref_arg(asm, 'a_' + n) if kind == 'a' else ref_ret(asm, 'r_' + n)
            if d1 is None: check('%s %s: one descriptor' % (kind, n), False, str(ds)); continue
            conv, rt, args = d1.split(':')
            got = args if kind == 'a' else rt
            h = re.search(r'(h(?:f|d|q|h|v8|v16))$', got)
            if got[:1] == 'm': cls = ('m',)
            elif h: cls = ('h',)
            else: cls = ('g',)
            ok = cls == want or (cls == ('m',) and want == ('g',) and kind == 'r') or (got.endswith('r') and kind == 'a' and want == ('g',))
            check('%s %s' % ('argument' if kind == 'a' else 'result', n), ok, 'descriptor %s, the compiler uses %s' % (d1, want))
    print('descclasstest: %d failed' % bad); sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
