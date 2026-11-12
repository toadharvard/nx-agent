# The platform lane (D25 #3, #4, #6)

This lane measures three D25 targets on emulator runs: unimplemented platform calls, failed file opens, and audio
decode and input mapping failures. It is S4-platform's metric. It is all project code. It reports our stand-ins'
own function names and caller addresses, and never a file path.

## Recording: platform/win32/platstat.cpp

Recording is off by default: each report site pays one load. It starts with `fnvnx_plat_start(sink)`, or with
`fnvnx_plat_autostart()` from a constructor (tools/perf/plat_autostart.c). `fnvnx_plat_stop` turns it off.

| class | reported by | detail |
|---|---|---|
| unimpl | `fnvnx_plat_note(FNVNX_NOTE_UNIMPL, ...)` for an entry whose function is missing and `fnvnx_wine_fixme(id)` for a body that does what Wine 9.0 does (name `wine-fixme`, detail `id N`): BinkOpen (no movie player), an unsupported TEB offset (`__readfsdword`) | - |
| noop | `FNVNX_NOOP()` in a documented Switch no-op: SteamAPI_Init and the Steam / GOG Galaxy interface accessors, winsock offline (WSAStartup, socket, gethostbyname), DebugBreak | - |
| open | a failed CreateFileA, the CRT's fopen (fnvnx_fopen) and _open (fnvnx_open) | path class (bsa, esm, save, ini, other), the Win32 error or errno, `ci yes/no/nodir` (see below), and an FNV hash of the Windows path |
| decode | `ov_open_callbacks` failing (or a build without libvorbis), an MP3 that minimp3 cannot open | the libvorbisfile code, or the decoder |
| input | a HID keyboard usage with no DirectInput code (platform/input/keymap.cpp) | the usage |

**The `ci` field.** It comes from an independent case-insensitive scan of the parent directory:
- `yes`: the name exists in another spelling, so the path mapper missed it;
- `no`: the file really is missing;
- `nodir`: the directory itself is missing.

**Counting.** Each distinct (class, name, caller, detail) is counted. `FNVNX_UNIMPL()` and `FNVNX_NOOP()` name the
enclosing function (`__PRETTY_FUNCTION__`, shortened to Class::method), and the caller is the game's return
address.

**Imports with no export.** These are the boot's own lines and need no recorder. toolchain/game/boot.cpp prints
`fnvnx boot: import <dll>!<name>: no export`, and a call to such a slot traps.

**Output.** Lines go to the sink, default os_debug_print:

    plat: <class> <name> caller 0x<pc>[ <detail>]             the first of each distinct entry (first 256)
    plat: count <class> <name> caller 0x<pc> n <n>[ <detail>] fnvnx_plat_flush: ExitProcess, and every 30 s
    plat: summary unimpl <distinct> <calls> noop ... open ... decode ... input ... dropped <n>

**Using it in other code.**
- New stand-ins mark missing functionality with `FNVNX_UNIMPL()` and deliberate Switch no-ops with
  `FNVNX_NOOP()`.
- A render backend can call `fnvnx_plat_note(FNVNX_NOTE_UNIMPL, name, caller, detail)` for unsupported D3D9 paths.
  The null device is generated (toolchain/game) and is not marked here.

## The report: tools/perf/plat.py

    plat.py <log>... [--elf guest.elf] --out report.md

**Metric (lower is better).** The sum of:
- distinct unimplemented entries: unimpl names plus imports with no export;
- distinct failed opens, by path hash;
- distinct decode failures;
- distinct unmapped inputs.

Entries are distinct by name, path or detail, not by caller, so the metric does not move when a relink moves
code. No-ops are listed but not counted.

**Tables.** Unimplemented entries (calls, call sites, caller functions as nm function starts), imports with no
export, failed opens (path hash, class, error, ci, calls), decode failures, unmapped inputs, and no-ops.

**Cumulative logs.** The last flush of a log holds. The JSON is fnvnx-plat/1.

## The lane: tools/farm/plat_job.sh

1. tests/s15/test_plat_report.py: plat.py on synthetic logs. The recorder's own test is tests/s4
   `plat_lane_opens_stubs_counts`. It checks four failed opens through all three entries, with class, error and the
   ci scan; that CREATE_NEW on an existing file does not count; that no path is printed; BinkOpen counted 3 times at
   one site; the no-ops; decode and input; the flush and summary lines; and that nothing is recorded after stop.
2. `tools/threads/variant.sh PLAT=1 WATCHDOG=0` relinks the full job's guest with plat_autostart.o.
   `boot_game.sh` runs it for PLAT_TIMEOUT seconds (default 300; 180 in quick_job). PLAT_LOGS / PLAT_ELF report a
   run made elsewhere (S18's scenarios).
3. plat.py.

**Where it runs.**
- **T1:** quick_job.sh check `plat`, on lane D (after build, with threads, frames and mem). quick_plan.py class
  `plat` covers plat.py, plat_autostart.c, PLAT.md, plat_job.sh, test_plat_report.py, variant.sh,
  tools/threads/report.py and tests/s15/mtqemu.
- platform/ changes select every check, as before.

## The original's baseline (failed opens, #4)

Some opens fail on a stock PC install too (a relative `Plugins.txt` in the game folder: the real one is under
AppData). They are counted against the port only when the original does not fail them:

1. The Wine run of the same scenario (tools/scenario/wine_scenario.sh) gets a trace list with the line
   `kernel32!CreateFileA` (tools/lockstep/fnvtrace.dll resolves `dll!Export` lines) and `FNVTRACE_DEREF=256`.
2. `tools/perf/plat_wine.py <wine.tsv> --out original_opens.json`: the game's own CreateFileA calls (return address in
   the exe image) that returned INVALID_HANDLE_VALUE, keyed like platstat.cpp (path class + FNV-1a of the path), no
   path text (fnvnx-plat-original/1).
3. `tools/perf/plat.py <logs> --original original_opens.json`: an open failing on both sides (same class and hash) is
   listed as `also fails in the original`, left out of the metric; `metric_all` keeps it. No hand-written list.
