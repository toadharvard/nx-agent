### J-XXXX | 2026-11-04 | stream: S6n-native-render (world) | status: wip | parent: render-015

The engine path's coverage (owner rule D21): routes, the family dispatch table, the translated path, draw counters.

* **What was built** (RENDER_NATIVE.md §4.5):
  - `n1/passes.{h,cpp}`: pass id -> route, and `nx::DrawStats` per frame (engine draws, native by route,
    translated, misrendered, skipped by reason, runtime compiles).
  - `engine::FamilyRows`: one row per family. Tile and image space (menuBG) are native; nolight, lit, depth, land,
    sky and every other pass take the translated route. Family streams switch only their row (CLAIMS.md).
  - The translated path: `Renderer::RenderTranslated` runs the game's VS/PS with S6b's UBO layout. The programs
    come from the engine's NiD3DPass, the constants from the hooked device's register files plus the pass's
    constant maps. The programs load from `translated.nxsp` (NX1's NXSP container, keyed by fnvnx_shader_hash).
    On deko3d only the DKSH is used: a program without one is a skipped draw, never a compile.
  - `render/native/tools/nxsp_pack.py` is the install-time packer (`--check`); `nxsp_cover.py` is the capture
    coverage check.
* **Measured**
  - n1 world frame: 31 engine draws (GL and the ILP32 guest). Skipped or misrendered: 29 -> 4. The 4 left are 1
    skinned geometry and 3 image-space effects.
  - Captures (nx_render's D3D9 executor, translated programs): menu_11640 57 draws, 0 skipped; docmitchell_13000
    57 draws, 0 skipped.
  - Pack from `/home/user/work/glsl`: 1809 programs, all with DKSH. Both captures' 765 programs and every draw are
    covered.
  - PSNR was not measured: there is no WineD3D reference (nxd9play) in this container.
* **Not verified**
  - The engine layouts the translated path reads are the decomp's: the current pass globals, the NiD3DPass and
    program offsets, and the constant-map entry fields. They are not checked on a running engine.
* **Requests**
  - To the native boot owner: call `engine::BindImagePassGlobals()` and
    `engine::HookRendererDevice(NiDX9Renderer*)` before the renderer creates its shaders. Load
    `fnvshd:/translated.nxsp` and call `Renderer::SetTranslatedStore`.
  - To the orchestrator: `/home/user/work/glsl/index.tsv` is truncated (15502 lines, the last one cut). The blobs
    are complete (1809), so only some names from package 019 are missing.
