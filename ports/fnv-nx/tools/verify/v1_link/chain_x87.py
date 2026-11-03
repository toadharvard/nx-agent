#!/usr/bin/env python3
"""chain_x87.py - BUG-C reproducer, end to end: the x87 result chain of oracle/chain_*.cpp (x86 under Wine prints
double=3.5 float=3.5) rebuilt for AArch64 with the bridges the code under test generates for the same declarations.

    bottom  float __thiscall C::G(void)        returns 3.5f
    middle  int __fastcall F(void *p) { return G(p); }   with G declared  int __fastcall G(void *)
    top     double __thiscall D::F(void) / float __thiscall E::F(void), bound to F

x86: the middle passes st(0) through, so both callers read 3.5. AArch64 with tools/link's bridges: the float
caller gets 3.5 (fmov bit-casts cancel), the double caller gets the float's bits in the low half of d0."""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import qemu_ilp32            # noqa: E402
import tool_bridge           # noqa: E402

G_REF, G_DEF = '?G@@YIHPAX@Z', '?G@C@@QAEMXZ'
D_REF, E_REF, F_DEF = '?F@D@@QAENXZ', '?F@E@@QAEMXZ', '?F@@YIHPAX@Z'


def rename(asm, name, target):
    import re
    text = re.sub(r'//.*', '', asm)
    old = re.search(r'^(\S+):', text, re.M).group(1)
    text = text.replace(old, name)
    return re.sub(r'\b(b|bl)\s+__fnv_\w+', r'\1 ' + target, text)


def main():
    work = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='v1_x87_')
    os.makedirs(work, exist_ok=True)
    dem = bh.undname([G_REF, G_DEF, D_REF, E_REF, F_DEF])
    asm = ['    .text']
    for ref, dfn, name, target in ((G_REF, G_DEF, 'G_ref', 'C_G'), (D_REF, F_DEF, 'D_F_ref', 'F_mid'),
                                   (E_REF, F_DEF, 'E_F_ref', 'F_mid')):
        kind, classes, code = tool_bridge.verdict(ref, dfn, dem, {}, {})
        print('%-40s -> %-40s tool: %s %s' % (dem[ref], dem[dfn], kind, ','.join(classes)))
        if kind == 'bridge':
            asm.append(rename(code, name, target))
        else:
            asm.append('    .globl %s\n%s:\n    b %s' % (name, name, target))
    s = os.path.join(work, 'bridges.S')
    open(s, 'w').write('\n'.join(asm) + '\n')
    c = os.path.join(work, 'chain.cpp')
    open(c, 'w').write(r'''
extern "C" void v1_hex(const void *, unsigned long); extern "C" void v1_puts(const char *);
extern "C" __attribute__((noinline)) float C_G(void *self) { return ((float *)self)[0] * 0.5f; }
extern "C" int G_ref(void *);
extern "C" __attribute__((noinline)) int F_mid(void *p) { return G_ref(p); }
extern "C" double D_F_ref(void *);
extern "C" float E_F_ref(void *);
int main() { float c = 7.0f; double d = D_F_ref(&c); float f = E_F_ref(&c);
  v1_puts("D "); v1_hex(&d, 8); v1_puts("\nF "); v1_hex(&f, 4); v1_puts("\n"); return 0; }
''')
    code, out = qemu_ilp32.run(qemu_ilp32.build([c, s], os.path.join(work, 'chain')))
    vals = dict(l.split() for l in out.split('\n') if l.strip())
    d = struct.unpack('<d', bytes.fromhex(vals['D']))[0]
    f = struct.unpack('<f', bytes.fromhex(vals['F']))[0]
    print('AArch64 with the tool\'s bridges: double=%r float=%r   (x86 under Wine: double=3.5 float=3.5)' % (d, f))
    ok = d == 3.5 and f == 3.5
    print('chain matches x86' if ok else 'BUG-C reproduced: the chain does not match x86')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
