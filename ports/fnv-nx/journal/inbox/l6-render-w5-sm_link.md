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
