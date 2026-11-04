# V6n defects in S6n's native renderer

One failing test per defect: `tests/v6n/run.sh <name>` (needs the host Nine build, see `docs/RENDER_VERIFY.md` §8).
A test passes once the defect is fixed. "Expected" is the oracle (Gallium Nine on softpipe) unless the row says
it is D3D9 behaviour checked analytically.

| name | claim violated | expected vs got | minimal repro |
|---|---|---|---|
| `border_swizzle` | C30 + C32 (`nx/state.h`: border is RGBA8, swizzles apply to the format's channels) | BORDER addressing with BORDERCOLOR 0x80ff4020 on A8, L8, L16, A8L8, A2R10G10B10, ATI1: Nine returns the border colour (1, .25, .125, .5) (L8/L16: alpha 1). A backend that applies S6n's view swizzle as an image-view swizzle (GL, and deko3d image views work the same way) swizzles the border too: A8 gives (0, 0, 0, 1), L8 (1, 1, 1, 1), A2R10G10B10 (.125, .25, 1, .5), ATI1 (1, 0, 0, 1). For A8 no border repacking can fix it (000R cannot produce RGB != 0), so the contract needs a rule: e.g. a native A8 / L8 / BGR10A2 format where one exists, or the inverse-swizzled border plus a shader-side select for the swizzled-constant channels. | `tests/v6n/defects/border_swizzle.scene` (four quadrants, one per format) |

## Not S6n defects (recorded so nobody files them)

| id | what | why it is not a defect |
|---|---|---|
| O1 | X8R8G8B8 target, DESTALPHA blend: Nine-on-softpipe reads the stored X byte (0 after a clear with alpha 0) when a depth buffer is bound, 1.0 without one | D3D9 reads 1.0. The target reads 1.0 (analytic check `o1_x8_dst_alpha`). Oracle quirk. |
| O2 | V8U8 texel -128 decodes to -128/127 on softpipe, -1.0 on llvmpipe | D3D9-era hardware varies; harmless. The conformance scene uses a seed without -128 next to the border. |
| D1 | DEPTHBIAS: Nine-on-softpipe applies about half the bias on D24 and 128x the bias on D16 (clamps to 1.0) | S6n's deviation D1 is D3D9 behaviour; checked analytically (`c40_depthbias_d24`, `c40_depthbias_d16`: target within 2 / 1 LSB of z + DEPTHBIAS). |
