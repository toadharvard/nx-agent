/* exports.cpp - DLL!name -> implementation, for import slots resolved by name.
 *
 * The authoritative name of every IAT slot of FalloutNV.exe is in the exe's own import directory (the decomp
 * names some slots inconsistently, docs/RUNTIME.md). The loader/link step reads that directory and asks
 * fnvnx_win32_lookup_export(dll, name) for each slot; NULL means "not provided" (d3d9 -> the device's module,
 * DirectInput/DirectSound/XInput, d3dx9_38 -> their providers' registered modules). */
#include "w32.h"
#include "../include/fnvnx_mmio.h"

extern "C" {
/* platform/win32/gdi32.cpp */
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
HBITMAP CreateCompatibleBitmap(HDC, int, int);
HBITMAP CreateDIBSection(HDC, const void *, UINT, void **, HANDLE, DWORD);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
int GetDIBits(HDC, HBITMAP, UINT, UINT, void *, void *, UINT);
COLORREF GetPixel(HDC, int, int);
COLORREF SetPixel(HDC, int, int, COLORREF);
BOOL StretchBlt(HDC, int, int, int, int, HDC, int, int, int, int, DWORD);
int SetStretchBltMode(HDC, int);
int GetDeviceCaps(HDC, int);
HDC GetDC(HWND);
int ReleaseDC(HWND, HDC);
HDC BeginPaint(HWND, void *);
BOOL EndPaint(HWND, const void *);
/* platform/win32/gdifont.cpp */
BOOL ExtTextOutA(HDC, int, int, UINT, const RECT *, LPCSTR, UINT, const INT *);
BOOL ExtTextOutW(HDC, int, int, UINT, const RECT *, LPCWSTR, UINT, const INT *);
BOOL GetTextExtentPoint32A(HDC, LPCSTR, int, SIZE *);
BOOL GetTextExtentPoint32W(HDC, LPCWSTR, int, SIZE *);
BOOL GetTextExtentExPointA(HDC, LPCSTR, int, int, LPINT, LPINT, SIZE *);
BOOL GetTextExtentExPointW(HDC, LPCWSTR, int, int, LPINT, LPINT, SIZE *);
DWORD GetCharacterPlacementA(HDC, LPCSTR, int, int, void *, DWORD);
DWORD GetCharacterPlacementW(HDC, LPCWSTR, int, int, void *, DWORD);
UINT GetOutlineTextMetricsA(HDC, UINT, void *);
BOOL GetCharABCWidthsI(HDC, UINT, UINT, unsigned short *, void *);
BOOL GetCharWidthI(HDC, UINT, UINT, unsigned short *, int *);
DWORD GetFontUnicodeRanges(HDC, void *);
int D3DKMTOpenAdapterFromHdc(void *);   /* d3dkmt.cpp */
int D3DKMTEscape(const void *);
HDC CreateDCA(LPCSTR, LPCSTR, LPCSTR, const void *);
int ExtEscape(HDC, int, int, LPCSTR, int, LPSTR);
int GetObjectA(HGDIOBJ, int, void *);
int GetObjectW(HGDIOBJ, int, void *);
HFONT CreateFontIndirectA(const void *);
HFONT CreateFontIndirectW(const void *);
BOOL GetTextMetricsA(HDC, void *);
BOOL GetTextMetricsW(HDC, void *);
int GetTextFaceA(HDC, int, char *);
int GetTextFaceW(HDC, int, void *);
int GetTextCharset(HDC);
UINT GetTextCharsetInfo(HDC, void *, DWORD);
DWORD GetFontLanguageInfo(HDC);
BOOL TranslateCharsetInfo(DWORD *, void *, DWORD);
DWORD GetGlyphOutlineA(HDC, UINT, UINT, void *, DWORD, void *, const void *);
DWORD GetGlyphOutlineW(HDC, UINT, UINT, void *, DWORD, void *, const void *);
COLORREF SetTextColor(HDC, COLORREF);
COLORREF SetBkColor(HDC, COLORREF);
int SetBkMode(HDC, int);
UINT SetTextAlign(HDC, UINT);
int SetMapMode(HDC, int);
BOOL MoveToEx(HDC, int, int, void *);
HGDIOBJ GetCurrentObject(HDC, UINT);
int fnvnx_ws2_WSAStartup(WORD, void *);
int fnvnx_ws2_WSACleanup(void);
int fnvnx_ws2_WSAGetLastError(void);
UINT_PTR fnvnx_ws2_socket(int, int, int);
int fnvnx_ws2_closesocket(UINT_PTR);
void fnvnx_ws2_WSASetLastError(int);
int fnvnx_ws2_bind(UINT_PTR, const void *, int);
int fnvnx_ws2_listen(UINT_PTR, int);
UINT_PTR fnvnx_ws2_accept(UINT_PTR, void *, int *);
int fnvnx_ws2_connect(UINT_PTR, const void *, int);
int fnvnx_ws2_send(UINT_PTR, const char *, int, int);
int fnvnx_ws2_recv(UINT_PTR, char *, int, int);
int fnvnx_ws2_setsockopt(UINT_PTR, int, int, const char *, int);
int fnvnx_ws2_ioctlsocket(UINT_PTR, long, unsigned long *);
int fnvnx_ws2_select(int, void *, void *, void *, const void *);
int fnvnx_ws2_WSAAsyncSelect(UINT_PTR, HWND, UINT, long);
unsigned short fnvnx_ws2_htons(unsigned short);
unsigned short fnvnx_ws2_ntohs(unsigned short);
unsigned long fnvnx_ws2_inet_addr(const char *);
char *fnvnx_ws2_inet_ntoa(unsigned long);
void *fnvnx_ws2_gethostbyname(const char *);
int fnvnx_ws2_gethostname(char *, int);
int fnvnx_ws2_fdisset(UINT_PTR, void *);
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
/* window.cpp, not in the shim headers */
int GetWindowTextA(HWND, LPSTR, int);
int GetClassNameA(HWND, LPSTR, int);
BOOL EnumDisplayDevicesA(LPCSTR, DWORD, void *, DWORD);
/* display.cpp */
LONG ChangeDisplaySettingsA(void *, DWORD);
/* resource.cpp */
BOOL InitCommonControlsEx(const void *);
void *ImageList_LoadImageA(HINSTANCE, LPCSTR, int, int, DWORD, UINT, UINT);
BOOL ImageList_Destroy(void *);
int ImageList_GetImageCount(void *);
BOOL ImageList_GetIconSize(void *, int *, int *);
void *FindResourceA(HMODULE, LPCSTR, LPCSTR);
void *FindResourceExA(HMODULE, LPCSTR, LPCSTR, WORD);
void *LoadResource(HMODULE, void *);
void *LockResource(void *);
DWORD SizeofResource(HMODULE, void *);
BOOL FreeResource(void *);
BOOL EnumResourceNamesA(HMODULE, LPCSTR, ENUMRESNAMEPROCA, LONG_PTR);
/* winmm waveOut (platform/audio/waveout.cpp) */
#define FNVNX_WAVEOUT_EXPORTS(X)                                                                            \
    X(waveOutOpen) X(waveOutClose) X(waveOutPrepareHeader) X(waveOutUnprepareHeader) X(waveOutWrite)         \
    X(waveOutPause) X(waveOutRestart) X(waveOutReset) X(waveOutSetVolume) X(waveOutGetVolume)                \
    X(waveOutGetPosition) X(waveOutGetNumDevs) X(waveOutGetDevCapsA) X(waveOutGetErrorTextA)
#define FNVNX_WAVEOUT_DECL(f) void f(void);
FNVNX_WAVEOUT_EXPORTS(FNVNX_WAVEOUT_DECL)
#undef FNVNX_WAVEOUT_DECL
}

/* kernel32 (encptr.cpp, fls.cpp) */
extern "C" {
LPVOID EncodePointer(LPVOID);
LPVOID DecodePointer(LPVOID);
DWORD FlsAlloc(void *);   /* its parameter is the game's callback: one guest word */
BOOL FlsFree(DWORD);
LPVOID FlsGetValue(DWORD);
BOOL FlsSetValue(DWORD, LPVOID);
}

/* user32 window functions without a declaration in the shim (platform/win32/window.cpp) */
extern "C" {
HWND GetTopWindow(HWND);
BOOL IsWindowVisible(HWND);
DWORD GetWindowThreadProcessId(HWND, LPDWORD);
BOOL UnregisterClassA(LPCSTR, HINSTANCE);
HCURSOR SetCursor(HCURSOR);
BOOL SetDoubleClickTime(UINT);
DWORD SetClassLongA(HWND, int, LONG);
HWND GetLastActivePopup(HWND);                                 /* window.cpp */
HANDLE GetProcessWindowStation(void);                           /* winsta.cpp */
BOOL GetUserObjectInformationA(HANDLE, int, void *, DWORD, LPDWORD);
}

namespace {
struct exp { const char *dll, *name; void *fn; };
#define E(dll, f) { dll, #f, (void *)&f }
#define K(f) E("kernel32", f)
#define U(f) E("user32", f)
/* a DLL whose exports another part of the platform serves (registered module, hook): it still is a module */
#define D(dll) { dll, NULL, NULL }
const exp g_exports[] = {
    /* kernel32 */
    K(CloseHandle), K(CompareFileTime), K(DisableThreadLibraryCalls), K(FatalAppExitA), K(CopyFileA), K(CreateDirectoryA), K(CreateEventA), K(CreateFileA),
    K(CreateMutexA), K(CreateSemaphoreA), K(CreateThread), K(DebugBreak), K(DeleteCriticalSection),
    K(DeleteFileA), K(EnterCriticalSection), K(ExitProcess), K(ExitThread), K(FileTimeToLocalFileTime),
    K(FileTimeToSystemTime), K(FindClose), K(FindFirstFileA), K(FindNextFileA), K(FlushFileBuffers),
    K(FormatMessageA), K(FreeLibrary), K(GetCommandLineA), K(GetCommandLineW), K(GetComputerNameA),
    K(GetCurrentDirectoryA), K(GetCurrentProcess), K(GetCurrentProcessId), K(GetCurrentThread),
    K(GetCurrentThreadId), K(GetEnvironmentVariableA), K(GetExitCodeThread), K(GetFileAttributesA),
    K(GetFileSize), K(GetFileSizeEx), K(GetFileTime), K(GetFullPathNameA), K(GetLastError), K(GetLocalTime),
    K(GetModuleFileNameA), K(GetModuleHandleA), K(GetPrivateProfileIntA), K(GetPrivateProfileSectionA),
    K(GetPrivateProfileSectionNamesA), K(GetPrivateProfileStringA), K(GetProcAddress), K(GetProcessHeap),
    K(GetSystemInfo), K(GetSystemTime), K(GetSystemTimeAsFileTime), K(GetThreadPriority), K(GetTickCount),
    K(GlobalMemoryStatus), K(GlobalMemoryStatusEx), K(HeapAlloc), K(HeapCreate), K(HeapDestroy), K(HeapFree),
    K(HeapReAlloc), K(HeapSize), K(InitializeCriticalSection), K(InitializeCriticalSectionAndSpinCount),
    K(InterlockedCompareExchange), K(InterlockedDecrement), K(InterlockedExchange), K(InterlockedExchangeAdd),
    K(InterlockedIncrement), K(IsBadCodePtr), K(IsBadReadPtr), K(IsBadWritePtr), K(IsDebuggerPresent),
    K(LeaveCriticalSection), K(LoadLibraryA), K(LocalAlloc), K(LocalFree), K(MoveFileA), K(OutputDebugStringA),
    K(QueryPerformanceCounter), K(QueryPerformanceFrequency), K(RaiseException), K(ReadFile), K(ReleaseMutex),
    K(ReleaseSemaphore), K(RemoveDirectoryA), K(ResetEvent), K(ResumeThread),
    K(SetCurrentDirectoryA), K(SetEndOfFile), K(SetEvent), K(SetFilePointer), K(SetFilePointerEx),
    K(SetLastError), K(SetThreadAffinityMask), K(SetThreadIdealProcessor), K(SetThreadPriority),
    K(SetThreadPriorityBoost), K(Sleep), K(SleepEx), K(SuspendThread), K(SwitchToThread),
    K(SystemTimeToFileTime), K(SystemTimeToTzSpecificLocalTime), K(TlsAlloc), K(TlsFree), K(TlsGetValue),
    K(TlsSetValue), K(EncodePointer), K(DecodePointer), K(FlsAlloc), K(FlsFree), K(FlsGetValue), K(FlsSetValue), K(TryEnterCriticalSection), K(VirtualAlloc), K(VirtualFree), K(VirtualProtect), K(VirtualQuery), K(GetWriteWatch), K(ResetWriteWatch),
    K(CreateFileMappingA), K(MapViewOfFile), K(UnmapViewOfFile), K(FlushViewOfFile),
    K(WaitForMultipleObjects), K(WaitForSingleObject), K(WriteFile), K(WritePrivateProfileSectionA),
    K(WritePrivateProfileStringA), K(lstrcatA), K(lstrcmpiA), K(lstrcpyA), K(lstrlenA),
    { "kernel32", "lstrcpy", (void *)&lstrcpyA },
    /* kernel32, the exe's own CRT only (crt_kernel32.cpp) */
    K(CompareStringA), K(CompareStringW), K(CreateFileW), K(EnumSystemLocalesA), K(FreeEnvironmentStringsA),
    K(FreeEnvironmentStringsW), K(GetACP), K(GetCPInfo), K(GetConsoleCP), K(GetConsoleMode),
    K(GetConsoleOutputCP), K(GetDriveTypeA), K(GetEnvironmentStrings), K(GetEnvironmentStringsW),
    K(GetFileType), K(GetLocaleInfoA), K(GetLocaleInfoW), K(GetModuleHandleW), K(GetOEMCP), K(GetStartupInfoA),
    K(GetStdHandle), K(GetStringTypeA), K(GetStringTypeW), K(GetTimeZoneInformation), K(GetUserDefaultLCID),
    K(IsValidCodePage), K(IsValidLocale), K(LCMapStringA), K(LCMapStringW), K(MultiByteToWideChar),
    K(SetEnvironmentVariableA), K(SetHandleCount), K(SetStdHandle), K(SetUnhandledExceptionFilter),
    K(TerminateProcess), K(UnhandledExceptionFilter), K(WideCharToMultiByte), K(WriteConsoleA),
    K(WriteConsoleW), K(ExpandEnvironmentStringsA), K(GetSystemDirectoryA), K(GetVersion), K(GetVersionExA),
    K(GetWindowsDirectoryA), K(IsDBCSLeadByte), K(IsProcessorFeaturePresent), K(SetConsoleCtrlHandler),
    K(SetErrorMode),
    K(DeleteFileW), K(MoveFileW), K(GetTempPathA), K(GetTempFileNameA), K(GetTempFileNameW),
    K(GetDateFormatA), K(GetTimeFormatA),
    /* user32 */
    U(AdjustWindowRect), U(AdjustWindowRectEx), U(CallNextHookEx), U(ClientToScreen), U(ClipCursor), U(GetClipCursor),
    U(CreateWindowExA), U(DefWindowProcA), U(DestroyWindow), U(DispatchMessageA), U(EnumChildWindows),
    U(FindWindowA), U(GetActiveWindow), U(GetAsyncKeyState), U(GetClientRect), U(GetCursorPos),
    U(GetDoubleClickTime), U(GetFocus), U(GetForegroundWindow), U(GetKeyState), U(GetSystemMetrics),
    U(GetWindow), U(GetWindowLongA), U(GetWindowRect), U(IsIconic), U(IsWindow), U(LoadCursorA), U(LoadIconA),
    U(MessageBoxA), U(PeekMessageA), U(PostMessageA), U(PostQuitMessage), U(RegisterClassA), U(ReleaseCapture),
    U(ScreenToClient), U(SendInput), U(SendMessageA), U(SetCapture), U(GetCapture), U(SetCursorPos), U(SetFocus),
    U(SetForegroundWindow), U(SetWindowLongA), U(SetWindowPos), U(SetWindowTextA), U(SetWindowsHookExA),
    U(ShowCursor), U(ShowWindow), U(SwapMouseButton), U(TranslateMessage), U(UnhookWindowsHookEx),
    U(GetTopWindow), U(IsWindowVisible), U(GetWindowThreadProcessId), U(UnregisterClassA), U(SetClassLongA),
    U(UpdateWindow), U(GetWindowTextA), U(GetClassNameA), U(EnumDisplayDevicesA), U(ChangeDisplaySettingsA), U(GetClassLongA),
    U(SetCursor), U(SetDoubleClickTime), U(GetLastActivePopup), U(GetProcessWindowStation), U(GetUserObjectInformationA),
    E("comctl32", InitCommonControlsEx), E("comctl32", ImageList_LoadImageA), E("comctl32", ImageList_Destroy),
    E("comctl32", ImageList_GetImageCount), E("comctl32", ImageList_GetIconSize),
    K(FindResourceA), K(FindResourceExA), K(FindResourceW), K(FindResourceExW), K(LoadResource), K(LockResource), K(SizeofResource), K(FreeResource),
    K(EnumResourceNamesA),
    E("gdi32", GetStockObject), E("gdi32", DeleteObject), E("gdi32", CreateCompatibleDC), E("gdi32", DeleteDC),
    E("gdi32", CreateCompatibleBitmap), E("gdi32", CreateDIBSection), E("gdi32", SelectObject), E("gdi32", GetDIBits),
    E("gdi32", GetPixel), E("gdi32", SetPixel), E("gdi32", StretchBlt), E("gdi32", SetStretchBltMode),
    E("gdi32", GetObjectA), E("gdi32", GetObjectW), E("gdi32", CreateFontIndirectA), E("gdi32", CreateFontIndirectW),
    E("gdi32", GetTextMetricsA), E("gdi32", GetTextMetricsW), E("gdi32", GetTextFaceA), E("gdi32", GetTextFaceW),
    E("gdi32", GetTextCharset), E("gdi32", GetTextCharsetInfo), E("gdi32", GetFontLanguageInfo),
    E("gdi32", TranslateCharsetInfo), E("gdi32", GetGlyphOutlineA), E("gdi32", GetGlyphOutlineW),
    E("gdi32", SetTextColor), E("gdi32", SetBkColor), E("gdi32", SetBkMode), E("gdi32", SetTextAlign),
    E("gdi32", SetMapMode), E("gdi32", MoveToEx), E("gdi32", GetCurrentObject),
    E("gdi32", ExtTextOutA), E("gdi32", ExtTextOutW), E("gdi32", GetTextExtentPoint32A), E("gdi32", GetTextExtentPoint32W),
    E("gdi32", GetTextExtentExPointA), E("gdi32", GetTextExtentExPointW), E("gdi32", GetCharacterPlacementA), E("gdi32", GetCharacterPlacementW),
    E("gdi32", GetOutlineTextMetricsA), E("gdi32", GetCharABCWidthsI), E("gdi32", GetCharWidthI), E("gdi32", GetFontUnicodeRanges), E("gdi32", D3DKMTOpenAdapterFromHdc), E("gdi32", D3DKMTEscape), E("gdi32", CreateDCA), E("gdi32", ExtEscape),
    E("gdi32", GetDeviceCaps), E("user32", GetDC), E("user32", ReleaseDC), E("user32", BeginPaint), E("user32", EndPaint),
    E("advapi32", RegCloseKey), E("advapi32", RegCreateKeyExA), E("advapi32", RegOpenKeyExA), E("advapi32", RegOpenKeyA),
    E("advapi32", RegQueryValueExA), E("advapi32", RegSetValueExA), E("advapi32", GetUserNameA), E("advapi32", SystemFunction036),
    E("shell32", CommandLineToArgvW), E("shell32", ShellExecuteA), E("shell32", SHGetFolderPathA),
    E("ole32", CoCreateGuid), E("ole32", CoCreateInstance), E("ole32", CoFreeUnusedLibraries),
    E("ole32", CoInitialize), E("ole32", CoInitializeEx), E("ole32", CoTaskMemAlloc), E("ole32", CoTaskMemFree),
    E("ole32", CoUninitialize),
    E("winmm", timeGetTime), E("winmm", timeBeginPeriod), E("winmm", timeEndPeriod),
    E("winmm", mmioOpenA), E("winmm", mmioClose), E("winmm", mmioRead), E("winmm", mmioWrite), E("winmm", mmioSeek),
    E("winmm", mmioGetInfo), E("winmm", mmioSetInfo), E("winmm", mmioAdvance), E("winmm", mmioFlush),
    E("winmm", mmioDescend), E("winmm", mmioAscend), E("winmm", mmioCreateChunk), E("winmm", mmioSendMessage),
    E("winmm", mmioStringToFOURCCA),
    E("winmm", waveOutOpen), E("winmm", waveOutClose), E("winmm", waveOutPrepareHeader),
    E("winmm", waveOutUnprepareHeader), E("winmm", waveOutWrite), E("winmm", waveOutPause),
    E("winmm", waveOutRestart), E("winmm", waveOutReset), E("winmm", waveOutSetVolume),
    E("winmm", waveOutGetVolume), E("winmm", waveOutGetPosition), E("winmm", waveOutGetNumDevs),
    E("winmm", waveOutGetDevCapsA), E("winmm", waveOutGetErrorTextA),
    { "ws2_32", "WSAStartup", (void *)&fnvnx_ws2_WSAStartup }, { "ws2_32", "WSACleanup", (void *)&fnvnx_ws2_WSACleanup },
    { "ws2_32", "WSAGetLastError", (void *)&fnvnx_ws2_WSAGetLastError }, { "ws2_32", "socket", (void *)&fnvnx_ws2_socket },
    { "ws2_32", "closesocket", (void *)&fnvnx_ws2_closesocket }, { "ws2_32", "htons", (void *)&fnvnx_ws2_htons },
    { "ws2_32", "ntohs", (void *)&fnvnx_ws2_ntohs }, { "ws2_32", "inet_addr", (void *)&fnvnx_ws2_inet_addr },
    { "ws2_32", "inet_ntoa", (void *)&fnvnx_ws2_inet_ntoa }, { "ws2_32", "gethostbyname", (void *)&fnvnx_ws2_gethostbyname },
    { "ws2_32", "gethostname", (void *)&fnvnx_ws2_gethostname }, { "ws2_32", "bind", (void *)&fnvnx_ws2_bind },
    { "ws2_32", "listen", (void *)&fnvnx_ws2_listen }, { "ws2_32", "accept", (void *)&fnvnx_ws2_accept },
    { "ws2_32", "connect", (void *)&fnvnx_ws2_connect }, { "ws2_32", "send", (void *)&fnvnx_ws2_send },
    { "ws2_32", "recv", (void *)&fnvnx_ws2_recv }, { "ws2_32", "setsockopt", (void *)&fnvnx_ws2_setsockopt },
    { "ws2_32", "ioctlsocket", (void *)&fnvnx_ws2_ioctlsocket }, { "ws2_32", "WSAAsyncSelect", (void *)&fnvnx_ws2_WSAAsyncSelect },
    { "ws2_32", "select", (void *)&fnvnx_ws2_select }, { "ws2_32", "WSASetLastError", (void *)&fnvnx_ws2_WSASetLastError }, { "ws2_32", "__WSAFDIsSet", (void *)&fnvnx_ws2_fdisset },
    E("steam_api", SteamAPI_Init), E("steam_api", SteamAPI_IsSteamRunning), E("steam_api", SteamAPI_Shutdown),
    E("steam_api", SteamAPI_RunCallbacks), E("steam_api", SteamAPI_RegisterCallback),
    E("steam_api", SteamAPI_UnregisterCallback), E("steam_api", SteamUser), E("steam_api", SteamFriends),
    E("steam_api", SteamUtils), E("steam_api", SteamApps), E("steam_api", SteamUserStats),
    D("d3d9"), D("dinput8"), D("dsound"), D("xinput1_3"), D("msvcrt"),
    /* binkw32.dll and libvorbisfile.dll are not provided here: they are loaded as translated PE images */
};
#undef E
#undef K
#undef U
#undef D

/* "KERNEL32.dll" == "kernel32" */
int dll_eq(const char *a, const char *b)
{
    size_t la = strlen(a), lb = strlen(b);
    if (la > 4 && !w32_stricmp(a + la - 4, ".dll"))
        la -= 4;
    if (lb > 4 && !w32_stricmp(b + lb - 4, ".dll"))
        lb -= 4;
    return la == lb && !w32_strnicmp(a, b, la);
}
} // namespace

extern "C" void *fnvnx_registered_module_export(const char *dll, const char *proc);   // module.cpp
extern "C" void *fnvnx_msvcrt_lookup(const char *name);                                // crt/msvcrt_exports.cpp

/* DLLs the exe imports under another module's name: GalaxyWrp.dll (GOG's build) wraps the Steam API with the
 * same export names; WSOCK32 (Winsock 1.1) forwards to ws2_32. {module the exe names, rows' dll} */
static const char *const g_alias[][2] = { { "galaxywrp", "steam_api" }, { "wsock32", "ws2_32" } };

static const char *dll_alias(const char *dll)
{
    if (dll)
        for (const auto &a : g_alias)
            if (dll_eq(dll, a[0]))
                return a[1];
    return dll;
}

/* The system modules, derived from the rows above: every distinct DLL name of the export table AND every alias
 * name that resolves to one of them (GetModuleHandleA("ws2_32"), ("wsock32"), ("steam_api"), ("galaxywrp") all name
 * a module: the alias is the same implementation under a second name). The i-th one, NULL past the last.
 * module.cpp turns each into a loaded, pinned module, so a DLL with exports cannot be missing there. */
extern "C" const char *fnvnx_win32_system_module(unsigned i)
{
    const char *seen[64];
    unsigned n = 0;
    for (const exp &e : g_exports) {
        const char *cand[1 + sizeof g_alias / sizeof g_alias[0]];
        unsigned nc = 0;
        cand[nc++] = e.dll;
        for (const auto &a : g_alias)
            if (!strcmp(e.dll, a[1]))
                cand[nc++] = a[0];
        for (unsigned c = 0; c < nc; c++) {
            bool dup = false;
            for (unsigned k = 0; k < n && !dup; k++)
                dup = !strcmp(seen[k], cand[c]);
            if (dup)
                continue;
            if (n == i)
                return cand[c];
            seen[n++] = cand[c];
        }
    }
    return NULL;
}

extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name)
{
    if (!name)
        return NULL;
    dll = dll_alias(dll);
    for (const exp &e : g_exports)
        if (e.fn && e.name && !strcmp(e.name, name) && (!dll || dll_eq(dll, e.dll)))
            return e.fn;
    if (dll && dll_eq(dll, "msvcrt"))
        return fnvnx_msvcrt_lookup(name);
    /* not an S4 export: modules registered at run time (S7's dsound/dinput8/xinput) */
    return dll ? fnvnx_registered_module_export(dll, name) : NULL;
}

/* Imports by ordinal: the exports' ordinals in the system DLLs the exe binds that way (WSOCK32 21 slots,
 * XINPUT1_3 #2/#3, DSOUND #11), resolved by name. Winsock 1.1's numbering (wsock32: 10 inet_addr, 11 inet_ntoa,
 * 12 ioctlsocket; ws2_32 orders those three differently and is not imported by ordinal). */
namespace {
struct ord { const char *dll; unsigned ordinal; const char *name; };
const ord g_ordinals[] = {
    {"wsock32", 1, "accept"}, {"wsock32", 2, "bind"}, {"wsock32", 3, "closesocket"}, {"wsock32", 4, "connect"},
    {"wsock32", 5, "getpeername"}, {"wsock32", 6, "getsockname"}, {"wsock32", 7, "getsockopt"},
    {"wsock32", 8, "htonl"}, {"wsock32", 9, "htons"}, {"wsock32", 10, "inet_addr"}, {"wsock32", 11, "inet_ntoa"},
    {"wsock32", 12, "ioctlsocket"}, {"wsock32", 13, "listen"}, {"wsock32", 14, "ntohl"}, {"wsock32", 15, "ntohs"},
    {"wsock32", 16, "recv"}, {"wsock32", 17, "recvfrom"}, {"wsock32", 18, "select"}, {"wsock32", 19, "send"},
    {"wsock32", 20, "sendto"}, {"wsock32", 21, "setsockopt"}, {"wsock32", 22, "shutdown"}, {"wsock32", 23, "socket"},
    {"wsock32", 51, "gethostbyaddr"}, {"wsock32", 52, "gethostbyname"}, {"wsock32", 57, "gethostname"},
    {"wsock32", 101, "WSAAsyncSelect"}, {"wsock32", 111, "WSAGetLastError"}, {"wsock32", 112, "WSASetLastError"},
    {"wsock32", 115, "WSAStartup"}, {"wsock32", 116, "WSACleanup"}, {"wsock32", 151, "__WSAFDIsSet"},
    {"xinput1_3", 2, "XInputGetState"}, {"xinput1_3", 3, "XInputSetState"}, {"xinput1_3", 4, "XInputGetCapabilities"},
    {"xinput1_3", 5, "XInputEnable"},
    {"dsound", 1, "DirectSoundCreate"}, {"dsound", 2, "DirectSoundEnumerateA"}, {"dsound", 11, "DirectSoundCreate8"},
};
} // namespace

extern "C" void *fnvnx_win32_lookup_import(const char *dll, const char *name, unsigned ordinal)
{
    if (ordinal == FNVNX_IMPORT_BY_NAME)
        return fnvnx_win32_lookup_export(dll, name);
    if (!dll)
        return NULL;
    for (const ord &o : g_ordinals)
        if (o.ordinal == ordinal && dll_eq(dll, o.dll))
            return fnvnx_win32_lookup_export(dll, o.name);
    return NULL;
}
