/* fnv-nx L6 GL 4.5 backend v4: the checks of a whole command stream that run before any op executes (the submit is
 * BAD_CALL and nothing ran, as for a malformed opcode). They are the ops whose arguments are statically wrong
 * regardless of the GL state they would meet:
 *   BeginPass4  targets that disagree in size or sample count, bad level / layer, no target at all (the device
 *               guarantees equality, D3D9 requires it);
 *   DefineLayout4, BindVtxN  attribute types, streams, locations, divisors and strides outside the ABI or the GL limits;
 *   DefineState  render-state w[3] bits that are reserved.
 * NXRS_M_LASTPIXEL is not checked: it has no GL state and never refuses a draw (Wine 9.0 wined3d keeps WINED3D_RS_LASTPIXEL
 * without a GL state and draws GL's diamond-exit lines whatever its value; the device logs the FIXME). */
#include "gl4.h"

#define W3_RESERVED 0xFFFFFFF0ull   /* bits 4..31 of w[3]: NXRS_M_* use bits 0-3 and 32-63 */

int gl4_stream_check(const uint32_t* w, const uint32_t* end) {
    uint32_t errs = g4.errCount;
    while (w < end && g4.errCount == errs) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        const uint32_t* a = w + 1;
        if (op == NxOp_End) break;
        if (op == NxOp_DefineState && argc >= 12 && a[0] && a[0] <= GL4_MAX_STATES) {
            uint64_t w3 = a[10] | (uint64_t)a[11] << 32;
            if (w3 & W3_RESERVED) gl4_err("DefineState %u: reserved w[3] bits 0x%llx", a[0], (unsigned long long)(w3 & W3_RESERVED));
        } else if (op == NxOp_BeginPass4) {
            Gl4Pass p;
            uint32_t pw, ph, ps;
            if (!gl4_pass_parse4(a, argc, &p)) gl4_pass_check(&p, &pw, &ph, &ps);
        } else if (op == NxOp_DefineLayout4) {
            gl4_layout4_check(a, argc);
        } else if (op == NxOp_BindVtxN) {
            if (argc < 5) gl4_err("BindVtxN: %u words", argc);
            else if (a[0] >= NX4_MAX_STREAMS || a[4] > (uint32_t)g4.maxVtxStride)
                gl4_err("BindVtxN: stream %u stride %u (max %d)", a[0], a[4], g4.maxVtxStride);
        }
        w += 1 + argc;
    }
    return g4.errCount == errs;
}
