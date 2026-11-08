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
* **vtables on real data, a subset** (31971038913d: the 157 requested classes whose hosts are among 25 random host
  units, 31 hosts, 2 jobs, ORDER_KEYS=unit):

  | run | seconds | phases | compile cache |
  |---|---:|---|---|
  | cold | 95 | probe rounds 79, emit 16 | 272 compiles, stored |
  | new out/vtables/ (fnvlink's own caches gone), compile cache warm | 4 | probe 1.0, emit 3.7 | 272 of 272 hits |
  | everything kept | 1 | 0.3 + 0.3 | not asked (fnvlink's caches) |

  All 56 emitted objects are byte-identical to the cold run's (cmp). At that ratio the full step (700-1,300 s on
  the farms) should take about 30-50 s on a warm machine.
* **bodies without synthesis**, here with the pristine MSVC objects (msvc/pristine/87eb9373a0bb) and the player's
  exe (gamedata/exe), 2 jobs: before 99 s, of which the exe pass was 87.7 s. Under the profiler, 65 s of the
  exe pass was vslots.Coff.symbols_in (387k calls, each sorting the whole symbol table). b9374f3 groups the table
  once per object: 33 s, exe pass 23.3 s. bodies.tsv, bind.tsv, synth.tsv and stats.txt are identical, and
  29,771 sections of 400 real objects give the same answers. The synthesis half of bodies compiles through
  compile_probe, i.e. the compile cache; the farm's LINKEQ check covers it end to end.
* tests: tests/t1/test_sigs_persist.py (new). test_link_incremental, test_objcache, test_full_linkeq,
  test_link, test_bindings, test_vtables, test_slotfill and test_abimanual pass.
* merged integration at 05fe90e into the branch (2b7bdf5).
* metric: resolve 1 of 31971038913d here: 104 -> 57 s (warm store); bodies front end 99 -> 33 s; vtables (subset) 95 -> 4 s. On farms (101-187 s per resolve, two per
  link) that should save about 1.5-3 min per link.
