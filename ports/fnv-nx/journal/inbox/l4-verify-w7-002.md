# L4 w7 002: warm ensure cost, test_bootmachine.py (task B)

Scratch worktrees, nothing committed to w7. GATE_CACHE fresh per tree, cold run then warm run (second run, same GATE_CACHE), sequential, wall time of `python3 -I verify/tests/test_bootmachine.py`.
- A: claude/fnv-l4-verify-w6 f36ee283 as is: cold 90.7 s, warm 62.3 s; 145 cases ok, 0 FAIL both runs.
- B: f36ee283 + 09b509e4 (claude/fnv-l1-contract-w14, cherry-picked --no-commit; recipe.py and test_recipe.py merged cleanly): cold 37.2 s, warm 6.2 s; 145 cases ok, 0 FAIL both runs.
Warm: 62.3 s -> 6.2 s (10x). Count of cases = lines starting `ok`.
