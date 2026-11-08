/* test_fs_conf.cpp - conformance of the file-system Win32 APIs with Windows behaviour as the CRT and the game use
 * them: return values, GetLastError codes, out parameters, 64-bit sizes and offsets (BSAs reach 1.5 GB). */
#include "s4_test.h"

#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#include "../../platform/win32/w32.h"

static void mkdir_win(const char *p) { CreateDirectoryA(p, NULL); }

static HANDLE open_ro(const char *p) { return CreateFileA(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL); }

TEST(conf_create_dispositions)
{
    mkdir_win("Data");
    mkdir_win("Data\\conf");
    DeleteFileA("Data\\conf\\d.bin");
    HANDLE h;
    /* OPEN_EXISTING / TRUNCATE_EXISTING on a missing file: 2 */
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_WRITE, 0, NULL, TRUNCATE_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    /* CREATE_ALWAYS / OPEN_ALWAYS on a missing file: success, last error 0 (not 183) */
    SetLastError(999);
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), 0);
    DWORD put;
    CHECK(WriteFile(h, "hello", 5, &put, NULL));
    CloseHandle(h);
    SetLastError(999);
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_READ, 0, NULL, OPEN_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK_EQ(GetFileSize(h, NULL), 5); /* OPEN_ALWAYS keeps the contents */
    CloseHandle(h);
    /* OPEN_EXISTING of an existing file: success and no 183 */
    SetLastError(999);
    h = open_ro("Data\\conf\\d.bin");
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), 0);
    CloseHandle(h);
    /* TRUNCATE_EXISTING empties it */
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_WRITE, 0, NULL, TRUNCATE_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileSize(h, NULL), 0);
    CloseHandle(h);
    /* TRUNCATE_EXISTING without write access is invalid */
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_READ, 0, NULL, TRUNCATE_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    /* CREATE_ALWAYS truncates an existing file and sets 183 */
    s4_write_file("Data\\conf\\d.bin", "abcdef");
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK_EQ(GetFileSize(h, NULL), 0);
    CloseHandle(h);
    /* CREATE_NEW on an existing file: 80 */
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_EXISTS);
    /* a bad disposition */
    h = CreateFileA("Data\\conf\\d.bin", GENERIC_READ, 0, NULL, 0, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* missing directory: 3; a file used as a directory: 3 */
    h = CreateFileA("Data\\conf\\nodir\\x.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    h = open_ro("Data\\conf\\d.bin\\x");
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    /* wildcards and an empty name are not file names */
    h = open_ro("Data\\conf\\*.bin");
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_INVALID_NAME);
    h = open_ro("");
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    /* CreateFileW takes the same paths */
    static const WCHAR wn[] = { 'D', 'a', 't', 'a', '\\', 'C', 'o', 'n', 'f', '\\', 'D', '.', 'b', 'i', 'n', 0 };
    h = CreateFileW(wn, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    static const WCHAR wm[] = { 'D', 'a', 't', 'a', '\\', 'n', 'o', 0 };
    h = CreateFileW(wm, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    h = CreateFileW(NULL, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    DeleteFileA("Data\\conf\\d.bin");
}

TEST(conf_directories_and_types)
{
    mkdir_win("Data");
    /* a directory needs FILE_FLAG_BACKUP_SEMANTICS */
    HANDLE h = CreateFileA("Data", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    h = CreateFileA("Data", 0, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileType(h), FILE_TYPE_DISK);
    char b[4];
    DWORD got = 7;
    CHECK(!ReadFile(h, b, 1, &got, NULL)); /* no data to read from a directory handle */
    CHECK_EQ(got, 0);
    CloseHandle(h);
    h = CreateFileA("Data", GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CHECK_EQ(GetFileAttributesA("Data") & FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_DIRECTORY);
    CHECK(!DeleteFileA("Data"));
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    /* GetFileType */
    s4_write_file("Data\\conf_t.bin", "x");
    h = open_ro("Data\\conf_t.bin");
    CHECK_EQ(GetFileType(h), FILE_TYPE_DISK);
    CloseHandle(h);
    CHECK_EQ(GetFileType(INVALID_HANDLE_VALUE), FILE_TYPE_UNKNOWN);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    DeleteFileA("Data\\conf_t.bin");
}

TEST(conf_access_and_close)
{
    mkdir_win("Data");
    s4_write_file("Data\\conf_a.bin", "abc");
    char b[8];
    DWORD n = 9;
    HANDLE w = CreateFileA("Data\\conf_a.bin", GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(w != INVALID_HANDLE_VALUE);
    CHECK(!ReadFile(w, b, 1, &n, NULL)); /* write-only handle */
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CHECK_EQ(n, 0);
    CHECK(FlushFileBuffers(w));
    CloseHandle(w);
    HANDLE r = open_ro("Data\\conf_a.bin");
    CHECK(!WriteFile(r, "z", 1, &n, NULL)); /* read-only handle */
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CHECK_EQ(n, 0);
    CHECK(!FlushFileBuffers(r));
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CHECK(!SetEndOfFile(r));
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CHECK(CloseHandle(r));
    CHECK(!CloseHandle(r));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(!CloseHandle(NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(!FlushFileBuffers(r));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK_EQ(GetFileSize(r, NULL), INVALID_FILE_SIZE);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK_EQ(SetFilePointer(r, 0, NULL, FILE_BEGIN), INVALID_SET_FILE_POINTER);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    /* a find handle is not a file handle, and the reverse */
    WIN32_FIND_DATAA fd;
    HANDLE f = FindFirstFileA("Data\\conf_a.bin", &fd);
    CHECK(f != INVALID_HANDLE_VALUE);
    CHECK(!ReadFile(f, b, 1, &n, NULL));
    CHECK(FindClose(f));
    CHECK(!FindClose(f));
    HANDLE fh = open_ro("Data\\conf_a.bin");
    CHECK(!FindClose(fh));
    CloseHandle(fh);
    DeleteFileA("Data\\conf_a.bin");
}

TEST(conf_read_write_values)
{
    mkdir_win("Data");
    s4_write_file("Data\\conf_rw.bin", "0123456789");
    HANDLE h = open_ro("Data\\conf_rw.bin");
    char b[16] = { 0 };
    DWORD got = 77;
    /* zero-length read succeeds with 0 */
    CHECK(ReadFile(h, b, 0, &got, NULL));
    CHECK_EQ(got, 0);
    /* a read across EOF is short and TRUE; the next one is TRUE with 0 (not ERROR_HANDLE_EOF) */
    CHECK(ReadFile(h, b, 100, &got, NULL));
    CHECK_EQ(got, 10);
    SetLastError(0);
    CHECK(ReadFile(h, b, 5, &got, NULL));
    CHECK_EQ(got, 0);
    CHECK_EQ(GetLastError(), 0);
    /* lpNumberOfBytesRead may be NULL for a synchronous read */
    SetFilePointer(h, 0, NULL, FILE_BEGIN);
    CHECK(ReadFile(h, b, 2, NULL, NULL));
    CHECK_EQ(b[1], '1');
    CloseHandle(h);
    /* WriteFile reports bytes written, zero-length succeeds, the position advances */
    h = CreateFileA("Data\\conf_rw.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    DWORD put = 5;
    CHECK(WriteFile(h, "ab", 0, &put, NULL));
    CHECK_EQ(put, 0);
    CHECK(WriteFile(h, "abcd", 4, &put, NULL));
    CHECK_EQ(put, 4);
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 4);
    /* write after a seek beyond EOF: the gap reads as zeros, seeking alone does not grow the file */
    CHECK_EQ(SetFilePointer(h, 8, NULL, FILE_BEGIN), 8);
    CHECK_EQ(GetFileSize(h, NULL), 4);
    CHECK(WriteFile(h, "Z", 1, &put, NULL));
    CHECK_EQ(GetFileSize(h, NULL), 9);
    CloseHandle(h);
    h = open_ro("Data\\conf_rw.bin");
    CHECK(ReadFile(h, b, 9, &got, NULL));
    CHECK_EQ(got, 9);
    CHECK(!memcmp(b, "abcd\0\0\0\0Z", 9));
    CloseHandle(h);
    DeleteFileA("Data\\conf_rw.bin");
}

TEST(conf_set_file_pointer)
{
    mkdir_win("Data");
    s4_write_file("Data\\conf_sp.bin", "0123456789");
    HANDLE h = CreateFileA("Data\\conf_sp.bin", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    /* returns the new low position; ERROR_SUCCESS is set so a caller can tell a position 0xFFFFFFFF */
    SetLastError(55);
    CHECK_EQ(SetFilePointer(h, 4, NULL, FILE_BEGIN), 4);
    CHECK_EQ(GetLastError(), 0);
    CHECK_EQ(SetFilePointer(h, 2, NULL, FILE_CURRENT), 6);
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_END), 10);
    CHECK_EQ(SetFilePointer(h, -1, NULL, FILE_END), 9);
    /* a negative seek before the start: INVALID_SET_FILE_POINTER and ERROR_NEGATIVE_SEEK, position unchanged */
    CHECK_EQ(SetFilePointer(h, -10, NULL, FILE_CURRENT), INVALID_SET_FILE_POINTER);
    CHECK_EQ(GetLastError(), 131);
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 9);
    CHECK_EQ(SetFilePointer(h, -1, NULL, FILE_BEGIN), INVALID_SET_FILE_POINTER); /* hi == NULL: signed distance */
    CHECK_EQ(GetLastError(), 131);
    LONG hi = -1; /* -1 : -1 is -1 as 64 bits */
    CHECK_EQ(SetFilePointer(h, -1, &hi, FILE_BEGIN), INVALID_SET_FILE_POINTER);
    CHECK_EQ(GetLastError(), 131);
    /* an unknown move method */
    CHECK_EQ(SetFilePointer(h, 0, NULL, 3), INVALID_SET_FILE_POINTER);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* seeking past EOF succeeds; reading there returns 0 bytes */
    CHECK_EQ(SetFilePointer(h, 1000, NULL, FILE_BEGIN), 1000);
    char b[4];
    DWORD got = 5;
    CHECK(ReadFile(h, b, 4, &got, NULL));
    CHECK_EQ(got, 0);
    /* the high part is an in/out parameter */
    hi = 0;
    CHECK_EQ(SetFilePointer(h, 3, &hi, FILE_BEGIN), 3);
    CHECK_EQ(hi, 0);
    /* SetFilePointerEx */
    LARGE_INTEGER d, np;
    d.QuadPart = -3;
    CHECK(SetFilePointerEx(h, d, &np, FILE_END));
    CHECK_EQ(np.QuadPart, 7);
    CHECK(SetFilePointerEx(h, d, NULL, FILE_CURRENT)); /* newpos may be NULL */
    d.QuadPart = -100;
    CHECK(!SetFilePointerEx(h, d, &np, FILE_BEGIN));
    CHECK_EQ(GetLastError(), 131);
    CloseHandle(h);
    DeleteFileA("Data\\conf_sp.bin");
}

TEST(conf_large_files)
{
    /* a sparse 5 GB file: sizes and positions past 2^31 and 2^32 */
    mkdir_win("Data");
    char hp[1100];
    CHECK_EQ(fnvnx_path_map("Data\\conf_big.bin", hp, sizeof hp), 0);
    DeleteFileA("Data\\conf_big.bin");
    s4_write_file("Data\\conf_big.bin", "");
    const long long big = 5LL * 1024 * 1024 * 1024 + 123;
    if (truncate(hp, (off_t)big) != 0) {
        fprintf(stderr, "  (no sparse files here: large-file checks skipped)\n");
        DeleteFileA("Data\\conf_big.bin");
        return;
    }
    HANDLE h = CreateFileA("Data\\conf_big.bin", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD hi = 0;
    CHECK_EQ(GetFileSize(h, &hi), (DWORD)(big & 0xFFFFFFFFu));
    CHECK_EQ(hi, (DWORD)(big >> 32));
    LARGE_INTEGER sz;
    CHECK(GetFileSizeEx(h, &sz));
    CHECK_EQ(sz.QuadPart, big);
    /* 0xC0000000 (3 GB) as a low part is a large unsigned position, not a negative one */
    LONG phi = 0;
    CHECK_EQ(SetFilePointer(h, (LONG)0xC0000000u, &phi, FILE_BEGIN), 0xC0000000u);
    CHECK_EQ(phi, 0);
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 0xC0000000u);
    phi = 1; /* 4 GB + 16 */
    CHECK_EQ(SetFilePointer(h, 16, &phi, FILE_BEGIN), 16);
    CHECK_EQ(phi, 1);
    LARGE_INTEGER d, np;
    d.QuadPart = -100;
    CHECK(SetFilePointerEx(h, d, &np, FILE_END));
    CHECK_EQ(np.QuadPart, big - 100);
    /* write and read back at 4 GB + 5 */
    d.QuadPart = 0x100000005LL;
    CHECK(SetFilePointerEx(h, d, &np, FILE_BEGIN));
    CHECK_EQ(np.QuadPart, 0x100000005LL);
    DWORD put = 0, got = 0;
    CHECK(WriteFile(h, "BSA!", 4, &put, NULL));
    CHECK_EQ(put, 4);
    CHECK(SetFilePointerEx(h, d, &np, FILE_BEGIN));
    char b[8] = { 0 };
    CHECK(ReadFile(h, b, 4, &got, NULL));
    CHECK_EQ(got, 4);
    CHECK(!memcmp(b, "BSA!", 4));
    /* overlapped read with a 64-bit offset */
    OVERLAPPED ov;
    memset(&ov, 0, sizeof ov);
    ov.Offset = 5;
    ov.OffsetHigh = 1;
    memset(b, 0, sizeof b);
    CHECK(ReadFile(h, b, 4, &got, &ov));
    CHECK(!memcmp(b, "BSA!", 4));
    /* a find over the file reports the 64-bit size */
    WIN32_FIND_DATAA fd;
    HANDLE f = FindFirstFileA("Data\\conf_big.bin", &fd);
    CHECK(f != INVALID_HANDLE_VALUE);
    CHECK_EQ(fd.nFileSizeHigh, (DWORD)(big >> 32));
    CHECK_EQ(fd.nFileSizeLow, (DWORD)(big & 0xFFFFFFFFu));
    FindClose(f);
    /* SetEndOfFile at the 4 GB position truncates there */
    d.QuadPart = 0x100000000LL;
    CHECK(SetFilePointerEx(h, d, &np, FILE_BEGIN));
    CHECK(SetEndOfFile(h));
    CHECK(GetFileSizeEx(h, &sz));
    CHECK_EQ(sz.QuadPart, 0x100000000LL);
    CloseHandle(h);
    DeleteFileA("Data\\conf_big.bin");
}

TEST(conf_find_files)
{
    mkdir_win("Data");
    mkdir_win("Data\\Fnd");
    s4_write_file("Data\\Fnd\\Fallout - Meshes.bsa", "12345");
    s4_write_file("Data\\Fnd\\fallout.esm", "1");
    s4_write_file("Data\\Fnd\\Update.BSA", "");
    mkdir_win("Data\\Fnd\\Sub");
    WIN32_FIND_DATAA fd;
    /* "*.bsa" matches case-insensitively, skips "." and "..", sorted, with sizes and attributes */
    HANDLE h = FindFirstFileA("Data\\Fnd\\*.bsa", &fd);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_STR(fd.cFileName, "Fallout - Meshes.bsa");
    CHECK_EQ(fd.nFileSizeLow, 5);
    CHECK_EQ(fd.nFileSizeHigh, 0);
    CHECK(!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY));
    CHECK(FindNextFileA(h, &fd));
    CHECK_STR(fd.cFileName, "Update.BSA");
    CHECK_EQ(fd.nFileSizeLow, 0);
    CHECK(!FindNextFileA(h, &fd));
    CHECK_EQ(GetLastError(), ERROR_NO_MORE_FILES);
    CHECK(!FindNextFileA(h, &fd)); /* and stays exhausted */
    CHECK_EQ(GetLastError(), ERROR_NO_MORE_FILES);
    CHECK(FindClose(h));
    /* "Data\\*": "." and ".." first, a directory has the DIRECTORY attribute and size 0 */
    h = FindFirstFileA("Data\\Fnd\\*", &fd);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_STR(fd.cFileName, ".");
    CHECK(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY);
    CHECK(FindNextFileA(h, &fd));
    CHECK_STR(fd.cFileName, "..");
    int subs = 0, files = 0;
    while (FindNextFileA(h, &fd)) {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            subs++;
            CHECK_STR(fd.cFileName, "Sub");
            CHECK_EQ(fd.nFileSizeLow, 0);
        } else
            files++;
    }
    CHECK_EQ(subs, 1);
    CHECK_EQ(files, 3);
    FindClose(h);
    /* "*.*", and the drive-qualified / forward-slash spellings */
    h = FindFirstFileA("data/fnd/*.*", &fd);
    CHECK(h != INVALID_HANDLE_VALUE);
    FindClose(h);
    h = FindFirstFileA("C:\\FalloutNV\\Data\\Fnd\\FALLOUT.*", &fd);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_STR(fd.cFileName, "fallout.esm");
    FindClose(h);
    /* an exact name that exists / not */
    h = FindFirstFileA("Data\\Fnd\\FALLOUT.ESM", &fd);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_STR(fd.cFileName, "fallout.esm");
    FindClose(h);
    CHECK(FindFirstFileA("Data\\Fnd\\nothing.esm", &fd) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(FindFirstFileA("Data\\Fnd\\*.xyz", &fd) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(FindFirstFileA("Data\\NoSuchDir\\*.bsa", &fd) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK(FindFirstFileA("Data\\Fnd\\fallout.esm\\*", &fd) == INVALID_HANDLE_VALUE); /* a file is no directory */
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK(FindFirstFileA("", &fd) == INVALID_HANDLE_VALUE);
    CHECK(FindFirstFileA(NULL, &fd) == INVALID_HANDLE_VALUE);
    DeleteFileA("Data\\Fnd\\Fallout - Meshes.bsa");
    DeleteFileA("Data\\Fnd\\fallout.esm");
    DeleteFileA("Data\\Fnd\\Update.BSA");
    RemoveDirectoryA("Data\\Fnd\\Sub");
    RemoveDirectoryA("Data\\Fnd");
}

TEST(conf_attributes_delete_move_copy)
{
    mkdir_win("Data");
    CHECK_EQ(GetFileAttributesA("Data\\conf_nothing.txt"), INVALID_FILE_ATTRIBUTES);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(GetFileAttributesA("Data\\nodir\\x.txt"), INVALID_FILE_ATTRIBUTES);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK_EQ(GetFileAttributesA("Data\\*.bsa"), INVALID_FILE_ATTRIBUTES);
    CHECK_EQ(GetLastError(), ERROR_INVALID_NAME);
    CHECK_EQ(GetFileAttributesA("C:\\"), FILE_ATTRIBUTE_DIRECTORY);
    CHECK_EQ(GetFileAttributesA("Data\\"), FILE_ATTRIBUTE_DIRECTORY); /* trailing separator */
    /* DeleteFile: missing 2, wildcard 123, read-only 5 */
    CHECK(!DeleteFileA("Data\\conf_nothing.txt"));
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(!DeleteFileA("Data\\*.txt"));
    CHECK_EQ(GetLastError(), ERROR_INVALID_NAME);
    s4_write_file("Data\\conf_ro.txt", "x");
    mkdir_win("Data");
    char hp[1100];
    fnvnx_path_map("Data\\conf_ro.txt", hp, sizeof hp);
    chmod(hp, 0444);
    CHECK(GetFileAttributesA("Data\\conf_ro.txt") & FILE_ATTRIBUTE_READONLY);
    CHECK(!DeleteFileA("Data\\conf_ro.txt"));
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    chmod(hp, 0644);
    CHECK(DeleteFileA("Data\\conf_ro.txt"));
    /* MoveFile: missing source 2, existing target 183, missing target dir 3 */
    s4_write_file("Data\\conf_m1.txt", "1");
    s4_write_file("Data\\conf_m2.txt", "2");
    CHECK(!MoveFileA("Data\\conf_none.txt", "Data\\conf_m3.txt"));
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(!MoveFileA("Data\\conf_m1.txt", "Data\\conf_m2.txt"));
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK(!MoveFileA("Data\\conf_m1.txt", "Data\\nodir\\m.txt"));
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK(MoveFileA("Data\\conf_m1.txt", "Data\\conf_m3.txt"));
    /* CopyFile: onto itself is a sharing violation and must not destroy the file */
    CHECK(!CopyFileA("Data\\conf_m2.txt", "DATA\\CONF_M2.TXT", FALSE));
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    HANDLE h = open_ro("Data\\conf_m2.txt");
    CHECK_EQ(GetFileSize(h, NULL), 1);
    CloseHandle(h);
    CHECK(!CopyFileA("Data\\conf_none.txt", "Data\\conf_c.txt", FALSE));
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(!CopyFileA("Data\\conf_m2.txt", "Data\\nodir\\c.txt", FALSE));
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK(!CopyFileA("Data\\conf_m2.txt", "Data\\conf_m3.txt", TRUE));
    CHECK_EQ(GetLastError(), ERROR_FILE_EXISTS);
    CHECK(CopyFileA("Data\\conf_m2.txt", "Data\\conf_m3.txt", FALSE));
    DeleteFileA("Data\\conf_m2.txt");
    DeleteFileA("Data\\conf_m3.txt");
    /* CreateDirectory */
    CHECK(CreateDirectoryA("Data\\conf_dir", NULL));
    CHECK(!CreateDirectoryA("Data\\conf_dir", NULL));
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK(!CreateDirectoryA("Data\\conf_dir\\a\\b", NULL));
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    CHECK(!CreateDirectoryA("", NULL));
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    RemoveDirectoryA("Data\\conf_dir");
}

TEST(conf_path_functions)
{
    char b[300];
    LPSTR part;
    /* GetFullPathNameA: length without the NUL; too small: size needed including the NUL, buffer untouched */
    DWORD want = (DWORD)strlen("C:\\FalloutNV\\Data\\x.txt");
    memset(b, 'Q', sizeof b);
    CHECK_EQ(GetFullPathNameA("Data\\x.txt", 5, b, &part), want + 1);
    CHECK_EQ(b[0], 'Q');
    CHECK_EQ(GetFullPathNameA("Data\\x.txt", sizeof b, b, &part), want);
    CHECK_STR(b, "C:\\FalloutNV\\Data\\x.txt");
    CHECK_STR(part, "x.txt");
    CHECK_EQ(GetFullPathNameA("data/./sub/../x.txt", sizeof b, b, &part), want); /* case of the input is kept */
    CHECK_STR(b, "C:\\FalloutNV\\data\\x.txt");
    CHECK_EQ(GetFullPathNameA("Data\\dir\\", sizeof b, b, &part), strlen("C:\\FalloutNV\\Data\\dir\\"));
    CHECK(part == NULL); /* trailing separator: no file part */
    CHECK_EQ(GetFullPathNameA("d:\\a\\b.txt", sizeof b, b, &part), strlen("D:\\a\\b.txt"));
    CHECK_STR(b, "D:\\a\\b.txt");
    CHECK_EQ(GetFullPathNameA("", sizeof b, b, &part), 0);
    CHECK_EQ(GetFullPathNameA("\\x", sizeof b, b, &part), strlen("C:\\x"));
    /* GetCurrentDirectoryA */
    DWORD cl = (DWORD)strlen("C:\\FalloutNV");
    CHECK_EQ(GetCurrentDirectoryA(0, NULL), cl + 1);
    CHECK_EQ(GetCurrentDirectoryA(cl, b), cl + 1); /* exactly too small */
    CHECK_EQ(GetCurrentDirectoryA(cl + 1, b), cl);
    CHECK_STR(b, "C:\\FalloutNV");
    /* GetModuleFileNameA: the exe path; truncation reports ERROR_INSUFFICIENT_BUFFER with the buffer size */
    DWORD el = (DWORD)strlen("C:\\FalloutNV\\FalloutNV.exe");
    CHECK_EQ(GetModuleFileNameA(NULL, b, sizeof b), el);
    CHECK_STR(b, "C:\\FalloutNV\\FalloutNV.exe");
    SetLastError(0);
    CHECK_EQ(GetModuleFileNameA(NULL, b, 8), 8);
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_STR(b, "C:\\Fall");
    /* GetDriveTypeA */
    CHECK_EQ(GetDriveTypeA("C:\\"), DRIVE_FIXED);
    CHECK_EQ(GetDriveTypeA("c:\\"), DRIVE_FIXED);
    CHECK_EQ(GetDriveTypeA(NULL), DRIVE_FIXED);
    CHECK_EQ(GetDriveTypeA("Q:\\"), DRIVE_NO_ROOT_DIR);
}

TEST(conf_flush_and_set_end_of_file)
{
    mkdir_win("Data");
    s4_write_file("Data\\conf_eof.bin", "0123456789");
    HANDLE h = CreateFileA("Data\\conf_eof.bin", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(FlushFileBuffers(h));
    SetFilePointer(h, 4, NULL, FILE_BEGIN);
    CHECK(SetEndOfFile(h));
    CHECK_EQ(GetFileSize(h, NULL), 4);
    SetFilePointer(h, 20, NULL, FILE_BEGIN); /* SetEndOfFile past the end extends */
    CHECK(SetEndOfFile(h));
    CHECK_EQ(GetFileSize(h, NULL), 20);
    LARGE_INTEGER sz;
    CHECK(GetFileSizeEx(h, &sz));
    CHECK_EQ(sz.QuadPart, 20);
    CloseHandle(h);
    DeleteFileA("Data\\conf_eof.bin");
}
