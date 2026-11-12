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
    CHECK_STR(rel("Data\\Meshes\\x.nif"), "/Data/Meshes/x.nif");                 /* relative to cwd */
    CHECK_STR(rel("C:\\FalloutNV\\FalloutNV.exe"), "/FalloutNV.exe");
    CHECK_STR(rel("c:/falloutnv//Data/./a.txt"), "/Data/a.txt");                  /* '/' '//' '.' */
    CHECK_STR(rel("\\FalloutNV\\Data\\b"), "/Data/b");                             /* drive-rooted */
    CHECK_STR(rel("Data\\..\\..\\..\\FalloutNV\\c"), "/c");                        /* '..' stops at C:\ */
    CHECK_STR(rel("C:\\FalloutNV\\..\\Windows\\win.ini"), "/drive_c/Windows/win.ini");  /* outside the mounts */
    CHECK_STR(rel("D:\\Other\\f"), "/drive_d/Other/f");
    CHECK_STR(rel("Data\\name.txt. "), "/Data/name.txt");                         /* trailing dot/space */
    CHECK_STR(rel("C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\Fallout.ini"),
              "/user/Documents/My Games/FalloutNV/Fallout.ini");
    CHECK_STR(rel("\\\\?\\C:\\FalloutNV\\x"), "/x");
    char b[64];
    CHECK_EQ(fnvnx_path_map("\\\\server\\share\\x", b, sizeof b), ERROR_PATH_NOT_FOUND);
}

TEST(path_case_insensitive_resolution)
{
    mkdir_host("Data/Textures/Interface");
    s4_write_file("Data\\Textures\\Interface\\Loading.DDS", "x");
    /* existing components resolve to the host spelling, new ones keep the game's */
    CHECK_STR(rel("DATA\\textures\\INTERFACE\\loading.dds"), "/Data/Textures/Interface/Loading.DDS");
    CHECK_STR(rel("data\\TEXTURES\\NewDir\\New.dds"), "/Data/Textures/NewDir/New.dds");
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
    mkdir_host("Data");
    CHECK(SetCurrentDirectoryA("data"));
    GetCurrentDirectoryA(sizeof b, b);
    CHECK_STR(b, "C:\\FalloutNV\\data");
    CHECK_STR(rel("Sound\\fx.wav"), "/Data/Sound/fx.wav");
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
    mkdir_host("Data/Find");
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

/* NX1 check 5: the SD layout as the player copies it (sdmc:/switch/fnv = <root>: FalloutNV.exe and Data/ from a PC
 * install, any case), the game's own spellings of its files, and the profile directories it writes. */
static void touch_host(const char *sub)
{
    char p[1100];
    snprintf(p, sizeof p, "%s/%s", s4_root(), sub);
    FILE *f = fopen(p, "wb");
    CHECK(f != NULL);
    if (f)
        fclose(f);
}

extern "C" void fnvnx_path_reset(const char *root);

static void nx1_root(const char *sub)   /* a root of its own: the other tests' Data/ must not meet this DATA/ */
{
    char p[1100];
    snprintf(p, sizeof p, "%s/%s", s4_root(), sub);
    fnvnx_path_reset(p);
}

TEST(nx1_sd_layout_game_paths)
{
    nx1_root("nx1a");
    mkdir_host("nx1a/DATA/Sound/Voice/FALLOUTNV.ESM");    /* a directory named like the plugin, to trip a lazy lookup */
    mkdir_host("nx1a/DATA/music/Explore");
    touch_host("nx1a/FALLOUTNV.EXE");
    touch_host("nx1a/DATA/falloutnv.esm");
    touch_host("nx1a/DATA/fallout - meshes.BSA");
    touch_host("nx1a/DATA/Fallout - Textures.bsa");
    static const struct { const char *win, *want; } cases[] = {
        { "C:\\FalloutNV\\FalloutNV.exe", "/nx1a/FALLOUTNV.EXE" },
        { "Data\\FalloutNV.esm", "/nx1a/DATA/falloutnv.esm" },
        { "Data\\Fallout - Meshes.bsa", "/nx1a/DATA/fallout - meshes.BSA" },
        { "data\\fallout - textures.BSA", "/nx1a/DATA/Fallout - Textures.bsa" },
        { "Data\\Music\\Explore\\x.mp3", "/nx1a/DATA/music/Explore/x.mp3" },
        { "Data\\Sound\\Voice\\FalloutNV.esm\\a.ogg", "/nx1a/DATA/Sound/Voice/FALLOUTNV.ESM/a.ogg" },
    };
    for (const auto &c : cases)
        CHECK_STR(rel(c.win), c.want);
    HANDLE h = CreateFileA("Data\\Fallout - Meshes.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    fnvnx_path_reset(s4_root());
}

TEST(nx1_profile_writable)
{
    nx1_root("nx1b");
    /* path_reset made the profile folders; the game creates Saves\ itself and writes its INIs */
    const char *docs = "C:\\Users\\Player\\Documents\\My Games\\FalloutNV";
    char p[300];
    snprintf(p, sizeof p, "%s\\Saves", docs);
    CHECK(CreateDirectoryA(p, NULL) || GetLastError() == ERROR_ALREADY_EXISTS);
    snprintf(p, sizeof p, "%s\\Saves\\Save 1.fos", docs);
    HANDLE h = CreateFileA(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD n = 0;
    CHECK(WriteFile(h, "FO3SAVEGAME", 11, &n, NULL) && n == 11);
    CloseHandle(h);
    CHECK_STR(rel(p), "/nx1b/user/Documents/My Games/FalloutNV/Saves/Save 1.fos");
    snprintf(p, sizeof p, "%s\\FalloutPrefs.ini", docs);
    CHECK(WritePrivateProfileStringA("Display", "iSize W", "1280", p));
    char v[16] = "";
    GetPrivateProfileStringA("Display", "iSize W", "", v, sizeof v, p);
    CHECK_STR(v, "1280");
    CHECK_STR(rel("C:\\Users\\Player\\AppData\\Local\\FalloutNV\\plugins.txt"), "/nx1b/user/AppData/Local/FalloutNV/plugins.txt");
    fnvnx_path_reset(s4_root());
}
