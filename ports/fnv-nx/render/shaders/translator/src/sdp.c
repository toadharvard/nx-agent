/* sdp.c - shader package reader/writer. Format: SDP.md. */
#include "fnvnx_sdp.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t rd32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
static void wr32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

int fnvnx_sdp_parse(const void *buf, size_t len, fnvnx_sdp *out, char *err, size_t errlen)
{
    const uint8_t *b = (const uint8_t *)buf;
    memset(out, 0, sizeof(*out));
    if (len < FNVNX_SDP_HEADER) { snprintf(err, errlen, "file too small (%zu bytes)", len); return -1; }
    out->magic = rd32(b); out->count = rd32(b + 4); out->data_size = rd32(b + 8);
    if (out->magic != FNVNX_SDP_MAGIC) { snprintf(err, errlen, "bad magic %u (expected 100)", out->magic); return -1; }
    if ((size_t)out->data_size != len - FNVNX_SDP_HEADER) {
        snprintf(err, errlen, "data size %u != file size - 12 (%zu)", out->data_size, len - FNVNX_SDP_HEADER); return -1;
    }
    /* each entry is at least 260 bytes: bound count before allocating */
    if (out->count > (len - FNVNX_SDP_HEADER) / (FNVNX_SDP_NAME_SIZE + 4)) {
        snprintf(err, errlen, "count %u does not fit in %zu bytes", out->count, len); return -1;
    }
    out->entries = (fnvnx_sdp_entry *)calloc(out->count ? out->count : 1, sizeof(fnvnx_sdp_entry));
    if (!out->entries) { snprintf(err, errlen, "out of memory"); return -1; }
    size_t pos = FNVNX_SDP_HEADER;
    for (uint32_t i = 0; i < out->count; i++) {
        fnvnx_sdp_entry *e = &out->entries[i];
        if (len - pos < FNVNX_SDP_NAME_SIZE + 4) { snprintf(err, errlen, "entry %u: truncated header", i); goto fail; }
        memcpy(e->name, b + pos, FNVNX_SDP_NAME_SIZE);
        e->name[FNVNX_SDP_NAME_SIZE] = 0;
        e->size = rd32(b + pos + FNVNX_SDP_NAME_SIZE);
        pos += FNVNX_SDP_NAME_SIZE + 4;
        if (e->size > len - pos) { snprintf(err, errlen, "entry %u (%s): size %u past end", i, e->name, e->size); goto fail; }
        e->offset = (uint32_t)pos;
        e->data = b + pos;
        pos += e->size;
    }
    if (pos != len) { snprintf(err, errlen, "%zu trailing bytes after %u entries", len - pos, out->count); goto fail; }
    return 0;
fail:
    fnvnx_sdp_free(out);
    return -1;
}

void fnvnx_sdp_free(fnvnx_sdp *sdp)
{
    free(sdp->entries);
    sdp->entries = NULL;
    sdp->count = 0;
}

int fnvnx_sdp_write(const fnvnx_sdp_entry *entries, uint32_t count, uint8_t **out, size_t *outlen)
{
    size_t len = FNVNX_SDP_HEADER;
    for (uint32_t i = 0; i < count; i++) len += FNVNX_SDP_NAME_SIZE + 4 + entries[i].size;
    uint8_t *b = (uint8_t *)calloc(1, len);
    if (!b) return -1;
    wr32(b, FNVNX_SDP_MAGIC); wr32(b + 4, count); wr32(b + 8, (uint32_t)(len - FNVNX_SDP_HEADER));
    size_t pos = FNVNX_SDP_HEADER;
    for (uint32_t i = 0; i < count; i++) {
        strncpy((char *)b + pos, entries[i].name, FNVNX_SDP_NAME_SIZE); /* zero padded by calloc */
        wr32(b + pos + FNVNX_SDP_NAME_SIZE, entries[i].size);
        pos += FNVNX_SDP_NAME_SIZE + 4;
        if (entries[i].size) memcpy(b + pos, entries[i].data, entries[i].size);
        pos += entries[i].size;
    }
    *out = b; *outlen = len;
    return 0;
}
