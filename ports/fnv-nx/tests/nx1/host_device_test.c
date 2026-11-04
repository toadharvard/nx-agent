/* NX1: toolchain/host/host_device.inc executed, not only compiled. Built as a static AArch64 Linux program against
 * the real libnx headers with the libnx calls it makes stubbed here, run under qemu-aarch64 (tests/nx1/check_platform.py):
 *   - the launcher override: removed when the marker names this title, left alone otherwise;
 *   - the start refusals: applet mode, too little memory;
 *   - the crash handler: the log it writes through fsFs (registers, guest offsets), the override and the marker it
 *     deletes, and that it ends the process;
 *   - the applet pump: an exit request runs the cleanup and exits 0 (in a child process).
 * Paths: "sdmc:/..." is a relative directory named "sdmc:" in the test's working directory. */
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <switch.h>

static FILE *g_log;
static char g_logbuf[16384];
static void logf_(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    size_t l = strlen(g_logbuf);
    vsnprintf(g_logbuf + l, sizeof g_logbuf - l, fmt, ap);
    va_end(ap);
}

/* ---- libnx stubs ---- */
static u64 s_pid = 0x0100abcd12345678ull, s_mem = 3264ull << 20;
static AppletType s_type = AppletType_Application;
static int s_loops, s_exit_after = -1, s_boost_normal;
static jmp_buf s_exit_jmp;
static char s_written[8192];
static size_t s_written_len;
static char s_deleted[8][96];
static int s_ndeleted;
static FsFileSystem s_fs;

Result svcGetInfo(u64 *out, u32 id, Handle h, u64 sub) {
    (void)h; (void)sub;
    *out = id == InfoType_ProgramId ? s_pid : id == InfoType_TotalMemorySize ? s_mem : 0;
    return 0;
}
AppletType appletGetAppletType(void) { return s_type; }
AppletOperationMode appletGetOperationMode(void) { return AppletOperationMode_Handheld; }
AppletFocusState appletGetFocusState(void) { return AppletFocusState_InFocus; }
ApmPerformanceMode appletGetPerformanceMode(void) { return ApmPerformanceMode_Normal; }
void appletHook(AppletHookCookie *c, AppletHookFn fn, void *p) { (void)c; (void)fn; (void)p; }
void appletUnhook(AppletHookCookie *c) { (void)c; }
bool appletMainLoop(void) { return ++s_loops != s_exit_after; }
Result appletSetCpuBoostMode(ApmCpuBoostMode m) { s_boost_normal += m == ApmCpuBoostMode_Normal; return 0; }
Result threadCreate(Thread *t, ThreadFunc f, void *a, void *st, size_t sz, int prio, int cpu) {
    (void)t; (void)f; (void)a; (void)st; (void)sz; (void)prio; (void)cpu;
    return 1;   /* the pump runs synchronously in the test instead */
}
Result threadStart(Thread *t) { (void)t; return 0; }
Result threadWaitForExit(Thread *t) { (void)t; return 0; }
Result threadClose(Thread *t) { (void)t; return 0; }
void svcSleepThread(s64 ns) { (void)ns; }
void NX_NORETURN svcExitProcess(void) { longjmp(s_exit_jmp, 1); }
Result svcGetThreadId(u64 *id, Handle h) { (void)h; *id = 7; return 0; }
Result svcQueryMemory(MemoryInfo *mi, u32 *pi, u64 addr) {
    memset(mi, 0, sizeof *mi);
    mi->addr = addr & ~0xFFFull;
    mi->size = 0x1000;
    mi->perm = Perm_Rw;
    *pi = 0;
    return 0;
}
Result svcOutputDebugString(const char *s, u64 n) { (void)s; (void)n; return 0; }
FsFileSystem *fsdevGetDeviceFileSystem(const char *name) { return !strcmp(name, "sdmc") ? &s_fs : NULL; }
Result fsFsDeleteFile(FsFileSystem *fs, const char *p) {
    (void)fs;
    if (s_ndeleted < 8) snprintf(s_deleted[s_ndeleted++], sizeof s_deleted[0], "%s", p);
    return 0;
}
Result fsFsCreateFile(FsFileSystem *fs, const char *p, s64 size, u32 opt) { (void)fs; (void)p; (void)size; (void)opt; return 0; }
Result fsFsOpenFile(FsFileSystem *fs, const char *p, u32 mode, FsFile *f) {
    (void)fs; (void)mode; (void)f;
    return strcmp(p, "/switch/fnv/crash.log") ? 1 : 0;
}
Result fsFileWrite(FsFile *f, s64 off, const void *buf, u64 n, u32 opt) {
    (void)f; (void)opt;
    if (off + n < sizeof s_written) memcpy(s_written + off, buf, n), s_written_len = off + n;
    return 0;
}
void fsFileClose(FsFile *f) { (void)f; }
Result fsFsCommit(FsFileSystem *fs) { (void)fs; return 0; }

#include "../../toolchain/host/host_device.inc"

/* ---- the test ---- */
static int failures;
#define CHECK(c, ...) do { if (!(c)) { failures++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } \
                           else printf("ok   " #c "\n"); } while (0)

static int exists(const char *p) { struct stat st; return stat(p, &st) == 0; }
static void put(const char *p, const char *s) { FILE *f = fopen(p, "w"); fputs(s, f); fclose(f); }
static void setup_override(const char *marker) {
    mkdir("sdmc:", 0777); mkdir("sdmc:/switch", 0777); mkdir("sdmc:/switch/fnv", 0777);
    mkdir("sdmc:/atmosphere", 0777); mkdir("sdmc:/atmosphere/contents", 0777);
    mkdir("sdmc:/atmosphere/contents/0100abcd12345678", 0777);
    put("sdmc:/atmosphere/contents/0100abcd12345678/exefs.nsp", "PFS0");
    if (marker) put("sdmc:/switch/fnv/override.txt", marker);
    else unlink("sdmc:/switch/fnv/override.txt");
}

int main(int argc, char **argv) {
    if (argc > 1 && chdir(argv[1]) != 0) return 2;
    /* override removal */
    svcGetInfo(&g_program_id, InfoType_ProgramId, 0, 0);
    setup_override("0100abcd12345678\n");
    remove_launcher_override();
    CHECK(!exists("sdmc:/atmosphere/contents/0100abcd12345678/exefs.nsp") && !exists("sdmc:/switch/fnv/override.txt"),
          "marker for this title: override and marker removed");
    setup_override("0100000000001234\n");
    remove_launcher_override();
    CHECK(exists("sdmc:/atmosphere/contents/0100abcd12345678/exefs.nsp") && exists("sdmc:/switch/fnv/override.txt"),
          "marker for another title: both left alone");
    setup_override(NULL);
    remove_launcher_override();
    CHECK(exists("sdmc:/atmosphere/contents/0100abcd12345678/exefs.nsp"), "no marker: an override of someone else stays");

    /* refusals */
    s_type = AppletType_LibraryApplet;
    CHECK(device_preflight() == 1 && strstr(g_logbuf, "title takeover"), "applet mode refused with the takeover hint");
    s_type = AppletType_Application;
    s_mem = 1500ull << 20;
    CHECK(device_preflight() == 1 && strstr(g_logbuf, "the game needs"), "too little memory refused");
    s_mem = 3264ull << 20;
    CHECK(device_preflight() == 0, "an application with the full pool passes");

    /* crash handler */
    setup_override("0100abcd12345678\n");
    crash_handler_arm(0x02200000, 0x01b92000);
    ThreadExceptionDump d;
    memset(&d, 0, sizeof d);
    d.error_desc = ThreadExceptionDesc_InstructionAbort;
    d.pc.x = 0x02200000 + 0x1234;
    d.lr.x = 0x7100000000ull;
    d.sp.x = (u64)(uintptr_t)&d;
    d.far.x = 0xdeadbeef;
    d.cpu_gprs[3].x = 0x02200000 + 0x10;
    if (!setjmp(s_exit_jmp)) {
        __libnx_exception_handler(&d);
        CHECK(0, "the crash handler returned");
    }
    s_written[s_written_len] = 0;
    CHECK(strstr(s_written, "fnv-nx crash\n") != NULL, "crash log header");
    CHECK(strstr(s_written, "instruction abort") != NULL, "error class named");
    CHECK(strstr(s_written, "pc  0000000002201234 (guest+00001234)") != NULL, "pc with its guest offset: %.80s", s_written);
    CHECK(strstr(s_written, "x03 0000000002200010 (guest+00000010)") != NULL, "registers with guest offsets");
    CHECK(strstr(s_written, "far 00000000deadbeef") != NULL, "fault address");
    CHECK(strstr(s_written, "program 0100abcd12345678") != NULL, "program id");
    CHECK(strstr(s_written, "stack:") != NULL, "stack words");
    int ov = 0, mk = 0;
    for (int i = 0; i < s_ndeleted; i++) {
        ov |= !strcmp(s_deleted[i], "/atmosphere/contents/0100abcd12345678/exefs.nsp");
        mk |= !strcmp(s_deleted[i], "/switch/fnv/override.txt");
    }
    CHECK(ov && mk, "the crash handler deletes the override and the marker (%d deletes)", s_ndeleted);

    /* applet pump: the third message is an exit request */
    fflush(stdout);
    pid_t pid = fork();
    if (pid == 0) {
        s_exit_after = 3;
        g_pump_run = 1;
        g_exiting = 0;
        setup_override("0100abcd12345678\n");
        pump_main(NULL);
        _exit(3);   /* not reached: pump_main exits */
    }
    int st = 0;
    waitpid(pid, &st, 0);
    CHECK(WIFEXITED(st) && WEXITSTATUS(st) == 0, "an exit request ends the process with exit(0) (status %#x)", st);
    CHECK(!exists("sdmc:/atmosphere/contents/0100abcd12345678/exefs.nsp"), "the exit cleanup removed the override");

    printf("host_device: %d failures\n", failures);
    return failures != 0;
}
