// fnv-nx native renderer: capture replay (host). See replay.h.
#include "replay.h"
#include "capture.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

static uint64_t call(const NxGpuApi* api, uint32_t fn, uint64_t a) { return nxgpu_dispatch(api, fn, &a, 1); }
static uint32_t pad4(uint32_t n) { return (n + 3) & ~3u; }

int nx_replay(const NxGpuApi* api, const uint8_t* d, size_t size, NxReplayStats* st,
              void (*onReadback)(void*, uint32_t, const NxReadback*, const uint8_t*), void* user, char* err, size_t errlen) {
    NxReplayStats local;
    if (!st) st = &local;
    memset(st, 0, sizeof *st);
#define FAIL(...) do { snprintf(err, errlen, __VA_ARGS__); return -1; } while (0)
    if (size < 8 || memcmp(d, "NXCAP\0\0\1", 8)) FAIL("not a capture");
    enum { kMax = 4096 };
    static uint32_t memMap[257], imgMap[kMax + 2], smpMap[kMax + 1], shMap[kMax + 1];
    static uint8_t* memBuf[257];
    memset(memMap, 0, sizeof memMap); memset(imgMap, 0, sizeof imgMap); memset(smpMap, 0, sizeof smpMap);
    memset(shMap, 0, sizeof shMap);
    size_t at = 8;
    while (at + 8 <= size) {
        uint32_t tag, len;
        memcpy(&tag, d + at, 4); memcpy(&len, d + at + 4, 4);
        const uint8_t* p = d + at + 8;
        if (at + 8 + len > size) FAIL("record at %zu truncated", at);
        at += 8 + len;
        switch (tag) {
        case nx::kCapInfo: break;
        case nx::kCapMem: {
            uint32_t a[3]; memcpy(a, p, 12);
            if (a[0] > 256) FAIL("memory id %u", a[0]);
            memBuf[a[0]] = (uint8_t*)memalign(0x1000, a[1]);
            memset(memBuf[a[0]], 0, a[1]);
            NxMemBlockDesc m = {(uint64_t)(uintptr_t)memBuf[a[0]], a[1], a[2]};
            memMap[a[0]] = (uint32_t)call(api, NXGPU_FN_MEM_CREATE, (uint64_t)(uintptr_t)&m);
            if (!memMap[a[0]]) FAIL("mem_create failed");
            break;
        }
        case nx::kCapImage: {
            uint32_t id; NxImageDesc dsc;
            memcpy(&id, p, 4); memcpy(&dsc, p + 4, sizeof dsc);
            if (id > kMax) FAIL("image id %u", id);
            dsc.data = dsc.data_size ? (uint64_t)(uintptr_t)(p + 4 + sizeof dsc) : 0;
            uint32_t nid = (uint32_t)call(api, NXGPU_FN_IMAGE_CREATE, (uint64_t)(uintptr_t)&dsc);
            if (!nid) FAIL("image_create failed (id %u)", id);
            imgMap[id] = nid; imgMap[id + 1] = nid + 1;   // the sRGB view slot, when there is one
            st->images++;
            break;
        }
        case nx::kCapSampler: {
            uint32_t id; NxSamplerState s;
            memcpy(&id, p, 4); memcpy(&s, p + 4, sizeof s);
            if (id > kMax) FAIL("sampler id %u", id);
            smpMap[id] = (uint32_t)call(api, NXGPU_FN_SAMPLER_CREATE, (uint64_t)(uintptr_t)&s);
            st->samplers++;
            break;
        }
        case nx::kCapShader: {
            uint32_t a[4]; memcpy(a, p, 16);
            if (a[0] > kMax) FAIL("shader id %u", a[0]);
            NxShaderDesc sd; memset(&sd, 0, sizeof sd);
            sd.stage = a[1]; sd.dksh_size = a[2]; sd.glsl_size = a[3];
            sd.dksh = a[2] ? (uint64_t)(uintptr_t)(p + 16) : 0;
            sd.glsl = a[3] ? (uint64_t)(uintptr_t)(p + 16 + pad4(a[2])) : 0;
            shMap[a[0]] = (uint32_t)call(api, NXGPU_FN_SHADER_CREATE, (uint64_t)(uintptr_t)&sd);
            if (!shMap[a[0]]) FAIL("shader_create failed (id %u)", a[0]);
            st->shaders++;
            break;
        }
        case nx::kCapSubmit: {
            uint32_t h[3]; memcpy(h, p, 12);
            uint32_t* w = (uint32_t*)malloc((size_t)h[2] * 4 + 4);
            memcpy(w, p + 12, (size_t)h[2] * 4);
            const uint8_t* q = p + 12 + (size_t)h[2] * 4;
            uint32_t n; memcpy(&n, q, 4); q += 4;
            for (uint32_t i = 0; i < n; i++) {
                uint32_t r[3]; memcpy(r, q, 12); q += 12;
                if (r[0] <= 256 && memBuf[r[0]]) memcpy(memBuf[r[0]] + r[1], q, r[2]);
                q += pad4(r[2]);
            }
            // remap resource ids inside the stream
            for (uint32_t i = 0; i < h[2];) {
                uint32_t op = w[i] & 0xFFFF, argc = w[i] >> 16;
                uint32_t* a = w + i + 1;
                switch (op) {
                case NxOp_DefineState: if (argc >= 3) { a[1] = shMap[a[1] & 0xFFFF]; a[2] = shMap[a[2] & 0xFFFF]; } break;
                case NxOp_BeginPass: if (argc >= 2) { a[0] = a[0] ? imgMap[a[0]] : 0; a[1] = a[1] ? imgMap[a[1]] : 0; } break;
                case NxOp_BindUbo: if (argc >= 3) a[2] = memMap[a[2]]; break;
                case NxOp_BindVtx: case NxOp_BindIdx: if (argc >= 1) a[0] = memMap[a[0]]; break;
                case NxOp_BindTextures:
                    for (uint32_t t = 0; argc >= 3 && t < a[2] && 3 + t < argc; t++) {
                        uint32_t hd = a[3 + t], img = hd & 0xFFFFF, smp = hd >> 20;
                        a[3 + t] = NX_TEX_HANDLE(img <= kMax ? imgMap[img] : 0, smp <= kMax ? smpMap[smp] : 0);
                    }
                    break;
                default: break;
                }
                if (op == NxOp_End) break;
                i += 1 + argc;
            }
            NxSubmit s; memset(&s, 0, sizeof s);
            s.stream = (uint64_t)(uintptr_t)w; s.words = h[2]; s.frame = h[0]; s.present = h[1];
            call(api, NXGPU_FN_SUBMIT, (uint64_t)(uintptr_t)&s);
            call(api, NXGPU_FN_WAIT_FRAME, h[0]);
            free(w);
            st->frames++;
            break;
        }
        case nx::kCapReadback: {
            NxReadback r; uint32_t bytes;
            memcpy(&r, p, sizeof r); memcpy(&bytes, p + sizeof r, 4);
            r.image = r.image ? imgMap[r.image] : 0;
            uint8_t* out = (uint8_t*)malloc((size_t)r.w * r.h * 4 + 4);
            r.dst = (uint64_t)(uintptr_t)out; r.dst_size = r.w * r.h * 4;
            if (call(api, NXGPU_FN_READBACK, (uint64_t)(uintptr_t)&r) == 0) {
                if (bytes && (bytes != r.dst_size || memcmp(out, p + sizeof r + 4, bytes))) st->readbackMismatches++;
                if (onReadback) onReadback(user, st->readbacks, &r, out);
            }
            st->readbacks++;
            free(out);
            break;
        }
        default: break;   // unknown records are skipped
        }
    }
    return 0;
#undef FAIL
}
