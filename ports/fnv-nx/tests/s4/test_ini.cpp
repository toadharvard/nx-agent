/* test_ini.cpp - GetPrivateProfile* / WritePrivateProfile* semantics on a Fallout.ini-like file. */
#include "s4_test.h"

static const char INI[] = "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\Fallout.ini";

static void seed(void)
{
    s4_write_file(INI,
                  "[General]\r\n"
                  "sLanguage=ENGLISH\r\n"
                  "  uGridsToLoad = 5  \r\n"
                  "; comment=ignored\r\n"
                  "sQuoted=\"  spaced value  \"\r\n"
                  "iNeg=-42\r\n"
                  "iHex=0x1F\r\n"
                  "iJunk=12abc\r\n"
                  "bNoValue\r\n"
                  "sLanguage=SHADOWED\r\n"
                  "\r\n"
                  "[Display]\r\n"
                  "iSize W=1280\r\n"
                  "iSize H=720\r\n"
                  "[general]\r\n"
                  "sLater=second section of the same name is not searched\r\n");
}

TEST(ini_get_string_rules)
{
    seed();
    char b[256];
    CHECK_EQ(GetPrivateProfileStringA("General", "sLanguage", "", b, sizeof b, INI), 7);
    CHECK_STR(b, "ENGLISH"); /* first key wins */
    CHECK_EQ(GetPrivateProfileStringA("GENERAL", "UGRIDSTOLOAD", "", b, sizeof b, INI), 1);
    CHECK_STR(b, "5"); /* case-insensitive, trimmed */
    GetPrivateProfileStringA("General", "sQuoted", "", b, sizeof b, INI);
    CHECK_STR(b, "  spaced value  "); /* surrounding quotes removed, inner blanks kept */
    GetPrivateProfileStringA("General", "; comment", "dflt", b, sizeof b, INI);
    CHECK_STR(b, "dflt");
    CHECK_EQ(GetPrivateProfileStringA("General", "bNoValue", "x", b, sizeof b, INI), 0);
    CHECK_STR(b, ""); /* present without '=': empty value, not the default */
    CHECK_EQ(GetPrivateProfileStringA("General", "missing", "def   ", b, sizeof b, INI), 3);
    CHECK_STR(b, "def"); /* default loses trailing blanks */
    GetPrivateProfileStringA("General", "missing", NULL, b, sizeof b, INI);
    CHECK_STR(b, "");
    GetPrivateProfileStringA("Display", "iSize W", "", b, sizeof b, INI);
    CHECK_STR(b, "1280"); /* keys with spaces */
    GetPrivateProfileStringA("General", "sLater", "nope", b, sizeof b, INI);
    CHECK_STR(b, "nope");
    /* truncation: size - 1 characters, terminated */
    CHECK_EQ(GetPrivateProfileStringA("General", "sLanguage", "", b, 4, INI), 3);
    CHECK_STR(b, "ENG");
    GetPrivateProfileStringA("General", "sLanguage", "d", b, sizeof b, "C:\\nowhere\\none.ini");
    CHECK_STR(b, "d");
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
}

TEST(ini_lists)
{
    seed();
    char b[256];
    DWORD n = GetPrivateProfileStringA(NULL, NULL, "", b, sizeof b, INI);
    CHECK_EQ(n, strlen("General") + 1 + strlen("Display") + 1 + strlen("general") + 1);
    CHECK_STR(b, "General");
    CHECK_STR(b + 8, "Display");
    CHECK(b[n] == 0);
    n = GetPrivateProfileStringA("Display", NULL, "", b, sizeof b, INI);
    CHECK_EQ(n, 16);
    CHECK_STR(b, "iSize W");
    CHECK_STR(b + 8, "iSize H");
    n = GetPrivateProfileStringA("Display", NULL, "", b, 10, INI); /* truncated list: size - 2 */
    CHECK_EQ(n, 8);
    CHECK(b[8] == 0 && b[9] == 0);
    n = GetPrivateProfileSectionA("Display", b, sizeof b, INI);
    CHECK_STR(b, "iSize W=1280");
}

TEST(ini_get_int)
{
    seed();
    CHECK_EQ(GetPrivateProfileIntA("General", "uGridsToLoad", 3, INI), 5);
    CHECK_EQ((INT)GetPrivateProfileIntA("General", "iNeg", 0, INI), -42);
    CHECK_EQ(GetPrivateProfileIntA("General", "iHex", 0, INI), 31);
    CHECK_EQ(GetPrivateProfileIntA("General", "iJunk", 0, INI), 12);
    CHECK_EQ(GetPrivateProfileIntA("General", "sLanguage", 7, INI), 0); /* not a number */
    CHECK_EQ(GetPrivateProfileIntA("General", "bNoValue", 7, INI), 7);  /* empty: default */
    CHECK_EQ(GetPrivateProfileIntA("General", "missing", 9, INI), 9);
}

TEST(ini_write)
{
    seed();
    char b[256];
    CHECK(WritePrivateProfileStringA("General", "uGridsToLoad", "7", INI));
    CHECK_EQ(GetPrivateProfileIntA("General", "uGridsToLoad", 0, INI), 7);
    CHECK(WritePrivateProfileStringA("General", "sNew", "added", INI));
    GetPrivateProfileStringA("General", "sNew", "", b, sizeof b, INI);
    CHECK_STR(b, "added");
    CHECK(WritePrivateProfileStringA("Audio", "fVolume", "0.5", INI));
    GetPrivateProfileStringA("audio", "fvolume", "", b, sizeof b, INI);
    CHECK_STR(b, "0.5");
    CHECK(WritePrivateProfileStringA("General", "sLanguage", NULL, INI)); /* delete first occurrence */
    GetPrivateProfileStringA("General", "sLanguage", "", b, sizeof b, INI);
    CHECK_STR(b, "SHADOWED");
    CHECK(WritePrivateProfileStringA("Display", NULL, NULL, INI)); /* delete section */
    CHECK_EQ(GetPrivateProfileIntA("Display", "iSize W", -1, INI), (UINT)-1);
    /* the file on disk: comment kept, new key inside [General] before the blank line, CRLF kept */
    HANDLE h = CreateFileA(INI, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    char file[1024] = { 0 };
    DWORD got = 0;
    ReadFile(h, file, sizeof file - 1, &got, NULL);
    CloseHandle(h);
    CHECK(strstr(file, "; comment=ignored\r\n") != NULL);
    CHECK(strstr(file, "sLanguage=SHADOWED\r\nsNew=added\r\n") != NULL);
    CHECK(strstr(file, "uGridsToLoad=7\r\n") != NULL);
    CHECK(strstr(file, "[Display]") == NULL);
    CHECK(strstr(file, "\r\n[Audio]\r\nfVolume=0.5\r\n") != NULL);
    /* external change is picked up (size changes) */
    s4_write_file(INI, "[General]\r\nsLanguage=GERMAN\r\n");
    GetPrivateProfileStringA("General", "sLanguage", "", b, sizeof b, INI);
    CHECK_STR(b, "GERMAN");
    /* a new file is created; a missing directory fails */
    DeleteFileA("C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\FalloutPrefs.ini");
    CHECK(WritePrivateProfileStringA("Controls", "fMouseSensitivity", "0.0025",
                                     "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\FalloutPrefs.ini"));
    CHECK(GetFileAttributesA("C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\FalloutPrefs.ini") != INVALID_FILE_ATTRIBUTES);
    CHECK(!WritePrivateProfileStringA("A", "b", "c", "C:\\Users\\Player\\NoDir\\x.ini"));
}

TEST(ini_bare_name_is_windows_dir)
{
    CreateDirectoryA("C:\\Windows", NULL);
    CHECK(WritePrivateProfileStringA("S", "k", "v", "fnvnx_test.ini"));
    CHECK(GetFileAttributesA("C:\\Windows\\fnvnx_test.ini") != INVALID_FILE_ATTRIBUTES);
}
