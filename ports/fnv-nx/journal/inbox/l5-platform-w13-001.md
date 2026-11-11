# w13 item B: Fallout.ini on the declared machine — case (1)
The exe creates Fallout.ini itself: SHGetFolderPathA(CSIDL_PERSONAL), CreateDirectoryA "My Games" and "FalloutNV", GetFileAttributesA (CRT _access),
then CopyFileA("Fallout_default.ini", ini, bFailIfExists=1). All six calls are implemented by the platform (functions: SHGetFolderPathA,
CreateDirectoryA, DeleteFileA, CopyFileA, GetFileAttributesA, GetPrivateProfileIntA). The declared machine has no Fallout.ini on a fresh install.
Change: profile.cpp no longer seeds Fallout.ini; FNVNX_PROFILE_INI removed; test_profile.cpp checks absence + new test exe_creates_fallout_ini
(the exe's call sequence on the platform, byte copy, second copy fails). Call sites: private note fnv-nx-build l5-platform/manifest notes/l5-w13-fallout-ini.md.
Not touched (launcher files, out of this item): FalloutPrefs.ini and plugins.txt are still seeded by profile.cpp.
Results: s4 506 tests, 0 failed; s7 84 tests, 0 failed.
