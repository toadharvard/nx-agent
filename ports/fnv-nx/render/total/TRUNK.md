# d3d9total on the trunk (VERDICT class 7: uncovered D3D9 enum value or SM op)

## gates.json entry

```json
{"name": "d3d9total", "argv": ["python3", "-I", "ports/fnv-nx/render/total/gate_d3d9total.py"], "result": "d3d9total.json"}
```

## admit.json root entry

```json
{"path": "ports/fnv-nx/render/total", "class": "tool", "gates": ["pure", "selftests"]}
```

L1 also adds `ports/fnv-nx/render/total` to the argv of the `pure` and `selftests` gates.

## Declared inputs

The gate reads only the git-tracked files of these paths (`git ls-files`). It copies them to `GATE_OUT/d3d9total.work/in`, and a declared path with no tracked file makes the gate red:

- `render/d3d9/front`
- `render/d3d9/backend`
- `render/d3d9/spec/d3d9_spec.json`
- `shim/include`
- `render/shaders/total/sm_table.json`, `gen_sm_rows.py`, `fnvnx_sm_rows.h`
- `render/shaders/mojoshader.patch`
- `render/shaders/build.sh` (`MOJO_PIN`)
- `render/total/externals.json`

**External.** MojoShader is a recipe external. `externals.json` names fnv-nx-build `tools/mojoshader`:
- commit `ad5dff84830c2863c841f4b1f4e3df78c705b383`, which must equal `MOJO_PIN`;
- `tree_sha256` `e49a1b65e09c86647fd8c0b26c7a8e16e4a16e6ad1290a1e7ada85047306f361`, which is the verify/cas.py tree hash.

It is fetched as the recipe does: a bare repository under `GATE_CACHE/external/<commit>`, then `git fetch --depth 1 $ARTIFACTS_URL <commit>`, then read-tree and checkout-index. A hash mismatch makes the gate red. There is no env path and no `FNVNX_SHADERS_DEPS`.

**Programs.** The gate uses the absolute paths `/usr/bin/gcc`, `/usr/bin/clang` and `/usr/bin/git` from verify/env.json. It runs compile-only passes (`-fsyntax-only` and the clang AST dump). It does not link and does not run what it builds.

**Environment.** It reads `GATE_OUT`, `GATE_CACHE` (optional) and `ARTIFACTS_URL` (optional, defaults to the fnv-nx-build URL).

## Verdicts (step 4)

| Run | Verdict |
|---|---|
| `gate_pure.py ports/fnv-nx/render/total` (trunk bab699b9 checkout) | green, 4 of 4 files accepted by puregate (tests/ is outside the universe) |
| `gate_tests.py --jobs 3 ports/fnv-nx/render/total` (trunk checkout) | green, 1 of 1 test files pass in 43.5 s: 47 of 47 mutations killed, base 332 of 332 |
| The gate run as l1.py runs it (trunk checkout, GATE_OUT, GATE_CACHE) | green, 332 of 332, 4 s, external fetched from the default ARTIFACTS_URL (github fnv-nx-build) |
| The gate on this branch | green, 332 of 332: 161 of 161 enum values, 12 of 12 enumerations sensitive; 171 of 171 SM commands, 0 mutations not killed; 3 s |

`tests/test_d3d9total.py` checks 47 rule-derived mutations, and each one makes the gate red on exactly the ids its check guards:
- for each of the 12 switched enumerations: a header value changed, an enumerator dropped, and the front switch retyped to int;
- a case label missing in the front;
- an SM table row changed, both without and with regenerating the rows header;
- the pin differs;
- the tree hash is wrong;
- the patch is corrupt;
- the value, stage and comparison assertions are dead;
- the PHASE token is not consumed;
- a declared input is untracked.

The test also checks that the gate's tree hash equals `verify/cas.py ingest`.
