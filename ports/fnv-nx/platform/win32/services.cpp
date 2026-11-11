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
    W32Log().s("ShellExecuteA(").s(op ? op : "open").s(", ").s(file ? file : "").s(") not available").emit();
    return (HINSTANCE)(uintptr_t)31; /* SE_ERR_NOASSOC (<= 32 means failure) */
}

/* SHGetFolderPathA. The CSIDL table is the Windows 7 default location of every folder id 0x00-0x3e (the shell's
 * known-folder defaults) in the ENV F2 profile tree; ENV P4/P1 make this a 32-bit Windows 7, so the x86 variants of the
 * system and Program Files folders equal the base ones. "%U" is the profile directory of the token's user. A null
 * path marks a virtual folder (no file-system path: E_FAIL); an id not in the table is not a CSIDL (E_INVALIDARG).
 * Flags (the high byte): CREATE creates the folder (and its parents), DONT_VERIFY returns the path unchecked,
 * NO_ALIAS and PER_USER_INIT do not change the path (no alias or lazily initialised folder exists here). Without
 * either, a folder that does not exist gives S_FALSE (with its path). Tokens: NULL is the current user, -1 the default
 * user profile (C:\Users\Default); any other value is not a token handle (E_FAIL). SHGFP_TYPE_CURRENT and _DEFAULT are the
 * same location: nothing is redirected. */
#define SH_RM "%U\\AppData\\Roaming"
#define SH_SM SH_RM "\\Microsoft\\Windows\\Start Menu"
#define SH_PD "C:\\ProgramData"
#define SH_PDSM SH_PD "\\Microsoft\\Windows\\Start Menu"
#define SH_PUB "C:\\Users\\Public"
static const struct { unsigned char id; const char *path; } g_csidl[] = {
    { 0x00, "%U\\Desktop" }, { 0x01, NULL }, { 0x02, SH_SM "\\Programs" }, { 0x03, NULL }, { 0x04, NULL },
    { 0x05, "%U\\Documents" }, { 0x06, "%U\\Favorites" }, { 0x07, SH_SM "\\Programs\\Startup" },
    { 0x08, SH_RM "\\Microsoft\\Windows\\Recent" }, { 0x09, SH_RM "\\Microsoft\\Windows\\SendTo" }, { 0x0a, NULL },
    { 0x0b, SH_SM }, { 0x0c, "%U\\Documents" }, { 0x0d, "%U\\Music" }, { 0x0e, "%U\\Videos" },
    { 0x10, "%U\\Desktop" }, { 0x11, NULL }, { 0x12, NULL }, { 0x13, SH_RM "\\Microsoft\\Windows\\Network Shortcuts" },
    { 0x14, "C:\\Windows\\Fonts" }, { 0x15, SH_RM "\\Microsoft\\Windows\\Templates" },
    { 0x16, SH_PDSM }, { 0x17, SH_PDSM "\\Programs" }, { 0x18, SH_PDSM "\\Programs\\Startup" },
    { 0x19, SH_PUB "\\Desktop" }, { 0x1a, SH_RM }, { 0x1b, SH_RM "\\Microsoft\\Windows\\Printer Shortcuts" },
    { 0x1c, "%U\\AppData\\Local" }, { 0x1d, NULL }, { 0x1e, NULL }, { 0x1f, SH_PUB "\\Favorites" },
    { 0x20, "%U\\AppData\\Local\\Microsoft\\Windows\\Temporary Internet Files" },
    { 0x21, SH_RM "\\Microsoft\\Windows\\Cookies" }, { 0x22, "%U\\AppData\\Local\\Microsoft\\Windows\\History" },
    { 0x23, SH_PD }, { 0x24, "C:\\Windows" }, { 0x25, "C:\\Windows\\system32" }, { 0x26, "C:\\Program Files" },
    { 0x27, "%U\\Pictures" }, { 0x28, "%U" }, { 0x29, "C:\\Windows\\system32" }, { 0x2a, "C:\\Program Files" },
    { 0x2b, "C:\\Program Files\\Common Files" }, { 0x2c, "C:\\Program Files\\Common Files" },
    { 0x2d, SH_PD "\\Microsoft\\Windows\\Templates" }, { 0x2e, SH_PUB "\\Documents" },
    { 0x2f, SH_PDSM "\\Programs\\Administrative Tools" }, { 0x30, SH_SM "\\Programs\\Administrative Tools" },
    { 0x31, NULL }, { 0x35, SH_PUB "\\Music" }, { 0x36, SH_PUB "\\Pictures" }, { 0x37, SH_PUB "\\Videos" },
    { 0x38, "C:\\Windows\\Resources" }, { 0x39, "C:\\Windows\\Resources\\1033" }, { 0x3a, SH_PD "\\OEM Links" },
    { 0x3b, "%U\\AppData\\Local\\Microsoft\\Windows\\Burn\\Burn" }, { 0x3d, NULL }, { 0x3e, "C:\\Users" },
};

extern "C" HRESULT SHGetFolderPathA(HWND h, int csidl, HANDLE tok, DWORD flags, LPSTR out)
{
    W32_UNUSED(h);
    W32_UNUSED(flags);   /* SHGFP_TYPE_CURRENT / _DEFAULT: one location */
    if (!out)
        return E_INVALIDARG;
    out[0] = 0;
    unsigned id = (unsigned)csidl & 0xFF, fl = (unsigned)csidl & 0xFF00u;
    const char *pat = NULL;
    bool known = false;
    for (size_t i = 0; i < sizeof g_csidl / sizeof *g_csidl; i++)
        if (g_csidl[i].id == id) {
            pat = g_csidl[i].path;
            known = true;
            break;
        }
    if (!known)
        return E_INVALIDARG;
    uintptr_t t = (uintptr_t)tok;
    if (t != 0 && t != (uintptr_t)-1 && t != 0xFFFFFFFFu)
        return E_FAIL;          /* no such token handle */
    if (!pat)
        return E_FAIL;          /* a virtual folder */
    const char *user = (t == 0) ? W32_USER_DIR : "C:\\Users\\Default";
    char path[MAX_PATH];
    size_t o = 0;
    for (const char *p = pat; *p && o + 1 < sizeof path; p++) {
        if (p[0] == '%' && p[1] == 'U') {
            o += (size_t)snprintf(path + o, sizeof path - o, "%s", user);
            p++;
        } else
            path[o++] = *p;
    }
    path[o < sizeof path ? o : sizeof path - 1] = 0;
    snprintf(out, MAX_PATH, "%s", path);
    if (fl & 0x4000u /* CSIDL_FLAG_DONT_VERIFY */)
        return S_OK;
    DWORD a = GetFileAttributesA(path);
    if (a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY))
        return S_OK;
    if (!(fl & 0x8000u /* CSIDL_FLAG_CREATE */))
        return S_FALSE;
    for (char *p = path + 3; ; p++) {   /* "C:\" exists; create each component */
        if (*p == '\\' || !*p) {
            char c = *p;
            *p = 0;
            if (!CreateDirectoryA(path, NULL) && GetLastError() != ERROR_ALREADY_EXISTS)
                return E_FAIL;
            *p = c;
            if (!c)
                break;
        }
    }
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
