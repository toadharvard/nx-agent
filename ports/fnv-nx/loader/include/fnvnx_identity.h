#ifndef FNVNX_IDENTITY_H
#define FNVNX_IDENTITY_H
/* Identity of the game executable (loader rule S4).
 * Everything the link derived from the exe (the universe of code entries, the N1 certificate, BODYSEL) holds
 * only for the exact file it was built from.  The loader checks it before mapping, on every start. */
#include <stddef.h>
#include <stdint.h>

#define FNVNX_IDENTITY_MISMATCH 5 /* invariant: the exe file is the exact file the image was built from */

/* 0 when SHA-256(file[0..size)) equals want, FNVNX_IDENTITY_MISMATCH otherwise (start fails with a message). */
int fnvnx_image_identity(const uint8_t *file, size_t size, const uint8_t want[32]);

#endif
