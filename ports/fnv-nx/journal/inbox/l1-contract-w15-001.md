### J-XXXX | 2026-11-12 | stream: L1 contract w15 | status: wip | parent: lead L1

* **did** - The guest ELF link runs only through verify/recipe.py. link/ld.py is gone (git mv to link/ldcheck.py, now a pure check).
  - Rule (RECIPE.md "Link", "Variants"):
    - `link.roots` holds other admitted product roots (or `root@NAME` pairs). Their objects follow the root's own, in list order. A cycle is a check error. A red input root makes the link red, stage `ld:<root>`.
    - `link.kind` is pie (default), exe or shared. `link.libs` is `{external, dirs, names, startfiles, endfiles}`, or `{system: [..]}` for shared libraries of env.json packages.
    - Every object is linked whole, with no gc-sections. The options are fixed by the rule and the refusal set R stays.
    - Every linked object must have the target (ELF class, e_machine) of the link emulation, from its ELF header. A mismatch is red, naming the object.
    - Post stage `ldcheck:<root>` (hermetic python, ELF symbol tables only) checks rules 1-5, writes the crt0 header words and outputs the final ELF plus the gate file `ld`.
    - `variants: {NAME: {compiler, flags}}` replaces only the keys it names. The name is in every stage key (env FNVNX_VARIANT, absent for the implicit variant). Objects go to GATE_CACHE/objects/<tree>/<root>@<NAME>/.
    - `gate_build.product_roots` now returns (root, variant) pairs, spelled `root` / `root@NAME`. build, determinism, objstub, linkstrict, argorder and contract's per-root gates quantify over them.
  - Native x86 compilers answer `-print-prog-name=as/ld` with a bare name. `unitstage.prog` resolves it on the host PATH, so an x86_64 variant builds.
* **census of removed linker calls** - link/ld.py had 2 `subprocess.run` linker calls (lines 473, 506 of the base). Now 0 `subprocess`/`os.system` in link/ outside tests (git grep, and a test asserts it).
* **tests** - test_link 38/38 (new: two roots, weak undefined, ABS32 in .rodata, missing FNXG, no crt0, cycle, determinism of two cold builds, one per kind pie/exe/shared, libs.system, wrong-target link, variants: aarch64 LP64 + x86_64 variants into two images, wrong-target variant link, variant name in keys).
  - test_recipe 53/53, test_linkstrict 54/54, test_objstub 220/220, test_plugins 40/40, test_gate_contract 69/69, test_argorder 4/4. All 0 failed.
  - The test_link entry is in tests/oracle.json.
* **L1 line** at head f90aa27 (merged w4 4ece886 and clean e35b870): `L1 green 25/25 gates, 244.4 s, over_budget False`.
* **Out of scope, readers (what each needs)**
  - gate_contract.py RESULTS (units / addrmap / aliases) are read by exefacts/n1 (`--units`), objfacts (`out/addrmap.tsv`), gate_data (`out/aliases.ld`) and pcrel_abs. `entries.tsv` is read by disp. They are still produced by the contract stage from the root's out dir.
  - layout.json: no reader in contract/, link/ or verify/.
* **notes for others**
  - The real crt0 header is data in an "ax" section, which objstub flags as uncovered. The toy fixtures use an "a" section. The owner of the real crt0 (L5) should move the header words to an "a" section.
  - The pre-existing red of test_argorder and test_guest here came from the private-external fetch. Not touched by this change.
* **not done (asked later by the lead / L3b)**
  - `link.libs` archives inside an external, passed as file paths, with the members ld pulls (from -Map) recorded in the link facts and judged by linkstrict/objstub: not implemented. The existing `link.libs` {external, dirs, names} already searches external archives but does not record the pulled members.
  - Variants: gates not listed above (gate_contract's per-root image gates) take the pair spelling through `quantify`, but they have run only on roots without variants here.

### Follow-ups 2026-11-12 (head after the merge of trunk aaa709c8)
* **did**
  - Archive members pulled by ld are recorded (`link_archives` in objects.json, from the ld map) and judged by linkstrict and objstub as objects: a stub member is red, an undefined member symbol is a red link.
  - The objects.json vs object-symbols check: a link is red when the recorded defined global/weak symbols differ from what the objects define, read with ldcheck's own reader.
  - A tool root named in `link.roots` is refused.
  - Cross-root outputs: `outputs` and `uses` (RECIPE.md "Outputs and uses"). The stage key holds the content hash; the output is at /uses/<root>/<name>. Red cases are check errors.
  - Generated `sources`: units listed by a generate stage as `<external>:<path>` lines; the content is in the unit keys; a changed list adds only the new units. Red cases: stage `sources:<root>`.
* **tests** - test_recipe 58, test_link 45, test_uses 16 (new), test_gate_contract 109, test_argorder 4, test_plugins 40, test_linkstrict 60, test_objstub 222; all 0 failed.
* **L1 line** - `L1 green 27/27 gates, 291.3 s, over_budget False` (verdict run alone on the machine).
  - Budget margin is 9 s. The selftests gate is the long pole (test_plugins ~104 s, test_recipe ~66 s under --jobs 3).
  - A verdict run while other test runs share the machine goes red (timeouts or "No space left on device" from /tmp test scratch).
* **notes**
  - `farm/setup_verdict_env.sh` fails at the hashed-wheel step on this machine (`pip ... -r -`: "Could not open requirements file: -"). The z3 wheel was installed from a requirements file instead. Farm owners may hit the same.
  - Not done: a test root (class test) consuming an output, until test roots are built by the recipe. A real-root variant run waits for the first product root.
