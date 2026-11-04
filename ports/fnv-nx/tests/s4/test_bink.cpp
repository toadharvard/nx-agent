/* test_bink.cpp - binkw32.dll stub (platform/win32/bink.cpp): movies are skipped, every call is NULL-safe. */
#include "s4_test.h"

extern "C" int _BinkPause_8(void *, int);

TEST(bink_stub_skips_movies)
{
    typedef void *(*open_fn)(const char *, unsigned);
    typedef int (*h_fn)(void *);
    typedef void (*v_fn)(void *);
    typedef int (*pause_fn)(void *, int);
    typedef int (*rect_fn)(void *, void *, int, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned,
                           unsigned);
    typedef void *(*ds_fn)(unsigned long);
    typedef int (*snd_fn)(void *, unsigned long);
    /* the IAT's decorated names */
    open_fn open = (open_fn)fnvnx_win32_lookup_export("binkw32.dll", "_BinkOpen@8");
    CHECK(open != NULL);
    SetLastError(0);
    CHECK(open && open("Data\\Video\\FNVIntro.bik", 0) == NULL);
    CHECK(GetLastError() == ERROR_FILE_NOT_FOUND);
    const char *one[] = {"_BinkClose@4", "_BinkDoFrame@4", "_BinkNextFrame@4", "_BinkWait@4"};
    for (const char *n : one)
        CHECK(fnvnx_win32_lookup_export("BINKW32.DLL", n) != NULL);
    ((v_fn)fnvnx_win32_lookup_export("binkw32", "_BinkClose@4"))(NULL);
    ((v_fn)fnvnx_win32_lookup_export("binkw32", "_BinkNextFrame@4"))(NULL);
    CHECK_EQ(((h_fn)fnvnx_win32_lookup_export("binkw32", "_BinkDoFrame@4"))(NULL), 0);
    CHECK_EQ(((h_fn)fnvnx_win32_lookup_export("binkw32", "_BinkWait@4"))(NULL), 0);
    CHECK_EQ(((pause_fn)fnvnx_win32_lookup_export("binkw32", "_BinkPause@8"))(NULL, 1), 0);
    rect_fn rect = (rect_fn)fnvnx_win32_lookup_export("binkw32", "_BinkCopyToBufferRect@44");
    CHECK(rect && rect(NULL, NULL, 0, 0, 0, 0, 0, 0, 0, 0, 0) == 0);
    ds_fn ds = (ds_fn)fnvnx_win32_lookup_export("binkw32", "_BinkOpenDirectSound@4");
    snd_fn snd = (snd_fn)fnvnx_win32_lookup_export("binkw32", "_BinkSetSoundSystem@8");
    CHECK(ds && snd && snd(ds(0), 0) == 1);
    /* plain names (GetProcAddress, the units' own references) and the decomp's _BinkPause_8 */
    CHECK(fnvnx_win32_lookup_export("binkw32.dll", "BinkOpen") == (void *)open);
    CHECK(fnvnx_win32_lookup_export("binkw32.dll", "_BinkOpen@4") == NULL);
    CHECK(_BinkPause_8(NULL, 0) == 0);
}
