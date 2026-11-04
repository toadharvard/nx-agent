/* profile.cpp - the first run's profile files, as the PC launcher (FalloutNVLauncher.exe) writes them; the game expects
 * them (WinMain's launcher gate 0x004da570 exits without FalloutPrefs.ini's sD3DDevice). There is no launcher on the
 * Switch, so boot.cpp calls fnvnx_first_run_profile() before WinMain. Each file is made only when it is missing,
 * through this Win32 layer (path.cpp mounts), so a player's own files always win.
 *
 * What the launcher writes on a first run, observed on the owner's install (GOG 1.4.0.525(a)) under Wine with the full
 * Data/ (S5-data, journal inbox s5-data-001):
 *   My Games\FalloutNV\Fallout.ini       a copy of C:\FalloutNV\Fallout_default.ini
 *   My Games\FalloutNV\FalloutPrefs.ini  Fallout_default.ini plus [Display] sD3DDevice="<adapter 0's Description>"
 *                                        (no preset: Low/Medium/High/VeryHigh.ini are only the Options dialog's)
 *   AppData\Local\FalloutNV\plugins.txt  empty (the add-ons load through their Data\*.nam files)
 *   AppData\Local\FalloutNV\NVDLCList.txt empty
 * The adapter's Description comes from d3d9.dll's Direct3DCreate9()->GetAdapterIdentifier(0), as the launcher gets it
 * (the null device: "fnv-nx null D3D9 device"); when no d3d9 module answers, the null device's name is written.
 * Machine-specific keys the launcher's Options dialog would set for this screen: [Display] iSize W/H 1280x720.
 *
 * Returns a mask of FNVNX_PROFILE_* for the files it made (0: all were there or nothing could be made). */
#include "w32.h"

#include <stdio.h>

#define PROFILE "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\"
#define LOCAL "C:\\Users\\Player\\AppData\\Local\\FalloutNV\\"
#define INSTALL "C:\\FalloutNV\\"
#define NULL_DEVICE "fnv-nx null D3D9 device"

static int exists(const char *win) { return GetFileAttributesA(win) != INVALID_FILE_ATTRIBUTES; }

static int make_empty(const char *path)
{
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return 0;
    CloseHandle(h);
    return 1;
}

/* adapter 0's Description, as the launcher reads it: IDirect3D9 slot 5 GetAdapterIdentifier, slot 2 Release (the
 * D3DADAPTER_IDENTIFIER9 layout: Driver[512], then Description[512]) */
static void adapter_description(char *out, size_t n)
{
    snprintf(out, n, "%s", NULL_DEVICE);
    HMODULE m = LoadLibraryA("d3d9.dll");
    if (!m)
        return;
    typedef void *(*create_fn)(UINT);
    create_fn create = (create_fn)(void *)GetProcAddress(m, "Direct3DCreate9");
    void *d3d = create ? create(32 /* D3D_SDK_VERSION */) : NULL;
    if (!d3d)
        return;
    void **vt = *(void ***)d3d;
    typedef HRESULT (*gai_fn)(void *, UINT, DWORD, void *);
    typedef ULONG (*release_fn)(void *);
    static char id[2048];
    memset(id, 0, sizeof id);
    if (((gai_fn)vt[5])(d3d, 0, 0, id) == 0 && id[512])
        snprintf(out, n, "%.511s", id + 512);
    ((release_fn)vt[2])(d3d);
}

extern "C" unsigned fnvnx_first_run_profile(void)
{
    unsigned made = 0;
    if (!exists(PROFILE "Fallout.ini") && exists(INSTALL "Fallout_default.ini") &&
        CopyFileA(INSTALL "Fallout_default.ini", PROFILE "Fallout.ini", TRUE))
        made |= FNVNX_PROFILE_INI;
    if (!exists(PROFILE "FalloutPrefs.ini") && exists(INSTALL "Fallout_default.ini") &&
        CopyFileA(INSTALL "Fallout_default.ini", PROFILE "FalloutPrefs.ini", TRUE)) {
        char dev[600], quoted[620];
        adapter_description(dev, sizeof dev);
        snprintf(quoted, sizeof quoted, "\"%s\"", dev);   /* the launcher writes the quotes into the value */
        if (WritePrivateProfileStringA("Display", "sD3DDevice", quoted, PROFILE "FalloutPrefs.ini") &&
            WritePrivateProfileStringA("Display", "iSize W", "1280", PROFILE "FalloutPrefs.ini") &&
            WritePrivateProfileStringA("Display", "iSize H", "720", PROFILE "FalloutPrefs.ini"))
            made |= FNVNX_PROFILE_PREFS;
    }
    if (!exists(LOCAL "plugins.txt") && make_empty(LOCAL "plugins.txt"))
        made |= FNVNX_PROFILE_PLUGINS;
    if (!exists(LOCAL "NVDLCList.txt"))
        make_empty(LOCAL "NVDLCList.txt");
    if (made)
        w32_log("first-run profile: made%s%s%s\n", made & FNVNX_PROFILE_INI ? " Fallout.ini" : "",
                made & FNVNX_PROFILE_PREFS ? " FalloutPrefs.ini" : "",
                made & FNVNX_PROFILE_PLUGINS ? " plugins.txt" : "");
    return made;
}
