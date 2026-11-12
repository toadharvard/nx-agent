# fnv-nx runtime (stream S4)

What the game expects from the platform that the decomp does not define, how each piece is classified, and how
`platform/` provides it. Measured on decomp `87eb9373` (`build/units.json`). Machine-readable tables:
`crt_symbols.tsv` (deleted, no name-based table) (every undefined `.text` address: class,
identification, provider) and [`platform/win32/imports.tsv`](../platform/win32/imports.tsv) (every IAT slot).
This file lists names and addresses only; it contains no decomp code.

## Counts

**Correction to J-0002.** The census counted 10,805 undefined `.text` addresses because it compared address
*strings*: units write the same address as `0x004013E0` and `0x004013e0`. Compared as integers, units.json has
94,513 referenced addresses (not 97,575) and **4,618** referenced `.text` addresses (`0x401000..0xfde577`) without
a definition; 5,923 of the "undefined" ones were defined functions spelled in upper case.

| class | addresses | what it is | runtime provides |
|---|---:|---|---|
| `eh` | 4,238 | `__ehhandler$<function>` stubs (MSVC C++ EH funclets, 0x00ee..0x00f3) | nothing: GCC emits its own unwind tables |
| `atexit_dtor` | 121 | ``dynamic atexit destructor for 'x''`` (`??__F`, 0x00fc..0x00fd) | nothing: GCC registers static destructors itself |
| `crt` | 117 | MSVC 2008 static C runtime (libcmt, 0x00ec4000..0x00ece2ff) | libc / libm, or `platform/crt` for MSVC-only or MSVC-different functions |
| `cpplib` | 30 | MSVC C++ library internals (libcpmt, 0x00ec3020..0x00ec3d32): `std::_Lockit`, `_Xlen`, `ios_base`, locale, `std::exception` | 4 in `platform/crt`, rest listed as next |
| `helper` | 27 | compiler helpers: `_ftol2(_sse)`, `_CIsin`..., `_allmul`..., `_alloca_probe`, `__security_check_cookie`, EH vector iterators, `__RTDynamicCast`, `_CxxThrowException`, SEH handlers | the 8 the source calls **by name** (`platform/crt/helpers.cpp`); the rest are implicit and vanish with GCC |
| `crt_inline` | 8 | out-of-line copies of CRT header inlines in game code (`floor(float)`, `strchr(char*,int)`, ...) | libc/libm (`floorf`, `strchr`, ...) |
| `thunk` | 11 | `jmp [__imp_x]` stubs of ws2_32 (0x00ee6d44..0x00ee6d80) | `fnvnx_ws2_*` |
| `body_named` | 20 | game/engine functions with a real name but no body in the decomp | **needs decompiling** (list below) |
| `body_unnamed` | 33 | `FUN_xxxxxxxx` / `*_shell` / `GlobalObject*` names only | **needs decompiling** (list below) |
| `mid_named` | 3 | named functions whose address lies inside the recorded size of the previous function | **needs decompiling** (the recorded size is probably wrong) |
| `mid_data`, `data` | 10 | data symbols (`g_..._arg1`, `$LN85`, `UNK_`) whose "address" is a jump-table label or a garbage value | nothing; S2 should drop them |
| **total** | **4,618** | | |

Of the 259 addresses that are neither EH funclets nor atexit destructors, 243 are referenced by name from the source (an identifier of the address appears in
`src/` or `include/`), so their GCC objects will have undefined symbols that S2 resolves to the provider in
`crt_symbols.tsv`. The implicit helpers (`_allmul`, `_CIcos`, `_alloca_probe`, EH funclets, atexit destructors)
are only in units.json because MSVC emitted them; GCC objects never reference them.

**Imports.** 288 decomp names point at **186** IAT slots (0x00fdf000..0x00fdf49c, plus 2 mislabelled data
addresses). By DLL: kernel32 78, user32 30, d3dx9_38 22, steam_api 11, binkw32 9, ws2_32 8, ole32 7, advapi32 5,
vorbisfile 5, shell32 2, d3d9/dinput8/dsound/gdi32/winmm/xinput1_3 1 each, 1 unassigned. Providers: **140 slots
implemented here** (`platform/win32`), 32 belong to S6 (d3d9, d3dx9, Bink), 8 to S7 (DirectInput, DirectSound,
XInput, vorbisfile), 4 slots have no trustworthy name in the decomp, 2 are not IAT slots.

The decomp names IAT slots inconsistently: **31 slots** carry more than one name, e.g. slot `0x00fdf1c0`
(InterlockedIncrement, 236 units) is called `EnterCriticalSection` by one unit and `0x00fdf1c4`
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

The engine calls these by address and the decomp has no body for them. `units` = referencing units, `src` =
source files naming them.

| address | class | name (one of the referencing names) | units | src |
|---|---|---|---:|---:|
| `0x00401460` | body_named | `void * __cdecl CopyMemoryBlock(void *, void const *, unsigned int)` | 118 | 230 |
| `0x00402d40` | mid_named | `void __thiscall BaseFormComponent::Init(void)` | 1 | 471 |
| `0x00402d60` | mid_named | `void __thiscall BaseFormComponent::CopyFromBase(class BaseFormComponent *)` | 1 | 56 |
| `0x00402d70` | mid_named | `bool __thiscall BaseFormComponent::CompareWithBase(class BaseFormComponent *)` | 1 | 50 |
| `0x00404d80` | body_named | `int __cdecl strcpy_s(char *, unsigned int, char *)` | 6 | 109 |
| `0x00404da0` | body_named | `__thiscall SettingPointerMap::~SettingPointerMap(void)` | 1 | 1 |
| `0x00406d50` | body_named | `int __cdecl AppendPreview(char *, unsigned int, char const *)` | 58 | 103 |
| `0x0042f570` | body_named | `__thiscall BoundObjectArray::BoundObjectArray(void)` | 2 | 248 |
| `0x0042f5a0` | body_named | `unsigned int __cdecl MinCount(unsigned int, unsigned int)` | 21 | 33 |
| `0x00447300` | body_named | `struct fnv_00950c20::xList * __thiscall fnv_00950c20::xLoader::F447300(char const *, char const *, i` | 3 | 3 |
| `0x004564f0` | body_named | `int __cdecl BatchCompare(char const *, char const *, unsigned int)` | 12 | 14 |
| `0x0045cfe0` | body_unnamed | `__thiscall GlobalObject2_0045cfe0::GlobalObject2_0045cfe0(char const *, char const *)` | 4 | 67 |
| `0x00462d40` | body_named | `void __cdecl fnv_0046ddb0::SplitPath(char const *, char *, int, char *, int, char *, int, char *, in` | 4 | 4 |
| `0x0046fd30` | body_named | `int __cdecl _mbsicmp(unsigned char const *, unsigned char const *)` | 3 | 6 |
| `0x00481230` | body_unnamed | `char const * __thiscall BipedHelper_46ec10::FUN_00481230(int)` | 3 | 4 |
| `0x00481280` | body_unnamed | `char const * __thiscall ArmorNames::FUN_00481280(int)` | 2 | 2 |
| `0x00481310` | body_unnamed | `void __thiscall FUN_00481310_shell::FUN_00481310(void)` | 1 | 1 |
| `0x004b7580` | body_named | `_memcpy_s` | 3 | 18 |
| `0x004f6e60` | body_unnamed | `bool __thiscall RegionAnswer::FUN_004f6e60(void)` | 1 | 1 |
| `0x0050f9a0` | body_unnamed | `void __thiscall fnv_0047d220::ABDHelper_47d220::FUN_0050f9a0(class TESLevItem *)` | 15 | 22 |
| `0x0065de30` | body_named | `__thiscall ctor_0065d3a0::RebindAllocator::RebindAllocator(struct ctor_0065d3a0::FUN_0065de30 const ` | 8 | 284 |
| `0x0065de40` | body_unnamed | `unsigned int __thiscall FUN_0065d4e0_Pool::Alloc(unsigned int)` | 1 | 188 |
| `0x008d0430` | body_named | `class PatrolActorPackageData * __thiscall LowProcess::GetCurrentPackageData(void)` | 6 | 227 |
| `0x009fea2a` | body_unnamed | `NVAPI-like GPU query (renderer.cpp), likely NVIDIA lib` | 1 | 1 |
| `0x009fead6` | thunk | `NVAPI entry: jmp [nvapi_QueryInterface slot]; fnvnx_nvapi_not_initialized (-4, R6 recon6-007)` | 1 | 1 |
| `0x009feae8` | thunk | `NVAPI entry: jmp [nvapi_QueryInterface slot]; fnvnx_nvapi_not_initialized (-4, R6 recon6-007)` | 1 | 1 |
| `0x009febd8` | thunk | `NVAPI entry: jmp [nvapi_QueryInterface slot]; fnvnx_nvapi_not_initialized (-4, R6 recon6-007)` | 1 | 1 |
| `0x00aa1750` | body_named | `unsigned int __thiscall NiFile::FileRead(void *, unsigned int)` | 3 | 4 |
| `0x00aa1810` | body_unnamed | `unsigned int __thiscall FUN_00aff950_shell::FUN_00aa1810(void *, unsigned int)` | 2 | 2 |
| `0x00aa7830` | body_named | `__thiscall fnv_00b044a0::BVecQ::~BVecQ(void)` | 12 | 23 |
| `0x00aa78a0` | body_unnamed | `__thiscall FUN_00aa78a0::FUN_00aa78a0(unsigned int, unsigned int, char const *, bool)` | 1 | 1 |
| `0x00aa7930` | body_unnamed | `void __thiscall FakeHeap::FUN_00aa7930(void)` | 1 | 2 |
| `0x00aa8610` | body_named | `unsigned int __cdecl _access_s(void const *, unsigned int, unsigned int, void *)` | 1 | 2 |
| `0x00aa8660` | body_named | `unsigned int __cdecl NiFile::WriteBytesToFile(void const *, unsigned int, unsigned int, struct _iobu` | 1 | 2 |
| `0x00ad7000` | body_unnamed | `void __thiscall FUN_00ad7000_stub::FUN_00ad7000(void)` | 2 | 4 |
| `0x00ad7020` | body_unnamed | `void __thiscall C::FUN_00ad7020(int)` | 1 | 1 |
| `0x00af25b0` | body_named | `void __thiscall TemplateList_00a0ae70::AddTail(class TileTemplate_00a0ae70 **)` | 5 | 175 |
| `0x00cb26e0` | body_named | `void __thiscall SubObj::CallSub(struct Block64 *)` | 14 | 426 |
| `0x00cf8690` | body_unnamed | `void __cdecl FUN_00cf8690(struct hkAabbUint32 *, struct hkAabbUint32 const *)` | 2 | 4 |
| `0x00ec3020` | body_named | `__thiscall fnv_00ec20c0::NiTMapBase<class fnv_00ec20c0::DFALL<class fnv_00ec20c0::NiTMapItem<char co` | 1 | 0 |
| `0x00f1d2eb` | body_unnamed | `void __cdecl FUN_00f1d2eb(void)` | 2 | 1 |
| `0x00f1e4f0` | body_unnamed | `void __cdecl FUN_00f1e4f0(void)` | 2 | 1 |
| `0x00f2d322` | body_unnamed | `int __cdecl FUN_00f2d322(void *, void *, void *, void *)` | 1 | 1 |
| `0x00f2d57b` | body_unnamed | `_FUN_00f2d57b` | 1 | 1 |
| `0x00fda290` | body_unnamed | `void __cdecl FUN_00fda290(void)` | 1 | 1 |
| `0x00fda2a0` | body_unnamed | `void __cdecl fnv_00a01000::FUN_00fda2a0(void)` | 1 | 1 |
| `0x00fdad30` | body_unnamed | `_FUN_00fdad30` | 1 | 5 |
| `0x00fdad60` | body_unnamed | `void __cdecl FUN_00fdad60(void)` | 1 | 2 |
| `0x00fdad80` | body_unnamed | `void __cdecl FUN_00fdad80(void)` | 1 | 2 |
| `0x00fdae10` | body_unnamed | `void __cdecl FUN_00fdae10(void)` | 1 | 1 |
| `0x00fdb140` | body_unnamed | `_FUN_00fdb140` | 1 | 1 |
| `0x00fdb2a0` | body_unnamed | `void __cdecl FUN_00fdb2a0(void)` | 1 | 1 |
| `0x00fdb2b0` | body_unnamed | `void __cdecl FUN_00fdb2b0(void)` | 1 | 2 |
| `0x00fdc3f0` | body_unnamed | `void __cdecl fnv_00b00980::FUN_00fdc3f0(void)` | 1 | 1 |
| `0x00fdd690` | body_unnamed | `void __cdecl FUN_00fdd690(void)` | 1 | 5 |
| `0x00fde350` | body_unnamed | `_FUN_00fde350` | 1 | 1 |

(`src` counts files that contain any identifier of the address, an upper bound for common names such as `Init`.)
Notes: `0x00401460` (118 units) is a memcpy-shaped game helper, `0x00406d50` (58 units) a `strcat_s`-shaped one,
`0x0042f5a0` a `min`; the four `0x009fe...` functions are GPU vendor queries in `renderer.cpp` (likely a statically
linked NVIDIA library, to be stubbed rather than decompiled); the 15 `0x00fd...`/`0x00fc...` `FUN_` entries are
static-object destructors that the source registers explicitly with `atexit`; `0x00402d40/60/70` are tiny
`BaseFormComponent` virtuals inside the recorded size of `FUN_00402d30`/`FUN_00402d50`.

## Data symbols at code addresses (10)

| address | class | names |
|---|---|---|
| `0x00416a81` | mid_data | `?g_011d36b4_arg1@@3QBDB` |
| `0x0041727a` | mid_data | `?g_011d3c1c_arg1@@3QBDB` |
| `0x00476c85` | mid_data | `?g_011d1f80_arg1@@3QBDB` |
| `0x0052667c` | mid_data | `?g_011d46a8_arg1@@3QBDB` |
| `0x006e6e6e` | mid_data | `?g_011d3450_arg1@@3QBDB` |
| `0x007e7e7e` | mid_data | `?g_011d4b04_arg1@@3QBDB` |
| `0x00823c04` | mid_data | `$LN85` |
| `0x00a00000` | mid_data | `?FUN_00a00000@@3DA ?g_011c59e0_arg1@@3QBDB ?g_011c5a4c_arg1@@3QBDB ?g_011c5c80_a` |
| `0x00fdd3b0` | data | `?UNK_00fdd3b0@@3DA` |
| `0x00fde2a0` | data | `?FUN_00fde2a0@@3PAXA` |

## CRT, C++ library, helpers, inlines and thunks (193)

`how`: `n` named by the decomp, `i` inferred, `u` unidentified (candidate in the name). `units` / `src` as above.

| address | class | function | how | units | src | provider |
|---|---|---|---|---:|---:|---|
| `0x00406cc0` | crt_inline | floor(float) -> floorf | n | 20 | 29 | libc: floorf |
| `0x0043b9d0` | crt_inline | strchr(char*, int) C++ overload -> strchr | n | 8 | 20 | libc: strchr |
| `0x004812f0` | crt_inline | strstr(const char*, const char*) C++ overload -> strstr | n | 8 | 10 | libc: strstr |
| `0x004e4430` | crt_inline | ceil(float) -> ceilf | n | 13 | 15 | libc: ceilf |
| `0x004e4470` | crt_inline | cos(float) -> cosf | n | 13 | 56 | libc: cosf |
| `0x004e44d0` | crt_inline | float(float) math overload - unidentified (sin/sqrt?) | n | 5 | 5 | libm (float overload) |
| `0x004f6e40` | crt_inline | acos(float) -> acosf | n | 4 | 4 | libc: acosf |
| `0x005dc270` | crt_inline | float(float) math overload - unidentified | n | 1 | 1 | libm (float overload) |
| `0x00ec30b0` | cpplib | std::_Xlength_error ("string too long") [_Xlen] | n | 5 | 26 | fnvnx_xlength_error |
| `0x00ec30e8` | cpplib | std::_Xout_of_range ("invalid string position") [_Xran] | n | 4 | 6 | fnvnx_xout_of_range |
| `0x00ec31b3` | cpplib | libcpmt void(void*) - unidentified (locale/ios helper) | u | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec3205` | cpplib | std::ios_base::_Ios_base_dtor | n | 4 | 4 | next: MSVC C++ library internal |
| `0x00ec3241` | cpplib | std::uncaught_exception | n | 1 | 1 | libstdc++: std::uncaught_exception |
| `0x00ec3246` | cpplib | libcpmt ctor() - unidentified (std::_Init_locks?) | u | 1 | 1 | next: MSVC C++ library internal |
| `0x00ec3260` | cpplib | libcpmt member void() - unidentified (std::_Mutex::_Lock?) | u | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec3277` | cpplib | libcpmt member void() - unidentified (std::_Mutex::_Unlock?) | u | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec3280` | cpplib | libcpmt member void() - unidentified | u | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec3289` | cpplib | libcpmt ctor() - unidentified (std::_Mutex::_Mutex?) | u | 1 | 3 | next: MSVC C++ library internal |
| `0x00ec32ba` | cpplib | libcpmt dtor - unidentified (std::_Mutex::~_Mutex?) | u | 1 | 3 | next: MSVC C++ library internal |
| `0x00ec32e3` | cpplib | std::_Lockit::_Lockit(int) | n | 2 | 3 | fnvnx_lockit_ctor |
| `0x00ec330b` | cpplib | std::_Lockit::~_Lockit() | n | 2 | 2 | fnvnx_lockit_dtor |
| `0x00ec33da` | cpplib | std::_Fiopen(const char*, openmode, int) | i | 1 | 1 | next: MSVC C++ library internal |
| `0x00ec34d4` | cpplib | libcpmt int() - unidentified (locale::_Init/_Getgloballocale) | u | 2 | 2 | next: MSVC C++ library internal |
| `0x00ec3599` | cpplib | std::locale::facet::_Facet_Register | n | 1 | 1 | next: MSVC C++ library internal |
| `0x00ec35d7` | cpplib | libcpmt void(void*) - unidentified (_Locinfo_dtor?) | u | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec36a6` | cpplib | libcpmt int() - unidentified (locale::_Getgloballocale?) | u | 2 | 2 | next: MSVC C++ library internal |
| `0x00ec3740` | cpplib | std::_Locinfo::_Locinfo_ctor(_Locinfo*, const std::string&) | i | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec379c` | cpplib | _Tolower(int, const _Ctypevec*) | i | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec38a9` | cpplib | _Getctype() | i | 1 | 1 | next: MSVC C++ library internal |
| `0x00ec3909` | cpplib | _Toupper(int, const _Ctypevec*) | i | 1 | 2 | next: MSVC C++ library internal |
| `0x00ec3a75` | cpplib | libcpmt ctor(const char*, int, int) - unidentified | u | 1 | 5 | next: MSVC C++ library internal |
| `0x00ec3bad` | cpplib | libcpmt int(int) - unidentified | u | 3 | 4 | next: MSVC C++ library internal |
| `0x00ec3bda` | cpplib | libcpmt short(short) - unidentified (_Getwctype/towlower?) | u | 2 | 2 | next: MSVC C++ library internal |
| `0x00ec3bf0` | crt | __int64(__int64) - unidentified (_abs64?) | u | 1 | 1 | identify from the exe |
| `0x00ec3d33` | helper | __CxxFrameHandler3 | n | 4 | 0 | implicit: GCC EH |
| `0x00ec408c` | helper | __security_check_cookie | n | 534 | 12 | fnvnx_security_check_cookie |
| `0x00ec43fb` | helper | __RTDynamicCast | n | 393 | 132 | fnvnx_rt_dynamic_cast |
| `0x00ec44d0` | crt | memcpy / memmove | n | 98 | 263 | libc: memcpy |
| `0x00ec4835` | crt | memcmp | n | 62 | 79 | libc: memcmp |
| `0x00ec5ec0` | helper | _alloca_probe / _alloca_probe_16 (_chkstk) | n | 126 | 3 | implicit: GCC |
| `0x00ec5fce` | helper | `eh vector destructor iterator' | n | 87 | 28 | fnvnx_eh_vector_dtor |
| `0x00ec6040` | helper | _CIsqrt | n | 99 | 8 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00ec6054` | crt | sqrt | n | 6 | 117 | libc: sqrt |
| `0x00ec6130` | crt | strlen | n | 64 | 155 | libc: strlen |
| `0x00ec61c0` | crt | memset | n | 144 | 414 | libc: memset |
| `0x00ec623a` | crt | sprintf | n | 47 | 64 | libc: sprintf |
| `0x00ec62c0` | helper | _ftol2_sse | n | 222 | 29 | fnvnx_ftol2 (operand passed) / rule |
| `0x00ec62f6` | helper | _ftol2 | n | 8 | 1 | fnvnx_ftol2 (operand passed) / rule |
| `0x00ec6370` | crt | strcpy | n | 9 | 18 | libc: strcpy |
| `0x00ec6380` | crt | strcat | n | 5 | 7 | libc: strcat |
| `0x00ec658f` | crt | atexit | n | 53 | 31 | fnvnx_atexit |
| `0x00ec65a6` | crt | strcpy_s | n | 61 | 138 | platform/crt: strcpy_s |
| `0x00ec67aa` | crt | tolower | n | 12 | 15 | libc: tolower |
| `0x00ec68e4` | crt | _stricmp | n | 14 | 46 | platform/crt: _stricmp |
| `0x00ec6940` | crt | floor | i | 6 | 15 | libc: floor |
| `0x00ec6bb5` | crt | vsprintf_s | n | 3 | 4 | platform/crt: vsprintf_s |
| `0x00ec6bd2` | crt | strcat_s | n | 23 | 84 | platform/crt: strcat_s |
| `0x00ec6cc4` | crt | vsprintf | i | 3 | 6 | libc: vsprintf |
| `0x00ec6cde` | crt | fabs | n | 21 | 152 | libc: fabs |
| `0x00ec6da0` | crt | strcmp | n | 15 | 42 | libc: strcmp |
| `0x00ec6e30` | crt | strrchr | n | 19 | 26 | libc: strrchr |
| `0x00ec6f20` | crt | qsort | n | 9 | 9 | libc: qsort |
| `0x00ec716d` | crt | bsearch | n | 2 | 7 | libc: bsearch |
| `0x00ec7230` | crt | void*(void*, const void*, size_t) - unidentified (memmove/_memccpy?) | u | 21 | 35 | identify from the exe |
| `0x00ec7595` | crt | _finite | n | 11 | 13 | platform/crt: _finite |
| `0x00ec75b1` | crt | _isnan | n | 10 | 13 | platform/crt: _isnan |
| `0x00ec7690` | crt | strchr | n | 12 | 26 | libc: strchr |
| `0x00ec7750` | crt | strstr | n | 29 | 36 | libc: strstr |
| `0x00ec77e0` | helper | _allshr | n | 4 | 0 | implicit: GCC |
| `0x00ec7810` | helper | _allshl | n | 11 | 0 | implicit: GCC |
| `0x00ec782f` | helper | `eh vector constructor iterator' | n | 91 | 3 | fnvnx_eh_vector_ctor |
| `0x00ec7894` | helper | _CxxThrowException | n | 31 | 18 | fnvnx_cxx_throw_exception |
| `0x00ec78e0` | cpplib | std::exception::exception() | n | 4 | 42 | next: MSVC C++ library internal |
| `0x00ec78f1` | cpplib | std::exception::exception(const char* const&) | n | 11 | 491 | next: MSVC C++ library internal |
| `0x00ec7944` | cpplib | std::exception::exception(const exception&) | i | 2 | 2 | next: MSVC C++ library internal |
| `0x00ec79a1` | cpplib | std::exception::~exception() | i | 6 | 9 | next: MSVC C++ library internal |
| `0x00ec79c4` | cpplib | std::bad_cast::bad_cast(const char*) | n | 1 | 1 | next: MSVC C++ library internal |
| `0x00ec7c56` | crt | _invalid_parameter_noinfo | n | 38 | 42 | platform/crt: _invalid_parameter_noinfo |
| `0x00ec7c66` | crt | memcpy_s | n | 6 | 16 | platform/crt: memcpy_s |
| `0x00ec7ce3` | crt | memmove_s | n | 13 | 24 | platform/crt: memmove_s |
| `0x00ec7d40` | crt | abs | n | 20 | 82 | libc: abs |
| `0x00ec7d50` | crt | clock | i | 1 | 1 | fnvnx_clock |
| `0x00ec7ec0` | crt | _strnicmp | n | 27 | 40 | platform/crt: _strnicmp |
| `0x00ec7f1e` | crt | _splitpath_s | n | 6 | 8 | platform/crt: _splitpath_s |
| `0x00ec8244` | crt | _mbscmp | i | 1 | 1 | platform/crt: _mbscmp |
| `0x00ec825b` | crt | _makepath_s | n | 3 | 5 | platform/crt: _makepath_s |
| `0x00ec858a` | crt | int(const char*, const char*) string compare - unidentified (_mbsicmp/_stricoll?) | u | 2 | 3 | identify from the exe |
| `0x00ec85e3` | crt | _errno | i | 3 | 4 | platform/crt: _errno |
| `0x00ec862c` | crt | rename | i | 3 | 3 | fnvnx_rename |
| `0x00ec8660` | helper | _chkstk (second probe entry) | n | 45 | 1 | implicit: GCC |
| `0x00ec86e1` | crt | isalpha | n | 3 | 4 | libc: isalpha |
| `0x00ec8860` | crt | isdigit | n | 1 | 2 | libc: isdigit |
| `0x00ec8965` | crt | isspace | i | 1 | 2 | libc: isspace |
| `0x00ec89e9` | crt | isalnum | n | 1 | 1 | libc: isalnum |
| `0x00ec8a19` | crt | strncmp | n | 11 | 17 | libc: strncmp |
| `0x00ec8d08` | crt | strtol | n | 3 | 5 | libc: strtol |
| `0x00ec8d33` | crt | strtoul (R1; was listed as _itoa) | i | 2 | 3 | libc: strtoul |
| `0x00ec8d5f` | crt | strncpy_s | n | 13 | 25 | platform/crt: strncpy_s |
| `0x00ec8feb` | crt | _strlwr_s | n | 2 | 2 | platform/crt: _strlwr_s |
| `0x00ec9010` | crt | atan | n | 2 | 9 | libc: atan |
| `0x00ec9050` | helper | _CIatan | n | 4 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00ec9130` | crt | fmod | n | 3 | 17 | libc: fmod |
| `0x00ec913a` | helper | _CIfmod | n | 6 | 13 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00ec923c` | crt | _stat | i | 2 | 3 | fnvnx_stat |
| `0x00ec9610` | crt | asin | i | 2 | 2 | libc: asin |
| `0x00ec9650` | helper | _CIasin | n | 3 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00ec9760` | crt | atan2 | n | 4 | 5 | libc: atan2 |
| `0x00ec9774` | crt | fprintf | i | 1 | 1 | libc: fprintf |
| `0x00ec9907` | crt | fclose | n | 10 | 15 | libc: fclose |
| `0x00ec9a47` | crt | fopen | n | 8 | 12 | fnvnx_fopen |
| `0x00ec9a5e` | crt | fopen_s | n | 3 | 4 | fnvnx_fopen_s |
| `0x00ec9ac0` | crt | pow | n | 5 | 19 | libc: pow |
| `0x00ec9b00` | helper | _CIpow | n | 10 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00ec9d4a` | crt | printf | i | 1 | 4 | libc: printf |
| `0x00ec9e10` | crt | ceil | n | 7 | 10 | libc: ceil |
| `0x00ec9f30` | crt | cos | n | 9 | 58 | libc: cos |
| `0x00ec9f70` | helper | _CIcos | n | 20 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00eca060` | crt | sin | n | 7 | 43 | libc: sin |
| `0x00eca0a0` | helper | _CIsin | n | 17 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00eca190` | crt | acos | i | 1 | 2 | libc: acos |
| `0x00eca1d0` | helper | _CIacos | n | 8 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00eca2e0` | crt | memchr | i | 1 | 3 | libc: memchr |
| `0x00eca38d` | crt | sprintf_s | i | 2 | 3 | platform/crt: sprintf_s |
| `0x00eca4a6` | crt | sscanf | n | 6 | 10 | libc: sscanf |
| `0x00eca573` | crt | atof | n | 9 | 11 | libc: atof |
| `0x00eca590` | crt | tan | n | 2 | 5 | libc: tan |
| `0x00eca5d0` | helper | _CItan | n | 2 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00eca6bd` | crt | atol | n | 3 | 5 | libc: atol |
| `0x00eca6d3` | crt | atoi | n | 15 | 23 | libc: atoi |
| `0x00eca7f4` | crt | toupper | n | 8 | 10 | libc: toupper |
| `0x00eca903` | crt | void(void*, void*, void*) - unidentified | u | 1 | 1 | identify from the exe |
| `0x00eca920` | crt | double(double) - unidentified (log/log10?) | u | 2 | 3 | identify from the exe |
| `0x00ecacb2` | crt | char*(char*, const char*, size_t) - unidentified (strncat?) | u | 1 | 1 | identify from the exe |
| `0x00ecad86` | crt | wcstombs_s | i | 1 | 1 | platform/crt: wcstombs_s |
| `0x00ecada6` | crt | srand | n | 4 | 5 | fnvnx_srand |
| `0x00ecadb8` | crt | rand | n | 30 | 36 | fnvnx_rand |
| `0x00ecadda` | crt | _time64 | n | 6 | 11 | _time64 |
| `0x00ecae30` | crt | exp | n | 2 | 9 | libc: exp |
| `0x00ecae6c` | helper | _CIexp | n | 4 | 0 | implicit: libm call (explicit arg-less calls need a rule) |
| `0x00ecaebb` | helper | `eh vector copy constructor iterator' | i | 1 | 2 | fnvnx_eh_vector_copy_ctor |
| `0x00ecb086` | crt | fwrite | n | 5 | 7 | libc: fwrite |
| `0x00ecb3a8` | crt | fread | n | 4 | 5 | libc: fread |
| `0x00ecb562` | crt | ftell | n | 4 | 5 | libc: ftell |
| `0x00ecb65a` | crt | fseek | n | 8 | 12 | libc: fseek |
| `0x00ecb849` | crt | void(int, int, int) - unidentified (_ltoa/_ultoa?) | u | 1 | 1 | identify from the exe |
| `0x00ecba8e` | crt | void(int, int, int) - unidentified (_ltoa/_ultoa?) | u | 1 | 1 | identify from the exe |
| `0x00ecbc5b` | crt | _strupr_s | i | 2 | 2 | platform/crt: _strupr_s |
| `0x00ecbc80` | crt | double(double) - unidentified (log/log10?) | u | 3 | 4 | identify from the exe |
| `0x00ecbe60` | crt | vprintf | i | 1 | 1 | libc: vprintf |
| `0x00ecbf05` | crt | _access | n | 8 | 11 | fnvnx_access |
| `0x00ecbf20` | crt | strpbrk | n | 3 | 3 | libc: strpbrk |
| `0x00ecc071` | crt | int(...) - unidentified (string compare used on a command-line option) | u | 1 | 1 | identify from the exe |
| `0x00ecc104` | crt | fgets | n | 2 | 2 | libc: fgets |
| `0x00ecc250` | helper | _alldiv | n | 3 | 0 | implicit: libgcc |
| `0x00ecc300` | helper | _allmul | n | 6 | 0 | implicit: GCC |
| `0x00ecc5f8` | crt | strtok_s | n | 5 | 6 | platform/crt: strtok_s |
| `0x00ecc6e6` | crt | strtok | n | 1 | 1 | libc: strtok |
| `0x00ecc7b0` | crt | strncpy | n | 4 | 5 | libc: strncpy |
| `0x00ecc8e0` | helper | _except_handler4 | n | 2 | 0 | none: SEH not reproduced |
| `0x00eccbdc` | crt | int(int, int) - unidentified (_controlfp?) | u | 1 | 1 | identify from the exe |
| `0x00eccd99` | crt | doexit | n | 1 | 1 | fnvnx_doexit |
| `0x00eccec5` | crt | exit | n | 3 | 27 | libc: exit |
| `0x00eccf5d` | crt | realloc | n | 2 | 2 | libc: realloc |
| `0x00ecd1c7` | crt | malloc | n | 2 | 65 | libc: malloc |
| `0x00ecd291` | crt | free | n | 3 | 183 | libc: free |
| `0x00ecd31f` | crt | _msize | n | 1 | 2 | platform/crt: _msize |
| `0x00ecd3c2` | crt | fputc | n | 1 | 1 | libc: fputc |
| `0x00ecd5bf` | crt | ungetc | n | 1 | 1 | libc: ungetc |
| `0x00ecd631` | crt | fgetc | n | 1 | 1 | libc: fgetc |
| `0x00ecd735` | crt | fgetpos | i | 1 | 1 | libc: fgetpos |
| `0x00ecd7a3` | crt | fsetpos | i | 1 | 1 | libc: fsetpos |
| `0x00ecd7e8` | crt | _fseeki64 | i | 1 | 2 | _fseeki64 |
| `0x00ecda70` | crt | fflush | i | 3 | 4 | libc: fflush |
| `0x00ecdacc` | crt | feof | n | 1 | 1 | libc: feof |
| `0x00ecdb00` | crt | wcslen | n | 3 | 4 | fnvnx_wcslen16 |
| `0x00ecdb42` | crt | strerror | i | 1 | 2 | libc: strerror |
| `0x00ecdd16` | crt | _gcvt | n | 3 | 3 | platform/crt: _gcvt |
| `0x00ecddf1` | crt | _vsnprintf | i | 2 | 3 | platform/crt: _vsnprintf |
| `0x00ecdf2d` | crt | strtod | i | 2 | 4 | libc: strtod |
| `0x00ecdf44` | crt | __iob_func | i | 1 | 1 | NONE: needs a rule (MSVC FILE array layout) |
| `0x00ece104` | helper | _except_handler3 | n | 1 | 0 | none: SEH not reproduced |
| `0x00ece23e` | crt | char*(char*, const char*) - unidentified (_mbstok?) | u | 1 | 1 | identify from the exe |
| `0x00ee6d44` | thunk | ws2_32 closesocket | i | 1 | 2 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d4a` | thunk | ws2_32 socket | i | 1 | 2 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d50` | thunk | ws2_32 WSAGetLastError | i | 1 | 2 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d56` | thunk | ws2_32 recv | i | 1 | 2 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d5c` | thunk | ws2_32 send | i | 1 | 2 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d62` | thunk | ws2_32 connect (sockaddr, len) | i | 1 | 1 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d68` | thunk | ws2_32 ws2_32 (1 arg) | i | 1 | 1 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d6e` | thunk | ws2_32 ws2_32 (1 arg) | i | 1 | 1 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d74` | thunk | ws2_32 htons | i | 1 | 1 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d7a` | thunk | ws2_32 ws2_32 (4 args: select?) | i | 1 | 2 | fnvnx_ws2_* (see imports.tsv) |
| `0x00ee6d80` | thunk | ws2_32 ioctlsocket | i | 1 | 2 | fnvnx_ws2_* (see imports.tsv) |

## Imports (186 IAT slots)

`api` is the name most units use; `other names` are what other units call the same slot (see "Counts").

| slot | dll | api | units | other names | provider |
|---|---|---|---:|---|---|
| `0x00fdf000` | advapi32 | RegCreateKeyExA | 1 | - | S4 platform/win32 |
| `0x00fdf004` | advapi32 | RegCloseKey | 3 | - | S4 platform/win32 |
| `0x00fdf008` | advapi32 | RegQueryValueExA | 3 | - | S4 platform/win32 |
| `0x00fdf00c` | advapi32 | RegOpenKeyExA | 3 | - | S4 platform/win32 |
| `0x00fdf010` | advapi32 | RegSetValueExA | 1 | - | S4 platform/win32 |
| `0x00fdf014` | kernel32? | GetComputerNameA | 1 | - | S4 platform/win32 |
| `0x00fdf02c` | dinput8 | DirectInput8Create | 1 | - | S7 |
| `0x00fdf034` | dsound | DirectSoundCreate8 | 1 | - | S7 |
| `0x00fdf03c` | gdi32 | GetStockObject | 1 | - | S4 platform/win32 |
| `0x00fdf044` | kernel32 | TlsGetValue | 162 | free | S4 platform/win32 |
| `0x00fdf048` | kernel32 | TlsAlloc | 1 | - | S4 platform/win32 |
| `0x00fdf04c` | kernel32 | TlsSetValue | 7 | - | S4 platform/win32 |
| `0x00fdf050` | kernel32 | TlsFree | 1 | - | S4 platform/win32 |
| `0x00fdf054` | kernel32 | InitializeCriticalSection | 12 | - | S4 platform/win32 |
| `0x00fdf058` | kernel32 | DeleteCriticalSection | 29 | DestroyWindow | S4 platform/win32 |
| `0x00fdf05c` | kernel32 | EnterCriticalSection | 57 | ArenaEnterCriticalSection, FUN_00fdf05c, fnv_EnterCriticalSection | S4 platform/win32 |
| `0x00fdf060` | kernel32 | GetTickCount | 18 | timeGetTime | S4 platform/win32 |
| `0x00fdf064` | kernel32 | FindClose | 10 | - | S4 platform/win32 |
| `0x00fdf068` | kernel32 | FindNextFileA | 5 | - | S4 platform/win32 |
| `0x00fdf06c` | kernel32 | lstrcpyA | 2 | CompareFileTime | S4 platform/win32 |
| `0x00fdf070` | kernel32 | FindFirstFileA | 9 | - | S4 platform/win32 |
| `0x00fdf074` | kernel32 | lstrcatA | 2 | - | S4 platform/win32 |
| `0x00fdf078` | kernel32 | lstrcpy | 2 | - | S4 platform/win32 |
| `0x00fdf07c` | kernel32 | CloseHandle | 19 | DeleteObject, FreeLibrary | S4 platform/win32 |
| `0x00fdf080` | kernel32 | ReadFile | 3 | - | S4 platform/win32 |
| `0x00fdf084` | kernel32 | GetFileSize | 2 | - | S4 platform/win32 |
| `0x00fdf088` | kernel32 | CreateFileA | 3 | - | S4 platform/win32 |
| `0x00fdf08c` | kernel32 | LocalFree | 2 | - | S4 platform/win32 |
| `0x00fdf090` | kernel32 | FormatMessageA | 1 | - | S4 platform/win32 |
| `0x00fdf094` | kernel32 | GetLastError | 5 | - | S4 platform/win32 |
| `0x00fdf098` | kernel32 | FileTimeToSystemTime | 1 | - | S4 platform/win32 |
| `0x00fdf0a0` | kernel32 | QueryPerformanceCounter | 7 | - | S4 platform/win32 |
| `0x00fdf0a4` | kernel32 | QueryPerformanceFrequency | 6 | - | S4 platform/win32 |
| `0x00fdf0a8` | kernel32 | FreeLibrary | 2 | IMP_fdf0a8 | S4 platform/win32 |
| `0x00fdf0ac` | kernel32 | GetProcAddress | 2 | IMP_fdf0ac | S4 platform/win32 |
| `0x00fdf0b0` | kernel32 | LoadLibraryA | 2 | IMP_fdf0b0 | S4 platform/win32 |
| `0x00fdf0b4` | kernel32 | CreateSemaphoreA | 9 | - | S4 platform/win32 |
| `0x00fdf0b8` | kernel32 | CreateDirectoryA | 6 | - | S4 platform/win32 |
| `0x00fdf0bc` | kernel32 | WritePrivateProfileStringA | 3 | - | S4 platform/win32 |
| `0x00fdf0c0` | kernel32 | GetCurrentDirectoryA | 1 | - | S4 platform/win32 |
| `0x00fdf0c4` | kernel32 | GetPrivateProfileIntA | 1 | - | S4 platform/win32 |
| `0x00fdf0c8` | kernel32 | DebugBreak | 7 | - | S4 platform/win32 |
| `0x00fdf0cc` | kernel32 | CreateEventA | 3 | - | S4 platform/win32 |
| `0x00fdf0d0` | kernel32 | SetEvent | 4 | Sleep | S4 platform/win32 |
| `0x00fdf0d4` | kernel32 | DeleteFileA | 6 | - | S4 platform/win32 |
| `0x00fdf0d8` | kernel32 | GetSystemTime | 1 | - | S4 platform/win32 |
| `0x00fdf0dc` | kernel32 | WriteFile | 1 | - | S4 platform/win32 |
| `0x00fdf0e0` | kernel32 | CoCreateGuid | 1 | - | S4 platform/win32 |
| `0x00fdf0ec` | kernel32 | ExitProcess | 2 | - | S4 platform/win32 |
| `0x00fdf0f0` | kernel32 | IsDebuggerPresent | 1 | - | S4 platform/win32 |
| `0x00fdf0f4` | kernel32 | GetCommandLineW | 1 | - | S4 platform/win32 |
| `0x00fdf0f8` | kernel32 | GetSystemInfo | 5 | - | S4 platform/win32 |
| `0x00fdf0fc` | kernel32 | CreateMutexA | 1 | - | S4 platform/win32 |
| `0x00fdf100` | kernel32 | LeaveCriticalSection | 58 | ArenaLeaveCriticalSection, FUN_00fdf100, fnv_Import_LeaveCriticalSection, fnv_LeaveCriticalSection | S4 platform/win32 |
| `0x00fdf104` | kernel32 | Sleep | 12 | - | S4 platform/win32 |
| `0x00fdf108` | kernel32 | WaitForMultipleObjects | 2 | - | S4 platform/win32 |
| `0x00fdf10c` | kernel32 | VirtualFree | 4 | - | S4 platform/win32 |
| `0x00fdf110` | kernel32 | GetCurrentProcess | 2 | - | S4 platform/win32 |
| `0x00fdf114` | kernel32 | VirtualAlloc | 6 | - | S4 platform/win32 |
| `0x00fdf11c` | kernel32 | SetThreadPriority | 2 | - | S4 platform/win32 |
| `0x00fdf120` | kernel32 | GetCurrentThread | 2 | - | S4 platform/win32 |
| `0x00fdf124` | kernel32 | CreateThread | 6 | - | S4 platform/win32 |
| `0x00fdf12c` | kernel32 | SetThreadIdealProcessor | 1 | - | S4 platform/win32 |
| `0x00fdf130` | kernel32 | InitializeCriticalSectionAndSpinCount | 1 | - | S4 platform/win32 |
| `0x00fdf134` | kernel32 | SystemTimeToTzSpecificLocalTime | 1 | - | S4 platform/win32 |
| `0x00fdf13c` | kernel32 | ? (decomp: GO) | 1 | GO | resolve name from the exe import directory |
| `0x00fdf140` | kernel32 | SetFilePointerEx | 1 | - | S4 platform/win32 |
| `0x00fdf144` | kernel32 | GlobalMemoryStatus | 1 | - | S4 platform/win32 |
| `0x00fdf148` | kernel32 | ExitThread | 2 | - | S4 platform/win32 |
| `0x00fdf14c` | kernel32 | GlobalMemoryStatusEx | 1 | - | S4 platform/win32 |
| `0x00fdf154` | kernel32 | RaiseException | 2 | - | S4 platform/win32 |
| `0x00fdf158` | kernel32 | ResumeThread | 4 | - | S4 platform/win32 |
| `0x00fdf15c` | kernel32 | SuspendThread | 2 | FreeLibrary | S4 platform/win32 |
| `0x00fdf160` | kernel32 | GetFileAttributesA | 2 | - | S4 platform/win32 |
| `0x00fdf164` | kernel32 | GetPrivateProfileStringA | 3 | - | S4 platform/win32 |
| `0x00fdf168` | kernel32 | GetExitCodeThread | 2 | - | S4 platform/win32 |
| `0x00fdf174` | kernel32 | RtlUnwind | 1 | - | S4 platform/win32 |
| `0x00fdf1b0` | kernel32 | TryEnterCriticalSection | 2 | fnv_Import_TryEnterCriticalSection | S4 platform/win32 |
| `0x00fdf1b4` | kernel32 | ReleaseSemaphore | 11 | - | S4 platform/win32 |
| `0x00fdf1b8` | kernel32 | CopyFileA | 1 | - | S4 platform/win32 |
| `0x00fdf1bc` | kernel32 | InterlockedCompareExchange | 8 | - | S4 platform/win32 |
| `0x00fdf1c0` | kernel32 | InterlockedIncrement | 248 | EnterCriticalSection | S4 platform/win32 |
| `0x00fdf1c4` | kernel32 | InterlockedDecrement | 293 | FUN_00fdf1c4_import, IsBadCodePtr, LeaveCriticalSection, TryEnterCriticalSection ... | S4 platform/win32 |
| `0x00fdf1c8` | kernel32 | WaitForSingleObject | 19 | FUN_00fdf1c8 | S4 platform/win32 |
| `0x00fdf1cc` | kernel32 | ResetEvent | 1 | - | S4 platform/win32 |
| `0x00fdf1d0` | kernel32 | GetCurrentThreadId | 16 | - | S4 platform/win32 |
| `0x00fdf1ec` | kernel32 | InterlockedExchangeAdd | 3 | DAT_00fdf1ec | S4 platform/win32 |
| `0x00fdf270` | shell32 | CommandLineToArgvW | 1 | - | S4 platform/win32 |
| `0x00fdf278` | shell32 | ShellExecuteA | 2 | - | S4 platform/win32 |
| `0x00fdf280` | user32 | LoadIconA | 1 | - | S4 platform/win32 |
| `0x00fdf288` | user32 | ShowWindow | 1 | - | S4 platform/win32 |
| `0x00fdf28c` | user32 | SendMessageA | 3 | - | S4 platform/win32 |
| `0x00fdf294` | user32 | DispatchMessageA | 3 | - | S4 platform/win32 |
| `0x00fdf298` | user32 | TranslateMessage | 3 | - | S4 platform/win32 |
| `0x00fdf29c` | user32 | PeekMessageA | 3 | - | S4 platform/win32 |
| `0x00fdf2a4` | user32 | SetWindowPos | 3 | IMP_fdf2a4 | S4 platform/win32 |
| `0x00fdf2a8` | user32 | GetWindow | 1 | - | S4 platform/win32 |
| `0x00fdf2ac` | user32 | ShowCursor | 2 | - | S4 platform/win32 |
| `0x00fdf2b0` | user32 | GetSystemMetrics | 3 | IMP_fdf2b0 | S4 platform/win32 |
| `0x00fdf2b4` | user32 | RegisterClassA | 1 | - | S4 platform/win32 |
| `0x00fdf2b8` | user32 | CreateWindowExA | 2 | IMP_fdf2b8 | S4 platform/win32 |
| `0x00fdf2c0` | user32 | GetDoubleClickTime | 1 | - | S4 platform/win32 |
| `0x00fdf2c4` | user32 | SwapMouseButton | 1 | - | S4 platform/win32 |
| `0x00fdf2c8` | user32 | UnhookWindowsHookEx | 1 | - | S4 platform/win32 |
| `0x00fdf2d4` | user32 | SendInput | 1 | - | S4 platform/win32 |
| `0x00fdf2d8` | user32 | SetWindowsHookExA | 1 | - | S4 platform/win32 |
| `0x00fdf2dc` | user32 | SetWindowTextA | 2 | - | S4 platform/win32 |
| `0x00fdf2e0` | user32 | DefWindowProcA | 2 | - | S4 platform/win32 |
| `0x00fdf2e4` | user32 | EnumChildWindows | 1 | - | S4 platform/win32 |
| `0x00fdf2e8` | user32 | GetWindowLongA | 2 | IMP_fdf2e8 | S4 platform/win32 |
| `0x00fdf2ec` | user32 | ? (user32; decomp: IMP_fdf2ec) | 1 | IMP_fdf2ec | resolve name from the exe import directory |
| `0x00fdf2f4` | user32 | MessageBoxA | 4 | - | S4 platform/win32 |
| `0x00fdf2f8` | user32 | AdjustWindowRectEx | 1 | - | S4 platform/win32 |
| `0x00fdf2fc` | user32 | GetForegroundWindow | 3 | GetActiveWindow | S4 platform/win32 |
| `0x00fdf300` | user32 | SetForegroundWindow | 1 | - | S4 platform/win32 |
| `0x00fdf304` | user32 | FindWindowA | 1 | - | S4 platform/win32 |
| `0x00fdf308` | user32 | GetAsyncKeyState | 1 | - | S4 platform/win32 |
| `0x00fdf30c` | user32 | CallNextHookEx | 1 | - | S4 platform/win32 |
| `0x00fdf310` | user32 | AdjustWindowRect | 2 | IMP_fdf310 | S4 platform/win32 |
| `0x00fdf334` | winmm | timeGetTime | 1 | - | S4 platform/win32 |
| `0x00fdf364` | ws2_32 | WSAStartup | 1 | - | S4 platform/win32 |
| `0x00fdf368` | ws2_32 | gethostname | 1 | - | S4 platform/win32 |
| `0x00fdf36c` | ws2_32 | listen | 1 | - | S4 platform/win32 |
| `0x00fdf370` | ws2_32 | bind | 1 | - | S4 platform/win32 |
| `0x00fdf374` | ws2_32 | setsockopt | 1 | - | S4 platform/win32 |
| `0x00fdf378` | ws2_32 | inet_ntoa | 1 | - | S4 platform/win32 |
| `0x00fdf384` | ws2_32 | accept | 1 | - | S4 platform/win32 |
| `0x00fdf388` | ws2_32 | ntohs | 1 | - | S4 platform/win32 |
| `0x00fdf394` | xinput1_3 | XInputGetState | 1 | - | S7 |
| `0x00fdf3a0` | binkw32 | BinkSetSoundSystem | 1 | - | S6 (video) |
| `0x00fdf3a4` | binkw32 | BinkClose | 1 | - | S6 (video) |
| `0x00fdf3a8` | binkw32 | BinkNextFrame | 1 | - | S6 (video) |
| `0x00fdf3ac` | binkw32 | BinkWait | 1 | - | S6 (video) |
| `0x00fdf3b0` | binkw32 | BinkPause | 1 | BinkPause_8, SetThreadPriorityBoost | S6 (video) |
| `0x00fdf3b4` | binkw32 | BinkOpenDirectSound | 1 | - | S6 (video) |
| `0x00fdf3b8` | binkw32 | BinkOpen | 1 | - | S6 (video) |
| `0x00fdf3bc` | binkw32 | BinkDoFrame | 1 | - | S6 (video) |
| `0x00fdf3c0` | binkw32 | BinkCopyToBufferRect | 1 | - | S6 (video) |
| `0x00fdf3c8` | d3d9 | D3DPERF_SetOptions | 1 | - | S6 |
| `0x00fdf3d0` | d3dx9_38 | D3DXLoadSurfaceFromSurface | 1 | - | S6 |
| `0x00fdf3d4` | d3dx9_38 | ? (d3dx9; decomp: ImportedFunction) | 1 | ImportedFunction | S6 |
| `0x00fdf3d8` | d3dx9_38 | D3DXAssembleShader | 1 | - | S6 |
| `0x00fdf3dc` | d3dx9_38 | D3DXAssembleShaderFromFileA | 1 | - | S6 |
| `0x00fdf3e0` | d3dx9_38 | D3DXCompileShaderFromFileA | 1 | - | S6 |
| `0x00fdf3e4` | d3dx9_38 | D3DXGetShaderConstantTable | 1 | - | S6 |
| `0x00fdf3e8` | d3dx9_38 | D3DXCreateBuffer | 1 | - | S6 |
| `0x00fdf3ec` | d3dx9_38 | D3DXVec4Transform | 1 | - | S6 |
| `0x00fdf3f4` | d3dx9_38 | D3DXGetPixelShaderProfile | 1 | - | S6 |
| `0x00fdf3f8` | d3dx9_38 | D3DXGetImageInfoFromFileInMemory | 1 | - | S6 |
| `0x00fdf3fc` | d3dx9_38 | D3DXCreateTextureFromFileInMemory | 1 | - | S6 |
| `0x00fdf400` | d3dx9_38 | D3DXCreateCubeTextureFromFileInMemory | 1 | - | S6 |
| `0x00fdf404` | d3dx9_38 | D3DXCreateVolumeTextureFromFileInMemory | 1 | - | S6 |
| `0x00fdf408` | d3dx9_38 | D3DXCompileShader | 1 | - | S6 |
| `0x00fdf40c` | d3dx9_38 | D3DXMatrixRotationYawPitchRoll | 1 | - | S6 |
| `0x00fdf410` | d3dx9_38 | D3DXMatrixMultiplyTranspose | 1 | - | S6 |
| `0x00fdf414` | d3dx9_38 | D3DXVec3TransformCoord | 1 | - | S6 |
| `0x00fdf418` | d3dx9_38 | D3DXPlaneTransform | 1 | - | S6 |
| `0x00fdf41c` | d3dx9_38 | D3DXVec3Normalize | 1 | - | S6 |
| `0x00fdf424` | d3dx9_38 | D3DXMatrixMultiply | 1 | - | S6 |
| `0x00fdf42c` | d3dx9_38 | D3DXMatrixTranspose | 1 | - | S6 |
| `0x00fdf430` | d3dx9_38 | D3DXPlaneNormalize | 1 | - | S6 |
| `0x00fdf43c` | vorbisfile | ov_clear | 1 | - | S7 (libvorbisfile) |
| `0x00fdf440` | vorbisfile | ov_pcm_total | 1 | - | S7 (libvorbisfile) |
| `0x00fdf444` | vorbisfile | ov_seekable | 1 | - | S7 (libvorbisfile) |
| `0x00fdf448` | vorbisfile | ov_read | 1 | - | S7 (libvorbisfile) |
| `0x00fdf44c` | vorbisfile | ov_info | 1 | - | S7 (libvorbisfile) |
| `0x00fdf454` | ole32 | CoInitialize | 2 | GetModuleHandleA | S4 platform/win32 |
| `0x00fdf458` | ole32 | CoInitializeEx | 1 | - | S4 platform/win32 |
| `0x00fdf45c` | ole32 | CoTaskMemFree | 1 | - | S4 platform/win32 |
| `0x00fdf460` | ole32 | CoTaskMemAlloc | 1 | - | S4 platform/win32 |
| `0x00fdf464` | ole32 | CoUninitialize | 1 | - | S4 platform/win32 |
| `0x00fdf468` | ole32 | CoFreeUnusedLibraries | 1 | - | S4 platform/win32 |
| `0x00fdf46c` | ole32 | CoCreateInstance | 2 | - | S4 platform/win32 |
| `0x00fdf474` | steam_api | SteamFriends | 1 | - | S4 platform/win32 |
| `0x00fdf478` | steam_api | SteamAPI_Init | 1 | - | S4 platform/win32 |
| `0x00fdf47c` | steam_api | SteamAPI_UnregisterCallback | 1 | - | S4 platform/win32 |
| `0x00fdf480` | steam_api | SteamAPI_Shutdown | 1 | - | S4 platform/win32 |
| `0x00fdf484` | steam_api | SteamUser | 2 | - | S4 platform/win32 |
| `0x00fdf488` | steam_api | SteamAPI_RunCallbacks | 1 | - | S4 platform/win32 |
| `0x00fdf48c` | steam_api | SteamAPI_RegisterCallback | 1 | B6FFB20Notify | S4 platform/win32 |
| `0x00fdf490` | steam_api | SteamUserStats | 2 | SteamUserStatsAccessor | S4 platform/win32 |
| `0x00fdf494` | steam_api | SteamApps | 1 | - | S4 platform/win32 |
| `0x00fdf498` | steam_api | SteamAPI_IsSteamRunning | 1 | - | S4 platform/win32 |
| `0x00fdf49c` | steam_api | SteamUtils? / SteamUser? (conflict) | 2 | SteamUser, SteamUtils | S4 platform/win32 |
| `0x010b7eb0` | (not an IAT slot) | FreeBlockImp | 1 | - | none: decomp mislabel (data address) |
| `0x010c4cec` | (not an IAT slot) | Free | 1 | - | none: decomp mislabel (data address) |

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
`0x00ec3bad`, `0x00ec3bda` cpplib -> crt (byteswaps), so cpplib 28, crt 119. Code: `platform/crt/cpplib.cpp`,
`eh.cpp`, `iob.c` (+ `fnvnx_iobuf.h`, `fnvnx_msvc_cpp.h`).

### The libcpmt internals (25) and the 12 unknown libcmt entries

Identified from the decomp's call sites (arguments, the code around the call, object offsets the callers use)
and VC9's library layout. `conf.`: h high, m medium, l low.

| address | function | evidence (call sites) | conf. | provider |
|---|---|---|---|---|
| `0x00ec31b3` | `ios_base::_Addstd(ios_base*)` | `basic_ios::init(sb, isstd)` calls it when `isstd` | h | `fnvnx_ios_base_addstd` |
| `0x00ec3205` | `ios_base::_Ios_base_dtor` | (named) | h | `fnvnx_ios_base_dtor` |
| `0x00ec3246` | `_Mutex::_Mutex()` | member at +4 of `basic_streambuf` (`_Mylock`), constructed in its ctor | h | `fnvnx_mutex_ctor` |
| `0x00ec3260` | `_Mutex::~_Mutex()` | called on `this+4` at the end of `~basic_streambuf` | h | `fnvnx_mutex_dtor` |
| `0x00ec3277` | `_Mutex::_Lock()` | on `rdbuf()+4` in the ostream sentry constructor | h | `fnvnx_mutex_lock` |
| `0x00ec3280` | `_Mutex::_Unlock()` | on `rdbuf()+4` in the sentry destructor | h | `fnvnx_mutex_unlock` |
| `0x00ec3289` / `0x00ec32ba` | `_Init_locks::_Init_locks()` / `~_Init_locks()` | ctor/dtor of 3 libcpmt statics (0x0126fb90, 0x0126fc49, 0x0126fc94) | h | `fnvnx_init_locks_ctor` / `_dtor` |
| `0x00ec33da` | `_Fiopen(name, openmode, prot)` | `basic_filebuf::open` | h | `fnvnx_fiopen` |
| `0x00ec34d4` | `locale::_Getgloballocale()` | fallback of the inline `_Getfacet` when `_Xparent`; `_Incref` in `locale()` | h | `fnvnx_locale_getgloballocale` |
| `0x00ec3599` | `locale::facet::_Facet_Register` | (named) | h | `fnvnx_facet_register` |
| `0x00ec35d7` | `_Locinfo::_Locinfo_dtor(_Locinfo*)` | body of `~_Locinfo` before the 4 string members | h | `fnvnx_locinfo_dtor` |
| `0x00ec36a6` | `locale::_Init()` | `locale()`: `_Ptr = _Init()` in `ios_base::_Init` | h | `fnvnx_locale_init` |
| `0x00ec3740` | `_Locinfo::_Locinfo_ctor(this, const string&)` | (inferred by S4; the string argument: R2, the byte-matched 0x00ad3ca0) | h | `fnvnx_locinfo_ctor` |
| `0x00ec379c` / `0x00ec3909` | `_Tolower` / `_Toupper(int, const _Ctypevec*)` | `ctype<char>::do_tolower/upper` with `this+8` | h | `fnvnx_tolower_ctype` / `fnvnx_toupper_ctype` |
| `0x00ec38a9` | `_Getctype()` | returns a 16-byte `_Ctypevec` by value | h | `fnvnx_getctype` |
| `0x00ec3a75` | `basic_ostream<char>(streambuf*, bool)` + MSVC's vbase flag | the libcpmt standard ostream at 0x0126fc10 (filebuf at 0x0126fbc0, 0x50 bytes before), args (sb, 0, 1); its dtor 0x00ace2d0 reads the vbptr | m | `fnvnx_ostream_ctor` |
| `0x00ec3bad` | `_byteswap_ulong` | `*p = f(*p)` in the 4-byte `BSEndian` helpers | h | `fnvnx_byteswap_ulong` |
| `0x00ec3bda` | `_byteswap_ushort` | the 2-byte `BSEndian` helper | h | `fnvnx_byteswap_ushort` |
| `0x00ec78e0` .. `0x00ec79a1` | `std::exception` ctor, ctor(`const char* const&`), copy ctor, dtor | (named / inferred) | h | `fnvnx_exception_ctor`, `_ctor_str`, `_copy_ctor`, `_dtor` |
| `0x00ec79c4` | `bad_cast::bad_cast(const char*)` | (named) | h | `fnvnx_bad_cast_ctor` |
| `0x00ec3bf0` | `_byteswap_uint64` | 8-byte `*p = f(*p)` in `tesfile.cpp` | h | `fnvnx_byteswap_uint64` |
| `0x00ec7230` | `memcpy` (the second copy next to `memmove` 0x00ec44d0) | `hkString::memCpy`; one caller shifts an overlapping range up | h | libc `memmove` (VC9's `memcpy` is overlap-safe; also for 0x00ec44d0) |
| `0x00ec858a` | `_mbsicmp` (`_tcsicmp` under `_MBCS`; or the `_strcmpi` entry) | compares a window class with "Button", file extensions | m | `_mbsicmp` |
| `0x00eca903` | `_mbsnbcpy` / `_mbsncpy` | (dst, src, buffer size) | m | `_mbsnbcpy` |
| `0x00eca920` | `log` | Box-Muller `sqrt(-2 log s / s)`; float wrapper next to `logf` | h | libc `log` |
| `0x00ecacb2` | `_mbsncpy` / `_mbsnbcpy` (the other one) | name into an uninitialized buffer, size = buffer | m | `_mbsncpy` |
| `0x00ecb849` / `0x00ecba8e` | `_mbsnbcmp` / `_mbsnbicmp` | console command prefixes (`"reload "`, 7); which is which from code size | l | `_mbsnbcmp` / `_mbsnbicmp` |
| `0x00ecbc80` | `log10` | volume attenuation in hundredths of dB | h | libc `log10` |
| `0x00ecc071` | `_wcsicmp` | `CommandLineToArgvW` option after `-` against a wide literal | m | `fnvnx_wcsicmp16` |
| `0x00eccbdc` | `_getcwd(char*, int)` | NiPath: buffer of 260, result tested non-NULL | h | `fnvnx_getcwd` |
| `0x00ece23e` | `_mbstok` | movie list tokenizer (with the `_mbsicmp` above: the movie code is `_MBCS`) | m | `fnvnx_mbstok` (own position) |

All MBCS functions behave as their single-byte counterparts (the "C" code page has no lead bytes). The image
settles the `m`/`l` rows (a byte comparison with VC9 SP1 `libcmt.lib`).

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
decomp's three ThrowInfos are typed from the code around the calls: 0x01140ef8 = `std::bad_alloc` (exception
built from a null message, vftable 0x010176f8, the allocators' overflow paths; h), 0x01140cb0 =
`std::length_error` (a `logic_error(const string&)` object, vftable 0x010173dc; m) - both registered by
`fnvnx_eh_register_fnv_known()` - and 0x01170384 = SpeedTree's exception class (string, int), which needs a
native type: S1 must first give its per-unit placeholder names (`CIdvException`, `ExceptionB07`,
`IdvException`, `ExcQ`, `Exc59`) one class, then `fnvnx_eh_register_type<That>(0x01170384)`.

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
* Objects get MSVC image vftables written into their vptr by hand (0x010176f8 bad_alloc, 0x010173dc
  length_error, 0x010a2f9c / 0x010a2fa4 ios_base / basic_ios, 0x010a318c ostream vbase): the runtime never
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
* **FreeBlockImp** is not data: the decomp's `call [0x010b7eb0]` reads a `.rdata` vtable word holding `0x00cb9cb0`,
  `mov ecx, 0x011f6238; jmp 0x00aa4060` = `MemoryManager::Deallocate` on the global heap = FormHeap_Free
  (`0x00401030`), which the stand-in now calls.
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
