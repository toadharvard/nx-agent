// D3DFORMAT -> memory layout (NxTexFmt). Source: Wine 9.0 d3d9/device.c wined3dformat_from_d3dformat (the id map: printable four bytes pass through as FOURCC, the 48 enum values below, everything
// else = WINED3DFMT_UNKNOWN), wined3d/utils.c formats[] (bpp), format_block_info[] (block sizes), format_base_flags[] (MAPPABLE: ATI1N, ATI2N, D16_LOCKABLE, INTZ; BROKEN_PITCH: ATI1N, ATI2N), the YV12 / NV12
// height scale 3/2 (init_format_texture_info_gl, GL shaders present), wined3d_format_calculate_pitch / _size, d3d9 wined3d_device_create(..., surface_alignment 4, ...).
// A format with bytes == 0 (UNKNOWN, INST, RESZ, ATOC, NVDB, unmapped ids, unknown FOURCCs) has size 0: wined3d_texture_init returns WINED3DERR_INVALIDCALL for it (`!offset`).
// One total table: the test (tests/l6/tex part a / h) checks every format class of the env table (render/d3d9/env) has a row here or is a zero-size class.
#include "res_tex.h"
#define FC(a, b, c, d) ((uint32_t)(uint8_t)(a) | (uint32_t)(uint8_t)(b) << 8 | (uint32_t)(uint8_t)(c) << 16 | (uint32_t)(uint8_t)(d) << 24)
namespace {
const uint8_t BL = NXTF_BLOCKS | NXTF_COMPRESSED, NV = NXTF_NOVERIFY | NXTF_BLOCKS, BK = NXTF_BROKEN, MP = NXTF_MAPPABLE;
struct Row { uint32_t id; uint8_t bytes, bw, bh, bb, flags, hn, hd; };
#define P(id, n) {id, n, 1, 1, n, 0, 0, 0}                       // plain: n bytes per pixel
const Row T[] = {
    P(20, 3), P(21, 4), P(22, 4), P(23, 2), P(24, 2), P(25, 2), P(26, 2), P(27, 1), P(28, 1), P(29, 2), P(30, 2), P(31, 4), P(32, 4), P(33, 4), P(34, 4), P(35, 4), P(36, 8),
    P(40, 2), P(41, 1), P(50, 1), P(51, 2), P(52, 1), P(60, 2), P(61, 2), P(62, 4), P(63, 4), P(64, 4), P(67, 4),
    {70, 2, 1, 1, 2, MP, 0, 0}, P(71, 4), P(73, 2), P(75, 4), P(77, 4), P(79, 4), P(80, 2), P(81, 2), P(82, 4), P(83, 4), P(101, 2), P(102, 4), P(110, 8), P(111, 2), P(112, 4), P(113, 8), P(114, 4), P(115, 8), P(116, 16), P(117, 2),
    {FC('U', 'Y', 'V', 'Y'), 2, 2, 1, 4, NV, 0, 0}, {FC('Y', 'U', 'Y', '2'), 2, 2, 1, 4, NV, 0, 0}, {FC('Y', 'V', '1', '2'), 1, 1, 1, 1, 0, 3, 2}, {FC('N', 'V', '1', '2'), 1, 1, 1, 1, 0, 3, 2},
    {FC('D', 'X', 'T', '1'), 1, 4, 4, 8, BL, 0, 0}, {FC('D', 'X', 'T', '2'), 1, 4, 4, 16, BL, 0, 0}, {FC('D', 'X', 'T', '3'), 1, 4, 4, 16, BL, 0, 0}, {FC('D', 'X', 'T', '4'), 1, 4, 4, 16, BL, 0, 0}, {FC('D', 'X', 'T', '5'), 1, 4, 4, 16, BL, 0, 0},
    P(FC('M', 'E', 'T', '1'), 1), P(FC('G', 'R', 'G', 'B'), 1), P(FC('R', 'G', 'B', 'G'), 1),
    {FC('A', 'T', 'I', '1'), 1, 4, 4, 8, BL | NV | BK | MP, 0, 0}, {FC('A', 'T', 'I', '2'), 1, 4, 4, 16, BL | NV | BK | MP, 0, 0},
    P(FC('N', 'V', 'H', 'U'), 2), P(FC('N', 'V', 'H', 'S'), 2), {FC('I', 'N', 'T', 'Z'), 4, 1, 1, 4, MP, 0, 0}, P(FC('N', 'U', 'L', 'L'), 4), P(FC(' ', 'R', '1', '6'), 2), P(FC('A', 'L', '1', '6'), 4),
};
}
bool nx_texfmt(uint32_t d, NxTexFmt* f) {
    for (const Row& r : T) if (r.id == d) { *f = {r.bytes, r.bw, r.bh, r.bb, r.flags, r.hn, r.hd}; return true; }
    return false;   // INST / RESZ / ATOC / NVDB (bytes 0), UNKNOWN, unmapped numbers, unknown FOURCCs
}
uint64_t nx_tex_store(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t d) {   // bytes the layer keeps for a sub-resource: the D3D size, and for BROKEN_PITCH formats at least the whole blocks nx_img_push reads (w*h*bytes is below one block for 1x1..4x1)
    uint64_t n = nx_tex_size(f, w, h, d);
    if (f.flags & NXTF_BROKEN) { uint64_t c = (uint64_t)((w + f.bw - 1) / f.bw) * f.bb * ((h + f.bh - 1) / f.bh) * d; if (c > n) n = c; }
    return n;
}
void nx_tex_pitch(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t* row, uint32_t* slice) {
    if (f.flags & NXTF_BLOCKS) {
        uint32_t rb = (w + f.bw - 1) / f.bw, sb = (h + f.bh - 1) / f.bh;
        *row = (rb * f.bb + 3) & ~3u; *slice = *row * sb;
    } else { *row = (f.bytes * w + 3) & ~3u; *slice = *row * h; }
    if (f.hn) *slice = *slice * f.hn / f.hd;
}
uint64_t nx_tex_size(const NxTexFmt& f, uint32_t w, uint32_t h, uint32_t d) {
    if (f.flags & NXTF_BROKEN) return (uint64_t)w * h * d * f.bytes;     // wined3d_format_calculate_size: BROKEN_PITCH
    uint32_t row, slice; nx_tex_pitch(f, w, h, &row, &slice); return (uint64_t)slice * d;
}
