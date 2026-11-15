# w12 -> L1: loader generated tables (item a), branch claude/fnv-l1-contract-w12L2

Base: platform-root 84f12313f + w24 pluginfix b707cd5c4 + w12h d7bd67694 + w22 c4544e18b (all four merged; conflicts resolved toward the
platform-root side for resolve_input (_tracked_dir) and objstub errno_probe, with the pluginfix assume domains (`gen`) kept in gate_objstub).

Done (loader/build.json):
- externals exe, redist, manifest, wine: copies of the pins of contract_inputs.json (test_loader_tables.py compares them; no second pin).
- generated stages, the existing mechanism only (externals at /ext, {stage, path} chain): declareddlls.py (wine specs) -> modules.py
  (--c-out gen/modules.c, --keys-c-out gen/import_keys.c) and contract/mkidentity.py (gen/identity.c).
- Not as planned: modules -> exports -> iat as a {stage, path} chain is not expressible. A generator declares its outputs by name and the exports/imports
  files are one per image of the run. So modules.py now writes the import table itself (`--keys-c-out`, iat.c_source over the same bridge rows and the imports of the
  exe and of every image of the order: one rule, no copy); `--out-dir` is optional. iat.py is unchanged and still the tool of the iat gate.
  Without --out-dir a slot without a provider is not a stage failure (it is the iat gate's finding).
- mkidentity.py moved loader/tools -> contract/: a generator sees only its owner tree and imports exepin from contract/. L5 owns it; the move is the only change besides
  the path in boot.c's comment.
- No checked-in copy of the 8 symbols existed on this base (test_loader_tables.py asserts none is defined by a tracked loader file).
- Test: contract/tests/test_modules.py (run() writes both C files on a synthetic exe), verify/tests/test_loader_tables.py. 

Not done / open:
- I did not build the loader root (needs the private exe/redist/manifest externals and the gcc-msvcmodel runtime): the "root builds with the 8 symbols defined" check and
  the bootguest undefined set belong to a farm run; the keys of the three stages are the content of the exe external, so a changed pin changes them by construction.
- verify/declared_dlls.tsv is still tracked (gates read it); the loader no longer needs it.
