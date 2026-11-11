/* module.cpp - modules, command line, process exit, debugging and small kernel32 helpers. */
#include "w32.h"
#include "fnvnx_boundary.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- modules. ONE table, ONE rule: a module is (a) the exe, whose HMODULE is its original image base where the
 * loader (S3) maps the data image, (b) a system DLL - one per distinct DLL name of the export table
 * (fnvnx_win32_system_module), all "loaded" and pinned - or (c) a translated game module registered by the loader
 * with fnvnx_register_module, or (d) a translated game module / the exe, whose image the loader reports with
 * fnvnx_win32_register_image: its HMODULE is its base address and its exports are read from the PE export directory
 * in memory. A translated module that is not loaded yet is loaded by the loader (fnvnx_bnd_module_load). Whatever is
 * not in the table and not loadable is not loaded: ERROR_MOD_NOT_FOUND.
 *
 * Name matching is the loader's search for an already loaded module: case-insensitive; a name without an extension
 * gets ".dll", a trailing '.' means "no extension"; a name without a path is matched by the module's file name, a
 * name with a path by the module's full path ('/' is '\'). */
#define EXE_BASE_DEFAULT 0x00400000u   /* the exe's HMODULE until the loader registers its image */
#define MAX_MODULES 128

struct module {
    char file[64];                 /* "kernel32.dll", "atimgpud.dll" */
    fnvnx_module_resolver resolve; /* registered resolver, or NULL */
    int refs;                      /* registered modules: the loader's own reference + one per LoadLibrary */
    int system;                    /* pinned */
    int image;                     /* a registered image: HMODULE = base, exports from its PE export directory */
    uint32_t base, size;           /* the image */
};
static struct module g_modules[MAX_MODULES];
static int g_nmodules;
static int g_modules_init;
static os_lock_t g_mod_lock;
extern "C" HMODULE fnvnx_win32_exe_module(void);
static uint32_t g_exe_base = EXE_BASE_DEFAULT, g_exe_size;   /* the exe's image (size 0: not registered) */
#define EXE_MODULE ((HMODULE)(uintptr_t)g_exe_base)

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
    snprintf(dir, sizeof dir, "%s", W32_EXE_PATH);
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
    snprintf(full, sizeof full, "%s", W32_EXE_PATH);
    if (!w32_stricmp(f, hp ? full : base_name(full)))
        return EXE_MODULE;
    HMODULE h = NULL;
    os_lock(&g_mod_lock);
    init_locked();
    for (int i = 0; i < g_nmodules && !h; i++) {
        module_path(&g_modules[i], full, sizeof full);
        if (!w32_stricmp(f, hp ? full : g_modules[i].file)) {
            h = g_modules[i].image ? (HMODULE)(uintptr_t)g_modules[i].base : (HMODULE)&g_modules[i];
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
    for (int i = 0; i < g_nmodules; i++)
        if (g_modules[i].image && (uintptr_t)g_modules[i].base == (uintptr_t)h)
            return &g_modules[i];
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
    if (h)
        return h;
    /* not loaded: a translated module is loaded by the loader (it registers the image); anything else is absent */
    uint32_t base = 0;
    uint32_t err = fnvnx_bnd_module_load(name, &base);
    if (!err) {
        h = module_lookup(name, 1);
        if (!h)
            err = ERROR_DLL_INIT_FAILED;   /* the loader reported success without registering the image */
    }
    if (err) {
        w32_log("LoadLibraryA(\"%s\"): error %u", name, (unsigned)err);
        SetLastError(err);
        return NULL;
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

/* ---- the PE export directory of a registered image, read from memory (every offset bounds-checked against the
 * image: a malformed directory is "not found"). 0: found (*rva = the export's address, or *fwd = its forwarder string,
 * NUL-terminated inside the image); 1: not found. */
static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint32_t rd16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static bool in_img(uint32_t n, uint64_t off, uint64_t len) { return off <= n && len <= n - off; }

static int pe_export(uint32_t base, uint32_t n, LPCSTR proc, uint32_t *rva, const char **fwd)
{
    const uint8_t *b = (const uint8_t *)(uintptr_t)base;
    *fwd = NULL;
    if (!in_img(n, 0, 0x40) || rd16(b) != 0x5A4D)
        return 1;
    uint64_t pe = rd32(b + 0x3C), opt = pe + 24;
    if (!in_img(n, pe, 24) || rd32(b + pe) != 0x4550 || !in_img(n, opt, 96 + 8) || rd16(b + opt) != 0x10B ||
        rd32(b + opt + 92) < 1)
        return 1;
    uint32_t drva = rd32(b + opt + 96), dsize = rd32(b + opt + 100);
    if (!drva || !dsize || !in_img(n, drva, 40))
        return 1;
    const uint8_t *d = b + drva;
    uint32_t obase = rd32(d + 16), nfun = rd32(d + 20), nnam = rd32(d + 24), af = rd32(d + 28), an = rd32(d + 32),
             ao = rd32(d + 36);
    uint64_t idx;
    if ((uintptr_t)proc <= 0xFFFF) {
        uint32_t ord = (uint32_t)(uintptr_t)proc;
        if (ord < obase)
            return 1;
        idx = ord - obase;
    } else {
        bool found = false;
        idx = 0;
        if (!in_img(n, an, 4ull * nnam) || !in_img(n, ao, 2ull * nnam))
            return 1;
        size_t pl = strlen(proc);
        for (uint32_t i = 0; i < nnam && !found; i++) {
            uint32_t nr = rd32(b + an + 4ull * i);
            if (!in_img(n, nr, pl + 1) || memcmp(b + nr, proc, pl + 1))
                continue;
            idx = rd16(b + ao + 2ull * i);
            found = true;
        }
        if (!found)
            return 1;
    }
    if (idx >= nfun || !in_img(n, af, 4ull * nfun))
        return 1;
    uint32_t r = rd32(b + af + 4 * idx);
    if (!r || !in_img(n, r, 1))
        return 1;
    if (r >= drva && r - drva < dsize) {   /* a forwarder: "DLL.Name" or "DLL.#ordinal" inside the directory */
        uint32_t len = 0;
        while (r + len < n && b[r + len])
            len++;
        if (r + len >= n)
            return 1;
        *fwd = (const char *)b + r;
        return 0;
    }
    *rva = r;
    return 0;
}

static FARPROC proc_address(HMODULE h, LPCSTR proc, int depth);

/* a forwarder string "DLL.Name" / "DLL.#N": the target module is loaded (as the loader does when it resolves the
 * forward), then looked up by name or ordinal; a cycle of forwarders ends at depth 16 */
static FARPROC follow_forwarder(const char *f, int depth)
{
    const char *dot = strchr(f, '.');
    char mod[64];
    if (!dot || dot == f || (size_t)(dot - f) >= sizeof mod || !dot[1] || depth >= 16) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return NULL;
    }
    memcpy(mod, f, (size_t)(dot - f));
    mod[dot - f] = 0;
    HMODULE t = LoadLibraryA(mod);
    if (!t)
        return NULL;
    LPCSTR pn = dot + 1;
    if (*pn == '#') {
        unsigned long o = strtoul(pn + 1, NULL, 10);
        if (!o || o > 0xFFFF) {
            SetLastError(ERROR_PROC_NOT_FOUND);
            return NULL;
        }
        pn = (LPCSTR)(uintptr_t)o;
    }
    return proc_address(t, pn, depth + 1);
}

static FARPROC proc_address(HMODULE h, LPCSTR proc, int depth)
{
    FARPROC f = NULL;
    char dll[64] = "";
    uint32_t ibase = 0, isize = 0;
    int known = h == EXE_MODULE;
    os_lock(&g_mod_lock);
    struct module *m = module_of(h);
    if (m) {
        known = 1;
        snprintf(dll, sizeof dll, "%s", m->file);
        if (m->image) {
            ibase = m->base;
            isize = m->size;
        }
    } else if (known && g_exe_size) {
        ibase = g_exe_base;
        isize = g_exe_size;
    }
    os_unlock(&g_mod_lock);
    if (!known || !proc) {
        SetLastError(known ? ERROR_PROC_NOT_FOUND : ERROR_INVALID_HANDLE);
        return NULL;
    }
    if (isize) {   /* a registered image: its own export directory */
        uint32_t rva = 0;
        const char *fwd = NULL;
        if (pe_export(ibase, isize, proc, &rva, &fwd)) {
            SetLastError(ERROR_PROC_NOT_FOUND);
            return NULL;
        }
        return fwd ? follow_forwarder(fwd, depth) : (FARPROC)(uintptr_t)(ibase + rva);
    }
    if (m) {
        /* by name, or by ordinal ((uintptr_t)proc <= 0xFFFF): the import binding's lookup */
        const char *nm = (uintptr_t)proc > 0xFFFF ? proc : NULL;
        unsigned ord = nm ? FNVNX_IMPORT_BY_NAME : (unsigned)(uintptr_t)proc;
        f = (FARPROC)fnvnx_win32_lookup_import(dll, nm, ord);
        if (f && nm && w32_mem_on)
            f = w32_mem_hook_proc(proc, f);   /* the memory lane: D3D9 resources (memtrack.cpp) */
        /* the value the game gets for a platform function: its import key (product), its address (host tests) */
        if (f)
            f = (FARPROC)(uintptr_t)fnvnx_bnd_export_value(dll, nm, ord, (const void *)f);
    }
    if (!f)
        SetLastError(ERROR_PROC_NOT_FOUND);
    return f;
}

extern "C" FARPROC GetProcAddress(HMODULE h, LPCSTR proc) { return proc_address(h, proc, 0); }

/* ---- images (fnvnx_win32_ext.h) */
extern "C" HMODULE fnvnx_win32_exe_module(void) { return (HMODULE)(uintptr_t)g_exe_base; }
static unsigned prot_of(unsigned rwx)
{
    if (rwx & 4)
        return rwx & 2 ? 0x40 /* PAGE_EXECUTE_READWRITE */ : rwx & 1 ? 0x20 /* PAGE_EXECUTE_READ */ : 0x10 /* PAGE_EXECUTE */;
    return rwx & 2 ? 0x04 /* PAGE_READWRITE */ : rwx & 1 ? 0x02 /* PAGE_READONLY */ : 0x01 /* PAGE_NOACCESS */;
}

extern "C" void fnvnx_resource_register_image(HMODULE m, const void *base, size_t size);   /* resource.cpp */

extern "C" void fnvnx_win32_register_image(const char *name, uint32_t base, uint32_t size)
{
    char f[64], exe[W32_PATH_MAX];
    int hp;
    if (!normalize(base_name(name), f, sizeof f, &hp))
        return;
    snprintf(exe, sizeof exe, "%s", W32_EXE_PATH);
    HMODULE h = (HMODULE)(uintptr_t)base;
    os_lock(&g_mod_lock);
    init_locked();
    if (!w32_stricmp(f, base_name(exe))) {   /* the process image: the HMODULE of GetModuleHandle(NULL) */
        g_exe_base = base;
        g_exe_size = size;
    } else {
        struct module *m = find_locked(f);   /* a system DLL's name, or the same module again: its image is replaced */
        if (!m && g_nmodules < MAX_MODULES) {
            m = &g_modules[g_nmodules++];
            snprintf(m->file, sizeof m->file, "%s", f);
            m->refs = 1;
        }
        if (!m) {
            os_unlock(&g_mod_lock);
            return;
        }
        m->image = 1;
        m->base = base;
        m->size = size;
    }
    os_unlock(&g_mod_lock);
    fnvnx_resource_register_image(h, (const void *)(uintptr_t)base, size);
    /* the page map: the whole image, then each range of it with its protection */
    fnvnx_win32_vm_note(base, size, MEM_IMAGE, PAGE_READONLY);
    struct fnvnx_bnd_range r[96];
    unsigned n = fnvnx_bnd_image_ranges(base, r, 96);
    for (unsigned i = 0; i < n && i < 96; i++)
        if (r[i].va >= base && r[i].size && r[i].size <= size && r[i].va - base <= size - r[i].size)
            fnvnx_win32_vm_note(r[i].va, r[i].size, MEM_IMAGE, prot_of(r[i].prot));
}

extern "C" DWORD GetModuleFileNameA(HMODULE h, LPSTR buf, DWORD n)
{
    n = W32_DW(n);
    char tmp[W32_PATH_MAX];
    if (!h || h == EXE_MODULE) {
        snprintf(tmp, sizeof tmp, "%s", W32_EXE_PATH);
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
    size_t n = strlen(W32_EXE_PATH) + 3;
    for (int i = 1; i < argc; i++)
        n += strlen(argv[i]) * 2 + 3;
    char *s = (char *)malloc(n + 1);
    if (!s)
        return;
    size_t o = (size_t)sprintf(s, "\"%s\"", W32_EXE_PATH);
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
        const char *exe = W32_EXE_PATH;
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
    fnvnx_bnd_process_detach();   /* DllMain(DLL_PROCESS_DETACH) of the translated modules */
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

/* answered from the page map for every byte of the range; a guard page makes the probe fail and consumes the guard
 * (the probe's own access is the first one). IsBadCodePtr probes one byte for read, as Windows does. */
extern "C" BOOL IsBadCodePtr(FARPROC p) { return !w32_vm_probe((const void *)p, 1, 8); }
extern "C" BOOL IsBadReadPtr(const void *p, UINT_PTR n) { return !w32_vm_probe(p, (size_t)n, 0); }
extern "C" BOOL IsBadWritePtr(LPVOID p, UINT_PTR n) { return !w32_vm_probe(p, (size_t)n, 1); }

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

