/* host_tz.inc - the console time zone at one instant (FNVNX_PLAT_TZ_AT, tz_offset_minutes). Standalone: <switch.h>, <string.h>,
 * fnvnx/hostapi.h; tests/s4/backends runs it under qemu. */
/* TZ_AT (hostapi.h): the console's zone at one instant. 0, or -errno (wire numbers): out null -> EFAULT; the time service refuses
 * the instant (its only input-dependent refusal is the range of the instant) -> EOVERFLOW. abbr: the first 7 bytes, then NUL. */
static u64 host_tz_at(u64 utc_s, FnvnxTzAt *o) {
    if (!o) return (u64)-(s64)FNVNX_WE_FAULT;
    TimeCalendarTime cal;
    TimeCalendarAdditionalInfo info;
    if (R_FAILED(timeToCalendarTimeWithMyRule(utc_s, &cal, &info))) return (u64)-(s64)FNVNX_WE_OVERFLOW;
    o->off_s = info.offset;
    o->dst = info.DST != 0;
    memcpy(o->abbr, info.timezoneName, 7);
    o->abbr[7] = 0;
    return 0;
}
