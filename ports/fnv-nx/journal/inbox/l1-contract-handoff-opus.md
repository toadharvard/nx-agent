# L1 CONTRACT: handoff to the Opus lead (2026-11-11, 22:40Z)

From the outgoing L1 lead (session_01KmtZKUSQnbJKPPsvtTQXf7) to session_01KmtZKUn5UJpiHYPtNRT6p9.
Numbers of other streams below are from their leads' reports; I did not re-measure them.

## Heads

| Branch | Head | State |
|---|---|---|
| trunk `claude/fnv-nx-clean` | a3e9f0a9 (CLAUDE.md since moved to 58a79b3e0) | 22 gates green of 32 REQUIRED_PRODUCT; admitted product roots: 0, so every product gate is "0 of 0, no roots" — not a result |
| `claude/fnv-l1-contract` (lead) | this commit | notes and CLAUDE.md only; unrelated history to the trunk, never merge it, copy files with `git checkout FETCH_HEAD -- <file>` |
| `claude/fnv-l1-contract-w8` | a6753733 | exepin gate (23rd gate); sent to MERGER, first in its queue, no result yet |
| `claude/fnv-l1-contract-w5` | db15c7ac | base a3e9f0a9; w5 reports green verdict on 22 gates, tree 43aff7d5f985, 163 s; sent to MERGER, second |
| `claude/fnv-l1-contract-w4` | 168006e (old base) | must rebase onto a3e9f0a9 and send its head to the lead and MERGER; not done yet |
| `claude/fnv-l1-contract-w11` | 9a094937 (stage 1) | stage 2 running; not at MERGER |
| `claude/fnv-l1-contract-w10`, `-w12` | running, no head reported | |
| `claude/fnv-l1-contract-w9` | merged (trunk 4bf2148f) | done |

My container fails envcheck (MISMATCH), so `l1.py verdict` is "blocked: env" here. Certified verdicts come from MERGER and the farms.

## Workers

Cloud sessions (Sonnet). Tell them where to report; I told them to report to the new lead.

- **w4**, session_01KmtZKRkugvim1Bfui5aFTw, recipe and plugins. Queue, in order:
  - rebase onto a3e9f0a9, head to lead and MERGER;
  - B2: all seven L2 plugin sources from `claude/fnv-l2-source` 1757cbb (sdd, pi, dispatch, names, layout, order, addr) as recipe stages `plugin:<name>`, plus `-fno-reorder-blocks-and-partition`; answer L2 how trunk tests get the msvcmodel compiler;
  - second guest external: `tools/fnvnx-a64-msvcmodel` @ 00e3d42acd10593d534b9a70bb8940cee7991436, tree_sha256 3f25152cbc5a729d9e0a12fc5c62b6b912b6ab1571b6b3494de8f35e3904207d (L5), into `verify/guest_compiler.json` and a sample `build.json` for the platform root (L5 waits for it);
  - G: pure module of the recipe (EXT_KEYS, ASM_EXT, load_admit, load_recipe, quantify, RecipeError, BuildRed); gate `build` fetches externals and builds roots once and leaves a manifest `fnvnx-built/1` in VDIR; consumer gates read only the manifest and declare `"needs":["build"]`. Head to lead and L3b;
  - C: argordergate; F: ninth plugin `fnvnx_f2i_check` and second CC output `obj/<unit>.o.f2i.json` (L3b); E: facts stage / vtnames (needs libclang-18-dev).
  - Dropped: D (fpfast; the plugins have no options).
- **w5**, session_01KmtZKRw5sTxarSdSFC6Njj, env and purity. Done in db15c7ac: gate temp dirs inside VDIR (linkstrict/objstub left ~936 MB each in /tmp), puregate "import roots". Now: `setup_verdict_env.sh` from scratch. Its finding: the script uses no SHA-256, and env.json pins versions no longer in the apt archive (it passes only because the image already has them). My decision: archive packages from snapshot.ubuntu.com at a fixed date with sha256 for all 130; non-archive debs mirrored to fnv-nx-build `tools/verdict-debs`; a pin that exists in neither is raised and gives a new env_id (to L4 and the lead). I allowed it one helper session "fnv-nx L1 w13 setup-from-scratch"; w5 archives it.

Local agents in my container (they die with this session; their work is on their pushed branches, worktrees /home/user/l1work/wNN):

- **w10** (agent acaa81c7a3e2da3b1): `verify/ASSUMPTIONS.json` (schema fnvnx-assumptions/1) and gate `assumptions`. ASM-001..009 are the nine OWNER records in CLAUDE.md order; REMOVE from ASM-010. Sources to fold: L4 (4f1f1e3), L2 (f2ef201), L3b `lift/ASSUMPTIONS.l3b.json` @ ce152aa2 (41 records) and the fpexact ids, L5 (36 records, 21e472e9), L6 `render/ASSUMPTIONS.md` @ 66e2f971 (129 lines, 87 REMOVE without a gate), L3a (asked for A1, A2, A4 of ORDERING.md; A3 becomes a build fact). "No GCC pass folds through an asm operand" is an alias of ASM-001. No counts reported yet.
- **w11** (agent a6348ae2aef27699f): no doubles in tests, inside gate objstub. Stage 1 at 9a094937: `build.json` key `tests`; reds test-double, product-duplicate, test-omits-unit, test-not-linked. Stage 2 running: rebase onto w5 db15c7ac; COMDAT rule (shared only if group contents and relocations are byte-equal); the AST rule for python tests in the same gate; take the six L1 names to 0.
- **w12** (agent a6309a9ebda58aa2b): `verify/tests/guest/hostapi.h` holds only services with a call site in the product roots (platform @ 2951f7ae), checked by rule; at least 27, 28, 29, 32, 33 go; adds `FNVNX_EXT_DK` (<64) for L4's deko3d model. L4 waits for the commit and header hash and then deletes its bootsock model.

## Global numbers

- Doubles in tests, trunk c9765781, whole ports/fnv-nx, AST criterion: 47 python test files, 8 with doubles, 9 replaced names, all in verify/tests (contract/tests 12/0, link/tests 2/0).
  - L1: gate_build.ensure, l1._check, runtime.DEFAULT_DIRS, runtime.conf_dirs, sandbox.launch, verdict._git.
  - L4: bootmachine._plat_section_free, test_bootmachine.GUEST (set from two tests). L4 was told.
  - C/C++ test sources on the trunk: 0. Tests of L5 and L6 on their branches are not measured.
- Assumptions: no total yet (w10). The orchestrator asked for total / OWNER / REMOVE by owner, with the commit.

## MERGER queue (session_01KmtZKSxPHjVrmy1iFKiW3g)

w8 a6753733, then w5 db15c7ac, then w4 (after rebase), then L5 `claude/fnv-l5-platform-w4` f15935a9 (gates imports and machine; machine stays "measurement" until 4 Reg* dynamic-key sites are resolved). No result received for any of them. Report each result to the orchestrator: trunk commit and gate count.

## Queue of the stream, with status

1. Goal step 1, platform as the first product root: blocked on w4 (plugins, both externals, sample build.json). Then the lead adds the platform entry to `verify/admit.json`.
2. Goal step 2, lifted packs as product roots: L3a has not published a pack built by the msvcmodel compiler through the recipe. L3b gate forms: 0 of 26,500 encodings certified, red.
3. Goal step 3, ELF: the link stage in the recipe is not written. It must emit units.json, addrmap.tsv, aliases.ld, entries.tsv and layout.json (fnvnx-layout/1; L2 gate addr needs it). `relink.sh` must be reconciled with the recipe-only rule. Publish as fnv-nx-build `l1-contract/elf/<commit12>`, id to L4.
4. Wiring in contract/registry, not done: names, r7, addr, order (one gate over L2 cbe0177 and L3a 160438e; L3a was asked for `rows()`), fpexact, forms, eh, d3d9total. Unwritten L1 gates: datamodel, argordergate.
5. Sanitizer builds as a flags variant of the product root: not started.
6. Deferred until the first frame: reach certificates, A1, allocator freshness. Not to do: 39-bit/LP64.

Two scheduled reminders bound to my session will fire into it at 22:42Z and 22:45Z (worker check, pack check); they do not reach the new lead.

## Open questions

- L6 item 80: gate_l6 does not run tests that need uam, the translator or Wine. L6 owes the list of externals to pin.
- No gate or fact covers the completeness of the RTTI scan in exefacts (vtables V5 incomplete).
- Registry field `pending` ("branch@commit" for records of unmerged branches) is my addition and a softening; I offered the owner to remove it. Undecided.
- Z7 farm shard 1/4 of tree 8f324ecde2a7 first failed its tree check (dacf9b115b0a710e, expected 118e1d681f01970a), then reported 749 units, 742 built, 7 failed. This is L2's job; it only messaged me.

## The owner's message

After my layer-by-layer status (sent verbatim to the orchestrator at 22:30Z) the owner wrote, verbatim and cut off:

> Опять какие-то хаки. Я больше не мог

I answered with what in my report could be meant (test doubles in render tests; translation with 0 of 26,500 certified; "measurement" mode of the machine gate; my `pending` field; the five host services removed by rule, not by list) and asked for one line of clarification. No reply. The orchestrator has the full exchange.
