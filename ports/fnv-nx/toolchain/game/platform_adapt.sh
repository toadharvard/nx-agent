#!/bin/bash
# Adapts two platform/win32 files to the current shim (S1's fnvnx_win32.h, d972371) for the game build, on copies
# in <out dir>; platform/ itself is S4's. The shim now
#   - declares Interlocked* with C++ linkage (sync.cpp defines them extern "C": "conflicting declaration"), and
#   - adds void* overloads of the critical-section and QueryPerformance* functions (exports.cpp takes their
#     address: "address of overloaded function").
# Needed upstream (S4/S1): drop extern "C" on sync.cpp's Interlocked* definitions; in exports.cpp take the
# address of the LPCRITICAL_SECTION / LARGE_INTEGER* overload explicitly (as done here).
#   toolchain/game/platform_adapt.sh <platform dir> <out dir>    -> <out>/sync.cpp, <out>/exports.cpp
set -euo pipefail
P=$1; O=$2
mkdir -p "$O"
sed -E 's/^extern "C" (LONG|PVOID) (Interlocked[A-Za-z]+\()/\1 \2/' "$P/win32/sync.cpp" > "$O/sync.cpp"
sed -E \
	-e 's/^(#define K\(f\) E\("kernel32", f\))$/\1\n#define KC(f, T) { "kernel32", #f, (void *)static_cast<T>(\&f) }/' \
	-e 's/K\((InitializeCriticalSection|EnterCriticalSection|LeaveCriticalSection|DeleteCriticalSection)\)/KC(\1, void (*)(LPCRITICAL_SECTION))/g' \
	-e 's/K\(TryEnterCriticalSection\)/KC(TryEnterCriticalSection, BOOL (*)(LPCRITICAL_SECTION))/g' \
	-e 's/K\(InitializeCriticalSectionAndSpinCount\)/KC(InitializeCriticalSectionAndSpinCount, BOOL (*)(LPCRITICAL_SECTION, DWORD))/g' \
	-e 's/K\((QueryPerformanceCounter|QueryPerformanceFrequency)\)/KC(\1, BOOL (*)(LARGE_INTEGER *))/g' \
	"$P/win32/exports.cpp" > "$O/exports.cpp"
# the copies include "w32.h" and friends relative to their directory
sed -i "s|#include \"w32.h\"|#include \"$P/win32/w32.h\"|" "$O/sync.cpp" "$O/exports.cpp"
