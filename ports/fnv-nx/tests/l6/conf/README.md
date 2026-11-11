# Wine d3d9 conformance tests -> recorded D9S1 streams (V1a)

Wine 9.0's `dlls/d3d9/tests` (device, stateblock, visual, d3d9ex; LGPL) are built standalone with mingw and run under Wine + Xvfb
(llvmpipe), plain and through our recorder `render/tools/rec` (proxy d3d9.dll). The Wine sources and all streams stay in scratch
(`/home/user/work/l6/conf`); only our own scripts and measurements are in the repo.

## Rebuild / rerun
```
tests/l6/conf/build.sh [WORK]       # WORK default /home/user/work/l6/conf: clones wine-9.0 (tag = installed runtime), builds WORK/out/*.exe,
                                    # WORK/rec/d3d9.dll (recorder as built) and WORK/rec_nofree/d3d9.dll (diagnostic variant, see below)
python3 -I tests/l6/conf/run.py     # all four tests, ~25 min (plain device/visual take 4-5 min each); exit 1 iff a check failed
CONF_TESTS=stateblock CONF_REC=rec CONF_PFX=... CONF_TIMEOUT=600 python3 -I tests/l6/conf/run.py
```
wine/test.h is header-only (`-DSTANDALONE` gives its own main); its `wine/debug.h` is replaced by a 20-line shim written by build.sh.
Tests link `-ld3d9 -luser32 -lgdi32 -lole32 -luuid` with `-include initguid.h`. run.py sets the Wine AeDebug debugger to `false` so a
crashing test ends the process instead of hanging on winedbg.

## Outputs
Streams `WORK/streams[.rec]/<t>.d9s` (run A) and `<t>.b.d9s` (run B); logs `WORK/runs[.rec]/<t>/{plain,A,B}.out`; `d9cmp` JSON
`WORK/cmp[.rec]/<t>.json`; `WORK/nondet.txt`. In the repo: `conf_summary.tsv` (one row per test, run with `rec_nofree`),
`conf_summary.rec.tsv` (same with the recorder as built), `conf_never_called.txt` (spec methods no test reached, `Iface::Method`).
Checks: (i) neutrality = summary line (pid stripped) + exit code equal plain vs recorded; on a violation the plain run is repeated
and the first differing line printed; (ii) every stream decodes (`d9s1.py`); (iii) `d9cmp A B` per test.

## Result (2026-11-10, Wine 9.0 / llvmpipe)
Only stateblock passes all checks. device, d3d9ex, visual **crash under the recorder** (exit 5, partial stream) and differ from the
plain run before the crash; plain runs are stable (plain rerun = plain). Recorder defects found (render/tools/rec + gen, not fixed here):
1. Wrapper freed when `Release` returns 0 (`D9Rec::commit` -> `free_obj`). Wine keeps such objects alive (implicit swapchain, back
   buffers) and tests use them again (device.c:1294): use after free; as built, device.exe dies in GetPrivateData with a NULL guid
   (a freed SwapChain9 wrapper's memory reused by a Surface9 wrapper, slot 5 = GetBackBuffer vs GetPrivateData).
2. Out handle params are zeroed before the call and wrapped after it whatever the HRESULT, so `*ppX` is clobbered where Windows/Wine
   leave it alone (device.c:1391.., d3d9ex.c:705). Also wrapper identity: GetRenderTarget vs GetBackBuffer return different pointers
   once the first wrapper was dropped (device.c:1592, 1802).
3. The real-pointer -> id map has no death notification (Reset frees implicit surfaces silently): stream ids depend on heap address
   reuse (device.exe run A vs B differ in `CreateRenderTarget` n_new).
`rec_nofree` (diagnostic, scratch) keeps wrappers alive after Release->0 and unlinks the real pointer; it removes crash 1 only.
Its streams are not valid vectors for objects that reach Release->0. Numbers: see conf_summary.tsv.
