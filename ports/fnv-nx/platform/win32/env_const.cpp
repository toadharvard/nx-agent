/* env_const.cpp - the imports whose documented result is one constant for every argument in this environment
 * (ENV_TRUTH.md, MSDN). The bodies are generated from manifest/env_const.tsv by manifest/gen_env_const.py
 * (the recipe's generated stages in platform/build.json run it; env_const.gen.inc is not a source file). */
#include <fnvnx_export.h>
#include "w32.h"

#include "env_const.gen.inc"
FNVNX_EXPORT("galaxywrp.dll", "SteamUserStats", SteamUserStats, "func");
FNVNX_EXPORT("galaxywrp.dll", "SteamApps", SteamApps, "func");
FNVNX_EXPORT("galaxywrp.dll", "SteamUtils", SteamUtils, "func");
FNVNX_EXPORT("galaxywrp.dll", "SteamFriends", SteamFriends, "func");
FNVNX_EXPORT("galaxywrp.dll", "SteamUser", SteamUser, "func");
FNVNX_EXPORT("galaxywrp.dll", "SteamAPI_IsSteamRunning", SteamAPI_IsSteamRunning, "func");
FNVNX_EXPORT("galaxywrp.dll", "SteamAPI_Init", SteamAPI_Init, "func");
FNVNX_EXPORT("kernel32.dll", "FreeResource", FreeResource, "func");
FNVNX_EXPORT("kernel32.dll", "LockResource", LockResource, "func");
FNVNX_EXPORT("kernel32.dll", "SetHandleCount", SetHandleCount, "func");
FNVNX_EXPORT("kernel32.dll", "GetUserDefaultLCID", GetUserDefaultLCID, "func");
FNVNX_EXPORT("kernel32.dll", "IsDebuggerPresent", IsDebuggerPresent, "func");
FNVNX_EXPORT("kernel32.dll", "GetProcessHeap", GetProcessHeap, "func");
FNVNX_EXPORT("kernel32.dll", "GetCurrentThread", GetCurrentThread, "func");
FNVNX_EXPORT("kernel32.dll", "GetCurrentProcess", GetCurrentProcess, "func");
