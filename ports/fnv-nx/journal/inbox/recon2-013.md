# recon2-013 | 2026-11-08 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-012

* **trap loop** (owner order, orchestrator 05:32): 5 Sonnet workers, one row at a time. R2 reviews every result before
  it is published to fnv-nx-build recon/*. The review re-runs build_and_match on its own copy, does a gcc compile with
  the replace pairs applied, and re-reads the exe evidence.
  - Rows taken: 224 of the d1ed15e74620 top-down list (inflated). From the first batches on, the queue holds only rows
    that also trap in the clean-ish link 0540aff0d154, and typeinfo/vtable rows go to S2-bodies as one class.
  - Published: 59 recon dirs (about 49 bindings, 9 replaces, 3 bodies, 4 inline).
  - Rejected or returned by review: 9. Five per-row GCC-only vcall hacks; a devirtualized-call binding; a binding whose
    GCC operand layout differs from the exe's; an inline body where the exe has an out-of-line copy; a memcpy_s/memmove_s
    mix-up, fixed and then published.
  - Held: edits inside an unplaced, exe-divergent copy of 0xd0f300 (hkpcontinuoussimulation.cpp: the orchestrator is to
    decide on redirecting its caller); a GCC-only user dtor that would change a type's triviality; bindings that would
    hand the exe function a wrong `this` or wrong argument registers.
  - Class notes to S2-bodies, about 15 classes:
    - vtable probe name hiding (closed by vtables.ELAB) and the prelude vs units with their own std::exception;
    - template dtor pairing, pure-virtual BaseFormComponent with bogus units.json addresses, an `__imp_` name inside an
      image vftable, vtable aliases;
    - runtime before units.json for std::locale/bad_cast, MSVC STL internals in units' own namespace std;
    - ADDR_IN_NAME missing `g_target_<hex>`.
* **public fixes** (claude/fnv-recon2):
  - 55c71259: vtables.py joins host units with '|' (paths with spaces were torn apart); imports.tsv names slot
    0x00fdf3d4 D3DXSaveTextureToFileA.
  - 91a41063: fnvlink GAME_RENAMES __errno_location -> _errno.
  - 02577e28: REC1-RETARGET. recon.json `retarget_functions` [{address, old, new}] moves a placed function's recipe
    symbol when a recon fixes its decoration (EQ's 0x008d1e50 float -> pointer return, recon/actor_retptr).
* **list on the clean link** 97deacd38aec: s2d/list/97deacd38aec (aa26b484, topdown.tsv).
  - 252 stubs (181 function, 71 data): s2-bodies 157, recon 95. 158 statically reached.
  - The loop now works the 95 recon rows in rank order.
* **infra**: match_recon's per-recon decomp worktrees filled the disk (156 x 160 MB). Cleaned; the review now removes
  each one after use.
