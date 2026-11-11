L1 (w10), 2026-11-11: class 18, gate `assumptions` (verify/gate_assumptions.py, verify/ASSUMPTIONS.json, format fnvnx-assumptions/1). Branch claude/fnv-l1-contract-w10, merged with trunk 55cbf861. Handover: the lead changed; this note is the full report.

Numbers (gate output, whole set): 100 entries = 9 OWNER (ASM-001..009, == items 1..9 of CLAUDE.md "OWNER, the only accepted ones") + 91 REMOVE.
REMOVE by owner: L1 11, L1+L3b 1, L2 12, L3a 7, L3b 23, L3b+L4 1, L4 20, L5 14, L6 2.
Trunk 53; pending: claude/fnv-l2-source 6, claude/fnv-l3b-lift 28 (L3b @ce152aa2; fpexact ids @eb6f0b2b), claude/fnv-l5-platform 13.
removed_by without an existing gate: 88 (planned 38, "no gate yet" 30, OPEN 20).
Mentions: 94 of 94 certified (every mention of assum*/допущени* in prose of a tracked text file cites a registry id). Gate green on the tree.

Rules of the gate (red): mention without id; id without entry; stray id; tenth OWNER entry; OWNER quote not verbatim in exactly one item; dead used_by; bad owner / removed_by (forms gate:, fact:, planned:, no gate yet:, OPEN: with text).
Choices: CLAUDE.md is read as the owner's source, not scanned; record roots (JOURNAL.md, journal/: admit.json "record") not scanned; JSON is data; .py matched on comments and docstrings only; ASM-005 quote is evening item 5, "external libraries by documentation" is its alias; A-EDGE aliased to ASM-006 (my choice).

Not done / open:
- L6 (render/ASSUMPTIONS.md @ claude/fnv-l6-render@66e2f971, ~87 pending REMOVE entries, 13 OWNER-class lines to aliases of ASM-001..008): NOT entered (only 2 L6 entries). Second commit for the new lead.
- bootsock A2..A4 kept REMOVE under L4, though L5 classed them as console behaviour (maybe ASM-004 aliases); RULES A7 a possible ASM-004 alias.
- L2 labels only partly registered; L3b not_mine items (L3a) and the L5 FACT items (10) are not entries; 'RTTI-scan' dropped (no mention).
- Verification: assumptions gate green; oracle green; admit green 404/404; verify/tests/test_gate_assumptions.py passes (fails: 0). gate_pure on contract/link/rules/lift: not re-run alone (a run including verify/ was red on 29 files, verify is not a pure root; gate_assumptions.py and the test use subprocess for git). selftests (verify farm contract link rules lift): 43 of 47 pass; failing test_envcheck (known local MISMATCH), test_gates, test_objstub, test_recipe: NOT investigated whether pre-existing or caused by my comment edits (objstub.py, recipe.py, gates.json); check first.

Update: re-run of the 3 failing selftests: test_gates fails on the local env mismatch (/usr/include tree), test_recipe on 'No space left on device', test_objstub passes alone (0 failed). None come from the assumptions edits.
