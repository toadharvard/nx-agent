/* test_imports.cpp - import slots by ordinal and under aliased DLL names (platform/win32/exports.cpp). */
#include "s4_test.h"

TEST(imports_wsock32_ordinals)
{
    /* Winsock 1.1 numbering, all forwarded to the ws2_32 entries */
    struct { unsigned ord; const char *name; } k[] = {
        {1, "accept"}, {2, "bind"}, {3, "closesocket"}, {4, "connect"}, {9, "htons"}, {10, "inet_addr"},
        {11, "inet_ntoa"}, {12, "ioctlsocket"}, {13, "listen"}, {15, "ntohs"}, {16, "recv"}, {18, "select"},
        {19, "send"}, {21, "setsockopt"}, {23, "socket"}, {52, "gethostbyname"}, {57, "gethostname"},
        {101, "WSAAsyncSelect"}, {111, "WSAGetLastError"}, {115, "WSAStartup"}, {151, "__WSAFDIsSet"},
    };
    for (auto &e : k) {
        void *p = fnvnx_win32_lookup_import("WSOCK32.dll", NULL, e.ord);
        CHECK(p != NULL);
        CHECK(p == fnvnx_win32_lookup_export("ws2_32.dll", e.name));
    }
    CHECK(fnvnx_win32_lookup_import("WSOCK32.dll", NULL, 999) == NULL);
    CHECK(fnvnx_win32_lookup_import("KERNEL32.dll", NULL, 3) == NULL);
    typedef unsigned short (*hs)(unsigned short);
    CHECK_EQ(((hs)fnvnx_win32_lookup_import("wsock32", NULL, 9))(0x1234), 0x3412);
    typedef int (*isset)(UINT_PTR, void *);
    CHECK_EQ(((isset)fnvnx_win32_lookup_import("wsock32", NULL, 151))(5, NULL), 0);
    /* by name through the alias as well */
    CHECK(fnvnx_win32_lookup_import("wsock32.dll", "socket", FNVNX_IMPORT_BY_NAME) ==
          fnvnx_win32_lookup_export("ws2_32", "socket"));
}

TEST(imports_galaxywrp_is_steam_api)
{
    const char *n[] = {"SteamAPI_Init", "SteamAPI_IsSteamRunning", "SteamAPI_Shutdown", "SteamAPI_RunCallbacks",
                       "SteamAPI_RegisterCallback", "SteamAPI_UnregisterCallback", "SteamUser", "SteamFriends",
                       "SteamUtils", "SteamApps", "SteamUserStats"};
    for (const char *x : n) {
        void *p = fnvnx_win32_lookup_import("GalaxyWrp.dll", x, FNVNX_IMPORT_BY_NAME);
        CHECK(p != NULL);
        CHECK(p == fnvnx_win32_lookup_export("steam_api.dll", x));
    }
    CHECK(fnvnx_win32_lookup_export("galaxywrp", "CreateFileA") == NULL);
}
