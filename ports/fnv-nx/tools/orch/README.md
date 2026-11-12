# Orchestrator tools

* `adopt.sh <origin/claude/branch>` - 3-way adoption of a stream branch into the integration branch (files only the
  stream changed are taken; files both sides changed are merged line-wise; a file that still conflicts is left to the
  stream, which merges integration and resolves).
* `farm_prompt.txt` - the farm session prompt. Replace NAME, JOB, ARGS and ENVX (extra env,
  or empty). RUN is flock-guarded (one runner per machine), clears the runner's head marker, and runs the job detached
  with RUNNER_KILL_S=86400, so neither the 2 h background ceiling nor FARM_DEADLINE cuts it.

## Owner rules for the orchestrator (standing; collected 2026-11-08)

These come from the owner and bind every orchestrator session, including one that resumes after a context reset.

1. **Language.** The orchestrator writes to the owner in Russian.
2. **Every message carries the full status.** Each reply to the owner ends with:
   - a detailed status of the whole task: the blockers toward zero, the newest farm numbers, and what lands next with
     an ETA;
   - a status line for every agent: each stream session, and each farm with its job and head.
   It comes after the answer itself, never instead of it.
3. **The owner's time is finite: no waiting.** Never hold work for a verdict when a merge or a parallel path exists.
   Merge first and verify after (CLAUDE.md: farms verify after the merge, and a commit a report shows broke something
   is reverted). Start parallel verifications on idle farms instead of queueing them. Give the ETA, and whenever it
   exceeds about an hour, say what would shorten it.
4. **Honest zero.** Blockers are driven to zero honestly: no stubs, zero-fill or silenced checks without evidence
   (D23).
5. **Row accounting.** Every stream batch accounts for every row it took, with exactly one status per row (fixed /
   already closed at head / not a defect (class) / duplicate / held (reason) / handed to <stream> / rejected), and
   the counts add up. Every merge with no local check names the farm check that verifies it and the expected
   number.
6. **Traps top-down.** Traps are worked by boot hits first, then by call depth from the entry (R2's topdown.tsv).
   Streams use loops of Sonnet workers, and the stream's Opus session reviews each result before it merges.
7. **Budget.** The orchestrator stays lean:
   - no full session listings in context (save them to a file and filter);
   - read only the summary and stage tables of reports;
   - one check-in per hour;
   - streams batch their messages hourly.
   At most 50 sessions; light jobs may share a container.
8. **Containers (D17).** Jobs longer than about 10 minutes run on farms, never in stream containers.
9. **Autoadopt** runs detached (`setsid nohup` loop), never as a harness background task. A container restart kills
   it, so each check-in verifies it is running.
10. **wip branches.** Try farms read `wip/*` from toadharvard/fnv-nx-build. A stream that pushes a wip branch pushes
    it to both remotes, fast-forward only.
11. **Ratchet: what works stays working.** Every working item is recorded per metric with the head that first
    reached it: identical functions (eqa), passing fuzz functions, boot milestones and skips, passing shader
    programs, trap and undefined counts, D25 counts. Every farm report checks the newest head against that ledger.
    A regression makes the report's first line `- REGRESSION`. The commit that caused it is fixed forward before the next merge of the same
    area. T1 owns tools/farm/ratchet.py.
12. **Fix forward, no rollbacks.** A regression is fixed forward by its owner, ahead of other work. Reverting needs the
    owner's explicit approval (owner, 2026-11-08: "Не надо ниче роллбекать"; "Какой откат? Ты же фикс написал").
13. **Layout changes are checked whole, against the exe, before merge.** A change to the ABI, the vtables or the
    compile flags gets the full static check first: the all-tables gate (every RTTI vftable, native ==
    transform(exe)), not only call-site samples and boots. Compile-flag changes go in together, so that one full
    recompile covers them, and each gets a sample compile of about 50 units first. Workers run on Sonnet; Opus only
    reviews. Owner, 2026-11-08: "Хули мы не можем взять и запихнуть правильную таблицу"; "сонеты, не опусы".
