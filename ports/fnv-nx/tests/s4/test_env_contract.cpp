/* test_env_contract.cpp - the environment states of platform/manifest/ENV_TRUTH.md as the imports report them:
 * version and directories (P4, P5), temp files, processor features, error mode, environment expansion, date and
 * time formats (L1), the module table (M1-M5), no console (F5), no Steam client (M6), no debugger (P3). */
#include "s4_test.h"
#include "../../platform/win32/w32.h"

#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

struct OSVI { DWORD dwOSVersionInfoSize, dwMajorVersion, dwMinorVersion, dwBuildNumber, dwPlatformId; char szCSDVersion[128]; };
struct OSVIEX { OSVI b; WORD wServicePackMajor, wServicePackMinor, wSuiteMask; BYTE wProductType, wReserved; };
typedef BOOL (*ctrl_fn)(DWORD);
extern "C" {
BOOL GetVersionExA(void *);
DWORD GetVersion(void);
UINT GetSystemDirectoryA(LPSTR, UINT);
UINT GetWindowsDirectoryA(LPSTR, UINT);
UINT SetErrorMode(UINT);
DWORD GetTempPathA(DWORD, LPSTR);
UINT GetTempFileNameA(LPCSTR, LPCSTR, UINT, LPSTR);
UINT GetTempFileNameW(LPCWSTR, LPCWSTR, UINT, LPWSTR);
BOOL IsProcessorFeaturePresent(DWORD);
DWORD ExpandEnvironmentStringsA(LPCSTR, LPSTR, DWORD);
BOOL IsDBCSLeadByte(BYTE);
BOOL DisableThreadLibraryCalls(HMODULE);
BOOL DeleteFileW(LPCWSTR);
BOOL MoveFileW(LPCWSTR, LPCWSTR);
void *FindResourceW(HMODULE, LPCWSTR, LPCWSTR);
void *FindResourceA(HMODULE, LPCSTR, LPCSTR);
LSTATUS RegOpenKeyA(HKEY, LPCSTR, PHKEY);
int GetDateFormatA(LCID, DWORD, const SYSTEMTIME *, LPCSTR, LPSTR, int);
int GetTimeFormatA(LCID, DWORD, const SYSTEMTIME *, LPCSTR, LPSTR, int);
void FatalAppExitA(UINT, LPCSTR);
BOOL SetConsoleCtrlHandler(ctrl_fn, BOOL);
int fnvnx_module_load_count(HMODULE);
bool SteamAPI_Init(void);
bool SteamAPI_IsSteamRunning(void);
void SteamAPI_Shutdown(void);
void SteamAPI_RunCallbacks(void);
void SteamAPI_RegisterCallback(void *, int);
void SteamAPI_UnregisterCallback(void *);
void *SteamUser(void);
void *SteamFriends(void);
void *SteamUtils(void);
void *SteamApps(void);
void *SteamUserStats(void);
int fnvnx_steam_callback_count(void);
void *fnvnx_msvcrt_lookup(const char *);
}

#define ERR_AFTER(stmt, e) do { SetLastError(0xDEAD); stmt; CHECK_EQ(GetLastError(), (e)); } while (0)

TEST(env_version)
{
    OSVIEX v;
    memset(&v, 0xCC, sizeof v);
    v.b.dwOSVersionInfoSize = sizeof(OSVI);
    CHECK(GetVersionExA(&v));
    CHECK_EQ(v.b.dwMajorVersion, 6); CHECK_EQ(v.b.dwMinorVersion, 1); CHECK_EQ(v.b.dwBuildNumber, 7601);
    CHECK_EQ(v.b.dwPlatformId, 2);
    CHECK_STR(v.b.szCSDVersion, "Service Pack 1");
    CHECK_EQ(v.wServicePackMajor, 0xCCCC);   /* the short structure: nothing written past it */
    v.b.dwOSVersionInfoSize = sizeof(OSVIEX);
    CHECK(GetVersionExA(&v));
    CHECK_EQ(v.wServicePackMajor, 1); CHECK_EQ(v.wServicePackMinor, 0); CHECK_EQ(v.wSuiteMask, 0x100);
    CHECK_EQ(v.wProductType, 1); CHECK_EQ(v.wReserved, 0);
    v.b.dwOSVersionInfoSize = sizeof(OSVI) - 1;
    SetLastError(0);
    CHECK(!GetVersionExA(&v));
    CHECK_EQ(GetLastError(), 122);
    CHECK_EQ(GetVersion(), 0x1DB10106u);
}

TEST(env_directories)
{
    char b[MAX_PATH];
    CHECK_EQ(GetSystemDirectoryA(b, sizeof b), 19);
    CHECK_STR(b, "C:\\Windows\\system32");
    CHECK_EQ(GetSystemDirectoryA(b, 19), 20);   /* too small: the size needed, NUL included */
    CHECK_EQ(GetSystemDirectoryA(NULL, 0), 20);
    CHECK_EQ(GetWindowsDirectoryA(b, sizeof b), 10);
    CHECK_STR(b, "C:\\Windows");
    CHECK_EQ(GetWindowsDirectoryA(b, 10), 11);
    CHECK_EQ(GetFileAttributesA("C:\\Windows\\system32") & 0x10, 0x10);
    CHECK_EQ(GetFileAttributesA("C:\\Windows") & 0x10, 0x10);
    WIN32_FIND_DATAA fd;
    HANDLE f = FindFirstFileA("C:\\Windows\\system32\\*.dll", &fd);   /* exists and is empty */
    CHECK(f == INVALID_HANDLE_VALUE);
    if (f != INVALID_HANDLE_VALUE) FindClose(f);
}

TEST(env_temp)
{
    static const char T[] = "C:\\Users\\Player\\AppData\\Local\\Temp\\";
    char b[MAX_PATH], n[MAX_PATH], n2[MAX_PATH];
    CHECK_EQ(GetTempPathA(sizeof b, b), sizeof T - 1);
    CHECK_STR(b, T);
    CHECK_EQ(GetTempPathA(5, n), sizeof T);
    CHECK_EQ(GetFileAttributesA(T) & 0x10, 0x10);
    CHECK(CreateDirectoryA("C:\\tmpx", NULL) || GetLastError() == ERROR_ALREADY_EXISTS);
    SetEnvironmentVariableA("TMP", "C:\\tmpx");   /* TMP first, then TEMP */
    CHECK_EQ(GetTempPathA(sizeof n, n), 8);
    CHECK_STR(n, "C:\\tmpx\\");
    SetEnvironmentVariableA("TMP", NULL);
    CHECK_EQ(GetTempPathA(sizeof n, n), sizeof T - 1);   /* TEMP */
    SetEnvironmentVariableA("TMP", "C:\\Users\\Player\\AppData\\Local\\Temp");
    /* uUnique 0: a new empty file each time */
    UINT u = GetTempFileNameA(b, "fnvx", 0, n);
    CHECK(u != 0 && u <= 0xFFFF);
    char want[MAX_PATH];
    snprintf(want, sizeof want, "%sfnv%X.tmp", T, (unsigned)u);
    CHECK_STR(n, want);
    CHECK_EQ(GetFileAttributesA(n) & 0x10, 0);
    HANDLE h = CreateFileA(n, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileSize(h, NULL), 0);
    CloseHandle(h);
    UINT u2 = GetTempFileNameA(b, "fnvx", 0, n2);
    CHECK(u2 != 0 && strcmp(n, n2) != 0);
    CHECK(DeleteFileA(n2));
    /* nonzero: that number, no file */
    CHECK_EQ(GetTempFileNameA("C:\\tmpx", "ab", 0x1ABCD, n2), 0x1ABCD);
    CHECK_STR(n2, "C:\\tmpx\\abABCD.tmp");
    ERR_AFTER(GetFileAttributesA(n2), ERROR_FILE_NOT_FOUND);
    SetLastError(0);
    CHECK_EQ(GetTempFileNameA("C:\\no\\such\\dir", "ab", 0, n2), 0);
    CHECK_EQ(GetLastError(), 267);
    CHECK_EQ(GetTempFileNameA("C:\\no\\such\\dir", "ab", 5, n2), 0);
    CHECK_EQ(GetLastError(), 267);
    char longp[400] = "C:\\tmpx\\";
    memset(longp + 8, 'a', 250); longp[258] = 0;
    CHECK_EQ(GetTempFileNameA(longp, "ab", 0, n2), 0);
    CHECK_EQ(GetLastError(), 111);
    /* wide: the same file namespace */
    WCHAR wn[MAX_PATH], wm[MAX_PATH] = L"C:\\tmpx\\moved \u0416.tmp";
    UINT uw = GetTempFileNameW(L"C:\\tmpx", L"w\u00e9", 0, wn);
    CHECK(uw != 0);
    char an[MAX_PATH];
    WideCharToMultiByte(CP_ACP, 0, wn, -1, an, sizeof an, NULL, NULL);
    snprintf(want, sizeof want, "C:\\tmpx\\w\xe9%X.tmp", (unsigned)uw);
    CHECK_STR(an, want);
    CHECK_EQ(GetFileAttributesA(an) & 0x10, 0);
    CHECK(MoveFileW(wn, wm));
    ERR_AFTER(GetFileAttributesA(an), ERROR_FILE_NOT_FOUND);
    h = CreateFileW(wm, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    SetLastError(0);
    CHECK(!MoveFileW(wn, wm)); CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(DeleteFileW(wm));
    SetLastError(0);
    CHECK(!DeleteFileW(wm)); CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(!DeleteFileW(NULL));
    CHECK(DeleteFileA(n));
}

extern "C" unsigned char fnvnx_pf_table[64];
TEST(env_processor_and_modes)
{
    static const int yes[] = { 2, 3, 6, 8, 9, 10, 12 };
    for (unsigned f = 0; f < 80; f++) {
        bool want = false;
        for (int y : yes) want |= (unsigned)y == f;
        CHECK_EQ(IsProcessorFeaturePresent(f), want ? TRUE : FALSE);
        if (f < 64) CHECK_EQ(fnvnx_pf_table[f], want);
    }
    CHECK_EQ(IsProcessorFeaturePresent(0xFFFFFFFFu), FALSE);
    UINT old = SetErrorMode(0x8003);
    CHECK_EQ(SetErrorMode(1), 0x8003);
    CHECK_EQ(SetErrorMode(old), 1);
    for (unsigned c = 0; c < 256; c++) CHECK_EQ(IsDBCSLeadByte((BYTE)c), FALSE);
}

TEST(env_expand)
{
    char b[128];
    SetEnvironmentVariableA("FNVX_A", "va lue");
    CHECK_EQ(ExpandEnvironmentStringsA("x%FNVX_A%y%fnvx_a%", b, sizeof b), 15);   /* chars stored, NUL included */
    CHECK_STR(b, "xva luey" "va lue");
    CHECK_EQ(ExpandEnvironmentStringsA("%NO_SUCH_VAR_X%-%", b, sizeof b), 18);
    CHECK_STR(b, "%NO_SUCH_VAR_X%-%");
    CHECK_EQ(ExpandEnvironmentStringsA("a%%b%FNVX_A", b, sizeof b), 12);
    CHECK_STR(b, "a%%b%FNVX_A");
    CHECK_EQ(ExpandEnvironmentStringsA("%FNVX_A%", b, 3), 7);   /* too small: the size needed */
    CHECK_EQ(ExpandEnvironmentStringsA("%FNVX_A%", NULL, 0), 7);
    CHECK_EQ(ExpandEnvironmentStringsA("", b, sizeof b), 1);
    CHECK_STR(b, "");
    SetEnvironmentVariableA("FNVX_A", NULL);
}

TEST(env_date_time_format)
{
    SYSTEMTIME st = { 2010, 10, 0 /* wrong on purpose: recomputed */, 19, 15, 5, 7, 0 };
    char b[128];
    CHECK_EQ(GetDateFormatA(0x409, 0, &st, NULL, b, sizeof b), 11); CHECK_STR(b, "10/19/2010");
    CHECK_EQ(GetDateFormatA(0x400, 1, &st, NULL, b, sizeof b), 11); CHECK_STR(b, "10/19/2010");
    CHECK(GetDateFormatA(0x409, 2, &st, NULL, b, sizeof b)); CHECK_STR(b, "Tuesday, October 19, 2010");
    CHECK(GetDateFormatA(0x409, 8 | 0x80000000u, &st, NULL, b, sizeof b)); CHECK_STR(b, "October 2010");
    CHECK(GetDateFormatA(0x409, 0, &st, "d dd ddd dddd ddddd|M MM MMM MMMM|y yy yyyy yyyyy|g gg", b, sizeof b));
    CHECK_STR(b, "19 19 Tue Tuesday Tuesday|10 10 Oct October|10 10 2010 2010|A.D. A.D.");
    SYSTEMTIME s2 = { 2005, 3, 0, 4, 0, 0, 0, 0 };
    CHECK(GetDateFormatA(0x409, 0, &s2, "d-dd-M-MM-y-yy 'it''s d' x'q", b, sizeof b));
    CHECK_STR(b, "4-04-3-03-5-05 it's d xq");
    CHECK_EQ(GetDateFormatA(0x409, 0, &st, "yyyy", NULL, 0), 5);
    ERR_AFTER(CHECK_EQ(GetDateFormatA(0x409, 0, &st, "yyyy", b, 4), 0), ERROR_INSUFFICIENT_BUFFER);
    ERR_AFTER(CHECK_EQ(GetDateFormatA(0x409, 1, &st, "yyyy", b, sizeof b), 0), ERROR_INVALID_FLAGS);
    ERR_AFTER(CHECK_EQ(GetDateFormatA(0x409, 3, &st, NULL, b, sizeof b), 0), ERROR_INVALID_FLAGS);
    ERR_AFTER(CHECK_EQ(GetDateFormatA(0x40C, 0, &st, NULL, b, sizeof b), 0), ERROR_INVALID_PARAMETER);   /* fr-FR: not installed */
    SYSTEMTIME bad = { 2011, 2, 0, 29, 0, 0, 0, 0 };
    ERR_AFTER(CHECK_EQ(GetDateFormatA(0x409, 0, &bad, NULL, b, sizeof b), 0), ERROR_INVALID_PARAMETER);
    bad.wDay = 28; bad.wHour = 99;   /* the time fields do not matter to a date */
    CHECK(GetDateFormatA(0x409, 0, &bad, NULL, b, sizeof b)); CHECK_STR(b, "2/28/2011");
    SYSTEMTIME now; GetLocalTime(&now);
    char want[32]; snprintf(want, sizeof want, "%04d", now.wYear);
    CHECK(GetDateFormatA(0x409, 0, NULL, "yyyy", b, sizeof b)); CHECK_STR(b, want);

    CHECK_EQ(GetTimeFormatA(0x409, 0, &st, NULL, b, sizeof b), 11); CHECK_STR(b, "3:05:07 PM");
    CHECK(GetTimeFormatA(0x409, 2, &st, NULL, b, sizeof b)); CHECK_STR(b, "3:05 PM");
    CHECK(GetTimeFormatA(0x409, 1, &st, NULL, b, sizeof b)); CHECK_STR(b, "3 PM");
    CHECK(GetTimeFormatA(0x409, 4, &st, NULL, b, sizeof b)); CHECK_STR(b, "3:05:07");
    CHECK(GetTimeFormatA(0x409, 8, &st, NULL, b, sizeof b)); CHECK_STR(b, "15:05:07 PM");
    CHECK(GetTimeFormatA(0x409, 2 | 4 | 8 | 0x80000000u, &st, NULL, b, sizeof b)); CHECK_STR(b, "15:05");
    CHECK(GetTimeFormatA(0x409, 0, &st, "h hh H HH hhh|m mm|s ss|t tt ttt 'o''clock h' d", b, sizeof b));
    CHECK_STR(b, "3 03 15 15 03|5 05|7 07|P PM PM o'clock h d");
    SYSTEMTIME mid = { 2010, 1, 0, 1, 0, 0, 9, 0 };
    CHECK(GetTimeFormatA(0x409, 0, &mid, "h:H:tt", b, sizeof b)); CHECK_STR(b, "12:0:AM");
    mid.wHour = 12;
    CHECK(GetTimeFormatA(0x409, 0, &mid, "h:H:t", b, sizeof b)); CHECK_STR(b, "12:12:P");
    CHECK_EQ(GetTimeFormatA(0x409, 0, &st, "HH", NULL, 0), 3);
    ERR_AFTER(CHECK_EQ(GetTimeFormatA(0x409, 0, &st, "HH", b, 2), 0), ERROR_INSUFFICIENT_BUFFER);
    ERR_AFTER(CHECK_EQ(GetTimeFormatA(0x409, 0x10, &st, NULL, b, sizeof b), 0), ERROR_INVALID_FLAGS);
    SYSTEMTIME tb = st; tb.wMinute = 60;
    ERR_AFTER(CHECK_EQ(GetTimeFormatA(0x409, 0, &tb, NULL, b, sizeof b), 0), ERROR_INVALID_PARAMETER);
    tb = st; tb.wMonth = 99;   /* the date fields do not matter to a time */
    CHECK(GetTimeFormatA(0x409, 0, &tb, "H", b, sizeof b)); CHECK_STR(b, "15");
    CHECK(GetTimeFormatA(0x409, 0, NULL, NULL, b, sizeof b) > 0);
}

static BOOL ctrl_a(DWORD) { return TRUE; }
static BOOL ctrl_b(DWORD) { return FALSE; }
TEST(env_console_and_registry)
{
    CHECK(SetConsoleCtrlHandler(ctrl_a, TRUE));
    CHECK(SetConsoleCtrlHandler(ctrl_a, TRUE));   /* a handler may be listed more than once */
    CHECK(SetConsoleCtrlHandler(ctrl_a, FALSE));
    CHECK(SetConsoleCtrlHandler(ctrl_a, FALSE));
    ERR_AFTER(CHECK(!SetConsoleCtrlHandler(ctrl_a, FALSE)), ERROR_INVALID_PARAMETER);
    ERR_AFTER(CHECK(!SetConsoleCtrlHandler(ctrl_b, FALSE)), ERROR_INVALID_PARAMETER);
    CHECK(SetConsoleCtrlHandler(NULL, TRUE));     /* ignore Ctrl+C */
    CHECK(SetConsoleCtrlHandler(NULL, FALSE));

    HKEY k = NULL, hklm = (HKEY)(uintptr_t)0x80000002u, made = NULL;
    CHECK_EQ(RegOpenKeyA(hklm, NULL, &k), 0); CHECK(k == hklm);
    k = NULL;
    CHECK_EQ(RegOpenKeyA(hklm, "", &k), 0); CHECK(k == hklm);
    CHECK_EQ(RegCreateKeyExA(hklm, "Software\\FnvxEnvTest", 0, NULL, 0, 0xF003F, NULL, &made, NULL), 0);
    DWORD one = 1;
    CHECK_EQ(RegSetValueExA(made, "v", 0, 4, (const BYTE *)&one, 4), 0);
    RegCloseKey(made);
    CHECK_EQ(RegOpenKeyA(hklm, "software\\fnvxenvtest", &k), 0);
    DWORD got = 0, len = 4;
    CHECK_EQ(RegQueryValueExA(k, "v", NULL, NULL, (LPBYTE)&got, &len), 0); CHECK_EQ(got & 0xFFFFFFFFu, 1);
    CHECK_EQ(RegSetValueExA(k, "w", 0, 4, (const BYTE *)&one, 4), 0);   /* MAXIMUM_ALLOWED: write access too */
    RegCloseKey(k);
    CHECK_EQ(RegOpenKeyA(hklm, "Software\\FnvxNoSuchKey", &k), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(RegOpenKeyA(hklm, "x", NULL), ERROR_INVALID_PARAMETER);

    /* F5: no console, no standard handles */
    DWORD ids[] = { STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE }, n = 77, mode = 0;
    for (DWORD id : ids) {
        SetLastError(0);
        CHECK(GetStdHandle(id) == NULL);
        CHECK_EQ(GetLastError(), 0);
    }
    ERR_AFTER(CHECK(GetStdHandle(5) == INVALID_HANDLE_VALUE), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK_EQ(GetFileType(NULL), FILE_TYPE_UNKNOWN), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK_EQ(GetConsoleCP(), 0), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK_EQ(GetConsoleOutputCP(), 0), ERROR_INVALID_HANDLE);
    HANDLE f = CreateFileA("C:\\envstd.txt", GENERIC_WRITE | GENERIC_READ, 0, NULL, CREATE_ALWAYS, 0, NULL);
    HANDLE hs[] = { NULL, f, INVALID_HANDLE_VALUE, (HANDLE)(uintptr_t)7 };
    for (HANDLE h : hs) {
        ERR_AFTER(CHECK(!GetConsoleMode(h, &mode)), ERROR_INVALID_HANDLE);
        ERR_AFTER(CHECK(!WriteConsoleA(h, "x", 1, &n, NULL)), ERROR_INVALID_HANDLE);
        ERR_AFTER(CHECK(!WriteConsoleW(h, L"x", 1, &n, NULL)), ERROR_INVALID_HANDLE);
    }
    ERR_AFTER(CHECK(!WriteFile(NULL, "x", 1, &n, NULL)), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK(!WriteFile((HANDLE)(uintptr_t)7, "x", 1, &n, NULL)), ERROR_INVALID_HANDLE);   /* the old pseudo handle */
    CHECK(SetStdHandle(STD_OUTPUT_HANDLE, f));
    CHECK(GetStdHandle(STD_OUTPUT_HANDLE) == f);
    CHECK_EQ(GetFileType(GetStdHandle(STD_OUTPUT_HANDLE)), FILE_TYPE_DISK);
    CHECK(SetStdHandle(STD_OUTPUT_HANDLE, NULL));
    CHECK(GetStdHandle(STD_OUTPUT_HANDLE) == NULL);
    ERR_AFTER(CHECK(!SetStdHandle(5, f)), ERROR_INVALID_HANDLE);
    CloseHandle(f);
    DeleteFileA("C:\\envstd.txt");
    /* the CRT's stdout/stderr without handles: output is discarded, the calls succeed as in a GUI process */
    typedef int (*puts_fn)(const char *);
    puts_fn p = (puts_fn)fnvnx_msvcrt_lookup("puts");
    if (p) CHECK(p("env_contract: discarded") >= 0);
    OutputDebugStringA(NULL);   /* no debugger: delivered nowhere */
    OutputDebugStringA("env_contract: nobody listens");
    CHECK(!IsDebuggerPresent());
}

static FARPROC game_resolve(const char *p) { return !strcmp(p, "AtiProbe") ? (FARPROC)(void *)&GetTickCount : NULL; }
TEST(env_modules)
{
    static const char *const sys[] = { "kernel32", "user32", "gdi32", "advapi32", "shell32", "ole32", "comctl32", "winmm",
                                       "wsock32", "dsound", "dinput8", "xinput1_3", "d3d9", "msvcrt", "ws2_32", "steam_api", "galaxywrp" };
    HMODULE seen[17];
    char b[MAX_PATH], want[MAX_PATH], q[MAX_PATH];
    for (int i = 0; i < 14; i++) {
        HMODULE h = GetModuleHandleA(sys[i]);
        seen[i] = h;
        CHECK(h != NULL && h != GetModuleHandleA(NULL));
        for (int j = 0; j < i; j++) CHECK(seen[j] != h);
        snprintf(q, sizeof q, "%s.DLL", sys[i]);
        for (char *c = q; *c && *c != '.'; c++) *c = (char)(*c >= 'a' && *c <= 'z' ? *c - 32 : *c);
        CHECK(GetModuleHandleA(q) == h);
        CHECK(LoadLibraryA(q) == h);
        snprintf(q, sizeof q, "c:/WINDOWS\\System32\\%s", sys[i]);
        CHECK(LoadLibraryA(q) == h);
        snprintf(q, sizeof q, "C:\\Windows\\%s.dll", sys[i]);   /* not where the module is */
        ERR_AFTER(CHECK(LoadLibraryA(q) == NULL), 126);
        snprintf(want, sizeof want, "C:\\Windows\\system32\\%s.dll", sys[i]);
        CHECK_EQ(GetModuleFileNameA(h, b, sizeof b), strlen(want));
        CHECK_STR(b, want);
        CHECK(FreeLibrary(h)); CHECK(FreeLibrary(h));   /* never unloaded */
        CHECK(GetModuleHandleA(sys[i]) == h);
        CHECK(DisableThreadLibraryCalls(h));
    }
    WCHAR wk[] = L"Kernel32.dll";
    CHECK(GetModuleHandleW(wk) == seen[0]);
    CHECK(GetModuleHandleA(NULL) == (HMODULE)(uintptr_t)0x00400000u);
    CHECK(DisableThreadLibraryCalls(GetModuleHandleA(NULL)));
    static const char *const absent[] = { "nvapi.dll", "atiadlxx.dll", "atiadlxy.dll", "dbghelp.dll", "usp10.dll", "d3d9d.dll",
                                          "mscoree.dll", "kernel33", "C:\\Windows\\system32\\nvapi.dll", "" };
    for (const char *a : absent) {
        ERR_AFTER(CHECK(LoadLibraryA(a) == NULL), 126);
        ERR_AFTER(CHECK(GetModuleHandleA(a) == NULL), 126);
    }
    WCHAR wm[] = L"mscoree.dll";
    ERR_AFTER(CHECK(GetModuleHandleW(wm) == NULL), 126);
    /* GetProcAddress: the import binding's lookup, by name and by ordinal */
    HMODULE k = seen[0];
    CHECK((void *)GetProcAddress(k, "GetTickCount") == (void *)&GetTickCount);
    CHECK((void *)GetProcAddress(k, "IsProcessorFeaturePresent") == (void *)&IsProcessorFeaturePresent);
    CHECK((void *)GetProcAddress(seen[1], "MessageBoxA") == (void *)&MessageBoxA);
    CHECK((void *)GetProcAddress(seen[3], "RegOpenKeyA") == (void *)&RegOpenKeyA);
    CHECK(GetProcAddress(seen[8], "WSAStartup") == (FARPROC)fnvnx_win32_lookup_export("wsock32", "WSAStartup"));
    CHECK(GetProcAddress(seen[8], "WSAStartup") != NULL);
    CHECK(GetProcAddress(seen[8], (LPCSTR)(uintptr_t)115) == GetProcAddress(seen[8], "WSAStartup"));
    CHECK(GetProcAddress(seen[13], "memcpy") == (FARPROC)fnvnx_msvcrt_lookup("memcpy"));
    CHECK(GetProcAddress(seen[13], "memcpy") != NULL);
    ERR_AFTER(CHECK(GetProcAddress(k, "MessageBoxA") == NULL), 127);   /* another module's export */
    ERR_AFTER(CHECK(GetProcAddress(k, "NoSuchExport") == NULL), 127);
    ERR_AFTER(CHECK(GetProcAddress(seen[8], (LPCSTR)(uintptr_t)999) == NULL), 127);
    ERR_AFTER(CHECK(GetProcAddress(GetModuleHandleA(NULL), "GetTickCount") == NULL), 127);
    ERR_AFTER(CHECK(GetProcAddress((HMODULE)(uintptr_t)0x12345678, "GetTickCount") == NULL), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK(GetProcAddress(NULL, "GetTickCount") == NULL), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK(!FreeLibrary((HMODULE)(uintptr_t)0x12345678)), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK(!FreeLibrary(NULL)), ERROR_INVALID_HANDLE);
    ERR_AFTER(CHECK(!DisableThreadLibraryCalls((HMODULE)(uintptr_t)0x12345678)), 126);
    /* a translated game module registered by the loader (M1) */
    ERR_AFTER(CHECK(LoadLibraryA("ATIMGPUD.dll") == NULL), 126);
    fnvnx_register_module("atimgpud.dll", game_resolve);
    HMODULE a = GetModuleHandleA("AtiMgpud");
    CHECK(a != NULL);
    int base = fnvnx_module_load_count(a);
    CHECK(base >= 1);
    CHECK(LoadLibraryA("ATIMGPUD.dll") == a);
    CHECK(LoadLibraryA("C:\\FalloutNV\\atimgpud.DLL") == a);
    CHECK_EQ(fnvnx_module_load_count(a), base + 2);
    CHECK(GetProcAddress(a, "AtiProbe") == (FARPROC)(void *)&GetTickCount);
    ERR_AFTER(CHECK(GetProcAddress(a, "Other") == NULL), 127);
    CHECK_EQ(GetModuleFileNameA(a, b, sizeof b), 25);
    CHECK_STR(b, "C:\\FalloutNV\\atimgpud.dll");
    CHECK(FreeLibrary(a)); CHECK(FreeLibrary(a));
    CHECK_EQ(fnvnx_module_load_count(a), base);
    CHECK(FreeLibrary(a));   /* the loader's own reference stays: translated code is never unmapped */
    CHECK_EQ(fnvnx_module_load_count(a), base);
    CHECK(GetModuleHandleA("atimgpud.dll") == a);
    CHECK_EQ(fnvnx_module_load_count(seen[0]), -1);   /* system: pinned */
    CHECK_EQ(fnvnx_module_load_count((HMODULE)(uintptr_t)0x12345678), 0);
}

TEST(env_resource_wide)
{
    /* without an image both entry points fail alike; with integer ids the wide one is the same lookup */
    SetLastError(0);
    void *a = FindResourceA((HMODULE)(uintptr_t)0x5550000, (LPCSTR)(uintptr_t)1, (LPCSTR)(uintptr_t)16);
    DWORD ea = GetLastError();
    SetLastError(0);
    void *w = FindResourceW((HMODULE)(uintptr_t)0x5550000, (LPCWSTR)(uintptr_t)1, (LPCWSTR)(uintptr_t)16);
    CHECK(a == w); CHECK_EQ(GetLastError(), ea);
    WCHAR nm[] = L"NAME";
    CHECK(FindResourceW((HMODULE)(uintptr_t)0x5550000, nm, (LPCWSTR)(uintptr_t)16) == NULL);
}

TEST(env_steam_not_running)
{
    int cb1 = 0, cb2 = 0;
    CHECK(!SteamAPI_IsSteamRunning());
    CHECK(!SteamAPI_Init());
    CHECK(!SteamAPI_Init());
    CHECK(SteamUser() == NULL); CHECK(SteamFriends() == NULL); CHECK(SteamUtils() == NULL);
    CHECK(SteamApps() == NULL); CHECK(SteamUserStats() == NULL);
    int base = fnvnx_steam_callback_count();
    SteamAPI_RegisterCallback(&cb1, 1101);
    SteamAPI_RegisterCallback(&cb2, 1102);
    SteamAPI_RegisterCallback(&cb1, 1103);   /* one object, two ids: two registrations */
    CHECK_EQ(fnvnx_steam_callback_count(), base + 3);
    SteamAPI_RunCallbacks();
    CHECK(cb1 == 0 && cb2 == 0);   /* the objects are not touched: nothing is delivered */
    SteamAPI_UnregisterCallback(&cb1);
    CHECK_EQ(fnvnx_steam_callback_count(), base + 1);
    SteamAPI_UnregisterCallback(&cb1);
    SteamAPI_UnregisterCallback(&cb2);
    CHECK_EQ(fnvnx_steam_callback_count(), base);
    SteamAPI_Shutdown();
    CHECK(!SteamAPI_IsSteamRunning());
}

/* One test per reached Galaxy/Steam entry (platform/manifest/check_galaxy.py: the 11 names of the exe's galaxywrp.dll import). Oracles: the
 * Steamworks SDK documentation cited at each body (manifest/env_const.tsv, services.cpp), state "client not running / not initialised". */
TEST(steam_SteamAPI_Init) { CHECK(!SteamAPI_Init()); CHECK(!SteamAPI_Init()); /* a repeated call fails the same way */ }
TEST(steam_SteamAPI_IsSteamRunning) { CHECK(!SteamAPI_IsSteamRunning()); SteamAPI_Init(); CHECK(!SteamAPI_IsSteamRunning()); }
TEST(steam_SteamAPI_Shutdown)
{
    SteamAPI_Shutdown();   /* valid when never initialised, and twice */
    SteamAPI_Shutdown();
    CHECK(!SteamAPI_IsSteamRunning());
    int o = 0, base = fnvnx_steam_callback_count();
    SteamAPI_RegisterCallback(&o, 1);
    SteamAPI_Shutdown();
    CHECK_EQ(fnvnx_steam_callback_count(), base + 1);   /* the registrations belong to their objects */
    SteamAPI_UnregisterCallback(&o);
}
TEST(steam_SteamAPI_RunCallbacks)
{
    int o = 0;
    SteamAPI_RegisterCallback(&o, 7);
    SteamAPI_RunCallbacks();
    SteamAPI_RunCallbacks();
    CHECK(o == 0);   /* nothing is queued without a client: the registered object is never touched */
    SteamAPI_UnregisterCallback(&o);
}
TEST(steam_SteamAPI_RegisterCallback)
{
    int o = 0, p = 0, base = fnvnx_steam_callback_count();
    SteamAPI_RegisterCallback(&o, 101);
    CHECK_EQ(fnvnx_steam_callback_count(), base + 1);
    SteamAPI_RegisterCallback(&o, 102);   /* one object, two ids: two registrations */
    SteamAPI_RegisterCallback(&p, 101);
    CHECK_EQ(fnvnx_steam_callback_count(), base + 3);
    for (int i = 0; i < 100; i++) SteamAPI_RegisterCallback(&p, 200 + i);   /* the registry grows past its first capacity */
    CHECK_EQ(fnvnx_steam_callback_count(), base + 103);
    SteamAPI_UnregisterCallback(&o);
    SteamAPI_UnregisterCallback(&p);
    CHECK_EQ(fnvnx_steam_callback_count(), base);
}
TEST(steam_SteamAPI_UnregisterCallback)
{
    int o = 0, p = 0, base = fnvnx_steam_callback_count();
    SteamAPI_UnregisterCallback(&o);   /* an object with no registration: a no-op */
    CHECK_EQ(fnvnx_steam_callback_count(), base);
    SteamAPI_RegisterCallback(&o, 1);
    SteamAPI_RegisterCallback(&p, 2);
    SteamAPI_RegisterCallback(&o, 3);
    SteamAPI_UnregisterCallback(&o);   /* all pairs of the object, the others stay */
    CHECK_EQ(fnvnx_steam_callback_count(), base + 1);
    SteamAPI_UnregisterCallback(&p);
    CHECK_EQ(fnvnx_steam_callback_count(), base);
}
TEST(steam_SteamUser) { CHECK(SteamUser() == NULL); SteamAPI_Init(); CHECK(SteamUser() == NULL); }
TEST(steam_SteamFriends) { CHECK(SteamFriends() == NULL); SteamAPI_Init(); CHECK(SteamFriends() == NULL); }
TEST(steam_SteamUtils) { CHECK(SteamUtils() == NULL); SteamAPI_Init(); CHECK(SteamUtils() == NULL); }
TEST(steam_SteamApps) { CHECK(SteamApps() == NULL); SteamAPI_Init(); CHECK(SteamApps() == NULL); }
TEST(steam_SteamUserStats) { CHECK(SteamUserStats() == NULL); SteamAPI_Init(); CHECK(SteamUserStats() == NULL); }

/* runs f in a child process; the wait status */
static int in_child(void (*f)(void))
{
    fflush(NULL);
    pid_t p = fork();
    if (p == 0) {
        if (!freopen("/dev/null", "w", stderr)) _exit(98);
        f();
        _exit(99);   /* f returned */
    }
    int st = -1;
    waitpid(p, &st, 0);
    return st;
}
static int g_box_fd = -1;
static int box_hook(const char *text, const char *caption, UINT type)
{
    char m[128];
    int n = snprintf(m, sizeof m, "%s|%s|%u", text ? text : "(null)", caption ? caption : "(null)", (unsigned)(type & 0xF));
    if (write(g_box_fd, m, (size_t)n) != n) _exit(97);
    return IDOK;
}
static void do_fatal_exit(void) { fnvnx_set_msgbox_hook(box_hook); FatalAppExitA(0, "fatal text"); }
static void do_debug_break(void) { DebugBreak(); }
TEST(env_fatal_exit_and_debug_break)
{
    int fds[2];
    CHECK(pipe(fds) == 0);
    g_box_fd = fds[1];
    int st = in_child(do_fatal_exit);
    close(fds[1]);
    char m[128] = "";
    ssize_t n = read(fds[0], m, sizeof m - 1);
    close(fds[0]);
    if (n > 0) m[n] = 0;
    CHECK(strncmp(m, "fatal text|", 11) == 0 && strstr(m, "|0") != NULL);   /* the message, an OK box */
    CHECK(WIFEXITED(st) && WEXITSTATUS(st) == 0);                          /* then ExitProcess */
    /* P3: DebugBreak raises EXCEPTION_BREAKPOINT; it never returns as a no-op */
    st = in_child(do_debug_break);
    CHECK(!(WIFEXITED(st) && WEXITSTATUS(st) == 99));
}

/* Tooling cross-check, not a contract test: every bridge row whose native symbol the tree has resolves. */
TEST(env_bridge_rows_resolve)
{
    const char *path = getenv("FNVNX_BRIDGE");
    FILE *f = fopen(path ? path : "/home/user/l5out/manifest/bridge.tsv", "r");
    if (!f) { fprintf(stderr, "  env_bridge_rows_resolve: no bridge.tsv, nothing checked\n"); return; }
    char line[1024];
    int rows = 0, bad = 0;
    while (fgets(line, sizeof line, f)) {
        char *col[8] = { 0 };
        int nc = 0;
        for (char *p = line; nc < 8; nc++) {
            col[nc] = p;
            p += strcspn(p, "\t\r\n");
            if (!*p) { nc++; break; }
            *p++ = 0;
        }
        if (nc < 7 || !strcmp(col[0], "dll") || !strncmp(col[6], "fnvl_", 5)) continue;
        rows++;
        if (!fnvnx_win32_lookup_import(col[0], col[1], FNVNX_IMPORT_BY_NAME) ||
            (*col[2] && !fnvnx_win32_lookup_import(col[0], NULL, (unsigned)atoi(col[2])))) {
            bad++;
            fprintf(stderr, "  bridge: unresolved %s!%s%s%s (%s)\n", col[0], col[1], *col[2] ? " #" : "", col[2], col[3]);
        }
    }
    fclose(f);
    fprintf(stderr, "  env_bridge_rows_resolve: %d rows, %d unresolved\n", rows, bad);
}
