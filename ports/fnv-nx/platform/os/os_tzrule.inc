/* os_tzrule.inc - os_tz_rule(), shared by both backends. The including file defines
 *     static int tzr_at(int64_t utc_s, int32_t *off_s, int *is_dst, char abbr[8]);
 * (0 = ok): the zone's UTC offset in seconds, its daylight flag and its abbreviation at one instant. The rule is
 * derived from that primitive alone, so every backend yields the rule of the zone its own time source reports.
 *
 * Rule (what a Windows TIME_ZONE_INFORMATION can say): at most one daylight period a year, ended and begun by two
 * transitions in the current year. A transition is written as "n-th weekday of a month at hh:mm wall time" (n = 5: the
 * last) when that recurring rule reproduces the zone's transitions in each of the years Y-2 .. Y+2 (checked instant
 * by instant against tzr_at); else as the absolute date in the current year. A zone with no transition, or with an
 * offset change that is not a daylight change, is a zone without daylight time at its present offset. */

static int64_t tzr_days_from_civil(int64_t y, unsigned m, unsigned d)
{
    y -= m <= 2;
    int64_t era = (y >= 0 ? y : y - 399) / 400;
    unsigned yoe = (unsigned)(y - era * 400);
    unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + (int64_t)doe - 719468;
}
static void tzr_civil(int64_t days, int *y, unsigned *m, unsigned *d)
{
    days += 719468;
    int64_t era = (days >= 0 ? days : days - 146096) / 146097;
    unsigned doe = (unsigned)(days - era * 146097);
    unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    int64_t yy = (int64_t)yoe + era * 400;
    unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    unsigned mp = (5 * doy + 2) / 153;
    *d = doy - (153 * mp + 2) / 5 + 1;
    *m = mp < 10 ? mp + 3 : mp - 9;
    *y = (int)(yy + (*m <= 2));
}
static int tzr_dim(int y, unsigned m)
{
    static const unsigned char dm[12] = { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };
    return dm[m - 1] + (m == 2 && ((y % 4 == 0 && y % 100) || y % 400 == 0));
}
static int tzr_wday(int64_t days) { return (int)(((days % 7) + 7 + 4) % 7); }   /* 1970-01-01 was a Thursday */

/* a transition inside (a, b] (utc seconds; they differ in state): bisect to the second */
static int64_t tzr_bisect(int64_t a, int64_t b)
{
    int32_t oa;
    int da;
    char n[8];
    tzr_at(a, &oa, &da, n);
    while (b - a > 1) {
        int64_t m = a + (b - a) / 2;
        int32_t om;
        int dm_;
        tzr_at(m, &om, &dm_, n);
        if (om == oa && dm_ == da)
            a = m;
        else
            b = m;
    }
    return b;   /* the first second in the new state */
}

/* the local wall time (seconds since 1970 read as local) of the n-th (5: last) weekday of a month, hh:mm */
static int64_t tzr_rule_local(int y, const os_tzdate_t *d)
{
    int64_t d1 = tzr_days_from_civil(y, d->month, 1);
    int day = 1 + (d->wday - tzr_wday(d1) + 7) % 7 + (d->week - 1) * 7;
    while (day > tzr_dim(y, d->month))
        day -= 7;
    return (tzr_days_from_civil(y, d->month, (unsigned)day)) * 86400 + d->hour * 3600 + d->minute * 60;
}

/* does the rule date reproduce the transition of year y (state change at tr: before -> after)? */
static int tzr_check(int y, const os_tzdate_t *d, int32_t off_before, int dst_before, int32_t off_after, int dst_after)
{
    int64_t utc = tzr_rule_local(y, d) - off_before;
    int32_t o1, o2;
    int d1, d2;
    char n[8];
    if (tzr_at(utc - 1, &o1, &d1, n) || tzr_at(utc, &o2, &d2, n))
        return 0;
    return o1 == off_before && d1 == dst_before && o2 == off_after && d2 == dst_after;
}

static void tzr_describe(int64_t tr_utc, int32_t off_before, os_tzdate_t *d, int32_t ob, int db, int32_t oa, int da, int year)
{
    int64_t local = tr_utc + off_before;
    int64_t days = local >= 0 ? local / 86400 : -((-local + 86399) / 86400);
    int64_t sec = local - days * 86400;
    int yy;
    unsigned mm, dd;
    tzr_civil(days, &yy, &mm, &dd);
    os_tzdate_t c;
    memset(&c, 0, sizeof c);
    c.month = (uint16_t)mm;
    c.wday = (uint16_t)tzr_wday(days);
    c.hour = (uint16_t)(sec / 3600);
    c.minute = (uint16_t)((sec % 3600) / 60);
    int nth = (int)((dd - 1) / 7 + 1), last = (int)dd + 7 > tzr_dim(yy, mm);
    int cand[2] = { nth, last ? 5 : 0 };
    for (int k = 0; k < 2; k++) {
        if (!cand[k])
            continue;
        c.week = (uint16_t)cand[k];
        int ok = 1;
        for (int y = year - 2; y <= year + 2 && ok; y++)
            ok = tzr_check(y, &c, ob, db, oa, da);
        if (ok) {
            *d = c;
            return;
        }
    }
    memset(d, 0, sizeof *d);
    d->year = (uint16_t)yy;               /* absolute date of this year */
    d->month = (uint16_t)mm;
    d->wday = c.wday;
    d->week = (uint16_t)dd;               /* the day of the month */
    d->hour = c.hour;
    d->minute = c.minute;
}

int os_tz_rule(os_tzrule_t *r)
{
    memset(r, 0, sizeof *r);
    int64_t now = os_wall_time_us() / 1000000;
    int32_t off_now;
    int dst_now;
    char abbr[8];
    if (tzr_at(now, &off_now, &dst_now, abbr))
        return -1;
    int64_t days = (now + off_now) >= 0 ? (now + off_now) / 86400 : -((-(now + off_now) + 86399) / 86400);
    int yy;
    unsigned mm, dd;
    tzr_civil(days, &yy, &mm, &dd);
    /* the transitions of the local year yy: daily probes find the days on which the state changes */
    int64_t start = tzr_days_from_civil(yy, 1, 1) * 86400 - off_now;
    int64_t end = tzr_days_from_civil(yy + 1, 1, 1) * 86400 - off_now;
    struct { int64_t t; int32_t ob, oa; int db, da; char name_after[8]; } tr[4];
    int ntr = 0, many = 0;
    int32_t po;
    int pd;
    char pn[8];
    tzr_at(start, &po, &pd, pn);
    for (int64_t t = start + 86400; t <= end + 86400; t += 86400) {
        int32_t o;
        int dd2;
        char n[8];
        if (tzr_at(t, &o, &dd2, n))
            return -1;
        if (o != po || dd2 != pd) {
            if (ntr == 4) { many = 1; break; }
            int64_t x = tzr_bisect(t - 86400, t);
            tr[ntr].t = x; tr[ntr].ob = po; tr[ntr].oa = o; tr[ntr].db = pd; tr[ntr].da = dd2;
            memcpy(tr[ntr].name_after, n, 8);
            ntr++;
            po = o; pd = dd2;
        }
    }
    snprintf(r->std_name, sizeof r->std_name, "%s", abbr);
    snprintf(r->dst_name, sizeof r->dst_name, "%s", abbr);
    r->std_off_min = r->dst_off_min = off_now / 60;
    /* one daylight period: a transition into daylight and one out of it */
    if (!many && ntr == 2 && tr[0].db != tr[0].da && tr[1].db != tr[1].da && tr[0].da != tr[1].da) {
        int to_dst = tr[0].da ? 0 : 1;      /* index of the transition into daylight */
        int to_std = 1 - to_dst;
        r->has_dst = 1;
        r->std_off_min = tr[to_std].oa / 60;
        r->dst_off_min = tr[to_dst].oa / 60;
        snprintf(r->std_name, sizeof r->std_name, "%s", tr[to_std].name_after);
        snprintf(r->dst_name, sizeof r->dst_name, "%s", tr[to_dst].name_after);
        tzr_describe(tr[to_dst].t, tr[to_dst].ob, &r->dst_begin, tr[to_dst].ob, tr[to_dst].db, tr[to_dst].oa, tr[to_dst].da, yy);
        tzr_describe(tr[to_std].t, tr[to_std].ob, &r->dst_end, tr[to_std].ob, tr[to_std].db, tr[to_std].oa, tr[to_std].da, yy);
    } else if (dst_now) {
        /* an irregular zone: report the present state as the zone's only one */
        r->std_off_min = r->dst_off_min = off_now / 60;
    }
    return 0;
}
