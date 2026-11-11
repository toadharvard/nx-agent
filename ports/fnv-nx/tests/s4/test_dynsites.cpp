/* test_dynsites.cpp - dynamic module / name lookups (LoadLibraryA, GetModuleHandleA, GetProcAddress) the game and its
 * modules make: a present system module answers in any letter-case, with or without ".dll"; the modules ENV_TRUTH M7
 * declares absent answer NULL + ERROR_MOD_NOT_FOUND; RtlGenRandom (SystemFunction036) fills its buffer. Cases dyn_*. */
#include "s4_test.h"
#include "host_boundary.h"
#include <strings.h>

#ifndef ERROR_MOD_NOT_FOUND
#define ERROR_MOD_NOT_FOUND 126L
#endif

extern "C" {
HMODULE GetModuleHandleA(LPCSTR);
HMODULE LoadLibraryA(LPCSTR);
FARPROC GetProcAddress(HMODULE, LPCSTR);
DWORD GetLastError(void);
void SetLastError(DWORD);
}

/* every present system module, spelled the ways the sites pass it */
TEST(dyn_present_modules_any_spelling)
{
    static const char *const names[] = { "kernel32", "user32", "advapi32", "ws2_32", "wsock32", "steam_api", "galaxywrp",
                                         "d3d9", "dsound", "dinput8", "xinput1_3", "ole32", "gdi32", "winmm" };
    char buf[64];
    for (const char *n : names) {
        HMODULE base = GetModuleHandleA(n);
        CHECK(base != NULL);
        snprintf(buf, sizeof buf, "%s.dll", n);
        CHECK(GetModuleHandleA(buf) == base);
        for (char *p = buf; *p; p++)
            if (*p >= 'a' && *p <= 'z')
                *p = (char)(*p - 32);
        CHECK(GetModuleHandleA(buf) == base);
        CHECK(LoadLibraryA(buf) == base);
    }
}

TEST(dyn_absent_modules_by_owner_decision)
{
    static const char *const names[] = { "usp10.dll", "ddraw.dll", "mss32.dll", "d3d9d.dll", "MSS32.DLL", "DDRAW.DLL" };
    for (const char *n : names) {
        SetLastError(0);
        CHECK(LoadLibraryA(n) == NULL);
        CHECK_EQ(GetLastError(), ERROR_MOD_NOT_FOUND);
        SetLastError(0);
        CHECK(GetModuleHandleA(n) == NULL);
        CHECK_EQ(GetLastError(), ERROR_MOD_NOT_FOUND);
    }
}

TEST(dyn_rtlgenrandom)
{
    HMODULE adv = GetModuleHandleA("advapi32.dll");
    CHECK(adv != NULL);
    typedef unsigned char (*fn_t)(void *, unsigned long);
    fn_t f = (fn_t)GetProcAddress(adv, "SystemFunction036");
    CHECK(f != NULL);
    if (!f)
        return;
    unsigned char a[256], b[256];
    memset(a, 0, sizeof a);
    memset(b, 0, sizeof b);
    CHECK(f(a, sizeof a) != 0);
    CHECK(f(b, sizeof b) != 0);
    CHECK(memcmp(a, b, sizeof a) != 0);          /* two draws differ */
    unsigned nz = 0;
    for (unsigned char c : a)
        nz += c != 0;
    CHECK(nz > 128);                             /* not a zero-filled buffer */
    unsigned char g[16];
    memset(g, 0xA5, sizeof g);
    CHECK(f(g + 4, 5) != 0);
    CHECK(g[3] == 0xA5 && g[9] == 0xA5);         /* exactly len bytes written */
    CHECK(f(g, 0) != 0);                          /* len 0: success, nothing written */
}

/* a name with a bridge row but no platform body (L6's d3d9 `sym` rows: DebugSetMute, Direct3DShaderValidatorCreate9,
 * D3DPERF_SetOptions) is returned by GetProcAddress as the row's value; a name with no row stays ERROR_PROC_NOT_FOUND */
static fnvnx_gaddr key_hook(const char *dll, const char *name, uint32_t)
{
    return dll && name && !strcasecmp(dll, "d3d9.dll") && !strcmp(name, "DebugSetMute") ? (fnvnx_gaddr)0x00100010u : 0;
}
TEST(dyn_bridge_row_without_platform_body)
{
    HMODULE d = GetModuleHandleA("d3d9");
    CHECK(d != NULL);
    SetLastError(0);
    CHECK(GetProcAddress(d, "DebugSetMute") == NULL);            /* no row, no body */
    CHECK_EQ(GetLastError(), 127L);
    g_bnd_key_hook = key_hook;
    CHECK((uintptr_t)GetProcAddress(d, "DebugSetMute") == 0x00100010u);
    SetLastError(0);
    CHECK(GetProcAddress(d, "NoSuchExport") == NULL);
    CHECK_EQ(GetLastError(), 127L);
    g_bnd_key_hook = NULL;
}
