# lift/tests/guest: the lift tests as guest programs (L3a item 16, for L4's tests/guest_lift)

Each driver is a directory with:
- `gen.py <out>`: a `generated` stage (tools of `lift/tools` mounted at /tool; pinned python modules only through the stage's `py_modules`). It computes every expected value
  from its reference (MPFR through `tools/mpfr_oracle.py`; the x86 sources through the pinned keystone/capstone) and writes ONLY the tables the guest includes.
- `main.cc`: the guest `main`. It compares every case itself and returns 0, or the 1-based number of the first differing case (`labels.txt` names it); every case's result is
  also kept in a global array `g_res[]` for the boot machine to dump.
- no host harness: no Unicorn, no keystone call outside the stage.

Done (generator + main, syntax-checked only, never built or run here): `citest/`, `imptest/`, `msvcrttest/` (its three cases that end the process, terminal.json, run as `main` with the case index and are checked by the boot machine: stop, exit code, memory words). Not built here: the root's `build.json`, its admission and the stage's `py_modules` form wait for the merged base
(claude/fnv-l3a-lift-int2) and L1's pinned-module stage; disptest follows with the lift/bridges wiring on int2; instest is parked (its reference becomes the pinned Sail model through isla, not Unicorn).
The lifted units and the native main/natives are two roots (the origin rule): the generated stage lives in the lifted sub-root (LIFTED_ARGS); the native test root links it.

## Roots (build.json pair per test; admit rows pending L1's stage form)
Each test is two roots: `<t>/lifted` (class `lifted`: `gen.py` only, no tracked C++; one generated stage with `py_modules`
keystone-engine/capstone/gmpy2, input `lift/tools` mounted and passed as argv[2]; outputs under `gen/`; lifted arguments by origin)
and `<t>/native` (class `test`: `main.cc`, native arguments and plugins, `uses` the lifted root's `gen` output, `link.roots`
rt/guest and platform). Forms `uses`, `link.roots` and `py_modules` follow w20/w14/L1 and are unchecked by recipe.py on int2 until they land.
