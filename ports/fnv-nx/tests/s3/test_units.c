/* Unit tests of the parts of the loader that do not need an image: S2 address-map import and the adapter for MSVC
 * deleting destructors (S2 kind G) called with real (host) function pointers. */
#include "fnvnx_loader.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fails;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static void *t_alloc(void *ctx, uint32_t n) { (void)ctx; return malloc(n); }
static void t_free(void *ctx, void *p) { (void)ctx; free(p); }

/* a fake polymorphic object with an Itanium-shaped vtable: [0] f, [1] D1, [2] D0 */
static int d0_calls, d1_calls, other_calls, trap_calls;
static void f_other(void *self) { (void)self; other_calls++; }
static void f_d1(void *self) { (void)self; d1_calls++; }
static void f_d0(void *self) { (void)self; d0_calls++; }
static void f_d0b(void *self) { (void)self; d0_calls += 100; }
static int d1c_calls, d0c_calls;
static void f_d1c(void *self) { (void)self; d1c_calls++; }
static void f_d0c(void *self) { (void)self; d0c_calls++; }
static void f_d0d(void *self) { (void)self; }
static void f_d0e(void *self) { (void)self; }
static void *vtbl[4];   /* [3] = 0 stands for what follows a vtable in .rodata (next offset-to-top) */
static struct { void **vptr; } obj = {vtbl};
static uint32_t trapped_orig, reported;
static void report(uint32_t orig) { reported = orig; }
static void trap(uint32_t orig, void *self, unsigned flags) { (void)self; (void)flags; trap_calls++; trapped_orig = orig; }

int main(void) {
    fnvnx_os os;
    fnvnx_codemap map;
    const fnvnx_codemap_entry *e;
    memset(&os, 0, sizeof os);
    os.alloc = t_alloc; os.free = t_free;

    /* S2 map + extra entries */
    {
        static const fnvnx_s2_addrmap_entry s2[] = {
            {0x00401000u, (void *)(uintptr_t)0x1000}, {0x00402000u, (void *)(uintptr_t)0x2000},
            {0x00403000u, (void *)(uintptr_t)0x3000}};
        static const char kinds[] = {'F', 'G', 'X'};
        static const fnvnx_codemap_entry extra[] = {
            {0x00400800u, 0, FNVNX_AM_TEXT}, {0x00402000u, 0, FNVNX_AM_TEXT}, {0x00402008u, 0, FNVNX_AM_MID}};
        CHECK(fnvnx_codemap_from_s2(&map, &os, s2, 3, kinds, extra, 3) == FNVNX_OK);
        CHECK(map.n == 5);
        e = fnvnx_codemap_find(&map, 0x00402000u);
        CHECK(e && e->native == 0x2000 && FNVNX_AM_KIND(e->info) == FNVNX_AM_FUNC && (e->info & FNVNX_AM_F_DELDTOR));
        e = fnvnx_codemap_find(&map, 0x00403000u);
        CHECK(e && !(e->info & FNVNX_AM_F_DELDTOR));
        e = fnvnx_codemap_find(&map, 0x00402008u);
        CHECK(e && FNVNX_AM_KIND(e->info) == FNVNX_AM_MID);
        CHECK(fnvnx_codemap_find(&map, 0x00400800u) != NULL && fnvnx_codemap_find(&map, 0x00401004u) == NULL);
        free((void *)map.e);
        /* V (image vftable) and E (text without definition) kinds */
        {
            static const fnvnx_s2_addrmap_entry s2v[] = {{0x00401000u, (void *)(uintptr_t)0x1000}, {0x00ec1000u, NULL},
                                                         {0x01000100u, (void *)(uintptr_t)0x5008}};
            CHECK(fnvnx_codemap_from_s2(&map, &os, s2v, 3, "FEV", NULL, 0) == FNVNX_OK && map.n == 3);
            e = fnvnx_codemap_find(&map, 0x01000100u);
            CHECK(e && FNVNX_AM_KIND(e->info) == FNVNX_AM_VTABLE && e->native == 0x5008);
            e = fnvnx_codemap_find(&map, 0x00ec1000u);
            CHECK(e && FNVNX_AM_KIND(e->info) == FNVNX_AM_TEXT && e->native == 0);
            free((void *)map.e);
        }
        /* unsorted input is refused */
        {
            static const fnvnx_s2_addrmap_entry bad[] = {{0x2000u, NULL}, {0x1000u, NULL}};
            CHECK(fnvnx_codemap_from_s2(&map, &os, bad, 2, NULL, NULL, 0) == FNVNX_E_FORMAT && map.e == NULL);
        }
    }

    /* deleting-destructor adapter */
    {
        fnvnx_deldtor_fn_t a, b, a2;
        vtbl[0] = (void *)(uintptr_t)f_other;
        vtbl[1] = (void *)(uintptr_t)f_d1;
        vtbl[2] = (void *)(uintptr_t)f_d0;
        fnvnx_deldtor_trap = trap;
        {
            uintptr_t f[3] = {(uintptr_t)f_other, (uintptr_t)f_d1, (uintptr_t)f_d0}, lo = f[0], hi = f[0];
            int k;
            for (k = 1; k < 3; k++) { if (f[k] < lo) lo = f[k]; if (f[k] > hi) hi = f[k]; }
            fnvnx_deldtor_set_text((const void *)lo, (const void *)(hi + 1));
        }
        a = fnvnx_deldtor_fn(f_d0, 0x00402000u);
        b = fnvnx_deldtor_fn(f_d0b, 0x00402100u);
        a2 = fnvnx_deldtor_fn(f_d0, 0x00402000u);
        CHECK(a && b && a != b && a == a2);                   /* one adapter per D0 */
        CHECK(a(&obj, 1) == &obj && d0_calls == 1 && d1_calls == 0);    /* delete: D0 */
        CHECK(a(&obj, 0) == &obj && d0_calls == 1 && d1_calls == 1);    /* destroy only: D1 from the vtable */
        CHECK(other_calls == 0 && trap_calls == 0);
        a(&obj, 3);                                                    /* vector delete: trap */
        CHECK(trap_calls == 1 && trapped_orig == 0x00402000u);
        b(&obj, 0);                                                    /* D0 not in this object's vtable */
        CHECK(trap_calls == 2 && trapped_orig == 0x00402100u && d1_calls == 1);
        b(&obj, 1);
        CHECK(d0_calls == 101);
        CHECK(fnvnx_deldtor_fn(NULL, 0) == NULL);
    }

    /* with S2's D1/D0 table (fnvnx_addrmap_deldtors): D1 comes from the table, not from the object's vtable */
    {
        static fnvnx_s2_deldtor tab[2];
        fnvnx_deldtor_fn_t c, d, e2;
        int traps0 = trap_calls;
        tab[0].orig = 0x00402200u; tab[0].d1 = (void *)(uintptr_t)f_d1c; tab[0].d0 = (void *)(uintptr_t)f_d0c;
        tab[1].orig = 0x00402300u; tab[1].d1 = NULL; tab[1].d0 = (void *)(uintptr_t)f_d0d;
        fnvnx_deldtor_set_table(tab, 2);
        c = fnvnx_deldtor_fn(f_d0c, 0x00402200u);
        CHECK(c && c(&obj, 0) == &obj && d1c_calls == 1 && d1_calls == 1);   /* obj's vtable does not hold f_d0c */
        CHECK(c(&obj, 1) == &obj && d0c_calls == 1);
        d = fnvnx_deldtor_fn(f_d0d, 0x00402300u);                             /* no D1 in the table: trap */
        d(&obj, 0);
        CHECK(trap_calls == traps0 + 1 && trapped_orig == 0x00402300u);
        e2 = fnvnx_deldtor_fn(f_d0e, 0x00402400u);                            /* not in the table: trap, no search */
        e2(&obj, 0);
        CHECK(trap_calls == traps0 + 2 && trapped_orig == 0x00402400u && d1_calls == 1);
        c(&obj, 2);                                                           /* vector delete: trap */
        CHECK(trap_calls == traps0 + 3);
    }

    /* apply: kind G goes through the deldtor hook, never straight to D0 */
    {
        fnvnx_apply_stats st;
        fnvnx_apply_hooks hk;
        fnvnx_image img;
        fnvnx_pe pe;
        fnvnx_fixup fx[2];
        fnvnx_codemap_entry ents[2] = {{0x00401000u, 0x1000, FNVNX_AM_FUNC},
                                       {0x00402000u, 0x2000, FNVNX_AM_FUNC | FNVNX_AM_F_DELDTOR}};
        static uint32_t page[1024];
        memset(&pe, 0, sizeof pe);
        memset(&img, 0, sizeof img);
        pe.image_base = 0x00400000u;
        img.pe = &pe;
        img.load_base = 0x00400000u;
        img.bias = (uintptr_t)page - 0x01000000u;
        img.nmapped = 1;
        img.map[0].va = 0x01000000u; img.map[0].size = 0x1000; img.map[0].sec = 0;
        page[0] = 0x00401000u; page[1] = 0x00402000u;
        map.e = ents; map.n = 2;
        memset(fx, 0, sizeof fx);
        fx[0].loc = 0x01000000u; fx[0].target = 0x00401000u; fx[0].klass = FNVNX_FX_VTABLE_COL; fx[0].conf = 3;
        fx[1].loc = 0x01000004u; fx[1].target = 0x00402000u; fx[1].klass = FNVNX_FX_VTABLE_COL; fx[1].conf = 3;
        CHECK(fnvnx_fixups_apply(&img, &map, fx, 2, 1, NULL, &st) == FNVNX_OK);
        CHECK(page[0] == 0x1000 && page[1] == 0x00402000u && st.applied == 1 && st.unresolved == 1);
        page[0] = 0x00401000u;
        memset(&hk, 0, sizeof hk);
        hk.deldtor = fnvnx_deldtor_adapter;
        fnvnx_fixups_apply(&img, &map, fx, 2, 1, &hk, &st);
        if ((uintptr_t)fnvnx_deldtor_fn((void (*)(void *))(uintptr_t)0x2000, 0x00402000u) <= 0xffffffffu)
            CHECK(st.adapted == 1 && page[1] != 0x00402000u);
        else CHECK(st.adapted == 0 && st.unresolved == 1);   /* a host adapter address does not fit 32 bits */
    }
    /* trap stubs report the original address they stand for */
    {
        fnvnx_trap_fn_t a = fnvnx_trap_fn(0x00ec1240u), b = fnvnx_trap_fn(0x00ec1250u);
        CHECK(a && b && a != b && fnvnx_trap_fn(0x00ec1240u) == a);
        fnvnx_trap_report = report;
        b();
        CHECK(reported == 0x00ec1250u);
        a();
        CHECK(reported == 0x00ec1240u);
    }
    printf("test_units: %s\n", fails ? "FAILED" : "ok");
    return fails ? 1 : 0;
}
