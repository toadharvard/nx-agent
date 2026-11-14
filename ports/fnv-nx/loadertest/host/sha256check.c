/* sha256check: the ARMv8 SHA-256 implementation of src/sha256.c (built with -march=armv8-a+crypto, __ARM_FEATURE_SHA2) against the
   NIST vectors, and against the portable implementation (the same source built without it, renamed fnvnx_sha256_portable) on
   1,000 pseudo-random lengths 0..4096 (fixed LCG).  Run under qemu-aarch64 by `make sha256check`. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

void fnvnx_sha256(const void *data, size_t size, uint8_t out[32]);
void fnvnx_sha256_portable(const void *data, size_t size, uint8_t out[32]);

static int fails;

static void hex(const uint8_t d[32], char s[65]) {
    int i;
    for (i = 0; i < 32; i++) sprintf(s + 2 * i, "%02x", d[i]);
}

static void vec(const char *name, const void *m, size_t n, const char *want) {
    uint8_t a[32], b[32];
    char ha[65], hb[65];
    fnvnx_sha256(m, n, a); fnvnx_sha256_portable(m, n, b);
    hex(a, ha); hex(b, hb);
    if (strcmp(ha, want) || strcmp(hb, want)) { printf("FAIL %s: isa %s portable %s want %s\n", name, ha, hb, want); fails++; }
}

int main(void) {
    static const char m448[] = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    static const char m896[] = "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu";
    uint8_t *buf = malloc(1000000), d1[32], d2[32];
    uint64_t x = 12345;
    size_t i, j, n;
    vec("empty", "", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    vec("abc", "abc", 3, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    vec("448", m448, strlen(m448), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    vec("896", m896, strlen(m896), "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1");
    memset(buf, 'a', 1000000);
    vec("million a", buf, 1000000, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    for (i = 0; i < 1000; i++) {
        x = x * 6364136223846793005ULL + 1442695040888963407ULL;
        n = (size_t)((x >> 33) % 4097);
        for (j = 0; j < n; j++) { x = x * 6364136223846793005ULL + 1442695040888963407ULL; buf[j] = (uint8_t)(x >> 56); }
        fnvnx_sha256(buf, n, d1); fnvnx_sha256_portable(buf, n, d2);
        if (memcmp(d1, d2, 32)) { printf("FAIL random case %zu length %zu\n", i, n); fails++; }
    }
    free(buf);
    printf(fails ? "sha256check: FAILED %d\n" : "sha256check: ok (5 vectors, 1000 random lengths)\n", fails);
    return fails != 0;
}
