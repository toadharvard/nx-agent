# recon2-009 | 2026-11-08 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-008

* **blocker (orchestrator, urgent)**: every full build failed in prepare at the integration head. REC1-REPL raised
  when one recon's replace `old` did not occur exactly once.
  - **class fix** (e92bc37, tools/rules/s1_compile.py): such a recon is rejected whole for that unit (no replace, no
    append, no symbols), so its rows stay visible trap stubs and the prepare goes on. Each rejection is counted
    (REC1-REJECT), printed to stderr (unit, recon dir, pair, count, start of `old`) and listed in
    <out>/build/recon_rejected.tsv (REC1-REJECTED-RECONS). tests/s1 0 failed (new cases: no half-apply, a malformed
    pair, the report); tests/s11 ok.
  - **the offender**: a prepare at aec3080 with all recon/* rejects exactly one recon, R1's
    recon/havok_common_serialize_util_hkserializeutil. STAND4 now strips the decomp's stand-in of FUN_00d48a90 that
    its pair 0 wrapped. Re-targeted (0eaab77d): pair 0 now opens the #if at the FUN_00d48760 stand-in. Each `old`
    occurs once, and the unit compiles under the game GCC flags. The guest's call still passes the options argument
    to the unit's own FUN_00d48a90(int, int, int, int).
* **lesson**: REPL recons are bound to text that later prepare rules may change (STAND1-5, HARN1). Every prepare now
  lists the recons it rejects, so a rule change shows up as a visible list instead of a stopped build.
