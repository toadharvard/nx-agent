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
