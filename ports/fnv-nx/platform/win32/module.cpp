/* module.cpp - modules, command line, process exit, debugging and small kernel32 helpers. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- modules. ONE table, ONE rule: a module is (a) the exe, whose HMODULE is its original image base where the
 * loader (S3) maps the data image, (b) a system DLL - one per distinct DLL name of the export table
 * (fnvnx_win32_system_module), all "loaded" and pinned - or (c) a translated game module registered by the loader
 * with fnvnx_register_module. Whatever is not in the table is not loaded: ERROR_MOD_NOT_FOUND.
 *
 * Name matching is the loader's search for an already loaded module: case-insensitive; a name without an extension
 * gets ".dll", a trailing '.' means "no extension"; a name without a path is matched by the module's file name, a
 * name with a path by the module's full path ('/' is '\'). */
#define EXE_MODULE ((HMODULE)(uintptr_t)0x00400000u)
#define MAX_MODULES 64

struct module {
    char file[64];                 /* "kernel32.dll", "atimgpud.dll" */
    fnvnx_module_resolver resolve; /* registered resolver, or NULL */
    int refs;                      /* registered modules: the loader's own reference + one per LoadLibrary */
    int system;                    /* pinned */
};
static struct module g_modules[MAX_MODULES];
static int g_nmodules;
static int g_modules_init;
static os_lock_t g_mod_lock;

extern "C" const char *fnvnx_win32_system_module(unsigned i);   /* exports.cpp */

static const char *base_name(const char *p)
{
    const char *b = p;
    for (; *p; p++)
        if (*p == '\\' || *p == '/' || *p == ':')
            b = p + 1;
    return b;
}

/* the file name a module name denotes: the default extension applied, '/' made '\'; 0 if it cannot name a module */
static int normalize(const char *in, char *out, size_t n, int *has_path)
{
    size_t len = strlen(in);
    if (len + 5 > n)
        return 0;
    *has_path = 0;
    for (size_t i = 0; i <= len; i++) {
        out[i] = in[i] == '/' ? '\\' : in[i];
        if (in[i] == '\\' || in[i] == '/' || in[i] == ':')
            *has_path = 1;
    }
    if (!strchr(base_name(out), '.'))
        strcat(out, ".dll");
    else if (len && out[len - 1] == '.')
        out[len - 1] = 0;
    return 1;
}

static void module_path(const struct module *m, char *out, size_t n)
{
    char dir[W32_PATH_MAX];
    snprintf(dir, sizeof dir, "%s", w32_exe_path());
    *(char *)base_name(dir) = 0;   /* the game directory, with its trailing '\' */
    snprintf(out, n, "%s%s", m->system ? "C:\\Windows\\system32\\" : dir, m->file);
}

static void init_locked(void)
{
    if (g_modules_init)
        return;
    g_modules_init = 1;
    for (unsigned i = 0; g_nmodules < MAX_MODULES; i++) {
        const char *n = fnvnx_win32_system_module(i);
        if (!n)
            break;
        struct module *m = &g_modules[g_nmodules++];
        snprintf(m->file, sizeof m->file, "%s.dll", n);
        m->system = 1;
    }
}

static struct module *find_locked(const char *file)
{
    for (int i = 0; i < g_nmodules; i++)
        if (!w32_stricmp(g_modules[i].file, file))
            return &g_modules[i];
    return NULL;
}

extern "C" void fnvnx_register_module(const char *dll, fnvnx_module_resolver resolve)
{
    char f[64];
    int hp;
    if (!normalize(base_name(dll), f, sizeof f, &hp))
        return;
    os_lock(&g_mod_lock);
    init_locked();
    struct module *m = find_locked(f);   /* a system DLL: its exports gain the resolver; the same name again: replaced */
    if (!m && g_nmodules < MAX_MODULES) {
        m = &g_modules[g_nmodules++];
        snprintf(m->file, sizeof m->file, "%s", f);
        m->refs = 1;
    }
    if (m)
        m->resolve = resolve;
    os_unlock(&g_mod_lock);
}

/* Resolver of a module registered with fnvnx_register_module (S7 audio/input, ...), for export lookups. */
extern "C" void *fnvnx_registered_module_export(const char *dll, const char *proc)
{
    fnvnx_module_resolver r = NULL;
    char f[64];
    int hp;
    if (dll && !normalize(base_name(dll), f, sizeof f, &hp))
        return NULL;
    os_lock(&g_mod_lock);
    init_locked();
    for (int i = 0; i < g_nmodules && !r; i++)
        if (g_modules[i].resolve && (!dll || !w32_stricmp(f, g_modules[i].file)))
            r = g_modules[i].resolve;
    os_unlock(&g_mod_lock);
    return r ? (void *)r(proc) : NULL;
}

/* the loaded module a name denotes; the exe, a table entry (taken with `take` references) or NULL */
static HMODULE module_lookup(const char *name, int take)
{
    char f[W32_PATH_MAX];
    int hp;
    if (!normalize(name, f, sizeof f, &hp))
        return NULL;
    char full[W32_PATH_MAX];
    snprintf(full, sizeof full, "%s", w32_exe_path());
    if (!w32_stricmp(f, hp ? full : base_name(full)))
        return EXE_MODULE;
    HMODULE h = NULL;
    os_lock(&g_mod_lock);
    init_locked();
    for (int i = 0; i < g_nmodules && !h; i++) {
        module_path(&g_modules[i], full, sizeof full);
        if (!w32_stricmp(f, hp ? full : g_modules[i].file)) {
            h = (HMODULE)&g_modules[i];
            if (take && !g_modules[i].system)
                g_modules[i].refs++;
        }
    }
    os_unlock(&g_mod_lock);
    return h;
}

/* the table entry of a handle (not the exe) or NULL */
static struct module *module_of(HMODULE h)
{
    struct module *m = (struct module *)h;
    uintptr_t d = (uintptr_t)h - (uintptr_t)g_modules;
    if (d >= sizeof g_modules || d % sizeof g_modules[0])
        return NULL;
    return (int)(m - g_modules) < g_nmodules ? m : NULL;
}

extern "C" int fnvnx_module_load_count(HMODULE h)
{
    os_lock(&g_mod_lock);
    struct module *m = module_of(h);
    int n = !m ? 0 : m->system ? -1 : m->refs;
    os_unlock(&g_mod_lock);
    return n;
}

extern "C" HMODULE GetModuleHandleA(LPCSTR name)
{
    if (!name)
        return EXE_MODULE;
    HMODULE h = module_lookup(name, 0);
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
    HMODULE h = module_lookup(name, 1);
    if (!h) {
        w32_log("LoadLibraryA(\"%s\"): no such module on this platform", name);
        SetLastError(ERROR_MOD_NOT_FOUND);
    }
    return h;
}

/* A reference is dropped down to the loader's own, which stays: translated code is never unmapped. */
extern "C" BOOL FreeLibrary(HMODULE h)
{
    if (h == EXE_MODULE)
        return TRUE;
    os_lock(&g_mod_lock);
    struct module *m = module_of(h);
    if (m && !m->system && m->refs > 1)
        m->refs--;
    os_unlock(&g_mod_lock);
    if (!m)
        SetLastError(ERROR_INVALID_HANDLE);
    return m != NULL;
}

/* Without per-thread attach notifications to suppress this only validates the handle (ERROR_MOD_NOT_FOUND). */
extern "C" BOOL DisableThreadLibraryCalls(HMODULE h)
{
    os_lock(&g_mod_lock);
    int ok = h == EXE_MODULE || module_of(h);
    os_unlock(&g_mod_lock);
    if (!ok)
        SetLastError(ERROR_MOD_NOT_FOUND);
    return ok;
}

extern "C" FARPROC GetProcAddress(HMODULE h, LPCSTR proc)
{
    FARPROC f = NULL;
    char dll[64] = "";
    int known = h == EXE_MODULE;
    os_lock(&g_mod_lock);
    struct module *m = module_of(h);
    if (m) {
        known = 1;
        snprintf(dll, sizeof dll, "%s", m->file);
    }
    os_unlock(&g_mod_lock);
    if (!known || !proc) {
        SetLastError(known ? ERROR_PROC_NOT_FOUND : ERROR_INVALID_HANDLE);
        return NULL;
    }
    if (m) {
        /* by name, or by ordinal ((uintptr_t)proc <= 0xFFFF): the import binding's lookup */
        if ((uintptr_t)proc > 0xFFFF)
            f = (FARPROC)fnvnx_win32_lookup_import(dll, proc, FNVNX_IMPORT_BY_NAME);
        else if ((uintptr_t)proc)
            f = (FARPROC)fnvnx_win32_lookup_import(dll, NULL, (unsigned)(uintptr_t)proc);
        if (f && (uintptr_t)proc > 0xFFFF && w32_mem_on)
            f = w32_mem_hook_proc(proc, f);   /* the memory lane: D3D9 resources (memtrack.cpp) */
    }
    if (!f)
        SetLastError(ERROR_PROC_NOT_FOUND);
    return f;
}

extern "C" DWORD GetModuleFileNameA(HMODULE h, LPSTR buf, DWORD n)
{
    n = W32_DW(n);
    char tmp[W32_PATH_MAX];
    if (!h || h == EXE_MODULE) {
        snprintf(tmp, sizeof tmp, "%s", w32_exe_path());
    } else {
        os_lock(&g_mod_lock);
        struct module *m = module_of(h);
        if (m)
            module_path(m, tmp, sizeof tmp);
        os_unlock(&g_mod_lock);
        if (!m) {
            SetLastError(ERROR_MOD_NOT_FOUND);
            return 0;
        }
    }
    const char *path = tmp;
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

/* DebugBreak: EXCEPTION_BREAKPOINT raised by the platform; it returns when the exception was continued. */
extern "C" void DebugBreak(void) { w32_raise(0x80000003u, 0, 0, NULL); }

/* FatalAppExitA: the documented sequence - a system-modal message box with the text and an OK button, then the
 * process ends (ExitProcess(0)). The action argument is reserved and must be 0. */
extern "C" void FatalAppExitA(UINT action, LPCSTR msg)
{
    W32_UNUSED(action);
    MessageBoxA(NULL, msg, NULL, 0x1000 /* MB_SYSTEMMODAL | MB_OK */);
    ExitProcess(0);
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

/* the game build's toolchain/game/boot.cpp reads unmapped image constants from the exe; elsewhere p as it is */
extern "C" __attribute__((weak)) const void *fnvnx_image_const(const void *p, uint32_t n, void *buf)
{
    (void)n;
    (void)buf;
    return p;
}
