/* test_saves.cpp - the save folder on the SD card, driven the way the exe saves.
 *
 * The save path is SHGetFolderPathA(CSIDL_PERSONAL) + "\My Games\FalloutNV\" + sLocalSavePath ("Saves\"). Before
 * writing, SaveOwner::OpenSaveFile (0x00857370) and BGSSaveLoadManager (0x00850100) run CreateDirectoryA on the
 * folder, then rotate the backups: if _access(name) succeeds, DeleteFileA(name.bak) when it exists, then
 * rename(name, name.bak) (the CRT's rename 0x00ec862c, which never replaces; _access is 0x00ecbf05). The save list
 * comes from FindFirstFileA on the folder, ordered by ftLastWriteTime (0x00846a20). The test checks that sequence and
 * that the files land under <root>/user/Documents/My Games/FalloutNV/Saves on the host fs (sdmc: on the device). */
#include "s4_test.h"
#include "fnvnx_msvcrt.h"
#include "../../platform/os/os_clock.h"

#include <stdio.h>
#include <stdlib.h>

#include <string>
#include <vector>

static std::vector<unsigned char> pattern(unsigned n, unsigned seed)
{
    std::vector<unsigned char> v(n);
    for (unsigned i = 0; i < n; i++)
        v[i] = (unsigned char)((i * 2654435761u + seed) >> 13);
    return v;
}

static bool write_crt(const std::string &win, const std::vector<unsigned char> &d)
{
    HANDLE h = CreateFileA(win.c_str(), GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return false;
    DWORD put = 0;
    bool ok = WriteFile(h, d.data(), (DWORD)d.size(), &put, NULL) && put == d.size();
    return CloseHandle(h) && ok;
}

static std::vector<unsigned char> read_win32(const std::string &win)
{
    std::vector<unsigned char> v;
    HANDLE h = CreateFileA(win.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return v;
    DWORD size = GetFileSize(h, NULL), got = 0;
    v.resize(size);
    if (size && (!ReadFile(h, v.data(), size, &got, NULL) || got != size))
        v.clear();
    CloseHandle(h);
    return v;
}

/* the backup rotation as 0x00857370 does it, one backup (iBackupCount 1) */
static void rotate(const std::string &name)
{
    std::string bak = name + ".bak";
    if (fnvnx_access(name.c_str(), 0) == 0) {
        if (fnvnx_access(bak.c_str(), 0) == 0)
            DeleteFileA(bak.c_str());
        CHECK(MoveFileA(name.c_str(), bak.c_str()) != 0);
    }
}

static std::vector<std::string> list(const std::string &pattern_, std::vector<uint64_t> *times = NULL)
{
    std::vector<std::string> names;
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern_.c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE)
        return names;
    do {
        names.push_back(fd.cFileName);
        if (times)
            times->push_back((uint64_t)fd.ftLastWriteTime.dwHighDateTime << 32 | fd.ftLastWriteTime.dwLowDateTime);
    } while (FindNextFileA(h, &fd));
    CHECK(GetLastError() == ERROR_NO_MORE_FILES);
    FindClose(h);
    return names;
}

TEST(save_folder_and_backup_rotation)
{
    char docs[MAX_PATH];
    CHECK(SHGetFolderPathA(NULL, CSIDL_PERSONAL | CSIDL_FLAG_CREATE, NULL, 0, docs) == S_OK);
    CHECK(!strcmp(docs, "C:\\Users\\Player\\Documents"));
    std::string dir = std::string(docs) + "\\My Games\\FalloutNV\\Saves\\";
    std::string save = dir + "Save 1 Courier Goodsprings 00 03 12.fos";

    /* a clean folder (repeat runs) */
    for (const char *n : {"", ".bak"})
        DeleteFileA((save + n).c_str());
    DeleteFileA((dir + "Autosave.fos").c_str());

    /* CreateDirectoryA on the folder, as before every save: the first call may create it, a second reports it */
    CreateDirectoryA(dir.c_str(), NULL);
    CHECK(!CreateDirectoryA(dir.c_str(), NULL));
    CHECK(GetLastError() == ERROR_ALREADY_EXISTS);
    DWORD attr = GetFileAttributesA(dir.c_str());
    CHECK(attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_DIRECTORY));

    /* first save: nothing to rotate */
    std::vector<unsigned char> s1 = pattern(300 * 1024 + 17, 1);
    rotate(save);
    CHECK(fnvnx_access((save + ".bak").c_str(), 0) == -1);
    CHECK(write_crt(save, s1));
    CHECK(read_win32(save) == s1);

    /* second save: name -> name.bak, then the new file */
    std::vector<unsigned char> s2 = pattern(310 * 1024 + 3, 2);
    rotate(save);
    CHECK(fnvnx_access(save.c_str(), 0) == -1);
    CHECK(read_win32(save + ".bak") == s1);
    CHECK(write_crt(save, s2));

    /* third save: the old .bak is deleted first, because rename never replaces */
    CHECK(MoveFileA(save.c_str(), (save + ".bak").c_str()) == 0);
    std::vector<unsigned char> s3 = pattern(290 * 1024, 3);
    rotate(save);
    CHECK(read_win32(save + ".bak") == s2);
    CHECK(write_crt(save, s3));
    CHECK(read_win32(save) == s3);

    /* the save list: *.fos only, with write times that order them */
    os_sleep_ns(20000000);   /* write times must differ */
    std::vector<unsigned char> a = pattern(1000, 4);
    CHECK(write_crt(dir + "Autosave.fos", a));
    std::vector<uint64_t> t;
    std::vector<std::string> names = list(dir + "*.fos", &t);
    CHECK(names.size() == 2);
    bool found_save = false, found_auto = false;
    uint64_t t_save = 0, t_auto = 0;
    for (size_t i = 0; i < names.size(); i++) {
        if (names[i] == "Save 1 Courier Goodsprings 00 03 12.fos") { found_save = true; t_save = t[i]; }
        if (names[i] == "Autosave.fos") { found_auto = true; t_auto = t[i]; }
    }
    CHECK(found_save && found_auto);
    CHECK(t_save != 0 && t_auto > t_save);
    CHECK(list(dir + "*.fos.bak").size() == 1);

    /* the files are on the host fs (sdmc: on the device) under the root's user folder */
    std::string host = std::string(os_default_root()) + "/user/Documents/My Games/FalloutNV/Saves/Autosave.fos";
    FILE *f = fopen(host.c_str(), "rb");
    CHECK(f != NULL);
    if (f) {
        unsigned char buf[1100];
        CHECK(fread(buf, 1, sizeof buf, f) == a.size());
        CHECK(!memcmp(buf, a.data(), a.size()));
        fclose(f);
    }

    /* deleting a save from the menu */
    CHECK(DeleteFileA((dir + "Autosave.fos").c_str()));
    CHECK(list(dir + "*.fos").size() == 1);
}
