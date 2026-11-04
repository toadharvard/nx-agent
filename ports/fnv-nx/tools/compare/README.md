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

## d9cap: the capture layer

`d3d9/d9cap.c` (built by `d3d9/build.sh` as `d3d9.dll`) is a proxy d3d9. It loads the real one (by default
`<system directory>\d3d9.dll`, which is Wine's builtin), forwards every call to it, and journals the D3D9 call stream
as plain NXD9. nxd9play and nx_render read the journal as it is.

To capture, put `d3d9.dll` beside the exe and run the exe under Wine with the native override:

```sh
cp $BIN/d3d9.dll "$GAME/"
WINEDLLOVERRIDES='d3d9=n,b' S16_CAPTURE='Z:\tmp\run.nxd9' S16_CAPTURE_FRAMES=100-102 wine "$GAME/FalloutNV.exe"
wine $BIN/nxd9play.exe 'Z:\tmp\run.nxd9' -o 'Z:\tmp\run'       # the journal replays like any capture
```

| env | meaning |
|---|---|
| `S16_CAPTURE` | Journal path, as a Windows path. Default `d9cap.nxd9` in the current directory. |
| `S16_CAPTURE_FRAMES` | `first-last` (frames are numbered by Present, from 0; `N` means `N-N`, `first-` has no end). State and resources carry across frames, so everything is journaled from the start. Before `first`, the records that only produce pixels are dropped (DRAW, DRWI, CLR, STRC, CFIL, UP draws). The file closes after frame `last`'s PRES. A frame that builds on an earlier frame's pixels (a render target cleared only in part) differs from the real run. |
| `S16_CAPTURE_MAX_MB` | Size cap in MiB (fractions allowed). The journal stops before the record that would pass the cap, so it holds only whole records. |
| `S16_REAL_D3D9` | Path of the real d3d9, for when the system path resolves back to the proxy. d9cap checks for that case and says so on stderr. |

When the journal closes, d9cap prints one line on stderr: frames, records, bytes, `unjournaled` and the reason it
stopped. `unjournaled` counts calls that changed GPU contents or state in a way NXD9 cannot carry:
* FVF draws (there is no SetFVF record);
* ProcessVertices;
* a partial UpdateSurface;
* a lock of a render-target texture;
* state blocks (Set* calls inside Begin/EndStateBlock are not journaled, and Apply is not seen);
* Reset's return to the default state;
* objects first seen through a Get* call.

Mapping:
* Creation calls become TEX, SURF, VB, IB, DECL, VSH and PSH. Shaders carry the bytecode and its FNV-1a 64 hash, with
  no GLSL.
* Unlocks become VBU/IBU (the locked range) and TEXU/TEXC (the whole level or face, read back after a partial lock).
* UpdateTexture becomes TEXU/TEXC from the system-memory source. System-memory textures get no records of their own.
* UP draws go through a scratch VB/IB of d9cap's own, then stream 0 and the indices are unset, as D3D9 leaves them.
* The back buffer is surface 0 and the auto depth-stencil is 0xFFFFFFFF, so neither gets a SURF record.

Wrappers keep COM identity: GetDevice, GetTexture, GetSurfaceLevel, GetContainer, QueryInterface and the other
getters return the same wrappers. A resource wrapper lives exactly as long as its resource, because the runtime
releases a sentinel attached with `SetPrivateData(D3DSPD_IUNKNOWN)` when it destroys the resource. These are not
wrapped and pass through as the runtime's objects: volume textures, state blocks, queries, and the `*Ex` interfaces
(QueryInterface refuses them, and `Direct3DCreate9Ex` returns E_NOTIMPL). `d9cap_stats` (an extra export) reports
live wrappers and journal counters for tests.

`tests/s16/test_d9cap.py` runs the round trip under Wine. Every synthetic capture plus an extension-record capture is
replayed through nxd9play with the proxy, and the resulting journal through plain nxd9play must be pixel-identical.
`tests/s16/d9cap_probe.c` checks forwarding, identity, lifetime, UP draws and the different ways of filling a texture.

## Captures

* **Synthetic** (`synth.py`): our own shaders (D3D9 assembly), geometry and textures. The generator is committed;
  its output is rebuilt on demand.
* **From the game's data** (the shader packages in `gamedata/shaders`, frames recorded from the owner's exe): these
  are derived from game files, so they and their images stay in the private repository `fnv-nx-build`. Only the
  generators and the numbers come here.
* **Permutations** (`perm.py`): one frame per unique program of a package (shaderpackage019, the one the engine
  loads: 815 unique of 1,007). Every program runs through the whole pipeline: declaration, constants, samplers,
  states, and the GLSL nx_render embeds.
  - A pixel program gets V6n's driver VS.
  - A vertex program gets a generated PS that sums its outputs, and a declaration with the engine's element
    types.
  - Relative addressing is kept inside the arrays it indexes. Bone indices are D3DCOLOR x 765.01 within the CTAB
    palette, as Gamebryo's skin VS expect. With UBYTE4 indices, 37 of 39 first-run differences were reads far
    outside c0..c255, where D3D9 is undefined.

  `--programs <list>` restricts the set, for example to the permutations a recorded frame used.

## Conditioning floor (permutation captures)

`perm.py --perturb 1e-5` writes the same captures with every float input scaled by 1 + 1e-5 u. The reference
rendered from them, compared with the unperturbed reference, shows which frames are ill-conditioned: frames whose
image moves when the inputs move by float rounding (V6n's SENS idea). A candidate difference on such a frame is not
evidence of a defect. On shaderpackage019, one frame (SM3016) is ill-conditioned, and the candidate matches the
reference exactly there.
