# L2 w3: plugin census of the platform root (claude/fnv-l5-platform-w10 f872bcd, f3a5bd4, root ports/fnv-nx/platform)

Status: partial. 73 units seen: 14 OK, 58 fail in the preprocessor (non-plugin), 1 fails in CC (non-plugin, see below); plugin errors: 0. The earlier census on a temp root is withdrawn.

Commits: L5 branch claude/fnv-l5-platform-w10 f872bcd2b2d3 and re-run on f3a5bd4 (recipe check: valid), identical result, msvcmodel b4057704, L2 w3 branch merged with origin/claude/fnv-nx-clean.
Build: `recipe.py build --root ports/fnv-nx/platform` (fresh store, unchanged checkout) -> red at stage pp, 0 objects. Every unit that failed failed in the PREPROCESSOR (non-plugin); no CC stage ran for them, so no plugin diagnostic exists.

OK (11 C++ under all 8 plugins + 3 C): audio/backend ds3d mixer reverb, boundary/host_hooks, clock/clock_real clock_rt clock_virtual, com/combridge, crt/msvcrt_table.gen, win32/inflate (C++); guest/os_guest.c, os/os_world.c, os/sock_guest.c (C).
CC failure: input/swkbd.cpp: /ext/a64/aarch64-none-elf/include/c++/15.2.0/bits/alloc_traits.h:123:7: error: explicit template argument list not allowed (libstdc++ 15.2 headers of the fnvnx-a64 external under the GCC 13 msvcmodel compiler; not a plugin message).
First preprocessor error of each of the 58 units:
| count | missing header (non-plugin) |
|---|---|
| 41 | fnvnx_win32.h (via fnvnx_win32_ext.h:11) |
| 8 | fnvnx_callback.h (via crt/fnvnx_msvcrt.h:15) |
| 3 | dsound.h (combridge_if.gen.h:5; audio/dsound.cpp:14) |
| 3 | dinput.h (input/dinput.cpp, keymap.cpp, register.cpp) |
| 1 | xinput.h (input/xinput.cpp) |
| 1 | fnvnx_win32.h (via include/fnvnx_render_hooks.h:19) |
| 1 | fnvnx_importkey.h (boundary/boundary_guest.cpp:11) |
The headers are shim/loader/toolchain include dirs that the root does not provide yet (L5's note: no admitted root for them). I did not work around it. Re-run on the next commit of the branch.
