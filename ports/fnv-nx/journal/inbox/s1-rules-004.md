### J-XXXX | 2026-11-07 | stream: S1-rules | status: active | parent: s1-rules-003

* **D24 owner table, fuzz pass 8b233f99831a** (all 8 shards, hard divergences by cause from the shard summaries
  `<head12>-fuzz.json`; `tools/difffuzz/owners.py <head12>`, which runs after every fuzz pass; 7,212 hard functions):

| owner | hard | causes |
|---|---:|---|
| S1-rules | 1685 | oracle-fault 712, unclassified 658, signedness 250, signedness? 65 |
| S2-bodies | 1503 | native-undefined 810, native-stub-data 344, after-native-undefined 327, native-trap 19, native-zero-stub 3 |
| F1 | 2680 | call-flow 865, native-early-exit 551, native-inlined 495, native-extra-calls 392, falloff? 276, native-stale-result 60, native-empty-callee 23, native-missing-free 10, native-empty-body 8 |
| F2 | 320 | vptr-differs 106, dyninit-no-atexit 71, canned-this 52, thunk-binding 33, layout-offset 28, native-vcall-oracle-direct 28, byvalue-arg-dtor 1, ctor-this 1 |
| F3 | 2357 | native-null-read 485, x86-only-store 417, store-width 310, callee-saved 253, native-garbage-arg 223, uninit-arm 213, split-global 121, native-only-store 106, literal-store 87, arg-stack-vs-object 66, native-address 36, arg-other-buffer 23, native-wild-private 13, ilp32-wrap 4 |
| F4 | 59 | float 44, float-tolerance 15 |
| ALL | 8604 | 8 shards |

* The pass predates d20-sign / d20-ilk / d20-malloc. The next pass on the integration head (they are adopted:
  b062d8d) moves about 1,000 call-flow divergences (F1) out, and signedness (S1-rules) from 250 to ~30.
* S1-rules' share: oracle-fault 712 and unclassified 658 are next in my queue, after D26's farm verdict.
