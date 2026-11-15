#!/usr/bin/env python3
"""descclasstest - the DISPATCH1 descriptor's argument and result classes against the pinned compiler's own code (L3a, item 26).

The dispatch plugin (toolchain/dispatch/fnvnx_dispatch.cc) takes the class of every argument and result from the backend (pass_by_reference, function_arg,
function_value). This test is the reference check: for every type of the list below the real plugin compiles an indirect call, and the class it wrote into
.fnvnx.disp is compared with the registers the same compiler uses in the prologue of a callee of the same signature (the callee is compiled in the same recipe root;
every guest root loads the full plugin set, and none of the plugins chooses the registers of a plain function body)
(argument: which register file the first use reads; result: which register file the epilogue writes, x8 for a result in memory). Only the register FILE is compared: the compiler
reads or packs the pieces of an aggregate at widths of its own choosing (fmov x0, d0 for {float, float}), so the element width is the backend's answer, which bridgegen
checks for consistency with the size (an element code that does not divide the size into 1..4 parts is a build error). It verifies the rule; it selects nothing.

    test_descclass.py
Every compile is a stage of the recipe (toolchain/tests/plugbuild.py): the prepared machinery, the pinned msvcmodel compiler, no direct compiler call.
"""
import os, re, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE)); FNV = os.path.dirname(LIFT)
sys.path.insert(0, LIFT + '/tools')
sys.path.insert(0, os.path.join(FNV, 'toolchain', 'tests'))
import bridgegen
import plugbuild as pb

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
    ('ct', 'struct CT { CT() : a(0), b(0) {} int a, b; };', 'CT'),
    ('in', 'struct B0 { int a; }; struct IN : B0 { int b; };', 'IN'),
    ('vf', 'struct VF { virtual void f(); int a; };', 'VF'),
    ('mf', 'struct MF { int a, b; int f() { return a; } };', 'MF'),
    ('mix', 'struct MX { float a; int b; };', 'MX'),
    ('i16', 'struct I16 { int a, b, c, d; };', 'I16'),
    ('i20', 'struct I20 { int a, b, c, d, e; };', 'I20'),
]
CC = '__attribute__((fnvnx_cc("stack")))'


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


def asm_of(obj):
    """the object's functions as `name:` + one `mnemonic operands` line per instruction + `.size` (the form ref_arg / ref_ret / func read): the recipe-built
    object read back by the pinned objdump (reading, not compiling)"""
    out = []
    for sym, lines in pb.objdump(obj).items():
        out.append(sym + ':')
        for ln in lines:
            m = re.match(r'\s*[0-9a-f]+:\t[0-9a-f]{8}\s+\t?(.*)$', ln)
            if m: out.append('\t' + re.sub(r'\t+', ' ', m.group(1)))
        out.append('\t.size')
    return '\n'.join(out) + '\n'


def func(asm, fn):
    out, on = [], False
    for ln in asm.split('\n'):
        if re.match(r'^%s:' % re.escape(fn), ln): on = True; continue
        if on and re.match(r'\s*\.size', ln): break
        if on: out.append(ln)
    return out


def main():
    w = tempfile.mkdtemp(prefix='descclass-')
    try:
        return run_all(w)
    finally:
        shutil.rmtree(w, ignore_errors=True)


def run_all(w):
    plug = [pb.build(n, w) for n in ('sdd', 'names', 'dispatch')]
    decls = '\n'.join(t[1] for t in TYPES)
    call = ''.join('void c_a_%s(unsigned long p, %s x) { ((void (%s *)(%s))p)(x); }\n%s c_r_%s(unsigned long p) { return ((%s (%s *)(void))p)(); }\n'
                   % (n, T, CC, T, T, n, T, CC) for n, d, T in TYPES)
    ref = ''.join('%s g_%s;\nextern "C" void __attribute__((noinline)) a_%s(%s x) { g_%s = x; }\nextern "C" %s __attribute__((noinline)) r_%s(void) { return g_%s; }\n' % (T, n, n, T, n, T, n, n) for n, d, T in TYPES)
    open(w + '/ref.cc', 'w').write(decls + '\n' + ref)
    r = pb.compile(w + '/ref.cc', w + '/ref.o', plug)
    if r.returncode: print('reference callees do not compile: ' + r.stderr[:800]); return 1
    asm = asm_of(w + '/ref.o')
    bad = 0
    def check(name, ok, info=''):
        nonlocal bad
        print('%-44s %s %s' % (name, 'ok' if ok else 'FAIL', info)); bad += not ok
    # per-function check: compile each call alone so its descriptor is the unit's only one
    for n, d, T in TYPES:
        for kind in ('a', 'r'):
            src = '#define CC\n' + decls + '\n' + [c for c in call.split('\n') if c.startswith(('void c_a_%s(' % n, '%s c_r_%s(' % (T, n)))][0 if kind == 'a' else 1] + '\n'
            open(w + '/one.cc', 'w').write(src)
            r = pb.compile(w + '/one.cc', w + '/one.o', plug)
            if r.returncode: check('%s %s: the plugin compiles' % (kind, n), False, r.stderr[:200]); continue
            ds = bridgegen.read_obj(w + '/one.o')
            d1 = ds[0] if len(ds) == 1 else None
            want = ref_arg(asm, 'a_' + n) if kind == 'a' else ref_ret(asm, 'r_' + n)
            if d1 is None: check('%s %s: one descriptor' % (kind, n), False, str(ds)); continue
            conv, rt, args = d1.split(':')
            got = args if kind == 'a' else rt
            rm = re.fullmatch(r'a([0-9]+)([RH])(g|m|h[a-z0-9]+)', rt) if kind == 'r' else None
            if kind == 'r': got = rm.group(3) if rm else rt
            h = re.search(r'(h(?:f|d|q|h|v8|v16))$', got)
            if got[:1] == 'm': cls = ('m',)
            elif h: cls = ('h',)
            else: cls = ('g',)
            ok = cls == want or (cls == ('m',) and want == ('g',) and kind == 'r') or (got.endswith('r') and kind == 'a' and want == ('g',))
            # MSVC: a type that is not a POD is not returned in registers (documents cited in the plugin): x on the result, never on an argument
            nonpod = n in ('ct', 'in', 'vf')
            if kind == 'r' and rm: ok = ok and (rm.group(2) == 'R') == (not nonpod and int(rm.group(1)) in (1, 2, 4, 8))   # X: the x86 side, from the POD rule and the size
            check('%s %s' % ('argument' if kind == 'a' else 'result', n), ok, 'descriptor %s, the compiler uses %s' % (d1, want))
    print('descclasstest: %d failed' % bad); return 1 if bad else 0


if __name__ == '__main__': sys.exit(main())
