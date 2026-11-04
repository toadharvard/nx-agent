/* fnv-nx native renderer: replay of a capture (capture.h) into a backend table (LP64 host). */
#ifndef FNVNX_NX_REPLAY_H
#define FNVNX_NX_REPLAY_H
#include <stdint.h>
#include <stddef.h>
#include "nx/gpu_abi.h"

typedef struct NxReplayStats {
    uint32_t frames, images, shaders, samplers, readbacks, readbackMismatches;
} NxReplayStats;

/* Replays the capture in data[size] through api (nxgpu_dispatch). For each RDBK record it reads the same image back
 * and compares it with the captured bytes; onReadback (may be 0) gets the replayed pixels. Returns 0 on success,
 * -1 on a malformed file or a call that failed (err filled). */
int nx_replay(const NxGpuApi* api, const uint8_t* data, size_t size, NxReplayStats* stats,
              void (*onReadback)(void* user, uint32_t index, const NxReadback* r, const uint8_t* rgba), void* user,
              char* err, size_t errlen);
/* Tools (nx_render): called before (after = 0) and after (after = 1) every replayed SUBM record. */
void nx_replay_set_submit_hook(void (*hook)(void* user, uint32_t frame, uint32_t present, int after), void* user);
#endif
