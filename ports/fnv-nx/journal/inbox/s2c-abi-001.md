### J-XXXX | 2026-11-04 | stream: S2c-abi | status: wip | parent: J-0149

* **note** - Branch `claude/fnv-s2c-abi`, from the integration branch at c1039e7. The metric is measured offline with
  the new `tools/link/abireplay.py`, which replays link/c0d6c71516f6's `abi.tsv` and layouts in 3 s. Undecorated C
  names cannot be replayed without the tree; those rows keep the link's verdict. The next farm link confirms the
  figures. That link also re-probes every layout, because of the pod2 rule.
* **did** - Bridge-gap pairs, measured as S1b's abiclass BRIDGE pairs (unique src/address/reference):
  **2,221 -> 58** (replayed). The commits:
  - `c9ea866` The synthesized vtable/body objects (`<host>#vtables`) asked for layouts under their own name. 985
    probe rows failed and 1,283 manual rows were never decided on bytes. They now use the host unit. 2,221 -> 943.
  - `8a8a6c8` Variadic pairs (`abi.varargs_check`). AArch64 Linux passes anonymous arguments like named ones. A
    variadic reference needs no bridge when the definition reads only named parameters: the retail loggers
    0x005b5e40, 0x0040fbe0, 0x00a29680 and 0x00c3c220 are `return 0`. A variadic definition needs none when the
    reference's tail is integers, pointers or doubles. Narrow tails are extended in place. 943 -> 246.
  - `15be800` The link stage builds and passes the port's own overrides by default (`OVERRIDE=auto`, through
    `tools/link/overrides/build.sh`). Before, farm links ran without the 0x00703c00 logger replacement. 246 -> 100.
  - `505c184` Unprototyped `int f(...)` references, and both-variadic pairs where the definition names more: 32-bit
    integer and pointer parameters taken from the anonymous words. 100 -> 65.
  - `4bbb171` `T *__restrict` was classified as an unknown type. It is now a pointer (0x00a6dbd0 Negate and 3 more).
    65 -> 58.
  - Tests that run under qemu-aarch64: `test_varargs.py` has 12 verdicts and 8 executed pairs; dropping the
    extensions fails 2. `test_bridges.py` executes 9 bridges, one new. `test_bridges.execute` is shared.
* **did (S13b hand-offs)** - S2c's rows in `reports/s13b-causes`:
  - **import-slot-called 11 -> 0** (`e3db828`, comment fixed in the next commit). The rows were VirtualAlloc and
    VirtualFree (c10) and EnterCriticalSection (c11). resolve binds these calls to the API, and the harness traces
    the native API at its IAT slot address. That is `runtime-equiv`, as report.py already said. A slot of another
    import stays `import-slot-called`. Correction: e3db828's message names only VirtualAlloc and VirtualFree.
  - **split-global 35** breaks down as follows:
    - 3 are real link bugs, fixed in `d40c0c5`. tesform.cpp defines `DAT_011c54cc` and
      `fnv_00485a70::DAT_011c54cc` (and 011c54d0) in one object. objcopy renames one old name per new name, so the
      second stayed a strong private `.bss` copy. It is now `__fnv_<addr>_d<n>`, defined as the address symbol in
      aliases.ld. test_bindings has a check that fails without the fix. On link/c0d6c71516f6 only tesform shows this.
    - 26 are function-local statics plus their guards. The saved records say `local-static`, a harness artifact:
      only their own function names them. Re-run records (`_now`) seem to drop `arm_local_static` (for S13b).
    - 6 are decomp globals that no unit places at an image address: `g_pTileItemFreeList` (menu.cpp), `g_w1` and
      `g_wv` (blk_00de0000_o2), `g_c2` and `g_cn` (blk_00e60000_o2), and 0x00616890/0x006167d0. They are for S1.
  - **native-address 111 + native-stub-data 70**: 181 records, read from the cache histories.
    - 149 are vptr stores. The oracle writes an image vftable. The native writes either its own vtable (107, a
      kind-V pairing the harness does not see) or the stub of an unemitted `_ZTV` (42). These belong to S2c-vt
      (exe_pair, synthesis) and S13 (compare against the V map). I did not touch vtable code.
    - 21 are native reads of a named data stub (an unbound variable).
    - 3 are `??__E` initializers passing a stub as the string argument of GlobalObject2 0x0045cfe0.
    - The rest are 4 call-target rows, 1 call-args, 1 call-count and 2 other mem-writes.
  - **native-undefined 291**: 170 of the functions call names the trial link leaves undefined. These are mostly
    out-of-line inlines such as `NiPointer<T>::operator T*` (13 functions), NiRefObject refcounts, destructors and
    ListBox accessors. The pristine objects call them out of line, which is S2d's `exe-addr` class. The farm link
    ran without the exe, so that class never bound anything. S2d's `74e3a4a` (link_job FNVNX_EXE) is not merged
    yet. The per-function list is on the private branch `reports/s2c-abi` (`native_undefined.tsv`, for S2d; `s2c_records.tsv` groups the vptr rows for S2c-vt and S13). The other 121 functions call nothing undefined: their names went to
    trap stubs, or the record is older than the link.
* **measured** - abireplay on link/c0d6c71516f6 with the override table: rows bridge 4,732, manual 79, same 1,995,
  x86-inconsistent 3,276 (before: bridge 4,183, manual 2,632, x86-inconsistent 3,267). The 9 new x86-inconsistent
  rows are pairs that were `manual` before and are now decided on bytes.
* **open / next**
  - The 58 pairs left: about 35 are undecorated C names the replay cannot read. Then 0x00cd7160
    `Triangle::FUN(...) const` (a variadic member with a struct result: decomp junk), 0x00703e80 ShowButtonDialog,
    0x00ba8ec0's variadic ctor, Havok `hkBool` by value vs `char`, and `?:` typedef'd aggregates.
  - The next queue items are reg-shift and convention moves.
* **for the orchestrator** - Run a farm link at this branch's head to confirm the replayed figures. `OVERRIDE=auto`
  changes the Link fingerprint, so every link reruns once.
* **for S2c-vt** - `tools/pipeline/build.py` Link.run now handles `OVERRIDE` itself (auto/none/table). Your
  LINK_OVERRIDES=render change can append a second `--override` there, but fnvlink takes only one `--override` (the
  last wins). Merge the tables or make the option repeatable.
* **checked** - tools/link: test_link, test_bridges, test_varargs, test_override, test_bindings, test_startup and
  test_vtables ok. tests/s13: test_causes, test_difffuzz and test_fuzzcache 0 failures. tests/s9 test_provenance and
  tests/s10 test_tools ok. Rules were not touched, so tests/s1 and tests/s11 were not run.
