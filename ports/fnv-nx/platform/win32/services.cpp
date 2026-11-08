/* services.cpp - advapi32 registry (in memory), shell32, ole32, ws2_32 and steam_api.
 *
 * Registry: a process-local store seeded with the keys the launcher-less game expects
 * (HKLM\SOFTWARE\Bethesda Softworks\FalloutNV "Installed Path"). Writes are kept for the session only.
 * Winsock: Havok's remote-debugger socket is the only user; networking is reported as unavailable. The
 * functions are named fnvnx_ws2_* so they cannot collide with the BSD socket API of the target libc; the
 * link step (S2) maps the ws2_32 IAT slots and import thunks to them (docs/RUNTIME.md).
 * Steam / GOG Galaxy (orchestrator policy): no-op success. SteamAPI_Init and IsSteamRunning succeed, every interface
 * accessor returns NULL: the exe null-checks each one (0x006ff741.., 0x00af28f9.., 0x00af2979, 0x00af2dce), so
 * achievements, the overlay and the store queries are skipped and callbacks run as no-ops. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- registry */
struct regval { char key[160], name[64]; DWORD type; DWORD len; BYTE data[MAX_PATH + 4]; };
static struct regval g_reg[64];
static int g_nreg;
static os_lock_t g_reg_lock;
struct regkey { uint32_t magic; char path[160]; };
#define REGKEY_MAGIC 0x4B474552u

static void reg_root(HKEY k, char *out, size_t n)
{
    struct regkey *rk = (struct regkey *)k;
    if (k == HKEY_LOCAL_MACHINE) snprintf(out, n, "HKLM");
    else if (k == HKEY_CURRENT_USER) snprintf(out, n, "HKCU");
    else if (k == HKEY_CLASSES_ROOT) snprintf(out, n, "HKCR");
    else if (k == HKEY_USERS) snprintf(out, n, "HKU");
    else if (rk && rk->magic == REGKEY_MAGIC) snprintf(out, n, "%s", rk->path);
    else out[0] = 0;
}

static void reg_set(const char *key, const char *name, DWORD type, const void *data, DWORD len)
{
    int i;
    for (i = 0; i < g_nreg; i++)
        if (!w32_stricmp(g_reg[i].key, key) && !w32_stricmp(g_reg[i].name, name))
            break;
    if (i == g_nreg) {
        if (g_nreg == 64)
            return;
        g_nreg++;
    }
    snprintf(g_reg[i].key, sizeof g_reg[i].key, "%s", key);
    snprintf(g_reg[i].name, sizeof g_reg[i].name, "%s", name);
    g_reg[i].type = type;
    g_reg[i].len = len > sizeof g_reg[i].data ? (DWORD)sizeof g_reg[i].data : len;
    memcpy(g_reg[i].data, data, g_reg[i].len);
}

static void reg_seed(void)
{
    static int seeded;
    if (seeded++)
        return;
    const char *k = "HKLM\\SOFTWARE\\Bethesda Softworks\\FalloutNV";
    const char *ip = "C:\\FalloutNV\\";
    reg_set(k, "Installed Path", REG_SZ, ip, (DWORD)strlen(ip) + 1);
    reg_set("HKLM\\SOFTWARE\\Wow6432Node\\Bethesda Softworks\\FalloutNV", "Installed Path", REG_SZ, ip, (DWORD)strlen(ip) + 1);
}

static int reg_key_exists(const char *path)
{
    size_t l = strlen(path);
    for (int i = 0; i < g_nreg; i++)
        if (!w32_strnicmp(g_reg[i].key, path, l) && (g_reg[i].key[l] == 0 || g_reg[i].key[l] == '\\'))
            return 1;
    return 0;
}

static LSTATUS reg_open(HKEY parent, LPCSTR sub, PHKEY out, int create, LPDWORD disp)
{
    os_lock(&g_reg_lock);
    reg_seed();
    char path[160];
    reg_root(parent, path, sizeof path);
    if (!path[0]) {
        os_unlock(&g_reg_lock);
        return ERROR_INVALID_HANDLE;
    }
    if (sub && *sub) {
        size_t l = strlen(path);
        snprintf(path + l, sizeof path - l, "\\%s", sub);
    }
    int exists = reg_key_exists(path);
    if (!exists && !create) {
        os_unlock(&g_reg_lock);
        return ERROR_FILE_NOT_FOUND;
    }
    if (!exists)
        reg_set(path, "", REG_NONE, "", 0); /* marker so the key exists */
    os_unlock(&g_reg_lock);
    struct regkey *k = (struct regkey *)calloc(1, sizeof *k);
    if (!k)
        return ERROR_NOT_ENOUGH_MEMORY;
    k->magic = REGKEY_MAGIC;
    snprintf(k->path, sizeof k->path, "%s", path);
    *out = (HKEY)k;
    if (disp)
        *disp = exists ? REG_OPENED_EXISTING_KEY : REG_CREATED_NEW_KEY;
    return ERROR_SUCCESS;
}

extern "C" LSTATUS RegOpenKeyExA(HKEY k, LPCSTR sub, DWORD o, REGSAM s, PHKEY out)
{
    W32_UNUSED(o); W32_UNUSED(s);
    return reg_open(k, sub, out, 0, NULL);
}

extern "C" LSTATUS RegCreateKeyExA(HKEY k, LPCSTR sub, DWORD r, LPSTR c, DWORD o, REGSAM s, void *sa, PHKEY out, LPDWORD disp)
{
    W32_UNUSED(r); W32_UNUSED(c); W32_UNUSED(o); W32_UNUSED(s); W32_UNUSED(sa);
    return reg_open(k, sub, out, 1, disp);
}

extern "C" LSTATUS RegQueryValueExA(HKEY k, LPCSTR name, LPDWORD res, LPDWORD type, LPBYTE data, LPDWORD len)
{
    W32_UNUSED(res);
    char path[160];
    reg_root(k, path, sizeof path);
    os_lock(&g_reg_lock);
    reg_seed();
    LSTATUS r = ERROR_FILE_NOT_FOUND;
    for (int i = 0; i < g_nreg; i++)
        if (!w32_stricmp(g_reg[i].key, path) && !w32_stricmp(g_reg[i].name, name ? name : "") && g_reg[i].type != REG_NONE) {
            if (type)
                *type = g_reg[i].type;
            if (data && len && W32_DW(*len) < g_reg[i].len)
                r = ERROR_MORE_DATA;
            else {
                if (data)
                    memcpy(data, g_reg[i].data, g_reg[i].len);
                r = ERROR_SUCCESS;
            }
            if (len)
                *len = g_reg[i].len;
            break;
        }
    os_unlock(&g_reg_lock);
    return r;
}

extern "C" LSTATUS RegSetValueExA(HKEY k, LPCSTR name, DWORD res, DWORD type, const BYTE *data, DWORD len)
{
    W32_UNUSED(res);
    char path[160];
    reg_root(k, path, sizeof path);
    if (!path[0])
        return ERROR_INVALID_HANDLE;
    os_lock(&g_reg_lock);
    reg_set(path, name ? name : "", type, data, W32_DW(len));
    os_unlock(&g_reg_lock);
    return ERROR_SUCCESS;
}

extern "C" LSTATUS RegCloseKey(HKEY k)
{
    struct regkey *rk = (struct regkey *)k;
    if (rk && rk->magic == REGKEY_MAGIC) {
        rk->magic = 0;
        free(rk);
    }
    return ERROR_SUCCESS;
}

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
