/* fnvnx_win32_ext.h - Win32 declarations the runtime implements beyond shim/include/fnvnx_win32.h.
 *
 * Everything here is imported by FalloutNV.exe (docs/RUNTIME.md, imports table) but not yet declared by the
 * shim (stream S1 owns it). S1 can move these into the shim; the #ifndef guards keep both orders working.
 * Also: the runtime's own hooks (fnvnx_*) used by the loader (S3), input (S7) and render (S6) streams.
 */
#ifndef FNVNX_WIN32_EXT_H
#define FNVNX_WIN32_EXT_H

#include "fnvnx_win32.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- constants missing from the shim */
#ifndef ERROR_SUCCESS
#define ERROR_SUCCESS 0L
#define ERROR_INVALID_FUNCTION 1L
#define ERROR_FILE_NOT_FOUND 2L
#define ERROR_PATH_NOT_FOUND 3L
#define ERROR_ACCESS_DENIED 5L
#define ERROR_INVALID_HANDLE 6L
#define ERROR_NOT_ENOUGH_MEMORY 8L
#define ERROR_OUTOFMEMORY 14L
#define ERROR_NO_MORE_FILES 18L
#define ERROR_SHARING_VIOLATION 32L
#define ERROR_HANDLE_EOF 38L
#define ERROR_NOT_SUPPORTED 50L
#define ERROR_FILE_EXISTS 80L
#define ERROR_INVALID_PARAMETER 87L
#define ERROR_CALL_NOT_IMPLEMENTED 120L
#define ERROR_INSUFFICIENT_BUFFER 122L
#define ERROR_INVALID_NAME 123L
#define ERROR_MOD_NOT_FOUND 126L
#define ERROR_PROC_NOT_FOUND 127L
#define ERROR_DIR_NOT_EMPTY 145L
#define ERROR_ALREADY_EXISTS 183L
#define ERROR_MORE_DATA 234L
#define ERROR_NOT_OWNER 288L
#define ERROR_TOO_MANY_POSTS 298L
#define ERROR_INVALID_ADDRESS 487L
#endif
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
LONG CompareFileTime(const FILETIME *, const FILETIME *);
BOOL FileTimeToSystemTime(const FILETIME *, LPSYSTEMTIME);
BOOL SystemTimeToFileTime(const SYSTEMTIME *, LPFILETIME);
BOOL FileTimeToLocalFileTime(const FILETIME *, LPFILETIME);
BOOL SystemTimeToTzSpecificLocalTime(LPTIME_ZONE_INFORMATION, const SYSTEMTIME *, LPSYSTEMTIME);
BOOL IsDebuggerPresent(void);
void DebugBreak(void);
void RaiseException(DWORD, DWORD, DWORD, const ULONG_PTR *);
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
typedef LRESULT (*HOOKPROC)(int, WPARAM, LPARAM);
typedef BOOL (*WNDENUMPROC)(HWND, LPARAM);
HWND GetWindow(HWND, UINT);
UINT GetDoubleClickTime(void);
BOOL SwapMouseButton(BOOL);
HHOOK SetWindowsHookExA(int, HOOKPROC, HINSTANCE, DWORD);
BOOL UnhookWindowsHookEx(HHOOK);
LRESULT CallNextHookEx(HHOOK, int, WPARAM, LPARAM);
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

/* ---- runtime hooks (not Win32) */
/* Paths (docs/RUNTIME.md "Paths"). Windows prefix (case-insensitive, '\' or '/') -> host directory. */
int fnvnx_path_mount(const char *win_prefix, const char *host_dir);
void fnvnx_path_reset(const char *root);        /* default mounts under root (NULL: os_default_root) */
/* Maps a Windows path to a host path, resolving existing components case-insensitively. Returns 0 or a
 * Win32 error code (ERROR_PATH_NOT_FOUND when a directory component is missing and !for_create...). */
DWORD fnvnx_path_map(const char *win_path, char *host, size_t host_size);
void fnvnx_set_command_line(int argc, char **argv);
/* implicit TLS of the image (.tls section of FalloutNV.exe, from the loader, S3) */
void fnvnx_tls_set_template(const void *init_data, size_t init_size, size_t total_size);
void *fnvnx_tls_block(void);                     /* this thread's block; what fs:[0x2c][0] points at */
void **fnvnx_tls_array(void);                    /* what fs:[0x2c] returns */
/* builtin modules for LoadLibraryA/GetProcAddress (xinput1_3.dll, ... registered by S6/S7) */
typedef FARPROC (*fnvnx_module_resolver)(const char *proc);
void fnvnx_register_module(const char *dll_name, fnvnx_module_resolver resolve);
/* Win32 export table: implementation of a DLL import by name (for IAT slots resolved from the exe) */
void *fnvnx_win32_lookup_export(const char *dll, const char *name);
/* window/input plumbing for S6/S7 */
HWND fnvnx_main_window(void);
void fnvnx_set_display_size(int w, int h);
void fnvnx_post_message(HWND, UINT, WPARAM, LPARAM);
void fnvnx_set_key_state(int vk, int down);
void fnvnx_set_cursor_pos(int x, int y);
/* COM classes (e.g. XAudio2 from S7) for CoCreateInstance */
typedef HRESULT (*fnvnx_com_factory)(const GUID *iid, void **out);
void fnvnx_register_com_class(const GUID *clsid, fnvnx_com_factory f);
/* message boxes: default answer policy is the default button; tests can capture the text */
typedef int (*fnvnx_msgbox_hook)(const char *text, const char *caption, UINT type);
void fnvnx_set_msgbox_hook(fnvnx_msgbox_hook);

#ifdef __cplusplus
}
#endif
#endif
