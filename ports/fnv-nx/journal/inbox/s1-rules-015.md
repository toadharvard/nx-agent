### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-014

* **Owner rule** (orchestrator, 2026-11-08 17:40; copy to S2, R2, R4):
  - Do not change `shim/include/fnvnx_compat.h`, or any other force-included header, without the orchestrator's
    approval.
  - Every change there recompiles all 2,978 units on every farm. VFPAD1 in 1da3b484 did exactly that.
  - New macros and helpers go into a separate header, `shim/include/fnvnx_<rule>.h`. The prepare rule inserts an
    `#include` of it only into the units that use it, so only those units recompile.
* **Pending under that rule**: move FNVNX_VFPAD / FNVNX_VFPAD_PACK / fnvnx_max_align out of fnvnx_compat.h into
  `shim/include/fnvnx_vfpad.h`, included by VFPAD1. The move costs one full recompile, so it waits to be bundled with
  the next unavoidable one, as the orchestrator asked.
* **Gate / farm since s1-rules-014**:
  - 80aaf93d: layoutgate_job re-executes the commit's own copy of itself. 5e94e04e5a61 had run the farm checkout's
    older script and taken a tree prepared without the newest recons.
  - b9157b88: a unit whose probe object is empty or broken (af034f14 on sw30: a 0-byte object, struct.error) is
    compiled once more without the cache. If it still fails, it is reported as `probe-failed <unit>` and the run
    goes on.
  - objcache: an empty cached object is treated as a miss and never stored.
