/* Host test of loader/src/prelink.c with synthetic data.
 * Build and run from ports/fnv-nx:
 * cc -std=c99 -Wall -Wextra -Werror -g -fsanitize=address,undefined -Iloader/include -Iloader/src tests/loader/test_prelink.c loader/src/prelink.c loader/src/sha256.c -o /tmp/test_prelink && /tmp/test_prelink
 */
#include "fnvnx_prelink.h"
#include "fnvnx_loader.h"
#include <stdio.h>
#include <string.h>

#define BASE 0x400000u
#define IMG_SIZE 64u
#define TLO 0x401000u
#define THI 0x402000u
#define NAT 0x7000000u

static uint8_t img[IMG_SIZE];
static int fails;

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static uint8_t *at(void *ctx, uint32_t va, uint32_t size) {
    (void)ctx;
    if (va < BASE || va - BASE > IMG_SIZE || size > IMG_SIZE - (va - BASE)) return NULL;
    return img + (va - BASE);
}
static void put(uint32_t off, uint32_t v) {
    for (int i = 0; i < 4; i++) img[off + (uint32_t)i] = (uint8_t)(v >> (8 * i));
}
static uint32_t get(uint32_t off) {
    return (uint32_t)img[off] | ((uint32_t)img[off + 1] << 8) | ((uint32_t)img[off + 2] << 16) | ((uint32_t)img[off + 3] << 24);
}
static void reset(void) {
    memset(img, 0xEE, sizeof img);
    put(0, TLO + 0x10);
    put(4, TLO + 0x20);
    put(9, TLO + 0x30); /* unaligned */
    put(16, TLO + 0x40);
}

static void test_ok(void) {
    struct fnvnx_ptr_rec t[] = {{BASE + 0, NAT}, {BASE + 4, NAT + 8}, {BASE + 9, NAT + 16}, {BASE + 16, NAT + 24}, {0, 0}};
    uint32_t n = 99, bad = 99;
    reset();
    CHECK(fnvnx_prelink_apply(t, TLO, THI, at, NULL, &n, &bad) == 0);
    CHECK(n == 4);
    CHECK(get(0) == NAT && get(4) == NAT + 8 && get(9) == NAT + 16 && get(16) == NAT + 24);
    CHECK(img[8] == 0xEE && img[13] == 0xEE);
    /* applying twice: first word no longer code */
    n = 99;
    CHECK(fnvnx_prelink_apply(t, TLO, THI, at, NULL, &n, &bad) == FNVNX_PRELINK_NOT_CODE);
    CHECK(bad == 0);
    /* NULL outputs accepted */
    reset();
    CHECK(fnvnx_prelink_apply(t, TLO, THI, at, NULL, NULL, NULL) == 0);
}

static void test_empty(void) {
    struct fnvnx_ptr_rec t[] = {{0, 0}};
    uint32_t n = 99, bad = 99;
    reset();
    CHECK(fnvnx_prelink_apply(t, TLO, THI, at, NULL, &n, &bad) == 0);
    CHECK(n == 0);
}

static void test_fail(struct fnvnx_ptr_rec *t, int code, uint32_t idx, uint32_t untouched_off) {
    uint32_t n = 99, bad = 99, before;
    reset();
    before = get(untouched_off);
    CHECK(fnvnx_prelink_apply(t, TLO, THI, at, NULL, &n, &bad) == code);
    CHECK(bad == idx);
    CHECK(get(untouched_off) == before);
    CHECK(get(0) == (idx > 0 ? NAT : TLO + 0x10)); /* earlier record applied */
}

static void test_errors(void) {
    struct fnvnx_ptr_rec unm[] = {{BASE, NAT}, {BASE + IMG_SIZE - 2, NAT}, {0, 0}};
    struct fnvnx_ptr_rec unm0[] = {{BASE - 4, NAT}, {0, 0}};
    struct fnvnx_ptr_rec nc[] = {{BASE, NAT}, {BASE + 32, NAT}, {0, 0}}; /* word at 32 is 0xEEEEEEEE */
    struct fnvnx_ptr_rec ord[] = {{BASE, NAT}, {BASE + 4, NAT}, {BASE + 4, NAT}, {0, 0}};
    struct fnvnx_ptr_rec ovl[] = {{BASE, NAT}, {BASE + 3, NAT}, {0, 0}};
    struct fnvnx_ptr_rec dec[] = {{BASE + 16, NAT}, {BASE + 4, NAT}, {0, 0}};
    struct fnvnx_ptr_rec nat0[] = {{BASE, NAT}, {BASE + 4, 0}, {0, 0}};
    struct fnvnx_ptr_rec natc[] = {{BASE, NAT}, {BASE + 4, TLO}, {0, 0}};
    struct fnvnx_ptr_rec natc2[] = {{BASE, NAT}, {BASE + 4, THI - 1}, {0, 0}};
    struct fnvnx_ptr_rec nate[] = {{BASE, NAT}, {BASE + 4, THI}, {0, 0}};
    uint32_t n, bad;

    test_fail(unm, FNVNX_PRELINK_UNMAPPED, 1, 4);
    test_fail(unm0, FNVNX_PRELINK_UNMAPPED, 0, 4);
    test_fail(nc, FNVNX_PRELINK_NOT_CODE, 1, 32);
    test_fail(ovl, FNVNX_PRELINK_ORDER, 1, 4);
    /* descending: record 0 (word at 16) applied, record 1 rejected, word at 4 untouched */
    reset();
    CHECK(fnvnx_prelink_apply(dec, TLO, THI, at, NULL, &n, &bad) == FNVNX_PRELINK_ORDER);
    CHECK(bad == 1 && get(16) == NAT && get(4) == TLO + 0x20);
    test_fail(nat0, FNVNX_PRELINK_BAD_NATIVE, 1, 4);
    test_fail(natc, FNVNX_PRELINK_BAD_NATIVE, 1, 4);
    test_fail(natc2, FNVNX_PRELINK_BAD_NATIVE, 1, 4);
    /* native == text_hi is outside the half-open range: accepted */
    reset();
    CHECK(fnvnx_prelink_apply(nate, TLO, THI, at, NULL, &n, &bad) == 0);
    CHECK(n == 2 && get(4) == THI);
    /* ORDER case: record 1 (BASE+4) was applied before record 2 failed */
    reset();
    CHECK(fnvnx_prelink_apply(ord, TLO, THI, at, NULL, &n, &bad) == FNVNX_PRELINK_ORDER);
    CHECK(bad == 2 && get(4) == NAT);
}

static void test_identity(void) {
    uint8_t file[100], want[32], bad[32];
    size_t i;
    for (i = 0; i < sizeof file; i++) file[i] = (uint8_t)(i * 7 + 1);
    fnvnx_sha256(file, sizeof file, want);
    CHECK(fnvnx_image_identity(file, sizeof file, want) == 0);
    memcpy(bad, want, 32);
    bad[31] ^= 1;
    CHECK(fnvnx_image_identity(file, sizeof file, bad) == FNVNX_PRELINK_IDENTITY);
    file[50] ^= 1;
    CHECK(fnvnx_image_identity(file, sizeof file, want) == FNVNX_PRELINK_IDENTITY);
    file[50] ^= 1;
    CHECK(fnvnx_image_identity(file, sizeof file - 1, want) == FNVNX_PRELINK_IDENTITY);
}

int main(void) {
    test_ok();
    test_empty();
    test_errors();
    test_identity();
    if (fails) { fprintf(stderr, "test_prelink: %d failures\n", fails); return 1; }
    puts("test_prelink: ok");
    return 0;
}
