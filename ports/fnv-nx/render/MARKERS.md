# render/d3d9 markers (objstub gate, classes 1 and 2)

Measured at commit 24f18cdb (branch claude/fnv-l6-render-w5, base claude/fnv-l6-render-w2), working tree = that commit plus the measurement changes below.

Gate command line, as l1.py/verdict.py runs it (cwd = checkout root):

```
GATE_CACHE=<cache> GATE_OUT=<out> GATE_COMMIT=<commit> python3 -I ports/fnv-nx/verify/gate_objstub.py
```

Result: `objstub: red 1754/1779 25 findings: V 21, wine-fixme-in-real-body 4` (functions judged 1779 in 69 objects of 1 root; certified 1754). Gate not changed. The per-finding list below comes from the same `root_check` + `objstub.judge` calls the gate runs (the gate's own JSON keeps only the first 50 ids and the class counts).

## Totals (global, whole root)

- Findings: **25** (of 1779 functions).
- Class 1 (T, trap/abort path): **0**.
- Class 2 (V, vacuous body without a constants row): **21**.
- Wine rule (`wine-fixme-in-real-body`, reported by the same gate): **4**.

## Measurement conditions (not product state)

- Root build: `recipe.py build --root ports/fnv-nx/render/d3d9`: green, 69 objects, stages run 151 hit 0, compiler external gcc-msvcmodel b4057704. The committed root is RED: `dev/ffp_prog.cpp` fails (`../gen/ffp_glsl.gen.h` missing, the gen_glsl.py step is not in the recipe; w2 inbox). For this measurement that one tracked file was dropped from the index (not committed) so 69 of 70 units were built and judged. Its functions are NOT in the 1779.
- `include` names ports/fnv-nx/shim/include, render/shaders/include and render/backend, which are not admitted roots; for the run they were added to verify/admit.json locally as class tool entries (not committed).
- l1.py runs gates in a clean worktree of a commit; here the gate ran in the working tree (same code, same recipe).

## Findings (file, symbol, class, what)

| file | symbol | class | what |
|---|---|---|---|
| render/d3d9/dev/copy_misc.cpp | `NxDevice9Ex::DeletePatch(unsigned int)` | V | result v0=None x0=('K', 2289436780) x1=None; no constants row |
| render/d3d9/dev/copy_misc.cpp | `NxDevice9Ex::DrawTriPatch(unsigned int, float const*, _D3DTRIPATCH_INFO const*)` | V | result v0=None x0=('K', 2289436780) x1=None; no constants row |
| render/d3d9/dev/copy_misc.cpp | `NxDevice9Ex::DrawRectPatch(unsigned int, float const*, _D3DRECTPATCH_INFO const*)` | V | result v0=None x0=('K', 2289436780) x1=None; no constants row |
| render/d3d9/dev/copy_misc.cpp | `NxDevice9::DeletePatch(unsigned int)` | V | result v0=None x0=('K', 2289436780) x1=None; no constants row |
| render/d3d9/dev/copy_misc.cpp | `NxDevice9::DrawTriPatch(unsigned int, float const*, _D3DTRIPATCH_INFO const*)` | V | result v0=None x0=('K', 2289436780) x1=None; no constants row |
| render/d3d9/dev/copy_misc.cpp | `NxDevice9::DrawRectPatch(unsigned int, float const*, _D3DRECTPATCH_INFO const*)` | V | result v0=None x0=('K', 2289436780) x1=None; no constants row |
| render/d3d9/dev/d3d9_adapter.cpp | `NxDirect3D9::RegisterSoftwareDevice(void*)` | V | result v0=None x0=('K', 0) x1=None; no constants row |
| render/d3d9/dev/d3d9_adapter.cpp | `NxDirect3D9Ex::RegisterSoftwareDevice(void*)` | V | result v0=None x0=('K', 0) x1=None; no constants row |
| render/d3d9/dev/d3d9_exports.cpp | `D3DPERF_SetMarker` | wine-fixme-in-real-body | the function calls fnvnx_wine_fixme and does more than a simplified body |
| render/d3d9/dev/d3d9_exports.cpp | `D3DPERF_SetOptions` | wine-fixme-in-real-body | the function calls fnvnx_wine_fixme and does more than a simplified body |
| render/d3d9/dev/d3d9_exports.cpp | `D3DPERF_SetRegion` | wine-fixme-in-real-body | the function calls fnvnx_wine_fixme and does more than a simplified body |
| render/d3d9/dev/d3d9_exports.cpp | `DebugSetMute` | V | result v0=None x0=None x1=None; no constants row |
| render/d3d9/dev/d3d9_exports.cpp | `nx_d3d9_export_count` | V | result v0=None x0=('K', 11) x1=None; no constants row |
| render/d3d9/dev/dev_misc.cpp | `NxDevice9Ex::GetNumberOfSwapChains()` | V | result v0=None x0=('K', 1) x1=None; no constants row |
| render/d3d9/dev/dev_misc.cpp | `NxDevice9::GetNumberOfSwapChains()` | V | result v0=None x0=('K', 1) x1=None; no constants row |
| render/d3d9/dev/pvw_api.cpp | `NxDevice9::ProcessVertices(unsigned int, unsigned int, unsigned int, IDirect3DVertexBuffer9*, IDirect3DVertexDeclaration9*, unsigned long)` | wine-fixme-in-real-body | the function calls fnvnx_wine_fixme and does more than a simplified body |
| render/d3d9/dev/state_rt.cpp | `nx_iid_of(IDirect3DSurface9*)` | V | result v0=None x0=('A', ('sec', 59, 0)) x1=None; no constants row |
| render/d3d9/dev/state_rt.cpp | `nx_iid_of(IDirect3DBaseTexture9*)` | V | result v0=None x0=('A', ('sec', 57, 0)) x1=None; no constants row |
| render/d3d9/dev/state_rt.cpp | `nx_iid_of(IDirect3DIndexBuffer9*)` | V | result v0=None x0=('A', ('sec', 58, 0)) x1=None; no constants row |
| render/d3d9/dev/state_rt.cpp | `nx_iid_of(IDirect3DPixelShader9*)` | V | result v0=None x0=('A', ('sec', 54, 0)) x1=None; no constants row |
| render/d3d9/dev/state_rt.cpp | `nx_iid_of(IDirect3DVertexBuffer9*)` | V | result v0=None x0=('A', ('sec', 60, 0)) x1=None; no constants row |
| render/d3d9/dev/state_rt.cpp | `nx_iid_of(IDirect3DVertexShader9*)` | V | result v0=None x0=('A', ('sec', 55, 0)) x1=None; no constants row |
| render/d3d9/dev/state_rt.cpp | `nx_iid_of(IDirect3DVertexDeclaration9*)` | V | result v0=None x0=('A', ('sec', 56, 0)) x1=None; no constants row |
| render/d3d9/dev/x1_validator.cpp | `NxShaderValidator9::AddRef()` | V | result v0=None x0=('K', 2) x1=None; no constants row |
| render/d3d9/dev/x1_validator.cpp | `NxShaderValidator9::Release()` | V | result v0=None x0=('K', 1) x1=None; no constants row |

Class V reading: the function's result is a constant (`x0` K/A = constant/address) with no constants row; W: calls fnvnx_wine_fixme but does more than a simplified body (cites Wine 9.0 simplified bodies only).
