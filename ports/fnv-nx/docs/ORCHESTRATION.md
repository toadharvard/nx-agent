# fnv-nx: how the project works (orchestrator's charter)

Written 2026-11-14 at the owner's request ("Зафиксируй текущее понимание проекта и разделение обязанностей... чтобы
при очередном компакте не потерять ту суть, которую я в тебя заложил"). Every session re-reads this file and CLAUDE.md
after a compaction. CLAUDE.md holds the binding rules in full; this file is their map. Where they differ, CLAUDE.md
wins, and the difference is a defect of this file.

## 1. The goal

**A full native port of the game to the Switch.** The parts:

- the exe and its modules, fully lifted from x86 to C++ (lifted code, not emulated or interpreted);
- that C++, compiled by our own compiler into one guest ELF;
- beneath it, a native platform: Win32 and the CRT over libnx, for a declared machine;
- above it, the D3D9 front end, over two backends. GL is the reference and renders in the emulator. deko3d is for the
  console and is checked against GL frames.

**The path, in order.** Nothing off it gets quota.

1. The first product root (platform) on the trunk.
2. The lifted packs as product roots.
3. The ELF.
4. The boot in the L4 emulator: exe entry, then WinMain, then the first frame (the GL backend's first present), with
   instructions per frame per thread.
5. The main menu (its rule comes from an input scenario), then speed, then the Switch (deko3d, Switch host root, NSP).

**Speed targets.** 45 fps is the floor and 60 fps the target, on the console's 1.02 GHz CPU. They are measured from
the first frame on. No design choice before then may close the way to them: no one-guest-thread-at-a-time, no
interpreter on the product path, no fast path switched off for good.

## 2. The owner's essence (what must never be lost)

1. **Correct by construction.** Every step is closed by a known rule that covers the whole class, with a proof argument,
   and a gate in the L1 verdict checks it.
   - Forbidden: stubs, no-ops, placeholders, trap paths, fallbacks, heuristics, name guesses, hand lists, sampling as
     proof, and measure-then-skip.
   - Tests and runs verify; they never decide.
2. **Correctness is absolute.** The only exceptions are the 9 OWNER assumptions, decided in advance (verify/ASSUMPTIONS.json):
   1. the compiler is correct;
   2. SHA-256;
   3. Win32 and D3D9 as documented;
   4. the console until a run on it;
   5. native code is ASan/UBSan-tested, not proven;
   6. no wild accesses by the exe;
   7. ILP32;
   8. IEEE FP;
   9. the pinned formal tools and published theorems.

   Every other assumption is REMOVE, with an owner and a gate. No session adds an assumption.
3. **The trunk is always green.** Green means that what the compiler built does not fail at run time. The stub gate is
   an absolute 0.
4. **One build machinery.** `verify/recipe.py` plus `GATE_CACHE`, with:
   - the pinned compiler (env.json);
   - the game's own compiler (msvcmodel GCC 13.3, VC++2008 data model) with our plugins in every key;
   - private sources only as pinned externals.

   It is universal, content-keyed and fast (≤300 s). A second path is a violation.
5. **Correctness never switches off a fast path.** The fast path gets the exact semantics by a class rule. A temporary
   switch-off goes into `verify/PERFDEBT.json` with its restoring rule, and the file is 0 at the first frame.
6. **Quota only on the path.** Not spent on: repeated summaries, censuses of unchanged numbers, verifiers or farms that
   block no path step, or reruns on unchanged content. One verdict per candidate.
7. **Real numbers.** Every number is a global total with its commit. Never samples.
8. **The owner is not disturbed.** The orchestrator decides everything the owner would. Only milestones are reported:
   exe entry, WinMain, menu, first frame, Switch.
9. **Legal.** Game data and anything decomp-derived stay in private fnv-nx-build, never in the public repo.

## 3. Roles

| Role | Who | Decides | Does not |
|---|---|---|---|
| Owner | the human | goals, OWNER assumptions | get asked for a yes |
| Orchestrator | the session titled "New Vegas Switch port" | task order, priorities, worker caps, rule rulings between layers, merges to the trunk (only on a green `l1.py merge` verdict), session lifecycle, standing orders (written into CLAUDE.md, then a message points to them) | mechanical work; merging red; widening Legal or the rules |
| Lead (Opus), one per layer | see section 4 | correctness within its layer; accepts or rejects worker results (the rule closes the class, the numbers are global, the build used the machinery) | mechanical work itself; work off the path |
| Worker (Sonnet) | created by a lead | nothing beyond its task | anything not named in its task (files, rule, check, branch) |
| Farm (Sonnet) | created by the orchestrator or a lead | nothing | commits (the runner publishes) |

**Operating rules:**

- A lead runs at most 3 workers at a time, unless the orchestrator raises the cap for a bottleneck.
- Each lead keeps `journal/status/<layer>.md` current; that file is its report.
- The plan of record is `journal/status/ALL.md`, "Path plan". It lists items with owner, rule and done-condition.
- A blocked item names its blocker and the blocker's owner. Waiting means taking the next item, never idling.
- A gap with no known rule goes to the orchestrator. It is never improvised.
- An instruction a lead thinks is wrong: say why, and carry on with the rest of the queue.

## 4. Layers (named in words to the owner)

| Layer | Owner's name | Owns |
|---|---|---|
| L1 | Контракт и сборка | recipe.py and the cache; the verdict and all gates; trunk merges; the assumptions and PERFDEBT registries; the loader root; the guest link (link/guest.json, onedef, link root); the IAT root; the call-graph reach certificates |
| L2 | Исходники и плагины | the GCC plugins (order, ftoi, addr, dispatch, names, sdd, pi); N1 name certification (identity, not role); TLS and extents facts; the shim/compat headers |
| L3a | Лифт | the lifter and its facts (dispatch, EH tables, flags, DEF0/DEFPRIOR, control-state value analysis); the packs; lift/rt; the bridges; the product ELF root |
| L3b | Проверка лифта | shape/form certificates against the Sail model (the pack's own universe); SEH/EH conformance; full CONTEXT restore; lift tests with no doubles |
| L4 | Эмулятор | the boot machine (the shared kernel/GPU model below every test); boot milestones; instructions per frame; frames stored by content |
| L5 | Платформа | Win32/CRT for the declared machine; the guest runtime msvcmodel13; rt/guest (`main`, threads); registry, FalloutPrefs, fonts, Galaxy, network-down; later the Switch host root |
| L6 | Рендер D3D9 | the D3D9 front end to the nxgpu stream; the shader translator; sm_link at 0 failures |
| L6A | Бэкенды GL и deko3d | gl4 (the reference, recipe-built, the boot machine's GL); deko3d after the first frame, checked against GL |

## 5. How a change reaches the trunk

1. A worker builds on the current candidate through recipe.py, then reports its head and its verdict line to its lead.
2. The lead checks it (rule, global numbers, machinery) and sends it to the orchestrator.
3. The orchestrator merges the pieces into the candidate branch (now `claude/fnv-next3b`) and runs one verdict:
   `python3 -I ports/fnv-nx/verify/l1.py verdict --repo . --commit HEAD --verdicts ../vdir --budget-s 300`, in the
   background.
4. If it is green, the orchestrator runs `l1.py merge` into `claude/fnv-nx-clean`. If it is red, each red goes back to
   its owner.

## 6. Standing decisions that shape the remaining work (2026-11-14)

- The platform root enters the trunk only built on msvcmodel13, never on gcc 15 headers.
- L3b certifies exactly the shapes the product pack uses, and does not wait for the pack root.
- **Hardware exceptions.**
  - Under OWNER 6 there is no asynchronous fault recovery.
  - #DE, int3, int n, ud2, into, bound and unmasked FP exceptions are raised synchronously at their instruction, with
    the precise CONTEXT.
  - One value analysis proves that every control-state write (FPU CW, MXCSR, EFLAGS TF/AC/DF) keeps the documented
    value. A site it cannot decide gets a call-graph reach certificate and goes to `fnvnx_reach_violation`. A site
    that really sets the bit means the feature is implemented.
- SEH continue restores the whole CONTEXT that ContextFlags names, as Windows documents and Wine 9.0 implements.
- **Shaders.** The game's shaders are a static set. sm_link over it is at 0 failures, and a backend link failure goes
  to `fnvnx_reach_violation` with that certificate.
- **float→int.** Every conversion (C casts, lrint and friends, vector and target builtins) has x86 semantics: in range
  it converts; out of range or NaN it gives integer-indefinite. It is made exact on the fast path by the ftoi plugin.
- **The menu milestone** waits until after the first frame. N1 certifies identity, not role, so no function is picked
  by name.
- **Per-target recipes** (`"recipe": {"guest", "host"}`). A product image of target T may link a root's T variant.
  Flags variants such as the sanitizer one are test-only.
- **The selftests universe** is every test file by one naming rule. It lands right after the first root.
