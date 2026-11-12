# l6-render-w6 (Sonnet worker, L6 render) — objstub on render/d3d9: 25 findings -> 0

Branch claude/fnv-l6-render-w6, base claude/fnv-l6-render-w5 (c7b4db7e). Commits: 1bb7a25e, b4ae9ebb (R1), 1f0b4012 (R2), 7229f043 (R4), e64ffc9a (R3).

## Gate line (python3 -I verify/gate_objstub.py, as w5 ran it)

- before (w5, MARKERS.md, measured at 24f18cdb): `objstub: red 1754/1779 25 findings: V 21, wine-fixme-in-real-body 4`
- after (working tree = e64ffc9a plus the local measurement conditions below): `objstub: green 1772/1772 1772 functions in 69 objects of 1 roots, no trap path, no vacuous body`
- Gate, objstub.py, gate_objstub.py: unchanged. Root build green, 69 objects (recipe.py, msvcmodel b4057704, 151 stages cold).

## Measurement conditions (uncommitted, as w5)

- `dev/ffp_prog.cpp` dropped from the index (`git rm --cached`; `../gen/ffp_glsl.gen.h` has no recipe step; w2 inbox). Its functions are not among the 1772.
- verify/admit.json: local class `tool` entries for ports/fnv-nx/shim/include, render/shaders/include, render/backend.
- GATE_CACHE = /tmp/claude-0/b (store = its `store`), ARTIFACTS_URL default (fnv-nx-build, compiler external pinned in guest_compiler.json).

## Rules and what each closed

R1, Wine 9.0 simplified bodies. Sources read from the wine-9.0 tag (raw files), not from memory. One `fnvnx_wine_fixme(id)` then Wine's result; rows 48-54 are in verify/wine_fixme.tsv, the only table (round 2 below removed the copy in const_fixme.tsv):

| id | contract name | wine_ref | kind, value |
|---|---|---|---|
| 48 | IDirect3DDevice9::DrawRectPatch | wine-9.0:dlls/d3d9/device.c:d3d9_device_DrawRectPatch | u32 0 |
| 49 | IDirect3DDevice9::DrawTriPatch | wine-9.0:dlls/d3d9/device.c:d3d9_device_DrawTriPatch | u32 0 |
| 50 | IDirect3DDevice9::DeletePatch | wine-9.0:dlls/d3d9/device.c:d3d9_device_DeletePatch | u32 0x8876086C |
| 51 | IDirect3D9::RegisterSoftwareDevice | wine-9.0:dlls/wined3d/directx.c:wined3d_register_software_device | u32 0 |
| 52 | DebugSetMute | wine-9.0:dlls/d3d9/d3d9_main.c:DebugSetMute | void |
| 53 | IDirect3DShaderValidator9::AddRef | wine-9.0:dlls/d3d9/d3d9_main.c:shader_validator_AddRef | u32 2 |
| 54 | IDirect3DShaderValidator9::Release | wine-9.0:dlls/d3d9/d3d9_main.c:shader_validator_Release | u32 1 |

- CORRECTION to the brief: Wine 9.0 `d3d9_device_DrawRectPatch` and `d3d9_device_DrawTriPatch` are `FIXME(...unimplemented.); return D3D_OK;`. Only `d3d9_device_DeletePatch` returns D3DERR_INVALIDCALL. The old product bodies returned D3DERR_INVALIDCALL for all three (with a comment that Wine's D3D_OK "is not copied"). By the owner's Wine rule the body is Wine's, so Draw*Patch now return D3D_OK. tests/l6/copy (cases.py `PT`, one mutation) follow.
- DebugSetMute, shader_validator_AddRef/Release: Wine logs nothing or only TRACE. The brief asks for one row each, so each call logs one line (the TRACE line of Wine) with ids 52-54. QueryInterface stays a real body without a log line.
- RegisterSoftwareDevice: d3d9_RegisterSoftwareDevice forwards to wined3d_register_software_device (FIXME stub, WINED3D_OK), so the cited function is the wined3d one.
- D3DPERF_SetMarker/SetOptions/SetRegion and DebugSetMute are `void` bodies of the log line only. The compiler made the last call a tail call (`b fnvnx_wine_fixme`), which the gate takes for a real body (W). Rule: `NX_WINE_FIXME_VOID(id)` (const_fixme.h) = the call plus an empty `asm volatile("" ::: "memory")`, which emits no instruction and keeps `bl` + `ret`. This is the cause of the 3 W findings on D3DPERF_Set*; the bodies were already one call.

R2, GetNumberOfSwapChains (both classes) returns `NxDevLife::implicit_chains` (Wine: wined3d_device_get_swapchain_count = device->swapchain_count). The field is incremented in nx_chain_create for an implicit chain (the creation path of the device). `implicit_chain(d, i)` (GetSwapChain, GetBackBuffer, gamma, raster, display mode, front buffer data) now tests `i < implicit_chains` instead of `i < 1`.

R3, `nx_iid_of` (7 overloads) -> `NxIid<T*>::value`, a constexpr reference to the interface GUID; `NX_IID_OF(p)` names it from the pointer type (state_rt.h). `nx_d3d9_export_count` -> `extern const int` initialised with sizeof table / sizeof entry (d3d9_exports.cpp; user: tests/l6/exports/t_exports.cpp). No other user in the tree (grep over render, tests, py, json).

R4, ProcessVertices. Wine 9.0 d3d9_device_ProcessVertices forwards to wined3d_device_process_vertices, which does real work (transforms the vertices into the destination buffer); its FIXMEs are branch-level (rows 10-15 are kind `branch`, already in the table). Ours does the same real work (pvw_core.cpp) and has no vacuous body. The remaining W finding of the first measurement was not a Wine-rule issue: the two identical methods NxDevice9::ProcessVertices / NxDevice9Ex::ProcessVertices were folded by the compiler into one symbol with two names, and the gate keeps the log call only for the first name of an alias range, so the other name looked like a real body with no id. Rule: the body and its branch log live in one non-inlined function (`process`, pvw_api.cpp); the two methods only call it. L1 may want to know: objstub's `wine` list is per first name of an alias range; any real body with a `branch` id that the compiler merges would show the same. No gate change made.

## Tests

The existing host-g++ tests of render/d3d9 could NOT run in this container (they do not go through the recipe; they build with the host g++ -m32): tests/l6/exports and x1 stop at `bits/c++config.h` (no 32-bit libstdc++ headers), const / devlife / pvw at the missing generated `../gen/state.gen.h` (the test's gen step did not produce it here), copy at the missing FNVNX_SHADERS_BUILD. They were updated by rule and are NOT verified by a run:
- tests/l6/const: ids 41-54, cases for both classes of the patches and RegisterSoftwareDevice, validator AddRef/Release and DebugSetMute now logged, mutation texts follow `NX_WINE_FIXME_VOID`.
- tests/l6/x1: DebugSetMute logs 52, AddRef/Release log 53/54, two mutations follow.
- tests/l6/copy: `PT` expectations 0, 0, INVALIDCALL; mutation text follows.
- tests/l6/exports: `nx_d3d9_export_count` is an object.
The product code of all of this is compiled by the recipe (green) and judged by objstub (green). Pre-existing, not touched: t_exports.cpp / t_x1.cpp define product-like symbols (doubles; w5's census lists them).

## Round 2 (lead review of 8d20d51e)

- One table. verify/wine_fixme.tsv is the only place of id, wine_ref, kind and value. Deleted: render/d3d9/dev/const_fixme.tsv and contract_consts.tsv (every row of it restated a wine_fixme row; the objstub `--constants` table is not used by render/d3d9: build.json has no `constants` key). pvw_fixme.tsv and d5a_fixme.tsv keep only the extra facts per id (condition, multiplicity / body); their name and Wine function columns are gone.
- Ids in code are generated: `gen/gen_wine_ids.py` (a generated step of render/d3d9/build.json, input verify/wine_fixme.tsv) writes `gen/wine_fixme_ids.gen.h`, one `NXW_<wine function>` constant per row; `dev/const_fixme.h` includes it and holds only the `fnvnx_wine_fixme` declaration and `NX_WINE_FIXME_VOID`. All hand-written id enums (NX_CF_*, NX_WINE_FIXME_*, PVW_FIXME_*, D5A_FIXME_*) are replaced. A name without a row does not compile; the tool refuses two rows with the same Wine function name.
- env/gen_env.py: the caps assertion now says what the code relies on (Wine 9.0 bodies, cited, rows 48-50). render/ASSUMPTIONS.md row 20 and QUEUE.md line 201 are closed.
- Measurement after merging origin/claude/fnv-l6-render (ffp_prog.cpp is in): recipe build green, 70 objects; `objstub: green 1784/1784 1784 functions in 70 objects of 1 roots, no trap path, no vacuous body`. Local condition left: admit.json tool entries for the include roots (not committed).
- Tests updated, still not run here (same reasons): every test that generates the gen directory now also runs gen_wine_ids.py; test_const reads rows 41-54 of verify/wine_fixme.tsv and checks the generated constants; its part c (contract_consts) is gone.

## For L1: two objstub quirks (no gate change made)

1. Tail call of a log. A function whose body is only `fnvnx_wine_fixme(id)` (void result) is compiled with the call as a tail `b`. objstub takes a branch to fnvnx_wine_fixme for a real body (class W, `wine-fixme-address-taken` / `wine-fixme-in-real-body`). Product rule used: `NX_WINE_FIXME_VOID(id)` = the call followed by an empty `asm volatile("" ::: "memory")` (no instruction, keeps `bl` + `ret`). The gate could accept a final `b fnvnx_wine_fixme` of a void function itself.
2. Folded aliases. When the compiler folds two identical functions (-fipa-icf: here NxDevice9::ProcessVertices and NxDevice9Ex::ProcessVertices) into one body with two symbols, the `wine` call list names only the first symbol, so objstub judges the second as a real body with no id (`wine-fixme-in-real-body`; the `branch` exemption looks up ids per name). Product rule used: the body and its log live in one `noinline` function that the two methods call. The gate could attribute the call sites of an alias range to every name of the range.
