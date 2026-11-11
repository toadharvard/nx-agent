# w13 item A: FILETIME/SYSTEMTIME conversions
Functions (platform/win32/time.cpp), ported from Wine 9.0 and cited per function: FileTimeToSystemTime, SystemTimeToFileTime, FileTimeToLocalFileTime,
SystemTimeToTzSpecificLocalTime (get_timezone_id, compare_tzdate), GetSystemTime, GetLocalTime, GetSystemTimeAsFileTime, CompareFileTime (NULL -> -1).
Local variants use the existing TZ_AT clock zone only. Not added (not in imports.tsv): LocalFileTimeToFileTime, TzSpecificLocalTimeToSystemTime, Dos* time.
Deviation from Wine, by "Win32 as documented": Wine 9.0 RtlTimeFieldsToTime accepts Feb 29 in every year and years 30828..32767 (tick count wraps);
here both are FALSE + ERROR_INVALID_PARAMETER (Windows, documented domain 1601..30827; existing test wc_leap_years_and_weekdays requires it). Say if Wine's quirk is wanted.
Order note: the lead asked A restricted to the first-frame path; I did not establish that set statically (no call graph used), so A covers every time function
in the import table (4 imported: CompareFileTime, FileTimeToSystemTime, GetSystemTime, SystemTimeToTzSpecificLocalTime) plus the exported helpers. Separate commit, droppable.
Tests: tests/s4/test_k32_time_domain.cpp, 8 cases, oracles independent of platform code; no doubles. s4 506 tests 0 failed; s7 84, 0 failed.
