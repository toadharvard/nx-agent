# l1-contract-w18-001: universe of the contract gates for roots that are not a game tree

Rule (verify/gate_contract.py `judge`, VERDICT.md "Universe rule of the per-root gates for roots that are not a game tree"):
- game gates (n1 n2_data n2_call n2_vcall xc xunit rtti pcrel bodysel): items = functions of the units under the root's `game`
  directory. No `game` and no link: universe 0, green, no inputs file needed.
- link gates (abs disp reach reachlink gpa): items come from the linked guest ELF. `link` null: universe 0, green.
- `game` is required (red) exactly for a root with a link (on the trunk the only link is the guest ELF; there is no `kind` field).
  A root with `game` and no link is red for the game gates: its units would escape (no image judges them).
- Nothing escapes: every guest object reaches the gates through the one image that links it. `link.roots` is not in the trunk's
  recipe, so the gate does not read it; the rule rests on `link` alone. gate_build.quantify needed no change (it sums per-root results).

Tests (verify/tests/test_gate_contract.py, no doubles added; the pre-existing in-process `gate_build.ensure` replacement of that file is untouched):
neither game nor link -> green 0 (all 14 gates); game without link -> red for the 9 game gates, 0 for the 5 link gates; image root
without game -> red (14 gates); image root with game judged as before. 109 passed / 109.

Item 1 (GalaxyWrp.dll row): NOT done, see the message to the lead: contract/modules.py reproduces the mp4 modules.tsv and exports
byte for byte; GalaxyWrp.dll is a bridge dll (11 rows in the L5 manifest) so by the rule it has no row, and as a module its imports
(msvcp110, msvcr110, ...) are in no gamedata directory. Needs an owner/L5 decision.
