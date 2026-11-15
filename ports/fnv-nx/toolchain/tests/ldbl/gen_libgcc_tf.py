#!/usr/bin/env python3
"""gen_libgcc_tf.py <gcc-13.3.0/libgcc dir> -> libgcc_tf.txt: every symbol of libgcc (aarch64) whose type is the 128-bit TFmode, taken from the pinned
libgcc source by its own build rules (no names guessed):
  config/aarch64/t-softfp: softfp_float_modes := tf, softfp_int_modes, softfp_extensions, softfp_truncations (the pairs that involve tf);
  config/t-softfp: the function families per mode (softfp_float_funcs, softfp_floatint_funcs) and the extend/trunc names, as GNU make
    evaluates them;
  soft-fp/<f>.c: the strong_alias lines of each such file add its other global symbols (__gttf2 with __getf2 ...);
  Makefile.in: lib2funcs members of the TF mode of libgcc2 (_powitf2 _multc3 _divtc3).
The names are written with the leading `__` of libgcc's C symbols. Run once per pinned GCC (the test reads the file)."""
import os
import re
import subprocess
import sys
import tempfile

lib = sys.argv[1]
mk = 'srcdir=%s\ninclude $(srcdir)/config/aarch64/t-softfp\nsoftfp_extensions := $(filter %%tf,$(softfp_extensions)) $(filter tf%%,$(softfp_extensions))\n' % lib
mk += 'softfp_truncations := $(filter %tf,$(softfp_truncations)) $(filter tf%,$(softfp_truncations))\nsoftfp_extras :=\n'
mk += 'include $(srcdir)/config/t-softfp\nall:\n\t@echo $(softfp_func_list)\n'
with tempfile.NamedTemporaryFile('w', suffix='.mk', delete=False) as f:
    f.write(mk)
funcs = subprocess.run(['make', '-s', '-f', f.name], capture_output=True, text=True, check=True).stdout.split()
os.unlink(f.name)
syms = set()
for fn in funcs:
    syms.add('__' + fn)
    src = open(os.path.join(lib, 'soft-fp', fn + '.c')).read()
    for m in re.finditer(r'strong_alias\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)', src):
        syms.add(m.group(2))
mf = open(os.path.join(lib, 'Makefile.in')).read()
for m in re.finditer(r'^lib2funcs = (.*?)\n\n', mf, re.S | re.M):
    for t in m.group(1).replace('\\\n', ' ').split():
        if t in ('_powitf2', '_multc3', '_divtc3'):
            syms.add('_' + t)
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'libgcc_tf.txt'), 'w').write(''.join(s + '\n' for s in sorted(syms)))
print(len(syms), 'symbols')
