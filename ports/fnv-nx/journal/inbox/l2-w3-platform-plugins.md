# L2 w3: measurement build of L5's platform sources with the guest compiler and all 8 plugins

Measurement only: not a product root, nothing committed under platform/. Our own platform code only.

## Commits
- L2 branch head: 40ee1c1 (claude/fnv-l2-source), merged with claude/fnv-nx-clean d7fac9b (merge dbba171 on claude/fnv-l2-source-w3).
- Platform sources: claude/fnv-l5-platform 10a3bc7cf90e (ports/fnv-nx/platform + toolchain/guest/{os_guest.c,platform_glue.cpp}).
- msvcmodel compiler: fnv-nx-build tools/gcc-ilp32-msvcmodel b4057704 (verify/guest_compiler.json).
- Plugins: all 8 of recipe.PLUGIN_SOURCES / PLUGIN_ARGS, flags = recipe.SEMANTICS_FLAGS + build_guest.sh's flags.
- Externals (commit): libnx 518bf11a (nx/external/bsd/include), minimp3 8008c550. FreeType headers: 2.13.2 orig tarball of mirror tools/freetype 269e23eb, include/ only (the mirror holds a tarball, not a tree: not an external).

## Units: 72, not 76
build_guest.sh's own list (platform/{win32,crt,audio,input,boundary,com,os} + clock_rt, clock_real + toolchain/guest/{os_guest.c,platform_glue.cpp}, minus the 5 HOST_SIDE files) is 72 units: 64 C++ and 8 C. FreeType units not included (C, no plugin).
Generated inputs were produced outside the recipe with the repo's own generators (nls_tables.inc, winerr_msgs.inc, *env_const.gen.inc, combridge*.gen.*; tools/nls and tools/mc mirrors) and placed as files of the measurement root.

## Result A: flags exactly as build_guest.sh (with -w)
| outcome | units |
|---|---|
| OK | 7 (C units: crt/heap.c iob.c math.c stdio.c string.c, os/os_world.c, toolchain/guest/os_guest.c) |
| plugin error fnvnx_names, "NAMES1: '-w' hides the class-key reports the names are computed from" | 63 (every C++ unit that reached CC; the check is at line 1 of the unit, a flag check) |
| non-plugin | 2 (below) |
Finding for L5: `-w` in COMMON must go for C++ (fnvnx_names refuses it).

## Result B: same, without -w (C and C++)
| outcome | units |
|---|---|
| OK (all 8 plugins) | 66 |
| plugin error | 0 attributed to a named plugin |
| compiler ICE with all plugins loaded (not attributed) | 4 |
| non-plugin | 2 |
Plugins sdd/addr/names/pi/dispatch/layout/order/ftoi reported no diagnostic of their own on 66 units.

| unit | first diagnostic |
|---|---|
| platform/win32/ini.cpp | ini.cpp:124:9: internal compiler error: in expand_expr_addr_expr_1, at expr.cc:8710 (fn `pstring fold(const char*)`, during RTL pass: expand, via expand_builtin/store_expr) |
| platform/win32/registry.cpp | registry.cpp:62:13: same ICE (fn `std::string fold(const std::string&)`) |
| platform/win32/gdifont.cpp | gdifont.cpp:287:22: same ICE (a lambda `unesc` building a std::string) |
| platform/win32/window.cpp | window.cpp:490:14: same ICE (`rlist(w->upd, w->upd + w->nupd)`) |
| platform/os/sock_guest.c | non-plugin: libnx bsd/include/netdb.h:68: `sys/_types.h` not found (that header is newlib's; the guest compiler's libc headers are the distribution glibc cross headers) |
| platform/win32/winsock.cpp | non-plugin: libnx bsd/include/netinet/in.h:44: `sys/_types.h` not found (same) |
The ICE is a GCC abort in expand_builtin under 7 loaded plugin events (PLUGIN_PRE_GENERICIZE: pi, order, layout; others at unit start/finish/IPA); the plugin is not isolated (a native root must load all 8, so I did not drop any). First suspect from the pass ("during RTL pass: expand", builtin with an ADDR_EXPR operand): fnvnx_order (args=rtl) or fnvnx_pi; unverified.

## Other non-plugin notes (for L5/L1, not counted above)
- The recipe mounts each `include` directory flat at /inc/N and builds the PP view from the closure. Headers that include `../os/os.h` from platform/include (fnvnx_clock.h) do not resolve with the `include` list of build_guest.sh; I used the whole measurement root as include 0 plus `-isystem/-iquote /inc/0/<dir>` flags (accepted by toolflags). L5's real root will hit this.
- The environment here lacked the env.json packages libgmp-dev/libmpfr-dev/libmpc-dev (plugin build: gmp.h) and the *-arm64-cross packages (the external compiler's headers are symlinks to /usr/aarch64-linux-gnu/include); I installed them at env.json's versions (farm/setup_verdict_env.sh, apt part). The tool-runtime cache is keyed before that: a store made without them is stale.
