# L4 w7 001: lib/sha256 intake, stopped at the merge

Base: claude/fnv-l4-verify at 6608f34 (128 commits). Merge target: origin/claude/fnv-l6-render-w5 at 869b056a (2041 commits).

Result: `git merge 869b056a` -> "refusing to merge unrelated histories" (merge-base is empty).
Forcing it (--allow-unrelated-histories) would bring 1922 files differing from this branch
(796164 insertions), including all of loader/, lib/, render and tests. This is far beyond the
allowed scope (lib/sha256, lib/sha256_tests, loader Makefile/header edits), so per the task: stopped.

Also: this branch has no ports/fnv-nx/lib/ and no ports/fnv-nx/loader/ at all, so the baseline for
(3) (loader/src/sha256.c at the merge base) and (2) (loader as the Makefile builds it) does not exist here.

Paths the 869b056a tree has for the task (would need a cherry-pick by path, not a merge):
- ports/fnv-nx/lib/sha256/{build.json,include/fnvnx_sha256.h,sha256.c}
- ports/fnv-nx/lib/sha256_tests/{README,monte.c,nist.c,vectors/*}
- ports/fnv-nx/loader/Makefile, loader/include/fnvnx_loader.h (and the rest of loader/ is absent here)

Checks (0)-(5): not run. Nothing else changed.
Decision needed from the lead: (a) base w7 on claude/fnv-l6-render (782e8872) or the trunk
instead of claude/fnv-l4-verify, or (b) allow a path-only import of the lib/sha256 files into this branch.
