# L1 w12 (branch claude/fnv-l1-contract-w12h) head 50f7ec6a

Commits (each with a test, over base 141b07580): guest-link profile derived from the pinned runtime driver's -### (stage drv:<root>, gate guestlink, link/guest.json) ; weak-undefined (guest) red ; libs.system = any env.json-pinned package file (ld-linux incl.) ; link.loader (declared loader) ; trees fix (tool_file/tree_of via gate_admit.trees) ; link.roots per-target resolution 4055ae79 ; ldcheck linker-defined name 3b17106d ; host exe startfiles/endfiles/dynamic linker from the recipe driver's -### 50f7ec6a.

Open:
- Verdict run vd3 (head 2d486ac0, cold with externals): red, 41 gates, 1221 s (cold fetch, not an incremental figure). Reds there cascade from lib/sha256 (w14-src2 root invalid under next3b rules: build, datamodel, determinism, imports, machine, objstub) plus gates with missing results (addr32, eh, flags, order). guestlink/linkstrict exit 2 on that run because of lib/sha256. No gate-by-gate diff against the base yet: w17's log not received.
- test_link.py aborts at the variants repo (recipe.RecipeError: include 'ports/fnv-nx/toy/inc' lies in no admitted product root of target 'host'), pre-existing at the merge base; every check after that line does not run.
- Host weak-undefined dynsym (crt objects: __gmon_start__, __cxa_finalize, _ITM_*) is not output-undefined for kind exe/shared (ELF optional-reference semantics); strong undefined still must be library-defined. L1 to confirm.
