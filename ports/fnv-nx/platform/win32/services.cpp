/* services.cpp - shell32, ole32, ws2_32 and steam_api (the registry is in registry.cpp).
 *
 * Winsock is in winsock.cpp.
 * Steam / GOG Galaxy: the Steamworks API as it is without a running client (ENV_TRUTH M6), at the end. */
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

/* ---- steam_api / galaxywrp.dll: the public Steamworks contract in the state "Steam client not running"
 * (ENV_TRUTH M6). SteamAPI_Init fails every time (no client to connect to), SteamAPI_IsSteamRunning is false, every
 * interface accessor is NULL (no ISteam* object exists without a client), no callback is ever delivered (callbacks
 * come from the client), and the callback registration list is the real one: RegisterCallback adds an
 * (object, id) pair, UnregisterCallback removes all pairs of the object. */
static os_lock_t g_steam_lock;
static struct steam_reg { void *cb; int id; } *g_steam_regs;
static int g_steam_nregs, g_steam_cap;

extern "C" bool SteamAPI_Init(void) { return false; }
extern "C" bool SteamAPI_IsSteamRunning(void) { return false; }
/* nothing was initialised, so there is nothing to release; the registrations belong to their objects */
extern "C" void SteamAPI_Shutdown(void) {}
/* with no client no callback exists to dispatch; the registered objects are not touched */
extern "C" void SteamAPI_RunCallbacks(void) {}

extern "C" void SteamAPI_RegisterCallback(void *cb, int id)
{
    os_lock(&g_steam_lock);
    if (g_steam_nregs == g_steam_cap) {
        int cap = g_steam_cap ? g_steam_cap * 2 : 16;
        struct steam_reg *n = (struct steam_reg *)realloc(g_steam_regs, (size_t)cap * sizeof *n);
        if (!n) {
            os_unlock(&g_steam_lock);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return;
        }
        g_steam_regs = n;
        g_steam_cap = cap;
    }
    g_steam_regs[g_steam_nregs].cb = cb;
    g_steam_regs[g_steam_nregs].id = id;
    g_steam_nregs++;
    os_unlock(&g_steam_lock);
}

extern "C" void SteamAPI_UnregisterCallback(void *cb)
{
    os_lock(&g_steam_lock);
    int o = 0;
    for (int i = 0; i < g_steam_nregs; i++)
        if (g_steam_regs[i].cb != cb)
            g_steam_regs[o++] = g_steam_regs[i];
    g_steam_nregs = o;
    os_unlock(&g_steam_lock);
}

extern "C" int fnvnx_steam_callback_count(void)
{
    os_lock(&g_steam_lock);
    int n = g_steam_nregs;
    os_unlock(&g_steam_lock);
    return n;
}

extern "C" void *SteamUser(void) { return NULL; }
extern "C" void *SteamFriends(void) { return NULL; }
extern "C" void *SteamUtils(void) { return NULL; }
extern "C" void *SteamApps(void) { return NULL; }
extern "C" void *SteamUserStats(void) { return NULL; }
