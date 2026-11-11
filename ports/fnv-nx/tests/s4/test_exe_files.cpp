/* test_exe_files.cpp - the files of the declared machine: a file exists only if the exe or a game module creates it, or the install puts it
 * there; the platform seeds nothing. This tests the calls the exe makes on those files (private facts: fnv-nx-build l5-platform/manifest
 * notes/l5-w13-*.md), on synthetic install files written here:
 *   Fallout.ini      the exe copies Fallout_default.ini (SHGetFolderPathA, CreateDirectoryA, GetFileAttributesA, CopyFileA)
 *   FalloutPrefs.ini the exe only reads it before the first frame (the launcher writes it); its own saves are WritePrivateProfileStringA
 *   Plugins.txt      the exe only reads it, with the CRT's fopen "r" (CreateFileA OPEN_EXISTING): absent is ERROR_FILE_NOT_FOUND
 *   NVDLCList.txt    no code in the exe or its modules names it */
#include "s4_test.h"

#include <stdio.h>
#include <stdlib.h>

#include <initializer_list>
#include <string>

static void put(const char *win, const char *text)
{
    HANDLE h = CreateFileA(win, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD put = 0;
    WriteFile(h, text, (DWORD)strlen(text), &put, NULL);
    CloseHandle(h);
}

static std::string get(const char *win)
{
    HANDLE h = CreateFileA(win, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return "(missing)";
    char buf[8192];
    DWORD got = 0;
    ReadFile(h, buf, sizeof buf, &got, NULL);
    CloseHandle(h);
    return std::string(buf, got);
}

#define PROF "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\"
#define LOCAL "C:\\Users\\Player\\AppData\\Local\\FalloutNV\\"

#define PROF "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\"
#define LOCAL "C:\\Users\\Player\\AppData\\Local\\FalloutNV\\"

static void clean(void)
{
    for (const char *f : {PROF "Fallout.ini", PROF "FalloutPrefs.ini", LOCAL "plugins.txt", LOCAL "Plugins.txt", LOCAL "NVDLCList.txt",
                          "C:\\FalloutNV\\Fallout_default.ini"})
        DeleteFileA(f);
}

/* a fresh machine has none of the profile files */
TEST(fresh_machine_has_no_profile_files)
{
    clean();
    for (const char *f : {PROF "Fallout.ini", PROF "FalloutPrefs.ini", LOCAL "Plugins.txt", LOCAL "NVDLCList.txt"})
        CHECK_EQ(GetFileAttributesA(f), INVALID_FILE_ATTRIBUTES);
}

/* The exe's own Fallout.ini creation (a fresh install has Fallout_default.ini and no Fallout.ini): its calls, in its order, on this
 * layer: SHGetFolderPathA(CSIDL_PERSONAL) + "\\My Games\\", CreateDirectoryA, + "FalloutNV\\", CreateDirectoryA, GetFileAttributesA of
 * the .ini (the CRT's _access), CopyFileA("Fallout_default.ini", ini, TRUE) with the name relative to the current directory
 * (the install). The result is a byte copy, and a second copy onto the existing file fails (bFailIfExists). */
TEST(exe_creates_fallout_ini)
{
    clean();
    const char *def = "[General]\r\nsTestKey=exe\r\n";
    put("C:\\FalloutNV\\Fallout_default.ini", def);
    SetCurrentDirectoryA("C:\\FalloutNV");
    char dir[MAX_PATH];
    CHECK_EQ(SHGetFolderPathA(NULL, 5, NULL, 0, dir), S_OK);
    strcat(dir, "\\My Games\\");
    CreateDirectoryA(dir, NULL);
    strcat(dir, "FalloutNV\\");
    CreateDirectoryA(dir, NULL);
    char ini[MAX_PATH];
    snprintf(ini, sizeof ini, "%sFallout.ini", dir);
    CHECK_EQ(GetFileAttributesA(ini), INVALID_FILE_ATTRIBUTES);
    CHECK(CopyFileA("Fallout_default.ini", ini, TRUE));
    CHECK(get(ini) == def);
    SetLastError(0);
    CHECK(!CopyFileA("Fallout_default.ini", ini, TRUE));
    CHECK_EQ(GetLastError(), ERROR_FILE_EXISTS);
    clean();
}

/* FalloutPrefs.ini absent: reading it (GetPrivateProfileStringA, what the settings load does) returns the default and creates nothing;
 * the exe's own save, WritePrivateProfileStringA, creates it (wine-9.0:dlls/kernelbase/registry.c:WritePrivateProfileStringW creates the file). */
TEST(prefs_ini_read_absent_then_written_by_exe)
{
    clean();
    CreateDirectoryA("C:\\Users\\Player\\Documents\\My Games", NULL);
    CreateDirectoryA("C:\\Users\\Player\\Documents\\My Games\\FalloutNV", NULL);
    char v[64];
    GetPrivateProfileStringA("Display", "sD3DDevice", "", v, sizeof v, PROF "FalloutPrefs.ini");
    CHECK_STR(v, "");
    CHECK_EQ(GetFileAttributesA(PROF "FalloutPrefs.ini"), INVALID_FILE_ATTRIBUTES);
    CHECK(WritePrivateProfileStringA("Controls", "fX", "1", PROF "FalloutPrefs.ini"));
    CHECK(GetFileAttributesA(PROF "FalloutPrefs.ini") != INVALID_FILE_ATTRIBUTES);
    clean();
}

/* Plugins.txt absent: the exe's fopen "r" is CreateFileA(GENERIC_READ, OPEN_EXISTING): INVALID_HANDLE_VALUE + ERROR_FILE_NOT_FOUND
 * (its directory exists: the exe creates AppData\Local\FalloutNV itself), and nothing is created. */
TEST(plugins_txt_read_absent)
{
    clean();
    CreateDirectoryA("C:\\Users\\Player\\AppData\\Local\\FalloutNV", NULL);
    SetLastError(0);
    HANDLE h = CreateFileA(LOCAL "Plugins.txt", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    CHECK(h == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(GetFileAttributesA(LOCAL "Plugins.txt"), INVALID_FILE_ATTRIBUTES);
}
