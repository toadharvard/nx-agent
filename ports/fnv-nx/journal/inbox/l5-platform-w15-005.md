# l5-platform-w15-005: dynamic-name sites resolved by one rule (platform/manifest/resolve_dynsites.py)

Tool: platform/manifest/resolve_dynsites.py (--selftest passes). It applies L1's constant propagation (contract/argflow.py of the trunk, taken by --contract)
to every name-lookup row of dynsites.tsv: uses of the slot (call, thunk callers, call reg), D1-D4 derivation of the name argument, GetModuleHandle(NULL) =
the process image, NOT_A_MODULE (an immediate < 0x10000 is no handle: no pair), and the cell rule (conditional). Ranges of images without code facts are
built by the closure rule in the tool. Facts and images are path arguments; manifest result at fnv-nx-build l5-platform/manifest 45a4870e.

Global numbers (commit 45a4870e of the manifest, exe + atimgpud + binkw32 + libvorbis + libvorbisfile): name-lookup rows 261; rows needing a derivation
(dynamic operand or dynamic hmod) 43: resolved 18, conditional on the cell rule 3, unreached (no call reaches the thunk/slot) 4, open 18.
Open 18 = 10 dbghelp GetProcAddress rows (hModule is the field [this+0] of a global object; memfx escape: data word holds the object's address) +
8 rows of d3dx9_31 / d3dx9_38 (the images are not in gamedata/exe nor in any container of this account: needed to resolve 0x56b719 and the rest).
Conditional 3 (cell, memfx cannot exclude an indexed store): exe 0xda284a LoadLibraryA("dbghelp.dll"); binkw32 0x18014728 GetProcAddress(DSOUND.DLL,
DirectSoundCreate); binkw32 0x1800c186 GetProcAddress(DDRAW.DLL, DirectDrawCreate).

Kinds on the declared machine (names asked; bodies not written):
- kernel32.dll (exe via GetModuleHandleW "KERNEL32.DLL"; atimgpud, libvorbis*, exe): EncodePointer, DecodePointer, FlsAlloc, FlsFree, FlsGetValue,
  FlsSetValue, InitializeCriticalSectionAndSpinCount: platform DLL, bridge rows exist for all.
- dbghelp.dll (exe 0xda284a, Havok StackTracer): SymInitialize, SymRefreshModuleList, SymGetOptions, SymSetOptions, SymGetSymFromAddr, StackWalk64,
  SymFunctionTableAccess64, SymGetModuleBase64, SymGetLineFromAddr64, SymCleanup. A real Windows has dbghelp.dll: a DLL the platform must serve (10 bridge
  rows, none exist). CONFLICT: ENV_TRUTH row M3 declares dbghelp absent (LoadLibrary -> NULL, 126); "never absent if a real Windows has it" overrides.
- ddraw.dll (binkw32): DirectDrawCreate. Real Windows has it: platform DLL (ENV_TRUTH M7 declares it absent: same conflict).
- dsound.dll (binkw32): DirectSoundCreate: already served.
- mscoree.dll CorExitProcess (CRT): real Windows has it; GetModuleHandle of it answers NULL until it is declared.
- GetModuleHandle(NULL) in atimgpud/binkw32/libvorbis/libvorbisfile = the exe image (falloutnv.exe).
