/* test_misc.cpp - time, virtual memory, heaps, command line, registry, windows/messages, export table. */
#include "s4_test.h"

#include <stdlib.h>

extern "C" uint64_t os_monotonic_ns(void);

TEST(time_counters)
{
    LARGE_INTEGER f, a, b;
    CHECK(QueryPerformanceFrequency(&f));
    CHECK(f.QuadPart >= 1000000);
    QueryPerformanceCounter(&a);
    DWORD t0 = GetTickCount();
    Sleep(50);
    QueryPerformanceCounter(&b);
    DWORD dt = GetTickCount() - t0;
    double ms = (double)(b.QuadPart - a.QuadPart) * 1000.0 / (double)f.QuadPart;
    CHECK(ms >= 45 && ms < 500);
    CHECK(dt >= 40 && dt < 500);
    CHECK(timeGetTime() - GetTickCount() < 5);
}

TEST(time_wall_clock_conversions)
{
    SYSTEMTIME st = { 2010, 10, 2, 19, 13, 37, 42, 123 }; /* FNV release day, a Tuesday */
    FILETIME ft;
    CHECK(SystemTimeToFileTime(&st, &ft));
    SYSTEMTIME back;
    CHECK(FileTimeToSystemTime(&ft, &back));
    CHECK_EQ(back.wYear, 2010);
    CHECK_EQ(back.wMonth, 10);
    CHECK_EQ(back.wDay, 19);
    CHECK_EQ(back.wDayOfWeek, 2);
    CHECK_EQ(back.wHour, 13);
    CHECK_EQ(back.wMinute, 37);
    CHECK_EQ(back.wSecond, 42);
    CHECK_EQ(back.wMilliseconds, 123);
    FILETIME epoch = { 0xD53E8000u, 0x019DB1DEu }; /* 1970-01-01 */
    CHECK(FileTimeToSystemTime(&epoch, &back));
    CHECK_EQ(back.wYear, 1970);
    CHECK_EQ(back.wDayOfWeek, 4);
    CHECK_EQ(CompareFileTime(&epoch, &ft), -1);
    CHECK_EQ(CompareFileTime(&ft, &ft), 0);
    SYSTEMTIME now, local;
    GetSystemTime(&now);
    GetLocalTime(&local);
    CHECK(now.wYear >= 2024 && now.wMonth >= 1 && now.wMonth <= 12);
    CHECK(local.wYear >= 2024);
    FILETIME sft;
    GetSystemTimeAsFileTime(&sft);
    CHECK(CompareFileTime(&sft, &ft) == 1);
}

TEST(virtual_alloc)
{
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    CHECK_EQ(si.dwPageSize, 4096);
    CHECK_EQ(si.dwAllocationGranularity, 65536);
    CHECK(si.dwNumberOfProcessors >= 1);
    char *r = (char *)VirtualAlloc(NULL, 1 << 20, MEM_RESERVE, PAGE_READWRITE);
    CHECK(r != NULL);
    CHECK(((uintptr_t)r & 0xFFFF) == 0);
    MEMORY_BASIC_INFORMATION mbi;
    VirtualQuery(r, &mbi, sizeof mbi);
    CHECK_EQ(mbi.State, MEM_RESERVE);
    CHECK_EQ(mbi.RegionSize, 1 << 20);
    char *c = (char *)VirtualAlloc(r + 8192 + 100, 5000, MEM_COMMIT, PAGE_READWRITE);
    CHECK(c == r + 8192); /* rounded down to the page */
    memset(c, 0x5A, 8192); /* pages 2 and 3 are usable */
    VirtualQuery(r + 8192, &mbi, sizeof mbi);
    CHECK_EQ(mbi.State, MEM_COMMIT);
    CHECK_EQ(mbi.RegionSize, 8192);
    CHECK(mbi.AllocationBase == r);
    CHECK(VirtualFree(c, 4096, MEM_DECOMMIT));
    CHECK(VirtualAlloc(c, 4096, MEM_COMMIT, PAGE_READWRITE) == c);
    CHECK_EQ(c[0], 0); /* recommitted pages are zero */
    CHECK_EQ(c[4096], 0x5A);
    CHECK(!VirtualFree(r + 4096, 0, MEM_RELEASE)); /* release needs the base */
    CHECK(VirtualFree(r, 0, MEM_RELEASE));
    VirtualQuery(r, &mbi, sizeof mbi);
    CHECK_EQ(mbi.State, MEM_FREE);
    char *rc = (char *)VirtualAlloc(NULL, 100, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    CHECK(rc != NULL);
    rc[99] = 1;
    CHECK(VirtualFree(rc, 0, MEM_RELEASE));
    CHECK(VirtualAlloc(NULL, 0, MEM_COMMIT, PAGE_READWRITE) == NULL);
}

TEST(heap_functions)
{
    HANDLE h = GetProcessHeap();
    unsigned char *p = (unsigned char *)HeapAlloc(h, HEAP_ZERO_MEMORY, 64);
    CHECK(p && p[0] == 0 && p[63] == 0);
    CHECK(HeapSize(h, 0, p) >= 64);
    p = (unsigned char *)HeapReAlloc(h, HEAP_ZERO_MEMORY, p, 4096);
    CHECK(p && p[4095] == 0);
    CHECK(HeapFree(h, 0, p));
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof ms;
    CHECK(GlobalMemoryStatusEx(&ms));
    CHECK(ms.ullTotalPhys > 0 && ms.ullAvailPhys <= ms.ullTotalPhys);
}

TEST(command_line)
{
    LPSTR a = GetCommandLineA();
    CHECK(!strncmp(a, "\"C:\\FalloutNV\\FalloutNV.exe\"", 28));
    static const WCHAR cmd[] = L"\"C:\\Path With\\game.exe\" -a \"b c\" d\\\\\"e f\" g\\\"h \"\"\"";
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(cmd, &argc);
    CHECK_EQ(argc, 6);
    char conv[6][64];
    for (int i = 0; i < argc && i < 6; i++) {
        int k = 0;
        for (; argv[i][k] && k < 63; k++)
            conv[i][k] = (char)argv[i][k];
        conv[i][k] = 0;
    }
    CHECK_STR(conv[0], "C:\\Path With\\game.exe");
    CHECK_STR(conv[1], "-a");
    CHECK_STR(conv[2], "b c");
    CHECK_STR(conv[3], "d\\e f"); /* 2n backslashes + quote: n backslashes, quote toggles */
    CHECK_STR(conv[4], "g\"h");   /* 2n+1: literal quote */
    CHECK_STR(conv[5], "\"");     /* "" inside quotes */
    LocalFree(argv);
}

extern "C" void fnvnx_registry_set_path(const char *host_path);
extern "C" void fnvnx_registry_reset_for_test(void);

TEST(registry_in_memory)
{
    char hive[1200];
    snprintf(hive, sizeof hive, "%s/misc.hiv", s4_root());
    remove(hive);
    fnvnx_registry_set_path(hive);
    fnvnx_registry_reset_for_test();
    HKEY k;
    /* the tree starts empty: the installer keys are created by whoever needs them */
    CHECK_EQ(RegCreateKeyExA(HKEY_LOCAL_MACHINE, "Software\\Bethesda Softworks\\FalloutNV", 0, NULL, 0, 0xF003F, NULL, &k, NULL), ERROR_SUCCESS);
    CHECK_EQ(RegSetValueExA(k, "Installed Path", 0, REG_SZ, (const BYTE *)"C:\\FalloutNV\\", 14), ERROR_SUCCESS);
    RegCloseKey(k);
    CHECK_EQ(RegOpenKeyExA(HKEY_LOCAL_MACHINE, "Software\\Bethesda Softworks\\FalloutNV", 0, 0x20019, &k), ERROR_SUCCESS);
    char buf[MAX_PATH];
    DWORD type = 0, len = sizeof buf;
    CHECK_EQ(RegQueryValueExA(k, "Installed Path", NULL, &type, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_EQ(type, REG_SZ);
    CHECK_STR(buf, "C:\\FalloutNV\\");
    len = 2;
    CHECK_EQ(RegQueryValueExA(k, "Installed Path", NULL, &type, (LPBYTE)buf, &len), ERROR_MORE_DATA);
    CHECK_EQ(RegCloseKey(k), ERROR_SUCCESS);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Software\\Nothing", 0, 0, &k), ERROR_FILE_NOT_FOUND);
    DWORD disp = 0;
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "Software\\FNVNX", 0, NULL, 0, 0xF003F, NULL, &k, &disp), ERROR_SUCCESS);
    CHECK_EQ(disp, REG_CREATED_NEW_KEY);
    DWORD v = 7;
    CHECK_EQ(RegSetValueExA(k, "n", 0, REG_DWORD, (const BYTE *)&v, sizeof v), ERROR_SUCCESS);
    DWORD out = 0;
    len = sizeof out;
    CHECK_EQ(RegQueryValueExA(k, "n", NULL, NULL, (LPBYTE)&out, &len), ERROR_SUCCESS);
    CHECK_EQ(out, 7);
    RegCloseKey(k);
}

static int g_wndproc_calls;
static LRESULT test_proc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    g_wndproc_calls++;
    return m == 0x400 ? (LRESULT)(w + l) : DefWindowProcA(h, m, w, l);
}

TEST(window_and_messages)
{
    CHECK(FindWindowA("Fallout: New Vegas", NULL) == NULL); /* no other instance */
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = test_proc;
    wc.lpszClassName = "Fallout: New Vegas";
    CHECK(RegisterClassA(&wc) != 0);
    HWND h = CreateWindowExA(0, "Fallout: New Vegas", "Fallout: New Vegas", 0, 0, 0, 1280, 720, NULL, NULL, NULL, NULL);
    CHECK(h != NULL);
    CHECK(FindWindowA("fallout: new vegas", NULL) == h);
    CHECK(fnvnx_main_window() == h);
    CHECK_EQ(SendMessageA(h, 0x400, 2, 3), 5);
    MSG m;
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    PostMessageA(h, 0x401, 1, 0);
    fnvnx_post_message(NULL, WM_KEYDOWN, 0x41, 0);
    CHECK(PeekMessageA(&m, NULL, WM_KEYDOWN, WM_KEYDOWN, PM_NOREMOVE)); /* filtered, kept */
    CHECK_EQ(m.message, WM_KEYDOWN);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    CHECK_EQ(m.message, 0x401);
    int before = g_wndproc_calls;
    DispatchMessageA(&m);
    CHECK_EQ(g_wndproc_calls, before + 1);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    CHECK_EQ(m.message, WM_KEYDOWN);
    CHECK(m.hwnd == h);
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    RECT rc;
    fnvnx_set_display_size(1280, 720);
    CHECK(GetClientRect(h, &rc));
    CHECK_EQ(rc.right, 1280);
    CHECK_EQ(GetSystemMetrics(SM_CYSCREEN), 720);
    SetWindowLongA(h, GWL_USERDATA, 99);
    CHECK_EQ(GetWindowLongA(h, GWL_USERDATA), 99);
    fnvnx_set_key_state(0x1B, 1);
    CHECK(GetAsyncKeyState(0x1B) & 0x8000);
    fnvnx_set_key_state(0x1B, 0);
    CHECK_EQ(GetAsyncKeyState(0x1B), 0);
    CHECK_EQ(MessageBoxA(h, "Continue?", "test", MB_YESNO), IDYES);
    CHECK_EQ(MessageBoxA(h, "Continue?", "test", MB_YESNO | MB_DEFBUTTON2), IDNO);
    CHECK(DestroyWindow(h));
    CHECK(fnvnx_main_window() == NULL);
}

TEST(modules_and_exports)
{
    CHECK(GetModuleHandleA(NULL) == (HMODULE)(uintptr_t)0x00400000u);
    HMODULE x0 = LoadLibraryA("xinput1_3.dll");   /* ENV_TRUTH M5: a system DLL, loaded before anything registers */
    CHECK(x0 != NULL);
    fnvnx_register_module("XINPUT1_3.dll", [](const char *p) -> FARPROC {
        return !strcmp(p, "XInputGetState") ? (FARPROC)(void *)&GetTickCount : NULL;
    });
    HMODULE x = LoadLibraryA("C:\\Windows\\System32\\xinput1_3");
    CHECK(x == x0);   /* registering gives the module its exports, it does not make a second module */
    CHECK(GetProcAddress(x, "XInputGetState") == (FARPROC)(void *)&GetTickCount);
    CHECK(GetProcAddress(x, "XInputSetState") == NULL);
    CHECK(fnvnx_win32_lookup_export("KERNEL32.dll", "CreateFileA") == (void *)&CreateFileA);
    CHECK(fnvnx_win32_lookup_export("user32", "PeekMessageA") == (void *)&PeekMessageA);
    CHECK(fnvnx_win32_lookup_export("kernel32", "PeekMessageA") == NULL);
    CHECK(fnvnx_win32_lookup_export("d3d9.dll", "Direct3DCreate9") == NULL); /* S6 */
    CHECK(fnvnx_win32_lookup_export("ws2_32.dll", "htons") != NULL);
    char name[32];
    DWORD n = sizeof name;
    CHECK(GetComputerNameA(name, &n));
    char docs[MAX_PATH];
    CHECK_EQ(SHGetFolderPathA(NULL, CSIDL_PERSONAL, NULL, 0, docs), S_OK);
    CHECK_STR(docs, "C:\\Users\\Player\\Documents");
    GUID g1, g2;
    CoCreateGuid(&g1);
    CoCreateGuid(&g2);
    CHECK(memcmp(&g1, &g2, sizeof g1) != 0);
    CHECK_EQ(CoCreateInstance(&g1, NULL, 1, &g2, (LPVOID *)&x), CO_E_NOTINITIALIZED);   /* no CoInitialize on this thread */
    CHECK_EQ(CoInitialize(NULL), S_OK);
    CHECK_EQ(CoCreateInstance(&g1, NULL, 1, &g2, (LPVOID *)&x), REGDB_E_CLASSNOTREG);
    CoUninitialize();
}

static HRESULT com_test_factory(const GUID *, void **out)
{
    *out = (void *)&com_test_factory;
    return S_OK;
}
static int g_com_idle = 1;
static HRESULT com_test_can_unload(void) { return g_com_idle ? S_OK : S_FALSE; }

/* COM apartments (ole32): the per-thread initialisation count, the model of the first call, the creation errors */
TEST(com_apartments_and_creation)
{
    GUID clsid = { 0x11111111, 0x2222, 0x3333, { 1, 2, 3, 4, 5, 6, 7, 8 } }, iid = clsid, dll_clsid = clsid;
    dll_clsid.Data1 = 0x22222222;
    void *obj = NULL;
    CHECK_EQ(CoInitializeEx((LPVOID)&obj, 0), E_INVALIDARG);           /* the reserved pointer must be NULL */
    CHECK_EQ(CoInitializeEx(NULL, 2), S_OK);                           /* STA, first time */
    CHECK_EQ(CoInitializeEx(NULL, 2), S_FALSE);                        /* repeat */
    CHECK_EQ(CoInitializeEx(NULL, 0), RPC_E_CHANGED_MODE);             /* MTA after STA on this thread */
    CHECK_EQ(CoInitialize(NULL), S_FALSE);
    CHECK(fnvnx_register_com_class(&clsid, com_test_factory));
    CHECK_EQ(CoCreateInstance(&clsid, NULL, 1, &iid, &obj), S_OK);
    CHECK(obj == (void *)&com_test_factory);
    CHECK_EQ(CoCreateInstance(&clsid, NULL, 1, &iid, NULL), E_POINTER);
    CHECK_EQ(CoCreateInstance(&clsid, (void *)&obj, 1, &iid, &obj), CLASS_E_NOAGGREGATION);
    CHECK(obj == NULL);
    CHECK_EQ(CoCreateInstance(&clsid, NULL, 4 /* CLSCTX_LOCAL_SERVER */, &iid, &obj), REGDB_E_CLASSNOTREG);
    /* servers: a resident one stays through CoFreeUnusedLibraries, a DLL server goes when it says it is idle */
    CHECK(fnvnx_register_com_dll(&dll_clsid, com_test_factory, com_test_can_unload));
    g_com_idle = 0;
    CoFreeUnusedLibraries();
    CHECK_EQ(CoCreateInstance(&dll_clsid, NULL, 1, &iid, &obj), S_OK);
    g_com_idle = 1;
    CoFreeUnusedLibraries();
    CHECK_EQ(CoCreateInstance(&dll_clsid, NULL, 1, &iid, &obj), REGDB_E_CLASSNOTREG);
    CHECK_EQ(CoCreateInstance(&clsid, NULL, 1, &iid, &obj), S_OK);
    /* the count balances: three successful calls above (S_OK, S_FALSE, S_FALSE) */
    CoUninitialize();
    CoUninitialize();
    CHECK_EQ(CoCreateInstance(&clsid, NULL, 1, &iid, &obj), S_OK);
    CoUninitialize();
    CHECK_EQ(CoCreateInstance(&clsid, NULL, 1, &iid, &obj), CO_E_NOTINITIALIZED);
    CoUninitialize();                                                  /* unbalanced: nothing to release */
    CHECK_EQ(CoInitializeEx(NULL, 0), S_OK);                           /* the model is free again: MTA */
    CoUninitialize();
}

/* many regions, created and released out of order, committed page by page (the game's MemoryPool::CommitPage): the
 * region table stays sorted, every lookup finds its own region, the edges belong to the right one. Prints the time
 * per page commit. */
TEST(virtual_alloc_many_regions)
{
    enum { N = 300 };
    char *r[N];
    for (int i = 0; i < N; i++) {
        r[i] = (char *)VirtualAlloc(NULL, 0x20000, MEM_RESERVE, PAGE_READWRITE);
        CHECK(r[i] != NULL);
    }
    for (int i = 0; i < N; i += 3) {   /* release a third, out of order */
        CHECK(VirtualFree(r[i], 0, MEM_RELEASE));
        r[i] = NULL;
    }
    uint64_t t0 = os_monotonic_ns();
    int bad = 0, commits = 0;
    for (int pass = 0; pass < 2; pass++)
        for (int i = N - 1; i >= 0; i--) {
            if (!r[i])
                continue;
            for (int pg = 0; pg < 0x20000; pg += 0x1000) {
                char *p = (char *)VirtualAlloc(r[i] + pg, 0x1000, MEM_COMMIT, PAGE_READWRITE);
                bad += p != r[i] + pg;
                commits++;
                if (p && pass == 0)
                    p[0] = (char)i;
            }
        }
    uint64_t t1 = os_monotonic_ns();
    CHECK_EQ(bad, 0);
    for (int i = 0; i < N; i++)
        if (r[i]) {
            MEMORY_BASIC_INFORMATION mi;
            CHECK(VirtualQuery(r[i] + 0x1ffff, &mi, sizeof mi) == sizeof mi);
            CHECK(mi.AllocationBase == r[i]);
            CHECK_EQ(r[i][0x1f000], (char)i);
        }
    CHECK(VirtualAlloc(r[1] + 0x20000 - 0x1000, 0x2000, MEM_COMMIT, PAGE_READWRITE) == NULL);   /* past the end */
    for (int i = 0; i < N; i++)
        if (r[i])
            CHECK(VirtualFree(r[i], 0, MEM_RELEASE));
    printf("   virtual_alloc_many_regions: %d page commits, %.2f us each\n", commits,
           (double)(t1 - t0) / 1000.0 / commits);
}

/* the commit limit, as Windows reports it: the heap constructor 0x00aa3880 exits when ullAvailPageFile < 512 MB */
TEST(memory_status_page_file)
{
    MEMORYSTATUSEX ms;
    memset(&ms, 0xcc, sizeof ms);
    ms.dwLength = sizeof ms;
    CHECK(GlobalMemoryStatusEx(&ms));
    CHECK_EQ(ms.dwLength, sizeof ms);   /* 64 on the ILP32 guest */
    CHECK(ms.ullTotalPhys > 0 && ms.ullAvailPhys <= ms.ullTotalPhys);
    CHECK(ms.ullTotalPageFile >= ms.ullTotalPhys && ms.ullAvailPageFile <= ms.ullTotalPageFile);
    CHECK(ms.ullAvailPageFile >= ms.ullAvailPhys + ms.ullTotalPhys / 2);
    CHECK(ms.ullAvailPageFile >= 0x20000000ull);   /* the game's threshold */
    CHECK(ms.ullTotalVirtual == 0x7FFE0000ull && ms.ullAvailVirtual <= ms.ullTotalVirtual);
    CHECK_EQ(ms.ullAvailExtendedVirtual, 0);
    CHECK(ms.dwMemoryLoad <= 100);
    MEMORYSTATUS m;
    GlobalMemoryStatus(&m);
    CHECK(m.dwTotalPageFile >= m.dwTotalPhys && m.dwAvailPageFile >= m.dwAvailPhys);
}
