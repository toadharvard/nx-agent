/* test_inflate.cpp - platform/win32/inflate.cpp: zlib/DEFLATE vectors (stored, fixed, dynamic from Python's zlib at test
 * writing time; hand-assembled streams through a bit writer) and every error class of the validation rule. */
#include "s4_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <string>
#include <vector>

#include "../../platform/win32/inflate.h"

typedef std::vector<uint8_t> bytes;
static const std::string DYN_TXT = "the lazy the lazy the dog quick dog quick dog brown while brown while brown seven fox seven fox seven jumps wizards jumps wizards jumps quietly over quietly over quietly a box a box a the lazy the lazy the dog quick dog quick dog brown while brown while brown seven fox seven fox seven jumps wizards jumps wizards jumps quietly over quietly over quietly a box a box a the lazy the lazy the dog quick dog quick dog brown while brown while brown seven fox seven fox seven";
static const std::vector<uint8_t> DYN_Z = {0x78,0xda,0xed,0x8e,0xc1,0x11,0x80,0x20,0x10,0x03,0x5b,0xb9,0xd6,0x40,0x4e,0x41,0x91,0x53,0x38,0x40,0xa8,0x5e,0x3e,0xce,0x38,0x8a,0x1d,0xf8,0xc9,0x6c,0x3e,0xc9,0xb2,0x46,0xb0,0xa2,0x16,0xe0,0x3b,0x28,0x9a,0x60,0x8f,0x66,0x58,0x1e,0x24,0x3d,0x65,0x07,0x59,0x1b,0x8b,0x1d,0x0e,0x98,0xd0,0xc1,0x48,0xc7,0x8b,0xe6,0xb8,0x6e,0x01,0xb2,0xa9,0xc2,0xab,0xd0,0x6d,0xed,0x03,0xd9,0x16,0xa0,0x84,0xbe,0x5f,0x04,0xc8,0xb6,0x77,0x25,0xff,0xde,0xdf,0xde,0x27,0x4e,0x7d,0xad,0x69};
static const std::string FIX_TXT = "abcabcabcabcabcabcXabcabcabcabcabcabcXabcabcabcabcabcabcX";
static const std::vector<uint8_t> FIX_Z = {0x78,0x01,0x4b,0x4c,0x4a,0x4e,0x44,0x45,0x11,0x89,0x44,0x09,0x01,0x00,0x76,0xa5,0x15,0xb5};
static const std::vector<uint8_t> STORED_Z = {0x78,0x01,0x01,0x0c,0x00,0xf3,0xff,0x68,0x65,0x6c,0x6c,0x6f,0x20,0x73,0x74,0x6f,0x72,0x65,0x64,0x1e,0xd5,0x04,0xc6};
static const std::vector<uint8_t> EMPTY_Z = {0x78,0xda,0x03,0x00,0x00,0x00,0x00,0x01};

static uint32_t adler(const bytes &d) { uint32_t a = 1, b = 0; for (uint8_t c : d) { a = (a + c) % 65521; b = (b + a) % 65521; } return b << 16 | a; }
struct BW {   /* DEFLATE bit writer: bits() LSB-first fields, code() Huffman codes MSB-first */
    bytes b; int nb = 0;
    void bit(unsigned v) { if (!nb) b.push_back(0); b.back() |= (v & 1) << nb; nb = (nb + 1) & 7; }
    void bits(unsigned v, int n) { for (int i = 0; i < n; i++) bit(v >> i); }
    void code(unsigned v, int n) { for (int i = n - 1; i >= 0; i--) bit(v >> i); }
    void align() { nb = 0; }
};
static bytes zwrap(const bytes &deflate, const bytes &plain, uint8_t cmf = 0x78, uint8_t flg = 0x01)
{
    bytes z = { cmf, flg }; z.insert(z.end(), deflate.begin(), deflate.end());
    uint32_t a = adler(plain); for (int i = 3; i >= 0; i--) z.push_back(a >> (8 * i));
    return z;
}
static int run(const bytes &z, bytes *out = NULL, size_t max = 0)
{
    /* exact-size heap copy: an over-read is a sanitizer / heap error, not a silent success */
    uint8_t *c = (uint8_t *)malloc(z.size() ? z.size() : 1); memcpy(c, z.data(), z.size());
    uint8_t *o = NULL; size_t on = 0;
    int r = max ? fnvnx_inflate_zlib_max(c, z.size(), &o, &on, max) : fnvnx_inflate_zlib(c, z.size(), &o, &on);
    if (r == 0 && out) out->assign(o, o + on);
    CHECK((r == 0) == (o != NULL));
    free(o); free(c);
    return r;
}
static bytes S(const std::string &s) { return bytes(s.begin(), s.end()); }
static bytes lcg(size_t n) { bytes v(n); uint32_t x = 12345; for (auto &c : v) { x = x * 1103515245u + 12345u; c = x >> 16; } return v; }
/* fixed-Huffman literal / match writers (RFC 1951 3.2.6) */
static void flit(BW &w, unsigned s) { if (s < 144) w.code(0x30 + s, 8); else if (s < 256) w.code(0x190 + s - 144, 9); else if (s < 280) w.code(s - 256, 7); else w.code(0xC0 + s - 280, 8); }

TEST(inflate_valid_streams)
{
    bytes o;
    CHECK_EQ(run(STORED_Z, &o), 0); CHECK(o == S("hello stored"));
    CHECK_EQ(run(FIX_Z, &o), 0); CHECK(o == S(FIX_TXT));
    CHECK_EQ(run(DYN_Z, &o), 0); CHECK(o == S(DYN_TXT));
    CHECK_EQ(run(EMPTY_Z, &o), 0); CHECK(o.empty());
    /* two stored blocks (non-final, final), the second one empty */
    { BW w; w.bits(0, 1); w.bits(0, 2); w.align(); bytes d = w.b; d.insert(d.end(), { 3, 0, 0xFC, 0xFF, 'a', 'b', 'c' });
      BW v; v.bits(1, 1); v.bits(0, 2); v.align(); d.insert(d.end(), v.b.begin(), v.b.end()); d.insert(d.end(), { 0, 0, 0xFF, 0xFF });
      CHECK_EQ(run(zwrap(d, S("abc")), &o), 0); CHECK(o == S("abc")); }
    /* long distance: 20000 stored bytes, then a fixed block with a 258-byte match at distance 20000 (code 28 + 13 extra bits) */
    { bytes plain = lcg(20000); BW w; w.bits(0, 1); w.bits(0, 2); w.align();
      bytes d = w.b; d.insert(d.end(), { 0x20, 0x4E, 0xDF, 0xB1 }); d.insert(d.end(), plain.begin(), plain.end());
      BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 285); f.code(28, 5); f.bits(20000 - 16385, 13); flit(f, 256);
      d.insert(d.end(), f.b.begin(), f.b.end());
      bytes want = plain; for (int i = 0; i < 258; i++) want.push_back(want[want.size() - 20000]);
      CHECK_EQ(run(zwrap(d, want), &o), 0); CHECK(o == want); }
    /* overlapping match: 'a' then length 10 distance 1 */
    { BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 'a'); flit(f, 264); f.code(0, 5); flit(f, 256);
      CHECK_EQ(run(zwrap(f.b, S("aaaaaaaaaaa")), &o), 0); CHECK(o == S("aaaaaaaaaaa")); }
}

TEST(inflate_errors)
{
    bytes d = DYN_Z, o;
    for (size_t k = 0; k < d.size(); k++) CHECK(run(bytes(d.begin(), d.begin() + k)) != 0);   /* every truncation, no over-read */
    for (size_t k = 0; k < STORED_Z.size(); k++) CHECK(run(bytes(STORED_Z.begin(), STORED_Z.begin() + k)) != 0);
    CHECK_EQ(run(bytes(d.begin(), d.end() - 1)), FNVNX_INF_TRUNCATED);
    { bytes b = d; b.push_back(0); CHECK_EQ(run(b), FNVNX_INF_TRAILING); }
    { bytes b = d; b.back() ^= 1; CHECK_EQ(run(b), FNVNX_INF_ADLER); }
    { bytes b = d; b[0] = 0x79; CHECK_EQ(run(b), FNVNX_INF_HEADER); }                   /* CM 9 */
    { bytes b = d; b[0] = 0x88; b[1] = 0x1C; CHECK_EQ(run(b), FNVNX_INF_HEADER); }     /* CINFO 8: window > 32 KiB (0x881C % 31 == 0) */
    { bytes b = d; b[1] ^= 1; CHECK_EQ(run(b), FNVNX_INF_HEADER); }                    /* FCHECK */
    { bytes b = d; b[1] = 0x20; CHECK_EQ(run(b), FNVNX_INF_HEADER); }                  /* FDICT set, FCHECK valid (0x7820 % 31 == 0) */
    CHECK_EQ(run(S("\x78")), FNVNX_INF_HEADER); CHECK_EQ(run(bytes()), FNVNX_INF_HEADER);
    { BW w; w.bits(1, 1); w.bits(0, 2); w.align(); bytes b = w.b; b.insert(b.end(), { 3, 0, 0xFC, 0xFE, 1, 2, 3 }); CHECK_EQ(run(zwrap(b, S("abc"))), FNVNX_INF_BAD_CODE); }   /* NLEN */
    { BW w; w.bits(1, 1); w.bits(0, 2); w.align(); bytes b = w.b; b.insert(b.end(), { 9, 0, 0xF6, 0xFF, 1, 2, 3 }); CHECK_EQ(run(zwrap(b, S("abc"))), FNVNX_INF_TRUNCATED); }   /* LEN beyond input */
    { BW w; w.bits(1, 1); w.bits(3, 2); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }   /* block type 3 */
    { BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 'a'); flit(f, 257); f.code(1, 5); flit(f, 256);   /* length 3 distance 2 > 1 byte produced */
      CHECK_EQ(run(zwrap(f.b, S("aaaa"))), FNVNX_INF_DISTANCE); }
    { BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 257); f.code(0, 5); flit(f, 256); CHECK_EQ(run(zwrap(f.b, S("aaa"))), FNVNX_INF_DISTANCE); }   /* nothing produced yet */
    { BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 'a'); flit(f, 257); f.code(30, 5); flit(f, 256); CHECK_EQ(run(zwrap(f.b, S("aaaa"))), FNVNX_INF_BAD_CODE); }   /* distance code 30 */
    { BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 286); CHECK_EQ(run(zwrap(f.b, bytes())), FNVNX_INF_BAD_CODE); }   /* length symbol 286 */
    { bytes b = lcg(3000); BW w; w.bits(0, 1); w.bits(0, 2); w.align();   /* match beyond the header's 2 KiB window (CINFO 3) */
      bytes z = w.b; z.insert(z.end(), { 0xB8, 0x0B, 0x47, 0xF4 }); z.insert(z.end(), b.begin(), b.end());
      BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 257); f.code(22, 5); f.bits(0, 10); flit(f, 256); z.insert(z.end(), f.b.begin(), f.b.end());   /* distance 2049 */
      bytes want = b; for (int i = 0; i < 3; i++) want.push_back(want[want.size() - 2049]);
      CHECK_EQ(run(zwrap(z, want, 0x38, 0x11)), FNVNX_INF_DISTANCE); CHECK_EQ(run(zwrap(z, want)), 0); }   /* 0x3811 % 31 == 0 */
    /* dynamic headers: code-length code lengths in the order 16,17,18,0 */
    auto dynhdr = [](BW &w, unsigned hlit, unsigned hdist, std::initializer_list<unsigned> cl) { w.bits(1, 1); w.bits(2, 2); w.bits(hlit, 5); w.bits(hdist, 5); w.bits((unsigned)cl.size() - 4, 4); for (unsigned l : cl) w.bits(l, 3); };
    { BW w; dynhdr(w, 0, 0, { 1, 1, 1, 1 }); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }   /* over-subscribed */
    { BW w; dynhdr(w, 0, 0, { 1, 0, 0, 0 }); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }   /* incomplete code-length code */
    { BW w; dynhdr(w, 0, 0, { 0, 0, 0, 0 }); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }   /* no codes at all */
    { BW w; dynhdr(w, 31, 0, { 1, 0, 0, 1 }); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }  /* HLIT 288 */
    { BW w; dynhdr(w, 0, 31, { 1, 0, 0, 1 }); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }  /* HDIST 32 */
    { BW w; dynhdr(w, 0, 0, { 1, 0, 0, 1 }); w.code(1, 1); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }   /* repeat-previous first */
    { BW w; dynhdr(w, 0, 0, { 1, 0, 0, 1 }); for (int i = 0; i < 258; i++) w.code(0, 1); w.code(0, 7); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }   /* no end-of-block code */
    { BW w; dynhdr(w, 0, 0, { 0, 0, 1, 1 }); w.code(1, 1); w.bits(127, 7); w.code(1, 1); w.bits(127, 7); CHECK_EQ(run(zwrap(w.b, bytes())), FNVNX_INF_BAD_CODE); }   /* repeat overflows */
    /* output bound */
    CHECK_EQ(run(DYN_Z, &o, 468), FNVNX_INF_TOO_BIG); CHECK_EQ(run(DYN_Z, &o, 469), 0); CHECK_EQ(run(STORED_Z, &o, 11), FNVNX_INF_TOO_BIG);
    { BW f; f.bits(1, 1); f.bits(1, 2); flit(f, 'a'); for (int i = 0; i < 40; i++) { flit(f, 285); f.code(0, 5); } flit(f, 256);   /* 1 + 40 * 258 bytes */
      CHECK_EQ(run(zwrap(f.b, bytes(1 + 40 * 258, 'a')), &o), 0); CHECK_EQ(o.size(), 1 + 40 * 258);
      CHECK_EQ(run(zwrap(f.b, bytes(1 + 40 * 258, 'a')), &o, 1 + 40 * 258 - 1), FNVNX_INF_TOO_BIG); }
}
