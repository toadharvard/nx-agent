### J-XXXX | 2026-11-04 | stream: S1-fuzzA | status: wip | parent: J-0149

* **state** - Queue items 1-3 done once over the current caches; STAND3 committed. 190 native-null-read functions
  remain open for S1 (no family of more than a few functions left among them).
* **inputs** (local, out of git) - fuzz/cache/{1..26}of26 as of about 14:00, link/0bd144db1464, t/0bd144db1464,
  msvc/pristine/87eb9373a0bb, gamedata/exe. Records re-derived with `causes._load_campaign`.
  - Per-record lists, the effects / probe outputs and the scripts are on the private branch `reports/s1-fuzz-a`
    (`s1_fuzz_a_queue.tsv`: address, cause, category, unit, symbol, reproducer).
* **measured** - The metric is native-null-read + native-trap functions over the 26 caches. It is now 475:
  null-read 305 functions (329 records), trap 170 (171).
  - The inbox note S1-fuzzq-001 counted 185 and 70, so the caches have grown since.
* **did** - STAND3 (`15cc42e`). It is S1-fuzzB's STAND2 (`57fe267`) extended to the definitions STAND2 leaves out:
  - in-class bodies, and out-of-class or free definitions marked inline;
  - only in `/Od` units without `/Ob1`, `/Ob2`, `/O1`, `/O2` or `/Ox`, where MSVC inlines nothing;
  - only when every spelling of the name in the unit binds one decompiled address.
  - Example: in modelloader, `~_Mem_00441d20() { FUN_0044b160(this); }` -> 0x0040c110 (magicitem.cpp).
  - It has 109 sites in 55 units. The 25 largest of those units compile.
  - A fresh fuzz of the 11 modelloader null-read functions, with STAND2 on both sides: 11 diverge without STAND3,
    2 with it. So the metric goes 475 -> 466; the caches follow when the farms re-fuzz.
  - tests/s1: 0 failed. tests/s11: ok.
* **found: native-trap, 170 functions. No S1 translation bug.**
  - **154 functions, exc:7, for S2c-vt.** The native's virtual call lands on the link's `fnvnx_vslot_trap`
    (`brk #0xf5`, a virtual "defined nowhere"); 5 of them land on `fnvnx_special_trap` (`brk #0xf6`).
    - These are the "shared PCs" of S1-fuzzq-001: every unit image has one `fnvnx_vslot_trap`, so all of its
      functions fault at the same pc.
    - The exe vftable has a real function in that slot. In the port this is a crash whenever the slot is called.
    - Largest names: `MiddleHighProcess::GetCurrentPackage` (21 functions), `TESForm::GetName2` (6),
      `MiddleHighProcess::vfn_268`, `S_00c6aca0_Base::vfn_90` (4 each), and about 25 further MiddleHighProcess
      slots. The `__fnvnx_vtrap_*` -> name map is in out/refs.tsv, rows `vslot-unresolved`.
    - Fix: bind those slots to the exe vftable's function (`__fnv_<slot target>`).
  - **9 functions, exc:1 on data, for S2c-vt.** A native indirect call (`blr`/`br`) jumps into `_ZTS*`, `_ZTI*` or
    `_ZTV*` bytes: the slot is read past the end of a native vtable that is shorter than the exe's.
    - Examples: tesobjectrefr_o2_variant destructors 00c8bed0, 00c9f3b0, 00ca4da0, 00d54e60 (slot 0xa4),
      009b36f0, 004d0f40, 004a4990, 00ae7050, 006106a0.
  - **7 functions, exc:1 `udf`, for S13 (harness).** A `bl` to a noreturn named stub is intercepted and allowed to
    return. Functions: 00b43490, 00a63ae0, 00b429d0, 00b0e600, 00b0ecf0, 00b0f410, 00e89460.
    - The stubs are `__cxa_throw`, `__cxa_throw_bad_array_new_length` and the like.
    - Fix: end the run as "throws" on both machines, or mark these as soft.
  - **Proposal for S13b** - Refine these records out of the S1 cause:
    - the instruction at an `exc:7` pc is `brk #0xf5` or `#0xf6` -> `vslot-trap` (S2c);
    - an `exc:1` pc inside an object symbol -> `vtable-overrun` (S2c);
    - `udf` right after a `bl` to a noreturn stub -> `noreturn-returned` (artifact).
    - The record does not keep the pc's symbol. The runner could add it, so the refinement would not need the image.
* **found: native-null-read, 305 functions**
  - **9 functions: STAND3**, fixed (above).
  - **49 functions, for S13 (harness).** A same-unit callee has unresolved x86 relocations, so the oracle
    intercepts it, while the native runs its body. Almost all of these are TLS (`_tls_state` SECREL).
    - Example: bsaudiomanager_ob1_variant `FUN_00adb1a0`, 35 functions.
    - Fix: intercept the native entry of such a callee too, or model the TLS relocations.
  - **17 functions, for S13.** A canned result that is not 0/1 is read as bool: MSVC tests `al`, GCC tests bit 0.
    - It comes from wild or virtual calls, e.g. a slot read from an unplanted vptr: 009a8600 vfn_0100.
    - Forcing the low byte of every canned result to 0/1 removes these divergences.
    - Fix: canned results of indirect calls follow the call site's declared result type, as direct calls already do.
  - **9 functions, for S13.** A virtual override of a method a SECONDARY base introduces: MSVC's body takes `this`
    as that base subobject, the native body takes the full object, and both machines get the same pointer.
    - Examples: 00409ce0 (+4), `PlayerCharacter::GetActorValue` 0093ac20 (+0xa4, ActorValueOwner), 00d089c0,
      00d9a480, 0093ad30, 00cad820, 00df6b10, 0092d050, 00980110.
    - Detection: the function sits in an exe vftable whose COL offset is not 0. The native `this` is then x86
      `this` minus that offset.
  - **1 function**, another oracle-intercepted same-unit callee (S13).
  - **30 functions** no longer diverge on link 0bd144 and the current harness. They drop at the next re-fuzz.
  - **190 functions are open (S1).** By read set (`triage --reads`), counted over records:
    - x86-only image reads 73: mostly exe vftable slots of planted vptrs, and x87 constants;
    - same reads 67;
    - native-only buffer reads 30;
    - constant offset shifts 28 (some are the secondary-base case above);
    - x86-only buffer reads 23;
    - other 25.
    - The native fault lines are mostly virtual calls through a pointer whose vptr is 0 on the native side. The
      control flow already differed earlier, and the cause of that differs per function.
* **harness notes for S13**
  - The oracle intercepts 0x0040c110 although the native never names it. The "inlined callees" preload should
    have loaded it from magicitem's object. With STAND3 the question disappears in this case, not in general.
  - Several 2-record functions give another verdict on every replay (e.g. 004fc9e0, 00575bb0: the native now
    faults on a padded vtable copy at 0x0bc00108).
* **next** - The 190 open functions, unit by unit, starting with the largest clusters: tesobjectrefr, actor,
  highprocess, tesconditionfunctions, bstempeffect*. Re-measure when the farms have re-fuzzed with STAND2/STAND3.
