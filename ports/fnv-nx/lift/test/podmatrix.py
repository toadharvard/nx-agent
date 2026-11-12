#!/usr/bin/env python3
"""podmatrix - the class-returning matrix of the x86 hidden-pointer rule (DISPATCH1, msvc_pod).
decls() is the C++ text of every case: one feature at a time (FEATURES, plus `none`), at depth 0 (the class itself, C_), depth 1 (a member of that
class, D_) and depth 1 through an array (A_), for each payload size SIZES. The same text is compiled by the reference (cl 15.00.30729.01, VC 2008 SP1,
fnv-nx-build toolchain/msvc9) as `T fn_T(); extern T g_T; void c_T() { g_T = fn_T(); }` with /c /O2 /FAs: the caller reads the result through eax:edx
(`regs`) or pushes the address of a temporary (`hidden`). The table `cl_pod_table.tsv` (`<type><TAB>regs|hidden`, header lines `#`) is that output,
a committed fact; defdisptest compares the plugin with every row, no expected value is written by hand.
Run: python3 podmatrix.py            prints decls() (the input of the cl run)"""
FEATURES = ('none', 'ctor', 'copyctor', 'copyassign', 'dtor', 'virtual', 'base', 'private', 'protected', 'refmember')
SIZES = (1, 2, 4, 8, 12)
DEPTHS = (('C', 0), ('D', 1), ('A', 2))   # prefix of the type name, depth


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
    head = 'struct %s%s' % (nm, ' : B_%s' % nm if feat == 'base' else '')
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
    return out


def decls():
    return '\n'.join(c[1] for c in cases()) + '\n'


def cl_source():
    """the reference input: one function per type, caller reads the result"""
    return decls() + ''.join('%s fn_%s();\nextern %s g_%s;\nvoid c_%s() { g_%s = fn_%s(); }\n' % ((n,) * 7) for n, _ in cases())


if __name__ == '__main__':
    import sys
    sys.stdout.write(cl_source() if '--cl' in sys.argv else decls())
