/* gdifont.cpp - GDI fonts, stage 1: font objects and the stock fonts, the face list, font matching, text metrics,
 * charset translation and GetGlyphOutline. The results equal the reference environment's (Wine 9.0 on Ubuntu 24.04)
 * by construction: the same rasteriser (FreeType 2.13.2 with Debian's patches, ext/freetype via
 * tools/fetch_freetype.sh), the same font files, and Wine's own rules (own code following them) for the face list
 * (family/face ordering, bitmap sizes, signatures), the LOGFONT -> face match, the realised size, the metrics and the
 * GetGlyphOutline layouts. Layout of the structures is the guest's fixed 32-bit one (byte offsets, LOGFONTW = 92,
 * TEXTMETRICW = 60, GLYPHMETRICS = 20, CHARSETINFO = 32 bytes).
 *
 * Fonts come from three directories that the root declares (fnvnx_declared_fonts, fnvnx_win32_ext.h; no setter): the Wine fonts directory (the .fon
 * bitmap fonts of the registry and system lists, the TrueType files), the host fonts (every sfnt file below it,
 * what fontconfig lists in the reference) and the registry export directory (FontSubstitutes, Fonts, the hardware
 * profile Fonts key, Wine\Fonts\Replacements, CodePage\ACP, as .reg files).
 *
 * Stage 1 assumptions (stated, not hidden): display DC at 96 dpi, MM_TEXT, identity world transform; system locale
 * en-US (name language 0x409); TC_RA_ABLE (bitmap fonts allowed); no vertical (@) families; no FontLink, no
 * fontconfig-derived family fallbacks beyond Wine's three built-in names per family class. */
#include "w32.h"
#include "gdi_int.h"
#include "font_faces.h"

#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_TRUETYPE_TABLES_H
#include FT_SFNT_NAMES_H
#include FT_WINFONTS_H
#include FT_OUTLINE_H
#include FT_GLYPH_H
#include FT_TRIGONOMETRY_H
#include FT_TRUETYPE_IDS_H
#include FT_MODULE_H

#include <algorithm>
#include <map>
#include <math.h>
#include <ctype.h>
#include <string>
#include <strings.h>
#include <vector>

typedef std::u16string ustr;

namespace {
/* ---- structures with the guest layout */
struct LF { int32_t h, w, esc, orient, weight; uint8_t it, ul, so, cs, op, cp, q, pf; char16_t face[32]; };
struct TMW { int32_t h, asc, desc, il, el, aw, mw, wt, oh, ax, ay; char16_t fc, lc, dc, bc; uint8_t it, ul, so, pf, cs; };
struct FSig { uint32_t usb[4], csb[2]; };
struct CSI { uint32_t cs, acp; FSig fs; };
static_assert(sizeof(LF) == 92 && sizeof(TMW) == 60 && sizeof(CSI) == 32, "guest layouts");

enum { NTM_ITALIC = 1, NTM_BOLD = 0x20, NTM_REGULAR = 0x40, NTM_PS_OPENTYPE = 0x20000 };
enum { FS_LATIN1 = 1, FS_SYMBOL = 0x80000000u };
enum { ANSI_CS = 0, DEFAULT_CS = 1, SYMBOL_CS = 2, OEM_CS = 255 };
const uint32_t GERR = 0xFFFFFFFFu;

const CSI charset_info[] = {
    { 0, 1252, { {0}, { 1, 0 } } }, { 238, 1250, { {0}, { 2, 0 } } }, { 204, 1251, { {0}, { 4, 0 } } }, { 161, 1253, { {0}, { 8, 0 } } },
    { 162, 1254, { {0}, { 0x10, 0 } } }, { 177, 1255, { {0}, { 0x20, 0 } } }, { 178, 1256, { {0}, { 0x40, 0 } } },
    { 186, 1257, { {0}, { 0x80, 0 } } }, { 163, 1258, { {0}, { 0x100, 0 } } }, { 222, 874, { {0}, { 0x10000, 0 } } },
    { 128, 932, { {0}, { 0x20000, 0 } } }, { 134, 936, { {0}, { 0x40000, 0 } } }, { 129, 949, { {0}, { 0x80000, 0 } } },
    { 136, 950, { {0}, { 0x100000, 0 } } }, { 130, 1361, { {0}, { 0x200000, 0 } } }, { 254, 65001, { {0}, { 0x04000000, 0 } } },
    { 2, 42, { {0}, { FS_SYMBOL, 0 } } } };

/* TranslateCharsetInfo: src is the charset / code page value, or the address of the fsCsb word */
bool translate_charset(uintptr_t src, CSI *cs, uint32_t flags)
{
    for (const CSI &c : charset_info) {
        bool hit = flags == 1 ? (uint32_t)src == c.cs : flags == 2 ? (uint32_t)src == c.acp
                 : flags == 3 ? (c.fs.csb[0] & *(const uint32_t *)src) != 0 : false;
        if (hit) { *cs = c; return true; }
    }
    return false;
}

/* ---- names */
char16_t lc(char16_t c) { return c < 0x80 ? (c >= 'A' && c <= 'Z' ? c + 32 : c) : (c >= 0xC0 && c <= 0xDE && c != 0xD7 ? c + 32 : c); }
int fcmp(const ustr &a, const ustr &b, size_t n = (size_t)-1)
{
    for (size_t i = 0; i < n; i++) {
        char16_t x = i < a.size() ? lc(a[i]) : 0, y = i < b.size() ? lc(b[i]) : 0;
        if (x != y) return x < y ? -1 : 1;
        if (!x) break;
    }
    return 0;
}
ustr widen(const char *s) { ustr r; while (*s) r += (char16_t)(uint8_t)*s++; return r; }
std::string narrow(const ustr &s) { std::string r; for (char16_t c : s) r += c < 0x100 ? (char)c : '?'; return r; }

/* ---- the face list */
struct Family;
struct Face {
    ustr style, full; std::string file; int index = 0; bool scalable = true; uint32_t ntm = 0, version = 0; FSig fs = {};
    int sh = 0, sw = 0, x_ppem = 0, y_ppem = 0, il = 0; Family *fam = nullptr;   /* bitmap strike: height, width, ppem (26.6), internal leading */
};
struct Family { ustr name, second; std::vector<Face *> faces; };

std::string g_wine_dir;   /* copy of fnvnx_declared_fonts()->dir, made once by load_all */
std::vector<Family *> g_fam;
bool g_loaded;
FT_Library g_lib;
std::map<std::string, std::vector<std::pair<std::string, std::string>>> g_reg;   /* lower-case key path -> (value name, REG_SZ data) */
std::vector<std::pair<std::string, std::string>> g_subst;                           /* from[,cs] -> to[,cs] in file order */
ustr g_def_roman, g_def_modern, g_def_swiss;
int g_acp = 1252;
os_lock_t g_flock;
struct FL { FL() { os_lock(&g_flock); } ~FL() { os_unlock(&g_flock); } };

int namecmp(const ustr &a, const ustr &b)
{
    auto prio = [](const ustr &s) { return !fcmp(s, g_def_swiss, 31) ? 0 : !fcmp(s, g_def_modern, 31) ? 1 : !fcmp(s, g_def_roman, 31) ? 2 : 3; };
    int pa = prio(a), pb = prio(b), va = !a.empty() && a[0] == '@', vb = !b.empty() && b[0] == '@';
    if (pa != pb) return pa - pb;
    if (va != vb) return va - vb;
    return fcmp(a.substr(va), b.substr(vb), 31);
}
Family *find_family(const ustr &n) { for (Family *f : g_fam) if (!namecmp(n, f->name)) return f; return nullptr; }
Family *find_family_any(const ustr &n)
{
    if (Family *f = find_family(n)) return f;
    for (Family *f : g_fam) if (!f->second.empty() && !namecmp(n, f->second)) return f;
    return nullptr;
}
int style_order(const Face *f)
{
    switch (f->ntm & (NTM_REGULAR | NTM_BOLD | NTM_ITALIC)) { case NTM_REGULAR: return 0; case NTM_BOLD: return 1; case NTM_ITALIC: return 2; case NTM_BOLD | NTM_ITALIC: return 3; }
    return 9999;
}
bool faces_equal(const Face *a, const Face *b)
{
    if (fcmp(a->full, b->full)) return false;
    if (a->scalable) return true;
    return a->y_ppem == b->y_ppem && !memcmp(&a->fs, &b->fs, sizeof a->fs);
}
void add_face(const ustr &fam, const ustr &second, Face *f)
{
    Family *F = find_family(fam);
    if (!F) { F = new Family; F->name = fam; F->second = second; g_fam.push_back(F); }
    size_t at = F->faces.size();
    for (size_t i = 0; i < F->faces.size(); i++) {
        Face *c = F->faces[i];
        if (faces_equal(f, c)) {
            if (f->file == c->file || f->version <= c->version) { delete f; return; }   /* already there / the original is newer */
            F->faces[i] = f; f->fam = F; delete c; return;
        }
        if (style_order(f) < style_order(c)) { at = i; break; }
    }
    f->fam = F;
    F->faces.insert(F->faces.begin() + at, f);
}

/* ---- sfnt name table: Wine's language preference (en-US as the system language) */
int name_score(const FT_SfntName &n)
{
    int res = 0, lang;
    switch (n.platform_id) {
    case TT_PLATFORM_MICROSOFT:
        if (n.encoding_id != TT_MS_ID_UNICODE_CS && n.encoding_id != TT_MS_ID_SYMBOL_CS) return 0;
        res = 5; lang = n.language_id; break;
    case TT_PLATFORM_MACINTOSH: if (n.language_id != 0) return 0; lang = 0x409; break;   /* Mac English only; other Mac languages are not mapped */
    case TT_PLATFORM_APPLE_UNICODE: if (n.language_id != 0) return 0; res = 2; lang = 0x409; break;
    default: return 0;
    }
    return res + (lang == 0x409 ? 30 : (lang & 0x3ff) == 9 ? 20 : 0);
}
ustr sfnt_name(FT_Face ft, int id)
{
    if (!FT_IS_SFNT(ft)) return ustr();
    int best = 0, bi = -1;
    FT_UInt n = FT_Get_Sfnt_Name_Count(ft);
    for (FT_UInt i = 0; i < n; i++) {
        FT_SfntName nm;
        if (FT_Get_Sfnt_Name(ft, i, &nm) || nm.name_id != id) continue;
        int s = name_score(nm);
        if (s > best) { best = s; bi = (int)i; }
    }
    ustr r;
    FT_SfntName nm;
    if (bi >= 0 && !FT_Get_Sfnt_Name(ft, (FT_UInt)bi, &nm)) {
        if (nm.platform_id == TT_PLATFORM_MACINTOSH) for (FT_UInt i = 0; i < nm.string_len; i++) r += (char16_t)nm.string[i];
        else for (FT_UInt i = 0; i + 1 < nm.string_len; i += 2) r += (char16_t)(nm.string[i] << 8 | nm.string[i + 1]);
    }
    return r;
}
bool has_table(FT_Face ft, const char *t)
{
    FT_ULong len = 0;
    return !FT_Load_Sfnt_Table(ft, FT_MAKE_TAG(t[0], t[1], t[2], t[3]), 0, NULL, &len);
}
void get_fontsig(FT_Face ft, FSig *fs)
{
    memset(fs, 0, sizeof *fs);
    TT_OS2 *os2 = (TT_OS2 *)FT_Get_Sfnt_Table(ft, FT_SFNT_OS2);
    FT_WinFNT_HeaderRec h;
    if (os2) {
        fs->usb[0] = os2->ulUnicodeRange1; fs->usb[1] = os2->ulUnicodeRange2; fs->usb[2] = os2->ulUnicodeRange3; fs->usb[3] = os2->ulUnicodeRange4;
        if (os2->version == 0) fs->csb[0] = os2->usFirstCharIndex >= 0xf000 && os2->usFirstCharIndex < 0xf100 ? FS_SYMBOL : FS_LATIN1;
        else { fs->csb[0] = os2->ulCodePageRange1; fs->csb[1] = os2->ulCodePageRange2; }
    } else if (!FT_Get_WinFNT_Header(ft, &h)) {
        static const struct { int cs; uint32_t fs; } m[] = { { 0, 1 }, { 238, 2 }, { 204, 4 }, { 161, 8 }, { 162, 0x10 }, { 177, 0x20 }, { 178, 0x40 }, { 186, 0x80 },
            { 163, 0x100 }, { 222, 0x10000 }, { 128, 0x20000 }, { 134, 0x40000 }, { 129, 0x80000 }, { 136, 0x100000 }, { 130, 0x200000 }, { 2, FS_SYMBOL } };
        for (auto &e : m) if (e.cs == h.charset) fs->csb[0] = e.fs;
    }
    if (!fs->csb[0])
        for (int i = 0; i < ft->num_charmaps; i++) {
            FT_Encoding e = ft->charmaps[i]->encoding;
            if (e == FT_ENCODING_UNICODE || e == FT_ENCODING_APPLE_ROMAN) fs->csb[0] |= FS_LATIN1;
            else if (e == FT_ENCODING_MS_SYMBOL) fs->csb[0] |= FS_SYMBOL;
        }
}

/* A declared input that is absent or unreadable is a broken build invariant, not a machine without it: the process ends once, through
 * the host exit slot (os_exit_process), naming the input. An empty directory that is present stays a valid (empty) machine. */
[[noreturn]] void declared_input_broken(const char *what, const std::string &path)
{
    std::string m = std::string("fnvnx: declared input broken: ") + what + ": " + path;
    os_debug_print(m.c_str());
    os_exit_process(78);
}
bool read_file(const std::string &p, std::vector<uint8_t> &out)
{
    int fd = os_file_open(p.c_str(), OS_O_READ);
    if (fd < 0) return false;
    os_stat_t st;
    bool ok = !os_file_fstat(fd, &st);
    if (ok) { out.resize((size_t)st.size); ok = os_file_pread(fd, out.data(), out.size(), 0) == (int64_t)out.size(); }
    os_file_close(fd);
    return ok;
}

/* one font file -> faces (new_ft_face / unix_face_create / add_gdi_face) */
void add_font_file(const std::string &path, bool allow_bitmap, bool listed)   /* listed: the directory scan returned it, so it must be readable */
{
    std::vector<uint8_t> data;
    if (!read_file(path, data)) { if (listed) declared_input_broken("font file listed by its directory is unreadable", path); return; }   /* a registry-named file the set lacks: Wine 9.0 load_registry_fonts skips it */
    if (data.empty()) return;
    for (FT_Long idx = 0, count = 1; idx < count; idx++) {
        FT_Face ft;
        if (FT_New_Memory_Face(g_lib, data.data(), (FT_Long)data.size(), idx, &ft)) break;
        count = ft->num_faces;
        bool scalable = FT_IS_SCALABLE(ft) != 0, ok = ft->family_name && ft->style_name;
        TT_OS2 *os2 = (TT_OS2 *)FT_Get_Sfnt_Table(ft, FT_SFNT_OS2);
        if (!FT_IS_SFNT(ft)) { if (scalable || !allow_bitmap) ok = false; }
        else if (!os2 || !FT_Get_Sfnt_Table(ft, FT_SFNT_HHEA) || !FT_Get_Sfnt_Table(ft, FT_SFNT_HEAD)) ok = false;
        else if (!memcmp(os2->achVendID, "Wine", 4) && has_table(ft, "EBSC")) ok = false;   /* Wine's bitmap-only intermediate sfnt */
        if (ok) {
            Face *f = new Face;
            f->file = path; f->index = (int)idx; f->scalable = scalable;
            ustr fam = sfnt_name(ft, TT_NAME_ID_FONT_FAMILY), second, sty = sfnt_name(ft, TT_NAME_ID_FONT_SUBFAMILY);
            if (fam.empty()) fam = widen(ft->family_name);
            if (sty.empty()) sty = widen(ft->style_name);
            f->style = sty;
            f->full = sfnt_name(ft, TT_NAME_ID_FULL_NAME);
            if (f->full.empty()) f->full = fam + u" " + sty;
            if (FT_IS_SFNT(ft) && os2 && !has_table(ft, "EBDT") && !has_table(ft, "CBDT")) {   /* the opentype parser's rules */
                f->ntm = (os2->fsSelection & 1 ? NTM_ITALIC : 0) | (os2->fsSelection & 0x20 ? NTM_BOLD : 0) | (os2->fsSelection & 0x40 ? NTM_REGULAR : 0);
            } else {
                FT_WinFNT_HeaderRec h;
                f->ntm = (ft->style_flags & FT_STYLE_FLAG_ITALIC ? NTM_ITALIC : 0) | (ft->style_flags & FT_STYLE_FLAG_BOLD ? NTM_BOLD : 0);
                if (!scalable && !FT_Get_WinFNT_Header(ft, &h) && h.weight > 400) f->ntm |= NTM_BOLD;
            }
            if (!f->ntm) f->ntm = NTM_REGULAR;
            if (has_table(ft, "CFF ")) f->ntm |= NTM_PS_OPENTYPE;
            if (TT_Header *hd = (TT_Header *)FT_Get_Sfnt_Table(ft, FT_SFNT_HEAD)) f->version = (uint32_t)hd->Font_Revision;
            get_fontsig(ft, &f->fs);
            if (!scalable && ft->num_fixed_sizes > 0) {
                FT_Bitmap_Size *s = ft->available_sizes;
                FT_WinFNT_HeaderRec h;
                f->sh = s->height; f->sw = s->width; f->x_ppem = (int)s->x_ppem; f->y_ppem = (int)s->y_ppem;
                if (!FT_Get_WinFNT_Header(ft, &h)) {
                    f->il = h.internal_leading;
                    if (h.external_leading > 0 && f->sh == h.pixel_height + h.external_leading) f->sh = h.pixel_height;
                }
            }
            if (fam[0] == '.') delete f;   /* names starting with a dot are ignored */
            else add_face(fam, second, f);
        }
        FT_Done_Face(ft);
    }
}

/* ---- the declared machine's font registry (Wine 9.0, ANSI 1252 / OEM 437, 96 dpi). Every value is generated from Wine's own files by
 * platform/manifest/gen_fontmachine.py (font.c nls_update_font_list row + update_font_list/add_font_list, wine.inf.in [Fonts] FontSubstitutes,
 * fontconfig-config 30-metric-aliases.conf for Replacements; inputs pinned in fnv-nx-build tools/fonts-wine9); nothing here is typed by hand.
 * The font files are exactly that mirror's fonts/ in one directory; nothing else is read. */
#include "font_machine.gen.inc"
void declare_machine()
{
    const std::string nt = "hkey_local_machine\\software\\microsoft\\windows nt\\currentversion\\";
    g_reg["hkey_local_machine\\system\\currentcontrolset\\control\\nls\\codepage"].push_back({ "ACP", std::to_string(kAcp) });
    for (auto &r : kHwFonts) g_reg["hkey_local_machine\\system\\currentcontrolset\\hardware profiles\\current\\software\\fonts"].push_back({ r[0], r[1] });
    for (auto &r : kFontsKey) g_reg[nt + "fonts"].push_back({ r[0], r[1] });
    for (auto &r : kSubst) g_reg[nt + "fontsubstitutes"].push_back({ r[0], r[1] });
    for (auto &r : kReplace) g_reg["hkey_current_user\\software\\wine\\fonts\\replacements"].push_back({ r[0], r[1] });
}
const std::vector<std::pair<std::string, std::string>> &reg_key(const char *k)
{
    static const std::vector<std::pair<std::string, std::string>> none;
    std::string s = k;
    for (char &c : s) c = (char)tolower((unsigned char)c);
    auto it = g_reg.find(s);
    return it == g_reg.end() ? none : it->second;
}

/* ---- loading (font_init) */
std::vector<std::string> list_dir(const std::string &d)
{
    std::vector<std::string> v;
    os_dir *dir = os_dir_open(d.c_str());
    if (!dir) declared_input_broken("declared directory missing or unreadable", d);
    while (const char *n = os_dir_next(dir)) v.push_back(n);
    os_dir_close(dir);
    std::sort(v.begin(), v.end());
    return v;
}
bool is_full_name(const ustr &n) { for (Family *f : g_fam) for (Face *x : f->faces) if (x->scalable && !fcmp(x->full, n, 63)) return true; return false; }
bool enum_fallback(int pf, int i, ustr *out)
{
    if (i >= 3) return false;
    int fixed = (pf & 1) || (pf & 0xf0) == 0x30, serif = !fixed && (pf & 0xf0) == 0x10;
    const char *n = fixed ? (i == 0 ? w32_face_name(FACE_COURIER_NEW) : i == 1 ? w32_face_name(FACE_LIBERATION_MONO) : w32_face_name(FACE_BITSTREAM_VERA_SANS_MONO))
                  : serif ? (i == 0 ? w32_face_name(FACE_TIMES_NEW_ROMAN) : i == 1 ? w32_face_name(FACE_LIBERATION_SERIF) : w32_face_name(FACE_BITSTREAM_VERA_SERIF))
                          : (i == 0 ? w32_face_name(FACE_ARIAL) : i == 1 ? w32_face_name(FACE_LIBERATION_SANS) : w32_face_name(FACE_BITSTREAM_VERA_SANS));
    *out = widen(n);
    return true;
}
void set_default(int pf, ustr *def)
{
    ustr n;
    for (int i = 0; enum_fallback(pf, i, &n); i++) if (find_family(n)) { *def = n; return; }
}
void load_all()
{
    if (g_loaded) return;
    g_loaded = true;
    const fnvnx_font_dirs *d = fnvnx_declared_fonts();   /* a declared input of the root: no setter, no unset state */
    g_wine_dir = d->dir;
    if (!g_lib && FT_Init_FreeType(&g_lib)) declared_input_broken("FreeType does not start (FT_Init_FreeType: out of memory)", "libfreetype");
    declare_machine();
    for (auto &kv : reg_key("HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Control\\Nls\\CodePage")) if (kv.first == "ACP") g_acp = atoi(kv.second.c_str());
    auto sys = reg_key("HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Hardware Profiles\\Current\\Software\\Fonts");
    for (const char *k : { "FONTS.FON", "OEMFONT.FON", "FIXEDFON.FON" })   /* load_system_bitmap_fonts */
        for (auto &kv : sys) if (kv.first == k) add_font_file(g_wine_dir + "/" + kv.second, true, false);
    for (const std::string &n : list_dir(g_wine_dir)) {
        os_stat_t st;
        if (!os_stat((g_wine_dir + "/" + n).c_str(), &st) && st.is_dir) continue;   /* Wine opens regular files only */
        add_font_file(g_wine_dir + "/" + n, false, true);
    }   /* the data directory */
    for (auto &kv : reg_key("HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts")) {   /* load_registry_fonts */
        std::string v = kv.first;
        if (v.size() > 11 && v.compare(v.size() - 11, 11, " (TrueType)") == 0) v.resize(v.size() - 11);
        if (is_full_name(widen(v.c_str()))) continue;
        const std::string &path = kv.second;
        if (path.size() > 4 && !strcasecmp(path.c_str() + path.size() - 4, ".fon") && !(path.size() > 1 && path[1] == ':')) add_font_file(g_wine_dir + "/" + path, true, false);
    }
    set_default(0x10, &g_def_roman); set_default(0x30, &g_def_modern); set_default(0x20, &g_def_swiss);   /* reorder_font_list */
    std::stable_sort(g_fam.begin(), g_fam.end(), [](Family *a, Family *b) { return namecmp(a->name, b->name) < 0; });
    for (auto &kv : reg_key("HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\FontSubstitutes")) {   /* load_gdi_font_subst */
        std::string from = kv.first, to = kv.second;
        int fcs = -1, tcs = -1;
        size_t c = from.rfind(','), d = to.rfind(',');
        if (c != std::string::npos && c + 1 < from.size()) { fcs = atoi(from.c_str() + c + 1); from.resize(c); }
        if (d != std::string::npos && d + 1 < to.size()) { tcs = atoi(to.c_str() + d + 1); to.resize(d); }
        if ((fcs == 0 || tcs == fcs) && tcs != 1) g_subst.push_back({ from + "\x01" + std::to_string(fcs), to + "\x01" + std::to_string(tcs) });
    }
    for (auto &kv : reg_key("HKEY_CURRENT_USER\\Software\\Wine\\Fonts\\Replacements")) {   /* "NewName"="OldName": an alias family */
        if (find_family_any(widen(kv.first.c_str()))) continue;
        if (Family *f = find_family_any(widen(kv.second.c_str()))) f->second = widen(kv.first.c_str());
    }
}
const char *subst_of(const ustr &name, int cs, int *to_cs)
{
    static std::string r;
    for (auto &s : g_subst) {
        size_t a = s.first.find('\x01'), b = s.second.find('\x01');
        int fcs = atoi(s.first.c_str() + a + 1);
        if (!fcmp(widen(s.first.substr(0, a).c_str()), name) && (fcs == cs || fcs == -1)) { *to_cs = atoi(s.second.c_str() + b + 1); r = s.second.substr(0, b); return r.c_str(); }
    }
    return nullptr;
}

/* ---- matching (find_matching_face) */
bool can_select(const Face *f, const FSig &fs, bool bitmap_ok)
{
    if (!f->scalable && !bitmap_ok) return false;
    if (!fs.csb[0]) return true;
    return (fs.csb[0] & f->fs.csb[0]) != 0;   /* (no FontLink list in stage 1) */
}
Face *best_in_family(const Family *fam, const LF &lf, const FSig &fs, bool bitmap_ok)
{
    Face *best = nullptr, *best_bitmap = nullptr;
    unsigned best_score = 4;
    int best_diff = 0, it = !!lf.it, bd = lf.weight > 550, height = lf.h;
    for (Face *f : fam->faces) {
        int score = (!!(f->ntm & NTM_ITALIC) ^ it) + (!!(f->ntm & NTM_BOLD) ^ bd);
        if (!can_select(f, fs, bitmap_ok) || (unsigned)score > best_score) continue;
        best_score = (unsigned)score; best = f;
        if (f->scalable && score == 0) break;
        if (!f->scalable) {
            int diff = height > 0 ? height - f->sh : -height - (f->sh - f->il);
            if (!best_bitmap || (best_diff > 0 && diff >= 0 && diff < best_diff) || (best_diff < 0 && diff > best_diff)) {
                best_diff = diff; best_bitmap = f;
                if (score == 0 && best_diff == 0) break;
            }
        }
    }
    return !best ? nullptr : best->scalable ? best : best_bitmap;
}
Face *by_name(const ustr &name, const char *subst, const LF &lf, const FSig &fs, bool bitmap_ok, ustr *orig)
{
    Family *fam = find_family_any(name);
    Face *f = fam ? best_in_family(fam, lf, fs, bitmap_ok) : nullptr;
    if (!f && subst) { fam = find_family_any(widen(subst)); f = fam ? best_in_family(fam, lf, fs, bitmap_ok) : nullptr; }
    if (f) { if (orig && fam != f->fam) *orig = fam->name; return f; }
    for (Family *x : g_fam) for (Face *y : x->faces) if (!fcmp(y->full, name, 31) && can_select(y, fs, bitmap_ok)) return y;   /* full face name */
    return nullptr;
}
Face *any_face(const LF &lf, const FSig &fs, bool bitmap_ok)
{
    ustr n;
    for (int i = 0; enum_fallback(lf.pf, i, &n); i++) if (Family *f = find_family_any(n)) if (Face *x = best_in_family(f, lf, fs, false)) return x;
    for (Family *f : g_fam) if (f->name[0] != '@') if (Face *x = best_in_family(f, lf, fs, false)) return x;
    if (!bitmap_ok) return nullptr;
    for (Family *f : g_fam) if (f->name[0] != '@') if (Face *x = best_in_family(f, lf, fs, true)) return x;
    return nullptr;
}
Face *match_face(const LF &lf, CSI *csi, bool bitmap_ok, bool *substituted, ustr *orig)
{
    if (!translate_charset(lf.cs, csi, 1)) csi->fs.csb[0] = 0;
    ustr fname(lf.face);
    if (lf.face[0]) {
        int to_cs = -1;
        const char *sub = subst_of(fname, lf.cs, &to_cs);
        if (sub) { if (to_cs != -1) translate_charset((uintptr_t)to_cs, csi, 1); *substituted = true; }
        if (Face *f = by_name(fname, sub, lf, csi->fs, bitmap_ok, orig)) return f;
    }
    *substituted = false;
    if (!csi->fs.csb[0] && !translate_charset((uintptr_t)g_acp, csi, 2)) csi->fs.csb[0] = 0;
    if (Face *f = any_face(lf, csi->fs, bitmap_ok)) return f;
    if (csi->fs.csb[0]) { csi->fs.csb[0] = 0; if (Face *f = any_face(lf, csi->fs, bitmap_ok)) return f; }
    return nullptr;
}

/* ---- realised fonts (select_font / freetype_load_font) */
struct RF {
    LF lf; Face *face = nullptr; FT_Face ft = nullptr; std::vector<uint8_t> *data = nullptr;
    bool scalable = false, fake_bold = false, fake_italic = false, use_lf_name = false, tm_done = false;
    int ppem = 0, yMax = 0, yMin = 0, scale_y = 1, aveWidth = 0, ntmAvgWidth = 0, otmDescent = 0;
    ustr family; uint32_t charset = 0, codepage = 0; FSig fs = {}; TMW tm = {};
};
std::vector<RF *> g_rf;

int fix_windescent(int d) { return abs((int16_t)d); }
int ppem_for_height(FT_Face ft, int height)
{
    TT_OS2 *os2 = (TT_OS2 *)FT_Get_Sfnt_Table(ft, FT_SFNT_OS2);
    TT_HoriHeader *hh = (TT_HoriHeader *)FT_Get_Sfnt_Table(ft, FT_SFNT_HHEA);
    const int MAXP = 65535;
    if (!height) height = 16;
    if (height > 0) {
        int wd = fix_windescent(os2->usWinDescent), units = os2->usWinAscent + wd == 0 ? hh->Ascender - hh->Descender : os2->usWinAscent + wd;
        int ppem = (int)FT_MulDiv(ft->units_per_EM, height, units);
        if (ppem > 1 && FT_MulDiv(units, ppem, ft->units_per_EM) > height) --ppem;
        return ppem > MAXP ? 1 : ppem;
    }
    return height >= -MAXP ? -height : 1;
}
int load_vdmx(RF *f, int height)
{
    FT_ULong len = 0;
    if (FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('V', 'D', 'M', 'X'), 0, NULL, &len) || len < 6) return 0;
    std::vector<uint8_t> t(len);
    if (FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('V', 'D', 'M', 'X'), 0, t.data(), &len)) return 0;
    auto be = [&](size_t o) { return o + 1 < t.size() ? (t[o] << 8 | t[o + 1]) : 0; };
    int nrat = be(4), off = -1;
    for (int i = 0; i < nrat; i++) {
        size_t r = 6 + 4 * (size_t)i;
        if (r + 3 >= t.size() || !t[r]) continue;
        if ((t[r + 1] == 0 && t[r + 2] == 0 && t[r + 3] == 0) || (1 == t[r + 1] && 1 >= t[r + 2] && 1 <= t[r + 3])) { off = be(6 + 4 * (size_t)nrat + 2 * (size_t)i); break; }
    }
    if (off < 0 || (size_t)off + 4 > t.size()) return 0;
    int recs = be(off), startsz = t[off + 2], endsz = t[off + 3], ppem = 0;
    size_t v = (size_t)off + 4;
    if (v + 6 * (size_t)recs > t.size()) return 0;
    auto rec = [&](int i, int k) { return be(v + 6 * (size_t)i + 2 * (size_t)k); };
    if (height > 0) {
        for (int i = 0; i < recs; i++) {
            int ymax = (int16_t)rec(i, 1), ymin = (int16_t)rec(i, 2);
            ppem = rec(i, 0);
            if (ymax - ymin == height) { f->yMax = ymax; f->yMin = ymin; break; }
            if (ymax - ymin > height) {
                if (--i < 0) return 0;
                f->yMax = (int16_t)rec(i, 1); f->yMin = (int16_t)rec(i, 2); ppem = rec(i, 0);
                break;
            }
        }
        if (!f->yMax) ppem = 0;
    } else {
        ppem = -height;
        if (ppem < startsz || ppem > endsz) return 0;
        for (int i = 0; i < recs; i++) {
            int ph = rec(i, 0);
            if (ph > ppem) return 0;
            if (ph == ppem) { f->yMax = (int16_t)rec(i, 1); f->yMin = (int16_t)rec(i, 2); break; }
        }
    }
    return ppem;
}
void pick_charmap(FT_Face ft, int charset)
{
    static const FT_Encoding reg[] = { FT_ENCODING_UNICODE, FT_ENCODING_APPLE_ROMAN, FT_ENCODING_MS_SYMBOL }, sym[] = { FT_ENCODING_MS_SYMBOL, FT_ENCODING_UNICODE, FT_ENCODING_APPLE_ROMAN };
    for (FT_Encoding e : charset == SYMBOL_CS ? sym : reg) {
        FT_CharMap c[4] = { 0, 0, 0, 0 }, def = 0;   /* by platform 0..3 */
        for (int i = 0; i < ft->num_charmaps; i++) {
            if (ft->charmaps[i]->encoding != e) continue;
            int p = ft->charmaps[i]->platform_id;
            if (p >= 0 && p <= 3) c[p] = ft->charmaps[i]; else def = ft->charmaps[i];
        }
        FT_CharMap pick = c[3] ? c[3] : c[1] ? c[1] : c[2] ? c[2] : c[0] ? c[0] : def;
        if (pick && !FT_Set_Charmap(ft, pick)) return;
    }
    if (!ft->charmap && ft->num_charmaps) FT_Set_Charmap(ft, ft->charmaps[0]);
}

RF *realize(LF lf, bool bitmap_ok = true)
{
    load_all();
    if (!fcmp(ustr(lf.face), u"Symbol")) lf.cs = SYMBOL_CS;
    lf.w = abs(lf.w);
    for (RF *r : g_rf) if (!memcmp(&r->lf, &lf, sizeof lf)) return r;
    CSI csi = {};
    bool subst = false;
    ustr orig;
    Face *face = match_face(lf, &csi, bitmap_ok, &subst, &orig);
    if (!face) return nullptr;
    RF *f = new RF;
    f->lf = lf; f->face = face; f->use_lf_name = subst; f->scalable = face->scalable; f->fs = face->fs;
    f->fake_italic = lf.it && !(face->ntm & NTM_ITALIC);
    f->fake_bold = lf.weight > 550 && !(face->ntm & NTM_BOLD);
    f->family = orig.empty() ? face->fam->name : orig;
    if (!csi.fs.csb[0]) {   /* get_nearest_charset */
        if (translate_charset((uintptr_t)g_acp, &csi, 2) && ((csi.fs.csb[0] & face->fs.csb[0]) || false)) {}
        else {
            bool done = false;
            for (int i = 0; i < 32 && !done; i++) { uint32_t b = 1u << i; if ((face->fs.csb[0] & b) && translate_charset((uintptr_t)&b, &csi, 3)) done = true; }
            if (!done) { csi.acp = (uint32_t)g_acp; csi.cs = DEFAULT_CS; }
        }
    }
    f->charset = csi.cs; f->codepage = csi.acp;
    int height = lf.h;
    f->aveWidth = height ? lf.w : 0;
    if (!face->scalable) {
        int diff = height > 0 ? height - face->sh : -height - (face->sh - face->il);
        height = abs(height);   /* the identity world matrix: GDI_ROUND(height * 1.0) */
        if (height != 0) height = diff;
        height += face->sh;
        int scale = (height + face->sh - 1) / face->sh, scaled = scale * face->sh;
        if (scale > 2 && scaled - height > face->sh / 4) scale--;
        else if (scale == 2 && scaled - height > face->sh / 4 - 1) scale--;
        f->scale_y = scale;
    }
    f->data = new std::vector<uint8_t>;
    if (!read_file(face->file, *f->data)) declared_input_broken("font file of a listed face is unreadable", face->file);
    if (FT_New_Memory_Face(g_lib, f->data->data(), (FT_Long)f->data->size(), face->index, &f->ft)) { delete f->data; delete f; return nullptr; }
    FT_UInt w = 0, h = 0;
    if (f->scalable) {
        f->ppem = load_vdmx(f, lf.h);
        if (!f->ppem) f->ppem = ppem_for_height(f->ft, lf.h);
        h = (FT_UInt)f->ppem;
    } else {
        w = (FT_UInt)(face->x_ppem >> 6); h = (FT_UInt)(face->y_ppem >> 6);
        f->ppem = (int)h;
    }
    FT_Set_Pixel_Sizes(f->ft, w, h);
    pick_charmap(f->ft, (int)f->charset);
    g_rf.push_back(f);
    return f;
}

/* ---- text metrics (freetype_set_outline_text_metrics / _bitmap_ / scale_font_metrics) */
bool outline_metrics(RF *f)
{
    if (!f->scalable) return false;
    if (f->tm_done) return true;
    FT_Face ft = f->ft;
    TT_OS2 *os2 = (TT_OS2 *)FT_Get_Sfnt_Table(ft, FT_SFNT_OS2);
    TT_HoriHeader *hh = (TT_HoriHeader *)FT_Get_Sfnt_Table(ft, FT_SFNT_HHEA);
    if (!os2 || !hh) return false;
    FT_Fixed em = (FT_Fixed)FT_MulDiv(f->ppem, 1 << 16, ft->units_per_EM);
    int wd = fix_windescent(os2->usWinDescent), asc, desc;
    if (os2->usWinAscent + wd == 0) { asc = hh->Ascender; desc = -hh->Descender; } else { asc = os2->usWinAscent; desc = wd; }
    f->ntmAvgWidth = os2->xAvgCharWidth;
    auto SX = [&](long x) { return (int)FT_MulFix(x, em); };
    TMW &t = f->tm;
    memset(&t, 0, sizeof t);
    if (f->yMax) { t.asc = f->yMax; t.desc = -f->yMin; t.il = t.asc + t.desc - (int)ft->size->metrics.y_ppem; }
    else { t.asc = SX(asc); t.desc = SX(desc); t.il = SX(asc + desc - ft->units_per_EM); }
    t.h = t.asc + t.desc;
    t.el = std::max(0, SX(hh->Line_Gap - ((asc + desc) - (hh->Ascender - hh->Descender))));
    t.aw = SX(os2->xAvgCharWidth);
    if (!t.aw) t.aw = 1;
    t.mw = SX(ft->bbox.xMax - ft->bbox.xMin);
    t.wt = 400;
    if (f->fake_bold) t.wt = 700;
    else if (ft->style_flags & FT_STYLE_FLAG_BOLD) { if (os2->usWeightClass > 500) t.wt = os2->usWeightClass; }
    else if (os2->usWeightClass <= 500) t.wt = os2->usWeightClass;
    t.ax = t.ay = 96;
    bool sym = os2->usFirstCharIndex >= 0xf000 && os2->usFirstCharIndex < 0xf100;
    for (int i = 0; i < ft->num_charmaps && !sym; i++) sym = ft->charmaps[i]->encoding == FT_ENCODING_MS_SYMBOL;
    if (sym) { t.fc = 0; t.lc = 0xf0ff; t.bc = 0x20; t.dc = 0x1f; }
    else {
        t.fc = os2->usFirstCharIndex; t.lc = os2->usLastCharIndex;
        t.bc = os2->usFirstCharIndex <= 1 ? os2->usFirstCharIndex + 2 : os2->usFirstCharIndex > 0xff ? 0x20 : os2->usFirstCharIndex;
        t.dc = (char16_t)(t.bc - 1);
    }
    t.it = f->fake_italic || (ft->style_flags & FT_STYLE_FLAG_ITALIC) ? 255 : 0;
    t.ul = f->lf.ul ? 255 : 0; t.so = f->lf.so ? 255 : 0;
    t.pf = !FT_IS_FIXED_WIDTH(ft) && (os2->version == 0xFFFFU || os2->panose[3] != 9) ? 1 : 0;   /* TMPF_FIXED_PITCH, inverted sense */
    int fam = os2->panose[0];
    if (fam == 3) t.pf |= 0x40;          /* FF_SCRIPT */
    else if (fam == 4) t.pf |= 0x50;     /* FF_DECORATIVE */
    else if (t.pf == 0 || os2->panose[3] == 9) t.pf = 0x30;   /* FF_MODERN */
    else {
        int ss = os2->panose[1];   /* serif styles: cove..triangle -> FF_ROMAN, sans..rounded -> FF_SWISS */
        if (ss >= 2 && ss <= 10) t.pf |= 0x10;
        else if (ss >= 11 && ss <= 15) t.pf |= 0x20;
    }
    if (FT_IS_SCALABLE(ft)) t.pf |= 2;   /* TMPF_VECTOR */
    if (FT_IS_SFNT(ft)) t.pf |= (f->face->ntm & NTM_PS_OPENTYPE) ? 8 : 4;
    t.cs = (uint8_t)f->charset;
    f->otmDescent = SX(os2->sTypoDescender);
    f->tm_done = true;
    return true;
}
bool bitmap_metrics(RF *f)
{
    if (f->tm_done) return true;
    FT_Face ft = f->ft;
    FT_WinFNT_HeaderRec h;
    TMW &t = f->tm;
    memset(&t, 0, sizeof t);
    if (!FT_Get_WinFNT_Header(ft, &h)) {
        t.h = h.pixel_height; t.asc = h.ascent; t.desc = t.h - t.asc; t.il = h.internal_leading; t.el = h.external_leading;
        t.aw = h.avg_width; t.mw = h.max_width; t.wt = h.weight; t.ax = h.horizontal_resolution; t.ay = h.vertical_resolution;
        t.fc = h.first_char; t.lc = h.last_char; t.dc = (char16_t)(h.default_char + h.first_char); t.bc = (char16_t)(h.break_char + h.first_char);
        t.it = h.italic; t.pf = h.pitch_and_family; t.cs = h.charset;
    } else {
        t.asc = (int)(ft->size->metrics.ascender >> 6); t.desc = (int)(-ft->size->metrics.descender >> 6); t.h = t.asc + t.desc;
        t.il = t.h - (int)ft->size->metrics.y_ppem; t.el = (int)(ft->size->metrics.height >> 6) - t.h;
        t.mw = (int)(ft->size->metrics.max_advance >> 6); t.aw = t.mw * 2 / 3; t.wt = ft->style_flags & FT_STYLE_FLAG_BOLD ? 700 : 400;
        t.ax = t.ay = 96; t.fc = 1; t.lc = 255; t.dc = 32; t.bc = 32; t.it = ft->style_flags & FT_STYLE_FLAG_ITALIC ? 1 : 0;
        t.pf = FT_IS_FIXED_WIDTH(ft) ? 0 : 1; t.cs = (uint8_t)f->charset;
    }
    t.ul = f->lf.ul ? 0xff : 0; t.so = f->lf.so ? 0xff : 0;
    if (f->fake_bold) t.wt = 700;
    f->tm_done = true;
    return true;
}
int rnd(double x) { return (int)floor(x + 0.5); }
bool get_tm(RF *f, TMW *out)
{
    if (!outline_metrics(f) && !bitmap_metrics(f)) return false;
    TMW t = f->tm;
    if (f->aveWidth && (f->aveWidth + t.h - 1) / t.h > 100) f->aveWidth = 0;
    double sx = f->aveWidth ? (double)f->aveWidth / f->tm.aw : f->scale_y, sy = f->scale_y;
    t.h = rnd(t.h * sy); t.asc = rnd(t.asc * sy); t.desc = rnd(t.desc * sy); t.il = rnd(t.il * sy); t.el = rnd(t.el * sy);
    t.oh = rnd(t.oh * sx);
    if (f->fake_bold) { if (!f->scalable) t.oh++; t.aw++; t.mw++; }
    t.aw = rnd(t.aw * sx); t.mw = rnd(t.mw * sx);
    t.ax = t.ay = 96;   /* the DC's LOGPIXELS */
    *out = t;
    return true;
}
/* stock LOGFONTs (gdiobj.c, ANSI system charset) */
bool stock_lf(int idx, LF *lf)
{
    struct S { int idx, h, w, wt, cs, pf; const char *face; };
    const S t[] = { { 10, 12, 0, 400, OEM_CS, 0x31, "" }, { 11, 12, 0, 400, 0, 0x31, w32_face_name(FACE_COURIER) }, { 12, 12, 0, 400, 0, 0x22, w32_face_name(FACE_MS_SANS_SERIF) },
        { 13, 16, 7, 700, 0, 0x22, w32_face_name(FACE_SYSTEM) }, { 14, 16, 0, 700, 0, 0x22, w32_face_name(FACE_SYSTEM) }, { 16, 16, 0, 400, 0, 0x31, w32_face_name(FACE_COURIER) },
        { 17, -11, 0, 400, 0, 0x22, w32_face_name(FACE_MS_SHELL_DLG) } };
    for (const S &s : t) if (s.idx == idx) {
        memset(lf, 0, sizeof *lf);
        lf->h = s.h; lf->w = s.w; lf->weight = s.wt; lf->cs = (uint8_t)s.cs; lf->pf = (uint8_t)s.pf;
        for (int i = 0; s.face[i]; i++) lf->face[i] = (char16_t)s.face[i];
        return true;
    }
    return false;
}
bool lf_of(void *hf, LF *lf)
{
    gdi_objinfo oi;
    if (!gdi_obj_info(hf, &oi) || oi.kind != GDIK_FONT) return false;
    if (oi.payload) { memcpy(lf, oi.payload, sizeof *lf); return true; }
    return stock_lf(oi.stock, lf);
}
RF *cur(void *hdc)   /* the DC's font as realised in a GM_COMPATIBLE DC: the glyph orientation is the escapement */
{
    uint32_t h = gdi_dc_font(hdc);
    LF lf;
    if (!h || !lf_of((void *)(uintptr_t)h, &lf)) return nullptr;
    lf.orient = lf.esc;
    return realize(lf);
}

/* ---- glyph outlines (freetype_get_glyph_outline) */
struct GM { uint32_t bx, by; int32_t ox, oy; int16_t ix, iy; };
FT_Fixed fixed_of(double d) { return (FT_Fixed)(d * 0x10000); }
bool user_matrices(RF *f, const uint8_t *m2, FT_Matrix mats[3])   /* get_transform_matrices; false = no transform needed */
{
    static const FT_Matrix I = { 1 << 16, 0, 0, 1 << 16 };
    bool need = false;
    mats[2] = I;
    double ratio = f->scale_y;
    if (f->aveWidth) { outline_metrics(f) || bitmap_metrics(f); ratio = (double)f->aveWidth / f->tm.aw; }
    if (ratio != 1.0 || f->scale_y != 1) {
        FT_Matrix s = { fixed_of(ratio), 0, 0, (FT_Fixed)f->scale_y << 16 };
        FT_Matrix_Multiply(&s, &mats[2]);
        need = true;
    }
    if (f->fake_italic) { FT_Matrix s = { 1 << 16, (1 << 16) >> 2, 0, 1 << 16 }; FT_Matrix_Multiply(&s, &mats[2]); need = true; }
    mats[0] = mats[2];
    if (f->scalable && f->lf.orient % 3600) {
        FT_Vector a;
        FT_Vector_Unit(&a, FT_MulDiv(1 << 16, f->lf.orient, 10));
        FT_Matrix r = { a.x, -a.y, a.y, a.x };
        FT_Matrix_Multiply(&r, &mats[0]);
        need = true;
    }
    mats[1] = mats[0];
    if (m2) {
        auto fx = [&](int o) { return (FT_Fixed)((int)(int16_t)(m2[o + 2] | m2[o + 3] << 8) << 16 | (m2[o] | m2[o + 1] << 8)); };
        FT_Matrix u = { fx(0), fx(8), fx(4), fx(12) };   /* eM11, eM21, eM12, eM22 */
        for (int i = 0; i < 3; i++) FT_Matrix_Multiply(&u, &mats[i]);
        need = true;
    }
    return need;
}
FT_Vector adv_metric(RF *f, FT_Pos base, const FT_Matrix *tm)
{
    FT_Vector adv = { base, 0 };
    FT_Fixed em = 0;
    bool full = false;
    if (outline_metrics(f) && !(f->tm.pf & 1)) {
        em = (FT_Fixed)FT_MulDiv(f->ppem, 1 << 16, f->ft->units_per_EM);
        unsigned avg = (unsigned)FT_MulFix(f->ntmAvgWidth, em);
        full = avg > 0 && ((base + 63) >> 6) == FT_MulFix(f->ntmAvgWidth * 2, em);
        if (full && !tm) adv.x = (avg * 2) << 6;
    }
    if (tm) {
        FT_Vector_Transform(&adv, tm);
        if (full && adv.y == 0) { FT_Vector v = { f->ntmAvgWidth, 0 }; FT_Vector_Transform(&v, tm); adv.x = (FT_MulFix(v.x, em) * 2) << 6; }
    }
    if (f->fake_bold) {
        if (!tm) adv.x += 1 << 6;
        else { FT_Vector v = { 1 << 6, 0 }; FT_Vector_Transform(&v, tm); FT_Fixed len = FT_Vector_Length(&v); if (len) { adv.x += (v.x << 6) / len; adv.y += (v.y << 6) / len; } }
    }
    adv.x = (adv.x + 63) & -64;
    adv.y = -((adv.y + 63) & -64);
    return adv;
}
void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
void pointfx(const FT_Vector &v, uint8_t *p)   /* FTVectorToPOINTFX: two FIXED (fract, value) */
{
    for (int k = 0; k < 2; k++) {
        FT_Pos c = k ? v.y : v.x;
        unsigned fr = (unsigned)((c & 0x3f) << 10);
        fr |= (fr >> 6) | (fr >> 12);
        put16(p + 4 * k, (uint16_t)fr);
        put16(p + 4 * k + 2, (uint16_t)(c >> 6));
    }
}
unsigned native_outline(FT_Outline *o, bool bezier, uint8_t *buf)
{
    unsigned needed = 0, point = 0;
    for (int c = 0; c < o->n_contours; c++) {
        int last = o->contours[c];
        if (!bezier && (int)point == last) { point++; continue; }   /* a one-point contour */
        unsigned start = needed, first = point;
        if (buf) { put32(buf + needed + 4, 24); pointfx(o->points[point], buf + needed + 8); }
        needed += 16;
        point++;
        while ((int)point <= last) {
            uint8_t *pc = buf ? buf + needed : nullptr;
            bool on = o->tags[point] & FT_CURVE_TAG_ON;
            unsigned type = on ? 1 : bezier ? 3 : 2, n = 0;
            do {
                if (!bezier || on) { if (pc) pointfx(o->points[point], pc + 4 + 8 * n); n++; point++; }
                else {   /* conic -> cubic */
                    FT_Vector p0 = o->points[point - 1], p1 = o->points[point], p3, c1, c2;
                    if (!(o->tags[point - 1] & FT_CURVE_TAG_ON)) { p0.x = (p0.x + p1.x + 1) >> 1; p0.y = (p0.y + p1.y + 1) >> 1; }
                    if ((int)point + 1 > last) p3 = o->points[first];
                    else { p3 = o->points[point + 1]; if (!(o->tags[point + 1] & FT_CURVE_TAG_ON)) { p3.x = (p3.x + p1.x + 1) >> 1; p3.y = (p3.y + p1.y + 1) >> 1; } }
                    c1.x = (2 * p1.x + 1) / 3; c1.y = (2 * p1.y + 1) / 3; c2 = c1;
                    c1.x += (p0.x + 1) / 3; c1.y += (p0.y + 1) / 3; c2.x += (p3.x + 1) / 3; c2.y += (p3.y + 1) / 3;
                    if (pc) { pointfx(c1, pc + 4 + 8 * n); pointfx(c2, pc + 4 + 8 * (n + 1)); pointfx(p3, pc + 4 + 8 * (n + 2)); }
                    n += 3; point++;
                }
            } while ((int)point <= last && (o->tags[point] & FT_CURVE_TAG_ON) == (o->tags[point - 1] & FT_CURVE_TAG_ON));
            if (!bezier && (int)point > last && !(o->tags[point - 1] & FT_CURVE_TAG_ON)) { if (pc) pointfx(o->points[first], pc + 4 + 8 * n); n++; }   /* the start point closes a conic run */
            else if ((int)point <= last && (o->tags[point] & FT_CURVE_TAG_ON)) {
                if (!bezier) { if (pc) pointfx(o->points[point], pc + 4 + 8 * n); n++; }   /* the closing point of a conic run */
                point++;
            }
            if (pc) { put16(pc, (uint16_t)type); put16(pc + 2, (uint16_t)n); }
            needed += 4 + 8 * n;
        }
        if (buf) put32(buf + start, needed - start);
    }
    return needed;
}
unsigned glyph_index(RF *f, unsigned ch, bool by_index)
{
    FT_Face ft = f->ft;
    auto lookup = [&](unsigned g, bool use_enc, bool *handled) -> unsigned {
        *handled = !(!use_enc ^ (ft->charmap->encoding == FT_ENCODING_NONE));
        if (!*handled) return g;
        if (ft->charmap->encoding == FT_ENCODING_MS_SYMBOL) {
            auto sym = [&](unsigned c) { if (c < 0x100) c += 0xf000; unsigned r = FT_Get_Char_Index(ft, c); return r ? r : FT_Get_Char_Index(ft, c - 0xf000); };
            unsigned r = sym(g);
            if (!r) { char16_t w = (char16_t)g; char b[2]; if (WideCharToMultiByte((UINT)g_acp, 0, (LPCWSTR)&w, 1, b, 2, NULL, NULL) > 0) r = sym((uint8_t)b[0]); }
            return r;
        }
        return FT_Get_Char_Index(ft, g);
    };
    bool h;
    if (by_index) { unsigned g = ch; unsigned r = lookup(g, false, &h); return h ? r : g; }
    unsigned r = lookup(ch, true, &h);
    if (h) return r;
    uint8_t b[2];
    char16_t w = (char16_t)ch;
    if (WideCharToMultiByte(f->codepage, 0, (LPCWSTR)&w, 1, (LPSTR)b, 2, NULL, NULL) != 1) return 0;
    r = lookup(b[0], false, &h);
    return h ? r : b[0];
}
uint32_t glyph_outline(RF *f, unsigned ch, unsigned format, uint8_t *gm20, uint32_t buflen, uint8_t *buf, const uint8_t *m2, int32_t *abc = nullptr)
{
    FT_Face ft = f->ft;
    bool by_index = format & 0x80;
    format &= ~0x80u;
    unsigned index = glyph_index(f, ch, by_index);
    static const uint8_t ident[16] = { 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0 };
    if (m2 && !memcmp(m2, ident, 16)) m2 = nullptr;
    FT_Matrix mats[3], *M = user_matrices(f, m2, mats) ? mats : nullptr;
    FT_Int lf = FT_LOAD_IGNORE_GLOBAL_ADVANCE_WIDTH;
    bool unhinted = format & 0x100;
    format &= ~0x100u;
    if (unhinted) lf |= FT_LOAD_NO_HINTING;
    else switch (format) { case 1: lf |= FT_LOAD_TARGET_MONO; break; case 4: case 5: case 6: lf |= FT_LOAD_TARGET_NORMAL; break; }
    if (M || format != 1) lf |= FT_LOAD_NO_BITMAP;
    FT_Error err = FT_Load_Glyph(ft, index, lf);
    if (err && !(lf & FT_LOAD_NO_HINTING)) { lf |= FT_LOAD_NO_HINTING; err = FT_Load_Glyph(ft, index, lf); }
    if (err) return GERR;
    FT_GlyphSlot gs = ft->glyph;
    FT_Glyph_Metrics met = gs->metrics;
    if (f->fake_bold) {
        bool ok = false;
        if (gs->format == FT_GLYPH_FORMAT_OUTLINE && !FT_Outline_Embolden(&gs->outline, FT_MulDiv(f->ppem, 1 << 6, 24))) {
            FT_BBox b; FT_Outline_Get_CBox(&gs->outline, &b);
            met.width = b.xMax - b.xMin; met.height = b.yMax - b.yMin; met.horiBearingX = b.xMin; met.horiBearingY = b.yMax; ok = true;
        }
        if (!ok && met.width) met.width += 1 << 6;
    }
    if (outline_metrics(f) || bitmap_metrics(f)) {   /* clip to the text metrics' ascent / descent */
        int top = (int)std::min<FT_Pos>(met.horiBearingY, f->tm.asc << 6), bottom = (int)std::max<FT_Pos>(met.horiBearingY - met.height, -(f->tm.desc << 6));
        met.horiBearingY = top; met.height = top - bottom;
    }
    FT_BBox bb = { 0, 0, 0, 0 };   /* get_transformed_bbox */
    if (!M) { bb.xMin = met.horiBearingX & -64; bb.xMax = (met.horiBearingX + met.width + 63) & -64; bb.yMax = (met.horiBearingY + 63) & -64; bb.yMin = (met.horiBearingY - met.height) & -64; }
    else {
        for (int xc = 0; xc < 2; xc++) for (int yc = 0; yc < 2; yc++) {
            FT_Vector v = { met.horiBearingX + xc * met.width, met.horiBearingY - yc * met.height };
            FT_Vector_Transform(&v, &M[1]);
            if (!xc && !yc) { bb.xMin = bb.xMax = v.x; bb.yMin = bb.yMax = v.y; }
            else { if (v.x < bb.xMin) bb.xMin = v.x; else if (v.x > bb.xMax) bb.xMax = v.x; if (v.y < bb.yMin) bb.yMin = v.y; else if (v.y > bb.yMax) bb.yMax = v.y; }
        }
        bb.xMin &= -64; bb.xMax = (bb.xMax + 63) & -64; bb.yMin &= -64; bb.yMax = (bb.yMax + 63) & -64;
    }
    GM g;   /* compute_metrics (no vertical writing) */
    FT_Vector adv, org;
    if (!M) { adv = adv_metric(f, met.horiAdvance, nullptr); g.ix = (int16_t)(adv.x >> 6); g.iy = 0; org.x = bb.xMin; org.y = bb.yMax; }
    else { org.x = bb.xMin; org.y = bb.yMax; adv = adv_metric(f, met.horiAdvance, &M[0]); g.ix = (int16_t)(adv.x >> 6); g.iy = (int16_t)(adv.y >> 6); }
    if (abc) {   /* the ABC widths of compute_metrics: abcA + abcB + abcC is the advance of the unrotated glyph */
        FT_Pos adv_x;
        if (!M) { abc[0] = (int32_t)(bb.xMin >> 6); abc[1] = (int32_t)((met.width + 63) >> 6); adv_x = adv.x; }
        else {
            FT_Vector u = adv_metric(f, met.horiAdvance, &M[2]), v = { met.horiBearingX, 0 };
            adv_x = FT_Vector_Length(&u);
            FT_Vector_Transform(&v, &M[2]);
            abc[0] = met.horiBearingX > 0 ? (int32_t)(FT_Vector_Length(&v) >> 6) : -(int32_t)((FT_Vector_Length(&v) + 63) >> 6);
            v.x = met.horiBearingX + met.width; v.y = 0;
            FT_Vector_Transform(&v, &M[2]);
            abc[1] = (int32_t)(((FT_Vector_Length(&v) + 63) >> 6) - abc[0]);
        }
        if (!abc[1]) abc[1] = 1;
        abc[2] = (int32_t)(adv_x >> 6) - abc[0] - abc[1];
    }
    g.ox = (int32_t)(org.x >> 6); g.oy = (int32_t)(org.y >> 6);
    g.bx = (uint32_t)((bb.xMax - bb.xMin) >> 6); g.by = (uint32_t)((bb.yMax - bb.yMin) >> 6);
    if (!g.bx) g.bx = 1;
    if (!g.by) g.by = 1;
    if (gm20) { put32(gm20, g.bx); put32(gm20 + 4, g.by); put32(gm20 + 8, (uint32_t)g.ox); put32(gm20 + 12, (uint32_t)g.oy); put16(gm20 + 16, (uint16_t)g.ix); put16(gm20 + 18, (uint16_t)g.iy); }
    unsigned width = (unsigned)((bb.xMax - bb.xMin) >> 6), height = (unsigned)((bb.yMax - bb.yMin) >> 6);
    switch (format) {
    case 0: return 1;
    case 1: case 4: case 5: case 6: {
        bool mono = format == 1;
        unsigned pitch = mono ? ((width + 31) >> 5) << 2 : (width + 3) / 4 * 4, needed = pitch * height;
        if (!buf || !buflen) return needed;
        if (!needed || needed > buflen) return GERR;
        int maxl = format == 4 ? 4 : format == 5 ? 16 : format == 6 ? 64 : 255;
        if (gs->format == FT_GLYPH_FORMAT_BITMAP) {
            const uint8_t *src = gs->bitmap.buffer;
            uint8_t *dst = buf;
            if (!mono) memset(buf, 0, buflen);
            int w = mono ? (int)std::min<unsigned>(pitch, (gs->bitmap.width + 7) >> 3) : (int)std::min<unsigned>(pitch, gs->bitmap.width), h = (int)std::min<unsigned>(height, gs->bitmap.rows);
            static const uint8_t masks[8] = { 0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1 };
            while (h--) {
                if (mono) {
                    if (!f->fake_bold) memcpy(dst, src, (size_t)w);
                    else { dst[0] = 0; for (int x = 0; x < w; x++) { dst[x] = (dst[x] & 0x80) | (src[x] >> 1) | src[x]; if (x + 1 < (int)pitch) dst[x + 1] = (src[x] & 1) << 7; } }
                } else for (int x = 0; x < w; x++) if (src[x / 8] & masks[x % 8]) { dst[x] = (uint8_t)maxl; if (f->fake_bold && x + 1 < (int)pitch) dst[x + 1] = (uint8_t)maxl; }
                src += gs->bitmap.pitch; dst += pitch;
            }
        } else if (gs->format == FT_GLYPH_FORMAT_OUTLINE) {
            FT_Bitmap bm;
            bm.width = width; bm.rows = height; bm.pitch = (int)pitch; bm.pixel_mode = mono ? FT_PIXEL_MODE_MONO : FT_PIXEL_MODE_GRAY; bm.buffer = buf;
            if (M) FT_Outline_Transform(&gs->outline, &M[1]);
            FT_Outline_Translate(&gs->outline, -bb.xMin, -bb.yMin);
            memset(buf, 0, buflen);
            FT_Outline_Get_Bitmap(g_lib, &gs->outline, &bm);
            if (!mono && maxl != 255) for (unsigned r = 0; r < height; r++) for (unsigned c = 0; c < width; c++) { uint8_t &p = buf[r * pitch + c]; p = (uint8_t)((p * (maxl + 1)) / 256); }
        } else return GERR;
        return needed;
    }
    case 2: case 3: {
        if (gs->format != FT_GLYPH_FORMAT_OUTLINE) return GERR;
        if (!buflen) buf = nullptr;
        if (M && buf) FT_Outline_Transform(&gs->outline, &M[1]);
        unsigned needed = native_outline(&gs->outline, format == 3, nullptr);
        if (!buf || !buflen) return needed;
        if (needed > buflen) return GERR;
        return native_outline(&gs->outline, format == 3, buf);
    }
    }
    return GERR;
}

void lf_to_a(const LF &w, uint8_t *a)   /* LOGFONTA: the same 28 bytes, the face as 8-bit */
{
    memcpy(a, &w, 28);
    for (int i = 0; i < 32; i++) a[28 + i] = w.face[i] < 0x100 ? (uint8_t)w.face[i] : '?';
}
} // namespace

/* ---- exports */
extern "C" int GetObjectW(HGDIOBJ h, int cb, void *buf)
{
    gdi_objinfo oi;
    if (!gdi_obj_info(h, &oi)) return 0;
    uint8_t tmp[128];
    int size = 0;
    memset(tmp, 0, sizeof tmp);
    switch (oi.kind) {
    case GDIK_FONT: { LF lf; if (!lf_of(h, &lf)) return 0; memcpy(tmp, &lf, 92); size = 92; if (!buf) return size; break; }
    case GDIK_BRUSH: put32(tmp, oi.hollow ? 1 : 0); put32(tmp + 4, ((oi.color & 0xff) << 16) | (oi.color & 0xff00) | ((oi.color >> 16) & 0xff)); size = 12; if (!buf) return size; break;
    case GDIK_PEN:
        if (!buf) return 16;   /* LOGPEN: style, width POINT, colour */
        if (cb < 16) return 0;
        put32(tmp, oi.hollow ? 5 : 0); put32(tmp + 12, ((oi.color & 0xff) << 16) | (oi.color & 0xff00) | ((oi.color >> 16) & 0xff));
        memcpy(buf, tmp, 16);
        return 16;
    case GDIK_PALETTE: if (!buf) return 2; if (cb < 2) return 0; put16((uint8_t *)buf, (uint16_t)(oi.nct ? oi.nct : 20)); return 2;
    case GDIK_BITMAP: {
        struct BM { int32_t type, w, h, wb; uint16_t planes, bpp; void *bits; };
        struct DS { BM bm; uint32_t bih[10]; uint32_t bf[3]; HANDLE sect; uint32_t off; };
        if (!buf) return (int)sizeof(BM);
        DS d;
        memset(&d, 0, sizeof d);
        d.bm.w = oi.w; d.bm.h = oi.h; d.bm.wb = oi.pitch; d.bm.planes = 1; d.bm.bpp = (uint16_t)oi.bpp;
        if (oi.dib && cb >= (int)sizeof d) {
            d.bm.bits = (void *)oi.bits;
            d.bih[0] = 40; d.bih[1] = (uint32_t)oi.w; d.bih[2] = (uint32_t)oi.h; d.bih[3] = 1u | (uint32_t)oi.bpp << 16;
            d.bih[5] = (uint32_t)oi.pitch * (uint32_t)oi.h;
            if (oi.mask[0] | oi.mask[1] | oi.mask[2]) { d.bih[4] = 3; memcpy(d.bf, oi.mask, 12); }
            memcpy(buf, &d, sizeof d);
            return (int)sizeof d;
        }
        if (cb < (int)sizeof(BM)) return 0;
        if (oi.dib) d.bm.bits = (void *)oi.bits;
        memcpy(buf, &d.bm, sizeof(BM));
        return (int)sizeof(BM);
    }
    default: return 0;
    }
    int n = std::min(cb, size);
    memcpy(buf, tmp, (size_t)n);
    return n;
}
extern "C" int GetObjectA(HGDIOBJ h, int cb, void *buf)
{
    gdi_objinfo oi;
    LF lf;
    if (!gdi_obj_info(h, &oi) || oi.kind != GDIK_FONT) return GetObjectW(h, cb, buf);
    if (!buf) return 60;
    if (!lf_of(h, &lf)) return 0;
    uint8_t a[60];
    lf_to_a(lf, a);
    int n = std::min(cb, 60);
    memcpy(buf, a, (size_t)n);
    return n;
}
static HGDIOBJ create_font(const LF &lf) { return (HGDIOBJ)gdi_font_create(&lf); }
extern "C" HFONT CreateFontIndirectW(const void *lf) { return lf ? (HFONT)create_font(*(const LF *)lf) : NULL; }
extern "C" HFONT CreateFontIndirectA(const void *lfa)
{
    if (!lfa) return NULL;
    LF lf;
    memset(&lf, 0, sizeof lf);
    memcpy(&lf, lfa, 28);
    const uint8_t *a = (const uint8_t *)lfa + 28;
    for (int i = 0; i < 31 && a[i]; i++) lf.face[i] = a[i];
    return (HFONT)create_font(lf);
}
extern "C" BOOL GetTextMetricsW(HDC hdc, void *out)
{
    FL l;
    RF *f = cur(hdc);
    TMW t;
    if (!f || !get_tm(f, &t)) return FALSE;
    memcpy(out, &t, sizeof t);
    return TRUE;
}
extern "C" BOOL GetTextMetricsA(HDC hdc, void *out)
{
    FL l;
    RF *f = cur(hdc);
    TMW t;
    if (!f || !get_tm(f, &t)) return FALSE;
    uint8_t *o = (uint8_t *)out;   /* text_metric_WtoA */
    memcpy(o, &t, 44);
    uint8_t first = (uint8_t)std::min<int>(t.fc, 255), last;
    if (t.cs == SYMBOL_CS) { first = 0x1e; last = 0xff; }
    else if (t.pf & 4) { first = (uint8_t)(t.dc - 1); last = (uint8_t)std::min<int>(t.lc, 255); }
    else last = (uint8_t)std::min<int>(t.lc, 255);
    o[44] = first; o[45] = last; o[46] = (uint8_t)t.dc; o[47] = (uint8_t)t.bc; o[48] = t.it; o[49] = t.ul; o[50] = t.so; o[51] = t.pf; o[52] = t.cs;
    memset(o + 53, 0, 3);
    return TRUE;
}
static ustr face_name(HDC hdc)
{
    FL l;
    RF *f = cur(hdc);
    return !f ? ustr(1, u'\1') : f->use_lf_name ? ustr(f->lf.face) : f->family;   /* '\1' marks "no font" */
}
extern "C" int GetTextFaceW(HDC hdc, int count, void *name)
{
    ustr n = face_name(hdc);
    if (n.size() == 1 && n[0] == 1) return 0;
    int len = (int)n.size() + 1;
    if (name && count > 0) {
        char16_t *o = (char16_t *)name;
        int k = std::min(count - 1, (int)n.size());
        for (int i = 0; i < k; i++) o[i] = n[i];
        o[k] = 0;
        len = std::min(count, len);
    }
    return len;
}
extern "C" int GetTextFaceA(HDC hdc, int count, char *name)
{
    ustr w = face_name(hdc);
    if (w.size() == 1 && w[0] == 1) return 0;
    std::string n = narrow(w);
    if (!name) return (int)n.size() + 1;
    if (!count) return 0;
    int k = std::min(count - 1, (int)n.size());
    memcpy(name, n.data(), (size_t)k);
    name[k] = 0;
    return k;   /* GetTextFaceA does not count the NUL */
}
extern "C" UINT GetTextCharsetInfo(HDC hdc, void *fs, DWORD flags)
{
    W32_UNUSED(flags);
    FL l;
    RF *f = cur(hdc);
    if (!f) return DEFAULT_CS;
    if (fs) memcpy(fs, &f->fs, sizeof f->fs);   /* FONTSIGNATURE: usb[4], csb[2] */
    return f->charset;
}
extern "C" int GetTextCharset(HDC hdc) { return (int)GetTextCharsetInfo(hdc, NULL, 0); }
extern "C" DWORD GetFontLanguageInfo(HDC hdc)
{
    FSig fs;
    memset(&fs, 0, sizeof fs);
    GetTextCharsetInfo(hdc, &fs, 0);
    uint32_t c = fs.csb[0];
    DWORD r = 0;
    if (c & (0x20000 | 0x40000 | 0x80000 | 0x100000 | 0x200000)) r |= 1;   /* GCP_DBCS */
    if (c & 0x40) r |= 0x10;                                                  /* GCP_GLYPHSHAPE */
    FL l;
    if (RF *f = cur(hdc)) {
        FT_ULong len = 0;
        std::vector<uint8_t> k;
        if (f->scalable && !FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('k', 'e', 'r', 'n'), 0, NULL, &len) && len >= 4) {
            k.resize(len);
            FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('k', 'e', 'r', 'n'), 0, k.data(), &len);
            size_t o = 4;
            unsigned pairs = 0, nt = k[2] << 8 | k[3];
            for (unsigned i = 0; i < nt && o + 6 <= k.size(); i++) {
                unsigned length = k[o + 2] << 8 | k[o + 3], cov = k[o + 4] << 8 | k[o + 5];
                if ((cov >> 8) == 0 && o + 8 <= k.size()) pairs += k[o + 6] << 8 | k[o + 7];
                o += length;
            }
            if (pairs) r |= 8;   /* GCP_USEKERNING */
        }
    }
    return r;
}
extern "C" BOOL TranslateCharsetInfo(DWORD *src, void *cs, DWORD flags)
{
    CSI c;
    if (flags < 1 || flags > 3 || !translate_charset((uintptr_t)src, &c, (uint32_t)flags)) return FALSE;
    memcpy(cs, &c, sizeof c);
    return TRUE;
}
static DWORD get_outline(HDC hdc, UINT ch, UINT format, void *gm, DWORD size, void *buf, const void *mat, bool ansi)
{
    if (!mat) return GERR;
    FL l;
    RF *f = cur(hdc);
    if (!f) return GERR;
    if (ansi && !(format & 0x80)) {   /* GetGlyphOutlineA: the byte in the font's code page -> Unicode */
        char b = (char)(ch & 0xff);
        WCHAR w = 0;
        MultiByteToWideChar(f->codepage, 0, &b, 1, &w, 1);
        ch = (UINT)(uint16_t)w;
    }
    uint8_t g[20];
    memset(g, 0, sizeof g);
    uint32_t r = glyph_outline(f, ch & 0xffff, format, g, (uint32_t)size, (uint8_t *)buf, (const uint8_t *)mat);
    if (r != GERR && gm) memcpy(gm, g, sizeof g);
    return W32_DW(r);
}
extern "C" DWORD GetGlyphOutlineA(HDC hdc, UINT ch, UINT format, void *gm, DWORD size, void *buf, const void *mat) { return get_outline(hdc, ch, format, gm, size, buf, mat, true); }
extern "C" DWORD GetGlyphOutlineW(HDC hdc, UINT ch, UINT format, void *gm, DWORD size, void *buf, const void *mat) { return get_outline(hdc, ch, format, gm, size, buf, mat, false); }

extern "C" int GetDeviceCaps(HDC, int);
/* ---- DC text state (stored on the DC object in gdi32.cpp). Each returns the previous value and fails on a bad DC.
 * SetMapMode stores and returns any of the eight modes; coordinate transforms other than MM_TEXT are a later stage. */
static uint32_t dc_swap(HDC hdc, int field, uint32_t v, uint32_t fail)
{
    int32_t prev = 0;
    if (!gdi_dc_state(hdc, field, 1, (int32_t)v, &prev)) { SetLastError(ERROR_INVALID_HANDLE); return fail; }
    return (uint32_t)prev;
}
extern "C" COLORREF SetTextColor(HDC hdc, COLORREF c) { return W32_DW(dc_swap(hdc, GDI_TS_TEXTCOLOR, (uint32_t)c, GERR)); }
extern "C" COLORREF SetBkColor(HDC hdc, COLORREF c) { return W32_DW(dc_swap(hdc, GDI_TS_BKCOLOR, (uint32_t)c, GERR)); }
extern "C" int SetBkMode(HDC hdc, int mode)
{
    if (mode != 1 && mode != 2) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }   /* TRANSPARENT, OPAQUE */
    return (int)dc_swap(hdc, GDI_TS_BKMODE, (uint32_t)mode, 0);
}
extern "C" UINT SetTextAlign(HDC hdc, UINT align) { return dc_swap(hdc, GDI_TS_ALIGN, align, GERR); }
extern "C" int SetMapMode(HDC hdc, int mode)   /* MM_TEXT 1 .. MM_ISOTROPIC 7, MM_ANISOTROPIC 8: the extents of each mode (set_map_mode) */
{
    int32_t prev = 0;
    if (!gdi_dc_state(hdc, GDI_TS_MAPMODE, 0, 0, &prev)) { SetLastError(ERROR_INVALID_HANDLE); return 0; }
    if (mode < 1 || mode > 8) return 0;
    if (mode == prev && mode >= 7) return prev;   /* the extents of a mode that is already current are kept */
    if (mode != 8) {
        int32_t wx = 1, wy = 1, vx = 1, vy = 1;
        if (mode != 1) {
            int sx = GetDeviceCaps(hdc, 4), sy = GetDeviceCaps(hdc, 6), rx = GetDeviceCaps(hdc, 8), ry = GetDeviceCaps(hdc, 10);   /* HORZSIZE, VERTSIZE, HORZRES, VERTRES */
            auto muldiv = [](int a, int b, int c) { int64_t n = (int64_t)a * b; return (int32_t)((n + (n < 0 ? -(c / 2) : c / 2)) / c); };
            const int num = mode == 4 ? 1000 : mode == 5 ? 10000 : 14400;   /* LOENGLISH, HIENGLISH, TWIPS: units per inch, the size is in mm (254 per 10 inch) */
            switch (mode) {
            case 2: case 7: wx = sx * 10; wy = sy * 10; break;
            case 3: wx = sx * 100; wy = sy * 100; break;
            default: wx = muldiv(num, sx, 254); wy = muldiv(num, sy, 254); break;   /* LOENGLISH, HIENGLISH, TWIPS */
            }
            vx = rx; vy = -ry;
        }
        gdi_dc_state(hdc, GDI_TS_WEXTX, 1, wx, NULL); gdi_dc_state(hdc, GDI_TS_WEXTY, 1, wy, NULL);
        gdi_dc_state(hdc, GDI_TS_VEXTX, 1, vx, NULL); gdi_dc_state(hdc, GDI_TS_VEXTY, 1, vy, NULL);
    }
    return (int)dc_swap(hdc, GDI_TS_MAPMODE, (uint32_t)mode, 0);
}
extern "C" BOOL MoveToEx(HDC hdc, int x, int y, void *prev_point)
{
    int32_t px = 0, py = 0;
    if (!gdi_dc_state(hdc, GDI_TS_POSX, 1, x, &px) || !gdi_dc_state(hdc, GDI_TS_POSY, 1, y, &py)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    if (prev_point) { memcpy(prev_point, &px, 4); memcpy((uint8_t *)prev_point + 4, &py, 4); }   /* POINT: two 32-bit LONGs */
    return TRUE;
}
extern "C" HGDIOBJ GetCurrentObject(HDC hdc, UINT type)
{
    int k = type == 1 ? GDIK_PEN : type == 2 ? GDIK_BRUSH : type == 5 ? GDIK_PALETTE : type == 6 ? GDIK_FONT : type == 7 ? GDIK_BITMAP : 0;   /* OBJ_* */
    return (HGDIOBJ)(uintptr_t)(k ? gdi_dc_object(hdc, k) : 0);
}

/* ======================================================================================================
 * Stage 2: text output (ExtTextOut), text extents, character placement, outline text metrics, display DC.
 * Wine 9.0's win32u/gdi32 is the cross-check of what the reference computes; the code below is written from the
 * documented contracts. Assumptions stated here once: the DC is GM_COMPATIBLE with the identity world transform (no
 * SetWorldTransform / SetGraphicsMode exists in this DC), window / viewport origins are 0, no inter-character
 * extra spacing and no justification (SetTextCharacterExtra / SetTextJustification keep their DC defaults, 0),
 * no layout mirroring, no clip region (drawing is clipped to the DC's surface and ETO_CLIPPED's rectangle).
 * Antialiasing rule (freetype_get_aa_flags): surfaces of <= 8 bpp and NONANTIALIASED_QUALITY draw monochrome
 * glyphs; otherwise the glyphs are 17-level (GGO_GRAY4) unless the face is hinted by the TrueType bytecode
 * interpreter and its gasp table has no GASP_DOGRAY behaviour for the realised ppem; the face default is the
 * reference's fontconfig default (antialiasing on) and CLEARTYPE qualities fall back to grey levels (no LCD output).
 * One glyph rasteriser: the glyphs are the GetGlyphOutline bitmaps of stage 1. */
namespace {
enum { TX_OPAQUE = 2, TX_CLIPPED = 4, TX_GLYPH_INDEX = 0x10, TX_RTLREADING = 0x80, TX_IGNORELANGUAGE = 0x1000, TX_PDY = 0x2000 };
enum { TX_UPDATECP = 1, TX_RIGHT = 2, TX_CENTER = 6, TX_BOTTOM = 8, TX_BASELINE = 24, TX_RTL = 0x100 };
enum { GX_REORDER = 2, GX_USEKERNING = 8, GX_JUSTIFY = 0x10000, GX_CLASSIN = 0x80000, GX_MAXEXTENT = 0x100000, GX_SYMSWAPOFF = 0x800000 };
enum { HEBREW_CS = 177, ARABIC_CS = 178 };

/* ---- the DC's mapping mode: logical <-> device (set_map_mode extents, zero origins) */
struct Map { double fx = 1, fy = 1, ix = 1, iy = 1; };
bool dc_map(HDC hdc, Map *m)
{
    int32_t w[4];
    for (int i = 0; i < 4; i++) if (!gdi_dc_state(hdc, GDI_TS_WEXTX + i, 0, 0, &w[i])) return false;
    if (w[0] && w[1] && w[2] && w[3]) { m->fx = (double)w[2] / w[0]; m->fy = (double)w[3] / w[1]; m->ix = 1.0 / m->fx; m->iy = 1.0 / m->fy; }
    return true;
}
int d2w_x(const Map &m, int v) { return rnd(v * m.ix); }        /* INTERNAL_XDSTOWS */
int d2w_y(const Map &m, int v) { return rnd(v * m.iy); }
int w2d_y(const Map &m, int v) { return rnd(v * m.fy); }        /* INTERNAL_YWSTODS */
int lp_w(const Map &m, int v) { return rnd(v * fabs(m.ix)); }   /* width_to_LP, height_to_LP: scaled, not mirrored */
int lp_h(const Map &m, int v) { return rnd(v * fabs(m.iy)); }

/* ---- strings: the DC's font code page */
ustr mb2w(RF *f, const char *s, int count)
{
    if (count < 0) count = (int)strlen(s);
    UINT cp = f ? (UINT)f->codepage : (UINT)g_acp;
    ustr r;
    if (cp == 42) { for (int i = 0; i < count; i++) r += (char16_t)((uint8_t)s[i] >= 0x20 ? 0xf000 + (uint8_t)s[i] : (uint8_t)s[i]); return r; }   /* CP_SYMBOL */
    int n = MultiByteToWideChar(cp, 0, s, count, NULL, 0);
    if (n <= 0) return r;
    r.resize((size_t)n);
    MultiByteToWideChar(cp, 0, s, count, (LPWSTR)&r[0], n);
    return r;
}
std::vector<int> mb_lens(RF *f, const char *s, int count)   /* bytes of each decoded character: 2 for a DBCS lead byte */
{
    std::vector<int> r;
    UINT cp = f ? (UINT)f->codepage : (UINT)g_acp;
    for (int i = 0; i < count; i++) {
        WCHAR w;
        int len = 1;
        if (cp != 42 && i + 1 < count && MultiByteToWideChar(cp, 8 /* MB_ERR_INVALID_CHARS */, s + i, 1, &w, 1) == 0) len = 2;
        r.push_back(len);
        i += len - 1;
    }
    return r;
}

/* ---- advances: the ABC widths of stage 1's glyph path, summed per character */
void glyph_abc(RF *f, unsigned ch, bool idx, int32_t abc[3])
{
    uint8_t gm[20];
    abc[0] = abc[1] = abc[2] = 0;
    glyph_outline(f, ch, idx ? 0x80 : 0, gm, 0, nullptr, nullptr, abc);
}
bool positions(RF *f, const char16_t *s, int n, bool idx, std::vector<int> &dx, int *cx, int *cy)   /* get_char_positions, device units */
{
    TMW tm;
    if (!get_tm(f, &tm)) return false;
    dx.assign((size_t)n, 0);
    int pos = 0;
    for (int i = 0; i < n; i++) { int32_t abc[3]; glyph_abc(f, s[i], idx, abc); pos += abc[0] + abc[1] + abc[2]; dx[(size_t)i] = pos; }
    *cx = n ? dx[(size_t)n - 1] : 0;
    *cy = n ? tm.h : 0;
    return true;
}
/* NtGdiGetTextExtentExW: logical units; dxs / nfit only up to the first character that does not fit */
bool extent_core(HDC hdc, RF *f, const char16_t *s, int count, int max_ext, int *nfit, int *dxs, SIZE *size, bool idx)
{
    Map m;
    std::vector<int> pos;
    int cx = 0, cy = 0;
    if (!dc_map(hdc, &m) || !positions(f, s, count, idx, pos, &cx, &cy)) return false;
    int i = 0;
    for (; i < count; i++) {
        unsigned dx = (unsigned)abs(d2w_x(m, pos[(size_t)i]));
        if (nfit && dx > (unsigned)max_ext) break;
        if (dxs) dxs[i] = (int)dx;
    }
    if (nfit) *nfit = i;
    size->cx = abs(d2w_x(m, cx));
    size->cy = abs(d2w_y(m, cy));
    return true;
}
BOOL text_extent_w(HDC hdc, const char16_t *s, int count, int max_ext, int *nfit, int *dxs, SIZE *size, bool idx)
{
    if (count < 0 || (count && !s) || !size) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    FL l;
    if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    RF *f = cur(hdc);
    return f && extent_core(hdc, f, s, count, max_ext, nfit, dxs, size, idx);
}

/* ---- kerning: the font's 'kern' table, horizontal format-0 subtables, scaled to the realised ppem */
std::map<RF *, std::vector<uint8_t>> g_kern;
int kern_pair(RF *f, char16_t a, char16_t b)
{
    if (!f->scalable) return 0;
    auto it = g_kern.find(f);
    if (it == g_kern.end()) {
        FT_ULong len = 0;
        std::vector<uint8_t> t;
        if (!FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('k', 'e', 'r', 'n'), 0, NULL, &len) && len >= 4) { t.resize(len); FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('k', 'e', 'r', 'n'), 0, t.data(), &len); }
        it = g_kern.emplace(f, std::move(t)).first;
    }
    const std::vector<uint8_t> &k = it->second;
    if (k.size() < 4) return 0;
    unsigned ga = glyph_index(f, a, false), gb = glyph_index(f, b, false), nt = k[2] << 8 | k[3];
    long total = 0;
    size_t o = 4;
    for (unsigned i = 0; i < nt && o + 8 <= k.size(); i++) {
        unsigned length = k[o + 2] << 8 | k[o + 3], cov = k[o + 4] << 8 | k[o + 5], np = k[o + 6] << 8 | k[o + 7];
        if ((cov >> 8) == 0 && (cov & 1) && o + 14 + 6 * (size_t)np <= k.size()) {   /* format 0, horizontal */
            for (unsigned j = 0; j < np; j++) {
                const uint8_t *p = &k[o + 14 + 6 * (size_t)j];
                if ((unsigned)(p[0] << 8 | p[1]) == ga && (unsigned)(p[2] << 8 | p[3]) == gb) {
                    long v = (int16_t)(p[4] << 8 | p[5]);
                    total = (cov & 8) ? v : total + v;   /* the override bit replaces the earlier tables' value */
                    break;
                }
            }
        }
        o += length ? length : k.size();
    }
    double v = (double)total * f->ppem / f->ft->units_per_EM;
    return (int)(v < 0 ? -floor(-v + 0.5) : floor(v + 0.5));
}

/* ---- bidirectional ordering (UAX #9 without explicit embeddings), for GCP_REORDER and ExtTextOut on RTL fonts.
 * The class table is by Unicode block range (L, R, AL, numbers, separators, marks, neutrals) and so only as exact
 * as the blocks it lists: Hebrew, Arabic and its supplements, Syriac, Thaana, N'Ko, the presentation forms. */
enum { BC_L, BC_R, BC_AL, BC_EN, BC_ES, BC_ET, BC_AN, BC_CS, BC_NSM, BC_BN, BC_B, BC_S, BC_WS, BC_ON };
int bidi_class(char16_t c)
{
    auto in = [&](unsigned a, unsigned b) { return c >= a && c <= b; };
    if (c == '\n' || c == '\r' || in(0x1c, 0x1e) || c == 0x85 || c == 0x2029) return BC_B;
    if (c == '\t' || c == 0xb || c == 0x1f) return BC_S;
    if (c == 0xc || c == ' ' || in(0x2000, 0x200a) || c == 0x2028 || c == 0x205f || c == 0x3000 || c == 0x1680) return BC_WS;
    if (c < 0x20 || in(0x7f, 0x9f) || c == 0xad || in(0x200b, 0x200d)) return BC_BN;
    if (in('0', '9') || in(0x6f0, 0x6f9) || c == 0xb2 || c == 0xb3 || c == 0xb9) return BC_EN;
    if (in(0x660, 0x669) || c == 0x66b || c == 0x66c) return BC_AN;
    if (c == '+' || c == '-') return BC_ES;
    if (c == '#' || c == '$' || c == '%' || in(0xa2, 0xa5) || c == 0xb0 || c == 0xb1 || in(0x20a0, 0x20cf) || c == 0x66a) return BC_ET;
    if (c == ',' || c == '.' || c == '/' || c == ':' || c == 0xa0) return BC_CS;
    if (in(0x300, 0x36f) || in(0x483, 0x489) || in(0x591, 0x5bd) || c == 0x5bf || c == 0x5c1 || c == 0x5c2 || c == 0x5c4 || c == 0x5c5 || c == 0x5c7 ||
        in(0x610, 0x61a) || in(0x64b, 0x65f) || c == 0x670 || in(0x6d6, 0x6dc) || in(0x6df, 0x6e4) || c == 0x6e7 || c == 0x6e8 || in(0x6ea, 0x6ed) ||
        c == 0x711 || in(0x730, 0x74a) || in(0x7a6, 0x7b0) || in(0x7eb, 0x7f3) || in(0x8d4, 0x8ff) || in(0xfe00, 0xfe0f) || in(0xfe20, 0xfe2f)) return BC_NSM;
    if (c == 0x200f || c == 0x5be || c == 0x5c0 || c == 0x5c3 || c == 0x5c6 || in(0x5d0, 0x5ff) || in(0x7c0, 0x7ea) || in(0x7f4, 0x7ff) || in(0x800, 0x83e) || in(0xfb1d, 0xfb4f)) return BC_R;
    if (c == 0x61c || in(0x600, 0x60f) || in(0x61b, 0x64a) || in(0x66d, 0x66f) || in(0x671, 0x6d5) || c == 0x6e5 || c == 0x6e6 || c == 0x6ee || c == 0x6ef ||
        in(0x6fa, 0x710) || in(0x712, 0x72f) || in(0x74d, 0x7a5) || c == 0x7b1 || in(0x840, 0x858) || in(0x860, 0x8d3) || in(0xfb50, 0xfdff) || in(0xfe70, 0xfeff)) return BC_AL;
    if (in(0x21, 0x2f) || in(0x3a, 0x40) || in(0x5b, 0x60) || in(0x7b, 0x7e) || c == 0xa1 || c == 0xa6 || c == 0xa7 || c == 0xa9 || c == 0xab || c == 0xac || c == 0xae ||
        c == 0xb4 || c == 0xb6 || c == 0xb7 || c == 0xb8 || c == 0xbb || c == 0xbc || c == 0xbd || c == 0xbe || c == 0xbf || c == 0xd7 || c == 0xf7 ||
        in(0x2010, 0x2027) || in(0x2030, 0x205e) || in(0x2190, 0x2bff) || in(0x3001, 0x3004) || in(0x3008, 0x3020)) return BC_ON;
    return BC_L;
}
struct Bidi { std::vector<int> order, level, cls; };   /* order: visual position -> logical index */
void bidi_run(const char16_t *s, int n, int base, const uint8_t *preset, Bidi *out)
{
    std::vector<int> t((size_t)n), orig((size_t)n);
    for (int i = 0; i < n; i++) {
        int c = bidi_class(s[i]);
        if (preset) switch (preset[i]) { case 1: c = BC_L; break; case 2: c = BC_R; break; case 3: c = BC_AL; break; case 4: c = BC_ON; break; case 6: c = BC_EN; break; case 5: c = BC_AN; break; default: break; }   /* GCPCLASS_* */
        t[(size_t)i] = orig[(size_t)i] = c;
    }
    int sos = base ? BC_R : BC_L;
    for (int i = 0; i < n; i++) if (t[(size_t)i] == BC_NSM) t[(size_t)i] = i ? t[(size_t)i - 1] : sos;                         /* W1 */
    int last = sos;                                                                                                          /* W2, W3 */
    for (int i = 0; i < n; i++) { int c = t[(size_t)i]; if (c == BC_L || c == BC_R || c == BC_AL) last = c; else if (c == BC_EN && last == BC_AL) t[(size_t)i] = BC_AN; }
    for (int i = 0; i < n; i++) if (t[(size_t)i] == BC_AL) t[(size_t)i] = BC_R;
    for (int i = 1; i + 1 < n; i++) {                                                                                        /* W4 */
        int c = t[(size_t)i], a = t[(size_t)i - 1], b = t[(size_t)i + 1];
        if (c == BC_ES && a == BC_EN && b == BC_EN) t[(size_t)i] = BC_EN;
        else if (c == BC_CS && a == b && (a == BC_EN || a == BC_AN)) t[(size_t)i] = a;
    }
    for (int i = 0; i < n; i++) if (t[(size_t)i] == BC_ET) {                                                                  /* W5 */
        int j = i; while (j < n && t[(size_t)j] == BC_ET) j++;
        if ((i && t[(size_t)i - 1] == BC_EN) || (j < n && t[(size_t)j] == BC_EN)) for (int k = i; k < j; k++) t[(size_t)k] = BC_EN;
        i = j - 1;
    }
    for (int i = 0; i < n; i++) if (t[(size_t)i] == BC_ES || t[(size_t)i] == BC_ET || t[(size_t)i] == BC_CS) t[(size_t)i] = BC_ON;   /* W6 */
    last = sos;                                                                                                              /* W7 */
    for (int i = 0; i < n; i++) { int c = t[(size_t)i]; if (c == BC_L || c == BC_R) last = c; else if (c == BC_EN && last == BC_L) t[(size_t)i] = BC_L; }
    auto neutral = [&](int c) { return c == BC_B || c == BC_S || c == BC_WS || c == BC_ON || c == BC_BN; };
    auto dir = [&](int c) { return c == BC_L ? (int)BC_L : (c == BC_R || c == BC_EN || c == BC_AN) ? (int)BC_R : -1; };
    for (int i = 0; i < n; i++) if (neutral(t[(size_t)i])) {                                                                   /* N1, N2 */
        int j = i; while (j < n && neutral(t[(size_t)j])) j++;
        int before = i ? dir(t[(size_t)i - 1]) : sos, after = j < n ? dir(t[(size_t)j]) : sos;
        int d = before == after && before >= 0 ? before : sos;
        for (int k = i; k < j; k++) t[(size_t)k] = d;
        i = j - 1;
    }
    out->level.assign((size_t)n, base);
    for (int i = 0; i < n; i++) {                                                                                            /* I1, I2 */
        int c = t[(size_t)i], &lv = out->level[(size_t)i];
        if (!(lv & 1)) { if (c == BC_R) lv += 1; else if (c == BC_AN || c == BC_EN) lv += 2; }
        else if (c == BC_L || c == BC_EN || c == BC_AN) lv += 1;
    }
    for (int i = n - 1; i >= 0 && (orig[(size_t)i] == BC_WS || orig[(size_t)i] == BC_BN); i--) out->level[(size_t)i] = base;   /* L1 */
    for (int i = 0; i < n; i++) if (orig[(size_t)i] == BC_S || orig[(size_t)i] == BC_B) {
        out->level[(size_t)i] = base;
        for (int k = i - 1; k >= 0 && (orig[(size_t)k] == BC_WS || orig[(size_t)k] == BC_BN); k--) out->level[(size_t)k] = base;
    }
    out->order.resize((size_t)n);
    for (int i = 0; i < n; i++) out->order[(size_t)i] = i;
    int hi = 0, lo = 255;
    for (int v : out->level) { hi = std::max(hi, v); if (v & 1) lo = std::min(lo, v); }
    for (int lv = hi; lv >= lo && lv > 0; lv--)                                                                              /* L2 */
        for (int i = 0; i < n; i++) if (out->level[(size_t)out->order[(size_t)i]] >= lv) {
            int j = i; while (j < n && out->level[(size_t)out->order[(size_t)j]] >= lv) j++;
            std::reverse(out->order.begin() + i, out->order.begin() + j);
            i = j;
        }
    out->cls = orig;
}
char16_t mirror(char16_t c)   /* Bidi_Mirrored pairs of the Basic Latin and Latin-1 blocks */
{
    switch (c) { case '(': return ')'; case ')': return '('; case '<': return '>'; case '>': return '<'; case '[': return ']'; case ']': return '['; case '{': return '}'; case '}': return '{';
                 case 0xab: return 0xbb; case 0xbb: return 0xab; default: return c; }
}

/* ---- glyph bitmaps for drawing: the GetGlyphOutline bitmap in the DC's antialiasing format */
unsigned aa_flags_of(RF *f, int bpp)
{
    if (bpp <= 8 || f->lf.q == 3 /* NONANTIALIASED_QUALITY */) return 1;   /* GGO_BITMAP */
    if (!f->fake_bold && !f->fake_italic && FT_Get_TrueType_Engine_Type(g_lib) == FT_TRUETYPE_ENGINE_TYPE_PATENTED) {
        FT_ULong len = 0;
        std::vector<uint8_t> t;
        if (!FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('g', 'a', 's', 'p'), 0, NULL, &len) && len >= 8) {
            t.resize(len);
            FT_Load_Sfnt_Table(f->ft, FT_MAKE_TAG('g', 'a', 's', 'p'), 0, t.data(), &len);
            unsigned ver = t[0] << 8 | t[1], nr = t[2] << 8 | t[3];
            if (ver <= 1 && len >= (nr * 2 + 2) * 2) {
                unsigned flags = 0;
                for (unsigned i = 0; i < nr; i++) {
                    flags = t[4 + 4 * i + 2] << 8 | t[4 + 4 * i + 3];
                    if ((unsigned)f->ft->size->metrics.y_ppem <= (unsigned)(t[4 + 4 * i] << 8 | t[4 + 4 * i + 1])) break;
                }
                if (!(flags & 2 /* GASP_DOGRAY */)) return 1;
            }
        }
    }
    return 5;   /* GGO_GRAY4_BITMAP */
}
struct GBmp { int bx = 0, by = 0, ox = 0, oy = 0, cx = 0, cy = 0, stride = 0; std::vector<uint8_t> cov; };
bool glyph_bmp(RF *f, unsigned ch, bool idx, unsigned aa, GBmp &g)   /* cache_glyph_bitmap: the glyph, else glyph 0, else the blank */
{
    unsigned fmt = aa | (idx ? 0x80 : 0), used = ch, tries[3] = { ch, 0, 0x20 };
    uint8_t gm[20];
    uint32_t ret = GERR;
    for (unsigned t : tries) { used = t; memset(gm, 0, sizeof gm); ret = glyph_outline(f, t, fmt, gm, 0, nullptr, nullptr); if (ret != GERR) break; }
    if (ret == GERR) return false;
    auto r32 = [&](int o) { int32_t v; memcpy(&v, gm + o, 4); return v; };
    g.bx = r32(0); g.by = r32(4); g.ox = r32(8); g.oy = r32(12); g.cx = (int16_t)(gm[16] | gm[17] << 8); g.cy = (int16_t)(gm[18] | gm[19] << 8);
    if (!ret) g.bx = g.by = 0;
    g.stride = ((g.bx * 8 + 31) >> 5) << 2;
    size_t size = (size_t)g.by * (size_t)g.stride;
    g.cov.assign(size ? size : 1, 0);
    if (!size) return true;
    std::vector<uint8_t> raw(size, 0);
    if (glyph_outline(f, used, fmt, gm, (uint32_t)size, raw.data(), nullptr) == GERR) return false;
    if (aa == 1) {   /* 1 bpp rows -> 0 / 0x10 bytes */
        int sstride = ((g.bx + 31) >> 5) << 2;
        for (int y = 0; y < g.by; y++) for (int x = 0; x < g.bx; x++) g.cov[(size_t)y * (size_t)g.stride + (size_t)x] = (raw[(size_t)y * (size_t)sstride + (size_t)(x >> 3)] >> (7 - (x & 7))) & 1 ? 0x10 : 0;
    } else g.cov = raw;
    return true;
}
void render_string(HDC hdc, RF *f, unsigned aa, int x, int y, UINT flags, const char16_t *s, UINT count, const int *dx, const int *clip, uint32_t color)
{
    for (UINT i = 0; i < count; i++) {
        GBmp g;
        if (!glyph_bmp(f, s[i], (flags & TX_GLYPH_INDEX) != 0, aa, g)) continue;
        if (g.bx > 0 && g.by > 0) gdi_dc_glyph(hdc, x + g.ox, y - g.oy, g.bx, g.by, g.cov.data(), g.stride, color, clip);
        if (dx) { if (flags & TX_PDY) { x += dx[2 * i]; y += dx[2 * i + 1]; } else x += dx[i]; }
        else { x += g.cx; y += g.cy; }
    }
}

/* ---- outline text metrics (freetype_set_outline_text_metrics + scale_outline_font_metrics), device units */
struct OTMv {
    TMW tm; uint8_t panose[10]; uint32_t fsSel, fsType; int32_t rise, run, italic, em, asc, desc, gap, cap, xh, box[4], macA, macD, macGap, minppem,
        sub[2], suboff[2], sup[2], supoff[2], strikeSize, strikePos, underSize, underPos;
};
struct OTMs { std::string family, face, style, full; };   /* 8-bit (the system ANSI code page) */
bool otm_dev(RF *f, OTMv *o, OTMs *s)
{
    if (!f->scalable || !outline_metrics(f)) return false;
    FT_Face ft = f->ft;
    TT_OS2 *os2 = (TT_OS2 *)FT_Get_Sfnt_Table(ft, FT_SFNT_OS2);
    TT_HoriHeader *hh = (TT_HoriHeader *)FT_Get_Sfnt_Table(ft, FT_SFNT_HHEA);
    TT_Postscript *post = (TT_Postscript *)FT_Get_Sfnt_Table(ft, FT_SFNT_POST);
    TT_Header *head = (TT_Header *)FT_Get_Sfnt_Table(ft, FT_SFNT_HEAD);
    TMW dev;
    if (!os2 || !hh || !get_tm(f, &dev)) return false;
    FT_Fixed em = (FT_Fixed)FT_MulDiv(f->ppem, 1 << 16, ft->units_per_EM);
    double sy = f->scale_y, sx = f->aveWidth ? (double)f->aveWidth / f->tm.aw : sy;   /* scale_outline_font_metrics, identity world matrix */
    auto SX = [&](long v) { return rnd((int)FT_MulFix(v, em) * sx); };
    auto SY = [&](long v) { return rnd((int)FT_MulFix(v, em) * sy); };
    memset(o, 0, sizeof *o);
    o->tm = dev;
    memcpy(o->panose, os2->panose, 10);
    o->fsSel = os2->fsSelection | (f->fake_italic ? 1u : 0u) | (f->fake_bold ? 1u << 5 : 0u);
    o->fsType = os2->fsType & 0x30e;
    o->rise = hh->caret_Slope_Rise; o->run = hh->caret_Slope_Run;
    o->italic = post ? rnd((double)post->italicAngle / 65536.0 * 10.0) : 0;   /* tenths of a degree, from the post table */
    o->em = ft->units_per_EM;
    o->asc = SY(os2->sTypoAscender); o->desc = SY(os2->sTypoDescender); o->gap = SY(os2->sTypoLineGap);
    o->cap = SY(os2->sCapHeight); o->xh = SY(os2->sxHeight);
    o->box[0] = SX(ft->bbox.xMin); o->box[1] = SY(ft->bbox.yMax); o->box[2] = SX(ft->bbox.xMax); o->box[3] = SY(ft->bbox.yMin);   /* left, top, right, bottom */
    o->macA = rnd(f->tm.asc * sy); o->macD = -rnd(f->tm.desc * sy); o->macGap = SY(hh->Line_Gap);
    o->minppem = head ? head->Lowest_Rec_PPEM : 0;
    o->sub[0] = SX(os2->ySubscriptXSize); o->sub[1] = SY(os2->ySubscriptYSize); o->suboff[0] = SX(os2->ySubscriptXOffset); o->suboff[1] = SY(os2->ySubscriptYOffset);
    o->sup[0] = SX(os2->ySuperscriptXSize); o->sup[1] = SY(os2->ySuperscriptYSize); o->supoff[0] = SX(os2->ySuperscriptXOffset); o->supoff[1] = SY(os2->ySuperscriptYOffset);
    o->strikeSize = SY(os2->yStrikeoutSize); o->strikePos = SY(os2->yStrikeoutPosition);
    o->underSize = post ? SY(post->underlineThickness) : 0; o->underPos = post ? SY(post->underlinePosition) : 0;
    ustr full = sfnt_name(ft, 3);   /* the unique font identifier; a font without one reports its full name */
    if (full.empty()) full = f->face->full;
    if (s) { s->family = narrow(f->family); s->face = narrow(f->face->full); s->style = narrow(f->face->style); s->full = narrow(full); }
    return true;
}
void otm_logical(const Map &m, OTMv *o)   /* NtGdiGetOutlineTextMetricsInternalW: device -> logical */
{
    TMW &t = o->tm;
    t.h = lp_h(m, t.h); t.asc = lp_h(m, t.asc); t.desc = lp_h(m, t.desc); t.il = lp_h(m, t.il); t.el = lp_h(m, t.el);
    t.aw = lp_w(m, t.aw); t.mw = lp_w(m, t.mw); t.oh = lp_w(m, t.oh);
    o->asc = lp_h(m, o->asc); o->desc = lp_h(m, o->desc); o->gap = d2w_y(m, o->gap); o->cap = d2w_y(m, o->cap); o->xh = d2w_y(m, o->xh);
    o->box[1] = lp_h(m, o->box[1]); o->box[3] = lp_h(m, o->box[3]); o->box[0] = lp_w(m, o->box[0]); o->box[2] = lp_w(m, o->box[2]);
    o->macA = lp_h(m, o->macA); o->macD = lp_h(m, o->macD); o->macGap = d2w_y(m, o->macGap);
    o->sub[0] = lp_w(m, o->sub[0]); o->sub[1] = lp_h(m, o->sub[1]); o->suboff[0] = lp_w(m, o->suboff[0]); o->suboff[1] = lp_h(m, o->suboff[1]);
    o->sup[0] = lp_w(m, o->sup[0]); o->sup[1] = lp_h(m, o->sup[1]); o->supoff[0] = lp_w(m, o->supoff[0]); o->supoff[1] = lp_h(m, o->supoff[1]);
    o->strikeSize = d2w_y(m, o->strikeSize); o->strikePos = lp_h(m, o->strikePos); o->underSize = lp_h(m, o->underSize); o->underPos = lp_h(m, o->underPos);
}

/* ---- ExtTextOut */
BOOL ext_text_out(HDC hdc, int x, int y, UINT flags, const RECT *rect, const char16_t *str, UINT count, const int *lpdx)
{
    if (count > 0x7fffffffu) return FALSE;
    FL l;
    if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    if (count && !str) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    RF *f = cur(hdc);
    Map m;
    TMW tm;
    LF lf;
    if (!f || !dc_map(hdc, &m) || !get_tm(f, &tm) || !lf_of((void *)(uintptr_t)gdi_dc_font(hdc), &lf)) return FALSE;
    int32_t align = 0, bkmode = 2, bk = 0, tc = 0, cx = 0, cy = 0;
    gdi_dc_state(hdc, GDI_TS_ALIGN, 0, 0, &align); gdi_dc_state(hdc, GDI_TS_BKMODE, 0, 0, &bkmode); gdi_dc_state(hdc, GDI_TS_BKCOLOR, 0, 0, &bk);
    gdi_dc_state(hdc, GDI_TS_TEXTCOLOR, 0, 0, &tc); gdi_dc_state(hdc, GDI_TS_POSX, 0, 0, &cx); gdi_dc_state(hdc, GDI_TS_POSY, 0, 0, &cy);
    if (flags & TX_RTLREADING) align |= TX_RTL;
    /* RTL fonts: ordering by the bidirectional algorithm (the base direction is the reading order), unless
     * ETO_GLYPH_INDEX / ETO_IGNORELANGUAGE; for any other font the flag changes only the alignment above */
    std::vector<char16_t> vis;
    std::vector<int> dxv;
    if (!(flags & (TX_GLYPH_INDEX | TX_IGNORELANGUAGE)) && count > 0 && (f->charset == HEBREW_CS || f->charset == ARABIC_CS)) {
        Bidi b;
        bidi_run(str, (int)count, (align & TX_RTL) ? 1 : 0, nullptr, &b);
        vis.resize(count);
        for (UINT i = 0; i < count; i++) { char16_t c = str[b.order[i]]; vis[i] = (b.level[(size_t)b.order[i]] & 1) ? mirror(c) : c; }
        if (lpdx) {
            size_t per = (flags & TX_PDY) ? 2 : 1;
            dxv.resize(count * per);
            for (UINT i = 0; i < count; i++) for (size_t k = 0; k < per; k++) dxv[i * per + k] = lpdx[(size_t)b.order[i] * per + k];
            lpdx = dxv.data();
        }
        str = vis.data();
        flags |= TX_IGNORELANGUAGE;
    }
    if (align & TX_UPDATECP) { x = cx; y = cy; }
    double cosE = 1, sinE = 0;
    int esc = (tm.pf & 2) ? lf.esc : 0;   /* a bitmap font is never rotated */
    if (esc) { cosE = cos(esc * M_PI / 1800); sinE = sin(esc * M_PI / 1800); }
    int rcl = 0, rct = 0, rcr = 0, rcb = 0;
    uint32_t color = (uint32_t)tc & 0xffffff, bkc = (uint32_t)bk & 0xffffff;
    if (rect && (flags & (TX_OPAQUE | TX_CLIPPED))) {
        int a = rnd(rect->left * m.fx), b = rnd(rect->top * m.fy), c = rnd(rect->right * m.fx), d = rnd(rect->bottom * m.fy);
        rcl = std::min(a, c); rcr = std::max(a, c); rct = std::min(b, d); rcb = std::max(b, d);
        if (flags & TX_OPAQUE) gdi_dc_fill(hdc, rcl, rct, rcr, rcb, bkc);
    } else flags &= ~(UINT)TX_CLIPPED;
    if (!count) return TRUE;
    x = rnd(x * m.fx); y = rnd(y * m.fy);
    std::vector<int> deltas;   /* x, y pairs */
    int wx = 0, wy = 0;
    SIZE sz;
    if (lpdx || esc) {
        deltas.assign(2 * (size_t)count, 0);
        if (lpdx) for (UINT i = 0; i < count; i++) {
            deltas[2 * i] = lpdx[(flags & TX_PDY) ? 2 * i : i];
            deltas[2 * i + 1] = (flags & TX_PDY) ? -lpdx[2 * i + 1] : 0;
        } else {
            std::vector<int> dx(count);
            extent_core(hdc, f, str, (int)count, -1, nullptr, dx.data(), &sz, (flags & TX_GLYPH_INDEX) != 0);
            for (UINT i = 0; i < count; i++) deltas[2 * i] = dx[i] - (i ? dx[i - 1] : 0);
        }
        int totx = 0, toty = 0;
        for (UINT i = 0; i < count; i++) {
            totx += deltas[2 * i]; toty += deltas[2 * i + 1];
            int ex = (int)(cosE * totx + sinE * toty), ey = (int)(-sinE * totx + cosE * toty);
            ex = rnd(ex * m.fx); ey = rnd(ey * m.fy);   /* lp_to_dp of the vector (origin 0) */
            deltas[2 * i] = ex - wx; deltas[2 * i + 1] = ey - wy;
            wx = ex; wy = ey;
        }
        flags |= TX_PDY;
    } else {
        extent_core(hdc, f, str, (int)count, 0, nullptr, nullptr, &sz, (flags & TX_GLYPH_INDEX) != 0);
        wx = rnd(sz.cx * m.fx);
    }
    int asc = abs(w2d_y(m, lp_h(m, tm.asc))), desc = abs(w2d_y(m, lp_h(m, tm.desc)));
    switch (align & (TX_RIGHT | TX_CENTER)) {   /* TA_LEFT | TA_RIGHT | TA_CENTER */
    case 0:
        if (align & TX_UPDATECP) { gdi_dc_state(hdc, GDI_TS_POSX, 1, rnd((x + wx) * m.ix), NULL); gdi_dc_state(hdc, GDI_TS_POSY, 1, rnd((y + wy) * m.iy), NULL); }
        break;
    case TX_CENTER: x -= wx / 2; y -= wy / 2; break;
    case TX_RIGHT:
        x -= wx; y -= wy;
        if (align & TX_UPDATECP) { gdi_dc_state(hdc, GDI_TS_POSX, 1, rnd(x * m.ix), NULL); gdi_dc_state(hdc, GDI_TS_POSY, 1, rnd(y * m.iy), NULL); }
        break;
    default: break;
    }
    switch (align & TX_BASELINE) {   /* TA_TOP | TA_BOTTOM | TA_BASELINE */
    case 0: y += (int)(asc * cosE); x += (int)(asc * sinE); break;
    case TX_BOTTOM: y -= (int)(desc * cosE); x -= (int)(desc * sinE); break;
    default: break;
    }
    int bpp = gdi_dc_depth(hdc);
    if (bkmode != 1 /* TRANSPARENT */ && !((flags & TX_CLIPPED) && (flags & TX_OPAQUE)) &&
        (!(flags & TX_OPAQUE) || !rect || x < rcl || x + wx >= rcr || y - asc < rct || y + desc >= rcb)) {
        int bl = x, bt = y - asc, br = x + wx, bb = y + desc;
        if (flags & TX_CLIPPED) { bl = std::max(bl, rcl); bt = std::max(bt, rct); br = std::min(br, rcr); bb = std::min(bb, rcb); }
        if (br > bl && bb > bt) gdi_dc_fill(hdc, bl, bt, br, bb, bkc);
    }
    int clip[4] = { rcl, rct, rcr, rcb };
    render_string(hdc, f, aa_flags_of(f, bpp), x, y, flags & ~(UINT)TX_OPAQUE, str, count, deltas.empty() ? nullptr : deltas.data(), (flags & TX_CLIPPED) ? clip : nullptr, color);
    if (lf.ul || lf.so) {
        OTMv o;
        int upos = 0, uw = asc / 20 + 1, spos = asc / 2, sw = uw;   /* no outline metrics: the fixed rule on the ascent */
        if (otm_dev(f, &o, nullptr)) {
            otm_logical(m, &o);
            auto lw = [&](int v) { int w = abs(w2d_y(m, v)); if (!w) w = 1; return v < 0 ? -w : w; };
            upos = abs(w2d_y(m, o.underPos)); if (o.underPos < 0) upos = -upos;
            uw = lw(o.underSize);
            spos = abs(w2d_y(m, o.strikePos)); if (o.strikePos < 0) spos = -spos;
            sw = lw(o.strikeSize);
        }
        auto line = [&](int pos, int w) {
            int32_t p[8];
            p[0] = (int)(x - (pos + w / 2) * sinE); p[1] = (int)(y - (pos + w / 2) * cosE);
            p[2] = (int)(x + wx - (pos + w / 2) * sinE); p[3] = (int)(y + wy - (pos + w / 2) * cosE);
            p[4] = (int)(p[2] + w * sinE); p[5] = (int)(p[3] + w * cosE);
            p[6] = (int)(p[0] + w * sinE); p[7] = (int)(p[1] + w * cosE);
            if (!esc) gdi_dc_fill(hdc, p[0], std::min(p[1], p[7]), p[2], std::max(p[1], p[7]), color);
            else gdi_dc_poly(hdc, p, 4, color);
        };
        if (lf.ul) line(upos, uw);
        if (lf.so) line(spos, sw);
    }
    return TRUE;
}
BOOL ext_text_out_a(HDC hdc, int x, int y, UINT flags, const RECT *rect, const char *str, UINT count, const int *dx)
{
    if (count > 0x7fffffffu) return FALSE;
    if (flags & TX_GLYPH_INDEX) return ext_text_out(hdc, x, y, flags, rect, (const char16_t *)str, count, dx);
    if (count && !str) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    RF *f;
    {
        FL l;
        if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
        f = cur(hdc);
    }
    ustr w = mb2w(f, str ? str : "", (int)count);
    std::vector<int> dxw;
    if (dx) {
        std::vector<int> lens = mb_lens(f, str, (int)count);
        size_t per = (flags & TX_PDY) ? 2 : 1, i = 0;
        for (int len : lens) {
            for (size_t k = 0; k < per; k++) { int v = dx[i * per + k]; if (len == 2) v += dx[(i + 1) * per + k]; dxw.push_back(v); }
            i += (size_t)len;
        }
    }
    return ext_text_out(hdc, x, y, flags, rect, w.data(), (UINT)w.size(), dx ? dxw.data() : nullptr);
}

/* ---- GetCharacterPlacement */
struct GCPR { uint32_t lStructSize; char *lpOutString; uint32_t *lpOrder; int *lpDx; int *lpCaretPos; char *lpClass; uint16_t *lpGlyphs; uint32_t nGlyphs; int nMaxFit; };
DWORD placement_w(HDC hdc, RF *f, const char16_t *str, int count, int max_extent, GCPR *res, DWORD flags, std::vector<char16_t> *outw)
{
    Map m;
    TMW tm;
    SIZE size;
    if (!dc_map(hdc, &m) || !get_tm(f, &tm)) return 0;
    if (!res) return extent_core(hdc, f, str, count, 0, nullptr, nullptr, &size, false) ? (DWORD)MAKELONG(size.cx, size.cy) : 0;
    unsigned n = std::min<unsigned>((unsigned)count, res->nGlyphs);
    /* advances in logical units; kern[i] is the kerning between character i and i + 1 */
    std::vector<int> adv((size_t)count), kern((size_t)count, 0);
    {
        std::vector<int> pos;
        int cx, cy;
        positions(f, str, count, false, pos, &cx, &cy);
        for (int i = 0; i < count; i++) adv[(size_t)i] = d2w_x(m, pos[(size_t)i]) - (i ? d2w_x(m, pos[(size_t)i - 1]) : 0);
    }
    if (flags & GX_USEKERNING) for (int i = 0; i + 1 < count; i++) kern[(size_t)i] = d2w_x(m, kern_pair(f, str[i], str[i + 1]));
    int proc = count;   /* the characters processed: the ones that fit into nMaxExtent with GCP_MAXEXTENT */
    if (flags & GX_MAXEXTENT) {
        long acc = 0;
        proc = 0;
        for (int i = 0; i < count; i++) {
            if (acc + adv[(size_t)i] > max_extent) break;
            acc += adv[(size_t)i] + kern[(size_t)i];
            proc++;
        }
        n = std::min<unsigned>(n, (unsigned)proc);
        res->nMaxFit = proc;
    }
    res->nGlyphs = n;
    int kern_total = 0;
    for (int i = 0; i + 1 < proc; i++) kern_total += kern[(size_t)i];
    Bidi b;
    bool reorder = (flags & GX_REORDER) != 0;
    if (reorder) bidi_run(str, count, 0, (flags & GX_CLASSIN) && res->lpClass ? (const uint8_t *)res->lpClass : nullptr, &b);
    else {
        b.order.resize((size_t)count); b.level.assign((size_t)count, 0); b.cls.resize((size_t)count);
        for (int i = 0; i < count; i++) { b.order[(size_t)i] = i; b.cls[(size_t)i] = bidi_class(str[i]); }
    }
    std::vector<char16_t> outs(n);
    for (unsigned j = 0; j < n; j++) {
        int li = b.order[j];
        char16_t c = str[li];
        outs[j] = (reorder && !(flags & GX_SYMSWAPOFF) && (b.level[(size_t)li] & 1)) ? mirror(c) : c;
    }
    if (res->lpOutString && outw) *outw = outs;
    if (res->lpOrder) for (unsigned j = 0; j < n; j++) res->lpOrder[j] = (uint32_t)b.order[j];
    if (res->lpClass && !(flags & GX_CLASSIN)) for (unsigned j = 0; j < n; j++) {
        switch (b.cls[(size_t)b.order[j]]) {   /* GCPCLASS_* */
        case BC_L: res->lpClass[j] = 1; break;  case BC_R: res->lpClass[j] = 2; break;  case BC_AL: res->lpClass[j] = 3; break;
        case BC_EN: res->lpClass[j] = 6; break; case BC_AN: res->lpClass[j] = 5; break; case BC_ET: res->lpClass[j] = 7; break;
        case BC_ES: case BC_CS: res->lpClass[j] = 8; break;
        default: res->lpClass[j] = 4; break;
        }
    }
    std::vector<int> dxs(n);
    for (unsigned j = 0; j < n; j++) { int li = b.order[j]; dxs[j] = adv[(size_t)li] + kern[(size_t)li]; }
    if ((flags & GX_JUSTIFY) && (flags & GX_MAXEXTENT)) {   /* the spare room goes to the break characters, the remainder to the first ones (SetTextJustification) */
        long total = 0;
        unsigned blanks = 0;
        for (unsigned j = 0; j < n; j++) { total += dxs[j]; if (outs[j] == tm.bc) blanks++; }
        long extra = (long)max_extent - total;
        if (extra > 0 && blanks) {
            long q = extra / (long)blanks, r = extra % (long)blanks;
            for (unsigned j = 0; j < n; j++) if (outs[j] == tm.bc) { dxs[j] += (int)q + (r > 0 ? 1 : 0); if (r > 0) r--; }
        }
    }
    if (res->lpDx) for (unsigned j = 0; j < n; j++) res->lpDx[j] = dxs[j];
    if (res->lpCaretPos) {   /* the caret position before each logical character: the leading edge of its glyph */
        std::vector<int> vpos(n);
        int acc = 0;
        for (unsigned j = 0; j < n; j++) { vpos[j] = acc; acc += dxs[j]; }
        for (unsigned j = 0; j < n; j++) { int li = b.order[j]; if ((unsigned)li < n) res->lpCaretPos[li] = (b.level[(size_t)li] & 1) ? vpos[j] + dxs[j] : vpos[j]; }
    }
    if (res->lpGlyphs) {   /* GetGlyphIndices: the cmap glyph, the default character's glyph for an unmapped one */
        unsigned dflt = glyph_index(f, tm.dc, false);
        for (unsigned j = 0; j < n; j++) { unsigned g = glyph_index(f, outs[j], false); res->lpGlyphs[j] = (uint16_t)(g ? g : dflt); }
    }
    if (!extent_core(hdc, f, str, proc, 0, nullptr, nullptr, &size, false)) return 0;
    return (DWORD)MAKELONG(size.cx + kern_total, size.cy);
}
} // namespace

/* ---- exports */
extern "C" BOOL ExtTextOutW(HDC hdc, int x, int y, UINT flags, const RECT *rect, LPCWSTR str, UINT count, const INT *dx)
{
    return ext_text_out(hdc, x, y, flags, rect, (const char16_t *)str, count, dx);
}
extern "C" BOOL ExtTextOutA(HDC hdc, int x, int y, UINT flags, const RECT *rect, LPCSTR str, UINT count, const INT *dx)
{
    return ext_text_out_a(hdc, x, y, flags, rect, str, count, dx);
}
extern "C" BOOL GetTextExtentExPointW(HDC hdc, LPCWSTR str, int count, int max_ext, LPINT nfit, LPINT dxs, SIZE *size)
{
    return text_extent_w(hdc, (const char16_t *)str, count, max_ext, nfit, dxs, size, false);
}
extern "C" BOOL GetTextExtentPoint32W(HDC hdc, LPCWSTR str, int count, SIZE *size)
{
    return text_extent_w(hdc, (const char16_t *)str, count, 0, nullptr, nullptr, size, false);
}
extern "C" BOOL GetTextExtentExPointA(HDC hdc, LPCSTR str, int count, int max_ext, LPINT nfit, LPINT dxs, SIZE *size)
{
    if (count < 0 || max_ext < -1 || (count && !str) || !size) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    RF *f;
    {
        FL l;
        if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
        f = cur(hdc);
    }
    ustr w = mb2w(f, str ? str : "", count);
    std::vector<int> wd(w.size());
    int fit = 0;
    if (!text_extent_w(hdc, w.data(), (int)w.size(), max_ext, nfit ? &fit : nullptr, dxs ? wd.data() : nullptr, size, false)) return FALSE;
    std::vector<int> lens = mb_lens(f, str ? str : "", count);
    size_t n = nfit ? (size_t)fit : w.size(), j = 0;
    if (dxs) for (size_t i = 0; i < n && i < lens.size(); i++) { for (int k = 0; k < lens[i]; k++) dxs[j++] = wd[i]; }   /* both bytes of a DBCS character get its value */
    if (nfit) { int bytes = 0; for (size_t i = 0; i < (size_t)fit && i < lens.size(); i++) bytes += lens[i]; *nfit = bytes; }
    return TRUE;
}
extern "C" BOOL GetTextExtentPoint32A(HDC hdc, LPCSTR str, int count, SIZE *size) { return GetTextExtentExPointA(hdc, str, count, 0, NULL, NULL, size); }

/* GetCharacterPlacement: GCP_RESULTS in the guest layout, pointers at native width */
static DWORD placement(HDC hdc, const char *stra, const char16_t *strw, int count, int max_extent, void *results, DWORD flags)
{
    if (count == 0) return 0;
    if (stra && count == -1) count = (int)strlen(stra);   /* the 8-bit entry takes -1 for a terminated string */
    if (count < 0 || (!stra && !strw)) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    GCPR *res = (GCPR *)results;
    if (res && res->lStructSize != sizeof(GCPR)) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    FL l;
    if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return 0; }
    RF *f = cur(hdc);
    if (!f) return 0;
    int nbytes = count;
    ustr w;
    if (stra) { w = mb2w(f, stra, count); strw = w.data(); count = (int)w.size(); }
    std::vector<char16_t> outw;
    DWORD r = placement_w(hdc, f, strw, count, max_extent, res, flags, &outw);
    if (stra && res && res->lpOutString && !outw.empty())   /* back to 8 bits in the font's code page, at most nCount bytes */
        WideCharToMultiByte((UINT)f->codepage, 0, (LPCWSTR)outw.data(), (int)outw.size(), res->lpOutString, nbytes, NULL, NULL);
    return r;
}
extern "C" DWORD GetCharacterPlacementW(HDC hdc, LPCWSTR str, int count, int max_extent, void *results, DWORD flags)
{
    return placement(hdc, nullptr, (const char16_t *)str, count, max_extent, results, flags);
}
extern "C" DWORD GetCharacterPlacementA(HDC hdc, LPCSTR str, int count, int max_extent, void *results, DWORD flags)
{
    return placement(hdc, str, nullptr, count, max_extent, results, flags);
}

/* GetOutlineTextMetricsA: OUTLINETEXTMETRICA, strings (family, face, style, full) appended behind the structure; the
 * otmp* fields hold their offsets from the start. A font that is not an outline (TrueType / OpenType) font: 0. */
struct OTMA {
    uint32_t otmSize; uint8_t tm[56]; uint8_t filler, panose[10]; uint32_t fsSelection, fsType; int32_t rise, run, italic; uint32_t em; int32_t asc, desc;
    uint32_t gap, cap, xh; int32_t box[4]; int32_t macA, macD; uint32_t macGap, minppem; int32_t sub[2], suboff[2], sup[2], supoff[2];
    uint32_t strikeSize; int32_t strikePos, underSize, underPos; char *pFamily, *pFace, *pStyle, *pFull;
};
extern "C" UINT GetOutlineTextMetricsA(HDC hdc, UINT cb, void *buf)
{
    FL l;
    if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return 0; }
    RF *f = cur(hdc);
    Map m;
    OTMv o;
    OTMs s;
    if (!f || !dc_map(hdc, &m) || !otm_dev(f, &o, &s)) return 0;
    otm_logical(m, &o);
    size_t needed = sizeof(OTMA) + s.family.size() + s.face.size() + s.style.size() + s.full.size() + 4;
    if (!buf || !cb) return (UINT)needed;
    std::vector<uint8_t> tmp(needed, 0);
    OTMA *a = (OTMA *)tmp.data();
    const TMW &t = o.tm;   /* text_metric_WtoA */
    memcpy(a->tm, &t, 44);
    uint8_t first = (uint8_t)std::min<int>(t.fc, 255), last;
    if (t.cs == SYMBOL_CS) { first = 0x1e; last = 0xff; }
    else if (t.pf & 4) { first = (uint8_t)(t.dc - 1); last = (uint8_t)std::min<int>(t.lc, 255); }
    else last = (uint8_t)std::min<int>(t.lc, 255);
    a->tm[44] = first; a->tm[45] = last; a->tm[46] = (uint8_t)t.dc; a->tm[47] = (uint8_t)t.bc; a->tm[48] = t.it; a->tm[49] = t.ul; a->tm[50] = t.so; a->tm[51] = t.pf; a->tm[52] = t.cs;
    memcpy(a->panose, o.panose, 10);
    a->fsSelection = o.fsSel; a->fsType = o.fsType; a->rise = o.rise; a->run = o.run; a->italic = o.italic; a->em = (uint32_t)o.em;
    a->asc = o.asc; a->desc = o.desc; a->gap = (uint32_t)o.gap; a->cap = (uint32_t)o.cap; a->xh = (uint32_t)o.xh;
    memcpy(a->box, o.box, sizeof a->box); a->macA = o.macA; a->macD = o.macD; a->macGap = (uint32_t)o.macGap; a->minppem = (uint32_t)o.minppem;
    memcpy(a->sub, o.sub, 8); memcpy(a->suboff, o.suboff, 8); memcpy(a->sup, o.sup, 8); memcpy(a->supoff, o.supoff, 8);
    a->strikeSize = (uint32_t)o.strikeSize; a->strikePos = o.strikePos; a->underSize = o.underSize; a->underPos = o.underPos;
    size_t off = sizeof(OTMA);
    const std::string *str[4] = { &s.family, &s.face, &s.style, &s.full };
    char **slot[4] = { &a->pFamily, &a->pFace, &a->pStyle, &a->pFull };
    for (int i = 0; i < 4; i++) { *slot[i] = (char *)(uintptr_t)off; memcpy(tmp.data() + off, str[i]->c_str(), str[i]->size() + 1); off += str[i]->size() + 1; }
    size_t n = std::min<size_t>(needed, cb);   /* a short buffer is filled as far as it goes */
    a->otmSize = (uint32_t)n;
    memcpy(buf, tmp.data(), n);
    return (UINT)n;
}

/* ---- by glyph index (d3dx9's font code): the same glyph path as GetGlyphOutline / GetTextExtentPoint (glyph_abc), the
 * logical conversion of NtGdiGetCharABCWidthsW with NTGDI_GETCHARABCWIDTHS_INT (each of A, B, C scaled on its own and
 * not mirrored: lp_w), the width of GetCharWidthI their sum (NtGdiGetCharWidthW with NTGDI_GETCHARWIDTH_INDICES).
 * gi NULL: consecutive indices from first. cgi == 0 succeeds. A NULL output buffer fails with ERROR_INVALID_PARAMETER;
 * ABC of a font that is not an outline (TrueType / OpenType) font fails (MSDN: only for TrueType fonts); an index the
 * face does not have yields 0, 0, 0. */
template <class Sink> static bool glyph_widths_i(HDC hdc, UINT first, UINT cgi, const uint16_t *gi, bool need_outline, Sink sink)
{
    FL l;
    if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return false; }
    RF *f = cur(hdc);
    Map m;
    if (!f || !dc_map(hdc, &m)) return false;
    if (need_outline && !f->scalable) return false;
    for (UINT i = 0; i < cgi; i++) {
        int32_t abc[3];
        glyph_abc(f, gi ? gi[i] : (unsigned)(first + i), true, abc);
        sink(i, lp_w(m, abc[0]), lp_w(m, abc[1]), lp_w(m, abc[2]));
    }
    return true;
}
extern "C" BOOL GetCharABCWidthsI(HDC hdc, UINT first, UINT cgi, uint16_t *gi, void *abc)
{
    if (!abc) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    int32_t *o = (int32_t *)abc;   /* ABC {int, UINT, int}: 3 x 32 bits */
    return glyph_widths_i(hdc, first, cgi, gi, true, [&](UINT i, int a, int b, int c) { o[3 * (size_t)i] = a; o[3 * (size_t)i + 1] = b; o[3 * (size_t)i + 2] = c; });
}
extern "C" BOOL GetCharWidthI(HDC hdc, UINT first, UINT cgi, uint16_t *gi, int *widths)
{
    if (!widths) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    return glyph_widths_i(hdc, first, cgi, gi, false, [&](UINT i, int a, int b, int c) { widths[i] = a + b + c; });
}

/* GetFontUnicodeRanges: GLYPHSET {cbThis, flAccel, cGlyphsSupported, cRanges, WCRANGE ranges[]} from the realised face's
 * Unicode cmap (BMP only: WCRANGE holds a WCHAR), contiguous runs of characters that have a glyph, ascending (reference:
 * Wine 9.0 win32u freetype.c freetype_get_unicode_ranges and font.c font_GetFontUnicodeRanges). lpgs NULL: only the
 * size. flAccel is 0 (the reference); a font whose charmap is not Unicode has no ranges (cRanges 0, size 16: the
 * reference returns the empty set's size). A run is cut at 65535 characters (WCRANGE.cGlyphs is 16 bits).
 * Returns the size in bytes; 0 for an invalid DC. The buffer must hold the size (the API has no size argument). */
extern "C" DWORD GetFontUnicodeRanges(HDC hdc, void *lpgs)
{
    FL l;
    if (!gdi_dc_font(hdc)) { SetLastError(ERROR_INVALID_HANDLE); return 0; }
    RF *f = cur(hdc);
    if (!f) return 0;
    std::vector<std::pair<uint16_t, uint16_t>> runs;   /* wcLow, cGlyphs */
    uint32_t total = 0;
    FT_Face ft = f->ft;
    if (ft->charmap && ft->charmap->encoding == FT_ENCODING_UNICODE) {
        FT_UInt g = 0;
        FT_ULong c = FT_Get_First_Char(ft, &g), prev = 0;
        while (g && c <= 0xFFFF) {
            if (runs.empty() || c != prev + 1 || runs.back().second == 0xFFFF)
                runs.push_back({ (uint16_t)c, 1 });
            else
                runs.back().second++;
            total++;
            prev = c;
            c = FT_Get_Next_Char(ft, c, &g);
        }
    }
    DWORD size = (DWORD)(16 + 4 * runs.size());
    if (lpgs) {
        uint8_t *p = (uint8_t *)lpgs;
        put32(p, size); put32(p + 4, 0); put32(p + 8, total); put32(p + 12, (uint32_t)runs.size());
        for (size_t i = 0; i < runs.size(); i++) { put16(p + 16 + 4 * i, runs[i].first); put16(p + 18 + 4 * i, runs[i].second); }
    }
    return size;
}
