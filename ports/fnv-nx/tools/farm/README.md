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

Consumers collect a commit's objects with `tools/farm/fetch_objs.sh <commit> <N> <obj dir> [prepared tree]`. The
result is the same layout as `tools/link/compile.sh` output (`objects.tsv` + objects). With a prepared tree, the
script also checks that the farm compiled exactly that tree (tree_hash.sh). The compile job is incremental: a commit
that does not change the prepared tree recompiles nothing, and a changed unit source recompiles only that unit.
Headers, flags or the compiler changing rebuilds the shard. The session git proxy refuses tag pushes and ref
deletions, so artifacts live on branches.

Rules for every job:

* Work trees live under `$FARM_HOME` (default `~/fnvnx-farm`), never inside the repository checkout.
* Reports hold numbers, unit names and short diagnostics only. Prepared source, objects, address lists, game files
  and Microsoft binaries are never committed (the repository is public).
* A job must be runnable by someone who knows nothing about it, so its script installs or fetches what it needs,
  or says what it needs in its header.
