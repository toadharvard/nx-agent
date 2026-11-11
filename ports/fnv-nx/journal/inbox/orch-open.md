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
| 10 | lifted pack product root: liftall generate-only, no g++/objcache/apt | L3a + L3b | done on L3a 73a708c (liftgen, root lift/pack, gate_forms.py); not on trunk |
| 11 | d3dx9_38 collect (32/32 lifted) and exe collect (64/64) | L3a | collect |
| 12 | rules/ root on trunk; s1b 224 uncertified sites become rules; r2_vorder on vft facts | L2 | woken 21:35 |
| 13 | gates.json: n1 n2 call data vcall xc xunit rtti pcrel bodysel disp abs reach imports d3d9total forms R7 | L1 | open |
| 14 | oracle gate (class 10); after-merge verifier runner.sh | L4 | open |
| 15 | exe fingerprint pin; cross deb hashes into setup_verdict_env.sh | L1 | open |
| 16 | trunk docs: loader rules, R9 TSO-MAP, N1-N4/R1 text; delete X86_FALLBACK, STATIC_CHECKS, ASM, Makefile | L1, L2 | open |
| 17 | cltree (N2) and fontstate (Wine font ref) producers | L2, L5/L4 | open |
| 18 | ELF link of 7-11 on trunk, green | L1 | waits 2-11 |
| 19 | boot in emulator to WinMain, menu, first frame | L4 | waits 18 |

## Numbers, 2026-11-11 21:45Z (totals from branch heads and fnv-nx-build refs; source commit per row)

| # | item | number | source |
|---|---|---|---|
| T | trunk claude/fnv-nx-clean | gates 7; product roots 0 (build.json 0); tracked files 496 | 1b603a4f |
| 1 | msvcmodel mirror tools/gcc-ilp32-msvcmodel | 0 commits; farm rebuilding on pinned env since 21:38 (envcheck OK) | farm session |
| 2 | external guest compiler | mechanism on L1 w4; guest_compiler.json absent; valid guest roots 0 | 5942a431 |
| 3 | plugins as recipe stages | 2/7 (order, ftoi); missing addr, dispatch, names, sdd, pi | 5942a431 |
| 4 | w3 numpy | done, merged | 1b603a4f |
| 8 | platform | bridge 945 rows, without body 0/945; s4 341 tests / 343 006 checks / 0 fail; s7 63 / 2 321 / 0; build_guest 94 sources, 1 836 symbols, 12 unresolved (sockets); L1/L3a hooks not called | L5 6621757d |
| 9 | render | d3d9total 332/332 (enum 161/161, SM 171/171); form dispatch 7538/7538; deko3d formats 35/35 | L6 1002ed36 |
| 10 | lift exe | universe 99 927; liftable 99 285; without body 642 (626 segment/far/privileged, 16 x87+VEX); instest unequal 37/488; chains 692 equal / 3 differ; imports 938 entries | L3a note 002 |
| 11 | lift shards (old path 977dacc958f4) | exe 64/64, d3dx9_38 32/32, binkw32 1/1, libvorbis 1/1, libvorbisfile 1/1; via recipe 0 | fnv-nx-build refs |
| 12 | L2 rules on trunk | 0; L2 branch took the trunk tree | 0165f219 |
| 13 | gates | trunk 7; +9 contract gates on L1 w6 (all red: no contract_inputs.json); oracle on L4; classes A-G in VERDICT 0/7 | 47713215, e4460fdf |
| 15 | exe pin | exepin.py WIP, worker interrupted | L1 w8 8eb087d5 |
| 17 | references | fontstate farm job written; cltree 0; published 0 | L4 0570c774 |
| 18 | ELF on trunk | 0 | - |

## Numbers, 2026-11-11 21:55Z (changes since 21:45Z; source commit per row)

| # | item | number | source |
|---|---|---|---|
| 8 | platform | guest archive UNRESOLVED 0 (newlib 141, runtime 18, hooks 14); s4 498 tests / 406 189 checks / 0 fail; s7 84 / 3 239 / 0; hooks called 14; platform/build.json waits 22:42Z | L5 1b27c5bf, 935d2355 |
| 10 | lift exe | instest call/jmp 280/280; formgate 0/26 500 (input not yet an external); `pure` 221 violations in 49/90 lift files | L3b 60e9b6b7, 9ea1b6c2 |
| 12 | L2 | R9 TSO-MAP in ORDERING.md; STATIC_CHECKS.md, ASM.md deleted; 262 names mismatches open | L2 068f6f59 |
| 13 | gates | trunk 7; L1 w6 gates.json 21 (7 + 14 contract, red until contract_inputs.json); VERDICT A-G 7/7 listed on w6; REQUIRED_PRODUCT 32 | L1 d38e80f6 |
| 13 | oracle | 35/35 (verify 31, contract 4); refs spec 59, contract 1; test_oracle 49 cases | L4 e4460fdf |
| 13 | MERGER | w3 merged; w4 bf3556e0 rerun after ENOSPC (17 leaked /tmp/objstub-* x 937 MB); w7 0d378d6a queued | L1 note |
| 17 | references | d3dx9 farms F-a 5/5 (127 s), F-b 13/13 (324 s), F-c running | L4 note |
| 16 | loader rules S1-S8, M2-M4 | no source in any plan; L1 defines them in verify/RULES.md during the loader rewrite | orch 21:55Z |
