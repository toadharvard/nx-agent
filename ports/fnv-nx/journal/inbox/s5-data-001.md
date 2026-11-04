### J-XXXX | 2026-11-04 | stream: S5-data | status: wip | parent: J-0149

S5-data (game data: file I/O and the real .esm/.bsa), branch `claude/fnv-s5-data`, which is `claude/fnv-s5` rebased
onto the integration branch.

* **did (queue 1, V1's store-watch defects)** - `e170aeb`. `tests/v1/test_s5_milestone_store.py` (taken unchanged
  from claude/fnv-v1) goes from 3 defects (W2, W6, W7) to 0, W1-W8 ok.
  - The hook range starts 12 bytes below the watched word, and stored_word keeps only stores that cover the whole
    word. This fixes W2 (`str x, [P+0x3c]`) and W6 (the same shape on the player pointer).
  - When the pointer at A changes or becomes 0, the old object's hook is deleted (W7).
* **did (queue 2, the Win32 file API path on the SD layout)** - `9369e31`.
  - `os_game_dir()` is the install, C:\FalloutNV. The game guest uses `sdmc:/switch/fnv` (Data/ at
    `sdmc:/switch/fnv/Data`), or `game_dir=` in `sdmc:/fnvnx/fnvnx.ini`. The host uses `$FNVNX_GAME_DIR`.
  - path.cpp mounts the install there, boot.cpp opens `<game dir>/FalloutNV.exe`, and the profile stays at
    `sdmc:/fnvnx/user`.
  - Package, TOOLCHAIN.md (sections 15, 18 and 20) and RUNTIME.md (Paths) are updated.
  - Memory-mapped reads do not apply: the exe imports no CreateFileMappingA or MapViewOfFile. The data path is
    CreateFileA, ReadFile, SetFilePointer, FindFirstFileA, GetFileAttributesA and GetPrivateProfileStringA.
  - The emulator has `emu_guest.py --game DIR` (`FARM_GAME` in farm_boot.sh), in `tests/s5/datapath.py`.
    - It mounts the install in place at `sdmc:/switch/fnv` and at the old `sdmc:/fnvnx/game`. Without --game, both
      are `<root>/fnvnx/game`, so old and new guests and the pipeline's `--file fnvnx/game/...` all work.
    - Path components fold case as FAT does. The guest relies on that: `os_fs_case_insensitive() = 1`, so path.cpp
      skips its directory scan.
    - The data milestones come from the host file calls: ini-read, archive-list, bsa-opened, esm-header and
      masters-loaded. They are `data` lines in milestones.txt and are in boot_milestones.py's keys.
    - File faults are counted by call, errno and path.
  - `tests/s5/synth_install.py` writes a synthetic install: our own small Fallout_default.ini, empty BSA headers and
    a one-record TES4. Summaries mark it "synthetic install".
* **did (first-run profile)** - `3338518`. `platform/win32/profile.cpp` `fnvnx_first_run_profile()`, which boot.cpp
  calls before WinMain. It writes the files the PC launcher writes, each only when it is missing:
  - Fallout.ini, a copy of Fallout_default.ini;
  - FalloutPrefs.ini, a copy of the install's Medium.ini (else Low.ini, High.ini, VeryHigh.ini), then
    `[Display]` set to 1280x720, full screen;
  - plugins.txt, listing FalloutNV.esm and the official add-on masters Data\ holds, in release order.
  The places are those GetSaveDirectory (0x00876ad0) takes: CSIDL_LOCAL_APPDATA and My Games\FalloutNV.
  Medium is my decision; the render stream may change it.
* **verified (V1 L1)** - With the decomp checkout and aarch64-linux-gnu-g++ -mabi=ilp32, the offsets are
  TESObjectREFR::parentCell 0x40, TESChildCell 0x18, data 0x20 and sizeof(TESForm) 0x18, the same as x86 MSVC.
  `first-cell store *0x011dea3c+0x40` is right under ILP32.
* **measured** - Stub boot of link 31971038913d's guest on the owner's exe (sha256 89e70204...dd0fe), local:

  | install | milestones reached | data | file faults |
  |---|---|---|---|
  | exe only | winmain | 0/5 | 41 (14 distinct, all ENOENT) |
  | synthetic install | winmain, ini-read | 1/5, bsa 0/3, masters 0/1 | 40 (13 distinct) |
  | synthetic install + the profile files (stand-in: this guest predates profile.cpp) | winmain, ini-read | 1/5 | 11 (the game's own optional probes: Custom.ini, old debug logs) |

  - With the synthetic INI, the real guest's INI path works end to end: GetPrivateProfileString, then path.cpp, then
    the host fs.
  - Both boots then stop at renderer init. LoadLibraryA("D3D9.DLL") fails, and this link's guest has no null-device
    symbols ("re:...CreateDevice is not in the link"). That item is S5-boot's. archive-list and the later
    milestones come after it.
  - The largest fault is 28 x `stat My Games/FalloutNV/FalloutPrefs.ini` ENOENT. On a PC the launcher writes that
    file. On the Switch nothing does yet (see next).
* **tests** - test_datapath.py 10 OK, test_tools.py OK (4 skipped), tests/s4 72 tests with 0 failed (new
  game_dir_outside_root and first_run_profile), tests/s7 16 tests with 0 failures, tests/gates/test_ready.py OK, V1 W1-W8 ok.
  - os_guest.c and boot.cpp are only syntax-checked, with the Linux cross g++. The ILP32 devkitA64 build is farm
    work (full1).
  - The `game_dir=` parser passed 7 cases in a scratch harness.
* **blocked (orchestrator)** - The full Data/ is not in this container.
  - The token on `gamedata/gog` is stale: lgogdownloader 3.12 (the Ubuntu package) says "Unable to read email and
    password". J-0149 says the refreshed token was not pushed.
  - Building lgogdownloader from source was refused by this session's permission check.
  - Please push the refreshed `galaxy_tokens.json` to `gamedata/gog`. Or put the owner's install (Data/ and
    Fallout_default.ini, about 9.5 GB) in the private repo as `gamedata/data` (LFS or split tars), or on a branch
    this session can fetch.
  - Meanwhile I use the exe and the synthetic install.
* **next (S5-data)**
  1. With the real Data/: `FARM_GAME=<install> tests/s5/farm_boot.sh <c12> <out>`, then walk ini-read through
     masters-loaded (queue 3: BSA open and read, ESM parse).
  2. Measure profile.cpp directly on the farm's next guest build (full1). Expected: 11 file faults.
  3. renderer-init: wait for S5-boot's D3D9.DLL fix, or boot a guest linked with D3D9=null, to reach archive-list.
  4. V1's new W9/W10/W13 (stlr, ldaxr/stlxr stores; integration e9e1981) pass on this branch as well, so there is
     nothing to do there.
