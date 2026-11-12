/* host_platform.inc - ext_call module FNVNX_EXT_PLATFORM (hostapi.h 1..15): ONE table, one row per service number: the
 * least number of arguments and the handler. platform_ext is total: every (fn, nargs) has exactly one answer.
 *   a socket service (host_sock_nargs(fn) >= 0, the FNVNX_PLAT_SOCK_* names of hostapi.h): host_sock_call answers (nargs wrong -> -FNVNX_WE_INVAL, its own rule)
 *   every other number, i.e. a number without a row and not a socket service (0, 10 until the access-fault delivery exists, 27..29, 32, 33,
 *   37 and up: whatever hostapi.h does not declare) -> FNVNX_EXT_NO_SERVICE
 *   nargs below the row's number, or nargs > 0 with a null argument array     -> FNVNX_EXT_BAD_NARGS
 *   otherwise the handler's answer, which hostapi.h states per service; more arguments than the row needs are ignored.
 * A service declared in hostapi.h without a row is a test failure (tests/s4/backends/hostapi_total.py).
 * Standalone like the other .inc files: needs the handlers' files included first (host_vm.inc, host_section.inc,
 * host_message.inc, host_textentry.inc, host_ids.inc, host_thread_activity.inc), logf_, g_cfg {core3, boost_loading} and libnx's applet. */
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
 * thread_create (an id that names none: NotFound); paused != 0 pauses it, 0 resumes it; else the kernel's result */
static u64 pf_thread_activity(const u64 *a) {
    HostThread *h = a[0] ? host_id_get(HOST_ID_THREAD, a[0]) : NULL;
    if (a[0] && !h) return FNVNX_HE_NOTFOUND;
    return host_thread_activity(a[0] ? h->t.handle : 0, a[0] == 0, a[1]);
}
static u64 pf_message(const u64 *a) { return host_show_message((const char *)a[0], (const char *)a[1]); }
static u64 pf_text_entry(const u64 *a) { return host_text_entry(a); }
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
static u64 pf_thread_self(const u64 *a) { (void)a; return h_thread_self(); }  
static u64 pf_unmap_fixed(const u64 *a) { return h_unmap_fixed(a[0], a[1], a[2]); }
/* TZ_AT (utc_seconds, FnvnxTzAt *): the platform lead's service, verbatim (hostapi.h states: 0, or non-zero without time zone data) */
static u64 pf_tz_at(const u64 *args) {
    FnvnxTzAt *o = (FnvnxTzAt *)args[1];
    TimeCalendarTime cal;
    TimeCalendarAdditionalInfo info;
    if (R_FAILED(timeToCalendarTimeWithMyRule(args[0], &cal, &info))) return 1;
    o->off_s = info.offset;
    o->dst = info.DST != 0;
    memcpy(o->abbr, info.timezoneName, sizeof o->abbr);
    o->abbr[sizeof o->abbr - 1] = 0;
    return 0;
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
    [FNVNX_PLAT_TEXT_ENTRY] = {5, pf_text_entry},
};
static uint64_t platform_ext(uint32_t fn, const uint64_t *args, uint32_t nargs) {
    /* the sockets (host_sock.inc): host_sock_nargs names the served ones; it checks nargs itself (exactly the count: else -FNVNX_WE_INVAL) */
    if (host_sock_nargs(fn) >= 0) return host_sock_call(fn, args, nargs);
    if (fn >= sizeof g_plat / sizeof g_plat[0] || !g_plat[fn].fn) return FNVNX_EXT_NO_SERVICE;
    if (nargs < g_plat[fn].nargs || (nargs && !args)) return FNVNX_EXT_BAD_NARGS;
    return g_plat[fn].fn(args);
}
