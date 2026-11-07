/* module.cpp - modules, command line, process exit, debugging and small kernel32 helpers. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- modules. The exe's HMODULE is its original image base, where the loader (S3) maps the data image. */
#define EXE_MODULE ((HMODULE)(uintptr_t)0x00400000u)

struct builtin_module { char name[64]; fnvnx_module_resolver resolve; };
static struct builtin_module g_modules[32];
static int g_nmodules;
static os_lock_t g_mod_lock;

static const char *base_name(const char *p)
{
    const char *b = p;
    for (; *p; p++)
        if (*p == '\\' || *p == '/' || *p == ':')
            b = p + 1;
    return b;
}

/* "xinput1_3" and "XINPUT1_3.DLL" name the same module */
static int same_module(const char *a, const char *b)
{
    a = base_name(a);
    size_t la = strlen(a), lb = strlen(b);
    if (la > 4 && !w32_stricmp(a + la - 4, ".dll"))
        la -= 4;
    if (lb > 4 && !w32_stricmp(b + lb - 4, ".dll"))
        lb -= 4;
    return la == lb && !w32_strnicmp(a, b, la);
}

extern "C" void fnvnx_register_module(const char *dll, fnvnx_module_resolver resolve)
{
    os_lock(&g_mod_lock);
    if (g_nmodules < 32) {
        snprintf(g_modules[g_nmodules].name, sizeof g_modules[0].name, "%s", dll);
        g_modules[g_nmodules].resolve = resolve;
        g_nmodules++;
    }
    os_unlock(&g_mod_lock);
}

/* Resolver of a module registered with fnvnx_register_module (S7 audio/input, ...), for export lookups. */
extern "C" void *fnvnx_registered_module_export(const char *dll, const char *proc)
{
    fnvnx_module_resolver r = NULL;
    os_lock(&g_mod_lock);
    for (int i = 0; i < g_nmodules && !r; i++)
        if (!dll || same_module(dll, g_modules[i].name))
            r = g_modules[i].resolve;
    os_unlock(&g_mod_lock);
    return r ? (void *)r(proc) : NULL;
}

static HMODULE module_handle(const char *name)
{
    os_lock(&g_mod_lock);
    HMODULE h = NULL;
    for (int i = 0; i < g_nmodules; i++)
        if (same_module(name, g_modules[i].name))
            h = (HMODULE)&g_modules[i];
    os_unlock(&g_mod_lock);
    return h;
}

extern "C" HMODULE GetModuleHandleA(LPCSTR name)
{
    if (!name || same_module(name, "FalloutNV.exe"))
        return EXE_MODULE;
    HMODULE h = module_handle(name);
    if (!h)
        SetLastError(ERROR_MOD_NOT_FOUND);
    return h;
}

extern "C" HMODULE LoadLibraryA(LPCSTR name)
{
    if (!name) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    HMODULE h = same_module(name, "FalloutNV.exe") ? EXE_MODULE : module_handle(name);
    if (!h) {
        w32_log("LoadLibraryA(\"%s\"): no such module on this platform", name);
        SetLastError(ERROR_MOD_NOT_FOUND);
    }
    return h;
}

extern "C" BOOL FreeLibrary(HMODULE h) { return h != NULL; }

extern "C" FARPROC GetProcAddress(HMODULE h, LPCSTR proc)
{
    FARPROC f = NULL;
    if ((uintptr_t)proc > 0xFFFF && h != EXE_MODULE) {
        struct builtin_module *m = (struct builtin_module *)h;
        if (m >= g_modules && m < g_modules + g_nmodules)
            f = m->resolve(proc);
        if (f && w32_mem_on)
            f = w32_mem_hook_proc(proc, f);   /* the memory lane: D3D9 resources (memtrack.cpp) */
    }
    if (!f)
        SetLastError(ERROR_PROC_NOT_FOUND);
    return f;
}

extern "C" DWORD GetModuleFileNameA(HMODULE h, LPSTR buf, DWORD n)
{
    n = W32_DW(n);
    const char *path = w32_exe_path();
    char tmp[W32_PATH_MAX];
    if (h && h != EXE_MODULE) {
        struct builtin_module *m = (struct builtin_module *)h;
        if (m < g_modules || m >= g_modules + g_nmodules) {
            SetLastError(ERROR_MOD_NOT_FOUND);
            return 0;
        }
        snprintf(tmp, sizeof tmp, "C:\\Windows\\System32\\%s", m->name);
        path = tmp;
    }
    DWORD len = (DWORD)strlen(path);
    if (!n)
        return 0;
    if (len >= n) { /* truncated, 0-terminated, ERROR_INSUFFICIENT_BUFFER (XP+ behaviour) */
        memcpy(buf, path, n - 1);
        buf[n - 1] = 0;
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return n;
    }
    memcpy(buf, path, len + 1);
    return len;
}

/* ---- command line */
static char *g_cmdline_a;
static WCHAR *g_cmdline_w;

extern "C" void fnvnx_set_command_line(int argc, char **argv)
{
    size_t n = strlen(w32_exe_path()) + 3;
    for (int i = 1; i < argc; i++)
        n += strlen(argv[i]) * 2 + 3;
    char *s = (char *)malloc(n + 1);
    if (!s)
        return;
    size_t o = (size_t)sprintf(s, "\"%s\"", w32_exe_path());
    for (int i = 1; i < argc; i++) {
        int q = !*argv[i] || strpbrk(argv[i], " \t\"");
        s[o++] = ' ';
        if (q)
            s[o++] = '"';
        for (const char *p = argv[i]; *p; p++) {
            if (*p == '"')
                s[o++] = '\\';
            s[o++] = *p;
        }
        if (q)
            s[o++] = '"';
    }
    s[o] = 0;
    free(g_cmdline_a);
    g_cmdline_a = s;
    free(g_cmdline_w);
    g_cmdline_w = (WCHAR *)malloc((o + 1) * sizeof(WCHAR));
    if (g_cmdline_w)
        for (size_t i = 0; i <= o; i++)
            g_cmdline_w[i] = (WCHAR)(unsigned char)s[i]; /* the command line is ASCII here */
}

extern "C" LPSTR GetCommandLineA(void)
{
    if (!g_cmdline_a)
        fnvnx_set_command_line(0, NULL);
    return g_cmdline_a;
}

extern "C" LPWSTR GetCommandLineW(void)
{
    if (!g_cmdline_w)
        fnvnx_set_command_line(0, NULL);
    return g_cmdline_w;
}

/* CommandLineToArgvW: the shell32 rules. argv[0] ends at the next space or closing quote (no escapes); then
 * 2n backslashes + quote -> n backslashes and a quote toggles quoting; 2n+1 backslashes + quote -> n
 * backslashes and a literal quote; "" inside quotes -> literal quote. One LocalAlloc block, freed by LocalFree. */
extern "C" LPWSTR *CommandLineToArgvW(LPCWSTR cmd, int *argc)
{
    if (!argc) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    size_t len = 0;
    if (cmd)
        while (cmd[len])
            len++;
    size_t maxargs = len / 2 + 2;
    size_t bytes = maxargs * sizeof(LPWSTR) + (len + maxargs + 2) * sizeof(WCHAR);
    LPWSTR *argv = (LPWSTR *)LocalAlloc(0x40, bytes);
    if (!argv) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    WCHAR *out = (WCHAR *)(argv + maxargs);
    int n = 0;
    const WCHAR *p = cmd;
    if (!cmd || !*cmd) {
        /* empty: argv[0] = the module path */
        const char *exe = w32_exe_path();
        argv[n++] = out;
        while (*exe)
            *out++ = (WCHAR)(unsigned char)*exe++;
        *out++ = 0;
        *argc = n;
        return argv;
    }
    /* argv[0] */
    argv[n++] = out;
    if (*p == '"') {
        p++;
        while (*p && *p != '"')
            *out++ = *p++;
        if (*p == '"')
            p++;
    } else {
        while (*p && *p != ' ' && *p != '\t')
            *out++ = *p++;
    }
    *out++ = 0;
    for (;;) {
        while (*p == ' ' || *p == '\t')
            p++;
        if (!*p)
            break;
        argv[n++] = out;
        int inq = 0;
        while (*p && (inq || (*p != ' ' && *p != '\t'))) {
            if (*p == '\\') {
                size_t bs = 0;
                while (*p == '\\') {
                    bs++;
                    p++;
                }
                if (*p == '"') {
                    for (size_t i = 0; i < bs / 2; i++)
                        *out++ = '\\';
                    if (bs & 1) {
                        *out++ = '"';
                        p++;
                    }
                } else {
                    for (size_t i = 0; i < bs; i++)
                        *out++ = '\\';
                }
            } else if (*p == '"') {
                if (inq && p[1] == '"') {
                    *out++ = '"';
                    p += 2;
                } else {
                    inq = !inq;
                    p++;
                }
            } else {
                *out++ = *p++;
            }
        }
        *out++ = 0;
    }
    argv[n] = NULL;
    *argc = n;
    return argv;
}

/* ---- process */
extern "C" void ExitProcess(UINT code)
{
    w32_log("ExitProcess(%u)", (unsigned)code);
    fnvnx_threads_flush();   /* the race checker's pairs and the watchdog's summary, when they are on */
    fnvnx_mem_flush();       /* the memory lane's peak, sites and summary, when it is on */
    fnvnx_plat_flush();      /* the platform lane's counts and summary, when it is on */
    os_exit_process((int)code);
}

extern "C" void OutputDebugStringA(LPCSTR s)
{
    if (s)
        os_debug_print(s);
}

extern "C" BOOL IsDebuggerPresent(void) { return FALSE; }

extern "C" void DebugBreak(void)
{
    /* without a debugger this would kill the process with a breakpoint exception; log and continue */
    w32_log("DebugBreak() ignored");
    FNVNX_NOOP();
}

extern "C" BOOL IsBadCodePtr(FARPROC p) { return p == NULL; }
extern "C" BOOL IsBadReadPtr(const void *p, UINT_PTR n) { return n && !p; }
extern "C" BOOL IsBadWritePtr(LPVOID p, UINT_PTR n) { return n && !p; }

extern "C" BOOL GetComputerNameA(LPSTR buf, LPDWORD n)
{
    static const char name[] = "SWITCH";
    if (!n || W32_DW(*n) < sizeof name) {
        if (n)
            *n = sizeof name;
        SetLastError(111); /* ERROR_BUFFER_OVERFLOW */
        return FALSE;
    }
    memcpy(buf, name, sizeof name);
    *n = sizeof name - 1;
    return TRUE;
}

/* GetEnvironmentVariableA: crt_kernel32.cpp, next to the environment block it reads */

/* FormatMessageA: FROM_SYSTEM yields "Error <n>." (no message tables); FROM_STRING copies the string
 * without insert processing beyond %0. */
extern "C" DWORD FormatMessageA(DWORD flags, LPCVOID src, DWORD id, DWORD lang, LPSTR buf, DWORD n, void *args)
{
    W32_UNUSED(lang);
    W32_UNUSED(args);
    flags = W32_DW(flags);
    char msg[512];
    if (flags & FORMAT_MESSAGE_FROM_STRING) {
        snprintf(msg, sizeof msg, "%s", src ? (const char *)src : "");
        char *z = strstr(msg, "%0");
        if (z)
            *z = 0;
    } else {
        snprintf(msg, sizeof msg, "Error %lu.\r\n", (unsigned long)W32_DW(id));
    }
    DWORD len = (DWORD)strlen(msg);
    if (flags & FORMAT_MESSAGE_ALLOCATE_BUFFER) {
        char *p = (char *)LocalAlloc(0, (len + 1 > W32_DW(n) ? len + 1 : W32_DW(n)));
        if (!p)
            return 0;
        memcpy(p, msg, len + 1);
        *(char **)buf = p;
        return len;
    }
    if (W32_DW(n) <= len) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    memcpy(buf, msg, len + 1);
    return len;
}

extern "C" LPSTR lstrcpyA(LPSTR d, LPCSTR s) { return d && s ? strcpy(d, s) : NULL; }
extern "C" LPSTR lstrcatA(LPSTR d, LPCSTR s) { return d && s ? strcat(d, s) : NULL; }
extern "C" int lstrlenA(LPCSTR s) { return s ? (int)strlen(s) : 0; }
extern "C" int lstrcmpiA(LPCSTR a, LPCSTR b) { int c = w32_stricmp(a ? a : "", b ? b : ""); return c < 0 ? -1 : c > 0; }
