### J-XXXX | 2026-11-07 | stream: T1-fastbench | status: wip | parent: J-0156

* **item 1 done: reference capture cache** (claude/fnv-t1-fastbench e4f28eb)
  - The cell sweep's Wine boot (~330 of ~370 s per cell) only re-made references that never change. Now
    `sweep_job.sh` captures a scene only when it is missing from the capture store under the current key, and
    otherwise unpacks and replays it.
  - The key covers the exe sha, the game data content, the d9cap/nxd9play build, the harness files (wine_ref,
    ref_ini, nxd9trim/norm/io, replay.py), Wine, the settle/journal settings, CAPTURE_V, and the store and
    REF_FORMAT versions. Each scene is also checked against its scene-JSON hash.
  - The reference replay (nxd9play under Wine) is cached too, with `scene_stats.py --ref-cache`.
  - `tools/compare/capstore.py` stores captures losslessly with deduplication: payloads of 4 KiB or more are kept
    once, by content. On 4 real cells, 1,012 MB of captures became 81 MB (4 MB of skeletons, 77 MB of blobs), and
    unpacking (sha256 checked) takes ~5 s per scene. The 3rd and 4th scenes added only 5 MB and 1 MB, so the full
    1,155-scene store should be a few GB, not the ~78 GB that xz per scene would need.
  - Where it lives: fnv-nx-build `capture/<key>/<k>of<N>` (private), and on each farm `$FARM_HOME/capture/<key>`
    (the union of the shard branches, fetched at setup).
  - Modes: `SWEEP_CAPTURE=auto` (default) replays cached scenes and captures the rest; `replay` only replays;
    `capture` refreshes everything. Raw per-program-set captures are now opt-in (`SWEEP_RAW=1`).
  - Rule for a full run: any key input that changes makes a new key. The next sweep of all shards is then the
    populating capture run; every later head only replays.
* **metric** - cell sweep: 3.5 h per shard (~126 machine-h) -> an estimated ~0.45 h per shard (~45 s per scene,
  ~14 machine-h) after one populating pass. The replay time is measured on the farm once the cache is filled.
  Full verification on one warm farm is still hours, and items 2-4 bring it down.
* **tests** - `tests/t1/test_capstore.py` (5: round trip, dedup, corruption detected, export, scene_stats using a
  cached reference with no Wine) and `tests/t1/test_sweep_cache.sh` (9: publish to and fetch from a bare repository,
  key stability, a new key when the data changes); tests/s16 OK. There was no end-to-end run with Wine here: the
  container has no Wine or game install, and setup is longer than 10 minutes.
* **farm START (orchestrator)** - after merging e4f28eb, one populating pass over the 36 shards, cells only:
  `SWEEP_PROGRAMS=0 bash /home/user/fnv-nx-build/farm/start.sh sw<k> sweep <k> 36` for k = 1..36. It takes about as
  long as the last sweep: ~3.5 h, i.e. two runner passes, with resume through done.tsv. Each shard publishes
  `capture/<key>/<k>of36`. Check: `capture cache <key> (auto): N scenes cached` in each summary's notes, and
  `git ls-remote origin 'refs/heads/capture/*' | wc -l` = 36. After that, any sweep of a new head replays only.
