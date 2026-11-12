/* host_total_test.c - the guest<->host boundary is TOTAL: the platform dispatch of the console host (toolchain/host/host_platform.inc, with
 * host_vm.inc, host_section.inc, host_message.inc, host_ids.inc, host_thread_activity.inc, host_ext.inc: the same code host.c includes) executed under
 * qemu-aarch64 against the kernel double kdouble.h, with libnx's applet / thread-activity / error-applet calls stubbed. Every service
 * number hostapi.h declares is called with nargs = 0 and must answer one of the header's stated codes, never FNVNX_EXT_NO_SERVICE
 * (hostapi_total.py generates the list of declared services from the header with the C preprocessor: FNVNX_DECL_H); every service gets
 * its wrong-nargs, invalid-argument and success cases. Test names (hostapi.h table): total_declared, total_unknown, total_nargs,
 * total_ext, cpu_boost, thread_activity, thread_ids, message, display_docked, via_dispatch_vm, via_dispatch_section,
 * tz_at, via_dispatch_sock. */
#include "kdouble.h"
#include "fnvnx/hostapi.h"

/* ---- libnx stubs of the non-kernel services the dispatch reaches */
static int s_logs;
static void logf_(const char *fmt, ...) { (void)fmt; s_logs++; }
static Result s_boost_rc;
static int s_boost_calls, s_boost_last = -1;
Result appletSetCpuBoostMode(ApmCpuBoostMode m) { s_boost_calls++; s_boost_last = (int)m; return s_boost_rc; }
static AppletOperationMode s_opmode = AppletOperationMode_Handheld;
AppletOperationMode appletGetOperationMode(void) { return s_opmode; }
static Handle s_act_handle = 0xdead;
static ThreadActivity s_act = (ThreadActivity)-1;
static int s_act_calls;
static Result s_act_rc;
Result svcSetThreadActivity(Handle h, ThreadActivity a) { s_act_handle = h; s_act = a; s_act_calls++; return s_act_rc; }
static char s_dlg[4096], s_full[4096];
static int s_shown;
static Result s_create_rc;
Result errorApplicationCreate(ErrorApplicationConfig *c, const char *d, const char *f)
{
    memset(c, 0, sizeof *c);
    snprintf(s_dlg, sizeof s_dlg, "%s", d);
    snprintf(s_full, sizeof s_full, "%s", f);
    return s_create_rc;
}
Result errorApplicationShow(ErrorApplicationConfig *c) { (void)c; s_shown++; return 0; }

static int s_tz_fail;
Result timeToCalendarTimeWithMyRule(u64 ts, TimeCalendarTime *c, TimeCalendarAdditionalInfo *i)
{
    memset(c, 0, sizeof *c); memset(i, 0, sizeof *i);
    i->offset = (s32)(ts % 7200) - 3600; i->DST = (ts & 1); memcpy(i->timezoneName, "CESTXYZW", 8);
    return s_tz_fail ? MAKERESULT(Module_Libnx, LibnxError_NotFound) : 0;
}
uint64_t host_sock_call_ext(uint32_t fn, const uint64_t *a, uint32_t n);   /* host_sock_total.c: the real host_sock.inc */
#define host_sock_call host_sock_call_ext
static Handle g_own_process = OWN_HANDLE;
static struct { int core3, boost_loading; } g_cfg = {1, 1};
#include "../../../toolchain/host/host_ids.inc"
#include "../../../toolchain/host/host_thread_activity.inc"
#include "../../../toolchain/host/host_vm.inc"
#include "../../../toolchain/host/host_section.inc"
#include "../../../toolchain/host/host_ext.inc"
#include "../../../toolchain/host/host_message.inc"
#include "../../../toolchain/host/host_platform.inc"
#undef free
#undef memalign

/* the declared services: generated from the header by hostapi_total.py, or the list as of this file */
#ifdef FNVNX_DECL_H
#include FNVNX_DECL_H
#else
static const struct { const char *name; unsigned v; } g_decl[] = {
    {"FNVNX_PLAT_CPU_BOOST", FNVNX_PLAT_CPU_BOOST}, {"FNVNX_PLAT_THREAD_ACTIVITY", FNVNX_PLAT_THREAD_ACTIVITY},
    {"FNVNX_PLAT_MESSAGE", FNVNX_PLAT_MESSAGE}, {"FNVNX_PLAT_VM_RESERVE", FNVNX_PLAT_VM_RESERVE},
    {"FNVNX_PLAT_VM_COMMIT", FNVNX_PLAT_VM_COMMIT}, {"FNVNX_PLAT_VM_DECOMMIT", FNVNX_PLAT_VM_DECOMMIT},
    {"FNVNX_PLAT_VM_RELEASE", FNVNX_PLAT_VM_RELEASE}, {"FNVNX_PLAT_VM_PROTECT", FNVNX_PLAT_VM_PROTECT},
    {"FNVNX_PLAT_DISPLAY_DOCKED", FNVNX_PLAT_DISPLAY_DOCKED},
    {"FNVNX_PLAT_SECTION_CREATE", FNVNX_PLAT_SECTION_CREATE}, {"FNVNX_PLAT_SECTION_MAP", FNVNX_PLAT_SECTION_MAP},
    {"FNVNX_PLAT_SECTION_UNMAP", FNVNX_PLAT_SECTION_UNMAP}, {"FNVNX_PLAT_SECTION_FREE", FNVNX_PLAT_SECTION_FREE},
    {"FNVNX_PLAT_TZ_AT", FNVNX_PLAT_TZ_AT},
    {"FNVNX_PLAT_SOCK_SOCKET", FNVNX_PLAT_SOCK_SOCKET}, {"FNVNX_PLAT_SOCK_CLOSE", FNVNX_PLAT_SOCK_CLOSE}, {"FNVNX_PLAT_SOCK_BIND", FNVNX_PLAT_SOCK_BIND}, {"FNVNX_PLAT_SOCK_LISTEN", FNVNX_PLAT_SOCK_LISTEN}, {"FNVNX_PLAT_SOCK_ACCEPT", FNVNX_PLAT_SOCK_ACCEPT}, {"FNVNX_PLAT_SOCK_CONNECT", FNVNX_PLAT_SOCK_CONNECT}, {"FNVNX_PLAT_SOCK_SENDTO", FNVNX_PLAT_SOCK_SENDTO}, {"FNVNX_PLAT_SOCK_RECVFROM", FNVNX_PLAT_SOCK_RECVFROM}, {"FNVNX_PLAT_SOCK_SETSOCKOPT", FNVNX_PLAT_SOCK_SETSOCKOPT}, {"FNVNX_PLAT_SOCK_GETSOCKOPT", FNVNX_PLAT_SOCK_GETSOCKOPT}, {"FNVNX_PLAT_SOCK_POLL", FNVNX_PLAT_SOCK_POLL}, {"FNVNX_PLAT_SOCK_GETFL", FNVNX_PLAT_SOCK_GETFL}, {"FNVNX_PLAT_SOCK_SETFL", FNVNX_PLAT_SOCK_SETFL}, 
};
#endif
#define NDECL (sizeof g_decl / sizeof g_decl[0])

/* what the header states a service may answer with nargs = 0: the dispatch's own refusal, or (DISPLAY_DOCKED takes no argument) 0 / 1 */
static int stated_with_no_args(u64 r) { return r == FNVNX_EXT_BAD_NARGS || r == 0 || r == 1 || r == (u64)-(s64)FNVNX_WE_INVAL; }   /* sockets: hostapi.h "nargs must be exactly ... else -FNVNX_WE_INVAL" */

static u64 call(u32 fn, u32 nargs, u64 a0, u64 a1, u64 a2, u64 a3)
{
    u64 a[4] = {a0, a1, a2, a3};
    return h_ext_call(FNVNX_EXT_PLATFORM, fn, (u64)(uintptr_t)a, nargs);
}
#define B(a) ((volatile uint8_t *)(uintptr_t)(a))

int main(int argc, char **argv)
{
    int strict = argc > 1 && !strcmp(argv[1], "--served");   /* hostapi_total.py: a declared service without a handler row fails the run */
    kdouble_init();
    T(fnvnx_host_register_ext(FNVNX_EXT_PLATFORM, platform_ext) == 0);

    /* the header's numbers are the libnx / kernel results the host builds (so the header can state them as numbers) */
    T(FNVNX_HE_NOMEM == MAKERESULT(Module_Libnx, LibnxError_OutOfMemory) && FNVNX_HE_ALREADY == MAKERESULT(Module_Libnx, LibnxError_AlreadyInitialized));
    T(FNVNX_HE_NOTFOUND == MAKERESULT(Module_Libnx, LibnxError_NotFound) && FNVNX_HE_BADINPUT == MAKERESULT(Module_Libnx, LibnxError_BadInput));
    T(FNVNX_HE_SIZE == R_KERNEL(101) && FNVNX_HE_ADDR == R_KERNEL(102) && FNVNX_HE_RANGE == R_KERNEL(106) && FNVNX_HE_PERM == R_KERNEL(108));
    T(FNVNX_EXT_NO_MODULE == (u64)-255 && FNVNX_EXT_NO_SERVICE == (u64)-254 && FNVNX_EXT_BAD_NARGS == (u64)-253);

    /* total_declared: every declared service has a handler row: nargs = 0 never answers NO_SERVICE */
    int unserved = 0;
    for (size_t i = 0; i < NDECL; i++) {
        u64 r = call(g_decl[i].v, 0, 0, 0, 0, 0);
        if (r == FNVNX_EXT_NO_SERVICE) { unserved++; printf("UNSERVED %s = %u: ext_call answers FNVNX_EXT_NO_SERVICE\n", g_decl[i].name, g_decl[i].v); }
        else if (!stated_with_no_args(r)) { g_fail++; printf("FAIL %s with nargs 0 answered %#llx, which the header does not state\n", g_decl[i].name, (unsigned long long)r); }
    }
    if (strict) g_fail += unserved;

    /* total_unknown: a number of module 11 that is not declared answers NO_SERVICE with any nargs, an unregistered module NO_MODULE */
    for (u32 fn = 0; fn < 64; fn++) {
        int declared = 0;
        for (size_t i = 0; i < NDECL; i++) declared |= g_decl[i].v == fn;
        if (declared) continue;
        T(call(fn, 0, 0, 0, 0, 0) == FNVNX_EXT_NO_SERVICE && call(fn, 4, 1, 2, 3, 4) == FNVNX_EXT_NO_SERVICE);
    }
    T(call(0xFFFFFFFFu, 4, 0, 0, 0, 0) == FNVNX_EXT_NO_SERVICE && h_ext_call(FNVNX_EXT_PLATFORM, 0x10000000Bull, 0, 0) == FNVNX_EXT_NO_SERVICE);
    T(h_ext_call(12, 1, 0, 0) == FNVNX_EXT_NO_MODULE && h_ext_call(64, 11, 0, 0) == FNVNX_EXT_NO_MODULE && h_ext_call(~0ull, 11, 0, 0) == FNVNX_EXT_NO_MODULE);

    /* total_nargs: one argument fewer than the service takes -> BAD_NARGS (the handler is not reached); more are ignored; nargs > 0
     * with a null array -> BAD_NARGS. n is each declared service's own number of arguments. */
    static const struct { u32 fn, n; } need[] = {
        {FNVNX_PLAT_CPU_BOOST, 1}, {FNVNX_PLAT_THREAD_ACTIVITY, 2}, {FNVNX_PLAT_MESSAGE, 2}, {FNVNX_PLAT_VM_RESERVE, 2}, {FNVNX_PLAT_VM_COMMIT, 2},
        {FNVNX_PLAT_VM_DECOMMIT, 2}, {FNVNX_PLAT_VM_RELEASE, 1}, {FNVNX_PLAT_VM_PROTECT, 3}, {FNVNX_PLAT_DISPLAY_DOCKED, 0},
        {FNVNX_PLAT_SECTION_CREATE, 1}, {FNVNX_PLAT_SECTION_MAP, 4}, {FNVNX_PLAT_SECTION_UNMAP, 2}, {FNVNX_PLAT_SECTION_FREE, 1},
        {FNVNX_PLAT_TZ_AT, 2},
    };
    int calls0 = s_act_calls + s_boost_calls + s_shown;
    for (size_t i = 0; i < sizeof need / sizeof need[0]; i++) {
        if (need[i].n) {
            T(call(need[i].fn, need[i].n - 1, 0, 0, 0, 0) == FNVNX_EXT_BAD_NARGS);
            T(h_ext_call(FNVNX_EXT_PLATFORM, need[i].fn, 0, need[i].n) == FNVNX_EXT_BAD_NARGS);
        }
        T(platform_ext(need[i].fn, NULL, 0) == (need[i].n ? FNVNX_EXT_BAD_NARGS : platform_ext(need[i].fn, NULL, 0)));
    }
    T(s_act_calls + s_boost_calls + s_shown == calls0);   /* no handler ran */

    /* ---- cpu_boost: config off -> 1; mode on/off -> 0 and the applet is asked on a change only; an applet failure is the result, the mode stays */
    g_cfg.boost_loading = 0;
    T(call(1, 1, 1, 0, 0, 0) == 1 && s_boost_calls == 0);
    g_cfg.boost_loading = 1;
    T(call(1, 1, 5, 0, 0, 0) == 0 && s_boost_calls == 1 && s_boost_last == ApmCpuBoostMode_FastLoad && g_boost == 1);   /* any non-zero is fast */
    T(call(1, 1, 1, 0, 0, 0) == 0 && s_boost_calls == 1);
    T(call(1, 1, 0, 0, 0, 0) == 0 && s_boost_calls == 2 && s_boost_last == ApmCpuBoostMode_Normal && g_boost == 0);
    s_boost_rc = MAKERESULT(Module_Libnx, LibnxError_ShouldNotHappen);
    T(call(1, 1, 1, 0, 0, 0) == MAKERESULT(Module_Libnx, LibnxError_ShouldNotHappen) && g_boost == 0);
    s_boost_rc = 0;
    T(call(1, 1, 1, 0, 0, 0) == 0 && g_boost == 1 && call(1, 1, 0, 0, 0, 0) == 0);

    /* ---- thread_ids + thread_activity: ids are handles; 0 is the caller; made-up and freed ids name nothing */
    HostThread *th = calloc(1, sizeof *th), *th2 = calloc(1, sizeof *th2);
    th->t.handle = 0x77; th2->t.handle = 0x88;
    u64 id = host_id_new(HOST_ID_THREAD, th), id2 = host_id_new(HOST_ID_THREAD, th2);
    T(id && id2 && id != id2 && (id & 0xFFFF) && !(id >> 63));
    int c0 = s_act_calls;
    T(call(2, 2, 0, 1, 0, 0) == FNVNX_HE_BADINPUT && s_act_calls == c0);                    /* the calling thread */
    T(call(2, 2, 0x12345678, 1, 0, 0) == FNVNX_HE_NOTFOUND && call(2, 2, ~0ull, 1, 0, 0) == FNVNX_HE_NOTFOUND && call(2, 2, 1, 1, 0, 0) == FNVNX_HE_NOTFOUND);   /* made up */
    T(call(2, 2, id, 1, 0, 0) == 0 && s_act_handle == 0x77 && s_act == ThreadActivity_Paused);
    T(call(2, 2, id2, 0, 0, 0) == 0 && s_act_handle == 0x88 && s_act == ThreadActivity_Runnable);
    s_act_rc = MAKERESULT(1, 59);
    T(call(2, 2, id, 7, 0, 0) == MAKERESULT(1, 59) && s_act == ThreadActivity_Paused);     /* any non-zero pauses; the kernel's result as it is */
    s_act_rc = 0;
    T(host_id_get(HOST_ID_DIR, id) == NULL && host_id_del(HOST_ID_DIR, id) == NULL && host_id_get(HOST_ID_THREAD, id) == th);   /* kind checked */
    T(host_id_del(HOST_ID_THREAD, id) == th && host_id_del(HOST_ID_THREAD, id) == NULL);     /* a second free of the id */
    c0 = s_act_calls;
    T(call(2, 2, id, 1, 0, 0) == FNVNX_HE_NOTFOUND && s_act_calls == c0);                    /* freed */
    HostThread *th3 = calloc(1, sizeof *th3);
    u64 id3 = host_id_new(HOST_ID_THREAD, th3);
    T(id3 && id3 != id && (id3 & 0xFFFF) == (id & 0xFFFF) && host_id_get(HOST_ID_THREAD, id) == NULL && host_id_get(HOST_ID_THREAD, id3) == th3);   /* slot reused, generation differs */
    T(host_id_get(HOST_ID_THREAD, id3 | (1ull << 40)) == NULL && host_id_get(HOST_ID_THREAD, 0) == NULL && host_id_get(HOST_ID_THREAD, FNVNX_ID_MAX + 1) == NULL);
    host_id_del(HOST_ID_THREAD, id2); host_id_del(HOST_ID_THREAD, id3);
    /* the table is bounded: FNVNX_ID_MAX live ids, the next is 0 and nothing is lost */
    {
        static u64 ids[FNVNX_ID_MAX + 1];
        int ok = 1;
        for (int i = 0; i < FNVNX_ID_MAX; i++) { ids[i] = host_id_new(HOST_ID_DIR, th); ok &= ids[i] != 0; }
        T(ok && host_id_new(HOST_ID_THREAD, th) == 0);
        for (int i = 0; i < FNVNX_ID_MAX; i++) ok &= host_id_del(HOST_ID_DIR, ids[i]) == th;
        T(ok && host_id_new(HOST_ID_THREAD, th) != 0);
    }
    free(th); free(th2); free(th3);

    /* ---- message: null strings read as "" and the applet's result is the answer */
    T(call(3, 2, 0, 0, 0, 0) == 0 && s_shown == 1 && !strcmp(s_dlg, "") && !strcmp(s_full, ""));
    T(call(3, 2, (u64)(uintptr_t)"cap", (u64)(uintptr_t)"line1\nline2", 0, 0) == 0 && !strcmp(s_dlg, "cap\nline1"));
    s_create_rc = FNVNX_HE_BADINPUT;
    T(call(3, 2, 0, 0, 0, 0) == FNVNX_HE_BADINPUT);
    s_create_rc = 0;

    /* ---- display_docked: 1 in console mode, 0 handheld; takes no argument, extra ones ignored */
    s_opmode = AppletOperationMode_Console;
    T(call(9, 0, 0, 0, 0, 0) == 1 && call(9, 3, 7, 8, 9, 0) == 1 && platform_ext(9, NULL, 0) == 1);
    s_opmode = AppletOperationMode_Handheld;
    T(call(9, 0, 0, 0, 0, 0) == 0 && call(9, 2, 1, 2, 0, 0) == 0);

    /* ---- via_dispatch_vm: the argument mapping of the VM services through ext_call (the cases themselves: host_vm_test.c, host_prot_test.c) */
    u64 rv = call(4, 2, 0, 0x100000, 0, 0);
    T(rv && !(rv & 0xFFFF) && call(4, 2, 0, 0, 0, 0) == 0);
    T(call(5, 2, rv, 0x3000, 0, 0) == 0 && kstate(rv) == A_ALIAS_CODE_DATA && call(5, 2, rv + 0x1000, 0, 0, 0) == FNVNX_HE_BADINPUT);
    T(call(8, 3, rv, 0x1000, 1, 0) == 0 && kperm(rv) == 1 && call(8, 3, rv, 0x1000, 0, 0) == 0 && kperm(rv) == 0 && call(8, 3, rv, 0x1000, 2, 0) == FNVNX_HE_PERM);
    T(call(8, 3, rv + 1, 0x1000, 3, 0) == FNVNX_HE_ADDR && call(8, 3, rv, 0, 3, 0) == FNVNX_HE_SIZE && call(8, 3, rv + 0x10000, 0x1000, 3, 0) == FNVNX_HE_RANGE);
    T(call(6, 2, rv, 0x1000, 0, 0) == 0 && kperm(rv) == 3 && call(7, 1, rv + 0x1000, 0, 0, 0) == FNVNX_HE_BADINPUT && call(7, 1, rv, 0, 0, 0) == 0 && kstate(rv) == A_FREE);

    /* ---- via_dispatch_section */
    u64 sid = call(11, 1, 0x4000, 0, 0, 0);
    T(sid == 1 && call(11, 1, 0, 0, 0, 0) == ERR(FNVNX_SEC_E_SIZE));
    u64 va = call(12, 4, sid, 0, 0, 0x2000);
    T(va && !(va >> 63) && kstate(va) == A_SHARED && call(12, 4, 99, 0, 0, 0x1000) == ERR(FNVNX_SEC_E_ID));
    T(call(13, 2, va, 0x2000, 0, 0) == 0 && call(13, 2, va, 0x2000, 0, 0) == ERR(FNVNX_SEC_E_RANGE) && call(14, 1, sid, 0, 0, 0) == 0 && call(14, 1, sid, 0, 0, 0) == ERR(FNVNX_SEC_E_ID));

    /* ---- tz_at (utc_seconds, FnvnxTzAt *): 0 and the zone, or non-zero when the time service has none */
    {
        FnvnxTzAt z;
        T(call(34, 2, 5000, (u64)(uintptr_t)&z, 0, 0) == 0 && z.off_s == (s32)(5000 % 7200) - 3600 && z.dst == 0 && !strcmp(z.abbr, "CESTXYZ"));
        T(call(34, 2, 5001, (u64)(uintptr_t)&z, 0, 0) == 0 && z.dst == 1);
        s_tz_fail = 1;
        T(call(34, 2, 5000, (u64)(uintptr_t)&z, 0, 0) == 1);
        s_tz_fail = 0;
    }
    /* ---- the declared socket services reach the real handler: a wrong count is -WE_INVAL (the handler's own rule); every number
     * the header does not declare (27..29, 32, 33, 37 and up) is NO_SERVICE (total_unknown above) */
    for (u32 fn = 0; fn < 64; fn++)
        if (host_sock_nargs(fn) >= 0) T(call(fn, 0, 0, 0, 0, 0) == (u64)-(s64)FNVNX_WE_INVAL);
    for (u32 fn = 27; fn <= 33; fn++)
        if (fn != FNVNX_PLAT_SOCK_GETFL && fn != FNVNX_PLAT_SOCK_SETFL && fn != FNVNX_PLAT_TZ_AT && fn != FNVNX_PLAT_THREAD_SELF)
            T(call(fn, 5, 0, 0, 0, 0) == FNVNX_EXT_NO_SERVICE);

    T(g_free_while_locked == 0 && g_unmapcode_with_views == 0);
    if (unserved) printf("host_total: %d declared service(s) not served%s\n", unserved, strict ? "" : " (reported, not failed here: hostapi_total.py --served fails on it)");
    printf("host_total: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
