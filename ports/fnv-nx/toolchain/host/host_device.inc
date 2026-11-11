/* host_device.inc - device integration of the host (stream NX1, docs/NRO.md; included by host.c):
 *   - the launcher's override: removed as soon as the game process runs, so nothing stays installed;
 *   - start refusals: applet mode, too little application memory;
 *   - the applet message pump: the game never calls appletMainLoop (its Win32 message loop is guest-side), so HOME,
 *     sleep/resume, docked/handheld switches and "Close software" would never be answered. A host thread runs
 *     appletMainLoop for the whole run and acts on the messages;
 *   - the crash handler: any thread's CPU exception (host or guest) writes sdmc:/switch/fnv/crash.log through raw
 *     fsFs calls (no newlib, no heap), removes the override and ends the process.
 */
#include "fnvnx/sdlayout.h"

/* ---- the launcher's override ---------------------------------------------------------------------------- */
static u64 g_program_id;

/* Removes sdmc:/atmosphere/contents/<us>/exefs.nsp and the marker when the launcher installed them for this title.
 * Called after S10's reservation (its relaunch path, appletRestartProgram, needs the override still in place). */
static void remove_launcher_override(void) {
    FILE *f = fopen(FNVNX_SD_MARKER, "r");
    unsigned long long id = 0;
    if (!f) {
        logf_("fnvnx host: no launcher marker (%s): started without fnv.nro, nothing to remove\n", FNVNX_SD_MARKER);
        return;
    }
    if (fscanf(f, "%16llx", &id) != 1) id = 0;
    fclose(f);
    if (id != g_program_id) {
        logf_("fnvnx host: launcher marker names %016llx, this is %016lx: left alone\n", id, g_program_id);
        return;
    }
    char ov[128];
    snprintf(ov, sizeof ov, FNVNX_ATMOSPHERE_OVERRIDE_FMT, g_program_id);
    int r1 = remove(ov), r2 = remove(FNVNX_SD_MARKER);
    logf_("fnvnx host: removed the launcher's override %s (%s), marker (%s)\n", ov, r1 ? "FAILED" : "ok",
          r2 ? "FAILED" : "ok");
}

/* ---- the precompiled shader pack ------------------------------------------------------------------------- */
/* fnv.nro's RomFS holds shaders.nxsp, the native renderer's DKSH (toolchain/nro/shaderpack.py); the guest's shader
 * table reads it as fnvshd:/shaders.nxsp through the host file calls. Nothing is compiled on the console: without
 * the pack the renderer has no shaders (NX1 check 9). Returns 0 when mounted. */
static int mount_shader_pack(void) {
    FILE *f = fopen(FNVNX_SD_NRO, "rb");
    unsigned char h[0x80], a[0x38];
    u32 size = 0;
    u64 off = 0, len = 0;
    int ok = f && fread(h, 1, sizeof h, f) == sizeof h && !memcmp(h + 0x10, "NRO0", 4);
    if (ok) {
        memcpy(&size, h + 0x18, 4);
        ok = !fseek(f, size, SEEK_SET) && fread(a, 1, sizeof a, f) == sizeof a && !memcmp(a, "ASET", 4);
    }
    if (ok) {
        memcpy(&off, a + 8 + 32, 8);   /* AssetSection romfs {offset, size} after icon and nacp */
        memcpy(&len, a + 8 + 40, 8);
        ok = len != 0;
    }
    if (f) fclose(f);
    Result rc = ok ? romfsMountFromFsdev(FNVNX_SD_NRO, (u64)size + off, "fnvshd") : MAKERESULT(Module_Libnx, LibnxError_NotFound);
    logf_("fnvnx host: shader pack: %s %s\n", FNVNX_SD_NRO, R_SUCCEEDED(rc) ? "RomFS mounted as fnvshd:" :
          "has no RomFS: the renderer gets no shaders (nothing is compiled on the console)");
    /* the game's own shaders (translated.nxsp, shaders_nx.py on the owner's side): an NXSP v1 next to the game, read
     * by the translated path; absent or broken, the game's programs get no DKSH and nothing is compiled either */
    unsigned char g[32];
    u32 gn = 0;
    f = fopen(FNVNX_SD_TRANSLATED, "rb");
    int gok = f && fread(g, 1, sizeof g, f) == sizeof g && !memcmp(g, "NXSP", 4) && g[4] == 1;
    if (gok) memcpy(&gn, g + 8, 4);
    if (f) fclose(f);
    if (gok)
        logf_("fnvnx host: game shaders: %s, %u precompiled programs, pack id %02x%02x%02x%02x...\n",
              FNVNX_SD_TRANSLATED, gn, g[20], g[21], g[22], g[23]);
    else
        logf_("fnvnx host: game shaders: %s %s: the game's own shaders are unavailable (build it with "
              "toolchain/nro/shaders_nx.py from Data/Shaders; nothing is compiled on the console)\n",
              FNVNX_SD_TRANSLATED, f ? "is not an NXSP v1 pack" : "missing");
    return R_SUCCEEDED(rc) ? 0 : 1;
}

/* ---- start refusals ------------------------------------------------------------------------------------- */
/* 0 when the process may run the game; otherwise the reason was logged. */
static int device_preflight(void) {
    svcGetInfo(&g_program_id, InfoType_ProgramId, CUR_PROCESS_HANDLE, 0);
    AppletType at = appletGetAppletType();
    u64 total = 0;
    svcGetInfo(&total, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    logf_("fnvnx host: program %016lx, applet type %d, application memory %lu MiB\n", g_program_id, (int)at,
          total >> 20);
    if (at != AppletType_Application && at != AppletType_SystemApplication) {
        logf_("fnvnx host: STOP: not running as an application (applet type %d). Start fnv-nx through title takeover:"
              " hold R while starting a game, then pick fnv-nx in hbmenu.\n", (int)at);
        return 1;
    }
    if (total < FNVNX_MIN_APP_MEMORY) {
        logf_("fnvnx host: STOP: %lu MiB of application memory, the game needs %llu MiB.\n", total >> 20,
              FNVNX_MIN_APP_MEMORY >> 20);
        return 1;
    }
    return 0;
}

/* ---- applet message pump -------------------------------------------------------------------------------- */
static volatile int g_pump_run, g_pump_alive = 1, g_exiting, g_exit_locked;
static volatile int g_operation_mode = -1, g_focus = -1;   /* AppletOperationMode, AppletFocusState */
static Thread g_pump_thread;
static AppletHookCookie g_hook;

static void cleanup_for_exit(const char *why);
static int g_sock_up;               /* host_sock_init() succeeded (host_sock.inc) */
static void host_sock_exit(void);

static void applet_hook(AppletHookType hook, void *param) {
    (void)param;
    switch (hook) {
    case AppletHookType_OnFocusState:
        g_focus = appletGetFocusState();
        logf_("fnvnx host: focus %s\n", g_focus == AppletFocusState_InFocus ? "in" :
              g_focus == AppletFocusState_OutOfFocus ? "out (HOME / overlay)" : "background");
        break;
    case AppletHookType_OnOperationMode:
        g_operation_mode = appletGetOperationMode();
        logf_("fnvnx host: operation mode %s (display target %s)\n", g_operation_mode == AppletOperationMode_Console ?
              "docked" : "handheld", g_operation_mode == AppletOperationMode_Console ? "1920x1080" : "1280x720");
        break;
    case AppletHookType_OnPerformanceMode:
        logf_("fnvnx host: performance mode %d\n", (int)appletGetPerformanceMode());
        break;
    case AppletHookType_OnResume:
        logf_("fnvnx host: resumed from sleep\n");
        break;
    case AppletHookType_OnExitRequest:
        logf_("fnvnx host: exit requested by the system\n");
        break;
    default:
        break;
    }
}

static void pump_main(void *arg) {
    (void)arg;
    while (g_pump_run) {
        if (!appletMainLoop()) {   /* "Close software" from HOME, or a system shutdown */
            g_pump_alive = 0;
            cleanup_for_exit("the system asked the application to exit");
            if (g_log) {
                fclose(g_log);
                g_log = NULL;
            }
            /* exit(), not svcExitProcess: libnx's exit path ends with ISelfController::Exit for an NSO
             * application (applet.c _appletExitProcess), the orderly exit AM expects after an ExitRequest */
            exit(0);
        }
        svcSleepThread(16000000);   /* ~60 Hz; appletMainLoop returns at once when no message is pending */
    }
}

static void pump_start(void) {
    /* Without appletLockExit, AM force-terminates the process on "Close software" and no ExitRequest ever reaches the
     * pump: cleanup_for_exit (boost, override, logs) would not run (V1 NX1-EXIT-P1). With it AM sends the request and
     * waits up to 15 s; cleanup_for_exit unlocks before the process ends (libnx applet.h). */
    g_exit_locked = R_SUCCEEDED(appletLockExit());
    g_operation_mode = appletGetOperationMode();
    g_focus = appletGetFocusState();
    appletHook(&g_hook, applet_hook, NULL);
    g_pump_run = 1;
    /* core 0 next to the main thread, priority 0x2C like it: it wakes rarely and only does IPC */
    Result rc = threadCreate(&g_pump_thread, pump_main, NULL, NULL, 0x4000, 0x2C, 0);
    if (R_SUCCEEDED(rc)) rc = threadStart(&g_pump_thread);
    logf_("fnvnx host: applet pump %s; operation mode %s; exit %s\n", R_SUCCEEDED(rc) ? "started" : "FAILED",
          g_operation_mode == AppletOperationMode_Console ? "docked" : "handheld",
          g_exit_locked ? "locked (cleanup on Close software)" : "NOT locked");
    if (R_FAILED(rc)) {
        g_pump_run = 0;
        if (g_exit_locked) {   /* nobody would answer the exit request: let AM terminate us instead of waiting 15 s */
            g_exit_locked = 0;
            appletUnlockExit();
        }
    }
}

static void pump_stop(void) {
    if (!g_pump_run) return;
    g_pump_run = 0;
    threadWaitForExit(&g_pump_thread);
    threadClose(&g_pump_thread);
    appletUnhook(&g_hook);
}

/* ---- crash handler -------------------------------------------------------------------------------------- */
alignas(16) u8 __nx_exception_stack[0x4000];
u64 __nx_exception_stack_size = sizeof(__nx_exception_stack);
static FsFileSystem *g_sd_fs;   /* sdmc's file system, taken at start: the handler makes raw fsFs calls */
static u64 g_crash_guest_base, g_crash_guest_size;
static const char *g_crash_note;   /* why the process ends when the crash is a decision (host_fault.inc), else NULL */
static char g_crash_buf[4096];
static size_t g_crash_len;

static void cput(const char *s) {
    while (*s && g_crash_len < sizeof g_crash_buf - 1) g_crash_buf[g_crash_len++] = *s++;
}
static void chex(u64 v, int digits) {
    char t[17];
    for (int i = digits - 1; i >= 0; i--, v >>= 4) t[i] = "0123456789abcdef"[v & 15];
    t[digits] = 0;
    cput(t);
}
static void creg(const char *name, u64 v) {
    cput(name);
    cput(" ");
    chex(v, 16);
    if (g_crash_guest_size && v - g_crash_guest_base < g_crash_guest_size) {
        cput(" (guest+");
        chex(v - g_crash_guest_base, 8);
        cput(")");
    }
    cput("\n");
}

void __libnx_exception_handler(ThreadExceptionDump *ctx) {
    g_crash_len = 0;
    cput(g_exiting ? "fnv-nx crash during exit\n" : "fnv-nx crash\n");
    if (g_crash_note) { cput("note "); cput(g_crash_note); cput("\n"); }
    cput("program ");
    chex(g_program_id, 16);
    cput("\nerror_desc ");
    chex(ctx->error_desc, 8);
    switch (ctx->error_desc) {   /* libnx ThreadExceptionDesc; data aborts arrive as Other, the esr tells them */
    case ThreadExceptionDesc_InstructionAbort: cput(" instruction abort\n"); break;
    case ThreadExceptionDesc_MisalignedPC: cput(" misaligned pc\n"); break;
    case ThreadExceptionDesc_MisalignedSP: cput(" misaligned sp\n"); break;
    case ThreadExceptionDesc_SError: cput(" SError\n"); break;
    case ThreadExceptionDesc_BadSVC: cput(" svc not allowed by the NPDM\n"); break;
    case ThreadExceptionDesc_Trap: cput(" trap (undefined instruction or system register)\n"); break;
    default: cput(" other (data abort, breakpoint...: see esr)\n"); break;
    }
    cput("esr ");
    chex(ctx->esr, 8);
    cput("\n");
    creg("pc ", ctx->pc.x);
    creg("lr ", ctx->lr.x);
    creg("sp ", ctx->sp.x);
    creg("far", ctx->far.x);
    cput("guest image ");
    chex(g_crash_guest_base, 16);
    cput(" size ");
    chex(g_crash_guest_size, 8);
    cput(" (symbolize guest+offsets with fnvnx.elf: addr2line -e fnvnx.elf <link base + offset>)\n");
    for (int i = 0; i < 29; i++) {
        char n[4] = { 'x', (char)('0' + i / 10), (char)('0' + i % 10), 0 };
        creg(n, ctx->cpu_gprs[i].x);
    }
    creg("fp ", ctx->fp.x);
    u64 tid = 0;
    svcGetThreadId(&tid, CUR_THREAD_HANDLE);
    creg("thread", tid);
    /* the stack words above sp (a raw backtrace for the owner's report) */
    MemoryInfo mi;
    u32 pi;
    if (R_SUCCEEDED(svcQueryMemory(&mi, &pi, ctx->sp.x)) && (mi.perm & Perm_R)) {
        cput("stack:\n");
        for (u64 p = ctx->sp.x & ~7ull, n = 0; n < 32 && p + 8 <= mi.addr + mi.size; p += 8, n++)
            creg("  ", *(volatile u64 *)p);
    }
    if (g_sd_fs) {
        static const char path[] = FNVNX_FS_CRASH_LOG;
        FsFile f;
        fsFsDeleteFile(g_sd_fs, path);
        if (R_SUCCEEDED(fsFsCreateFile(g_sd_fs, path, g_crash_len, 0)) &&
            R_SUCCEEDED(fsFsOpenFile(g_sd_fs, path, FsOpenMode_Write, &f))) {
            fsFileWrite(&f, 0, g_crash_buf, g_crash_len, FsWriteOption_Flush);
            fsFileClose(&f);
        }
        char ov[64] = "/atmosphere/contents/0000000000000000/exefs.nsp";   /* = FNVNX_FS_OVERRIDE_FMT, no printf */
        for (int i = 0; i < 16; i++) ov[21 + i] = "0123456789abcdef"[g_program_id >> (60 - 4 * i) & 15];
        fsFsDeleteFile(g_sd_fs, ov);
        fsFsDeleteFile(g_sd_fs, FNVNX_FS_MARKER);
        fsFsCommit(g_sd_fs);
    }
    svcOutputDebugString(g_crash_buf, g_crash_len);
    svcExitProcess();
}

static void crash_handler_arm(u64 guest_base, u64 guest_size) {
    g_sd_fs = fsdevGetDeviceFileSystem("sdmc");
    g_crash_guest_base = guest_base;
    g_crash_guest_size = guest_size;
}

/* Everything that must happen before the process ends, whichever way: no boost left on, the logs flushed. */
static void cleanup_for_exit(const char *why) {
    if (g_exiting++) return;
    logf_("fnvnx host: exiting: %s\n", why);
    appletSetCpuBoostMode(ApmCpuBoostMode_Normal);   /* never leave the console boosted */
    if (g_sock_up) {
        g_sock_up = 0;
        host_sock_exit();
    }
    remove_launcher_override();   /* normally gone already; after a relaunch-only run it is still there */
    if (g_log) fflush(g_log);
    if (g_exit_locked) {          /* libnx: appletUnlockExit before the process ends */
        g_exit_locked = 0;
        appletUnlockExit();
    }
}
