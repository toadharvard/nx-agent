/* test_display_contract.cpp - ENV_TRUTH D1: one adapter, one monitor, two modes (1280x720x32@60 handheld, 1920x1080x32@60
 * docked); the dock state is the one source of the display mode: GetSystemMetrics, GetDeviceCaps, the desktop window,
 * ChangeDisplaySettingsA, EnumDisplayDevicesA and nx_env_profile all report it. Contract: MSDN. */
#include "s4_test.h"

#include <stdlib.h>
#include <string>
#include <initializer_list>
#include <stdint.h>

extern "C" {
void os_posix_set_docked(int);
void fnvnx_display_mode_changed(void);
void fnvnx_registry_set_path(const char *host_path);
void fnvnx_registry_reset_for_test(void);
LONG ChangeDisplaySettingsA(void *, DWORD);
BOOL EnumDisplayDevicesA(LPCSTR, DWORD, void *, DWORD);
HDC GetDC(HWND);
int ReleaseDC(HWND, HDC);
int GetDeviceCaps(HDC, int);
HWND fnvnx_desktop_window(void);
}
#define GetDesktopWindow fnvnx_desktop_window
#include "fnvnx_render_hooks.h"

namespace {
struct DM {   /* DEVMODEA, 156 bytes */
    char name[32]; uint16_t spec, drv, size, extra; uint32_t fields; int32_t px, py; uint32_t orient, fixedout;
    int16_t color, duplex, yres, tt, collate; char form[32]; uint16_t logpix;
    uint32_t bpp, w, h, dflags, freq, icmm, icmi, media, dither, r1, r2, panw, panh;
};
static_assert(sizeof(DM) == 156, "DEVMODEA");
struct DD { uint32_t cb; char name[32], str[128]; uint32_t flags; char id[128], key[128]; };
static_assert(sizeof(DD) == 424, "DISPLAY_DEVICEA");
enum { F_POS = 0x20, F_ORIENT = 0x80, F_FIXED = 0x20000, F_BPP = 0x40000, F_W = 0x80000, F_H = 0x100000, F_DFLAGS = 0x200000, F_FREQ = 0x400000 };
enum { UPD = 1, TST = 2, FULL = 4, GLOB = 8, PRIM = 0x10, VIDP = 0x20, UNSAFE = 0x100, NOUNSAFE = 0x200, RESET_EX = 0x20000000, NORESET = 0x10000000, RESET = 0x40000000 };
enum { OK = 0, BADMODE = -2, NOTUPD = -3, BADFLAGS = -4, BADPARAM = -5 };

void dock(int d) { os_posix_set_docked(d); fnvnx_display_mode_changed(); }
DM mode(int w, int h, uint32_t f = F_W | F_H | F_BPP) { DM d; memset(&d, 0, sizeof d); d.size = sizeof d; d.fields = f; d.w = w; d.h = h; d.bpp = 32; d.freq = 60; return d; }
int dw(int d) { return d ? 1920 : 1280; }
int dh(int d) { return d ? 1080 : 720; }

int g_msgs, g_last_w, g_last_l;
LRESULT dproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (m == 0x7E) { g_msgs++; g_last_w = (int)w; g_last_l = (int)l; }
    return DefWindowProcA(h, m, w, l);
}
HWND topwin(const char *cls)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = dproc; wc.lpszClassName = cls;
    RegisterClassA(&wc);
    return CreateWindowExA(0, cls, "d", 0x90000000u, 0, 0, 100, 100, NULL, NULL, NULL, NULL);
}
}

TEST(display_profile_and_state)
{
    for (int d = 0; d <= 1; d++) {
        dock(d);
        CHECK_EQ((int)nx_env_profile(), d);
        CHECK_EQ(GetSystemMetrics(SM_CXSCREEN), dw(d));
        CHECK_EQ(GetSystemMetrics(SM_CYSCREEN), dh(d));
        HDC dc = GetDC(NULL);
        CHECK_EQ(GetDeviceCaps(dc, 8), dw(d));        /* HORZRES */
        CHECK_EQ(GetDeviceCaps(dc, 10), dh(d));       /* VERTRES */
        CHECK_EQ(GetDeviceCaps(dc, 12), 32);          /* BITSPIXEL */
        CHECK_EQ(GetDeviceCaps(dc, 116), 60);         /* VREFRESH */
        CHECK_EQ(GetDeviceCaps(dc, 118), dw(d));      /* DESKTOPHORZRES */
        CHECK_EQ(GetDeviceCaps(dc, 117), dh(d));      /* DESKTOPVERTRES */
        ReleaseDC(NULL, dc);
        RECT r;
        CHECK(GetWindowRect(GetDesktopWindow(), &r));
        CHECK_EQ(r.left, 0); CHECK_EQ(r.top, 0); CHECK_EQ(r.right, dw(d)); CHECK_EQ(r.bottom, dh(d));
        CHECK(GetClientRect(GetDesktopWindow(), &r));
        CHECK_EQ(r.right, dw(d)); CHECK_EQ(r.bottom, dh(d));
    }
    dock(0);
}

TEST(display_enum_adapter)
{
    DD d;
    memset(&d, 0xCC, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA(NULL, 0, &d, 0));
    CHECK_STR(d.name, "\\\\.\\DISPLAY1");
    CHECK_EQ(d.flags, 0x1 | 0x4 | 0x10);              /* ATTACHED_TO_DESKTOP | PRIMARY_DEVICE | VGA_COMPATIBLE (the reference's adapter flags) */
    CHECK(d.str[0] && d.id[0] && d.key[0]);
    CHECK(!strncmp(d.id, "PCI\\VEN_", 8));
    CHECK(!strncmp(d.key, "\\Registry\\Machine\\System\\CurrentControlSet\\Control\\Video\\{", 58));
    CHECK_EQ(d.cb, sizeof d);
    for (DWORD i = 1; i < 4; i++) { memset(&d, 0, sizeof d); d.cb = sizeof d; CHECK(!EnumDisplayDevicesA(NULL, i, &d, 0)); }
    CHECK(!EnumDisplayDevicesA(NULL, 0x7FFFFFFFu, &d, 0));
    /* the interface-name flag is for monitors only; an adapter ignores it */
    memset(&d, 0, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA(NULL, 0, &d, 1)); CHECK_STR(d.name, "\\\\.\\DISPLAY1"); CHECK(!strncmp(d.id, "PCI\\", 4));
}

TEST(display_enum_monitor)
{
    DD d;
    memset(&d, 0xCC, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA("\\\\.\\DISPLAY1", 0, &d, 0));
    CHECK_STR(d.name, "\\\\.\\DISPLAY1\\Monitor0");
    CHECK_EQ(d.flags, 0x1 | 0x2);                    /* ACTIVE | ATTACHED */
    CHECK(!strncmp(d.id, "MONITOR\\", 8)); CHECK(d.str[0]); CHECK(d.key[0]);
    memset(&d, 0, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA("\\\\.\\display1", 0, &d, 0));   /* names compare without case */
    memset(&d, 0, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA("\\\\.\\DISPLAY1", 0, &d, 1));   /* EDD_GET_DEVICE_INTERFACE_NAME */
    CHECK(!strncmp(d.id, "\\\\?\\DISPLAY#", 12));
    CHECK(strstr(d.id, "{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}") != NULL);
    memset(&d, 0, sizeof d); d.cb = sizeof d;
    CHECK(!EnumDisplayDevicesA("\\\\.\\DISPLAY1", 1, &d, 0));
    CHECK(!EnumDisplayDevicesA("\\\\.\\DISPLAY2", 0, &d, 0));
    CHECK(!EnumDisplayDevicesA("garbage", 0, &d, 0));
    CHECK(!EnumDisplayDevicesA("\\\\.\\DISPLAY1\\Monitor0", 0, &d, 0));
    /* the structure size is validated, with ERROR_INVALID_PARAMETER */
    d.cb = sizeof d - 4; SetLastError(0);
    CHECK(!EnumDisplayDevicesA(NULL, 0, &d, 0)); CHECK_EQ(GetLastError(), 87);
    d.cb = 0; CHECK(!EnumDisplayDevicesA(NULL, 0, &d, 0));
    d.cb = sizeof d + 4; CHECK(!EnumDisplayDevicesA(NULL, 0, &d, 0));
    CHECK(!EnumDisplayDevicesA(NULL, 0, NULL, 0));
}

TEST(display_change_validation)
{
    for (int d = 0; d <= 1; d++) {
        dock(d);
        int w = dw(d), h = dh(d), ow = dw(1 - d), oh = dh(1 - d);
        DM m = mode(w, h);
        CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        CHECK_EQ(ChangeDisplaySettingsA(&m, TST), OK);
        m = mode(ow, oh);                                       /* the other mode: the console cannot switch */
        CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        CHECK_EQ(ChangeDisplaySettingsA(&m, TST), BADMODE);
        CHECK_EQ(ChangeDisplaySettingsA(&m, FULL), BADMODE);
        CHECK_EQ(GetSystemMetrics(SM_CXSCREEN), w);             /* and nothing changed */
        m = mode(800, 600); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        m = mode(w, h + 1); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        m = mode(w, h); m.bpp = 16; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        m = mode(w, h); m.bpp = 24; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        /* fields not set keep the current value: a bad value in an unset field is not looked at */
        m = mode(ow, oh, F_BPP); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m = mode(ow, oh, F_W); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        m = mode(w, oh, F_W); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m = mode(ow, h, F_H); m.h = h; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m = mode(w, h, 0); m.w = 7; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);          /* no field at all */
        /* refresh: 60, or 0 / 1 = the default */
        m = mode(w, h, F_W | F_H | F_FREQ);
        for (uint32_t f : { 0u, 1u, 60u }) { m.freq = f; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK); }
        for (uint32_t f : { 2u, 30u, 59u, 61u, 75u, 120u }) { m.freq = f; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE); }
        m = mode(w, h, F_DFLAGS); m.dflags = 0; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m.dflags = 2; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);                   /* DM_INTERLACED */
        m.dflags = 1; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);                   /* DM_GRAYSCALE */
        m = mode(w, h, F_POS); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m.px = 10; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        m.px = 0; m.py = -5; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE);
        m = mode(w, h, F_ORIENT); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        for (uint32_t o : { 1u, 2u, 3u }) { m.orient = o; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADMODE); }
        m.orient = 4; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);
        m = mode(w, h, F_FIXED);
        for (uint32_t o : { 0u, 1u, 2u }) { m.fixedout = o; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK); }
        m.fixedout = 3; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);
        /* dmSize: the fields set must lie inside it; below dmFields nothing can be read */
        m = mode(w, h);
        m.size = 0; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);
        m.size = 43; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);
        m.size = 44; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);                   /* width, height, bpp lie beyond */
        m.size = 108; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);                  /* bpp inside, width not */
        m.size = 116; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);                        /* through dmPelsHeight */
        m.size = 156; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m.size = 400; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m = mode(w, h, F_FREQ); m.size = 120; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);
        m.size = 124; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m = mode(w, h, F_POS); m.size = 48; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), BADPARAM);
        m.size = 52; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m = mode(w, h, 0); m.size = 44; CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);
        m = mode(w, h, 0x1F | 0x10000000u); CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);   /* printer fields are not ours */
        /* no devmode: the registry / default mode */
        CHECK_EQ(ChangeDisplaySettingsA(NULL, 0), OK);
    }
    dock(0);
}

TEST(display_change_flags)
{
    char hive[1200];
    snprintf(hive, sizeof hive, "%s/display.hiv", s4_root());
    remove(hive);
    fnvnx_registry_set_path(hive);
    fnvnx_registry_reset_for_test();
    dock(0);
    DM m = mode(1280, 720);
    /* invalid flag sets */
    CHECK_EQ(ChangeDisplaySettingsA(&m, 0x80000000u), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(&m, 0x80), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(&m, 0x400), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(&m, GLOB), BADFLAGS);                 /* all users without the registry */
    CHECK_EQ(ChangeDisplaySettingsA(&m, GLOB | TST), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(&m, NORESET), BADFLAGS);              /* only with CDS_UPDATEREGISTRY */
    CHECK_EQ(ChangeDisplaySettingsA(&m, UPD | NORESET | RESET), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(&m, UPD | NORESET | RESET_EX), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(&m, UPD | FULL), BADFLAGS);           /* persistent and temporary */
    CHECK_EQ(ChangeDisplaySettingsA(&m, UNSAFE | NOUNSAFE), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(NULL, GLOB), BADFLAGS);
    CHECK_EQ(ChangeDisplaySettingsA(&m, VIDP), BADPARAM);                 /* its VIDEOPARAMETERS pointer is missing */
    /* valid ones */
    for (DWORD f : { (DWORD)TST, (DWORD)FULL, (DWORD)PRIM, (DWORD)UNSAFE, (DWORD)NOUNSAFE, (DWORD)(FULL | PRIM), (DWORD)(TST | FULL),
                     (DWORD)(PRIM | TST), (DWORD)(UPD | NORESET), (DWORD)(UPD | GLOB), (DWORD)RESET, (DWORD)RESET_EX })
        CHECK_EQ(ChangeDisplaySettingsA(&m, f), OK);
    /* CDS_UPDATEREGISTRY persists the mode, per user; CDS_GLOBAL also for all users */
    fnvnx_registry_reset_for_test();
    fnvnx_registry_set_path(hive);
    remove(hive);
    const char *base = "System\\CurrentControlSet\\Control\\Video\\{6A8D3C52-0F4E-4B7A-9C11-5E2B7D90A4C3}\\0000";
    DD ad; memset(&ad, 0, sizeof ad); ad.cb = sizeof ad;
    CHECK(EnumDisplayDevicesA(NULL, 0, &ad, 0));
    CHECK(strstr(ad.key, base) != NULL);                                  /* the adapter's DeviceKey names the same key */
    CHECK_EQ(ChangeDisplaySettingsA(&m, TST | UPD), OK);
    HKEY k;
    CHECK(RegOpenKeyExA(HKEY_CURRENT_USER, base, 0, 0x20019, &k) != ERROR_SUCCESS);
    for (int d = 0; d <= 1; d++) {
        dock(d);
        DM c = mode(dw(d), dh(d));
        CHECK_EQ(ChangeDisplaySettingsA(&c, UPD), OK);
        CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, base, 0, 0x20019, &k), ERROR_SUCCESS);
        struct { const char *n; DWORD v; } want[] = { { "DefaultSettings.BitsPerPel", 32 }, { "DefaultSettings.XResolution", (DWORD)dw(d) },
            { "DefaultSettings.YResolution", (DWORD)dh(d) }, { "DefaultSettings.VRefresh", 60 }, { "DefaultSettings.Flags", 0 },
            { "DefaultSettings.Orientation", 0 } };
        for (auto &w : want) {
            uint32_t v = 0xDEAD; DWORD ty = 0, len = 4;
            CHECK_EQ(RegQueryValueExA(k, w.n, NULL, &ty, (LPBYTE)&v, &len), ERROR_SUCCESS);
            CHECK_EQ(ty, REG_DWORD); CHECK_EQ(v, w.v);
        }
        RegCloseKey(k);
    }
    CHECK(RegOpenKeyExA(HKEY_LOCAL_MACHINE, base, 0, 0x20019, &k) != ERROR_SUCCESS);   /* not global yet */
    DM c = mode(1920, 1080);
    CHECK_EQ(ChangeDisplaySettingsA(&c, UPD | GLOB), OK);
    CHECK_EQ(RegOpenKeyExA(HKEY_LOCAL_MACHINE, base, 0, 0x20019, &k), ERROR_SUCCESS);
    { uint32_t v = 0; DWORD ty, len = 4; CHECK_EQ(RegQueryValueExA(k, "DefaultSettings.XResolution", NULL, &ty, (LPBYTE)&v, &len), ERROR_SUCCESS); CHECK_EQ(v, 1920); }
    RegCloseKey(k);
    dock(0);
    fnvnx_registry_reset_for_test();
    remove(hive);
}

TEST(display_change_message)
{
    dock(0);
    HWND a = topwin("DispA"), b = topwin("DispB");
    DM m = mode(1280, 720);
    g_msgs = 0;
    CHECK_EQ(ChangeDisplaySettingsA(&m, 0), OK);                          /* same mode: no change, no message */
    CHECK_EQ(ChangeDisplaySettingsA(&m, TST), OK);
    CHECK_EQ(ChangeDisplaySettingsA(NULL, 0), OK);
    CHECK_EQ(g_msgs, 0);
    CHECK_EQ(ChangeDisplaySettingsA(&m, RESET), OK);                      /* applied although unchanged: 2 windows */
    CHECK_EQ(g_msgs, 2);
    CHECK_EQ(g_last_w, 32); CHECK_EQ(g_last_l, (int)(1280 | (720 << 16)));
    g_msgs = 0;
    m = mode(1920, 1080);
    CHECK_EQ(ChangeDisplaySettingsA(&m, RESET), BADMODE);
    CHECK_EQ(g_msgs, 0);
    /* a dock change is a mode change: WM_DISPLAYCHANGE(bpp, MAKELONG(w, h)) to every top-level window */
    dock(1);
    CHECK_EQ(g_msgs, 2); CHECK_EQ(g_last_w, 32); CHECK_EQ(g_last_l, (int)(1920 | (1080 << 16)));
    g_msgs = 0;
    dock(0);
    CHECK_EQ(g_msgs, 2); CHECK_EQ(g_last_w, 32); CHECK_EQ(g_last_l, (int)(1280 | (720 << 16)));
    DestroyWindow(a); DestroyWindow(b);
}

TEST(display_metrics_table)
{
    const int CXMIN = 119 /* 91 + 4 * 7: Tahoma -13 px average width 7 */, MENUCHECK = 13 /* Tahoma -11 px: tmHeight 13 */;
    /* the expected table from the contract: Wine 9.0 reference window metrics; index -> value (W, H: the screen) */
    for (int d = 0; d <= 1; d++) {
        dock(d);
        int W = dw(d), H = dh(d), v[110];
        memset(v, 0, sizeof v);
        v[0] = W; v[1] = H; v[2] = 17; v[3] = 17; v[4] = 26; v[5] = 1; v[6] = 1; v[7] = 3; v[8] = 3; v[9] = 17; v[10] = 17;
        v[11] = 32; v[12] = 32; v[13] = 32; v[14] = 32; v[15] = 19; v[16] = W; v[17] = H + 18; v[19] = 1; v[20] = 17; v[21] = 17;
        v[28] = CXMIN; v[29] = 34; v[30] = 18; v[31] = 25; v[32] = 4; v[33] = 4; v[34] = CXMIN; v[35] = 34; v[36] = 4; v[37] = 4;
        v[38] = 75; v[39] = 75; v[43] = 3; v[45] = 2; v[46] = 2; v[47] = 160; v[48] = 31; v[49] = 16; v[50] = 16; v[51] = 18;
        v[52] = 17; v[53] = 17; v[54] = 18; v[55] = 18; v[56] = 8; v[57] = 160; v[58] = 31; v[59] = W + 12; v[60] = H + 12;
        v[61] = W + 8; v[62] = H + 52; v[63] = 3; v[68] = 4; v[69] = 4; v[71] = MENUCHECK; v[72] = MENUCHECK; v[75] = 1; v[78] = W; v[79] = H;
        v[80] = 1; v[81] = 1; v[83] = 1; v[84] = 1; v[91] = 1;
        for (int i = 0; i <= 100; i++) {
            CHECK_EQ(GetSystemMetrics(i), v[i]);
            if (GetSystemMetrics(i) != v[i]) fprintf(stderr, "  index %d\n", i);
        }
        CHECK_EQ(GetSystemMetrics(0x1000), 0);    /* SM_REMOTESESSION */
        CHECK_EQ(GetSystemMetrics(0x2000), 0);    /* SM_SHUTTINGDOWN */
        CHECK_EQ(GetSystemMetrics(0x2001), 0);    /* SM_REMOTECONTROL */
        CHECK_EQ(GetSystemMetrics(0x2002), 1);    /* SM_CARETBLINKINGENABLED */
        for (int i : { 0x1001, 0x1002, 0x1FFF, 0x2003, 0x2FFF, 0x3000, 101, 255, 4096 + 100, 0x7FFFFFFF, -1, -100, (int)0x80000000 })
            CHECK_EQ(GetSystemMetrics(i), 0);     /* an index Windows does not define */
        for (int i = 0x1003; i < 0x1100; i++) CHECK_EQ(GetSystemMetrics(i), 0);
        for (int i = 0x2003; i < 0x2100; i++) CHECK_EQ(GetSystemMetrics(i), 0);
    }
    /* SM_SWAPBUTTON follows SwapMouseButton */
    CHECK_EQ(GetSystemMetrics(23), 0);
    SwapMouseButton(TRUE); CHECK_EQ(GetSystemMetrics(23), 1);
    SwapMouseButton(FALSE); CHECK_EQ(GetSystemMetrics(23), 0);
    dock(0);
}

TEST(display_consistency_after_dock_change)
{
    HWND w = CreateWindowExA(0, "Static", "x", 0x90000000u, 0, 0, 0, 0, NULL, NULL, NULL, NULL);   /* a window must not disturb the state */
    int seq[] = { 1, 0, 0, 1, 1, 0 };
    for (int d : seq) {
        dock(d);
        int W = GetSystemMetrics(SM_CXSCREEN), H = GetSystemMetrics(SM_CYSCREEN);
        CHECK_EQ(W, dw(d)); CHECK_EQ(H, dh(d));
        CHECK_EQ(GetSystemMetrics(78), W); CHECK_EQ(GetSystemMetrics(79), H);
        CHECK_EQ(GetSystemMetrics(16), W);
        CHECK_EQ(GetSystemMetrics(80), 1);
        CHECK_EQ((int)nx_env_profile(), d);
        HDC dc = GetDC(NULL);
        CHECK_EQ(GetDeviceCaps(dc, 8), W); CHECK_EQ(GetDeviceCaps(dc, 10), H);
        ReleaseDC(NULL, dc);
        RECT r; GetWindowRect(GetDesktopWindow(), &r);
        CHECK_EQ(r.right, W); CHECK_EQ(r.bottom, H);
        DM m = mode(W, H); CHECK_EQ(ChangeDisplaySettingsA(&m, TST), OK);
        m = mode(dw(1 - d), dh(1 - d)); CHECK_EQ(ChangeDisplaySettingsA(&m, TST), BADMODE);
    }
    DestroyWindow(w);
    /* fnvnx_set_display_size (a host that knows the output size) feeds the same state */
    fnvnx_set_display_size(1920, 1080);
    CHECK_EQ((int)nx_env_profile(), 1); CHECK_EQ(GetSystemMetrics(SM_CXSCREEN), 1920);
    fnvnx_set_display_size(1280, 720);
    CHECK_EQ((int)nx_env_profile(), 0); CHECK_EQ(GetSystemMetrics(SM_CYSCREEN), 720);
    dock(0);
}
