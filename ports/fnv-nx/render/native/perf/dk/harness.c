/* fnv-nx P6n: the deko3d backend's per-frame recording, counted. Includes render/native/host/nxgpu.c unchanged
 * (its static record() is what the render thread runs per frame), links the real libdeko3d.a (LP64) and libnx
 * stand-ins (stubs.c), and replays the command streams of an .nxcap capture (perf/native_drv_cap) into record().
 * Run by perf/dk/count.py under Unicorn: input at PERF_INPUT, marks via perf_mark(), end via perf_exit().
 * Input blob: u64 nxcap bytes, u64 dksh pack offset, u64 dksh pack bytes, then the .nxcap, then the pack
 * (u32 count, then per shader u32 stage, u32 bytes, bytes padded to 8). */
#include "../../host/nxgpu.c"

#define PERF_INPUT 0x70000000ull
void __attribute__((noinline)) perf_mark(uint32_t id) { __asm__ volatile("" :: "r"(id) : "memory"); }
void __attribute__((noinline, noreturn)) perf_exit(int code) { __asm__ volatile("" :: "r"(code) : "memory"); for (;;) __asm__ volatile("wfi"); }
volatile uint32_t perf_result[16];

static uint32_t rd(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }

int main(void) {
    const uint64_t* hdr = (const uint64_t*)PERF_INPUT;
    const uint8_t* cap = (const uint8_t*)(PERF_INPUT + 64);
    uint64_t capBytes = hdr[0];
    const uint8_t* pack = (const uint8_t*)(PERF_INPUT + hdr[1]);
    if (!nxgpu_init(1280, 720)) { perf_result[0] = 1; perf_exit(1); }
    /* shaders: the pack's DKSH cycled over ids 1..N (odd = vertex, even = fragment, as perf/gen_stream.py numbers them) */
    uint32_t nsh = rd(pack), nv = 0, np = 0, vi[32], pi[32];
    const uint8_t* sp[64]; uint32_t ss[64];
    const uint8_t* q = pack + 4;
    for (uint32_t i = 0; i < nsh && i < 64; i++) {
        uint32_t stage = rd(q);
        ss[i] = rd(q + 4); sp[i] = q + 8; q += 8 + ((ss[i] + 7) & ~7u);
        if (stage == 0 && nv < 32) vi[nv++] = i; else if (stage == 1 && np < 32) pi[np++] = i;
    }
    if (!nv || !np) { perf_result[0] = 2; perf_exit(2); }
    for (uint32_t id = 1; id <= 480; id++) {
        uint32_t frag = !(id & 1), k = frag ? pi[(id / 2) % np] : vi[(id / 2) % nv];
        NxShaderDesc d; memset(&d, 0, sizeof d);
        d.stage = frag; d.dksh = (uint64_t)(uintptr_t)sp[k]; d.dksh_size = ss[k];
        if (api_shader_create((u64)(uintptr_t)&d) != id) { perf_result[0] = 2; perf_result[1] = id; perf_exit(2); }
    }
    /* resources and frames of the capture */
    uint32_t frames = 0;
    uint64_t off = 8;
    while (off + 8 <= capBytes) {
        uint32_t tag = rd(cap + off), n = rd(cap + off + 4);
        const uint8_t* p = cap + off + 8;
        off += 8 + ((n + 3) & ~3u);
        if (tag == 0x434D454Du) {            /* MEMC id size kind: caller-backed storage */
            uint32_t size = rd(p + 4);
            void* mem = aligned_alloc(0x1000, size);
            NxMemBlockDesc d = {(uint64_t)(uintptr_t)mem, size, rd(p + 8)};
            if (api_mem_create((u64)(uintptr_t)&d) != rd(p)) { perf_result[0] = 3; perf_exit(3); }
        } else if (tag == 0x43474D49u) {     /* IMGC id desc data: created without upload */
            NxImageDesc d; memcpy(&d, p + 4, sizeof d); d.data = 0; d.data_size = 0;
            if (api_image_create((u64)(uintptr_t)&d) != rd(p)) { perf_result[0] = 4; perf_result[1] = rd(p); perf_exit(4); }
        } else if (tag == 0x43504D53u) {     /* SMPC id state */
            api_sampler_create((u64)(uintptr_t)(p + 4));
        } else if (tag == 0x4D425553u) {     /* SUBM frame present words ... */
            NxSubmit s; memset(&s, 0, sizeof s);
            s.frame = rd(p); s.present = rd(p + 4); s.words = rd(p + 8); s.stream = (uint64_t)(uintptr_t)(p + 12);
            uint32_t slot = s.frame % NXGPU_FRAMES;
            NxFrameStats fs; memset(&fs, 0, sizeof fs);
            perf_mark(2 * frames + 1);
            dkCmdBufClear(g.cmd[slot]);
            dkCmdBufAddMemory(g.cmd[slot], g.cmdMem, slot * CMDMEM_PER_FRAME, CMDMEM_PER_FRAME);
            record(g.cmd[slot], &s, &fs);
            perf_mark(2 * frames + 2);
            perf_result[2] = fs.draws; perf_result[3] = fs.state_binds; perf_result[4] = fs.block_binds;
            perf_result[5] = s.words; perf_result[6] = g.nBlock[0] + g.nBlock[1] + g.nBlock[2] + g.nBlock[3] + g.nBlock[4];
            frames++;
        }
    }
    perf_result[0] = 0; perf_result[7] = frames;
    perf_exit(0);
}
