# w15 006: import_escape.tsv from documented annotations

Rule (platform/manifest/import_escape.py): a pointer slot is `n` iff the documented prototype annotates it In, not Out,
and const (LPCSTR, LPCWSTR, const T*); every other pointer is `e` (Out/Inout, non-const In, function and interface
pointers, unannotated); non-pointer slots are `n`. Source: Windows.Win32.winmd of windows-rs 8ca69fda (sha256
a74c9df5...), mirrored as fnv-nx-build branch tools/win32metadata. The old LLVM nocapture analysis, its fixture and the
guest/posix tables are deleted.

Global numbers (bridge.tsv 1554 rows, manifest l5-platform/manifest 07d79c5): 346 rows annotated (323 exact module,
23 module-alias), 1207 unannotated (1086 COM interface methods, 103 msvcrt, 11 galaxywrp, 5 Interlocked*, 2 d3d9).
Against the previous 256 rows: 230 common (65 slots e->n, 53 slots n->e), 26 rows now unannotated (msvcrt 10, galaxywrp 11,
Interlocked 5). Slots n/e over the table: 468 / 363. kernel32!LoadLibraryA arg0 = n, GetProcAddress = `en` (arg1 n).
Note: a handle type (HANDLE, HMODULE) is declared as a pointer type, non-const, so it is `e`.

## Update (after the lead's question on unannotated rows)
No letter is a default. import_escape.tsv now has a row for every bridge row (1553): resolved 1459 = COM 1077 (interface method of the
metadata at the bridge ordinal, name equal, vtable = base interface methods then own), exact module 323, module-alias 23, no slot 36;
unresolved 94, written with `?` per slot (177 `?` slots), listed in import_escape_report.tsv: msvcrt 78 (no pinned UCRT SAL source), kernel32
Interlocked* 5 (intrinsics, not in the metadata), galaxywrp 2 (SteamAPI_RegisterCallback/UnregisterCallback: the 9 other galaxy rows have no slot),
COM 9 (IDirect3DShaderValidator9 6: undocumented interface; IUnknownCom 3: the bridge's own name of IUnknown). Slots over the table: n 1435, e 2368, ? 177.
Note for the galaxy rows: SteamAPI_RegisterCallback STORES its first argument in the registry (platform/win32/services.cpp g_steam_regs): the
old LLVM table's `nn` for it was wrong; documented Steamworks semantics (listener kept until unregistered) = e.
Receiver slot of a COM method = a pointer with no annotation: e.
