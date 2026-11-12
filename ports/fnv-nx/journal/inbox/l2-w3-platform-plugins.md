# L2 w3: plugin census of the platform root (claude/fnv-l5-platform-w10 ba9e78a, root ports/fnv-nx/platform)

Root: `recipe.py check` valid at ba9e78a (shim and loader admitted as header roots). At the branch head 3dff7e3 `check` refuses the shim/loader include dirs (waits for L1 w16), so I measured ba9e78a. Built unchanged, no extra flags, fresh store, msvcmodel b4057704, all 8 plugins. Totals over all 73 units:

| outcome | units |
|---|---|
| OK (PP + CC under all 8 plugins) | 65 |
| plugin error (any of sdd/addr/names/pi/dispatch/layout/order/ftoi) | 0 |
| CC fail, non-plugin | 7 |
| PP fail, non-plugin | 1 |

Non-plugin rows (first error line):
| unit | diagnostic |
|---|---|
| crt/cpplib.cpp, crt/helpers.cpp, input/swkbd.cpp, win32/ini.cpp, win32/registry.cpp, win32/registry_seed.cpp, win32/window.cpp | /ext/a64/aarch64-none-elf/include/c++/15.2.0/bits/alloc_traits.h:123:7: error: explicit template argument list not allowed (libstdc++ 15.2 headers of the a64 external under the GCC 13 msvcmodel compiler; sent to L1) |
| win32/gdifont.cpp | pp: /src/win32/gdifont.cpp:20:10: fatal error: ft2build.h: No such file or directory (FreeType headers not provided by the root) |
The 7 CC units stop on the header error before any plugin result for them. The earlier ICE units (ini, registry, gdifont, window) are therefore not reached yet; ICE fixture: l2-w3-ice-fixture.md.
