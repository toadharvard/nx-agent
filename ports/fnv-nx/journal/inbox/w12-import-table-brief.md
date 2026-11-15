# Brief for w15: the import bridge table, one path (from w12, L1 item)

Branch: claude/fnv-l1-contract-w12L2 @efce93818 or its successor (cand2 merged). You have the verdict env.

## State
- bridgefacts has the stage `gen:2` (contract/bridgegen.py) with output BRIDGE (bridge.tsv): names = /ext/manifest/manifest/sys_names.tsv, specs = /ext/wine-specs/specs (platform's pins), name table + va record from bridgefacts/crt, extra rows = the stage outputs com_rows.tsv and d9bridge_rows.tsv.
- loader: modules.py reads /uses/ports/fnv-nx/bridgefacts/BRIDGE (the pinned /ext/manifest bridge.tsv and the loader's manifest external are gone).
- Not built anywhere yet (the host of w12 cannot run the recipe). First step: build bridgefacts and loader, fix what the build shows.

## Remaining second paths (delete in the same commit as their replacement)
1. verify/gate_imports.py `bridge_of`: derives the table itself from `<root>/imports.in.json` (names, specs, extra = COM only, extra_other, name_table, va). Replace: read BRIDGE from gate_build.ensure("ports/fnv-nx/bridgefacts")["outputs"]["BRIDGE"]; stage B (`bridgetable.derive(syms=...)` judging the root's symbols against the rows, imports.judge) stays but runs over the SAME rows; delete the imports.in.json declaration and platform/imports.in.json, and the gate's declaration reader (IN_KEYS, rp). Check that the platform's table includes the d3d9 rows now (the old declaration had COM only; d9 rows were `extra_other`).
2. verify/gate_contract.py + verify/contract_inputs.json `"bridge": "manifest/manifest/bridge.tsv"` (read via P2(ext, doc["bridge"]) for `iat.bridge_rows` and the --bridge arg of gpa_names): the pinned file. Replace with the BRIDGE output of bridgefacts; delete the key.
3. Any other reader of a pinned bridge.tsv: `grep -rn "bridge.tsv\|manifest/bridge" ports/fnv-nx` (non-journal).

## Check (the acceptance)
- gate imports and the loader root green (a build at one commit, run on the verdict env).
- Byte-equal bridge rows: the BRIDGE output equals the rows the old paths derived (gate_imports' derive for platform, loader stage A at 5e3f9edf2 with COM+D9 rows) for every row they both had; the difference (if any) is only rows the old path lacked, listed with the reason.
- verify/tests/test_loader_tables.py, test_gate_imports.py, test_bridgefacts.py pass (test_bridgefacts fails on resolve_input({root,path}) before your work: that form does not exist in recipe.py; make the test use the declared form).

## Report
One message to L1: commit, global row counts (BRIDGE rows, per bind class), the list of deleted paths.
