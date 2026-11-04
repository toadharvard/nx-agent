/* w32.h - internals shared by the Win32 implementation files (not seen by game code). */
#ifndef FNVNX_W32_H
#define FNVNX_W32_H

#include "fnvnx_win32_ext.h"
#include "../os/os.h"

#include <stdarg.h>
#include <string.h>

#define W32_TLS_SLOTS 1088 /* 64 + 1024, as on Windows */

/* per-thread state, created lazily for any thread that touches the runtime */
struct w32_waiter {
    os_cond_t cond;
};
struct w32_obj;
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
};
struct w32_thread *w32_self(void);

/* ---- kernel objects: HANDLE is a pointer to one of these */
enum { W32_EVENT = 1, W32_MUTEX, W32_SEMAPHORE, W32_THREAD, W32_FILE, W32_FIND, W32_HEAP };
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
};
struct w32_obj *w32_obj_new(int type, size_t size, void (*destroy)(struct w32_obj *));
/* Handles are indices into a table with a generation count (stale or double-closed handles fail with
 * ERROR_INVALID_HANDLE instead of touching freed memory); values are multiples of 4 below 2^32 and never
 * NULL, INVALID_HANDLE_VALUE or a pseudo handle. */
HANDLE w32_handle_open(struct w32_obj *o);         /* the handle takes over one reference; NULL if full */
struct w32_obj *w32_obj_get(HANDLE h, int type);   /* type 0: any; NULL + ERROR_INVALID_HANDLE if bad */
BOOL w32_handle_close(HANDLE h, int type);         /* type 0: any */
void w32_obj_ref(struct w32_obj *o);
void w32_obj_unref(struct w32_obj *o);
/* object wait support (sync.cpp): global lock around all waitable state */
extern os_lock_t w32_wait_lock;
void w32_obj_signal_locked(struct w32_obj *o);     /* wake waiters of o; caller holds w32_wait_lock */

/* ---- threads (thread.cpp) */
#define W32_PSEUDO_THREAD ((HANDLE)(LONG_PTR)-2)
#define W32_PSEUDO_PROCESS ((HANDLE)(LONG_PTR)-1)
struct w32_thread_obj;
int w32_thread_signaled(struct w32_obj *o);

/* ---- paths (path.cpp) */
#define W32_PATH_MAX 1024
/* Windows -> host. create_last: the last component may not exist (it keeps the given spelling). */
DWORD w32_map_path(const char *win, char *host, size_t n);
void w32_virtual_cwd(char *out, size_t n);
DWORD w32_full_path(const char *win, char *out, size_t n); /* normalised "x:\\a\\b" */
const char *w32_exe_path(void);              /* virtual Windows path of FalloutNV.exe */
const char *w32_user_dir(void);              /* virtual Windows path of the documents folder */
/* errno-style os error -> Win32 error code */
DWORD w32_os_error(int neg);
int w32_wildcard_match(const char *pat, const char *name); /* Windows *, ? matching, case-insensitive */

/* ---- misc */
void w32_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
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
typedef LONG (*LPTOP_LEVEL_EXCEPTION_FILTER)(struct _EXCEPTION_POINTERS *);
typedef BOOL (*LOCALE_ENUMPROCA)(LPSTR);

extern "C" {
UINT GetDriveTypeA(LPCSTR);
int MultiByteToWideChar(UINT, DWORD, LPCSTR, int, LPWSTR, int);
int WideCharToMultiByte(UINT, DWORD, LPCWSTR, int, LPSTR, int, LPCSTR, LPBOOL);
BOOL TerminateProcess(HANDLE, UINT);
LONG UnhandledExceptionFilter(struct _EXCEPTION_POINTERS *);
LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER);
void GetStartupInfoA(LPSTARTUPINFOA);
HMODULE GetModuleHandleW(LPCWSTR);
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
BOOL GetStringTypeA(LCID, DWORD, LPCSTR, int, LPWORD);
BOOL GetStringTypeW(DWORD, LPCWSTR, int, LPWORD);
BOOL IsValidLocale(LCID, DWORD);
BOOL EnumSystemLocalesA(LOCALE_ENUMPROCA, DWORD);
LCID GetUserDefaultLCID(void);
BOOL GetUserNameA(LPSTR, LPDWORD);           /* advapi32 */
DWORD GetClassLongA(HWND, int);              /* user32 */
}
/* the standard handles (GetStdHandle): fixed pseudo values outside the handle table. ReadFile/WriteFile/CloseHandle
 * route them here: -1 = not a standard handle, else the BOOL result */
int w32_std_io(HANDLE h, int write, void *buf, DWORD n, LPDWORD done);
int w32_is_std_handle(HANDLE h);

#endif
