# L6 tests and the clean branch's verdict (proposal for L4, task Z0)

How the verdict works (verify/verdict.py, verify/gates.json, verify/gate_tests.py): `verdict.py run` runs each gate of
`verify/gates.json` (argv, cwd = clean worktree of the commit, env `GATE_OUT`), in file order. A gate writes
`GATE_OUT/<result>` = `{"format":"fnvnx-gate/1","name","status","universe","certified","uncertified":[<=50],"detail"}`.
A gate is green iff exit 0, the result is valid, `name` matches, `status` is green and `certified == universe`.
The verdict is green iff there is at least one gate and all gates are green. `merge` needs the stored green verdict
of the merged tree, and the ratchet: the set of gate names never shrinks.

What existing gates see of L6: `selftests` runs `DIR/tests/test_*.py` for the DIRs it is given
(verify farm contract link rules lift). L6 tests live at `tests/l6/<area>/test_*.py`, `tests/s6n`, `tests/s6b`, so
**no L6 test is in any gate today**. Adding `ports/fnv-nx/tests/l6` to the selftests argv would find nothing
(`tests/l6/tests/` does not exist) and a layout change is not needed.

Proposal: a new gate `l6`, with the runner owned by L6 (`render/total/gate_l6.py`, written, tested for 2 items):
universe = its ITEMS table (10 items: the four `make -C tests/s6n` targets unit/scenes/capture/nx_render, the five
`tests/l6` tests that need only gcc + python3 + EGL surfaceless (spec, wrap, cmp, dev, res), and `render/total/no_old_stack.sh`); certified =
exit status 0; no skip logic (same rule as gate_tests.py). Entry for `verify/gates.json` (L4 owns the file; NOT edited):

```json
  {
   "name": "l6",
   "argv": ["python3", "-I", "ports/fnv-nx/render/total/gate_l6.py"],
   "result": "l6.json"
  }
```

Needs in the gate's sandbox: g++/gcc, python3, make, libegl + Mesa llvmpipe (surfaceless GL 4.5), numpy and PIL for the
s6n scene comparison (scenes.py, test_nx_render.py). If L4's sandbox has no GL, `s6n-*` items are uncertified and the
gate is red; then split the item table into `l6` (no GL) and `l6-gl`.

Not registered (they need an input the gate cannot have yet), to enter when the input is pinned in `verify/env.json`
or the stage graph:
| test | needs | owner of the input |
|---|---|---|
| `tests/l6/test_sm_total.py --build <sm3c>` | built translator (render/shaders with uam + MojoShader) | L6 build stage; today red before and after Z0 (7538 ok, unresolved 8, conflicts 4: S3b in progress) |
| `python3 tests/s6b/run.py --build <host>` | the same host build | L6 build stage |
| `tests/l6/rec/test_rec.py`, `tests/l6/recjob/test_recjob.py` | Wine 9.0 + i686 mingw (recjob also skips its shellcheck part when absent: skip logic) | L4 farm job (M1a) |
| `tests/l6/ffp_ps`, `ffp_vs`, `sm_fix/test_*.py` | uam (`UAM` env, default under /home/user/work) and the sm3c host build | L6 build stage |
| `tests/l6/env/*`, `tests/l6/conf/*` | live files of running workers | L6 when the tasks land |
| `make -C tests/s6n emu` | unicorn + guest.nso | L5/L4 |
Statement of fact (not a gap in the gate): the `selftests` gate and the new gate are independent; adding `l6` only
adds a gate name, so the ratchet is not affected.
