# HANDOFF2: continue fnv-nx in another account (2026-11-11)

This document is the complete state as of the pause on 2026-11-10. It replaces docs/HANDOFF.md where the two
conflict. Read it fully, then read CLAUDE.md, which is binding.

## 0. Who you are and what to do first

- **Your role.** You are the new orchestrator. In CLAUDE.md, "the orchestrator" means your session. The old session
  ids in this repository belong to the previous account and are dead.
- **Repositories.**
  - `toadharvard/nx-agent` is public. The integration branch is `claude/port-nsw-un6t9s`; the clean branch is
    `claude/fnv-nx-clean`.
  - `toadharvard/fnv-nx-build` is private. It holds game data, decomp-derived artifacts, farm reports and plans.
    Attaching it with add_repo is authorized.
- **First actions:** §7. Do not redo the analysis. Everything below was decided by the owner or measured.

## 1. The owner's intent (verbatim where quoted)

**Goal.** A native Nintendo Switch port of Fallout: New Vegas (FalloutNV.exe plus the decompilation), playable,
correct by construction, fast.

**Priorities.**
1. Correctness by construction: proven, not hoped.
2. Performance: 45 fps stable, 60 a stretch, below 30 a fail, no resolution cuts. Performance is counted in
   executed instructions per frame.
3. Fast and cheap hypothesis checks.

**Rules, in the owner's words:**
- "Каждое решение должно быть без падений и эвристик. Только на правилах, закрывающих весь класс задач."
- No stubs, stand-ins, trap paths, measure-then-skip, sampling as proof, per-site lists, or regex guesses.
- Fuzz, boots and scenarios verify; they never decide.

**Organization.**
- Fable leads, each with Sonnet workers. The master plan decomposes the work into lead streams, and each lead
  decomposes its own stream into Sonnet tasks.
- The owner wants a result now, and tokens are expensive: run only the leads on the critical path.

**Communication.** Answer the owner in Russian, short and concrete. Ask only what only the owner can decide; decide
routine technical questions yourself.

## 2. Binding rules and decisions (all in CLAUDE.md, read it)

- The owner order of 2026-11-10: class-closing rules only, never patches.
- The owner decisions of 2026-11-10:
  - downloads are authorized;
  - merges into the clean branch only on a green L1 verdict;
  - real multicore parallelism;
  - native proof N1–N4 without SMT;
  - plain IEEE float with exact classes;
  - one reference CPU model;
  - perf counted as instructions per frame;
  - the Switch product decisions: Xbox layout 1:1, system swkbd, rumble on, docked 1080p / handheld 720p,
    frame pacing chosen after the first measured frame;
  - packaging (final: an own-title NSP; development: an exefs override; deferred until the game runs).
- **Plan of record:** MASTER_PLAN4 (fnv-nx-build `plan/master3`, `audit/MASTER_PLAN4.md`), plus
  docs/PLAN_FIRST_ELF.md for the concrete next steps. MASTER_PLAN3 and MASTER_PLAN2 are background and apply only where
  they do not conflict.

## 3. The architecture (super-rule and mechanisms)

**Super-rule.** The contract comes from the exe; a native body earns its place, otherwise the body is lifted from the
exe's x86 bytes. Every exe address gets a body.

### Body selection

**BODYSEL** (L1) decides native or lifted statically, before any run. A function is NATIVE only if all four hold:
- **N1:** the MSVC 2008 compile of the decomp source is byte-equal to the exe up to relocations, with symbols scoped
  per unit;
- **N2:** contract sets are equal (data, calls, vcall, vptr writes), compared over closures under inlining;
- **N3:** the plugin's portable-subset verdict;
- **N4:** closure under inlining.

Everything else is LIFTED.

### Exe as the source of truth (L1)

- **Exe facts:** reloc, RTTI, vftables, prototypes from MSVC decorated names.
- **Tables as data:** vptr = the exe vftable address.
- **R3: code pointers in the image are NEVER rewritten.** Every indirect call or jump goes through a dispatcher
  "exe address → guest entry". Every relocated target in an executable section gets an entry; an undecodable byte
  raises #UD as on x86.
- **Names are unit-local**; references resolve by address through the N1 certificate.
- **Data with external linkage is one object** at its exe address.
- **RTTI goes through the exe RTTI** (__RTDynamicCast); GCC `_ZTI` and `__dynamic_cast` are banned.
- **Start through the exe's own CRT.** The loader only maps the image, sets protection and TLS, then jumps to the exe
  entry. The exe's SHA-256 is checked on every start.

### Compiler (L2)

- **Base compiler.** Patched GCC 13.3 for ILP32 with the MSVC data model: `size_t` = unsigned int, `ptrdiff_t` = int,
  the array cookie only for non-trivial destructors, and sized `delete[]` taking sizeof(element).
- **Plugins, applied to the whole class:**
  - **SDD2:** one destructor slot, MSVC `??_G`/`??_E`;
  - **Π:** the N3 verdict plus the inlining tree;
  - **TSO:** `ldar`/`stlr` on shared memory, with locality rules;
  - **DISPATCH1:** indirect calls go through the dispatcher;
  - **CC1:** calling conventions as attributes;
  - **NAMES1:** an MSVC mangler inside GCC, gated against cl objects;
  - **COOKIE1;**
  - **FTOI/E1:** float→int with MSVC semantics; float→enum is a compile error, as in cl.
  - `-fno-devirtualize`.
- **REC-CERT and PATCH-CERT.** Reconstructions and per-site source patches are data that need a certificate for the
  current prepare rule set. They are applied whole or not at all. Without a certificate, BODYSEL decides.
- **Prepare output must compile with BOTH GCC and cl.** GCC-only inserts get a cl equivalent with a neutrality lemma.

### Lifter (L3)

- **Translation.** x86 → C → GCC, through a DSL with C and SMT backends.
- **Form certificates (R2).** A form without a certificate is a build error (a measurement build is the only
  exception). References:
  - Sail x86, patched in two places (ror CF, rotate count modulo width), checked on the host over 488 M cases;
  - the FP-SMT theory for IEEE arithmetic;
  - the K x86-64 semantics for SIMD/MMX;
  - an SDM-transcribed reference module for x87 control, E1, E2, cpuid and rdtsc;
  - the exhaustive table for rcp/rsqrt.
- **R8 FP.** IEEE of the source types; x87 stack as SSA; RC split; two shadow flag sets.
- **R9 memory.** Class (a) plain where provably thread-local (currently 64.2 %), the rest `ldar`/`stlr`. Escape
  summaries are interprocedural; import summaries are derived from L5's implementation.
- **R4.** Full MSVC C++ EH and SEH through lifted frames.
- **MODULE-LIFT for non-system PEs:** binkw32, libvorbis(file), atimgpud, d3dx9_38, d3dx9_31. The d3dx DLLs come from
  the owner's install, fnv-nx-build `gamedata/redist`.

### Platform (L5)

- **Imports.** The manifest holds every statically reachable import (440 unique), each implemented totally to
  contract.
- **ENV-TRUTH.** The environment is the reference environment: Steamworks "client not running", no network, XInput
  only, Windows 7 SP1, en-US/1252, the GOG install layout.
- **Fonts and NLS.** Fonts are Wine 9.0 + Liberation (`tools/fonts`) rasterized by FreeType at the reference build;
  NLS tables are Wine's (`tools/nls`).
- **Switch app lifecycle → Win32 messages.** Virtual clocks in verification mode on the guest side.

### Render (L6)

- **D3D9 boundary.**
  - Totality: every enum value and every SM1-3 op is translated, otherwise a build error; there is no skip-draw path.
  - The null device is removed. Off-console runs use the native renderer with its GL 4.5 backend on llvmpipe.
- **Shader translator in the runtime**, with a shipped pre-translated cache (L1 proved that runtime compilation paths
  exist).
- **One reference D3D9 profile** (Wine 9.0 + llvmpipe, two screen profiles). Video memory comes from the Switch budget.
- **Stream equality** with the original per thread, under virtual clocks (L4 on the Wine side, L5 on the guest side).

### Verify (L4)

- **Merges.** Each merge runs in a declared environment (`verify/env.json`, `farm/setup_verdict_env.sh`) with a
  sandbox, and the determinism rule R12-T applies: AST lint of our tools, reproducibility flags for external tools
  (`/Brepro` measured), `PYTHONHASHSEED=0`.
- **Merge command:** `python3 -s -B ports/fnv-nx/verify/l1.py merge --repo <nx-agent> --into claude/fnv-nx-clean
  --from <branch> --verdicts <dir> --remote origin`. It pushes atomically. A cold verdict takes ~70–85 s.
- **Counter.** Deterministic instruction counter `verify/icount.py`, per thread and per milestone.

## 4. State at the pause (numbers)

**Clean branch `claude/fnv-nx-clean` 1b280af4: green.** It holds L4 verify, L1 contract and L5 platform. Nothing
runnable exists under the new scheme yet. The old linker was removed; the last runnable binary was the old pipeline at
e1ca6b02, 5/10 milestones.

**L1 CONTRACT** (branch head 8026374c):
- exe facts: 87,509 functions in 33 s;
- N1 68,354/68,657 equal; the misses are 211 (missing u2713 object), 59 and 33;
- BODYSEL is total over 96,809 entries;
- gates (data, call, vcall, xc) run in 2.4 s;
- readers of L2's sections and the binding are done;
- d3dx reachability is published (font GDI path reachable);
- **missing:** the ld stage, the loader, the IAT stub interface, and BODYSEL on real N3.

**L2 SOURCE** (head ae8cb9b9; final sha 7200c194 used for certification):
- SDD2, Π, TSO, DISPATCH1, CC1, NAMES1 (736/739 on 36 units), COOKIE1, FTOI and REC-CERT are done;
- the patched compiler is built and accepted on farms (63aa47fa, 5 min 40 s);
- the GCC tree builds 2,975/2,978;
- cl builds the prepared tree at 2,142/2,988. Causes: `__builtin_trap` 343, which goes away with the FALL1 removal;
  `fnvnx_asm.h` 242; C2146 172; VC8 1;
- **certification at 7200c194** (fnv-nx-build `cert/7200c194d825`): reconstructions 473/491, patches 668/892;
- **next:** put `certs.json` into the source tree, then the unified recompile; fix 4 tool defects (L4 note 005); the
  cl-compat class; the 3 GCC tree failures.

**L3 LIFT** (head 540456cf):
- code map of the exe is complete: 12,440,971 bytes, 96,809 entries; 96,389 entries are liftable;
- chains: 753/754 equal to x86;
- Sail proofs: 511/607 keys (DSL mismatch 13, model defect 20, model gap 58, timeout 5), in fnv-nx-build
  `l3-lift/bench`;
- the Sail build is NOT reproducible from the script: 3 manual steps (opam 2.2.1 / OCaml 4.14.2 / Sail 0.20.3, clones
  of isla e9b5d94 and sail-x86-from-acl2 0c6c023, opam env, and a separate `make isla-sail`);
- FP/SIMD certificates: 0;
- dispatcher: 23 instructions per lookup; a fast path of 3–5 is planned;
- EH: correct on synthetic cases only;
- **missing:** the packer (universe → objects + dispatcher table), bodies for 420 entries, the start runtime, 29 msvcrt
  names, R9 in the generated code.

**L4 VERIFY** (head 736579b1, 2 commits not merged: aa3dc55e and 736579b1, merge them through the MERGER):
- farms are idle; there are 12 slots;
- the missing-items note is `journal/inbox/l4-verify-005.md`.

**L5 PLATFORM** (head 2a0a07ca):
- 305/440 imports total by a measured audit; exe imports 240/240 have bodies;
- 45 bodies are missing for the needed DLLs;
- 51 partial and 5 stub implementations remain;
- ENV-TRUTH contradictions found: Steam init returns true; console handles; DebugBreak; GetModuleHandle knows only 6/14
  DLLs; GetVersionEx is missing;
- 300 tests, 0 failing; font GDI stage 1 is byte-equal to the reference dump.

**L6 RENDER** (head 1d583def):
- SM1-3 translator 7,538/7,538; device methods 297/497; FFP implemented;
- the stream recorder is generated from one interface table;
- recordings of the original (handheld and docked) reached a cell: 765 shader bytecodes, all created in one frame;
- run-to-run is not deterministic without virtual clocks.

**Perf (estimate, not measured).**
- Budget: ~18 M instructions per frame on the main thread (45 fps, 1.02 GHz, IPC 0.8).
- Rough estimate: 26–33 M without levers, 17–22 M with the levers:
  - our own render thread (record D3D9 commands, execute on another core);
  - the game's threaded AI, Havok and animation;
  - parallel culling;
  - native hot code.
- The GPU model gives a median of ~10 ms at 720p; docked 1080p and memory bandwidth (shared LPDDR4) are risks.

## 5. Where things are (fnv-nx-build, private)

| Branch | What |
|---|---|
| `plan/master3` | MASTER_PLAN 2/3/4, audits, reconciliations, PoC lifter, experiment scripts |
| `cert/7200c194d825` | REC/PATCH certification results (certs.json, cert.tsv, patchcert.tsv) |
| `l1-contract/*` | exefacts outputs, d3dx reachability (reach_l3universe.json) |
| `l3-lift/bench` | Sail proof results, benches |
| `l5-platform/manifest` | import manifest, audit2 (all.tsv, summary.tsv) |
| `l6-render/d9rec/*` | D3D9 recordings of the original |
| `tools/sail-x86`, `tools/fonts`, `tools/nls` | mirrors |
| `gamedata/*`, `gamedata/redist` | owner game data; d3dx9_31/38 DLLs |
| `msvc/prepared/72e1664119aa`, `c/86b7beb2d271` | cl and GCC objects of the prepared tree |
| `capture/39478bf91f93cd48/*`, `reports/sw*`, `reports/swc*` | render cell-sweep captures and reports |

## 6. The plan: next steps (docs/PLAN_FIRST_ELF.md has the tables and estimates)

**Narrow goal first: a fully LIFTED first ELF, started from the exe entry point.** It is a measurement build until
every form is certified. This gives back a runnable binary and tests all the seams at once.

1. **L3a.** A consistent lifter tree, then the universe after R3, then the packer, then the start runtime (import
   entries from L5's bridge.tsv, TEB and stack, SEH, timer), then the 420 missing entries.
2. **L1.** The ld stage (ILP32 PIE; gate: dispatcher entries = universe, 0 undefined) and the loader (exe at
   0x00400000, PE protection, TLS, start through the exe CRT).
3. **L5.** Only the imports on the start path: the 4 off-contract functions, the ENV-TRUTH contradictions, and the DLL
   bodies needed at start.
4. **L4.** The emulator boot stage for the new ELF, with milestones and icount. Report up: ELF built → WinMain → main
   menu → first frame.
5. **In parallel, L3b.** The 13 DSL mismatches, a reproducible Sail build, FP/SIMD certificates, R9 and R8 in the
   generated code.
6. **After the ELF boots.**
   - L2: certs.json in the tree, the unified recompile, BODYSEL on real N3, native↔lifted bridges.
   - L6: device methods 497/497, virtual clocks.
   - L7 PERF only after the first measured frame.
7. **Discipline.** New problem classes found along the way are recorded but NOT fixed unless they block the first ELF.

Estimates (±50 %, historically optimistic by 2–4x):
- first ELF started: 2–3 days;
- menu and first frame: +3–5 days;
- with L3 split into L3a and L3b, 4–7 days to the first frame.

## 7. How to resume (concrete)

1. **Attach the repos.** Attach fnv-nx-build (add_repo with push access).
   - Merge `claude/fnv-l4-verify` (aa3dc55e, 736579b1) into the clean branch with the merge command from §3, in a
     container where `farm/setup_verdict_env.sh` succeeded and `verify/envcheck.py` prints OK.
   - Or recreate a MERGER session with that job; its prompt is in docs/handoff2/MERGER.md.
2. **Create lead sessions** with model `claude-fable-5-2`, one cloud session per lead, each on its own branch
   `claude/fnv-<lead>` created from its existing branch.
   - Brief = docs/handoff2/LEAD_BRIEF_COMMON.md + the stream paragraph from MASTER_PLAN4 §5 + this document's §6
     step.
   - Start only L3a, L1, L5 and L4. Add L3b when tokens allow. L2 and L6 wait for the first ELF.
3. **Farms.** At most 12, owned by L4, recreated from fnv-nx-build `farm/` (docs/HANDOFF.md §3). Heavy jobs (>10 min)
   run only on farms. Lead containers do NOT compute while idle: background processes there live only during a tool
   call.
4. **Report to the owner** only at milestones and decisions, in numbers.

## 8. Known pitfalls

- **Permissions are granted in advance, not session by session.**
  - The orchestrator does not wait for approvals. Everything the work needs is pre-approved through allow rules in
    the repository's `.claude/settings.json`: building external code, workers, cloud sessions, rebase and
    force-with-lease on own branches.
  - The rule set is in docs/handoff2/SETTINGS_ALLOW.json. A session cannot edit its own permission rules; the
    classifier refuses this as self-modification. So the owner commits that file as `.claude/settings.json` ONCE.
    After that, every new session that starts with this repository gets the rules.
  - Cloud sessions read the file only when they start with this single repository. Attach fnv-nx-build later with
    add_repo.
- **Memory and disk.** Containers have 16 GB RAM and no swap; OOM kills happened. Keep ≤2 heavy processes and ≤3
  Sonnet workers per lead. Free disk by deleting stale scratch.
- **`python3 -I` ignores PYTHONHASHSEED.** Stages run with `python3 -s -B`.
- **Reachability must be computed as an over-approximation first.** An early report "font path unreachable" was
  wrong.
- **Lead estimates run over by 2–8x.** Verify every "done" against a gate, not a note.
- **Commits** end with exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No session ids in commits or
  files.
- **No PRs.** No game data or decomp-derived artifacts in the public repo.
