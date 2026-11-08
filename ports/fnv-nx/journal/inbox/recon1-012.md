### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-011)

* **did** - The void class has no open items left. Four follow-ups from recon1-011 are on fnv-nx-build
  `recon/<unit>_void`, all GCC-only REC1-REPL. Each passes try.py with all bytes equal and compiles, and objdump
  shows the value reaching the native return.
  - 0x00aa1370 (nisystem console): the exe body is `jmp _strnicmp`. It is now `int (const char*, const char*,
    unsigned)` and keeps the native name `_Z12FUN_00aa1370v` through an asm label.
  - 0x00e4a540 (hkalignscenetonodeoptions): the exe body is `mov ecx,[ecx+0x14]; jmp 0x00e3ed00`. It is now
    `int (a, b)` and tail-calls `__fnv_00e3ed00(m_14, a, b)`, with the same asm-label technique.
  - 0x00a5d2f0 (blk_00a40000_o2): it now returns the stream's vtbl[0x24](this) bool. The bsshadertextureset
    wrapper's value no longer drops here.
  - 0x008691b0 (unmapped/sleep): it now returns TryLock()'s bool (0x0078d200). The bsspinlock wrapper's value no
    longer drops here.
* **metric** - void-class real cases still open: 2 -> 0 (89 of 89 closed: 87 + 2). Across R1's three shards,
  2 names remain, both routed by the orchestrator to S2-bodies: hk3AxisSweep32CreateBroadPhase (unreferenced class)
  and matrixvt 0x00658360 (link).
