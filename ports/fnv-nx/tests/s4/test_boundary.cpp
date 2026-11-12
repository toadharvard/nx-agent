/* test_boundary.cpp - the platform's boundary to the loader and the lifted-code runtime (shim/include/fnvnx_boundary.h,
 * host implementation tests/s4/host_boundary.cpp): registered images (module table, PE exports by name / ordinal /
 * forwarder, resources, page map), the value of a system export, game threads, the exit-handler list. */
#include "s4_test.h"

#include "fnvnx_boundary.h"
#include "fnvnx_msvcrt.h"
#include "fnvnx_win32_ext.h"
#include "host_boundary.h"

#include <string.h>

extern "C" {
BOOL DisableThreadLibraryCalls(HMODULE);
void *FindResourceExA(HMODULE, LPCSTR, LPCSTR, WORD);
void *LoadResource(HMODULE, void *);
DWORD SizeofResource(HMODULE, void *);
DWORD GetClassLongA(HWND, int);
void *os_vm_reserve(void *hint, size_t size);
int os_vm_commit(void *p, size_t size);
}

namespace {
const unsigned IMG_SIZE = 0x6000;

void w16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }
void w32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }

/* A synthetic PE32 in memory at a 32-bit address (the loader's VAs are 32-bit):
 *   0x0000 headers (R)   0x1000 .text (RX)   0x2000 .rdata (R: export directory)   0x3000 gap (no access)
 *   0x4000 .data (RW)    0x5000 .rsrc (R)
 * exports (ordinal base 5): 5 Alpha 0x1010, 6 Beta 0x1020, 7 Fwd -> "KERNEL32.GetTickCount", 8 (no name) 0x1030,
 *                           9 FwdOrd -> "SYNTH.#5" (a forwarder to this module's ordinal 5)
 * resources: type 10 (RCDATA), name 1, language 0x409: the bytes "HELLO". */
uint8_t *make_image(const char *dllname, uint32_t *base)
{
    void *a = VirtualAlloc(NULL, IMG_SIZE, MEM_RESERVE, PAGE_READWRITE);
    if (!a)
        return NULL;
    VirtualFree(a, 0, MEM_RELEASE);
    uint8_t *b = (uint8_t *)os_vm_reserve(a, IMG_SIZE);
    if (!b || os_vm_commit(b, IMG_SIZE))
        return NULL;
    memset(b, 0, IMG_SIZE);
    *base = (uint32_t)(uintptr_t)b;
    w16(b, 0x5A4D);
    w32(b + 0x3C, 0x40);
    w32(b + 0x40, 0x4550);
    w16(b + 0x44, 0x14C);            /* i386 */
    w16(b + 0x46, 4);                /* sections */
    w16(b + 0x54, 0xE0);             /* SizeOfOptionalHeader */
    uint8_t *opt = b + 0x58;
    w16(opt, 0x10B);
    w32(opt + 56, IMG_SIZE);
    w32(opt + 60, 0x1000);
    w32(opt + 92, 16);
    w32(opt + 96, 0x2000), w32(opt + 100, 0x200);    /* export directory */
    w32(opt + 96 + 16, 0x5000), w32(opt + 96 + 20, 0x200);   /* resource directory */
    struct { const char *n; uint32_t rva, ch; } sec[4] = {
        {".text", 0x1000, 0x60000020u}, {".rdata", 0x2000, 0x40000040u}, {".data", 0x4000, 0xC0000040u}, {".rsrc", 0x5000, 0x40000040u}};
    uint8_t *sh = opt + 0xE0;
    for (int i = 0; i < 4; i++, sh += 40) {
        strcpy((char *)sh, sec[i].n);
        w32(sh + 8, 0x1000), w32(sh + 12, sec[i].rva), w32(sh + 16, 0x1000), w32(sh + 20, sec[i].rva), w32(sh + 36, sec[i].ch);
    }
    /* export directory at 0x2000 */
    uint8_t *d = b + 0x2000;
    w32(d + 12, 0x2130);   /* Name */
    w32(d + 16, 5), w32(d + 20, 5), w32(d + 24, 4);
    w32(d + 28, 0x2040), w32(d + 32, 0x2060), w32(d + 36, 0x2070);
    uint32_t funcs[5] = {0x1010, 0x1020, 0x2160, 0x1030, 0x2140};
    memcpy(b + 0x2040, funcs, sizeof funcs);
    uint32_t names[4] = {0x2110, 0x2118, 0x2120, 0x2150};
    memcpy(b + 0x2060, names, sizeof names);
    uint16_t ords[4] = {0, 1, 2, 4};
    memcpy(b + 0x2070, ords, sizeof ords);
    strcpy((char *)b + 0x2160, "KERNEL32.GetTickCount");
    strcpy((char *)b + 0x2110, "Alpha");
    strcpy((char *)b + 0x2118, "Beta");
    strcpy((char *)b + 0x2120, "Fwd");
    strcpy((char *)b + 0x2130, dllname);
    strcpy((char *)b + 0x2140, "SYNTH.#5");
    strcpy((char *)b + 0x2150, "FwdOrd");
    /* resources at 0x5000: root -> type 10 -> name 1 -> language 0x409 -> data entry -> bytes */
    uint8_t *r = b + 0x5000;
    w16(r + 14, 1), w32(r + 16, 10), w32(r + 20, 0x80000020u);
    w16(r + 0x20 + 14, 1), w32(r + 0x30, 1), w32(r + 0x34, 0x80000040u);
    w16(r + 0x40 + 14, 1), w32(r + 0x50, 0x409), w32(r + 0x54, 0x60);
    w32(r + 0x60, 0x5100), w32(r + 0x64, 5);
    memcpy(r + 0x100, "HELLO", 5);
    return b;
}

MEMORY_BASIC_INFORMATION Q(const void *p)
{
    MEMORY_BASIC_INFORMATION m;
    memset(&m, 0, sizeof m);
    VirtualQuery(p, &m, sizeof m);
    return m;
}

LPCSTR ID(unsigned n) { return (LPCSTR)(uintptr_t)n; }
} // namespace

TEST(bnd_registered_image_exports)
{
    uint32_t base = 0;
    uint8_t *b = make_image("synth.dll", &base);
    CHECK(b != NULL);
    if (!b)
        return;
    int exports0 = g_bnd_exports;
    CHECK(GetModuleHandleA("synth.dll") == NULL && GetLastError() == ERROR_MOD_NOT_FOUND);
    fnvnx_win32_register_image("C:\\game\\Data\\synth.dll", base, IMG_SIZE);
    HMODULE h = GetModuleHandleA("SYNTH");
    CHECK(h == (HMODULE)(uintptr_t)base);
    CHECK(GetModuleHandleA("synth.dll") == h);
    char path[260];
    DWORD n = GetModuleFileNameA(h, path, sizeof path);
    CHECK(n > 10 && !strcmp(path + n - 10, "\\synth.dll"));
    /* by name, by ordinal, forwarder to a system DLL, forwarder by ordinal back into the module, absent names */
    CHECK(GetProcAddress(h, "Alpha") == (FARPROC)(uintptr_t)(base + 0x1010));
    CHECK(GetProcAddress(h, "Beta") == (FARPROC)(uintptr_t)(base + 0x1020));
    CHECK(GetProcAddress(h, ID(5)) == (FARPROC)(uintptr_t)(base + 0x1010));
    CHECK(GetProcAddress(h, ID(6)) == (FARPROC)(uintptr_t)(base + 0x1020));
    CHECK(GetProcAddress(h, ID(8)) == (FARPROC)(uintptr_t)(base + 0x1030));
    FARPROC tick = GetProcAddress(GetModuleHandleA("kernel32.dll"), "GetTickCount");
    CHECK(tick != NULL);
    CHECK(GetProcAddress(h, "Fwd") == tick);
    CHECK(GetProcAddress(h, "FwdOrd") == (FARPROC)(uintptr_t)(base + 0x1010));
    CHECK(GetProcAddress(h, ID(9)) == (FARPROC)(uintptr_t)(base + 0x1010));
    CHECK(GetProcAddress(h, "alpha") == NULL && GetLastError() == ERROR_PROC_NOT_FOUND);   /* names compare exactly */
    CHECK(GetProcAddress(h, "Gamma") == NULL && GetLastError() == ERROR_PROC_NOT_FOUND);
    CHECK(GetProcAddress(h, ID(4)) == NULL && GetLastError() == ERROR_PROC_NOT_FOUND);     /* below the ordinal base */
    CHECK(GetProcAddress(h, ID(10)) == NULL && GetLastError() == ERROR_PROC_NOT_FOUND);    /* past the table */
    CHECK(GetProcAddress(h, NULL) == NULL);
    /* the image's own functions are never turned into export values (they are guest addresses already) */
    CHECK(g_bnd_exports == exports0 + 2);   /* only the two system lookups of GetTickCount (direct, and as the forwarder target) */
    /* LoadLibrary of a loaded translated module: the same handle, one more reference; FreeLibrary drops it */
    int refs = fnvnx_module_load_count(h);
    CHECK(LoadLibraryA("synth.dll") == h && fnvnx_module_load_count(h) == refs + 1);
    CHECK(FreeLibrary(h) && fnvnx_module_load_count(h) == refs);
    /* the same module registered again keeps one table entry */
    fnvnx_win32_register_image("synth.dll", base, IMG_SIZE);
    CHECK(GetModuleHandleA("synth.dll") == h && fnvnx_module_load_count(h) == refs);
}

TEST(bnd_registered_image_resources)
{
    uint32_t base = 0;
    uint8_t *b = make_image("synthres.dll", &base);
    CHECK(b != NULL);
    if (!b)
        return;
    fnvnx_win32_register_image("synthres.dll", base, IMG_SIZE);
    HMODULE h = GetModuleHandleA("synthres.dll");
    CHECK(h == (HMODULE)(uintptr_t)base);
    void *r = FindResourceExA(h, ID(10), ID(1), 0x409);
    CHECK(r != NULL);
    CHECK_EQ(SizeofResource(h, r), 5);
    const void *p = LoadResource(h, r);
    CHECK(p != NULL && !memcmp(p, "HELLO", 5));
    CHECK(FindResourceExA(h, ID(10), ID(2), 0x409) == NULL && GetLastError() == 1814 /* ERROR_RESOURCE_NAME_NOT_FOUND */);
    CHECK(FindResourceExA(h, ID(11), ID(1), 0x409) == NULL && GetLastError() == 1813 /* ERROR_RESOURCE_TYPE_NOT_FOUND */);
}

TEST(bnd_disable_thread_calls)
{
    uint32_t base = 0;
    uint8_t *b = make_image("synthdtc.dll", &base);
    CHECK(b != NULL);
    if (!b)
        return;
    fnvnx_win32_register_image("synthdtc.dll", base, IMG_SIZE);
    HMODULE h = GetModuleHandleA("synthdtc.dll");
    int n0 = g_bnd_disabled_n;
    /* the exe and the platform's own modules: validated only, the loader is not told */
    CHECK(DisableThreadLibraryCalls(GetModuleHandleA(NULL)));
    CHECK(DisableThreadLibraryCalls(GetModuleHandleA("kernel32.dll")));
    CHECK_EQ(g_bnd_disabled_n, n0);
    /* a translated module: its base reaches the boundary, once however often it is asked */
    CHECK(DisableThreadLibraryCalls(h));
    CHECK(DisableThreadLibraryCalls(h));
    CHECK_EQ(g_bnd_disabled_n, n0 + 1);
    CHECK(g_bnd_disabled[n0] == base);
    /* a handle that is none of these: ERROR_MOD_NOT_FOUND, nothing recorded */
    SetLastError(0);
    CHECK(!DisableThreadLibraryCalls((HMODULE)(uintptr_t)0x12345678) && GetLastError() == ERROR_MOD_NOT_FOUND);
    CHECK_EQ(g_bnd_disabled_n, n0 + 1);
}

TEST(bnd_registered_image_page_map)
{
    uint32_t base = 0;
    uint8_t *b = make_image("synthvm.dll", &base);
    CHECK(b != NULL);
    if (!b)
        return;
    fnvnx_win32_register_image("synthvm.dll", base, IMG_SIZE);
    struct { unsigned off, size; DWORD prot; } want[] = {
        {0x0000, 0x1000, PAGE_READONLY}, {0x1000, 0x1000, PAGE_EXECUTE_READ}, {0x2000, 0x1000, PAGE_READONLY},
        {0x3000, 0x1000, PAGE_NOACCESS}, {0x4000, 0x1000, PAGE_READWRITE}, {0x5000, 0x1000, PAGE_READONLY}};
    for (auto &w : want) {
        MEMORY_BASIC_INFORMATION m = Q(b + w.off + 0x10);
        CHECK(m.Type == MEM_IMAGE && m.State == MEM_COMMIT);
        CHECK(m.AllocationBase == b);
        CHECK(m.BaseAddress == b + w.off);
        CHECK_EQ(m.RegionSize, w.size);
        CHECK_EQ(m.Protect, w.prot);
    }
}

TEST(bnd_getprocaddress_system_is_export_value)
{
    HMODULE k = GetModuleHandleA("kernel32.dll");
    int e0 = g_bnd_exports;
    FARPROC f = GetProcAddress(k, "GetTickCount");
    CHECK(f != NULL);
    CHECK_EQ(g_bnd_exports, e0 + 1);   /* the value came from the boundary */
    CHECK((void *)f == (void *)fnvnx_bnd_export_value("kernel32.dll", "GetTickCount", FNVNX_BOUNDARY_BY_NAME,
                                                     fnvnx_win32_lookup_export("kernel32.dll", "GetTickCount")));
    /* by ordinal (wsock32 1 = accept) and by name give one value */
    HMODULE ws = LoadLibraryA("wsock32.dll");
    CHECK(ws != NULL);
    FARPROC a1 = GetProcAddress(ws, ID(1)), a2 = GetProcAddress(ws, "accept");
    CHECK(a1 != NULL && a1 == a2);
    /* an export that does not exist: no value, ERROR_PROC_NOT_FOUND, no boundary call */
    e0 = g_bnd_exports;
    CHECK(GetProcAddress(k, "NoSuchExportAtAll") == NULL && GetLastError() == ERROR_PROC_NOT_FOUND);
    CHECK_EQ(g_bnd_exports, e0);
}

TEST(bnd_default_window_procedure_value)
{
    /* the platform's default procedure reaches the game as its export value and comes back as the default */
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpszClassName = "bnd_defproc";
    wc.lpfnWndProc = NULL;
    CHECK(RegisterClassA(&wc) != 0);
    HWND h = CreateWindowExA(0, "bnd_defproc", "t", 0, 0, 0, 10, 10, NULL, NULL, NULL, NULL);
    CHECK(h != NULL);
    FARPROC def = GetProcAddress(GetModuleHandleA("user32.dll"), "DefWindowProcA");
    CHECK(def != NULL);
    CHECK((uintptr_t)GetWindowLongA(h, GWL_WNDPROC) == (uintptr_t)def);
    CHECK((uintptr_t)GetClassLongA(h, -24 /* GCL_WNDPROC */) == (uintptr_t)def);
    LONG old = SetWindowLongA(h, GWL_WNDPROC, (LONG)(uintptr_t)def);   /* subclassing back to the default */
    CHECK((uintptr_t)old == (uintptr_t)def && (uintptr_t)GetWindowLongA(h, GWL_WNDPROC) == (uintptr_t)def);
    DestroyWindow(h);
}

namespace {
volatile LONG g_tparam;
DWORD t_return(LPVOID p) { g_tparam = (LONG)(uintptr_t)p; return 77; }
DWORD t_exit(LPVOID) { ExitThread(55); return 0; }
} // namespace

TEST(bnd_threads_through_the_boundary)
{
    int c0 = g_bnd_threads_created, r0 = g_bnd_threads_run, f0 = g_bnd_threads_freed, u0 = g_bnd_threads_freed_unrun;
    DWORD tid = 0;
    HANDLE t = test_CreateThread(NULL, 0, t_return, (LPVOID)(uintptr_t)1234, 0, &tid);
    CHECK(t != NULL);
    CHECK_EQ(WaitForSingleObject(t, 5000), 0);
    DWORD code = 0;
    CHECK(GetExitCodeThread(t, &code) && code == 77 && g_tparam == 1234);
    CloseHandle(t);
    CHECK_EQ(g_bnd_threads_created, c0 + 1);
    CHECK_EQ(g_bnd_threads_run, r0 + 1);
    CHECK_EQ(g_bnd_threads_freed, f0 + 1);   /* the context was given back when the start routine returned */
    /* ExitThread from inside: the same path, the exit code is the argument */
    t = test_CreateThread(NULL, 0, t_exit, NULL, 0, &tid);
    CHECK(t != NULL);
    CHECK_EQ(WaitForSingleObject(t, 5000), 0);
    CHECK(GetExitCodeThread(t, &code) && code == 55);
    CloseHandle(t);
    CHECK_EQ(g_bnd_threads_created, c0 + 2);
    CHECK_EQ(g_bnd_threads_freed, f0 + 2);
    CHECK_EQ(g_bnd_threads_freed_unrun, u0);
    /* created suspended: the context exists, nothing runs until ResumeThread */
    t = test_CreateThread(NULL, 0, t_return, (LPVOID)(uintptr_t)9, CREATE_SUSPENDED, &tid);
    CHECK(t != NULL);
    CHECK_EQ(g_bnd_threads_created, c0 + 3);
    CHECK_EQ(g_bnd_threads_run, r0 + 2);
    CHECK_EQ(ResumeThread(t), 1);
    CHECK_EQ(WaitForSingleObject(t, 5000), 0);
    CHECK(GetExitCodeThread(t, &code) && code == 77 && g_tparam == 9);
    CloseHandle(t);
    CHECK_EQ(g_bnd_threads_run, r0 + 3);
    CHECK_EQ(g_bnd_threads_freed, f0 + 3);
    CHECK_EQ(g_bnd_threads_freed_unrun, u0);
}

namespace {
uint32_t late_loader(const char *name, uint32_t *base)
{
    if (!strcmp(name, "late_bad.dll"))
        return ERROR_DLL_INIT_FAILED;
    if (!strcmp(name, "late_ghost.dll"))
        return 0;   /* claims success but registers nothing */
    uint32_t b = 0;
    if (!make_image("late.dll", &b))
        return ERROR_BAD_EXE_FORMAT;
    fnvnx_win32_register_image("late.dll", b, IMG_SIZE);
    *base = b;
    return 0;
}
} // namespace

TEST(bnd_loadlibrary_of_a_module_not_loaded_yet)
{
    /* no loader / not a translated module: ERROR_MOD_NOT_FOUND */
    CHECK(LoadLibraryA("nonexistent_module.dll") == NULL && GetLastError() == ERROR_MOD_NOT_FOUND);
    g_bnd_loader = late_loader;
    HMODULE h = LoadLibraryA("LATE");
    CHECK(h != NULL);
    CHECK(GetModuleHandleA("late.dll") == h);
    CHECK(GetProcAddress(h, "Alpha") == (FARPROC)((uintptr_t)h + 0x1010));
    CHECK_EQ(fnvnx_module_load_count(h), 2);   /* the loader's reference and this LoadLibrary's */
    CHECK(LoadLibraryA("late.dll") == h);      /* loaded now: no second load */
    CHECK_EQ(fnvnx_module_load_count(h), 3);
    CHECK(LoadLibraryA("late_bad.dll") == NULL && GetLastError() == ERROR_DLL_INIT_FAILED);
    CHECK(LoadLibraryA("late_ghost.dll") == NULL && GetLastError() == ERROR_DLL_INIT_FAILED);
    g_bnd_loader = NULL;
}

