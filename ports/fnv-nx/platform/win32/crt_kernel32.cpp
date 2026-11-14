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
 * There is no console and no standard handle (ENV_TRUTH F5): as in a Windows GUI process GetStdHandle gives NULL until
 * SetStdHandle sets one, and every console call fails with ERROR_INVALID_HANDLE. */
#include "w32.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

/* ---- code page 1252 */
static const uint16_t k1252_hi[32] = {
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D,
    0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A,
    0x0153, 0x009D, 0x017E, 0x0178,
};

static inline unsigned w1252(unsigned char b) { return b >= 0x80 && b < 0xA0 ? k1252_hi[b - 0x80] : b; }

/* no console object exists in this process: the console code pages are those of the attached console, of which
 * there is none */
extern "C" UINT GetConsoleCP(void)
{
    SetLastError(ERROR_INVALID_HANDLE);
    return 0;
}

extern "C" UINT GetConsoleOutputCP(void)
{
    SetLastError(ERROR_INVALID_HANDLE);
    return 0;
}

/* GetTimeZoneInformation: time.cpp, next to the rule its time conversions apply */

/* ---- standard handles: NULL (none) until SetStdHandle sets one; the value is not checked, as on Windows */
static HANDLE g_std[3];

static int std_index(DWORD which)
{
    switch ((uint32_t)W32_DW(which)) {
    case 0xFFFFFFF6u: return 0;   /* STD_INPUT_HANDLE */
    case 0xFFFFFFF5u: return 1;   /* STD_OUTPUT_HANDLE */
    case 0xFFFFFFF4u: return 2;   /* STD_ERROR_HANDLE */
    default: return -1;
    }
}

extern "C" HANDLE GetStdHandle(DWORD which)
{
    int i = std_index(which);
    if (i < 0) {
        SetLastError(ERROR_INVALID_HANDLE);
        return INVALID_HANDLE_VALUE;
    }
    return __atomic_load_n(&g_std[i], __ATOMIC_SEQ_CST);
}

extern "C" BOOL SetStdHandle(DWORD which, HANDLE h)
{
    int i = std_index(which);
    if (i < 0) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    __atomic_store_n(&g_std[i], h, __ATOMIC_SEQ_CST);
    return TRUE;
}

bool w32_file_is_device(HANDLE h);   /* file.cpp */
extern "C" DWORD GetFileType(HANDLE h)
{
    struct w32_obj *o = w32_obj_get(h, 0);   /* ERROR_INVALID_HANDLE when bad (NULL included) */
    if (!o)
        return FILE_TYPE_UNKNOWN;
    if (o->type == W32_FILE)
        return w32_file_is_device(h) ? FILE_TYPE_CHAR : FILE_TYPE_DISK;
    SetLastError(ERROR_SUCCESS);
    return FILE_TYPE_UNKNOWN;
}

/* the console functions act on console handles; the process has no console object, so no handle is one */
extern "C" BOOL GetConsoleMode(HANDLE h, LPDWORD mode)
{
    W32_UNUSED(mode);
    W32_UNUSED(h);
    SetLastError(ERROR_INVALID_HANDLE);
    return FALSE;
}

extern "C" BOOL WriteConsoleA(HANDLE h, const void *buf, DWORD n, LPDWORD put, LPVOID reserved)
{
    W32_UNUSED(h); W32_UNUSED(buf); W32_UNUSED(n); W32_UNUSED(put); W32_UNUSED(reserved);
    SetLastError(ERROR_INVALID_HANDLE);
    return FALSE;
}

extern "C" BOOL WriteConsoleW(HANDLE h, const void *buf, DWORD n, LPDWORD put, LPVOID reserved)
{
    W32_UNUSED(h); W32_UNUSED(buf); W32_UNUSED(n); W32_UNUSED(put); W32_UNUSED(reserved);
    SetLastError(ERROR_INVALID_HANDLE);
    return FALSE;
}

typedef PHANDLER_ROUTINE ctrl_handler_fn;
static ctrl_handler_fn *g_ctrl;   /* the handler list; most recently added first on delivery (none is delivered) */
static size_t g_nctrl;
static bool g_ctrl_ignore;        /* SetConsoleCtrlHandler(NULL, TRUE) */
static os_lock_t g_ctrl_lock;

extern "C" BOOL SetConsoleCtrlHandler(ctrl_handler_fn fn, BOOL add)
{
    BOOL r = TRUE;
    os_lock(&g_ctrl_lock);
    if (!fn) {
        g_ctrl_ignore = add != 0;
    } else if (add) {
        ctrl_handler_fn *n = (ctrl_handler_fn *)realloc(g_ctrl, (g_nctrl + 1) * sizeof *n);
        if (n) {
            g_ctrl = n;
            g_ctrl[g_nctrl++] = fn;
        } else {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            r = FALSE;
        }
    } else {
        size_t i = g_nctrl;
        while (i && g_ctrl[i - 1] != fn)
            i--;
        if (!i) {
            SetLastError(ERROR_INVALID_PARAMETER);
            r = FALSE;
        } else {
            memmove(g_ctrl + i - 1, g_ctrl + i, (g_nctrl - i) * sizeof *g_ctrl);
            g_nctrl--;
        }
    }
    os_unlock(&g_ctrl_lock);
    return r;
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
        W32Log().s("TerminateProcess(").u((unsigned)code).s(")").emit();
        os_exit_process((int)code);
    }
    SetLastError(ERROR_INVALID_HANDLE);   /* no other processes */
    return FALSE;
}

void w32_log_unhandled(const void *ep, const char *tail);   /* thread.cpp: next to the record layout */

static uintptr_t g_top_filter;   /* the game's filter (LPTOP_LEVEL_EXCEPTION_FILTER value) */

extern "C" LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER f)
{
    return LPTOP_LEVEL_EXCEPTION_FILTER(__atomic_exchange_n(&g_top_filter, f.value(), __ATOMIC_SEQ_CST));
}

/* as on Windows: the top-level filter decides; without one (or when it continues the search) the process is to
 * be terminated by the caller (EXCEPTION_EXECUTE_HANDLER) - there is no error reporting dialog */
extern "C" LONG UnhandledExceptionFilter(struct _EXCEPTION_POINTERS *ep)
{
    LPTOP_LEVEL_EXCEPTION_FILTER f(__atomic_load_n(&g_top_filter, __ATOMIC_SEQ_CST));   /* no lock is held across the call */
    w32_log_unhandled(ep, f ? ": calling the top-level filter" : ": no top-level filter");
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

/* a variable's name for ordering: up to its '=', searched after the first character so that the "=C:=..." drive
 * variables keep their leading '='; compared by upper-case 1252 letters, as the Windows environment block is kept */
static unsigned env_up(unsigned char c)
{
    if (c >= 'a' && c <= 'z')
        return c - 32u;
    if (c >= 0xE0 && c != 0xF7 && c != 0xFF)
        return c - 32u;   /* Latin-1 small letters */
    return c;
}

static int env_cmp(const char *a, const char *b)
{
    int ea = a[0] == '=', eb = b[0] == '=';
    if (ea != eb)
        return eb - ea;   /* variables whose name starts with '=' come first */
    for (size_t i = (size_t)ea;; i++) {
        unsigned x = a[i] == '=' ? 0 : env_up((unsigned char)a[i]), y = b[i] == '=' ? 0 : env_up((unsigned char)b[i]);
        if (x != y)
            return x < y ? -1 : 1;
        if (!x)
            return 0;
    }
}

static int env_qsort_cmp(const void *a, const void *b) { return env_cmp(*(const char *const *)a, *(const char *const *)b); }

/* the session's own variables, every value derived from the platform's one source for it: the processor count
 * from GetSystemInfo (P1), the directories from the path layer (F1/F2/P5). Caller holds the lock. */
static void env_add(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void env_add(const char *fmt, ...)
{
    char tmp[W32_PATH_MAX * 2];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    char **v = (char **)realloc(g_env, (g_nenv + 2) * sizeof *v);
    if (!v)
        return;
    g_env = v;
    if ((v[g_nenv] = strdup(tmp)))
        g_nenv++;
}

static void env_seed(void)
{
    if (g_env_seeded)
        return;
    g_env_seeded = true;
    const char *user = W32_USER_DIR, *win = W32_WIN_DIR, *sys = W32_SYS_DIR;
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    char drive[3] = { win[0], ':', 0 };
    env_add("ALLUSERSPROFILE=%s\\ProgramData", drive);
    env_add("APPDATA=%s\\AppData\\Roaming", user);
    env_add("COMPUTERNAME=FNVNX");
    env_add("ComSpec=%s\\cmd.exe", sys);
    env_add("HOMEDRIVE=%s", drive);
    env_add("HOMEPATH=%s", user + 2);
    env_add("LOCALAPPDATA=%s\\AppData\\Local", user);
    env_add("NUMBER_OF_PROCESSORS=%u", (unsigned)W32_DW(si.dwNumberOfProcessors));
    env_add("OS=Windows_NT");
    env_add("PATH=%s;%s", sys, win);
    env_add("PATHEXT=.COM;.EXE;.BAT;.CMD");
    env_add("PROCESSOR_ARCHITECTURE=%s", si.wProcessorArchitecture == 0 ? "x86" : "AMD64");
    env_add("ProgramData=%s\\ProgramData", drive);
    env_add("ProgramFiles=%s\\Program Files", drive);
    env_add("SystemDrive=%s", drive);
    env_add("SystemRoot=%s", win);
    env_add("TEMP=%s\\AppData\\Local\\Temp", user);
    env_add("TMP=%s\\AppData\\Local\\Temp", user);
    env_add("USERNAME=Player");
    env_add("USERPROFILE=%s", user);
    env_add("windir=%s", win);
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
    char *b = (char *)malloc(total);
    const char **order = (const char **)malloc((g_nenv + 1) * sizeof *order);
    if (b && order) {
        for (size_t i = 0; i < g_nenv; i++)
            order[i] = g_env[i];
        qsort(order, g_nenv, sizeof *order, env_qsort_cmp);   /* the block is sorted by name, as Windows keeps it */
        char *p = b;
        for (size_t i = 0; i < g_nenv; i++) {
            size_t l = strlen(order[i]) + 1;
            memcpy(p, order[i], l);
            p += l;
        }
        p[0] = p[1] = 0;   /* an empty block is two NULs */
    } else {
        free(b);
        b = NULL;
    }
    free(order);
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
