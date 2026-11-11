/* host_platform.inc - ext_call module FNVNX_EXT_PLATFORM (hostapi.h 1..15): ONE table, one row per service number: the
 * least number of arguments and the handler. platform_ext is total: every (fn, nargs) has exactly one answer.
 *   fn 16..33 (sockets): host_sock_call answers, BEFORE the two checks below: nargs != the service's exact count -> -FNVNX_WE_INVAL
 *                        (even for a null array); the exact count with a null array -> FNVNX_EXT_BAD_NARGS; else the service's answer
 *   fn without a row (0, 36 and up) -> FNVNX_EXT_NO_SERVICE
 *   nargs below the row's number, or nargs > 0 with a null argument array     -> FNVNX_EXT_BAD_NARGS
 *   otherwise the handler's answer, which hostapi.h states per service; more arguments than the row needs are ignored.
 * A service declared in hostapi.h without a row is a test failure (tests/s4/backends/hostapi_total.py).
 * Standalone like the other .inc files: needs the handlers' files included first (host_vm.inc, host_section.inc,
 * host_message.inc, host_ids.inc, host_thread_activity.inc, host_fault.inc), logf_, g_cfg {core3, boost_loading} and libnx's applet. */
static int g_boost;   /* the CPU boost mode set last */
static Result set_cpu_boost(int mode) {
    if (mode == g_boost) return 0;
    Result rc = appletSetCpuBoostMode(mode ? ApmCpuBoostMode_FastLoad : ApmCpuBoostMode_Normal);
    logf_("fnvnx host: cpu boost %s%s\n", mode ? "FastLoad" : "Normal", R_FAILED(rc) ? " (FAILED)" : "");
    if (R_SUCCEEDED(rc)) g_boost = mode;
    return rc;
}

static u64 pf_cpu_boost(const u64 *a) { return g_cfg.boost_loading ? set_cpu_boost(a[0] ? 1 : 0) : 1; }
/* THREAD_ACTIVITY (id, paused): id 0 names the calling thread (refused: BadInput, host_thread_activity.inc), another id a thread of
 * thread_create or a THREAD_SELF record (an id that names none: NotFound; a thread not started yet or already finished: BadInput); paused != 0 pauses it, 0 resumes it; else the kernel's result */
static u64 pf_thread_activity(const u64 *a) {
    HostThread *h = a[0] ? host_id_get(HOST_ID_THREAD, a[0]) : NULL;
    if (a[0] && !h) return FNVNX_HE_NOTFOUND;
    if (h) {   /* a thread that is not running (not started yet, or its entry returned) has nothing to pause or resume */
        u32 st = __atomic_load_n(&h->state, __ATOMIC_ACQUIRE);
        if (!(st & HT_STARTED) || (st & HT_FINISHED)) return FNVNX_HE_BADINPUT;
    }
    return host_thread_activity(a[0] ? h->t.handle : 0, a[0] == 0, a[1]);
}
/* ACCESS_FAULT_HANDLER (handler, stack_top): host_fault.inc:host_fault_register */
static u64 pf_access_fault_handler(const u64 *a) { return host_fault_register(a[0], a[1]); }
static u64 pf_message(const u64 *a) { return host_show_message((const char *)a[0], (const char *)a[1]); }
static u64 pf_vm_reserve(const u64 *a) { return host_vm_reserve(a[0], a[1]); }
static u64 pf_vm_commit(const u64 *a) { return host_vm_commit(a[0], a[1]); }
static u64 pf_vm_decommit(const u64 *a) { return host_vm_decommit(a[0], a[1]); }
static u64 pf_vm_release(const u64 *a) { return host_vm_release(a[0]); }
static u64 pf_vm_protect(const u64 *a) { return host_vm_protect(a[0], a[1], a[2]); }
static u64 pf_display_docked(const u64 *a) { (void)a; return appletGetOperationMode() == AppletOperationMode_Console ? 1 : 0; }
static u64 pf_section_create(const u64 *a) { return host_section_create(a[0]); }
static u64 pf_section_map(const u64 *a) { return host_section_map(a[0], a[1], a[2], a[3]); }
static u64 pf_section_unmap(const u64 *a) { return host_section_unmap(a[0], a[1]); }
static u64 pf_section_free(const u64 *a) { return host_section_free(a[0]); }
static u64 pf_unmap_fixed(const u64 *a) { return h_unmap_fixed(a[0], a[1], a[2]); }
/* TZ_AT (utc_seconds, FnvnxTzAt *): host_v2.inc:host_tz_at (the platform lead's service; hostapi.h states its results) */
static u64 pf_tz_at(const u64 *a) { return host_tz_at(a[0], (FnvnxTzAt *)a[1]); }
/* THREAD_SELF (): a record for the CALLING thread (the main thread: libnx's threadGetSelf carries its real handle) under a thread id;
 * the platform lead's code, the id being a host_ids.inc table handle. The record is STARTED, so thread_start refuses it; only
 * THREAD_ACTIVITY (and set_priority / set_cpu) use its id. 0 when memory or the id table is exhausted. */
static u64 pf_thread_self(const u64 *a) {
    (void)a;
    HostThread *h = calloc(1, sizeof *h);
    if (!h) return 0;
    h->t.handle = threadGetSelf()->handle;
    h->state = HT_STARTED | HT_SELF;
    u64 id = host_id_new(HOST_ID_THREAD, h);
    if (!id) free(h);
    return id;
}

typedef struct { u32 nargs; u64 (*fn)(const u64 *a); } PlatService;
static const PlatService g_plat[] = {
    [FNVNX_PLAT_CPU_BOOST] = {1, pf_cpu_boost},
    [FNVNX_PLAT_THREAD_ACTIVITY] = {2, pf_thread_activity},
    [FNVNX_PLAT_MESSAGE] = {2, pf_message},
    [FNVNX_PLAT_VM_RESERVE] = {2, pf_vm_reserve},
    [FNVNX_PLAT_VM_COMMIT] = {2, pf_vm_commit},
    [FNVNX_PLAT_VM_DECOMMIT] = {2, pf_vm_decommit},
    [FNVNX_PLAT_VM_RELEASE] = {1, pf_vm_release},
    [FNVNX_PLAT_VM_PROTECT] = {3, pf_vm_protect},
    [FNVNX_PLAT_DISPLAY_DOCKED] = {0, pf_display_docked},
    [FNVNX_PLAT_SECTION_CREATE] = {1, pf_section_create},
    [FNVNX_PLAT_SECTION_MAP] = {4, pf_section_map},
    [FNVNX_PLAT_SECTION_UNMAP] = {2, pf_section_unmap},
    [FNVNX_PLAT_SECTION_FREE] = {1, pf_section_free},
    [FNVNX_PLAT_UNMAP_FIXED] = {3, pf_unmap_fixed},
    [FNVNX_PLAT_TZ_AT] = {2, pf_tz_at},
    [FNVNX_PLAT_THREAD_SELF] = {0, pf_thread_self},
    [FNVNX_PLAT_ACCESS_FAULT_HANDLER] = {2, pf_access_fault_handler},
};
static uint64_t platform_ext(uint32_t fn, const uint64_t *args, uint32_t nargs) {
    /* 16..33: the sockets (host_sock.inc, the platform lead's): it checks nargs itself (exactly the count: else -FNVNX_WE_INVAL) */
    if (fn >= FNVNX_PLAT_SOCK_FIRST && fn <= FNVNX_PLAT_SOCK_LAST) return host_sock_call(fn, args, nargs);
    if (fn >= sizeof g_plat / sizeof g_plat[0] || !g_plat[fn].fn) return FNVNX_EXT_NO_SERVICE;
    if (nargs < g_plat[fn].nargs || (nargs && !args)) return FNVNX_EXT_BAD_NARGS;
    return g_plat[fn].fn(args);
}
