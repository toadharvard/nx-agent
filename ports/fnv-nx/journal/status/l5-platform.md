# L5 PLATFORM — status

Lead: Opus 5.5, session_01KmtZKTGM2tLun8zgoE6X44 (from 2026-11-11 22:35; replaces session_01KmtZKQxUvd3zhpaSizDjKW).
Branch: claude/fnv-l5-platform. Workers: Sonnet 5.5, branches claude/fnv-l5-platform-w<N>.

## Tasks (goal-path order)

| # | Task | Worker | Branch | Status |
|---|---|---|---|---|
| 1 | First product root: `platform/build.json` fnvnx-recipe/1, msvcmodel external (b4057704 / b60eafe7) + fnvnx-a64-msvcmodel headers (00e3d42a / 3f25152c), 8 plugins, admitted, built by recipe.py in GATE_CACHE; census of red units by cause | w7 session_01KmtZKTMZZBSRk7vPjUsu95 | -w7 (from L1 -w4 + L5 -w4) | doing |
| 2 | imports + machine gates f15935a9 through MERGER | lead | -w4 | doing: selftests running |
| 3 | Doubles to 0: 582 tests (s4 498, s7 84), 6 files / 87 symbols of doubles + 7 boundary double files; 7 product units not linked | — | — | next, after w7 census (needs the root's unit set) |
| 4a | DirectShow stubs out (old w1, reissued) | w8 session_01KmtZKRToHhAash6PcSd6rA | -w8 | doing |
| 4b | crt how=inferred; retire L5 loader copy; delete native-vorbis build path (old w2 + new C) | w9 session_01KmtZKUMZLSvzv7uGGqu6Qw | -w9 | doing |
| 4c | vorbis real decoder (old w3) | — | — | closed: libvorbis*.dll are lifted PE modules (ENV_TRUTH M1, R10в); the leftover native path goes in 4b |
| 5 | FILETIME/SYSTEMTIME domain 1601..30827; Fallout.ini = Fallout_default.ini or absent | — | — | queued |

Old workers w1, w2, w3 (session_01KmtZKSjF1rc6JAiuz1rvJg, _01KmtZKU6xzxGuGdkrtzwNGD, _01KmtZKSqJdpbUBBzAcvizej): failed at start on a usage limit, never ran; archived.

## Open cross-layer items
- FreeType sources are in an external; recipe compiles only root units: needs a recipe rule for external source units or its own root (L1). Raised by w7 census.
- REQUIRED_PRODUCT gates missing from gates.json block admission of any product root (owners per VERDICT.md: L1, L2, L3a, L3b, L4, L6).

## Global numbers (last measured by the predecessor, 7967978d; to be remeasured through the recipe)
- tests/s4 477 tests / 404 725 checks / 0 failed; tests/s7 84 / 3 237 / 0 (host Makefiles, not yet the recipe).
- bridge.tsv (private 634e4af): 1 552 rows.
- Platform root through recipe.py: not measured yet (w7).
