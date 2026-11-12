/* fnvnx_win32.h - the subset of the Win32 API surface the decomp uses, as declarations.
 *
 * Types follow the Windows SDK definitions for a 32-bit target (ILP32 keeps their sizes). Implementations
 * live in the platform layer (stream S4). Grown from census misses; keep sorted by area.
 */
#ifndef FNVNX_WIN32_H
#define FNVNX_WIN32_H
/* the SDK's own packing (8, MSVC's default), whatever region includes this header: DECL1 / DECL2 put the include
 * where the decomp's prototype or SDK type was, 16 times inside the #pragma pack(push, 4) of its generated layout
 * headers (S11: LARGE_INTEGER, ULARGE_INTEGER, MEMORYSTATUSEX came out 4-aligned there) */
#include "fnvnx_callback.h"
#pragma pack(push, 8)
/* the SDK's own include guards: the decomp's generated layout headers (include/_FILETIME.h, _RTL_CRITICAL_SECTION.h,
 * _ULARGE_INTEGER.h, _WIN32_FIND_DATAA.h) skip their copies when the SDK came first - their copies carry Xbox 360
 * layouts (big-endian FILETIME / ULARGE_INTEGER halves, 28-byte RTL_CRITICAL_SECTION), the PC SDK ones are below. */
#define _WINDEF_
#define _WINNT_
#define _WINBASE_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- base types (windef.h / winnt.h / basetsd.h) */
typedef unsigned long DWORD;
typedef int BOOL;
typedef unsigned char BYTE;
typedef unsigned short WORD;
typedef float FLOAT;
typedef int INT;
typedef unsigned int UINT;
typedef long LONG;
typedef unsigned long ULONG;
typedef short SHORT;
typedef unsigned short USHORT;
typedef char CHAR;
typedef unsigned char UCHAR;
typedef wchar_t WCHAR;
typedef long long LONGLONG;
typedef unsigned long long ULONGLONG;
typedef unsigned long long DWORD64;
typedef unsigned long long UINT64;
typedef long long INT64;
typedef unsigned int UINT32;
typedef int INT32;
typedef int INT_PTR, *PINT_PTR;
typedef unsigned int UINT_PTR, *PUINT_PTR;
typedef long LONG_PTR, *PLONG_PTR;
typedef unsigned long ULONG_PTR, *PULONG_PTR;
typedef ULONG_PTR SIZE_T, DWORD_PTR;
typedef LONG_PTR SSIZE_T;
typedef void VOID, *PVOID, *LPVOID;
typedef const void *LPCVOID;
typedef BYTE *PBYTE, *LPBYTE;
typedef BOOL *PBOOL, *LPBOOL;
typedef DWORD *PDWORD, *LPDWORD;
typedef WORD *PWORD, *LPWORD;
typedef LONG *PLONG, *LPLONG;
typedef INT *PINT, *LPINT;
typedef UINT *PUINT;
typedef ULONG *PULONG;
typedef CHAR *PCHAR, *LPSTR, *PSTR;
typedef const CHAR *LPCSTR, *PCSTR;
typedef WCHAR *PWCHAR, *LPWSTR, *PWSTR;
typedef const WCHAR *LPCWSTR, *PCWSTR;
typedef LPSTR LPTSTR;
typedef LPCSTR LPCTSTR;
typedef char TCHAR;
typedef BYTE BOOLEAN;
typedef LONG HRESULT;
typedef WORD ATOM;
typedef DWORD COLORREF;
typedef UINT_PTR WPARAM;
typedef LONG_PTR LPARAM;
typedef LONG_PTR LRESULT;

typedef void *HANDLE;
typedef HANDLE *PHANDLE, *LPHANDLE;
typedef HANDLE HGLOBAL, HLOCAL;
#define FNVNX_DECLARE_HANDLE(n) struct n##__ { int unused; }; typedef struct n##__ *n
FNVNX_DECLARE_HANDLE(HWND);
FNVNX_DECLARE_HANDLE(HINSTANCE);
FNVNX_DECLARE_HANDLE(HDC);
FNVNX_DECLARE_HANDLE(HICON);
FNVNX_DECLARE_HANDLE(HMENU);
FNVNX_DECLARE_HANDLE(HBRUSH);
FNVNX_DECLARE_HANDLE(HFONT);
FNVNX_DECLARE_HANDLE(HBITMAP);
FNVNX_DECLARE_HANDLE(HKEY);
FNVNX_DECLARE_HANDLE(HMONITOR);
typedef HINSTANCE HMODULE;
typedef HICON HCURSOR;
typedef void *HGDIOBJ;
typedef int (*FARPROC)(void);

#define FALSE 0
#define TRUE 1
#define CONST const
#define FAR
#define NEAR
#define far
#define near
#define MAX_PATH 260
#define INFINITE 0xFFFFFFFF
#define INVALID_HANDLE_VALUE ((HANDLE)(LONG_PTR)-1)
#define S_OK ((HRESULT)0)
#define S_FALSE ((HRESULT)1)
#define E_FAIL ((HRESULT)0x80004005L)
#define E_OUTOFMEMORY ((HRESULT)0x8007000EL)
#define SUCCEEDED(hr) (((HRESULT)(hr)) >= 0)
#define FAILED(hr) (((HRESULT)(hr)) < 0)
#define MAKEFOURCC(a, b, c, d) ((DWORD)(BYTE)(a) | ((DWORD)(BYTE)(b) << 8) | ((DWORD)(BYTE)(c) << 16) | ((DWORD)(BYTE)(d) << 24))
#define LOWORD(l) ((WORD)((DWORD_PTR)(l) & 0xffff))
#define HIWORD(l) ((WORD)((DWORD_PTR)(l) >> 16))
#define LOBYTE(w) ((BYTE)((DWORD_PTR)(w) & 0xff))
#define HIBYTE(w) ((BYTE)((DWORD_PTR)(w) >> 8))
#define MAKELONG(a, b) ((LONG)(((WORD)(a)) | ((DWORD)((WORD)(b))) << 16))

typedef union _LARGE_INTEGER {
    struct { DWORD LowPart; LONG HighPart; };
    struct { DWORD LowPart; LONG HighPart; } u;
    LONGLONG QuadPart;
} LARGE_INTEGER, *PLARGE_INTEGER;
typedef union _ULARGE_INTEGER {
    struct { DWORD LowPart; DWORD HighPart; };
    struct { DWORD LowPart; DWORD HighPart; } u;
    ULONGLONG QuadPart;
} ULARGE_INTEGER, *PULARGE_INTEGER;

typedef struct tagPOINT { LONG x, y; } POINT, *PPOINT, *LPPOINT;
typedef struct tagRECT { LONG left, top, right, bottom; } RECT, *PRECT, *LPRECT;
typedef const RECT *LPCRECT;
typedef struct tagSIZE { LONG cx, cy; } SIZE, *PSIZE, *LPSIZE;
typedef struct _GUID { unsigned long Data1; unsigned short Data2, Data3; unsigned char Data4[8]; } GUID, IID, CLSID;
#ifdef __cplusplus
typedef const GUID &REFGUID;
typedef const IID &REFIID;
#else
typedef const GUID *REFGUID;
typedef const IID *REFIID;
#endif
typedef struct _FILETIME { DWORD dwLowDateTime, dwHighDateTime; } FILETIME, *PFILETIME, *LPFILETIME;
typedef struct _SYSTEMTIME {
    WORD wYear, wMonth, wDayOfWeek, wDay, wHour, wMinute, wSecond, wMilliseconds;
} SYSTEMTIME, *PSYSTEMTIME, *LPSYSTEMTIME;

/* ---- synchronization (winbase.h); layout of the 32-bit SDK, 24 bytes */
typedef struct _RTL_CRITICAL_SECTION_DEBUG RTL_CRITICAL_SECTION_DEBUG, *PRTL_CRITICAL_SECTION_DEBUG;
typedef struct _RTL_CRITICAL_SECTION {
    PRTL_CRITICAL_SECTION_DEBUG DebugInfo;
    LONG LockCount;
    LONG RecursionCount;
    HANDLE OwningThread;
    HANDLE LockSemaphore;
    ULONG_PTR SpinCount;
} RTL_CRITICAL_SECTION, *PRTL_CRITICAL_SECTION, CRITICAL_SECTION, *PCRITICAL_SECTION, *LPCRITICAL_SECTION;

void InitializeCriticalSection(LPCRITICAL_SECTION);
BOOL InitializeCriticalSectionAndSpinCount(LPCRITICAL_SECTION, DWORD);
void EnterCriticalSection(LPCRITICAL_SECTION);
BOOL TryEnterCriticalSection(LPCRITICAL_SECTION);
void LeaveCriticalSection(LPCRITICAL_SECTION);
void DeleteCriticalSection(LPCRITICAL_SECTION);
/* Interlocked*: C linkage as in the SDK (the platform layer defines them); prepare.py INTL1 drops the decomp's own
 * C++ inline fallbacks (include/layout_support.h, non-MSVC branch). */
/* They are inline here (S12-ordering, docs/ORDERING.md "Interlocked"): x86 lock semantics, i.e. the SEQ_CST RMW plus
 * a trailing dmb ish, with misaligned operands (a split lock on x86, a fault for ldaxr) handed to sync.cpp. gnu_inline:
 * no copy is ever emitted, so an address taken (or a call the compiler does not inline) is sync.cpp's out-of-line
 * definition with the same semantics. The fnvnx_order plugin lowers refcount sites to relaxed / release before this
 * body is inlined. sync.cpp itself defines FNVNX_W32_IMPL and sees plain prototypes. */
LONG fnvnx_interlocked_slow(int op, volatile LONG *p, LONG a, LONG b);  /* op 0 inc, 1 dec, 2 xchg, 3 xadd, 4 cmpxchg */
#if defined(FNVNX_W32_IMPL) || !defined(__GNUC__)
LONG InterlockedIncrement(volatile LONG *);
LONG InterlockedDecrement(volatile LONG *);
LONG InterlockedExchange(volatile LONG *, LONG);
LONG InterlockedExchangeAdd(volatile LONG *, LONG);
LONG InterlockedCompareExchange(volatile LONG *, LONG, LONG);
PVOID InterlockedCompareExchangePointer(PVOID volatile *, PVOID, PVOID);
PVOID InterlockedExchangePointer(PVOID volatile *, PVOID);
#else
#define FNVNX_ILK inline __attribute__((gnu_inline, always_inline))
#define FNVNX_ILK_MISALIGNED(p) __builtin_expect(((uintptr_t)(p) & (sizeof(*(p)) - 1)) != 0, 0)
#define FNVNX_ILK_FULL(expr) ({ __typeof__(expr) r_ = (expr); __atomic_thread_fence(__ATOMIC_SEQ_CST); r_; })
FNVNX_ILK LONG InterlockedIncrement(volatile LONG *p)
{
    if (FNVNX_ILK_MISALIGNED(p)) return fnvnx_interlocked_slow(0, p, 0, 0);
    return FNVNX_ILK_FULL(__atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST));
}
FNVNX_ILK LONG InterlockedDecrement(volatile LONG *p)
{
    if (FNVNX_ILK_MISALIGNED(p)) return fnvnx_interlocked_slow(1, p, 0, 0);
    return FNVNX_ILK_FULL(__atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST));
}
FNVNX_ILK LONG InterlockedExchange(volatile LONG *p, LONG v)
{
    if (FNVNX_ILK_MISALIGNED(p)) return fnvnx_interlocked_slow(2, p, v, 0);
    return FNVNX_ILK_FULL(__atomic_exchange_n(p, v, __ATOMIC_SEQ_CST));
}
FNVNX_ILK LONG InterlockedExchangeAdd(volatile LONG *p, LONG v)
{
    if (FNVNX_ILK_MISALIGNED(p)) return fnvnx_interlocked_slow(3, p, v, 0);
    return FNVNX_ILK_FULL(__atomic_fetch_add(p, v, __ATOMIC_SEQ_CST));
}
FNVNX_ILK LONG InterlockedCompareExchange(volatile LONG *p, LONG xchg, LONG cmp)
{
    if (FNVNX_ILK_MISALIGNED(p)) return fnvnx_interlocked_slow(4, p, xchg, cmp);
    __atomic_compare_exchange_n(p, &cmp, xchg, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return cmp;
}
FNVNX_ILK PVOID InterlockedCompareExchangePointer(PVOID volatile *p, PVOID xchg, PVOID cmp)
{
    if (FNVNX_ILK_MISALIGNED(p))
        return (PVOID)(uintptr_t)fnvnx_interlocked_slow(4, (volatile LONG *)p, (LONG)(intptr_t)xchg, (LONG)(intptr_t)cmp);
    __atomic_compare_exchange_n(p, &cmp, xchg, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return cmp;
}
FNVNX_ILK PVOID InterlockedExchangePointer(PVOID volatile *p, PVOID v)
{
    if (FNVNX_ILK_MISALIGNED(p))
        return (PVOID)(uintptr_t)fnvnx_interlocked_slow(2, (volatile LONG *)p, (LONG)(intptr_t)v, 0);
    return FNVNX_ILK_FULL(__atomic_exchange_n(p, v, __ATOMIC_SEQ_CST));
}
#undef FNVNX_ILK_FULL
/* Refcount sites (docs/ORDERING.md "Refcounts"): the fnvnx_order plugin redirects an InterlockedIncrement whose
 * result is discarded, and an InterlockedDecrement whose result is discarded or only tested against 0, to these when
 * the operand is a 32-bit refcount field by name (m_uiRefCount, refs, ...). What MSVC's own ARM64 STL does for
 * shared_ptr: the increment of a reference one already holds needs no order; the decrement releases this thread's
 * uses of the object, and the thread that reaches 0 acquires everyone's before it deletes. */
#define FNVNX_RC_HELPER static inline __attribute__((always_inline))   /* a macro: prepare.py's name scan reads lines */
FNVNX_RC_HELPER LONG fnvnx_refcount_inc(volatile LONG *p)
{
    if (FNVNX_ILK_MISALIGNED(p)) return fnvnx_interlocked_slow(0, p, 0, 0);
    return __atomic_add_fetch(p, 1, __ATOMIC_RELAXED);
}
FNVNX_RC_HELPER LONG fnvnx_refcount_dec(volatile LONG *p)
{
    if (FNVNX_ILK_MISALIGNED(p)) return fnvnx_interlocked_slow(1, p, 0, 0);
    LONG r = __atomic_sub_fetch(p, 1, __ATOMIC_RELEASE);
    if (r == 0) __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return r;
}
#endif
HANDLE CreateEventA(void *, BOOL, BOOL, LPCSTR);
HANDLE CreateMutexA(void *, BOOL, LPCSTR);
HANDLE CreateSemaphoreA(void *, LONG, LONG, LPCSTR);
BOOL SetEvent(HANDLE);
BOOL ResetEvent(HANDLE);
BOOL ReleaseMutex(HANDLE);
BOOL ReleaseSemaphore(HANDLE, LONG, LPLONG);
DWORD WaitForSingleObject(HANDLE, DWORD);
DWORD WaitForMultipleObjects(DWORD, const HANDLE *, BOOL, DWORD);
BOOL CloseHandle(HANDLE);
#define WAIT_OBJECT_0 0x00000000L
#define WAIT_TIMEOUT 0x00000102L
#define WAIT_FAILED 0xFFFFFFFF

/* ---- threads, TLS, time */
FNVNX_CALLBACK_T(LPTHREAD_START_ROUTINE, DWORD, (LPVOID));
HANDLE CreateThread(void *, SIZE_T, LPTHREAD_START_ROUTINE, LPVOID, DWORD, LPDWORD);
DWORD ResumeThread(HANDLE);
DWORD SuspendThread(HANDLE);
BOOL SetThreadPriority(HANDLE, int);
DWORD_PTR SetThreadAffinityMask(HANDLE, DWORD_PTR);
DWORD SetThreadIdealProcessor(HANDLE, DWORD);
HANDLE GetCurrentThread(void);
DWORD GetCurrentThreadId(void);
HANDLE GetCurrentProcess(void);
DWORD GetCurrentProcessId(void);
void Sleep(DWORD);
DWORD SleepEx(DWORD, BOOL);
BOOL SwitchToThread(void);
void ExitProcess(UINT);
DWORD TlsAlloc(void);
LPVOID TlsGetValue(DWORD);
BOOL TlsSetValue(DWORD, LPVOID);
BOOL TlsFree(DWORD);
typedef LPVOID (*PFN_TlsGetValue)(DWORD);
DWORD GetTickCount(void);
BOOL QueryPerformanceCounter(LARGE_INTEGER *);
BOOL QueryPerformanceFrequency(LARGE_INTEGER *);
void GetLocalTime(LPSYSTEMTIME);
void GetSystemTime(LPSYSTEMTIME);
void GetSystemTimeAsFileTime(LPFILETIME);
BOOL FileTimeToSystemTime(const FILETIME *, LPSYSTEMTIME);
BOOL SystemTimeToFileTime(const SYSTEMTIME *, LPFILETIME);
BOOL FileTimeToLocalFileTime(const FILETIME *, LPFILETIME);
LONG CompareFileTime(const FILETIME *, const FILETIME *);
DWORD GetLastError(void);
void SetLastError(DWORD);
void RaiseException(DWORD, DWORD, DWORD, const ULONG_PTR *);

typedef struct _SYSTEM_INFO {
    union { DWORD dwOemId; struct { WORD wProcessorArchitecture; WORD wReserved; }; };
    DWORD dwPageSize;
    LPVOID lpMinimumApplicationAddress;
    LPVOID lpMaximumApplicationAddress;
    DWORD_PTR dwActiveProcessorMask;
    DWORD dwNumberOfProcessors;
    DWORD dwProcessorType;
    DWORD dwAllocationGranularity;
    WORD wProcessorLevel;
    WORD wProcessorRevision;
} SYSTEM_INFO, *LPSYSTEM_INFO;
void GetSystemInfo(LPSYSTEM_INFO);

/* ---- memory */
LPVOID VirtualAlloc(LPVOID, SIZE_T, DWORD, DWORD);
BOOL VirtualFree(LPVOID, SIZE_T, DWORD);
HANDLE GetProcessHeap(void);
LPVOID HeapAlloc(HANDLE, DWORD, SIZE_T);
BOOL HeapFree(HANDLE, DWORD, LPVOID);
HANDLE LocalFree(HANDLE);
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define MEM_DECOMMIT 0x4000
#define MEM_RELEASE 0x8000
#define PAGE_NOACCESS 0x01
#define PAGE_READONLY 0x02
#define PAGE_READWRITE 0x04

/* ---- files */
typedef struct _WIN32_FIND_DATAA {
    DWORD dwFileAttributes;
    FILETIME ftCreationTime, ftLastAccessTime, ftLastWriteTime;
    DWORD nFileSizeHigh, nFileSizeLow, dwReserved0, dwReserved1;
    CHAR cFileName[MAX_PATH];
    CHAR cAlternateFileName[14];
} WIN32_FIND_DATAA, *PWIN32_FIND_DATAA, *LPWIN32_FIND_DATAA, WIN32_FIND_DATA, *LPWIN32_FIND_DATA;
typedef struct _OVERLAPPED {
    ULONG_PTR Internal, InternalHigh;
    union { struct { DWORD Offset, OffsetHigh; }; PVOID Pointer; };
    HANDLE hEvent;
} OVERLAPPED, *LPOVERLAPPED;
HANDLE CreateFileA(LPCSTR, DWORD, DWORD, void *, DWORD, DWORD, HANDLE);
BOOL ReadFile(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
BOOL WriteFile(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
DWORD SetFilePointer(HANDLE, LONG, PLONG, DWORD);
DWORD GetFileSize(HANDLE, LPDWORD);
BOOL FlushFileBuffers(HANDLE);
BOOL DeleteFileA(LPCSTR);
BOOL CreateDirectoryA(LPCSTR, void *);
BOOL MoveFileA(LPCSTR, LPCSTR);
BOOL CopyFileA(LPCSTR, LPCSTR, BOOL);
DWORD GetFileAttributesA(LPCSTR);
HANDLE FindFirstFileA(LPCSTR, LPWIN32_FIND_DATAA);
BOOL FindNextFileA(HANDLE, LPWIN32_FIND_DATAA);
BOOL FindClose(HANDLE);
DWORD GetCurrentDirectoryA(DWORD, LPSTR);
DWORD GetModuleFileNameA(HMODULE, LPSTR, DWORD);
HMODULE GetModuleHandleA(LPCSTR);
HMODULE LoadLibraryA(LPCSTR);
FARPROC GetProcAddress(HMODULE, LPCSTR);
BOOL FreeLibrary(HMODULE);
UINT GetPrivateProfileIntA(LPCSTR, LPCSTR, INT, LPCSTR);
DWORD GetPrivateProfileStringA(LPCSTR, LPCSTR, LPCSTR, LPSTR, DWORD, LPCSTR);
BOOL WritePrivateProfileStringA(LPCSTR, LPCSTR, LPCSTR, LPCSTR);
void OutputDebugStringA(LPCSTR);
#define GENERIC_READ 0x80000000L
#define GENERIC_WRITE 0x40000000L
#define FILE_SHARE_READ 0x00000001
#define FILE_SHARE_WRITE 0x00000002
#define CREATE_NEW 1
#define CREATE_ALWAYS 2
#define OPEN_EXISTING 3
#define OPEN_ALWAYS 4
#define FILE_ATTRIBUTE_READONLY 0x00000001
#define FILE_ATTRIBUTE_DIRECTORY 0x00000010
#define FILE_ATTRIBUTE_NORMAL 0x00000080
#define INVALID_FILE_ATTRIBUTES ((DWORD)-1)
#define FILE_BEGIN 0
#define FILE_CURRENT 1
#define FILE_END 2
#define INVALID_SET_FILE_POINTER ((DWORD)-1)

/* ---- windowing (only what the engine's bootstrap touches) */
FNVNX_CALLBACK_T(WNDPROC, LRESULT, (HWND, UINT, WPARAM, LPARAM));
typedef struct tagMSG { HWND hwnd; UINT message; WPARAM wParam; LPARAM lParam; DWORD time; POINT pt; } MSG, *LPMSG;
typedef struct tagWNDCLASSA {
    UINT style; WNDPROC lpfnWndProc; int cbClsExtra, cbWndExtra; HINSTANCE hInstance; HICON hIcon;
    HCURSOR hCursor; HBRUSH hbrBackground; LPCSTR lpszMenuName, lpszClassName;
} WNDCLASSA, WNDCLASS, *LPWNDCLASSA;
int MessageBoxA(HWND, LPCSTR, LPCSTR, UINT);
LRESULT SendMessageA(HWND, UINT, WPARAM, LPARAM);
BOOL PostMessageA(HWND, UINT, WPARAM, LPARAM);
BOOL PeekMessageA(LPMSG, HWND, UINT, UINT, UINT);
BOOL TranslateMessage(const MSG *);
LRESULT DispatchMessageA(const MSG *);
LRESULT DefWindowProcA(HWND, UINT, WPARAM, LPARAM);
ATOM RegisterClassA(const WNDCLASSA *);
HWND CreateWindowExA(DWORD, LPCSTR, LPCSTR, DWORD, int, int, int, int, HWND, HMENU, HINSTANCE, LPVOID);
BOOL DestroyWindow(HWND);
BOOL ShowWindow(HWND, int);
BOOL UpdateWindow(HWND);
HWND FindWindowA(LPCSTR, LPCSTR);
BOOL SetForegroundWindow(HWND);
LONG GetWindowLongA(HWND, int);
LONG SetWindowLongA(HWND, int, LONG);
BOOL AdjustWindowRect(LPRECT, DWORD, BOOL);
BOOL AdjustWindowRectEx(LPRECT, DWORD, BOOL, DWORD);
BOOL GetClientRect(HWND, LPRECT);
BOOL GetWindowRect(HWND, LPRECT);
BOOL SetWindowPos(HWND, HWND, int, int, int, int, UINT);
int GetSystemMetrics(int);
HICON LoadIconA(HINSTANCE, LPCSTR);
HCURSOR LoadCursorA(HINSTANCE, LPCSTR);
HGDIOBJ GetStockObject(int);
int ShowCursor(BOOL);
BOOL SetCursorPos(int, int);
BOOL GetCursorPos(LPPOINT);
short GetAsyncKeyState(int);
short GetKeyState(int);
LPWSTR GetCommandLineW(void);
LPSTR GetCommandLineA(void);
LPWSTR *CommandLineToArgvW(LPCWSTR, int *);

/* ---- compiler intrinsics MSVC declares in <intrin.h> / <stdlib.h> */
unsigned long long __rdtsc(void);
unsigned long __readfsdword(unsigned long);
/* _ReadWriteBarrier: declared (as a function, the decomp re-declares and qualifies it) in fnvnx_compat.h */
#define _byteswap_ulong(x) __builtin_bswap32(x)
#define _byteswap_ushort(x) __builtin_bswap16(x)
#define _byteswap_uint64(x) __builtin_bswap64(x)
#ifndef FNVNX_COMPAT_H   /* game units: fnvnx_compat.h defines them with C linkage (the decomp re-declares them) */
static inline unsigned int _rotl(unsigned int v, int s) { s &= 31; return s ? (v << s) | (v >> (32 - s)) : v; }
static inline unsigned int _rotr(unsigned int v, int s) { s &= 31; return s ? (v >> s) | (v << (32 - s)) : v; }
#endif

/* ---- C++ exception plumbing referenced by name in a few units (compiler-generated on MSVC) */
typedef struct _ThrowInfo _ThrowInfo;
void _CxxThrowException(void *, _ThrowInfo *);

/* ==== merged from platform/include/fnvnx_win32_ext.h (S4): imports the runtime implements */
#ifndef WAIT_ABANDONED
#define WAIT_ABANDONED 0x00000080L
#endif
#ifndef INVALID_FILE_SIZE
#define INVALID_FILE_SIZE ((DWORD)0xFFFFFFFF)
#endif
#ifndef STILL_ACTIVE
#define STILL_ACTIVE 259
#endif
#ifndef CREATE_SUSPENDED
#define CREATE_SUSPENDED 0x00000004
#endif
#ifndef TRUNCATE_EXISTING
#define TRUNCATE_EXISTING 5
#endif
#ifndef FILE_SHARE_DELETE
#define FILE_SHARE_DELETE 0x00000004
#endif
#ifndef FILE_ATTRIBUTE_HIDDEN
#define FILE_ATTRIBUTE_HIDDEN 0x00000002
#define FILE_ATTRIBUTE_SYSTEM 0x00000004
#define FILE_ATTRIBUTE_ARCHIVE 0x00000020
#endif
#ifndef FILE_FLAG_OVERLAPPED
#define FILE_FLAG_WRITE_THROUGH 0x80000000
#define FILE_FLAG_OVERLAPPED 0x40000000
#define FILE_FLAG_NO_BUFFERING 0x20000000
#define FILE_FLAG_RANDOM_ACCESS 0x10000000
#define FILE_FLAG_SEQUENTIAL_SCAN 0x08000000
#define FILE_FLAG_BACKUP_SEMANTICS 0x02000000
#endif
#ifndef MEM_FREE
#define MEM_FREE 0x10000
#define MEM_PRIVATE 0x20000
#define MEM_TOP_DOWN 0x100000
#endif
#ifndef PAGE_EXECUTE_READWRITE
#define PAGE_WRITECOPY 0x08
#define PAGE_EXECUTE 0x10
#define PAGE_EXECUTE_READ 0x20
#define PAGE_EXECUTE_READWRITE 0x40
#define PAGE_GUARD 0x100
#define PAGE_NOCACHE 0x200
#endif
#ifndef HEAP_ZERO_MEMORY
#define HEAP_NO_SERIALIZE 0x00000001
#define HEAP_GENERATE_EXCEPTIONS 0x00000004
#define HEAP_ZERO_MEMORY 0x00000008
#define HEAP_REALLOC_IN_PLACE_ONLY 0x00000010
#endif
#ifndef THREAD_PRIORITY_NORMAL
#define THREAD_PRIORITY_IDLE (-15)
#define THREAD_PRIORITY_LOWEST (-2)
#define THREAD_PRIORITY_BELOW_NORMAL (-1)
#define THREAD_PRIORITY_NORMAL 0
#define THREAD_PRIORITY_ABOVE_NORMAL 1
#define THREAD_PRIORITY_HIGHEST 2
#define THREAD_PRIORITY_TIME_CRITICAL 15
#define THREAD_PRIORITY_ERROR_RETURN 0x7fffffff
#endif
#ifndef MB_OK
#define MB_OK 0x0L
#define MB_OKCANCEL 0x1L
#define MB_ABORTRETRYIGNORE 0x2L
#define MB_YESNOCANCEL 0x3L
#define MB_YESNO 0x4L
#define MB_RETRYCANCEL 0x5L
#define MB_DEFBUTTON2 0x100L
#define MB_DEFBUTTON3 0x200L
#define MB_ICONHAND 0x10L
#define MB_ICONQUESTION 0x20L
#define MB_ICONEXCLAMATION 0x30L
#define MB_ICONASTERISK 0x40L
#define MB_ICONERROR MB_ICONHAND
#define MB_ICONWARNING MB_ICONEXCLAMATION
#define MB_ICONINFORMATION MB_ICONASTERISK
#define MB_SYSTEMMODAL 0x1000L
#define MB_TASKMODAL 0x2000L
#define MB_SETFOREGROUND 0x10000L
#define MB_TOPMOST 0x40000L
#define IDOK 1
#define IDCANCEL 2
#define IDABORT 3
#define IDRETRY 4
#define IDIGNORE 5
#define IDYES 6
#define IDNO 7
#endif
#ifndef PM_REMOVE
#define PM_NOREMOVE 0x0000
#define PM_REMOVE 0x0001
#endif
#ifndef GWL_WNDPROC
#define GWL_WNDPROC (-4)
#define GWL_HINSTANCE (-6)
#define GWL_ID (-12)
#define GWL_STYLE (-16)
#define GWL_EXSTYLE (-20)
#define GWL_USERDATA (-21)
#endif
#ifndef SM_CXSCREEN
#define SM_CXSCREEN 0
#define SM_CYSCREEN 1
#endif
#ifndef WM_QUIT
#define WM_NULL 0x0000
#define WM_DESTROY 0x0002
#define WM_ACTIVATE 0x0006
#define WM_SETFOCUS 0x0007
#define WM_CLOSE 0x0010
#define WM_QUIT 0x0012
#define WM_ACTIVATEAPP 0x001C
#define WM_KEYDOWN 0x0100
#define WM_KEYUP 0x0101
#define WM_CHAR 0x0102
#define WM_MOUSEMOVE 0x0200
#endif
#ifndef FORMAT_MESSAGE_ALLOCATE_BUFFER
#define FORMAT_MESSAGE_ALLOCATE_BUFFER 0x00000100
#define FORMAT_MESSAGE_IGNORE_INSERTS 0x00000200
#define FORMAT_MESSAGE_FROM_STRING 0x00000400
#define FORMAT_MESSAGE_FROM_SYSTEM 0x00001000
#endif
#ifndef MS_VC_EXCEPTION
#define MS_VC_EXCEPTION 0x406D1388 /* SetThreadName through RaiseException */
#endif
#ifndef MAXIMUM_WAIT_OBJECTS
#define MAXIMUM_WAIT_OBJECTS 64
#endif

/* ---- kernel32 imports not in the shim */
typedef struct _MEMORYSTATUS {
    DWORD dwLength, dwMemoryLoad;
    SIZE_T dwTotalPhys, dwAvailPhys, dwTotalPageFile, dwAvailPageFile, dwTotalVirtual, dwAvailVirtual;
} MEMORYSTATUS, *LPMEMORYSTATUS;
typedef struct _MEMORYSTATUSEX {
    DWORD dwLength, dwMemoryLoad;
    ULONGLONG ullTotalPhys, ullAvailPhys, ullTotalPageFile, ullAvailPageFile, ullTotalVirtual,
        ullAvailVirtual, ullAvailExtendedVirtual;
} MEMORYSTATUSEX, *LPMEMORYSTATUSEX;
typedef struct _TIME_ZONE_INFORMATION TIME_ZONE_INFORMATION, *LPTIME_ZONE_INFORMATION;
typedef struct _MEMORY_BASIC_INFORMATION {
    PVOID BaseAddress, AllocationBase;
    DWORD AllocationProtect;
    SIZE_T RegionSize;
    DWORD State, Protect, Type;
} MEMORY_BASIC_INFORMATION, *PMEMORY_BASIC_INFORMATION;

BOOL GetExitCodeThread(HANDLE, LPDWORD);
void ExitThread(DWORD) __attribute__((noreturn));
BOOL SetThreadPriorityBoost(HANDLE, BOOL);
int GetThreadPriority(HANDLE);
BOOL SetFilePointerEx(HANDLE, LARGE_INTEGER, PLARGE_INTEGER, DWORD);
BOOL GetFileSizeEx(HANDLE, PLARGE_INTEGER);
BOOL SetEndOfFile(HANDLE);
BOOL GetFileTime(HANDLE, LPFILETIME, LPFILETIME, LPFILETIME);
BOOL SetCurrentDirectoryA(LPCSTR);
BOOL RemoveDirectoryA(LPCSTR);
DWORD GetFullPathNameA(LPCSTR, DWORD, LPSTR, LPSTR *);
void GlobalMemoryStatus(LPMEMORYSTATUS);
BOOL GlobalMemoryStatusEx(LPMEMORYSTATUSEX);
SIZE_T VirtualQuery(LPCVOID, PMEMORY_BASIC_INFORMATION, SIZE_T);
LPVOID HeapReAlloc(HANDLE, DWORD, LPVOID, SIZE_T);
SIZE_T HeapSize(HANDLE, DWORD, LPCVOID);
HANDLE HeapCreate(DWORD, SIZE_T, SIZE_T);
BOOL HeapDestroy(HANDLE);
HANDLE LocalAlloc(UINT, SIZE_T);
BOOL SystemTimeToTzSpecificLocalTime(LPTIME_ZONE_INFORMATION, const SYSTEMTIME *, LPSYSTEMTIME);
BOOL IsDebuggerPresent(void);
void DebugBreak(void);
void RtlUnwind(PVOID, PVOID, PVOID, PVOID);
BOOL IsBadCodePtr(FARPROC);
BOOL IsBadReadPtr(const void *, UINT_PTR);
BOOL IsBadWritePtr(LPVOID, UINT_PTR);
BOOL GetComputerNameA(LPSTR, LPDWORD);
DWORD FormatMessageA(DWORD, LPCVOID, DWORD, DWORD, LPSTR, DWORD, void *);
LPSTR lstrcpyA(LPSTR, LPCSTR);
LPSTR lstrcatA(LPSTR, LPCSTR);
int lstrlenA(LPCSTR);
int lstrcmpiA(LPCSTR, LPCSTR);
#define lstrcpy lstrcpyA
DWORD GetEnvironmentVariableA(LPCSTR, LPSTR, DWORD);
DWORD GetPrivateProfileSectionA(LPCSTR, LPSTR, DWORD, LPCSTR);
BOOL WritePrivateProfileSectionA(LPCSTR, LPCSTR, LPCSTR);
DWORD GetPrivateProfileSectionNamesA(LPSTR, DWORD, LPCSTR);

/* ---- winmm */
DWORD timeGetTime(void);
UINT timeBeginPeriod(UINT);
UINT timeEndPeriod(UINT);

/* ---- user32 / gdi32 imports not in the shim */
typedef struct HHOOK__ *HHOOK;
FNVNX_CALLBACK_T(HOOKPROC, LRESULT, (int, WPARAM, LPARAM));
FNVNX_CALLBACK_T(WNDENUMPROC, BOOL, (HWND, LPARAM));
HWND GetWindow(HWND, UINT);
UINT GetDoubleClickTime(void);
BOOL SwapMouseButton(BOOL);
HHOOK SetWindowsHookExA(int, HOOKPROC, HINSTANCE, DWORD);
BOOL UnhookWindowsHookEx(HHOOK);
LRESULT CallNextHookEx(HHOOK, int, WPARAM, LPARAM);
/* SendInput's records (winuser.h; 28 bytes, as on x86) */
typedef struct tagMOUSEINPUT { LONG dx, dy; DWORD mouseData, dwFlags, time; ULONG_PTR dwExtraInfo; } MOUSEINPUT, *PMOUSEINPUT,
    *LPMOUSEINPUT;
typedef struct tagKEYBDINPUT { WORD wVk, wScan; DWORD dwFlags, time; ULONG_PTR dwExtraInfo; } KEYBDINPUT, *PKEYBDINPUT,
    *LPKEYBDINPUT;
typedef struct tagHARDWAREINPUT { DWORD uMsg; WORD wParamL, wParamH; } HARDWAREINPUT, *PHARDWAREINPUT, *LPHARDWAREINPUT;
typedef struct tagINPUT {
    DWORD type;
    union { MOUSEINPUT mi; KEYBDINPUT ki; HARDWAREINPUT hi; };
} INPUT, *PINPUT, *LPINPUT;
#define INPUT_MOUSE 0
#define INPUT_KEYBOARD 1
#define INPUT_HARDWARE 2
#define MOUSEEVENTF_MOVE 0x0001
#define MOUSEEVENTF_LEFTDOWN 0x0002
#define MOUSEEVENTF_LEFTUP 0x0004
#define MOUSEEVENTF_RIGHTDOWN 0x0008
#define MOUSEEVENTF_RIGHTUP 0x0010
#define MOUSEEVENTF_MIDDLEDOWN 0x0020
#define MOUSEEVENTF_MIDDLEUP 0x0040
#define MOUSEEVENTF_WHEEL 0x0800
#define MOUSEEVENTF_ABSOLUTE 0x8000
#define KEYEVENTF_EXTENDEDKEY 0x0001
#define KEYEVENTF_KEYUP 0x0002
UINT SendInput(UINT, void *, int);
BOOL SetWindowTextA(HWND, LPCSTR);
BOOL EnumChildWindows(HWND, WNDENUMPROC, LPARAM);
HWND GetActiveWindow(void);
HWND GetForegroundWindow(void);
HWND SetFocus(HWND);
HWND GetFocus(void);
BOOL DeleteObject(HGDIOBJ);
BOOL ClientToScreen(HWND, LPPOINT);
BOOL ScreenToClient(HWND, LPPOINT);
BOOL ClipCursor(const RECT *);
HWND SetCapture(HWND);
BOOL ReleaseCapture(void);
void PostQuitMessage(int);
BOOL IsIconic(HWND);
BOOL IsWindow(HWND);

/* ---- advapi32 (registry; in memory, seeded with the install path) */
typedef LONG LSTATUS;
typedef DWORD REGSAM;
typedef HKEY *PHKEY;
#ifndef HKEY_LOCAL_MACHINE
#define HKEY_CLASSES_ROOT ((HKEY)(ULONG_PTR)((LONG)0x80000000))
#define HKEY_CURRENT_USER ((HKEY)(ULONG_PTR)((LONG)0x80000001))
#define HKEY_LOCAL_MACHINE ((HKEY)(ULONG_PTR)((LONG)0x80000002))
#define HKEY_USERS ((HKEY)(ULONG_PTR)((LONG)0x80000003))
#define REG_NONE 0
#define REG_SZ 1
#define REG_EXPAND_SZ 2
#define REG_BINARY 3
#define REG_DWORD 4
#define REG_CREATED_NEW_KEY 1
#define REG_OPENED_EXISTING_KEY 2
#endif
LSTATUS RegOpenKeyExA(HKEY, LPCSTR, DWORD, REGSAM, PHKEY);
LSTATUS RegCreateKeyExA(HKEY, LPCSTR, DWORD, LPSTR, DWORD, REGSAM, void *, PHKEY, LPDWORD);
LSTATUS RegQueryValueExA(HKEY, LPCSTR, LPDWORD, LPDWORD, LPBYTE, LPDWORD);
LSTATUS RegSetValueExA(HKEY, LPCSTR, DWORD, DWORD, const BYTE *, DWORD);
LSTATUS RegCloseKey(HKEY);

/* ---- shell32 */
HINSTANCE ShellExecuteA(HWND, LPCSTR, LPCSTR, LPCSTR, LPCSTR, INT);
HRESULT SHGetFolderPathA(HWND, int, HANDLE, DWORD, LPSTR);
#ifndef CSIDL_PERSONAL
#define CSIDL_PERSONAL 0x0005
#define CSIDL_APPDATA 0x001a
#define CSIDL_LOCAL_APPDATA 0x001c
#define CSIDL_COMMON_APPDATA 0x0023
#define CSIDL_FLAG_CREATE 0x8000
#endif

/* ---- ole32 */
HRESULT CoInitialize(LPVOID);
HRESULT CoInitializeEx(LPVOID, DWORD);
void CoUninitialize(void);
HRESULT CoCreateInstance(const GUID *, void *, DWORD, const GUID *, LPVOID *);
HRESULT CoCreateGuid(GUID *);
LPVOID CoTaskMemAlloc(SIZE_T);
void CoTaskMemFree(LPVOID);
void CoFreeUnusedLibraries(void);
#ifndef REGDB_E_CLASSNOTREG
#define REGDB_E_CLASSNOTREG ((HRESULT)0x80040154L)
#define E_NOTIMPL ((HRESULT)0x80004001L)
#define E_NOINTERFACE ((HRESULT)0x80004002L)
#define E_INVALIDARG ((HRESULT)0x80070057L)
#endif


/* ---- GDI structures (wingdi.h / winnt.h); struct tags as in the SDK, d3d9.h forward-declares some of them */
typedef struct tagPALETTEENTRY { BYTE peRed, peGreen, peBlue, peFlags; } PALETTEENTRY, *PPALETTEENTRY, *LPPALETTEENTRY;
typedef struct _RGNDATAHEADER { DWORD dwSize, iType, nCount, nRgnSize; RECT rcBound; } RGNDATAHEADER, *PRGNDATAHEADER;
typedef struct _RGNDATA { RGNDATAHEADER rdh; char Buffer[1]; } RGNDATA, *PRGNDATA, *LPRGNDATA;
typedef struct _LUID { DWORD LowPart; LONG HighPart; } LUID, *PLUID;
#pragma pack(push, 2)
typedef struct tagBITMAPFILEHEADER {
    WORD bfType; DWORD bfSize; WORD bfReserved1, bfReserved2; DWORD bfOffBits;
} BITMAPFILEHEADER, *LPBITMAPFILEHEADER, *PBITMAPFILEHEADER;
#pragma pack(pop)
typedef struct tagBITMAPINFOHEADER {
    DWORD biSize; LONG biWidth, biHeight; WORD biPlanes, biBitCount; DWORD biCompression, biSizeImage;
    LONG biXPelsPerMeter, biYPelsPerMeter; DWORD biClrUsed, biClrImportant;
} BITMAPINFOHEADER, *LPBITMAPINFOHEADER, *PBITMAPINFOHEADER;
#define BI_RGB 0L

#ifdef __cplusplus
}
#endif

/* ---- COM base (unknwn.h); d3d9.h keeps its own copy behind FNVNX_IUNKNOWN_DEFINED */
#define MAKE_HRESULT(sev, fac, code) ((HRESULT)(((unsigned long)(sev) << 31) | ((unsigned long)(fac) << 16) | ((unsigned long)(code))))
#define E_NOTIMPL ((HRESULT)0x80004001L)
#define E_NOINTERFACE ((HRESULT)0x80004002L)
#define E_POINTER ((HRESULT)0x80004003L)
#define E_ABORT ((HRESULT)0x80004004L)
#define E_UNEXPECTED ((HRESULT)0x8000FFFFL)
#define E_INVALIDARG ((HRESULT)0x80070057L)
#ifdef __cplusplus
#define FNVNX_IUNKNOWN_DEFINED
#ifndef STDMETHODCALLTYPE   /* fnvnx_compat.h defines it for the game; platform/ builds without it */
#define STDMETHODCALLTYPE
#endif
struct IUnknown
{
    virtual HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppvObject) = 0;
    virtual ULONG STDMETHODCALLTYPE AddRef() = 0;
    virtual ULONG STDMETHODCALLTYPE Release() = 0;
};
typedef IUnknown *LPUNKNOWN;
#endif

#if defined(__cplusplus) && defined(FNVNX_COMPAT_H)   /* game units only: platform/ takes these addresses */
/* The decomp declares the critical-section functions with its own parameter types (int*, char*, local copies of
 * _RTL_CRITICAL_SECTION) and passes those; DECL1 drops those prototypes, these take their place (same function). */
inline void InitializeCriticalSection(void *p) { InitializeCriticalSection((LPCRITICAL_SECTION)p); }
inline BOOL InitializeCriticalSectionAndSpinCount(void *p, DWORD n) { return InitializeCriticalSectionAndSpinCount((LPCRITICAL_SECTION)p, n); }
inline void EnterCriticalSection(void *p) { EnterCriticalSection((LPCRITICAL_SECTION)p); }
inline BOOL TryEnterCriticalSection(void *p) { return TryEnterCriticalSection((LPCRITICAL_SECTION)p); }
inline void LeaveCriticalSection(void *p) { LeaveCriticalSection((LPCRITICAL_SECTION)p); }
inline void DeleteCriticalSection(void *p) { DeleteCriticalSection((LPCRITICAL_SECTION)p); }
inline BOOL QueryPerformanceCounter(long long *p) { return QueryPerformanceCounter((LARGE_INTEGER *)p); }
inline BOOL QueryPerformanceCounter(unsigned long long *p) { return QueryPerformanceCounter((LARGE_INTEGER *)p); }
inline BOOL QueryPerformanceFrequency(long long *p) { return QueryPerformanceFrequency((LARGE_INTEGER *)p); }
inline BOOL QueryPerformanceFrequency(unsigned long long *p) { return QueryPerformanceFrequency((LARGE_INTEGER *)p); }

#if defined(__ILP32__) || defined(_WIN32)   /* the SDK sizes hold for 32-bit targets (host tests run LP64) */
static_assert(sizeof(BITMAPFILEHEADER) == 14 && sizeof(BITMAPINFOHEADER) == 40 && sizeof(LUID) == 8, "SDK layout");
static_assert(sizeof(CRITICAL_SECTION) == 24 && sizeof(LARGE_INTEGER) == 8 && sizeof(SYSTEM_INFO) == 36, "SDK layout");
static_assert(sizeof(INPUT) == 28 && sizeof(MOUSEINPUT) == 24, "SDK layout");
#endif
#endif
#pragma pack(pop)
#endif
