/* services.cpp - shell32, ole32, ws2_32 and steam_api (the registry is in registry.cpp).
 *
 * Winsock is in winsock.cpp.
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
