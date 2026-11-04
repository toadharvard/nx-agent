### J-XXXX | 2026-11-04 | stream: render-lit | status: wip | parent: render-014

* **did** - d9 families: hand-written GLSL for the engine's lit D3D9 programs, on their own interface
  (registers, samplers, declarations per fnvnx_dk_layout.h), so each replaces the translated program of a draw one
  for one (RENDER_NATIVE.md §4.5).
  - `render/native/capture/d3d9_families.{h,cpp}`: classifier by interface (CTAB names/registers/counts, dcl
    inputs; no names, hashes or game files), `nx_d9_pair` picks the variant.
  - Families `shaders/d9_adts.*` (ADTS10: ambient + directional + 2/3/5 points, normal map, specular; variants p2s,
    p3, p5), `d9_envmap.*` (cube reflection), `d9_fogpass.*` (fog colour pass), `d9_epilogue.glsl` (glsldk
    driver UBO: alpha test, fixed fog). All compile with uam to DKSH in the package build.
  - `d3d9_exec.cpp`: a recognised pair draws with its d9 family; otherwise translated. `nx_render --families
    all|none`, `--draw-log`, `--raw` (float target after each draw, `nxgl_read_rgba32f`), `--force-visible`.
  - `render/verify/shaders/d9_parity.py` (metric + per-family error), `d9_synth.py` (synthetic NXD9 per package).
  - `tests/s6n/test_d9_families.cpp`: 24 checks, no game data.
* **measured** - METRIC lit draws native / all lit draws in the real captures: 0/27 -> 27/27 (docmitchell 27/27;
  menu has no lit draws). Per-family parity error (max abs on the float target, pixels beyond 2/255 and 1e-3 rel):
  - docmitchell: adts 21 draws, 2.97 M px written, 1.5e-5, 0 bad. Its envmap/fogpass draws write no pixel
    (off-screen, also with depth off), so they are checked on synthetic captures.
  - synthetic 012 and 019 (8 pairs x 6 seeds, every accepted program): adts 72 draws 4.9e-4 / 0 bad, envmap 12
    draws 2.4e-4 / 0 bad, fogpass 12 draws 2.4e-4 / 0 bad, per package. The residual is one fp16 ULP.
  - No regressions: d3d9_state/front/harness unchanged, 19/19 goldens, nx_render 47/0, n1 113/0,
    nx_render_robust ok.
* **found** - Under Wine the game loads package 012/013 (ps_2_x, 960 of the captures' programs), not 019. The lit
  programs it draws (SLS2022/2031, SLS2025/2034, SLS2050/2057, SLS1017/1030) have one register map in all 16
  packages, so the families use fixed registers and the classifier checks them.
* **found** - SKIN20xx vertex programs share the ADTS constant table and inputs but write other outputs; they
  never pair with an ADTS pixel program (light count / specular bit differ), so they stay translated.
* **decided** - Interface recognition over hash lists: works for any package and the console's engine path, and
  keeps hashes and names of game files out of git. Fixed registers instead of per-program defines: one map in all
  packages, so one DKSH per variant and no run-time compile.
* **next** - The Lighting30 (SM3, ps_3_0) programs of package 019 (skin, hair, eyes, parallax, shadowed lights):
  they need a capture on the 3_0 path (the Wine run picked 012) or synthetic coverage; then the SKIN2x family.
