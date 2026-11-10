/* Host test of loader/src/identity.c with synthetic data.
 * Build and run from ports/fnv-nx:
 * cc -std=c99 -Wall -Wextra -Werror -g -fsanitize=address,undefined -Iloader/include -Iloader/src tests/loader/test_identity.c loader/src/identity.c loader/src/sha256.c -o <out>/test_identity && <out>/test_identity
 */
#include "fnvnx_identity.h"
#include "fnvnx_loader.h"
#include <stdio.h>
#include <string.h>

static int fails;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

int main(void) {
    uint8_t file[100], want[32], bad[32];
    size_t i;
    for (i = 0; i < sizeof file; i++) file[i] = (uint8_t)(i * 7 + 1);
    fnvnx_sha256(file, sizeof file, want);
    CHECK(fnvnx_image_identity(file, sizeof file, want) == 0);
    memcpy(bad, want, 32);
    bad[31] ^= 1;
    CHECK(fnvnx_image_identity(file, sizeof file, bad) == FNVNX_IDENTITY_MISMATCH);
    file[50] ^= 1;
    CHECK(fnvnx_image_identity(file, sizeof file, want) == FNVNX_IDENTITY_MISMATCH);
    file[50] ^= 1;
    CHECK(fnvnx_image_identity(file, sizeof file - 1, want) == FNVNX_IDENTITY_MISMATCH);
    if (fails) { fprintf(stderr, "test_identity: %d failures\n", fails); return 1; }
    puts("test_identity: ok");
    return 0;
}
