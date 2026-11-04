# S6n requests to the orchestrator (linker, prepare rules)

Each request has a failing test under `tests/s6n/`; the request is done when the test passes
(`python3 -I tests/s6n/test_requests.py`, run from ports/fnv-nx). S6n never edits `tools/link/` or `tools/rules/`.

## R1. `fnvlink.py resolve --override render/native/overrides.tsv`

* **R1a** - table format: tab-separated `address`, `symbol`, `ABI/notes`; `#` comments. The current
  `load_overrides()` reads column 3 as an object path; S6n's column 3 is free text. The objects come from R1c.
* **R1b** - `address` may be `vslot:<MSVC vftable symbol>:<slot>`: the override replaces the function the image
  vftable holds in that slot (the linker knows the vftable contents from the image; S6n cannot see them, the exe is
  not in its container). Equivalent to writing that function's address, plus a check that the slot exists.
  `load_overrides()` returns such rows as `('vslot', vftable, slot)` in place of the integer address (or resolves
  them when the image is available).
* **R1c** - `--override-lib <archive>`: the archive that defines the override symbols (S6n's
  `libnxrender.a`, built by `render/native/n1/build.sh`, planned); rows whose symbol the archive lacks are an error,
  rows marked `N1-pending` in the notes are skipped with a count in the report.

Test: `tests/s6n/test_requests.py::test_r1_vslot_rows` (parses `render/native/overrides.tsv` through
`tools/link/fnvlink.load_overrides` and expects the vslot rows back). Fails today: `int('vslot:...', 16)` raises.

## R2. Image vftable dump for the renderer classes

A TSV `<vftable symbol> <slot> <function address> <decomp symbol>` for `??_7NiDX9Renderer@@6B@`,
`??_7NiDX9RenderState@@6B@`, `??_7TileShader@@6B@`, `??_7BSShaderNoLighting@@6B@` (from the image, as
`tools/link/vslots.py` already reads it), committed as `render/native/vftables.tsv` or attached to the journal
entry. It settles the "inferred" slots of overrides.tsv (95-97, 101, 102) and the candidate implementation
addresses in `N1_PLAN.md` §2.

Test: `test_r2_vftable_dump` (expects `render/native/vftables.tsv` with slots 95-111 of NiDX9Renderer).

**Status (S6n, round 4): done locally from the user's own exe.** `python3 -I render/native/tools/exe_vftables.py
<FalloutNV.exe> NiDX9Renderer NiDX9RenderState TileShader BSShaderNoLighting --units ext/fnv_decomp/build/units.json
--anchor TileShader=0x00bca760 --anchor BSShaderNoLighting=0x00bc9a50` writes the gitignored `vftables.tsv`
(NiDX9Renderer and NiDX9RenderState from RTTI; the two BSShader classes have none and are found from an anchor slot
plus the constructor's vptr store). It settles the slots: 95-102 as expected except 97 (Do_DisplayFrame is the
engine's deferred-release function, not the Present at 0x00e7ccd0), and 107 = 108 (one function for shapes and
strips). overrides.tsv now binds every row by address and records the slot as `vft=<class>:<slot>`, which
`tests/s6n/test_overrides.py` checks against the dump when it exists. The round-3 MSVC-object attempt (no object
emits `??_7NiDX9Renderer@@6B@`) is superseded. `test_r2_vftable_dump` passes where the dump exists.

## R3. Prepare rule NATIVE1: drop units

`tools/rules/native1.py <units.json> <render/native/drop_units.txt> <out units.json>`: copies units.json without the
units listed (one source path per line, `#` comments, glob patterns allowed), and reports the `@fnv` addresses
those units defined that no override row covers (they would become unresolved). S6n's list for N1 is
`render/native/drop_units.txt` (empty until the N1 overrides cover the whole NiDX9Renderer surface).

Test: `test_r3_native1_rule` (runs the rule on a three-unit units.json fixture and expects one unit dropped).
