# w16: recipe rules A (sources + patch), B (built tools), 3m (root mirror), fixtures

Branch claude/fnv-l1-contract-w16 (from origin/claude/fnv-nx-clean), merged origin/claude/fnv-l1-contract-w4 and origin/claude/fnv-l1-contract-w15 (variants, link kinds, link.roots).
Specs: verify/RECIPE.md "Sources and patches (rule A)", "Tool roots", "Built tools (rule B)", "Fixtures"; TOOLS.md gate_fixtures.py.

## What changed
- Rule A: `"sources": [{external, dir, patches, include}]` (any root class). Units `<ext>:<path>`; stage `patch:<root>[:<ext>]` (GNU patch pinned in env.json, verify/patchtree.py). `"isystem"` (paths inside an external's `at` only). `include` inside the root is refused (quoted relative include). Bare `-print-prog-name` answers resolved via `unitstage.prog` (programs: list, then PATH; none = red naming it).
- Rule B (own commit range, not extended): `generated` tool `{root, output}` = an ELF built by another admitted root's recipe in the same store; tool roots (class tool + recipe): host x86_64 drivers, static `ld`, ELF64 x86-64 check.
- 3m: stages see ONE mirrored `/src` tree: the root at `/src/<root>/...`, each include dir at `/src/<its repo path>`; only declared files exist. `unitstage --prefix`. No sandbox.py change.
- Fixtures: `"fixtures": {unit: {gate, expect: "red", finding}}`; built, never a unit/object/link input; gate `fixtures` (gate_fixtures.py, gates:24) runs the named gate with GATE_FIXTURES=1 (gate_build.fixture_view) and requires its result `findings` [{id, code}] to be exactly the declared code. objstub emits findings. L3b must add `findings` to forms/fpexact (message sent to session_01KmtZKSQpF5Su9oG96Kbe4A). The fpexact bare-float-cast fixture itself is L3b's to add when the gate exists (not on my branch). `fixtures` is NOT in gate_admit REQUIRED_PRODUCT (lead's call).
- env.json pins: patch 2.7.6-7build3; libegl-dev, libgl-dev, libglx-dev 1.7.0-1build1 (GL_PROBE in envcheck). env_id 2e84e07908a7e7c13e5ae5ec90a0cd086f44d5de7a3d83b22ebbc4fc77e16988 (was 5c31a460e5019680380000dd1bc19c3599f432a90699ac8de555e5fcb49c39d3).
- Toy tests moved to quoted relative includes (`"inc/toy.h"`, `"gen/gen.h"`), in test_recipe, test_linkstrict, test_objstub, test_link, test_gate_machine.

## Tests (real env.json tools, no doubles)
test_recipe 80/80, test_fixtures 14/14, test_link all passed, test_linkstrict 54/54, test_gate_machine 10/10, test_gates 50/50, test_objstub 0 failed.

## L1 line at my head
See the final line in my message to the lead (written after the last verdict run; this file is not edited after it, to keep the head stable).
