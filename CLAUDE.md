# Standing instructions from the repository owner

These instructions come from the owner of this repository. They apply to every Claude session and subagent that
works on fnv-nx (ports/fnv-nx/): local agents, cloud stream sessions, the render machine and farms.

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
  - **Values outside an assumed domain (orchestrator decision, 2026-11-12).** A value that a generator-produced total domain does not cover, where the domain rests on an OWNER assumption (for example the errno set of the pinned libc under ASM-004), goes to `fnvnx_assume_violation(asm_id, value)`, beside `fnvnx_reach_violation`. It exits loudly through the host exit slot with the assumption id and the value.
    - `asm_id` is a literal whose record in `verify/ASSUMPTIONS.json` is OWNER. A REMOVE id, a missing id, a non-literal id or a hand-written domain makes the build red. Owner of the gate: L1.
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
- **Our own compiler (the game's compiler)** is `toolchain/msvcmodel` (MSVCMODEL1). It is GCC 13.3.0 built from the sha256-pinned GNU tarball, with `msvcmodel.patch` applied. Under `-mabi=ilp32` it has the exe's Visual C++ 2008 data model: `size_t` = `unsigned int`, `ptrdiff_t` = `int`, and MSVC's new[] cookie and delete[] size. The headers and binutils come from the pinned distribution cross packages in `env.json`.
  - It enters every guest product root as the recipe's external compiler (`{"external", "cc", "cxx"}`, RECIPE.md): a fnv-nx-build mirror `tools/gcc-ilp32-msvcmodel`, pinned by commit and `tree_sha256`.
  - Our GCC plugins are built against its own plugin headers, as hermetic recipe stages; each `.so` is a declared input, so it is in every key. They are `fnvnx_order` (x86 TSO and MSVC volatile become `ldar`/`stlr`; MSVC argument order), `fnvnx_ftoi` (MSVC `_ftol`), and L2's `addr`, `dispatch`, `names`, `sdd` and `pi` (plus the clang `vtnames`).
  - Native and decomp C++ load them all.
  - Lifted C++ is compiled by the same compiler; the lifter emits the memory ordering itself (R9 (б)).
  - The stock `g++-13` of `env.json` is the base the compiler is built from. It is not the game's compiler.
  - The guest runtime is built by the game's own compiler: `tools/fnvnx-a64-msvcmodel13` (L5 w14, with a MANIFEST.json the recipe compares against the compiler before any unit runs). Its libstdc++ (headers and library) is GCC 13.3's own, the same version as the compiler. newlib and libnx come from the fnvnx-a64 devkit sources, rebuilt with msvcmodel. The gcc 15 libstdc++ headers of fnvnx-a64 are not used for guest code: they do not parse under gcc 13 (orchestrator decision, 2026-11-12).
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
- **Assumptions: one registry, nine accepted.** The owner asked "Сколько допущений?", and no one could answer.
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
