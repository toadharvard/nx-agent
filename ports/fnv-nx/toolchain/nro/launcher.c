/* fnv.nro - the fnv-nx launcher (stream NX1, docs/NRO.md).
 *
 * An NRO runs inside nx-hbloader's process, with hbloader's 39-bit address space and heap. The game needs its own
 * NPDM (32BitNoReserved: the data window at 0x00400000..0x02100000, D1/D8), and stock hbloader cannot run in a 32-bit
 * space as an application (its heap request exceeds the 2 GiB heap region and it aborts with 2347-0009). So this NRO
 * does not run the game itself. Started by title takeover (hold R while starting any installed game, hbmenu, fnv-nx):
 *   1. it refuses applet mode (album) with a message: the game needs the application memory pool;
 *   2. it checks the SD layout (fnvnx/sdlayout.h) and the memory of the pool;
 *   3. it copies sdmc:/switch/fnv/fnv-exefs.nsp to sdmc:/atmosphere/contents/<this title>/exefs.nsp (never over an
 *      override it did not install) and writes the marker sdmc:/switch/fnv/override.txt;
 *   4. once R is released, appletRestartProgram: Atmosphere's loader starts the title again from our exefs, with our
 *      NPDM. The host removes the override and the marker as soon as it runs (toolchain/host/host.c), so nothing
 *      stays installed. Y removes a leftover override (after a power loss) without starting.
 * Everything it prints also goes to sdmc:/switch/fnv/launcher.log.
 */
#include <dirent.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include <switch.h>

#include "fnvnx/sdlayout.h"

#ifndef FNVNX_VERSION
#define FNVNX_VERSION "dev"
#endif

static FILE *g_log;
static PadState g_pad;

static void say(const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    fputs(buf, stdout);
    if (g_log) { fputs(buf, g_log); fflush(g_log); }
    consoleUpdate(NULL);
}

/* Waits for one of `want` (HidNpadButton_*); returns the button pressed, 0 when the applet asks us to exit. */
static u64 wait_buttons(u64 want) {
    while (appletMainLoop()) {
        padUpdate(&g_pad);
        u64 d = padGetButtonsDown(&g_pad) & want;
        if (d) return d;
        consoleUpdate(NULL);
        svcSleepThread(16000000);
    }
    return 0;
}

static void wait_released(void) {
    while (appletMainLoop()) {
        padUpdate(&g_pad);
        if (!padGetButtons(&g_pad)) return;
        svcSleepThread(16000000);
    }
}

static int is_file(const char *p, off_t *size) {
    struct stat st;
    if (stat(p, &st) != 0 || !S_ISREG(st.st_mode)) return 0;
    if (size) *size = st.st_size;
    return 1;
}

/* Does dir hold an entry named `name`, in any case? (The SD card folds case; this works either way.) */
static int ci_has(const char *dir, const char *name) {
    DIR *d = opendir(dir);
    if (!d) return 0;
    struct dirent *e;
    int found = 0;
    while (!found && (e = readdir(d)))
        found = !strcasecmp(e->d_name, name);
    closedir(d);
    return found;
}

static int copy_file(const char *from, const char *to) {
    char tmp[300];
    snprintf(tmp, sizeof tmp, "%s.tmp", to);
    FILE *in = fopen(from, "rb"), *out = in ? fopen(tmp, "wb") : NULL;
    int ok = in && out;
    static char buf[64 << 10];
    size_t n;
    while (ok && (n = fread(buf, 1, sizeof buf, in)) > 0)
        ok = fwrite(buf, 1, n, out) == n;
    if (in) {
        ok = ok && !ferror(in);
        fclose(in);
    }
    if (out && fclose(out) != 0) ok = 0;
    if (ok) {
        unlink(to);
        ok = rename(tmp, to) == 0;
    }
    if (!ok) unlink(tmp);
    return ok;
}

static int same_file(const char *a, const char *b) {
    FILE *fa = fopen(a, "rb"), *fb = fopen(b, "rb");
    int same = fa && fb;
    static char ba[16 << 10], bb[16 << 10];
    while (same) {
        size_t na = fread(ba, 1, sizeof ba, fa), nb = fread(bb, 1, sizeof bb, fb);
        same = na == nb && !memcmp(ba, bb, na);
        if (na == 0 || na < sizeof ba) break;
    }
    if (fa) fclose(fa);
    if (fb) fclose(fb);
    return same;
}

/* The program id in the marker, 0 when there is none. */
static u64 read_marker(void) {
    FILE *f = fopen(FNVNX_SD_MARKER, "r");
    unsigned long long id = 0;
    if (f) {
        if (fscanf(f, "%16llx", &id) != 1) id = 0;
        fclose(f);
    }
    return id;
}

static void override_path(char *out, size_t n, u64 id) { snprintf(out, n, FNVNX_ATMOSPHERE_OVERRIDE_FMT, id); }

static void mkdirs_for(const char *file) {
    char p[300];
    snprintf(p, sizeof p, "%s", file);
    for (char *s = strchr(p + strlen("sdmc:/"), '/'); s; s = strchr(s + 1, '/')) {
        *s = 0;
        mkdir(p, 0777);
        *s = '/';
    }
}

/* Removes an override this launcher installed (marker present), for id or, with id 0, the marker's. */
static void remove_leftover(u64 id) {
    u64 m = read_marker();
    if (!m || (id && m != id)) {
        say("No leftover override (marker %s %s).\n", FNVNX_SD_MARKER, m ? "names another title" : "absent");
        return;
    }
    char ov[128];
    override_path(ov, sizeof ov, m);
    int had = is_file(ov, NULL);
    if (had && !same_file(ov, FNVNX_SD_EXEFS)) {
        say("Not removing %s: it is not fnv-nx's exefs (changed since). Remove it by hand.\n", ov);
        return;
    }
    if (had) unlink(ov);
    unlink(FNVNX_SD_MARKER);
    say("Removed the leftover override of %016lx%s.\n", m, had ? "" : " (it was already gone)");
}

static int fail_wait(void) {
    say("\nPress + to return to hbmenu.\n");
    wait_buttons(HidNpadButton_Plus);
    return 1;
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    consoleInit(NULL);
    padConfigureInput(8, HidNpadStyleSet_NpadStandard);
    padInitializeAny(&g_pad);
    mkdir("sdmc:/switch", 0777);
    mkdir(FNVNX_SD_DIR, 0777);
    g_log = fopen(FNVNX_SD_LAUNCH_LOG, "w");
    say("fnv-nx launcher %s\n\n", FNVNX_VERSION);

    AppletType at = appletGetAppletType();
    if (at != AppletType_Application && at != AppletType_SystemApplication) {
        say("fnv-nx cannot start in applet mode (started from the album, applet type %d).\n"
            "The game needs the full application memory, which only title takeover gives:\n"
            "  hold R while you start any installed game, keep holding until hbmenu appears,\n"
            "  then start fnv-nx from there.\n", (int)at);
        int r = fail_wait();
        if (g_log) fclose(g_log);
        consoleExit(NULL);
        return r;
    }
    if (hosversionBefore(5, 0, 0)) {
        say("fnv-nx needs system version 5.0.0 or later (appletRestartProgram).\n");
        goto out_fail;
    }

    u64 pid = 0, mem = 0;
    svcGetInfo(&pid, InfoType_ProgramId, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&mem, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    say("title %016lx, application memory %lu MiB\n", pid, mem >> 20);
    if (mem < FNVNX_MIN_APP_MEMORY) {
        say("Not enough application memory: %lu MiB, fnv-nx needs %llu MiB.\n", mem >> 20, FNVNX_MIN_APP_MEMORY >> 20);
        goto out_fail;
    }

    /* the SD layout */
    off_t sz = 0;
    int bad = 0;
    struct { const char *path, *what; int required; } files[] = {
        { FNVNX_SD_EXEFS, "game process (exefs)", 1 },
        { FNVNX_SD_GUEST, "game module", 1 },
        { FNVNX_SD_EXE, "your FalloutNV.exe", 1 },
    };
    for (size_t i = 0; i < sizeof files / sizeof files[0]; i++) {
        int ok = is_file(files[i].path, &sz);
        say("  %-36s %s", files[i].path, ok ? "ok" : "MISSING");
        if (ok) say(" (%lld KiB)", (long long)(sz >> 10));
        say("  %s\n", files[i].what);
        bad += !ok && files[i].required;
    }
    static const char *data[] = { "FalloutNV.esm", "Fallout - Meshes.bsa", "Fallout - Textures.bsa",
                                  "Fallout - Sound.bsa", "Fallout - Misc.bsa" };
    for (size_t i = 0; i < sizeof data / sizeof data[0]; i++) {
        int ok = ci_has(FNVNX_SD_DATA, data[i]);
        say("  %s/%-24s %s\n", FNVNX_SD_DATA, data[i], ok ? "ok" : "MISSING");
        bad += !ok;
    }
    struct stat st;
    if (stat("sdmc:/atmosphere", &st) != 0) {
        say("  sdmc:/atmosphere missing: fnv-nx needs Atmosphere (its loader applies the exefs override).\n");
        bad++;
    }
    if (bad) {
        say("\n%d item(s) missing. Copy them as docs/NRO_CHECKLIST.md says, then start fnv-nx again.\n", bad);
        goto out_fail;
    }

    /* the override */
    char ov[128];
    override_path(ov, sizeof ov, pid);
    u64 marker = read_marker();
    if (is_file(ov, NULL) && !(marker == pid || same_file(ov, FNVNX_SD_EXEFS))) {
        say("\n%s already exists and is not fnv-nx's (a mod of this game?).\n"
            "fnv-nx leaves it alone. Start fnv-nx through another game (title takeover).\n", ov);
        goto out_fail;
    }
    if (marker && marker != pid) remove_leftover(marker);   /* a leftover on another title */

    say("\nA: start Fallout: New Vegas    Y: remove a leftover override and exit    +: exit\n");
    u64 b = wait_buttons(HidNpadButton_A | HidNpadButton_Y | HidNpadButton_Plus);
    if (b & HidNpadButton_Y) {
        remove_leftover(0);
        if (g_log) fclose(g_log);
        consoleExit(NULL);
        return 0;
    }
    if (!(b & HidNpadButton_A)) {
        if (g_log) fclose(g_log);
        consoleExit(NULL);
        return 0;
    }

    mkdirs_for(ov);
    if (!copy_file(FNVNX_SD_EXEFS, ov)) {
        say("Could not write %s (errno %d). Is the SD card write-protected or full?\n", ov, errno);
        goto out_fail;
    }
    FILE *m = fopen(FNVNX_SD_MARKER, "w");
    if (!m || fprintf(m, "%016lx\n", pid) < 0 || fclose(m) != 0) {
        unlink(ov);
        say("Could not write %s; nothing was installed.\n", FNVNX_SD_MARKER);
        goto out_fail;
    }
    say("installed %s\nrelease all buttons (holding R would start hbmenu again)...\n", ov);
    wait_released();
    say("restarting %016lx with the fnv-nx game process\n", pid);
    if (g_log) { fclose(g_log); g_log = NULL; }
    Result rc = appletRestartProgram(NULL, 0);
    /* on success AM ends this process; we only get here on failure */
    g_log = fopen(FNVNX_SD_LAUNCH_LOG, "a");
    say("appletRestartProgram failed: 0x%x. Removing the override again.\n", rc);
    unlink(ov);
    unlink(FNVNX_SD_MARKER);
out_fail:
    fail_wait();
    if (g_log) fclose(g_log);
    consoleExit(NULL);
    return 1;
}
