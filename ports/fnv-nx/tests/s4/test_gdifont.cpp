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
#include <stdint.h>
#include <stdlib.h>
#include <map>
#include <string>
#include <vector>

extern "C" {
void fnvnx_gdi_set_font_dirs(const char *wine_fonts_dir, const char *host_fonts_dir, const char *registry_dir);
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
    fnvnx_gdi_set_font_dirs(wine.c_str(), host, (r + "/reg").c_str());

    FT_Library lib;
    FT_Int ma = 0, mi = 0, pa = 0;
    CHECK_EQ(FT_Init_FreeType(&lib), 0);
    FT_Library_Version(lib, &ma, &mi, &pa);
    printf("gdifont: FreeType %d.%d.%d\n", ma, mi, pa);
    CHECK(ma == 2 && mi == 13 && pa == 2);
    FT_Done_FreeType(lib);
    scan_formats(wine);
    scan_formats(host);

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
