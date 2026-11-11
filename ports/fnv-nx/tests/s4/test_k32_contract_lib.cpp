/* test_k32_contract_lib.cpp - FreeLibrary / LoadLibrary reference counts and the loader's unload (task W20 group 5).
 * The loader's fnvnx_module_unload is the host boundary's recorded hook (tests/s4/host_boundary.cpp). */
#include "s4_test.h"
#include "fnvnx_boundary.h"
#include "fnvnx_win32_ext.h"
#include <string.h>

extern "C" {
void *os_vm_reserve(void *hint, size_t size);
int os_vm_commit(void *p, size_t size);
extern uint32_t g_bnd_unload_base[16];
extern int g_bnd_unload_n;
extern int g_bnd_unload_outcome;
extern uint32_t g_bnd_unload_error;
}

static uint32_t image_at(const char *name)
{
    void *a = VirtualAlloc(NULL, 0x2000, MEM_RESERVE, PAGE_READWRITE);
    if (!a)
        return 0;
    VirtualFree(a, 0, MEM_RELEASE);
    uint8_t *b = (uint8_t *)os_vm_reserve(a, 0x2000);
    if (!b || os_vm_commit(b, 0x2000))
        return 0;
    memset(b, 0, 0x2000);
    uint32_t base = (uint32_t)(uintptr_t)b;
    fnvnx_win32_register_image(name, base, 0x2000);
    return base;
}

static void reset_hook(int outcome, uint32_t err)
{
    g_bnd_unload_n = 0;
    g_bnd_unload_outcome = outcome;
    g_bnd_unload_error = err;
}

TEST(bnd_freelibrary_unload_rule)
{
    uint32_t base = image_at("flib_a.dll");
    CHECK(base != 0);
    HMODULE h = GetModuleHandleA("flib_a.dll");
    CHECK(h == (HMODULE)(uintptr_t)base);
    /* a LoadLibrary count above 0 is only counted down: the loader is not asked */
    reset_hook(FNVNX_BND_UNLOADED, 0);
    CHECK(LoadLibraryA("flib_a.dll") == h);
    CHECK(LoadLibraryA("flib_a.dll") == h);
    CHECK(FreeLibrary(h));
    CHECK_EQ(g_bnd_unload_n, 0);
    /* the count reaches 0: the loader unloads: the module is gone from the table, its pages from the page map */
    MEMORY_BASIC_INFORMATION mbi;
    CHECK(VirtualQuery((void *)(uintptr_t)base, &mbi, sizeof mbi) && mbi.Type == MEM_IMAGE);
    CHECK(FreeLibrary(h));
    CHECK_EQ(g_bnd_unload_n, 1);
    CHECK_EQ(g_bnd_unload_base[0], base);
    SetLastError(0);
    CHECK(GetModuleHandleA("flib_a.dll") == NULL && GetLastError() == ERROR_MOD_NOT_FOUND);
    CHECK(!VirtualQuery((void *)(uintptr_t)base, &mbi, sizeof mbi) || mbi.Type != MEM_IMAGE);
    CHECK(!FreeLibrary(h) && GetLastError() == ERROR_INVALID_HANDLE);   /* the handle is stale now */
    /* the same name registers again (a new load) into a fresh table entry */
    uint32_t base2 = image_at("flib_a.dll");
    CHECK(GetModuleHandleA("flib_a.dll") == (HMODULE)(uintptr_t)base2);
    CHECK_EQ(fnvnx_module_load_count((HMODULE)(uintptr_t)base2), 1);
}

TEST(bnd_freelibrary_kept_pinned_and_errors)
{
    uint32_t base = image_at("flib_b.dll");
    HMODULE h = GetModuleHandleA("flib_b.dll");
    CHECK(h == (HMODULE)(uintptr_t)base);
    /* KEPT: the platform's reference is dropped, the module stays loaded at count 0 and is still found */
    reset_hook(FNVNX_BND_KEPT, 0);
    CHECK(LoadLibraryA("flib_b.dll") == h);
    CHECK(FreeLibrary(h));
    CHECK_EQ(g_bnd_unload_n, 1);
    CHECK(GetModuleHandleA("flib_b.dll") == h);
    CHECK_EQ(fnvnx_module_load_count(h), 1);
    /* PINNED (a static module): FreeLibrary succeeds and nothing changes */
    reset_hook(FNVNX_BND_PINNED, 0);
    CHECK(LoadLibraryA("flib_b.dll") == h);
    CHECK(FreeLibrary(h));
    CHECK_EQ(g_bnd_unload_n, 1);
    CHECK(GetModuleHandleA("flib_b.dll") == h);
    CHECK_EQ(fnvnx_module_load_count(h), 1);
    /* the loader's refusals: no explicit load of this module -> ERROR_MOD_NOT_FOUND, not a loaded module -> ERROR_INVALID_HANDLE */
    reset_hook(FNVNX_BND_KEPT, 126);
    SetLastError(0);
    CHECK(!FreeLibrary(h) && GetLastError() == ERROR_MOD_NOT_FOUND);
    reset_hook(FNVNX_BND_KEPT, 6);
    CHECK(!FreeLibrary(h) && GetLastError() == ERROR_INVALID_HANDLE);
    CHECK(GetModuleHandleA("flib_b.dll") == h);
    /* handles that are no module */
    CHECK(!FreeLibrary((HMODULE)(uintptr_t)0x12345678) && GetLastError() == ERROR_INVALID_HANDLE);
    CHECK(FreeLibrary(GetModuleHandleA(NULL)));                         /* the exe: always TRUE */
    CHECK(FreeLibrary(GetModuleHandleA("kernel32.dll")));               /* system DLLs are pinned */
    reset_hook(FNVNX_BND_PINNED, 0);
}
