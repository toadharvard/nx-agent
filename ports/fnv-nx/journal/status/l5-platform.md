# L5 PLATFORM — status

Handoff read: l5-platform-handoff-opus.md @ cabcaddc.
Lead: Opus 5.5, session_01KmtZKTGM2tLun8zgoE6X44 (from 2026-11-11 22:35; replaces session_01KmtZKQxUvd3zhpaSizDjKW).
Branch: claude/fnv-l5-platform. Workers: Sonnet 5.5, branches claude/fnv-l5-platform-w<N>.

## Tasks (goal-path order)

| # | Task | Worker | Branch | Status |
|---|---|---|---|---|
| 1 | First product root: `platform/build.json` fnvnx-recipe/1, msvcmodel external (b4057704 / b60eafe7) + fnvnx-a64-msvcmodel headers (00e3d42a / 3f25152c), 8 plugins, admitted, built by recipe.py in GATE_CACHE; census of red units by cause | w10 session_01KmtZKTMZZBSRk7vPjUsu95 | -w10 (trunk a2453fe9 + L5 -w4) | doing; FreeType via L1's `sources` key (L1 -w16); plugin census by L2 w3 session_01KmtZKSTD6uibY3r7BhtJ7f on this root |
| 2 | imports + machine gates f15935a9 through MERGER | lead | -w4 | blocked by MERGER queue (accepted by L1, after its w8/w4/w5); gate selftests need the pinned env (here: no aarch64 gcc-13, 7 cases red on input error only) |
| 3a | Doubles census as a re-runnable check (`platform/manifest/check_doubles.py`, measurement; the gate stays L1's) + per-file replacement plan | w14 session_01KmtZKRTZQHQpngLDfUUZTy | -w14 | doing |
| 3b | Doubles to 0: 582 tests (s4 498, s7 84), 6 files / 87 symbols + 7 boundary double files; 7 product units not linked — tests become guest ELFs on the root's objects in the L4 boot machine | — | — | blocked by w10 (root unit set) and L4 (`boot.py test --elf --root`, L4) |
| 4a | DirectShow stubs out (old w1, reissued) | w11 session_01KmtZKRToHhAash6PcSd6rA | -w11 | doing |
| 4b | crt how=inferred; retire L5 loader copy; delete native-vorbis build path (old w2 + new C) | w9 session_01KmtZKUMZLSvzv7uGGqu6Qw | -w9 | doing |
| 4c | vorbis real decoder (old w3) | — | — | closed: libvorbis*.dll are lifted PE modules (ENV_TRUTH M1, R10в); the leftover native path goes in 4b |
| 2b | Dynamic-key Reg* sites (4 of 20, wrappers 0xc5d670/0xc5d780/0xc5d920): parameter rule over all entries + RTTI receiver/field-writer rule; keeps `machine` a measurement build until done | w12 session_01KmtZKTLfyNCVz9mSxafRwL | -w12 (draft a2fe53fb picked) | doing |
| 2c | World stop under ASan (draft 50956c3e on -w8) | — | -w8 | deferred: it patches os_posix.c, a test double that item 3 removes |
| 1b | hostapi.h = L1 -w12 6766d18a byte for byte; delete sock services 27/28/29/32/33 wrappers; dispatch = declared table; six zero-use services answered | w15 session_01KmtZKUyMdaSxX1XL93a9dF | -w15 | doing |
| 1c | lift/rt undefined symbols on the platform side (world stop exists: fnvl_host_world_stop/resume, os_world.c; host_ticks_ns, host_tls_slot, fnvnx_win32_*, trace sink) | lead | — | names agreed with L3a (fnvl_host_* prefix, rt adopts fnvl_host_world_stop/resume); owner table waits for L3a w3 rt build (blocked on L2 fnvnx_order fix) |
| 5 | FILETIME/SYSTEMTIME domain 1601..30827 (Wine 9.0 cited); Fallout.ini absent if the exe's create path is found | w13 session_01KmtZKSGk4B4w7m8NiPpsWR | -w13 | doing: Fallout.ini first (boot path); time domain only for pre-first-frame calls |

Branch names -w7/-w8 hold the predecessor's drafts (a2fe53fb regsites, 50956c3e world stop); -w6 is an accidental old pointer. New workers use -w9 and up.
Old workers w1, w2, w3 (session_01KmtZKSjF1rc6JAiuz1rvJg, _01KmtZKU6xzxGuGdkrtzwNGD, _01KmtZKSqJdpbUBBzAcvizej): failed at start on a usage limit, never ran; archived.

## Farms reporting to L5
- tc-repro session_01KmtZKSzcic61GPMwx3wqug (fnvnx-a64-msvcmodel bit-for-bit rebuild; reports to L4 and L5): running.

## Open cross-layer items
- FreeType: L1 adds recipe key `sources` (L1 w16), commit to follow.
- Guest libc/libstdc++ headers: msvcmodel gcc 13.3 + fnvnx-a64-msvcmodel newlib/libstdc++ 15.2 via -nostdinc; confirmed by L1 (22:47); L1 adds a check that the library mirror's manifest records -mabi=ilp32, -fshort-wchar, msvcmodel; risks measured by w10: libstdc++ 15 under gcc 13, -fshort-wchar vs 4-byte wchar_t library.
- REQUIRED_PRODUCT gates missing from gates.json block admission of any product root (owners per VERDICT.md: L1, L2, L3a, L3b, L4, L6).

## Global numbers (last measured by the predecessor, 7967978d; to be remeasured through the recipe)
- tests/s4 477 tests / 404 725 checks / 0 failed; tests/s7 84 / 3 237 / 0 (host Makefiles, not yet the recipe).
- bridge.tsv (private 634e4af): 1 552 rows.
- Platform root through recipe.py: not measured yet (w7).
