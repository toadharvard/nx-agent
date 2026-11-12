/* tests/s10/test_xc.c - host tests of the guest initializer runner (startup/guest).
 *
 * Synthetic initializers placed at fake original addresses; no game code or data. Build and run through
 * tests/s10/run.sh (ASan/UBSan). */
#include "fnvnx_xc.h"

#include <setjmp.h>
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

/* ---- call log ---------------------------------------------------------------------------------------- */
static char g_log[4096];
static void logc(const char *s) { strncat(g_log, s, sizeof g_log - strlen(g_log) - 1); }
static void reset_log(void) { g_log[0] = 0; }

/* Two "units" whose initializers interleave in address order, as the decomp's generated units do:
 * unit A at 0x00f40010/30/50, unit B at 0x00f40020/40. GCC's per-unit order would be A1 A2 A3 B1 B2. */
static int g_obj_a1, g_obj_b1;
static void a1(void) { logc("a1 "); g_obj_a1 = 7; }
static void b1(void) { logc("b1 "); g_obj_b1 = g_obj_a1 + 1; } /* depends on a1 having run */
static void a2(void) { logc("a2 "); }
static void b2(void) { logc("b2 "); }
static void a3(void) { logc("a3 "); }
static void lib(void) { logc("lib "); }  /* outside the group (e.g. a library __xc entry) */
static void late(void) { logc("late "); }

#define GLO 0x00f34eb0u
#define GHI 0x00fc7a70u

static const struct fnvnx_xc_entry k_table[] = {
    { 0x00a62110u, lib }, { 0x00f40010u, a1 }, { 0x00f40020u, b1 }, { 0x00f40030u, a2 },
    { 0x00f40040u, b2 },  { 0x00f40050u, a3 }, { 0x00f40060u, late },
};
#define K_N ((uint32_t)(sizeof k_table / sizeof k_table[0]))

static void hook_before(void *ctx, uint32_t orig, uint32_t seq) {
    uint32_t *last = ctx;
    if (seq == 0) last[0] = 0;
    last[0]++;
    last[1] = orig;
}

static void test_table_checks(void) {
    int32_t bad = 0;
    CHECK(fnvnx_xc_check_table(k_table, K_N, &bad) == FNVNX_XC_OK && bad == -1);
    struct fnvnx_xc_entry t[3] = { { 0x10, a1 }, { 0x30, a2 }, { 0x20, a3 } };
    CHECK(fnvnx_xc_check_table(t, 3, &bad) == FNVNX_XC_E_UNSORTED && bad == 2);
    t[2].orig = 0x30; /* duplicate */
    CHECK(fnvnx_xc_check_table(t, 3, &bad) == FNVNX_XC_E_UNSORTED && bad == 2);
    t[2].orig = 0x40;
    t[1].fn = NULL;
    CHECK(fnvnx_xc_check_table(t, 3, &bad) == FNVNX_XC_E_UNSORTED && bad == 1);
    CHECK(fnvnx_xc_find(k_table, K_N, 0x00f40040u)->fn == b2);
    CHECK(fnvnx_xc_find(k_table, K_N, 0x00f40041u) == NULL);
    CHECK(fnvnx_xc_find(k_table, K_N, 0x00a62110u)->fn == lib);
    CHECK(fnvnx_xc_find(k_table, 0, 0x10) == NULL);
    /* malformed tables never run anything */
    fnvnx_xc_report rep;
    reset_log();
    t[1].fn = a2;
    t[2].orig = 0x20;
    CHECK(fnvnx_xc_run_address_order(t, 3, 0, 0xFFFFFFFFu, 0, NULL, &rep) == FNVNX_XC_E_UNSORTED);
    CHECK(rep.bad_index == 2 && rep.called == 0 && g_log[0] == 0);
}

static void test_address_order(void) {
    fnvnx_xc_report rep;
    uint32_t seen[2] = { 0, 0 };
    fnvnx_xc_hooks h = { seen, hook_before, NULL };
    reset_log();
    g_obj_a1 = g_obj_b1 = 0;
    CHECK(fnvnx_xc_run_address_order(k_table, K_N, GLO, GHI, 0, &h, &rep) == FNVNX_XC_OK);
    CHECK(strcmp(g_log, "a1 b1 a2 b2 a3 late ") == 0);
    CHECK(g_obj_b1 == 8);
    CHECK(rep.called == 6 && rep.skipped_outside == 1 && rep.mode == FNVNX_XC_MODE_ADDRESS);
    CHECK(seen[0] == 6 && seen[1] == 0x00f40060u);
    reset_log();
    CHECK(fnvnx_xc_run_address_order(k_table, K_N, GLO, GHI, FNVNX_XC_ALL_ENTRIES, NULL, &rep) == FNVNX_XC_OK);
    CHECK(strcmp(g_log, "lib a1 b1 a2 b2 a3 late ") == 0 && rep.called == 7);
}

static void test_image_order(void) {
    fnvnx_xc_report rep;
    /* image table: library entry first, a NULL pad, the game entries with b1/a1 swapped (an object the
     * linker placed differently), an undecompiled initializer, a folded (duplicate) slot; 'late' absent. */
    const uint32_t slots[] = { 0x00a62110u, 0, 0x00f40020u, 0x00f40010u, 0x00f40030u, 0x00f40044u,
                               0x00f40040u, 0x00f40050u, 0x00f40050u, 0 };
    reset_log();
    CHECK(fnvnx_xc_run_image_order(k_table, K_N, slots, 10, GLO, GHI, 0, NULL, &rep) == FNVNX_XC_OK);
    CHECK(strcmp(g_log, "lib b1 a1 a2 b2 a3 a3 ") == 0);
    CHECK(rep.mode == FNVNX_XC_MODE_IMAGE && rep.image_slots == 10 && rep.image_null == 2);
    CHECK(rep.image_missing == 1 && rep.n_missing == 1 && rep.missing_sample[0] == 0x00f40044u);
    CHECK(rep.image_duplicates == 1);
    CHECK(rep.inversions == 1 && rep.inversion_sample[0] == 0x00f40010u); /* a1 after b1 */
    CHECK(rep.not_in_image == 1 && rep.not_in_image_sample[0] == 0x00f40060u && rep.leftovers_called == 0);
    reset_log();
    CHECK(fnvnx_xc_run_image_order(k_table, K_N, slots, 10, GLO, GHI, FNVNX_XC_RUN_LEFTOVERS, NULL, &rep) == 0);
    CHECK(strcmp(g_log, "lib b1 a1 a2 b2 a3 a3 late ") == 0 && rep.leftovers_called == 1);
    /* an image in address order: no inversions (the claim holds) */
    const uint32_t sorted[] = { 0, 0x00f40010u, 0x00f40020u, 0x00f40030u, 0x00f40040u, 0x00f40050u, 0x00f40060u, 0 };
    reset_log();
    CHECK(fnvnx_xc_run_image_order(k_table, K_N, sorted, 8, GLO, GHI, 0, NULL, &rep) == 0);
    CHECK(rep.inversions == 0 && rep.not_in_image == 0 && rep.image_missing == 0 && rep.called == 6);
    CHECK(strcmp(g_log, "a1 b1 a2 b2 a3 late ") == 0);
    char buf[1024];
    size_t n = fnvnx_xc_format_report(&rep, buf, sizeof buf);
    CHECK(n > 0 && strstr(buf, "image order") && strstr(buf, "inversions vs address order: 0"));
    CHECK(fnvnx_xc_format_report(&rep, buf, 8) <= 7 && strlen(buf) <= 7);
}

static void test_locate(void) {
    /* Synthetic .rdata: strings, a vftable-like run (text pointers, no group words), a decoy run with one
     * group word, then __xc_a NULL, two library entries, the game entries with an inner NULL pad, __xc_z,
     * then __xi (CRT pointers) and data. */
    const uint32_t TLO = 0x00401000u, THI = 0x00fdf000u;
    uint32_t mem[64];
    uint32_t k = 0;
    mem[k++] = 0x6c6c6548; mem[k++] = 0x0000006f;                 /* "Hello" */
    mem[k++] = 0x00a00010; mem[k++] = 0x00a00020; mem[k++] = 0x00a00030; /* vftable */
    mem[k++] = 0x01100000;                                         /* data pointer: ends the run */
    mem[k++] = 0x00b00000; mem[k++] = 0x00f40100; mem[k++] = 0x00b00010; /* decoy: 1 group word */
    mem[k++] = 0x3f800000;                                         /* 1.0f */
    uint32_t xc_a = k;
    mem[k++] = 0;                                                  /* __xc_a */
    mem[k++] = 0x00ec3246; mem[k++] = 0x00ec3289;                  /* libcpmt initializers (.text) */
    for (uint32_t i = 0; i < 6; i++) mem[k++] = 0x00f40010u + 0x10u * i;
    mem[k++] = 0;                                                  /* linker pad */
    for (uint32_t i = 0; i < 4; i++) mem[k++] = 0x00f41000u + 0x10u * i;
    uint32_t xc_z = k;
    mem[k++] = 0;                                                  /* __xc_z */
    mem[k++] = 0;                                                  /* __xi_a */
    mem[k++] = 0x00ec5000; mem[k++] = 0x00ec5100;                  /* CRT C initializers */
    mem[k++] = 0;                                                  /* __xi_z */
    mem[k++] = 0x01200000;
    uint32_t va = 0x01000000u, b = 0, e = 0, hits = 0;
    CHECK(fnvnx_xc_locate(mem, va, k, TLO, THI, GLO, GHI, 4, &b, &e, &hits) == FNVNX_XC_OK);
    CHECK(hits == 10);
    CHECK(b == va + 4 * (xc_a + 1) && e == va + 4 * xc_z);
    const uint32_t span_b = b, span_e = e;
    CHECK(fnvnx_xc_locate(mem, va, k, TLO, THI, GLO, GHI, 11, &b, &e, &hits) == FNVNX_XC_E_NOTFOUND);
    CHECK(fnvnx_xc_locate(mem, va, xc_a, TLO, THI, GLO, GHI, 1, &b, &e, &hits) == FNVNX_XC_OK && hits == 1);
    CHECK(fnvnx_xc_locate(mem, va, 6, TLO, THI, GLO, GHI, 1, &b, &e, &hits) == FNVNX_XC_E_NOTFOUND);
    CHECK(fnvnx_xc_locate(mem, va, k, GLO, THI, TLO, GHI, 1, &b, &e, &hits) == FNVNX_XC_E_ARG);
    /* the located span drives image mode: library entries and the new 0x00f41000.. block are missing */
    fnvnx_xc_report rep;
    reset_log();
    uint32_t n = (span_e - span_b) / 4;
    CHECK(fnvnx_xc_run_image_order(k_table, K_N, mem + (span_b - va) / 4, n, GLO, GHI, 0, NULL, &rep) == 0);
    CHECK(rep.image_slots == 13 && rep.image_null == 1 && rep.image_missing == 6 && rep.called == 6);
    CHECK(strcmp(g_log, "a1 b1 a2 b2 a3 late ") == 0);
    /* __xi after __xc_z (loader-seam audit): past __xc_z and __xi_a, the two CRT C initializers, up to __xi_z */
    uint32_t xb = 0, xe = 0;
    CHECK(fnvnx_xi_locate(mem, va, k, span_e, TLO, THI, &xb, &xe) == FNVNX_XC_OK);
    CHECK(xb == va + 4 * (xc_z + 2) && xe == xb + 8);
    CHECK(mem[(xb - va) / 4] == 0x00ec5000 && mem[(xe - va) / 4] == 0);
    CHECK(fnvnx_xi_locate(mem, va, xc_z + 3, span_e, TLO, THI, &xb, &xe) == FNVNX_XC_E_NOTFOUND);  /* no __xi_z */
    CHECK(fnvnx_xi_locate(mem, va, k, va + 4 * k, TLO, THI, &xb, &xe) == FNVNX_XC_E_NOTFOUND);
    CHECK(fnvnx_xi_locate(mem, va, k, va + 2, TLO, THI, &xb, &xe) == FNVNX_XC_E_ARG);
    {   /* an empty table: __xc_z, __xi_a, __xi_z */
        uint32_t m2[6] = {0x00f40010u, 0, 0, 0, 0, 0x01200000u};
        CHECK(fnvnx_xi_locate(m2, va, 6, va + 4, TLO, THI, &xb, &xe) == FNVNX_XC_OK && xb == xe);
    }
}

static void test_generated_entry_without_table(void) {
    /* this binary links no xctab.c: the weak table is empty */
    fnvnx_xc_report rep;
    CHECK(fnvnx_xc_run_initializers(NULL, 0, 0, NULL, &rep) == 0 && rep.called == 0 && rep.table_entries == 0);
    const uint32_t slots[] = { 0x00f40010u };
    CHECK(fnvnx_xc_run_initializers(slots, 1, 0, NULL, &rep) == 0 && rep.image_missing == 1);
}

int main(void) {
    test_table_checks();
    test_address_order();
    test_image_order();
    test_locate();
    test_generated_entry_without_table();
    printf("test_xc: %d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
