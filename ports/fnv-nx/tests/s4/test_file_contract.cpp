/* test_file_contract.cpp - the file imports on their documented (MSDN) domain: sharing modes, the five creation
 * dispositions, DELETE_ON_CLOSE, FILE_APPEND_DATA, READONLY, OVERLAPPED on synchronous and overlapped handles,
 * DeleteFile/MoveFile/CopyFile against open handles. Each check is result plus GetLastError. */
#include "s4_test.h"

#include <stdlib.h>
#include <unistd.h>

#include "../../platform/win32/w32.h"

#ifndef DELETE
#define DELETE 0x00010000L
#endif
#ifndef FILE_SHARE_DELETE
#define FILE_SHARE_DELETE 0x00000004
#endif
#ifndef FILE_APPEND_DATA
#define FILE_APPEND_DATA 0x0004
#endif
#ifndef FILE_FLAG_DELETE_ON_CLOSE
#define FILE_FLAG_DELETE_ON_CLOSE 0x04000000
#endif
#ifndef ERROR_FILE_EXISTS
#define ERROR_FILE_EXISTS 80L
#endif
#ifndef ERROR_ALREADY_EXISTS
#define ERROR_ALREADY_EXISTS 183L
#endif
#ifndef ERROR_INVALID_PARAMETER
#define ERROR_INVALID_PARAMETER 87L
#endif

#define D "Data\\fc\\"

static void setup(void) { CreateDirectoryA("Data", NULL); CreateDirectoryA("Data\\fc", NULL); }
static int exists(const char *p) { return GetFileAttributesA(p) != INVALID_FILE_ATTRIBUTES; }
static HANDLE open_(const char *p, DWORD acc, DWORD share, DWORD disp, DWORD fl = 0)
{
    return CreateFileA(p, acc, share, NULL, disp, fl, NULL);
}
static void rm_host(const char *win)
{
    char h[1024];
    if (!fnvnx_path_map(win, h, sizeof h))
        unlink(h);
}
static void make(const char *p, const char *data)
{
    rm_host(p);
    s4_write_file(p, data);
}

TEST(fc_sharing_matrix)
{
    setup();
    make(D "share.bin", "0123456789");
    const DWORD acc[3] = { GENERIC_READ, GENERIC_WRITE, DELETE };
    const DWORD need[3] = { FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE };
    int bad = 0, n = 0;
    for (int a1 = 0; a1 < 3; a1++)
    for (int s1 = 0; s1 < 8; s1++)
    for (int a2 = 0; a2 < 3; a2++)
    for (int s2 = 0; s2 < 8; s2++) {
        HANDLE h1 = open_(D "share.bin", acc[a1], (DWORD)s1, OPEN_EXISTING);
        CHECK(h1 != INVALID_HANDLE_VALUE);   /* the first open of a file never conflicts */
        SetLastError(999);
        HANDLE h2 = open_(D "share.bin", acc[a2], (DWORD)s2, OPEN_EXISTING);
        int ok = (s1 & (int)need[a2]) && (s2 & (int)need[a1]);
        n++;
        if (ok != (h2 != INVALID_HANDLE_VALUE) || (!ok && GetLastError() != ERROR_SHARING_VIOLATION))
            bad++;
        if (h2 != INVALID_HANDLE_VALUE) CloseHandle(h2);
        CloseHandle(h1);
    }
    CHECK_EQ(n, 576);
    CHECK_EQ(bad, 0);
    /* a closed handle releases its share mode; names fold case; access 0 never conflicts */
    HANDLE h1 = open_(D "share.bin", GENERIC_READ, 0, OPEN_EXISTING);
    HANDLE h2 = open_("data\\FC\\SHARE.BIN", GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING);
    CHECK(h2 == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    HANDLE h3 = open_(D "share.bin", 0, 0, OPEN_EXISTING);
    CHECK(h3 != INVALID_HANDLE_VALUE);
    CloseHandle(h3);
    CloseHandle(h1);
    h2 = open_("data\\FC\\SHARE.BIN", GENERIC_READ, 0, OPEN_EXISTING);
    CHECK(h2 != INVALID_HANDLE_VALUE);
    CloseHandle(h2);
    /* a refused CREATE_ALWAYS must not truncate */
    h1 = open_(D "share.bin", GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING);
    h2 = open_(D "share.bin", GENERIC_WRITE, FILE_SHARE_READ, CREATE_ALWAYS);
    CHECK(h2 == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CHECK_EQ(GetFileSize(h1, NULL), 10);
    CloseHandle(h1);
}

TEST(fc_delete_move_open_handles)
{
    setup();
    make(D "dm.bin", "x");
    HANDLE h = open_(D "dm.bin", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING);
    CHECK(!DeleteFileA(D "dm.bin"));
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CHECK(!MoveFileA(D "dm.bin", D "dm2.bin"));
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CHECK(exists(D "dm.bin"));
    CloseHandle(h);
    h = open_(D "dm.bin", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, OPEN_EXISTING);
    rm_host(D "dm2.bin");
    CHECK(MoveFileA(D "dm.bin", D "dm2.bin"));
    HANDLE h2 = open_(D "dm2.bin", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_DELETE, OPEN_EXISTING);
    CHECK(h2 == INVALID_HANDLE_VALUE);   /* the open handle follows the file to its new name */
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CHECK(DeleteFileA(D "dm2.bin"));
    CHECK(!exists(D "dm2.bin"));
    CloseHandle(h);
    /* copy: the source is read with FILE_SHARE_READ, the destination written with share 0 */
    make(D "cs.bin", "src");
    make(D "cd.bin", "dst");
    h = open_(D "cs.bin", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING);
    CHECK(!CopyFileA(D "cs.bin", D "cd.bin", FALSE));
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CloseHandle(h);
    h = open_(D "cd.bin", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING);
    CHECK(!CopyFileA(D "cs.bin", D "cd.bin", FALSE));
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CloseHandle(h);
    CHECK(CopyFileA(D "cs.bin", D "cd.bin", FALSE));
    CHECK(!CopyFileA(D "cs.bin", "Data\\fc", FALSE));   /* destination is a directory */
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CHECK(!CopyFileA("Data\\fc", D "cd2.bin", FALSE));   /* source is a directory */
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
}

struct disp_row { DWORD disp; int existed; int ok; DWORD err_or_last; };

TEST(fc_dispositions)
{
    setup();
    static const disp_row rows[] = {
        { CREATE_NEW, 0, 1, 0 },          { CREATE_NEW, 1, 0, ERROR_FILE_EXISTS },
        { CREATE_ALWAYS, 0, 1, 0 },       { CREATE_ALWAYS, 1, 1, ERROR_ALREADY_EXISTS },
        { OPEN_EXISTING, 0, 0, ERROR_FILE_NOT_FOUND }, { OPEN_EXISTING, 1, 1, 0 },
        { OPEN_ALWAYS, 0, 1, 0 },         { OPEN_ALWAYS, 1, 1, ERROR_ALREADY_EXISTS },
        { TRUNCATE_EXISTING, 0, 0, ERROR_FILE_NOT_FOUND }, { TRUNCATE_EXISTING, 1, 1, 0 },
    };
    for (size_t i = 0; i < sizeof rows / sizeof rows[0]; i++) {
        const disp_row &r = rows[i];
        if (r.existed) make(D "disp.bin", "abcdef"); else rm_host(D "disp.bin");
        SetLastError(999);
        HANDLE h = open_(D "disp.bin", GENERIC_READ | GENERIC_WRITE, 0, r.disp);
        CHECK_EQ(h != INVALID_HANDLE_VALUE, r.ok);
        CHECK_EQ(GetLastError(), r.err_or_last);
        if (h != INVALID_HANDLE_VALUE) {
            DWORD sz = GetFileSize(h, NULL);
            int truncates = r.disp == CREATE_ALWAYS || r.disp == TRUNCATE_EXISTING;
            CHECK_EQ(sz, r.existed && !truncates ? 6 : 0);
            CloseHandle(h);
        }
    }
    /* TRUNCATE_EXISTING without GENERIC_WRITE; invalid dispositions */
    make(D "disp.bin", "abcdef");
    HANDLE h = open_(D "disp.bin", GENERIC_READ, 0, TRUNCATE_EXISTING);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    for (DWORD d = 0; d < 8; d += d == 0 ? 6 : 1) {   /* 0, 6, 7 */
        h = open_(D "disp.bin", GENERIC_READ, 0, d);
        CHECK(h == INVALID_HANDLE_VALUE);
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    }
    h = open_(D "disp.bin", GENERIC_READ, 0, OPEN_EXISTING);   /* the refusals truncated nothing */
    CHECK_EQ(GetFileSize(h, NULL), 6);
    CloseHandle(h);
    /* directories, missing parents, NULL name */
    h = open_("Data\\fc", GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    h = open_("Data\\fc", GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    h = open_("Data\\fc", GENERIC_READ, FILE_SHARE_READ, CREATE_NEW, FILE_FLAG_BACKUP_SEMANTICS);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_EXISTS);
    h = open_("Data\\fc\\nodir\\x.bin", GENERIC_WRITE, 0, CREATE_NEW);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    h = open_(NULL, GENERIC_READ, 0, OPEN_EXISTING);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
}

TEST(fc_delete_on_close)
{
    setup();
    make(D "doc.bin", "x");
    HANDLE h = open_(D "doc.bin", GENERIC_READ, FILE_SHARE_DELETE | FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_DELETE_ON_CLOSE);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK(exists(D "doc.bin"));
    HANDLE h2 = open_(D "doc.bin", GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING);   /* lacks FILE_SHARE_DELETE */
    CHECK(h2 == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    h2 = open_(D "doc.bin", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, OPEN_EXISTING);
    CHECK(h2 != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    CHECK(exists(D "doc.bin"));   /* another handle is still open */
    CloseHandle(h2);
    CHECK(!exists(D "doc.bin"));  /* the last close removes it */
    /* sharing denies DELETE_ON_CLOSE when an existing open does not share delete */
    make(D "doc.bin", "x");
    h2 = open_(D "doc.bin", GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING);
    h = open_(D "doc.bin", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, OPEN_EXISTING, FILE_FLAG_DELETE_ON_CLOSE);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CloseHandle(h2);
    CHECK(exists(D "doc.bin"));
    /* created and removed in one handle's life */
    rm_host(D "doc2.bin");
    h = open_(D "doc2.bin", GENERIC_WRITE, 0, CREATE_NEW, FILE_FLAG_DELETE_ON_CLOSE);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK(exists(D "doc2.bin"));
    CloseHandle(h);
    CHECK(!exists(D "doc2.bin"));
}

TEST(fc_append_data)
{
    setup();
    make(D "app.bin", "AAAA");
    HANDLE h = open_(D "app.bin", FILE_APPEND_DATA, FILE_SHARE_READ, OPEN_EXISTING);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD n = 0;
    SetFilePointer(h, 1, NULL, FILE_BEGIN);
    CHECK(WriteFile(h, "bb", 2, &n, NULL));
    CHECK_EQ(n, 2);
    SetFilePointer(h, 0, NULL, FILE_BEGIN);
    CHECK(WriteFile(h, "c", 1, &n, NULL));
    OVERLAPPED ov;
    memset(&ov, 0, sizeof ov);
    ov.Offset = 0;   /* an explicit offset does not move an append-only write either */
    CHECK(WriteFile(h, "d", 1, &n, &ov));
    CloseHandle(h);
    h = open_(D "app.bin", GENERIC_READ, 0, OPEN_EXISTING);
    char b[16] = { 0 };
    CHECK(ReadFile(h, b, sizeof b - 1, &n, NULL));
    CHECK_EQ(n, 8);
    CHECK_STR(b, "AAAAbbcd");
    CHECK(!WriteFile(h, "x", 1, &n, NULL));   /* a read handle does not write */
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CloseHandle(h);
    /* FILE_WRITE_DATA|FILE_APPEND_DATA (GENERIC_WRITE) is a positioned write */
    h = open_(D "app.bin", GENERIC_WRITE, 0, OPEN_EXISTING);
    CHECK(WriteFile(h, "Z", 1, &n, NULL));
    CloseHandle(h);
    h = open_(D "app.bin", GENERIC_READ, 0, OPEN_EXISTING);
    memset(b, 0, sizeof b);
    ReadFile(h, b, sizeof b - 1, &n, NULL);
    CHECK_STR(b, "ZAAAbbcd");
    CloseHandle(h);
}

TEST(fc_readonly_attribute)
{
    setup();
    rm_host(D "ro.bin");
    HANDLE h = open_(D "ro.bin", GENERIC_WRITE, 0, CREATE_NEW, FILE_ATTRIBUTE_READONLY);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD n = 0;
    CHECK(WriteFile(h, "abc", 3, &n, NULL));   /* the creating handle keeps its access */
    CloseHandle(h);
    CHECK(GetFileAttributesA(D "ro.bin") & FILE_ATTRIBUTE_READONLY);
    h = open_(D "ro.bin", GENERIC_WRITE, 0, OPEN_EXISTING);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    h = open_(D "ro.bin", GENERIC_READ | FILE_APPEND_DATA, 0, OPEN_EXISTING);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    h = open_(D "ro.bin", GENERIC_WRITE, 0, CREATE_ALWAYS);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    h = open_(D "ro.bin", GENERIC_READ, 0, OPEN_EXISTING, FILE_FLAG_DELETE_ON_CLOSE);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    h = open_(D "ro.bin", GENERIC_READ, 0, OPEN_EXISTING);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileSize(h, NULL), 3);
    CloseHandle(h);
    CHECK(!DeleteFileA(D "ro.bin"));
    CHECK_EQ(GetLastError(), ERROR_ACCESS_DENIED);
    CHECK(exists(D "ro.bin"));
    CHECK(CopyFileA(D "ro.bin", D "ro2.bin", FALSE));
    CHECK(GetFileAttributesA(D "ro2.bin") & FILE_ATTRIBUTE_READONLY);   /* CopyFile carries the attribute */
    rm_host(D "ro.bin");
    rm_host(D "ro2.bin");
    make(D "ro.bin", "new");   /* a re-created file has fresh attributes */
    CHECK(!(GetFileAttributesA(D "ro.bin") & FILE_ATTRIBUTE_READONLY));
}

TEST(fc_utf16_names)
{
    setup();
    make(D "wide.bin", "w");
    static const WCHAR cyr[] = { 'D','a','t','a','\\','f','c','\\',0x41F,0x440,0x438,0x432,0x435,0x442,'.','b','i','n',0 };
    static const WCHAR emo[] = { 'D','a','t','a','\\','f','c','\\',0xD83D,0xDE00,'.','b','i','n',0 };
    static const WCHAR bad[] = { 'D','a','t','a','\\','f','c','\\',0xD83D,'x',0 };
    HANDLE hc = CreateFileW(cyr, GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL);
    CHECK(hc != INVALID_HANDLE_VALUE);
    DWORD put;
    CHECK(WriteFile(hc, "cyr", 3, &put, NULL));
    CloseHandle(hc);
    hc = CreateFileW(cyr, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(hc != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileSize(hc, NULL), 3);
    CloseHandle(hc);
    static const WCHAR cyr_up[] = { 'D','a','t','a','\\','f','c','\\',0x41F,0x440,0x438,0x432,0x435,0x442,'.','b','i','n',0 };
    CHECK(CreateFileW(cyr_up, GENERIC_READ, 0, NULL, CREATE_NEW, 0, NULL) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_EXISTS);
    hc = CreateFileW(emo, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);   /* surrogate pair */
    CHECK(hc != INVALID_HANDLE_VALUE);
    CloseHandle(hc);
    hc = CreateFileW(emo, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(hc != INVALID_HANDLE_VALUE);
    CloseHandle(hc);
    CHECK(CreateFileW(bad, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_INVALID_NAME);
    /* the A API names a Latin-1 file the W API reaches too */
    s4_write_file("Data\\fc\\caf\xe9.bin", "a");
    static const WCHAR cafe[] = { 'D','a','t','a','\\','f','c','\\','c','a','f',0xE9,'.','b','i','n',0 };
    hc = CreateFileW(cafe, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(hc != INVALID_HANDLE_VALUE);
    CloseHandle(hc);
    static const WCHAR ok[] = { 'D','a','t','a','\\','f','c','\\','W','I','D','E','.','b','i','n',0 };
    HANDLE h = CreateFileW(ok, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    HANDLE h2 = CreateFileW(ok, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h2 == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_SHARING_VIOLATION);
    CloseHandle(h);
}

TEST(fc_readwrite_overlapped_on_sync_handle)
{
    setup();
    make(D "ov.bin", "0123456789");
    HANDLE h = open_(D "ov.bin", GENERIC_READ | GENERIC_WRITE, 0, OPEN_EXISTING);
    DWORD n = 99;
    char b[16] = { 0 };
    OVERLAPPED ov;
    memset(&ov, 0, sizeof ov);
    ov.Offset = 4;
    CHECK(ReadFile(h, b, 3, &n, &ov));
    CHECK_EQ(n, 3);
    CHECK(!memcmp(b, "456", 3));
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 7);   /* pointer = offset + bytes */
    memset(&ov, 0, sizeof ov);
    ov.Offset = 8;
    CHECK(WriteFile(h, "XYZW", 4, &n, &ov));   /* extends the file */
    CHECK_EQ(n, 4);
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 12);
    CHECK_EQ(GetFileSize(h, NULL), 12);
    /* EOF: success with 0 bytes without OVERLAPPED; with one, ERROR_HANDLE_EOF on any handle */
    memset(&ov, 0, sizeof ov);
    ov.Offset = 12;
    n = 99;
    SetLastError(0);
    CHECK(!ReadFile(h, b, 4, &n, &ov));
    CHECK_EQ(GetLastError(), ERROR_HANDLE_EOF);
    CHECK_EQ(n, 0);
    ov.Offset = 500;
    n = 99;
    CHECK(!ReadFile(h, b, 4, &n, &ov));
    CHECK_EQ(GetLastError(), ERROR_HANDLE_EOF);
    n = 99;
    CHECK(ReadFile(h, b, 4, &n, NULL));
    CHECK_EQ(n, 0);
    /* a NULL byte count is not an error: the I/O happens, the count is just not stored */
    SetFilePointer(h, 0, NULL, FILE_BEGIN);
    CHECK(ReadFile(h, b, 2, NULL, NULL));
    CHECK(!memcmp(b, "01", 2));
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 2);
    CHECK(WriteFile(h, "q", 1, NULL, NULL));
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 3);
    memset(&ov, 0, sizeof ov);
    CHECK(ReadFile(h, b, 1, NULL, &ov));
    CHECK_EQ(ov.InternalHigh, 1);
    CloseHandle(h);
}

TEST(fc_readwrite_overlapped_handle)
{
    setup();
    make(D "ova.bin", "0123456789");
    HANDLE h = open_(D "ova.bin", GENERIC_READ | GENERIC_WRITE, 0, OPEN_EXISTING, FILE_FLAG_OVERLAPPED);
    CHECK(h != INVALID_HANDLE_VALUE);
    HANDLE ev = CreateEventA(NULL, TRUE, TRUE, NULL);   /* starts signalled: the I/O must reset it first */
    OVERLAPPED ov;
    memset(&ov, 0, sizeof ov);
    ov.Offset = 2;
    ov.hEvent = ev;
    char b[16] = { 0 };
    DWORD n = 0;
    CHECK(ReadFile(h, b, 4, &n, &ov));   /* completing synchronously is permitted */
    CHECK_EQ(n, 4);
    CHECK(!memcmp(b, "2345", 4));
    CHECK_EQ(ov.InternalHigh, 4);
    CHECK_EQ(ov.Internal, 0);
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_OBJECT_0);
    memset(&ov, 0, sizeof ov);
    ov.Offset = 10;
    ov.hEvent = ev;
    SetLastError(0);
    CHECK(!ReadFile(h, b, 4, &n, &ov));   /* at EOF an overlapped read reports ERROR_HANDLE_EOF */
    CHECK_EQ(GetLastError(), ERROR_HANDLE_EOF);
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_OBJECT_0);
    memset(&ov, 0, sizeof ov);
    ov.Offset = 10;
    ov.hEvent = ev;
    CHECK(WriteFile(h, "ab", 2, NULL, &ov));   /* the byte count may be NULL with an OVERLAPPED */
    CHECK_EQ(ov.InternalHigh, 2);
    CHECK_EQ(GetFileSize(h, NULL), 12);
    CHECK(!ReadFile(h, b, 1, &n, NULL));       /* an overlapped handle needs an OVERLAPPED */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(!WriteFile(h, "a", 1, &n, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CloseHandle(ev);
    CloseHandle(h);
}

TEST(fc_misc_size_flush)
{
    setup();
    rm_host(D "big.bin");
    HANDLE h = open_(D "big.bin", GENERIC_READ | GENERIC_WRITE, 0, CREATE_NEW);
    LARGE_INTEGER d;
    d.QuadPart = 5ll << 30;
    CHECK(SetFilePointerEx(h, d, NULL, FILE_BEGIN));
    CHECK(SetEndOfFile(h));
    DWORD hi = 0;
    CHECK_EQ(GetFileSize(h, &hi), (5ll << 30) & 0xffffffffll);
    CHECK_EQ(hi, 1);
    SetLastError(0);
    CHECK_EQ(GetFileSize(h, NULL), INVALID_FILE_SIZE);   /* does not fit a DWORD */
    CHECK(GetLastError() != 0);
    CloseHandle(h);
    rm_host(D "big.bin");
    SetLastError(0);
    CHECK(!FlushFileBuffers(GetStdHandle(STD_OUTPUT_HANDLE)));   /* no standard handles (F5): NULL is no handle */
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
}
