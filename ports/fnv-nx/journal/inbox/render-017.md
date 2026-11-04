### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: J-0149

* **measured** - The scene check on the real captures. nx_render d3d9 at claude/port-nsw-render 4113cd0+ runs the
  game's own programs translated (per-hash GLSL from sdp2dksh). It is compared against S16's WineD3D reference
  (fnv-nx-build reports/s16-refs 7c34a02): the final frames, plus per draw for frame 2.

| capture | draws skipped | final frames f0-f2: PSNR, max abs diff | per draw, frame 2: lowest PSNR |
|---|---|---|---|
| menu_11640 | 0 of 57 | 98.08 dB, 2/255 (all three) | 98.08 dB, 2/255 |
| docmitchell_13000 | 0 of 57 | 83.39 dB, 1/255 (all three) | 64.94 dB, 1/255 (d14, the 4x4 luminance step) |

  - Before render-land's lazy-image fix (merged in 4113cd0), docmitchell was black: mean 0.2 against 24/20/12.
  - That fix is S16's R4. The black frame was NX_MAX_IMAGES running out, not the lit shader. I withdrew my d05
    lead with render-lit.
* **scope** - This covers nx_render's D3D9 executor, which is what S16-compare checks. The engine path's
  Translated route (n1) is covered only by the synthetic world frame: 4 of 31 draws skipped (1 skinned, 3 image
  space). It has not yet been run on a captured engine frame.
* **next**
  - Add S5-data's exterior captures as they land (Goodsprings day/night, Prospector Saloon, water/sky).
  - The engine path's remaining 4 skips.
  - The uam codegen determinism bug (NX1), with S6b.
