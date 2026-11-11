/* test_k32_contract_misc.cpp - small kernel32 imports of task W20: lstrcmpiA. */
#include "s4_test.h"

#include "../../platform/win32/w32.h"

extern "C" int lstrcmpiA(LPCSTR, LPCSTR);
extern "C" int CompareStringA(LCID, DWORD, LPCSTR, int, LPCSTR, int);

TEST(k32c_lstrcmpi)
{
    /* the locale comparison (en-US, NORM_IGNORECASE) minus 2 */
    CHECK_EQ(lstrcmpiA("a", "A"), 0);
    CHECK_EQ(lstrcmpiA("a", "B"), -1);
    CHECK_EQ(lstrcmpiA("Z", "a"), 1);
    CHECK_EQ(lstrcmpiA("abc", "ABD"), -1);
    CHECK_EQ(lstrcmpiA("abc", "AB"), 1);
    CHECK_EQ(lstrcmpiA("", ""), 0);
    CHECK_EQ(lstrcmpiA("", "a"), -1);
    CHECK_EQ(lstrcmpiA("\xE4", "\xC4"), 0);           /* a-umlaut equals its capital */
    CHECK_EQ(lstrcmpiA("\xE9", "e"), 1);              /* accents differ from the plain letter */
    CHECK_EQ(lstrcmpiA("\xE9", "f"), -1);             /* but sort before the next letter (not by code point) */
    CHECK_EQ(lstrcmpiA("a1", "a2"), -1);
    CHECK_EQ(lstrcmpiA("_a", "a"), -1);               /* punctuation sorts before letters */
    /* NULL sorts before any string, two NULLs are equal */
    CHECK_EQ(lstrcmpiA(NULL, NULL), 0);
    CHECK_EQ(lstrcmpiA(NULL, "a"), -1);
    CHECK_EQ(lstrcmpiA("a", NULL), 1);
    CHECK_EQ(lstrcmpiA(NULL, ""), -1);
    /* the word-sort rule: the same answer as CompareStringA with NORM_IGNORECASE */
    static const char *w[] = { "co-op", "coop", "co op", "cop", "can't", "cant", "cans", "Strasse", "Stra\xDF" "e" };
    for (size_t i = 0; i < sizeof w / sizeof *w; i++)
        for (size_t j = 0; j < sizeof w / sizeof *w; j++)
            CHECK_EQ(lstrcmpiA(w[i], w[j]), CompareStringA(0x0400, 1, w[i], -1, w[j], -1) - 2);
}
