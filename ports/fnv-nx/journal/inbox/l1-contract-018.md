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
- bootsock A2..A4 kept REMOVE under L4, though L5 classed them as console behaviour (maybe ASM-004 aliases); RULES A7 (ASM-029) a possible ASM-004 alias.
- L2 labels only partly registered; L3b not_mine items (L3a) and the L5 FACT items (10) are not entries; 'RTTI-scan' dropped (no mention).
- Verification: assumptions gate green; oracle green; admit green 404/404; verify/tests/test_gate_assumptions.py passes (fails: 0). gate_pure on contract/link/rules/lift: not re-run alone (a run including verify/ was red on 29 files, verify is not a pure root; gate_assumptions.py and the test use subprocess for git). selftests (verify farm contract link rules lift): 43 of 47 pass; failing test_envcheck (known local MISMATCH), test_gates, test_objstub, test_recipe: NOT investigated whether pre-existing or caused by my comment edits (objstub.py, recipe.py, gates.json); check first.

Update: re-run of the 3 failing selftests: test_gates fails on the local env mismatch (/usr/include tree), test_recipe on 'No space left on device', test_objstub passes alone (0 failed). None come from the assumptions edits.

Final (w10 finish), measured at code commit 8a01fda (trunk 08f6ea30 merged; CLAUDE.md unchanged from trunk):
- The `pending` field is gone from schema, gate, tests, docs. The 47 pending records (L2 6, L3b 28, L5 13) are deleted. Also deleted: the seven records numbered 47 to 53 (L4 time-zone range, kernel codes, bootsock A1..A4, env pin): the trunk reworded those texts into facts, so no file cites them any more (rule 4 holds for every record).
- "L6A" is in LEADS. Rule 7 (id blocks, from the lead's addition): ids below 200 free, from 200 in the owner lead's block (first lead of a joint owner): L3a 2xx, L3b 3xx, L4 4xx, L5 5xx, L6 6xx, L6A 7xx, L2 8xx. In the gate docstring, TOOLS.md, VERDICT.md; tested. Records are ASM-001..046 (all below 200).
- Records: 46. OWNER 9 (ASM-001..009). REMOVE 37 by owner lead: L1 11, L4 14, L2 4, L3a 3, L6 2, L5 1, L1+L3b 1, L3b+L4 1. REMOVE by removed_by form: gate 3, fact 0, planned 12, no gate yet 22, OPEN 0 (without an existing gate: 34).
- Mentions: 88 certified of 93. The gate is RED on 5 mentions of trunk text that only a new record can certify (not invented here):
  (a) "the `names` plugin ran on every native unit" (contract/gate_call.py:22, verify/RULES.md:37, verify/TOOLS.md:83) - L2/L1 (names plugin);
  (b) toolchain/names/fnvnx_names.cc:81 "the assumption: the warning is not suppressed for a declaration" - L2;
  (c) verify/RECIPE.md:90 "the declaration `lifted_by` names the lifter is the owner's (admit.json is reviewed)" - L1/L4.
  Each needs a record (id below 200 for L1, 8xx for L2) or the text reworded as a fact by its owner. (The two "Stated assumptions" headings of TOOLS.md/bootsock.py I reworded to "Stated rules of the model": the trunk had turned A1/A2 into cited facts.)
- Env: setup_verdict_env.sh left libc6-dev-i386 and libclang-18-dev uninstalled; I installed exactly the env.json pins by apt (same versions). envcheck: OK.
- L1 (verdict, env OK, commit 8a01fda): 22 of 24 gates green, 153 s. Red: assumptions (above) and selftests 50/52: test_argorder and test_guest fail identically on the trunk 08f6ea30 in this env (the private external gcc-msvcmodel cannot be fetched: "could not read Username for github.com", no fnv-nx-build access in this container). test_gates failed only on my branch (its expected gate list lacked `assumptions`); fixed in 8a01fda. test_objstub and test_recipe pass in this env. test_gate_assumptions: 0 failed.

Final 2 (w10 finish, lead's decisions applied), measured at code commit 6066375 (trunk f1059af merged):
- Added by the lead's decisions: ASM-050 (L1, planned: gate datamodel w17; cited in gate_call.py, RULES.md, TOOLS.md), an L2 record for the -Wmismatched-tags premise (its id is retired since; see the registry). RECIPE.md "lifted_by" is stated as a check (no lifter file on the trunk yet: `lifted_by` must be a tracked .py of an admitted tool root; recipe.py checks it); test_plugins.py has three new red cases (untracked file, non-.py, file of the product root); 5 of 5 plugin tests groups pass.
- Records: 48 = 9 OWNER + 39 REMOVE. REMOVE by owner: L1 12, L4 14, L2 5, L3a 3, L6 2, L5 1, L1+L3b 1, L3b+L4 1. By removed_by: gate 3, fact 0, planned 14, no gate yet 22, OPEN 0.
- Mentions: 92 of 98 certified. Red on 6 mentions that arrived with the trunk (new text of others, no record, none invented): contract/hive.py:17 "regsites.tsv is complete over the exe and the modules", contract/imports.py:13 "objects.json lists every global, weak symbol", verify/TOOLS.md:274 and :360 (copies of those two), verify/RECIPE.md:64 "units.json is ..." and verify/recipe.py:318 "unit_facts is produced from the exe/decomp facts by the generator, not by hand".
- L1 (verdict at 6066375, env OK): 24 of 26 gates green, 208 s. Red: assumptions (above) and selftests 55/57 (test_argorder, test_guest: need fnv-nx-build access, identical on the trunk).

Final 3 (w10, trunk e35b870 merged), measured at code commit 2f1bbe2:
- The 6 trunk mentions and the 2 gate_call clauses have L1 records: ASM-051 (regsites.tsv complete, owner L1+L5, planned: gate that every Reg* site's key and value-name are statically resolved), ASM-052 (objects.json lists every global/weak symbol, planned: recipe link check w15), ASM-053 (pristine cl names are the exe's contract, planned: namegate L2), ASM-054 (one entity per decorated name link-wide, planned: a link check; the xc gate judges static initializers only and does not prove uniqueness). The unit_facts mentions went away with the trunk's own rewrite (e35b870).
- Records: 52 = 9 OWNER + 43 REMOVE. By owner: L1 15, L4 14, L2 5, L3a 3, L6 2, L5 1, L1+L3b 1, L1+L5 1, L3b+L4 1. By removed_by: gate 3, fact 0, planned 18, no gate yet 22, OPEN 0.
- Mentions: 96 of 96 certified; assumptions gate GREEN.
- L1: 25 of 26 gates green, 180.5 s. Red: selftests 54/56 (test_argorder, test_guest need fnv-nx-build access; identical on the trunk).
