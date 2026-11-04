# Working across machines

fnv-nx work runs in more than one Claude Code cloud session, each one in its own container. The containers share
nothing but this git repository. This file is the protocol between them.

## Roles

| machine | branch it writes | owns | does not touch |
|---|---|---|---|
| orchestrator (the original session) | `claude/port-nsw-un6t9s` (integration branch) | `JOURNAL.md`, `docs/ARCHITECTURE.md`, `tools/rules/`, `tools/link/`, `tools/equiv/equiv.py` classes, everything not listed for another machine; merges every other branch after verification (D14) | - |
| farm runners | `claude/port-nsw-farm<N>` | `ports/fnv-nx/reports/farm<N>/` (text reports of the jobs in `tools/farm/`) | everything else: runners run jobs, they do not develop |
| render | `claude/port-nsw-render` | `render/native/`, `docs/RENDER_NATIVE.md`, `docs/RENDER_VERIFY.md`, render tests (`tests/s6n/`, `tests/v6n/`) | the integration branch, `JOURNAL.md`, rules, linker |

When a machine needs a change in files it does not own (e.g. an `--override` feature in the linker, a prepare rule),
it describes it, with a failing test where possible, in its inbox note (below). The owner makes the change.

## Flow

1. Each machine reads the integration branch. It rebases its own branch onto the integration branch regularly, and
   always before reporting.
2. It commits only to its own branch and pushes there after every step.
3. The orchestrator reads that branch and the session's transcript, verifies the result (re-runs the tests; for D14
   the work goes to an independent verifier) and cherry-picks it into the integration branch.

## Journal

`JOURNAL.md` has one writer, the orchestrator, so entry ids stay unique. Other machines write **draft entries** as
files `journal/inbox/<machine>-<nnn>.md`, numbered from 001 per machine, in the entry format of `JOURNAL.md` with
`J-XXXX` in place of the id. When the orchestrator merges one, it appends the entry with the next id and deletes the
inbox file in the same commit.

## What never goes into git

The rules of `docs/ARCHITECTURE.md` (Legal) hold on every machine. Prepared trees, objects, address lists and
tables derived from the decomp, game files, the game's shader blobs, and Microsoft binaries stay in the container
that produced them. Each machine rebuilds what it needs locally from the documented scripts (`tools/fetch.sh`,
`toolchain/`, `render/nine/build.sh`, `tools/equiv/README.md`). Synthetic test content we make ourselves (test
scenes, golden images of them, fuzz reproducers) is fine to commit.

## Commit messages

Commit messages end with exactly one trailer line, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
There is no `Claude-Session` line, and no session URL or session id appears anywhere in commits or files.

## Ten-minute rule (D17)

Any job that can take more than about 10 minutes runs on a farm machine (`tools/farm/<job>_job.sh`), never in a
development session. That covers full prepare, compile, census, link, probe, MSVC gate, fuzz campaigns, the static
gate and full boots. Development agents never wait for a build:
- while a farm job runs, they keep working on the next item of their queue;
- they read the result when its report lands, and check their own change with a small local test (one unit,
  minimal tree, a single relink);
- a job that does not exist yet as a farm job is written first, then started on a farm.

## Load

A session's container has 4 CPUs, 15 GB RAM and a fixed disk allowance. Keep at most about 5 heavy jobs per
container, one per agent, and run long commands in the background. Containers have restarted under higher load.
