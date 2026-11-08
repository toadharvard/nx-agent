### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **incremental link step 2: resolve's per-unit source scans, kept between link processes.** On 31971038913d here
  (one core), each step was checked at the same out/ path against the committed fnvlink.py, cold and warm, with
  every resolve output file identical (diff -r, the stores excluded). After the integration merge 2b7bdf5, resolve
  takes 66 s with no store (other streams' new work; it was 57 s before the merge).

  | commit | kept in out/cache/ | resolve 1 (stores warm) |
  |---|---|---:|
  | 838291a | sigs.marshal: signature parses | 66 s (store-less: 66) |
  | a88472f | hvs.marshal: hand_vptr_stores sites per unit (sha1 of text, bound names, vftable set) | 53 s |
  | 0563c81 | cdecls.marshal: CDecls.find answers per (text sha1, name); CDecls parses only on a miss | 38 s |

  Phases now: references 17.8 s, msvc side 8.2 s, objcopy args 2.8 s, the rest under 2.5 s each.
  Each store is under the producing file's sha256 and pruned to the entries used; a cold store costs the old time.
* tests: test_link_incremental, test_link, test_bindings, test_abimanual, test_vtables, test_varargs,
  test_colliding and test_override pass.
* measurement caveat found: a bodies run into the same out/ makes resolve read out/bodies/, so the comparisons
  after that point use a fresh baseline from the committed code (the earlier checks came before it).
* metric: resolve 1 here 104 -> 38 s (warm stores). On the farms both resolves were 235-370 s per link; expected
  about 90-140 s now. leq1's phase metrics will show it.
