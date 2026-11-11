/* test_k32_contract_sh.cpp - SHGetFolderPathA on every CSIDL (task W20 group 9). Locations: Windows 7 defaults under the
 * ENV F2 profile tree (C:\Users\Player, C:\ProgramData, C:\Users\Public, C:\Windows, C:\Program Files); virtual folders have no
 * file-system path (E_FAIL); undefined ids are E_INVALIDARG. ENV P4/P1: 32-bit Windows 7, so the x86 variants equal the base. */
#include "s4_test.h"

#include "../../platform/win32/w32.h"

#ifndef E_INVALIDARG
#define E_INVALIDARG ((HRESULT)0x80070057)
#endif
#ifndef S_FALSE
#define S_FALSE ((HRESULT)1)
#endif
#define CSIDL_FLAG_DONT_VERIFY_ 0x4000
#define CSIDL_FLAG_NO_ALIAS_ 0x1000
#define CSIDL_FLAG_PER_USER_INIT_ 0x0800

#define U "C:\\Users\\Player"
#define RM U "\\AppData\\Roaming"
#define SMD RM "\\Microsoft\\Windows\\Start Menu"
#define PD "C:\\ProgramData"
#define PDSM PD "\\Microsoft\\Windows\\Start Menu"
#define PUB "C:\\Users\\Public"

struct sh_case { int csidl; const char *path; };   /* path NULL: no file-system path (E_FAIL) */
static const sh_case g_sh[] = {
    { 0x00, U "\\Desktop" }, { 0x01, NULL }, { 0x02, SMD "\\Programs" }, { 0x03, NULL }, { 0x04, NULL },
    { 0x05, U "\\Documents" }, { 0x06, U "\\Favorites" }, { 0x07, SMD "\\Programs\\Startup" },
    { 0x08, RM "\\Microsoft\\Windows\\Recent" }, { 0x09, RM "\\Microsoft\\Windows\\SendTo" }, { 0x0a, NULL },
    { 0x0b, SMD }, { 0x0c, U "\\Documents" }, { 0x0d, U "\\Music" }, { 0x0e, U "\\Videos" },
    { 0x10, U "\\Desktop" }, { 0x11, NULL }, { 0x12, NULL }, { 0x13, RM "\\Microsoft\\Windows\\Network Shortcuts" },
    { 0x14, "C:\\Windows\\Fonts" }, { 0x15, RM "\\Microsoft\\Windows\\Templates" },
    { 0x16, PDSM }, { 0x17, PDSM "\\Programs" }, { 0x18, PDSM "\\Programs\\Startup" }, { 0x19, PUB "\\Desktop" },
    { 0x1a, RM }, { 0x1b, RM "\\Microsoft\\Windows\\Printer Shortcuts" }, { 0x1c, U "\\AppData\\Local" },
    { 0x1d, NULL }, { 0x1e, NULL }, { 0x1f, PUB "\\Favorites" },
    { 0x20, U "\\AppData\\Local\\Microsoft\\Windows\\Temporary Internet Files" },
    { 0x21, RM "\\Microsoft\\Windows\\Cookies" }, { 0x22, U "\\AppData\\Local\\Microsoft\\Windows\\History" },
    { 0x23, PD }, { 0x24, "C:\\Windows" }, { 0x25, "C:\\Windows\\system32" }, { 0x26, "C:\\Program Files" },
    { 0x27, U "\\Pictures" }, { 0x28, U }, { 0x29, "C:\\Windows\\system32" }, { 0x2a, "C:\\Program Files" },
    { 0x2b, "C:\\Program Files\\Common Files" }, { 0x2c, "C:\\Program Files\\Common Files" },
    { 0x2d, PD "\\Microsoft\\Windows\\Templates" }, { 0x2e, PUB "\\Documents" },
    { 0x2f, PDSM "\\Programs\\Administrative Tools" }, { 0x30, SMD "\\Programs\\Administrative Tools" }, { 0x31, NULL },
    { 0x35, PUB "\\Music" }, { 0x36, PUB "\\Pictures" }, { 0x37, PUB "\\Videos" }, { 0x38, "C:\\Windows\\Resources" },
    { 0x39, "C:\\Windows\\Resources\\1033" }, { 0x3a, PD "\\OEM Links" },
    { 0x3b, U "\\AppData\\Local\\Microsoft\\Windows\\Burn\\Burn" }, { 0x3d, NULL }, { 0x3e, "C:\\Users" },
};

TEST(k32c_shgetfolderpath_all_csidl)
{
    char out[300];
    bool defined[256] = {};
    for (size_t i = 0; i < sizeof g_sh / sizeof *g_sh; i++) {
        const sh_case &c = g_sh[i];
        defined[c.csidl] = true;
        memset(out, 'x', sizeof out);
        HRESULT hr = SHGetFolderPathA(NULL, c.csidl | CSIDL_FLAG_CREATE, NULL, 0, out);
        s4_checks++;
        if (!c.path) {
            if (hr != E_FAIL) {
                s4_failures++;
                fprintf(stderr, "  FAIL csidl %#x: hr %#lx want E_FAIL\n", c.csidl, (unsigned long)hr);
            }
            continue;
        }
        if (hr != S_OK || strcmp(out, c.path)) {
            s4_failures++;
            fprintf(stderr, "  FAIL csidl %#x: hr %#lx path [%s] want [%s]\n", c.csidl, (unsigned long)hr, hr == S_OK ? out : "-", c.path);
        }
    }
    /* every other id is not a CSIDL */
    for (int id = 0; id < 256; id++)
        if (!defined[id]) {
            HRESULT hr = SHGetFolderPathA(NULL, id, NULL, 0, out);
            s4_checks++;
            if (hr != E_INVALIDARG) {
                s4_failures++;
                fprintf(stderr, "  FAIL undefined csidl %#x: hr %#lx\n", id, (unsigned long)hr);
            }
        }
    CHECK_EQ(SHGetFolderPathA(NULL, 0xeeee, NULL, 0, out), E_INVALIDARG);
}

TEST(k32c_shgetfolderpath_flags_and_tokens)
{
    char out[300];
    /* a folder that does not exist: S_FALSE without CREATE, S_OK with DONT_VERIFY, created by CREATE */
    const char *burn = U "\\AppData\\Local\\Microsoft\\Windows\\Burn\\Burn";
    RemoveDirectoryA(burn);
    RemoveDirectoryA(U "\\AppData\\Local\\Microsoft\\Windows\\Burn");
    memset(out, 0, sizeof out);
    CHECK_EQ(SHGetFolderPathA(NULL, 0x3b, NULL, 0, out), S_FALSE);
    CHECK_STR(out, burn);
    CHECK(GetFileAttributesA(burn) == INVALID_FILE_ATTRIBUTES);
    CHECK_EQ(SHGetFolderPathA(NULL, 0x3b | CSIDL_FLAG_DONT_VERIFY_, NULL, 0, out), S_OK);
    CHECK_STR(out, burn);
    CHECK(GetFileAttributesA(burn) == INVALID_FILE_ATTRIBUTES);
    CHECK_EQ(SHGetFolderPathA(NULL, 0x3b | CSIDL_FLAG_CREATE, NULL, 0, out), S_OK);   /* creates the whole chain */
    CHECK(GetFileAttributesA(burn) & FILE_ATTRIBUTE_DIRECTORY);
    CHECK_EQ(SHGetFolderPathA(NULL, 0x3b, NULL, 0, out), S_OK);
    /* NO_ALIAS and PER_USER_INIT do not change the path */
    CHECK_EQ(SHGetFolderPathA(NULL, 0x05 | CSIDL_FLAG_NO_ALIAS_ | CSIDL_FLAG_PER_USER_INIT_, NULL, 0, out), S_OK);
    CHECK_STR(out, U "\\Documents");
    /* SHGFP_TYPE_DEFAULT is the same location */
    CHECK_EQ(SHGetFolderPathA(NULL, 0x1c, NULL, 1, out), S_OK);
    CHECK_STR(out, U "\\AppData\\Local");
    /* tokens: NULL is the current user, -1 the default user profile; any other handle is not a token */
    CHECK_EQ(SHGetFolderPathA(NULL, 0x1c | CSIDL_FLAG_DONT_VERIFY_, (HANDLE)(intptr_t)-1, 0, out), S_OK);
    CHECK_STR(out, "C:\\Users\\Default\\AppData\\Local");
    CHECK_EQ(SHGetFolderPathA(NULL, 0x23 | CSIDL_FLAG_DONT_VERIFY_, (HANDLE)(intptr_t)-1, 0, out), S_OK);   /* common folders: same */
    CHECK_STR(out, PD);
    CHECK_EQ(SHGetFolderPathA(NULL, 0x00, (HANDLE)2, 1, out), E_FAIL);
    CHECK_EQ(SHGetFolderPathA(NULL, 0x01, (HANDLE)(intptr_t)-1, 0, out), E_FAIL);
}
