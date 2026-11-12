/* test_procenv.cpp - the process environment a Windows GUI exe started from its own folder gets before WinMain, and
 * that the static MSVC CRT reads: command line, environment block, current directory, process heap, startup info,
 * module identity, default thread stack, standard handles (the PE-loader seam audit, items 1-8). */
#include "s4_test.h"

#include "../../platform/win32/w32.h"

#include <ctype.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

#define STARTF_USESHOWWINDOW 1
static size_t w16(const WCHAR *w) { size_t n = 0; while (w[n]) n++; return n; }

/* 1. command line: full line, exe path first and quoted, stable pointer; the CRT's _wincmdln skips argv[0] */
TEST(procenv_command_line)
{
    char *a = GetCommandLineA();
    WCHAR *w = GetCommandLineW();
    CHECK(a != NULL && w != NULL);
    CHECK(GetCommandLineA() == a);   /* stable: the same pointer every call */
    CHECK(GetCommandLineW() == w);
    CHECK_EQ(a[0], '"');
    CHECK(strncmp(a + 1, "C:\\FalloutNV\\FalloutNV.exe\"", 27) == 0);
    CHECK_EQ(w16(w), strlen(a));   /* the wide line is the same text */
    for (size_t i = 0; a[i]; i++)
        CHECK_EQ(w[i], (unsigned char)a[i]);
    /* _wincmdln: past the quoted name, the rest (after blanks) is lpCmdLine */
    char *p = a + 1;
    while (*p && *p != '"')
        p++;
    CHECK_EQ(*p, '"');
    p++;
    while (*p == ' ')
        p++;
    /* boot passes lpCmdLine "" to WinMain: that matches only when the process has no arguments. With arguments
     * GetCommandLineA's tail is non-empty while WinMain still gets "" (boot.cpp: toolchain, not changed here) */
    char *argv[] = { (char *)"fnv", (char *)"-a", (char *)"two words", (char *)"" };
    fnvnx_set_command_line(4, argv);
    char *b = GetCommandLineA();
    CHECK(b != NULL && GetCommandLineA() == b);
    CHECK_STR(b, "\"C:\\FalloutNV\\FalloutNV.exe\" -a \"two words\" \"\"");
    fnvnx_set_command_line(1, argv);
    CHECK_STR(GetCommandLineA(), "\"C:\\FalloutNV\\FalloutNV.exe\"");   /* no arguments: just the quoted path */
    CHECK_EQ(GetCommandLineW()[0], '"');
    int argc = 0;
    LPWSTR *av = CommandLineToArgvW(GetCommandLineW(), &argc);
    CHECK_EQ(argc, 1);
    if (av)
        LocalFree(av);
    (void)p;
}

/* 2. environment block */
static int env_has(const char *blk, const char *name)
{
    size_t l = strlen(name);
    for (const char *p = blk; *p; p += strlen(p) + 1)
        if (!strncasecmp(p, name, l) && p[l] == '=')
            return 1;
    return 0;
}

TEST(procenv_environment_defaults)
{
    char *e = GetEnvironmentStrings();
    CHECK(e != NULL);
    if (!e)
        return;
    CHECK(e[0] != 0);   /* non-empty: the CRT's _setenvp needs a block */
    const char *want[] = { "PATH", "TEMP", "TMP", "SystemRoot", "windir", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                           "USERNAME", "COMPUTERNAME", "OS", "SystemDrive", "ComSpec", "ProgramFiles", "HOMEDRIVE",
                           "HOMEPATH", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PATHEXT" };
    for (const char *n : want)
        CHECK(env_has(e, n));
    for (const char *p = e; *p; p += strlen(p) + 1)
        CHECK(strchr(p, '=') != NULL && p[0] != '=');   /* every entry is NAME=VALUE */
    FreeEnvironmentStringsA(e);
    WCHAR *w = GetEnvironmentStringsW();
    CHECK(w != NULL && w[0] != 0);
    if (w) {
        const WCHAR *q = w;
        int n = 0;
        while (*q) {
            q += w16(q) + 1;
            n++;
        }
        CHECK(n >= 15);
        CHECK(FreeEnvironmentStringsW(w));
    }
    char buf[260];
    CHECK_EQ(GetEnvironmentVariableA("SystemRoot", buf, sizeof buf), 10);
    CHECK_STR(buf, "C:\\Windows");
    CHECK(GetEnvironmentVariableA("userprofile", buf, sizeof buf) > 0);   /* case-insensitive */
    CHECK_STR(buf, "C:\\Users\\Player");
}

TEST(procenv_environment_roundtrip)
{
    char buf[32];
    SetLastError(0);
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_PE_MISSING", buf, sizeof buf), 0);
    CHECK_EQ(GetLastError(), ERROR_ENVVAR_NOT_FOUND);
    CHECK(!SetEnvironmentVariableA("FNVNX_PE_MISSING", NULL));   /* deleting what is not there */
    CHECK_EQ(GetLastError(), ERROR_ENVVAR_NOT_FOUND);
    CHECK(SetEnvironmentVariableA("FNVNX_PE", "hello"));
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_PE", buf, sizeof buf), 5);
    CHECK_STR(buf, "hello");
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_PE", buf, 5), 6);    /* one short: the size with the NUL */
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_PE", buf, 6), 5);    /* exactly fits */
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_PE", NULL, 0), 6);
    CHECK(SetEnvironmentVariableA("FNVNX_PE", "bye"));           /* replace */
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_PE", buf, sizeof buf), 3);
    CHECK(SetEnvironmentVariableA("FNVNX_PE_EMPTY", ""));
    SetLastError(77);
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_PE_EMPTY", buf, sizeof buf), 0);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);                     /* empty value: 0 and no error */
    char *e = GetEnvironmentStrings();
    CHECK(e && env_has(e, "FNVNX_PE") && env_has(e, "FNVNX_PE_EMPTY"));
    FreeEnvironmentStringsA(e);
    CHECK(SetEnvironmentVariableA("FNVNX_PE", NULL));
    CHECK(SetEnvironmentVariableA("FNVNX_PE_EMPTY", NULL));
    e = GetEnvironmentStrings();
    CHECK(e && !env_has(e, "FNVNX_PE") && !env_has(e, "FNVNX_PE_EMPTY") && env_has(e, "PATH"));
    FreeEnvironmentStringsA(e);
}

/* 3. current directory: the game folder at start, relative paths resolve against it and follow a change */
TEST(procenv_current_directory)
{
    CreateDirectoryA("Data", NULL);   /* the case's own state: the game directory's Data folder (other cases create it too) */
    char buf[260];
    CHECK_EQ(GetCurrentDirectoryA(sizeof buf, buf), 12);
    CHECK_STR(buf, "C:\\FalloutNV");
    CHECK_EQ(GetCurrentDirectoryA(12, buf), 13);   /* too small: the size with the NUL */
    CHECK_EQ(GetCurrentDirectoryA(13, buf), 12);   /* exactly fits */
    CHECK_EQ(GetCurrentDirectoryA(0, NULL), 13);
    CHECK_EQ(GetFullPathNameA("Data\\x.esm", sizeof buf, buf, NULL), 23);
    CHECK_STR(buf, "C:\\FalloutNV\\Data\\x.esm");
    s4_write_file("C:\\FalloutNV\\Data\\pe_probe.txt", "pe");
    CHECK(GetFileAttributesA("Data\\pe_probe.txt") != INVALID_FILE_ATTRIBUTES);   /* "Data\\..." relative opens */
    CHECK(SetCurrentDirectoryA("Data"));
    CHECK_EQ(GetCurrentDirectoryA(sizeof buf, buf), 17);
    CHECK_STR(buf, "C:\\FalloutNV\\Data");
    CHECK(GetFileAttributesA("pe_probe.txt") != INVALID_FILE_ATTRIBUTES);         /* now relative to Data */
    CHECK(GetFileAttributesA("Data\\pe_probe.txt") == INVALID_FILE_ATTRIBUTES);
    CHECK(!SetCurrentDirectoryA("NoSuchDir"));
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK(!SetCurrentDirectoryA("pe_probe.txt"));                                  /* a file is not a directory */
    CHECK(SetCurrentDirectoryA(".."));
    GetCurrentDirectoryA(sizeof buf, buf);
    CHECK_STR(buf, "C:\\FalloutNV");
    DeleteFileA("Data\\pe_probe.txt");
}

/* 4. process heap */
TEST(procenv_heap)
{
    HANDLE h = GetProcessHeap();
    CHECK(h != NULL && GetProcessHeap() == h);
    void *z = HeapAlloc(h, HEAP_ZERO_MEMORY, 100);
    CHECK(z != NULL);
    for (int i = 0; z && i < 100; i++)
        CHECK_EQ(((unsigned char *)z)[i], 0);
    CHECK(HeapSize(h, 0, z) >= 100);   /* at least the requested size (malloc_usable_size: a block may be larger) */
    CHECK_EQ((uintptr_t)z & 7, 0);
    void *a = HeapAlloc(h, 0, 0), *b = HeapAlloc(h, 0, 0);   /* zero bytes: unique, non-NULL */
    CHECK(a != NULL && b != NULL && a != b);
    CHECK(HeapSize(h, 0, a) != (SIZE_T)-1);
    void *c = HeapAlloc(h, 0, 1);
    CHECK(HeapSize(h, 0, c) >= 1);
    memset(z, 0xAB, 100);
    z = HeapReAlloc(h, HEAP_ZERO_MEMORY, z, 300);                 /* grow: old kept, new tail zeroed */
    CHECK(z != NULL);
    CHECK(HeapSize(h, 0, z) >= 300);
    CHECK_EQ(((unsigned char *)z)[99], 0xAB);
    for (int i = 100; z && i < 300; i++)
        CHECK_EQ(((unsigned char *)z)[i], 0);
    z = HeapReAlloc(h, 0, z, 10);                                 /* shrink */
    CHECK(HeapSize(h, 0, z) >= 10);
    CHECK_EQ(((unsigned char *)z)[9], 0xAB);
    CHECK(HeapReAlloc(h, 0, NULL, 8) == NULL);                    /* NULL block is an error, not an allocation */
    /* in place only: shrinking always works and keeps the pointer; a huge growth fails and leaves the block. Heap
     * blocks are plain malloc blocks (a LocalAlloc / CRT block may reach HeapFree, as Windows allows): HeapSize is
     * malloc_usable_size, at least the requested size */
    void *same = HeapReAlloc(h, HEAP_REALLOC_IN_PLACE_ONLY, z, 5);
    CHECK(same == z);
    CHECK(HeapSize(h, 0, z) >= 5);
    CHECK(HeapReAlloc(h, HEAP_REALLOC_IN_PLACE_ONLY, z, (SIZE_T)1 << 30) == NULL);
    CHECK(HeapSize(h, 0, z) >= 5);
    CHECK(HeapFree(h, 0, z));
    CHECK(HeapFree(h, 0, a));
    CHECK(HeapFree(h, 0, b));
    CHECK(HeapFree(h, 0, c));
    CHECK(HeapFree(h, 0, NULL));
    /* a private heap for the CRT's _crtheap */
    HANDLE p = HeapCreate(0, 0x10000, 0);
    CHECK(p != NULL);
    void *q = HeapAlloc(p, HEAP_ZERO_MEMORY, 64);
    CHECK(q != NULL && HeapSize(p, 0, q) >= 64);
    CHECK(HeapFree(p, 0, q));
    CHECK(HeapDestroy(p));
    void *r = HeapAlloc(GetProcessHeap(), 0, 16);   /* the process heap still works after it */
    CHECK(r != NULL);
    HeapFree(GetProcessHeap(), 0, r);
}

/* 5. startup info */
TEST(procenv_startup_info)
{
    STARTUPINFOA si;
    memset(&si, 0xCC, sizeof si);
    GetStartupInfoA(&si);
    CHECK_EQ(si.cb, sizeof si);
    CHECK(si.dwFlags == 0 || si.dwFlags == STARTF_USESHOWWINDOW);
    CHECK(si.lpReserved == NULL || !*si.lpReserved);
    CHECK(si.lpDesktop == NULL || !*si.lpDesktop || isprint((unsigned char)*si.lpDesktop));
    CHECK(si.lpTitle == NULL || !*si.lpTitle || isprint((unsigned char)*si.lpTitle));
    CHECK_EQ(si.cbReserved2, 0);        /* no inherited CRT handles */
    CHECK(si.lpReserved2 == NULL);
    CHECK_EQ(si.dwX | si.dwY | si.dwXSize | si.dwYSize | si.dwXCountChars | si.dwYCountChars | si.dwFillAttribute, 0);
    if (si.dwFlags & STARTF_USESHOWWINDOW)
        CHECK(si.wShowWindow == 1 /* SW_SHOWNORMAL */);
    CHECK(si.hStdInput == NULL && si.hStdOutput == NULL && si.hStdError == NULL);   /* STARTF_USESTDHANDLES is off */
}

/* 6. module identity */
TEST(procenv_module_identity)
{
    HMODULE m = GetModuleHandleA(NULL);
    CHECK_EQ((uintptr_t)m, 0x00400000);
    CHECK(GetModuleHandleW(NULL) == m);
    CHECK(GetModuleHandleA("FalloutNV.exe") == m);
    CHECK(GetModuleHandleA("falloutnv.EXE") == m);
    char buf[260];
    const char *exe = "C:\\FalloutNV\\FalloutNV.exe";
    DWORD len = (DWORD)strlen(exe);
    CHECK_EQ(GetModuleFileNameA(NULL, buf, sizeof buf), len);
    CHECK_STR(buf, exe);
    CHECK_EQ(GetModuleFileNameA(m, buf, sizeof buf), len);
    CHECK_STR(buf, exe);
    CHECK_EQ(GetModuleFileNameA(NULL, buf, 0), 0);
    memset(buf, 'x', sizeof buf);
    SetLastError(0);
    CHECK_EQ(GetModuleFileNameA(NULL, buf, 8), 8);   /* truncated: returns the size, NUL-terminated */
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_STR(buf, "C:\\Fall");
    SetLastError(0);
    CHECK_EQ(GetModuleFileNameA(NULL, buf, len), len);   /* one short of the NUL */
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_EQ(buf[len - 1], 0);
    SetLastError(0);
    CHECK_EQ(GetModuleFileNameA(NULL, buf, len + 1), len);   /* exactly fits */
    CHECK_EQ(GetLastError(), 0);
    const char *dlls[] = { "kernel32.dll", "KERNEL32.DLL", "user32.dll" };
    for (const char *d : dlls) {
        HMODULE k = GetModuleHandleA(d);
        CHECK(k != NULL);
        CHECK(k != m);
        CHECK(GetModuleFileNameA(k, buf, sizeof buf) > 0);
    }
    WCHAR wk[] = { 'K', 'E', 'R', 'N', 'E', 'L', '3', '2', '.', 'D', 'L', 'L', 0 };
    HMODULE k = GetModuleHandleW(wk);
    CHECK(k != NULL && k == GetModuleHandleA("kernel32.dll"));
    CHECK(GetProcAddress(k, "GetCommandLineA") != NULL);
    CHECK(GetProcAddress(k, "HeapAlloc") != NULL);
    SetLastError(0);
    CHECK(GetModuleHandleA("no_such_module.dll") == NULL);
    CHECK_EQ(GetLastError(), ERROR_MOD_NOT_FOUND);
}

/* 7. default thread stack: 1 MiB (the exe's SizeOfStackReserve), larger requests honoured */
static DWORD stack_probe(LPVOID out)
{
    pthread_attr_t a;
    size_t sz = 0;
    void *addr = NULL;
    if (!pthread_getattr_np(pthread_self(), &a)) {
        pthread_attr_getstack(&a, &addr, &sz);
        pthread_attr_destroy(&a);
    }
    *(size_t *)out = sz;
    return 0;
}

static size_t thread_stack(SIZE_T req)
{
    size_t got = 0;
    HANDLE t = test_CreateThread(NULL, req, stack_probe, &got, 0, NULL);
    if (!t)
        return 0;
    WaitForSingleObject(t, INFINITE);
    CloseHandle(t);
    return got;
}

TEST(procenv_thread_stack)
{
    size_t d = thread_stack(0);
    CHECK(d >= 1024 * 1024);                 /* the default is the exe's reserve, 1 MiB (glibc may reuse a cached larger stack) */
    CHECK(thread_stack(4 * 1024 * 1024) >= 4 * 1024 * 1024);
    CHECK(thread_stack(1024 * 1024) >= 1024 * 1024);
    CHECK(thread_stack(4096) >= 256 * 1024);  /* small requests are raised: the engine's task threads recurse */
}

/* 8. standard handles: a GUI process has no console and no standard handles (ENV_TRUTH F5) */
TEST(procenv_std_handles)
{
    DWORD ids[] = { STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE };
    for (DWORD id : ids) {
        CHECK(GetStdHandle(id) == NULL);   /* _ioinit treats NULL as "no handle" and skips the stream */
        DWORD mode = 0;
        CHECK(!GetConsoleMode(GetStdHandle(id), &mode));
    }
    DWORD put = 99;
    SetLastError(0);
    CHECK(!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), "stdout line\r\n", 13, &put, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK_EQ(GetFileType((HANDLE)(uintptr_t)0x7777), FILE_TYPE_UNKNOWN);
    CHECK(GetLastError() != ERROR_SUCCESS);
}
