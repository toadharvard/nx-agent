### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **bootloop_job.sh (farm job `bootloop`), the fast fix -> link -> boot loop.** Owner order: the boot must reach
  10/10 milestones, and a full build takes 72-100 min per iteration.
  * One persistent build directory ($FARM_HOME/bootloop/build) serves every head. Prepare is kept, or rewritten
    file by file. Compile rebuilds only the changed units. The link takes its compiles from the compile cache
    (objcache, seeded from linkcache), and the guest is relinked.
  * It runs `make guest` only: no census, raw boot, host, package or tests.
  * Then the stub boot runs on that guest with FNVNX_STUB_NULLDEV=1 (S5's --stub-nulldev, on integration since
    the merge) and EMU_TIMEOUT 480 s.
  * The report gives:
    * the seconds per iteration, against the 15 min target;
    * a step table: sources, toolchain, inputs, build, linkeq, stub boot, cache publish;
    * a stage table (ran or cached, seconds, units compiled, cache hits and misses);
    * the game milestones n/10 and the last one reached, and the stop;
    * the sidecar fnvnx-bootloop/1.
  * The ratchet measures its milestones as "bootloop: stub boot (nulldev) milestones", with a "reached" count.
* **The warm == cold check is inside bootloop.** leq1 has published no report, and no leq1 session was visible
  here. Once per machine, on the first iteration whose link took compiles from the cache, the link and guest are
  built again with an empty cache at the same paths, and the sha256 of guest.nso and addrmap.tsv are compared.
  * A pass writes bootloop/linkeq.done.
  * A FAIL fails the job, keeps link.warm and guest.warm, and is checked again on the next iteration.
  * The first iteration on a machine is cold (a full compile, about an hour). The check runs on the second.
  * Run in the bootloop, the check covers the cache that is actually in use.
* **Expected warm iteration** for a head that changes a few units: sources and toolchain about 30 s; prepare
  kept, or rewritten for the changed files (prepare.py itself about 570 s when its inputs change); compile of
  the changed units; link about 10-15 min warm (bodies and vtables were 28 s and 4 s warm here on subsets;
  resolve 38 s); guest about 95 s; stub boot at most 480 s. Every farm iteration reports the real numbers.
* Test: tests/t1/test_bootloop.sh (stand-in make and farm_boot.sh: the stages, the check pass and FAIL, the
  report, the sidecar, the ratchet).
