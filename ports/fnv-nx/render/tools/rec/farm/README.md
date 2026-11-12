# d9rec farm job (M1a)

Records the original game's D3D9 call stream under Wine with our recorder (`../`), in one reference-environment profile, and counts it.

- `d9rec_job.sh <commit> <handheld|docked> <report.md>`: the job. `D9REC_DRY=1` replaces the game by nxd9play on synthetic captures (test).
- `d9rec_stats.py STREAM [--md --json --tsv-dir DIR --window N --expect-mode WxH@R]`: standalone statistics of any D9S1 stream (game or guest).
- Test: `tests/l6/recjob/test_recjob.py` (stats vs NXD9 counts, hand-counted histogram, shellcheck, dry run of both profiles under Wine).

## Wiring (L4): one line in `tools/farm/runner.sh`, before the `*)` case
```
    d9rec)   bash "$NX/ports/fnv-nx/render/tools/rec/farm/d9rec_job.sh" "$H" "$@" "$R" ;;
```
Start: `farm/start.sh d9r1 d9rec handheld` and `farm/start.sh d9r2 d9rec docked` (arguments after the job name: profile). Needs `ARTIFACTS` (push) and
the player's install as `$FARM_HOME/game` (`tools/gamedata_fetch.sh`, as sweep). Exit 0 with any verdict, 1 when setup/profile check/a scene stream failed
(the report says which; a hang or crash of the game is a line in the report, never hidden).

## What it does
1. Setup: missing tools installed with apt when root (short list; sweep_job.sh's `setup()` is not callable, see below); install via `gamedata_fetch.sh`;
   `wine_ref.sh setup` at the profile size in its own prefix `$FARM_HOME/d9rec/<c12>/<profile>/wine` (never the sweep's), then
   `wine reg add HKCU\Software\Wine\Direct3D /v VideoMemorySize /d $D9REC_VIDEO_MEM_MB` (variable at the top of the script, default 960; read back and printed).
2. Profile: `Xvfb -screen 0 WxHx24 -extension RANDR`. **Single mode works this way**: Wine's winex11 enumerates what the X server's RandR reports. With RandR
   (Xvfb default) Wine exposes one mode but at 0 Hz and also a 16-bit R5G6B5 mode; without RandR it exposes exactly the current mode, 1280x720 (or 1920x1080)
   at 60 Hz, X8R8G8B8 only (measured here, Wine 9.0, both screen sizes). `wine_ref.sh` calls plain `Xvfb`, so the job puts a shim `Xvfb` first on PATH
   that appends `-extension RANDR`. The profile check (every run of the job) runs `tests/l6/rec/envprobe.c` under the recorder on that screen and requires, in the
   recorded stream, GetAdapterModeCount(X8R8G8B8) = 1 and every EnumAdapterModes answer = WxH@60 (`d9rec_stats.py --expect-mode`).
3. Recorder built from the checkout (`rec/build.sh`, keyed), copied by `wine_ref.sh` (`S16_D9CAP=<dll>`) into the prefix's game mirror and removed after each run.
   Scenes (`scenes.json` generated from `tools/compare/scenes.json` + `cells.py`): `menu` (boot to the main menu, hold) and `cell` (`coc` into the first cell of
   `cells.py list`, or `D9REC_CELL`, hold). Hold = `D9REC_HOLD_SHOTS` (6) screenshots `D9REC_HOLD_GAP_S` (5) s apart after the settle time; the game is stopped after
   the shots (`S16_STOP_AFTER_FRAMES=1`), the stream is complete up to the last Present. Each scene is recorded twice (timeout `D9REC_TIMEOUT_S`, 1200 s).
4. `d9rec_stats.py` on run 1 (sections 1-10 of the report: size, shader creates by frame with hashes FNV-1a 64 / last frame with a create / histogram over the last
   100 frames, other creates, locks, draws, FFP draws and calls, state-block and query methods, HRESULT failures, env answers by method, modes, called methods,
   never-called spec methods); `tests/l6/rec/dump_env.py` writes the env answers TSV; `d9cmp.py run1 run2` is the determinism section (equal / first det mismatch / env diffs /
   masked pix; timeout `D9REC_CMP_TIMEOUT_S`).
5. Publish: report to the runner's path; private branch `l6-render/d9rec/<c12>/<profile>` of `$ARTIFACTS`: `<scene>/run{1,2}.d9s.xz` (parts of 95 MB above 99 MB: `cat run1.d9s.xz.part* > run1.d9s.xz`),
   `env_answers.tsv`, `perframe.tsv`, `methods.tsv`, `modes.tsv`, `stats1.json`, `cmp.txt`, `cmp.json`, `env_probe.tsv`, `probe.d9s`, `SHA256SUMS`. Game-derived: private only.
   The report names no cell (the cell id names game content); it is counts.

## M1b: the first farm run never rendered a world cell (783ec078)
Recorded timeline (docked `cell`, 6,010 frames): menu from frame 1,990 (19-28 draws, 225 calls per frame, constant), first change at frame 5,752 (+1 draw, a vertex buffer
created: the console opening), frame 6,008 releases the 26 menu vertex buffers, then 137 calls after the last Present (CreateTexture 1024x512 DXT1, 11 levels, the game's own
CPU mip chain: GetSurfaceLevel / GetDesc / LockRect / UnlockRect for levels 0-3) and the stream ends **between GetDesc and the LockRect of level 3**. Handheld: identical 137
trailing calls, change at frame 9,447 of 9,854. The scene flow itself worked (the console opened and `coc` began to unload the menu: the loading screen's textures are what was being built).
The game stopped calling D3D (crash, hang or kill) at the first LockRect after the load started; wine_ref.sh went on typing and sleeping and exited 0. Nothing in the flow depends
on d9cap (d9cap.c reads only S16_CAPTURE*, S16_REAL_D3D9; no frame counter, marker or stop mechanism). Job changes: world verdict (`d9rec_stats.py --require-world`, section 11, exit 4;
the job marks `FAILED: scene cell did not reach a world cell` and exits 1), per-run watcher (`watch.tsv`: stream size and game-process count every 5 s: crash vs hang), wine.log
error lines (`WINEDEBUG=err+all`, `D9REC_WINEDEBUG`), the commit check as a prefix compare, cells.py output to a file instead of `| head -1`.
Env: `D9REC_WORLD_MIN_DRAWS/FRAMES/TEXTURES` (100 / 10 / 50: the rule is in the `d9rec_stats.py` docstring).

## Not reusable from tools/farm and tools/compare without edits (for L4 / the compare owner)
- `tools/farm/sweep_job.sh` `setup()` (apt block, shader tools, nxd9play, worktree) runs on `source`; no function library. Proposal: move the tool check + apt install into
  `tools/farm/lib_job.sh` as `farm_need_tools <cmds>` and use it here (this job only needs wine, Xvfb, xdotool, xwd, convert, xz, mingw g++).
- `tools/compare/wine_ref.sh:41-42` (`xstart`): `Xvfb "$DISP" -screen 0 ...` has no argument hook. Proposal: `Xvfb ... ${S16_XVFB_ARGS:-}` (the job then sets
  `S16_XVFB_ARGS='-extension RANDR'` and drops the PATH shim). Also its `pkill -f "Xvfb $DISP "` kills any Xvfb on that display of the machine (the job passes `S16_DISPLAY`).
- `tools/compare/wine_ref.sh:90` (`VideoMemorySize` in `s16.reg`) is hard-wired 1024: proposal `${S16_VIDEO_MEM_MB:-1024}`; the job overwrites the key after `setup`.
- `wine_ref.sh:34` names the proxy `S16_D9CAP` and `:135` copies it to `d3d9.dll`: works with our recorder as is (any DLL), only the variable name says d9cap.
- `wine_ref.sh run` has no per-scene frame-gap option except the env `S16_FRAME_GAP_S` (used) and takes `frames` from the scene JSON (the job writes its own scenes.json).

## Limits (not verified without the game)
- The real boot, the menu and `coc` flows, the stream size of a real session (textures are recorded at every lock: possibly GBs; `d9s1.py` keeps every record in memory, about 1-2 KB per
  record), d9cmp time on such streams (`D9REC_CMP_TIMEOUT_S` reports a timeout), and whether the original's stream is deterministic.
- FFP draw counting follows Set*Shader calls per device (after a state-block Apply the stage is "unknown" until the next Set; Reset clears both).
- A stream cut by a killed game is decoded up to its last complete record; the error and the undecoded byte count are printed.
