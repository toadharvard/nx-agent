/* xc.c - dynamic initializer runner (stream S10, docs/STARTUP.md "Static initialization").
 *
 * The natives come from the generated table (orig -> native, sorted); the order comes either from the
 * original addresses (ADDRESS mode) or from the image's own __xc table (IMAGE mode). See fnvnx_xc.h.
 */
#include "fnvnx_xc.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern const struct fnvnx_xc_entry fnvnx_xc_table[] __attribute__((weak));
extern const uint32_t fnvnx_xc_table_count __attribute__((weak));
extern const uint32_t fnvnx_xc_group_lo __attribute__((weak));
extern const uint32_t fnvnx_xc_group_hi __attribute__((weak));

static void sample(uint32_t *arr, uint32_t *n, uint32_t v) {
    if (*n < FNVNX_XC_SAMPLES) arr[(*n)++] = v;
}

int fnvnx_xc_check_table(const struct fnvnx_xc_entry *t, uint32_t n, int32_t *bad_index) {
    for (uint32_t i = 0; i < n; i++) {
        if (!t[i].fn || (i && t[i].orig <= t[i - 1].orig)) {
            if (bad_index) *bad_index = (int32_t)i;
            return FNVNX_XC_E_UNSORTED;
        }
    }
    if (bad_index) *bad_index = -1;
    return FNVNX_XC_OK;
}

static int32_t find_index(const struct fnvnx_xc_entry *t, uint32_t n, uint32_t orig) {
    uint32_t lo = 0, hi = n;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (t[mid].orig < orig) lo = mid + 1;
        else hi = mid;
    }
    return (lo < n && t[lo].orig == orig) ? (int32_t)lo : -1;
}

const struct fnvnx_xc_entry *fnvnx_xc_find(const struct fnvnx_xc_entry *t, uint32_t n, uint32_t orig) {
    int32_t i = find_index(t, n, orig);
    return i < 0 ? NULL : &t[i];
}

static void call(const struct fnvnx_xc_entry *e, const fnvnx_xc_hooks *h, fnvnx_xc_report *rep) {
    uint32_t seq = rep->called++;
    if (h && h->before) h->before(h->ctx, e->orig, seq);
    e->fn();
    if (h && h->after) h->after(h->ctx, e->orig, seq);
}

static int begin(const struct fnvnx_xc_entry *t, uint32_t n, uint32_t mode, fnvnx_xc_report *rep) {
    memset(rep, 0, sizeof *rep);
    rep->mode = mode;
    rep->table_entries = n;
    rep->bad_index = -1;
    if (n && !t) return FNVNX_XC_E_ARG;
    return fnvnx_xc_check_table(t, n, &rep->bad_index);
}

int fnvnx_xc_run_address_order(const struct fnvnx_xc_entry *t, uint32_t n, uint32_t group_lo, uint32_t group_hi,
                               uint32_t flags, const fnvnx_xc_hooks *hooks, fnvnx_xc_report *rep) {
    fnvnx_xc_report local;
    if (!rep) rep = &local;
    int rc = begin(t, n, FNVNX_XC_MODE_ADDRESS, rep);
    if (rc) return rc;
    for (uint32_t i = 0; i < n; i++) {
        if (!(flags & FNVNX_XC_ALL_ENTRIES) && (t[i].orig < group_lo || t[i].orig >= group_hi)) {
            rep->skipped_outside++;
            continue;
        }
        call(&t[i], hooks, rep);
    }
    return FNVNX_XC_OK;
}

int fnvnx_xc_run_image_order(const struct fnvnx_xc_entry *t, uint32_t n, const uint32_t *slots, uint32_t nslots,
                             uint32_t group_lo, uint32_t group_hi, uint32_t flags, const fnvnx_xc_hooks *hooks,
                             fnvnx_xc_report *rep) {
    fnvnx_xc_report local;
    if (!rep) rep = &local;
    int rc = begin(t, n, FNVNX_XC_MODE_IMAGE, rep);
    if (rc) return rc;
    if (nslots && !slots) return FNVNX_XC_E_ARG;
    unsigned char *seen = calloc(n ? n : 1, 1);
    if (!seen) return FNVNX_XC_E_NOMEM;
    rep->image_slots = nslots;
    uint32_t prev = 0;
    int have_prev = 0;
    for (uint32_t s = 0; s < nslots; s++) {
        uint32_t orig = slots[s];
        if (!orig) {
            rep->image_null++;
            continue;
        }
        int32_t i = find_index(t, n, orig);
        if (i < 0) {
            rep->image_missing++;
            sample(rep->missing_sample, &rep->n_missing, orig);
            continue;
        }
        if (seen[i]) rep->image_duplicates++;
        seen[i] = 1;
        if (have_prev && orig < prev) {
            rep->inversions++;
            sample(rep->inversion_sample, &rep->n_inversions, orig);
        }
        prev = orig;
        have_prev = 1;
        call(&t[i], hooks, rep);
    }
    for (uint32_t i = 0; i < n; i++) {
        if (seen[i] || t[i].orig < group_lo || t[i].orig >= group_hi) continue;
        rep->not_in_image++;
        sample(rep->not_in_image_sample, &rep->n_not_in_image, t[i].orig);
    }
    if (flags & FNVNX_XC_RUN_LEFTOVERS) {
        for (uint32_t i = 0; i < n; i++) {
            if (seen[i] || t[i].orig < group_lo || t[i].orig >= group_hi) continue;
            rep->leftovers_called++;
            call(&t[i], hooks, rep);
        }
    }
    free(seen);
    return FNVNX_XC_OK;
}

static int is_code(uint32_t w, uint32_t lo, uint32_t hi) { return w >= lo && w < hi; }

int fnvnx_xc_locate(const uint32_t *mem, uint32_t va_lo, uint32_t nwords, uint32_t text_lo, uint32_t text_hi,
                    uint32_t group_lo, uint32_t group_hi, uint32_t min_hits, uint32_t *begin_va, uint32_t *end_va,
                    uint32_t *hits_out) {
    if (!mem || !begin_va || !end_va || group_lo < text_lo || group_hi > text_hi) return FNVNX_XC_E_ARG;
    uint32_t best_hits = 0, best_first = 0, best_last = 0;
    uint32_t run_hits = 0, run_first = 0, run_last = 0;
    for (uint32_t i = 0; i <= nwords; i++) {
        uint32_t w = i < nwords ? mem[i] : 1u; /* sentinel: 1 is never a code address */
        if (i < nwords && (w == 0 || is_code(w, text_lo, text_hi))) {
            if (w >= group_lo && w < group_hi) {
                if (!run_hits) run_first = i;
                run_last = i;
                run_hits++;
            }
            continue;
        }
        if (run_hits > best_hits) {
            best_hits = run_hits;
            best_first = run_first;
            best_last = run_last;
        }
        run_hits = 0;
    }
    if (hits_out) *hits_out = best_hits;
    if (!best_hits || best_hits < min_hits) return FNVNX_XC_E_NOTFOUND;
    /* Extend across library entries (non-NULL code pointers) up to the NULL sentinels. */
    uint32_t b = best_first, e = best_last + 1;
    while (b > 0 && mem[b - 1] != 0 && is_code(mem[b - 1], text_lo, text_hi)) b--;
    while (e < nwords && mem[e] != 0 && is_code(mem[e], text_lo, text_hi)) e++;
    *begin_va = va_lo + 4u * b;
    *end_va = va_lo + 4u * e;
    return FNVNX_XC_OK;
}

static size_t put(char *buf, size_t cap, size_t len, const char *fmt, uint32_t a, uint32_t b) {
    if (len >= cap) return len;
    int k = snprintf(buf + len, cap - len, fmt, (unsigned)a, (unsigned)b);
    if (k < 0) return len;
    len += (size_t)k;
    return len < cap ? len : cap - 1;
}

static size_t put_list(char *buf, size_t cap, size_t len, const char *label, const uint32_t *s, uint32_t n) {
    if (!n || len >= cap) return len;
    int k = snprintf(buf + len, cap - len, "  %s:", label);
    if (k > 0) len = len + (size_t)k < cap ? len + (size_t)k : cap - 1;
    for (uint32_t i = 0; i < n; i++) len = put(buf, cap, len, " %#010x", s[i], 0);
    return put(buf, cap, len, "\n", 0, 0);
}

size_t fnvnx_xc_format_report(const fnvnx_xc_report *r, char *buf, size_t cap) {
    if (!cap) return 0;
    buf[0] = 0;
    size_t len = 0;
    len = put(buf, cap, len, r->mode == FNVNX_XC_MODE_IMAGE ? "fnvnx xc: image order, %u natives, %u called\n"
                                                             : "fnvnx xc: address order, %u natives, %u called\n",
              r->table_entries, r->called);
    if (r->bad_index >= 0) len = put(buf, cap, len, "  table malformed at index %u\n", (uint32_t)r->bad_index, 0);
    if (r->mode == FNVNX_XC_MODE_ADDRESS) {
        len = put(buf, cap, len, "  outside the group (not run): %u\n", r->skipped_outside, 0);
        return len;
    }
    len = put(buf, cap, len, "  image slots %u, NULL %u\n", r->image_slots, r->image_null);
    len = put(buf, cap, len, "  missing natives %u, duplicate slots %u\n", r->image_missing, r->image_duplicates);
    len = put(buf, cap, len, "  natives not named by the image %u (run afterwards: %u)\n", r->not_in_image,
              r->leftovers_called);
    len = put(buf, cap, len, "  order inversions vs address order: %u\n", r->inversions, 0);
    len = put_list(buf, cap, len, "missing", r->missing_sample, r->n_missing);
    len = put_list(buf, cap, len, "not in image", r->not_in_image_sample, r->n_not_in_image);
    len = put_list(buf, cap, len, "inversions at", r->inversion_sample, r->n_inversions);
    return len;
}

int fnvnx_xc_run_initializers(const uint32_t *img_slots, uint32_t img_nslots, uint32_t flags,
                              const fnvnx_xc_hooks *hooks, fnvnx_xc_report *rep) {
    const struct fnvnx_xc_entry *t = fnvnx_xc_table;
    uint32_t n = &fnvnx_xc_table_count ? fnvnx_xc_table_count : 0;
    uint32_t lo = &fnvnx_xc_group_lo ? fnvnx_xc_group_lo : 0;
    uint32_t hi = &fnvnx_xc_group_hi ? fnvnx_xc_group_hi : 0xFFFFFFFFu;
    if (!t) n = 0;
    if (img_slots) return fnvnx_xc_run_image_order(t, n, img_slots, img_nslots, lo, hi, flags, hooks, rep);
    return fnvnx_xc_run_address_order(t, n, lo, hi, flags, hooks, rep);
}
