### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **incremental link (owner order via the orchestrator): measured first.** These are the per-phase seconds of
  seven published links (link/<c12>/meta.json, 4 jobs):

  | head | resolve 1 | layouts | vtables | bodies | resolve 2 | apply+check |
  |---|---:|---:|---:|---:|---:|---:|
  | 2e000047 | 101 | 200 | 831 | 1,648 | 146 | 19 |
  | 970ea993 | 134 | 232 | 1,037 | 998 | 189 | 23 |
  | d1ed15e7 | 150 | 139 | 697 | 529 | 133 | 18 |
  | 4a15e91c | 53 | 74 | 441 | 2,741 | 63 | 7 |
  | 8798dd62 | - | 1 | 313 | - | - | - |

  vtables and bodies are 75-85 % of a link. Both are compiles of host units through vtables.compile_probe (about
  0.6 s per compile, thousands per link). The layout probe is already incremental: 8798dd62 had every row
  cached, under 1 s. vtables was not: 313 s, with all 1,721 host probes run again. Its per-file `.done.json`
  remembers only the last key per name. probe_cached was 0 on 2e000047, 970ea993, d1ed15e7 and 4a15e91c.
  Resolve itself is 3-5 min.
* **step 1, d9c070d: `tools/link/objcache.py`**, a content-addressed cache of the link's own compiles. It returns
  the status, stderr and object bytes only when every input is the same:
  - the compiler binaries;
  - the command line verbatim, so a hit has the same paths, and the objects name their source;
  - cwd and the source;
  - the files the flags name (plugins, order keys);
  - the file names under each -I directory;
  - the compiler's environment;
  - every dependency gcc -MD reports, plus their directories' file names.

  Failed compiles are cached too, so the probes' drop loops replay. A timeout is never cached. I checked that -MD
  leaves the object bytes unchanged. `FNVNX_LINK_OBJCACHE_VERIFY=1` compiles even on a hit and compares;
  `FNVNX_LINK_OBJCACHE=none` turns the cache off. The default location is `~/.cache/fnvnx/linkobj`, pruned LRU to
  8 GB by the link stage. bodies, vtables and the layout probe print per-phase seconds and a cache line, which go
  into the link metrics (link_objcache_*, link_bodies_phase_*, link_vtables_phase_*).
* **equivalence.** `tests/t1/test_link_incremental.py` runs the link stage's full order on test_vtables' classes,
  at one fixed path, for two heads; head 2 changes a vtable host unit.
  - warm (out/ kept), warm with a new out/ and only head 1's cache, and a repeat: the same sha256 as the cold link
    for all 22 outputs (addrmap.tsv, aliases.ld, refs, the synthesized vtable and body objects, every fixed/
    object, the trial link).
  - two cold links are identical.
  - the changed host compiled again (3 of 26 compiles), the rest came from the cache.

  On four real host units of t/979cb1d50668, the cached object equals a plain compile byte for byte. A hit takes
  0.001 s against 0.55 s for a compile; a miss costs about 5 % more.
* **farm check: full_job step 4a, `LINKEQ=1`.** It runs once per farm, on the first head whose link took compiles
  from the cache. It sets link/ and guest/ aside, links and builds the guest again cold at the same paths with an
  empty cache, and compares the sha256 of guest.nso and addrmap.tsv. A pass writes `$F/linkeq.done`. A FAIL keeps
  link.warm/ and guest.warm/, and the report line says so. Test: tests/t1/test_full_linkeq.sh. Cost: one cold
  link plus the guest, about 1 h, once. Suggested START, for the orchestrator to choose when (full1 is busy now):
  `LINKEQ=1 bash /home/user/fnv-nx-build/farm/start.sh full1 full`. It needs the second full head after the merge;
  the first one fills the cache.
* **548df56: resolve per-phase seconds** (link_resolve<n>_phase_*). Resolve is the next step once the farm numbers
  show what remains.
* **compile.sh fix** (6ca280e, in note 007): full1's exit 1 after the order keys.
* metric: link after a small change: fixture 26 -> 3 compiles; farm minutes pending. Expected: vtables+bodies from
  1,200-4,400 s to their non-compile part (the phase metrics will show it), while resolve's 4-6 min stays.
* next: the farm phase numbers, then the remainder: resolve per-object results and bodies' non-compile passes.
  The target is a 5-minute warm link.
