/* test_fs.cpp - Windows path mapping (case-insensitive), file handles, FindFirstFile, CRT file wrappers. */
#include "s4_test.h"

#include <stdlib.h>
#include <sys/stat.h>

#include "fnvnx_msvcrt.h"

static const char *host(const char *win)
{
    static char buf[1024];
    if (fnvnx_path_map(win, buf, sizeof buf))
        return "(error)";
    return buf;
}

static const char *rel(const char *win)
{
    const char *h = host(win);
    size_t l = strlen(s4_root());
    return !strncmp(h, s4_root(), l) ? h + l : h;
}

static void mkdir_host(const char *sub)
{
    char p[1100];
    snprintf(p, sizeof p, "%s/%s", s4_root(), sub);
    char cmd[1200];
    snprintf(cmd, sizeof cmd, "mkdir -p '%s'", p);
    CHECK(system(cmd) == 0);
}

TEST(path_mapping_rules)
{
    CHECK_STR(rel("Data\\Meshes\\x.nif"), "/game/Data/Meshes/x.nif");                 /* relative to cwd */
    CHECK_STR(rel("C:\\FalloutNV\\FalloutNV.exe"), "/game/FalloutNV.exe");
    CHECK_STR(rel("c:/falloutnv//Data/./a.txt"), "/game/Data/a.txt");                  /* '/' '//' '.' */
    CHECK_STR(rel("\\FalloutNV\\Data\\b"), "/game/Data/b");                             /* drive-rooted */
    CHECK_STR(rel("Data\\..\\..\\..\\FalloutNV\\c"), "/game/c");                        /* '..' stops at C:\ */
    CHECK_STR(rel("C:\\FalloutNV\\..\\Windows\\win.ini"), "/drive_c/Windows/win.ini");  /* outside the mounts */
    CHECK_STR(rel("D:\\Other\\f"), "/drive_d/Other/f");
    CHECK_STR(rel("Data\\name.txt. "), "/game/Data/name.txt");                         /* trailing dot/space */
    CHECK_STR(rel("C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\Fallout.ini"),
              "/user/Documents/My Games/FalloutNV/Fallout.ini");
    CHECK_STR(rel("\\\\?\\C:\\FalloutNV\\x"), "/game/x");
    char b[64];
    CHECK_EQ(fnvnx_path_map("\\\\server\\share\\x", b, sizeof b), ERROR_PATH_NOT_FOUND);
}

TEST(path_case_insensitive_resolution)
{
    mkdir_host("game/Data/Textures/Interface");
    s4_write_file("Data\\Textures\\Interface\\Loading.DDS", "x");
    /* existing components resolve to the host spelling, new ones keep the game's */
    CHECK_STR(rel("DATA\\textures\\INTERFACE\\loading.dds"), "/game/Data/Textures/Interface/Loading.DDS");
    CHECK_STR(rel("data\\TEXTURES\\NewDir\\New.dds"), "/game/Data/Textures/NewDir/New.dds");
    HANDLE h = CreateFileA("data\\textures\\interface\\LOADING.dds", GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
}

TEST(cwd_and_full_path)
{
    char b[300];
    CHECK_EQ(GetCurrentDirectoryA(sizeof b, b), strlen("C:\\FalloutNV"));
    CHECK_STR(b, "C:\\FalloutNV");
    CHECK_EQ(GetCurrentDirectoryA(4, b), strlen("C:\\FalloutNV") + 1); /* too small: size needed */
    mkdir_host("game/Data");
    CHECK(SetCurrentDirectoryA("data"));
    GetCurrentDirectoryA(sizeof b, b);
    CHECK_STR(b, "C:\\FalloutNV\\data");
    CHECK_STR(rel("Sound\\fx.wav"), "/game/Data/Sound/fx.wav");
    CHECK(!SetCurrentDirectoryA("NoSuchDir"));
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK(SetCurrentDirectoryA("C:\\FalloutNV"));
    LPSTR part = NULL;
    CHECK_EQ(GetFullPathNameA("Data\\..\\Fallout.ini", sizeof b, b, &part), strlen("C:\\FalloutNV\\Fallout.ini"));
    CHECK_STR(b, "C:\\FalloutNV\\Fallout.ini");
    CHECK_STR(part, "Fallout.ini");
    CHECK_EQ(GetModuleFileNameA(NULL, b, sizeof b), strlen("C:\\FalloutNV\\FalloutNV.exe"));
    CHECK_STR(b, "C:\\FalloutNV\\FalloutNV.exe");
}

TEST(file_create_dispositions_and_errors)
{
    DeleteFileA("Data\\disp.bin");
    HANDLE h = CreateFileA("Data\\disp.bin", GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    h = CreateFileA("data\\DISP.bin", GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_EXISTS);
    h = CreateFileA("Data\\disp.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CloseHandle(h);
    h = CreateFileA("Data\\missing.bin", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    h = CreateFileA("Data\\NoDir\\missing.bin", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    h = CreateFileA("Data\\disp.bin", GENERIC_READ, 0, NULL, OPEN_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CloseHandle(h);
    CHECK(CloseHandle(h) == FALSE); /* stale handle */
    CHECK(DeleteFileA("DATA\\disp.BIN"));
    CHECK(!DeleteFileA("Data\\disp.bin"));
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
}

TEST(file_read_write_seek)
{
    s4_write_file("Data\\rw.bin", "0123456789");
    HANDLE h = CreateFileA("Data\\rw.bin", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD hi = 7;
    CHECK_EQ(GetFileSize(h, &hi), 10);
    CHECK_EQ(hi, 0);
    char b[16] = { 0 };
    DWORD got = 0;
    CHECK_EQ(SetFilePointer(h, 3, NULL, FILE_BEGIN), 3);
    CHECK(ReadFile(h, b, 4, &got, NULL));
    CHECK_EQ(got, 4);
    CHECK_STR(b, "3456");
    CHECK_EQ(SetFilePointer(h, -2, NULL, FILE_END), 8);
    CHECK(ReadFile(h, b, 10, &got, NULL));
    CHECK_EQ(got, 2);
    CHECK(ReadFile(h, b, 10, &got, NULL)); /* EOF: success with 0 bytes */
    CHECK_EQ(got, 0);
    CHECK_EQ(SetFilePointer(h, -20, NULL, FILE_CURRENT), INVALID_SET_FILE_POINTER);
    LARGE_INTEGER d, np;
    d.QuadPart = 5;
    CHECK(SetFilePointerEx(h, d, &np, FILE_BEGIN));
    CHECK_EQ(np.QuadPart, 5);
    CHECK(SetEndOfFile(h));
    CHECK_EQ(GetFileSize(h, NULL), 5);
    DWORD put;
    CHECK(WriteFile(h, "AB", 2, &put, NULL));
    CHECK_EQ(GetFileSize(h, NULL), 7);
    /* overlapped: positional, completes synchronously and signals the event */
    OVERLAPPED ov;
    memset(&ov, 0, sizeof ov);
    ov.Offset = 1;
    ov.hEvent = CreateEventA(NULL, TRUE, FALSE, NULL);
    memset(b, 0, sizeof b);
    CHECK(ReadFile(h, b, 3, &got, &ov));
    CHECK_STR(b, "123");
    CHECK_EQ(ov.InternalHigh, 3);
    CHECK_EQ(WaitForSingleObject(ov.hEvent, 0), WAIT_OBJECT_0);
    ov.Offset = 100;
    CHECK(!ReadFile(h, b, 3, &got, &ov));
    CHECK_EQ(GetLastError(), ERROR_HANDLE_EOF);
    CloseHandle(ov.hEvent);
    FILETIME w;
    CHECK(GetFileTime(h, NULL, NULL, &w));
    SYSTEMTIME st;
    CHECK(FileTimeToSystemTime(&w, &st));
    CHECK(st.wYear >= 2024);
    CloseHandle(h);
    CHECK(ReadFile(h, b, 1, &got, NULL) == FALSE);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
}

TEST(find_first_file)
{
    mkdir_host("game/Data/Find");
    s4_write_file("Data\\Find\\b.esp", "1");
    s4_write_file("Data\\Find\\A.esm", "22");
    s4_write_file("Data\\Find\\c.ESP", "333");
    s4_write_file("Data\\Find\\readme", "4");
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA("data\\find\\*.esp", &fd);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_STR(fd.cFileName, "b.esp");
    CHECK_EQ(fd.nFileSizeLow, 1);
    CHECK(!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY));
    CHECK(FindNextFileA(h, &fd));
    CHECK_STR(fd.cFileName, "c.ESP");
    CHECK(!FindNextFileA(h, &fd));
    CHECK_EQ(GetLastError(), ERROR_NO_MORE_FILES);
    CHECK(FindClose(h));
    /* "*" lists . and .. first, then names in case-insensitive order */
    h = FindFirstFileA("Data\\Find\\*", &fd);
    const char *want[] = { ".", "..", "A.esm", "b.esp", "c.ESP", "readme" };
    int i = 0;
    do {
        if (i < 6)
            CHECK_STR(fd.cFileName, want[i]);
        i++;
    } while (FindNextFileA(h, &fd));
    CHECK_EQ(i, 6);
    FindClose(h);
    h = FindFirstFileA("Data\\Find\\*.*", &fd); /* *.* matches names without a dot too */
    i = 0;
    do
        i++;
    while (FindNextFileA(h, &fd));
    CHECK_EQ(i, 6);
    FindClose(h);
    h = FindFirstFileA("Data\\Find\\?.esm", &fd);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_STR(fd.cFileName, "A.esm");
    FindClose(h);
    CHECK(FindFirstFileA("Data\\Find\\*.bsa", &fd) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(FindFirstFileA("Data\\NoSuch\\*.bsa", &fd) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    h = FindFirstFileA("Data\\FIND", &fd); /* no wildcard: the entry itself */
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_STR(fd.cFileName, "Find");
    CHECK(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY);
    FindClose(h);
}

TEST(file_attributes_move_copy_dirs)
{
    CHECK_EQ(GetFileAttributesA("Data"), FILE_ATTRIBUTE_DIRECTORY);
    CHECK_EQ(GetFileAttributesA("Data\\nothing"), INVALID_FILE_ATTRIBUTES);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(CreateDirectoryA("Data\\NewDir", NULL));
    CHECK(!CreateDirectoryA("data\\newdir", NULL));
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK(!CreateDirectoryA("Data\\X\\Y", NULL));
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    s4_write_file("Data\\NewDir\\a.txt", "hello");
    CHECK(GetFileAttributesA("Data\\NewDir\\A.TXT") & FILE_ATTRIBUTE_ARCHIVE);
    CHECK(CopyFileA("Data\\NewDir\\a.txt", "Data\\NewDir\\b.txt", TRUE));
    CHECK(!CopyFileA("Data\\NewDir\\a.txt", "Data\\NewDir\\b.txt", TRUE));
    CHECK_EQ(GetLastError(), ERROR_FILE_EXISTS);
    CHECK(CopyFileA("Data\\NewDir\\a.txt", "Data\\NewDir\\b.txt", FALSE));
    CHECK(!MoveFileA("Data\\NewDir\\a.txt", "Data\\NewDir\\b.txt"));
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK(MoveFileA("Data\\NewDir\\a.txt", "Data\\NewDir\\c.txt"));
    CHECK_EQ(GetFileAttributesA("Data\\NewDir\\a.txt"), INVALID_FILE_ATTRIBUTES);
    HANDLE h = CreateFileA("Data\\NewDir\\c.txt", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    char b[8] = { 0 };
    DWORD got;
    ReadFile(h, b, 7, &got, NULL);
    CHECK_STR(b, "hello");
    CloseHandle(h);
    CHECK(!RemoveDirectoryA("Data\\NewDir"));
    CHECK_EQ(GetLastError(), ERROR_DIR_NOT_EMPTY);
    DeleteFileA("Data\\NewDir\\b.txt");
    DeleteFileA("Data\\NewDir\\c.txt");
    CHECK(RemoveDirectoryA("Data\\NewDir"));
}

TEST(crt_file_wrappers)
{
    CreateDirectoryA("Data", NULL);
    s4_write_file("Data\\crt.txt", "line1\r\nline2\r\n");
    FILE *f = fnvnx_fopen("DATA\\Crt.TXT", "rt");
    CHECK(f != NULL);
    if (f) {
        char b[32];
        CHECK(fgets(b, sizeof b, f) != NULL);
        CHECK_STR(b, "line1\r\n"); /* binary: text-mode CRLF translation is a known gap */
        CHECK(_fseeki64(f, 0, SEEK_END) == 0);
        CHECK_EQ(_ftelli64(f), 14);
        fclose(f);
    }
    FILE *g = NULL;
    CHECK_EQ(fnvnx_fopen_s(&g, "Data\\crt2.txt", "w"), 0);
    if (g) {
        fputs("x", g);
        fclose(g);
    }
    CHECK_EQ(fnvnx_access("data\\crt2.TXT", 0), 0);
    CHECK_EQ(fnvnx_access("data\\none.txt", 0), -1);
    struct fnvnx_msvc_stat st;
    CHECK_EQ(sizeof st, 48);
    CHECK_EQ(offsetof(struct fnvnx_msvc_stat, st_size), 20);
    CHECK_EQ(offsetof(struct fnvnx_msvc_stat, st_mtime_), 32);
    CHECK_EQ(fnvnx_stat("Data\\crt.txt", &st), 0);
    CHECK_EQ(st.st_size, 14);
    CHECK((st.st_mode & 0170000) == 0100000);
    CHECK_EQ(fnvnx_rename("Data\\crt2.txt", "Data\\crt.txt"), -1); /* never replaces */
    CHECK_EQ(fnvnx_rename("Data\\crt2.txt", "Data\\crt3.txt"), 0);
    CHECK_EQ(fnvnx_remove("Data\\crt3.txt"), 0);
    CHECK_EQ(fnvnx_mkdir("Data\\CrtDir"), 0);
    CHECK_EQ(fnvnx_rmdir("Data\\crtdir"), 0);
    char cwd[64];
    CHECK_STR(fnvnx_getcwd(cwd, sizeof cwd), "C:\\FalloutNV");
}
