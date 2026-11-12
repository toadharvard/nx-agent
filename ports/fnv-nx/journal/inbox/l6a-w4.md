# l6a-w4: deko3d backend as product root ports/fnv-nx/render/backend (RED, two blockers)

Branch claude/fnv-l6a-deko-w4, based on trunk 08f6ea3. Recipe: host target, LP64, aarch64-linux-gnu-gcc-13 (env.json), -nostdinc.
Files: 29 copied byte-for-byte from claude/fnv-l6a-deko 4335c142 (units: 18 nx/dk_*.c + nxgpu4_check.c = 19 C units, 0 C++, 0 asm;
headers: dk.h dk_fmt.h dk_msres_dksh.h dk_scale_dksh.h dk_state.h gpu_abi.h state.h nxgpu4.h nxgpu4_check.h nxgpu4_shader.h = 10),
+ dkabi.tsv (77 lines, byte-for-byte) + build.json = 31 files. Moved from render/d3d9/backend to render/backend as told.
No gl4_*, no build/bootstrap scripts, no selftest/. No plugins (all C).
Externals (fnv-nx-build, commit / tree_sha256):
- libnx tools/libnx 518bf11ad6a52149e43fbd0fa83b0087a430f446 / 28642e369e75dfe075aac6ee427a82ee9e0cbbe6941be36b0e977da3c96d9298 at /ext/libnx (switch.h, switch/*, from nx/include)
- deko3d tools/deko3d-373bfd43d8b2 efc4858975273ddae82c03745e5c2d8b2bdda893 / 5a51dcc3f4b66baef807a77c18b80366fee145031946e3973b4adbd8ed32defe at /ext/deko3d (include/deko3d.h)
- fnvnx-a64 tools/fnvnx-a64-msvcmodel 00e3d42acd10593d534b9a70bb8940cee7991436 / 3f25152cbc5a729d9e0a12fc5c62b6b912b6ab1571b6b3494de8f35e3904207d at /ext/a64
  (newlib headers aarch64-none-elf/include, needed by libnx's sys/lock.h; gcc builtin headers lib/gcc/aarch64-none-elf/15.2.0/include, named inside the pinned tree, so -nostdinc works; default multilib = LP64)
- (gcc-msvcmodel b4057704, tree b60eafe7... verified by cas ingest, was used only before the host correction; not in the recipe now)
Hashes computed by cas.py ingest of `git archive` exports (no .git).

## Results (global, 19/19 units)
- recipe.py check: valid. envcheck: OK.
- build x2 fresh stores: RED both, stage pp:nx/dk_blit.c, units compiled 0/19, stages run 0 hit 0 (equal: yes, same failure; objects.json equal is vacuous).
  `aarch64-linux-gnu-gcc-13: error: unrecognized command-line option '-mtp=soft'`
  BLOCKER 1: -mtp=soft is a devkitPro patch of gcc 15 (fnvnx-a64); the pinned gcc-13 of env.json does not have it. Not substituted in the committed head.
- Experiment (local, not committed): without -mtp=soft the build stops at cc:nx/dk_blit.c (the build aborts at the first red stage, so the other 18 units are not judged):
  /ext/a64/aarch64-none-elf/include/sys/lock.h:30,36,50,51: -Werror=unused-parameter, -Werror=return-type (newlib header, reached from libnx mutex.h via dk.h:33).
  BLOCKER 2 (machinery gap): recipe `include` gives -I; there is no -isystem for external headers, so warnings inside newlib/libnx headers fail -Werror (devkitA64 treats its include dirs as system dirs). Needs an `isystem` key in recipe.py (verify/*.py not edited by me).
- L1 verdict (l1.py verdict, branch commit affb6b14, tree 4c7e0572, 260 s, 23 gates): red. green: pure 35/35, selftests 51/51, oracle 51/51, argordergate 0/0.
  red: admit 0/452 (13 required product gates are not in gates.json: abi addr32 d3d9total datamodel eh exepin forms fpexact imports machine namegate order r7; all 452 files uncertified because of that, not because of the root),
  build 0/19, determinism 0/19, objstub, linkstrict, n1, n2_data, n2_call, n2_vcall, xc, xunit, rtti, pcrel, bodysel, abs, disp, reach, reachlink, gpa (all 0/1: no objects.json).
- undefined-symbol set vs dkabi.tsv: not available (no object built).
- dkabi.tsv publish to fnv-nx-build l6a/dkabi: not done (build red; the session has read access only to fnv-nx-build).
- Plugin mechanism: exists in trunk recipe.py (plugins/plugin_cxx, 08f6ea3); not used, since the root has no C++ unit.

## Update (branch head fbd1c779; supersedes the red results above)
Everything above is history; the current state of the root is below. All numbers are global (19/19 units of render/backend).
- Commits on top of the first report: aed30f4 NXSP v2 (G table), 2952e46 NxShaderD3D key[32] (size 64, offset asserts), 16893be NXSP v2 D table in dk_shader.c/dk.h (48-byte header, offsets >= 48, D rows 64 bytes, lookup by sha256 key with stage/variant/link/bytecode byte compare; fnv1a64, crc32, pack_find, the "%016llx" lookup and the variant/link refusal deleted; only ver 2 is read), fbd1c779 revert of the stack-protector/fortify flags.
- Measured on a scratch merge of w16 dbfaefeb (merge b5e8f078, not pushed) with fbd1c779: specdefaults passes; two fresh stores, both green, 19 objects, stages run 38 hit 0; the 19 object sha256 are equal across the two runs and none equals the 885852ac run's (expected: different flags and w16 keys).
- Undefined set: 183 raw, 97 defined in the root, 86 external = 74 dkabi.tsv rows + 9 libc (clock_gettime free malloc memcmp memcpy memset realloc snprintf vsnprintf; strcmp/strncmp left with pack_find) + 3 embedder hooks (nxdk_guest_ptr nxdk_on_present nxdk_shader_pack). No __*_chk and no __stack_chk_* names. TLS relocations and TLS symbols: 0.
- build.json states no new target option: the specdefaults stage asked for none beyond -march=armv8-a+crc+crypto, -mtune=cortex-a57, -fPIE (as before); sources of those values: devkitA64 switch rules / build_dk.sh, per the lead.
- L1 verdict (l1.py) was not rerun after these commits.
- Not done: the shader producer (shaderpack.py, on claude/fnv-l6-render-w2b) still writes NXSP v1 and must write v2 with G and D tables; dkabi.tsv publication to fnv-nx-build l6a/dkabi stays with the lead. L6's variant bit 1 (FLAT) widens the D-table check to var <= 3 with a pixel shader refused when (var & 1); not applied, waits for L6's nxgpu4_shader.h.
- Environment findings (L1's, files not edited by me):
  1. The pip step of farm/setup_verdict_env.sh at 885852ac..de8ed638 failed (`pip install --require-hashes -r -`: "No such file or directory: '-'"). I installed z3-solver 5.4.1.0 with the sha256 of env.json from a requirements file: the same pinned wheel and hash.
  2. At dbfaefeb the script goes through farm/install_mirror.py, which crashes with `KeyError: 'autoconf'` (line 63, `doc["packages"][name]`). The three python modules then missing (gmpy2 2.3.2, keystone-engine 0.9.2, pefile 2024.8.26) I installed with exact versions and the sha256 of env.json python.wheel_sha256 (--require-hashes --no-deps, from the package index, not the mirror); envcheck.py then reported OK. I did not compare installed-files hashes beyond envcheck's own check.
- Status: deko3d work stopped by order; branch left at fbd1c779.
