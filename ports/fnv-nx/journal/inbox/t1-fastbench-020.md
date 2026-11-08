### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **Lockstep at function granularity (owner).**
  * 80f43568: boot_trace.py compare lists the first --max-div (50) divergences. The kinds are a value that
    differs, a call that differs, missing calls (the exe calls them, the guest does not) and extra calls (the
    guest calls them, the exe does not). Each item gives both caller chains, in compare.txt and compare.json.
  * 716ffcb4, bf6945d3: every bootloop iteration traces the stub boot on the union of the references' lists and
    compares against each one. The references are bt1's newest trace/<link12> and S18's scenario/original
    trace-coc (boot -> menu -> coc -> in the cell; 972,721 calls). --only-list filters each comparison to one
    reference's list.
  * On bt1's trace/3f7a6c35e923: 7 items, against 1 before. That guest trace is only 20 calls, because its
    discovery ran on the slow emulator.
* **Time slices and determinism.**
  * S5 2d1afaeb's timer slices were about 10x faster, and nondeterministic. 620572bc preempts at host-call
    returns instead.
  * build.py's raw boot passes --budget 0 only with an emu_guest.py that has --hc-slice, and otherwise the old
    count. 4fdd4848 had passed --slice-ms 2000, which would have re-armed the timer every 2 s; that is fixed in
    a49f72c6.
  * The farm boot jobs unset BUDGET, SLICE_MS, EMU_BUDGET and EMU_SLICE_MS.
  * The ratchet excludes the boot metrics of heads whose emu_guest.py has --slice-ms but no --hc-slice
    (2d1afaeb..6da559aa).
  * tools/farm/boot_determinism.py, and a bootloop step that runs the same stub boot twice, once per machine and
    emulator version.
* **Null device.** The stub boot runs with FNVNX_STUB_NULLDEV=1 (full_job 4b, bootloop). The ratchet keeps those
  milestones apart, as "(nulldev)" metrics.
* **Farms at 10:55.**
  * full1 4264bfd74f36 published t/4264bfd74f36: the tree fix works.
  * The fuzz farms are to be restarted.
  * The ledger exists but is empty: the persistent runners still run the runner.sh they started with. A restart
    turns on the ratchet, the FAILED-report fix and the bl<k> defaults.
  * bl1's first iteration is the cold compile. leq1 has not reported yet.
