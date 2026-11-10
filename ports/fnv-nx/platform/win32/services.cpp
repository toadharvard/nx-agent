/* services.cpp - shell32, ole32, ws2_32 and steam_api (the registry is in registry.cpp).
 *
 * Winsock: Havok's remote-debugger socket is the only user; networking is reported as unavailable. The
 * functions are named fnvnx_ws2_* so they cannot collide with the BSD socket API of the target libc; the
 * link step (S2) maps the ws2_32 IAT slots and import thunks to them (docs/RUNTIME.md).
 * Steam / GOG Galaxy (orchestrator policy): no-op success. SteamAPI_Init and IsSteamRunning succeed, every interface
 * accessor returns NULL: the exe null-checks each one (0x006ff741.., 0x00af28f9.., 0x00af2979, 0x00af2dce), so
 * achievements, the overlay and the store queries are skipped and callbacks run as no-ops. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- shell32 */
extern "C" HINSTANCE ShellExecuteA(HWND h, LPCSTR op, LPCSTR file, LPCSTR params, LPCSTR dir, INT show)
{
    W32_UNUSED(h); W32_UNUSED(params); W32_UNUSED(dir); W32_UNUSED(show);
    w32_log("ShellExecuteA(%s, %s) not available", op ? op : "open", file ? file : "");
    return (HINSTANCE)(uintptr_t)31; /* SE_ERR_NOASSOC (<= 32 means failure) */
}

extern "C" HRESULT SHGetFolderPathA(HWND h, int csidl, HANDLE tok, DWORD flags, LPSTR out)
{
    W32_UNUSED(h); W32_UNUSED(tok); W32_UNUSED(flags);
    const char *sub;
    switch (csidl & 0xFF) {
    case CSIDL_PERSONAL: sub = "\\Documents"; break;
    case CSIDL_APPDATA: sub = "\\AppData\\Roaming"; break;
    case CSIDL_LOCAL_APPDATA: sub = "\\AppData\\Local"; break;
    case CSIDL_COMMON_APPDATA: snprintf(out, MAX_PATH, "C:\\ProgramData"); return S_OK;
    default: out[0] = 0; return E_FAIL;
    }
    snprintf(out, MAX_PATH, "%s%s", w32_user_dir(), sub);
    if (csidl & CSIDL_FLAG_CREATE)
        CreateDirectoryA(out, NULL);
    return S_OK;
}

/* ---- ole32 */
struct comclass { GUID clsid; fnvnx_com_factory f; };
static struct comclass g_com[16];
static int g_ncom;

extern "C" void fnvnx_register_com_class(const GUID *clsid, fnvnx_com_factory f)
{
    if (g_ncom < 16) {
        g_com[g_ncom].clsid = *clsid;
        g_com[g_ncom].f = f;
        g_ncom++;
    }
}

extern "C" HRESULT CoInitialize(LPVOID r) { W32_UNUSED(r); return S_OK; }
extern "C" HRESULT CoInitializeEx(LPVOID r, DWORD m) { W32_UNUSED(r); W32_UNUSED(m); return S_OK; }
extern "C" void CoUninitialize(void) {}
extern "C" void CoFreeUnusedLibraries(void) {}
extern "C" LPVOID CoTaskMemAlloc(SIZE_T n) { return malloc(n ? n : 1); }
extern "C" void CoTaskMemFree(LPVOID p) { free(p); }

extern "C" HRESULT CoCreateInstance(const GUID *clsid, void *outer, DWORD ctx, const GUID *iid, LPVOID *out)
{
    W32_UNUSED(outer); W32_UNUSED(ctx);
    if (out)
        *out = NULL;
    for (int i = 0; i < g_ncom; i++)
        if (!memcmp(&g_com[i].clsid, clsid, sizeof(GUID)))
            return g_com[i].f(iid, out);
    return REGDB_E_CLASSNOTREG;
}

extern "C" HRESULT CoCreateGuid(GUID *g)
{
    uint32_t w[4];
    for (int i = 0; i < 4; i++)
        w[i] = os_random32();
    g->Data1 = w[0];
    g->Data2 = (unsigned short)w[1];
    g->Data3 = (unsigned short)(((w[1] >> 16) & 0x0FFF) | 0x4000); /* version 4 */
    memcpy(g->Data4, &w[2], 8);
    g->Data4[0] = (unsigned char)((g->Data4[0] & 0x3F) | 0x80);
    return S_OK;
}

/* ---- ws2_32: offline (orchestrator policy): every network call fails with WSAENETDOWN; byte-order and address
 * formatting helpers are plain functions */
#define WSAENETDOWN_ 10050
extern "C" int fnvnx_ws2_WSAStartup(WORD ver, void *data) { W32_UNUSED(ver); W32_UNUSED(data); FNVNX_NOOP(); return WSAENETDOWN_; }
extern "C" int fnvnx_ws2_WSACleanup(void) { return 0; }
extern "C" int fnvnx_ws2_WSAGetLastError(void) { return WSAENETDOWN_; }
extern "C" UINT_PTR fnvnx_ws2_socket(int af, int type, int proto) { W32_UNUSED(af); W32_UNUSED(type); W32_UNUSED(proto); FNVNX_NOOP(); return ~(UINT_PTR)0; }
extern "C" int fnvnx_ws2_closesocket(UINT_PTR s) { W32_UNUSED(s); return -1; }   /* no socket was ever made */
extern "C" int fnvnx_ws2_fail(void) { return -1; } /* bind, listen, accept, connect, send, recv, setsockopt, ioctlsocket, WSAAsyncSelect */
extern "C" unsigned short fnvnx_ws2_htons(unsigned short v) { return (unsigned short)((v >> 8) | (v << 8)); }
extern "C" unsigned short fnvnx_ws2_ntohs(unsigned short v) { return (unsigned short)((v >> 8) | (v << 8)); }
extern "C" unsigned long fnvnx_ws2_inet_addr(const char *s) { W32_UNUSED(s); return 0xFFFFFFFFul; }
extern "C" char *fnvnx_ws2_inet_ntoa(unsigned long a) { W32_UNUSED(a); static char b[] = "0.0.0.0"; return b; }
extern "C" void *fnvnx_ws2_gethostbyname(const char *n) { W32_UNUSED(n); FNVNX_NOOP(); return NULL; }
extern "C" int fnvnx_ws2_gethostname(char *b, int n) { if (b && n > 0) b[0] = 0; return -1; }
extern "C" int fnvnx_ws2_fdisset(UINT_PTR s, void *set) { W32_UNUSED(s); W32_UNUSED(set); return 0; }   /* __WSAFDIsSet: select never succeeds */

/* ---- NVAPI: no nvapi.dll on the Switch. The exe's NvAPI_* entry points are `jmp [slot]` thunks into the
 * table nvapi_QueryInterface fills at run time (0x009fead6, 0x009feae8, 0x009febd8; crt_symbols.tsv class thunk);
 * with the library absent each returns NVAPI_API_NOT_INITIALIZED (-4) and the renderer takes its non-NVIDIA path
 * (R2 recon2-002, R6 recon6-007). Any arguments, nothing written. */
extern "C" int fnvnx_nvapi_not_initialized(void) { FNVNX_NOOP(); return -4; }

/* ---- steam_api: no-op success (see the top) */
extern "C" bool SteamAPI_Init(void) { FNVNX_NOOP(); return true; }   /* the platform lane: documented no-ops */
extern "C" bool SteamAPI_IsSteamRunning(void) { return true; }
extern "C" void SteamAPI_Shutdown(void) {}
extern "C" void SteamAPI_RunCallbacks(void) {}
extern "C" void SteamAPI_RegisterCallback(void *cb, int id) { W32_UNUSED(cb); W32_UNUSED(id); }
extern "C" void SteamAPI_UnregisterCallback(void *cb) { W32_UNUSED(cb); }
extern "C" void *SteamUser(void) { FNVNX_NOOP(); return NULL; }
extern "C" void *SteamFriends(void) { FNVNX_NOOP(); return NULL; }
extern "C" void *SteamUtils(void) { FNVNX_NOOP(); return NULL; }
extern "C" void *SteamApps(void) { FNVNX_NOOP(); return NULL; }
extern "C" void *SteamUserStats(void) { FNVNX_NOOP(); return NULL; }
