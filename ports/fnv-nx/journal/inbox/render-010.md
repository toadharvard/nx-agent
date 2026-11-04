### J-XXXX | 2026-11-04 | stream: V6n-render | status: wip | parent: render-006

* **did** - Shader parity on every program of the owner's 16 `shaderpackage*.sdp` (local only; nothing from them is
  in git), `fd2e626`:
  - `render/verify/shaders/sdpfuzz.py` translates each program with S6b's `sdp2dksh`. For 4 random seeds (constants,
    textures, vertex inputs), the original bytecode runs on the oracle (`v6n_nine`) and the translation on
    `v6n_gl`.
  - Vertex programs are read back through a generated pixel shader into a float target, plus depth.
  - A failure only counts beyond the oracle's own sensitivity (inputs nudged by 1e-5).
  - Scene-format additions: generic vertex declarations, 8 samplers, zero-filled unwritten VS outputs.
  - Output: `build/v6n/sdpfuzz/results.json`, schema `v6n-sdpfuzz/1` (RENDER_VERIFY §3.7), per program:
    status, cause, max colour error, max depth LSB, bad px.
* **found** - Totals over 15,791 programs (8,160 PS / 7,631 VS), all parsed:

  | result | programs |
  |---|---|
  | translated and run | 11,161 |
  | translation failures (S6b), centroid unsupported | 4,614 |
  | translation failures (S6b), relative addressing | 16 |
  | pass | 8,386 |
  | fail | 2,775 |

  Of the fails: 2,257 are D3D9 numerics (they pass under Nine's numeric rules), 128 use cube samplers (the harness
  cannot feed them yet), 154 are oracle NaN and 236 are open. By unique bytecode (843): 621 pass, 170 numerics,
  10 cube, 16 oracle NaN, 26 open, of which 20 are GRASS wind vertex shaders. Package pairs 010/011, 012/013,
  014/015, 016/017 and 018/019 are byte-identical.
* **found** - S6b translator findings P2-P5 (RENDER_VERIFY §7; synthetic tests in `tests/v6n/vs/findings`, reported,
  not counted):
  - P2: vs < 3.0 colour outputs not clamped to [0,1];
  - P3: `log` -> log2(x) instead of log2(|x|);
  - P4: normalising a zero vector gives NaN where Nine gives 0, and rcp(0) differs;
  - P5: the 4,630 translation failures (centroid, relative addressing).
* **found** - C69 (S6n's family semantics) falsified in 7 of 12 cases. `tests/v6n/claims/c69_families.py` runs S6n's
  families on S6n's GL backend, and the game's original program for the same true pass (757 TILE, 66 NOLIGHTING,
  569 SKY, 147 LANDAD, 2 ZONLY_At) on Nine, with synthetic inputs. It reads `FNVNX_SDP` and skips without it.
  - Match (5): tile, tile scroll offset, nolight tex / texvc / vc.
  - Filed as failing tests: tile_texscroll_scale, tile_alphamap, tile_vc, nolight_falloff_curve,
    sky_vertex_weights, land_light, depth_alphatest. S6n is asked to fix them.
  - lit is not checked: the SM3 PS do not translate (P5), and LightData is not mapped yet.
* **found** - Oracle quirk O9: softpipe drops row-0/column-0 pixels when it clips triangles with varying w; the
  vertex fuzz skips them.
* **found** - The exe cannot settle configured vs emitted passes. The SLS pass array (`DAT_011fdff8`), the Lighting30
  table and the tile constants lie in the zero-filled part of `.data` and are filled at run time, so this needs a
  run-time capture (E1).
* **verified** - The render machine checked the commit trailers and that no game-derived file is in git.
  `tests/v6n/run.sh` at `fd2e626` gives "selfcheck PASS, open defects: yes" (the 7 family defects; per V6n, not
  re-run here).
* **metrics** - programs=15791 translated=11161 pass=8386 fail=2775 translate_fail=4630 unique_bytecode=843
  unique_open=26 c69_match=5/12 defects_open=7
* **request** - Orchestrator / S6b: fix P2-P5, centroid above all (4,614 programs, all SM3 lighting PS).
* **next** - Cube and volume textures in the scene format; root-cause the 26 open unique programs; the lit/SM3 check
  after P5; the pass-marker join on real data once engine families emit markers.
