# l2 w3: long double = binary64 under ILP32 (msvcmodel.patch), 2026-11-15

Rule: in `gcc/config/aarch64/aarch64.h` `LONG_DOUBLE_TYPE_SIZE` is `(TARGET_ILP32 ? 64 : 128)`; in `aarch64.cc`
`aarch64_scalar_mode_supported_p` also accepts TFmode. Base: msvcmodel.patch content equal on origin/claude/fnv-l6-render-w2
(bf29345c0, the newest commit on toolchain/msvcmodel) and on the L2 branch (25cfdeed2). Read against the sha256-pinned
gcc-13.3.0 tarball; the new patch applies cleanly (`patch -p1 --dry-run`), and differs from the old one only by these two hunks.

Every use of the size in GCC 13.3 (gcc/ and libgcc/):
- `config/aarch64/aarch64.h:96` defines it (128). Changed.
- `defaults.h:524` fallback, unused (the macro is defined).
- `tree.cc:9494` `build_common_tree_nodes`: `TYPE_PRECISION(long_double_type_node)`; layout_type picks DFmode for 64. A node of its own,
  so `long_double_type_node != double_type_node` (cp/mangle.cc:2754/2758 compare nodes: 'e' vs 'd'; the names plugin gives 'O').
- `targhooks.cc:446` `default_scalar_mode_supported_p`: a float mode is supported only if its precision is FLOAT/DOUBLE/LONG_DOUBLE size.
  With 64 the 128-bit TFmode would become unsupported: `_Float128`/`_Float64x` (`default_floatn_mode`, targhooks.cc:493ff), `mode(TF)` in
  libgcc's softfp (libgcc/config/aarch64/sfp-machine.h) and `__LIBGCC_HAS_TF_MODE__` (c-family/c-cppbuiltin.cc:1366) all need it.
  So aarch64.cc aarch64_scalar_mode_supported_p (line 26734) returns true for TFmode: the second hunk.
- `c-family/c-cppbuiltin.cc:1247` builds `__LDBL_*` from the long double mode (DFmode now: MANT_DIG 53, MAX == __DBL_MAX__).
  The libgcc mode macros (1357ff): DFmode takes the DBL suffix first (1373); TFmode now takes the _Float128 suffix (1393).
- `aarch64.cc:19992` HFA/HVA base type TFmode -> long_double_type_node: only reached for a TFmode aggregate member, which a binary64
  long double no longer is (a _Float128 member gives a different node); no code to change.
- Language front ends (cp, c, ubsan, dwarf2out, gimple-ssa-sprintf, builtin-types.def): they take the node and its mode, no size literal.

libgcc: the TF soft-float routines (libgcc/config/aarch64/t-softfp `softfp_float_modes := tf`, the `__addtf3` family) stay in the ILP32
multilib: they implement _Float128 / `1.0q`, and the TFmode support above keeps the files compiling. `long double` arithmetic is DFmode,
hardware instructions and the double libgcc entries. LP64 is untouched.

Not covered here (a rebuild of the guest runtime on the new compiler does it): newlib's `*l` libm functions and `printf %Lf`
come out of the rebuild with `__LDBL_MANT_DIG__ == 53` (L5's tools/fnvnx-a64-msvcmodel13).

Test: toolchain/tests/test_ldbl.py (recipe, pinned compiler, product plugins; fixture tests/ldbl/ldbl.cpp). It FAILS on the compiler
now pinned (static asserts: 128-bit long double) and turns green with the rebuild. The compiler mirror `tools/gcc-ilp32-msvcmodel`
(commit + tree_sha256 in verify/guest_compiler.json) is a farm job; I did not build GCC.
