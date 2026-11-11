/* The exe identity check (see fnvnx_identity.h). */
#include "fnvnx_identity.h"
#include "fnvnx_loader.h"

int fnvnx_image_identity(const uint8_t *file, size_t size, const uint8_t want[32]) {
    uint8_t got[32];
    uint8_t diff = 0;
    unsigned k;

    fnvnx_sha256(file, size, got);
    for (k = 0; k < 32; k++) diff = (uint8_t)(diff | (got[k] ^ want[k]));
    return diff == 0 ? 0 : FNVNX_IDENTITY_MISMATCH;
}
