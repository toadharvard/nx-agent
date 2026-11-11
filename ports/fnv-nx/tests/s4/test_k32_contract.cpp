/* test_k32_contract.cpp - kernel32 / shell32 / winsock imports on their documented (MSDN) domain: DOS device names and
 * special paths in CreateFile, GetFullPathName on Windows 7's canonical names, ... Expected values for the name rules
 * are Windows 7 results as recorded by Wine 9.0 conformance tests (ntdll/tests/path.c, kernel32/tests/console.c,
 * kernel32/tests/file.c). Environment rows: ENV_TRUTH F1 (one drive C:), F5 (no console), N1 (no network). */
#include "s4_test.h"

#include <stdlib.h>
#include <unistd.h>

#include "../../platform/win32/w32.h"

#ifndef ERROR_BAD_NETPATH
#define ERROR_BAD_NETPATH 53L
#endif
#ifndef ERROR_INVALID_HANDLE
#define ERROR_INVALID_HANDLE 6L
#endif
#ifndef ERROR_INVALID_FUNCTION
#define ERROR_INVALID_FUNCTION 1L
#endif
#ifndef DELETE
#define DELETE 0x00010000L
#endif
#ifndef FILE_SHARE_DELETE
#define FILE_SHARE_DELETE 0x00000004
#endif
#ifndef FILE_FLAG_DELETE_ON_CLOSE
#define FILE_FLAG_DELETE_ON_CLOSE 0x04000000
#endif
#ifndef FILE_FLAG_NO_BUFFERING
#define FILE_FLAG_NO_BUFFERING 0x20000000
#endif
#ifndef FILE_FLAG_WRITE_THROUGH
#define FILE_FLAG_WRITE_THROUGH 0x80000000u
#endif

/* ---- group 2: CreateFile devices and special paths */
static DWORD open_err(const char *name, DWORD acc = GENERIC_READ, DWORD disp = OPEN_EXISTING, DWORD fl = 0)
{
    SetLastError(0xdeadbeef);
    HANDLE h = CreateFileA(name, acc, 0, NULL, disp, fl, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        CloseHandle(h);
        return 0;
    }
    return GetLastError();
}

TEST(k32c_createfile_nul_device)
{
    static const char *names[] = { "NUL", "nul", "Nul.txt", "NUL:", "C:\\NUL", "C:\\no\\such\\dir\\nul", "Data\\nul.dat",
                                   "nul  ", "NUL  .x", "C:nul", "\\\\.\\NUL", "\\\\?\\nul", "\\\\.\\nul", "//./NUL",
                                   "C:\\FalloutNV\\NUL.log" };
    for (size_t i = 0; i < sizeof names / sizeof *names; i++) {
        HANDLE h = CreateFileA(names[i], GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
        CHECK(h != INVALID_HANDLE_VALUE);
        if (h == INVALID_HANDLE_VALUE) {
            fprintf(stderr, "  name %s err %lu\n", names[i], (unsigned long)GetLastError());
            continue;
        }
        CHECK_EQ(GetFileType(h), FILE_TYPE_CHAR);
        char b[8] = { 1, 1, 1, 1, 1, 1, 1, 1 };
        DWORD n = 77;
        CHECK(ReadFile(h, b, sizeof b, &n, NULL));      /* end of file at once */
        CHECK_EQ(n, 0);
        n = 77;
        CHECK(WriteFile(h, "abcdef", 6, &n, NULL));     /* discarded, all reported written */
        CHECK_EQ(n, 6);
        CHECK(FlushFileBuffers(h));
        CHECK_EQ(GetFileSize(h, NULL), 0);
        CHECK(CloseHandle(h));
    }
    /* any documented disposition opens the device; an invalid one is a parameter error as for files */
    for (DWORD d = CREATE_NEW; d <= OPEN_ALWAYS; d++)
        CHECK_EQ(open_err("NUL", GENERIC_WRITE, d), 0);
    CHECK_EQ(open_err("NUL", GENERIC_WRITE, 0), ERROR_INVALID_PARAMETER);
    CHECK_EQ(open_err("NUL", GENERIC_WRITE, 9), ERROR_INVALID_PARAMETER);
    /* a handle keeps the access it was opened with */
    HANDLE h = CreateFileA("NUL", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    DWORD n;
    CHECK(!WriteFile(h, "x", 1, &n, NULL));
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CloseHandle(h);
    h = CreateFileA("NUL", GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    char c;
    CHECK(!ReadFile(h, &c, 1, &n, NULL));
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CloseHandle(h);
    /* an overlapped NUL handle needs an OVERLAPPED; with one, a read ends at the end of file */
    h = CreateFileA("NUL", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK(!ReadFile(h, &c, 1, &n, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    OVERLAPPED ov;
    memset(&ov, 0, sizeof ov);
    CHECK(WriteFile(h, "ab", 2, &n, &ov));
    CHECK_EQ(n, 2);
    CHECK(!ReadFile(h, &c, 1, &n, &ov));
    CHECK_EQ(GetLastError(), ERROR_HANDLE_EOF);
    CloseHandle(h);
}

TEST(k32c_createfile_devices_and_namespaces)
{
    CreateDirectoryA("Data", NULL);
    /* ENV F5: no console in the process: opening the console devices fails with ERROR_INVALID_HANDLE */
    CHECK_EQ(open_err("CONOUT$", GENERIC_WRITE), ERROR_INVALID_HANDLE);
    CHECK_EQ(open_err("conin$", GENERIC_READ), ERROR_INVALID_HANDLE);
    CHECK_EQ(open_err("CON", GENERIC_READ), ERROR_INVALID_HANDLE);
    CHECK_EQ(open_err("CON", GENERIC_READ | GENERIC_WRITE), ERROR_INVALID_HANDLE);
    CHECK_EQ(open_err("\\\\.\\CONOUT$", GENERIC_WRITE), ERROR_INVALID_HANDLE);
    CHECK_EQ(open_err("\\\\.\\CON", GENERIC_WRITE), ERROR_INVALID_HANDLE);
    /* no serial or parallel hardware: the device names resolve to nothing */
    static const char *ports[] = { "COM1", "com9", "LPT1", "lpt9:", "PRN", "AUX", "C:\\x\\COM2.txt", "\\\\.\\COM1" };
    for (size_t i = 0; i < sizeof ports / sizeof *ports; i++)
        CHECK_EQ(open_err(ports[i]), ERROR_FILE_NOT_FOUND);
    /* COM0, LPT0, COM10 and longer names are ordinary file names */
    CHECK_EQ(open_err("COM0"), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(open_err("Data\\lpt0.txt"), ERROR_FILE_NOT_FOUND);
    HANDLE h = CreateFileA("COM10", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileType(h), FILE_TYPE_DISK);
    CloseHandle(h);
    DeleteFileA("COM10");
    /* pipes: no such pipe */
    CHECK_EQ(open_err("\\\\.\\pipe\\nosuch"), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(open_err("\\\\.\\PIPE\\a\\b", GENERIC_READ | GENERIC_WRITE), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(open_err("\\\\?\\pipe\\nosuch"), ERROR_FILE_NOT_FOUND);
    /* unknown device-namespace names */
    CHECK_EQ(open_err("\\\\.\\NoSuchDevice"), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(open_err("\\\\.\\NUL2"), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(open_err("\\\\.\\Z:"), ERROR_FILE_NOT_FOUND);
    /* ENV N1: no network: an unreachable server */
    CHECK_EQ(open_err("\\\\server\\share\\file.txt"), ERROR_BAD_NETPATH);
    CHECK_EQ(open_err("//server/share/file.txt", GENERIC_WRITE, CREATE_ALWAYS), ERROR_BAD_NETPATH);
    CHECK_EQ(open_err("\\\\server"), ERROR_BAD_NETPATH);
    CHECK_EQ(open_err("\\\\?\\UNC\\server\\share\\f"), ERROR_BAD_NETPATH);
    /* \\?\ and \\.\ prefixes on drive paths name the same files as the plain path */
    CreateDirectoryA("Data", NULL);
    s4_write_file("Data\\ns.txt", "xyz");
    h = CreateFileA("\\\\?\\C:\\FalloutNV\\Data\\ns.txt", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileSize(h, NULL), 3);
    CloseHandle(h);
    h = CreateFileA("\\\\.\\C:\\FalloutNV\\Data\\ns.txt", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    /* a directory needs BACKUP_SEMANTICS; the root volume path with a trailing separator is not a file */
    CHECK_EQ(open_err("C:\\FalloutNV\\Data"), ERROR_ACCESS_DENIED);
    CHECK_EQ(open_err("C:\\FalloutNV\\Data\\"), ERROR_PATH_NOT_FOUND);
    CHECK_EQ(open_err("C:\\", GENERIC_READ, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS), 0);
    CHECK_EQ(open_err("C:\\FalloutNV\\Data\\", GENERIC_READ, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS), 0);
    DeleteFileA("Data\\ns.txt");
}

TEST(k32c_createfile_flags)
{
    CreateDirectoryA("Data", NULL);
    /* DELETE_ON_CLOSE: removed at the last close, not before */
    DeleteFileA("Data\\doc.bin");
    HANDLE h1 = CreateFileA("Data\\doc.bin", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                            NULL, CREATE_NEW, FILE_FLAG_DELETE_ON_CLOSE, NULL);
    CHECK(h1 != INVALID_HANDLE_VALUE);
    HANDLE h2 = CreateFileA("Data\\doc.bin", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                            OPEN_EXISTING, 0, NULL);
    CHECK(h2 != INVALID_HANDLE_VALUE);
    CloseHandle(h1);
    CHECK(GetFileAttributesA("Data\\doc.bin") != INVALID_FILE_ATTRIBUTES);
    CloseHandle(h2);
    CHECK(GetFileAttributesA("Data\\doc.bin") == INVALID_FILE_ATTRIBUTES);
    /* NO_BUFFERING: sector-aligned buffer, length and file position (512-byte sectors of the C: volume) */
    char *raw = (char *)malloc(4096 + 512);
    char *buf = (char *)(((uintptr_t)raw + 511) & ~(uintptr_t)511);
    memset(buf, 'q', 2048);
    h1 = CreateFileA("Data\\nb.bin", GENERIC_READ | GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_FLAG_NO_BUFFERING, NULL);
    CHECK(h1 != INVALID_HANDLE_VALUE);
    DWORD n = 0;
    CHECK(WriteFile(h1, buf, 1024, &n, NULL));
    CHECK_EQ(n, 1024);
    CHECK(!WriteFile(h1, buf, 100, &n, NULL));                 /* length not a multiple */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(!WriteFile(h1, buf + 1, 512, &n, NULL));             /* buffer not aligned */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetFilePointer(h1, 7, NULL, FILE_BEGIN);
    CHECK(!ReadFile(h1, buf, 512, &n, NULL));                  /* position not aligned */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetFilePointer(h1, 512, NULL, FILE_BEGIN);
    memset(buf, 0, 512);
    CHECK(ReadFile(h1, buf, 512, &n, NULL));
    CHECK_EQ(n, 512);
    CHECK_EQ(buf[0], 'q');
    CHECK(ReadFile(h1, buf, 0, &n, NULL));                     /* a zero length is aligned */
    CloseHandle(h1);
    /* the same file through a buffered handle has no such rules */
    h1 = CreateFileA("Data\\nb.bin", GENERIC_READ, 0, NULL, OPEN_EXISTING, FILE_FLAG_WRITE_THROUGH | FILE_FLAG_RANDOM_ACCESS, NULL);
    CHECK(ReadFile(h1, buf + 1, 3, &n, NULL));
    CHECK_EQ(n, 3);
    CloseHandle(h1);
    free(raw);
    DeleteFileA("Data\\nb.bin");
}

/* ---- group 3: GetFullPathName */
static const char *fp(const char *in, const char **part = NULL)
{
    static char out[600];
    static char *fpart;
    fpart = (char *)0x1;
    DWORD n = GetFullPathNameA(in, sizeof out, out, &fpart);
    if (!n)
        return "(fail)";
    if (part)
        *part = fpart;
    return out;
}

TEST(k32c_getfullpath_names)
{
    SetCurrentDirectoryA("C:\\FalloutNV");
    struct { const char *in, *out, *file; } t[] = {
        { "c:/test", "C:\\test", "test" },
        { "c:/test/", "C:\\test\\", NULL },
        { "c:/test     ", "C:\\test", "test" },
        { "c:/test.", "C:\\test", "test" },
        { "c:/test  ....   ..   ", "C:\\test", "test" },
        { "c:/test/  ....   ..   ", "C:\\test\\", NULL },
        { "c:/test/..", "C:\\", NULL },
        { "c:/test/.. ", "C:\\test\\", NULL },
        { "c:/TEST", "C:\\TEST", "TEST" },
        { "c:/test./file", "C:\\test\\file", "file" },
        { "c:/test.. /file", "C:\\test.. \\file", "file" },
        { "c:/test/././file", "C:\\test\\file", "file" },
        { "c:/test\\.\\.\\file", "C:\\test\\file", "file" },
        { "c:/test/\\.\\.\\file", "C:\\test\\file", "file" },
        { "c:/test\\\\.\\.\\file", "C:\\test\\file", "file" },
        { "c:/test\\test1\\..\\.\\file", "C:\\test\\file", "file" },
        { "c:///test\\.\\.\\file//", "C:\\test\\file\\", NULL },
        { "c:///test\\..\\file\\..\\//", "C:\\", NULL },
        { "c:/test../file", "C:\\test.\\file", "file" },
        { "c:\\test\\*.", "C:\\test\\*", "*" },
        { "c:\\test\\a*b.*", "C:\\test\\a*b.*", "a*b.*" },
        { "C:\\test", "C:\\test", "test" },
        { "c:/", "C:\\", NULL },
        { "c:.", "C:\\FalloutNV", "FalloutNV" },
        { "c:foo", "C:\\FalloutNV\\foo", "foo" },
        { "c:foo/bar", "C:\\FalloutNV\\foo\\bar", "bar" },
        { "c:./foo", "C:\\FalloutNV\\foo", "foo" },
        { "\\foo", "C:\\foo", "foo" },
        { "foo", "C:\\FalloutNV\\foo", "foo" },
        { ".", "C:\\FalloutNV", "FalloutNV" },
        { "..", "C:\\", NULL },
        { "...", "C:\\FalloutNV\\", NULL },
        { "./foo", "C:\\FalloutNV\\foo", "foo" },
        { "foo/..", "C:\\FalloutNV", "FalloutNV" },
        { "\\FalloutNV\\nul", "\\\\.\\nul", NULL },
        { "C:\\nonexistent\\nul", "\\\\.\\nul", NULL },
        { "C:NUL.", "\\\\.\\NUL", NULL },
        { "C:NUL", "\\\\.\\NUL", NULL },
        { "AUX", "\\\\.\\AUX", NULL },
        { "COM1", "\\\\.\\COM1", NULL },
        { "c:\\nul:", "\\\\.\\nul", NULL },
        { "c:prn     ", "\\\\.\\prn", NULL },
        { "c:nul0", "C:\\FalloutNV\\nul0", "nul0" },
        { "c:\\lpt0.txt", "C:\\lpt0.txt", "lpt0.txt" },
        { "?<>*\"|:", "C:\\FalloutNV\\?<>*\"|:", "?<>*\"|:" },
        { "\\\\foo", "\\\\foo", NULL },
        { "//foo", "\\\\foo", NULL },
        { "\\/foo", "\\\\foo", NULL },
        { "//", "\\\\", NULL },
        { "//foo/", "\\\\foo\\", NULL },
        { "//foo/bar/baz", "\\\\foo\\bar\\baz", "baz" },
        { "//.", "\\\\.\\", NULL },
        { "//./", "\\\\.\\", NULL },
        { "//.//", "\\\\.\\", NULL },
        { "//./foo", "\\\\.\\foo", "foo" },
        { "//./foo/", "\\\\.\\foo\\", NULL },
        { "//./foo/bar", "\\\\.\\foo\\bar", "bar" },
        { "//./foo/.", "\\\\.\\foo", "foo" },
        { "//./foo/..", "\\\\.\\", NULL },
        { "//?/", "\\\\?\\", NULL },
        { "//?//", "\\\\?\\", NULL },
        { "//?/foo", "\\\\?\\foo", "foo" },
        { "//?/foo/", "\\\\?\\foo\\", NULL },
        { "//?/foo/bar", "\\\\?\\foo\\bar", "bar" },
        { "//?/foo/.", "\\\\?\\foo", "foo" },
        { "//?/foo/..", "\\\\?\\", NULL },
        { "CONIN$", "C:\\FalloutNV\\CONIN$", "CONIN$" },
        { "CONOUT$", "C:\\FalloutNV\\CONOUT$", "CONOUT$" },
        { "\\??\\foo", "C:\\??\\foo", "foo" },
    };
    for (size_t i = 0; i < sizeof t / sizeof *t; i++) {
        const char *part = (const char *)0x1;
        const char *r = fp(t[i].in, &part);
        s4_checks++;
        if (strcmp(r, t[i].out)) {
            s4_failures++;
            fprintf(stderr, "  FAIL full path of \"%s\": \"%s\" want \"%s\"\n", t[i].in, r, t[i].out);
            continue;
        }
        s4_checks++;
        const char *want = t[i].file ? (strlen(r) - strlen(t[i].file) + r) : NULL;
        if (part != want) {
            s4_failures++;
            fprintf(stderr, "  FAIL file part of \"%s\": %s want %s\n", t[i].in, part ? part : "NULL", t[i].file ? t[i].file : "NULL");
        }
    }
    /* the buffer protocol: too small returns the size needed with the NUL and leaves the part alone */
    char b[64];
    LPSTR part = (LPSTR)0x5;
    CHECK_EQ(GetFullPathNameA("foo", 4, b, &part), strlen("C:\\FalloutNV\\foo") + 1);
    CHECK(part == (LPSTR)0x5);
    CHECK_EQ(GetFullPathNameA("foo", (DWORD)strlen("C:\\FalloutNV\\foo") + 1, b, &part), strlen("C:\\FalloutNV\\foo"));
    CHECK_EQ(GetFullPathNameA("foo", (DWORD)strlen("C:\\FalloutNV\\foo"), b, &part), strlen("C:\\FalloutNV\\foo") + 1);
    CHECK_EQ(GetFullPathNameA("foo", 0, NULL, NULL), strlen("C:\\FalloutNV\\foo") + 1);
    CHECK_EQ(GetFullPathNameA("foo", sizeof b, b, NULL), strlen("C:\\FalloutNV\\foo"));
    /* the per-drive current directory of another drive: the "=D:" variable, else the root */
    CHECK_STR(fp("D:x"), "D:\\x");
    SetEnvironmentVariableA("=D:", "D:\\work");
    CHECK_STR(fp("d:x\\y"), "D:\\work\\x\\y");
    SetEnvironmentVariableA("=D:", NULL);
    CHECK_STR(fp("d:x"), "D:\\x");
}
