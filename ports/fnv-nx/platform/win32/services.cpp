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
    snprintf(out, MAX_PATH, "%s%s", W32_USER_DIR, sub);
    if (csidl & CSIDL_FLAG_CREATE)
        CreateDirectoryA(out, NULL);
    return S_OK;
}

/* ---- ole32
 * Apartments: every thread has an initialisation count and the apartment model of its first successful
 * CoInitialize[Ex] (COINIT_APARTMENTTHREADED = 2 is an STA, anything else an MTA). A thread that never initialised is
 * implicitly in the MTA while at least one thread of the process holds an MTA initialisation (g_mta_threads).
 * Servers: the in-process servers are registered with their class factory; a server registered with
 * fnvnx_register_com_class is part of the image (resident: DllCanUnloadNow would answer S_FALSE for ever), one
 * registered with fnvnx_register_com_dll carries its own can-unload query which CoFreeUnusedLibraries asks. No registered
 * factory takes an outer unknown, so no class supports aggregation. */
enum { W32_COINIT_STA = 2 };
static __thread int g_co_count;
static __thread DWORD g_co_model;
static int g_mta_threads;

struct comclass { GUID clsid; fnvnx_com_factory f; fnvnx_com_can_unload can_unload; };
static struct comclass g_com[16];
static int g_ncom;
static os_lock_t g_com_lock;

static int com_register(const GUID *clsid, fnvnx_com_factory f, fnvnx_com_can_unload can_unload)
{
    os_lock(&g_com_lock);
    int ok = g_ncom < 16;
    if (ok) {
        g_com[g_ncom].clsid = *clsid;
        g_com[g_ncom].f = f;
        g_com[g_ncom].can_unload = can_unload;
        g_ncom++;
    }
    os_unlock(&g_com_lock);
    return ok;
}

extern "C" int fnvnx_register_com_class(const GUID *clsid, fnvnx_com_factory f)
{
    return com_register(clsid, f, NULL);
}

extern "C" int fnvnx_register_com_dll(const GUID *clsid, fnvnx_com_factory f, fnvnx_com_can_unload can_unload)
{
    return can_unload && com_register(clsid, f, can_unload);
}

extern "C" HRESULT CoInitializeEx(LPVOID reserved, DWORD model)
{
    if (reserved)
        return E_INVALIDARG;
    DWORD m = (model & W32_COINIT_STA) ? W32_COINIT_STA : 0;
    if (g_co_count && g_co_model != m)
        return RPC_E_CHANGED_MODE;
    HRESULT hr = g_co_count ? S_FALSE : S_OK;
    if (!g_co_count) {
        g_co_model = m;
        if (!m)
            __atomic_add_fetch(&g_mta_threads, 1, __ATOMIC_SEQ_CST);
    }
    g_co_count++;
    return hr;
}

extern "C" HRESULT CoInitialize(LPVOID reserved)
{
    return CoInitializeEx(reserved, W32_COINIT_STA);
}

extern "C" void CoUninitialize(void)
{
    if (!g_co_count)
        return; /* no successful CoInitialize on this thread to balance */
    if (--g_co_count == 0 && !g_co_model)
        __atomic_sub_fetch(&g_mta_threads, 1, __ATOMIC_SEQ_CST);
}

extern "C" void CoFreeUnusedLibraries(void)
{
    os_lock(&g_com_lock);
    int o = 0;
    for (int i = 0; i < g_ncom; i++) {
        if (g_com[i].can_unload && g_com[i].can_unload() == S_OK)
            continue; /* the server's DllCanUnloadNow said it is idle: its class leaves the registry */
        g_com[o++] = g_com[i];
    }
    g_ncom = o;
    os_unlock(&g_com_lock);
}
extern "C" LPVOID CoTaskMemAlloc(SIZE_T n) { return malloc(n ? n : 1); }
extern "C" void CoTaskMemFree(LPVOID p) { free(p); }

extern "C" HRESULT CoCreateInstance(const GUID *clsid, void *outer, DWORD ctx, const GUID *iid, LPVOID *out)
{
    if (!out)
        return E_POINTER;
    *out = NULL;
    if (!clsid || !iid)
        return E_INVALIDARG;
    if (!g_co_count && !__atomic_load_n(&g_mta_threads, __ATOMIC_SEQ_CST))
        return CO_E_NOTINITIALIZED;
    if (!(ctx & (CLSCTX_INPROC_SERVER | CLSCTX_INPROC_HANDLER)))
        return REGDB_E_CLASSNOTREG; /* only in-process servers exist */
    fnvnx_com_factory f = NULL;
    os_lock(&g_com_lock);
    for (int i = 0; i < g_ncom && !f; i++)
        if (!memcmp(&g_com[i].clsid, clsid, sizeof(GUID)))
            f = g_com[i].f;
    os_unlock(&g_com_lock);
    if (!f)
        return REGDB_E_CLASSNOTREG;
    if (outer)
        return CLASS_E_NOAGGREGATION;
    return f(iid, out);
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
/* fnvnx_nvapi_not_initialized: win32/env_const.cpp, from env_const.tsv (ENV:M2) */
/* ---- steam_api / galaxywrp.dll: the public Steamworks contract in the state "Steam client not running"
 * (ENV_TRUTH M6). SteamAPI_Init fails every time (no client to connect to), SteamAPI_IsSteamRunning is false, every
 * interface accessor is NULL (no ISteam* object exists without a client), no callback is ever delivered (callbacks
 * come from the client), and the callback registration list is the real one: RegisterCallback adds an
 * (object, id) pair, UnregisterCallback removes all pairs of the object. */
static os_lock_t g_steam_lock;
static struct steam_reg { void *cb; int id; } *g_steam_regs;
static int g_steam_nregs, g_steam_cap;

/* SteamAPI_Init, SteamAPI_IsSteamRunning and the interface accessors: env_const.tsv (ENV:M6). Init never succeeds, so
 * the pipe to the client (HSteamPipe, 0 = none) is never opened. */
static int g_steam_pipe;

/* releases the pipe to the client and marks the API uninitialised; the registrations belong to their objects */
extern "C" void SteamAPI_Shutdown(void)
{
    os_lock(&g_steam_lock);
    g_steam_pipe = 0;
    os_unlock(&g_steam_lock);
}

/* dispatches the callbacks the client queued on the pipe to the registered objects; without a pipe nothing is queued */
extern "C" void SteamAPI_RunCallbacks(void)
{
    os_lock(&g_steam_lock);
    int pipe = g_steam_pipe;
    os_unlock(&g_steam_lock);
    if (!pipe)
        return;
}

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
