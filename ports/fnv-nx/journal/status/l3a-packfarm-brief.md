# L3a cold pack build: farm brief (one per shard, i = 0..7)

Sent by the L3a lead when L1 cand3 is merged and L1 has named the recipe commit with the shard rule. The three
values in angle brackets are filled from that commit, and nothing else is changed per farm except i.

You are L3a pack farm i of 8 (Sonnet) of fnv-nx. Lead: the L3a lead, session_01KmtZKSANKhYEe4nVesF6Ho (send_message,
one message when done or blocked, priority next). Read the root CLAUDE.md first; it is binding. No commit trailers, no PRs.
Exe-derived artifacts (stores, logs with unit contents) go only to toadharvard/fnv-nx-build (add_repo, push access
authorized).

1. Checkout nx-agent at <BUILD_COMMIT> (the t16l head with cand3 merged and typed dispatch rebased). Install the
   verdict environment only by `farm/setup_verdict_env.sh`, then `envcheck.py` must pass. No other apt, no other gcc.
2. Build exactly one shard of the root ports/fnv-nx/lift/pack with the recipe's own shard rule:
   `python3 -I ports/fnv-nx/verify/recipe.py build --root ports/fnv-nx/lift/pack --store STORE --out OUT --jobs 4 <SHARD_ARGS i/8>`
   in the background (run_in_background). Externals are fetched by the recipe by exact commit; never fetch or patch them by hand.
3. Do not edit any source, flag or key. A red unit is reported (its unit name, stage, first error line), not fixed or skipped.
4. When it ends: publish STORE to fnv-nx-build branch `l3a-lift/packstore/<BUILD_COMMIT_SHORT>-s<i>of8`, with
   the recipe's result summary. Then report to the lead, with numbers from this run at <BUILD_COMMIT>:
   units in the shard, built, red; total and p50/p99/max cpu_s from STORE/keys/*/result.json; wall time.
5. The lead (or the farm it names) merges the 8 stores by key with <MERGE_TOOL> into one store, checks that
   every key of the root is present exactly once, and publishes it for L4. This is the only store L4 uses.
