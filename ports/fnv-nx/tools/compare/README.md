# tools/compare: S16 render parity

S16 checks our D3D9-on-NVN renderer against a reference D3D9 renderer, frame by frame.

* **Reference:** `nxd9play.exe` (`d3d9/`, built with mingw-w64) replays an NXD9 capture through real D3D9. It runs
  under Wine, where D3D9 is WineD3D on Mesa llvmpipe.
* **Candidate:** `nx_render d3d9` (the render machine's entry point, docs/RENDER_NATIVE.md section 8) renders the same
  capture through the native D3D9 translator and the GL reference backend.

Both write `f<frame %05u>.png` per `PRES`, so one comparison reads both. The scene-level tools from the first round
(`compare.py`, `scenediff.py`, `esm.py`, `ref_ini.py`, `scenes.py`, `wine_ref.sh`: the real exe under Wine, driven by
console scripts) are still here. They are for later, when we compare whole game scenes.

## The loop

```sh
B=render/shaders/build/host                  # render/shaders/build.sh --tests: s6b_asm, sdp2dksh
tools/compare/d3d9/build.sh $BIN             # nxd9play.exe
make -C tests/s6n OUT=$BIN $BIN/nx_render    # from the render machine's head (claude/port-nsw-render)
python3 -I tools/compare/synth.py build --asm $B/s6b_asm --sdp2dksh $B/sdp2dksh --out $CAPS
python3 -I tools/compare/replay.py ref  --exe $BIN/nxd9play.exe --prefix $PFX --out $RUN/ref1 --lp-threads 4 $CAPS/*.nxd9
python3 -I tools/compare/replay.py ref  --exe $BIN/nxd9play.exe --prefix $PFX --out $RUN/ref2 --lp-threads 1 $CAPS/*.nxd9
python3 -I tools/compare/replay.py cand --bin $BIN/nx_render --out $RUN/cand $CAPS/*.nxd9
python3 -I tools/compare/capcompare.py --ref $RUN/ref1 --ref2 $RUN/ref2 --cand $RUN/cand --out $RUN/gate --diffs
```

`tools/farm/compare_job.sh` runs the same loop as a farm job.

Needs: `wine32:i386`, `gcc-mingw-w64-i686`, `xvfb`, `libgl1-mesa-dri:i386`, and EGL for nx_render.

## Metric and threshold (S16 decision, 2026-11-04)

`capcompare.py` writes `capgate.json` (fnvnx-capgate/1). Its `summary.metric` is the number every S16 commit
quotes:

| field | meaning |
|---|---|
| `frames_over` / `frames` | Frames that fail the bounds below. A reference frame the candidate did not render counts as over. |
| `mean_abs`, `max_abs` | Mean and max absolute 8-bit channel difference, candidate against reference, over all compared frames. |
| `floor_mean_abs`, `floor_max_abs` | The same two numbers for the noise floor: reference run 1 against reference run 2. |

**Noise floor.** The floor is two reference runs with different llvmpipe thread counts (`LP_NUM_THREADS` 4 and 1).
Measured on the synthetic set it is 0: WineD3D on llvmpipe repeats itself bit for bit. A non-zero floor marks the
capture `unstable` and widens that frame's bounds by the floor.

**Threshold.** A frame passes when all of these hold:
* PSNR >= 40 dB;
* SSIM >= 0.98;
* FLIP <= 0.05 (when `flip_evaluator` is installed);
* at most 0.5 % of its pixels are off by more than 8 of 255 in any channel.

With a non-zero floor, each bound moves by the floor plus a margin, and never past the hard limits (`THRESHOLDS` in
`capcompare.py`).

Why these values:
* The two renderers translate shaders differently (MojoShader `glsldk` against WineD3D's GLSL backend). That leaves
  1-LSB rounding differences, which we accept: the synthetic set shows `max_abs` 1.
* A missing or wrong draw, wrong blend or state, or a texture-format error moves thousands of pixels by far more
  than 8, so it fails.

The target is `frames_over` 0.

## NXD9 extensions (nxd9play only)

nxd9play executes these records. nx_render skips them as unknown tags, which means a capture that uses them will
show up as a mismatch.

| tag | payload | D3D9 call |
|---|---|---|
| `STRC` | u32 src surface, flags (1 src rect, 2 dst rect), RECT src, u32 dst surface, RECT dst, u32 D3DTEXTUREFILTERTYPE | StretchRect |
| `CFIL` | u32 surface, flags (1 rect), RECT, D3DCOLOR | ColorFill |
| `TEXC` | u32 cube texture id, face, level, bytes, texels | LockRect on a cube face |
| `SFRQ` | u32 stream, divider | SetStreamSourceFreq (instancing) |
| `SURF` +8 | u32 level, u32 cube face, after the fields of RENDER_NATIVE 8.3 | GetSurfaceLevel / GetCubeMapSurface |

For a cube `TEX` (flags 2), each level's bytes hold the six faces one after another, in D3DCUBEMAP_FACES order.

## Captures

* **Synthetic** (`synth.py`): our own shaders (D3D9 assembly), geometry and textures. The generator is committed;
  its output is rebuilt on demand.
* **From the game's data** (the shader packages in `gamedata/shaders`, frames recorded from the owner's exe): these
  are derived from game files, so they and their images stay in the private repository `fnv-nx-build`. Only the
  generators and the numbers come here.
