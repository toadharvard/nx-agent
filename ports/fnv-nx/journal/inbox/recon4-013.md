### J-XXXX | 2026-11-07 | stream: R4 + S4-platform (claude/fnv-recon4) -> plat data from my own stub boot | status: wip | parent: recon4-012

* **did**
  - NRO_CHECKLIST.md step 2 now has the owner copy Fallout_default.ini, next to FalloutNV.exe (9aa672a).
    check_checklist: 21/21.
  - Installed unicorn 2.1.4 and capstone 5.0.9 with pip (the proxy allowed both).
  - **emu_guest --plat** (b72793a): writes 1 to platstat's fnvnx_plat_on before the guest runs, so a stub boot gives
    `plat:` lines without a PLAT=1 relink. The flag is in .bss, which crt0 never clears. Use it via
    `EMU_EXTRA=--plat` with farm_boot.sh.
  - Ran S5's stub boot myself: farm_boot.sh FARM_BOOTS=stub on link-head (= 0540aff0d154), with the owner's exe and
    Fallout_default.ini from gamedata/exe mounted as FARM_GAME.
    - It reached ini-read, winmain and renderer-init, then stopped at the null vslot 1 in RendererPacketMap (S5's
      frontier).
    - The recorder stayed off: that guest predates platstat.cpp (build.sh globs win32/*.cpp, so the next link has
      it).
    - A relink here needs DEVKITPRO (about 1 h to build), which is farm work.
  - **The platform fault on the boot path** (8bcd9b2): two stubbed faults in DirectInput's Device::SetDataFormat
    (invalid reads at 0x00ee6f18 and 0x00ee6e68).
    - Cause: the game's c_dfDIKeyboard / c_dfDIMouse tables point at GUIDs that MSVC placed in the exe's .text
      (checked against the owner's exe's section table), and the port maps the data sections only.
    - Fix: `fnvnx_image_const(p, n, buf)`. boot.cpp reads such constants from the exe file (the section table and
      the open file it already has) when the loader left the address unmapped; the platform's weak default returns
      p. dinput's guid_eq resolves both sides through it.
    - The same layout holds on the device, so the fix applies there too.
  - **Mistake, fixed**: I ran tools/fetch.sh with a directory argument by accident, and it cloned every pin (the
    decomp too) into an untracked `minimp3/` inside the tree. Nothing was committed. I moved it to `ext/`, the
    git-ignored default.
* **measured** - On the stub boot of 0540aff0d154: platform faults on the boot path 2 -> 0 expected (SetDataFormat
  for keyboard and mouse). That needs a relinked guest to confirm. #3/#4/#6 from the plat recorder: none yet,
  because the guest predates platstat.
  - The file faults of this boot (with the INI): 29 distinct, all ENOENT. Expected on a first run: plugins.txt,
    NVDLCList.txt, the My Games INIs, removals of ProgramFlow/ProgrammerOutput/Warnings.txt (log files the game
    deletes at start), ArchiveInvalidation.txt, Custom.ini, FALLOUT.INI. Missing data: the .bsa files (no Data/ on
    this card).
* **tests** - s7: 22 tests, 0 failures (new: input_dinput_format_guids_from_the_image). s4: 108, 0 failed.
* **for the full_job owner** - Add `EMU_EXTRA=--plat` to step 4b's stub boot (farm_boot.sh), and every full job
  gives #3/#4/#6 data on the boot path.
