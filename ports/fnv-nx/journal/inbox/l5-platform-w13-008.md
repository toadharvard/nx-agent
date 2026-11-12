# l5-platform-w13-008: file-set census on the current engine, decisions applied

- `platform/manifest/filesites.py` now runs on the current `regsites.py` engine (typed object fields, value sets, wrapper depth 6) by swapping the role
  table, the row builder and the out-buffer hook for the duration of the call; `fileset_classes.py` classifies; `check_filesites.py` is the selftest
  (synthetic image: constant name, wrapper through callers, directory-API buffer with an unresolved suffix, object field, directory create, LoadLibrary
  of an absent and of a platform module, a console device, and the domain gate). Wired into the s4 `regsites-check` target.
- Global numbers (private tables, fnv-nx-build `l5-platform/manifest` ef40779d): 396 sites; static 75, dynamic 321; FalloutNV.exe 130 rows, 18 static.
  Classes: device 5, module 30, sysmod 27, i 1, ii 4, iv 7, iii 0, unresolved 322. The typed-field engine resolves no further exe file name.
- Class (i) listing: the installed Data files from the gamedata/data branches (names only). Fallout_default.ini's file lists against it: 17 present, 10 absent.
- AppData/Local/FalloutNV is no longer created by the platform (`fnvnx_path_reset`); Documents/My Games/FalloutNV, AppData/Local/Temp and drive_c remain.
- Doubles: host_gpu removed (previous note); the remaining five double files all stand on the libnx/guest boundary and wait for L4 item 11 and L1 w15.
