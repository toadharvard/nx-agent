# l1-contract-w19-001 — selftests per-test cache, gate-result cache, gate `warm`

Branch claude/fnv-l1-contract-w19. Measured on this machine (4 cores), same machine before and after, verdict via `l1.py verdict`, fresh VDIR:

| | cold verdict (wall) | warm no-change verdict (wall) | warm gate rerun |
|---|---|---|---|
| before, e35b870 | 203.8 s | 5.4 s (verdict 3.4 s) | n/a |
| after, 888a05b (+ the B edit) | 217.0 s | 2.0 s (verdict 0.0 s) | 0.0 s, 0 recomputed |

Here all 55 test files are green, so w14's 140 s warm (a red test reran every time) did not reproduce; the old key was one all-or-nothing key per directory set, so any red file made every warm verdict rerun everything. L1 green line at 888a05b: `L1 green 888a05b94d1e3de8bc26c8d8928bee3ce96d4729 215.042s gates:26 over_budget:False`. Cold is 13 s slower: each test now runs in a private view copy (copy + `git init`/commit, ~0.25 s per file).

## Item 1: key and proof (`gate_tests.py`)
KEY(test) = sha256(path, env id, python, timeout, VIEW HASH). VIEW = the checkout without .git, __pycache__ and the result cache dir, **minus every test file the test does not name**. The test runs in a private copy of exactly that view (own git repo, fixed identity/dates), so the view hash is the set of files the test can see.
- Completeness: a test can read only files of its view; anything else is absent and fails again, identically. So result = f(view, env, python, timeout) given the stated assumption (the test is deterministic in those; a test reading the clock, the network or an absolute path outside the view is a defect of the test). The pinned externals are named by tracked files (guest_compiler.json etc.), so they are in the view.
- Test-to-test reads: the first cut excluded all other tests and 11 tests failed (they import `test_recipe`, `test_plugins`, `test_bootmachine`). Fix by rule: the closure of a test is taken over its AST (imported module, or a string constant, equal to the basename of a test file, transitively): a superset of what it can load. A computed dynamic name is not in the view and fails, never silently passes.
- Red results are cached with the last 2000 bytes of output (the failure text is shown in `detail`); a timeout is never stored (it is load, not a function of the inputs).
- A changed other file of the view reruns every test (coarse, sound; per-test narrowing for non-test files needs a declared, enforced read set and is not done here). The old `--key-dirs` option is gone (the whole checkout is the key).
- Tests (`tests/test_gates.py`): first run runs all, unchanged run runs none and agrees; mtime is not a change; a changed test reruns only itself; a changed importer/imported pair reruns both and not the unrelated test; a changed view file reruns all; another env.json/timeout is another key; red stays red with the same text and is not rerun; a timeout is not cached; test runs without the other test files.

## Item 2: gate `warm` (`verdict.py`, gates.json `warm_budget_s`, VERDICT.md "Budget and the warm rerun")
- `--cache`: a GREEN gate result is stored under key (gate, argv, result, commit, tree, env id) and not rerun on that key. Row field `recomputed` (0 on a hit; 1 + the units the gate reports itself, selftests reports the test files run).
- gates.json `"warm_budget_s": 2.0` makes `verdict.py run` add gate `warm` after a green run: all gates again on the same cache. Two certified parts (universe 2): nothing recomputed; the rerun within B. B = 2.0 s (measured 0.0 s; VERDICT.md states the same number, a test checks they are equal and <= 10). Verdict key `warm`: {seconds, recomputed, budget_s}. A red run does not evaluate `warm`.
- Tests: green cold/second verdict with 0 recomputed; a gate that deletes the cache keys makes `warm` red (named gate); a budget of 1e-9 makes it red (wall-clock part); a red gate is never stored and warm is skipped; gates file without the key has no `warm`.
- Not done: the 22 other gates are cached whole (by tree), not fact by fact; stage (`recipe.py`) recomputation is covered through the gate result of `build`, not counted per stage in `recomputed`.
