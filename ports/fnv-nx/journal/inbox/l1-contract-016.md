# l1-contract-016: contract gates wired into the verdict

Branch `claude/fnv-l1-contract-w6`. `verify/gates.json` now runs, after `determinism`, nine gates through `verify/gate_contract.py --gate NAME`:
n1, n2_data, n2_call, n2_vcall, xc, xunit, rtti, pcrel, bodysel (the stages of `contract/verdict.py`, run once per input set, cached in
`GATE_CACHE/contract/<key>`). `recipe.fetch_externals` was extracted from `Job.externals` (same behaviour) so the gates fetch externals the same way.
Inputs come only from `verify/contract_inputs.json` (`fnvnx-contract-inputs/1`): the product root and content-pinned externals (commit + tree_sha256).
Missing or invalid input = RED, universe 1, item `inputs`, with the reason. Today the file is absent, so all nine are red.

| gate | inputs | owner | status |
|---|---|---|---|
| n1, n2_*, xc, xunit, rtti, pcrel, bodysel | product root build (objects, ELF); externals: exe, units.json, pristine MSVC objects, universe.tsv, addrmap.tsv, aliases.ld | L1 (inputs: L2 for addrmap/aliases and universe, L3 for universe.tsv) | wired, red: no contract_inputs.json, no product root |
| reach (16) | exe + module call-graph facts | L1 | on `claude/fnv-l1-contract` (`gate_reach.py`); not copied |
| disp, abs, pcrel (link) | link dir, R3 universe | L1 | on `claude/fnv-l1-contract` (`link/gate_disp.py`, `gate_abs.py`, `gate_pcrel.py`); not copied |
| imports (4) | reachable-import manifest | L5 | `claude/fnv-l5-platform`: `platform/manifest/gate_imports.py` |
| forms (6) | PACK (external liftpack, forms.tsv), PROOF (certificate dir of proof/prove.py, private) | L3a | enters with L3a's merge (`claude/fnv-l3a-lift` @73a708c): `{"name":"forms","argv":["python3","-I","ports/fnv-nx/lift/tools/gate_forms.py","<PACK>","<PROOF>"],"result":"forms.json"}`; honestly red now. Expressing PACK and PROOF: not as argv paths. Use the external mechanism: a wrapper like gate_contract (inputs file with `external` entries, commit + tree_sha256, `recipe.fetch_externals`) resolves them and passes the fetched dirs to gate_forms.py. gate_forms.py itself needs no change |
| d3d9total (7) | tree only (render/total, render/d3d9) | L6 | enters with L6's merge (`claude/fnv-l6-render` @3fa8b8f1): `{"name":"d3d9total","argv":["python3","-I","ports/fnv-nx/render/total/gate_d3d9total.py"],"result":"d3d9total.json"}` |
| R7 (8) | equivalence tool, no gate file yet | L2 | `claude/fnv-l2-source`: `tools/equiv/r7.py`; enters with L2's merge once it writes fnvnx-gate/1 |

GATE_OUT check (L6): `verdict.py` sets `GATE_OUT=<per-gate dir>`, cwd = clean worktree, plus GATE_COMMIT and GATE_CACHE; this matches what the
d3d9total gate reads. Two things for L6: the gate calls gcc/g++ -m32 from PATH, which is not the pinned compiler of env.json (it must use the
recipe's compiler or the check moves to the recipe machinery); its MojoShader clone arrives via FNVNX_SHADERS_DEPS, which must become a
content-pinned external, else the gate is red.

## Open inputs

1. `contract_inputs.json` with the externals: exe (FalloutNV.exe), `units.json`, pristine MSVC objects dir, `universe.tsv`. Owner: L1/L2 publish them in fnv-nx-build and pin commit + tree_sha256.
2. `addrmap.tsv`, `aliases.ld`: link-stage outputs that `recipe.build` does not produce. Owner: L1/L2 publish as an external until the recipe emits them (minimal extension: copy them next to constants.tsv in the objects dir, as `constants` and `contract_names` are).
3. A product root in `admit.json` (needs `oracle` of class 10 too). Owner: L4.
4. Not verified: certified verdict (envcheck reports a /usr/include MISMATCH); nothing ran against real exe data; the cold external fetch was not exercised.

## Part 2: link gates and classes A-G

- `abs`, `disp`, `reach`, `reachlink`, `gpa` are wired through `gate_contract.py --gate NAME` (inputs: `entries`,
  `reach_requests`, `images`, `dynsites` in `contract_inputs.json`; a missing key is red naming the key).
- L3a `forms`: declare PACK (liftpack, forms.tsv) and PROOF (prove.py certificates) as externals (commit +
  tree_sha256), not paths. Enters with the owner's merge.
- `gate_admit.REQUIRED_PRODUCT` now names every gate of VERDICT.md, classes A-G included. No product root can be
  admitted until each exists in gates.json (rule 3), so the trunk stays without one until the owners merge.
- Open owners: A (w4 branch), B, C, D, E (w8 exepin.py), F, G have no gate yet.

## Part 3: contract_inputs.json

`verify/contract_inputs.json` declares six content-pinned externals of fnv-nx-build (cold fetch by commit and tree_sha256 checked, all six match):
exe (gamedata/exe 0948da1e), redist (gamedata/redist), msvc (msvc/pristine/87eb9373a0bb 64d64a8b: obj/u*.obj), facts (l3a-lift/facts 163ec389:
universe.tsv, ranges), manifest (l5-platform/manifest 2ac3091b: reach_requests.tsv, dynsites.tsv), inputs (new branch
l1-contract/inputs/e1ca6b02d60d 18d39fbb: units.json from t/e1ca6b02d60d, addrmap.tsv and aliases.ld from link/e1ca6b02d60d, entries.tsv from the
l3a pack f764786639b5). All 14 gates: red, one reason: ports/fnv-nx/platform is not a product root of admit.json (no product root, no recipe-built
objects or ELF). Gaps: the addrmap/aliases/units are of link e1ca6b02d60d (old pipeline), the entries of a later pack: a consistent set needs the
recipe to emit addrmap and aliases (L1/L2) and a product root (L4). game_prefix "game" is a placeholder for the root's layout (L4). exepin (E): w8.
Old measured counts on link e1ca (old stand-in pipeline, l1-contract/mp4 gates_e1ca): data 61 rows, call 1419, vcall 208, xc 12; bodysel
must turn each into LIFTED.

## Part 4: the universe is the admitted product roots

The `inputs` external (l1-contract/inputs/e1ca6b02d60d, an old-pipeline set) is no longer pinned and is removed from `contract_inputs.json`;
`root`, `game_prefix`, `units`, `addrmap`, `aliases`, `entries` are gone from it. Five true externals stay: exe, redist, msvc, facts, manifest.
- The 14 contract gates quantify over `gate_build.product_roots` through `gate_build.quantify` (one function; `NO_ROOT` is also the detail of
  `build`): no product root = universe 0, green; an admitted root that lacks a link, `game`, objects or an input is red `<root>:inputs` with the reason.
- Per-root inputs are read from the root's out dir (beside objects.json), names in `root_results`: units.json, addrmap.tsv, aliases.ld, entries.tsv
  (disp). The recipe/link emits none of them yet: red for the root naming the file, owner L1 link stage (units.json: exe unit table, L2/L3a).
- game prefix: new optional build.json key `game` (recipe.py check, RECIPE.md); required by the contract gates of a root that links, no default. Owner L4.

## Part 5: the image set is derived

`images` and `no_range_facts` are removed from contract_inputs.json (and its schema check); `ranges` {exe, module with %s = stem} says where range facts are.
`contract/images.py image_set(exe, dirs)` (one pure function on modules.PE, which gained `delay_dll_names`) = transitive closure of normal + delay-load
import directories from FalloutNV.exe, names resolved case-insensitively against externals exe and redist; not found = platform import (gate `imports`,
L5), not an image. Assumption: dynamic LoadLibrary names belong to `gpa`. Used by gpa, reach, reachlink (gate_contract.images_of).
On the real externals it yields: falloutnv.exe, galaxywrp.dll, galaxy.dll, d3dx9_38.dll, binkw32.dll, libvorbisfile.dll, libvorbis.dll. The five old images
are all in it; two more, GalaxyWrp.dll and Galaxy.dll (static imports of the exe, found in the exe external), have no range facts: each is a red item of
gpa/reach/reachlink of every root (owner L3a/L5). atimgpud.dll, GDFFalloutNV.dll, ssce5432.dll are not static imports (dynamic loads: gpa facts), so the old
atimgpud red item is gone. Test: contract/tests/test_images.py (A imports B, B imports missing C -> {A, B}; delay-load; cycle; case; dir order).
