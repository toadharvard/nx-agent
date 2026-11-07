# Orchestrator tools

* `adopt.sh <origin/claude/branch>` - 3-way adoption of a stream branch into the integration branch (files only the
  stream changed are taken; files both sides changed are merged line-wise; a file that still conflicts is left to the
  stream, which merges integration and resolves).
* `farm_prompt.txt` - the farm session prompt. Replace NAME, JOB, ARGS and ENVX (extra env, e.g. ` FUZZ_MAX_S=40000`,
  or empty). RUN is flock-guarded (one runner per machine), clears the runner's head marker, and runs the job detached
  with RUNNER_KILL_S=86400, so neither the 2 h background ceiling nor FARM_DEADLINE cuts it.
