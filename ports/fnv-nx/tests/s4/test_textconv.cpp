/* test_textconv.cpp - the pure conversions of the TEXT_ENTRY host service (toolchain/host/host_textconv.h, the real file): the cut,
 * UTF-16 to UTF-8, and UTF-8 to UTF-16 by the UTF-8 definition (hostapi.h 36). test:text_entry */
#include "s4_test.h"
#include "../../toolchain/host/host_textconv.h"
#include <string.h>

namespace {
size_t to16(const char *s, uint16_t *o) { return te_utf8_to_16((const unsigned char *)s, strlen(s), o); }
}

TEST(text_entry_conv)
{
    uint16_t u[64], p[4] = {0x41, 0xD83D, 0xDE00, 0x42};
    unsigned char b[64];
    CHECK_EQ(te_cut16(p, 4, 4), 4u);
    CHECK_EQ(te_cut16(p, 4, 2), 1u);            /* cut through the pair drops the pair */
    CHECK_EQ(te_cut16(p, 4, 3), 3u);
    uint16_t lone[3] = {0x41, 0xD83D, 0x42};
    CHECK_EQ(te_cut16(lone, 3, 2), 2u);         /* a lone high surrogate at the cut is kept (shown as U+FFFD) */
    CHECK_EQ(te_utf16_to_8(p, 4, b), 6u);
    CHECK_EQ(b[1], 0xF0); CHECK_EQ(b[2], 0x9F); CHECK_EQ(b[3], 0x98); CHECK_EQ(b[4], 0x80);
    CHECK_EQ(te_utf16_to_8(lone, 3, b), 5u);
    CHECK_EQ(b[1], 0xEF); CHECK_EQ(b[2], 0xBF); CHECK_EQ(b[3], 0xBD);
    CHECK_EQ(to16("a\xF0\x9F\x98\x80z", u), 4u);   /* a character above U+FFFF is two units */
    CHECK_EQ(u[1], 0xD83D); CHECK_EQ(u[2], 0xDE00);
    CHECK_EQ(to16("\xC3\xA9", u), 1u); CHECK_EQ(u[0], 0xE9);
    CHECK_EQ(to16("\x80", u), 1u); CHECK_EQ(u[0], 0xFFFD);          /* stray continuation */
    CHECK_EQ(to16("\xC0\xAF", u), 2u);                              /* overlong: one U+FFFD per byte */
    CHECK_EQ(to16("\xE0\x80\x80", u), 3u);
    CHECK_EQ(to16("\xED\xA0\x80", u), 3u);                          /* surrogate encoded in UTF-8 */
    CHECK_EQ(to16("\xF4\x90\x80\x80", u), 4u);                      /* above U+10FFFF */
    CHECK_EQ(to16("\xE2\x82", u), 2u);                              /* truncated */
    CHECK_EQ(to16("\xE2\x82\xAC", u), 1u); CHECK_EQ(u[0], 0x20AC);
}
