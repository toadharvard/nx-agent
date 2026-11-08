### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **incremental link, resolve.** Profiled resolve 1 of 31971038913d (2,922 objects) here on one core, with
  t/31971038913d and c/31971038913d/*of4 from fnv-nx-build; no exe, no overrides. Each step below was checked at
  the same out/ path against the committed code's output: every output file is identical (diff -r, the parse
  store excluded).

  | commit | change | resolve 1 |
  |---|---|---:|
  | before | | 104 s |
  | 6453f80 | memoize sigs' pure string functions (canon_type 3.3M calls, split_qual, canon_param, ...) | 88 s |
  | 838291a | out/cache/sigs.marshal: the parses of one link process for the next (resolve 1, resolve 2, bodies, vtables; keyed by sigs.py's sha256) | 76 s |
  | 0b2abf7 | CDecls.find: one pass per text instead of one per name (same sites: 30,714 lookups checked); hand_vptr_stores: patterns compiled once, statements without '=' skipped | 59 s |
  | d200b5c | memoize abi.classify (1.78M calls) | 57 s |

  With the store cold (the first link on a machine), resolve takes 96 s. The store is 111 MB in out/cache/.
  FNVNX_SIGS_PERSIST=0 turns it off.
  Phases now: references 27.5 s, vftables 12.7 s, msvc side 7.4 s, the rest under 3 s each.
* **bodies without synthesis** (`--no-synth`, here): load and classify take 8 s. The exe pass (the pristine
  objects plus the player's exe) cannot run here without game data. Its seconds come with the next farm link
  (link_bodies_phase_exe_pass_s), together with the other bodies and vtables phases and the objcache hits.
* tests: tests/t1/test_sigs_persist.py (new). test_link_incremental, test_objcache, test_full_linkeq,
  test_link, test_bindings, test_vtables, test_slotfill and test_abimanual pass.
* merged integration at 05fe90e into the branch (2b7bdf5).
* metric: resolve 1 of 31971038913d here: 104 -> 57 s (warm store). On farms (101-187 s per resolve, two per
  link) that should save about 1.5-3 min per link.
