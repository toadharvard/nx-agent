# l5-platform-w10-001: the platform product root on the trunk line

Branch claude/fnv-l5-platform-w10 (trunk a2453fe9 + L5 gates w4r 0533f920). Root `ports/fnv-nx/platform`, `build.json` fnvnx-recipe/1, admitted
(`target guest`, `cxx_origin native`, all REQUIRED_PRODUCT gates named). `recipe.py check`: valid. build_guest.sh is deleted (no second build path).
Not done on this branch: FreeType (needs L1's `sources` key), the host product root (platform_host/ is not admitted), tools (platform_tools/ not admitted).

## Numbers (global, whole root)
- Units in the root: 73 (tracked sources plus 3 generated: combridge.gen.cpp, combridge_exports.gen.cpp, msvcrt_table.gen.cpp; the .inc outputs are includes, not units).
- Build as committed (no shim/loader include dirs, they have no admitted root): 13 green / 60 red. Causes: fnvnx_win32.h missing 42, fnvnx_callback.h 8,
  dsound.h 3, dinput.h 3, fnvnx_importkey.h 1, xinput.h 1 (all shim/loader headers: 55 units), libstdc++ 15 under GCC 13: 1 (swkbd.cpp),
  netdb.h 1 (fixed since: libnx external/bsd/include added).
- MEASUREMENT ONLY (a temporary unpushed branch with shim/include and loader/include copied into platform/_census_*): 65 green / 8 red. Causes:
  (a) libstdc++ 15.2 headers under the msvcmodel GCC 13.3: 6 units (crt/cpplib.cpp, crt/helpers.cpp, input/swkbd.cpp, win32/registry.cpp,
      registry_seed.cpp, window.cpp), first error `bits/alloc_traits.h:123:7: explicit template argument list not allowed` (variable-template
      partial specialisation `__has_max_size<_Alloc, __void_t<...>>`); 1 unit (win32/ini.cpp) `no matching function for call to __alloc_traits::construct`
      (std::unordered_map with the platform's w32_alloc, same headers). Owner: L2 (compiler/plugins) with L1 (library mirror pairing).
  (b) FreeType: win32/gdifont.cpp: `ft2build.h: No such file` (the external tools/freetype holds the Ubuntu source tarballs, not a header tree).
      Owner: L1 (recipe `sources` key + an unpacked FreeType mirror).
- Stages (measurement run, cold for the compiler stages): 141 run / 8 hit; the same build run again: 0 run / 149 hit (the build is red, so the warm run is over the
  stages that exist).
- Plugins: all 8 of PLUGIN_SOURCES are in build.json in order; their stages are built and cached; no .so sha256 is in objects.json because the build is red.
- wchar_t (-fshort-wchar vs the 4-byte wchar_t of the a64 mirror: `__WCHAR_TYPE__ unsigned int`): from the undefined symbols of the 65 built objects
  (231 symbols not defined inside the root): 0 symbols of wchar.h/wctype.h, 0 demangled names with wchar_t or wstring. The 8 red units are not measured.
  The mirror README does not record -mabi=ilp32 compile flags nor -fshort-wchar (the library is built without it): a mirror rebuild if L1 requires it.
- Verdict (commit ae960daa, before the w4r merge): pure 38/38 green, selftests 56/56 green, oracle 56/56 green; admit RED 0/585 (the 11 gates forms, d3d9total, r7,
  namegate, abi, datamodel, order, fpexact, eh, exepin, addr32 are named by the root and missing from gates.json: owners L1/L2/L3); build 14/73; objstub,
  linkstrict, determinism (0/73), argordergate (0/65), n1 n2_* xc xunit rtti pcrel bodysel abs disp reach reachlink gpa imports machine: red, input errors
  (they need a green build of the root first). Verdict took 387 s (budget 300).

## Pins (fnv-nx-build mirrors, tree_sha256 checked with cas.py ingest)
gcc-msvcmodel b4057704 b60eafe7...; a64 00e3d42a 3f25152c...; libnx 518bf11a 28642e36...; minimp3 8008c550 6a1b01eb...; nls dc698d02 eab72b37...;
mc f82dd110 92291602...; freetype 269e23eb a38b0926...; manifest (l5-platform/manifest) 3f220e95 ed7e3549...; wine-specs 678c780e d21bd712...
(full values in platform/build.json). imports.in.json and registry.in.json now name these externals; imports gate got an optional `specs_external`.

## Open (reported, not patched)
1. shim/include and loader/include have no admitted root. Headers platform units include: shim fnvnx_win32.h fnvnx_callback.h dinput.h dsound.h xinput.h
   fnvnx_iobuf.h; loader fnvnx_importkey.h fnvnx_reach.h fnvnx_start.h. Owners: shim = the shim lead (L3a?), loader = loader lead; need admitted roots.
2. toolchain/guest runtime library files (runtime.c threads.c fs.c section_guest.c tm.cpp crt0.s guest_pie.specs build.sh tests/): `libfnvnx-guest.a`, built by
   toolchain/guest/build.sh; owner = the loader lead (commits "Take the loader lead's guest runtime and interface headers"). Not built here, not on this branch.
   os_guest.c and platform_glue.cpp moved to platform/guest/; toolchain/include (4 headers) comes with this branch to the toolchain root.
3. platform/manifest/import_escape.py and env_const_export.py still describe the old build (clang jobs, compile_cmds.txt); they are stale tools.
4. The tests that used host-side files are not on this branch (tests/ was not part of the copy).
