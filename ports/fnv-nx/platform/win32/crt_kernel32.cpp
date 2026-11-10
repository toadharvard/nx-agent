/* crt_kernel32.cpp - the Win32 imports only FalloutNV.exe's statically linked MSVC 2008 CRT calls: code pages,
 * locale (NLS) queries, the standard handles, the environment block, startup info, the top-level exception filter.
 *
 * The system is a single-byte en-US Windows: LCID 0x0409, ANSI and OEM code page both 1252 (one table; a real en-US
 * system has OEM 437, which only console and ".OCP" locale code would notice), no lead bytes. Conversions serve
 * CP_ACP/CP_OEMCP/CP_THREAD_ACP/1252 and CP_UTF8 with the Windows size conventions: a length of -1 includes the
 * terminating NUL, a destination size of 0 asks for the required size, a short buffer fails with
 * ERROR_INSUFFICIENT_BUFFER. 1252's five undefined bytes (0x81 0x8d 0x8f 0x90 0x9d) map to U+0081.. as on Windows;
 * WideCharToMultiByte uses no best-fit table (an unmappable character becomes the default character).
 * Case mapping and character types cover ASCII, Latin-1, Latin Extended-A, Greek, Cyrillic and full-width Latin;
 * CompareString orders by case-folded code unit, lower case before upper case on a tie (as Windows' "a" < "A"),
 * and LCMAP_SORTKEY produces keys whose memcmp order is that of CompareString.
 *
 * The standard handles are three pseudo values outside the handle table (not multiples of 4): output and error
 * go to the debug log line by line, input reads as end of file. They are character devices without a console
 * (GetFileType FILE_TYPE_CHAR, GetConsoleMode fails), so the CRT writes them with WriteFile. A Windows GUI process
 * without a console would have NULL standard handles and lose stdout; here printf reaches the log. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- code page 1252 */
static const uint16_t k1252_hi[32] = {
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D,
    0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A,
    0x0153, 0x009D, 0x017E, 0x0178,
};

static inline unsigned w1252(unsigned char b) { return b >= 0x80 && b < 0xA0 ? k1252_hi[b - 0x80] : b; }

extern "C" UINT GetConsoleCP(void) { return 1252; }
extern "C" UINT GetConsoleOutputCP(void) { return 1252; }

extern "C" DWORD GetTimeZoneInformation(LPTIME_ZONE_INFORMATION tz)
{
    if (!tz) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0xFFFFFFFFu;   /* TIME_ZONE_ID_INVALID */
    }
    /* one zone without daylight rules, at the offset GetLocalTime applies (UTC unless the OS reports one) */
    memset(tz, 0, sizeof *tz);
    tz->Bias = -(LONG)os_tz_offset_minutes();
    return TIME_ZONE_ID_UNKNOWN;
}

/* ---- standard handles */
#define STD_IN ((HANDLE)(uintptr_t)0x3)
#define STD_OUT ((HANDLE)(uintptr_t)0x7)
#define STD_ERR ((HANDLE)(uintptr_t)0xB)
static HANDLE g_std[3] = { STD_IN, STD_OUT, STD_ERR };
struct std_line { char buf[512]; size_t n; };
static std_line g_lines[2];
static os_lock_t g_std_lock;

int w32_is_std_handle(HANDLE h) { return h == STD_IN || h == STD_OUT || h == STD_ERR; }

static int std_index(DWORD which)
{
    switch ((uint32_t)W32_DW(which)) {
    case 0xFFFFFFF6u: return 0;
    case 0xFFFFFFF5u: return 1;
    case 0xFFFFFFF4u: return 2;
    default: return -1;
    }
}

static void std_emit(std_line *l)
{
    if (l->n && l->buf[l->n - 1] == '\r')
        l->n--;
    l->buf[l->n] = 0;
    os_debug_print(l->buf);
    l->n = 0;
}

static void std_write(HANDLE h, const char *p, size_t n)
{
    std_line *l = &g_lines[h == STD_ERR];
    os_lock(&g_std_lock);
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '\n') {
            std_emit(l);
            continue;
        }
        l->buf[l->n++] = p[i];
        if (l->n == sizeof l->buf - 1)
            std_emit(l);
    }
    os_unlock(&g_std_lock);
}

int w32_std_io(HANDLE h, int write, void *buf, DWORD n, LPDWORD done)
{
    if (!w32_is_std_handle(h))
        return -1;
    if (done)
        *done = 0;
    if (h == STD_IN) {
        if (write) {
            SetLastError(ERROR_ACCESS_DENIED);
            return FALSE;
        }
        return TRUE;   /* end of file */
    }
    if (!write) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    std_write(h, (const char *)buf, W32_DW(n));
    if (done)
        *done = W32_DW(n);
    return TRUE;
}

extern "C" HANDLE GetStdHandle(DWORD which)
{
    int i = std_index(which);
    if (i < 0) {
        SetLastError(ERROR_INVALID_HANDLE);
        return INVALID_HANDLE_VALUE;
    }
    return g_std[i];
}

extern "C" BOOL SetStdHandle(DWORD which, HANDLE h)
{
    int i = std_index(which);
    if (i < 0) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    g_std[i] = h;
    return TRUE;
}

extern "C" DWORD GetFileType(HANDLE h)
{
    if (w32_is_std_handle(h))
        return FILE_TYPE_CHAR;
    struct w32_obj *o = w32_obj_get(h, 0);   /* ERROR_INVALID_HANDLE when bad */
    if (!o)
        return FILE_TYPE_UNKNOWN;
    if (o->type == W32_FILE)
        return FILE_TYPE_DISK;
    SetLastError(ERROR_SUCCESS);
    return FILE_TYPE_UNKNOWN;
}

extern "C" BOOL GetConsoleMode(HANDLE h, LPDWORD mode)
{
    W32_UNUSED(mode);
    W32_UNUSED(h);
    SetLastError(ERROR_INVALID_HANDLE);   /* no console: the standard handles are plain character devices */
    return FALSE;
}

extern "C" BOOL WriteConsoleA(HANDLE h, const void *buf, DWORD n, LPDWORD put, LPVOID reserved)
{
    W32_UNUSED(reserved);
    if (put)
        *put = 0;
    if (h != STD_OUT && h != STD_ERR) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    std_write(h, (const char *)buf, W32_DW(n));
    if (put)
        *put = W32_DW(n);
    return TRUE;
}

extern "C" BOOL WriteConsoleW(HANDLE h, const void *buf, DWORD n, LPDWORD put, LPVOID reserved)
{
    W32_UNUSED(reserved);
    if (put)
        *put = 0;
    if (h != STD_OUT && h != STD_ERR) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    const WCHAR *w = (const WCHAR *)buf;
    for (DWORD i = 0, cnt = W32_DW(n); i < cnt;) {   /* in chunks, as the log's code page (1252) */
        char tmp[256];
        int k = (int)(cnt - i < sizeof tmp ? cnt - i : sizeof tmp);
        WideCharToMultiByte(CP_ACP, 0, w + i, k, tmp, k, NULL, NULL);
        std_write(h, tmp, (size_t)k);
        i += (DWORD)k;
    }
    if (put)
        *put = W32_DW(n);
    return TRUE;
}

extern "C" UINT SetHandleCount(UINT n) { return n; }

/* ---- files and modules by UTF-16 name: as the A functions, with the name in the ANSI code page */
static bool narrow_name(LPCWSTR w, char *out, size_t n)
{
    BOOL used = FALSE;
    int r = WideCharToMultiByte(CP_ACP, 0, w, -1, out, (int)n, NULL, &used);
    return r > 0 && !used;
}

HANDLE w32_create_file_name(LPCSTR name, DWORD access, DWORD share, DWORD disp, DWORD flags, int utf8); /* file.cpp */
extern "C" HANDLE CreateFileW(LPCWSTR name, DWORD access, DWORD share, void *sa, DWORD disp, DWORD flags, HANDLE tmpl)
{
    (void)sa; (void)tmpl;
    if (!name) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return INVALID_HANDLE_VALUE;
    }
    /* the layer names files in UTF-8 inside: the full UTF-16 name, surrogate pairs included, converts losslessly */
    char u[W32_PATH_MAX];
    size_t o = 0;
    for (const WCHAR *w = name; *w; w++) {
        uint32_t c = (uint16_t)*w;
        if (c >= 0xD800 && c < 0xDC00 && (uint16_t)w[1] >= 0xDC00 && (uint16_t)w[1] < 0xE000)
            c = 0x10000 + ((c - 0xD800) << 10) + ((uint16_t)*++w - 0xDC00);
        else if (c >= 0xD800 && c < 0xE000) {
            SetLastError(ERROR_INVALID_NAME);
            return INVALID_HANDLE_VALUE;
        }
        int k = c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
        if (o + (size_t)k + 1 > sizeof u) {
            SetLastError(ERROR_INVALID_NAME);
            return INVALID_HANDLE_VALUE;
        }
        if (k == 1)
            u[o++] = (char)c;
        else {
            u[o++] = (char)((k == 2 ? 0xC0 : k == 3 ? 0xE0 : 0xF0) | (c >> (6 * (k - 1))));
            for (int i = k - 2; i >= 0; i--)
                u[o++] = (char)(0x80 | ((c >> (6 * i)) & 0x3F));
        }
    }
    u[o] = 0;
    return w32_create_file_name(u, access, share, disp, flags, 1);
}

extern "C" HMODULE GetModuleHandleW(LPCWSTR name)
{
    if (!name)
        return GetModuleHandleA(NULL);
    char a[256];
    if (!narrow_name(name, a, sizeof a)) {
        SetLastError(ERROR_MOD_NOT_FOUND);
        return NULL;
    }
    return GetModuleHandleA(a);
}

/* kernel32.dll and user32.dll as modules: the CRT's _mtinit fails without GetModuleHandleW(L"KERNEL32.DLL") (it
 * then asks it for FlsAlloc/EncodePointer, absent here: it falls back to TLS), and __crtMessageBoxA reaches
 * MessageBoxA through LoadLibraryA("USER32.DLL"). GetProcAddress serves the export table; the guard stops the
 * table's fallback to registered modules from asking this resolver again. */
static __thread int g_sys_depth;
static FARPROC sys_export(const char *dll, const char *proc)
{
    if (g_sys_depth)
        return NULL;
    g_sys_depth++;
    void *p = fnvnx_win32_lookup_export(dll, proc);
    g_sys_depth--;
    return (FARPROC)p;
}
static FARPROC kernel32_export(const char *proc) { return sys_export("kernel32", proc); }
static FARPROC user32_export(const char *proc) { return sys_export("user32", proc); }
__attribute__((constructor(FNVNX_INIT_MODULES))) static void sys_modules_register(void)
{
    fnvnx_register_module("kernel32.dll", kernel32_export);
    fnvnx_register_module("user32.dll", user32_export);
}

/* ---- process */
extern "C" BOOL TerminateProcess(HANDLE h, UINT code)
{
    if (h == W32_PSEUDO_PROCESS) {
        w32_log("TerminateProcess(%u)", (unsigned)code);
        os_exit_process((int)code);
    }
    SetLastError(ERROR_INVALID_HANDLE);   /* no other processes */
    return FALSE;
}

static LPTOP_LEVEL_EXCEPTION_FILTER g_top_filter;

extern "C" LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER f)
{
    return __atomic_exchange_n(&g_top_filter, f, __ATOMIC_SEQ_CST);
}

/* as on Windows: the top-level filter decides; without one (or when it continues the search) the process is to
 * be terminated by the caller (EXCEPTION_EXECUTE_HANDLER) - there is no error reporting dialog */
extern "C" LONG UnhandledExceptionFilter(struct _EXCEPTION_POINTERS *ep)
{
    LPTOP_LEVEL_EXCEPTION_FILTER f = __atomic_load_n(&g_top_filter, __ATOMIC_SEQ_CST);
    w32_log("UnhandledExceptionFilter(%p)%s", (void *)ep, f ? ": calling the top-level filter" : "");
    if (f && ep) {
        LONG r = f(ep);
        if (r != EXCEPTION_CONTINUE_SEARCH)
            return r;
    }
    return EXCEPTION_EXECUTE_HANDLER;
}

extern "C" void GetStartupInfoA(LPSTARTUPINFOA si)
{
    if (!si)
        return;
    memset(si, 0, sizeof *si);
    si->cb = sizeof *si;
}

extern "C" UINT GetDriveTypeA(LPCSTR root)
{
    /* NULL, "\" and relative: the current drive (C:) */
    if (!root || !((root[0] | 32) >= 'a' && (root[0] | 32) <= 'z' && root[1] == ':'))
        return root && root[0] && root[0] != '\\' && root[0] != '/' ? DRIVE_NO_ROOT_DIR : DRIVE_FIXED;
    if (root[2] && !((root[2] == '\\' || root[2] == '/') && !root[3]))
        return DRIVE_NO_ROOT_DIR;   /* not a drive root */
    if ((root[0] | 32) == 'c')
        return DRIVE_FIXED;
    char host[W32_PATH_MAX];
    char win[4] = { root[0], ':', '\\', 0 };
    os_stat_t st;
    return !w32_map_path(win, host, sizeof host) && !os_stat(host, &st) && st.is_dir ? DRIVE_FIXED : DRIVE_NO_ROOT_DIR;
}

/* ---- environment: a process-local block (the CRT builds its environ from it), seeded with the variables a
 * Windows session always has (PATH, TEMP, SystemRoot, USERPROFILE, APPDATA...) on first use */
static char **g_env;   /* "NAME=value", NULL-terminated */
static size_t g_nenv;
static os_lock_t g_env_lock;
static bool g_env_seeded;

static const char *const ENV_DEFAULTS[] = {
    "ALLUSERSPROFILE=C:\\ProgramData", "APPDATA=C:\\Users\\Player\\AppData\\Roaming",
    "COMPUTERNAME=FNVNX", "ComSpec=C:\\Windows\\System32\\cmd.exe",
    "HOMEDRIVE=C:", "HOMEPATH=\\Users\\Player",
    "LOCALAPPDATA=C:\\Users\\Player\\AppData\\Local", "NUMBER_OF_PROCESSORS=4", "OS=Windows_NT",
    "PATH=C:\\Windows\\System32;C:\\Windows", "PATHEXT=.COM;.EXE;.BAT;.CMD",
    "PROCESSOR_ARCHITECTURE=x86", "ProgramData=C:\\ProgramData", "ProgramFiles=C:\\Program Files",
    "SystemDrive=C:", "SystemRoot=C:\\Windows", "TEMP=C:\\Users\\Player\\AppData\\Local\\Temp",
    "TMP=C:\\Users\\Player\\AppData\\Local\\Temp", "USERNAME=Player", "USERPROFILE=C:\\Users\\Player",
    "windir=C:\\Windows",
};

/* caller holds the lock */
static void env_seed(void)
{
    if (g_env_seeded)
        return;
    g_env_seeded = true;
    size_t n = sizeof ENV_DEFAULTS / sizeof *ENV_DEFAULTS;
    char **v = (char **)malloc((n + 1) * sizeof *v);
    if (!v)
        return;
    g_env = v;
    for (size_t i = 0; i < n; i++)
        if ((v[g_nenv] = strdup(ENV_DEFAULTS[i])))
            g_nenv++;
}

/* index of name, or -1; caller holds the lock */
static long env_find(const char *name)
{
    size_t ln = strlen(name);
    for (size_t i = 0; i < g_nenv; i++)
        if (!w32_strnicmp(g_env[i], name, ln) && g_env[i][ln] == '=')
            return (long)i;
    return -1;
}

static bool env_name_ok(const char *name) { return name && *name && !strchr(name + 1, '='); }

extern "C" BOOL SetEnvironmentVariableA(LPCSTR name, LPCSTR value)
{
    if (!env_name_ok(name)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    char *e = NULL;
    if (value) {
        size_t ln = strlen(name), lv = strlen(value);
        e = (char *)malloc(ln + lv + 2);
        if (!e) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return FALSE;
        }
        memcpy(e, name, ln);
        e[ln] = '=';
        memcpy(e + ln + 1, value, lv + 1);
    }
    os_lock(&g_env_lock);
    env_seed();
    long i = env_find(name);
    if (i < 0 && !e) {   /* deleting a variable that is not there */
        os_unlock(&g_env_lock);
        SetLastError(ERROR_ENVVAR_NOT_FOUND);
        return FALSE;
    }
    if (i >= 0) {
        free(g_env[i]);
        if (e)
            g_env[i] = e;
        else
            g_env[i] = g_env[--g_nenv];
    } else if (e) {
        char **n = (char **)realloc(g_env, (g_nenv + 2) * sizeof *n);
        if (!n) {
            os_unlock(&g_env_lock);
            free(e);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return FALSE;
        }
        g_env = n;
        g_env[g_nenv++] = e;
    }
    os_unlock(&g_env_lock);
    return TRUE;
}

extern "C" DWORD GetEnvironmentVariableA(LPCSTR name, LPSTR buf, DWORD n)
{
    n = W32_DW(n);
    if (!env_name_ok(name)) {
        SetLastError(ERROR_ENVVAR_NOT_FOUND);
        return 0;
    }
    os_lock(&g_env_lock);
    env_seed();
    long i = env_find(name);
    DWORD r = 0;
    if (i < 0) {
        SetLastError(ERROR_ENVVAR_NOT_FOUND);
    } else {
        const char *v = g_env[i] + strlen(name) + 1;
        DWORD len = (DWORD)strlen(v);
        if (len < n && buf) {
            memcpy(buf, v, len + 1);
            r = len;
            if (!len)
                SetLastError(ERROR_SUCCESS);   /* an empty value: 0 with no error */
        } else {
            r = len + 1;   /* the size needed, NUL included */
        }
    }
    os_unlock(&g_env_lock);
    return r;
}

extern "C" char *GetEnvironmentStrings(void)
{
    os_lock(&g_env_lock);
    env_seed();
    size_t total = 2;
    for (size_t i = 0; i < g_nenv; i++)
        total += strlen(g_env[i]) + 1;
    char *b = (char *)malloc(total), *p = b;
    if (b) {
        for (size_t i = 0; i < g_nenv; i++) {
            size_t l = strlen(g_env[i]) + 1;
            memcpy(p, g_env[i], l);
            p += l;
        }
        p[0] = p[1] = 0;   /* an empty block is two NULs */
    }
    os_unlock(&g_env_lock);
    if (!b)
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return b;
}

extern "C" WCHAR *GetEnvironmentStringsW(void)
{
    char *a = GetEnvironmentStrings();
    if (!a)
        return NULL;
    size_t n = 0;
    while (a[n] || a[n + 1])
        n++;
    n += 2;
    WCHAR *w = (WCHAR *)malloc(n * sizeof *w);
    if (w)
        for (size_t i = 0; i < n; i++)
            w[i] = (WCHAR)w1252((unsigned char)a[i]);
    else
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    free(a);
    return w;
}

extern "C" BOOL FreeEnvironmentStringsA(char *b)
{
    free(b);
    return TRUE;
}

extern "C" BOOL FreeEnvironmentStringsW(WCHAR *b)
{
    free(b);
    return TRUE;
}

/* ---- advapi32 / user32 */
extern "C" BOOL GetUserNameA(LPSTR buf, LPDWORD size)
{
    static const char name[] = "Player";   /* the profile directory's name (path.cpp: C:\Users\Player) */
    if (!size) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    if (W32_DW(*size) < sizeof name || !buf) {
        *size = sizeof name;
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }
    memcpy(buf, name, sizeof name);
    *size = sizeof name;   /* NUL included */
    return TRUE;
}
