/* test_profile.cpp - platform/win32/profile.cpp: the first run's profile files, as the PC launcher writes them
 * (observed under Wine on the owner's install, profile.cpp's header), on synthetic install files written here. */
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

static void clean(void)
{
    for (const char *f : {PROF "Fallout.ini", PROF "FalloutPrefs.ini", LOCAL "plugins.txt", LOCAL "NVDLCList.txt",
                          "C:\\FalloutNV\\Fallout_default.ini", "C:\\FalloutNV\\Medium.ini"})
        DeleteFileA(f);
}

TEST(first_run_profile)
{
    clean();
    const char *def = "[General]\r\nsTestKey=synthetic\r\n[Display]\r\nbFull Screen=1\r\niAdapter=0\r\n[Controls]\r\nfX=1\r\n";
    put("C:\\FalloutNV\\Fallout_default.ini", def);
    put("C:\\FalloutNV\\Medium.ini", "[Display]\r\nfSynth=2\r\n");   /* a preset: the launcher's first run ignores it */
    CHECK(fnvnx_first_run_profile() == (FNVNX_PROFILE_PREFS | FNVNX_PROFILE_PLUGINS));
    CHECK(get(PROF "Fallout.ini") == "(missing)");                               /* the exe makes it, not this layer */
    char v[256];
    GetPrivateProfileStringA("Display", "sD3DDevice", "", v, sizeof v, PROF "FalloutPrefs.ini");
    CHECK(strstr(v, "fnv-nx null D3D9 device") != NULL);                         /* no d3d9 module here: the null name */
    std::string prefs = get(PROF "FalloutPrefs.ini");
    CHECK(prefs.find("sD3DDevice=\"fnv-nx null D3D9 device\"") != std::string::npos);   /* quoted, as the launcher */
    CHECK(prefs.find("fSynth") == std::string::npos);                            /* no preset */
    GetPrivateProfileStringA("General", "sTestKey", "", v, sizeof v, PROF "FalloutPrefs.ini");
    CHECK_STR(v, "synthetic");                                                   /* the rest is Fallout_default.ini */
    GetPrivateProfileStringA("Controls", "fX", "", v, sizeof v, PROF "FalloutPrefs.ini");
    CHECK_STR(v, "1");
    CHECK(GetPrivateProfileIntA("Display", "iSize W", 0, PROF "FalloutPrefs.ini") == 1280);   /* the Switch's screen */
    CHECK(GetPrivateProfileIntA("Display", "iSize H", 0, PROF "FalloutPrefs.ini") == 720);
    CHECK(get(LOCAL "plugins.txt") == "");                                       /* empty, as the launcher's */
    CHECK(get(LOCAL "NVDLCList.txt") == "");

    /* the player's own files win: a second run makes nothing and leaves a Fallout.ini that exists alone */
    put(PROF "Fallout.ini", "[General]\r\nmine=1\r\n");
    CHECK(fnvnx_first_run_profile() == 0);
    CHECK(get(PROF "Fallout.ini") == "[General]\r\nmine=1\r\n");
    /* without the install's Fallout_default.ini nothing can be copied: only the empty load-order files */
    clean();
    CHECK(fnvnx_first_run_profile() == FNVNX_PROFILE_PLUGINS);
    CHECK(get(PROF "FalloutPrefs.ini") == "(missing)");
    clean();
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
