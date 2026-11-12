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
#include "font_faces.h"

#include <stdint.h>
#include <stdio.h>
#include "fnvnx_render_hooks.h"

extern "C" {
void fnvnx_set_display_size(int w, int h);
int fnvnx_swap_buttons(void);                    /* window.cpp: SwapMouseButton's state */
void fnwin_desktop_resize(void);                 /* window.cpp: the desktop window takes the display's size */
void fnvnx_display_mode_changed(void);
LONG ChangeDisplaySettingsA(void *, DWORD);
BOOL EnumDisplayDevicesA(LPCSTR, DWORD, void *, DWORD);
int GetSystemMetrics(int);
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
HFONT CreateFontIndirectW(const void *);
BOOL GetTextMetricsW(HDC, void *);
BOOL GetTextExtentPoint32A(HDC, LPCSTR, int, SIZE *);
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

/* the render profile (declared once, in fnvnx_render_hooks.h): docked is 1080p, handheld 720p */
enum NxEnvProfile nx_env_profile(void) { return cur_docked() ? NX_ENV_DOCKED : NX_ENV_HANDHELD; }

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

/* ---- GetSystemMetrics: one table, index -> rule.
 * Source of every row: the reference's win32u get_system_metrics (wine-9.0 dlls/win32u/sysparams.c, "ref:") over the
 * reference registry (HKCU\Control Panel\Desktop\WindowMetrics of the reference dump, "reg:"; values absent there take the
 * reference's defaults, e.g. MinWidth 154, MinArrange ARW_HIDE, DragWidth 4). The metrics derived from a font (SM_CXMIN,
 * SM_CXMENUCHECK) are computed from the reference's fonts (Tahoma: CaptionFont 10 pt = -13 px, MenuFont 8 pt = -11 px, weight
 * 400, reg:) with the platform's own text metrics, by the reference's get_char_dimensions rule. Rows are ENV_TRUTH D1/I5. */
namespace {
/* reg: the reference's window metrics, in pixels (twips / -15) */
enum { BORDER = 1, CAPTION_W = 18, CAPTION_H = 25, MENU_W = 18, MENU_H = 18, SCROLL_W = 17, SCROLL_H = 17, SMCAP_W = 17,
       SMCAP_H = 17, ICON_SPACING = 75, MIN_WIDTH = 154, MIN_GAP = 0, DLGFRAME = 3, ICON = 32, SMICON = 16, DRAG = 4 };
int sm(int i);
/* ref SM_CXFRAME/SM_CYFRAME: SM_CXDLGFRAME + max(BorderWidth, 1) */
int sm_frame_x(void) { return DLGFRAME + (BORDER < 1 ? 1 : BORDER); }
/* ref SM_CYCAPTION: iCaptionHeight + 1 */
int sm_caption(void) { return CAPTION_H + 1; }
/* the font-derived metrics: the reference's get_text_metr_size over the platform's text metrics (cached: the fonts are fixed) */
struct fontm { int ok, avg, h, el; };
fontm g_fm[2];            /* 0: caption font (avg char width), 1: menu font (tmHeight, tmExternalLeading) */
volatile int g_fm_done;
os_lock_t g_fm_lock;
void font_metrics(void)
{
    if (__atomic_load_n(&g_fm_done, __ATOMIC_ACQUIRE)) return;
    os_lock(&g_fm_lock);
    if (!g_fm_done) {
        static const int heights[2] = { -13, -11 };
        HDC dc = CreateCompatibleDC(NULL);
        for (int k = 0; k < 2; k++) {
            int32_t lf[23];
            memset(lf, 0, sizeof lf);
            lf[0] = heights[k]; lf[4] = 400;
            ((uint8_t *)lf)[21] = 1;   /* lfCharSet = DEFAULT_CHARSET */
            const char *face = w32_face_name(FACE_TAHOMA);
            for (int c = 0; face[c]; c++) ((char16_t *)&lf[7])[c] = (char16_t)face[c];   /* LOGFONTW.lfFaceName */
            HFONT f = CreateFontIndirectW(lf);
            HGDIOBJ old = dc && f ? SelectObject(dc, f) : NULL;
            int32_t tm[15];
            SIZE sz;
            static const char abc[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
            if (old && GetTextMetricsW(dc, tm) && GetTextExtentPoint32A(dc, abc, 52, &sz)) {
                g_fm[k].ok = 1;
                g_fm[k].avg = ((int)sz.cx / 26 + 1) / 2;   /* ref get_char_dimensions */
                g_fm[k].h = tm[0]; g_fm[k].el = tm[4];
            }
            if (old) SelectObject(dc, old);
            if (f) DeleteObject(f);
        }
        if (dc) DeleteDC(dc);
        /* cached once the platform has its fonts; until then the reference's own fallback (tmHeight -1, width 10) answers */
        if (g_fm[0].ok && g_fm[1].ok) __atomic_store_n(&g_fm_done, 1, __ATOMIC_RELEASE);
    }
    os_unlock(&g_fm_lock);
}
/* ref SM_CXMIN: 3 * iCaptionWidth + iCaptionHeight + 4 * (average caption-font char width, 10 if unknown) + 2 * SM_CXFRAME + 4 */
int sm_cxmin(void)
{
    font_metrics();
    int ret = g_fm[0].ok && g_fm[0].avg ? g_fm[0].avg : 10;
    return 3 * CAPTION_W + CAPTION_H + 4 * ret + 2 * sm_frame_x() + 4;
}
/* ref SM_CYMIN: SM_CYCAPTION + 2 * SM_CYFRAME */
int sm_cymin(void) { return sm_caption() + 2 * sm_frame_x(); }
/* ref SM_CXMENUCHECK/SM_CYMENUCHECK: menu font tmHeight <= 0 ? 13 : ((tmHeight + tmExternalLeading + 1) / 2) * 2 - 1 */
int sm_menucheck(void)
{
    font_metrics();
    int h = g_fm[1].ok ? g_fm[1].h : -1;
    return h <= 0 ? 13 : ((h + g_fm[1].el + 1) / 2) * 2 - 1;
}
/* ref SM_CXMAXIMIZED / SM_CYMAXIMIZED (both are the reference's own formulas, FIXME there) */
int sm_cxmaximized(void) { return fnvnx_display_w() + 2 * sm_frame_x(); }
int sm_cymaximized(void) { return fnvnx_display_h() + 2 * sm_caption(); }
struct rule { int idx; int val; int (*fn)(void); };
#define K(i, v) { i, v, nullptr }
#define F(i, f) { i, 0, f }
const rule k_rules[] = {
    F(0, [] { return fnvnx_display_w(); }),                       /* SM_CXSCREEN: ref primary monitor rect; ENV D1 */
    F(1, [] { return fnvnx_display_h(); }),                       /* SM_CYSCREEN */
    K(2, SCROLL_W), K(3, SCROLL_H),                               /* SM_CXVSCROLL, SM_CYHSCROLL: ref max(ScrollWidth, 8); reg 17 */
    F(4, sm_caption),                                             /* SM_CYCAPTION: ref iCaptionHeight + 1; reg 25 */
    K(5, 1), K(6, 1),                                             /* SM_CXBORDER, SM_CYBORDER: ref always 1 */
    K(7, DLGFRAME), K(8, DLGFRAME),                               /* SM_CXDLGFRAME, SM_CYDLGFRAME: ref always 3 */
    K(9, SCROLL_H), K(10, SCROLL_H),                              /* SM_CYVTHUMB, SM_CXHTHUMB: ref max(ScrollHeight, 8); reg 17 */
    K(11, ICON), K(12, ICON),                                     /* SM_CXICON, SM_CYICON: ref map_to_dpi(32), 96 dpi */
    K(13, ICON), K(14, ICON),                                     /* SM_CXCURSOR, SM_CYCURSOR: ref 32 at 96 dpi */
    K(15, MENU_H + 1),                                            /* SM_CYMENU: ref iMenuHeight + 1; reg 18 */
    F(16, [] { return sm_cxmaximized() - 2 * sm_frame_x(); }),    /* SM_CXFULLSCREEN: ref SM_CXMAXIMIZED - 2 * SM_CXFRAME */
    F(17, [] { return sm_cymaximized() - sm_cymin(); }),          /* SM_CYFULLSCREEN: ref SM_CYMAXIMIZED - SM_CYMIN */
    K(18, 0),                                                     /* SM_CYKANJIWINDOW: ref 0 */
    K(19, 1),                                                     /* SM_MOUSEPRESENT: ref 1; ENV I5 */
    K(20, SCROLL_H), K(21, SCROLL_H),                             /* SM_CYVSCROLL, SM_CXHSCROLL: ref max(ScrollHeight, 8) */
    K(22, 0),                                                     /* SM_DEBUG: ref 0 */
    F(23, fnvnx_swap_buttons),                                    /* SM_SWAPBUTTON: ref MouseButtonSwap entry (SwapMouseButton) */
    K(24, 0), K(25, 0), K(26, 0), K(27, 0),                       /* SM_RESERVED1..4: ref 0 */
    F(28, sm_cxmin),                                              /* SM_CXMIN: ref, caption font */
    F(29, sm_cymin),                                              /* SM_CYMIN: ref SM_CYCAPTION + 2 * SM_CYFRAME */
    K(30, CAPTION_W), K(31, CAPTION_H),                           /* SM_CXSIZE: ref max(CaptionWidth, 8); SM_CYSIZE: iCaptionHeight */
    F(32, sm_frame_x), F(33, sm_frame_x),                         /* SM_CXSIZEFRAME, SM_CYSIZEFRAME: ref SM_CXDLGFRAME + max(Border, 1) */
    F(34, sm_cxmin), F(35, sm_cymin),                             /* SM_CXMINTRACK, SM_CYMINTRACK: ref = SM_CXMIN, SM_CYMIN */
    K(36, DRAG), K(37, DRAG),                                     /* SM_CXDOUBLECLK, SM_CYDOUBLECLK: ref DoubleClickWidth/Height 4 */
    K(38, ICON_SPACING), K(39, ICON_SPACING),                     /* SM_CXICONSPACING, SM_CYICONSPACING: reg IconSpacing -1125 twips */
    K(40, 0),                                                     /* SM_MENUDROPALIGNMENT: ref MenuDropAlignment entry, default 0 */
    K(41, 0),                                                     /* SM_PENWINDOWS: ref 0 */
    K(42, 0),                                                     /* SM_DBCSENABLED: ref ANSI code page max char size > 1; Latin: 0 */
    K(43, 3),                                                     /* SM_CMOUSEBUTTONS: ref 3 */
    K(44, 0),                                                     /* SM_SECURE: ref 0 */
    K(45, 2), K(46, 2),                                           /* SM_CXEDGE, SM_CYEDGE: ref SM_CXBORDER + 1 */
    F(47, [] { return sm(57) + MIN_GAP; }),                       /* SM_CXMINSPACING: ref SM_CXMINIMIZED + MinHorzGap */
    F(48, [] { return sm(58) + MIN_GAP; }),                       /* SM_CYMINSPACING: ref SM_CYMINIMIZED + MinVertGap */
    K(49, SMICON), K(50, SMICON),                                 /* SM_CXSMICON, SM_CYSMICON: ref map_to_dpi(16) & ~1 */
    K(51, SMCAP_H + 1),                                           /* SM_CYSMCAPTION: ref iSmCaptionHeight + 1; reg 17 */
    K(52, SMCAP_W), K(53, SMCAP_H),                               /* SM_CXSMSIZE: ref SmCaptionWidth; SM_CYSMSIZE: iSmCaptionHeight */
    K(54, MENU_W), K(55, MENU_H),                                 /* SM_CXMENUSIZE: ref MenuWidth; SM_CYMENUSIZE: iMenuHeight */
    K(56, 8),                                                     /* SM_ARRANGE: ref MinArrange, default ARW_HIDE */
    K(57, MIN_WIDTH + 6),                                         /* SM_CXMINIMIZED: ref MinWidth (154) + 6 */
    K(58, CAPTION_H + 6),                                         /* SM_CYMINIMIZED: ref iCaptionHeight + 6 */
    F(59, [] { return fnvnx_display_w() + 4 + 2 * sm_frame_x(); }), /* SM_CXMAXTRACK: ref SM_CXVIRTUALSCREEN + 4 + 2 * SM_CXFRAME */
    F(60, [] { return fnvnx_display_h() + 4 + 2 * sm_frame_x(); }), /* SM_CYMAXTRACK: ref SM_CYVIRTUALSCREEN + 4 + 2 * SM_CYFRAME */
    F(61, sm_cxmaximized),                                        /* SM_CXMAXIMIZED: ref SM_CXSCREEN + 2 * SM_CXFRAME */
    F(62, sm_cymaximized),                                        /* SM_CYMAXIMIZED: ref SM_CYSCREEN + 2 * SM_CYCAPTION */
    K(63, 3),                                                     /* SM_NETWORK: ref 3 */
    K(67, 0),                                                     /* SM_CLEANBOOT: ref 0 */
    K(68, DRAG), K(69, DRAG),                                     /* SM_CXDRAG, SM_CYDRAG: ref DragWidth/Height 4 */
    K(70, 0),                                                     /* SM_SHOWSOUNDS: ref ShowSounds entry, default 0 */
    F(71, sm_menucheck), F(72, sm_menucheck),                     /* SM_CXMENUCHECK, SM_CYMENUCHECK: ref, menu font */
    K(73, 0), K(74, 0),                                           /* SM_SLOWMACHINE, SM_MIDEASTENABLED: ref 0 */
    K(75, 1),                                                     /* SM_MOUSEWHEELPRESENT: ref 1 */
    K(76, 0), K(77, 0),                                           /* SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN: ref virtual rect origin */
    F(78, [] { return fnvnx_display_w(); }),                      /* SM_CXVIRTUALSCREEN: one monitor (ENV D1) */
    F(79, [] { return fnvnx_display_h(); }),                      /* SM_CYVIRTUALSCREEN */
    K(80, 1), K(81, 1),                                           /* SM_CMONITORS: active monitors (1); SM_SAMEDISPLAYFORMAT: ref 1 */
    K(82, 0),                                                     /* SM_IMMENABLED: ref 0; ENV: no IME */
    K(83, 1), K(84, 1),                                           /* SM_CXFOCUSBORDER, SM_CYFOCUSBORDER: ref 1 */
    K(86, 0), K(87, 0), K(88, 0), K(89, 0),                       /* SM_TABLETPC, SM_MEDIACENTER: ref 0; SM_STARTER, SM_SERVERR2: 0 */
    K(91, 1),                                                     /* SM_MOUSEHORIZONTALWHEELPRESENT: MSDN (not in the reference) */
    K(92, 0),                                                     /* SM_CXPADDEDBORDER: reg PaddedBorderWidth 0 */
    K(94, 0), K(95, 0),                                           /* SM_DIGITIZER, SM_MAXIMUMTOUCHES: ref default 0; ENV I4/I5 */
    K(0x1000, 0),                                                 /* SM_REMOTESESSION: ref default 0 */
    K(0x2000, 0),                                                 /* SM_SHUTTINGDOWN: ref default 0 */
    K(0x2001, 0),                                                 /* SM_REMOTECONTROL: ref default 0 */
    K(0x2002, 1),                                                 /* SM_CARETBLINKINGENABLED: MSDN (the reference answers 0) */
};
#undef K
#undef F
int sm(int i)
{
    for (const rule &r : k_rules)
        if (r.idx == i)
            return r.fn ? r.fn() : r.val;
    return 0;   /* an index Windows does not define: ref default */
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
        d->flags = 0x1 /* ATTACHED_TO_DESKTOP */ | 0x4 /* PRIMARY_DEVICE */ | 0x10 /* VGA_COMPATIBLE: ref default adapter flags */;
        strcpy(d->id, "PCI\\VEN_10DE&DEV_0391&SUBSYS_00000000&REV_00");
        strcpy(d->key, "\\Registry\\Machine\\" VIDEO_KEY);
        return TRUE;
    }
    if (w32_stricmp(dev, ADAPTER_NAME)) return FALSE;
    strcpy(d->name, ADAPTER_NAME "\\Monitor0");
    strcpy(d->string, "Generic Non-PnP Monitor"   /* ref: device_desc of a monitor without EDID */);
    d->flags = 0x1 /* ACTIVE */ | 0x2 /* ATTACHED */;
    if (flags & 0x1)   /* EDD_GET_DEVICE_INTERFACE_NAME: GUID_DEVINTERFACE_MONITOR */
        strcpy(d->id, "\\\\?\\DISPLAY#Default_Monitor#0000&0000#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}");
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
