/* test_profile.cpp - platform/win32/profile.cpp: the first run's profile files (the PC launcher's job), on
 * synthetic install files written here. */
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
    char buf[4096];
    DWORD got = 0;
    ReadFile(h, buf, sizeof buf, &got, NULL);
    CloseHandle(h);
    return std::string(buf, got);
}

#define PROF "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\"
#define PLUG "C:\\Users\\Player\\AppData\\Local\\FalloutNV\\plugins.txt"

TEST(first_run_profile)
{
    DeleteFileA(PROF "Fallout.ini");
    DeleteFileA(PROF "FalloutPrefs.ini");
    DeleteFileA(PLUG);
    CreateDirectoryA("C:\\FalloutNV\\Data", NULL);
    /* nothing to copy from: only the display keys and no plugins.txt (no master in Data) */
    CHECK(fnvnx_first_run_profile() == FNVNX_PROFILE_PREFS);
    CHECK(GetPrivateProfileIntA("Display", "iSize W", 0, PROF "FalloutPrefs.ini") == 1280);
    CHECK(get(PLUG) == "(missing)");
    DeleteFileA(PROF "FalloutPrefs.ini");

    put("C:\\FalloutNV\\Fallout_default.ini", "[General]\r\nsTestKey=synthetic\r\n");
    put("C:\\FalloutNV\\Low.ini", "[Display]\r\niSize W=640\r\nfSynth=1\r\n");
    put("C:\\FalloutNV\\Medium.ini", "[Display]\r\niSize W=1024\r\niSize H=768\r\nfSynth=2\r\n");
    put("C:\\FalloutNV\\Data\\FalloutNV.esm", "TES4");
    put("C:\\FalloutNV\\Data\\HonestHearts.esm", "TES4");
    put("C:\\FalloutNV\\Data\\DeadMoney.esm", "TES4");
    put("C:\\FalloutNV\\Data\\Unofficial.esm", "TES4");
    CHECK(fnvnx_first_run_profile() == (FNVNX_PROFILE_INI | FNVNX_PROFILE_PREFS | FNVNX_PROFILE_PLUGINS));
    CHECK(get(PROF "Fallout.ini") == "[General]\r\nsTestKey=synthetic\r\n");
    char v[64];
    GetPrivateProfileStringA("Display", "fSynth", "", v, sizeof v, PROF "FalloutPrefs.ini");
    CHECK_STR(v, "2");                                                         /* Medium.ini first */
    CHECK(GetPrivateProfileIntA("Display", "iSize W", 0, PROF "FalloutPrefs.ini") == 1280);
    CHECK(GetPrivateProfileIntA("Display", "iSize H", 0, PROF "FalloutPrefs.ini") == 720);
    CHECK(GetPrivateProfileIntA("Display", "bFull Screen", 0, PROF "FalloutPrefs.ini") == 1);
    CHECK(get(PLUG) == "FalloutNV.esm\r\nDeadMoney.esm\r\nHonestHearts.esm\r\n");  /* release order, known only */

    /* the player's own files win: a second run makes nothing */
    put(PROF "Fallout.ini", "[General]\r\nmine=1\r\n");
    CHECK(fnvnx_first_run_profile() == 0);
    CHECK(get(PROF "Fallout.ini") == "[General]\r\nmine=1\r\n");

    DeleteFileA(PROF "Fallout.ini");
    DeleteFileA(PROF "FalloutPrefs.ini");
    DeleteFileA(PLUG);
    for (const char *f : {"Fallout_default.ini", "Low.ini", "Medium.ini", "Data\\FalloutNV.esm", "Data\\HonestHearts.esm",
                          "Data\\DeadMoney.esm", "Data\\Unofficial.esm"}) {
        char p[260];
        snprintf(p, sizeof p, "C:\\FalloutNV\\%s", f);
        DeleteFileA(p);
    }
}
