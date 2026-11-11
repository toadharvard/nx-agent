# w13 item 2: no seeding of profile files (rule: a file exists only if the exe/a module creates it or the install ships it)
profile.cpp deleted whole (it wrote four files: Fallout.ini [done, item 1], FalloutPrefs.ini, plugins.txt, NVDLCList.txt); fnvnx_first_run_profile, FNVNX_PROFILE_* and
the boot.cpp call removed. Call sites: private note fnv-nx-build l5-platform/manifest notes/l5-w13-profile-files.md.
- FalloutPrefs.ini: no exe code creates it before the first frame (the launcher writes it); the exe's own WritePrivateProfileStringA saves create it later. Absent.
  What the exe does when it is absent: WinMain's launcher gate (stored sD3DDevice default "" != adapter name) -> ShellExecuteA(<exe dir>\FalloutNVLauncher.exe)
  and WinMain returns 0 if the result is non-zero; it goes on only if ShellExecuteA returns 0 or the adapter list is empty. NOT seeded. This is the next boot item
  (platform's ShellExecuteA result / the d3d9 adapter name), for the lead to assign.
- Plugins.txt: read only (fopen "r", two sites); absent = file skipped. Never created. NVDLCList.txt: named nowhere in the exe or modules.
Tests: tests/s4/test_exe_files.cpp (replaces test_profile.cpp): fresh machine has none of the four, exe's Fallout.ini copy path, FalloutPrefs.ini read-absent then
exe-written, Plugins.txt open-absent = ERROR_FILE_NOT_FOUND. s4 508 tests 0 failed (run with -o hostapi-check: check_hostapi fails on the base claude/fnv-l5-platform
3bf9c4d6 itself, "ACCESS_FAULT_HANDLER (n=2)" and "TEXT_ENTRY (n=5)" not served by any host file, unchanged by this item); s7 89 tests 0 failed.
Left alone, stale after this: tools/lockstep/profile.py and tests/s5/menu_standins.c still describe/write the launcher's files for the Wine reference runs.
