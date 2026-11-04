// fnv-nx native renderer: capture replay (host). See replay.h.
#include "replay.h"
#include "capture.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

static void (*g_submitHook)(void*, uint32_t, uint32_t, int);
static void* g_submitUser;
void nx_replay_set_submit_hook(void (*hook)(void*, uint32_t, uint32_t, int), void* user) {
    g_submitHook = hook; g_submitUser = user;
}

static uint64_t call(const NxGpuApi* api, uint32_t fn, uint64_t a) { return nxgpu_dispatch(api, fn, &a, 1); }
static uint32_t pad4(uint32_t n) { return (n + 3) & ~3u; }

int nx_replay(const NxGpuApi* api, const uint8_t* d, size_t size, NxReplayStats* st,
              void (*onReadback)(void*, uint32_t, const NxReadback*, const uint8_t*), void* user, char* err, size_t errlen) {
    NxReplayStats local;
    if (!st) st = &local;
    memset(st, 0, sizeof *st);
#define FAIL(...) do { snprintf(err, errlen, __VA_ARGS__); return -1; } while (0)
    if (size < 8 || memcmp(d, "NXCAP\0\0\1", 8)) FAIL("not a capture");
    // .nxcap is outside input (a console capture): every size is checked in 64 bits against its record and these
    // bounds before it is allocated, copied or handed to the backend; ids are range-checked before any table lookup.
    enum : uint32_t { kMax = 4096, kMaxMem = 256u << 20, kMaxDim = 8192, kMaxTexels = 4096u * 4096u,
                      kMaxDrawVerts = 1u << 22, kMaxInstances = 1u << 16 };
    static constexpr uint64_t kMaxResident = 1ull << 30;
    static uint32_t memMap[257], memSize[257], imgMap[kMax + 2], smpMap[kMax + 1], shMap[kMax + 1];
    static uint8_t* memBuf[257];
    memset(memBuf, 0, sizeof memBuf);   // blocks of an earlier call stay alive: the backend may still reference them
    memset(memMap, 0, sizeof memMap); memset(memSize, 0, sizeof memSize); memset(imgMap, 0, sizeof imgMap);
    memset(smpMap, 0, sizeof smpMap); memset(shMap, 0, sizeof shMap);
    uint64_t resident = 0;
    size_t at = 8;
    while (at + 8 <= size) {
        uint32_t tag, len;
        memcpy(&tag, d + at, 4); memcpy(&len, d + at + 4, 4);
        const uint8_t* p = d + at + 8;
        if ((uint64_t)len > size - at - 8) FAIL("record at %zu truncated", at);
        at += 8 + len;
        switch (tag) {
        case nx::kCapInfo: break;
        case nx::kCapMem: {
            if (len < 12) FAIL("MEMC record at %zu: %u bytes", at - 8 - len, len);
            uint32_t a[3]; memcpy(a, p, 12);
            if (a[0] > 256) FAIL("memory id %u", a[0]);
            if (memBuf[a[0]]) FAIL("memory id %u defined twice", a[0]);
            if (!a[1] || a[1] > kMaxMem || a[1] > kMaxResident - resident) FAIL("memory block %u: %u bytes", a[0], a[1]);
            memBuf[a[0]] = (uint8_t*)memalign(0x1000, a[1]);
            if (!memBuf[a[0]]) FAIL("out of memory (block %u, %u bytes)", a[0], a[1]);
            memset(memBuf[a[0]], 0, a[1]);
            memSize[a[0]] = a[1]; resident += a[1];
            NxMemBlockDesc m = {(uint64_t)(uintptr_t)memBuf[a[0]], a[1], a[2]};
            memMap[a[0]] = (uint32_t)call(api, NXGPU_FN_MEM_CREATE, (uint64_t)(uintptr_t)&m);
            if (!memMap[a[0]]) FAIL("mem_create failed");
            break;
        }
        case nx::kCapImage: {
            uint32_t id; NxImageDesc dsc;
            if (len < 4 + sizeof dsc) FAIL("IMG record: %u bytes", len);
            memcpy(&id, p, 4); memcpy(&dsc, p + 4, sizeof dsc);
            if (id > kMax) FAIL("image id %u", id);
            if (!dsc.width || !dsc.height || dsc.width > kMaxDim || dsc.height > kMaxDim ||
                (uint64_t)dsc.width * dsc.height > kMaxTexels || dsc.mips > 14)
                FAIL("image %u: %ux%u, %u mips", id, dsc.width, dsc.height, dsc.mips);
            if (dsc.data_size > len - 4 - sizeof dsc) FAIL("image %u: data_size %u past its record", id, dsc.data_size);
            dsc.data = dsc.data_size ? (uint64_t)(uintptr_t)(p + 4 + sizeof dsc) : 0;
            uint32_t nid = (uint32_t)call(api, NXGPU_FN_IMAGE_CREATE, (uint64_t)(uintptr_t)&dsc);
            if (!nid) FAIL("image_create failed (id %u)", id);
            imgMap[id] = nid; imgMap[id + 1] = nid + 1;   // the sRGB view slot, when there is one
            st->images++;
            break;
        }
        case nx::kCapSampler: {
            uint32_t id; NxSamplerState s;
            if (len < 4 + sizeof s) FAIL("SMPL record: %u bytes", len);
            memcpy(&id, p, 4); memcpy(&s, p + 4, sizeof s);
            if (id > kMax) FAIL("sampler id %u", id);
            smpMap[id] = (uint32_t)call(api, NXGPU_FN_SAMPLER_CREATE, (uint64_t)(uintptr_t)&s);
            st->samplers++;
            break;
        }
        case nx::kCapShader: {
            if (len < 16) FAIL("SHDR record: %u bytes", len);
            uint32_t a[4]; memcpy(a, p, 16);
            if (a[0] > kMax) FAIL("shader id %u", a[0]);
            if (16 + (((uint64_t)a[2] + 3) & ~3ull) + a[3] > len) FAIL("shader %u: code past its record", a[0]);
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
            if (len < 16) FAIL("SUBM record: %u bytes", len);
            uint32_t h[3]; memcpy(h, p, 12);
            if (12 + 4 * (uint64_t)h[2] + 4 > len) FAIL("SUBM: %u words past its record", h[2]);
            uint32_t* w = (uint32_t*)malloc((size_t)h[2] * 4 + 4);
            if (!w) FAIL("out of memory (SUBM, %u words)", h[2]);
            memcpy(w, p + 12, (size_t)h[2] * 4);
            const uint8_t* q = p + 12 + (size_t)h[2] * 4;
            const uint8_t* rend = p + len;
            uint32_t n; memcpy(&n, q, 4); q += 4;
            for (uint32_t i = 0; i < n; i++) {
                uint32_t r[3];
                if (rend - q < 12) { free(w); FAIL("SUBM: memory range %u past its record", i); }
                memcpy(r, q, 12); q += 12;
                if ((uint64_t)(rend - q) < r[2]) { free(w); FAIL("SUBM: memory range %u past its record", i); }
                if (r[0] > 256 || !memBuf[r[0]] || (uint64_t)r[1] + r[2] > memSize[r[0]]) {
                    free(w); FAIL("SUBM: memory range %u (block %u, +%u, %u bytes) outside its block", i, r[0], r[1], r[2]);
                }
                memcpy(memBuf[r[0]] + r[1], q, r[2]);
                q += r[2];
                q += (rend - q) < (ptrdiff_t)(pad4(r[2]) - r[2]) ? (rend - q) : (pad4(r[2]) - r[2]);
            }
            // remap resource ids inside the stream
            for (uint32_t i = 0; i < h[2];) {
                uint32_t op = w[i] & 0xFFFF, argc = w[i] >> 16;
                if ((uint64_t)i + 1 + argc > h[2]) { free(w); FAIL("SUBM: op %u at word %u: %u arguments past the stream", op, i, argc); }
                uint32_t* a = w + i + 1;
                auto sh = [](uint32_t v) { v &= 0xFFFF; return v <= kMax ? shMap[v] : 0u; };
                auto img = [](uint32_t v) { return v && v <= kMax + 1 ? imgMap[v] : 0u; };
                auto mem = [](uint32_t v) { return v <= 256 ? memMap[v] : 0u; };
                switch (op) {
                case NxOp_DefineState: if (argc >= 3) { a[1] = sh(a[1]); a[2] = sh(a[2]); } break;
                case NxOp_BeginPass: if (argc >= 2) { a[0] = img(a[0]); a[1] = img(a[1]); } break;
                case NxOp_BindUbo: if (argc >= 3) a[2] = mem(a[2]); break;
                case NxOp_BindVtx: case NxOp_BindIdx: if (argc >= 1) a[0] = mem(a[0]); break;
                case NxOp_Draw: case NxOp_DrawIndexed:   // the same bounds as NXD9 draws (8.4); V1 H1 hung on 0x7fffffff instances
                    if (argc >= 3 && (a[1] > kMaxDrawVerts || a[2] > kMaxInstances || (uint64_t)a[1] * a[2] > kMaxDrawVerts)) {
                        const uint32_t nvx = a[1], ninst = a[2];
                        free(w); FAIL("SUBM: draw at word %u: %u vertices x %u instances over the limit", i, nvx, ninst);
                    }
                    break;
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
            if (g_submitHook) g_submitHook(g_submitUser, h[0], h[1], 0);
            call(api, NXGPU_FN_SUBMIT, (uint64_t)(uintptr_t)&s);
            call(api, NXGPU_FN_WAIT_FRAME, h[0]);
            if (g_submitHook) g_submitHook(g_submitUser, h[0], h[1], 1);
            free(w);
            st->frames++;
            break;
        }
        case nx::kCapReadback: {
            NxReadback r; uint32_t bytes;
            if (len < sizeof r + 4) FAIL("RDBK record: %u bytes", len);
            memcpy(&r, p, sizeof r); memcpy(&bytes, p + sizeof r, 4);
            if (bytes > len - sizeof r - 4) FAIL("RDBK: %u bytes past its record", bytes);
            r.image = r.image && r.image <= kMax + 1 ? imgMap[r.image] : 0;
            uint64_t rb64 = (uint64_t)r.w * r.h * nx_readback_bpp(r.format);
            if (!r.w || !r.h || r.w > kMaxDim || r.h > kMaxDim || rb64 > (uint64_t)kMaxTexels * 16)
                FAIL("RDBK: %ux%u", r.w, r.h);
            uint32_t rbBytes = (uint32_t)rb64;
            uint8_t* out = (uint8_t*)malloc((size_t)rbBytes + 4);
            if (!out) FAIL("out of memory (RDBK)");
            r.dst = (uint64_t)(uintptr_t)out; r.dst_size = rbBytes;
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
