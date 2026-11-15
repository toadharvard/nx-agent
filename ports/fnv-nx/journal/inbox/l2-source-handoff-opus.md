# l2-source handoff to the Opus lead (2026-11-11)

Branch `claude/fnv-l2-source`. Earlier notes: `l2-source-005.md`, `l2-source-006.md`. The pre-trunk L2 tree is in
history at 0d4bc1a; the tree and tools the numbers refer to are nx-agent 8f324ecde2a7 with decomp 87eb9373.

## 1. /Z7 farms and the tree hash (answered)
- `tools/farm/tree_hash.sh` is not path-independent: the prepared tree holds `build/recon_rejected.tsv`, and 14 of its
  lines begin with the absolute path of the `FNVNX_RECON` directory. My tree was prepared with recon in
  `/home/user/l2/recon/`, which gives 118e1d681f01970a; a farm with recon elsewhere gets another value
  (dacf9b115b0a710e on shard 1).
- The rule sent to all four farms, in `<tree>`:
  - A: `find . -type f ! -name '*.o' ! -path './build/recon_rejected.tsv' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16`
    must be 42a47a07ddaab6b0;
  - B: `sed 's#<FNVNX_RECON dir>/#RECON/#g' build/recon_rejected.tsv | sha256sum | cut -c1-16` must be 94094fd8e6e1633f.
  - Recon set content hash (the same find/sha256sum form inside the recon dir): 78a3fc773a4aef80, 503 dirs.
- Prepare recipe of that tree: worktree of 8f324ec, `FNVNX_RECON=<all recon/* branches> FNVNX_RECON_CERTIFY=1
  FNVNX_PATCH_CERTIFY=1 python3 -s -B tools/prepare.py <decomp> <tree>`, `FNVNX_PREPARE_SKIP` unset. The cl driver is
  `tools/equiv/r7.py cl` of 0d4bc1a.
- Shard 1 confirmed A and B and published fnv-nx-build `msvc/z7/8f324ecde2a7/1of4` @ a8ff7fd5 (749 units, 742 built,
  7 failed; it reports 738 .obj files for 742 built, not looked into). Shards 2-4 have the rule and have not reported.
  Farm sessions: 1of4 session_01KmtZKSuxC7ks3kferbyC6D, 2of4 session_01KmtZKS3a468SDWPQW1Drrm,
  3of4 session_01KmtZKU9urUtsHPMAyeK4X9, 4of4 session_01KmtZKTm9tSnKho4mw4Ehyw. They report to L2, L1 and L4.
- The farms have no VC8: unit u2713 fails there. Its /Z7 object was built locally with the new installer (see 5) and is
  not published; rebuild it on a farm with `toolchain/msvc/setup_msvc.sh`.
- The old prepare (with recon and s1b patches) is obsolete on the trunk; the new `rules/` must not write any path into
  the tree.

## 2. Queue with status
| Item | Status | Commit |
|---|---|---|
| Gates names, r7 (rows() form), helpers, tests | done; L1 wires them through the registry | d737932, 734b8d1, b4a843c |
| ABI tools (exevft, thisadj, vslots, vtables, abi, odr) | done | 8cb2449 |
| gate_argorder | withdrawn: the claim is pi's N3 verdict | 734b8d1 |
| Gate B (order, `.fnvnx.frame`), order plugin | done; L1 accepted | a80582d, cbe0177 |
| Gate F (addr, layout fact `fnvnx-layout/1`), addr plugin | done; red with objects until L1's link stage emits layout.json | bb8d8ed, cbe0177 |
| Plugins sdd, pi, dispatch, names, layout + tests | done; L1's w4 takes the sources from here | 6c5e16e, 1757cbb |
| VC8 installer, mirror | done; mirror fnv-nx-build `tools/msvc8` @ 5881374b0e69 | 6892de7 |
| Docs: ORDERING.md R9; STATIC_CHECKS.md, ASM.md deleted | done | 068f6f5 |
| s1b patches | deleted whole (decision accepted) | 0165f21 |
| `rules/` root (prepare.py, s1, s2, s8, s10; split rule) | work in progress, see 4 | this commit |
| Assumption labels in link/ and plugin comments | work in progress, see 4 | this commit |
| Plugin tests on msvcmodel through the recipe stage | not started; blocked by w4's answer (L1) | |
| 6 link test files onto real objects, negatives as byte mutations | not started | |
| vtnames (clang plugin) | not started; waits for libclang-18-dev in env.json (L1 w4 branch 168006e) | |
| VORDER as an msvcmodel compiler rule | not started | |
| names: declaration-type row for extern "C" references (L1 n2_call) | not started; after boot | |
| cl toolchain and cl tree as recipe externals, N2 lemma producer | not started; blocked by w4's answer on `generated` steps with a dynamic output set | |
| Registry records (REMOVE, owner L2) in verify/ASSUMPTIONS.json | not started; waits for the trunk head with the registry | |

## 3. Global numbers
- **names** (tree 8f324ec, all 2,978 units; gate at cbe0177, plugin at 6c5e16e; cl set of 2,950 objects, built without
  /Z7): 73,568 rows, 62,210 certified (84.6 %). Uncertified 11,358: 11,174 in 12 units whose source cl rejects
  (8 generated initializer units use `decltype`; 4 have type errors) and 184 name mismatches in 108 units.
  GCC built 2,926 of 2,978 units (52 fail under GCC).
- **cl failures**: 39 of 2,988 units; 38 are source compile errors, 1 was the missing VC8 driver (now builds).
- **r7**: 0 of 748,920 on the non-/Z7 objects; no number on /Z7 objects yet.
- **Test doubles**: 0 files with a replaced module of 10 link test files (at 4f761b8); 6 of the 10 feed gates
  hand-built objects (`tests/synth.py`, `tests/abi_fixture.py`).
- **addr, order, argorder on the whole tree**: no global number. A rebuild of the tree with all trunk plugins was
  running in my container (557 of 2,978 objects when I stopped) and is lost with it. It was a measurement run through
  the legacy `compile.sh`, not the recipe. The count of bit-field accesses through a pointer with a large or run-time
  displacement (a compile error in the addr plugin; no rule closes it) is therefore unknown; L1 and the orchestrator
  were promised it.

## 4. Work in progress committed with this note (not verified by me as finished)
- `rules/`: written by a worker, stopped mid-task. Treat as a draft until its tests, the purity gate and the
  equivalence check against the legacy rules are run. The worker's final status is in section 6 if it arrived in time.
- Edits in `link/*.py`, `link/tests/test_gate_r7.py` and comments of `toolchain/{addr,names,pi}`: the assumptions
  cleanup worker, stopped mid-task. Intended changes: r7 drops alignment from its claim; thisadj uncertifies a code
  address held under several this-offsets; labels become input facts, rules or `ASM-006`. Re-run the 11 link tests
  before relying on it.
- Workers were in-process subagents of my session, not sessions; they end with it and have no branches.

## 5. Decisions taken and reported
- VC8 binaries carry no Authenticode signature. The installer verifies Microsoft's signature on the compiler MSI and
  its cabinets and pins cl.exe, c1xx.dll and c2.dll by SHA-256.
- pi's `fpfast` option and verdict are removed (plain IEEE decision).
- A plugin built against the distribution g++-13 headers does not load into msvcmodel: one plugin build per compiler it
  loads into. `layout` includes `../names/fnvnx_names.cc`, so that file is an input of the layout stage.
- Uncertified classes that go to LIFTED: order — functions with alloca/VLA, by-value aggregate argument copies over
  16 bytes, incoming stack arguments; addr — `.cold` parts.

## 6. Open questions
- w4 (session_01KmtZKRkugvim1Bfui5aFTw): how a trunk test reaches the msvcmodel compiler; a `generated` step whose
  output set is a function of an external; how the cl side enters a recipe.
- pi treats a user-declared `__attribute__((const/pure))` like a compiler-derived flag; that flag is a claim of the
  source, not of the compiler (ASM-001 does not cover it). Not resolved.
- vslots text says the deleting-destructor slot is `??_G`; under the sdd rule it is `??_E`. Code versus text not checked.
- The 738 versus 742 object count of /Z7 shard 1.
- L1's session: session_01KmtZKUSQnbJKPPsvtTQXf7.

## 7. Final worker reports (arrived before the commit)
**`rules/`** (prepare.py, core.py, s1_compile.py, s2_bodies.py, s8_asm.py, dyninit_rewrite.py, s10_startup.py,
mscgate.py, tests/):
- Worker's results: 7 test files pass; the purity gate is green on rules/ (10 of 10 files); against the legacy tool
  with s1b, vorder, reccert, recon, memo and env removed, 5 of 5,521 output files differ, all by the one dropped
  splitting rule (STAND7, 5 sites). The token-stream post-check runs after every rule and over the whole tree; the real
  output has 148 `_MSC_VER` groups from asm, 95 passed through from the input, 0 violations. Full run 9m47s.
- Not done: no determinism run on real data with another hash seed; nothing compiled (cl or GCC) from the new output;
  the equivalence run predates a comment scrub; README columns are generic by class, not checked per rule; the scrub
  of game identifiers in comments was automatic and not reviewed. Review rules/ for decomp-derived text before it goes
  to the trunk.
- Open: (1) rules that emit `FNVNX_*` macros write the same text for both compilers but the shim expands them per
  compiler; the split gate does not see that class. (2) Rule data keyed on the exe: address ranges, `@fnv` tags,
  `fnv_<addr>`/`FUN_<addr>` spellings, CRT name lists; these must come from exe facts, not constants. (3) The asm rule
  raises an error for a construct it cannot wrap: a failure path. (4) No recipe form yet; `--win32-header` and
  `--crt-symbols` inputs exist only in the legacy tree. (5) POP2 reads `FUN_<addr>` names: not shown to be a class rule.

**Assumption labels in link/ and plugin comments:**
- Worker's results: 11 link test files pass; no "assum" line without `ASM-0` and no `A-` label left in link/ and
  toolchain/. r7 no longer compares alignment (three fixtures now certify; the alignment-only `undecided` class is
  gone). Bounds text is `Assumption ASM-006`. test_pi was not re-read after the comment edits.
- thisadj rule NOT implemented: "more than one distinct this-offset is uncertified" turns the green fixture red and
  would uncertify nearly every inherited slot of a secondary vftable. Current code: offset 0 wins; several offsets
  without 0 are uncertified. The single-entry premise is marked open in the file; it needs a rule. I told L1 this
  would be a checked rule; that statement is withdrawn.
- vslots and abi_native still apply a two-slot destructor rule (D1 then D0, minus one), while the sdd plugin gives one
  slot holding `??_E` and makes a D0 in a table a compile error. Code and plugin disagree; not fixed.
- gate_names does not check that a name belongs to this very declaration (two definitions in one unit with swapped
  names would pass). The cited sources of the read-only, stack and section-flag facts in a64order were not checked
  against what the gate reads. The reading of R9 for aggregate-copy loads has no explicit clause in ORDERING.md.
