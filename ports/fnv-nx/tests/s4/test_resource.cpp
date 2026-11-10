/* test_resource.cpp - platform/win32/resource.cpp: PE resource directory lookups, LoadIconA/LoadCursorA from module
 * resources, comctl32 image lists. The test lays out a synthetic mapped PE32 image (DOS header, NT headers, one
 * section, a resource directory) and installs it through fnvnx_resource_set_image_for_test. */
#include "s4_test.h"

#include <stdint.h>
#include <map>
#include <string>
#include <vector>

extern "C" {
void *FindResourceA(HMODULE, LPCSTR, LPCSTR);
void *FindResourceExA(HMODULE, LPCSTR, LPCSTR, WORD);
void *LoadResource(HMODULE, void *);
void *LockResource(void *);
DWORD SizeofResource(HMODULE, void *);
BOOL FreeResource(void *);
typedef BOOL (*enum_name_cb)(HMODULE, LPCSTR, LPSTR, LONG_PTR);
BOOL EnumResourceNamesA(HMODULE, LPCSTR, enum_name_cb, LONG_PTR);
BOOL InitCommonControlsEx(const void *);
void *ImageList_LoadImageA(HINSTANCE, LPCSTR, int, int, DWORD, UINT, UINT);
BOOL ImageList_Destroy(void *);
int ImageList_GetImageCount(void *);
BOOL ImageList_GetIconSize(void *, int *, int *);
int fnvnx_imagelist_pixels(void *, int, const uint32_t **);
int fnvnx_icon_pixels(HANDLE, const uint32_t **, int *, int *, int *, int *);
void fnvnx_resource_set_image_for_test(HMODULE, const void *, size_t);
}

typedef std::vector<uint8_t> bytes;
#define MI(n) ((LPCSTR)(uintptr_t)(n))
static const HMODULE MOD = (HMODULE)(uintptr_t)0x500000;
static const HMODULE EXE = (HMODULE)(uintptr_t)0x400000;

static void p16(bytes &b, unsigned v) { b.push_back(v & 255); b.push_back((v >> 8) & 255); }
static void p32(bytes &b, uint32_t v) { p16(b, v & 0xFFFF); p16(b, v >> 16); }
static void put32(bytes &b, size_t at, uint32_t v) { for (int i = 0; i < 4; i++) b[at + i] = (v >> (8 * i)) & 255; }

/* ---- resource tree: type -> name -> lang -> blob. Names are uppercase in the directory (sorted by uppercase) */
struct Key { bool named; std::string s; int id; bool operator<(const Key &o) const { return named != o.named ? named > o.named : named ? s < o.s : id < o.id; } };
struct Node { std::map<Key, Node> kids; bytes blob; bool leaf = false; };
static Key K(int id) { return Key{ false, "", id }; }
static Key K(const char *s) { return Key{ true, s, 0 }; }
static void add(Node &root, Key type, Key name, int lang, const bytes &data)
{
    if (lang < 0) { root.kids[type].kids[name]; return; }   /* a name with an empty language directory */
    Node &n = root.kids[type].kids[name].kids[K(lang)];
    n.leaf = true; n.blob = data;
}
static void emit_dir(const Node &d, int level, bytes &out, size_t at)
{
    size_t nn = 0, ni = 0;
    for (auto &kv : d.kids) (kv.first.named ? nn : ni)++;
    p32(out, 0); p32(out, 0); p16(out, 0); p16(out, 0); p16(out, (unsigned)nn); p16(out, (unsigned)ni);
    size_t ents = out.size();
    out.resize(out.size() + 8 * d.kids.size());
    size_t i = 0;
    for (auto &kv : d.kids) {
        uint32_t nm = kv.first.named ? 0x80000000u | (uint32_t)out.size() : (uint32_t)kv.first.id;
        if (kv.first.named) {
            p16(out, (unsigned)kv.first.s.size());
            for (char c : kv.first.s) p16(out, (unsigned char)c);
        }
        uint32_t off;
        if (level == 2) {
            off = (uint32_t)out.size();
            p32(out, 0); p32(out, (uint32_t)kv.second.blob.size()); p32(out, 0); p32(out, 0);   /* RVA patched below */
            while (out.size() & 3) out.push_back(0);
            size_t data = out.size();
            out.insert(out.end(), kv.second.blob.begin(), kv.second.blob.end());
            while (out.size() & 3) out.push_back(0);
            put32(out, off, (uint32_t)(0x1000 + data));
        } else {
            while (out.size() & 3) out.push_back(0);
            off = 0x80000000u | (uint32_t)out.size();
            emit_dir(kv.second, level + 1, out, 0);
        }
        put32(out, ents + 8 * i, nm); put32(out, ents + 8 * i + 4, off);
        i++;
    }
    (void)at;
}
static bytes build_image(const Node &root, bool with_dir = true)
{
    bytes rs;
    emit_dir(root, 0, rs, 0);
    bytes im(0x1000 + rs.size(), 0);
    im[0] = 'M'; im[1] = 'Z'; put32(im, 0x3C, 0x80);
    memcpy(&im[0x80], "PE\0\0", 4);
    im[0x84] = 0x4C; im[0x85] = 0x01; im[0x86] = 1;                 /* i386, 1 section */
    im[0x84 + 16] = 224;                                             /* SizeOfOptionalHeader */
    im[0x98] = 0x0B; im[0x99] = 0x01;                                /* PE32 */
    put32(im, 0x98 + 56, (uint32_t)im.size());
    put32(im, 0x98 + 92, 16);                                        /* NumberOfRvaAndSizes */
    if (with_dir) { put32(im, 0xF8 + 16, 0x1000); put32(im, 0xF8 + 20, (uint32_t)rs.size()); }
    memcpy(&im[0x178], ".rsrc", 5);
    put32(im, 0x178 + 8, (uint32_t)rs.size()); put32(im, 0x178 + 12, 0x1000);
    put32(im, 0x178 + 16, (uint32_t)rs.size()); put32(im, 0x178 + 20, 0x1000);
    memcpy(&im[0x1000], rs.data(), rs.size());
    return im;
}

/* ---- DIB builders */
static bytes dibhdr(int w, int h, int bpp, int comp, const std::vector<uint32_t> &pal, int clrused = -1)
{
    bytes b;
    p32(b, 40); p32(b, (uint32_t)w); p32(b, (uint32_t)h); p16(b, 1); p16(b, bpp); p32(b, comp); p32(b, 0); p32(b, 0); p32(b, 0);
    p32(b, clrused < 0 ? (uint32_t)pal.size() : (uint32_t)clrused); p32(b, 0);
    for (uint32_t c : pal) { b.push_back(c & 255); b.push_back((c >> 8) & 255); b.push_back((c >> 16) & 255); b.push_back(0); }  /* 0xRRGGBB in -> RGBQUAD B,G,R,0 */
    return b;
}
/* indexed rows, bottom-up (file order), pixel f(x, y) with y counted from the top */
template <class F> static void rows(bytes &b, int w, int h, int bpp, F f, bool topdown = false)
{
    int stride = ((w * bpp + 31) / 32) * 4;
    for (int r = 0; r < h; r++) {
        int y = topdown ? r : h - 1 - r;
        bytes row(stride, 0);
        for (int x = 0; x < w; x++) {
            unsigned v = f(x, y);
            if (bpp == 8) row[x] = v;
            else if (bpp == 4) row[x / 2] |= (x & 1) ? v : v << 4;
            else if (bpp == 1) row[x / 8] |= v << (7 - (x & 7));
            else if (bpp == 24) { row[3 * x] = v & 255; row[3 * x + 1] = (v >> 8) & 255; row[3 * x + 2] = (v >> 16) & 255; }
            else { for (int k = 0; k < 4; k++) row[4 * x + k] = (v >> (8 * k)) & 255; }
        }
        b.insert(b.end(), row.begin(), row.end());
    }
}
static const uint32_t MAGENTA = 0xFF00FF, RED = 0xFF0000, GREEN = 0x00FF00, BLUE = 0x0000FF, WHITE = 0xFFFFFF, BLACK = 0;

/* ---- the resources of the synthetic module */
static bytes dib_strip()   /* RT_BITMAP 180: 48x16 8bpp, three 16x16 images red | green | blue; magenta marks */
{
    bytes b = dibhdr(48, 16, 8, 0, { MAGENTA, RED, GREEN, BLUE });
    rows(b, 48, 16, 8, [](int x, int y) { return (x == 0 && y == 0) || (x == 5 && y == 5) || (x == 19 && y == 2) ? 0 : 1 + x / 16; });
    return b;
}
static bytes icon_dib(int w, int h, int bpp, bool andmask_opaque_left)
{
    std::vector<uint32_t> pal;
    if (bpp == 4) pal = { BLACK, RED, GREEN, BLUE, WHITE };
    if (bpp == 8) pal = { BLACK, RED, GREEN, BLUE, WHITE };
    bytes b = dibhdr(w, 2 * h, bpp, 0, pal);
    if (bpp == 32) rows(b, w, h, 32, [](int x, int y) { return (uint32_t)((x < 16 ? 0xFF : 0x80) << 24 | x << 16 | y << 8 | 0x55); });
    else rows(b, w, h, bpp, [](int x, int y) { return (unsigned)(1 + (x + y) % 4); });
    /* AND mask, 1bpp: pixels with x >= 16 (or all x >= w/2) transparent, unless the image has alpha */
    rows(b, w, h, 1, [&](int x, int) { return (unsigned)(!andmask_opaque_left ? 0 : (x >= w / 2 ? 1 : 0)); });
    return b;
}
static bytes grp(int type, const std::vector<std::vector<int>> &e)   /* {w, h, bitcount, bytes, id} */
{
    bytes b; p16(b, 0); p16(b, type); p16(b, (unsigned)e.size());
    for (auto &x : e) {
        if (type == 1) { b.push_back(x[0] & 255); b.push_back(x[1] & 255); b.push_back(0); b.push_back(0); p16(b, 1); p16(b, x[2]); }
        else { p16(b, x[0]); p16(b, x[1] * 2); p16(b, 1); p16(b, x[2]); }
        p32(b, x[3]); p16(b, x[4]);
    }
    return b;
}
static bytes cursor_blob()
{
    bytes b; p16(b, 5); p16(b, 9);   /* hotspot */
    bytes d = dibhdr(32, 64, 1, 0, { BLACK, WHITE });
    rows(d, 32, 32, 1, [](int x, int) { return (unsigned)(x & 1); });
    rows(d, 32, 32, 1, [](int x, int) { return (unsigned)(x >= 16); });
    b.insert(b.end(), d.begin(), d.end());
    return b;
}
static bytes str(const char *s) { return bytes(s, s + strlen(s) + 1); }

static Node make_tree()
{
    Node r;
    add(r, K(2), K(180), 0x409, dib_strip());
    { bytes b = dibhdr(4, 2, 24, 0, {}); rows(b, 4, 2, 24, [](int x, int y) { static const uint32_t c[8] = { 0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF, 0x000000, 0x123456, 0, 0 }; return c[y * 4 + x]; }); add(r, K(2), K(181), 0x409, b); }
    { bytes b = dibhdr(4, 2, 8, 1, { MAGENTA, RED, GREEN, BLUE });   /* RLE8: bottom row 4 x green; top row 2 x red, 2 x blue */
      for (int v : { 4, 2, 0, 0, 2, 1, 2, 3, 0, 1 }) { b.push_back(v); }
      add(r, K(2), K(182), 0x409, b); }
    { bytes b = dibhdr(8, 2, 1, 0, { BLACK, WHITE }); rows(b, 8, 2, 1, [](int x, int y) { return (unsigned)(y == 0 && "10100101"[x] == '1'); }); add(r, K(2), K(183), 0x409, b); }
    { bytes b = dibhdr(4, 2, 4, 2, { MAGENTA, RED, GREEN, BLUE });   /* RLE4: bottom 1,2,1,2; top absolute 0,3,2,1 */
      for (int v : { 4, 0x12, 0, 0, 0, 4, 0x03, 0x21, 0, 1 }) { b.push_back(v); }
      add(r, K(2), K(184), 0x409, b); }
    { bytes b = dibhdr(2, -2, 32, 0, {}); rows(b, 2, 2, 32, [](int x, int y) { return (uint32_t)(0x00302010 + x + 16 * y); }, true); add(r, K(2), K(185), 0x409, b); }
    { bytes b = dibhdr(4, 2, 4, 0, { MAGENTA, RED, GREEN, BLUE }); rows(b, 4, 2, 4, [](int x, int y) { return (unsigned)(x + 2 * y) & 3; }); add(r, K(2), K(186), 0x409, b); }
    /* icons: MAINICON = 16x16x4, 32x32x8, 32x32x32; 101 = 16x16x4 + 32x32x8; "ONLY4" = 16x16x4 (an image shared by id) */
    bytes i1 = icon_dib(16, 16, 4, true), i2 = icon_dib(32, 32, 8, true), i3 = icon_dib(32, 32, 32, true);
    add(r, K(3), K(1), 0x409, i1); add(r, K(3), K(2), 0x409, i2); add(r, K(3), K(3), 0x409, i3);
    add(r, K(14), K("MAINICON"), 0x409, grp(1, { { 16, 16, 4, (int)i1.size(), 1 }, { 32, 32, 8, (int)i2.size(), 2 }, { 32, 32, 32, (int)i3.size(), 3 } }));
    add(r, K(14), K("ONLY4"), 0x409, grp(1, { { 16, 16, 4, (int)i1.size(), 1 } }));
    add(r, K(14), K(101), 0x409, grp(1, { { 16, 16, 4, (int)i1.size(), 1 }, { 32, 32, 8, (int)i2.size(), 2 } }));
    add(r, K(1), K(70), 0x409, cursor_blob());
    add(r, K(12), K(7), 0x409, grp(2, { { 32, 32, 1, (int)cursor_blob().size() - 4, 70 } }));
    /* languages */
    add(r, K(10), K("DATA"), 0x409, str("english")); add(r, K(10), K("DATA"), 0x407, str("deutsch"));
    add(r, K(10), K("ONLYDE"), 0x407, str("de")); add(r, K(10), K("ONLYDE"), 0x411, str("ja"));
    add(r, K(10), K("NEUT"), 0, str("neutral"));
    add(r, K(10), K("EMPTYLANG"), -1, str("x"));
    add(r, K("MYTYPE"), K("HELLO"), 0x409, str("hi"));
    add(r, K("MYTYPE"), K(5), 0x409, str("five"));
    return r;
}

static std::string rs(void *h) { return h ? std::string((const char *)h) : std::string("(null)"); }
static std::string res_text(HMODULE m, void *r) { void *d = r ? LoadResource(m, r) : NULL; return rs(d ? LockResource(d) : NULL); }

TEST(resource_lookup_rules)
{
    bytes img = build_image(make_tree());
    fnvnx_resource_set_image_for_test(MOD, img.data(), img.size());
    void *r = FindResourceA(MOD, "data", MI(10));          /* name case-insensitive; thread default language 0x409 */
    CHECK(r != NULL);
    CHECK(res_text(MOD, r) == "english"); CHECK_EQ(SizeofResource(MOD, r), 8);
    CHECK(res_text(MOD, FindResourceExA(MOD, MI(10), "DATA", 0x407)) == "deutsch");
    CHECK(res_text(MOD, FindResourceExA(MOD, MI(10), "DATA", 0x40C)) == "english");   /* absent: user default */
    CHECK(res_text(MOD, FindResourceExA(MOD, MI(10), "ONLYDE", 0x40C)) == "de");       /* no 0x409/0/English: first */
    CHECK(res_text(MOD, FindResourceExA(MOD, MI(10), "NEUT", 0x407)) == "neutral");    /* neutral before first */
    CHECK(res_text(MOD, FindResourceA(MOD, "Hello", "mytype")) == "hi");              /* string type, string name */
    CHECK(res_text(MOD, FindResourceA(MOD, MI(5), "MYTYPE")) == "five");
    CHECK(res_text(MOD, FindResourceA(MOD, "#5", "MYTYPE")) == "five");
    CHECK(FindResourceA(MOD, "#180", "#2") != NULL);     /* "#n" is the integer n, for the type too */
    CHECK(FindResourceA(MOD, MI(180), MI(2)) == FindResourceA(MOD, "#180", "#2"));
    CHECK_EQ(SizeofResource(MOD, FindResourceA(MOD, MI(180), MI(2))), dib_strip().size());
    CHECK(FindResourceA(MOD, MI(99), MI(99)) == NULL); CHECK_EQ(GetLastError(), 1813);
    CHECK(FindResourceA(MOD, MI(999), MI(2)) == NULL); CHECK_EQ(GetLastError(), 1814);
    CHECK(FindResourceA(MOD, "NOSUCH", "MYTYPE") == NULL); CHECK_EQ(GetLastError(), 1814);
    CHECK(FindResourceA(MOD, "X", "NOTYPE") == NULL); CHECK_EQ(GetLastError(), 1813);
    CHECK(FindResourceA(MOD, "EMPTYLANG", MI(10)) == NULL); CHECK_EQ(GetLastError(), 1815);
    CHECK(FindResourceA((HMODULE)(uintptr_t)0x600000, MI(1), MI(2)) == NULL); CHECK_EQ(GetLastError(), 1812);   /* no image */
    CHECK(FreeResource(LoadResource(MOD, r)) == FALSE);
    CHECK(LockResource(NULL) == NULL);
    CHECK(LoadResource(MOD, NULL) == NULL);
    /* NULL = the process exe */
    fnvnx_resource_set_image_for_test(EXE, img.data(), img.size());
    CHECK(FindResourceA(NULL, "DATA", MI(10)) != NULL);
    fnvnx_resource_set_image_for_test(EXE, NULL, 0);
    CHECK(FindResourceA(NULL, "DATA", MI(10)) == NULL); CHECK_EQ(GetLastError(), 1812);
    /* enumeration: named ones first, then ids, each name once */
    struct E { static BOOL cb(HMODULE, LPCSTR, LPSTR n, LONG_PTR l) { std::string *o = (std::string *)l; *o += ((uintptr_t)n >> 16) ? std::string(n) + "," : "#" + std::to_string((uintptr_t)n) + ","; return TRUE; } };
    std::string seen;
    CHECK(EnumResourceNamesA(MOD, MI(10), E::cb, (LONG_PTR)&seen));
    CHECK(seen == "DATA,EMPTYLANG,NEUT,ONLYDE,");
    fnvnx_resource_set_image_for_test(MOD, NULL, 0);
}

TEST(resource_malformed_directories)
{
    bytes good = build_image(make_tree());
    fnvnx_resource_set_image_for_test(MOD, good.data(), good.size());
    CHECK(FindResourceA(MOD, MI(180), MI(2)) != NULL);
    auto bad = [&](const char *what, void (*patch)(bytes &)) {
        bytes b = good; patch(b);
        fnvnx_resource_set_image_for_test(MOD, b.data(), b.size());
        void *r = FindResourceA(MOD, MI(180), MI(2));
        s4_checks++; if (r) { s4_failures++; fprintf(stderr, "  FAIL %s: lookup succeeded\n", what); }
        CHECK_EQ(GetLastError(), 193);
    };
    bad("bad MZ", [](bytes &b) { b[0] = 'X'; });
    bad("bad PE", [](bytes &b) { b[0x80] = 'X'; });
    bad("e_lfanew outside", [](bytes &b) { put32(b, 0x3C, 0x7FFFFFF0u); });
    bad("not PE32", [](bytes &b) { b[0x98] = 0x0B; b[0x99] = 0x02; });
    bad("dir RVA outside", [](bytes &b) { put32(b, 0xF8 + 16, 0x7FFFFF00u); });
    bad("type subdir outside", [](bytes &b) { put32(b, 0x1000 + 16 + 8 * 2 + 4, 0x80000000u | 0x7FFFFFF0u); });   /* entry 2 = type 2 (MYTYPE, 1, 2, ...) */
    bad("entry count huge", [](bytes &b) { b[0x1000 + 14] = 0xFF; b[0x1000 + 15] = 0xFF; });
    bad("leaf where dir expected", [](bytes &b) { for (int i = 0; i < 8; i++) put32(b, 0x1000 + 16 + 4 + 8 * i, 0x40); });
    /* data entry pointing outside: found, but LoadResource fails and the size is 0 */
    bytes b = good;
    void *r0 = (fnvnx_resource_set_image_for_test(MOD, b.data(), b.size()), FindResourceA(MOD, MI(180), MI(2)));
    CHECK(r0 != NULL);
    uint8_t *e = (uint8_t *)r0; uint32_t rva = 0x7FFFFF00u; memcpy(e, &rva, 4);
    CHECK(LoadResource(MOD, r0) == NULL); CHECK_EQ(SizeofResource(MOD, r0), 0);
    /* a handle that is not inside the image */
    uint8_t other[16] = {0};
    CHECK(LoadResource(MOD, other) == NULL);
    /* a directory with no resource data directory */
    bytes nod = build_image(make_tree(), false);
    fnvnx_resource_set_image_for_test(MOD, nod.data(), nod.size());
    CHECK(FindResourceA(MOD, MI(180), MI(2)) == NULL); CHECK_EQ(GetLastError(), 1812);
    fnvnx_resource_set_image_for_test(MOD, NULL, 0);
}

TEST(resource_icons_and_cursors)
{
    bytes img = build_image(make_tree());
    fnvnx_resource_set_image_for_test(MOD, img.data(), img.size());
    HICON ic = LoadIconA(MOD, "MAINICON");   /* 32x32: closest size, then most colours = the 32bpp image */
    CHECK(ic != NULL);
    CHECK(ic == LoadIconA(MOD, "mainicon")); CHECK(ic == LoadIconA(MOD, "MAINICON"));
    const uint32_t *px = NULL; int w = 0, h = 0, hx = -1, hy = -1;
    CHECK(fnvnx_icon_pixels(ic, &px, &w, &h, &hx, &hy)); CHECK_EQ(w, 32); CHECK_EQ(h, 32);
    if (px) {
        CHECK_EQ(px[0], 0xFF000055u);                       /* 32bpp, alpha 0xFF at x < 16 (y=0, x=0) */
        CHECK_EQ(px[5 * 32 + 3], 0xFF030555u);              /* (x=3, y=5): top-down */
        CHECK_EQ(px[7 * 32 + 20], 0x80140755u);             /* alpha 0x80 at x >= 16: used as is, the AND mask ignored */
    }
    HICON i8 = LoadIconA(MOD, MI(101));       /* 32x32x8bpp with an AND mask: x >= 16 transparent */
    CHECK(i8 != NULL); CHECK(i8 != ic); CHECK(i8 == LoadIconA(MOD, "#101"));
    CHECK(fnvnx_icon_pixels(i8, &px, &w, &h, &hx, &hy)); CHECK_EQ(w, 32);
    if (px) {
        CHECK_EQ(px[0], 0xFFFF0000u);                       /* 1 + (0+0)%4 = idx1 red */
        CHECK_EQ(px[1], 0xFF00FF00u);                       /* idx2 green */
        CHECK_EQ(px[3 * 32 + 2], 0xFF00FF00u);              /* x+y=5: 1 + 1 = green */
        CHECK_EQ(px[16] >> 24, 0u);                          /* masked */
    }
    HICON i4 = LoadIconA(MOD, "ONLY4");       /* only a 16x16x4bpp image: chosen, not scaled */
    CHECK(i4 != NULL);
    CHECK(fnvnx_icon_pixels(i4, &px, &w, &h, &hx, &hy)); CHECK_EQ(w, 16); CHECK_EQ(h, 16);
    if (px) { CHECK_EQ(px[0], 0xFFFF0000u); CHECK_EQ(px[2], 0xFF0000FFu); CHECK_EQ(px[8] >> 24, 0u); }
    HCURSOR cu = LoadCursorA(MOD, MI(7));
    CHECK(cu != NULL); CHECK(cu == LoadCursorA(MOD, MI(7))); CHECK(cu != (HCURSOR)ic);
    CHECK(fnvnx_icon_pixels(cu, &px, &w, &h, &hx, &hy)); CHECK_EQ(w, 32); CHECK_EQ(h, 32); CHECK_EQ(hx, 5); CHECK_EQ(hy, 9);
    if (px) { CHECK_EQ(px[0], 0xFF000000u); CHECK_EQ(px[1], 0xFFFFFFFFu); CHECK_EQ(px[20 + 3 * 32] >> 24, 0u); }
    CHECK(LoadIconA(MOD, MI(7)) == NULL); CHECK_EQ(GetLastError(), 1814);   /* an icon id that is only a cursor group */
    CHECK(LoadIconA(MOD, "NOPE") == NULL); CHECK_EQ(GetLastError(), 1814);
    CHECK(LoadCursorA(MOD, "MAINICON") == NULL); CHECK_EQ(GetLastError(), 1814);
    CHECK(fnvnx_icon_pixels((HANDLE)(uintptr_t)8, &px, &w, &h, &hx, &hy) == 0);
    /* a corrupted image data: group says 32x32 but the RT_ICON is missing */
    fnvnx_resource_set_image_for_test(MOD, NULL, 0);
    CHECK(LoadIconA(MOD, "MAINICON") == NULL); CHECK_EQ(GetLastError(), 1812);
}

static bool px_eq(const uint32_t *p, int x, int y, int stride, uint32_t v) { return p && p[y * stride + x] == v; }

TEST(resource_imagelist)
{
    bytes img = build_image(make_tree());
    fnvnx_resource_set_image_for_test(MOD, img.data(), img.size());
    uint32_t icc[2] = { 8, 0x3FF };
    CHECK(InitCommonControlsEx(icc)); CHECK(!InitCommonControlsEx(NULL));
    icc[0] = 4; CHECK(!InitCommonControlsEx(icc)); icc[0] = 12; CHECK(!InitCommonControlsEx(icc));
    void *il = ImageList_LoadImageA(MOD, MI(180), 16, 1, 0xFF000000u /* CLR_DEFAULT */, 0, 0);
    CHECK(il != NULL); CHECK_EQ(ImageList_GetImageCount(il), 3);
    int cx = 0, cy = 0; CHECK(ImageList_GetIconSize(il, &cx, &cy)); CHECK_EQ(cx, 16); CHECK_EQ(cy, 16);
    const uint32_t *p0 = NULL, *p1 = NULL, *p2 = NULL;
    CHECK(fnvnx_imagelist_pixels(il, 0, &p0)); CHECK(fnvnx_imagelist_pixels(il, 1, &p1)); CHECK(fnvnx_imagelist_pixels(il, 2, &p2));
    CHECK(!fnvnx_imagelist_pixels(il, 3, &p2)); CHECK(!fnvnx_imagelist_pixels(il, -1, &p2));
    CHECK(px_eq(p0, 3, 3, 16, 0xFFFF0000u)); CHECK(px_eq(p1, 3, 3, 16, 0xFF00FF00u)); CHECK(px_eq(p2, 3, 3, 16, 0xFF0000FFu));
    CHECK(px_eq(p0, 0, 0, 16, 0)); CHECK(px_eq(p0, 5, 5, 16, 0)); CHECK(px_eq(p1, 3, 2, 16, 0));   /* top-left colour (magenta) masked */
    CHECK(ImageList_Destroy(il)); CHECK(!ImageList_Destroy(il)); CHECK(!ImageList_Destroy(NULL));
    CHECK_EQ(ImageList_GetImageCount(il), 0);
    il = ImageList_LoadImageA(MOD, MI(180), 16, 1, 0xFFFFFFFFu /* CLR_NONE */, 0, 0);
    CHECK(il && fnvnx_imagelist_pixels(il, 0, &p0)); CHECK(px_eq(p0, 0, 0, 16, 0xFFFF00FFu)); ImageList_Destroy(il);
    il = ImageList_LoadImageA(MOD, MI(180), 16, 1, 0x0000FF /* COLORREF red */, 0, 0);
    CHECK(il && fnvnx_imagelist_pixels(il, 0, &p0) && fnvnx_imagelist_pixels(il, 1, &p1));
    CHECK(px_eq(p0, 3, 3, 16, 0)); CHECK(px_eq(p1, 3, 3, 16, 0xFF00FF00u)); CHECK(px_eq(p0, 0, 0, 16, 0xFFFF00FFu)); ImageList_Destroy(il);
    CHECK(ImageList_LoadImageA(MOD, MI(180), 0, 1, 0xFFFFFFFFu, 0, 0) == NULL);
    CHECK(ImageList_LoadImageA(MOD, MI(180), 100, 1, 0xFFFFFFFFu, 0, 0) == NULL);
    CHECK(ImageList_LoadImageA(MOD, MI(777), 16, 1, 0xFFFFFFFFu, 0, 0) == NULL);
    CHECK(ImageList_LoadImageA(MOD, MI(180), 16, 1, 0xFFFFFFFFu, 9, 0) == NULL);
    /* every DIB format: 1 image of the whole width */
    auto one = [&](int id, int w, int h, std::initializer_list<std::pair<std::pair<int, int>, uint32_t>> want) {
        void *l = ImageList_LoadImageA(MOD, MI(id), w, 1, 0xFFFFFFFFu, 0, 0);
        const uint32_t *p = NULL;
        CHECK(l != NULL); if (!l) return;
        CHECK_EQ(ImageList_GetImageCount(l), 1); ImageList_GetIconSize(l, &cx, &cy); CHECK_EQ(cx, w); CHECK_EQ(cy, h);
        CHECK(fnvnx_imagelist_pixels(l, 0, &p));
        for (auto &t : want) { s4_checks++; if (!px_eq(p, t.first.first, t.first.second, w, t.second)) { s4_failures++; fprintf(stderr, "  FAIL dib %d (%d,%d)\n", id, t.first.first, t.first.second); } }
        ImageList_Destroy(l);
    };
    one(181, 4, 2, { { { 0, 0 }, 0xFFFF0000u }, { { 1, 0 }, 0xFF00FF00u }, { { 2, 0 }, 0xFF0000FFu }, { { 3, 0 }, 0xFFFFFFFFu }, { { 0, 1 }, 0xFF000000u }, { { 1, 1 }, 0xFF123456u } });   /* 24bpp */
    one(182, 4, 2, { { { 0, 0 }, 0xFFFF0000u }, { { 1, 0 }, 0xFFFF0000u }, { { 2, 0 }, 0xFF0000FFu }, { { 3, 0 }, 0xFF0000FFu }, { { 0, 1 }, 0xFF00FF00u }, { { 3, 1 }, 0xFF00FF00u } });   /* RLE8 */
    one(183, 8, 2, { { { 0, 0 }, 0xFFFFFFFFu }, { { 1, 0 }, 0xFF000000u }, { { 2, 0 }, 0xFFFFFFFFu }, { { 7, 0 }, 0xFFFFFFFFu }, { { 6, 0 }, 0xFF000000u }, { { 2, 1 }, 0xFF000000u } });   /* 1bpp */
    one(184, 4, 2, { { { 0, 1 }, 0xFFFF0000u }, { { 1, 1 }, 0xFF00FF00u }, { { 2, 1 }, 0xFFFF0000u }, { { 0, 0 }, 0xFFFF00FFu }, { { 1, 0 }, 0xFF0000FFu }, { { 2, 0 }, 0xFF00FF00u }, { { 3, 0 }, 0xFFFF0000u } });   /* RLE4 */
    one(185, 2, 2, { { { 0, 0 }, 0xFF302010u }, { { 1, 0 }, 0xFF302011u }, { { 0, 1 }, 0xFF302020u } });   /* 32bpp top-down: alpha dropped */
    one(186, 4, 2, { { { 0, 0 }, 0xFFFF00FFu }, { { 1, 0 }, 0xFFFF0000u }, { { 2, 0 }, 0xFF00FF00u }, { { 3, 0 }, 0xFF0000FFu }, { { 0, 1 }, 0xFF00FF00u } });   /* 4bpp */
    /* IMAGE_ICON: a one-image list from the icon resource */
    il = ImageList_LoadImageA(MOD, "MAINICON", 32, 1, 0xFFFFFFFFu, 1, 0);
    CHECK(il != NULL); CHECK_EQ(ImageList_GetImageCount(il), 1); ImageList_GetIconSize(il, &cx, &cy); CHECK_EQ(cx, 32); CHECK_EQ(cy, 32);
    CHECK(fnvnx_imagelist_pixels(il, 0, &p0) && px_eq(p0, 0, 0, 32, 0xFF000055u)); ImageList_Destroy(il);
    /* LR_LOADFROMFILE */
    bytes f = dibhdr(8, 2, 1, 0, { BLACK, WHITE }); rows(f, 8, 2, 1, [](int x, int y) { return (unsigned)((x + y) & 1); });
    bytes bmp; bmp.push_back('B'); bmp.push_back('M'); p32(bmp, (uint32_t)(14 + f.size())); p32(bmp, 0); p32(bmp, 14 + 40 + 8);
    bmp.insert(bmp.end(), f.begin(), f.end());
    HANDLE hf = CreateFileA("C:\\FalloutNV\\strip.bmp", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD put = 0; CHECK(hf != INVALID_HANDLE_VALUE && WriteFile(hf, bmp.data(), (DWORD)bmp.size(), &put, NULL)); CloseHandle(hf);
    il = ImageList_LoadImageA(NULL, "C:\\FalloutNV\\strip.bmp", 4, 1, 0xFFFFFFFFu, 0, 0x10);
    CHECK(il != NULL); CHECK_EQ(ImageList_GetImageCount(il), 2); ImageList_GetIconSize(il, &cx, &cy); CHECK_EQ(cx, 4); CHECK_EQ(cy, 2);
    CHECK(fnvnx_imagelist_pixels(il, 1, &p1) && px_eq(p1, 0, 0, 4, 0xFF000000u) && px_eq(p1, 1, 0, 4, 0xFFFFFFFFu) && px_eq(p1, 0, 1, 4, 0xFFFFFFFFu));
    ImageList_Destroy(il);
    CHECK(ImageList_LoadImageA(NULL, "C:\\FalloutNV\\nofile.bmp", 4, 1, 0xFFFFFFFFu, 0, 0x10) == NULL);
    fnvnx_resource_set_image_for_test(MOD, NULL, 0);
}
