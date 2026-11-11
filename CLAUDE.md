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
- **Our own compiler** is that pinned g++-13 together with our GCC plugins: `fnvnx_order` (x86 TSO and MSVC volatile become `ldar`/`stlr`; MSVC argument order) and `fnvnx_ftoi` (MSVC `_ftol` semantics), with the semantics flags of `tools/link/compile.sh`. `env.json` also pins `gcc-13-plugin-dev-aarch64-linux-gnu`, the plugins' build base.
  - Every native and decomp C++ body is compiled with both plugins (plan R9 (в), `ORDER_MODE=tso`).
  - Lifted C++ needs no plugins: the lifter emits the ordering itself (R9 (б)).
  - On the trunk, the plugins enter a recipe as a hermetic stage. The `.so` is a declared input, so it is part of every key; `toolflags.py` accepts `-fplugin=P` only under a declared input.
  - fnvnx-a64 (gcc 15 + newlib ILP32) is the Switch and libnx toolchain. It is not the compiler of the game units.
- **Build:** `verify/recipe.py` (`verify/RECIPE.md`). A product root has `build.json` and is admitted in `verify/admit.json`. Each unit is two hermetic stages (`unitstage.py`: PP, then CC), keyed by the content of what they read plus compiler and flags.
- **Cache:** the verdict's own store, `GATE_CACHE` (`l1.py` gives `VDIR/cache`; objects in `GATE_CACHE/store`). An unchanged unit is never compiled again. Do not add another cache or call the compiler directly.
- **Private sources** (lifted C++, decomp trees) enter a recipe only as an `external`: a fnv-nx-build commit plus `tree_sha256`.
- **Lifter:** `liftall.py` generates C++. Its objects are built by `recipe.py` in that store, as a product root with the pack as an external.
- Missing something in this machinery? Extend it and say so in the inbox. A parallel path is a rule violation.
