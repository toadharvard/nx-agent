# l5-platform-w13-009: registry seed as a `generated` stage of the platform root (trunk)

Branch claude/fnv-l5-platform-w13-trunk (base: w10 e28c6e98, plus the two header commits of w13-decl cherry-picked).

- platform/build.json: new stage `gen_registry_seed.py` with args `/ext/manifest/regsites.tsv` (private external `manifest`, already pinned: the
  exe-derived read sites), `/in/.../declared_registry.tsv` (public, declared machine), output `win32/registry_seed.gen.inc`.
  The tracked copy platform/win32/registry_seed.gen.inc is deleted. The generator writes the declared rows only, so its bytes do not depend on the exe.
- The hermetic python of a `generated` stage has no site packages. gen_registry_seed.py imports regsites, which imports regclass, which imported
  capstone at module level; regclass.py now guards that import like regsites.py does (the role table needs no disassembler).
- Proof so far: `recipe.py check` valid; the generator run with `python3 -I -S -B` (no site packages, as in the stage) from the pinned manifest
  commit's regsites.tsv gives bytes equal to the old line's tracked registry_seed.gen.inc. The cold/warm stage run needs the declared machine:
  this container is not it (`envcheck.py check`: tree /usr/include differs; the base image carries dev packages), so the recipe refuses to run here.
  A farm (declared env) runs: `recipe.py build --root ports/fnv-nx/platform` twice; expected: stage gen:platform:<n> built once, 0 run on the second.
- FalloutPrefs stage, NOT wired: gen_prefs_ini.py needs `--ident` ident_handheld.bin / ident_docked.bin, outputs of the render/d3d9 root (L6).
  There is no cross-root outputs/uses rule on the trunk yet (L1). Exact stage once the rule exists:
  {"tool": "ports/fnv-nx/platform/manifest/gen_prefs_ini.py", "args": ["/ext/manifest/prefs_required.tsv", "/out/win32/declared_files.gen.inc",
  "--ident", <ident_handheld.bin of render/d3d9>, "--ident", <ident_docked.bin of render/d3d9>], "inputs": [], "outputs": ["win32/declared_files.gen.inc"]}
  (`uses`: render/d3d9 outputs gen/ident_handheld.bin, gen/ident_docked.bin). Until then platform/win32/declared_files.cpp includes a file nothing makes:
  that unit is red in the platform root. Blocker owner: L1 (cross-root rule), L6 (outputs).
