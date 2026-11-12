#!/usr/bin/env python3
"""podmatrix - the class-returning matrix of the x86 hidden-pointer rule (DISPATCH1, msvc_pod).
decls() is the C++ text of every case: one feature at a time (FEATURES, plus `none`), at depth 0 (the class itself, C_), depth 1 (a member of that
class, D_), depth 1 through an array (A_) and depth 2 (a member whose class has the member, E_); controls `sm` (static data member) and `mf`
(non-virtual member function) must stay POD; `ebase` is an empty base, `base` a non-empty one, for each payload size SIZES. The same text is compiled by the reference (cl 15.00.30729.01, VC 2008 SP1,
fnv-nx-build toolchain/msvc9) as `T fn_T(); void c_T() { fn_T(); }` (fn_T only declared) with /c /O2 /FAs: the caller reads the result through eax:edx
(`regs`) or passes the address of its own temporary as the one argument (`hidden`); classify() decides by that structure and fails on anything else. The table `cl_pod_table.tsv` (`<type><TAB>regs|hidden`, header lines `#`) is that output,
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


def instrs(body):
    """the instructions of a PROC body as [(mnemonic, operand text)], comments and directives dropped"""
    out = []
    for l in body.split('\n'):
        l = l.split(';')[0].strip()
        m = re.match(r'([a-z]+)\b\s*(.*)$', l)
        if m and m.group(1) not in ('proc', 'endp', 'ret', 'align') and not l.startswith(('?', '$', '_', 'PUBLIC', 'EXTRN', 'TEXT', 'END')): out.append((m.group(1), m.group(2).strip()))
        elif m and m.group(1) == 'ret': out.append(('ret', ''))
    return out


def classify(asm):
    """{type: 'hidden'|'regs'} from the /FAs listing, by the structure of the fixed caller `void c_T() { fn_T(); }` (fn_T only declared, no parameter, no
    local of the caller): the call (or tail `jmp`) to fn_T is preceded by
      lea R, [esp|ebp ...] ; push R     -> hidden: exactly one argument, an address in the caller's own frame (the result temporary);
      anything that is not a push        -> regs: no argument is passed (the prologue's own pushes come earlier and are not adjacent to the call);
    a push right before the call that is not that pair, a missing call, or a `jmp` after a frame address argument is an ERROR naming the case, never a guess."""
    out = {}
    for m in re.finditer(r'^(\?c_(\w+)@@\S+)\s+PROC.*?^\1\s+ENDP', asm, re.S | re.M):
        t = m.group(2); ins = instrs(m.group(0))
        ci = [i for i, (op, rest) in enumerate(ins) if op in ('call', 'jmp') and re.search(r'\?fn_%s@@' % re.escape(t), rest)]
        if len(ci) != 1: raise SystemExit('podmatrix: %s: the caller has %d calls of fn_%s' % (t, len(ci), t))
        i = ci[0]
        if i >= 2 and ins[i - 1][0] == 'push' and ins[i - 2][0] == 'lea' and ins[i - 2][1].split(',')[0].strip() == ins[i - 1][1] \
                and re.search(r'\[(esp|ebp)[^\]]*\]', ins[i - 2][1]) and ins[i][0] == 'call':
            out[t] = 'hidden'
        elif i >= 1 and ins[i - 1][0] == 'push':
            raise SystemExit('podmatrix: %s: a push before the call that is not a frame address (%s; %s)' % (t, ins[i - 1], ins[i - 2] if i >= 2 else None))
        else:
            out[t] = 'regs'
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
    bad = [n for n in names if (n.split('_')[1] in CONTROLS and int(n.split('_')[2]) in (1, 2, 4, 8) and cls[n] != 'regs') or (int(n.split('_')[2]) == 12 and cls[n] != 'hidden')]
    if bad: raise SystemExit('podmatrix: the table is invalid: a control that is not regs or a size 12 that is not hidden: %s' % bad[:8])
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
