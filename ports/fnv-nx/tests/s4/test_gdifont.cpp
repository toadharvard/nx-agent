/* test_gdifont.cpp - platform/win32/gdifont.cpp against the reference dump (Wine 9.0 on Ubuntu 24.04, fontdump.c):
 * every `logfont`, `metrics`, `langinfo`, `charsetinfo` and `glyph` line of $FNVNX_FONT_REF/fontdump.txt is parsed at
 * run time and compared field by field (one check per field, per glyph buffer). The reference is required: without
 * FNVNX_FONT_REF the test fails. FNVNX_FONT_HOST is the host font directory (Liberation etc., the fontconfig side).
 * Structures use the guest's fixed 32-bit layouts (byte offsets), so the test holds on LP64 hosts.
 *
 * Two fields of the reference are not comparable and are handled by rule, not skipped silently:
 *  - GGO_METRICS returns size 1 and writes no buffer: the reference `data=` is the uninitialised malloc(1) byte.
 *  - GGO_BITMAP of a glyph taken from an embedded strike (bitmap fonts; Tahoma at its strike sizes - recognised by
 *    its box differing from the same glyph's GGO_GRAY8 box, which always comes from the outline): the reference copies
 *    (width+7)/8 bytes per 4-byte row and leaves the other row bytes uninitialised (heap garbage): only the defined
 *    bytes are compared.
 *  - `charsetinfo`: the dump's printf evaluates its arguments right to left, so cs/acp/fs are the memset zeros printed
 *    before TranslateCharsetInfo ran; ok= is compared with the reference, cs/acp/fs with Wine's charset table. */
#include "s4_test.h"

#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_SFNT_NAMES_H
#include FT_TRUETYPE_TABLES_H
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <functional>
#include <map>
#include <string>
#include <vector>

extern "C" {
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
HGDIOBJ GetStockObject(int);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
int GetObjectA(HGDIOBJ, int, void *);
BOOL GetTextMetricsA(HDC, void *);
int GetTextFaceA(HDC, int, char *);
int GetTextCharset(HDC);
DWORD GetFontLanguageInfo(HDC);
BOOL TranslateCharsetInfo(DWORD *, void *, DWORD);
DWORD GetGlyphOutlineA(HDC, UINT, UINT, void *, DWORD, void *, const void *);
}

namespace {
int g_checks, g_fails;
#define GCHK(cond, ...) do { g_checks++; if (!(cond)) { g_fails++; if (g_fails <= 40) fprintf(stderr, "  gdifont FAIL " __VA_ARGS__); } } while (0)

struct Line { std::string s; };
std::string field(const std::string &l, const char *key)   /* " key=value" up to a space; face="..." up to the last quote */
{
    std::string k = std::string(" ") + key + "=";
    size_t p = l.find(k);
    if (p == std::string::npos) return "";
    p += k.size();
    if (l[p] == '"') return l.substr(p + 1, l.rfind('"') - p - 1);
    size_t e = l.find(' ', p);
    return l.substr(p, e == std::string::npos ? std::string::npos : e - p);
}
long num(const std::string &l, const char *key) { return strtol(field(l, key).c_str(), NULL, 0); }
std::string word(const std::string &l, int n)
{
    size_t p = 0;
    for (int i = 0; i < n; i++) { p = l.find(' ', p); if (p == std::string::npos) return ""; p++; }
    return l.substr(p, l.find(' ', p) - p);
}
int stock_of(const std::string &n)
{
    static const struct { const char *n; int id; } t[] = { { "OEM_FIXED_FONT", 10 }, { "ANSI_FIXED_FONT", 11 }, { "ANSI_VAR_FONT", 12 },
        { "SYSTEM_FONT", 13 }, { "DEVICE_DEFAULT_FONT", 14 }, { "SYSTEM_FIXED_FONT", 16 }, { "DEFAULT_GUI_FONT", 17 } };
    for (auto &e : t) if (n == e.n) return e.id;
    return -1;
}
int32_t rd32(const uint8_t *p, int off) { int32_t v; memcpy(&v, p + off, 4); return v; }
int hexval(char c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }

/* a font file of a format the Debian FreeType links an extra library for (zlib/bzip2 compression, WOFF/WOFF2, PNG
 * strikes), found by magic and sfnt table tags */
bool exotic(const std::vector<uint8_t> &d)
{
    if (d.size() < 12) return false;
    if (d[0] == 0x1f && d[1] == 0x8b) return true;                              /* gzip: PCF.gz */
    if (!memcmp(d.data(), "BZh", 3)) return true;                               /* bzip2 */
    if (!memcmp(d.data(), "wOFF", 4) || !memcmp(d.data(), "wOF2", 4)) return true;
    if (!memcmp(d.data(), "ttcf", 4)) return false;                             /* collection: tables checked per face below */
    if (!(d[0] == 0 && d[1] == 1) && memcmp(d.data(), "true", 4) && memcmp(d.data(), "OTTO", 4)) return false;   /* not sfnt */
    int n = (d[4] << 8) | d[5];
    for (int i = 0; i < n && 12 + 16 * (i + 1) <= (int)d.size(); i++) {
        const char *t = (const char *)&d[12 + 16 * i];
        if (!memcmp(t, "sbix", 4) || !memcmp(t, "CBDT", 4) || !memcmp(t, "CBLC", 4) || !memcmp(t, "SVG ", 4)) return true;
    }
    return false;
}
void scan_formats(const std::string &dir)
{
    std::string cmd = "find '" + dir + "' -type f 2>/dev/null";
    FILE *f = popen(cmd.c_str(), "r");
    char path[1024];
    while (f && fgets(path, sizeof path, f)) {
        std::string p(path);
        p.resize(p.size() - 1);
        FILE *g = fopen(p.c_str(), "rb");
        if (!g) continue;
        std::vector<uint8_t> d(4096);
        d.resize(fread(d.data(), 1, d.size(), g));
        fclose(g);
        GCHK(!exotic(d), "font format needing libpng/zlib/bzip2/brotli: %s\n", p.c_str());
    }
    if (f) pclose(f);
}
} // namespace

TEST(text_gdifont_reference)   /* named to run after heap_destroy_frees_all_and_isolates: that test compares mallinfo2 and is sensitive to tcache state */
{
    const char *ref = getenv("FNVNX_FONT_REF"), *host = getenv("FNVNX_FONT_HOST");
    if (!ref || !host) { printf("gdifont: reference not available\n"); CHECK(false); return; }
    std::string r = ref, wine = r + "/wine-fonts/usr/share/wine/fonts";

    FT_Library lib;
    FT_Int ma = 0, mi = 0, pa = 0;
    CHECK_EQ(FT_Init_FreeType(&lib), 0);
    FT_Library_Version(lib, &ma, &mi, &pa);
    printf("gdifont: FreeType %d.%d.%d\n", ma, mi, pa);
    CHECK(ma == 2 && mi == 13 && pa == 2);
    FT_Done_FreeType(lib);
    const char *declared = getenv("FNVNX_FONTS");   /* the declared set (tools/fonts-wine9): every file the platform can load must be a format FreeType here reads without libpng/zlib/bzip2/brotli */
    CHECK(declared != nullptr);
    if (declared) scan_formats(declared);

    FILE *f = fopen((r + "/fontdump.txt").c_str(), "r");
    if (!f) { printf("gdifont: reference not available\n"); CHECK(false); return; }
    std::vector<std::string> lines;
    char *buf = (char *)malloc(1 << 20);
    while (fgets(buf, 1 << 20, f)) { std::string s(buf); while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back(); lines.push_back(s); }
    free(buf);
    fclose(f);

    HDC dc = CreateCompatibleDC(NULL);
    std::map<std::string, std::string> gray_box;   /* "font U+xxxx" -> bbx,bby of the GGO_GRAY8 line */
    for (const std::string &l : lines) if (word(l, 0) == "glyph" && word(l, 3) == "gray8") gray_box[word(l, 1) + word(l, 2)] = field(l, "bbx") + "," + field(l, "bby") + field(l, "orgx");
    bool bitmap_font = false;
    int nref = 0;
    for (const std::string &l : lines) {
        std::string kind = word(l, 0), name = word(l, 1);
        int sid = stock_of(name);
        if (sid < 0 || (kind != "font" && kind != "logfont" && kind != "metrics" && kind != "langinfo" && kind != "charsetinfo" && kind != "glyph")) continue;
        HGDIOBJ hf = GetStockObject(sid);
        SelectObject(dc, hf);
        if (kind == "font") {
            uint8_t lf[100];
            GCHK((int)GetObjectA(hf, sizeof lf, lf) == num(l, "getobject"), "%s GetObjectA size\n", name.c_str());
            nref++;
        } else if (kind == "logfont") {
            uint8_t lf[64];
            memset(lf, 0xAA, sizeof lf);
            GetObjectA(hf, sizeof lf, lf);
            struct { const char *k; int off, sz; } fl[] = { { "height", 0, 4 }, { "width", 4, 4 }, { "esc", 8, 4 }, { "orient", 12, 4 },
                { "weight", 16, 4 }, { "italic", 20, 1 }, { "underline", 21, 1 }, { "strike", 22, 1 }, { "charset", 23, 1 },
                { "outprec", 24, 1 }, { "clipprec", 25, 1 }, { "quality", 26, 1 }, { "pitch", 27, 1 } };
            for (auto &x : fl) {
                long v = x.sz == 4 ? rd32(lf, x.off) : lf[x.off];
                GCHK(v == num(l, x.k), "%s logfont %s: %ld vs %ld\n", name.c_str(), x.k, v, num(l, x.k));
            }
            GCHK(field(l, "face") == (const char *)(lf + 28), "%s logfont face '%s'\n", name.c_str(), (const char *)(lf + 28));
            nref++;
        } else if (kind == "metrics") {
            uint8_t tm[64];
            char face[64] = "";
            memset(tm, 0xAA, sizeof tm);
            GCHK(GetTextMetricsA(dc, tm), "%s GetTextMetricsA\n", name.c_str());
            GetTextFaceA(dc, sizeof face, face);
            GCHK(field(l, "face") == face, "%s face '%s' vs '%s'\n", name.c_str(), face, field(l, "face").c_str());
            struct { const char *k; int off, sz; } fl[] = { { "height", 0, 4 }, { "ascent", 4, 4 }, { "descent", 8, 4 }, { "intlead", 12, 4 },
                { "extlead", 16, 4 }, { "avew", 20, 4 }, { "maxw", 24, 4 }, { "weight", 28, 4 }, { "overhang", 32, 4 },
                { "aspx", 36, 4 }, { "aspy", 40, 4 }, { "first", 44, 1 }, { "last", 45, 1 }, { "default", 46, 1 }, { "break", 47, 1 },
                { "italic", 48, 1 }, { "underlined", 49, 1 }, { "struck", 50, 1 }, { "pitch", 51, 1 }, { "charset", 52, 1 } };
            for (auto &x : fl) {
                long v = x.sz == 4 ? rd32(tm, x.off) : tm[x.off];
                GCHK(v == num(l, x.k), "%s metrics %s: %ld vs %ld\n", name.c_str(), x.k, v, num(l, x.k));
            }
            bitmap_font = !(tm[51] & 2);   /* TMPF_VECTOR */
            nref++;
        } else if (kind == "langinfo") {
            GCHK((GetFontLanguageInfo(dc) & 0xffffffffu) == (DWORD)strtoul(word(l, 2).c_str(), NULL, 16), "%s GetFontLanguageInfo %lx\n", name.c_str(), (unsigned long)GetFontLanguageInfo(dc));
            GCHK(GetTextCharset(dc) == num(l, "textcharset"), "%s GetTextCharset\n", name.c_str());
            nref++;
        } else if (kind == "charsetinfo") {
            uint8_t tm[64], cs[32];
            GetTextMetricsA(dc, tm);
            DWORD src[1] = { tm[52] };
            memset(cs, 0, sizeof cs);
            GCHK(TranslateCharsetInfo((DWORD *)(uintptr_t)tm[52], cs, 1 /* TCI_SRCCHARSET */) == num(l, "ok"), "%s TranslateCharsetInfo ok\n", name.c_str());
            /* Wine's table row for the font's charset (ANSI here): charset, ACP, FS_LATIN1 (see the file header) */
            GCHK(rd32(cs, 0) == tm[52] && rd32(cs, 4) == 1252 && (uint32_t)rd32(cs, 24) == 1, "%s TCI_SRCCHARSET row\n", name.c_str());
            uint8_t c2[32] = { 0 };
            GCHK(TranslateCharsetInfo((DWORD *)(uintptr_t)1252, c2, 2 /* TCI_SRCCODEPAGE */) && rd32(c2, 0) == 0, "%s TCI_SRCCODEPAGE\n", name.c_str());
            memset(c2, 0, sizeof c2);
            src[0] = 1;
            GCHK(TranslateCharsetInfo(src, c2, 3 /* TCI_SRCFONTSIG */) && rd32(c2, 4) == 1252, "%s TCI_SRCFONTSIG\n", name.c_str());
            nref++;
        } else {   /* glyph NAME U+XXXX fmt size=.. */
            static const struct { const char *n; UINT f; } fm[] = { { "metrics", 0 }, { "bitmap", 1 }, { "native", 2 }, { "gray2", 4 }, { "gray4", 5 }, { "gray8", 6 } };
            UINT ch = (UINT)strtoul(word(l, 2).c_str() + 2, NULL, 16), gf = 0;
            for (auto &e : fm) if (word(l, 3) == e.n) gf = e.f;
            static const uint8_t id[16] = { 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0 };   /* MAT2 identity: {0,1},{0,0},{0,0},{0,1} */
            uint8_t gm[20];
            memset(gm, 0, sizeof gm);
            long want = num(l, "size");
            int32_t got = (int32_t)GetGlyphOutlineA(dc, ch, gf, gm, 0, NULL, id);
            GCHK(got == (int32_t)want, "%s %s U+%04X size %d vs %ld\n", name.c_str(), word(l, 3).c_str(), ch, got, want);
            nref++;
            if (want == -1) continue;
            const char *ks[] = { "bbx", "bby", "orgx", "orgy", "incx", "incy" };
            int offs[] = { 0, 4, 8, 12, 16, 18 };
            for (int i = 0; i < 6; i++) {
                long v = i < 5 && i != 4 ? rd32(gm, offs[i]) : (int16_t)(gm[offs[i]] | gm[offs[i] + 1] << 8);
                GCHK(v == num(l, ks[i]), "%s %s U+%04X %s %ld vs %ld\n", name.c_str(), word(l, 3).c_str(), ch, ks[i], v, num(l, ks[i]));
            }
            if (gf == 0) { GCHK(true, ""); continue; }   /* size 1, no buffer (see header) */
            bool strike = bitmap_font || gray_box[name + word(l, 2)] != field(l, "bbx") + "," + field(l, "bby") + field(l, "orgx");
            std::string hx = field(l, "data");
            std::vector<uint8_t> exp(hx.size() / 2), out(want > 0 && got > 0 ? got : 1, 0);
            for (size_t i = 0; i < exp.size(); i++) exp[i] = (uint8_t)(hexval(hx[2 * i]) << 4 | hexval(hx[2 * i + 1]));
            if (got > 0) GetGlyphOutlineA(dc, ch, gf, gm, got, out.data(), id);
            bool ok = (int)exp.size() == (got > 0 ? got : 0);
            for (size_t i = 0; ok && i < exp.size(); i++) {
                if (gf == 1 && strike && (int)(i % 4) >= (int)((rd32(gm, 0) + 7) / 8)) continue;   /* undefined bytes, see header */
                ok = exp[i] == out[i];
            }
            GCHK(ok, "%s %s U+%04X buffer differs (size %d)\n", name.c_str(), word(l, 3).c_str(), ch, got);
        }
    }
    DeleteDC(dc);
    printf("gdifont: reference checks %d (%d reference lines), failing %d\n", g_checks, nref, g_fails);
    s4_checks += g_checks;
    s4_failures += g_fails;
}

extern "C" {
COLORREF SetTextColor(HDC, COLORREF);
COLORREF SetBkColor(HDC, COLORREF);
int SetBkMode(HDC, int);
UINT SetTextAlign(HDC, UINT);
int SetMapMode(HDC, int);
BOOL MoveToEx(HDC, int, int, void *);
HGDIOBJ GetCurrentObject(HDC, UINT);
int GetObjectW(HGDIOBJ, int, void *);
HFONT CreateFontIndirectA(const void *);
BOOL DeleteObject(HGDIOBJ);
}
TEST(text_dc_state_and_getobject)
{
    const char *ref = getenv("FNVNX_FONT_REF"), *host = getenv("FNVNX_FONT_HOST");
    if (!ref || !host) { printf("gdifont: reference not available\n"); CHECK(false); return; }
    HDC dc = CreateCompatibleDC(NULL), bad = (HDC)(uintptr_t)0x12345;
    CHECK_EQ(SetTextColor(dc, 0x112233) & 0xffffffffu, 0);            /* defaults: black text, white background, OPAQUE */
    CHECK_EQ(SetTextColor(dc, 0x445566) & 0xffffffffu, 0x112233);
    CHECK_EQ(SetBkColor(dc, 0x010203) & 0xffffffffu, 0xFFFFFF);
    CHECK_EQ(SetBkColor(dc, 0) & 0xffffffffu, 0x010203);
    CHECK_EQ(SetBkMode(dc, 1), 2);
    CHECK_EQ(SetBkMode(dc, 2), 1);
    CHECK_EQ(SetBkMode(dc, 7), 0);
    CHECK_EQ(SetBkMode(dc, 1), 2);                                    /* the invalid mode was not stored */
    CHECK_EQ(SetTextAlign(dc, 6 | 24), 0);
    CHECK_EQ(SetTextAlign(dc, 0), 6 | 24);
    CHECK_EQ(SetMapMode(dc, 8), 1);
    CHECK_EQ(SetMapMode(dc, 1), 8);
    CHECK_EQ(SetMapMode(dc, 0), 0);
    CHECK_EQ(SetMapMode(dc, 9), 0);
    CHECK_EQ(SetMapMode(dc, 1), 1);
    int32_t pt[2] = { -1, -1 };
    CHECK(MoveToEx(dc, 5, -7, pt));
    CHECK(pt[0] == 0 && pt[1] == 0);
    CHECK(MoveToEx(dc, 1, 2, pt));
    CHECK(pt[0] == 5 && pt[1] == -7);
    CHECK(MoveToEx(dc, 0, 0, NULL));
    CHECK((SetTextColor(bad, 0) & 0xffffffffu) == 0xffffffffu);
    CHECK((SetBkColor(bad, 0) & 0xffffffffu) == 0xffffffffu);
    CHECK_EQ(SetBkMode(bad, 1), 0);
    CHECK((SetTextAlign(bad, 0) & 0xffffffffu) == 0xffffffffu);
    CHECK_EQ(SetMapMode(bad, 1), 0);
    CHECK(!MoveToEx(bad, 0, 0, pt));
    CHECK(GetCurrentObject(bad, 6) == NULL);
    CHECK(GetCurrentObject(dc, 6) == GetStockObject(13));              /* SYSTEM_FONT, BLACK_PEN, WHITE_BRUSH, DEFAULT_PALETTE */
    CHECK(GetCurrentObject(dc, 1) == GetStockObject(7));
    CHECK(GetCurrentObject(dc, 2) == GetStockObject(0));
    CHECK(GetCurrentObject(dc, 5) == GetStockObject(15));
    CHECK(GetCurrentObject(dc, 7) != NULL);
    CHECK(GetCurrentObject(dc, 3) == NULL);
    /* GetObject: fonts (created, A and W), brushes, pens, bitmaps, the size protocol */
    uint8_t lfa[60] = { 0 }, out[128];
    int32_t hgt = -20;
    memcpy(lfa, &hgt, 4);
    lfa[23] = 1;
    strcpy((char *)lfa + 28, "Tahoma");
    HFONT hf = CreateFontIndirectA(lfa);
    CHECK(hf != NULL);
    CHECK_EQ(GetObjectA(hf, 0, NULL), 60);
    CHECK_EQ(GetObjectW(hf, 0, NULL), 92);
    CHECK_EQ(GetObjectA(hf, 60, out), 60);
    CHECK(!memcmp(out, lfa, 60));
    CHECK_EQ(GetObjectA(hf, 10, out), 10);
    CHECK_EQ(GetObjectW(hf, 200, out), 92);
    CHECK(out[28] == 'T' && out[29] == 0 && out[30] == 'a');
    CHECK(SelectObject(dc, hf) == GetStockObject(13));
    CHECK(GetCurrentObject(dc, 6) == hf);
    char face[32];
    CHECK_EQ(GetTextFaceA(dc, sizeof face, face), 6);
    CHECK_STR(face, "Tahoma");
    uint8_t tm[64];
    CHECK(GetTextMetricsA(dc, tm));
    CHECK_EQ(rd32(tm, 0) - rd32(tm, 12), 20);                         /* height - internal leading = |lfHeight| */
    SelectObject(dc, GetStockObject(13));
    CHECK(DeleteObject(hf));
    CHECK_EQ(GetObjectA(hf, 60, out), 0);
    CHECK_EQ(GetObjectA(GetStockObject(4), 0, NULL), 12);              /* BLACK_BRUSH: BS_SOLID, 0 */
    memset(out, 0xAA, sizeof out);
    CHECK_EQ(GetObjectA(GetStockObject(0), 12, out), 12);              /* WHITE_BRUSH */
    CHECK(rd32(out, 0) == 0 && rd32(out, 4) == 0xFFFFFF && rd32(out, 8) == 0);
    CHECK_EQ(GetObjectA(GetStockObject(5), 4, out), 4);                /* NULL_BRUSH, partial copy */
    CHECK_EQ(rd32(out, 0), 1);
    CHECK_EQ(GetObjectA(GetStockObject(6), 16, out), 16);              /* WHITE_PEN: LOGPEN is 16 bytes */
    CHECK(rd32(out, 0) == 0 && rd32(out, 4) == 0 && rd32(out, 12) == 0xFFFFFF);
    CHECK_EQ(GetObjectA(GetStockObject(8), 16, out), 16);              /* NULL_PEN */
    CHECK_EQ(rd32(out, 0), 5);
    CHECK_EQ(GetObjectA(GetStockObject(6), 8, out), 0);
    HGDIOBJ bm = GetCurrentObject(dc, 7);
    int bsz = GetObjectA(bm, 0, NULL);
    CHECK_EQ(bsz, (int)(16 + 4 + sizeof(void *) + (sizeof(void *) == 8 ? 4 : 0)));
    CHECK_EQ(GetObjectA(bm, bsz, out), bsz);
    CHECK(rd32(out, 4) == 1 && rd32(out, 8) == 1 && out[16] == 1 && out[18] == 1);   /* the DC's 1x1 mono bitmap */
    CHECK_EQ(GetObjectA(bm, bsz - 1, out), 0);
    CHECK_EQ(GetObjectA((HGDIOBJ)(uintptr_t)0x12345, 60, out), 0);
    DeleteDC(dc);
}

/* ---- stage 2: the `text`, `extentex`, `placement` and `caps` lines of the reference dump, through the public API
 * exactly as fontdump.c called it. GetTextExtentExPointA writes dx[] only up to the characters that fit (win32u's
 * NtGdiGetTextExtentExW); the reference's remaining dx entries are uninitialised stack bytes, so for them the check is
 * that the call left the caller's sentinel untouched. */
extern "C" {
HDC GetDC(HWND);
int ReleaseDC(HWND, HDC);
int GetDeviceCaps(HDC, int);
UINT GetACP(void);
UINT GetOEMCP(void);
HBITMAP CreateDIBSection(HDC, const void *, UINT, void **, HANDLE, DWORD);
COLORREF GetPixel(HDC, int, int);
BOOL ExtTextOutA(HDC, int, int, UINT, const RECT *, const char *, UINT, const int *);
BOOL ExtTextOutW(HDC, int, int, UINT, const RECT *, const WCHAR *, UINT, const int *);
BOOL GetTextExtentPoint32A(HDC, const char *, int, SIZE *);
BOOL GetTextExtentPoint32W(HDC, const WCHAR *, int, SIZE *);
BOOL GetTextExtentExPointA(HDC, const char *, int, int, int *, int *, SIZE *);
BOOL GetTextExtentExPointW(HDC, const WCHAR *, int, int, int *, int *, SIZE *);
DWORD GetCharacterPlacementA(HDC, const char *, int, int, void *, DWORD);
DWORD GetCharacterPlacementW(HDC, const WCHAR *, int, int, void *, DWORD);
UINT GetOutlineTextMetricsA(HDC, UINT, void *);
HDC CreateDCA(const char *, const char *, const char *, const void *);
int ExtEscape(HDC, int, int, const char *, int, char *);
}
namespace {
struct GCP { uint32_t lStructSize; char *lpOutString; uint32_t *lpOrder; int *lpDx; int *lpCaretPos; char *lpClass; uint16_t *lpGlyphs; uint32_t nGlyphs; int nMaxFit; };
struct Dib { HDC dc = NULL; HBITMAP bmp = NULL, old = NULL; uint8_t *bits = NULL; int w = 0, h = 0; };
void dib_open(Dib &d, int w, int h, HGDIOBJ font)
{
    uint8_t bi[44];
    memset(bi, 0, sizeof bi);
    int32_t v = 40;
    memcpy(bi, &v, 4); v = w; memcpy(bi + 4, &v, 4); v = -h; memcpy(bi + 8, &v, 4);
    bi[12] = 1; bi[14] = 32;
    d.dc = CreateCompatibleDC(NULL);
    void *bits = NULL;
    d.bmp = (HBITMAP)CreateDIBSection(d.dc, bi, 0, &bits, NULL, 0);
    d.old = (HBITMAP)SelectObject(d.dc, d.bmp);
    if (font) SelectObject(d.dc, font);
    d.bits = (uint8_t *)bits; d.w = w; d.h = h;
    memset(bits, 0xff, (size_t)w * h * 4);
}
void dib_close(Dib &d) { SelectObject(d.dc, d.old); DeleteObject(d.bmp); DeleteDC(d.dc); }
std::string dib_rows(const Dib &d)   /* fontdump.c's row dump: the mean of the three channels per pixel */
{
    std::string r;
    char b[4];
    for (int y = 0; y < d.h; y++) {
        for (int x = 0; x < d.w; x++) { const uint8_t *p = d.bits + ((size_t)y * d.w + x) * 4; snprintf(b, sizeof b, "%02x", (p[0] + p[1] + p[2]) / 3); r += b; }
        if (y + 1 < d.h) r += '/';
    }
    return r;
}
}

TEST(text_gdifont_text)
{
    const char *ref = getenv("FNVNX_FONT_REF"), *host = getenv("FNVNX_FONT_HOST");
    if (!ref || !host) { printf("gdifont: reference not available\n"); CHECK(false); return; }
    std::string r = ref;
    FILE *f = fopen((r + "/fontdump.txt").c_str(), "r");
    if (!f) { CHECK(false); return; }
    std::vector<std::string> lines;
    char *buf = (char *)malloc(1 << 20);
    while (fgets(buf, 1 << 20, f)) { std::string s(buf); while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back(); lines.push_back(s); }
    free(buf);
    fclose(f);
    int c0 = g_checks, f0 = g_fails, nline[5] = { 0, 0, 0, 0, 0 };   /* text, extentex, placement, caps, not understood */
    for (const std::string &l : lines) {
        std::string kind = word(l, 0), name = word(l, 1);
        if (kind == "caps") {
            HDC scr = GetDC(NULL);
            GCHK(GetDeviceCaps(scr, 88) == num(l, "logpixelsx") && GetDeviceCaps(scr, 90) == num(l, "logpixelsy") && GetDeviceCaps(scr, 12) == num(l, "bitspixel") &&
                 GetDeviceCaps(scr, 8) == num(l, "horzres") && GetDeviceCaps(scr, 10) == num(l, "vertres") && (long)GetACP() == num(l, "acp") && (long)GetOEMCP() == num(l, "oemcp"), "caps line\n");
            ReleaseDC(NULL, scr);
            nline[3]++;
            continue;
        }
        if (kind != "text" && kind != "extentex" && kind != "placement") {
            static const char *known[] = { "font", "logfont", "metrics", "langinfo", "charsetinfo", "glyph" };
            bool ok = false;
            for (const char *k : known) if (kind == k) ok = true;
            GCHK(ok || kind.empty(), "line kind not understood: %s\n", kind.c_str());
            if (!ok && !kind.empty()) nline[4]++;
            continue;
        }
        int sid = stock_of(name);
        GCHK(sid >= 0, "font name %s\n", name.c_str());
        if (sid < 0) continue;
        size_t q1 = l.find('"'), q2 = l.find('"', q1 + 1);
        std::string str = kind == "placement" ? "AgW1" : l.substr(q1 + 1, q2 - q1 - 1);
        Dib d;
        dib_open(d, 160, 32, GetStockObject(sid));
        if (kind == "text") {
            SetTextColor(d.dc, 0);
            SetBkMode(d.dc, 1);
            ExtTextOutA(d.dc, 2, 2, 0, NULL, str.c_str(), (UINT)str.size(), NULL);
            SIZE sz = { 0, 0 };
            GCHK(GetTextExtentPoint32A(d.dc, str.c_str(), (int)str.size(), &sz), "%s extent call\n", name.c_str());
            char ext[64];
            snprintf(ext, sizeof ext, "%ld,%ld", (long)sz.cx, (long)sz.cy);
            GCHK(field(l, "extent") == ext, "%s text extent %s vs %s\n", name.c_str(), ext, field(l, "extent").c_str());
            size_t p = l.find("rows=");
            std::string want = l.substr(p + 5), got = dib_rows(d);
            bool eq = want == got;
            if (!eq) {
                size_t n = 0;
                while (n < want.size() && n < got.size() && want[n] == got[n]) n++;
                fprintf(stderr, "  gdifont: %s first pixel difference at byte %zu (row %zu col %zu)\n", name.c_str(), n, n / (160 * 2 + 1), (n % (160 * 2 + 1)) / 2);
            }
            GCHK(eq, "%s text pixels differ\n", name.c_str());
            nline[0]++;
        } else if (kind == "extentex") {
            int fit = -1, dxs[16];
            for (int &v : dxs) v = 0x5a5a5a5a;
            SIZE sz = { 0, 0 };
            GCHK(GetTextExtentExPointA(d.dc, str.c_str(), (int)str.size(), 30, &fit, dxs, &sz), "%s extentex call\n", name.c_str());
            GCHK(fit == num(l, "fit"), "%s fit %d vs %ld\n", name.c_str(), fit, num(l, "fit"));
            char sizes[64];
            snprintf(sizes, sizeof sizes, "%ld,%ld", (long)sz.cx, (long)sz.cy);
            GCHK(field(l, "size") == sizes, "%s extentex size %s vs %s\n", name.c_str(), sizes, field(l, "size").c_str());
            std::string dxl = l.substr(l.find("dx=") + 3);
            size_t pos = 0;
            for (int k = 0; k < (int)str.size(); k++) {
                size_t e = dxl.find(',', pos);
                long want = strtol(dxl.substr(pos, e == std::string::npos ? std::string::npos : e - pos).c_str(), NULL, 10);
                pos = e == std::string::npos ? dxl.size() : e + 1;
                if (k < fit) GCHK(dxs[k] == want, "%s extentex dx[%d] %d vs %ld\n", name.c_str(), k, dxs[k], want);
                else GCHK(dxs[k] == 0x5a5a5a5a, "%s extentex dx[%d] written beyond nfit\n", name.c_str(), k);
            }
            nline[1]++;
        } else {
            char out[16] = "";
            int dxp[16] = { 0 };
            GCP gcp;
            memset(&gcp, 0, sizeof gcp);
            gcp.lStructSize = sizeof gcp; gcp.lpOutString = out; gcp.lpDx = dxp; gcp.nGlyphs = 16;
            DWORD rv = GetCharacterPlacementA(d.dc, "AgW1", 4, 0, &gcp, 0);
            char want[16];
            snprintf(want, sizeof want, "0x%08lx", (unsigned long)(rv & 0xffffffffu));
            GCHK(word(l, 2) == std::string("ret=") + want, "%s placement ret %s vs %s\n", name.c_str(), want, word(l, 2).c_str());
            GCHK((long)gcp.nGlyphs == num(l, "nglyphs"), "%s placement nglyphs %u\n", name.c_str(), gcp.nGlyphs);
            std::string dxl = l.substr(l.find("dx=") + 3);
            char got[64];
            snprintf(got, sizeof got, "%d,%d,%d,%d", dxp[0], dxp[1], dxp[2], dxp[3]);
            GCHK(dxl == got, "%s placement dx %s vs %s\n", name.c_str(), got, dxl.c_str());
            GCHK(!strncmp(out, "AgW1", 4), "%s placement out string\n", name.c_str());
            nline[2]++;
        }
        dib_close(d);
    }
    printf("gdifont text: compared text %d, extentex %d, placement %d, caps %d lines, not understood %d, failing %d\n", nline[0], nline[1], nline[2], nline[3], nline[4], g_fails - f0);
    s4_checks += g_checks - c0;
    s4_failures += g_fails - f0;
    g_checks = c0; g_fails = f0;
}

/* ---- stage 2 contract checks: ExtTextOut flags, extents, placement, the display DC and ExtEscape */
namespace {
std::string draw(int w, int h, HGDIOBJ font, std::function<void(HDC)> fn)
{
    Dib d;
    dib_open(d, w, h, font);
    SetTextColor(d.dc, 0);
    SetBkMode(d.dc, 1);
    fn(d.dc);
    std::string r = dib_rows(d);
    dib_close(d);
    return r;
}
int dark_px(const std::string &rows)   /* pixels that are not white */
{
    int n = 0;
    for (size_t i = 0, k = 0; i < rows.size(); k++) { if (rows[i] == '/') { i++; continue; } if (!(rows[i] == 'f' && rows[i + 1] == 'f')) n++; i += 2; (void)k; }
    return n;
}
}
TEST(text_gdifont_api)
{
    const char *ref = getenv("FNVNX_FONT_REF"), *host = getenv("FNVNX_FONT_HOST");
    if (!ref || !host) { printf("gdifont: reference not available\n"); CHECK(false); return; }
    int c0 = g_checks, f0 = g_fails;
    HGDIOBJ font = GetStockObject(17);   /* DEFAULT_GUI_FONT */
    /* the display DC and ExtEscape */
    HDC scr = GetDC(NULL), disp = CreateDCA("DISPLAY", NULL, NULL, NULL), disp2 = CreateDCA(NULL, "\\\\.\\DISPLAY1", NULL, NULL);
    GCHK(disp && disp2 && disp != disp2, "CreateDCA display\n");
    GCHK(GetDeviceCaps(disp, 8) == GetDeviceCaps(scr, 8) && GetDeviceCaps(disp, 10) == GetDeviceCaps(scr, 10) && GetDeviceCaps(disp, 12) == GetDeviceCaps(scr, 12), "display DC caps = the desktop mode\n");
    GCHK(!CreateDCA("EPSON", NULL, NULL, NULL) && !CreateDCA(NULL, NULL, NULL, NULL) && !CreateDCA("DISPLAY", "\\\\.\\DISPLAY2", NULL, NULL) && !CreateDCA(NULL, "HP LaserJet", NULL, NULL), "CreateDCA other devices\n");
    int code = 1;
    GCHK(ExtEscape(disp, 8, 4, (const char *)&code, 0, NULL) == 0 && ExtEscape(disp, 8, 4, (const char *)&(code = 0x4001), 0, NULL) == 0, "QUERYESCSUPPORT answers 0\n");
    GCHK(ExtEscape(disp, 0x1234, 0, NULL, 0, NULL) == 0 && ExtEscape(disp, 1, 4, (const char *)&code, 4, (char *)&code) == 0, "unimplemented escapes return 0\n");
    GCHK(ExtEscape((HDC)(uintptr_t)0x777, 1, 0, NULL, 0, NULL) < 0 && ExtEscape(disp, 8, 0, NULL, 0, NULL) < 0 && ExtEscape(disp, 1, 4, NULL, 0, NULL) < 0, "ExtEscape errors are negative\n");
    GCHK(!ReleaseDC(NULL, disp) && DeleteDC(disp) && !DeleteDC(disp) && DeleteDC(disp2) && !DeleteDC(scr), "display DC owned by DeleteDC\n");
    ReleaseDC(NULL, scr);
    /* extents */
    HDC dc = CreateCompatibleDC(NULL);
    SelectObject(dc, font);
    SIZE sz = { 7, 7 };
    int fit = 9, dxs[4] = { 1, 1, 1, 1 };
    GCHK(GetTextExtentPoint32A(dc, "", 0, &sz) && sz.cx == 0 && sz.cy == 0, "empty extent\n");
    GCHK(GetTextExtentExPointA(dc, "", 0, 10, &fit, dxs, &sz) && fit == 0 && dxs[0] == 1, "empty extentex\n");
    GCHK(!GetTextExtentPoint32A(dc, "a", -1, &sz) && !GetTextExtentExPointA(dc, "a", 1, -2, NULL, NULL, &sz) && !GetTextExtentPoint32A(dc, "a", 1, NULL) &&
         !GetTextExtentPoint32A((HDC)(uintptr_t)0x999, "a", 1, &sz) && !GetTextExtentPoint32W(dc, NULL, 1, &sz), "extent errors\n");
    WCHAR wa[4] = { 'A', 'g', 'W', '1' };
    SIZE sa, sw;
    int dxa[4], dxw[4], fa = 0, fw = 0;
    GCHK(GetTextExtentExPointA(dc, "AgW1", 4, -1, &fa, dxa, &sa) && GetTextExtentExPointW(dc, wa, 4, -1, &fw, dxw, &sw) && fa == 4 && fw == 4 && sa.cx == sw.cx && sa.cy == sw.cy &&
         !memcmp(dxa, dxw, sizeof dxa) && dxa[3] == sa.cx, "A and W extents agree; max -1 fits everything\n");
    GCHK(GetTextExtentExPointW(dc, wa, 4, 0, &fw, NULL, &sw) && fw == 0 && sw.cx == sa.cx, "max 0 fits nothing, the size is the whole string\n");
    /* ExtTextOut */
    auto rows_at = [&](UINT align, int x, int y, UINT flags, const RECT *rc, const char *s, const int *dx) {
        return draw(160, 48, font, [&](HDC h) { SetTextAlign(h, align); ExtTextOutA(h, x, y, flags, rc, s, (UINT)strlen(s), dx); });
    };
    std::string base = rows_at(0, 20, 10, 0, NULL, "AgW1", NULL);
    GCHK(dark_px(base) > 20, "text drawn\n");
    GCHK(rows_at(2 /* TA_RIGHT */, 20 + sa.cx, 10, 0, NULL, "AgW1", NULL) == base, "TA_RIGHT\n");
    GCHK(rows_at(6 /* TA_CENTER */, 20 + sa.cx / 2, 10, 0, NULL, "AgW1", NULL) == base, "TA_CENTER\n");
    uint8_t tm[64];
    GetTextMetricsA(dc, tm);
    int asc = rd32(tm, 4), desc = rd32(tm, 8);
    GCHK(rows_at(24 /* TA_BASELINE */, 20, 10 + asc, 0, NULL, "AgW1", NULL) == base, "TA_BASELINE\n");
    GCHK(rows_at(8 /* TA_BOTTOM */, 20, 10 + asc + desc, 0, NULL, "AgW1", NULL) == base, "TA_BOTTOM\n");
    GCHK(draw(160, 48, font, [&](HDC h) { MoveToEx(h, 20, 10, NULL); SetTextAlign(h, 1); ExtTextOutA(h, 99, 99, 0, NULL, "AgW1", 4, NULL); int32_t p[2] = { 0, 0 }; MoveToEx(h, 0, 0, p); GCHK(p[0] == 20 + sa.cx && p[1] == 10, "TA_UPDATECP moves the position\n"); }) == base, "TA_UPDATECP draws at the position\n");
    int dx2[2] = { 10, 10 }, pdy[4] = { 10, 3, 10, 3 };
    std::string pa = draw(160, 48, font, [&](HDC h) { ExtTextOutA(h, 20, 20, 0, NULL, "A", 1, NULL); ExtTextOutA(h, 30, 20, 0, NULL, "A", 1, NULL); });
    GCHK(rows_at(0, 20, 20, 0, NULL, "AA", dx2) == pa, "lpDx\n");
    std::string pb = draw(160, 48, font, [&](HDC h) { ExtTextOutA(h, 20, 20, 0, NULL, "A", 1, NULL); ExtTextOutA(h, 30, 17, 0, NULL, "A", 1, NULL); });
    GCHK(rows_at(0, 20, 20, 0x2000 /* ETO_PDY */, NULL, "AA", pdy) == pb, "ETO_PDY\n");
    RECT rc = { 0, 0, 30, 48 };
    std::string clipped = rows_at(0, 20, 10, 4 /* ETO_CLIPPED */, &rc, "AgW1", NULL), full = rows_at(0, 20, 10, 0, NULL, "AgW1", NULL);
    bool inside = true, cut = false;
    for (int y = 0; y < 48; y++) for (int x = 0; x < 160; x++) {
        size_t o = ((size_t)y * 160 + x) * 2 + y;
        bool c = clipped.substr(o, 2) != "ff", u = full.substr(o, 2) != "ff";
        if (x >= 30) inside = inside && !c; else inside = inside && c == u;
        if (x >= 30 && u) cut = true;
    }
    GCHK(inside && cut, "ETO_CLIPPED cuts at the rectangle and keeps the rest\n");
    {   /* ETO_OPAQUE fills the rectangle, the background mode fills the text box */
        Dib d;
        dib_open(d, 100, 40, font);
        SetBkColor(d.dc, 0x0000FF);
        RECT r2 = { 10, 10, 50, 30 };
        ExtTextOutA(d.dc, 12, 12, 2 /* ETO_OPAQUE */, &r2, "", 0, NULL);
        GCHK(GetPixel(d.dc, 10, 10) == 0xFF && GetPixel(d.dc, 49, 29) == 0xFF && GetPixel(d.dc, 50, 29) == 0xFFFFFF && GetPixel(d.dc, 9, 10) == 0xFFFFFF, "ETO_OPAQUE rectangle\n");
        ExtTextOutA(d.dc, 60, 5, 0, NULL, "AgW1", 4, NULL);   /* OPAQUE background mode: the text box in the background colour */
        GCHK(GetPixel(d.dc, 60, 5) == 0xFF && GetPixel(d.dc, 60 + sa.cx - 1, 5 + asc + desc - 1) == 0xFF && GetPixel(d.dc, 60 + sa.cx, 5) == 0xFFFFFF, "opaque text box\n");
        dib_close(d);
    }
    {   /* ETO_GLYPH_INDEX: the indices GetCharacterPlacement reports draw the same glyphs */
        uint16_t gl[4];
        char outs[8];
        GCP g;
        memset(&g, 0, sizeof g);
        g.lStructSize = sizeof g; g.lpGlyphs = gl; g.lpOutString = outs; g.nGlyphs = 4;
        GCHK(GetCharacterPlacementA(dc, "AgW1", 4, 0, &g, 0x10 /* GCP_GLYPHSHAPE */) != 0 && gl[0] != 0 && gl[0] != gl[1], "glyph indices\n");
        std::string via_idx = draw(160, 48, font, [&](HDC h) { ExtTextOutW(h, 20, 10, 0x10, NULL, (const WCHAR *)gl, 4, NULL); });
        GCHK(via_idx == base, "ETO_GLYPH_INDEX\n");
    }
    /* GetCharacterPlacement */
    {
        int dxp[4], caret[4], dxk[4];
        uint32_t bad[32] = { 7 };
        uint32_t order[4];
        char cls[4], outs[8];
        GCP g;
        memset(&g, 0, sizeof g);
        g.lStructSize = sizeof g; g.lpOrder = order; g.lpDx = dxp; g.lpCaretPos = caret; g.lpClass = cls; g.lpOutString = outs; g.nGlyphs = 4;
        DWORD r = GetCharacterPlacementA(dc, "AgW1", 4, 0, &g, 0);
        GCHK((r & 0xffff) == (DWORD)sa.cx && (r >> 16) == (DWORD)sa.cy && g.nGlyphs == 4, "placement size packing\n");
        GCHK(order[0] == 0 && order[3] == 3 && caret[0] == 0 && caret[1] == dxp[0] && caret[3] == dxp[0] + dxp[1] + dxp[2] && cls[0] == 1 && cls[3] == 6, "order, caret positions, classes\n");
        GCHK(GetCharacterPlacementA(dc, "AgW1", 4, 0, NULL, 0) == r && GetCharacterPlacementA(dc, "AgW1", 4, 0, &g, 0) == r, "no results: the extent only\n");
        GCHK(GetCharacterPlacementA(dc, "AgW1", 0, 0, &g, 0) == 0 && GetCharacterPlacementA(dc, "AgW1", 4, 0, (void *)bad, 0) == 0 && GetCharacterPlacementA((HDC)(uintptr_t)0x999, "AgW1", 4, 0, &g, 0) == 0, "placement errors\n");
        int sum = 0;
        for (int i = 0; i < 4; i++) sum += dxp[i];
        GCHK(sum == sa.cx, "dx sums to the extent\n");
        g.nGlyphs = 2;
        memset(outs, 0, sizeof outs);
        GCHK(GetCharacterPlacementA(dc, "AgW1", 4, 0, &g, 0) == r && g.nGlyphs == 2 && !strncmp(outs, "Ag", 2) && outs[2] == 0, "nGlyphs bounds the arrays\n");
        g.nGlyphs = 4; g.lpDx = dxk;
        int fit5 = 0;
        for (int i = 0, a = 0; i < 4; i++) { a += dxp[i]; if (a <= dxp[0] + dxp[1] + 1) fit5 = i + 1; }
        r = GetCharacterPlacementA(dc, "AgW1", 4, dxp[0] + dxp[1] + 1, &g, 0x100000 /* GCP_MAXEXTENT */);
        GCHK(g.nMaxFit == fit5 && g.nGlyphs == (uint32_t)fit5 && (r & 0xffff) == (DWORD)(dxp[0] + dxp[1]) , "GCP_MAXEXTENT: %d fit, width %u\n", g.nMaxFit, (unsigned)(r & 0xffff));
        int dj[5];
        g.lpDx = dj; g.nGlyphs = 3;
        GetCharacterPlacementA(dc, "a b", 3, 60, &g, 0x100000 | 0x10000 /* GCP_MAXEXTENT | GCP_JUSTIFY */);
        GCHK(dj[0] + dj[1] + dj[2] == 60, "GCP_JUSTIFY fills nMaxExtent: %d\n", dj[0] + dj[1] + dj[2]);
        WCHAR he[4] = { 0x5d0, 0x5d1, 0x5d2, 0 }, ab[4] = { 'a', 0x5d0, 0x5d1, 'b' };
        g.lpDx = dxp; g.lpOutString = NULL; g.nGlyphs = 3;
        GCHK(GetCharacterPlacementW(dc, he, 3, 0, &g, 2 /* GCP_REORDER */) != 0 && order[0] == 2 && order[1] == 1 && order[2] == 0 && cls[0] == 2, "GCP_REORDER reverses a Hebrew run\n");
        g.nGlyphs = 4;
        GetCharacterPlacementW(dc, ab, 4, 0, &g, 2);
        GCHK(order[0] == 0 && order[1] == 2 && order[2] == 1 && order[3] == 3, "GCP_REORDER keeps the Latin ends\n");
    }
    DeleteDC(dc);
    printf("gdifont text api: failing %d\n", g_fails - f0);
    s4_checks += g_checks - c0;
    s4_failures += g_fails - f0;
    g_checks = c0; g_fails = f0;   /* the reference test above reports the running totals */
}

/* ---- GetOutlineTextMetricsA: there is no line for it in the reference dump, so it is verified against the font tables
 * of the realised face, read here directly with FreeType (OS/2, hhea, head, post, name) and scaled with the realised
 * ppem; the ppem is the lfHeight of a created font, or for a stock font the one at which the tables reproduce the
 * text metrics. Layout: OUTLINETEXTMETRICA with 32-bit fields and native-width pointer slots holding offsets. */
namespace {
struct OTMA { uint32_t otmSize; uint8_t tm[56]; uint8_t filler, panose[10]; uint32_t fsSelection, fsType; int32_t rise, run, italic; uint32_t em; int32_t asc, desc;
    uint32_t gap, cap, xh; int32_t box[4]; int32_t macA, macD; uint32_t macGap, minppem; int32_t sub[2], suboff[2], sup[2], supoff[2];
    uint32_t strikeSize; int32_t strikePos, underSize, underPos; char *pFamily, *pFace, *pStyle, *pFull; };
std::string ft_name(FT_Face ft, int id)
{
    std::string best;
    int score = -1;
    for (FT_UInt i = 0; i < FT_Get_Sfnt_Name_Count(ft); i++) {
        FT_SfntName n;
        if (FT_Get_Sfnt_Name(ft, i, &n) || n.name_id != id) continue;
        int s = n.platform_id == 3 ? (n.language_id == 0x409 ? 3 : 1) : n.platform_id == 1 && n.language_id == 0 ? 2 : -1;
        if (s <= score) continue;
        score = s;
        best.clear();
        if (n.platform_id == 3) for (FT_UInt k = 0; k + 1 < n.string_len; k += 2) best += (char)n.string[k + 1];
        else for (FT_UInt k = 0; k < n.string_len; k++) best += (char)n.string[k];
    }
    return best;
}
}
TEST(text_gdifont_otm)
{
    const char *ref = getenv("FNVNX_FONT_REF"), *host = getenv("FNVNX_FONT_HOST");
    if (!ref || !host) { printf("gdifont: reference not available\n"); CHECK(false); return; }
    std::string r = ref, wine = r + "/wine-fonts/usr/share/wine/fonts";
    int c0 = g_checks, f0 = g_fails, nfonts = 0, nskip = 0, nnon = 0;
    FT_Library lib;
    FT_Init_FreeType(&lib);
    std::vector<std::pair<std::string, std::vector<uint8_t>>> files;   /* every sfnt file of the two directories */
    for (const std::string &dir : { wine, std::string(host) }) {
        FILE *p = popen(("find -L '" + dir + "' -type f 2>/dev/null").c_str(), "r");
        char path[1024];
        while (p && fgets(path, sizeof path, p)) {
            std::string fn(path);
            fn.resize(fn.size() - 1);
            FILE *g = fopen(fn.c_str(), "rb");
            if (!g) continue;
            std::vector<uint8_t> d;
            uint8_t chunk[65536];
            size_t n;
            while ((n = fread(chunk, 1, sizeof chunk, g)) > 0) d.insert(d.end(), chunk, chunk + n);
            fclose(g);
            files.push_back({ fn, std::move(d) });
        }
        if (p) pclose(p);
    }
    struct Case { int stock; const char *face; int h, wt, it; } cases[] = { { 10, 0, 0, 0, 0 }, { 11, 0, 0, 0, 0 }, { 12, 0, 0, 0, 0 }, { 13, 0, 0, 0, 0 }, { 14, 0, 0, 0, 0 }, { 16, 0, 0, 0, 0 }, { 17, 0, 0, 0, 0 },
        { -1, "Liberation Sans", -20, 400, 0 }, { -1, "Liberation Serif", -13, 400, 1 }, { -1, "Liberation Mono", -11, 400, 0 } };
    HDC dc = CreateCompatibleDC(NULL);
    for (const Case &cs : cases) {
        HGDIOBJ fnt;
        if (cs.stock >= 0) fnt = GetStockObject(cs.stock);
        else {
            uint8_t lf[60] = { 0 };
            int32_t v = cs.h; memcpy(lf, &v, 4); v = cs.wt; memcpy(lf + 16, &v, 4);
            lf[20] = (uint8_t)cs.it; lf[23] = 0; strcpy((char *)lf + 28, cs.face);
            fnt = CreateFontIndirectA(lf);
        }
        SelectObject(dc, fnt);
        uint8_t tm[64];
        GetTextMetricsA(dc, tm);
        UINT need = GetOutlineTextMetricsA(dc, 0, NULL);
        if (!(tm[51] & 2 /* TMPF_VECTOR */)) {   /* not an outline font: 0 */
            GCHK(need == 0 && GetOutlineTextMetricsA(dc, 4, tm) == 0, "stock %d: not an outline font\n", cs.stock);
            nnon++;
            continue;
        }
        GCHK(need > sizeof(OTMA) && GetOutlineTextMetricsA(dc, 0, tm) == need, "size query\n");
        std::vector<uint8_t> buf(need + 8, 0xAA);
        GCHK(GetOutlineTextMetricsA(dc, need, buf.data()) == need, "full fill returns the size\n");
        OTMA o;
        memcpy(&o, buf.data(), sizeof o);
        auto str = [&](char *off) { return std::string((const char *)buf.data() + (uintptr_t)off); };
        std::string fam = str(o.pFamily), sty = str(o.pStyle);
        FT_Face ft = NULL;
        for (auto &e : files) {
            FT_Face t;
            if (FT_New_Memory_Face(lib, e.second.data(), (FT_Long)e.second.size(), 0, &t)) continue;
            if (FT_IS_SFNT(t) && ft_name(t, 1) == fam && ft_name(t, 2) == sty && !ft) { ft = t; continue; }
            FT_Done_Face(t);
        }
        if (!ft) { GCHK(false, "stock %d: no font file for %s / %s\n", cs.stock, fam.c_str(), sty.c_str()); continue; }
        TT_OS2 *os2 = (TT_OS2 *)FT_Get_Sfnt_Table(ft, FT_SFNT_OS2);
        TT_HoriHeader *hh = (TT_HoriHeader *)FT_Get_Sfnt_Table(ft, FT_SFNT_HHEA);
        TT_Header *head = (TT_Header *)FT_Get_Sfnt_Table(ft, FT_SFNT_HEAD);
        TT_Postscript *post = (TT_Postscript *)FT_Get_Sfnt_Table(ft, FT_SFNT_POST);
        int upem = ft->units_per_EM, ppem = -cs.h;
        if (cs.stock >= 0) {   /* the ppem at which the tables reproduce the text metrics */
            ppem = 0;
            for (int p = 1; p < 200 && !ppem; p++) {
                FT_Fixed em = FT_MulDiv(p, 65536, upem);
                if (FT_MulFix(os2->usWinAscent, em) + FT_MulFix(os2->usWinDescent, em) == rd32(tm, 0) && FT_MulFix(os2->xAvgCharWidth, em) == rd32(tm, 20)) ppem = p;
            }
            if (!ppem) { nskip++; FT_Done_Face(ft); continue; }
        }
        FT_Fixed em = FT_MulDiv(ppem, 65536, upem);
        auto SC = [&](long v) { return (int32_t)FT_MulFix(v, em); };
        std::string nm = "stock " + std::to_string(cs.stock);
        GCHK(o.otmSize == need, "%s otmSize\n", nm.c_str());
        GCHK(!memcmp(o.tm, tm, 53), "%s embedded TEXTMETRIC = GetTextMetricsA\n", nm.c_str());
        GCHK(o.filler == 0 && !memcmp(o.panose, os2->panose, 10), "%s panose\n", nm.c_str());
        GCHK(o.fsSelection == os2->fsSelection && o.fsType == (os2->fsType & 0x30eu), "%s fsSelection/fsType\n", nm.c_str());
        GCHK(o.rise == hh->caret_Slope_Rise && o.run == hh->caret_Slope_Run, "%s caret slope\n", nm.c_str());
        GCHK(o.italic == (int32_t)floor(post->italicAngle / 65536.0 * 10.0 + 0.5), "%s italic angle %d\n", nm.c_str(), o.italic);
        GCHK((int)o.em == upem && o.asc == SC(os2->sTypoAscender) && o.desc == SC(os2->sTypoDescender) && (int32_t)o.gap == SC(os2->sTypoLineGap), "%s typo metrics\n", nm.c_str());
        GCHK((int32_t)o.cap == SC(os2->sCapHeight) && (int32_t)o.xh == SC(os2->sxHeight), "%s cap / x height\n", nm.c_str());
        GCHK(o.box[0] == SC(head->xMin) && o.box[1] == SC(head->yMax) && o.box[2] == SC(head->xMax) && o.box[3] == SC(head->yMin), "%s font box\n", nm.c_str());
        GCHK(o.macA == rd32(tm, 4) && o.macD == -rd32(tm, 8) && (int32_t)o.macGap == SC(hh->Line_Gap) && o.minppem == head->Lowest_Rec_PPEM, "%s Mac metrics / min ppem\n", nm.c_str());
        GCHK(o.sub[0] == SC(os2->ySubscriptXSize) && o.sub[1] == SC(os2->ySubscriptYSize) && o.suboff[0] == SC(os2->ySubscriptXOffset) && o.suboff[1] == SC(os2->ySubscriptYOffset), "%s subscript\n", nm.c_str());
        GCHK(o.sup[0] == SC(os2->ySuperscriptXSize) && o.sup[1] == SC(os2->ySuperscriptYSize) && o.supoff[0] == SC(os2->ySuperscriptXOffset) && o.supoff[1] == SC(os2->ySuperscriptYOffset), "%s superscript\n", nm.c_str());
        GCHK((int32_t)o.strikeSize == SC(os2->yStrikeoutSize) && o.strikePos == SC(os2->yStrikeoutPosition) && o.underSize == SC(post->underlineThickness) && o.underPos == SC(post->underlinePosition), "%s strikeout / underscore\n", nm.c_str());
        std::string full = ft_name(ft, 3);
        if (full.empty()) full = ft_name(ft, 4);
        GCHK(fam == ft_name(ft, 1) && str(o.pFace) == ft_name(ft, 4) && sty == ft_name(ft, 2) && str(o.pFull) == full, "%s names\n", nm.c_str());
        GCHK((uintptr_t)o.pFamily == sizeof(OTMA) && (uintptr_t)o.pFace == sizeof(OTMA) + fam.size() + 1 && buf[need] == 0xAA, "%s strings follow the structure in order\n", nm.c_str());
        /* a short buffer is filled as far as it goes */
        std::vector<uint8_t> small(sizeof(OTMA) + 3, 0xBB);
        GCHK(GetOutlineTextMetricsA(dc, (UINT)small.size(), small.data()) == small.size() && *(uint32_t *)small.data() == small.size() && !memcmp(small.data() + 4, buf.data() + 4, small.size() - 4), "%s short buffer\n", nm.c_str());
        FT_Done_Face(ft);
        nfonts++;
    }
    GCHK(GetOutlineTextMetricsA((HDC)(uintptr_t)0x999, 0, NULL) == 0, "bad DC\n");
    DeleteDC(dc);
    FT_Done_FreeType(lib);
    GCHK(nfonts >= 3, "outline fonts verified: %d\n", nfonts);
    printf("gdifont otm (verified against the font tables, not the dump): %d outline fonts, %d not outline, %d skipped (no ppem), failing %d\n", nfonts, nnon, nskip, g_fails - f0);
    s4_checks += g_checks - c0;
    s4_failures += g_fails - f0;
    g_checks = c0; g_fails = f0;
}
