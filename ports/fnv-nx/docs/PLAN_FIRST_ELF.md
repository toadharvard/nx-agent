# Plan: first ELF under the new scheme, then first frame (orchestrator, 2026-11-10)

Source: L4's note l4-verify-005 (branch claude/fnv-l4-verify 736579b1) plus the leads' last notes. All work is
stopped by the owner (08:52Z). Only token-free farm runs continue. Estimates are lead hours (h) or Sonnet tasks (≤2 h
each), ±50 %. Numbers marked (L4) were measured by L4; the rest are the leads' own.

## A. Fully lifted first ELF: start from the exe entry point

| # | Missing | Owner | Depends on | Estimate |
|---|---|---|---|---|
| A1 | Consistent lifter tree; rerun golden/instest/chain/nltest/ehtest | L3 | — | 1–2 h |
| A2 | Universe after R3 (+~1,449 entries), re-published facts | L3 | A1 | 1–2 h |
| A3 | Packer: universe → C++ → objects + dispatcher table object + "entry → symbol" manifest | L3 | A2 | 2–3 h + farm ~1 h |
| A4 | Bodies for 420 of 96,809 entries (rdtsc, int, rcr, cpuid, pushal, xlatb, bts … ≈385; x87 transcendental/env ≈35, correctly rounded libm) | L3 | A1 | 6–9 h |
| A5 | Start runtime: import entries generated from bridge.tsv (440), thread/TEB/guest stack, SEH (RaiseException/RtlUnwind), timer, rdtsc/cpuid | L3 | A3 | 4–8 h |
| A6 | 29 msvcrt imports (EH, _CI*, fcw) | L3 | A5 | ~3–4 h (orchestrator estimate) |
| A7 | ld stage: L3 objects + runtime + loader → ELF ILP32 PIE; gate: dispatcher entries = universe, 0 undefined, 0 pinned PC-relative | L1 | A3 | ~4–6 h (orchestrator estimate) |
| A8 | Loader: map the exe at 0x00400000 with PE section protection, TLS from the exe directory, start through the exe entry and CRT (SHA-256 done) | L1 | A7 | ~4–6 h (orchestrator estimate) |
| A9 | IAT slot → generated stubs by prototype; L1/L3 interface | L1+L3 | A5, A7 | ~1–2 h |
| A10 | Import bodies for the exe: done (240/240); d3d9!D3DPERF_SetOptions | L5 / L6 | — | 1 function |
| A11 | 4 functions off contract on the way to WinMain (GetStdHandle, GetModuleHandle/GetProcAddress for 14 DLLs, GetEnvironmentStrings sorting, RaiseException/RtlUnwind ← A5) | L5 | — | 2–3 tasks |
| A12 | Emulator boot stage for the new ELF: milestones, icount, 32-bit space with a free exe image range | L4 | A8 | ~1 day |

**Critical path A:** A1 → A2 → A3 → A7 → A8 → A12, with A4/A5/A6/A9/A11 in parallel.
Wall-clock time to "ELF built and started" is about **2–3 days** of work by L3 + L1 + L4. L5 does A11 in parallel.

## B. From the first ELF to the main menu and first frame

| # | Missing | Owner | Estimate |
|---|---|---|---|
| B1 | 45 import bodies for statically needed DLLs (d3dx9_38: 22 font gdi32, 15 kernel32, 1 advapi32; bink/vorbis) | L5 | 5–6 tasks |
| B2 | 51 partial + 5 stub imports to contract, plus the ENV-TRUTH contradictions (Steam, console, DebugBreak, GetVersionEx …) | L5 | 10–12 tasks |
| B3 | Font gdi32 on FreeType from the reference build | L5 | 4–5 tasks |
| B4 | Device methods 297/497 → 497/497 | L6 | ~12–15 tasks |
| B5 | Virtual clocks in verification mode (Wine side, guest side) for exact stream comparison | L4 + L5 | 2–4 h |
| B6 | Lifted D3DX (d3dx9_38, d3dx9_31) through the same packer | L3 | farm + 1–2 h |

Main menu and first frame: **about 3–5 days** after A, depending on the gaps found by the discovery boot.

## C. Making the build GREEN (rules; the first ELF is a measurement build until these are done)

| # | Missing | Owner | Estimate |
|---|---|---|---|
| C1 | Form certificates. Integer: 358/393 against Sail. FP/SIMD: 0. Missing references: FP-SMT 71 mnemonics, K 40, SDM 17, table 3 | L3 | 8–16 h + farm |
| C2 | Sail build reproducible on a clean farm (opam/isla-sail setup) | L3 | 1–2 h |
| C3 | R9 memory ordering in lifted code (ldar/stlr classes, 64 % class (a)) | L3 | 4–8 h |
| C4 | R8 x87 formats (fild/fistp m64 above 2^53, SNaN), EFlags in CONTEXT, access violations in SEH | L3 | 4–6 h |
| C5 | rcp/rsqrt 2^32 table from a pinned farm host | L4 farm | machine hours |

## D. Second step: native bodies (L2), for performance

| # | Missing | Owner | Estimate |
|---|---|---|---|
| D1 | Final L2 sha 7200c194: REC/PATCH certification (running on lc1) → unified recompile → cl tree → namegate/argordergate | L2 + L4 | farm + 2–4 h |
| D2 | certs.json into the source tree | L2 | 1 h |
| D3 | 4 defects in L2 tools (compile.sh gcc for .c, reccert overlay, build_tree tmp by pid, compile_job waiting for keys) | L2 / L4 | 2–3 h |
| D4 | cl does not build 846/2,988 prepared units (L4): __builtin_trap 343 (goes away with FALL1 removal), fnvnx_asm.h 242, C2146 172, VC8 1 | L2 | 4–6 h |
| D5 | BODYSEL on real N3, link/resolve, native↔lifted bridges by prototype | L1 | 6–10 h |
| D6 | GCC tree failures: 3 units (FAMThread ambiguity, statsmenu float→enum, FindFirstFileA arguments) | L2 | 1–2 h |

## E. Load per lead (A + B + C), to the first green frame

| Lead | Work | Approx. |
|---|---|---|
| L3 LIFT | A1–A6, B6, C1–C4 | 35–60 h: the longest pole |
| L1 CONTRACT | A7–A9, then D5 | 10–14 h, then +6–10 h |
| L5 PLATFORM | A11, B1–B3, B5 | ~25 tasks ≈ 1.5–2 days |
| L6 RENDER | A10, B4 | ~15 tasks ≈ 1–1.5 days |
| L4 VERIFY | A12, B5, C5, farms | ~1.5 days |
| L2 SOURCE | D1–D4, D6 (not on the path to the first frame) | ~1–1.5 days |

To use the parallelism, split L3 into two leads:
- **L3a:** packer, runtime, entries (A).
- **L3b:** certificates and the R8/R9 rules (C).

Then the critical path is about **4–7 days** to the first frame. With a single L3 it is about 7–10 days.

## What keeps running without tokens (L4)
REC/PATCH certification of 7200c194 (lc1), D3D9 docked recording of the original (full1), Sail build plus proofs (l3c).
L4 sends the results to the orchestrator in one message.
