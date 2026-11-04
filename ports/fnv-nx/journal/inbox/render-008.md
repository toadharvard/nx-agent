### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: render-007

* **did** - S6n round 5, the N1 menu path (`72f12a0`, `271d44c`):
  - Engine textures (`n1/engine_textures.cpp`). The engine's `NiDX9SourceTextureData::LoadFromFilename` stays; its two
    D3DX calls are replaced. 0x00ee6e22 (image info) answers from the DDS header. 0x00ee6e1c (create texture) builds a
    native image through the DDS loader and the nx_d3d9 formats. The engine gets a `NativeTexture` laid out like an
    `IDirect3DTexture9`; CPU access (LockRect, GetSurfaceLevel) is refused. New state-cache rows, slots 55-57:
    SetTexture, GetTexture, ClearTextureUsage.
  - The geometry's alpha, material and tile-shader properties are read at +0x9C/+0xA4/+0xA8. Tint is the tile tint
    with alpha = tile x material alpha. The alpha property's blend and alpha-test settings become the draw's D3D9
    states and are restored afterwards.
  - Menu background as a native effect: `Renderer::RenderMenuBackground` plus a `menubg` shader. Its engine row
    `Setup` 0x00c04730 stays pending until the image-space dispatch is overridden.
  - Host test: a synthetic 3-tile menu in the engine's memory layout, drawn only through the stubs, with textures
    created through the D3DX overrides (checker background exact, alpha 0.5 blend, alpha test), plus the menu
    background over a rendered target. It passes on GL and as the ILP32 guest.
  - `overrides.tsv` now has 24 active and 4 pending rows. Claims C82-C84, RENDER_NATIVE.md §2.4.
* **found** - Locally, against the game's own ISMENUBG shader from the owner's packages, 63 of 64 pixels match within
  2/255; the one exception sits on a tone-texel boundary. Nothing from that check is committed.
* **found** - Risks for the link and runtime:
  - The texture-binding row 0x00e88a20 takes a pointer where the decomp declares an int (both 32-bit on ILP32). The
    linker's signature check may want an MSVC signature in column 4.
  - The tile-shader property is assumed for main-menu geometry; any other property type would be misread.
* **verified** - Re-run by the render machine at `271d44c`: `make -C tests/s6n` 527/41/29, 0 failed; n1 76/0;
  18/18 goldens; `test_overrides.py` OK. The ILP32-guest half (76/0, per S6n) was not re-run here.
* **metrics** - n1_checks=76 active_override_rows=24 pending_override_rows=4 menubg_local_match=63/64 claims=C1-C84
* **next** - Image-space dispatch override (then the MenuBg row). Then texture scrolling, the tile scissor and
  stencil flags, fonts if they are not tile geometry, skinning, cube/volume textures. A real menu texture check needs
  the BSA archives. The orchestrator's full link with these rows is the decisive check.
