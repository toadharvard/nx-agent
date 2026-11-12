/* test_plat.cpp - the platform lane's recorder (platform/win32/platstat.cpp, tools/perf/PLAT.md): failed opens
 * through CreateFileA, the CRT's fopen and _open (path class, error, the case-insensitive scan, no path printed),
 * unimplemented and no-op stand-ins (SteamUser, DebugBreak), decode and input notes, distinct counting by
 * caller, the flush lines and the summary. Recording is stopped at the end. */
#include "s4_test.h"

#include <stdlib.h>
#include <string>

#include "fnvnx_msvcrt.h"

static std::string g_lines;
static void sink(const char *l) { g_lines += l; g_lines += '\n'; }
static bool has(const char *s) { return g_lines.find(s) != std::string::npos; }

extern "C" int fnvnx_open(const char *path, int oflag, int pmode);   /* platform/crt/iob.c (lowio _open) */
/* an unimplemented stand-in, as platform code marks one (FNVNX_UNIMPL: the plat lane's D25 #3 count) */
extern "C" __attribute__((noinline)) void *plat_test_unimpl(void)
{
    fnvnx_plat_note(FNVNX_NOTE_UNIMPL, __func__, FNVNX_NOTE_RA, 0);
    return NULL;
}

TEST(plat_lane_opens_stubs_counts)
{
    unsigned long long d0[FNVNX_NOTE_NCLASS], c0[FNVNX_NOTE_NCLASS], d[FNVNX_NOTE_NCLASS], c[FNVNX_NOTE_NCLASS];
    fnvnx_plat_start(sink);
    fnvnx_plat_counts(d0, c0);
    char cmd[1200];
    snprintf(cmd, sizeof cmd, "mkdir -p '%s/PlatData'", s4_root());
    CHECK(system(cmd) == 0);
    /* failed opens: a BSA in an existing directory, an ESM in a missing one, a save, an ini through the CRT */
    CHECK(CreateFileA("PlatData\\Missing - Meshes.bsa", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL) ==
          INVALID_HANDLE_VALUE);
    CHECK(CreateFileA("NoSuchDir\\FalloutNV.esm", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL) == INVALID_HANDLE_VALUE);
    CHECK(fnvnx_fopen("PlatData\\Saves\\Save 1.fos", "rb") == NULL);
    CHECK(fnvnx_open("PlatData\\Fallout.ini", 0 /* _O_RDONLY */, 0) < 0);
    /* not a failed open: CREATE_NEW on an existing file */
    HANDLE h = CreateFileA("PlatData\\exists.txt", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    CloseHandle(h);
    CHECK(CreateFileA("PlatData\\exists.txt", GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL) == INVALID_HANDLE_VALUE);
    fnvnx_plat_counts(d, c);
    CHECK_EQ(d[FNVNX_NOTE_OPEN] - d0[FNVNX_NOTE_OPEN], 4ull);
    CHECK(has("plat: open CreateFileA caller 0x"));
    CHECK(has(" bsa err 2 ci no path "));      /* ERROR_FILE_NOT_FOUND, the directory exists */
    CHECK(has(" esm err 3 ci nodir path "));   /* ERROR_PATH_NOT_FOUND */
    CHECK(has("plat: open fopen caller 0x"));
    CHECK(has(" save err "));
    CHECK(has("plat: open _open caller 0x"));
    CHECK(has(" ini err "));
    CHECK(!has("Missing - Meshes"));            /* no path is printed */
    /* stand-ins: an unimplemented entry (plat_test_unimpl)  */
    for (int i = 0; i < 3; i++) CHECK(plat_test_unimpl() == NULL);   /* one call site: one distinct entry, 3 calls */
    fnvnx_plat_note(FNVNX_NOTE_DECODE, "decode_item", 0x1234, "decode -132");
    fnvnx_plat_note(FNVNX_NOTE_INPUT, "fnvnx_hid_usage_to_dik", 0, "hid usage 112");
    fnvnx_plat_note(FNVNX_NOTE_INPUT, "fnvnx_hid_usage_to_dik", 0, "hid usage 112");
    fnvnx_plat_counts(d, c);
    CHECK_EQ(d[FNVNX_NOTE_UNIMPL] - d0[FNVNX_NOTE_UNIMPL], 1ull);
    CHECK_EQ(c[FNVNX_NOTE_UNIMPL] - c0[FNVNX_NOTE_UNIMPL], 3ull);
    CHECK_EQ(d[FNVNX_NOTE_DECODE] - d0[FNVNX_NOTE_DECODE], 1ull);
    CHECK_EQ(d[FNVNX_NOTE_INPUT] - d0[FNVNX_NOTE_INPUT], 1ull);
    CHECK_EQ(c[FNVNX_NOTE_INPUT] - c0[FNVNX_NOTE_INPUT], 2ull);
    CHECK(has("plat: unimpl plat_test_unimpl caller 0x"));
    fnvnx_plat_flush();
    CHECK(has("plat: count unimpl plat_test_unimpl caller 0x"));
    CHECK(has(" n 3\n"));
    CHECK(has("plat: count input fnvnx_hid_usage_to_dik caller 0x00000000 n 2 hid usage 112"));
    CHECK(has("plat: summary unimpl "));
    fnvnx_plat_stop();
    size_t before = g_lines.size();
    CHECK(plat_test_unimpl() == NULL);   /* off: nothing recorded */
    fnvnx_plat_counts(d, c);
    CHECK_EQ(c[FNVNX_NOTE_UNIMPL] - c0[FNVNX_NOTE_UNIMPL], 3ull);
    CHECK_EQ(g_lines.size(), before);
}
