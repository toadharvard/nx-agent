/* profile.cpp - the first run's profile files, which the PC launcher (FalloutNVLauncher.exe) writes and the game
 * expects. There is no launcher on the Switch, so boot.cpp calls fnvnx_first_run_profile() before WinMain. Each file
 * is made only when it is missing, through this Win32 layer (path.cpp mounts), so a player's own files always win:
 *
 *   My Games\FalloutNV\Fallout.ini       a copy of C:\FalloutNV\Fallout_default.ini (as the launcher does)
 *   My Games\FalloutNV\FalloutPrefs.ini  a copy of the install's preset (Medium.ini, else Low.ini, High.ini,
 *                                        VeryHigh.ini: the launcher's choice for a mid-range GPU), then [Display]
 *                                        set to the Switch: 1280x720, full screen
 *   AppData\Local\FalloutNV\plugins.txt  FalloutNV.esm and the official add-on masters Data\ holds, in release order
 *
 * Returns a mask of FNVNX_PROFILE_* for the files it made (0: all were there or nothing could be made). */
#include "w32.h"

#include <stdio.h>

#define PROFILE "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\"
#define LOCAL "C:\\Users\\Player\\AppData\\Local\\FalloutNV\\"
#define INSTALL "C:\\FalloutNV\\"

static int exists(const char *win) { return GetFileAttributesA(win) != INVALID_FILE_ATTRIBUTES; }

static int make_plugins(const char *path)
{
    /* the add-ons in release order (Fallout: New Vegas Ultimate Edition); a master the install lacks is left out */
    static const char *const masters[] = {"FalloutNV.esm", "DeadMoney.esm", "HonestHearts.esm", "OldWorldBlues.esm",
                                          "LonesomeRoad.esm", "GunRunnersArsenal.esm", "ClassicPack.esm",
                                          "MercenaryPack.esm", "TribalPack.esm", "CaravanPack.esm"};
    char text[1024];
    size_t n = 0;
    for (const char *m : masters) {
        char p[W32_PATH_MAX];
        snprintf(p, sizeof p, INSTALL "Data\\%s", m);
        if (exists(p) && n + strlen(m) + 3 < sizeof text)
            n += (size_t)snprintf(text + n, sizeof text - n, "%s\r\n", m);
    }
    if (!n)
        return 0;
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return 0;
    DWORD put = 0;
    BOOL ok = WriteFile(h, text, (DWORD)n, &put, NULL);
    CloseHandle(h);
    return ok && put == n;
}

extern "C" unsigned fnvnx_first_run_profile(void)
{
    unsigned made = 0;
    if (!exists(PROFILE "Fallout.ini") && exists(INSTALL "Fallout_default.ini") &&
        CopyFileA(INSTALL "Fallout_default.ini", PROFILE "Fallout.ini", TRUE))
        made |= FNVNX_PROFILE_INI;
    if (!exists(PROFILE "FalloutPrefs.ini")) {
        static const char *const presets[] = {"Medium.ini", "Low.ini", "High.ini", "VeryHigh.ini"};
        for (const char *p : presets) {
            char src[W32_PATH_MAX];
            snprintf(src, sizeof src, INSTALL "%s", p);
            if (exists(src) && CopyFileA(src, PROFILE "FalloutPrefs.ini", TRUE))
                break;
        }
        /* the Switch's screen: handheld 720p (docked output is scaled by the host) */
        if (WritePrivateProfileStringA("Display", "iSize W", "1280", PROFILE "FalloutPrefs.ini") &&
            WritePrivateProfileStringA("Display", "iSize H", "720", PROFILE "FalloutPrefs.ini") &&
            WritePrivateProfileStringA("Display", "bFull Screen", "1", PROFILE "FalloutPrefs.ini"))
            made |= FNVNX_PROFILE_PREFS;
    }
    if (!exists(LOCAL "plugins.txt") && make_plugins(LOCAL "plugins.txt"))
        made |= FNVNX_PROFILE_PLUGINS;
    if (made)
        w32_log("first-run profile: made%s%s%s\n", made & FNVNX_PROFILE_INI ? " Fallout.ini" : "",
                   made & FNVNX_PROFILE_PREFS ? " FalloutPrefs.ini" : "",
                   made & FNVNX_PROFILE_PLUGINS ? " plugins.txt" : "");
    return made;
}
