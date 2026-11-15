/* w32.h - internals shared by the Win32 implementation files (not seen by game code). */
#ifndef FNVNX_W32_H
#define FNVNX_W32_H

#include "../include/fnvnx_win32_ext.h"
#include "../os/os.h"
#include "../include/fnvnx_clock.h"

#include <stdarg.h>
#include <string.h>

#define W32_TLS_SLOTS 1088 /* 64 + 1024, as on Windows */

/* per-thread state, created lazily for any thread that touches the runtime */
struct w32_waiter {
    os_cond_t cond;
};
struct w32_obj;
/* the thread's current blocking wait, for the wait-graph watchdog (waitgraph.cpp); written by the thread itself */
#define W32_WG_MAXOBJ 8
struct w32_wait_rec {
    volatile uint32_t seq;         /* odd while the thread writes the record; bumped per wait */
    int kind;                      /* 0 none, W32_WG_CS, W32_WG_OBJ */
    uint32_t pc;                   /* return address of the Win32 call (a guest code address) */
    uint32_t ms;                   /* the wait's timeout (INFINITE for none) */
    uint64_t t0;                   /* clk_rt_ns() at the start */
    int all, n;                    /* WaitForMultipleObjects' bWaitAll and count (n <= W32_WG_MAXOBJ recorded) */
    void *obj[W32_WG_MAXOBJ];      /* W32_WG_CS: the CRITICAL_SECTION; W32_WG_OBJ: struct w32_obj * */
};
enum { W32_WG_CS = 1, W32_WG_OBJ = 2 };
struct w32_thread {
    DWORD tid;
    DWORD last_error;
    void *tls[64];
    void **tls_ext;                /* 1024 more, allocated on first use */
    void *implicit_tls[1];         /* fs:[0x2c] points here; [0] = image TLS block of this thread */
    int implicit_tls_borrowed;     /* [0] came from fnvnx_image_tls_hook: not ours to free */
    struct w32_obj *self_obj;      /* thread object when created by CreateThread */
    struct w32_waiter waiter;
    char name[32];
    int boost;                     /* the loading-screen thread: CPU boost while it runs (OS_THREAD_BOOST) */
    int wg_slot;                   /* slot + 1 in the watchdog's thread table; 0: not registered */
    uint32_t wg_caller;            /* set by an interposer (hb.c's wraps) around the call: the game's call site */
    uint32_t mem_caller;           /* the memory lane (memtrack.cpp): the allocating stand-in's caller */
    int mem_inside;                /* > 0: inside a stand-in that records its own allocation */
    struct w32_wait_rec wait;
};
struct w32_thread *w32_self(void);
void w32_fls_thread_exit(void);   /* fls.cpp: the calling thread ends: its FLS callbacks run, its cells go */

/* ---- kernel objects: HANDLE is a pointer to one of these */
enum { W32_EVENT = 1, W32_MUTEX, W32_SEMAPHORE, W32_THREAD, W32_FILE, W32_FIND, W32_HEAP, W32_MAPPING, W32_WINSTA, W32_DESKTOP, W32_ADAPTER };
#define W32_MAGIC 0x46564E58u /* "FVNX" */
struct w32_link {                  /* one waiter linked to one object */
    struct w32_link *next, *prev;
    struct w32_thread *t;
};
struct w32_obj {
    uint32_t magic;
    int type;
    int refs;                      /* atomic */
    struct w32_link waiters;       /* list head, guarded by w32_wait_lock */
    void (*destroy)(struct w32_obj *);
    uint64_t clk_t;                /* the clock stamp of the last signal (creation for an object made signaled): a wait that succeeds takes it */
};
struct w32_obj *w32_obj_new(int type, size_t size, void (*destroy)(struct w32_obj *));
/* Handles are indices into a table with a generation count (stale or double-closed handles fail with
 * ERROR_INVALID_HANDLE instead of touching freed memory); values are multiples of 4 below 2^32 and never
 * NULL, INVALID_HANDLE_VALUE or a pseudo handle. */
HANDLE w32_handle_open(struct w32_obj *o);         /* the handle takes over one reference; NULL if full */
struct w32_obj *w32_obj_get(HANDLE h, int type);   /* type 0: any; NULL + ERROR_INVALID_HANDLE if bad */
struct w32_obj *w32_obj_get_ref(HANDLE h, int type); /* as w32_obj_get, plus a reference the caller drops */
BOOL w32_handle_close(HANDLE h, int type);         /* type 0: any */
void w32_obj_ref(struct w32_obj *o);
void w32_obj_unref(struct w32_obj *o);
/* object wait support (sync.cpp): global lock around all waitable state */
extern os_lock_t w32_wait_lock;
void w32_obj_signal_locked(struct w32_obj *o);     /* wake waiters of o; caller holds w32_wait_lock */
void w32_mutexes_abandon_locked(DWORD tid);        /* a thread ended: its mutexes become abandoned */

/* ---- threads (thread.cpp) */
#define W32_PSEUDO_THREAD ((HANDLE)(LONG_PTR)-2)
#define W32_PSEUDO_PROCESS ((HANDLE)(LONG_PTR)-1)
/* the handle of the process heap (memory.cpp g_proc_heap) */
#define PROCESS_HEAP ((HANDLE)(uintptr_t)0x00150000u)
struct w32_thread_obj;
int w32_thread_signaled(struct w32_obj *o);
DWORD w32_thread_obj_tid(struct w32_obj *o);       /* the thread id of a W32_THREAD object */

/* ---- wait-graph watchdog (waitgraph.cpp; off unless fnvnx_waitgraph_start was called) */
extern volatile int w32_wg_on;
void w32_wg_prepare(struct w32_thread *t);        /* before w32_wg_obj_begin, outside w32_wait_lock */
void w32_wg_cs_begin(struct w32_thread *t, LPCRITICAL_SECTION cs, uint32_t pc);
void w32_wg_obj_begin(struct w32_thread *t, struct w32_obj *const *objs, DWORD n, BOOL all, DWORD ms, uint32_t pc);
void w32_wg_end(struct w32_thread *t);
void w32_wg_forget(struct w32_thread *t);          /* the thread's block is about to be freed */
DWORD w32_wg_mutex_owner_locked(struct w32_obj *o); /* sync.cpp: owner tid of a W32_MUTEX, 0 if free */

/* ---- memory lane (memtrack.cpp; off unless fnvnx_mem_start / fnvnx_mem_autostart) */
extern volatile int w32_mem_on;
FARPROC w32_mem_hook_proc(const char *name, FARPROC f);   /* GetProcAddress: Direct3DCreate9 behind the recorder */

/* ---- paths (path.cpp) */
#define W32_PATH_MAX 1024
/* Windows -> host. create_last: the last component may not exist (it keeps the given spelling). */
DWORD w32_map_path(const char *win, char *host, size_t n);
void w32_virtual_cwd(char *out, size_t n);
DWORD w32_full_path(const char *win, char *out, size_t n); /* normalised "x:\\a\\b" */
/* what a name opened by CreateFile is (path.cpp): a file-system name, or a DOS device / device-namespace name */
enum { W32_NC_FILE, W32_NC_NUL, W32_NC_CONSOLE, W32_NC_PORT, W32_NC_PIPE, W32_NC_UNC, W32_NC_NONE };
int w32_name_class(const char *name, char *dev, size_t n);
/* virtual Windows paths of the environment (ENV_TRUTH F1, F2, P5): the install, the exe, the user's folder, the
 * system and Windows directories */
static const char W32_GAME_DIR[] = "C:\\FalloutNV";
static const char W32_EXE_PATH[] = "C:\\FalloutNV\\FalloutNV.exe";
static const char W32_USER_DIR[] = "C:\\Users\\Player";
static const char W32_SYS_DIR[] = "C:\\Windows\\system32";
static const char W32_WIN_DIR[] = "C:\\Windows";
/* errno-style os error -> Win32 error code */
DWORD w32_os_error(int neg);
int w32_wildcard_match(const char *pat, const char *name); /* Windows *, ? matching, case-insensitive */

/* ---- the page map's services (memory.cpp) to IsBad*Ptr (module.cpp) and to file views (filemap.cpp) */
int w32_vm_probe(const void *p, size_t n, int access);   /* access 0 read, 1 write, 8 code; 1: accessible */
#define W32_VIEW_FAIL ((void *)~(uintptr_t)0)   /* osmap's answer "no range will do": the last error is set, the page map stops */
typedef void *(*w32_view_osmap)(void *hint, size_t size, void *ctx);   /* maps the OS range of a view: exactly at hint; NULL: the OS
                                                                          does not have it, the next free fit is tried */
/* enters a view allocation of size bytes (granule placement at want or anywhere; osmap is called with the address
 * the map chose, and again with the next one if the OS refuses); image: MEM_IMAGE, else MEM_MAPPED. NULL: last error */
void *w32_vm_view_place(uintptr_t want, size_t size, w32_view_osmap osmap, void *ctx, DWORD alloc_prot, DWORD page_prot,
                        int committed, int image, int reserve_section);
size_t w32_vm_view_remove(uintptr_t base);   /* the view allocation at base leaves the map; its size, 0: not a view base */
void w32_vm_view_alias(uintptr_t base, size_t size, int file);   /* the view's pages alias a host section object (file: of a file-backed section) */
void w32_vm_view_prot(uintptr_t base, size_t size, DWORD prot);   /* the pages' current protection (map only) */

/* file.cpp: can the bytes of the file behind this handle change while the handle's file object lives? Yes iff the open
 * has write access or allows FILE_SHARE_WRITE (the share table admits no other writer otherwise) */
bool w32_file_writers_possible(HANDLE h);

/* ---- misc */
/* The debug log is typed, not variadic: a caller's pointer reaches only the bounded copy loop s() (a plain read of the
 * string), never a libc formatter or a `...` argument (LLVM treats a pointer in a C vararg as captured and written, which
 * would make every import that logs one unprovable). There is deliberately no printf-style entry point.
 *   W32Log l; l.s("LoadLibraryA(\"").s(name).s("\"): error ").u(err).emit();
 * One message = one local buffer ("[fnvnx] " + the pieces, truncated at 1023 bytes) handed to os_debug_print. u() prints
 * an unsigned decimal, h() "0x" + lowercase hex of an unsigned value, p() the address of a pointer (a pointer-to-integer
 * conversion: the pointer is then a capture, so p() is for pointers that are not import arguments). */
struct W32Log {
    char buf[1024];
    size_t n;
    W32Log();
    W32Log &s(const char *str);
    W32Log &u(unsigned long long v);
    W32Log &h(unsigned long long v);
    W32Log &p(const void *ptr) { return h((unsigned long long)(uintptr_t)ptr); }
    void emit();
};
#define W32_UNUSED(x) ((void)(x))
int w32_stricmp(const char *a, const char *b);
int w32_strnicmp(const char *a, const char *b, size_t n);
/* FILETIME helpers: 100 ns since 1601-01-01 */
ULONGLONG w32_unix_us_to_filetime(int64_t us);
static inline ULONGLONG w32_ft64(const FILETIME *f) { return ((ULONGLONG)f->dwHighDateTime << 32) | f->dwLowDateTime; }
static inline void w32_set_ft(FILETIME *f, ULONGLONG v) { f->dwLowDateTime = (DWORD)(v & 0xffffffffu); f->dwHighDateTime = (DWORD)(v >> 32); }
/* DWORD is 64-bit on LP64 hosts; keep values in the 32-bit Win32 range */
#define W32_DW(x) ((DWORD)((x) & 0xffffffffu))

/* ---- the exe's own CRT imports (crt_kernel32.cpp): only FalloutNV.exe's statically linked MSVC 2008 CRT calls
 * these, so the shim does not declare them; the SDK layouts and constants live here. */
#ifndef ERROR_ENVVAR_NOT_FOUND
#define ERROR_ENVVAR_NOT_FOUND 203L
#endif
#ifndef ERROR_INVALID_FLAGS
#define ERROR_INVALID_FLAGS 1004L
#endif
#ifndef ERROR_NO_UNICODE_TRANSLATION
#define ERROR_NO_UNICODE_TRANSLATION 1113L
#endif
#ifndef CP_ACP
#define CP_ACP 0
#define CP_OEMCP 1
#define CP_MACCP 2
#define CP_THREAD_ACP 3
#define CP_UTF7 65000
#define CP_UTF8 65001
#endif
#ifndef MB_PRECOMPOSED
#define MB_PRECOMPOSED 0x01
#define MB_ERR_INVALID_CHARS 0x08
#define WC_ERR_INVALID_CHARS 0x80
#define WC_NO_BEST_FIT_CHARS 0x400
#endif
#ifndef LCMAP_LOWERCASE
#define LCMAP_LOWERCASE 0x100
#define LCMAP_UPPERCASE 0x200
#define LCMAP_SORTKEY 0x400
#endif
#ifndef NORM_IGNORECASE
#define NORM_IGNORECASE 0x1
#endif
#ifndef CSTR_LESS_THAN
#define CSTR_LESS_THAN 1
#define CSTR_EQUAL 2
#define CSTR_GREATER_THAN 3
#endif
#ifndef CT_CTYPE1
#define CT_CTYPE1 0x1
#define CT_CTYPE2 0x2
#define CT_CTYPE3 0x4
#define C1_UPPER 0x001
#define C1_LOWER 0x002
#define C1_DIGIT 0x004
#define C1_SPACE 0x008
#define C1_PUNCT 0x010
#define C1_CNTRL 0x020
#define C1_BLANK 0x040
#define C1_XDIGIT 0x080
#define C1_ALPHA 0x100
#define C1_DEFINED 0x200
#endif
#ifndef LOCALE_ILANGUAGE
#define LOCALE_ILANGUAGE 0x1
#define LOCALE_SENGLANGUAGE 0x1001
#define LOCALE_SENGCOUNTRY 0x1002
#define LOCALE_IDEFAULTCODEPAGE 0xB
#define LOCALE_IDEFAULTANSICODEPAGE 0x1004
#define LOCALE_SABBREVLANGNAME 0x3
#define LOCALE_SABBREVCTRYNAME 0x7
#define LOCALE_SDECIMAL 0xE
#define LOCALE_SDAYNAME1 0x2A
#define LOCALE_SMONTHNAME1 0x38
#define LOCALE_NOUSEROVERRIDE 0x80000000u
#define LOCALE_USE_CP_ACP 0x40000000u
#define LOCALE_RETURN_NUMBER 0x20000000u
#endif
#ifndef LOCALE_USER_DEFAULT
#define LOCALE_USER_DEFAULT 0x0400
#define LOCALE_SYSTEM_DEFAULT 0x0800
#endif
#ifndef STD_INPUT_HANDLE
#define STD_INPUT_HANDLE ((DWORD)-10)
#define STD_OUTPUT_HANDLE ((DWORD)-11)
#define STD_ERROR_HANDLE ((DWORD)-12)
#endif
#ifndef FILE_TYPE_UNKNOWN
#define FILE_TYPE_UNKNOWN 0
#define FILE_TYPE_DISK 1
#define FILE_TYPE_CHAR 2
#define FILE_TYPE_PIPE 3
#endif
#ifndef DRIVE_UNKNOWN
#define DRIVE_UNKNOWN 0
#define DRIVE_NO_ROOT_DIR 1
#define DRIVE_FIXED 3
#endif
#ifndef TIME_ZONE_ID_UNKNOWN
#define TIME_ZONE_ID_UNKNOWN 0
#endif
#ifndef EXCEPTION_EXECUTE_HANDLER
#define EXCEPTION_EXECUTE_HANDLER 1
#define EXCEPTION_CONTINUE_SEARCH 0
#endif

typedef DWORD LCID;
typedef DWORD LCTYPE;
struct _TIME_ZONE_INFORMATION {   /* completes the shim's forward declaration */
    LONG Bias;
    WCHAR StandardName[32];
    SYSTEMTIME StandardDate;
    LONG StandardBias;
    WCHAR DaylightName[32];
    SYSTEMTIME DaylightDate;
    LONG DaylightBias;
};
typedef struct _cpinfo {
    UINT MaxCharSize;
    BYTE DefaultChar[2];
    BYTE LeadByte[12];
} CPINFO, *LPCPINFO;
typedef struct _STARTUPINFOA {
    DWORD cb;
    LPSTR lpReserved, lpDesktop, lpTitle;
    DWORD dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    WORD wShowWindow, cbReserved2;
    LPBYTE lpReserved2;
    HANDLE hStdInput, hStdOutput, hStdError;
} STARTUPINFOA, *LPSTARTUPINFOA;
struct _EXCEPTION_POINTERS;
FNVNX_CALLBACK_T(LPTOP_LEVEL_EXCEPTION_FILTER, LONG, (struct _EXCEPTION_POINTERS *));
FNVNX_CALLBACK_T(LOCALE_ENUMPROCA, BOOL, (LPSTR));

extern "C" {
UINT GetDriveTypeA(LPCSTR);
int MultiByteToWideChar(UINT, DWORD, LPCSTR, int, LPWSTR, int);
int WideCharToMultiByte(UINT, DWORD, LPCWSTR, int, LPSTR, int, LPCSTR, LPBOOL);
BOOL TerminateProcess(HANDLE, UINT);
LONG UnhandledExceptionFilter(struct _EXCEPTION_POINTERS *);
LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER);
void GetStartupInfoA(LPSTARTUPINFOA);
HMODULE GetModuleHandleW(LPCWSTR);
BOOL DisableThreadLibraryCalls(HMODULE);
void FatalAppExitA(UINT, LPCSTR);
/* an exception raised by platform code itself (thread.cpp): the installed backend, else the unhandled sequence */
void w32_raise(DWORD code, DWORD flags, DWORD nargs, const ULONG_PTR *args);
int LCMapStringA(LCID, DWORD, LPCSTR, int, LPSTR, int);
int LCMapStringW(LCID, DWORD, LPCWSTR, int, LPWSTR, int);
BOOL GetCPInfo(UINT, LPCPINFO);
HANDLE GetStdHandle(DWORD);
BOOL SetStdHandle(DWORD, HANDLE);
UINT GetACP(void);
UINT GetOEMCP(void);
BOOL IsValidCodePage(UINT);
BOOL SetEnvironmentVariableA(LPCSTR, LPCSTR);
char *GetEnvironmentStrings(void);
WCHAR *GetEnvironmentStringsW(void);
BOOL FreeEnvironmentStringsA(char *);
BOOL FreeEnvironmentStringsW(WCHAR *);
int CompareStringA(LCID, DWORD, LPCSTR, int, LPCSTR, int);
int CompareStringW(LCID, DWORD, LPCWSTR, int, LPCWSTR, int);
UINT SetHandleCount(UINT);
DWORD GetFileType(HANDLE);
UINT GetConsoleCP(void);
UINT GetConsoleOutputCP(void);
BOOL GetConsoleMode(HANDLE, LPDWORD);
BOOL WriteConsoleA(HANDLE, const void *, DWORD, LPDWORD, LPVOID);
BOOL WriteConsoleW(HANDLE, const void *, DWORD, LPDWORD, LPVOID);
HANDLE CreateFileW(LPCWSTR, DWORD, DWORD, void *, DWORD, DWORD, HANDLE);
DWORD GetTimeZoneInformation(LPTIME_ZONE_INFORMATION);
int GetLocaleInfoA(LCID, LCTYPE, LPSTR, int);
int GetLocaleInfoW(LCID, LCTYPE, LPWSTR, int);
int GetDateFormatA(LCID, DWORD, const SYSTEMTIME *, LPCSTR, LPSTR, int);
int GetTimeFormatA(LCID, DWORD, const SYSTEMTIME *, LPCSTR, LPSTR, int);
DWORD GetTempPathA(DWORD, LPSTR);
UINT GetTempFileNameA(LPCSTR, LPCSTR, UINT, LPSTR);
UINT GetTempFileNameW(LPCWSTR, LPCWSTR, UINT, LPWSTR);
BOOL DeleteFileW(LPCWSTR);
BOOL MoveFileW(LPCWSTR, LPCWSTR);
void *FindResourceW(HMODULE, LPCWSTR, LPCWSTR);
void *FindResourceExW(HMODULE, LPCWSTR, LPCWSTR, WORD);
BOOL GetStringTypeA(LCID, DWORD, LPCSTR, int, LPWORD);
BOOL GetStringTypeW(DWORD, LPCWSTR, int, LPWORD);
BOOL IsValidLocale(LCID, DWORD);
BOOL EnumSystemLocalesA(LOCALE_ENUMPROCA, DWORD);
LCID GetUserDefaultLCID(void);
BOOL GetUserNameA(LPSTR, LPDWORD);           /* advapi32 */
unsigned char SystemFunction036(void *, unsigned long);   /* advapi32 RtlGenRandom, services.cpp */
DWORD GetClassLongA(HWND, int);              /* user32 */
BOOL GetVersionExA(void *);                  /* sysinfo.cpp */
DWORD GetVersion(void);
UINT GetSystemDirectoryA(LPSTR, UINT);
UINT GetWindowsDirectoryA(LPSTR, UINT);
BOOL IsProcessorFeaturePresent(DWORD);
UINT SetErrorMode(UINT);
BOOL IsDBCSLeadByte(BYTE);
DWORD ExpandEnvironmentStringsA(LPCSTR, LPSTR, DWORD);
FNVNX_CALLBACK_T(PHANDLER_ROUTINE, BOOL, (DWORD));
FNVNX_CALLBACK_T(ENUMRESNAMEPROCA, BOOL, (HMODULE, LPCSTR, LPSTR, LONG_PTR));
FNVNX_CALLBACK_T(TIMERPROC, void, (HWND, UINT, UINT_PTR, DWORD));
BOOL SetConsoleCtrlHandler(PHANDLER_ROUTINE, BOOL);   /* crt_kernel32.cpp */
LSTATUS RegOpenKeyA(HKEY, LPCSTR, PHKEY);            /* advapi32 (registry.cpp) */
}
/* the platform's fixed Windows directories (sysinfo.cpp, ENV_TRUTH P5) */

#endif
