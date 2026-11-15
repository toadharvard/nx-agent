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
    HMODULE h = module_lookup(name, 0);
    if (h) {
        /* a translated image the platform holds no explicit load of (count 0: kept alive only as a dependency, or loaded
         * by the loader's own start) is held now, by the loader: the platform's count is the loader's hold */
        char nf[W32_PATH_MAX];
        int nhp;
        bool hold = false;
        os_lock(&g_mod_lock);
        struct module *hm = h == EXE_MODULE ? NULL : module_of(h);
        hold = hm && hm->image && !hm->system && hm->refs == 1;
        os_unlock(&g_mod_lock);
        if (hold && normalize(name, nf, sizeof nf, &nhp)) {
            uint32_t hb = 0;
            DWORD he = fnvnx_bnd_module_load(base_name(nf), &hb);
            if (he && he != ERROR_MOD_NOT_FOUND) {   /* MOD_NOT_FOUND: not in the loader's table, nothing of the loader's to hold */
                SetLastError(he);
                return NULL;
            }
        }
        return module_lookup(name, 1);
    }
    /* not loaded: a translated module is loaded by the loader (it registers the image); anything else is absent */
    /* the loader's table is by file name: a path must be the game directory's (the place translated modules live) */
    char f[W32_PATH_MAX], dir[W32_PATH_MAX];
    int hp;
    uint32_t base = 0, err = ERROR_MOD_NOT_FOUND;
    if (normalize(name, f, sizeof f, &hp)) {
        snprintf(dir, sizeof dir, "%s", W32_EXE_PATH);
        *(char *)base_name(dir) = 0;   /* the game directory, with its trailing '\\' */
        size_t dl = strlen(dir);
        if (!hp || (!w32_strnicmp(f, dir, dl) && f[dl] && !strpbrk(f + dl, "\\:")))
            err = fnvnx_bnd_module_load(base_name(f), &base);
    }
    if (!err) {
        h = module_lookup(name, 1);
        if (!h)
            err = ERROR_DLL_INIT_FAILED;   /* the loader reported success without registering the image */
    }
    if (err) {
        W32Log().s("LoadLibraryA(\"").s(name).s("\"): error ").u((unsigned)err).emit();
        SetLastError(err);
        return NULL;
    }
    return h;
}

extern "C" void fnvnx_resource_register_image(HMODULE m, const void *base, size_t size);   /* resource.cpp */

/* FreeLibrary. A reference is dropped; the platform's own count of a translated image (refs - 1: the extra one is the
 * loader's registration) reaching 0 hands the module to the loader (fnvnx_bnd_module_unload), whose rule is that of the
 * Windows loader: UNLOADED removes the image from the module table and its pages from the page map, KEPT leaves it
 * loaded (a live module imports it) at count 0, still found by GetModuleHandle; PINNED (a static module) changes
 * nothing and FreeLibrary still succeeds. A refusal of the loader is a bad handle (ERROR_INVALID_HANDLE) or a module this
 * process holds no load of (ERROR_MOD_NOT_FOUND). The exe, the system DLLs and resolver modules are never unloaded. */
extern "C" BOOL FreeLibrary(HMODULE h)
{
    if (h == EXE_MODULE)
        return TRUE;
    os_lock(&g_mod_lock);
    struct module *m = module_of(h);
    if (!m) {
        os_unlock(&g_mod_lock);
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    if (m->system || !m->image) {
        if (!m->system && m->refs > 1)
            m->refs--;
        os_unlock(&g_mod_lock);
        return TRUE;
    }
    uint32_t base = m->base, size = m->size;
    if (m->refs > 1)
        m->refs--;
    bool unload = m->refs == 1;
    os_unlock(&g_mod_lock);
    if (!unload)
        return TRUE;
    int outcome = FNVNX_BND_KEPT;
    DWORD e = fnvnx_bnd_module_unload(base, &outcome);
    if (e) {
        SetLastError(e);
        return FALSE;
    }
    if (outcome == FNVNX_BND_UNLOADED) {
        os_lock(&g_mod_lock);
        m = module_of(h);
        if (m && m->image && m->base == base && m->refs == 1) {
            m->image = 0;
            m->file[0] = 0;
            m->base = m->size = 0;
            m->refs = 0;
        }
        os_unlock(&g_mod_lock);
        fnvnx_resource_register_image(h, NULL, 0);
        fnvnx_win32_vm_unnote(base, size);
    }
    return TRUE;
}

/* The exe and the platform's own modules have no per-thread notifications to suppress: the handle is only validated. A
 * translated module (a registered image) is told to the loader (fnvnx_bnd_disable_thread_calls). */
extern "C" BOOL DisableThreadLibraryCalls(HMODULE h)
{
    os_lock(&g_mod_lock);
    struct module *m = h == EXE_MODULE ? NULL : module_of(h);
    int ok = h == EXE_MODULE || m != NULL;
    uint32_t base = m && m->image ? m->base : 0;
    os_unlock(&g_mod_lock);
    if (!ok) {
        SetLastError(ERROR_MOD_NOT_FOUND);
        return FALSE;
    }
    if (base) {
        DWORD err = fnvnx_bnd_disable_thread_calls(base);
        if (err) {
            SetLastError(err);
            return FALSE;
        }
    }
    return TRUE;
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
        /* the value the game gets for a function: its import key (product), its address (host tests). Asked also when the
         * platform tables have no body (f NULL): a bridge row (any bind: its body is bound at build, e.g. L6's d3d9 `sym`
         * rows) is a function GetProcAddress can return; no row -> 0 -> ERROR_PROC_NOT_FOUND below. */
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

/* The ranges of the image mapped at `base`, from its own PE section table -- the rule the loader maps by: the headers
 * read-only, each section with the protection of its R/W/X characteristics, the gaps between them no-access. Every
 * range is clipped to the image; a malformed table records nothing beyond the whole image already noted. */
static void note_sections(uint32_t base, uint32_t size)
{
    const uint8_t *b = (const uint8_t *)(uintptr_t)base;
    if (!in_img(size, 0, 0x40) || rd16(b) != 0x5A4D)
        return;
    uint64_t pe = rd32(b + 0x3C), opt = pe + 24;
    if (!in_img(size, pe, 24) || rd32(b + pe) != 0x4550 || !in_img(size, opt, 64) || rd16(b + opt) != 0x10B)
        return;
    uint32_t nsec = rd16(b + pe + 6), soh = rd32(b + opt + 60), cur = 0;
    uint64_t sh = opt + rd16(b + pe + 20);
    auto pg = [](uint64_t v) { return (v + 0xFFFu) & ~(uint64_t)0xFFFu; };
    auto note = [&](uint64_t rva, uint64_t len, unsigned rwx) {
        if (len && in_img(size, rva, len))
            fnvnx_win32_vm_note(base + (uintptr_t)rva, (size_t)len, MEM_IMAGE, prot_of(rwx));
    };
    cur = (uint32_t)(pg(soh) > size ? size : pg(soh));
    note(0, cur, 1);
    for (uint32_t i = 0; i < nsec && in_img(size, sh + 40ull * i, 40); i++) {
        const uint8_t *h = b + sh + 40ull * i;
        uint64_t rva = rd32(h + 12), len = pg(rd32(h + 8) > rd32(h + 16) ? rd32(h + 8) : rd32(h + 16)), ch = rd32(h + 36);
        if (rva > cur)
            note(cur, rva - cur, 0);
        note(rva, len, (ch & 0x40000000u ? 1u : 0u) | (ch & 0x80000000u ? 2u : 0u) | (ch & 0x20000000u ? 4u : 0u));
        if (rva + len > cur)
            cur = (uint32_t)(rva + len);
    }
    if (pg(size) > cur)
        note(cur, pg(size) - cur, 0);
}

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
        for (int i = 0; i < g_nmodules && !m; i++)   /* a slot an unloaded module left */
            if (!g_modules[i].file[0] && !g_modules[i].system && !g_modules[i].image && !g_modules[i].resolve) {
                m = &g_modules[i];
                snprintf(m->file, sizeof m->file, "%s", f);
                m->refs = 1;
            }
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
    note_sections(base, size);
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
    W32Log().s("ExitProcess(").u((unsigned)code).s(")").emit();
    w32_fls_thread_exit();   /* the calling thread's FLS callbacks, before the modules detach (fls.cpp) */
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

extern "C" void *fnvnx_resource_find_exact(HMODULE, LPCSTR, LPCSTR, WORD);   /* resource.cpp */
extern "C" void *LoadResource(HMODULE, void *);
extern "C" DWORD SizeofResource(HMODULE, void *);
#ifndef ERROR_NO_WORK_DONE
#define ERROR_NO_WORK_DONE 1237L
#endif
#ifndef ERROR_NOT_ENOUGH_MEMORY
#define ERROR_NOT_ENOUGH_MEMORY 8L
#endif
/* ---- FormatMessageA.
 * Message sources: FROM_STRING (the string wins over the other two), FROM_HMODULE (the RT_MESSAGETABLE resource, name 1,
 * of the module: how the system finds it), FROM_SYSTEM (the reference environment's English table, generated from
 * Wine 9.0's winerror.mc into winerr_msgs.inc: language 0x0409 only; an HRESULT of FACILITY_WIN32 with severity error
 * is folded to its Win32 code first). Formatting follows the Windows 7 rules recorded by Wine's conformance tests
 * (kernel32/tests/format_msg.c): inserts %1..%99 with optional !printf spec!, %0 ends the text, %n %r %t, %% %. %! %space
 * give the character, line breaks of the source are CRLF (a space under a width), MAX_WIDTH_MASK wraps at the width.
 * Arguments are 32-bit words (the ILP32 guest's va_list / DWORD_PTR array); a pointer argument is such a word. */
#include "winerr_msgs.inc"

namespace {
struct fm_args {
    int last;                 /* argument slots fetched so far */
    const uint32_t *array;    /* ARGUMENT_ARRAY: the array */
    const uint32_t *list;     /* va_list: the next unread word */
    bool have;
    uint64_t slot[102];
    char *tmp, *conv;         /* FM_TMP bytes each */
};
#define FM_TMP 33000

/* the argument slot nr (1-based; -1: the slot after the last fetched); a va_list is read forward as it is needed */
uint64_t fm_fetch(fm_args *a, int nr, bool is64)
{
    if (nr == -1)
        nr = a->last + 1;
    while (nr > a->last && a->last < 100) {
        uint64_t v = *a->list++;
        if (is64)
            v |= (uint64_t)*a->list++ << 32;
        a->slot[a->last++] = v;
    }
    return a->slot[nr - 1 < 0 ? 0 : (nr - 1 > 100 ? 100 : nr - 1)];
}

const char *fm_ptr(uint32_t w) { return (const char *)(uintptr_t)w; }

/* narrow a UTF-16 string / character for the ANSI output */
void fm_narrow(const WCHAR *w, char *out, size_t n)
{
    int k = WideCharToMultiByte(CP_ACP, 0, w, -1, out, (int)n, NULL, NULL);
    if (k <= 0)
        out[0] = 0;
}

/* one insert: %N or %N!spec!; returns false (INVALID_PARAMETER) on a malformed or unsupported spec, or the text overflowing */
enum { FM_OK, FM_BAD, FM_FULL };
int fm_insert(char *buf, size_t cap, size_t *pos, const char **srcp, int insert, fm_args *a)
{
    const char *f = *srcp;
    char flags[8] = "";
    int nflags = 0, width = 0, prec = -1, stars = 0, star_w = 0, star_p = 0;
    bool wide = false, is64 = false, have_prec = false;
    char type = 's';
    if (*f++ == '!') {
        const char *end = strchr(f, '!');
        if (!end || end - f > 30)
            return FM_BAD;
        *srcp = end + 1;
        for (; f < end && strchr(" +-#0", *f); f++)
            if (nflags < 6)
                flags[nflags++] = *f;
        flags[nflags] = 0;
        if (*f == '*') {
            star_w = ++stars;
            f++;
        } else
            for (; f < end && *f >= '0' && *f <= '9'; f++) {
                width = width * 10 + (*f - '0');
                if (width > 32768)
                    return FM_FULL;
            }
        if (f < end && *f == '.') {
            f++;
            have_prec = true;
            prec = 0;
            if (*f == '*') {
                star_p = ++stars;
                f++;
            } else
                for (; f < end && *f >= '0' && *f <= '9'; f++) {
                    prec = prec * 10 + (*f - '0');
                    if (prec > 32768)
                        return FM_FULL;
                }
        }
        if (stars > 2)
            return FM_BAD;
        /* size prefix: h l ll w I I32 I64 */
        if (f < end && *f == 'h') { wide = false; f++; }
        else if (f < end && *f == 'l') { wide = true; f++; if (f < end && *f == 'l') { is64 = true; f++; } }
        else if (f < end && *f == 'w') { wide = true; f++; }
        else if (f < end && *f == 'I') {
            f++;
            if (end - f >= 2 && f[0] == '6' && f[1] == '4') { is64 = true; f += 2; }
            else if (end - f >= 2 && f[0] == '3' && f[1] == '2') f += 2;
        }
        if (f + 1 != end || !strchr("cCsSdiuxXop", *f))
            return FM_BAD;
        type = *f;
        if (type == 'S' || type == 'C')
            wide = true;   /* in an ANSI message %S / %C are the wide forms */
    }       /* "%N": the string argument, *srcp stays at the character after the number */
    if (!a->have)
        return FM_BAD;
    /* the printf argument vector: star values, then the value (see the model in the tests: Windows' slot rules) */
    uint64_t av[3];
    int nav = 0;
    if (a->list) {
        fm_fetch(a, insert - 1, is64);
        int use = insert;
        for (int i = 0; i < stars; i++, use = -1)
            av[nav++] = fm_fetch(a, use, false);
        if (use == -1)
            a->last--;      /* Windows drops one slot after a star: the value is the next word */
        av[nav++] = fm_fetch(a, use, is64);
    } else {
        if (insert > 99)
            return FM_BAD;
        av[nav++] = a->array[insert - 1];
        if (a->last < insert)
            a->last = insert;
        for (int i = 0; i < stars; i++)
            av[nav++] = a->array[a->last++];
    }
    /* when stars are used the first arguments are width/precision */
    int vi = 0;
    if (star_w) {
        int w = (int)(int32_t)(uint32_t)av[vi++];
        if (w < 0) {
            if (nflags < 6) flags[nflags++] = '-';
            flags[nflags] = 0;
            w = -w;
        }
        width = w;
    }
    if (star_p) {
        int p = (int)(int32_t)(uint32_t)av[vi++];
        prec = p < 0 ? -1 : p;
        have_prec = p >= 0;
    }
    uint64_t val = av[vi];
    char *tmp = a->tmp, *conv = a->conv;
    const size_t TN = FM_TMP;
    char fmt[24];
    int n = 0;
    switch (type) {
    case 'd': case 'i':
        snprintf(fmt, sizeof fmt, "%%%s*.*%s", flags, is64 ? "lld" : "d");
        n = is64 ? snprintf(tmp, TN, fmt, width, have_prec ? prec : -1, (long long)val)
                 : snprintf(tmp, TN, fmt, width, have_prec ? prec : -1, (int)(int32_t)(uint32_t)val);
        break;
    case 'u': case 'x': case 'X': case 'o':
        snprintf(fmt, sizeof fmt, "%%%s*.*%s%c", flags, is64 ? "ll" : "", type);
        n = is64 ? snprintf(tmp, TN, fmt, width, have_prec ? prec : -1, (unsigned long long)val)
                 : snprintf(tmp, TN, fmt, width, have_prec ? prec : -1, (unsigned)(uint32_t)val);
        break;
    case 'p':
        snprintf(fmt, sizeof fmt, "%%%s*.*s", flags);
        snprintf(conv, FM_TMP, "%08X", (unsigned)(uint32_t)val);
        n = snprintf(tmp, TN, fmt, width, -1, conv);
        break;
    case 'c': case 'C': {
        char one[8];
        if (wide) {
            WCHAR w2[2] = { (WCHAR)(uint16_t)val, 0 };
            fm_narrow(w2, one, sizeof one);
        } else {
            one[0] = (char)val;
            one[1] = 0;
        }
        snprintf(fmt, sizeof fmt, "%%%s*s", flags);
        n = snprintf(tmp, TN, fmt, width, one[0] ? one : "");
        if (!one[0] && n >= 0) {            /* a NUL character prints as nothing, its field is still padded */
            n = snprintf(tmp, TN, fmt, width, "");
        }
        break;
    }
    default: { /* 's' 'S' */
        const char *s;
        if (wide) {
            const WCHAR *w = (const WCHAR *)(uintptr_t)(uint32_t)val;
            if (!w)
                s = "(null)";
            else {
                fm_narrow(w, conv, FM_TMP);
                s = conv;
            }
        } else
            s = val ? fm_ptr((uint32_t)val) : "(null)";
        snprintf(fmt, sizeof fmt, "%%%s*.*s", flags);
        n = snprintf(tmp, TN, fmt, width, have_prec ? prec : -1, s);
        break;
    }
    }
    if (n < 0 || (size_t)n >= TN || *pos + (size_t)n > cap - 1)
        return FM_FULL;
    memcpy(buf + *pos, tmp, (size_t)n);
    *pos += (size_t)n;
    return FM_OK;
}

/* the text of the message with its inserts; returns FM_OK, FM_BAD or FM_FULL; the result in buf (NUL-terminated) */
int fm_format(const char *src, unsigned long width, bool ignore_inserts, fm_args *a, char *buf, size_t cap)
{
    size_t pos = 0, line = 0;
    long space = -1;                      /* the last space of the line, -1: none */
    auto put = [&](const char *s, size_t k) -> bool {
        if (k > cap - pos)
            return false;
        memcpy(buf + pos, s, k);
        pos += k;
        return true;
    };
    for (; *src; src++) {
        bool ok = true;
        switch (*src) {
        case '\r':
            if (src[1] == '\n')
                src++;
            /* fall through */
        case '\n':
            if (!width) {
                ok = put("\r\n", 2);
                line = pos;
                space = -1;
                break;
            }
            /* fall through */
        case ' ':
            space = (long)pos;
            ok = put(" ", 1);
            break;
        case '\t':
            if (space == (long)pos - 1)
                space = (long)pos;
            ok = put("\t", 1);
            break;
        case '%':
            src++;
            switch (*src) {
            case 0:
                return FM_BAD;
            case 't':
                if (!width) {
                    ok = put("\t", 1);
                    break;
                }
                /* fall through */
            case 'n':
                ok = put("\r\n", 2);
                line = pos;
                space = -1;
                break;
            case 'r':
                ok = put("\r", 1);
                line = pos;
                space = -1;
                break;
            case '0':
                while (src[1])
                    src++;
                break;
            case '1': case '2': case '3': case '4': case '5': case '6': case '7': case '8': case '9':
                if (!ignore_inserts) {
                    int nr = *src++ - '0';
                    if (*src >= '0' && *src <= '9')
                        nr = nr * 10 + *src++ - '0';
                    int r = fm_insert(buf, cap, &pos, &src, nr, a);
                    if (r)
                        return r;
                    src--;
                    break;
                }
                /* fall through */
            default:
                ok = ignore_inserts ? put(src - 1, 2) : put(src, 1);
                break;
            }
            break;
        default:
            ok = put(src, 1);
            break;
        }
        if (!ok)
            return FM_FULL;
        if (width && pos - line >= width) {
            long diff = 2;
            size_t sp, next;
            if (space >= 0) {                         /* split at the last space */
                sp = (size_t)space;
                next = sp + 1;
                while (sp > line && (buf[sp - 1] == ' ' || buf[sp - 1] == '\t'))
                    sp--;
                diff -= (long)(next - sp);
            } else
                sp = next = pos;                      /* split at the end */
            if (diff > 0 && cap - pos < (size_t)diff)
                return FM_FULL;
            memmove(buf + sp + 2, buf + next, pos - next);
            pos = (size_t)((long)pos + diff);
            memcpy(buf + sp, "\r\n", 2);
            line = sp + 2;
            space = -1;
        }
    }
    if (pos >= cap)
        return FM_FULL;
    buf[pos] = 0;
    return FM_OK;
}

/* a message-table entry {USHORT Length, USHORT Flags, text}: copy the text as ANSI into out */
bool fm_entry_text(const uint8_t *e, const uint8_t *end, char *out, size_t n)
{
    if (end - e < 4)
        return false;
    unsigned len = e[0] | (e[1] << 8), flags = e[2] | (e[3] << 8);
    if (len < 4 || (size_t)(end - e) < len)
        return false;
    size_t tl = len - 4;
    if (flags & 1) {                                  /* UTF-16 */
        size_t k = tl / 2;
        WCHAR *w = (WCHAR *)malloc((k + 1) * 2);
        if (!w)
            return false;
        memcpy(w, e + 4, k * 2);
        w[k] = 0;
        fm_narrow(w, out, n);
        free(w);
    } else {
        size_t k = tl < n - 1 ? tl : n - 1;
        memcpy(out, e + 4, k);
        out[k] = 0;
    }
    return true;
}

/* RT_MESSAGETABLE of a module: resource name 1, the language asked for; error codes as RtlFindMessage maps them */
DWORD fm_find_module(HMODULE m, DWORD lang, DWORD id, char *out, size_t n)
{
    if (!m)
        m = GetModuleHandleA(NULL);
    SetLastError(ERROR_SUCCESS);
    void *r = fnvnx_resource_find_exact(m, (LPCSTR)(uintptr_t)11 /* RT_MESSAGETABLE */, (LPCSTR)(uintptr_t)1, (WORD)(lang & 0xFFFF));
    if (!r)
        return GetLastError() ? GetLastError() : 1814;
    const uint8_t *d = (const uint8_t *)LoadResource(m, r);
    DWORD sz = SizeofResource(m, r);
    if (!d || sz < 4)
        return 1814;
    const uint8_t *end = d + sz;
    uint32_t nb = d[0] | (d[1] << 8) | (d[2] << 16) | ((uint32_t)d[3] << 24);
    if (nb > (sz - 4) / 12)
        return 1812; /* ERROR_RESOURCE_DATA_NOT_FOUND */
    for (uint32_t i = 0; i < nb; i++) {
        const uint8_t *b = d + 4 + 12 * i;
        uint32_t lo = b[0] | (b[1] << 8) | (b[2] << 16) | ((uint32_t)b[3] << 24);
        uint32_t hi = b[4] | (b[5] << 8) | (b[6] << 16) | ((uint32_t)b[7] << 24);
        uint32_t off = b[8] | (b[9] << 8) | (b[10] << 16) | ((uint32_t)b[11] << 24);
        if (id < lo || id > hi)
            continue;
        if (off >= sz)
            return 1812;
        const uint8_t *e = d + off;
        for (uint32_t k = id - lo; k; k--) {
            if (end - e < 4)
                return 1812;
            e += e[0] | (e[1] << 8);
            if (e > end)
                return 1812;
        }
        return fm_entry_text(e, end, out, n) ? 0 : 1812;
    }
    return 317; /* ERROR_MR_MID_NOT_FOUND */
}

/* the system table: language 0x0409 only (a specific other language has no message: ERROR_RESOURCE_LANG_NOT_FOUND) */
DWORD fm_find_system(DWORD lang, DWORD id, char *out, size_t n)
{
    if (lang != 0 && lang != 0x0409)
        return 1815;
    if ((id >> 31) == 1 && ((id >> 16) & 0x1FFF) == 7)         /* an error HRESULT of FACILITY_WIN32 */
        id &= 0xFFFF;
    size_t lo = 0, hi = sizeof g_sysmsg_ids / sizeof *g_sysmsg_ids;
    while (lo < hi) {
        size_t mid = (lo + hi) / 2;
        if (g_sysmsg_ids[mid] < id)
            lo = mid + 1;
        else
            hi = mid;
    }
    if (lo == sizeof g_sysmsg_ids / sizeof *g_sysmsg_ids || g_sysmsg_ids[lo] != id)
        return 317;
    snprintf(out, n, "%s", g_sysmsg_text + g_sysmsg_off[lo]);
    return 0;
}
}   // namespace

static DWORD fm_message(DWORD, LPCVOID, DWORD, DWORD, LPSTR, DWORD, void *, char *, char *, char *, char *, unsigned long);

extern "C" DWORD FormatMessageA(DWORD flags, LPCVOID src, DWORD id, DWORD lang, LPSTR buf, DWORD n, void *args)
{
    flags = W32_DW(flags);
    id = W32_DW(id);
    lang = W32_DW(lang);
    n = W32_DW(n);
    if (flags & FORMAT_MESSAGE_ALLOCATE_BUFFER) {
        if (!buf) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return 0;
        }
        *(char **)buf = NULL;
    }
    if (n >= 32768) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    unsigned long width = flags & 0xff;
    if (width == 0xff)
        width = ~0ul;
    struct fm_work { char text[32768], out[32768], tmp[FM_TMP], conv[FM_TMP]; };
    fm_work *wk = (fm_work *)malloc(sizeof *wk);
    if (!wk) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return 0;
    }
    DWORD ret = fm_message(flags, src, id, lang, buf, n, args, wk->text, wk->out, wk->tmp, wk->conv, width);
    free(wk);
    return ret;
}

static DWORD fm_message(DWORD flags, LPCVOID src, DWORD id, DWORD lang, LPSTR buf, DWORD n, void *args, char *text, char *out, char *tmp, char *conv, unsigned long width)
{
    const char *msg;
    if (flags & 0x400 /* FROM_STRING */) {
        if (!src) {                                   /* documented as a fault: no text to format */
            SetLastError(ERROR_INVALID_PARAMETER);
            return 0;
        }
        msg = (const char *)src;
    } else {
        DWORD e = ERROR_INVALID_PARAMETER;
        if (flags & 0x800 /* FROM_HMODULE */)
            e = fm_find_module((HMODULE)src, lang, id, text, 32768);
        if (e && (flags & FORMAT_MESSAGE_FROM_SYSTEM))
            e = fm_find_system(lang, id, text, 32768);
        if (e) {
            SetLastError(e);
            return 0;
        }
        msg = text;
    }
    fm_args a;
    memset(&a, 0, sizeof a);
    a.tmp = tmp;
    a.conv = conv;
    if (args) {
        a.have = true;
        if (flags & 0x2000 /* ARGUMENT_ARRAY */)
            a.array = (const uint32_t *)args;
        else
            a.list = *(const uint32_t *const *)args;
    }
    /* the message may live in text[]: the result goes to out[] */
    int r = fm_format(msg, width, (flags & FORMAT_MESSAGE_IGNORE_INSERTS) != 0, &a, out, 32768);
    if (r == FM_FULL) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    if (r) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    DWORD len = (DWORD)strlen(out);
    if (!len) {
        SetLastError(ERROR_NO_WORK_DONE);
        return 0;
    }
    if (flags & FORMAT_MESSAGE_ALLOCATE_BUFFER) {
        char *p = (char *)LocalAlloc(0x40 /* LMEM_ZEROINIT */, len + 1 > n ? len + 1 : n);
        if (!p) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return 0;
        }
        memcpy(p, out, len + 1);
        *(char **)buf = p;
        return len;
    }
    if (len + 1 > n) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    memcpy(buf, out, len + 1);
    return len;
}

extern "C" LPSTR lstrcpyA(LPSTR d, LPCSTR s) { return d && s ? strcpy(d, s) : NULL; }
extern "C" LPSTR lstrcatA(LPSTR d, LPCSTR s) { return d && s ? strcat(d, s) : NULL; }
extern "C" int lstrlenA(LPCSTR s) { return s ? (int)strlen(s) : 0; }
/* lstrcmpiA: the locale comparison CompareStringA(LOCALE_USER_DEFAULT, NORM_IGNORECASE) with word sort, minus 2
 * (less -1, equal 0, greater 1); a NULL string sorts before any string, two NULLs are equal */
extern "C" int CompareStringA(LCID, DWORD, LPCSTR, int, LPCSTR, int);   /* nls.cpp */
extern "C" int lstrcmpiA(LPCSTR a, LPCSTR b)
{
    if (!a || !b)
        return a == b ? 0 : !a ? -1 : 1;
    return CompareStringA(0x0400 /* LOCALE_USER_DEFAULT */, 0x00000001 /* NORM_IGNORECASE */, a, -1, b, -1) - 2;
}

