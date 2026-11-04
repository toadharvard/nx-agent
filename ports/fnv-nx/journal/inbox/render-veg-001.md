### J-XXXX | 2026-11-04 | stream: render-veg | status: wip | parent: J-0137

* **claimed** - render/native/CLAIMS.md family rows on branch `claude/fnv-render-veg`: trees (SpeedTree
  branch/frond/leaf/billboard), grass (GRASS*), particles/precipitation/bolt/beam/decal/blood/vfog. Owner
  render-veg, status open, path stays "skipped -> Translated" until each family passes the scene check against
  Translated and costs no more GPU time. Merge of the rows: render machine.
* **plan** - in order of real019 GPU cost:
  1. grass (4.93 ms on real019 handheld 720p, 3.83 of it vertex: GRASS vso ~377 Maxwell instructions x 2,000
     vertices x 300 draws). Native `grass.vert/frag.glsl`: instance data per draw, one vertex stream (position,
     uv, colour), alpha test through the compile-time epilogue permutation (`__ea`), no runtime compile.
  2. trees (1.27 ms): SpeedTree leaf / branch / frond / billboard.
  3. particles and decals.
* **metric** - per family: draws on the native path / total in the captures, the parity error against Translated,
  and the family's GPU ms on real019 handheld 720p.
  - before: grass 4.93 ms, trees 1.27 ms, particles see report.json; native draws 0 / 0 in both captures.
  - The two captures (menu_11640, docmitchell_13000) contain no grass, tree, particle or decal draws (57 draws
    each: menu tiles; Doc Mitchell's hall wall). The draw ratio and the scene check need an exterior capture
    (e.g. Goodsprings at noon, with grass, Joshua trees and dust particles). Request to the orchestrator / S5-data.
