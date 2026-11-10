/* gdifont.cpp - GDI fonts, stage 1: font objects and the stock fonts, the face list, font matching, text metrics,
 * charset translation and GetGlyphOutline. The results equal the reference environment's (Wine 9.0 on Ubuntu 24.04)
 * by construction: the same rasteriser (FreeType 2.13.2 with Debian's patches, ext/freetype via
 * tools/fetch_freetype.sh), the same font files, and Wine's own rules (own code following them) for the face list
 * (family/face ordering, bitmap sizes, signatures), the LOGFONT -> face match, the realised size, the metrics and the
 * GetGlyphOutline layouts. Layout of the structures is the guest's fixed 32-bit one (byte offsets, LOGFONTW = 92,
 * TEXTMETRICW = 60, GLYPHMETRICS = 20, CHARSETINFO = 32 bytes).
 *
 * Fonts come from three directories given once by fnvnx_gdi_set_font_dirs: the Wine fonts directory (the .fon
 * bitmap fonts of the registry and system lists, the TrueType files), the host fonts (every sfnt file below it,
 * what fontconfig lists in the reference) and the registry export directory (FontSubstitutes, Fonts, the hardware
 * profile Fonts key, Wine\Fonts\Replacements, CodePage\ACP, as .reg files).
 *
 * Stage 1 assumptions (stated, not hidden): display DC at 96 dpi, MM_TEXT, identity world transform; system locale
 * en-US (name language 0x409); TC_RA_ABLE (bitmap fonts allowed); no vertical (@) families; no FontLink, no
 * fontconfig-derived family fallbacks beyond Wine's three built-in names per family class. */
#include "w32.h"
#include "gdi_int.h"

#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_TRUETYPE_TABLES_H
#include FT_SFNT_NAMES_H
#include FT_WINFONTS_H
#include FT_OUTLINE_H
#include FT_GLYPH_H
#include FT_TRIGONOMETRY_H
#include FT_TRUETYPE_IDS_H

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

std::string g_wine_dir, g_host_dir, g_reg_dir;
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
void add_font_file(const std::string &path, bool allow_bitmap)
{
    std::vector<uint8_t> data;
    if (!read_file(path, data) || data.empty()) return;
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

/* ---- registry export files (.reg, UTF-16LE) */
void parse_reg(const std::string &path)
{
    std::vector<uint8_t> d;
    if (!read_file(path, d) || d.size() < 2) return;
    std::string txt;
    for (size_t i = 2; i + 1 < d.size(); i += 2) txt += d[i + 1] ? '?' : (char)d[i];
    std::string key;
    size_t p = 0;
    while (p < txt.size()) {
        size_t e = txt.find('\n', p);
        std::string l = txt.substr(p, e == std::string::npos ? std::string::npos : e - p);
        p = e == std::string::npos ? txt.size() : e + 1;
        while (!l.empty() && l.back() == '\r') l.pop_back();
        if (l.size() > 2 && l[0] == '[') { key = l.substr(1, l.size() - 2); for (char &c : key) c = (char)tolower((unsigned char)c); g_reg[key]; continue; }
        size_t q = l.find("\"=\"");
        if (l.size() < 6 || l[0] != '"' || q == std::string::npos || l.back() != '"') continue;
        auto unesc = [](const std::string &s) { std::string r; for (size_t i = 0; i < s.size(); i++) { if (s[i] == '\\' && i + 1 < s.size()) i++; r += s[i]; } return r; };
        g_reg[key].push_back({ unesc(l.substr(1, q - 1)), unesc(l.substr(q + 3, l.size() - q - 4)) });
    }
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
    if (os_dir *dir = os_dir_open(d.c_str())) { while (const char *n = os_dir_next(dir)) v.push_back(n); os_dir_close(dir); }
    std::sort(v.begin(), v.end());
    return v;
}
void scan_host(const std::string &d)
{
    for (const std::string &n : list_dir(d)) {
        std::string p = d + "/" + n;
        os_stat_t st;
        if (os_stat(p.c_str(), &st)) continue;
        if (st.is_dir) { scan_host(p); continue; }
        size_t dot = n.rfind('.');
        std::string ext = dot == std::string::npos ? "" : n.substr(dot);
        if (ext == ".ttf" || ext == ".ttc" || ext == ".otf") add_font_file(p, false);
    }
}
bool is_full_name(const ustr &n) { for (Family *f : g_fam) for (Face *x : f->faces) if (x->scalable && !fcmp(x->full, n, 63)) return true; return false; }
bool enum_fallback(int pf, int i, ustr *out)
{
    static const char *const fixed[3] = { "Courier New", "Liberation Mono", "Bitstream Vera Sans Mono" }, *const serif[3] = { "Times New Roman", "Liberation Serif", "Bitstream Vera Serif" },
        *const sans[3] = { "Arial", "Liberation Sans", "Bitstream Vera Sans" };
    if (i >= 3) return false;
    *out = widen(((pf & 1) || (pf & 0xf0) == 0x30) ? fixed[i] : (pf & 0xf0) == 0x10 ? serif[i] : sans[i]);
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
    if (!g_lib && FT_Init_FreeType(&g_lib)) return;
    for (const std::string &n : list_dir(g_reg_dir)) if (n.size() > 4 && n.substr(n.size() - 4) == ".reg") parse_reg(g_reg_dir + "/" + n);
    for (auto &kv : reg_key("HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Control\\Nls\\CodePage")) if (kv.first == "ACP") g_acp = atoi(kv.second.c_str());
    auto sys = reg_key("HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Hardware Profiles\\Current\\Software\\Fonts");
    for (const char *k : { "FONTS.FON", "OEMFONT.FON", "FIXEDFON.FON" })   /* load_system_bitmap_fonts */
        for (auto &kv : sys) if (kv.first == k) add_font_file(g_wine_dir + "/" + kv.second, true);
    for (const std::string &n : list_dir(g_wine_dir)) add_font_file(g_wine_dir + "/" + n, false);   /* the data directory */
    scan_host(g_host_dir);                                                                          /* fontconfig's fonts */
    for (auto &kv : reg_key("HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts")) {   /* load_registry_fonts */
        std::string v = kv.first;
        if (v.size() > 11 && v.compare(v.size() - 11, 11, " (TrueType)") == 0) v.resize(v.size() - 11);
        if (is_full_name(widen(v.c_str()))) continue;
        const std::string &path = kv.second;
        if (path.size() > 4 && !strcasecmp(path.c_str() + path.size() - 4, ".fon") && !(path.size() > 1 && path[1] == ':')) add_font_file(g_wine_dir + "/" + path, true);
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
    if (!g_lib) return nullptr;
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
    if (!read_file(face->file, *f->data) || FT_New_Memory_Face(g_lib, f->data->data(), (FT_Long)f->data->size(), face->index, &f->ft)) { delete f->data; delete f; return nullptr; }
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
    static const S t[] = { { 10, 12, 0, 400, OEM_CS, 0x31, "" }, { 11, 12, 0, 400, 0, 0x31, "Courier" }, { 12, 12, 0, 400, 0, 0x22, "MS Sans Serif" },
        { 13, 16, 7, 700, 0, 0x22, "System" }, { 14, 16, 0, 700, 0, 0x22, "System" }, { 16, 16, 0, 400, 0, 0x31, "Courier" }, { 17, -11, 0, 400, 0, 0x22, "MS Shell Dlg" } };
    for (const S &s : t) if (s.idx == idx) {
        memset(lf, 0, sizeof *lf);
        lf->h = s.h; lf->w = s.w; lf->weight = s.wt; lf->cs = (uint8_t)s.cs; lf->pf = (uint8_t)s.pf;
        for (int i = 0; s.face[i]; i++) lf->face[i] = (char16_t)s.face[i];
        return true;
    }
    return false;
}
RF *font_of(void *hf)
{
    gdi_objinfo oi;
    if (!gdi_obj_info(hf, &oi) || oi.kind != GDIK_FONT) return nullptr;
    LF lf;
    if (oi.payload) memcpy(&lf, oi.payload, sizeof lf);
    else if (!stock_lf(oi.stock, &lf)) return nullptr;
    return realize(lf);
}
bool lf_of(void *hf, LF *lf)
{
    gdi_objinfo oi;
    if (!gdi_obj_info(hf, &oi) || oi.kind != GDIK_FONT) return false;
    if (oi.payload) { memcpy(lf, oi.payload, sizeof *lf); return true; }
    return stock_lf(oi.stock, lf);
}
RF *cur(void *hdc)
{
    uint32_t h = gdi_dc_font(hdc);
    return h ? font_of((void *)(uintptr_t)h) : nullptr;
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
uint32_t glyph_outline(RF *f, unsigned ch, unsigned format, uint8_t *gm20, uint32_t buflen, uint8_t *buf, const uint8_t *m2)
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
extern "C" void fnvnx_gdi_set_font_dirs(const char *wine, const char *host, const char *reg)
{
    FL l;
    for (RF *r : g_rf) { if (r->ft) FT_Done_Face(r->ft); delete r->data; delete r; }
    g_rf.clear();
    for (Family *f : g_fam) { for (Face *x : f->faces) delete x; delete f; }
    g_fam.clear(); g_reg.clear(); g_subst.clear();
    g_def_roman = g_def_modern = g_def_swiss = ustr();
    g_wine_dir = wine ? wine : ""; g_host_dir = host ? host : ""; g_reg_dir = reg ? reg : "";
    g_loaded = false;
}

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
        if (!buf) return 12;
        if (cb < 12) return 0;
        put32(tmp, oi.hollow ? 5 : 0); put32(tmp + 8, ((oi.color & 0xff) << 16) | (oi.color & 0xff00) | ((oi.color >> 16) & 0xff));
        memcpy(buf, tmp, 12);
        return 12;
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
