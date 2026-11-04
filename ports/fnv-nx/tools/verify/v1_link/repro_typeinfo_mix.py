#!/usr/bin/env python3
"""repro_typeinfo_mix.py - V1-link defect reproducer (C4): when units reconstruct one class with different bases,
the linked image pairs the vtable group of one unit with the typeinfo of another (both are weak COMDAT symbols; the
linker keeps the first copy of each in link order, independently). RTTI then contradicts the vtable: a secondary
vtable's offset-to-top names a base subobject the typeinfo does not have, so __dynamic_cast to that base fails.

    repro_typeinfo_mix.py <work dir> [<fixed dir> <trial.elf>]   # exit 1 while the defect is present

Without arguments after the work dir: hand-made units (no decomp content) linked like `fnvlink.py check` links
(sorted object names, aarch64linux32, --noinhibit-exec), audited with c4_definers.audit. With a run's fixed dir and
trial.elf: the same audit on that run (f12: 35 classes, e.g. a class whose own unit has ten bases and whose kept
typeinfo, from another unit, has one).
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c4_definers  # noqa: E402
import qemu_ilp32   # noqa: E402

# a_*.o sorts first: its view of C has one base and only needs C's typeinfo (typeid); b_*.o has the vtable
A_CPP = r'''
#include <typeinfo>
struct B1 { virtual ~B1() {} int x; };
struct C : B1 { virtual void f() {} int y; };
const std::type_info &ti_from_a() { return typeid(C); }
'''
B_CPP = r'''
struct B1 { virtual ~B1() {} int x; };
struct B2 { virtual void g() {} int z; };
struct C : B1, B2 { virtual void f() {} int y; };
C *make_c() { return new C; }
'''


def main():
    work = os.path.abspath(sys.argv[1])
    if len(sys.argv) > 3:
        return c4_definers.audit(sys.argv[2], sys.argv[3], os.path.join(work, 'audit.tsv'))
    if os.path.isdir(work):
        shutil.rmtree(work)
    fixed = os.path.join(work, 'fixed')
    os.makedirs(fixed)
    for name, text in (('a_view.cpp', A_CPP), ('b_vtable.cpp', B_CPP)):
        src = os.path.join(work, name)
        open(src, 'w').write(text)
        subprocess.run([qemu_ilp32.CXX, '-mabi=ilp32', '-O2', '-fPIC', '-c', src, '-o',
                        os.path.join(fixed, name + '.o')], check=True)
    elf = os.path.join(work, 'trial.elf')
    objs = sorted(os.path.join(fixed, n) for n in os.listdir(fixed))
    subprocess.run([qemu_ilp32.LD, '-m', 'aarch64linux32', '--noinhibit-exec', '-e', '0', '-o', elf, *objs],
                   capture_output=True)
    rc = c4_definers.audit(fixed, elf, os.path.join(work, 'audit.tsv'))
    print('DEFECT reproduced: the kept typeinfo of C is not the one of the unit whose vtable was kept' if rc else
          'not reproduced')
    return rc


if __name__ == '__main__':
    sys.exit(main())
