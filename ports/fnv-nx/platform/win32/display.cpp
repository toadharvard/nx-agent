/* display.cpp - the display of the environment (ENV_TRUTH D1): one source of truth and the user32 imports that report it.
 *
 * Model: the environment has ONE adapter with ONE monitor (\\.\DISPLAY1), and its mode set is exactly two modes:
 * 1280x720x32 @60 (handheld panel) and 1920x1080x32 @60 (docked TV output). Which one is current is a fact of the
 * console, reported by the OS layer (os_display_docked). The state below is that one bit; everything else is a
 * function of it: GetSystemMetrics (one table, index -> rule), GetDeviceCaps and the desktop window (they read
 * fnvnx_display_w/h), EnumDisplayDevicesA, ChangeDisplaySettingsA and the render profile (nx_env_profile).
 * The state is read from the OS layer on first use and again by fnvnx_display_mode_changed (the app-lifecycle
 * code calls it when the console is docked or undocked); fnvnx_set_display_size (hosts and tests that know the
 * output size) sets it directly: the output is docked iff it has more pixels than the handheld panel, which is exact
 * on the two-mode set.
 * ChangeDisplaySettingsA accepts a mode iff it equals the mode the output currently provides (the console cannot
 * switch its output on request), so it never changes the mode; the settings it persists are therefore always the
 * current mode. Registry layout (the reference's, Wine): the adapter's video key, values DefaultSettings.*.
 * WM_DISPLAYCHANGE goes to the top-level windows when the mode changes (dock change) and when CDS_RESET asks for
 * the settings to be applied although they are unchanged.
 * The metrics table values are the reference environment's (Wine 9.0 prefix, HKCU\Control Panel\Desktop\WindowMetrics:
 * BorderWidth 1, CaptionWidth 18, CaptionHeight 25, MenuWidth/Height 18, ScrollWidth/Height 17, SmCaptionWidth/Height
 * 17, PaddedBorderWidth 0, IconSpacing 75 px) and Wine's rules for what is derived from them. */
#include "window_int.h"

#include <stdint.h>
#include <stdio.h>

extern "C" {
void fnvnx_set_display_size(int w, int h);
int fnvnx_swap_buttons(void);                    /* window.cpp: SwapMouseButton's state */
void fnwin_desktop_resize(void);                 /* window.cpp: the desktop window takes the display's size */
void fnvnx_display_mode_changed(void);
LONG ChangeDisplaySettingsA(void *, DWORD);
BOOL EnumDisplayDevicesA(LPCSTR, DWORD, void *, DWORD);
int GetSystemMetrics(int);
}

/* the two modes of the mode set; index = docked */
struct dmode { int w, h; };
static const dmode k_modes[2] = { { 1280, 720 }, { 1920, 1080 } };
enum { MODE_BPP = 32, MODE_HZ = 60 };

static volatile int g_docked = -1;   /* -1: not read yet */
static int cur_docked(void)
{
    int d = __atomic_load_n(&g_docked, __ATOMIC_ACQUIRE);
    if (d < 0) {
        int expect = -1;
        d = os_display_docked() ? 1 : 0;
        if (!__atomic_compare_exchange_n(&g_docked, &expect, d, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
            d = expect;
    }
    return d;
}
extern "C" int fnvnx_display_w(void) { return k_modes[cur_docked()].w; }
extern "C" int fnvnx_display_h(void) { return k_modes[cur_docked()].h; }

/* the render profile (render/d3d9/dev/d3d9_adapter.h: `enum NxEnvProfile { NX_ENV_HANDHELD = 0, NX_ENV_DOCKED = 1 };
 * NxEnvProfile nx_env_profile();`, C++ linkage; an enum without a fixed type returns in the same register as int,
 * and the mangled name does not contain the return type, so this definition is the renderer's symbol) */
int nx_env_profile(void) { return cur_docked(); }

extern "C" void fnvnx_set_display_size(int w, int h)
{
    __atomic_store_n(&g_docked, (int64_t)w * h > (int64_t)k_modes[0].w * k_modes[0].h ? 1 : 0, __ATOMIC_RELEASE);
    fnwin_desktop_resize();
}
/* the console was docked or undocked: the state is re-read from the OS layer and the windows are told */
extern "C" void fnvnx_display_mode_changed(void)
{
    __atomic_store_n(&g_docked, os_display_docked() ? 1 : 0, __ATOMIC_RELEASE);
    fnvnx_app_display_change(fnvnx_display_w(), fnvnx_display_h());   /* sets the desktop and sends WM_DISPLAYCHANGE */
}

/* ---- GetSystemMetrics: one table, index -> rule */
namespace {
/* the reference's window metrics, in pixels */
enum { BORDER = 1, CAPTION_W = 18, CAPTION_H = 25, MENU_W = 18, MENU_H = 18, SCROLL_W = 17, SCROLL_H = 17, SMCAP_W = 17,
       SMCAP_H = 17, PADDED = 0, ICON_SPACING = 75, DLGFRAME = 3, ICON = 32, SMICON = 16, DRAG = 4 };
int sm(int i);
int sm_frame_x(void) { return BORDER + DLGFRAME + PADDED; }                  /* SM_CXFRAME: the sizing border */
int sm_caption(void) { return CAPTION_H + 1; }                               /* SM_CYCAPTION */
struct rule { int idx; int val; int (*fn)(void); };
#define K(i, v) { i, v, nullptr }
#define F(i, f) { i, 0, f }
const rule k_rules[] = {
    F(0, [] { return fnvnx_display_w(); }),                       /* SM_CXSCREEN */
    F(1, [] { return fnvnx_display_h(); }),                       /* SM_CYSCREEN */
    K(2, SCROLL_W), K(3, SCROLL_H),                               /* SM_CXVSCROLL, SM_CYHSCROLL */
    F(4, sm_caption),                                             /* SM_CYCAPTION */
    K(5, BORDER), K(6, BORDER),                                   /* SM_CXBORDER, SM_CYBORDER */
    K(7, DLGFRAME), K(8, DLGFRAME),                               /* SM_CXDLGFRAME, SM_CYDLGFRAME */
    K(9, SCROLL_H), K(10, SCROLL_W),                              /* SM_CYVTHUMB, SM_CXHTHUMB */
    K(11, ICON), K(12, ICON), K(13, ICON), K(14, ICON),           /* SM_CXICON, SM_CYICON, SM_CXCURSOR, SM_CYCURSOR */
    K(15, MENU_H + 1),                                            /* SM_CYMENU */
    F(16, [] { return fnvnx_display_w(); }),                      /* SM_CXFULLSCREEN */
    F(17, [] { return fnvnx_display_h() - sm_caption(); }),       /* SM_CYFULLSCREEN: without the caption */
    K(18, 0),                                                     /* SM_CYKANJIWINDOW */
    K(19, 1),                                                     /* SM_MOUSEPRESENT (ENV I5) */
    K(20, SCROLL_H), K(21, SCROLL_W),                             /* SM_CYVSCROLL, SM_CXHSCROLL */
    K(22, 0),                                                     /* SM_DEBUG */
    F(23, fnvnx_swap_buttons),                                    /* SM_SWAPBUTTON */
    K(24, 0), K(25, 0), K(26, 0), K(27, 0),                       /* SM_RESERVED1..4 */
    F(28, [] { return 3 * CAPTION_W + 2 * sm_frame_x(); }),       /* SM_CXMIN */
    F(29, [] { return sm_caption() + 2 * sm_frame_x(); }),        /* SM_CYMIN */
    K(30, CAPTION_W), K(31, CAPTION_H),                           /* SM_CXSIZE, SM_CYSIZE */
    F(32, sm_frame_x), F(33, sm_frame_x),                         /* SM_CXSIZEFRAME, SM_CYSIZEFRAME */
    F(34, [] { return sm(28); }), F(35, [] { return sm(29); }),   /* SM_CXMINTRACK, SM_CYMINTRACK */
    K(36, DRAG), K(37, DRAG),                                     /* SM_CXDOUBLECLK, SM_CYDOUBLECLK */
    K(38, ICON_SPACING), K(39, ICON_SPACING),                     /* SM_CXICONSPACING, SM_CYICONSPACING */
    K(40, 0), K(41, 0), K(42, 0),                                 /* SM_MENUDROPALIGNMENT, SM_PENWINDOWS, SM_DBCSENABLED */
    K(43, 3),                                                     /* SM_CMOUSEBUTTONS */
    K(44, 0),                                                     /* SM_SECURE */
    K(45, 2 * BORDER), K(46, 2 * BORDER),                         /* SM_CXEDGE, SM_CYEDGE */
    F(47, [] { return sm(57); }), F(48, [] { return sm(58); }),   /* SM_CXMINSPACING, SM_CYMINSPACING */
    K(49, SMICON), K(50, SMICON),                                 /* SM_CXSMICON, SM_CYSMICON */
    K(51, SMCAP_H + 1),                                           /* SM_CYSMCAPTION */
    K(52, SMCAP_W), K(53, SMCAP_H),                               /* SM_CXSMSIZE, SM_CYSMSIZE */
    K(54, MENU_W), K(55, MENU_H),                                 /* SM_CXMENUSIZE, SM_CYMENUSIZE */
    K(56, 8),                                                     /* SM_ARRANGE: ARW_HIDE */
    K(57, 160), K(58, 28),                                        /* SM_CXMINIMIZED, SM_CYMINIMIZED */
    F(59, [] { return fnvnx_display_w() + 2 * sm_frame_x(); }),   /* SM_CXMAXTRACK */
    F(60, [] { return fnvnx_display_h() + 2 * sm_frame_x(); }),   /* SM_CYMAXTRACK */
    F(61, [] { return fnvnx_display_w() + 2 * sm_frame_x(); }),   /* SM_CXMAXIMIZED */
    F(62, [] { return fnvnx_display_h() + 2 * sm_frame_x(); }),   /* SM_CYMAXIMIZED */
    K(63, 3),                                                     /* SM_NETWORK: the reference reports bit 0 and bit 1 */
    K(67, 0),                                                     /* SM_CLEANBOOT: normal boot */
    K(68, DRAG), K(69, DRAG),                                     /* SM_CXDRAG, SM_CYDRAG */
    K(70, 0),                                                     /* SM_SHOWSOUNDS */
    K(71, 13), K(72, 13),                                         /* SM_CXMENUCHECK, SM_CYMENUCHECK */
    K(73, 0), K(74, 0),                                           /* SM_SLOWMACHINE, SM_MIDEASTENABLED */
    K(75, 1),                                                     /* SM_MOUSEWHEELPRESENT */
    K(76, 0), K(77, 0),                                           /* SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN */
    F(78, [] { return fnvnx_display_w(); }),                      /* SM_CXVIRTUALSCREEN */
    F(79, [] { return fnvnx_display_h(); }),                      /* SM_CYVIRTUALSCREEN */
    K(80, 1), K(81, 1),                                           /* SM_CMONITORS, SM_SAMEDISPLAYFORMAT */
    K(82, 0),                                                     /* SM_IMMENABLED: no IME (the system keyboard types) */
    K(83, 1), K(84, 1),                                           /* SM_CXFOCUSBORDER, SM_CYFOCUSBORDER */
    K(86, 0), K(87, 0), K(88, 0), K(89, 0),                       /* SM_TABLETPC, SM_MEDIACENTER, SM_STARTER, SM_SERVERR2 */
    K(91, 1),                                                     /* SM_MOUSEHORIZONTALWHEELPRESENT */
    K(92, PADDED),                                                /* SM_CXPADDEDBORDER */
    K(94, 0), K(95, 0),                                           /* SM_DIGITIZER, SM_MAXIMUMTOUCHES: no digitizer input */
    K(0x1000, 0),                                                 /* SM_REMOTESESSION */
    K(0x2000, 0),                                                 /* SM_SHUTTINGDOWN */
    K(0x2001, 0),                                                 /* SM_REMOTECONTROL */
    K(0x2002, 1),                                                 /* SM_CARETBLINKINGENABLED */
};
#undef K
#undef F
int sm(int i)
{
    for (const rule &r : k_rules)
        if (r.idx == i)
            return r.fn ? r.fn() : r.val;
    return 0;   /* an index Windows does not define */
}
} // namespace

extern "C" int GetSystemMetrics(int i) { return sm(i); }

/* ---- EnumDisplayDevicesA */
namespace {
/* DISPLAY_DEVICEA with 32-bit fields (the game's layout) */
struct dispdev { uint32_t cb; char name[32], string[128]; uint32_t flags; char id[128], key[128]; };
#define VIDEO_GUID "{6A8D3C52-0F4E-4B7A-9C11-5E2B7D90A4C3}"
#define VIDEO_KEY "System\\CurrentControlSet\\Control\\Video\\" VIDEO_GUID "\\0000"
#define ADAPTER_NAME "\\\\.\\DISPLAY1"
#define MONITOR_CLASS "{4d36e96e-e325-11ce-bfc1-08002be10318}"
} // namespace
extern "C" BOOL EnumDisplayDevicesA(LPCSTR dev, DWORD i, void *out, DWORD flags)
{
    dispdev *d = (dispdev *)out;
    if (!d || d->cb != sizeof *d) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    if (i != 0) return FALSE;   /* one adapter, one monitor */
    uint32_t cb = d->cb;
    memset(d, 0, sizeof *d);
    d->cb = cb;
    if (!dev) {
        strcpy(d->name, ADAPTER_NAME);
        strcpy(d->string, "NVIDIA Tegra X1");
        d->flags = 0x1 /* ATTACHED_TO_DESKTOP */ | 0x4 /* PRIMARY_DEVICE */;
        strcpy(d->id, "PCI\\VEN_10DE&DEV_0391&SUBSYS_00000000&REV_00");
        strcpy(d->key, "\\Registry\\Machine\\" VIDEO_KEY);
        return TRUE;
    }
    if (w32_stricmp(dev, ADAPTER_NAME)) return FALSE;
    strcpy(d->name, ADAPTER_NAME "\\Monitor0");
    strcpy(d->string, "Generic PnP Monitor");
    d->flags = 0x1 /* ACTIVE */ | 0x2 /* ATTACHED */;
    if (flags & 0x1)   /* EDD_GET_DEVICE_INTERFACE_NAME: GUID_DEVINTERFACE_MONITOR */
        strcpy(d->id, "\\\\?\\DISPLAY#Default_Monitor#4&fnvnx&0&UID0#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}");
    else
        strcpy(d->id, "MONITOR\\Default_Monitor\\" MONITOR_CLASS "\\0000");
    strcpy(d->key, "\\Registry\\Machine\\System\\CurrentControlSet\\Control\\Class\\" MONITOR_CLASS "\\0000");
    return TRUE;
}

/* ---- ChangeDisplaySettingsA */
namespace {
enum { CDS_UPDATEREGISTRY = 0x1, CDS_TEST = 0x2, CDS_FULLSCREEN = 0x4, CDS_GLOBAL = 0x8, CDS_SET_PRIMARY = 0x10,
       CDS_VIDEOPARAMETERS = 0x20, CDS_ENABLE_UNSAFE_MODES = 0x100, CDS_DISABLE_UNSAFE_MODES = 0x200,
       CDS_RESET_EX = 0x20000000, CDS_NORESET = 0x10000000, CDS_RESET = 0x40000000,
       CDS_KNOWN = 0x7000033F };
enum { SUCCESSFUL = 0, RESTART = 1, FAILED = -1, BADMODE = -2, NOTUPDATED = -3, BADFLAGS = -4, BADPARAM = -5 };
enum { DM_POSITION = 0x20, DM_DISPLAYORIENTATION = 0x80, DM_DISPLAYFIXEDOUTPUT = 0x20000, DM_BITSPERPEL = 0x40000,
       DM_PELSWIDTH = 0x80000, DM_PELSHEIGHT = 0x100000, DM_DISPLAYFLAGS = 0x200000, DM_DISPLAYFREQUENCY = 0x400000 };
/* DEVMODEA offsets (bytes): dmSize 36, dmFields 40, dmPosition 44, dmDisplayOrientation 52, dmDisplayFixedOutput 56,
 * dmBitsPerPel 104, dmPelsWidth 108, dmPelsHeight 112, dmDisplayFlags 116, dmDisplayFrequency 120 */
struct dmfield { DWORD bit; unsigned off, len; };
const dmfield k_dmfields[] = { { DM_POSITION, 44, 8 }, { DM_DISPLAYORIENTATION, 52, 4 }, { DM_DISPLAYFIXEDOUTPUT, 56, 4 },
    { DM_BITSPERPEL, 104, 4 }, { DM_PELSWIDTH, 108, 4 }, { DM_PELSHEIGHT, 112, 4 }, { DM_DISPLAYFLAGS, 116, 4 },
    { DM_DISPLAYFREQUENCY, 120, 4 } };
uint32_t rd32(const uint8_t *p, unsigned off) { uint32_t v; memcpy(&v, p + off, 4); return v; }

/* the settings are kept per user (HKCU) or, with CDS_GLOBAL, for all users (HKLM): the adapter's video key */
bool persist(HKEY root)
{
    HKEY k;
    if (RegCreateKeyExA(root, VIDEO_KEY, 0, NULL, 0, 0xF003F, NULL, &k, NULL) != ERROR_SUCCESS) return false;
    const struct { const char *name; uint32_t v; } vals[] = {
        { "DefaultSettings.BitsPerPel", MODE_BPP }, { "DefaultSettings.XResolution", (uint32_t)fnvnx_display_w() },
        { "DefaultSettings.YResolution", (uint32_t)fnvnx_display_h() }, { "DefaultSettings.VRefresh", MODE_HZ },
        { "DefaultSettings.Flags", 0 }, { "DefaultSettings.XPanning", 0 }, { "DefaultSettings.YPanning", 0 },
        { "DefaultSettings.Orientation", 0 }, { "DefaultSettings.FixedOutput", 0 } };
    bool ok = true;
    for (const auto &v : vals)
        ok = ok && RegSetValueExA(k, v.name, 0, REG_DWORD, (const BYTE *)&v.v, 4) == ERROR_SUCCESS;
    return RegCloseKey(k) == ERROR_SUCCESS && ok;
}
} // namespace

extern "C" LONG ChangeDisplaySettingsA(void *devmode, DWORD flags)
{
    flags = W32_DW(flags);
    if ((flags & ~(DWORD)CDS_KNOWN) ||
        ((flags & CDS_GLOBAL) && !(flags & CDS_UPDATEREGISTRY)) ||                 /* all-users needs the registry */
        ((flags & CDS_NORESET) && !(flags & CDS_UPDATEREGISTRY)) ||                /* "not applied" is a registry-only flag */
        ((flags & CDS_NORESET) && (flags & (CDS_RESET | CDS_RESET_EX))) ||
        ((flags & CDS_UPDATEREGISTRY) && (flags & CDS_FULLSCREEN)) ||              /* persistent vs temporary */
        ((flags & CDS_ENABLE_UNSAFE_MODES) && (flags & CDS_DISABLE_UNSAFE_MODES)))
        return BADFLAGS;
    if (flags & CDS_VIDEOPARAMETERS) return BADPARAM;   /* its VIDEOPARAMETERS arrive in the Ex function's lParam */
    if (devmode) {
        const uint8_t *dm = (const uint8_t *)devmode;
        unsigned size = dm[36] | (dm[37] << 8);
        if (size < 44) return BADPARAM;
        DWORD fields = rd32(dm, 40);
        for (const dmfield &f : k_dmfields)
            if ((fields & f.bit) && size < f.off + f.len) return BADPARAM;   /* a field the structure does not hold */
        /* a field is accepted iff it names what the output provides now; fields not set keep the current value */
        bool ok = true;
        if (fields & DM_PELSWIDTH) ok = ok && rd32(dm, 108) == (uint32_t)fnvnx_display_w();
        if (fields & DM_PELSHEIGHT) ok = ok && rd32(dm, 112) == (uint32_t)fnvnx_display_h();
        if (fields & DM_BITSPERPEL) ok = ok && rd32(dm, 104) == MODE_BPP;
        if (fields & DM_DISPLAYFREQUENCY) { uint32_t f = rd32(dm, 120); ok = ok && (f == MODE_HZ || f <= 1); }   /* 0, 1: the default */
        if (fields & DM_DISPLAYFLAGS) ok = ok && rd32(dm, 116) == 0;                      /* neither interlaced nor grayscale */
        if (fields & DM_POSITION) ok = ok && rd32(dm, 44) == 0 && rd32(dm, 48) == 0;     /* the primary monitor is at the origin */
        if (fields & DM_DISPLAYORIENTATION) {
            uint32_t o = rd32(dm, 52);
            if (o > 3) return BADPARAM;
            ok = ok && o == 0;                                                            /* the panel does not rotate */
        }
        if (fields & DM_DISPLAYFIXEDOUTPUT) {
            if (rd32(dm, 56) > 2) return BADPARAM;   /* stretch / center only matter for a lower mode: none exists */
        }
        if (!ok) return BADMODE;
    }
    if (flags & CDS_TEST) return SUCCESSFUL;
    if ((flags & CDS_UPDATEREGISTRY) && devmode) {
        if (!persist(HKEY_CURRENT_USER) || ((flags & CDS_GLOBAL) && !persist(HKEY_LOCAL_MACHINE))) return NOTUPDATED;
    }
    if (flags & (CDS_RESET | CDS_RESET_EX))   /* applied although unchanged */
        fnvnx_app_display_change(fnvnx_display_w(), fnvnx_display_h());
    return SUCCESSFUL;
}
