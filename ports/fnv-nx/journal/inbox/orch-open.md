# Orchestrator: open items to the first frame (one list, checked every hour)

State 2026-11-11 21:40. Trunk claude/fnv-nx-clean bab699b9 holds tools and docs only: no product root,
so no game code is built or gated on trunk yet. Frames rendered: 0.

| # | item | owner | state |
|---|---|---|---|
| 1 | msvcmodel GCC mirrored: fnv-nx-build tools/gcc-ilp32-msvcmodel, commit + tree_sha256, pinned base | farm (L4) | building |
| 2 | external compiler in recipe (`{"external","cc","cxx"}`) | L1 | waits 1 |
| 3 | plugins as hermetic stages: order, ftoi, addr, dispatch, names, sdd, pi, vtnames | L1 w4 + L2 | w4 red (test_recipe, test_runtime) |
| 4 | w3 numpy red on test_recipe recipe.py:513 | L1 | open |
| 5 | fnvnx-a64 with msvcmodel.patch as external | L5 + L1 | L5 rebuilding |
| 6 | source externals: decomp, sse2neon, libnx, minimp3, libogg/libvorbis | L1 + L5 | open |
| 7 | loader product root (L1 owns; L5 copy with trap.c retired) | L1, L5 | open |
| 8 | platform + guest runtime product roots; dshow E_NOTIMPL out; crt how=inferred out | L5 | open |
| 9 | render product root; 29 markers to 0 | L6 | open |
| 10 | lifted pack product root: liftall generate-only, no g++/objcache/apt | L3a + L3b | L3a liftgen pushed |
| 11 | d3dx9_38 collect (32/32 lifted) and exe collect (64/64) | L3a | collect |
| 12 | rules/ root on trunk; s1b 224 uncertified sites become rules; r2_vorder on vft facts | L2 | woken 21:35 |
| 13 | gates.json: n1 n2 call data vcall xc xunit rtti pcrel bodysel disp abs reach imports d3d9total forms R7 | L1 | open |
| 14 | oracle gate (class 10); after-merge verifier runner.sh | L4 | open |
| 15 | exe fingerprint pin; cross deb hashes into setup_verdict_env.sh | L1 | open |
| 16 | trunk docs: loader rules, R9 TSO-MAP, N1-N4/R1 text; delete X86_FALLBACK, STATIC_CHECKS, ASM, Makefile | L1, L2 | open |
| 17 | cltree (N2) and fontstate (Wine font ref) producers | L2, L5/L4 | open |
| 18 | ELF link of 7-11 on trunk, green | L1 | waits 2-11 |
| 19 | boot in emulator to WinMain, menu, first frame | L4 | waits 18 |
