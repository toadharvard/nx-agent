// copy_util.cpp: helpers of copy_util.h.
#include "copy_util.h"
#include "dev_core.h"
#include <string.h>

bool nx_device_in_scene(NxObj* d) { NxDevLife* L = nx_life(d); return L && L->in_scene; }
int nx_dev_profile(NxObj* d) { return nx_env_profile_of(d); }
void nx_cpu_copy_rect(const NxImg* si, const NxSub* ss, const NxRc& sr, const NxImg* di, NxSub* ds, int dx, int dy) {
    const NxTexFmt& f = si->tf; bool blk = (f.flags & NXTF_BLOCKS) != 0; uint32_t unit = blk ? f.bb : f.bytes;
    uint32_t bw = blk ? f.bw : 1, bh = blk ? f.bh : 1, w = (uint32_t)(sr.r - sr.l), h = (uint32_t)(sr.b - sr.t);
    uint32_t rb = ((w + bw - 1) / bw) * unit, rows = (h + bh - 1) / bh; (void)di;
    const uint8_t* s = ss->mem + (size_t)(sr.t / bh) * ss->row + (size_t)(sr.l / bw) * unit; uint8_t* d = ds->mem + (size_t)(dy / bh) * ds->row + (size_t)(dx / bw) * unit;
    for (uint32_t y = 0; y < rows; y++) memmove(d + (size_t)y * ds->row, s + (size_t)y * ss->row, rb);
}
