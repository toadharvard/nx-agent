/* fnvnx_win32.h - MSVC 2008 stand-in for shim/include/fnvnx_win32.h in S11 equivalence builds (tools/equiv).
 *
 * The rules that include fnvnx_win32.h (DECL1, DECL2, SDK1, INTL1) claim that it declares the Win32 API with the
 * SDK's types. For MSVC x86 that is the real Windows SDK (v6.0A, from the Visual C++ 2008 Express ISO): this header
 * is the real <windows.h>, adjusted where the target shim deliberately differs from the SDK, so that MSVC sees what
 * the target sees:
 *
 *  - the SDK's function-name macros (CreateFile -> CreateFileA, GetObject -> GetObjectA, min / max, ...) are not in
 *    the target shim; they are undefined again (generated list, fnvnx_equiv_unaw.h, from the SDK in use) when this
 *    header is what brought <windows.h> in;
 *  - functions the target shim declares with a looser parameter type than the SDK (void * for
 *    LPSECURITY_ATTRIBUTES, va_list *, LPINPUT, IUnknown *; pointers for REFCLSID / REFIID) are declared with the
 *    shim's parameter types and the SDK's linkage, calling convention and dllimport: same import, same call;
 *  - the overloads the target shim adds for game units (void * for the critical-section functions, long long * for
 *    the performance counter) are C++ dllimport declarations of the same imports; tools/equiv binds the decorated
 *    import name to the import's address (Resolver, import names);
 *  - the decomp forward-declares `class _ULARGE_INTEGER;`, the SDK's type is `union _ULARGE_INTEGER`: GCC accepts
 *    the key mismatch (-fpermissive) and the shim's union is that type; MSVC rejects it (C2011) and decorates the
 *    key, so here the SDK's union keeps its typedef names under another tag and _ULARGE_INTEGER is a class with the
 *    SDK union's members and layout;
 *  - object-like SDK macros the shim does not define (winerror.h's ERROR_* codes, `small`, `hyper`, `Yield`,
 *    `_FILETIME_`) are undefined again: the decomp defines enumerators and types of those names;
 *  - SDK-HIDE: every SDK function and typedef name the target shim never names (equiv.py sdk_only_names) is read
 *    under another name (fnvnx_sdk_<name>): the decomp's own types and functions of those names (struct Arc,
 *    DragObject, typedef PCH, gethostname) do not collide with them, as they do not on the target;
 *  - FNVNX_CS28 (rule CS28).
 */
#ifndef FNVNX_EQUIV_WIN32_H
#define FNVNX_EQUIV_WIN32_H
#if defined(__cplusplus) && !defined(_WINDOWS_)
#define FNVNX_EQUIV_WINDOWS_HERE 1
#ifdef FNVNX_EQUIV_LEAN
/* equiv.py build --lean (retry of units the full SDK breaks): the target shim declares a small subset, and the
 * full <windows.h> also brings winioctl / rpc / ole names (enumerators like RequestLocation, `#define small char`)
 * that collide with the decomp's own names. WIN32_LEAN_AND_MEAN leaves those headers out. */
#define WIN32_LEAN_AND_MEAN
#endif
#define _ULARGE_INTEGER fnvnx_sdk_ULARGE_INTEGER
#define CreateEventA fnvnx_sdk_CreateEventA
#define CreateMutexA fnvnx_sdk_CreateMutexA
#define CreateSemaphoreA fnvnx_sdk_CreateSemaphoreA
#define CreateThread fnvnx_sdk_CreateThread
#define CreateFileA fnvnx_sdk_CreateFileA
#define CreateDirectoryA fnvnx_sdk_CreateDirectoryA
#define FormatMessageA fnvnx_sdk_FormatMessageA
#define SendInput fnvnx_sdk_SendInput
#define RegCreateKeyExA fnvnx_sdk_RegCreateKeyExA
#define CoCreateInstance fnvnx_sdk_CoCreateInstance
#include "fnvnx_equiv_sdkhide.h"     /* SDK-HIDE: SDK names the target shim never declares, renamed (generated) */
#include "fnvnx_equiv_sdk_windows.h"  /* the SDK's <windows.h> (generated; msvc/windows.h stands in for it) */
#include "fnvnx_equiv_sdkunhide.h"
#undef _ULARGE_INTEGER
#undef CreateEventA
#undef CreateMutexA
#undef CreateSemaphoreA
#undef CreateThread
#undef CreateFileA
#undef CreateDirectoryA
#undef FormatMessageA
#undef SendInput
#undef RegCreateKeyExA
#undef CoCreateInstance
#include "fnvnx_equiv_unaw.h"       /* generated: #undef of every SDK function-name macro */
#include "fnvnx_equiv_winerror.h"   /* generated: #undef of winerror.h's ERROR_* macros */
#undef min
#undef max
/* object-like SDK macros the target shim does not have, which the decomp uses as its own names: rpcndr.h's
 * `#define small char` / `hyper` (members and locals called `small`), winbase.h's `#define Yield()` (the decomp's
 * `void __cdecl Yield(unsigned long);`), wtypes.h's include guard `_FILETIME_` (a decomp struct of that name) */
#undef small
#undef hyper
#undef Yield
#undef _FILETIME_
/* winnt.h includes <ctype.h>; the target shim includes no CRT header, and MSVC's _tolower / _toupper macros break
 * the decomp's own prototypes of those names. */
#undef _tolower
#undef _toupper
class _ULARGE_INTEGER
{
  public:
    union {
        struct { DWORD LowPart; DWORD HighPart; };
        struct { DWORD LowPart; DWORD HighPart; } u;
        ULONGLONG QuadPart;
    };
};
extern "C" {
__declspec(dllimport) HANDLE WINAPI CreateEventA(void *, BOOL, BOOL, LPCSTR);
__declspec(dllimport) HANDLE WINAPI CreateMutexA(void *, BOOL, LPCSTR);
__declspec(dllimport) HANDLE WINAPI CreateSemaphoreA(void *, LONG, LONG, LPCSTR);
__declspec(dllimport) HANDLE WINAPI CreateThread(void *, SIZE_T, LPTHREAD_START_ROUTINE, LPVOID, DWORD, LPDWORD);
__declspec(dllimport) HANDLE WINAPI CreateFileA(LPCSTR, DWORD, DWORD, void *, DWORD, DWORD, HANDLE);
__declspec(dllimport) BOOL WINAPI CreateDirectoryA(LPCSTR, void *);
__declspec(dllimport) DWORD WINAPI FormatMessageA(DWORD, LPCVOID, DWORD, DWORD, LPSTR, DWORD, void *);
__declspec(dllimport) UINT WINAPI SendInput(UINT, void *, int);
__declspec(dllimport) LSTATUS WINAPI RegCreateKeyExA(HKEY, LPCSTR, DWORD, LPSTR, DWORD, REGSAM, void *, PHKEY, LPDWORD);
__declspec(dllimport) HRESULT WINAPI CoCreateInstance(const GUID *, void *, DWORD, const GUID *, LPVOID *);
}
#else
#include "fnvnx_equiv_sdkhide.h"     /* SDK-HIDE: SDK names the target shim never declares, renamed (generated) */
#include "fnvnx_equiv_sdk_windows.h"  /* the SDK's <windows.h> (generated; msvc/windows.h stands in for it) */
#include "fnvnx_equiv_sdkunhide.h"
#endif

#ifdef __cplusplus
struct FNVNX_CS28 : CRITICAL_SECTION { DWORD fnvnx_after_cs; };

extern "C++" {
__declspec(dllimport) void WINAPI InitializeCriticalSection(void *);
__declspec(dllimport) BOOL WINAPI InitializeCriticalSectionAndSpinCount(void *, DWORD);
__declspec(dllimport) void WINAPI EnterCriticalSection(void *);
__declspec(dllimport) BOOL WINAPI TryEnterCriticalSection(void *);
__declspec(dllimport) void WINAPI LeaveCriticalSection(void *);
__declspec(dllimport) void WINAPI DeleteCriticalSection(void *);
__declspec(dllimport) BOOL WINAPI QueryPerformanceCounter(long long *);
__declspec(dllimport) BOOL WINAPI QueryPerformanceCounter(unsigned long long *);
__declspec(dllimport) BOOL WINAPI QueryPerformanceFrequency(long long *);
__declspec(dllimport) BOOL WINAPI QueryPerformanceFrequency(unsigned long long *);
}
#else
typedef struct FNVNX_CS28 { CRITICAL_SECTION cs; DWORD fnvnx_after_cs; } FNVNX_CS28;
#endif
#endif
