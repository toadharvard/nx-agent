/* exports.cpp - DLL!name -> implementation, for import slots resolved by name.
 *
 * The authoritative name of every IAT slot of FalloutNV.exe is in the exe's own import directory (the decomp
 * names some slots inconsistently, docs/RUNTIME.md). The loader/link step reads that directory and asks
 * fnvnx_win32_lookup_export(dll, name) for each slot; NULL means "not provided" (d3d9 -> the device's module,
 * DirectInput/DirectSound/XInput/vorbisfile -> S7's registered modules). D3DX (render/nine/d3dx) is listed here by
 * weak reference. */
#include "w32.h"
#include "fnvnx_mmio.h"

extern "C" {
int fnvnx_ws2_WSAStartup(WORD, void *);
int fnvnx_ws2_WSACleanup(void);
int fnvnx_ws2_WSAGetLastError(void);
UINT_PTR fnvnx_ws2_socket(int, int, int);
int fnvnx_ws2_closesocket(UINT_PTR);
int fnvnx_ws2_fail(void);
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
/* bink.cpp */
void *BinkOpen(const char *, unsigned);
void BinkClose(void *);
int BinkDoFrame(void *);
void BinkNextFrame(void *);
int BinkWait(void *);
int BinkPause(void *, int);
int BinkCopyToBufferRect(void *, void *, int, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned,
                         unsigned);
void *BinkOpenDirectSound(unsigned long);
int BinkSetSoundSystem(void *, unsigned long);
/* window.cpp, not in the shim headers */
int GetWindowTextA(HWND, LPSTR, int);
int GetClassNameA(HWND, LPSTR, int);
BOOL EnumDisplayDevicesA(LPCSTR, DWORD, void *, DWORD);
BOOL InitCommonControlsEx(const void *);
void *ImageList_LoadImageA(HINSTANCE, LPCSTR, int, int, DWORD, UINT, UINT);
BOOL ImageList_Destroy(void *);
}

/* d3dx9_38.dll: render/nine/d3dx, linked into both guests (toolchain/game/build.sh D3D9=null, render/nine/game
 * D3D9=external). Weak references: a build without it (the host tests) links, and those slots stay NULL. */
#define FNVNX_D3DX_EXPORTS(X)                                                                                 \
    X(D3DXAssembleShader) X(D3DXAssembleShaderFromFileA) X(D3DXCompileShader) X(D3DXCompileShaderFromFileA)  \
    X(D3DXCreateBuffer) X(D3DXCreateCubeTextureFromFileInMemory) X(D3DXCreateCubeTextureFromFileInMemoryEx)   \
    X(D3DXCreateTextureFromFileInMemory) X(D3DXCreateTextureFromFileInMemoryEx)                              \
    X(D3DXCreateVolumeTextureFromFileInMemory) X(D3DXCreateVolumeTextureFromFileInMemoryEx)                  \
    X(D3DXFindShaderComment) X(D3DXGetImageInfoFromFileInMemory) X(D3DXGetPixelShaderProfile)                \
    X(D3DXGetShaderConstantTable) X(D3DXGetShaderConstantTableEx) X(D3DXGetShaderSize) X(D3DXGetShaderVersion) \
    X(D3DXGetVertexShaderProfile) X(D3DXLoadSurfaceFromMemory) X(D3DXLoadSurfaceFromSurface) X(D3DXMatrixInverse) \
    X(D3DXMatrixLookAtLH) X(D3DXMatrixMultiply) X(D3DXMatrixMultiplyTranspose) X(D3DXMatrixOrthoOffCenterLH)  \
    X(D3DXMatrixPerspectiveFovLH) X(D3DXMatrixRotationYawPitchRoll) X(D3DXMatrixTranspose) X(D3DXPlaneNormalize) \
    X(D3DXPlaneTransform) X(D3DXVec3Normalize) X(D3DXVec3TransformCoord) X(D3DXVec3TransformNormal)           \
    X(D3DXVec4Transform)
extern "C" {
#define FNVNX_D3DX_DECL(f) void f(void) __attribute__((weak));
FNVNX_D3DX_EXPORTS(FNVNX_D3DX_DECL)
#undef FNVNX_D3DX_DECL
}

/* D3DXSaveTextureToFileA: the exe imports it for the developer screenshot path; render/nine/d3dx does not write
 * image files. A weak fallback that fails (D3DERR_INVALIDCALL) as for an unwritable file; d3dx may override it. */
extern "C" __attribute__((weak)) HRESULT D3DXSaveTextureToFileA(const char *, int, void *, const void *)
{
    return (HRESULT)0x8876086Cu;
}

namespace {
struct exp { const char *dll, *name; void *fn; };
#define E(dll, f) { dll, #f, (void *)&f }
#define K(f) E("kernel32", f)
#define U(f) E("user32", f)
const exp g_exports[] = {
    /* kernel32 */
    K(CloseHandle), K(CompareFileTime), K(CopyFileA), K(CreateDirectoryA), K(CreateEventA), K(CreateFileA),
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
    K(ReleaseSemaphore), K(RemoveDirectoryA), K(ResetEvent), K(ResumeThread), K(RtlUnwind),
    K(SetCurrentDirectoryA), K(SetEndOfFile), K(SetEvent), K(SetFilePointer), K(SetFilePointerEx),
    K(SetLastError), K(SetThreadAffinityMask), K(SetThreadIdealProcessor), K(SetThreadPriority),
    K(SetThreadPriorityBoost), K(Sleep), K(SleepEx), K(SuspendThread), K(SwitchToThread),
    K(SystemTimeToFileTime), K(SystemTimeToTzSpecificLocalTime), K(TlsAlloc), K(TlsFree), K(TlsGetValue),
    K(TlsSetValue), K(TryEnterCriticalSection), K(VirtualAlloc), K(VirtualFree), K(VirtualQuery),
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
    K(WriteConsoleW),
    /* user32 */
    U(AdjustWindowRect), U(AdjustWindowRectEx), U(CallNextHookEx), U(ClientToScreen), U(ClipCursor),
    U(CreateWindowExA), U(DefWindowProcA), U(DestroyWindow), U(DispatchMessageA), U(EnumChildWindows),
    U(FindWindowA), U(GetActiveWindow), U(GetAsyncKeyState), U(GetClientRect), U(GetCursorPos),
    U(GetDoubleClickTime), U(GetFocus), U(GetForegroundWindow), U(GetKeyState), U(GetSystemMetrics),
    U(GetWindow), U(GetWindowLongA), U(GetWindowRect), U(IsIconic), U(IsWindow), U(LoadCursorA), U(LoadIconA),
    U(MessageBoxA), U(PeekMessageA), U(PostMessageA), U(PostQuitMessage), U(RegisterClassA), U(ReleaseCapture),
    U(ScreenToClient), U(SendInput), U(SendMessageA), U(SetCapture), U(SetCursorPos), U(SetFocus),
    U(SetForegroundWindow), U(SetWindowLongA), U(SetWindowPos), U(SetWindowTextA), U(SetWindowsHookExA),
    U(ShowCursor), U(ShowWindow), U(SwapMouseButton), U(TranslateMessage), U(UnhookWindowsHookEx),
    U(UpdateWindow), U(GetWindowTextA), U(GetClassNameA), U(EnumDisplayDevicesA), U(GetClassLongA),
    E("comctl32", InitCommonControlsEx), E("comctl32", ImageList_LoadImageA), E("comctl32", ImageList_Destroy),
    E("gdi32", GetStockObject), E("gdi32", DeleteObject),
    E("advapi32", RegCloseKey), E("advapi32", RegCreateKeyExA), E("advapi32", RegOpenKeyExA),
    E("advapi32", RegQueryValueExA), E("advapi32", RegSetValueExA), E("advapi32", GetUserNameA),
    E("shell32", CommandLineToArgvW), E("shell32", ShellExecuteA), E("shell32", SHGetFolderPathA),
    E("ole32", CoCreateGuid), E("ole32", CoCreateInstance), E("ole32", CoFreeUnusedLibraries),
    E("ole32", CoInitialize), E("ole32", CoInitializeEx), E("ole32", CoTaskMemAlloc), E("ole32", CoTaskMemFree),
    E("ole32", CoUninitialize),
    E("winmm", timeGetTime), E("winmm", timeBeginPeriod), E("winmm", timeEndPeriod),
    E("winmm", mmioOpenA), E("winmm", mmioClose), E("winmm", mmioRead), E("winmm", mmioWrite), E("winmm", mmioSeek),
    E("winmm", mmioGetInfo), E("winmm", mmioSetInfo), E("winmm", mmioAdvance), E("winmm", mmioFlush),
    E("winmm", mmioDescend), E("winmm", mmioAscend), E("winmm", mmioCreateChunk), E("winmm", mmioSendMessage),
    E("winmm", mmioStringToFOURCCA),
    { "ws2_32", "WSAStartup", (void *)&fnvnx_ws2_WSAStartup }, { "ws2_32", "WSACleanup", (void *)&fnvnx_ws2_WSACleanup },
    { "ws2_32", "WSAGetLastError", (void *)&fnvnx_ws2_WSAGetLastError }, { "ws2_32", "socket", (void *)&fnvnx_ws2_socket },
    { "ws2_32", "closesocket", (void *)&fnvnx_ws2_closesocket }, { "ws2_32", "htons", (void *)&fnvnx_ws2_htons },
    { "ws2_32", "ntohs", (void *)&fnvnx_ws2_ntohs }, { "ws2_32", "inet_addr", (void *)&fnvnx_ws2_inet_addr },
    { "ws2_32", "inet_ntoa", (void *)&fnvnx_ws2_inet_ntoa }, { "ws2_32", "gethostbyname", (void *)&fnvnx_ws2_gethostbyname },
    { "ws2_32", "gethostname", (void *)&fnvnx_ws2_gethostname }, { "ws2_32", "bind", (void *)&fnvnx_ws2_fail },
    { "ws2_32", "listen", (void *)&fnvnx_ws2_fail }, { "ws2_32", "accept", (void *)&fnvnx_ws2_fail },
    { "ws2_32", "connect", (void *)&fnvnx_ws2_fail }, { "ws2_32", "send", (void *)&fnvnx_ws2_fail },
    { "ws2_32", "recv", (void *)&fnvnx_ws2_fail }, { "ws2_32", "setsockopt", (void *)&fnvnx_ws2_fail },
    { "ws2_32", "ioctlsocket", (void *)&fnvnx_ws2_fail }, { "ws2_32", "WSAAsyncSelect", (void *)&fnvnx_ws2_fail },
    { "ws2_32", "select", (void *)&fnvnx_ws2_fail }, { "ws2_32", "__WSAFDIsSet", (void *)&fnvnx_ws2_fdisset },
    E("steam_api", SteamAPI_Init), E("steam_api", SteamAPI_IsSteamRunning), E("steam_api", SteamAPI_Shutdown),
    E("steam_api", SteamAPI_RunCallbacks), E("steam_api", SteamAPI_RegisterCallback),
    E("steam_api", SteamAPI_UnregisterCallback), E("steam_api", SteamUser), E("steam_api", SteamFriends),
    E("steam_api", SteamUtils), E("steam_api", SteamApps), E("steam_api", SteamUserStats),
#define FNVNX_D3DX_ENTRY(f) E("d3dx9_38", f),
    FNVNX_D3DX_EXPORTS(FNVNX_D3DX_ENTRY)
#undef FNVNX_D3DX_ENTRY
    E("d3dx9_38", D3DXSaveTextureToFileA),
    /* binkw32: the IAT's stdcall decorations, and the plain names for GetProcAddress */
#define B(f, bytes) { "binkw32", "_" #f "@" #bytes, (void *)&f }, E("binkw32", f)
    B(BinkOpen, 8), B(BinkClose, 4), B(BinkDoFrame, 4), B(BinkNextFrame, 4), B(BinkWait, 4), B(BinkPause, 8),
    B(BinkCopyToBufferRect, 44), B(BinkOpenDirectSound, 4), B(BinkSetSoundSystem, 8),
#undef B
};
#undef E
#undef K
#undef U

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

/* DLLs the exe imports under another module's name: GalaxyWrp.dll (GOG's build) wraps the Steam API with the
 * same export names; WSOCK32 (Winsock 1.1) forwards to ws2_32 */
static const char *dll_alias(const char *dll)
{
    if (dll && dll_eq(dll, "GalaxyWrp"))
        return "steam_api";
    if (dll && dll_eq(dll, "wsock32"))
        return "ws2_32";
    return dll;
}

extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name)
{
    if (!name)
        return NULL;
    dll = dll_alias(dll);
    for (const exp &e : g_exports)
        if (e.fn && !strcmp(e.name, name) && (!dll || dll_eq(dll, e.dll)))
            return e.fn;
    /* not an S4 export: modules registered at run time (S7's dsound/dinput8/xinput/vorbisfile) */
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
