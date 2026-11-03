# render/nine/precompile: offline nvc0 shader cache (stream S6d-nine-precompile)

Gallium Nine translates each D3D9 shader to TGSI, and nouveau compiles the TGSI with nv50_ir when the
shader is created (Mesa 20.1 `nvc0_sp_state_create` calls `nvc0_program_translate`), and again for
every Nine variant and nvc0 clip-plane re-translation. On the console that compile runs on the A57s and
causes hitches. This stream does three things:

* `nine-precompile` runs the same compile on a PC, ahead of time, for the shaders and variants the game
  will use;
* patch 0003 makes nvc0 look up a cache file before it compiles;
* a host test checks that a cached program is byte-identical to a fresh compile.

## Pieces

| path | what |
|---|---|
| `render/nine/host/build_host.sh` | host build (x86-64 Linux, no GPU, no libdrm, no meson): st/nine's shader translator, TGSI, nv50_ir and `nvc0_program.c` with patch 0003, and the tools |
| `render/nine/host/nvc0_fake_screen.c` | fake nvc0 `pipe_screen`. It `#include`s `nvc0_screen.c`, so the cap queries are nvc0's own functions, run on a zeroed screen that holds only chipset, VRAM size and `class_3d` |
| `render/nine/host/host_stubs.c` | two NIR entry points that Nine references but never reaches (the TGSI path is preferred) |
| `nine_host_compile.[ch]` | one compile = Nine `nine_translate_shader` + a pipe_context whose `create_vs/fs_state` does what `nvc0_sp_state_create` does; plus the program comparison |
| `nine_precompile.c` | the CLI |
| `patches/0003-nvc0-precompiled-cache.patch` | the runtime cache (`nvc0/nvc0_program_cache.[ch]`, a wrapper around `nvc0_program_translate`, fixup/relocation (de)serialisers in codegen) |
| `tests/s6d/` | extra SM1-3 corpus, the cache test harness, and the runner |

The nouveau sources are built on the host with `-D__SWITCH__`, as on the console, so the code paths
are the same: devkitPro's tree compiles out NIR and the Fermi emitter on Switch.

## Build and use

```sh
render/nine/host/build_host.sh <work> [jobs]        # MESA_SRC=<patched tree> reuses one, else clones + patches
<work>/out/bin/nine-precompile -o nvc0_shaders.cache --manifest manifest.tsv \
        [--variants ctor|common|all] [--chipset 0x120] [--fresh] shaderpackage*.sdp raw.vso ...
python3 tests/s6d/run.py --host <work>/out --asm <S6b build>/s6b_asm
```

* Input: `.sdp` packages, read with S6b's reader (`render/shaders/src/sdp.c`), or raw D3D9 token streams.
* Each shader is compiled in a child process. A compiler crash becomes a `crash(signal N)` row in the
  manifest and does not stop the run.
* `--chipset`: what libdrm_nouveau reports on the console. The default is **0x120**: devkitPro's
  libdrm_nouveau sets `chipset = nvgpu arch` (`NVGPU_GPU_ARCH_GM200`) without the implementation bits,
  so GM20B shows up as 0x120, not 0x12b. The chipset is part of the key. The test checks that 0x12b
  generates identical code (25/25 shaders).
* The manifest has one row per compile: bytecode SHA-1, name, model, variant, Nine's own 64-bit variant
  key, status (`stored` / `cached` = a duplicate of an earlier TGSI / `nine-fail` / `nvc0-fail` /
  `crash`), TGSI tokens, code bytes, GPRs, cache key.
* On the console: put the file on the SD card and set `NVC0_SHADER_CACHE=<path>`, or call
  `nvc0_pcache_configure(path, flags)` from the host module before creating the device.
  `NVC0_SHADER_CACHE_READONLY=1` (or `NVC0_PCACHE_READONLY`) stops misses from being appended. Without
  a path the cache is off and nvc0 behaves exactly as before.

## Cache key and value

The cache is keyed at the nvc0 level, not the Nine level:

`key = SHA-1(compiler_id[16], chipset u16, type u8, num_ucps u8, pad[4], cp.smem_size u32, TGSI tokens)`

That is everything `nvc0_program_translate` reads from the program, plus the compiler identity. The
(bytecode, Nine variant) pair is one step upstream. Nine turns it into TGSI deterministically, so:

* a key computed from TGSI equals "hash(bytecode) + variant key";
* Nine variants that generate the same TGSI share one entry (413 variant compiles -> 326 programs on
  the test corpus);
* a variant the tool did not enumerate simply misses, compiles and is appended, and is never wrong;
* bytecode hash and Nine key are kept in the tool's manifest for diagnosis.

`compiler_id` = SHA-1 of `"nvc0-pcache/1 mesa-20.1.0-rc3 fnvnx-0003-r1 optLevel=3"` plus the size of
the metadata record. A file with another id is discarded (and rewritten when writable). **Bump the
`r1` in `PCACHE_ID_STRING` whenever codegen, TGSI or Nine's translator changes.** Programs with stream
output (Nine's swvp `ProcessVertices` path) and compute programs are never cached. Debug builds skip the
cache when `NV50_PROG_*` overrides are set.

Value = everything `nvc0_program_translate` leaves in `struct nvc0_program`, before upload:

* `code` (`code_size` bytes);
* header `hdr[20]`, `flags[2]`;
* `num_gprs`, `need_tls`, `num_barriers`, `parm_size`;
* `vp.{clip_mode, clip_enable, cull_enable, num_ucps, edgeflag, need_vertex_id, need_draw_parameters,
  layer_viewport_relative}`;
* `fp.{early_z, colors, color_interp[2], sample_mask_in, force_persample_interp, flatshade,
  reads_framebuffer, post_depth_coverage}`;
* `tp.tess_mode`, `cp.{lmem_size, smem_size}`;
* the relocation table;
* the fixup table.

Relocations and fixups are applied at upload (`nvc0_program_upload_code`), so they are stored
unapplied, as translate left them. Fixup entries hold function pointers. The cache stores them as
`(emitter family << 8 | index)`, through the `getFixupApply{GM107,GK110,NVC0}` tables that the patch
adds to the emitters, so a file written by the x86-64 tool loads on the console.

## File format (little-endian, `NVC0_PCACHE_FORMAT_VERSION` 1)

```
header  32 B : "NVC0PCC\0" | u32 version=1 | u32 header_size=32 | u8 compiler_id[16]
record       : u32 'PREC' (0x43455250) | u32 payload_size | u8 key[20] | u32 fnv1a32(payload) | payload
payload      : meta (fixed-width struct nvc0_pcache_meta, 148 B: chipset, type, presence bits, the
               fields above, code_size, reloc_count, fixup_count)
               | code[code_size] | reloc_count x {data, mask, offset, bitPos, type} u32
               | fixup_count x {function id, packed ipa:4/reg:8/loc:20} u32
```

* Append-only; if two records share a key, the first wins.
* Loading reads the whole file (there is no mmap on Horizon) and builds an open-addressing index of
  key -> payload in memory.
* Records are checked with their checksum. At the first bad or truncated record the loader stops, and
  a writable open truncates the file back to the last good record.
* Misses append under a mutex (C11 `call_once` + `mtx_t`, which work on newlib and glibc) with
  `fflush` after each record.

## Variant keys (Mesa 20.1 st/nine) and what the tool generates

Nine compiles a shader once at create time (the "ctor" info: every key field 0, swvp from the device
flags), then again for each new value of its 64-bit variant key (`NineVertexShader9_UpdateKey`,
`NinePixelShader9_UpdateKey`):

| stage | key bits | source | `common` | `all` |
|---|---|---|---|---|
| vs | 0-3 | shadow (depth-compare) samplers, masked by used samplers | 0 | 0, each used sampler alone |
| vs < 3.0 | 8 | `D3DRS_FOGENABLE` | 0, 1 | 0, 1 |
| vs | 9 | software vertex processing | 0 (adapter is HW VP) | 0 |
| vs | 16-23 | int/bool constant combination (only with `shader_inline_constants`, off in `nine_adapter.c`) | - | - |
| vs writing oPts | 32-63 | `POINTSIZE_MIN/MAX` as halves | 1.0 / caps max (63) | same |
| ps | 0-15 | shadow samplers, masked by used samplers | 0 | 0, each used sampler alone |
| ps < 2.0 | 4-11 (6-17 for 1.4) | sampler texture types, 2 bits each (0 2D, 1 1D/unbound, 2 cube, 3 volume) | all 2D | + each sampler as 1D, cube, volume |
| ps < 1.4 | 12-19 | `D3DTTFF_PROJECTED` per stage | 0 | + each stage alone |
| ps < 3.0 | 20, 21-22 | `FOGENABLE`, `FOGTABLEMODE` | off, on+NONE (vertex fog), on+LINEAR | off, on x {NONE, EXP, EXP2, LINEAR} |
| ps | 22 | RT0 multisampled -> colour inputs centroid | 0 | 0, 1 |
| ps | 24-31 | int/bool constant combination (inline constants only) | - | - |
| nvc0 vp | - | user clip planes: re-translate with `num_ucps = last plane + 1` | 0, 1 | 0..8 |

Dimensions are enumerated one at a time on top of the defaults (no cross product), except fog x centroid
for ps and fog x clip planes for vs. Note that Nine 20.1's ps key overlaps itself: `FOGTABLEMODE << 21`
spills into bit 22 (centroid) for EXP2 and LINEAR. That is harmless here, because the cache is keyed by
TGSI.

**What still compiles at runtime:** combinations of more than one shadow sampler, ps_1_x
type/projection combinations across several samplers, point-size states other than the defaults,
`SetClipPlane` counts not in the set, swvp / `ProcessVertices` (stream output, not cached at all), Nine's
fixed-function shaders (`nine_ff.c`, generated from FF state, not fed to the tool), and anything Nine
creates internally. Each of these is compiled once on first use and appended to the file, so a second
run of the same scene hits. Running the game once with a writable cache and shipping that file covers
whatever the enumeration misses.

## Gate (`tests/s6d/run.py`, measured 2026-11-03 on the host)

Corpus: the 17 S6b test shaders + 9 written for S6d (`tests/s6d/shaders.s6d`): skinning through a0,
normal map + projected shadow, texkill/lrp/pow, oPts, ps_1_4 phases + cnd, ps_1_1 four-stage, vs_3_0
texldl + loop aL + bool if, ps_3_0 vFace/vPos/dsx/dsy/texldd/rep/break, vs_2_0 if b / rep i.

* `nine-precompile --variants all`: 26 shaders, 25 compile, 413 variant compiles, 0 Nine failures,
  0 nvc0 failures, 326 distinct programs, 230 KB of code, 251 KB cache file (`common`: 99 compiles,
  72 programs, 52 KB; `ctor`: 25 programs, 17 KB). Host time: 0.23 s for `all`.
* `s6d_cache_test`: 100 shader variants (ctor, fog, fog-linear/centroid for ps, 1 and 6 clip planes for
  vs), each compiled three ways (cache off / miss + store / hit after re-reading the file read-only).
  The cached program is **byte-identical** to the fresh compile in every case:
  * every field;
  * code;
  * relocation and fixup tables;
  * code after relocation + fixups for all four (`force_persample_interp`, `flatshade`) combinations.

  64 programs carry fixups; none of the corpus produces relocations, so the relocation serialiser is
  covered by a round-trip unit check. Robustness checks: corrupt payload, truncated tail, foreign
  compiler id, duplicate record, cache off. In total 614 checks, 0 failures.
* **Found: `vs20_call` (SM2 `call`/`label`/`ret`) crashes the stock compiler** (SIGSEGV in nv50_ir
  `BindArgumentsPass`). The fault is in Nine 20.1's TGSI. `CALL` before `LABEL` uses a label slot that
  does not exist yet: `inst_labels` is reallocated one element short and `num_inst_labels` is never
  updated. No `BGNSUB`/`ENDSUB` is emitted. The subroutine's final `ret` is dropped as "last
  instruction". The same crash happens on the console, at `CreateVertexShader`. Fix candidates: inline
  subroutines in Nine (`tx->inline_subroutines`), or fix CALL/LABEL. fxc rarely emits `call` for SM2/3
  game shaders, but it is a hard crash.

## Limits

* The host build is x86-64 and the console is AArch64. nv50_ir does constant folding with host floats.
  The host build uses `-ffp-contract=off`. AArch64 GCC may contract `a*b+c` in the compiler's own
  folding code, so a folded immediate could differ by 1 ulp between a host-made entry and an on-console
  compile of the same TGSI. Both programs are valid compiles; the cache serves the host one. Not
  observed; a console run comparing on-device compiles with the shipped file would settle it.
* Lookup cost: one SHA-1 over the TGSI (a few hundred tokens) per create, plus an index probe. The
  whole file is in memory (about 0.8 KB per program).
