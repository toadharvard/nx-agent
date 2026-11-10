/* Host test of loader/src/tls.c (rule S5) on a synthetic image held in memory.
 * Build and run from ports/fnv-nx:
 * make -C loader OUT=<out> && cc -std=c99 -Wall -Wextra -Werror -g -fsanitize=address,undefined -Iloader/include -Iloader/src tests/loader/test_tls.c <out>/libfnvnx_loader.a -o <out>/test_tls && <out>/test_tls
 */
#include "fnvnx_tls.h"
#include "fnvnx_loader.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fails;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

#define PREF 0x00400000u
#define SPAN 0x3000u

/* Layout (offsets from the load base): page 1 = ".data" (template, index word, callbacks), page 2 = ".tls dir". */
enum { O_TPL = 0x1100, O_IDX = 0x1200, O_CB = 0x1300, O_DIR = 0x2000, TPL_SIZE = 0x20 };

typedef struct world {
    fnvnx_pe pe;
    fnvnx_image img;
    uint8_t *mem;
    uint32_t base;   /* load base */
} world;

static void w_init(world *w, uint32_t base, int with_dir) {
    uint32_t i;
    memset(w, 0, sizeof *w);
    w->mem = (uint8_t *)calloc(SPAN, 1);
    w->base = base;
    w->pe.image_base = PREF;
    w->pe.ndirs = 16;
    if (with_dir) { w->pe.dir[FNVNX_DIR_TLS].rva = O_DIR; w->pe.dir[FNVNX_DIR_TLS].size = 24; }
    w->img.pe = &w->pe;
    w->img.bias = (uintptr_t)w->mem - (uintptr_t)base;
    w->img.load_base = base;
    w->img.delta = base - PREF;
    w->img.nmapped = 2;
    w->img.map[0].va = base + 0x1000; w->img.map[0].size = 0x1000; w->img.map[0].sec = 0;
    w->img.map[1].va = base + 0x2000; w->img.map[1].size = 0x1000; w->img.map[1].sec = 1;
    for (i = 0; i < TPL_SIZE; i++) w->mem[O_TPL + i] = (uint8_t)(0x40 + i);
}

static void w_free(world *w) { free(w->mem); }

static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

/* directory fields are VAs at the preferred base */
static void w_dir(world *w, uint32_t start, uint32_t end, uint32_t idx, uint32_t cb, uint32_t zf, uint32_t chars) {
    uint8_t *d = w->mem + O_DIR;
    put32(d + 0, start); put32(d + 4, end); put32(d + 8, idx);
    put32(d + 12, cb); put32(d + 16, zf); put32(d + 20, chars);
}

static void w_normal(world *w, uint32_t zf, uint32_t chars) {
    w_dir(w, PREF + O_TPL, PREF + O_TPL + TPL_SIZE, PREF + O_IDX, PREF + O_CB, zf, chars);
}

static void test_none(void) {
    world w;
    fnvnx_tls_layout l;
    w_init(&w, PREF, 0);
    memset(&l, 0xff, sizeof l);
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_OK);
    CHECK(l.present == 0 && l.template_va == 0 && l.template_size == 0 && l.zero_fill == 0 && l.block_size == 0 &&
          l.align == 0 && l.index_va == 0 && l.callbacks_va == 0);
    w_free(&w);
    /* fewer than 10 directories: none */
    w_init(&w, PREF, 1);
    w.pe.ndirs = 9;
    memset(&l, 0xff, sizeof l);
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_OK && l.present == 0 && l.block_size == 0);
    w_free(&w);
}

static void test_normal(uint32_t base) {
    world w;
    fnvnx_tls_layout l;
    uint8_t blk[TPL_SIZE + 0x30 + 16];
    uint32_t i, n;
    static const uint32_t nv[3] = {0, 1, 5};
    static const uint32_t av[3] = {1, 1, 16};
    w_init(&w, base, 1);
    for (n = 0; n < 3; n++) {
        w_normal(&w, 0x30, nv[n] << 20);
        memset(&l, 0, sizeof l);
        CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_OK);
        CHECK(l.present == 1);
        CHECK(l.template_va == base + O_TPL);
        CHECK(l.template_size == TPL_SIZE);
        CHECK(l.zero_fill == 0x30);
        CHECK(l.block_size == TPL_SIZE + 0x30);
        CHECK(l.align == av[n]);
        CHECK(l.index_va == base + O_IDX);
        CHECK(l.callbacks_va == base + O_CB);
    }
    w_normal(&w, 0x30, 0);
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_OK);
    memset(blk, 0xaa, sizeof blk);
    CHECK(fnvnx_tls_block_init(&w.img, &l, blk) == FNVNX_OK);
    for (i = 0; i < TPL_SIZE; i++) CHECK(blk[i] == (uint8_t)(0x40 + i));
    for (i = 0; i < 0x30; i++) CHECK(blk[TPL_SIZE + i] == 0);
    for (i = TPL_SIZE + 0x30; i < sizeof blk; i++) CHECK(blk[i] == 0xaa);
    /* no callbacks: 0 stays 0 */
    w_dir(&w, PREF + O_TPL, PREF + O_TPL + TPL_SIZE, PREF + O_IDX, 0, 0, 0);
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_OK && l.callbacks_va == 0 && l.block_size == TPL_SIZE);
    /* empty template, only zero fill */
    w_dir(&w, PREF + O_TPL, PREF + O_TPL, PREF + O_IDX, 0, 8, 0);
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_OK && l.template_size == 0 && l.block_size == 8);
    memset(blk, 0xaa, sizeof blk);
    CHECK(fnvnx_tls_block_init(&w.img, &l, blk) == FNVNX_OK);
    for (i = 0; i < 8; i++) CHECK(blk[i] == 0);
    CHECK(blk[8] == 0xaa);
    w_free(&w);
}

static void test_errors(void) {
    world w;
    fnvnx_tls_layout l;
    uint8_t blk[8];
    w_init(&w, PREF, 1);

    w_dir(&w, PREF + O_TPL + 8, PREF + O_TPL, PREF + O_IDX, 0, 0, 0);             /* End < Start */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_FORMAT);

    w_dir(&w, PREF + 0x5000, PREF + 0x5010, PREF + O_IDX, 0, 0, 0);               /* template unmapped */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_RANGE);

    w_dir(&w, PREF + 0x1ff0, PREF + 0x2010, PREF + O_IDX, 0, 0, 0);               /* straddles two sections */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_RANGE);

    w_dir(&w, PREF + O_TPL, PREF + O_TPL + TPL_SIZE, PREF + 0x6000, 0, 0, 0);     /* index word unmapped */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_RANGE);
    w_dir(&w, PREF + O_TPL, PREF + O_TPL + TPL_SIZE, 0, 0, 0, 0);                 /* no index word at all */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_RANGE);
    w_dir(&w, PREF + O_TPL, PREF + O_TPL + TPL_SIZE, PREF + 0x1ffe, 0, 0, 0);     /* index word cut by section end */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_RANGE);

    w_normal(&w, 0xfffffff0u, 0);                                                 /* size + zero fill overflows */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_FORMAT);

    w_normal(&w, 0, 0);                                                           /* directory too small */
    w.pe.dir[FNVNX_DIR_TLS].size = 23;
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_FORMAT);
    w.pe.dir[FNVNX_DIR_TLS].size = 24;

    w.pe.dir[FNVNX_DIR_TLS].rva = 0x7000;                                         /* directory itself unmapped */
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_E_RANGE);
    w.pe.dir[FNVNX_DIR_TLS].rva = O_DIR;

    CHECK(fnvnx_tls_layout_read(NULL, &l) == FNVNX_E_ARG);
    CHECK(fnvnx_tls_layout_read(&w.img, NULL) == FNVNX_E_ARG);

    /* block_init: bad arguments and a layout that does not fit the image */
    w_normal(&w, 0, 0);
    CHECK(fnvnx_tls_layout_read(&w.img, &l) == FNVNX_OK);
    CHECK(fnvnx_tls_block_init(&w.img, &l, NULL) == FNVNX_E_ARG);
    CHECK(fnvnx_tls_block_init(NULL, &l, blk) == FNVNX_E_ARG);
    CHECK(fnvnx_tls_block_init(&w.img, NULL, blk) == FNVNX_E_ARG);
    l.template_va = PREF + 0x5000;
    CHECK(fnvnx_tls_block_init(&w.img, &l, blk) == FNVNX_E_RANGE);
    l.template_va = PREF + O_TPL;
    l.block_size = l.template_size - 1;
    CHECK(fnvnx_tls_block_init(&w.img, &l, blk) == FNVNX_E_FORMAT);
    l.block_size = l.template_size;
    l.present = 0;
    CHECK(fnvnx_tls_block_init(&w.img, &l, blk) == FNVNX_E_ARG);
    w_free(&w);
}

int main(void) {
    test_none();
    test_normal(PREF);
    test_normal(0x00500000u);   /* moved image, directory still holds preferred-base VAs */
    test_errors();
    if (fails) { fprintf(stderr, "test_tls: %d failures\n", fails); return 1; }
    puts("ok");
    return 0;
}
