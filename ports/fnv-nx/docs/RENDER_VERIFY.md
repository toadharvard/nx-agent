# fnv-nx native renderer: independent verification (stream V6n, decision D14)

V6n checks the native deko3d renderer (S6n, `render/native/`, `docs/RENDER_NATIVE.md`) against Direct3D 9 semantics.
The correctness reference is Gallium Nine (Mesa st/nine, the path S6c ships) running on softpipe on the host. V6n
sees S6n's code and claims only. It derives D3D9 semantics from the D3D9 documentation and from Nine's source, and
treats every S6n claim as something to falsify.

Everything here is rebuilt from scripts. Decomp-derived tables (pass names, address lists, enumerations) are written
to `build/` (git-ignored) and never committed; this document carries counts and method only.

## 1. The real pass numbering

### 1.1 The finding

The decomp's enum header `src/bsshader/bsshader_shader_layout.h` (`BSShaderManager::eBSSMPassEnum`, 756 named
values, `COUNT = 756`) is **not** the numbering the FNV binary uses. It is a re-sorted/older list. The binary's own
numbering has **759** ids (0 = disabled, 1..758), and family blocks sit at different offsets.

### 1.2 Method (`render/verify/enum/pass_names.py <decomp> build/v6n`)

1. **Primary anchor: the binary's pass-name function** `FUN_00b4f9d0` (in `bsshadermanager.cpp`). It is a
   `switch (pass) { case N: return "BSSM_<name>"; }` compiled from the real enum. Every case label is a true id
   with its true name: **742 cases, 742 distinct ids, range 1..757**, no id or name twice.
2. **Join by name** with the header and print constant-offset runs. 737 names join; the header blocks map to
   the true blocks with offsets from +0 (ZONLY..DEPTHMAP) through +2 (grass, no-alpha), +58 (ADTS/ADT...),
   +64 (DIFFUSEPT2..TEXTURE), +70 (2x_SPECULAR and the 3X block), +74 (land ... sky), +80 (water), +86 (water depth,
   blood splatter, shadow volume, tile). The header's "unused" block (673..755, 83 values) maps **into the middle
   of live ranges** (AMBIENT* -> 88..96, AMBDIFFTEX* -> 100..146, DIFFUSEDIR/PT -> 300..305, ENVMAP* -> 580..583,
   SELFILLUM* -> 721..727, ...): those are live passes in FNV, not unused ones. 53 offset runs in all.
3. **Holes**: 15 true ids in 1..757 have no case. They are filled by the header neighbours of the same run:
   376+1..6 = the six `3XPLACEHOLDER*` (header 307..312, offset +70), 567 = `DISTANT_TREE_SILHOUETTE` (+74),
   599..603 = the five `VFOG_*` (+80), 705 = `WATER_NO_REFLECTIONS_Vc` (+80; the decomp lost this case, it left a
   `$LN85 aiShader == 705` label stub in the function), 344/345 = two of the header's four `SPECULAR{DIR,PT}_Sb`,
   `SPECULARDIR_{Mb,Md}` (ambiguous, both unemitted). `IMAGESPACE` (header 672, offset +86) is **758**.
4. **Independent anchors** (each one is code that uses an id, not a name):
   * `COUNT = 759`: the pass-flag dump in `bsshaderproperty.cpp` names entries only `if (type < 0x2f7)`;
     ShadowLightShader's per-pass arrays are `[759]` (`DAT_011fdff8`, `DAT_011fcc80`, `DAT_011fc0a0`, `DAT_011fb4c0`,
     `DAT_011fa8e0`).
   * Lighting30 (3X family): `index = pass - 376` with special cases 730/731/736/737/742/743/748/749 -> 104..111,
     and a 112-entry local table. True 3X block = 376..479 (104 ids) + the eight `3XWATER_DEPTH*` ids. Header: 306..409.
   * Blood splatter emits `flag + 0x2f0` = 752/753 = true `BLOOD_SPLATTER_FLARE`/`BLOOD_SPLATTER` (header 666/667).
   * The batch renderer excludes alpha passes `0x238..0x23e` (568..574) from depth sorting: true SKYBASEPRE..
     SKY_CLOUDS_UNDERWATER. Under the header, 568 would be WATER_STENCIL. So `FIRST_ALPHA_PASS` = **568**, not 494.
   * Accumulator render groups (`RenderPassGroup(first, last, ..)` literals in `bsshaderaccumulator.cpp` and
     staging): 1-567, 568-603, 604-647, 648-649, 650-683, 684-718, 719-727, 728-751 and the whole frame 1-753
     (0x2F1). The last group ends exactly at the true BLOOD_SPLATTER (753); under the header 753 is "unused".
   * Emitter-family agreement: every literal pass id passed to `ShaderPassList::Add` (FUN_00ba9ee0) was looked up
     in both numberings. Under the true numbering every emitter emits ids of its own family (the 1x texture-pass
     file emits 330..338 = TEXTURE*, water emits WATER*, sky emits SKY*, grass shadows 644..647 = GRASS_SHADOW*);
     the only literal not in the name function is 567 (the hole above, emitted next to DISTANT_TREE_BASE = 566).
     Under the header numbering the same ids land in foreign families (330 = 3XLIGHTING_HSpc in the 1x texture
     file) and 30 of them in the "unused" block.

### 1.3 Consequences for S6n's claims

`docs/RENDER_NATIVE.md` §1.3 states "756 values, 667 live, FIRST_ALPHA_PASS=494", "3X* passes 306-409",
"WATER* 568-638", "WATER_DEPTH* 642-665", "GRASS_* 20-59, 524-567", "NOLIGHTING* 66-87". All of these come from the
header and are wrong for FNV (only the 0-offset blocks, ZONLY..DEPTHMAP 1..19 and NOLIGHTING 65..87, survive).
Anything in S6n's code that keys on header pass ids is suspect (checked in `render/verify/STATUS.md`).

## 2. The pass space ("all situations")

`render/verify/enum/pass_space.py <decomp> build/v6n` joins, per true pass id, what the engine **emits** with how
it is **configured**, and writes `build/v6n/pass_space.tsv` (columns: id, name, class, in accumulator group, emit
sites and kind, how configured, VS binary + HLSL source + defines, PS binary + source + defines, static render
states, sampler-stage setup, the four SLS per-pass int arrays, skin and vertex-colour flags) plus
`emit_sites.tsv` and `overrides.tsv` (render-time state overrides). Method, per source:

* **emitted**: all 347 eight-argument calls of `ShaderPassList::Add` (FUN_00ba9ee0). Literal ids, or expressions
  resolved locally (C ternaries and `+`, free boolean terms enumerated over {0,1}, local `v = ..; v += ..;`
  substitution): 346 resolved, 1 unresolved (the id is a parameter of a forwarding helper).
* **configured**: ShadowLightShader by its global pass array (`DAT_011fdff8[759]`, setters for VS/PS/render
  states/stages and the per-pass int arrays; VS/PS references resolved to `SLS1/SLS1S/SLS2%03i.vso` and
  `SLS1/SLS2%03i.pso` and joined with the developer tables: 156 VS entries, 203 PS entries); Lighting30 by its
  `pass - 376` index map; Water by its 74-entry member array (base 648, inferred from the accumulator group);
  other classes by the ids their own files dispatch on (`case`, `==`, range checks); classes with one or two passes
  and no dispatch are marked "single-pass (inferred)".
* **in group**: the id lies in one of the accumulator's render groups. **reachable** = emitted and configured
  and in a group.

Counts (2026-11-04, decomp 87eb937):

| class | declared | named | configured | emitted | in group | reachable |
|---|---|---|---|---|---|---|
| ShadowLight | 403 | 401 | 338 | 227 | 403 | 212 |
| Lighting30 | 120 | 114 | 118 | 36 | 120 | 34 |
| TallGrass | 84 | 84 | 44 | 44 | 84 | 20 |
| Water | 73 | 72 | 71 | 71 | 73 | 69 |
| NoLighting | 22 | 22 | 22 | 22 | 22 | 22 |
| Sky | 13 | 13 | 10 | 10 | 13 | 10 |
| SLS water depth / NoLighting water depth | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 |
| VolumetricFog | 5 | 0 | 0 | 0 | 5 | 0 |
| DistantLOD | 3 | 3 | 2 | 3 | 3 | 2 |
| ShadowVolume | 3 | 3 | 0 | 0 | 0 | 0 |
| SpeedTree leaf / frond, DistantTree | 2 / 1 / 2 | 2 / 1 / 1 | 2 / 1 / 2 | 2 / 0 / 2 | 2 / 1 / 2 | 2 / 0 / 2 |
| Particle, GeometryDecal, BloodSplatter | 2 / 2 / 2 | 2 / 2 / 2 | 2 / 2 / 2 | 0 / 2 / 2 | 2 / 2 / 2 | 0 / 2 / 2 |
| Bolt, Beam, Precipitation, Tile, ImageSpace | 1 each | 1 / 1 / 1 / 1 / 0 | 1 each | 1 / 0 / 1 / 0 / 0 | 1 / 1 / 1 / 0 / 0 | 1 / 0 / 1 / 0 / 0 |
| **total** | **758** | **742** | **637** | **439** | **753** | **395** |

Reading the numbers: **395 pass ids are statically reachable** (emitted by a pass-list builder, configured by
their class, and inside a render group). The 439 emitted ids are the lower bound of "situations" the native
renderer must reproduce; ids with no static emitter (particles, beam, tile, image space, volumetric fog) are
selected through non-`Add` paths (their own render calls) and are covered by the scene-level capture of §6.
Static VS/PS bindings were found for 69 SLS passes (the remaining SLS passes take their shaders from setup
helpers that the line scanner does not follow; the pass-space table marks them, the capture/replay of §6 closes
the gap at run time).
