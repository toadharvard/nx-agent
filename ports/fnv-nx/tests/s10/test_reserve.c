/* tests/s10/test_reserve.c - host tests of the address window reservation planner (startup/host/reserve.c)
 * against fake memory maps, and a model of where ldr and the kernel place things at process start
 * (Atmosphère ldr_process_creation.cpp, Mesosphère KPageTableBase::FindFreeArea) to estimate how often a
 * launch has to be relaunched.  Usage: test_reserve [--trials N] */
#include "fnvnx_reserve.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail, g_checks;
#define CHECK(c)                                                                                           \
    do {                                                                                                   \
        g_checks++;                                                                                        \
        if (!(c)) {                                                                                        \
            g_fail++;                                                                                      \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c);                         \
        }                                                                                                  \
    } while (0)

/* ---- fake address space -------------------------------------------------------------------------------- */
#define MAXR 64
typedef struct {
    FnvnxResvRegion r[MAXR]; /* mapped regions, sorted, non-overlapping */
    uint32_t n;
    uint64_t heap_lo, heap_hi;
    uint32_t map_calls, unmap_calls, virt_calls, fail_map_rc;
    uint64_t last_src;
    int query_broken_at; /* query fails for this many calls in (negative: never) */
} Space;

static void sp_add(Space *s, uint64_t addr, uint64_t size, uint32_t type) {
    uint32_t i = 0;
    while (i < s->n && s->r[i].addr < addr) i++;
    memmove(&s->r[i + 1], &s->r[i], (s->n - i) * sizeof s->r[0]);
    s->r[i] = (FnvnxResvRegion){ addr, size, type, 3 };
    s->n++;
}

static int sp_remove(Space *s, uint64_t addr, uint64_t size) {
    for (uint32_t i = 0; i < s->n; i++)
        if (s->r[i].addr == addr && s->r[i].size == size) {
            memmove(&s->r[i], &s->r[i + 1], (s->n - i - 1) * sizeof s->r[0]);
            s->n--;
            return 1;
        }
    return 0;
}

static int sp_query(void *ctx, uint64_t a, FnvnxResvRegion *out) {
    Space *s = ctx;
    if (s->query_broken_at == 0) return -1;
    if (s->query_broken_at > 0) s->query_broken_at--;
    uint64_t lo = 0, hi = 1ull << 32;
    for (uint32_t i = 0; i < s->n; i++) {
        if (a >= s->r[i].addr && a < s->r[i].addr + s->r[i].size) {
            *out = s->r[i];
            return 0;
        }
        if (s->r[i].addr + s->r[i].size <= a) lo = s->r[i].addr + s->r[i].size;
        else if (s->r[i].addr > a) {
            hi = s->r[i].addr;
            break;
        }
    }
    *out = (FnvnxResvRegion){ lo, hi - lo, FNVNX_MEMTYPE_UNMAPPED, 0 };
    return 0;
}

static int sp_free(Space *s, uint64_t a, uint64_t size) {
    FnvnxResvRegion tmp[1];
    FnvnxResvOps ops = { s, sp_query, NULL, NULL, NULL };
    int saved = s->query_broken_at;
    s->query_broken_at = -1;
    int f = fnvnx_resv_scan(&ops, a, a + size, tmp, 1) == 0;
    s->query_broken_at = saved;
    return f;
}

static uint32_t sp_map(void *ctx, uint64_t dst, uint64_t src, uint64_t size) {
    Space *s = ctx;
    s->map_calls++;
    if (s->fail_map_rc) return s->fail_map_rc;
    if (src < s->heap_lo || src + size > s->heap_hi || (src & 0xFFF)) return 0xCC01; /* bad source */
    if (!sp_free(s, dst, size)) return 0xD401;                                       /* InvalidCurrentMemory */
    sp_add(s, dst, size, FNVNX_MEMTYPE_STACK);
    s->last_src = src;
    return 0;
}

static uint32_t sp_unmap(void *ctx, uint64_t dst, uint64_t src, uint64_t size) {
    Space *s = ctx;
    (void)src;
    s->unmap_calls++;
    return sp_remove(s, dst, size) ? 0 : 0xD401;
}

static uint32_t sp_virt(void *ctx, uint64_t addr, uint64_t size, void **h) {
    Space *s = ctx;
    s->virt_calls++;
    *h = (void *)(uintptr_t)(addr ^ size);
    return 0;
}

static void sp_init(Space *s) {
    memset(s, 0, sizeof *s);
    s->query_broken_at = -1;
    s->heap_lo = 0x48000000ull;
    s->heap_hi = 0x48000000ull + (512ull << 20);
    sp_add(s, s->heap_lo, s->heap_hi - s->heap_lo, 0x05); /* heap */
}

static const FnvnxResvRange k_ranges[] = {
    { FNVNX_RESV_IMAGE_LO, FNVNX_RESV_WINDOW_LO, FNVNX_RESV_BACKED, "image+text" },
    { FNVNX_RESV_WINDOW_LO, FNVNX_RESV_WINDOW_HI, FNVNX_RESV_REQUIRED | FNVNX_RESV_BACKED, "data-window" },
    { FNVNX_RESV_GUEST_BASE, FNVNX_RESV_GUEST_BASE + (64ull << 20), FNVNX_RESV_REQUIRED | FNVNX_RESV_BACKED, "guest" },
    { 0x20000000ull, 0x21000000ull, FNVNX_RESV_VIRTMEM, "later" },
};
#define NR 4

static FnvnxResvOps ops_for(Space *s) { return (FnvnxResvOps){ s, sp_query, sp_map, sp_unmap, sp_virt }; }

static void test_all_free(void) {
    Space s;
    sp_init(&s);
    sp_add(&s, 0x08000000ull, 0x200000, FNVNX_MEMTYPE_CODE);   /* host NSO elsewhere */
    sp_add(&s, 0x30000000ull, 0x100000, FNVNX_MEMTYPE_STACK);  /* main stack elsewhere */
    sp_add(&s, 0x31000000ull, 0x1000, FNVNX_MEMTYPE_THREADLOCAL);
    FnvnxResvOps ops = ops_for(&s);
    FnvnxResvResult res[NR];
    uint64_t used = 0;
    CHECK(fnvnx_resv_claim(&ops, k_ranges, NR, s.heap_lo, s.heap_hi, &used, res) == 0);
    uint64_t text = FNVNX_RESV_WINDOW_LO - FNVNX_RESV_IMAGE_LO, win = FNVNX_RESV_WINDOW_HI - FNVNX_RESV_WINDOW_LO;
    CHECK(used == text + win + (64ull << 20));
    CHECK(res[0].state == FNVNX_RESV_CLAIMED && res[0].backing == s.heap_hi - text);
    CHECK(res[1].state == FNVNX_RESV_CLAIMED && res[1].backing == s.heap_hi - text - win);
    CHECK(res[2].state == FNVNX_RESV_CLAIMED && res[2].backing == s.heap_hi - used);
    CHECK(res[3].state == FNVNX_RESV_PENDING && s.map_calls == 3);
    CHECK(!sp_free(&s, FNVNX_RESV_WINDOW_LO, win)); /* now mapped: later placements cannot land there */
    CHECK(fnvnx_resv_contains(k_ranges, NR, res, 0x01000000ull, 0x1000));
    CHECK(fnvnx_resv_contains(k_ranges, NR, res, FNVNX_RESV_WINDOW_LO, win));
    CHECK(!fnvnx_resv_contains(k_ranges, NR, res, FNVNX_RESV_WINDOW_HI - 0x1000, 0x2000)); /* straddles */
    CHECK(!fnvnx_resv_contains(k_ranges, NR, res, 0x20000000ull, 0x1000));                 /* virtmem only */
    CHECK(!fnvnx_resv_contains(k_ranges, NR, res, 0xFFFFF000ull, 0x2000));
    /* late phase: virtmem reservation */
    CHECK(fnvnx_resv_late(&ops, k_ranges, NR, res) == 0);
    CHECK(res[3].state == FNVNX_RESV_RESERVED && s.virt_calls == 1 && res[3].virtmem);
    uint32_t col = 9;
    CHECK(fnvnx_resv_unusable(k_ranges, NR, res, &col) == 0 && col == 0);
    /* release: the heap pages come back, the range is free again */
    uint64_t back = 0;
    CHECK(fnvnx_resv_release(&ops, &k_ranges[2], &res[2], &back) == 0 && back == s.heap_hi - used);
    CHECK(res[2].state == FNVNX_RESV_RELEASED && sp_free(&s, FNVNX_RESV_GUEST_BASE, 64ull << 20));
    CHECK(fnvnx_resv_release(&ops, &k_ranges[2], &res[2], &back) != 0); /* not claimed any more */
    char buf[2048];
    size_t n = fnvnx_resv_format(k_ranges, NR, res, buf, sizeof buf);
    CHECK(n > 0 && strstr(buf, "data-window") && strstr(buf, "claimed") && strstr(buf, "released") &&
          strstr(buf, "reserved"));
    CHECK(fnvnx_resv_format(k_ranges, NR, res, buf, 10) < 10);
    /* nothing to cure: no relaunch */
    FnvnxRelaunchRecord next;
    CHECK(fnvnx_relaunch_decide(NULL, 8, k_ranges, NR, res, &next) == 0);
}

static void test_collisions(void) {
    Space s;
    sp_init(&s);
    sp_add(&s, 0x01400000ull, 0x1000, FNVNX_MEMTYPE_THREADLOCAL); /* TLS page in the data window */
    sp_add(&s, 0x00800000ull, 0x400000, FNVNX_MEMTYPE_CODE);      /* host NSO in the .text range */
    sp_add(&s, 0x00c00000ull, 0x10000, FNVNX_MEMTYPE_CODE_DATA);
    FnvnxResvOps ops = ops_for(&s);
    FnvnxResvResult res[NR];
    uint64_t used = 0;
    CHECK(fnvnx_resv_claim(&ops, k_ranges, NR, s.heap_lo, s.heap_hi, &used, res) == 1);
    CHECK(res[0].state == FNVNX_RESV_COLLISION && res[0].n_conflicts == 2);
    CHECK(res[0].conflict[0].type == FNVNX_MEMTYPE_CODE && res[0].conflict[1].addr == 0x00c00000ull);
    CHECK(res[1].state == FNVNX_RESV_COLLISION && res[1].n_conflicts == 1 &&
          res[1].conflict[0].addr == 0x01400000ull && res[1].conflict[0].type == FNVNX_MEMTYPE_THREADLOCAL);
    CHECK(res[2].state == FNVNX_RESV_CLAIMED && used == (64ull << 20));
    uint32_t col = 0;
    CHECK(fnvnx_resv_unusable(k_ranges, NR, res, &col) == 1 && col == 1); /* the optional range does not count */
    char buf[2048];
    fnvnx_resv_format(k_ranges, NR, res, buf, sizeof buf);
    CHECK(strstr(buf, "COLLISION") && strstr(buf, "TLS page") && strstr(buf, "NSO code"));

    /* relaunch: first launch has no record */
    FnvnxRelaunchRecord prev = fnvnx_relaunch_decode(NULL, 0), next;
    CHECK(prev.attempts == 0);
    CHECK(fnvnx_relaunch_decide(&prev, 8, k_ranges, NR, res, &next) == 1);
    CHECK(next.magic == FNVNX_RELAUNCH_MAGIC && next.attempts == 1 && next.last_addr == 0x01400000ull &&
          next.last_type == FNVNX_MEMTYPE_THREADLOCAL && next.last_range == FNVNX_RESV_WINDOW_LO);
    /* the record survives the trip through the launch parameter, and counts up to the limit */
    unsigned char wire[sizeof next];
    memcpy(wire, &next, sizeof wire);
    prev = fnvnx_relaunch_decode(wire, sizeof wire);
    CHECK(prev.attempts == 1);
    for (int i = 0; i < 7; i++) {
        CHECK(fnvnx_relaunch_decide(&prev, 8, k_ranges, NR, res, &next) == 1);
        prev = next;
    }
    CHECK(prev.attempts == 8 && fnvnx_relaunch_decide(&prev, 8, k_ranges, NR, res, &next) == 0);
    /* foreign or damaged launch parameters count as a first launch */
    wire[0] ^= 1;
    CHECK(fnvnx_relaunch_decode(wire, sizeof wire).attempts == 0);
    CHECK(fnvnx_relaunch_decode(wire, 4).attempts == 0);
}

static void test_failures(void) {
    Space s;
    sp_init(&s);
    FnvnxResvOps ops = ops_for(&s);
    FnvnxResvResult res[NR];
    uint64_t used = 0;
    /* heap too small for everything: the first ranges get backing, the guest does not */
    uint64_t lo = s.heap_hi - (40ull << 20);
    CHECK(fnvnx_resv_claim(&ops, k_ranges, NR, lo, s.heap_hi, &used, res) == 1);
    CHECK(res[0].state == FNVNX_RESV_CLAIMED && res[1].state == FNVNX_RESV_CLAIMED);
    CHECK(res[2].state == FNVNX_RESV_FAILED && res[2].rc == FNVNX_RESV_RC_NO_HEAP);
    FnvnxRelaunchRecord next;
    CHECK(fnvnx_relaunch_decide(NULL, 8, k_ranges, NR, res, &next) == 0); /* relaunching would not help */

    /* the kernel refuses the mapping */
    sp_init(&s);
    s.fail_map_rc = 0xE401;
    CHECK(fnvnx_resv_claim(&ops, k_ranges, NR, s.heap_lo, s.heap_hi, &used, res) == 2);
    CHECK(res[1].state == FNVNX_RESV_FAILED && res[1].rc == 0xE401 && used == 0);

    /* svcQueryMemory failing counts as occupied */
    sp_init(&s);
    s.query_broken_at = 0;
    CHECK(fnvnx_resv_claim(&ops, k_ranges, 2, s.heap_lo, s.heap_hi, &used, res) == 1);
    CHECK(res[1].state == FNVNX_RESV_COLLISION && res[1].conflict[0].type == 0xFFu);

    /* misaligned range */
    sp_init(&s);
    const FnvnxResvRange bad = { 0x00400800ull, 0x00500000ull, FNVNX_RESV_REQUIRED | FNVNX_RESV_BACKED, "bad" };
    CHECK(fnvnx_resv_claim(&ops, &bad, 1, s.heap_lo, s.heap_hi, &used, res) == 1 && res[0].state == FNVNX_RESV_FAILED);

    /* something mapped between the early and the late phase (libnx service mapping) */
    sp_init(&s);
    const FnvnxResvRange vm[1] = { { 0x20000000ull, 0x21000000ull, FNVNX_RESV_REQUIRED | FNVNX_RESV_VIRTMEM, "vm" } };
    CHECK(fnvnx_resv_claim(&ops, vm, 1, s.heap_lo, s.heap_hi, &used, res) == 0 && res[0].state == FNVNX_RESV_PENDING);
    sp_add(&s, 0x20800000ull, 0x4000, FNVNX_MEMTYPE_SHARED);
    CHECK(fnvnx_resv_late(&ops, vm, 1, res) == 1 && res[0].state == FNVNX_RESV_COLLISION);
    FnvnxResvOps nov = ops;
    nov.virtmem_reserve = NULL;
    sp_remove(&s, 0x20800000ull, 0x4000);
    memset(res, 0, sizeof res[0]);
    CHECK(fnvnx_resv_late(&nov, vm, 1, res) == 1 && res[0].rc == FNVNX_RESV_RC_NO_VIRTMEM);
}

static void test_address_space_name(void) {
    CHECK(strcmp(fnvnx_resv_address_space_name(0x200000, 0x40000000, 0), "32BitNoReserved") == 0);
    CHECK(strcmp(fnvnx_resv_address_space_name(0x200000, 0x40000000, 0x40000000), "32Bit") == 0);
    CHECK(strcmp(fnvnx_resv_address_space_name(0x8000000, 0x80000000, 0x180000000ull), "64Bit36") == 0);
    CHECK(strcmp(fnvnx_resv_address_space_name(0x7100000000ull, 0x7180000000ull, 1), "64Bit39") == 0);
    CHECK(strcmp(fnvnx_resv_type_name(FNVNX_MEMTYPE_THREADLOCAL), "TLS page") == 0);
}

/* ---- placement model ----------------------------------------------------------------------------------- */
static uint64_t g_rng = 0x9E3779B97F4A7C15ull;
static uint64_t rnd(uint64_t n) { /* uniform in [0, n] */
    g_rng ^= g_rng << 13;
    g_rng ^= g_rng >> 7;
    g_rng ^= g_rng << 17;
    return n ? g_rng % (n + 1) : 0;
}

/* FindFreeArea in [lo, hi) with ASLR: 8 random page-aligned candidates in a free block with guard pages on
 * both sides, else first fit from a random offset (single-block model is enough for 1-3 objects). */
static uint64_t find_free(Space *s, uint64_t lo, uint64_t hi, uint64_t size, uint64_t guard) {
    for (int i = 0; i < 8; i++) {
        uint64_t c = lo + rnd((hi - lo - size - guard) / 0x1000) * 0x1000;
        if (c >= lo + guard && sp_free(s, c - guard, size + 2 * guard)) return c;
    }
    for (uint64_t c = lo + guard + rnd((hi - lo - size - guard) / 0x1000) * 0x1000; c + size + guard <= hi; c += 0x1000)
        if (sp_free(s, c - guard, size + 2 * guard)) return c;
    return 0;
}

static int hits(uint64_t a, uint64_t size, const FnvnxResvRange *r, uint32_t n) {
    for (uint32_t i = 0; i < n; i++)
        if ((r[i].flags & FNVNX_RESV_REQUIRED) && a < r[i].end && r[i].start < a + size) return 1;
    return 0;
}

/* One launch: ldr maps the NSO (2 MiB-aligned slide over the small map), the kernel maps the process-local
 * TLS page (shared by the main thread) and the main stack with FindFreeArea, 4 guard pages. */
static double model(const FnvnxResvRange *r, uint32_t n, uint64_t nso_size, uint64_t stack_size, long trials,
                    double *analytic) {
    const uint64_t lo = FNVNX_RESV_SMALL_LO, hi = FNVNX_RESV_SMALL_HI, guard = 4 * 0x1000;
    long bad = 0;
    for (long t = 0; t < trials; t++) {
        Space s;
        memset(&s, 0, sizeof s);
        s.query_broken_at = -1;
        uint64_t nso = lo + rnd((hi - lo - nso_size) / 0x200000) * 0x200000;
        sp_add(&s, nso, nso_size, FNVNX_MEMTYPE_CODE);
        uint64_t tls = find_free(&s, lo, hi, 0x1000, guard);
        sp_add(&s, tls, 0x1000, FNVNX_MEMTYPE_THREADLOCAL);
        uint64_t stack = find_free(&s, lo, hi, stack_size, guard);
        sp_add(&s, stack, stack_size, FNVNX_MEMTYPE_STACK);
        if (hits(nso, nso_size, r, n) || hits(tls, 0x1000, r, n) || hits(stack, stack_size, r, n)) bad++;
    }
    double m = (double)(hi - lo), a = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (!(r[i].flags & FNVNX_RESV_REQUIRED)) continue;
        double len = (double)(r[i].end - r[i].start);
        a += (len + (double)nso_size) / m + (len + 4096.0) / m + (len + (double)stack_size) / m;
    }
    *analytic = a;
    return (double)bad / (double)trials;
}

static void report_model(long trials) {
    const uint64_t MiB = 1ull << 20;
    const FnvnxResvRange window = { FNVNX_RESV_WINDOW_LO, FNVNX_RESV_WINDOW_HI, FNVNX_RESV_REQUIRED, "window" };
    const FnvnxResvRange image = { FNVNX_RESV_IMAGE_LO, FNVNX_RESV_WINDOW_HI, FNVNX_RESV_REQUIRED, "image" };
    const FnvnxResvRange guest64 = { FNVNX_RESV_GUEST_BASE, FNVNX_RESV_GUEST_BASE + 64 * MiB, FNVNX_RESV_REQUIRED, "g" };
    const FnvnxResvRange guest40 = { FNVNX_RESV_GUEST_BASE, FNVNX_RESV_GUEST_BASE + 40 * MiB, FNVNX_RESV_REQUIRED, "g" };
    const FnvnxResvRange adj40 = { FNVNX_RESV_WINDOW_HI, FNVNX_RESV_WINDOW_HI + 40 * MiB, FNVNX_RESV_REQUIRED, "g" };
    struct {
        const char *what;
        FnvnxResvRange r[2];
        uint32_t n;
        uint64_t nso;
    } cfg[] = {
        { "data window only, host NSO 2 MiB", { window }, 1, 2 * MiB },
        { "data window only, host NSO 32 MiB", { window }, 1, 32 * MiB },
        { "data window + guest 64 MiB @0x10000000, NSO 2 MiB", { window, guest64 }, 2, 2 * MiB },
        { "data window + guest 64 MiB @0x10000000, NSO 32 MiB", { window, guest64 }, 2, 32 * MiB },
        { "data window + guest 40 MiB @0x10000000, NSO 32 MiB", { window, guest40 }, 2, 32 * MiB },
        { "data window + guest 40 MiB @0x02100000, NSO 32 MiB", { window, adj40 }, 2, 32 * MiB },
        { "whole image range required + guest 64 MiB, NSO 32 MiB", { image, guest64 }, 2, 32 * MiB },
    };
    printf("relaunch model (%ld launches each; NSO slide 2 MiB-aligned, TLS page + 1 MiB main stack by "
           "FindFreeArea):\n", trials);
    for (size_t i = 0; i < sizeof cfg / sizeof cfg[0]; i++) {
        double an = 0, p = model(cfg[i].r, cfg[i].n, cfg[i].nso, MiB, trials, &an);
        double p9 = 1;
        for (int k = 0; k < 9; k++) p9 *= p;
        printf("  %-56s P(relaunch) %5.2f %% (first-order %5.2f %%), launches %.3f, 8 relaunches fail %.1e\n",
               cfg[i].what, 100 * p, 100 * an, 1 / (1 - p), p9);
        CHECK(p > 0 && p < 0.5 && p <= an * 1.1 && p >= an * 0.5); /* first order = union bound */
    }
}

int main(int argc, char **argv) {
    long trials = 20000;
    for (int i = 1; i + 1 < argc; i++)
        if (!strcmp(argv[i], "--trials")) trials = atol(argv[i + 1]);
    test_all_free();
    test_collisions();
    test_failures();
    test_address_space_name();
    report_model(trials);
    printf("test_reserve: %d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
