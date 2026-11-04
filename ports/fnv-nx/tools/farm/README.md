# Farm jobs

A farm machine is a separate cloud session that runs heavy jobs and does no development (`docs/MACHINES.md`). The
development agents stay in the orchestrator session. A farm runner:

1. watches the integration branch `claude/port-nsw-un6t9s` for new commits;
2. runs its job scripts from this directory on each new commit, in the background;
3. commits the text reports they write to its own branch `claude/port-nsw-farm<N>`, under
   `ports/fnv-nx/reports/farm<N>/`, and pushes.

Development agents read the reports with
`git fetch origin claude/port-nsw-farm<N> && git show origin/claude/port-nsw-farm<N>:ports/fnv-nx/reports/farm<N>/<file>`.

| job | script | needs | report |
|---|---|---|---|
| full compile census | `census_job.sh <commit> <report.md>` | git, python3, jq, `g++-aarch64-linux-gnu` | counts, gained/lost against the previous report, failing units with their first error |
| link objects, shard k of N | `compile_job.sh <commit> <k> <N> [report.md]` | + rsync; the private repository `toadharvard/fnv-nx-build` attached with push | objects on branch `c/<commit12>/<k>of<N>` of the artifacts repository (meta.json: commit, tree hash, counts) |
| MSVC equivalence gate, shard k of N | `equiv_job.sh <commit> <k> <N> <report.md>` | root (installs Wine, VC 2008 SP1/RTM from Microsoft's ISOs, ~6 GB disk), curl; the artifacts repository with push | units identical / differ / no-build, differing functions by kind (EH-only, retarget, code) and rule, per-rule table (docs/EQUIVALENCE.md); pristine MSVC objects on branch `msvc/pristine/<decomp12>` (built once, as `msvc/pristine-part/<decomp12>/<k>of<N>` parts until all N exist) |
| differential fuzzing, shard k of N | `fuzz_job.sh <commit> <k> <N> <report.md>` | + g++/binutils-aarch64-linux-gnu, python3 venv (unicorn, capstone); the compile shards of the commit; its link outputs on branch `link/<commit12>` (no job publishes them yet); once per decomp commit Wine + MSVC 2008 (root) unless branch `msvc/pristine/<decomp12>` exists | verdicts by stream and cause, hard divergences (address, cause, stream, unit); reproducers on branch `fuzz/<commit12>/<k>of<N>` |

Consumers collect a commit's objects with `tools/farm/fetch_objs.sh <commit> <N> <obj dir> [prepared tree]`. The
result is the same layout as `tools/link/compile.sh` output (`objects.tsv` + objects). With a prepared tree, the
script also checks that the farm compiled exactly that tree (tree_hash.sh). The compile job is incremental: a commit
that does not change the prepared tree recompiles nothing, and a changed unit source recompiles only that unit.
Headers, flags or the compiler changing rebuilds the shard. The session git proxy refuses tag pushes and ref
deletions, so artifacts live on branches.

| prepared tree for a commit | `prepare_job.sh <commit> [report.md]` | the artifacts repository with push | branch `t/<commit12>` (tree, meta.json); reused when no prepare input changed |

**Running a farm machine.** `tools/farm/runner.sh <name> <job> [args]` is the whole loop in one process:
- it watches the public integration branch (read-only, no credentials);
- it runs the job at each new head;
- it publishes the report to branch `reports/<name>` of the artifacts repository;
- it exits by itself before a 2 h background limit, and is then started again.

A farm session is created with the private artifacts repository as its source, so it needs no repository attach and
no permission prompt. It clones the public nx-agent itself. Its agent only keeps the runner alive. Reports of
runners are read from the artifacts repository:
`git -C /home/user/fnv-nx-build fetch -q origin reports/<name> && git -C /home/user/fnv-nx-build show origin/reports/<name>:<file>`.

Rules for every job:

* Work trees live under `$FARM_HOME` (default `~/fnvnx-farm`), never inside the repository checkout.
* Reports hold numbers, unit names and short diagnostics only. Prepared source, objects, address lists, game files
  and Microsoft binaries are never committed (the repository is public).
* A job must be runnable by someone who knows nothing about it, so its script installs or fetches what it needs,
  or says what it needs in its header.
