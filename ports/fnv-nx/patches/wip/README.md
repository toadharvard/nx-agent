# patches/wip - parked, NOT applied

s1b_patches.load_sites() reads only the `*.s1b` files directly in patches/, so nothing here is applied. These are the
hand-written pop callers at boot distance 5-6 (four worktree agents), parked at the owner's pause.
- hand_7 (12 callers) and hand_8 (13 callers): finished.
  - MSVC identical: hand_7 1,247 functions, hand_8 644. test_patches OK.
  - GCC: the new errors are `FNVNX_EBO2 does not name a type`, from the stale base tree (EBO2 postdates it).
    Re-run against a fresh tree.
  - hand_8's matrixvt sites live only in headers: run msvc_check with `--only` covering
    src/sdk/facegen/matrixvt_h_unit.cpp.
- hand_5 and hand_6: unfinished (the agents stopped at the rate limit). Not checked.
To merge, move each file to patches/, merge its renames ($SP/hand_<k>_renames.tsv on the S1b machine, or re-measure
with msvc_check --renames), run test_patches, msvc_check and gcc_check, and re-measure abiclass.
