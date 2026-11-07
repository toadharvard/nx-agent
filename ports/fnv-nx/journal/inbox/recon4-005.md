### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> D25 #3 #4 #6 platform lane | status: wip | parent: recon4-004

* **did** (tools/perf/PLAT.md; S4-platform's metric)
  - **Recorder**: platform/win32/platstat.cpp (new). It is off by default: one load per report site.
    - Counts each distinct (class, name, caller, detail), in five classes:
      - **unimpl**: `FNVNX_UNIMPL()`;
      - **noop**: `FNVNX_NOOP()`, a documented Switch no-op;
      - **open**: path class bsa / esm / save / ini / other, the Win32 error or errno, an independent
        case-insensitive scan of the parent directory (ci yes / no / nodir), and a path hash; no path is printed;
      - **decode**;
      - **input**.
    - Output: `plat:` lines. The first occurrence of each entry at once; counts and the summary at ExitProcess and
      every 30 s.
    - The macros are in fnvnx_win32_ext.h and name the enclosing function through `__PRETTY_FUNCTION__`; C files
      declare the two entry points locally.
  - **Report sites**:
    - unimpl: the 15 E_NOTIMPL methods of dshow.cpp, BinkOpen, `__readfsdword`'s unsupported TEB offset;
    - noop: SteamAPI_Init and the 5 Steam / GOG interface accessors, WSAStartup / socket / gethostbyname,
      DebugBreak;
    - open: CreateFileA (a map failure and an os open failure; CREATE_NEW on an existing file is not counted), the
      CRT's fnvnx_fopen and _open (fd_open now takes the caller);
    - decode: ov_open_callbacks failing or no libvorbis, and minimp3 failing to open the stream in dshow;
    - input: HID usages without a DirectInput code in keymap.cpp.
    - Imports with no export are the boot's existing `fnvnx boot: import ...: no export` lines.
  - **tools/perf/plat.py**:
    - metric = distinct unimplemented entries (unimpl names + imports with no export) + distinct failed opens (by
      path hash) + distinct decode failures + distinct unmapped inputs;
    - distinct by name, path or detail, not by caller, so it is stable across relinks;
    - no-ops listed, not counted;
    - tables per class, callers as nm function starts; the last flush per log holds; JSON fnvnx-plat/1.
  - **Lane**: tools/farm/plat_job.sh. Step 1: test_plat_report.py. Step 2: `variant.sh PLAT=1 WATCHDOG=0` relinks
    the guest with plat_autostart.o, which then runs under boot_game.sh; PLAT_LOGS takes S18's logs instead. Step
    3: plat.py. quick_job.sh check `plat` runs on lane D after mem, and quick_plan.py has a class `plat`.
* **measured** - Metric: not measured here; there is no guest build or exe in this container. full1's first
  plat_job run gives S4-platform its first number. This commit is a named prerequisite for D25 #3, #4 and #6.
* **tests**
  - tests/s4: 107 tests, 0 failed. The new test, plat_lane_opens_stubs_counts, covers 4 opens through the 3
    entries, with classes, errors and the ci scan; the CREATE_NEW non-failure; no path printed; BinkOpen 3 calls at
    one site; no-ops; decode and input; the flush and summary; nothing after stop.
  - tests/s15 test_plat_report: 9 checks, 0 failed.
  - tests/t1 test_quick.sh (results in the commit body).
  - platstat.cpp, file.cpp, services.cpp, bink.cpp, keymap.cpp, vorbisfile.cpp, iob.c and stdio.c compile clean for
    ILP32. dshow.cpp was compile-checked against a stand-in minimp3 header: minimp3 is not fetched here.
* **limits / asks**
  - The D3D9 stand-ins are not marked. The null device is generated in toolchain/game, and editing it would make
    quick_job ask for a full run.
  - **S16 / S6n**: mark unsupported D3D9 paths in render/ with `fnvnx_plat_note(FNVNX_PLAT_UNIMPL, ...)`.
  - XInput button mapping has no unmapped case today: every button is mapped.
* **next**
  1. Triage full1's first report: unimpl by calls, then failed opens with `ci yes` (path-mapper bugs).
  2. Mark the remaining silent stand-ins: the window.cpp and services.cpp one-liners, each as unimpl or noop.
