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

## Declared dlls and the image set (w18, later)
- contract/dllkind.py is the one rule (images.py, modules.py, gate_contract.images_of). Image = found, no bridge row, closure resolves to images/bridge
  dlls; declared = bridge dlls and found dlls the declared machine cannot load. The image set is total over the dll files (every image has a base,
  a dynamic LoadLibrary target included), exe-reached images first. contract_inputs.json now declares `bridge` (the manifest external's bridge.tsv), so
  the gates apply the rule.
- verify/declared_dlls.tsv: the declared machine's dlls = Wine 9.0 dlls/*/*.spec (mirror tools/wine-specs 678c780e, external `wine`, tree d21bd712),
  derived by contract/declareddlls.py. modules_declared.tsv gives `dll:provider` per missing import (wine | absent). Real result: galaxy.dll needs
  bcrypt, iphlpapi, msvcp110, msvcr110, mswsock, ws2_32, all `wine`; ssce5432 needs comdlg32 (`wine`). So every gap is L5's bridge rows.
- gpa_names.py ran with -I and could not import argflow (the gate could never have run on a root): fixed. A dynsite in a module with no image is now an
  open row, not a KeyError.
- OPEN (blocks regenerating mp4 gpa/ and reach/): the facts external has range facts only for binkw32, d3dx9_38, libvorbis, libvorbisfile. atimgpud, d3dx9_31
  and gdffalloutnv are images with no range facts, so the gpa stage leaves their 31+ GetProcAddress sites open and the reach certificates stay invalid
  (gpa-open). The old mp4 gpa/ had them derived from facts that are not in the pinned external. Owner: L3a/L2 (range facts of the three dlls). I did not
  overwrite mp4 gpa/ and reach/ with the worse result.
- OPEN: closure.tsv comes from L5's platform/manifest/pemanifest.py (not on the trunk), which takes the `contract-only` and `dynamic` dll lists as
  arguments, i.e. named lists. The role must come from dllkind (image | bridge | declared | platform); that edit is in L5's tool.
