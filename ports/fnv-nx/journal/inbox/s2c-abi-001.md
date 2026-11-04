### J-XXXX | 2026-11-04 | stream: S2c-abi | status: wip | parent: J-0149

* **note** - Branch `claude/fnv-s2c-abi`, from the integration branch at c1039e7. The metric is measured offline with
  the new `tools/link/abireplay.py`, which replays link/c0d6c71516f6's `abi.tsv` and layouts in 3 s. With `--tree`
  (the link's own prepared tree, private `t/7b3b20231f0d`, tree hash efb9808e) undecorated C names are replayed too;
  2 rows differ from the link because the replay reads layouts without the tree's POD check. The next farm link confirms the
  figures. That link also re-probes every layout, because of the pod2 rule.
* **did** - Bridge-gap pairs, measured as S1b's abiclass BRIDGE pairs (unique src/address/reference):
  **2,221 -> 35** (replayed with the tree). It reached 32, then V1 refuted the e2dbe73 rule (VA-U1..U4) and the
  figure went back to 70. Per-call-site types (vacall, below) bring it to 35. V2's va_arg-width check (VA2-VA4) then raises it to an
  honest 75. Format literals (below) bring it to 57. The commits:
  - `e0ed8d5` The synthesized vtable/body objects (`<host>#vtables`) asked for layouts under their own name. 985
    probe rows failed and 1,283 manual rows were never decided on bytes. They now use the host unit. 2,221 -> 943.
  - `0750425` Variadic pairs (`abi.varargs_check`). AArch64 Linux passes anonymous arguments like named ones. A
    variadic reference needs no bridge when the definition reads only named parameters: the retail loggers
    0x005b5e40, 0x0040fbe0, 0x00a29680 and 0x00c3c220 are `return 0`. A variadic definition needs none when the
    reference's tail is integers, pointers or doubles. Narrow tails are extended in place. 943 -> 246.
  - `94a3d29` The link stage builds and passes the port's own overrides by default (`OVERRIDE=auto`, through
    `tools/link/overrides/build.sh`). Before, farm links ran without the 0x00703c00 logger replacement. 246 -> 100.
  - `e2dbe73` Unprototyped `int f(...)` references, and both-variadic pairs where the definition names more: 32-bit
    integer and pointer parameters taken from the anonymous words. 100 -> 65.
  - `8e9ad7a` `T *__restrict` was classified as an unknown type. It is now a pointer (0x00a6dbd0 Negate and 3 more).
    65 -> 58.
  - Tests that run under qemu-aarch64: `test_varargs.py` has 12 verdicts and 8 executed pairs; dropping the
    extensions fails 2. `test_bridges.py` executes 9 bridges, one new. `test_bridges.execute` is shared.
* **did (S13b hand-offs)** - S2c's rows in `reports/s13b-causes`:
  - **import-slot-called 11 -> 0** (`4d723fb`; its comment is fixed in db26c31, whose message cites the pre-rebase id e3db828). The rows were VirtualAlloc and
    VirtualFree (c10) and EnterCriticalSection (c11). resolve binds these calls to the API, and the harness traces
    the native API at its IAT slot address. That is `runtime-equiv`, as report.py already said. A slot of another
    import stays `import-slot-called`. Correction: 4d723fb's message names only VirtualAlloc and VirtualFree.
  - **split-global 35** breaks down as follows:
    - 3 are real link bugs, fixed in `a8a32dd`. tesform.cpp defines `DAT_011c54cc` and
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
    - 3 are `??__E` initializers passing a stub as the second argument of GlobalObject2 0x0045cfe0. They are fixed
      in `d38ce7e` (`text-addr`). The generated initializers pass immediates as arrays placed at the number
      (`g_011c5a4c_arg1` at 0x00a00000, `g_011d3450_arg1` at 0x006e6e6e). Such an array or char label now binds to
      an absolute symbol at that address, which is the x86 value. 11 unit references on link/c0d6c71516f6. The
      metric goes from 70 to 67.
    - The rest are 4 call-target rows, 1 call-args, 1 call-count and 2 other mem-writes.
  - **native-undefined 291**: 170 of the functions call names the trial link leaves undefined. These are mostly
    out-of-line inlines such as `NiPointer<T>::operator T*` (13 functions), NiRefObject refcounts, destructors and
    ListBox accessors. The pristine objects call them out of line, which is S2d's `exe-addr` class. The farm link
    ran without the exe, so that class never bound anything. S2d's `74e3a4a` (link_job FNVNX_EXE) is not merged
    yet. The per-function list is on the private branch `reports/s2c-abi` (`native_undefined.tsv`, for S2d; `s2c_records.tsv` groups the vptr rows for S2c-vt and S13). The other 121 functions call nothing undefined: their names went to
    trap stubs, or the record is older than the link.
* **did (fuzz/causes/0bd144db1464, S2c 1,186 rows)** - These are vptr-unpaired 677, native-stub-data 448,
  native-address 111, thunk-binding 11 and split-global 8. vptr-unpaired is S2c-vt's.
  - **thunk-binding 11**, fixed by the commit "thisadj". MSVC enters an override of a secondary base's virtual with
    `this` at that base. The exe's vftables show which addresses (`tools/link/thisadj.py`, COL offset d > 0
    only). The 11 rows' `this` offsets of 24, 60, 136, 164 and 168 match the exe exactly. The address map now
    names `__fnvnx_msvc_<addr>`, aliased to GCC's `_ZThn<d>_` thunk, for the 79 such addresses on
    link/c0d6c71516f6 that have one. `__fnv_<addr>` stays the primary entry for native callers.
  - **For S13**: the harness enters a function at `__fnv_<addr>`. For these 79 addresses it should enter at the
    address map's link symbol, otherwise the rows remain.
  - **For the orchestrator**: resolve reads the exe through FNVNX_EXE / PLAYER_EXE (S2d's link_job wiring).
  - native-stub-data 448 and native-address 111 are not grouped yet on these heads.

* **V1 refutations, fixed (commit "V1 VA-U...")**
  - VA-U1..U4: an `int f(...)` call site's long long, double, struct by value or struct{float} gives other words
    than the integer definition reads. Definition parameters taken from anonymous words are manual again.
  - ABI-R1: a pointer with several cv-qualifiers was not recognized.
  - OVR-F1: the Link fingerprint now differs between auto, none and a table.
  - OVR-E1: an empty OVERRIDE means off.
  - RPL-B1: abireplay strips `#bodiesN`.
  - V1's test_s2c_s4_batch.py now reports "refuted: none" here.

* **did (vacall)** - Variadic references are decided by their call sites, as the orchestrator asked.
  - The plugin `tools/link/vacall/fnvnx_vacall.so` (-fsyntax-only, in `fnvlink.py probe`) records each call's argument
    classes after promotion.
  - When all calls of the reference in a unit agree, resolve 2 compares the definition with that fixed signature
    and bridges it, with abi2 bytes for a double or long long read as two ints. The bridge key includes the
    call-site signature.
  - test_vacall.py: V1's shapes plus mixed and disagreeing call sites, 4 pairs executed under qemu.
  - Replay: 13 units, 26 s, 70 -> 35.
  - Left: call sites that disagree (per-site bridges would need relocation retargeting), aggregates by value, and
    2 unrecorded units.
  - **For the orchestrator**: the link farm needs gcc-13-plugin-dev-aarch64-linux-gnu. Without it, vacall prints
    "no plugin" and those pairs stay manual.

* **V2 VA2-VA4, fixed** - A fixed reference against a variadic definition was `same` whenever the tail was
  int, pointer or double, but the body's va_arg widths are in neither signature.
  - The plugin now records each variadic definition (`VADEF`: none / escape / its va_arg classes).
  - The tail must share one width class with every va_arg; otherwise the pair is manual. Without a record it is
    manual too.
  - Replay: 35 -> 75. The 40 newly manual pairs are callers whose definition formats through vsnprintf: the
    0x00703c00 override 15, 0x00406d00 13, plus 0x00a305b0, 0x00aa0020 and others. The format string decides those
    widths.
  - V2's test_s2c_varargs.py: 3 ok. V1's batch: refuted none.

* **did (format literals)** - This is for the callers of definitions that forward their va_list to vsnprintf, as
  the orchestrator asked.
  - The plugin records each call's string-literal arguments for the requested callees (`FMT` lines).
  - `vacall.parse_format` reads MSVC printf conversions.
  - Exact widths at every site need no bridge. One shared regrouping (two ints read as `%lld` or `%f`) gets an abi2
    bridge keyed by the conversions.
  - The 0x00703c00 override declares `va=escape` (overrides.tsv column 5, carried by build.sh).
  - Replay: 75 -> 57 (109 units recorded).
  - Left: 0x00406d00's 13 callers pass a computed format.
  - test_vacall: %s, %lld and %f executed under qemu, a non-literal format and disagreeing sites manual. Without
    the bridges, 4 checks fail.

* **V2 T1 (thisadj), fixed** - A member reference through another class than the definition's (a shell declared
  at the subobject) now binds to `__fnvnx_msvc_<addr>`. At link-head that is 4 addresses and 7 references, e.g.
  0x008d4870 -> 0x008805f0. V2's test_s2c_thisadj.py: ok; test_bindings has the same check.

* **V2 X2 (text-addr), fixed** - A char label whose address the unit passes to a call stays extern-text (a trap),
  never the raw x86 address. The case was 0x00c1d8b0 `_atexit(&UNK_00fdd3b0)`, the static's x86 exit thunk.
  Arrays and labels stored as data keep the value. test_bindings has the case.

* **measured** - abireplay on link/c0d6c71516f6 with the override table: rows bridge 4,732, manual 79, same 1,995,
  x86-inconsistent 3,276 (before: bridge 4,183, manual 2,632, x86-inconsistent 3,267). The 9 new x86-inconsistent
  rows are pairs that were `manual` before and are now decided on bytes.
* **open / next**
  - The 32 pairs left (with the tree): 0x00cd7160
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
