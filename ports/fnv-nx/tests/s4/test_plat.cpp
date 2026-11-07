/* test_plat.cpp - the platform lane's recorder (platform/win32/platstat.cpp, tools/perf/PLAT.md): failed opens
 * through CreateFileA, the CRT's fopen and _open (path class, error, the case-insensitive scan, no path printed),
 * unimplemented and no-op stand-ins (BinkOpen, SteamUser, DebugBreak), decode and input notes, distinct counting by
 * caller, the flush lines and the summary. Recording is stopped at the end. */
#include "s4_test.h"

#include <stdlib.h>
#include <string>

#include "fnvnx_msvcrt.h"

static std::string g_lines;
static void sink(const char *l) { g_lines += l; g_lines += '\n'; }
static bool has(const char *s) { return g_lines.find(s) != std::string::npos; }

extern "C" void *SteamUser(void);
extern "C" int fnvnx_open(const char *path, int oflag, int pmode);   /* platform/crt/iob.c (lowio _open) */
typedef void *(*bink_open_fn)(const char *, unsigned);

TEST(plat_lane_opens_stubs_counts)
{
    unsigned long long d0[5], c0[5], d[5], c[5];
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
    CHECK_EQ(d[FNVNX_PLAT_OPEN] - d0[FNVNX_PLAT_OPEN], 4ull);
    CHECK(has("plat: open CreateFileA caller 0x"));
    CHECK(has(" bsa err 2 ci no path "));      /* ERROR_FILE_NOT_FOUND, the directory exists */
    CHECK(has(" esm err 3 ci nodir path "));   /* ERROR_PATH_NOT_FOUND */
    CHECK(has("plat: open fopen caller 0x"));
    CHECK(has(" save err "));
    CHECK(has("plat: open _open caller 0x"));
    CHECK(has(" ini err "));
    CHECK(!has("Missing - Meshes"));            /* no path is printed */
    /* stand-ins: BinkOpen (unimpl), SteamUser and DebugBreak (no-op) */
    bink_open_fn bo = (bink_open_fn)fnvnx_win32_lookup_export("binkw32.dll", "_BinkOpen@8");
    CHECK(bo != NULL);
    for (int i = 0; i < 3; i++) CHECK(bo("movie.bik", 0) == NULL);   /* one call site: one distinct entry, 3 calls */
    CHECK(SteamUser() == NULL);
    DebugBreak();
    fnvnx_plat_note(FNVNX_PLAT_DECODE, "ov_open_callbacks", 0x1234, "ov -132");
    fnvnx_plat_note(FNVNX_PLAT_INPUT, "fnvnx_hid_usage_to_dik", 0, "hid usage 112");
    fnvnx_plat_note(FNVNX_PLAT_INPUT, "fnvnx_hid_usage_to_dik", 0, "hid usage 112");
    fnvnx_plat_counts(d, c);
    CHECK_EQ(d[FNVNX_PLAT_UNIMPL] - d0[FNVNX_PLAT_UNIMPL], 1ull);
    CHECK_EQ(c[FNVNX_PLAT_UNIMPL] - c0[FNVNX_PLAT_UNIMPL], 3ull);
    CHECK_EQ(d[FNVNX_PLAT_NOOP] - d0[FNVNX_PLAT_NOOP], 2ull);
    CHECK_EQ(d[FNVNX_PLAT_DECODE] - d0[FNVNX_PLAT_DECODE], 1ull);
    CHECK_EQ(d[FNVNX_PLAT_INPUT] - d0[FNVNX_PLAT_INPUT], 1ull);
    CHECK_EQ(c[FNVNX_PLAT_INPUT] - c0[FNVNX_PLAT_INPUT], 2ull);
    CHECK(has("plat: unimpl BinkOpen caller 0x"));
    CHECK(has("plat: noop SteamUser caller 0x"));
    CHECK(has("plat: noop DebugBreak caller 0x"));
    fnvnx_plat_flush();
    CHECK(has("plat: count unimpl BinkOpen caller 0x"));
    CHECK(has(" n 3\n"));
    CHECK(has("plat: count input fnvnx_hid_usage_to_dik caller 0x00000000 n 2 hid usage 112"));
    CHECK(has("plat: summary unimpl "));
    fnvnx_plat_stop();
    size_t before = g_lines.size();
    CHECK(bo("movie.bik", 0) == NULL);   /* off: nothing recorded */
    fnvnx_plat_counts(d, c);
    CHECK_EQ(c[FNVNX_PLAT_UNIMPL] - c0[FNVNX_PLAT_UNIMPL], 3ull);
    CHECK_EQ(g_lines.size(), before);
}
