### J-XXXX | 2026-11-07 | stream: S1-rules | status: active | parent: J-0156

* **adopted S1-layout** (b358bc9) - claude/fnv-s1-layout has no merge base with integration; its own commits
  13bf8f7..f2ec85e (IDTOR1, EBO3, WIP STAND4) applied 3-way. The four conflicts were both-sides additions next to
  S1-refs' REC1/HUNIT1/XC2; both kept. STAND4 had no class: platform, like STAND1/2 (equiv.py, EQUIVALENCE.md).
  tests/s1 0 failed, tests/s11 ok. S1-layout-001.md is in the inbox as written.
* **bool-repr was mostly a classifier artefact** (2953a57, d19-bool, harness).
  - In shard 4 of 06f45fbb, 128 of the 161 bool-repr records had the hit byte inside a word with other nonzero
    bytes. Most were the 0x03 top byte of a planted pointer: 0x030000ec -> 0x010000ec moves the pointer, it is not a
    bool (00420600, 00420690, 0043bb80, 00449530 ...). Only 33 hit a lone byte.
  - Fix: the probe runs the case once with x86emu recording reads of planted memory (world.BYTE_READS). It then
    tries only bytes the oracle read with a 1-byte access and no wider access over them.
  - fnkeys: CHANGES d19-bool rechecks every cached bool-repr verdict once. SCOPED is now d19.
  - tests/s13: all pass (new test_boolrepr.py).
  - **Note for the metric**: bool-repr is a SOFT cause (report.py SOFT_CAUSES). Records that leave the bucket become
    the hard verdicts they always were, so the ALL hard column will RISE after the recheck: by up to ~640 over
    shards 4-8 (my estimate from shard 4's ratio). That is the harness no longer hiding real divergences, not a
    regression. The commit body's "hard 4,200 -> 4,200" is wrong on this point; read it as "rises".
* **call-flow, first look** (shard 4, 405 records): 227 call-count, 178 call-target, 88 of the call-targets have the
  native's next call equal to the oracle's (an extra or missing call). 93 records involve the allocator family:
  free 0x00ecd291 or malloc 0x00ecd1c7 in the oracle, against the game heap 0x00aa1460/0x00aa4060 or new/delete
  0x00401000/0x00401030 natively. For example 0041fd80 -> FUN_0041fe10 `delete unk0c` (Item with a virtual base):
  the oracle calls _free, the native calls `_ZN15S_0041fe10_ItemD1Ev`; 004206e0: oracle free x3, native 0x00aa1460
  x1. Next item: split call-flow by mechanism (allocator family, virtual-base delete, extra out-of-line D1).
* **START (farms)**: the fuzz shards on the integration head after this merges. Only the d19-bool rechecks
  (bool-repr records) refuzz:
  `tools/farm/fuzz_job.sh <integration head> <k> 8 reports/fz<k>/<head12>-fuzz.md` for k = 1..8.
  To check before merging, run a try farm on wip/s1-rules at 2953a57be56f.
