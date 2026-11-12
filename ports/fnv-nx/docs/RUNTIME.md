# fnv-nx runtime (stream S4)

What the game expects from the platform that the decomp does not define, how each piece is classified, and how
`platform/` provides it. Measured on decomp `87eb9373` (`build/units.json`). Machine-readable tables:
`crt_symbols.tsv` (deleted, no name-based table) (every undefined `.text` address: class,
identification, provider) and [`platform/win32/imports.tsv`](../platform/win32/imports.tsv) (every IAT slot).
This file lists names and addresses only; it contains no decomp code.

## Counts

**Correction to J-0002.** The census counted 10,805 undefined `.text` addresses because it compared address
*strings*: units write the same address as `<exe address>` and `<exe address>`. Compared as integers, units.json has
94,513 referenced addresses (not 97,575) and **4,618** referenced `.text` addresses (`0x401000..0xfde577`) without
a definition; 5,923 of the "undefined" ones were defined functions spelled in upper case.

| class | addresses | what it is | runtime provides |
|---|---:|---|---|
| `eh` | 4,238 | `__ehhandler$<function>` stubs (MSVC C++ EH funclets, 0x00ee..0x00f3) | nothing: GCC emits its own unwind tables |
| `atexit_dtor` | 121 | ``dynamic atexit destructor for 'x''`` (`??__F`, 0x00fc..0x00fd) | nothing: GCC registers static destructors itself |
| `crt` | 117 | MSVC 2008 static C runtime (libcmt, <exe address>..<exe address>) | libc / libm, or `platform/crt` for MSVC-only or MSVC-different functions |
| `cpplib` | 30 | MSVC C++ library internals (libcpmt, <exe address>..<exe address>): `std::_Lockit`, `_Xlen`, `ios_base`, locale, `std::exception` | 4 in `platform/crt`, rest listed as next |
| `helper` | 27 | compiler helpers: `_ftol2(_sse)`, `_CIsin`..., `_allmul`..., `_alloca_probe`, `__security_check_cookie`, EH vector iterators, `__RTDynamicCast`, `_CxxThrowException`, SEH handlers | the 8 the source calls **by name** (`platform/crt/helpers.cpp`); the rest are implicit and vanish with GCC |
| `crt_inline` | 8 | out-of-line copies of CRT header inlines in game code (`floor(float)`, `strchr(char*,int)`, ...) | libc/libm (`floorf`, `strchr`, ...) |
| `thunk` | 11 | `jmp [__imp_x]` stubs of ws2_32 (<exe address>..<exe address>) | `fnvnx_ws2_*` |
| `body_named` | 20 | game/engine functions with a real name but no body in the decomp | **needs decompiling** (list below) |
| `body_unnamed` | 33 | `FUN_xxxxxxxx` / `*_shell` / `GlobalObject*` names only | **needs decompiling** (list below) |
| `mid_named` | 3 | named functions whose address lies inside the recorded size of the previous function | **needs decompiling** (the recorded size is probably wrong) |
| `mid_data`, `data` | 10 | data symbols (`g_..._arg1`, `$LN85`, `UNK_`) whose "address" is a jump-table label or a garbage value | nothing; S2 should drop them |
| **total** | **4,618** | | |

Of the 259 addresses that are neither EH funclets nor atexit destructors, 243 are referenced by name from the source (an identifier of the address appears in
`src/` or `include/`), so their GCC objects will have undefined symbols that S2 resolves to the provider in
`crt_symbols.tsv`. The implicit helpers (`_allmul`, `_CIcos`, `_alloca_probe`, EH funclets, atexit destructors)
are only in units.json because MSVC emitted them; GCC objects never reference them.

**Imports.** 288 decomp names point at **186** IAT slots (<exe address>..<exe address>, plus 2 mislabelled data
addresses). By DLL: kernel32 78, user32 30, d3dx9_38 22, steam_api 11, binkw32 9, ws2_32 8, ole32 7, advapi32 5,
vorbisfile 5, shell32 2, d3d9/dinput8/dsound/gdi32/winmm/xinput1_3 1 each, 1 unassigned. Providers: **140 slots
implemented here** (`platform/win32`), 32 belong to S6 (d3d9, d3dx9, Bink), 8 to S7 (DirectInput, DirectSound,
XInput, vorbisfile), 4 slots have no trustworthy name in the decomp, 2 are not IAT slots.

The decomp names IAT slots inconsistently: **31 slots** carry more than one name, e.g. slot `<exe address>`
(InterlockedIncrement, 236 units) is called `EnterCriticalSection` by one unit and `<exe address>`
(InterlockedDecrement) is called `LeaveCriticalSection`, `TryEnterCriticalSection`, `IsBadCodePtr` and `free` by
others. Matching only needs the address, so these units compile to the right bytes on MSVC but their source calls
the wrong function. **The authoritative name of a slot is in the import directory of the player's
`FalloutNV.exe`**; S2/S3 should read it and resolve each slot with `fnvnx_win32_lookup_export(dll, name)`
(`platform/win32/exports.cpp`) rather than trust the decomp's spelling. The 31 slots are listed with their
alternative names in `imports.tsv`.

## Method

`refs_units.txt` = every (name, address, unit) of `units[].symbols`; defined = `units[].functions[].address`, both
compared as integers. Names demangled with `llvm-undname` (MSVC C++) and by hand for C/`stdcall` names. Rules, in
order: all names `__ehhandler$`/`__unwindfunclet$` -> `eh`; address in the hand-made CRT/C++/helper table (built
from the names the decomp gives, call sites, signatures and the CRT layout, e.g. each `f`/`_CIf` math pair is 0x40
apart) -> `crt`/`cpplib`/`helper` with how = `n` (named by the decomp), `i` (inferred) or `u` (unidentified, a
candidate is given); out-of-line header inlines -> `crt_inline`; ws2_32 stubs -> `thunk`; data-variable names only
(`@@3` storage class, `$LN`) -> `data`/`mid_data`; only `??__F` names -> `atexit_dtor`; else inside a defined
function's recorded `[address, address+size)` -> `mid_named`; else `body_named` when at least one name is not a
placeholder (`FUN_`, `*_shell`, `*_cls`, `GlobalObject*`), otherwise `body_unnamed`. Explicit source use counts
whole-word identifier hits per file in `src/` + `include/`. Regenerate with the scripts described in the S4 journal
entry (jq + Python over units.json; nothing is copied from the decomp).

The 12 CRT entries marked `u` and the 15 libcpmt internals cannot be named for certain from the decomp alone; the
CRT is linked as one block, so a byte comparison against a stock VC9 SP1 `libcmt.lib` with the player's exe would
settle them (next step).

## What the runtime provides

### From the target libc/libm (no code here)

`memcpy` (also `memmove`; VC9 has one implementation), `memcmp`, `memset`, `memchr`, `strlen`, `strcpy`, `strcat`,
`strcmp`, `strncmp`, `strncpy`, `strchr`, `strrchr`, `strstr`, `strpbrk`, `strtok`, `strtol`, `strtod`, `atoi`,
`atol`, `atof`, `tolower`, `toupper`, `isalpha`, `isdigit`, `isspace`, `isalnum`, `abs`, `qsort`, `bsearch`,
`malloc`, `realloc`, `free`, `exit`, `sprintf`, `vsprintf`, `printf`, `vprintf`, `fprintf`, `sscanf`, `fclose`,
`fread`, `fwrite`, `fseek`, `ftell`, `fgets`, `fgetc`, `fputc`, `ungetc`, `feof`, `fflush`, `fgetpos`, `fsetpos`,
`strerror`, `sqrt`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `exp`, `pow`, `fmod`, `floor`, `ceil`,
`fabs`, `floorf`, `ceilf`, `cosf`, `acosf`; `std::uncaught_exception` from libstdc++.

### `platform/crt` (MSVC semantics; `fnvnx_msvcrt.h`)

* strings: `_stricmp`, `_strnicmp`, `_strcmpi`, `_stricoll`, `_mbscmp`, `_mbsicmp`, `_mbsnicmp`, `_strlwr`,
  `_strupr`, `_strlwr_s`, `_strupr_s`, `_strdup`, `strcpy_s`, `strcat_s`, `strncpy_s`, `strncat_s`, `strtok_s`,
  `memcpy_s`, `memmove_s`, `wcstombs_s`, `fnvnx_wcslen16` (for `wcslen`: WCHAR is 16-bit);
* numbers: `_itoa`, `_ltoa`, `_ultoa`, `_i64toa`, `_ui64toa`, `_itoa_s`, `_ltoa_s`, `_ultoa_s`, `_atoi64`,
  `_strtoi64`, `_strtoui64`, `_gcvt`, `fnvnx_ftol2`;
* formatting: `_snprintf`, `_vsnprintf` (no terminator when full, -1 on truncation), `sprintf_s`, `vsprintf_s`,
  `_snprintf_s`, `_vsnprintf_s`, `_scprintf`, `_vscprintf`; `%I64d/u/x` and `%I32` are accepted;
* paths/files (Windows paths through the mapper): `fnvnx_fopen` (for `fopen`), `fnvnx_fopen_s`, `fnvnx_fsopen`,
  `fnvnx_remove`, `fnvnx_rename`, `fnvnx_access` (`_access`), `fnvnx_access_s`, `fnvnx_mkdir`, `fnvnx_rmdir`,
  `fnvnx_unlink`, `fnvnx_getcwd`, `fnvnx_chdir`, `fnvnx_stat` (`_stat`, MSVC `_stat64i32` layout, 48 bytes),
  `_fseeki64`, `_ftelli64`, `fnvnx_fileno`, `_splitpath`, `_splitpath_s`, `_makepath`, `_makepath_s`;
* floating point: `_finite`, `_isnan`, `_chgsign`, `_copysign`, `_hypot`, `_hypotf`, `_logb`, `_nextafter`,
  `_scalb`, `_controlfp`, `_control87`, `_controlfp_s`, `_clearfp`, `_statusfp`, `_fpreset`;
* heap/runtime: `_msize`, `_expand`, `_heapchk`, `_recalloc`, `_aligned_malloc`, `_aligned_offset_malloc`,
  `_aligned_realloc`, `_aligned_free`, `_aligned_msize`, `_errno`, `_invalid_parameter_noinfo`, `_purecall`,
  `fnvnx_atexit`, `fnvnx_doexit`, `fnvnx_clock` (`clock`, CLOCKS_PER_SEC 1000), `fnvnx_rand`/`fnvnx_srand`
  (`rand`/`srand`: MSVC LCG, RAND_MAX 0x7FFF, per thread), `_time64`;
* helpers called by name: `fnvnx_security_check_cookie`, `fnvnx_eh_vector_ctor`, `fnvnx_eh_vector_dtor`,
  `fnvnx_eh_vector_copy_ctor`, `fnvnx_rt_dynamic_cast` (+ `fnvnx_rtti_register`), `fnvnx_cxx_throw_exception`,
  `fnvnx_lockit_ctor`, `fnvnx_lockit_dtor`, `fnvnx_xlength_error`, `fnvnx_xout_of_range`.

Functions named `fnvnx_*` exist because the libc function of the MSVC name behaves differently (paths, `rand`,
`clock`, 16-bit `wcslen`) or because newlib uses the underscore name for its system-call layer (`_unlink`,
`_stat`, ...); S2 maps the CRT address (or the name in a GCC object) to the `fnvnx_` symbol.

### `platform/win32` (the Win32 subset; `exports.cpp` lists every export by DLL)

kernel32: critical sections (`Initialize[AndSpinCount]`, `Enter`, `TryEnter`, `Leave`, `Delete`), `Interlocked*`
(incl. `*Pointer`), `CreateEventA`, `CreateMutexA`, `CreateSemaphoreA`, `SetEvent`, `ResetEvent`, `ReleaseMutex`,
`ReleaseSemaphore`, `WaitForSingleObject`, `WaitForMultipleObjects`, `CloseHandle`, `CreateThread`,
`ResumeThread`, `SuspendThread`, `ExitThread`, `GetExitCodeThread`, `Set/GetThreadPriority`,
`SetThreadPriorityBoost`, `SetThreadAffinityMask`, `SetThreadIdealProcessor`, `GetCurrentThread[Id]`,
`GetCurrentProcess[Id]`, `Sleep`, `SleepEx`, `SwitchToThread`, `Tls{Alloc,Free,GetValue,SetValue}`,
`Get/SetLastError`, `GetTickCount`, `QueryPerformanceCounter/Frequency`, `GetLocalTime`, `GetSystemTime`,
`GetSystemTimeAsFileTime`, `FileTimeToSystemTime`, `SystemTimeToFileTime`, `FileTimeToLocalFileTime`,
`SystemTimeToTzSpecificLocalTime`, `CompareFileTime`, `CreateFileA`, `ReadFile`, `WriteFile`,
`SetFilePointer[Ex]`, `GetFileSize[Ex]`, `GetFileTime`, `SetEndOfFile`, `FlushFileBuffers`, `DeleteFileA`,
`CreateDirectoryA`, `RemoveDirectoryA`, `MoveFileA`, `CopyFileA`, `GetFileAttributesA`, `FindFirstFileA`,
`FindNextFileA`, `FindClose`, `Get/SetCurrentDirectoryA`, `GetFullPathNameA`, `GetPrivateProfileStringA`,
`GetPrivateProfileIntA`, `GetPrivateProfileSectionA`, `GetPrivateProfileSectionNamesA`,
`WritePrivateProfileStringA`, `WritePrivateProfileSectionA`, `VirtualAlloc`, `VirtualFree`, `VirtualQuery`,
`GetProcessHeap`, `HeapCreate/Destroy/Alloc/ReAlloc/Free/Size`, `LocalAlloc`, `LocalFree`, `GlobalMemoryStatus[Ex]`,
`GetSystemInfo`, `GetModuleHandleA`, `GetModuleFileNameA`, `LoadLibraryA`, `GetProcAddress`, `FreeLibrary`,
`GetCommandLineA/W`, `ExitProcess`, `OutputDebugStringA`, `IsDebuggerPresent`, `DebugBreak`, `RaiseException`
(SetThreadName), `RtlUnwind` (fatal), `IsBadCodePtr/ReadPtr/WritePtr`, `GetComputerNameA`, `FormatMessageA`,
`GetEnvironmentVariableA`, `lstrcpyA`, `lstrcatA`, `lstrlenA`, `lstrcmpiA`; plus `__readfsdword` (fake TEB).
user32/gdi32: `RegisterClassA`, `CreateWindowExA`, `DestroyWindow`, `IsWindow`, `FindWindowA`, `DefWindowProcA`,
`SendMessageA`, `PostMessageA`, `PostQuitMessage`, `PeekMessageA`, `TranslateMessage`, `DispatchMessageA`,
`ShowWindow`, `UpdateWindow`, `SetForegroundWindow`, `GetForegroundWindow`, `GetActiveWindow`, `SetFocus`,
`GetFocus`, `GetWindow`, `IsIconic`, `SetWindowTextA`, `EnumChildWindows`, `Get/SetWindowLongA`,
`AdjustWindowRect[Ex]`, `GetClientRect`, `GetWindowRect`, `SetWindowPos`, `ClientToScreen`, `ScreenToClient`,
`GetSystemMetrics`, `ShowCursor`, `Set/GetCursorPos`, `ClipCursor`, `SetCapture`, `ReleaseCapture`,
`GetAsyncKeyState`, `GetKeyState`, `GetDoubleClickTime`, `SwapMouseButton`, `SendInput`, `SetWindowsHookExA`,
`UnhookWindowsHookEx`, `CallNextHookEx`, `LoadIconA`, `LoadCursorA`, `GetStockObject`, `DeleteObject`,
`MessageBoxA`. advapi32: `RegOpenKeyExA`, `RegCreateKeyExA`, `RegQueryValueExA`, `RegSetValueExA`, `RegCloseKey`.
shell32: `CommandLineToArgvW`, `ShellExecuteA`, `SHGetFolderPathA`. ole32: `CoInitialize[Ex]`, `CoUninitialize`,
`CoCreateInstance`, `CoCreateGuid`, `CoTaskMemAlloc/Free`, `CoFreeUnusedLibraries`. winmm: `timeGetTime`,
`timeBegin/EndPeriod`. ws2_32 (as `fnvnx_ws2_*`): `WSAStartup`, `WSACleanup`, `WSAGetLastError`, `socket`,
`closesocket`, `bind`, `listen`, `accept`, `connect`, `send`, `recv`, `setsockopt`, `ioctlsocket`,
`WSAAsyncSelect`, `htons`, `ntohs`, `inet_addr`, `inet_ntoa`, `gethostbyname`, `gethostname`. steam_api:
`SteamAPI_Init` (false), `SteamAPI_IsSteamRunning`, `SteamAPI_Shutdown`, `SteamAPI_RunCallbacks`,
`SteamAPI_Register/UnregisterCallback`, `SteamUser`, `SteamFriends`, `SteamUtils`, `SteamApps`, `SteamUserStats`
(NULL).

Not here: d3d9, d3dx9_38 and binkw32 (S6), dinput8, dsound, xinput1_3 and vorbisfile (S7); they register through
`fnvnx_register_module` (LoadLibrary/GetProcAddress) or `fnvnx_register_com_class` (CoCreateInstance).

## Semantics that matter

* **Critical sections** live inside the game's 24-byte `CRITICAL_SECTION` (static-asserted on ILP32): the lock word
  is the first 32 bits of `LockSemaphore` (os lock: futex on Linux, libnx `Mutex` on Horizon), `OwningThread` holds
  the owner's thread id, `RecursionCount` the depth, `LockCount` is -1 free / 0 held. Recursive; all-zero storage is
  an unlocked section, so sections the engine zero-fills without `InitializeCriticalSection` work.
* **Waits**: one lock guards all waitable state; a waiting thread links itself to each object and sleeps on its own
  condition variable, signalling wakes only the linked threads. Auto-reset events release one waiter, manual-reset
  all; mutexes are recursive with owner checks; semaphores enforce the maximum; thread handles signal on exit.
  Named objects share one namespace (`ERROR_ALREADY_EXISTS` for the single-instance check).
* **Handles** are table indices with a generation (multiples of 4, never NULL/-1/-2): stale or double-closed
  handles fail with `ERROR_INVALID_HANDLE`. Pseudo handles: process -1, thread -2.
* **Threads**: ids are multiples of 4 from 0x1000; `CREATE_SUSPENDED` defers the start; `SuspendThread` of a
  running thread works on Horizon (`svcSetThreadActivity`) and is a logged no-op on the host. Win32 priorities map
  to Horizon 0x2C - p (clamped 0x18..0x3B); ideal processor/affinity map to cores 0-2. Default stack 1 MiB, never
  below 256 KiB. `RaiseException(0x406D1388)` (SetThreadName) names the thread and returns; any other code is fatal.
* **TLS**: 1088 `TlsAlloc` slots. The image's implicit TLS (`__declspec(thread)` data of FalloutNV.exe, reached as
  `((char**)__readfsdword(0x2c))[_tls_index] + off` in 15 places) gets a per-thread copy of the template the loader
  passes to `fnvnx_tls_set_template`. The loader stores 0 in the image's `_tls_index` (`fnvnx_image_tls_index_reset`, called by toolchain/game/boot.cpp: FalloutNV.exe holds 0xFFBF8C85 there on disk).
* **Fake TEB** for `__readfsdword`: 0x00 SEH chain (-1), 0x18 self, 0x20 pid, 0x24 tid, 0x2c TLS array, 0x30 PEB
  (zeroed: not being debugged), 0x34 last error, 0xE10.. TlsSlots[64]; other offsets read 0 and are logged.
* **Time**: `QueryPerformanceCounter/Frequency` read the same counter as S8's `__rdtsc`: CNTVCT_EL0 / CNTFRQ_EL0
  (19.2 MHz) on Horizon via `armGetSystemTick`/`armGetSystemTickFreq`, so Havok's calibration of rdtsc against QPC
  is exact. Host: 10 MHz monotonic. `GetTickCount`/`timeGetTime`: ms, 32-bit wrap.
* **x87 control word**: `_controlfp`/`_control87` keep MSVC's abstract control word; the rounding-control field is
  written to AArch64 **FPCR.RMode** (RN/RP/RM/RZ for `_RC_NEAR/UP/DOWN/CHOP`), so S8's `fistp` replacement (rounds
  in the current mode) follows the game's requests. Precision control (`_MCW_PC`) is accepted, remembered and has no
  effect; exception masks are remembered, traps never enabled; `_DN_FLUSH` sets FPCR.FZ. **The game must be compiled
  with `-frounding-math`**, otherwise GCC constant-folds conversions and `rint`-like operations assuming
  round-to-nearest (the host test caught exactly that).
* **INI** (`GetPrivateProfile*`): case-insensitive sections/keys, first match wins, `;` comments, values trimmed
  and unquoted, a key without `=` is an empty value, NULL section/key list names (`size-2` on truncation), defaults
  lose trailing blanks, `GetPrivateProfileInt` parses like `RtlUnicodeStringToInteger` base 0 (sign, `0x/0o/0b`,
  stops at the first non-digit, empty -> default), a bare file name means `C:\Windows\`. Files are cached by host
  path and re-read when size/mtime change; writes go through immediately, keep comments, line order and CRLF,
  insert new keys after the last key of the section and new sections at the end.
* **Virtual memory**: reservations 64 KiB aligned, commit per 4 KiB page, `MEM_RELEASE` needs base and size 0,
  `VirtualQuery` reports committed/reserved runs. Horizon: reservations are carved from the alias region and
  commit/decommit are `svcMapPhysicalMemory`/`svcUnmapPhysicalMemory`.
* **Modules**: `GetModuleHandleA(NULL)` = `0x00400000` (the original image base, where S3 maps the data image);
  other DLLs only exist if registered with `fnvnx_register_module`. Command line:
  `"C:\FalloutNV\FalloutNV.exe" <args>`; `CommandLineToArgvW` follows the shell32 quoting rules.
* **Windows**: one process-wide message queue; `SendMessage` calls the window procedure; `FindWindowA` only sees
  windows the game created (so the single-instance check passes); `MessageBoxA` logs and returns the default
  button; display size from `fnvnx_set_display_size` (S6), keys and cursor from `fnvnx_set_key_state` /
  `fnvnx_set_cursor_pos` / `fnvnx_post_message` (S7). Registry: a persistent hive file
  (`C:\FalloutNV\registry.hiv`, `registry.cpp`). The hive holds exactly the keys the exe and the modules read, found
  statically from their call sites (`platform/manifest/regsites.py`, run by `pemanifest.py`: root, key and value name by
  data-flow, wrapper arguments from the callers; the table is the private `regsites.tsv`). Values come from the declared
  machine, `platform/manifest/declared_registry.tsv` (`Installed Path` = the game directory, `W32_GAME_DIR` + `\`); on the
  first boot, when the hive does not exist, `boot.cpp` makes it from those rows (`registry_seed.cpp`, rows generated into
  the build by `gen_registry_seed.py`, which refuses a declared row that no static read site reads). A key with no declared
  value is absent, ERROR_FILE_NOT_FOUND. No installer script and no seed file.
* **Wide characters**: the game's `WCHAR`/`wchar_t` is 16-bit (`-fshort-wchar`); the runtime is built the same way
  and never passes game wide strings to libc wide functions.
* **CRT `rand`** must be MSVC's (the engine divides by 32767); `clock` counts milliseconds.

## Paths

The game sees a virtual Windows file system; `platform/win32/path.cpp` maps it onto one root directory:

| Windows path | host (Switch SD) | contents |
|---|---|---|
| `C:\FalloutNV\` (cwd at start, `GetModuleFileName`) | `<root>/` itself (`sdmc:/switch/fnv/` in the game guest, next to fnv.nro, as in a PC install) | the player's install: `Data\`, `Fallout_default.ini`, `FalloutNV.exe` (for S3) |
| `C:\Users\Player\` | `<root>/user/` (`sdmc:/switch/fnv/user/` in the game guest) | `Documents\My Games\FalloutNV\` (Fallout.ini, FalloutPrefs.ini, Saves\), `AppData\Local\FalloutNV\` (plugins.txt) |
| any other `X:\...` | `<root>/drive_x/...` | sandbox (e.g. `C:\Windows\` for bare INI names) |

The root is `sdmc:/switch/fnv` (FNVNX_SD_DIR of `toolchain/include/fnvnx/sdlayout.h`: the guest and a native Horizon
build) and `$FNVNX_ROOT` (default `./fnvnx-root`) on the host; more mounts can be
added with `fnvnx_path_mount`. Normalisation as Win32: `\` and `/` both separate, `.`/`..` resolved lexically
(`..` stops at the drive root), repeated separators collapse, trailing dots and spaces of a component are dropped,
`\\?\` prefixes are accepted, UNC paths fail, relative and drive-rooted paths use the virtual cwd. Each existing
component is matched case-insensitively against the host directory (exact spelling first); missing components keep
the game's spelling so new files are created as named. The SD card (FAT32/exFAT) folds case itself, so the
directory scan only runs on case-sensitive hosts. `FindFirstFile` returns `.`/`..` (except at a root) and sorts
case-insensitively like NTFS.

## Undefined code that needs decompiling (56)

The table of this section pairs exe addresses with names found in the decomp, so it is kept in the private artifacts repository (fnv-nx-build, branch l5-platform/manifest, tables/runtime_doc_sections.md; pin: tools/tables.pin.json). The public text keeps only the rule: the platform provides a body only where an import fact or an export table of a DLL the exe loads names it; no exe address is bound to a body by name.

## Data symbols at code addresses (10)

The table of this section pairs exe addresses with names found in the decomp, so it is kept in the private artifacts repository (fnv-nx-build, branch l5-platform/manifest, tables/runtime_doc_sections.md; pin: tools/tables.pin.json). The public text keeps only the rule: the platform provides a body only where an import fact or an export table of a DLL the exe loads names it; no exe address is bound to a body by name.

## CRT, C++ library, helpers, inlines and thunks (193)

The table of this section pairs exe addresses with names found in the decomp, so it is kept in the private artifacts repository (fnv-nx-build, branch l5-platform/manifest, tables/runtime_doc_sections.md; pin: tools/tables.pin.json). The public text keeps only the rule: the platform provides a body only where an import fact or an export table of a DLL the exe loads names it; no exe address is bound to a body by name.

## Imports (186 IAT slots)

The table of this section pairs exe addresses with names found in the decomp, so it is kept in the private artifacts repository (fnv-nx-build, branch l5-platform/manifest, tables/runtime_doc_sections.md; pin: tools/tables.pin.json). The public text keeps only the rule: the platform provides a body only where an import fact or an export table of a DLL the exe loads names it; no exe address is bound to a body by name.

## For other streams

* **S1 (shim)**: `fnvnx_win32.h` cannot be included from C (`typedef const GUID &REFGUID` is outside
  `#ifdef __cplusplus`), so the C parts of the runtime repeat a few prototypes. The shim lacks declarations for
  imported APIs the runtime implements: `platform/include/fnvnx_win32_ext.h` has them (memory status,
  `SetFilePointerEx`, `GetExitCodeThread`, `ExitThread`, `RaiseException`, registry, ole32, shell32, winmm, the
  user32 extras, error codes, `CREATE_SUSPENDED`, `MB_*`, `GWL_*`, `HEAP_*`, ...) behind `#ifndef` guards and can be
  merged into the shim. `__rdtsc` is now S8's (`fnvnx_asm.h`); the runtime does not define it. Arg-less explicit
  calls of register helpers (`FUN_00ec62c0()` = `_ftol2_sse` with its operand in ST0, 29 files; `helper_value()` =
  `_CIsqrt`, 8 files) cannot be functions on AArch64 and need prepare rules (S1/S8); calls that pass the operand map
  to `fnvnx_ftol2`. Game code must be compiled with `-fshort-wchar` and `-frounding-math`.
* **S2 (link)**: resolve undefined CRT/helper addresses with the `provider` column of `crt_symbols.tsv`; resolve
  IAT slots by the exe's import directory and `fnvnx_win32_lookup_export`; drop `eh`, `atexit_dtor`, `data`,
  `mid_data` references; map the ws2_32 thunks to `fnvnx_ws2_*`. The decomp's `__RTDynamicCast` calls pass MSVC
  TypeDescriptor addresses: generate `fnvnx_rtti_register(td, &typeid(T))` for every `??_R0?AV<T>@@@8`.
* **S3 (data image)**: call `fnvnx_tls_set_template` with the exe's TLS directory (start, end, zero fill) and
  write 0 to the image's `_tls_index`; `GetModuleHandleA(NULL)` returns 0x00400000; keep `fnvnx_path_reset` /
  mounts consistent with where the exe is read from (`sdmc:/fnv-nx/game/FalloutNV.exe`).
* **S5 (toolchain)**: os_horizon.c needs fsdev mounted before `os_init`, a non-zero system resource size in the
  NPDM (svcMapPhysicalMemory) and the alias region inside the 32-bit range; newlib reserves `_unlink`, `_stat`,
  ... (the runtime avoids those names); newlib's aarch64 `fenv` and `malloc_usable_size` are assumed.
* **S6/S7**: register DLL replacements with `fnvnx_register_module`, COM classes with `fnvnx_register_com_class`;
  drive the window with `fnvnx_set_display_size`, `fnvnx_post_message`, `fnvnx_set_key_state`,
  `fnvnx_set_cursor_pos`.

## Tests

`make -C tests/s4` builds `platform/` natively (os_posix backend, LP64) and runs 46 tests (1,612 checks): recursive
and zero-initialised critical sections under contention, Interlocked*, auto/manual events, timeouts,
WaitForMultiple any/all, semaphores, named recursive mutexes, suspended threads/exit codes/ExitThread, 1088 TLS
slots, image TLS + fake TEB, SetThreadName; path mapping rules and case-insensitive resolution, cwd, CreateFile
dispositions and error codes, read/write/seek/overlapped, FindFirstFile order and wildcards, attributes/move/copy/
directories, CRT file wrappers and the `_stat` layout; INI read/list/int/write/delete/cache rules; MSVC string,
format, secure, number, path, aligned heap, rand, `_controlfp` rounding, EH vector iterators, `__RTDynamicCast`
bridge, `_CxxThrowException`, `_Lockit`; QPC/ticks, FILETIME/SYSTEMTIME, VirtualAlloc/Free/Query, heaps, command
line parsing, registry, windows/messages/message boxes, modules and the export table. Clean under ASan+UBSan and
TSan. `make -C tests/s4 ilp32 LIBNX=<libnx>` parses every platform source with `aarch64-linux-gnu-gcc -mabi=ilp32`,
os_horizon.c against the libnx headers (with a stand-in for newlib's `sys/lock.h`).

## Not done yet

* MSVC text mode (`fopen` without `b`: CRLF <-> LF translation) - files are binary for now.
* The 25 libcpmt internals (`std::exception` family with MSVC layout, locale/ios helpers, `_Fiopen`) and the 12
  unidentified CRT entries; `__iob_func` (MSVC `FILE` array layout) needs a rule.
* `_CxxThrowException` with an object throws a wrapper (`fnvnx_msvc_exception`): `catch (...)` works, typed
  handlers do not (needs the ThrowInfo -> GCC type mapping from S2).
* Horizon backend untested (no toolchain yet); SuspendThread of running threads unsupported on the host.
* Directory-listing cache for the case-insensitive scan on case-sensitive hosts (only matters on the host).

## S4b-crt: the C++ library, MSVC stdio, typed `_CxxThrowException`

Follow-up stream to S4 (journal parent J-0009); the first three "Not done yet" items above are done here.
`crt_symbols.tsv` is updated in place and supersedes the table rows above for the addresses below; every `crt` /
`cpplib` / `helper` row now has a provider (none says `next:` / `identify` / `NONE` any more). Reclassified:
`<exe address>`, `<exe address>` cpplib -> crt (byteswaps), so cpplib 28, crt 119. Code: `platform/crt/cpplib.cpp`,
`eh.cpp`, `iob.c` (+ `fnvnx_iobuf.h`, `fnvnx_msvc_cpp.h`).

### The libcpmt internals (25) and the 12 unknown libcmt entries

The table of this section pairs exe addresses with names found in the decomp, so it is kept in the private artifacts repository (fnv-nx-build, branch l5-platform/manifest, tables/runtime_doc_sections.md; pin: tools/tables.pin.json). The public text keeps only the rule: the platform provides a body only where an import fact or an export table of a DLL the exe loads names it; no exe address is bound to a body by name.

### Object layouts (ILP32, static_asserted in the ILP32 check)

`std::exception` = vptr, `const char *_m_what`, `int _m_doFree` (12); `logic_error` family = exception + VC9
`string _Str` (40); `string` = `_Alval` (4), `_Bx` (16: buffer, or pointer when `_Myres >= 16`), `_Mysize`,
`_Myres` (28); `locale::facet` = vptr, `_Refs` (`(size_t)-1` immortal); `_Locimp` = facet, `_Facetvec` 8,
`_Facetcount` 0xc, `_Catmask` 0x10, `_Xparent` 0x14, `string _Name` 0x18 (0x34); `ios_base` (0x28, `_Ploc` at
0x24), `basic_ios` (0x34), `basic_ostream` (vbptr + basic_ios, 0x38); `_Locinfo` = `_Lockit` + 4 strings
(0x74); `_Mutex` = `void *_Mtx`; `_Ctypevec` (16, returned in x0:x1); MSVC `FILE` 32 bytes.

The exception constructors placement-construct `fnvnx::msvc::exc<std::exception>` (or `<std::bad_cast>`):
MSVC's fields after a vptr of this runtime whose class derives from the libstdc++ standard class, so `what()`
(MSVC semantics: `_m_what` or "Unknown exception") and typed `catch (std::exception&)` work; derived game classes
overwrite the vptr in their own constructors. The destructor frees an owned string and works on any object of
that layout. The locale is one immortal "C" `_Locimp` with no facets: the game's inline `use_facet` creates
facets on first use and registers them (`fnvnx_cpplib_facet_tidy` would release them; nothing calls it at exit,
game static destructors may still use facets). `_Getctype` returns MSVC's C-locale table (`_Delfl` 0: static,
`ctype<char>::_Tidy` frees nothing); `_Tolower`/`_Toupper` use the low byte only (the decomp declares them
`char(char, char*)`). `_Locinfo` always reports "C" (or "*" for any other name). `_Mutex` is recursive and
created on first use when only zero-filled. `_Ios_base_dtor` runs the `erase_event` callbacks, frees the
iword/pword and callback lists, and deletes `_Ploc` with `::operator delete` (the game's global operator delete
at the final link).

### MSVC stdio and lowio (`fnvnx_iobuf.h`, `iob.c`)

**Decision: the game's `FILE` is the runtime's `struct _iobuf`** with MSVC's 32-byte layout; every CRT function
that takes or returns a `FILE*` is `fnvnx_iob_*` (providers updated: `fopen`, `fopen_s`, `fclose`, `fread`,
`fwrite`, `fseek`, `ftell`, `_fseeki64`, `fgets`, `fgetc`, `fputc`, `ungetc`, `feof`, `fflush`, `fgetpos`,
`fsetpos`, `fprintf`, `printf`, `vprintf`), and `__iob_func` returns `fnvnx_iob[3]` (stdin, stdout, stderr at a
0x20 stride: the Havok writer computes `__iob_func() + 0x20/0x40`). `_ptr`/`_base` stay NULL and `_cnt` 0, so
the decomp's copy of `basic_filebuf::_Init` (which points the streambuf at `&_File->_base/_ptr/_cnt`) always
falls through to `fgetc`/`fputc`/`ungetc`; `_flag` mirrors `_IOREAD/_IOWRT/_IORW/_IOEOF/_IOERR`, `_file` is the
descriptor. `fnvnx_fopen` (host `FILE*`, binary) stays for runtime-internal use.

Text mode as MSVC: a descriptor opened without `b` (`fopen`), or without `_O_BINARY` (`fnvnx_open`; default
`_fmode` = `_O_TEXT`, `fnvnx_set_fmode`) reads CR LF as LF (also across buffer boundaries; a lone CR stays) and
stops at Ctrl-Z (until a seek or `clearerr`), and writes LF as CR LF; `fnvnx_setmode` switches a descriptor and
a stream on it at once; `_fdopen` follows the descriptor's mode ('t'/'b' only checked, as MSVC). `ftell` /
`fgetpos` / `fseek` are raw file offsets; `a` / `_O_APPEND` writes at the end; read/write switches on `+`
streams flush or drop the buffer themselves; `ungetc` keeps up to 8 characters; `D` deletes on close; `,ccs=`
opens a plain text stream. fprintf/printf use the MSVC formatter (`%I64d`). The standard streams report text
mode but pass bytes to the host's stdout/stderr unchanged; stdin is at end of file. Lowio:
`fnvnx_open/sopen/close/read/write/lseek(i64)/tell/filelength/eof/commit/setmode` (newlib owns `_open`,
`_read`, ...). Deviation: `fflush` on an input stream keeps the logical position (VC9 dropped buffered input).

For **S1**: include `fnvnx_iobuf.h` where DECL1 drops the decomp's `_iobuf` stdio prototypes, and drop the
decomp's own `struct _iobuf` definitions (one unit has the same fields). In C++ it includes `<cstdio>` first
and adds C++ overloads of the standard names taking `_iobuf*` (they coexist with libc's `FILE*` functions);
`fopen` cannot be overloaded: `-DFNVNX_IOBUF_FOPEN` maps it (macro), or call `fnvnx_iob_fopen` (S1's DECL1 puts
`shim/include/fnvnx_iobuf_fopen.h`, the header plus that macro and `std::fopen`, in place of a decomp `_iobuf* fopen`
prototype: DECL1-FOPEN, bsshader). A literal
`fflush(NULL)` would be ambiguous (none in the decomp). For **S2**: E entries must bind to the `provider`
column; a libc fallback by name now hands libc a runtime `_iobuf` (the runtime rejects foreign pointers with
EINVAL, libc would not).

### `_CxxThrowException(obj, ThrowInfo)` with typed handlers (`eh.cpp`)

> **Removed (W19).** `platform/crt/eh.cpp`, the `fnvnx_eh_vector_*` / `fnvnx_rt_dynamic_cast` / `fnvnx_rtti_*` helpers of
> `helpers.cpp` and their tests are gone: no import routes to them (the msvcrt name table serves no name the bridge binds
> `l3`, and no manifest row names them), and the native model called the game's ThrowInfo copy constructors / destructors as
> native function pointers. In the product the game's own translated EH code and the lifter runtime perform C++ EH and RTTI
> (`_CxxThrowException`, `__CxxFrameHandler`, `_except_handler3`, `?terminate`, type_info's destructor are bound `l3`). The
> text below describes the old native-unit pipeline only; `crt_symbols.tsv` rows that name these providers belong to it.

GCC unwinds the thrower's frame before the handler runs, so the object is copied into
`__cxa_allocate_exception` storage and thrown with an Itanium `type_info`, found in this order:

1. a registration by ThrowInfo address: `fnvnx_eh_register_throw(ti, &typeid(T), size, copy, dtor)` /
   `fnvnx_eh_register_type<T>(ti)` (native GCC type - available now), or
   `fnvnx_eh_register_msvc_std(ti, ".?AVlength_error@std@@")` (an MSVC standard exception object);
2. the ThrowInfo in the image (x86 layout, 32-bit absolute addresses: ThrowInfo -> CatchableTypeArray ->
   CatchableType -> TypeDescriptor): the most-derived CatchableType whose TypeDescriptor S2 registered with
   `fnvnx_rtti_register` (the `??_R0` -> `_ZTI` table of `__RTDynamicCast`) is thrown as that type with size
   `sizeOrOffset`, its copy constructor (`properties & 1`: bitwise) and the ThrowInfo's `pmfnUnwind` as
   destructor; code addresses inside the x86 `.text` go through `fnvnx_eh_set_code_resolver` (S2's address
   map). Otherwise the first TypeDescriptor named like an MSVC standard exception is translated;
3. otherwise `fnvnx_msvc_exception{object, throw_info}` (catch (...) only), as before.

Translation of MSVC standard exceptions: `exception`, `bad_alloc`, `bad_cast`, `bad_typeid`, `bad_exception`
-> `exc<std::X>` (MSVC `what()` with the copied `_m_what`); `logic_error`, `domain_error`, `invalid_argument`,
`length_error`, `out_of_range`, `runtime_error`, `range_error`, `overflow_error`, `underflow_error`,
`ios_base::failure` -> the libstdc++ class constructed from the MSVC `_Str`. `obj == NULL` is `throw;` (the
handled exception object itself is rethrown). Pointers are only followed inside `fnvnx_eh_set_image_range`
(the loader sets the data image's range; default 0x10000..), so a ThrowInfo of 0 falls back safely.

**What needs the image**: the TypeDescriptor names and CatchableTypeArrays (`.rdata`). Without it, the
decomp's three ThrowInfos are typed from the code around the calls: <exe address> = `std::bad_alloc` (exception
built from a null message, vftable <exe address>, the allocators' overflow paths; h), <exe address> =
`std::length_error` (a `logic_error(const string&)` object, vftable <exe address>; m) - both registered by
`fnvnx_eh_register_fnv_known()` - and <exe address> = SpeedTree's exception class (string, int), which needs a
native type: S1 must first give its per-unit placeholder names (`CIdvException`, `ExceptionB07`,
`IdvException`, `ExcQ`, `Exc59`) one class, then `fnvnx_eh_register_type<That>(<exe address>)`.

### Termination (S10)

`fnvnx_atexit` (`atexit`), `_onexit`, `fnvnx_doexit` (`doexit`), `fnvnx_exit` (`exit`, provider updated),
`fnvnx_cexit` (`_cexit`), `fnvnx_c_exit` (`_c_exit`) forward to S10's game onexit table (`fnvnx_xc_atexit`,
`fnvnx_xc_onexit`, `fnvnx_xc_doexit`, `startup/guest/onexit.c`) through weak references; without startup/ (the
host tests) libc's `atexit`/`exit` stand in. `ExitProcess` (platform/win32) is unchanged: on Windows it does
not run the exe's CRT onexit table either.

### Found in the decomp (for S1 / S2)

* `frondengine.cpp` and `frondengine_ob1_variant.cpp` declare the `bad_alloc` stand-in (`FUN_00ec78f1_t`) with
  2 ints (8 bytes); the exception constructor writes 12: a stack overrun on GCC. Needs a third field.
* Six call sites pass the ThrowInfo's first word (`DAT_01140ef8` by value) instead of its address
  (`blk_00ac0000_od` x2, `blk_00b00000_od` x3, `blk_00b40000_od` x1): they reach the catch (...) fallback only.
* Objects get MSVC image vftables written into their vptr by hand (<exe address> bad_alloc, <exe address>
  length_error, <exe address> / <exe address> ios_base / basic_ios, <exe address> ostream vbase): the runtime never
  calls through them (it translates by ThrowInfo), but a virtual call from game code would; these unnamed
  `DAT_` vftables need S2's V entries too.
* Many units carry their own `namespace std { class exception ... }` (MSVC layout): its vtable, `what()` and
  `~exception` resolve to libstdc++'s unless bound by address; `_ZTISt9exception` is shared, so typed handlers
  catch the runtime's exceptions, but only the virtual `what()` is safe on them (not `e._m_what`).
* One unit defines `_Mutex::_Mutex` (`FUN_00ec3246`) as an empty body: the runtime creates the lock on first
  use instead.

### Tests

`make -C tests/s4`: 67 tests, 1,949 checks (S4's 46 + 21): exception layout/semantics (deep/shallow copy,
`what()` through the vtable, `bad_cast` RTTI), `_Mutex` recursion and contention, `_Init_locks`, the global
`_Locimp` as the inline `_Getfacet` reads it, facet register/tidy, `_Locinfo`, the ctype table, `_Addstd` /
`_Ios_base_dtor` (callbacks, lists, double-opened standard streams), the ostream object and its destructor
path, `_Fiopen` modes; MSVC FILE layout and standard streams, text reads (CR LF, lone CR, Ctrl-Z, buffer
boundaries, ftell), text writes, seek/tell/ungetc, update and append modes, `_setmode`, error cases, `D`, lowio
(text, Ctrl-Z, CR LF split by a 1-byte read, `_O_EXCL`, `_O_APPEND`), `_fdopen`, the C++ overloads;
`_CxxThrowException` with a registered native type (base-class handler, copy and destructor counts), MSVC
`bad_alloc` / `length_error` / `runtime_error` / `bad_cast` objects, ThrowInfo tables built below 4 GiB as in
the image (std translation, registered TypeDescriptor with resolved copy constructor and `pmfnUnwind`, a simple
type, unknown-type and NULL fallbacks), `throw;`; byteswaps, `_mbstok`, `_mbsn*`, `_wcsicmp`; exit forwarding.
Clean under ASan+UBSan and TSan; all platform sources parse under ILP32 with the layout asserts.

### Not done yet (S4b)

* Image-dependent: confirm the `m`/`l` identifications and the three ThrowInfo types from the exe;
  virtual-base copy constructors (`properties & 4`) are called without MSVC's extra flag.
* `iword`/`pword`/`register_callback`, `locale(const char*)`, `_Locimp::_Addfac` and the other libcpmt entry
  points the game does not reference; `cout`/`cerr` are constructed but not usable (no native vftables).

## S4c: import slots and trap names (the S4 metric)

The metric (owner rule, J-0137 on): the guest's S4-owned trap stubs plus the exe's import slots that have no export
at boot; target 0. Measured locally, without a guest link (that is a farm job, D17):

* **trap names** - `tests/s4/trapcheck.sh <traps.txt> [addrmap.tsv]` compiles platform/win32, platform/crt,
  render/nine/d3dx and the null device as `toolchain/game/build.sh` does (ILP32, `aarch64-linux-gnu-g++`) and lists the
  S4-owned names of a guest's trap list (`tools/pipeline/undef.py` classes `win32_import`, `crt_msvc`, `libc_glibc`,
  the Bink names) that are still undefined. The trap list is the guest link's `undefined.txt`, or the functions of
  `fnvnx.elf` whose fourth instruction branches to `__fnvnx_undef_trap`. full1 `31971038913d`: 43 S4 names.
* **IAT** - `make -C tests/s4 iat EXE=<FalloutNV.exe>`: `tests/s4/iat/pe_imports.py` lists the exe's 280 slots
  (name or `#ordinal`); the `-m32` binary (`make -C tests/s4 m32`: platform/ + S7 + d3dx + the null device built for
  i386, the game's data model) resolves each one as `boot.cpp` does - after `fnvnx_audio_input_register` and
  `fnvnx_d3d9_register` - with `fnvnx_win32_lookup_import(dll, name, ordinal)`, and prints the slots without an
  export. full1 baseline 124 of 280.

| change | trap names | IAT slots |
|---|---:|---:|
| baseline (full1 `31971038913d`) | 43 | 124 |
| `D3DPERF_SetOptions` from null_d3d9.cpp's d3d9.dll module (already on the integration branch) | 42 | 123 |
| D3DX in the D3D9=null guest (`toolchain/game/build.sh`), `d3dx9_38` exports by weak reference | 19 | 98 |
| binkw32 stub (`bink.cpp`) | 10 | 89 |
| CRT names (`platform/crt/msvc_names.c`) | 0 | 89 |
| GalaxyWrp = steam_api, ordinals (WSOCK32, XINPUT1_3, DSOUND), `libvorbisfile.dll` | 0 | 48 |
| winmm mmio (`mmio.cpp`) | 0 | 41 |
| the exe CRT's kernel32 imports, GetUserNameA, GetClassLongA (`crt_kernel32.cpp`) | 0 | 0 |

Decisions:

* **Bink**: movies are skipped. `BinkOpen` returns NULL (`ERROR_FILE_NOT_FOUND`); the engine logs "Could not open %s
  for playback" and goes on as for a deleted `.bik`. The other entry points are NULL-safe no-ops;
  `BinkSetSoundSystem` reports success. The IAT's stdcall decorations (`_BinkOpen@8` ...) and the plain names are
  exported.
* **D3DX** is linked into the null-D3D9 guest with render/nine/game/build.sh's flags; the external (Nine) guest
  already gets it through `EXTRA_OBJS`, so it is not added there twice. `D3DXSaveTextureToFileA` (screenshot path)
  is a weak fallback that fails with `D3DERR_INVALIDCALL`.
* **FreeBlockImp** is not data: the decomp's `call [<exe address>]` reads a `.rdata` vtable word holding `<exe address>`,
  `mov ecx, <exe address>; jmp <exe address>` = `MemoryManager::Deallocate` on the global heap = FormHeap_Free
  (`<exe address>`), which the stand-in now calls.
* **Ordinals**: Winsock 1.1's numbering for wsock32 (10 `inet_addr`, 11 `inet_ntoa`, 12 `ioctlsocket`; ws2_32
  orders those differently and is not imported by ordinal); xinput1_3 #2/#3/#4/#5; dsound #1/#2/#11.
* **mmio**: positions come from `lDiskOffset` after every message (the game's procedure returns 0 from
  `MMIOM_SEEK`), `SEEK_CUR` is sent as `SEEK_SET`, `mmioAdvance(MMIO_READ)` at EOF returns 0 with
  `pchNext == pchEndRead`, allocated buffers have a zeroed guard byte. No installable I/O procedures.
* **kernel32 for the exe's CRT** (`crt_kernel32.cpp`, its header comment has the details): en-US, LCID 0x0409, ANSI
  and OEM code page both 1252 (one table; OEM 437 would matter only to console code); conversions for 1252 and UTF-8
  with the Windows size conventions; standard handles are pseudo handles whose output goes to the debug log (a GUI
  process on Windows would lose it); the environment starts empty; GetTimeZoneInformation's Bias follows
  GetLocalTime's offset. `kernel32.dll` and `user32.dll` are registered modules: MSVC 2008's `_mtinit` fails when
  `GetModuleHandleW(L"KERNEL32.DLL")` is NULL; GetProcAddress on them uses the export table (FlsAlloc/EncodePointer
  are absent, so the CRT uses plain TLS).
