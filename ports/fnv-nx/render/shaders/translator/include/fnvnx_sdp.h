/* fnvnx_sdp.h - reader/writer for Bethesda shader packages (shaderpackageNNN.sdp). See SDP.md. */
#ifndef FNVNX_SDP_H
#define FNVNX_SDP_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define FNVNX_SDP_MAGIC     100u   /* first header dword */
#define FNVNX_SDP_NAME_SIZE 256u   /* fixed, NUL padded */
#define FNVNX_SDP_HEADER    12u

typedef struct fnvnx_sdp_entry {
    char name[FNVNX_SDP_NAME_SIZE + 1]; /* always NUL terminated */
    const uint8_t *data;                /* points into the caller's buffer */
    uint32_t size;
    uint32_t offset;                    /* of the bytecode in the file */
} fnvnx_sdp_entry;

typedef struct fnvnx_sdp {
    uint32_t magic, count, data_size;
    fnvnx_sdp_entry *entries;           /* malloc'd, count entries */
} fnvnx_sdp;

/* Parses buf (which must outlive the result). Returns 0 on success, else -1 with err filled.
 * Strict: magic must be 100, data_size must equal len-12, entries must exactly fill the file. */
int fnvnx_sdp_parse(const void *buf, size_t len, fnvnx_sdp *out, char *err, size_t errlen);
void fnvnx_sdp_free(fnvnx_sdp *sdp);

/* Serialises entries into a malloc'd buffer (for tests / repacking). */
int fnvnx_sdp_write(const fnvnx_sdp_entry *entries, uint32_t count, uint8_t **out, size_t *outlen);

#ifdef __cplusplus
}
#endif
#endif
