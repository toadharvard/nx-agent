# lift/tests/guest: the lift tests as guest programs (L3a item 16, for L4's tests/guest_lift)

Each driver is a directory with:
- `gen.py <out>`: a `generated` stage (tools of `lift/tools` mounted at /tool; pinned python modules only through the stage's `py_modules`). It computes every expected value
  from its reference (MPFR through `tools/mpfr_oracle.py`; the x86 sources through the pinned keystone/capstone) and writes ONLY the tables the guest includes.
- `main.cc`: the guest `main`. It compares every case itself and returns 0, or the 1-based number of the first differing case (`labels.txt` names it); every case's result is
  also kept in a global array `g_res[]` for the boot machine to dump.
- no host harness: no Unicorn, no keystone call outside the stage.

Done: `citest/` (generator + main, syntax-checked only). Not built here: the root's `build.json`, its admission and the stage's `py_modules` form wait for the merged base
(claude/fnv-l3a-lift-int2) and L1's pinned-module stage; the lifted drivers (imptest, msvcrttest, disptest, instest) follow with `.x86` sources assembled in the stage.
