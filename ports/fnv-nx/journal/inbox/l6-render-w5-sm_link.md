# w5: sm_link gate (4.6) - plan and blockers

Conventions from L1: gates.d/sm_link.json (covers product-guest, result {universe, red, details}), certificates are
content-keyed recipe-stage outputs in GATE_CACHE/store (no VDIR dir), the gate reads the stage via recipe.resolve_input,
host variant of render/shaders/translator allowed in link.roots (mechanism: L1 w15c/w15d), Mesa 25.2.8 is a declared pinned external.

Universe (measured, gamedata/shaders, fnv-nx-build b545961f): 16 sdp, 15,791 entries (7,631 VS / 8,160 PS), 443 unique VS,
1,366 unique PS bytecodes, stage from the version token.

Blocked by: (1) host-target variant mechanism (L1 w15c), (2) Mesa pin as external with L4/L6A confirmation (plan 4.3),
(3) pair-class rule and form id, awaiting the L6 lead's answer. Nothing is written until these land; no stand-in checker.

## L6 lead answers (2026-11-14)
- Pair class accepted only as a proof: signature from the pinned Mesa program-interface query (inputs/outputs with name, type, array, location,
  component, qualifiers; default-block uniforms; blocks and layouts; combined-limit counts). Argument = list of linker cross-stage checks
  (GLSL 4.60 4.3.4, 4.3.5/4.4, 7.3) each a function of (sig_VS, sig_PS), cited to Mesa 25.2.8-0ubuntu0.24.04.4 linker.cpp, link_varyings.cpp,
  link_uniforms.c. A check that is not such a function => that class is linked exhaustively. Reflection JSON is not the signature.
- Variants are in the universe: FLAT_COLOR, VS_COMPLETE, link-set L, enumerated statically from the front's variant rule.
  Every stage x variant must compile (443 VS + 1,366 PS bytecodes).
- Translator host variant is w1b's (render/shaders/translator@host on 141b07580 + L1 w15c 1cac05621); I link its objects.
- Form id = sha256(VS)||sha256(PS)||L||variant bits; certificate per form id; gamedata/shaders external pinned b545961f + tree_sha256.

## First measurement (MEASUREMENT ONLY, not a verdict) - translator w1b-root 79fdbef8d (mirror f3e0e8a3), host objects 8 + lib/sha256@host
Environment: pinned Mesa 25.2.8-0ubuntu0.24.04.4 (env.json debs, envcheck OK), llvmpipe, EGL surfaceless, GL 4.5 core. Tool: scratch C programs
outside the recipe (not the gate; the gate is the tool root still to be written). Universe: gamedata/shaders b545961f, 16 sdp, 15,791 entries,
1,809 unique bytecodes (443 VS, 1,366 PS).
- translate + per-stage compile: VS 4 variants (complete x flat) 443/443 each = 1,772/1,772; PS 20 variants (flat x 5 EPI x 2 numerics)
  1,366/1,366 each = 27,320/27,320. Failures 0, refused 0.
- link: signature classes by the emission grammar (textual declaration lists): 202 VS classes x 298 PS classes = 60,196 class pairs, VS complete variant
  x PS base variant, one representative per class: 60,196 linked, 0 link failures, 0 compile failures.
Not yet covered (so this is not the gate's number): the two-way signature read (Mesa interface query), the flat variant and link-set L in the link,
the draw path's own complete-bit choice per pair, FFP stages, the backends' own GLSL rewrite. Earlier pins gave the same counts at c4990859.
