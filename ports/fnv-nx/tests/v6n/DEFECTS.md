# V6n defects in S6n's native renderer

One failing test per defect: `tests/v6n/run.sh <name>` (needs the host Nine build, see `docs/RENDER_VERIFY.md` §8).
Targets: `v6n_s6ngl` is S6n's own GL backend (`render/native/gl/nxgl.c`) driven through its NxGpuApi, with S6n's
translator and epilogue; V6n adds only the fixed-function model shaders.
A test passes once the defect is fixed. "Expected" is the oracle (Gallium Nine on softpipe) unless the row says
it is D3D9 behaviour checked analytically.

| name | status | claim violated | expected vs got | minimal repro |
|---|---|---|---|---|
| `y_fill_bias` | **fixed** by S6n 521ffaf (GL_LOWER_LEFT + y negated in the VS, no bias), verified by V6n: rastercheck 0 wrong px on all grids, texel scene matches | `nx/state.h` contract ("window space ... NxViewport +0.5 makes a +0.5-centre rasterizer cover exactly the pixels D3D9 covers"); `gl/nxgl.c` NXGL_Y_FILL_BIAS comment ("edges that are not on a pixel centre move by less than the rasterizer's precision") | The -1/256 px viewport shift fixes ties on horizontal edges only. (1) Coverage: against the exact D3D9 top-left rule (`render/verify/raster_ref.py`) S6n's backend gets 204 pixels wrong in 98 of 240 random triangles (integer vertices: 136 px in 44 of 60), because a tie on a slanted edge becomes slope-dependent; V6n's GL executor of the same contract (LOWER_LEFT origin + y negated in the VS, no bias) gets 0. (2) Attributes move too: a POINT-sampled quad whose pixel centres sit on texel boundaries (v decreasing with y) selects the next texel row: all 256 px differ from Nine. | `tests/v6n/defects/y_fill_bias.scene` (texel symptom) + `render/verify/rastercheck.py v6n_s6ngl` (coverage) |
| `border_swizzle` | **fixed** by S6n 17077d6 (NXFMT_CONVERT + `nx_d3d9_convert`), verified by V6n on both targets | C30 + C32 (`nx/state.h`: border is RGBA8, swizzles apply to the format's channels) | BORDER addressing with BORDERCOLOR 0x80ff4020 on A8, L8, L16, A8L8, A2R10G10B10, ATI1: Nine returns the border colour (1, .25, .125, .5) (L8/L16: alpha 1). A backend that applies S6n's view swizzle as an image-view swizzle (GL, and deko3d image views work the same way) swizzles the border too: A8 gives (0, 0, 0, 1), L8 (1, 1, 1, 1), A2R10G10B10 (.125, .25, 1, .5), ATI1 (1, 0, 0, 1). For A8 no border repacking can fix it (000R cannot produce RGB != 0), so the contract needs a rule: e.g. a native A8 / L8 / BGR10A2 format where one exists, or the inverse-swizzled border plus a shader-side select for the swizzled-constant channels. | `tests/v6n/defects/border_swizzle.scene` (four quadrants, one per format) |

## Not S6n defects (recorded so nobody files them)

| id | what | why it is not a defect |
|---|---|---|
| O1 | X8R8G8B8 target, DESTALPHA blend: Nine-on-softpipe reads the stored X byte (0 after a clear with alpha 0) when a depth buffer is bound, 1.0 without one | D3D9 reads 1.0. The target reads 1.0 (analytic check `o1_x8_dst_alpha`). Oracle quirk. |
| O2 | V8U8 texel -128 decodes to -128/127 on softpipe, -1.0 on llvmpipe | D3D9-era hardware varies; harmless. The conformance scene uses a seed without -128 next to the border. |
| O3 | Stock softpipe (S6c's host Nine as built) covers x >= trunc(edge x) for D3D9 pixel centres instead of x >= ceil(edge x): 6628 wrong px in 240 random triangles | Oracle bug, not S6n's. V6n's oracle `v6n_nine` links a patched copy of `sp_setup.c` (3 px left, float ties); `v6n_nine_stock` is the unpatched build. Matters for S6c's host gate too (Switch uses nouveau). |
| D1 | DEPTHBIAS: Nine-on-softpipe applies about half the bias on D24 and 128x the bias on D16 (clamps to 1.0) | S6n's deviation D1 is D3D9 behaviour; checked analytically (`c40_depthbias_d24`, `c40_depthbias_d16`: target within 2 / 1 LSB of z + DEPTHBIAS). |
