### J-XXXX | 2026-11-07 | stream: S1-rules | status: active | parent: s1-rules-001

* **baseline** (8b233f99831a, reports/fz1..fz8; link 9f865b0a4037, the same link as 06f45fbb): cumulative over 8 shards,
  67,311 functions. Hard 6,314 -> 7,212 after d19-bool. bool-repr 1,424 -> 290: bool-repr is soft, so ~900 hidden
  real divergences became hard, as note 001 expected. They went mostly to signedness 53 -> 252, x86-only-store
  231 -> 421, store-width 207 -> 310 and native-stub-data 252 -> 345. This hard count of 7,212 is the new baseline.
* **d20-sign** (c390a66, harness) - the signedness probe had bool-repr's flaw. On 8b233f99, 434 of its 461 memory
  hits were bytes inside a word read whole. It now counts only the top byte of a 1- or 2-byte oracle read. It
  rechecks the cached signedness verdicts. Hard count unchanged (signedness is hard); expected signedness ~30.
* **call-flow split by mechanism** (fuzz/cache 1..8 of 8, live hard records)
  - 821 records (697 functions) have the native call `fnvnx_interlocked_slow`. That is the shim's misaligned
    Interlocked path (fnvnx_win32.h -> sync.cpp), reached because the fuzzer plants misaligned objects; the oracle's
    `lock` op is no call. **d20-ilk** (3fabbdb) runs it as the Interlocked function its op names. Only functions
    whose native code names it refuzz.
  - 223 records (220 functions) have the CRT malloc 0x00ecd1c7 in the oracle. The oracle got a garbage canned result,
    the native's operator new a fresh block, and malloc was not in the `new` family. **d20-malloc** (35d7921) makes
    it an allocator of that family. Only functions whose oracle code calls malloc refuzz.
  - What remains is mostly per instance. Call-count with the same targets: 599 (native fewer) and 499 (native more).
    The most common missing target is atexit 0x00ec658f (69, generated/global_object_initializers_o2.cpp:
    dyninit-no-atexit, S10's real bug, left to S10). Free x2 against sized delete x1 in deleting destructors
    (listbox 00724200: the native frees once) needs an instance look. Calls into planted memory 0x03000000 that the
    native does not make: 56 records.
* **expected after the next shards** - hard 7,212 -> ~6,300 (up to 821 + 223 call-flow records leave; those with a
  real later difference stay hard under that cause); signedness 252 -> ~30.
* tests/s13 all pass (new test_ilkslow.py, test_crtmalloc.py; test_boolrepr.py extended). tests/s1 and tests/s11
  are untouched by these commits (no rule changes).
* **START (farms)** - after these merge, fz1..fz8 on the integration head: `tools/farm/fuzz_job.sh <head> <k> 8
  reports/fz<k>/<head12>-fuzz.md`, k = 1..8. Only the d20-sign rechecks and the d20-ilk / d20-malloc selections refuzz.
* **next** - native-early-exit, native-null-read, vptr-unpaired, uninit-*, ilp32-pointer, callee-saved, falloff?,
  in that order, from the next shards' records.
