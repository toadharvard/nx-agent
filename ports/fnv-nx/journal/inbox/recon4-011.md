### J-XXXX | 2026-11-07 | stream: R4 + S4-platform (claude/fnv-recon4) -> S4 queue 1-4 | status: wip | parent: recon4-010

* **did**
  - **2: `_Locinfo_ctor` takes `const std::string&`** (a073d5d). fnvnx_locinfo_ctor reads the name through the VC9
    string's c_str() (inline below 16, heap pointer from 16). crt_symbols.tsv and RUNTIME.md now give the signature.
    R2 can bind InitLocaleInfo38 / GvLocinfo / DestroyLocaleInfo38. The s4 test passes strings, including a 19-char
    heap name.
  - **4: Bink** (1183c33). BinkOpen already failed the way a missing .bik does: it returns NULL with
    ERROR_FILE_NOT_FOUND. V1 BINK-1 (tests/v1/test_s2c_s4_batch.py, run with the exe) checks that every call site of
    the exe's BinkOpen slot null-tests the handle, so the movies are skipped. It was counted as FNVNX_UNIMPL in
    #3. It is FNVNX_NOOP now, like Steam and Winsock. No decoder. test_plat uses a test-local unimpl entry instead.
  - **3: CopyMemoryBlock recursion** (a99e006). tools/link/call_cycles.py walks the direct calls and tail branches
    from memcpy, memmove, _memcpy, __memcpy, xmemcpy, FUN_00401460 and the __fnv_ aliases (0x00401460, 0x00ec44d0,
    0x00ec7230). It folds aliases by address, prints names only, and exits 1 on a cycle. Today FUN_00401460 is
    msvc_names.c's forwarder to libc memmove, and newlib's memmove calls nothing of the image: no cycle is possible.
    Run it on the guest once recon/bsmenu_font's body applies; I have no guest here.
  - **1: plat metric.**
    - No plat-lane report exists yet: quick lane D needs a passing build, and qk2's last build exited 128.
    - The newest raw boot (link/0540aff0d154 boot/emu.log) had 28 file faults, 20 distinct.
    - Fixed in the emulator harness, two causes:
      - The emulated card had no switch/fnv without --game (a5a4656). The farm boots pass only
        `--file switch/fnv/FalloutNV.exe`, so every mkdir of the profile (user/, Documents/My Games/FalloutNV,
        AppData/Local/FalloutNV, drive_c) failed with ENOENT: 9 distinct. DataPath now makes <root>/switch/fnv. An
        install is recognised by entries other than the port's, so a second DataPath still finds the exe.
        test_datapath has a farm-layout case.
      - The case probe statted sdmc:/switch/fnv/guest.nso, which the card lacked (100978b). emu_guest links the
        guest there; the probe's answer is unchanged.
    - Left, and expected on a first run: plugins.txt, NVDLCList.txt, the My Games INIs.
    - Left, and a data gap: Fallout_default.ini, FALLOUT.INI and Custom.ini in the game root. The farm's gamedata
      has the exe and Data/ only; gamedata/gog holds tokens, not the install. Without Fallout_default.ini the boot
      can't reach the ini-read milestone, and SArchiveList is missing.
* **finding for NX1 / the owner** - In a PC install, Fallout_default.ini sits next to FalloutNV.exe and the game reads
  it there. NRO_CHECKLIST.md step 2 has the owner copy only FalloutNV.exe (and Data/), and the launcher doesn't check
  for the file. Suggestion: step 2 copies Fallout_default.ini too, and the launcher lists it (optional or required:
  NX1's call). Same for the farm: put the owner's Fallout_default.ini on gamedata and pass
  `--file switch/fnv/Fallout_default.ini=...` in the boot stage.
* **measured** - #4 on the raw boot: 20 distinct -> 10 expected (9 mkdir ENOENT and the guest.nso probe go). Read
  from the farm's log; the next full run confirms it. #3: BinkOpen moves to noop (-1 per run that opens a movie).
  #6: no data yet.
* **tests** - s4: 108 tests, 0 failed (twice: the locinfo change, then the Bink/test_plat change). test_call_cycles:
  0 failed. test_datapath: 13 of 14 pass; the 14th (test_table_parses) imports unicorn, which this container lacks,
  and failed the same way before the change.
* **next** - the plat lane's first report (it needs a passing quick build) for #3 / #6. The next full run's raw boot
  for #4.
