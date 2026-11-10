# Standing instructions from the repository owner

These instructions come from the owner of this repository. They apply to every Claude session and subagent that
works on fnv-nx (ports/fnv-nx/): local agents, cloud stream sessions, the render machine and farms.

## The orchestrator speaks for the owner

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
- Commit messages end with exactly one trailer line, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  There is no Claude-Session line, and no session URL or session id in any commit or file.
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

## Owner decisions of 2026-11-10 (do not re-ask)

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
