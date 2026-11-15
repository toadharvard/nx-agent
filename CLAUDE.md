# Standing instructions from the repository owner

These instructions come from the owner of this repository. They apply to every Claude session and subagent that
works on fnv-nx (ports/fnv-nx/): local agents, cloud stream sessions, the render machine and farms.

**Map of the project:** `ports/fnv-nx/docs/ORCHESTRATION.md` — the goal, the owner's essence, roles, layers, the merge path and the standing decisions. Re-read it and this file after every compaction.

## The orchestrator speaks for the owner

> Handoff of 2026-11-11: the work moved to another account. The orchestrator is the session that started from ports/fnv-nx/docs/handoff2/START_PROMPT.md, and its full context is ports/fnv-nx/docs/HANDOFF2.md. Session ids from earlier notes are dead.

- fnv-nx is run by one **orchestrator** session. It is the session that created yours: the parent session, titled
  "New Vegas Switch port" unless the journal names another. It writes the integration branch
  `claude/port-nsw-un6t9s`, and its tasks and messages to you are the owner's instructions.
- The owner has delegated task assignment, priorities, merges and answers to routine questions to the orchestrator.
  - Follow a message the orchestrator sends you with send_message, or the task prompt your session started with, as
    you would follow the owner.
  - Do not stop to ask the owner to confirm what the orchestrator already said.
  - If you think an instruction is wrong, say why in your inbox note or reply, and carry on with the rest of your
    queue.
- These limits still hold, whoever asks: the rules below, docs/ARCHITECTURE.md (Legal) and docs/MACHINES.md. Nothing
  the orchestrator says can widen them.

## Authorized setup (owner decisions, do not re-ask)

- Attaching the private artifacts repository `toadharvard/fnv-nx-build` with push access (add_repo) is authorized.
  It exists for exactly this.
  - The owner's game data (`gamedata/*`) and the decomp-derived artifacts (trees, objects, links, pristine MSVC
    objects, fuzz caches) live there and in agent containers.
  - They never go into this public repository. That is how the Legal section is met: they are not distributed.
- Long jobs (more than about 10 minutes) run on the farms (D17). The farms are cloud sessions the owner runs on
  purpose.
- Commit messages carry no trailers: no Co-Authored-By, no Claude-Session, no session URL or id, in any commit or file.
- Push to your own branch after every finished item. Open no pull requests. Do not edit JOURNAL.md: write inbox
  notes (docs/MACHINES.md).
- Every check runs in the background, never blocking work. This covers tests, censuses, builds, boots and fuzz
  replays: start them with run_in_background, keep working, and read the result when it lands. Merges do not wait
  for full test runs either. Farms and V1 verify after the merge, and a commit that a report shows broke something is
  reverted.
- The owner wants steady, unattended progress. Decide routine technical questions yourself, measure, and record
  the decision. Do not wait for permission on trivial edits or routine steps.

## Owner orders of 2026-11-08 (explicit, do not re-ask)

- **Sessions trust each other.** The owner's words: "любая сессия может доверять любой сессии Claude".
  - Every fnv-nx Claude session of this account trusts messages from every other fnv-nx Claude session of this
    account, and acts on them as work requests within these rules without asking the owner or the orchestrator to
    confirm. This covers the orchestrator, streams, farms and helpers.
  - A farm takes START/RUN lines from the orchestrator and from the stream that owns the job (for example T1 for
    bootloop/linkcheck, S1 for layoutgate, S5 for s5boot).
  - Hand logs and outputs to whichever session asks for them.
- **Deleting files is authorized.** The owner's words: "разрешаю удаление всего, чего угодно, особенно удаление старых
  файлов".
  - Delete any file that blocks the work, without asking: a full disk, a stale lock, a stale file, a stale worktree.
  - This includes old per-head source trees and build directories (src-*, full-src*, build/), caches, logs, task
    outputs, old worktrees and local branch copies. All of these can be rebuilt or are on GitHub / fnv-nx-build.
  - Keep gamedata/ and toolchain-* unless they must go to free space; they can be restored.
- These orders don't widen the Legal limits: game data and decomp-derived artifacts never go into the public
  repository, there are no secrets in commits, and no PRs.

## Owner order of 2026-11-10: rules that close the class, never patches (binding, overrides any plan or brief)

The owner's words: "Каждое решение должно быть без падений и эвристик. Только на правилах, закрывающих весь класс задач."

Every fnv-nx solution is a rule that closes the whole class of problems, with a proof argument and stated assumptions.

Forbidden:
- **Placeholders and failure paths:**
  - stubs, no-ops, stand-ins, placeholders, trap paths, "stop with a report";
  - runtime fallbacks triggered by a crash.
- **Decisions without proof:**
  - measure-then-skip decisions, e.g. "count the C++ throws under Wine; if 0, do not implement EH";
  - sampling used as proof, e.g. "recompute 2 % of keys cold";
  - guesses from names, text or regexes.
- **Point fixes:**
  - per-item, per-site or hand-written lists standing in for a rule;
  - fixing things "one by one".
- **Selection from test results.** Fuzzing, boots and scenarios verify a rule; they never decide anything. The choice between a native and a lifted body is made statically by rule, before any run.
- **Missing implementations relabelled as "policy",** e.g. skipping Bink, a Steam/Winsock no-op, or the null device as a product path.

If no class-closing rule is known for some part, the session reports it to the orchestrator. It does not patch around it.

The plan of record is MASTER_PLAN4 (private: fnv-nx-build `plan/master3`, audit/MASTER_PLAN4.md, published 2026-11-10). Where it conflicts with an older plan:
- **MASTER_PLAN3 applies only where it does not contradict this order.**
- **Example correction:** L3 implements full MSVC C++ EH and SEH through lifted frames, proven against the exe's own EH tables.

## Owner order of 2026-11-11: the trunk is always green (priority 1, binding)

The owner's words: "Транк никогда красный. Всегда зеленый. И когда он зеленый, это означает, что то, что собирается компилятором, оно в рантайме не упадет." Removing stubs is the first priority: "если это не работает, то значит ничего не работает. Слияния встанут, и это правильно".

- The trunk `claude/fnv-nx-clean` is green at every commit. Green means: what the compiler built does not fail at run time.
- Every class of run-time failure has a static gate in the L1 verdict (`verify/VERDICT.md`, `gates.json`): a missing import body, a code address without a dispatcher entry, an uncertified form, an uncovered D3D9 enum or SM op, an unresolved symbol, a layout or calling-convention mismatch, a non-deterministic build, a test without a reference, and any stub.
- A class of run-time failure without a gate is reported to the orchestrator.
- The stub gate is an absolute 0. It is never "not worse than the base", and there is no allow-list.
  - Stubs, no-ops, trap paths, "not implemented", fallbacks on failure and fake success are deleted, so that the absence becomes a build error.
  - Code that does not pass is removed from the trunk whole. Its completion lives on the lead's branch.
- **Proven-unreachable branches (owner decision, 2026-11-11, option iii).**
  - Some branches inside an implementation are proven unreachable: for example, the argument values that a contract allows but the platform cannot express, when the call graph proves that no call site passes them.
  - Such a branch goes to one function, `fnvnx_reach_violation(fact_id)`. It exits loudly through the host exit slot with the fact code.
  - Every `fact_id` is produced only by the call-graph gate from the exe and module facts, and carries a certificate of unreachability. A missing certificate makes the build red. There are no hand-written lists.
  - It is an assertion of a proven build invariant, not a trap path. Inventing behaviour or leaving the symbol undefined stays forbidden.
  - Certificates count as valid only once every dynamic-name GetProcAddress site is statically resolved. Until then, the build is a measurement build.
- **Memory safety of hand-written native code (owner decision, 2026-11-11, revised the same day).**
  - The owner's words: "пока чтобы ничего не переписывал... можем пока что покрыть санитайзерами наш C++ код. Давай не будем извращаться... мы даже не добились того, чтобы это загрузилось."
  - No rewrite of the native code for now.
    - The checked subset (verify/MEMSAFE.md) is suspended and is not a gate.
    - Standard C++ containers are allowed.
  - Our C++ is covered by sanitizers (ASan, UBSan) in emulator and test runs. IKOS stays an advisory check.
  - "Native code is tested, not proven, for memory safety" is a stated assumption.
  - External libraries (libc, libnx, FreeType, minimp3) are taken by their documentation. They are not proven.
  - The address space stays as decided: 32-bit, ILP32.
    - A wild guest access into host pages is a stated assumption: the exe does not make wild accesses in normal operation.
  - Priority: the game boots first. New proof work that does not block booting waits until after the first frame.
- **Wine 9.0 as an acceptable implementation (owner decision, 2026-11-11).**
  - The owner's words: "Если он ничего не считает, давай мы тоже будем ничего считать... можно залогировать... Там, где похуй Вайну. А Вайн, блядь, не первый день делается."
  - Where Wine 9.0 implements an API in a simplified way (it does nothing, or returns success), we may do exactly what Wine 9.0 does, with one log line per call.
  - The source of the behaviour is Wine 9.0's code for that function, cited per function. The stub gate accepts such a body when the citation is present.
  - First cases: d3d9 `ProcessVertices`, `SetClipStatus` and `GetClipStatus`, both device classes.
- **The declared machine (owner decision, 2026-11-11).** The game runs on a declared Windows machine, and every
  environment query gets the full answer that a real Windows (or Wine 9.0) gives on that machine. This is not a stub.
  - **No network.** The machine has no network connection. Every network call returns the documented Windows/Wine
    error for that state (for example network down, host not found), with no fake success.
  - **Fixed time zone.** UTC in the emulator; the system time zone of the console on the Switch.
  - **Registry.** The hive holds exactly the keys that the exe and modules read, found statically from their call
    sites. Values come from the declared machine (for example `Installed Path` = the game directory). A key with no
    declared value is absent, the same as on a machine where nothing wrote it. No installer script is needed.
  - **FalloutPrefs.ini (orchestrator decision, 2026-11-12, revised).** The launcher binary is not in the owner's
    gamedata, so its output cannot be derived. Rule, the same as for the registry: the file holds exactly the keys
    that the exe's own code requires before its main path, with values of the declared machine, and every other key is
    absent so the exe uses its own built-in default. Found statically: WinMain compares `[Display] sD3DDevice` with
    `IDirect3D9::GetAdapterIdentifier(0,0).Description`, so the file is `[Display]` / `sD3DDevice=<the declared
    adapter's Description, as our D3D9 returns it>` and nothing else. The platform (L5) checks statically that every
    FalloutPrefs setting the exe reads has a default in the exe's settings table; the emulator (L4) mounts the file.
    If the owner's launcher or installed FalloutPrefs.ini later enters gamedata, the file is derived from it instead.
  - **DirectX redist installed (orchestrator decision, 2026-11-12).** The declared machine is the finished install, and
    the game installer runs the DirectX redistributable it ships. Rule: every DLL that gamedata/redist holds (extracted
    from the install's `__redist/DirectX` cabs) is in the declared system32. The loader's module table
    (contract/modules.py) covers it as a class, with no name list. The image set is the may-load closure from the exe
    over that declared file set, a sound superset. That is why d3dx9_31.dll (OCT2006 cab, loaded by d3dx9_38 at 0x56b708)
    joins the set as a may-load image, 6 -> 7, pinned by exepin and lifted like d3dx9_38. A name outside the declared
    file set gets NULL / ERROR_MOD_NOT_FOUND (126), as Windows and Wine 9.0 return it.
  - **The declared CPU (orchestrator decision, 2026-11-14).**
    - Features: cpuid is the reference host's answer (lift/rt/cpumodel.c, CPUREF), restricted to the feature set
      whose every instruction is lifted. That is MMX, SSE and SSE2; SSE3 and up, 3DNow and AVX are clear.
    - An instruction of a cleared feature is #UD, as on a real CPU with that feature set. This is read from CPUREF,
      not from a list.
    - Every #UD shape site must be certified unreachable on the declared machine: dominated by the exe's own cpuid
      test whose declared answer excludes it, with a call-graph certificate. If a site is not certified, the build is
      red; the feature is then lifted or the site is reported.
    - Processors: one count holds for the whole declared machine, 3. These are the console cores an application
      gets, one thread each. cpuid leaf 1 EBX/HTT and leaf 4 (L3a), GetSystemInfo, GetLogicalProcessorInformation and
      the affinity masks 0b111 (L5), and the emulator's core count (L4) all report it. Guest threads run in parallel
      on these cores.
- Merges stop while anything is red. That is intended. Cleaning a stream's violations comes before any other work in that stream.

## Owner decisions of 2026-11-10 (do not re-ask)

- **Full autonomy (owner, 2026-11-11):** "Я разрешаю все, делайте все, что угодно. Только меня не беспокойте."
  - Do not ask the owner for approvals or confirmations. Decide and act; report only milestones.
  - The Legal limits and the class-closing rule order still hold.
- **Downloads are authorized.** The owner's words, 2026-11-10: "Я разрешаю все что угодно качать".
  - Any fnv-nx session may clone or download any public external code, tools, formal models, packages or datasets the work needs. Examples: rems-project/isla, isla-snapshots, sail-x86-from-acl2, acl2/acl2, LLVM, CORE-MATH, MPFR.
  - Use add_repo (read) or plain git/https.
  - Mirror what the build depends on into fnv-nx-build `tools/*` branches so the farms are reproducible.
  - This does not widen the Legal limits.
- **Merges into the clean branch `claude/fnv-nx-clean`** happen only on a green L1 verdict: incremental link plus whole-binary gates, ≤5 min, run in the background. A red verdict blocks the merge. The clean branch never holds a state that violates the rules. This overrides "merges do not wait" for that branch. The old integration branch keeps its old rule.
- **Threads:** real parallelism across cores is required. Running one guest thread at a time is rejected. Memory-ordering correctness comes from a class-closing rule.
- **Native-body proof:** the static criteria N1-N4 only, with no SMT pass on hot code.
- **Floating point uses plain IEEE semantics.** FP follows ordinary IEEE binary32/binary64 semantics of the source types, natively, in both native and lifted code. Bit-equality with the exe's x87 80-bit results is not required.
  - These stay exact by rule:
    - float-to-int conversions, the MSVC `_ftol`/`fistp` semantics;
    - NaN behaviour of comparisons;
    - integer results of FP code;
    - FP bit patterns;
    - the rcp/rsqrt table.
  - Transcendentals return the correctly rounded value at the operand type.
- **One fixed reference CPU model:**
  - rcp/rsqrt use the reference CPU's exhaustive 2^32 table from a pinned farm host;
  - cpuid returns the reference CPU's fixed answer, and rdtsc reads the platform's monotonic timer.
- **Performance metric:** executed instructions per frame, main thread and each worker, counted deterministically in the emulator. The budget comes from 45 fps at 1.02 GHz with a stated IPC assumption.
- **Switch product decisions:**
  - The game's own Xbox-controller layout maps 1:1 onto the Switch buttons.
  - The system software keyboard handles all text entry.
  - Rumble is on, through the game's own controller vibration.
  - Docked output is 1080p, handheld 720p.
  - Frame pacing on the 60 Hz panel is decided from the first measured frame.
  - Packaging is deferred until the game runs in the emulator.
    - The final package is the game's own installed title: an NSP with its own title id, NPDM (32-bit address space), NACP and icon, packed with the owner's console keys.
    - Development runs use an Atmosphere exefs override.

## The prepared build machinery is the only one (owner, 2026-11-11, binding)

The owner's words: "Я буквально сказал, что я использую только конкретный компилятор... Мы нахуя инструменты подготавливали? Чтобы потом просто игнорить?"

- **Compiler:** only the one pinned in `verify/env.json` (g++-13-aarch64-linux-gnu 13.3.0-6ubuntu2~24.04.1cross1, binutils 2.42-4ubuntu2.10), installed by `farm/setup_verdict_env.sh` and checked by `envcheck.py`. No unpinned apt, no other gcc.
- **Our own compiler (the game's compiler)** is `toolchain/msvcmodel` (MSVCMODEL1). It is GCC 13.3.0 built from the sha256-pinned GNU tarball, with `msvcmodel.patch` applied. Under `-mabi=ilp32` it has the exe's Visual C++ 2008 data model. That model is one table, DATAMODEL, derived from Microsoft's documentation with a test per row: `size_t` = `unsigned int`, `ptrdiff_t` = `int`, signed `char`, 16-bit `wchar_t`, binary64 `long double`, MSVC's new[] cookie and delete[] size, and every other row of the table. **Target (orchestrator decision, 2026-11-15):** it is configured `--target=aarch64-none-elf --with-newlib` with the ILP32 multilib, because the guest is a Horizon process over newlib, not Linux. It is built in one build together with its target libraries (libgcc, crt, newlib from the pinned fnvnx-a64 devkit sources, GCC 13.3's own libstdc++), and the configure line is recorded. That one mirror is both the compiler of every guest root and the guest runtime. The headers are newlib's and its own. Binutils are the env.json 2.42 package when its ld has the aarch64elf32 emulations; otherwise binutils 2.42 are built from the pinned GNU sources in the same build.
  - It enters every guest product root as the recipe's external compiler (`{"external", "cc", "cxx"}`, RECIPE.md): a fnv-nx-build mirror `tools/gcc-ilp32-msvcmodel`, pinned by commit and `tree_sha256`.
  - Our GCC plugins are built against its own plugin headers, as hermetic recipe stages; each `.so` is a declared input, so it is in every key. They are `fnvnx_order` (x86 TSO and MSVC volatile become `ldar`/`stlr`; MSVC argument order), `fnvnx_ftoi` (MSVC `_ftol`), and L2's `addr`, `dispatch`, `names`, `sdd` and `pi` (plus the clang `vtnames`).
  - Native and decomp C++ load them all.
  - Lifted C++ is compiled by the same compiler; the lifter emits the memory ordering itself (R9 (б)).
  - The stock `g++-13` of `env.json` is the base the compiler is built from. It is not the game's compiler.
  - The guest runtime is built by the game's own compiler, in the same none-elf build (2026-11-15). Until that build lands, it is `tools/fnvnx-a64-msvcmodel13` (L5 w14, with a MANIFEST.json the recipe compares against the compiler before any unit runs). Its libstdc++ (headers and library) is GCC 13.3's own, the same version as the compiler. newlib and libnx come from the fnvnx-a64 devkit sources, rebuilt with msvcmodel. The gcc 15 libstdc++ headers of fnvnx-a64 are not used for guest code: they do not parse under gcc 13 (orchestrator decision, 2026-11-12).
- **Build:** `verify/recipe.py` (`verify/RECIPE.md`). A product root has `build.json` and is admitted in `verify/admit.json`. Each unit is two hermetic stages (`unitstage.py`: PP, then CC), keyed by the content of what they read plus compiler and flags.
- **Cache:** the verdict's own store, `GATE_CACHE` (`l1.py` gives `VDIR/cache`; objects in `GATE_CACHE/store`). An unchanged unit is never compiled again. Do not add another cache or call the compiler directly.
- **Private sources** (lifted C++, decomp trees) enter a recipe only as an `external`: a fnv-nx-build commit plus `tree_sha256`.
- **Lifter:** `liftall.py` generates C++. Its objects are built by `recipe.py` in that store, as a product root with the pack as an external.
- Missing something in this machinery? Extend it and say so in the inbox. A parallel path is a rule violation.

## Owner orders of 2026-11-11, evening (binding for every session; an order sent only by message is not enough)

The owner's words: "Ты когда пишешь че-то агентом они проебываются. Потому что это не в глобальных правилах."

- **Standing orders live here.** An order that binds more than one session is written into this file by the
  orchestrator and pushed to the trunk and the integration branch. A message only points to it.
  - Every lead merges the trunk's `CLAUDE.md` into its branch when told, and re-reads this file after every compaction.
  - If an orchestrator message and this file disagree, this file wins; report the conflict.
- **Assumptions: one registry, ten accepted.** The owner asked "Сколько допущений?", and no one could answer.
  - Every assumption has an id in `verify/ASSUMPTIONS.json`. The `assumptions` gate makes the trunk red for:
    - an "Assumption"/"допущение" in code or docs without an id;
    - an id without a record;
    - a new OWNER record without the owner's decision.
  - OWNER, the only accepted ones:
    1. the compiler is correct;
    2. SHA-256;
    3. Win32 and D3D9 as documented;
    4. the console's behaviour until a run on it;
    5. native code is tested by ASan/UBSan, not proven;
    6. the exe makes no wild accesses (array indices in bounds);
    7. ILP32;
    8. IEEE floating point;
    9. the pinned formal tools and published theorems are correct: the Sail CPU model at its pinned patch, isla and z3,
       the published proof of the x86-TSO to Arm memory mapping (owner decision, 2026-11-11).
    10. the x86 ISA as documented in the Intel SDM, for the parts that no pinned formal model covers (owner decision, 2026-11-15: "Тогда согласен делай");
        today the x87 machinery: TOP and stack indexing, tags, FSW C0-C3 and exception bits, FCW, the fcomi/fucomi EFLAGS,
        fld/fst conversions. It is transcribed as a z3 spec with an SDM citation on every line, owner L3b; values stay IEEE (8).
  - Every other assumption is REMOVE. Its record names the fact or gate that removes it and the owner of that work.
    Examples: callee-saved registers, `ret n`, no PIC and complete code ranges are checked from the exe's code; kernel
    result codes come from the pinned libnx headers; guard zones are a fact of the loader's layout.
  - A session never introduces a new assumption to get a gate green. It reports it to the orchestrator.
- **Real global numbers.** Every status, note and report gives totals over the whole set (all units, all sites, all
  tests) with the commit they were measured at. Sample counts ("on a sample of 232 units") never stand for a result.
- **The goal comes first.** The goal is a fully lifted ELF, then exe entry, WinMain, the main menu, the first frame in
  the emulator, then the Switch.
  - The next steps are:
    1. the first product root on the trunk (platform, built by the recipe with the msvcmodel compiler and all plugins);
    2. the lifted packs as product roots;
    3. the ELF;
    4. the boot.
  - A new gate or proof is written now only if it blocks one of these steps. Everything else waits until after the
    first frame.
- **Sanitizer builds** are a flags variant of the same product root in `verify/recipe.py`, with the msvcmodel compiler and
  plugins, in `GATE_CACHE`. The boot machine holds the shadow memory and the `__asan_*` callbacks; UBSan traps report
  the pc.

## Owner order of 2026-11-11, night: no doubles in tests (binding)

The owner's words, on a render defect that unit tests missed because the sibling modules were replaced in them:
"Это что за бред, заглушки?"

- A replaced module in a test is a stub. It counts in the stub gate, which stays an absolute 0.
- A test links the real product files of its root: the same unit set and flags that `verify/recipe.py` builds.
  - A test never defines a symbol that a product root defines. This includes recording doubles, fakes, mocks and
    hand-written stand-ins of sibling files.
  - The check is mechanical, per test binary: (symbols the test defines) ∩ (symbols the product roots define) = ∅.
    There is no allow-list. The owner is L1, as part of the stub gate.
- Below the product there is one boundary: the GL library (for the OpenGL backend), deko3d, libnx and the kernel. A test that runs on the host meets it in one
  shared model, the L4 boot machine (its GPU and kernel model, checked against its references). There is no
  per-test double of it.
- Failure injection is allowed only as an input of that shared model, such as a kernel or allocator result that
  Windows, Wine 9.0 or the console documents. A test never injects it by replacing a function.
- Existing tests with doubles are rewritten onto the real files and the shared model, or deleted. Each lead gives the
  global count of its tests with doubles (files and symbols, with the commit) and takes it to 0.

## Owner orders of 2026-11-11, late night: leads, workers, one fast build (binding)

The owner's words: "Агенты вызывают соннетов которые делают задачи. Задачи соннетов механические. Опусы чекают
корректность в рамках своей области. Обязательно трекать текущие задачи, статусы и общий прогресс." and "Заниматься
остальным можно только когда сборка идет и нет более приоритетных задач. Сборка с гейтами должна оставаться почти
моментальной. Сборка должна быть универсальной. И всегда быстрой. Единой."

- **Leads are Opus 5.5** (`claude-opus-5-5`), one per layer: L1 contract and verdict, L2 source, rules and plugins,
  L3a lift, L3b lift certification, L4 verify and boot machine, L5 platform, L6 render (D3D9 front end), L6A deko3d.
  - The lead gives mechanical tasks to **Sonnet 5.5 workers** (`claude-sonnet-5-5`): edits by a stated rule, builds
    and runs through the recipe or a farm, censuses, moving tests onto the real files.
    - A worker task names the files, the rule, the check that proves it and the branch.
    - Farms are Sonnet sessions too.
  - The lead checks every worker result for correctness within its layer before it takes it into the lead branch:
    - the rule closes the whole class;
    - the numbers are global, with the commit;
    - the build used the prepared machinery.
  - The lead does not do mechanical work itself.
- **The build comes first.** The one build is `verify/recipe.py` plus the L1 gates, in `GATE_CACHE`.
  - Any other work is done only while that build is running and nothing of higher priority is open.
  - The build with gates stays almost instant: content-keyed, so an unchanged unit is never rebuilt and a gate reads
    cached facts.
  - It is universal: every product root (platform, lifted packs, render, deko, loader, tests, sanitizer variant) is
    built by this one build. There is no per-layer build script, no second cache and no direct compiler call.
  - It is always fast. A change that makes the incremental verdict slower than its budget is red like any failing gate.
- **No idling.** Each lead always has its layer's next step on the goal path in progress.
  - Waiting on another layer is a reason to take the next item, not to stop.
  - A blocked item names its blocker and the layer that owns it.
- **Status, always current.** Each lead keeps `ports/fnv-nx/journal/status/<layer>.md` on its branch and pushes it after
  every finished item. It holds:
  - every current task, with its worker session id and status: doing, done (commit) or blocked (by what, owner);
  - the layer's global progress numbers, with the commit they were measured at.
  - The orchestrator collects these files into the overall progress.
- **Two render backends: OpenGL and deko3d (owner correction, 2026-11-11 22:45; it replaces the earlier
  "one backend" line, which was the orchestrator's mistake).** The owner's words: "их два. Один опенгл другой деко"
  and "Нахуя ты гл удалил. Ты же рендерил с помощью него."
  - The OpenGL backend (`gl4`, `nxgl`, the renderer pair and `verify/bootgpu.py` with its GL library) is product and
    stays. It is the backend that already rendered frames, and it renders in the emulator. Nothing of it is deleted.
  - The deko3d backend (L6A, `render/backend`) is the second backend, built beside it, for the console.
  - **OpenGL is the reference for deko3d** (owner, 22:50: "Опенгл должен был быть эталоном для деко"). The same
    nxgpu command stream (the same D3D9 front end, the same scenes and game frames) is rendered by both backends, and
    the deko3d frames are checked against the GL frames. A difference is a deko3d defect until it is explained by
    rule. GL frames are produced on the boot machine with the GL library. This check verifies deko3d; it never
    selects code.
  - The D3D9 front end (L6) serves both through the same nxgpu ABI.
  - The worker branches that deleted GL (`claude/fnv-l6-render-w1` 89fa6a889, `claude/fnv-l4-verify-w1` 68e37dab7)
    are not merged, ever. No session deletes or disables GL, Mesa in the boot machine, or their tests.

## Owner order of 2026-11-11, night: the first frame in this budget, correct by construction (binding)

The owner's words: "Трекай прогресс послойно и прогрессируй всегда. Задача понятная. Осталось 50% недельного лимита. За
это время можно закрыть первый кадр точно. Что говорить об остальном. Опять же все должно быть корректно по
построению."

- **Target:** the first frame in the L4 emulator within the remaining half of the weekly usage limit. Everything a
  session does is measured against that path: product roots → lifted packs as roots → ELF → boot → WinMain → menu →
  first frame.
- **Spend only on the path.** Work that does not move a layer toward the first frame waits until after it. Workers are
  Sonnet; long or mechanical runs go to farms or workers, not to a lead's own context. No repeated censuses of numbers
  that did not change; a gate reads cached facts.
- **Correct by construction.** Each step is closed by its rule and gate, never by a run that happened to pass. A shortcut
  that breaks a rule (a stub, a double, a second build path, a new assumption, a heuristic) is not progress: it is red.
- **Progress is tracked per layer** in `ports/fnv-nx/journal/status/<layer>.md` (see "late night"); the orchestrator
  collects them into `ports/fnv-nx/journal/status/ALL.md` on the integration branch every hour.

## Owner order of 2026-11-11, 23:00: the orchestrator decides, the owner is not disturbed (binding)

The owner's words: "Я запрещаю меня тревожить и просить мое да. Делай что хочешь... Я разрешаю тебе как оркестратору
решать что архивировать закрывать а что нет. Ты главный. А я сплю. Меня нельзя тревожить и нельзя не делать то что
необходимо."

- No session asks the owner for a yes, a confirmation or a choice. The orchestrator decides everything the owner would
  decide, within these rules and the Legal limits, and records the decision in ALL.md.
- The orchestrator decides which sessions are archived, stopped, resumed or created (leads, workers, farms, old waves).
  Stale, finished or duplicate sessions are archived without asking.
- Nothing necessary is left undone for want of an answer: a blocked decision goes to the orchestrator, never to the owner.
- No messages to the owner except milestones on the goal path (exe entry, WinMain, menu, first frame, Switch).

## Owner order of 2026-11-11, 23:00: GOG Galaxy is not lifted (binding)

The owner's words: "нахуя лифтить гэлакси... Ну нет доступа к нему. Похуй на него. Это как сокеты."

- Galaxy.dll and GalaxyWrp.dll are not lifted. The declared machine has no Galaxy service, the same as it has no network.
- Every Galaxy entry the exe or a module reaches answers the documented "Galaxy unavailable / not initialized / not
  signed in" result of the Galaxy SDK, the same way network calls answer the documented "network down" error. It is a
  declared-machine body (CLAUDE.md "The declared machine"), with the SDK documentation cited per function, not a stub.
- Owner of the bodies: platform (L5). Lifting work on Galaxy stops; modules.tsv lists Galaxy as a declared-machine
  module, not as a lifted one.

## Owner order of 2026-11-14: the full native port, correct by construction, quota only on the path (binding)

The owner's words: "делегируй остаток работы аккуратно так, чтобы мы действительно двигались к полному нативному
порту. Каждый шаг должен приближать нас по известным правилам описанным в проекте.", "мы не должны тратить квоту туда,
куда нет смысла. Лишние суммаризации или бесконечные верификаторы всего на свете ни к чему. Только корректность по
построению, закрываемая известным правилом." and "цели в 45/60 фпс остаются. Корректность тоже обязательна и
незыблема. С редкими заранее просчитанными исключениями. Например, мы верим в корректность gcc."

- **The goal is the full native port** on the Switch: the exe and its modules fully lifted, the native platform, the
  D3D9 front end over the GL backend (the reference) and deko3d. The order of the steps is the one in "The goal comes
  first".
- **Every step is closed by a known rule.** A rule is known when it is written in this file, `verify/VERDICT.md`,
  `verify/RECIPE.md` or MASTER_PLAN4, and the gate that checks it is in the L1 verdict. A step for which no known rule
  exists is reported to the orchestrator; it is not improvised.
- **Correctness is mandatory and unconditional.** The only exceptions are the OWNER records of
  `verify/ASSUMPTIONS.json` (for example: the compiler is correct). They are decided in advance; none is added without
  the owner.
- **Performance targets stay:** 45 fps is the floor and 60 fps (the panel's rate) is the target, on the console's
  1.02 GHz CPU. Correctness is never traded for speed. Speed work is measured from the first frame on. Until then no
  design choice may close the way to the targets: no one-guest-thread-at-a-time, no interpreter or emulator on the
  product path.
- **Quota goes only to path steps.** Not spent on:
  - summaries beyond the lead's own status file;
  - censuses of numbers that did not change;
  - new verifiers, gates or farms that do not block a path step;
  - runs repeated on unchanged content (the verdict's cache answers them).
  - One verdict per candidate. A lead updates its status file after each finished item; that is the report.
- **Orchestrator decisions of 2026-11-14 (path plan).** The tasks and their order are in
  `ports/fnv-nx/journal/status/ALL.md`, section "Path plan 2026-11-14", on the integration branch.
  - **Guest runtime first.** The platform root enters the trunk only when it is built against
    `tools/fnvnx-a64-msvcmodel13`, with its MANIFEST checked by the recipe. A build against the gcc 15 libstdc++ of
    fnvnx-a64 is a measurement. A source change made only so that the gcc 15 headers parse is reverted once
    msvcmodel13 is in.
  - **Shape certificates by the pack's own universe.** L3b certifies the shape keys that the product pack uses. L3a
    publishes that list with the relift. The work does not wait for the pack root to be admitted. The pack root enters
    the trunk when its forms are a subset of the certified ones. x87, SIMD and system shapes get certified builders from
    the same prover (the Sail model, ASM-009 / OWNER 9).
  - **Hardware exceptions in lifted code.**
    - Under OWNER 6 an access fault does not occur in normal operation, so there is no asynchronous fault recovery.
    - Every other x86 exception the exe can raise is synchronous at a known instruction: #DE of div/idiv, int3,
      int n, ud2, into, bound, and x87/SSE exceptions only when unmasked. The lifter emits the check there and raises
      through the SEH path, with the precise CONTEXT of that instruction.
    - It is a fact that every fldcw, ldmxcsr, fldenv, frstor and fxrstor site keeps the exceptions masked. A site the
      facts cannot decide is reported to the orchestrator.
  - **SEH continue restores the whole CONTEXT** that the record's ContextFlags names: integer, control, FloatSave and
    the extended registers. This is how Windows documents it (OWNER 3) and how Wine 9.0 implements it.
  - **Shaders.**
    - The game's shaders are a static set from its gamedata. The translator's link check (`sm_link`) over that whole
      set is a gate at 0 failures.
    - The backends' link-failure branch becomes `fnvnx_reach_violation`, with that gate's certificate as its fact.
      So neither GL nor deko3d has a run-time link-failure path.
  - **The product ELF root.**
    - Owner: L3a, with a worker.
    - L1 supplies the link stage (`link/guest.json`) and the onedef and link-root rules.
    - Undefined symbols get their owners from the closure:
      - `fnvnx_dispatch`: L3a's generated bridges;
      - the process entry `main`: L5's own root `rt/guest/main`, which only calls `fnvnx_loader_main()` (the L1 loader: map, imports, TLS, DllMain, TEB/stack, then `fnvnx_lift_enter(AddressOfEntryPoint)`). `fnvnx_lift_enter` (lift/rt) is the single bridge into lifted code, and there is no second entry path;
      - `fnvnx_import_key` / the IAT root: L1;
      - the pthread mutexes that libgcc's unwinder needs: L5, over its real threads.
  - **Acceptance.** The L4 boot milestones extend past loader init: exe entry, WinMain, first frame (GL in the
    emulator: the GL backend's first present). The menu gets an input-scenario rule after the first frame; no
    certified fact names it by role. Each is an observable in the boot machine's trace, recorded with the instructions per frame of every
    thread.
  - **The boot machine's GL** comes from the recipe-built gl4 root, not from the prebuilt libnxgl4 @2f0427f5.
  - **One registry.** `render/ASSUMPTIONS.md` is merged into `verify/ASSUMPTIONS.json` or deleted.
  - **Second build paths.** No new run goes through them. Each is deleted once its root is on the recipe:
    - the L5 host Makefiles s4/s7 and `build_guest.sh`;
    - the loader Makefile;
    - the unpinned apt in `farm/boot_job.sh`;
    - the lift tests' Unicorn harnesses.
  - **Off the path until the first frame:**
    - the deko3d harnesses and the uam work;
    - vtnames and decomp C++;
    - the native CRT;
    - toolchain mirror reproducibility;
    - IKOS;
    - any census of numbers that did not change.
- **Correctness never switches off a fast path (owner, 2026-11-14).** The owner's words: "Очень хотелось бы сразу классом
  оптимизаций это закрывать. Иначе получится, что мы просто так ухудшили производительность и забили".
  - A correctness rule is met by giving the fast path itself the exact semantics, by a rule over the whole class:
    a plugin rewrite, an exact helper, or a proof of range. Turning the fast path off is not the way. That covers
    SIMD, the vectorizer, an intrinsic, inline asm and a library's optimized path.
  - A switch-off that a green trunk needs before the class rule exists is recorded in `verify/PERFDEBT.json`. Each
    entry names the class rule that restores the fast path and its owner. The entry and the switch-off are deleted in
    the same commit that lands the rule. PERFDEBT stays at 0 when the first frame is measured.
  - First entry: `MINIMP3_NO_SIMD` (audio/dshow_filters.cpp, L5). The restoring rule is L2's: `fnvnx_ftoi` rewrites
    every explicit target float→int builtin (vcvtq_s32_f32, vcvtq_u32_f32 and their family) lane-exact. In range it
    converts; out of range or NaN it gives x86 integer-indefinite (cvttps2dq semantics). Then fpexact certifies the
    NEON path and the switch is removed.

## Owner order of 2026-11-15: parallel loops and deviations, after the first frame (binding)

The owner's words: "Делаем, так как данные уже будут для анализа этого" and "Это не должно быть так, что мы просто решили
исправить все баги. Это разрешено только если решает конкретную задачу, например, позволяет добиться большей
производительности за счет улучшения корректности."

- **Parallel-loop rule (speed, after the first frame).** A loop of the main thread runs its iterations on several cores
  only when it is proven statically, by the one points-to/write-set model, that:
  - the iterations write disjoint memory and read nothing another iteration writes;
  - they touch no shared state (allocator, reference counts, shared lists), or that state is made thread-safe natively first;
  - there is no floating-point reduction across iterations.

  Execution is fork-join, so the result is bit-identical to the sequential loop. A loop that is not proven stays sequential.
  Which loops to try comes from the per-function instructions-per-frame data; the proof decides.
- **Deviations (`verify/DEVIATIONS.json`).** A change of the exe's behaviour is allowed only when it serves a stated
  goal on the path (for example a speed rule), never as bug-fixing for its own sake. Each entry gives:
  - the original behaviour and why it is undefined or a defect (a data race, a crash, an out-of-bounds access);
  - the new behaviour;
  - the proof that every behaviour of the new program is one the original program could already show on its defined
    inputs. For example, adding synchronization to a race only removes interleavings;
  - the goal it unlocks, such as a rule id or a PERFDEBT/speed item;
  - its owner.

  A deviation without a goal, or without that proof, is red.
  - Example (owner): a worker thread writes UI state concurrently with the main thread. That is a race. Making the write
    ordered through the main thread lets the worker run on its own core.

## Owner order of 2026-11-15: defects impossible by construction (binding)

The owner's words: "Надо закрыть те проблемы которые мы обсуждали на корню. Чтобы новых подобных просто не могло
возникнуть."

The defects of 2026-11-14/15 had one cause: a fact was enumerated from memory, copied, or reachable by a second path,
instead of derived once from a complete source. Examples: `long double` missing from the data model; a linux-gnu target
for a Horizon guest; data lifted as code; a test compiling with stock g++; a dead second ftoi plugin; numbers counted on
a stale tree. The rules below make that class impossible. Adding checks on top of checks does not.

1. **Derive, never enumerate.**
   - Every set or table that defines behaviour or coverage is generated by a recipe stage from one complete, pinned
     source, with the source cited per row. Examples: the data model, the ISA spec, the Win32 bodies, the shader set,
     the image set, code ranges, thread kinds and gate universes.
   - The source is one of: the exe and module bytes, the owner's gamedata, or a pinned edition of the vendor's
     documentation (Microsoft, Intel, Arm, the Horizon/libnx headers).
   - A set written by hand from memory is red, even when it is complete today.
2. **One source of truth, everything else generated.**
   - A fact lives in exactly one file.
   - Whatever follows from it is produced by a recipe stage and is never written a second time. Examples: compiler flags
     from DATAMODEL, the dispatcher from the facts, the bridges from the descriptors.
   - A second copy of a fact is deleted, not gated.
3. **One path, alternatives deleted.**
   - There is no second compiler call, plugin, cache, build script or test runner.
   - When one is found, it is deleted in the same commit that reports it. It is never kept behind a check.
4. **Universes by one rule.** Every universe is defined by one rule over the tree or the exe, so a new item is inside
   automatically. Examples: files, tests, functions, sites, threads. "Every test file by the naming rule" counts;
   "these 12 tests" does not.
5. **The declared target is one record each, and everything reads it.** These are:
   - the guest platform (Horizon process over newlib, aarch64-none-elf, ILP32);
   - the console CPU (Cortex-A57 feature set);
   - the game's data model (DATAMODEL);
   - the declared x86 CPU (CPUREF);
   - the declared machine.

   No root or tool restates any of them.
6. **Numbers come only from a run at a commit.** A reported number is the output of a recipe or gate run, given with its
   commit and command, read from the cached facts. It is never retold from memory or counted on another tree.
7. **The trust chain ends at the OWNER registry.** A gate is ordinary code. Its lead checks it against its rule, and it is
   tested against its references. No gate checks another gate, and below the OWNER entries nothing is checked.
8. **A found defect closes its cause.**
   - Every defect report names the enumeration, copy or second path that let it happen.
   - The fix replaces that with a derivation, a generation or a deletion (rules 1-3).
   - A fix of the single item alone is not accepted.

## Orchestrator decision of 2026-11-15: memory ordering of runtime calls (binding for L1, L2)

- The guest runtime (newlib, libstdc++, libgcc, libnx) is compiled by the game's compiler with every plugin. So a callee
  whose body is C or C++ orders its own accesses, and a call to it is a plain call.
  - There is no fenced call and no libcall or string-op record.
  - Membership of a fold set never assigns a class.
- newlib is built with `-DPREFER_SIZE_OVER_SPEED`, read from the one guest compiler record. With it, its aarch64 `.S`
  string routines use their C sources.
- The remaining assembly is derived from the built runtime objects: every object assembled from `.S` or `.s`. Each one
  is closed by a stated class:
  - it touches only jmp_buf and the frame (D1);
  - it runs before threads;
  - it is a kernel call;
  - it is an atomic with its documented ordering.
  An assembly object with a memory access outside these classes makes the runtime stage red. There is no allow-list.
- The ordered C string loops switch off a fast path. They are `PD-RT-STRING-ORDERED` in `verify/PERFDEBT.json`
  (owner L2), 0 at the first frame. The restoring rule gives each string routine a body whose ordering is proven equal
  to the x86 semantics of the call in the source:
  - where the exe's own CRT routine uses `rep movs`/`stos`, SDM §8.2.4 fast-string semantics (fenced at entry and
    exit, unordered inside);
  - elsewhere, ordering per x86 store, by size path.

## Orchestrator decision of 2026-11-15: sources of the declared machine (binding for L3a, L3b, L5)

- Every row of the declared-machine records (`platform/declared/os.json`, `x86sys.json`) takes its value from these
  sources, in order:
  1. Microsoft documentation and the WDK 7.1 headers for Windows 7;
  2. Wine 9.0's code, only where it contradicts neither source 1 nor the declared OS version (Windows 7). For example,
     vector 0x29 (`__fastfail`, Windows 8 and later) is not in the set.
- No other source counts; ReactOS is not one. A row with no source stays undecided and names its blocker.
- A site in the exe or a module whose answer depends on an undecided row gets a call-graph reach certificate and goes
  to `fnvnx_reach_violation`. Without the certificate the build is red, and the row is reported to the orchestrator.
- Other code reads these records and never restates them (rule 2). seh.cc's trap rows are an example.
- **Where the Microsoft page is narrower (orchestrator, 2026-11-15 13:45Z; it applies to D3D9 and its shader models as well).**
  - Microsoft does not list a case, but Wine 9.0 accepts it. That case is a silence, not a contradiction, so source 2
    fills it and the accepted set is the union.
  - Each such case is a cited conflict row: the Microsoft page and section, and the Wine 9.0 file and line. Examples are
    `ps_3_0:vFace_src`, `ps:47` and `vs_3_0:v[a0.x]`.
  - Where Microsoft states the opposite, Microsoft holds.
  - "The opposite" means a Microsoft statement of what the system itself does: a result, an error code, a behaviour.
    A rule addressed to the application ("applications may not use vs_3_0 with the fixed function pipeline") names no
    result of the system, so it is a silence on that result, and Wine 9.0 fills it as above (orchestrator, 2026-11-15 16:00Z).

## Orchestrator decision of 2026-11-15 09:20Z: weekly budget (binding for every session)

- The owner's usage page reads: weekly 29 % used, reset on Saturday 2026-11-21 18:00Z. At the pace so far the limit ends
  on 2026-11-16 at night, five days early. The budget is at most about 11 % of the week per day until the reset.
- Until the orchestrator lifts this:
  - **Workers:** at most 4 per lead (owner, 2026-11-15 20:00Z: "Разреши лидерам большее количество саб-агентов
    реализаторов"; it was 2). Each runs only a path item that blocks the next step: first root, packs as roots, ELF, boot.
    - A lead splits each path item into independent pieces and runs them in parallel, one worker each. While a build runs,
      its worker prepares the next piece rather than waiting.
    - A worker whose piece is done, or waits on another layer, is archived, not left idle.
  - **Farms:** at most 4 per layer. Never rerun on unchanged content; a farm stops as soon as its queue is done.
  - **Leads:**
    - one message per finished item, no interim reports;
    - no re-reading of large files already read;
    - compact the context before 250k tokens;
    - an idle lead with nothing on the path waits; it does not invent work.
  - **Off the path:** no verifier, census, refactor or proof waits for nothing else. Everything that does not block one of
    the four steps waits until after the first frame.

## Orchestrator decision of 2026-11-15 09:45Z: message discipline (binding for every session)

Measured (fleet study, 09:20Z): 87 % of the orchestrator's turns were started by messages from other sessions, and half
of those turns did nothing. Each turn re-reads the whole context, 200-300k tokens. That is where the weekly limit goes.

- Send a message only when the receiver must act on it now: a ruling it needs, a blocker, or a finished item it takes
  over.
  - No acknowledgements, no "resumed", no "kept N workers", no FYI, no progress reports.
  - A reply that only says "accepted" is not sent.
- Status lives only in `journal/status/<layer>.md`, pushed when an item is finished. The orchestrator reads the status
  files in its hourly check. A status commit is not a message.
- One message per finished item, carrying everything in it: the commit, the global numbers, what is next. A worker
  reports to its lead once per item, not per step.
- Send with `priority: "later"`, unless it unblocks the receiver now.
- Farm prompts do not tell the farm to read CLAUDE.md: it is loaded automatically.

## Orchestrator decision of 2026-11-15 10:20Z: a candidate is frozen (binding for L1 and its workers)

- The trunk last moved on 2026-11-12. The candidate cand2 is 620 commits ahead of it, and every new item resets its
  verdict, so nothing merges. That is the cause.
- Rule:
  - A candidate's scope is frozen when its verdict starts.
  - It takes only fixes for the reds of its own verdict.
  - Every new item goes to the next candidate (cand3), which opens from the trunk right after the merge.
  - Small candidates, merged often: at least one trunk merge a day while anything is open.

## Orchestrator decision of 2026-11-15 10:30Z: call economy (binding for every session)

Measured by the research session: spend = API calls × the whole context, because every tool call re-reads the full
context. Mail made up 89 % of L1's calls; re-reading sources made up about 38 % of the workers' calls. So make fewer
calls, not smaller ones.

- **Calls:**
  - Run independent tool calls in parallel, in one response.
  - Chain dependent shell steps in one Bash call (`a && b`).
  - Read a file once, in full, and re-read only a range that changed. To find a symbol, use one `rg -n` with context,
    not a series of `sed -n` slices.
  - Put a script you run more than once in a file instead of retyping it.
- **Mail:**
  - Send a message only when the receiver must act or decide, or when a stage is finished. Blockers go at once.
  - One message per stage: collect the points and send them together.
  - Read all queued notifications in one ReadNotifications call. Decide, then send every reply in one response, as
    parallel send_message calls. Never send an acknowledgement.
  - Use priority "next", unless the receiver must stop what it is doing.
  - To learn a worker's state, read its status file or call list_sessions with children: true. Do not ask the worker.
  - Commit the status file once per stage, not after each message.
- **Code reading goes to a subagent (workers and leads).**
  - Use the Agent tool, type Explore, when an answer needs more than 3 read or search calls: tracing a function,
    comparing files, finding every user of a symbol.
  - The brief gives the question, the paths, what you already know and the output you want: file:line references and
    identifiers quoted verbatim, under 300 words.
  - Check every hash, address or id it returns with one targeted command before you commit or send it.
  - When you know the file and the place, do the one read or grep yourself.
- **Addendum, 12:45Z (cand2 cannot go green on its own reds).** The candidate merges cut, under the trunk rule "code that
  does not pass is removed from the trunk whole".
  - admit.json keeps only the roots that are green over every gate.
  - A test without a certified reference, and an uncertified generator together with the roots that use it, leave the
    candidate. Their completion stays on the lead's branch.
  - A gate's own failing selftest is fixed, not cut.
  - The warm verdict stays at most 300 s.
