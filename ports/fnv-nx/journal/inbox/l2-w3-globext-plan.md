# globext (global-extents fact): state at the pause, 2026-11-12

Paused by the L2 lead (orchestrator order: L2 works only on items that block the platform root or the lifted roots). Nothing here is merged anywhere.

## Code (branch claude/fnv-l2-source-w3-globext)
- contract/globext.py (+ test): `fnvnx-globext/1` writer. One line per certified object: `image 0xva size gates`, sorted; overlap is an error naming both; the same object in several units is one object.
- link/globext_cv.py (+ link/tests/test_globext_cv.py): the rule on the /Z7 objects (msvc/z7/8f324ecde2a7) bound to the pristine objects by a per-object equality proof; VA from N1; size from `.debug$S` S_GDATA32/S_LDATA32 + `.debug$T` (codeview.Table.size); certified_by `n1+cv+z7eq`.
- farm/globext_job.sh: fetches the pinned exe, msvc, units.json and the four z7 shards, runs n1.py and globext_cv.py.
- Plugin-row path (not needed for this fact): claude/fnv-l2-source-w3-globext-plugin (globext_run, globext_rows) and claude/fnv-l2-source-w3-layout (fnvnx_layout `.fnvnx.objects`, w4-cc1 0989525 merged).

## Measurements
- First farm run (pristine objects, CodeView rule): 0 of 18,126 certified; the pristine objects have no CodeView. fnv-nx-build facts/globext 74fe0e3b is that empty result, not for L1.
- Local, real objects, no N1: of 2,987 units, 699 z7==pristine, 2,250 unequal (2,050 sections, 200 symbols), 38 without a z7 object.

## To restart
1. Merge/verify: the verdict of the branch head (a run was started at 9ddae252; its line is in /tmp on the w3 container only).
2. Farm: `farm/globext_job.sh OUT --range 1183000 1271a9c --probe 11b76d0 11afe88 11ace08 11ace0c`, publish OUT to fnv-nx-build facts/globext, report counts, probes, commit, tree_sha256 to the lead and L1 w18.
3. Coverage is bounded by the 699 equal units; the lead decides whether that is enough for L1 w18.
