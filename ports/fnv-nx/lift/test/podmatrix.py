#!/usr/bin/env python3
"""podmatrix - the class-returning matrix of the x86 hidden-pointer rule (DISPATCH1, msvc_pod).
decls() is the C++ text of every case: one feature at a time (FEATURES, plus `none`), at depth 0 (the class itself, C_), depth 1 (a member of that
class, D_), depth 1 through an array (A_) and depth 2 (a member whose class has the member, E_); controls `sm` (static data member) and `mf`
(non-virtual member function) must stay POD; `ebase` is an empty base, `base` a non-empty one, for each payload size SIZES. The same text is compiled by the reference (cl 15.00.30729.01, VC 2008 SP1,
fnv-nx-build toolchain/msvc9) as `T fn_T(); void c_T() { fn_T(); }` with /c /O2 /FAs: the caller reads the result through eax:edx
(`regs`) or pushes the address of a temporary (`hidden`). The table `cl_pod_table.tsv` (`<type><TAB>regs|hidden`, header lines `#`) is that output,
a committed fact; defdisptest compares the plugin with every row, no expected value is written by hand.
Run: python3 podmatrix.py [--cl-source]   prints decls() / the cl input;  python3 podmatrix.py --cl CL.EXE --wine-prefix P --toolchain-commit C --out cl_pod_table.tsv"""
import os, re, sys
FEATURES = ('none', 'ctor', 'copyctor', 'copyassign', 'dtor', 'virtual', 'base', 'ebase', 'private', 'protected', 'refmember', 'sm', 'mf')
CONTROLS = ('none', 'sm', 'mf')   # must classify as POD at every depth: the predicate does not over-trigger
SIZES = (1, 2, 4, 8, 12)


def payload(size):
    return {1: 'char a;', 2: 'short a;', 4: 'int a;', 8: 'int a; int b;', 12: 'int a; int b; int c;'}[size]


def inner(feat, size, nm):
    """the class `nm` carrying one feature and a payload of SIZE bytes (a reference member is the 4-byte pointer plus the rest)"""
    p = payload(size)
    pre, body, post = '', '', ''
    if feat == 'refmember':
        if size < 4: return None
        p = 'int &r; ' + payload(size - 4) if size > 4 else 'int &r;'
    if feat == 'ctor': body = '%s() {} ' % nm
    elif feat == 'copyctor': body = '%s(const %s &o) {} ' % (nm, nm)
    elif feat == 'copyassign': body = '%s &operator=(const %s &o) { return *this; } ' % (nm, nm)
    elif feat == 'dtor': body = '~%s() {} ' % nm
    elif feat == 'virtual': body = 'virtual void f() {} '
    elif feat == 'base': pre = 'struct B_%s { int z; };\n' % nm
    elif feat == 'ebase': pre = 'struct B_%s {};\n' % nm
    elif feat == 'sm': body = 'static int s; '
    elif feat == 'mf': body = 'int f(); '
    head = 'struct %s%s' % (nm, ' : B_%s' % nm if feat in ('base', 'ebase') else '')
    if feat in ('private', 'protected'): return '%s { %s: %s };' % (head, feat, p)
    return '%s%s { %s%s };' % (pre, head, body, p)


def cases():
    """[(type name, declaration text)] in a fixed order; the first case of a (feature, size) carries the class, the others use it"""
    out = []
    for feat in FEATURES:
        for size in SIZES:
            base = 'I_%s_%d' % (feat, size)
            t = inner(feat, size, base)
            if t is None: continue
            out.append(('C_%s_%d' % (feat, size), t + '\ntypedef %s C_%s_%d;' % (base, feat, size)))
            out.append(('D_%s_%d' % (feat, size), 'struct D_%s_%d { %s m; };' % (feat, size, base)))
            out.append(('A_%s_%d' % (feat, size), 'struct A_%s_%d { %s m[1]; };' % (feat, size, base)))
            out.append(('E_%s_%d' % (feat, size), 'struct M_%s_%d { %s m; };\nstruct E_%s_%d { M_%s_%d m; };' % ((feat, size, base) + (feat, size, feat, size))))
    return out


def decls():
    return '\n'.join(c[1] for c in cases()) + '\n'


def cl_source():
    """the reference input: one function per type, caller reads the result"""
    return decls() + ''.join('%s fn_%s();\nvoid c_%s() { fn_%s(); }\n' % ((n,) * 4) for n, _ in cases())


def classify(asm):
    """{type: 'hidden'|'regs'} from the /FAs listing: fn_T takes no declared parameter, so any `push` between the start of c_T and its call to fn_T is
    the hidden result pointer; none = the result comes back in eax:edx / eax (one rule, read from the caller's own code)."""
    out = {}
    for m in re.finditer(r'^(\?c_(\w+)@@\S+)\s+PROC.*?^\1\s+ENDP', asm, re.S | re.M):
        body = m.group(0)
        call = re.search(r'call\s+\?fn_%s@@' % re.escape(m.group(2)), body)
        if call is None: raise SystemExit('podmatrix: no call of fn_%s in its caller' % m.group(2))
        out[m.group(2)] = 'hidden' if re.search(r'^\s*push\b', body[:call.start()], re.M) else 'regs'
    return out


def run_cl(cl, prefix, commit, outp):
    """the reference run: cl 15.00.30729.01 (VC 2008 SP1) under Wine on cl_source(); writes the table (deterministic: no clock, no path)"""
    import hashlib, subprocess, tempfile
    env = dict(os.environ, WINEPREFIX=prefix, WINEDEBUG='-all')
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, 'm.cpp'), 'w').write(cl_source())
        r = subprocess.run(['wine', cl, '/nologo', '/c', '/O2', '/FAs', '/Famn.asm', '/Fomn.obj', 'm.cpp'], cwd=td, env=env, capture_output=True, text=True)
        if r.returncode: raise SystemExit('podmatrix: cl failed:\n' + r.stdout[-3000:] + r.stderr[-1000:])
        ver = subprocess.run(['wine', cl], cwd=td, env=env, capture_output=True, text=True).stderr.split('\n')[0].strip()
        cls = classify(open(os.path.join(td, 'mn.asm'), errors='replace').read())
    names = [c[0] for c in cases()]
    if set(cls) != set(names): raise SystemExit('podmatrix: cl produced %d callers for %d cases' % (len(cls), len(names)))
    pk = subprocess.run('dpkg-query -W wine wine64 wine32 libwine 2>/dev/null; true', shell=True, capture_output=True, text=True).stdout.strip().split('\n')
    h = ['# MEASUREMENT of %d cases (podmatrix.py --cl): the caller of `T fn_T()` reads the result through a hidden pointer (hidden) or eax:edx (regs)' % len(names),
         '# cl: %s; cl.exe sha256 %s' % (ver, hashlib.sha256(open(cl, 'rb').read()).hexdigest()),
         '# toolchain commit (fnv-nx-build toolchain/msvc9): %s' % commit] + ['# dpkg-query -W: ' + p.replace('\t', ' ') for p in pk if p]
    open(outp, 'w').write('\n'.join(h) + '\n' + ''.join('%s\t%s\n' % (n, cls[n]) for n in names))


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--cl-source', action='store_true', help='print the reference input')
    ap.add_argument('--cl', help='path of cl.exe: run the reference and write the table')
    ap.add_argument('--wine-prefix'); ap.add_argument('--toolchain-commit'); ap.add_argument('--out')
    a = ap.parse_args()
    if a.cl:
        if not (a.wine_prefix and a.toolchain_commit and a.out): ap.error('--cl needs --wine-prefix, --toolchain-commit and --out')
        run_cl(a.cl, a.wine_prefix, a.toolchain_commit, a.out)
    else:
        sys.stdout.write(cl_source() if a.cl_source else decls())
