/* fnvnx_win32.h - the subset of the Win32 API surface the decomp uses, as declarations.
 *
 * Types follow the Windows SDK definitions for a 32-bit target (ILP32 keeps their sizes). Implementations
 * live in the platform layer (stream S4). Grown from census misses; keep sorted by area.
 */
#ifndef FNVNX_WIN32_H
#define FNVNX_WIN32_H

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
typedef const GUID &REFGUID;
typedef const IID &REFIID;
typedef struct _FILETIME { DWORD dwLowDateTime, dwHighDateTime; } FILETIME, *PFILETIME, *LPFILETIME;
typedef struct _SYSTEMTIME {
    WORD wYear, wMonth, wDayOfWeek, wDay, wHour, wMinute, wSecond, wMilliseconds;
} SYSTEMTIME, *PSYSTEMTIME, *LPSYSTEMTIME;

/* ---- synchronization (winbase.h); layout of the 32-bit SDK, 24 bytes */
typedef struct _RTL_CRITICAL_SECTION_DEBUG *PRTL_CRITICAL_SECTION_DEBUG;
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
LONG InterlockedIncrement(volatile LONG *);
LONG InterlockedDecrement(volatile LONG *);
LONG InterlockedExchange(volatile LONG *, LONG);
LONG InterlockedExchangeAdd(volatile LONG *, LONG);
LONG InterlockedCompareExchange(volatile LONG *, LONG, LONG);
PVOID InterlockedCompareExchangePointer(PVOID volatile *, PVOID, PVOID);
PVOID InterlockedExchangePointer(PVOID volatile *, PVOID);
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
typedef DWORD (*LPTHREAD_START_ROUTINE)(LPVOID);
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
DWORD GetLastError(void);
void SetLastError(DWORD);

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
typedef LRESULT (*WNDPROC)(HWND, UINT, WPARAM, LPARAM);
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
#define _ReadWriteBarrier() __asm__ __volatile__("" ::: "memory")
#define _byteswap_ulong(x) __builtin_bswap32(x)
#define _byteswap_ushort(x) __builtin_bswap16(x)
#define _byteswap_uint64(x) __builtin_bswap64(x)
static inline unsigned int _rotl(unsigned int v, int s) { s &= 31; return s ? (v << s) | (v >> (32 - s)) : v; }
static inline unsigned int _rotr(unsigned int v, int s) { s &= 31; return s ? (v >> s) | (v << (32 - s)) : v; }

/* ---- C++ exception plumbing referenced by name in a few units (compiler-generated on MSVC) */
typedef struct _ThrowInfo _ThrowInfo;
void _CxxThrowException(void *, _ThrowInfo *);

#ifdef __cplusplus
}
#endif
#endif
