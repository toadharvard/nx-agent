### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> loader-seam audit 18/18; correction on c91e58ce | status: done (farm boot to confirm) | parent: recon4-023

* **Loader-seam audit (owner's request): docs/LOADER_SEAMS.md, 18 duties, 10 pass, 6 fixed, 2 harmless differences.**
  - **`.text` readable as data (3afc1102).** FNVNX_MAP_CODE maps code sections read-only and never executable, and boot maps the PE headers too. The code-pointer scans, data lookups and the reloc list skip them. tests/s3 test_map_code_read_only.
  - **TLS callbacks (7d23f66a).** Listed (fnvnx_image_tls_callbacks), and the native ones are called before the entry point. The exe's list is empty. tests/s3 test_tls_callbacks_listed.
  - **WinMain lpCmdLine** is now the tail of GetCommandLineA.
  - **Environment and heap (4e5db6d3, Sonnet worker, reviewed).** The environment block is seeded with the Windows session variables. HeapReAlloc(NULL) and HeapFree(NULL) follow Windows. tests/s4 test_procenv.cpp, 9 tests. The worker's per-block heap header was not taken: LocalAlloc and CRT blocks can reach HeapFree.
  - **`__xi` C initializers (8d9a91d8).** fnvnx_xi_locate (startup/guest/xc.c, tests/s10) finds the table, and boot runs it before `__xc`. The exe's 8 entries are the static CRT's own, provided natively, so none is called.
  - **Differences:** every mapping is RW in the guest, and .reloc is not mapped.
  - **Tests:** tests/s3 25 OK, tests/s4 180 passed, tests/s10 0 failed. The boot changes take effect with the next guest link, and a farm boot should confirm them (the image window now holds .text).
* **Correction to c91e58ce (ini.cpp on libc malloc).** Its message says S5's sanitizer saw 26,466 bad frees from ini.cpp and expects them to go to 0. S5 has since found that its sanitizer missed most allocations: return hooks at already-translated addresses never fired (fixed in 8f8e609b, 6ed7e2a0). With the fix, the D26 boot shows 0 overlaps, 0 unknown-block frees and 0 ini.cpp rows. So the move fixed no live bug. It stays on its merits: kernel32's profile cache doesn't belong in the game's operator new heap.
