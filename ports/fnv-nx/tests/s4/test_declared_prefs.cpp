/* test_declared_prefs.cpp - the declared FalloutPrefs.ini (the launcher gate). The gate of WinMain compares the stored
 * [Display] sD3DDevice with the Description of IDirect3D9::GetAdapterIdentifier(0,0) case-insensitively and takes the launcher branch
 * when they differ; the declared machine is installed with the launcher run once, so the gate must take the normal-start branch.
 * The file is made by platform/win32/declared_files.cpp from the generated include (gen_prefs_ini.py, IDENT_* = the adapter identity images).
 * Oracle: the Description is read here from the same identity image; the gate predicate is evaluated on what
 * the platform's GetPrivateProfileStringA returns from the real file. No module is replaced. */
#include "s4_test.h"

#include <stdio.h>
#include <stdlib.h>
#include <strings.h>

#include <string>

extern "C" void fnvnx_declared_files_first_run(void);

#ifndef IDENT_PATH
#error IDENT_PATH must name the adapter identity image the include was generated from
#endif

#define PROF "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\"
#define PREFS PROF "FalloutPrefs.ini"

/* the oracle: the Description at offset 512 of the identity image (D3DADAPTER_IDENTIFIER9), up to its NUL */
static std::string description_from_table(void)
{
    FILE *f = fopen(IDENT_PATH, "rb");
    CHECK(f != NULL);
    char img[1100];
    CHECK_EQ(fread(img, 1, sizeof img, f), sizeof img);
    fclose(f);
    return std::string(img + 512, strnlen(img + 512, 512));
}

static std::string slurp(const char *win)
{
    HANDLE h = CreateFileA(win, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return "(missing)";
    char buf[4096];
    DWORD got = 0;
    ReadFile(h, buf, sizeof buf, &got, NULL);
    CloseHandle(h);
    return std::string(buf, got);
}

/* A fresh machine has no FalloutPrefs.ini; the first run makes it, and only it. */
TEST(declared_prefs_first_run_makes_the_file)
{
    DeleteFileA(PREFS);
    CHECK_EQ(GetFileAttributesA(PREFS), INVALID_FILE_ATTRIBUTES);
    fnvnx_declared_files_first_run();
    CHECK(GetFileAttributesA(PREFS) != INVALID_FILE_ATTRIBUTES);
    std::string t = slurp(PREFS);
    std::string want = "[Display]\r\nsD3DDevice=" + description_from_table() + "\r\n";
    CHECK(t == want);   /* exactly one key: every other key is absent */
}

/* The gate takes the normal-start branch: the exe's read of the stored value (GetPrivateProfileStringA through the settings reader)
 * compared case-insensitively with the adapter Description is equal; the default "" would differ (the launcher branch). */
TEST(gate_takes_the_normal_start_branch)
{
    DeleteFileA(PREFS);
    fnvnx_declared_files_first_run();
    std::string desc = description_from_table();
    char stored[512];
    GetPrivateProfileStringA("Display", "sD3DDevice", "", stored, sizeof stored, PREFS);
    CHECK_EQ(strcasecmp(stored, desc.c_str()), 0);   /* gate false: the normal start */
    CHECK(strcasecmp("", desc.c_str()) != 0);          /* the file absent: stored = the default "", gate true: the launcher branch */
    /* the settings the exe reads and the file does not hold take their built-in defaults: the key is absent */
    char other[64];
    DWORD n = GetPrivateProfileStringA("Display", "iSize W", "default", other, sizeof other, PREFS);
    CHECK(std::string(other) == "default");
    CHECK_EQ(n, 7u);
}

/* What the exe or the player changed is never overwritten. */
TEST(declared_prefs_never_overwrites)
{
    DeleteFileA(PREFS);
    fnvnx_declared_files_first_run();
    WritePrivateProfileStringA("Display", "sD3DDevice", "changed", PREFS);
    fnvnx_declared_files_first_run();
    char stored[64];
    GetPrivateProfileStringA("Display", "sD3DDevice", "", stored, sizeof stored, PREFS);
    CHECK(std::string(stored) == "changed");
    DeleteFileA(PREFS);
}
