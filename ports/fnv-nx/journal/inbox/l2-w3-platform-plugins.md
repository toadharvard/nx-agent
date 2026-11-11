# L2 w3: plugin census of the platform root (claude/fnv-l5-platform-w10 f872bcd, root ports/fnv-nx/platform)

Status: NO plugin census possible yet; the root does not preprocess. The earlier census on a temp root is withdrawn.

Commits: L5 branch claude/fnv-l5-platform-w10 f872bcd2b2d3 (recipe check: valid), msvcmodel b4057704, L2 w3 branch merged with origin/claude/fnv-nx-clean.
Build: `recipe.py build --root ports/fnv-nx/platform` (fresh store, unchanged checkout) -> red at stage pp, 0 objects. Every unit that failed failed in the PREPROCESSOR (non-plugin); no CC stage ran for them, so no plugin diagnostic exists.

Units seen: 58 (stage results of this build). First preprocessor error of each unit:
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
