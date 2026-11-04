### J-XXXX | 2026-11-04 | stream: render-land | status: wip | parent: J-0151

* **did** - Stream render-land started on `claude/fnv-render-land` (from the integration branch, with the render
  machine's branch at 82e694f merged in: the native families, nx_render and the NXD9 tooling live there; the six
  conflicts were cherry-pick duplicates in render-owned files and were taken from the render branch).
  - Claimed in `render/native/CLAIMS.md` (stream claims): terrain, sky, water, depth / shadow map, post (image space).
  - `render/native/tools/nxd9_census.py`: draws of an NXD9 capture per family. Family by the original file name of
    the (VS, PS) pair, resolved locally from the player's shader packages (`--sdp`); a draw is on the native path when
    both stages have a hand-written replacement (`render/native/shaders/native.tsv`, keyed by original name).
  - nx_render `--native <dir>`: `<hash>.glsl` there replaces the translated original (hand-written GLSL against
    `fnvnx_dk_layout.h`); the summary adds `native shaders N, native draws M`.
* **measured** - Census of the two real captures (fnv-nx-build gamedata/captures), 3 frames each:
  - menu_11640: 57 draws, all tile (TILE1000). No render-land family.
  - docmitchell_13000: 57 draws: lit 27 (SLS2xxx; includes the 3 draws whose PS bytecode is shared with SKY.pso but
    whose VS is SLS1017, so not sky), post 27, nolight 3. Post = 9 image-space draws per frame: HDR downsample 4 (3),
    copy (2), HDR adapt, bright-pass blur 13, blur 13, HDR blend-in cinematic (1 each).
  - Metric before: render-land native draws 0 / 27; parity error n/a (no native draw).
* **did (post, item 1)** - Hand-written GLSL for the image-space family against `fnvnx_dk_layout.h` (the translated
  shaders' slot: c[] in UBO 0, the driver block in UBO 2, samplers by register, varyings by usage), so a native
  shader drops in where the translation was: `shaders/is_dk.glsl` (common: driver epilogue, D3D9 rcp, weighted taps),
  `is_quad` (+ `uv2`), `is_copy`, `is_blur` (3-15 taps, bright-pass `bp3`-`bp15`), `is_hdrdown` (`4`, `adapt4`),
  `is_hdrblend_cin`; each with an `e0` (no epilogue) variant where it applies. All compile with uam to DKSH in the
  build.py package (183 shaders, 0 runtime compiles for these). native.tsv maps 20 originals (38 programs in the
  player's 16 packages, 0 refused by the constant-table check).
* **measured (after)** - docmitchell_13000 with `--native`: render-land native draws **27 / 27** (from 0 / 27);
  parity against the translated reference over all 60 images (3 frames + 57 per-draw targets): **max error 0**, 0
  pixels over 2/255. A mutation check (rgb x 0.5 in the HDR downsample) shows 18 images over the bound, so the
  native shaders are the ones that ran. Tests: `make -C tests/s6n all scenes nx_render capture n1` all pass
  (nx_render 52 checks, 9 new for `--native`).
* **decided** - native.tsv is keyed by original file name, never by hash: hashes of the player's files are a table
  derived from game data (MACHINES.md), so the hash -> GLSL directory is laid out locally from the player's packages.
* **open** - The captures hold no terrain, sky, water or shadow-map draws (an interior and the menu). Those families
  need an exterior capture (Goodsprings, day and night, with water): request to S16 / S5-data.
