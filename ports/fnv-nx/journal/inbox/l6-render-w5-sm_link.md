# w5: sm_link gate (4.6) - plan and blockers

Conventions from L1: gates.d/sm_link.json (covers product-guest, result {universe, red, details}), certificates are
content-keyed recipe-stage outputs in GATE_CACHE/store (no VDIR dir), the gate reads the stage via recipe.resolve_input,
host variant of render/shaders/translator allowed in link.roots (mechanism: L1 w15c/w15d), Mesa 25.2.8 is a declared pinned external.

Universe (measured, gamedata/shaders, fnv-nx-build b545961f): 16 sdp, 15,791 entries (7,631 VS / 8,160 PS), 443 unique VS,
1,366 unique PS bytecodes, stage from the version token.

Blocked by: (1) host-target variant mechanism (L1 w15c), (2) Mesa pin as external with L4/L6A confirmation (plan 4.3),
(3) pair-class rule and form id, awaiting the L6 lead's answer. Nothing is written until these land; no stand-in checker.
