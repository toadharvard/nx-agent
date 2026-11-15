# Loader seams: what the Windows PE loader does for FalloutNV.exe, and ours

This is the owner's loader-seam audit (R4/S4, 2026-11-08). Each duty the Windows loader or process creation performs
for the game is listed against the port's loader (`loader/`, S3), its boot (`toolchain/game/boot.cpp`) and the
platform layer (`platform/win32/`). Each row gives a status and the test that holds it.

Facts of the player's exe, from its headers; no game bytes are in this file:
- image base 0x00400000;
- subsystem GUI;
- stack reserve 1 MiB, heap reserve 1 MiB;
- `.text` 0x00401000;
- data window 0x00fdf000..0x01340000 (`.rdata`, `.data`, `.tls`, `CONST`, `.rsrc`; `.reloc` is discardable);
- TLS template 0x2c4 bytes, no zero fill, `AddressOfIndex` 0x0126fd98, an empty callback list;
- 280 imports from 17 DLLs.

**Status:** pass = matches Windows for the game, with a test. fixed = differed, fixed in this audit. differs =
differs from Windows, harmless for the game (why given).

| # | duty | Windows | port | status | test |
|---|---|---|---|---|---|
| 1 | relocations | applied when the image cannot load at its preferred base | loaded at the preferred base (delta 0, nothing to apply); `fnvnx_image_relocate` applies HIGHLOW fixups to a moved image | pass | tests/s3 `reloc` configs, moved and not moved |
| 2 | IAT binding | every slot gets its export, by name or ordinal | `fnvnx_imports_bind` through S4's export table and S7's modules: 280 of 280 resolved on link-head; unnamed IAT words get trap stubs | pass | tests/s3 imports; tests/s4 `test_imports`; `make -C tests/s4 iat EXE=` |
| 3 | `_tls_index` | the main module's index, 0 | `fnvnx_image_tls_index_reset` stores 0; the file holds 0xFFBF8C85 | pass (fixed earlier) | tests/s3 `test_tls_index_reset` |
| 4 | TLS callbacks | each called with (hModule, DLL_PROCESS_ATTACH, NULL) before the entry point, and DLL_THREAD_ATTACH per thread | `fnvnx_image_tls_callbacks` lists them; boot calls every callback the fixups made native, and reports an x86 one. The exe's list is empty | fixed (listed, called) | tests/s3 `test_tls_callbacks_listed` |
| 5 | TLS data per thread | each thread's block is a copy of the template, zero fill appended | the main thread copies the image `.tls` template into `__fnvnx_tls_image` before any game initializer; each new thread copies it in rt/guest/threads.c | pass (read-reviewed) | guest-only, no host test; boot log |
| 6 | headers mapped | MZ/PE headers readable at the image base (GetModuleHandle(NULL)) | `FNVNX_MAP_HEADERS`, read-only | fixed | tests/s3 (`--headers`) |
| 7 | `.text` readable as data | code is readable: constant tables, GUIDs and DirectInput formats the game keeps in `.text` | was unmapped (S7's rgodf fault, fixed per table through `fnvnx_image_const`). Now `FNVNX_MAP_CODE`: the file's bytes, read-only, never executable, so a branch to x86 code still faults; the scanners skip it | fixed | tests/s3 `test_map_code_read_only` (bytes, protection, identical scan, imports and fixups) |
| 8 | section protections | `.rdata` read-only, `.data` RW, `.text` RX | the guest's protect is a no-op: everything mapped stays RW | differs: a write to `.rdata` faults on Windows and not here. The game makes none (it would crash on the PC) | - |
| 9 | `.reloc`, discardable sections | mapped (readable) | not mapped | differs: nothing reads them after load | - |
| 10 | `.rsrc` | mapped | mapped | pass | tests/s3 |
| 11 | init order | DLLs initialised, then TLS callbacks, then the CRT's `_initterm` over `__xi` (C) and `__xc` (C++), then WinMain | platform first (S10 early init, before the game's static initialisers); in the first initializer (`boot_banner`) the loader, the TLS callbacks, then the image's `__xi` table (`fnvnx_xi_locate` after `__xc_z`: entries with a native definition called in table order, CRT entries the platform provides natively skipped, any other reported); then `__xc` in image order (S10); then WinMain. FalloutNV.exe's 8 `__xi` entries are the static CRT's own (onexit table, `_initstdio`, `__initmbctable`, encoded pointers, a terminator, `__CxxSetUnhandledExceptionFilter`) | fixed | tests/s10 `test_xc` (`fnvnx_xi_locate`: past `__xc_z`/`__xi_a`, up to `__xi_z`, empty table, no terminator); boot log `__xi: n called, n CRT entries provided natively, n missing` |
| 12 | process heap | GetProcessHeap, HeapAlloc/ReAlloc/Free/Size; CRT `_crtheap` via HeapCreate | over malloc; HeapReAlloc(NULL) fails, HeapFree(NULL) is TRUE. HeapSize is malloc_usable_size (>= the request; Windows also allows a larger block) | pass (fixed: NULL rules) | tests/s4 `procenv_heap`, `heap_functions` |
| 13 | command line | GetCommandLineA/W stable, program name first and quoted; WinMain gets the tail | `"C:\FalloutNV\FalloutNV.exe" args`. WinMain's lpCmdLine was always "": it is now GetCommandLineA past the program name | fixed (lpCmdLine) | tests/s4 `procenv_command_line` |
| 14 | environment | a non-empty double-NUL block (PATH, TEMP, SystemRoot, USERPROFILE, APPDATA...) for the CRT's `_setenvp` and getenv | was empty; now seeded with the session variables a Windows desktop has | fixed | tests/s4 `procenv_environment_defaults`, `procenv_environment_roundtrip`, `crt_k32_environment` |
| 15 | current directory | the game folder (Steam, shortcut) | `C:\FalloutNV`; relative paths follow SetCurrentDirectoryA | pass | tests/s4 `procenv_current_directory` |
| 16 | startup info, std handles | GetStartupInfoA filled; a GUI process has no console | cb set, every other field 0/NULL (no inherited handles for `_ioinit`); std handles valid, FILE_TYPE_CHAR, writes succeed | pass | tests/s4 `procenv_startup_info`, `procenv_std_handles` |
| 17 | module identity | GetModuleHandle(NULL) = image base; GetModuleFileName = the exe's path; system DLL handles valid for GetProcAddress | 0x00400000, `C:\FalloutNV\FalloutNV.exe`; kernel32/user32 handles | pass | tests/s4 `procenv_module_identity` |
| 18 | default thread stack | CreateThread(0) uses the exe's SizeOfStackReserve (1 MiB) | 1 MiB; larger requests honoured, < 256 KiB raised | pass | tests/s4 `procenv_thread_stack` |

All 18 are pass, fixed, or a harmless difference: 10 pass, 6 fixed, 2 differ.
