# d3d9total on the trunk (VERDICT class 7: uncovered D3D9 enum value or SM op)

## gates.json entry

```json
{"name": "d3d9total", "argv": ["python3", "-I", "ports/fnv-nx/render/total/gate_d3d9total.py"], "result": "d3d9total.json"}
```

## admit.json root entry

```json
{"path": "ports/fnv-nx/render/total", "class": "tool"}
```

L1 also adds `ports/fnv-nx/render/total` to the argv of the `pure` and `selftests` gates.

## Declared inputs

The gate reads only the git-tracked files of these paths (`git ls-files`). It copies them to `GATE_OUT/d3d9total.work/in`, and a declared path with no tracked file makes the gate red:

- `render/d3d9/front`, `bridgefacts/d3d9` (generator of D9_SPEC)
- the roots' own records: `render/d3d9/build.json` and the directories of its `include` list (nothing restated in the gate), `render/shaders/translator/build.json`
- `render/shaders/total` (the generator of the SM table and rows header, `build.json`: the gate runs the root's generate stage over the pinned mirrors; the table and the header are not tracked files)

**External.** The MojoShader tree is the `mojoshader` external of the translator root's recipe (`render/shaders/translator/build.json`, the patched mirror fnv-nx-build `tools/mojoshader-glsldk`). Its commit and `tree_sha256` are read from that record; the gate holds no pin of its own and applies no patch.

It is fetched as the recipe does: a bare repository under `GATE_CACHE/external/<commit>`, then `git fetch --depth 1 $ARTIFACTS_URL <commit>`, then read-tree and checkout-index. A hash mismatch makes the gate red. There is no env path and no `FNVNX_SHADERS_DEPS`.

**Programs.** The gate uses the absolute paths `/usr/bin/gcc`, `/usr/bin/clang` and `/usr/bin/git` from verify/env.json. It runs compile-only passes (`-fsyntax-only` and the clang AST dump). It does not link and does not run what it builds.

**Environment.** It reads `GATE_OUT`, `GATE_CACHE` (optional) and `ARTIFACTS_URL` (optional, defaults to the fnv-nx-build URL).

`tests/test_d3d9total.py` checks rule-derived mutations, and each one makes the gate red on exactly the ids its check guards:
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
