### J-XXXX | 2026-11-07 | stream: S1-rules | status: active | parent: s1-rules-002

* **D26 (a)+(b) on wip/d26** (77d0ebd, on S2-bodies' (c) 710d6ad / b1aba9b). Each part alone moves the boot backwards
  (S5 s5-003), so nothing below is merged alone.
  - (a) D22 for paired classes. S1-refs' WIP rule (fa47b9a) is adopted: r_vtable_dtor_slot, FNVNX_D22 /
    FNVNX_D22_DROP, generator tools/rules/_d22_classes.py.
    - List: generated with the owner's exe 89e7020487e8 and link 9f865b0a4037's addrmap: **147 classes**. Another
      141 paired classes are left alone because slot 0 is not a deleting destructor.
    - Published as private fnv-nx-build `recon/d22_vtable_dtor` (dd6f0a5, d22.json). recon.sh fetches it into
      FNVNX_RECON. Integration's rules do not read it, so it acts only with wip/d26.
  - (b) call-only declarations. The same FNVNX_D22, without a list, for every class that:
    - has no base and no destructor;
    - declares two or more virtuals and defines none of them in the file;
    - has a placeholder name as its first virtual (callonly_vt.py's set);
    - has no derived class in the file that defines anything.
    A statement call `p->ph(flags);` of a converted placeholder becomes FNVNX_D22_CALL (GCC: delete -> D0 at slot 1,
    else ~T() -> D1 at slot 0; MSVC: the original call). That happens only when every class in the file with a
    virtual of that name is converted.
  - Decomp-wide (4,903 files):
    - D22 462 sites, D22-CALLONLY 10,811, D22-DERIVED 110, D22-CALL 2;
    - D22-USED 570 skipped: the placeholder is called in another form, or a same-named virtual of an unconverted
      class.
    - Against S5's 11,680 file+name matches: ~10,800 converted. The rest are derived classes that define virtuals
      (they would need the undefined ~C) and those uses.
  - MSVC side unchanged: the macros expand to the original text (D22 neutral).
  - tests/s1: 0 failed (ILP32 slot checks: exe slot 2 at native +12; delete through slot 1 at +4). tests/s11: ok
    (STAND5 classified as on integration; the base of wip/d26 lacked it).
* **void_defs triage** (S5's 363, link 9f865b0a4037). For each, the instructions before every `ret` in the owner's
  exe. Private list: fnv-nx-build `reports/s1-void` (a1dac8e).
  - 28 explicit eax/al writes + 7 mixed: the exe returns a value the void definition drops. Real bugs, 35.
  - 206 tail calls: the value is the last callee's result. Natively it survives only if GCC also tail-calls; at
    risk.
  - 91 no eax write: the referencing declaration's return type is wrong, not the definition.
  - 31 undecided: no ret within the recipe size.
  - Fix as a class: the definition takes the return type the exe shows (35 + the tail-call ones whose callee
    returns). That is F1/EQ body territory; the list is the input.
* **START (farms, wip/d26 head after S2-bodies' and these commits)**:
  1. the full build + boot of that head: `tools/farm/full_job.sh <wip/d26 head> <report.md>`. It includes S5's
     stub boot, boot/stub/. Acceptance: skipped initializers not up from 1, milestones not down from 3 of 10.
  2. fuzz on that head's link: `tools/farm/fuzz_job.sh <wip/d26 head> <k> 8 <report.md>`, k = 1..8. Acceptance:
     hard not up from the integration baseline. Most keys change, since vtable layouts move.
