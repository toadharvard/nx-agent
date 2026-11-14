# l6 render w5: item 3.6c (doubles to 0), state at base 141b07580

Measured from this container (measurements, not verdicts: the env mismatch of setup_verdict_env is not fixed).

## Count at the base commit 141b07580 (claude/fnv-next3b), global over the whole tree ports/fnv-nx

- test files that define a symbol of a product root (doubles): 0 files, 0 distinct symbols. There is no `tests/l6` directory and no file under `ports/fnv-nx/tests/` on the base; `render/total/tests/test_d3d9total.py` is the only render test there and has no C/C++ source.
- `--unresolved-symbols=ignore-all` in a link command: 0 sites. The one textual hit is `verify/tests/test_recipe.py:418`, a negative case that proves recipe.py rejects the flag (it is the check, not a use).
- test roots in `verify/admit.json`: 0 (24 roots, classes doc/tool/product).

## Where the census lives

The 22 ignore-all link commands in 16 scripts (bridge 2, const 1, copy 2, dev 2, devlife 2, draw 1, drawgl 2, env 1, ffpdev 2, m1 2, oom 1, present 1, pvw 1, res 1, tex 1, x1 1) and the 26 doubling files are in `tests/l6/**` of claude/fnv-l6-render (census render/DOUBLES.md). That tree is not an ancestor of the base, so the trunk base already holds 0 doubles. Step 1 of the order (delete the ignore-all links and the scripts that only work with them) is therefore: none of the 16 scripts is carried onto this branch.

## Rule for what enters this branch (item 3.6c)

A test enters only as a root of class `test` (own build.json and recipe, `link.roots` of the real product roots, an oracle record per test), with no ignore-all and no symbol of a product root defined by the test. Tests that cannot be rewritten onto real files are not carried.
