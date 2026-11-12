# L3b LIFT-CERT status

Lead: session_01KmtZKSQpF5Su9oG96Kbe4A (Opus 5.5), branch claude/fnv-l3b-lift. Orchestrator: session_01KmtZKRGpFka9ShREBTYbTx (since 22:48 UTC). Took over from session_01KmtZKQYtMYtB5pgYaAbehV
on 2026-11-11 22:35 UTC. Handoff read: `journal/inbox/l3b-lift-handoff-opus.md` at fd8ef066. The predecessor's workers were in-session subagents: none survive; their WIP is in fnv-nx-build `l3b-lift/handoff` @9d250b2a (bundles l3b-r9, l3b-fpm).

## Tasks

| # | Task (goal-path item) | Worker | Branch | Status |
|---|---|---|---|---|
| 0 | Trunk CLAUDE.md into claude/fnv-l3b-lift | lead | claude/fnv-l3b-lift | done 6385fff6 (trunk 58a79b3e) |
| 1 | Census: direct toolchain calls and doubles over all of lift/ (item 2 measurement) | w1 session_01KmtZKSxmMppxYbrn72CCBH | claude/fnv-l3b-lift-w1 | done 1fb13833 (source-level: clang AST of the harness sources, no cross nm in the worker container) |
| 2 | lift/rt as a guest product root on verify/recipe.py (msvcmodel b4057704, plugins), flags = the pack flags (item 1) | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2 | first result be36b59a reviewed (11 units, cold builds equal 2d247c043666, warm run=0); root handed to L3a (orchestrator decision: L3a owns lift/rt), result sent to L3a; fnvnx_order edits dropped for L2 w4 ORDER-CONT; w2 now writes the test migration plan (tests -> recipe objects + boot.py test ELFs) |
| 2g | Gates forms + fpexact into the trunk gates.json (REQUIRED_PRODUCT; universe over admitted roots, pack read from the lifted root's recipe, inputs from the build gate's GATE_CACHE outputs), to MERGER via L1 | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2g (from trunk) | doing: 4c3b97f3 — verdict 28/29; the selftests red is a merge resolution in verify/ that took the old side (L1: trunk 8cfbcdbf passes all pure cases). Restoring verify/ from the trunk and re-running with the declared env only. Next: 4h fixtures. The ASM records and citations land in this merge, via w3 |
| 3 | R9 one form: merge L3a 160438e, delete rt/fnvl_mem.h and duplicate ordering tests; golden + gate_order | w3 session_01KmtZKSe6CHsWxfNeCn25nG | claude/fnv-l3b-lift-w3 | done 26ab4c2 (L3a 6660ae2 merged, form A and its 4 checks deleted); rt/ part reviewed and accepted by L3a (its w3 session_01KmtZKSV4DsNmobaEQ2Fzvq merges it into claude/fnv-l3a-lift-w3) |
| 4 | forms prover: per-encoding cert key (key, shape_sha, IR sha), 0x67 prefix, push/pop r16 + bound builders, farm run from the mirror | w4 session_01KmtZKSmVSGEpAznLZh6oF7 | claude/fnv-l3b-lift-w4 | doing: d7c7d612 reviewed — cert key, queue, 0x67, r16, bound accepted. Mirror shard 39/40 proved at 11.1 s/enc; the whole queue is 19 494 int shapes ≈ 60 CPU-h. 0x67: 270/295 proved, 11 FAILED (9 stack-size model defect, 2 segment prefix), 14 model gap; bound 75/75 model gap (not in Sail). Before the farm run: pin capstone/keystone by sha256 and mirror them, model hunks only as derived from the pinned ACL2 x86isa source (orchestrator decision 03:07: cite the ACL2 function and commit; record the new pin under assumption 9 in ASSUMPTIONS.json; nothing hand-written from SDM prose). Order: SS.B stack size and segment prefixes from ACL2, then a BOUND census if ACL2 does not model it (shapes, sites, global counts; unreachable ones drop out by the closure rule, the orchestrator decides the rest), then the full queue on the farms; a DSL fault for CS stores (lifter side to L3a) |
| 4b | Full shape-prover farm run over 19 481 encodings, certs.tsv | farms (to start) | fnv-nx-build l3b-lift/shapeprove | next, after w4 (cert keys must survive DSL edits) |
| 4c | gate forms red: liftpack external (shapes.tsv) not declared | - | - | blocked by the pack coordinates, L3a (liftgen writes shapes.tsv at L3a 3461ebb3; pack l3a-lift/pack/3461ebb3… being published) |
| 4d | Test migration plan (8 tests, 10 gaps) | w3 session_01KmtZKSe6CHsWxfNeCn25nG | claude/fnv-l3b-lift-w3 | done ca4bcfe4 |
| 4e | dsl_emit_test onto the recipe: generated lifted root lift/test/dslemit, FNVL_EMIT as generator argument, no direct g++ | w3 | claude/fnv-l3b-lift-w3 | blocked: lift/rt root on the trunk (L3a), which waits for L2 fnvnx_order ORDER-CONT+HARDREG, L2 fnvnx_ftoi with fnvnx_f2i, the L1 w16 headers class and L5 w12 fnvl_host.h. Last: w3t e138d31b (on L1 w14 79ac88f: gen certified by the pure gate, 0 violations; dslemit names the full REQUIRED_PRODUCT list — no test class); before that 4ce3c69e (on w2g; unit file names come from one function in gen_dslemit, the stage asserts written = declared, the test derives its expected units from the generator), 4096 units, full build waits for L1 w14. Bad shapes 7 561 at shapes 77524556: uncovered 7 136 (int 262, x87 2 145, simd 4 344, sys 385), emit errors 425 (segment 232, 16-bit registers 190, assertion 3). undef 17 = (mnemonic, flag) pairs where DSL and hand handler disagree on undefined flags. puregate 47 violations (fnvlift: L3a; proof: w2g; gen→fnvlift import: L1 rule asked). Review: revert fnvlift edits (set_emit sent to L3a), drop the duplicate rt roots, build.json regen check |
| 4f | Tests needing a link (instest_extra, x87dyn, mmxtest, sehcodes, chain, ehtest) | - | - | blocked by: L3a lift/rt root on the trunk (the two-root link is done: L1 w15 fea3d99; archive members from link.libs are recorded in objects.json link_archives: L1 w15 5d6e42ea; linkstrict/objstub reading them is pending, L1). All symbol gaps have real bodies: G1/G2/G3 in platform root (L5 claude/fnv-l5-platform-w10 f872bcd2, merge pending), G5 = newlib libc.a (tools/fnvnx-a64-msvcmodel 00e3d42a), G4 dispgen.py (L3a w4, doing), G7 exists (fnvnx_lift_attach + fnvnx_guest_invoke, rt/enter), G6 done (boot.py --watch-log, claude/fnv-l4-verify eeed354, L4 w4 a5868b88; reaches the trunk with the next L4 merge), G8 closed |
| 4g | EH oracle independent of rt/seh (Wine 9.0 trace or cited ReactOS rules) for ehtest (G9) | - | - | open, L3b (with item 5) |
| 4h | test_fpexact onto recipe fixtures | w2g | claude/fnv-l3b-lift-w2g | queued after w2g's rebase: fixtures are on L1 w16 5b0dd218; the f2i cases wait for L2 w1 fnvnx_ftoi |
| 5 | Gate eh (class D). Split agreed with L3a: coverage with enumeration by dataflow rules (a)-(d), unresolved site = red: L3a w2 session_01KmtZKSUoqsFSfuD18vvg8X; gates.json line: L3a w6 session_01KmtZKR34WmRCfdDoE7ajAt (order/eh/addr32). L3b part: runtime dispatch conformance (rt/seh, rt/msvcrt_eh) against Wine 9.0 traces + cited ReactOS, replacing bench/ehtest.py | L3a / lead | claude/fnv-l3a-lift-gates | L3a doing; L3b part queued after the forms farm run (first-frame order) |
| 6 | Open assumption records (12 of 41 open; A-FPMASK 55/830 unproven; fpm bundle WIP: libvorbis 15 -> 12) | - | - | open |
| 7 | A-CTX: seh.c restores only integer regs + eip from CONTEXT after ContinueExecution; Windows also restores FP/SSE | - | - | open, reported to the orchestrator |
| 8 | Split lock: A4 deleted, LKA world-stop decided (L3a w3 batch, host side fnvl_host_world_stop/resume by L5) | L3a | claude/fnv-l3a-lift-w3 | doing (L3a, L5) |

| 4i | Assumption records: the 28 unmapped of the 41 L3b ids plus I-FPMASK and I-FPSTICKY become aliases or REMOVE records in verify/ASSUMPTIONS.json (L1 w10 format); 13 are already aliased | w3 session_01KmtZKSe6CHsWxfNeCn25nG | claude/fnv-l3b-lift-w3t | doing: w3a e22798f8 adds 29 entries; gate red with 50 errors (not tracked on w10; block rule; files cite A-XXX). Decided: renumber the 19 L3a-owned entries to 2xx and the 1 L2-owned entry to 8xx, keeping the old id as an alias; rebuild on w2g; script the L3b-file citation rewrite from the alias map; L3a rewrites the fnvlift/rt citations; everything lands in one merge with w2g (the trunk stays green) |

## Reported gaps (owner layer)

- Contract gates n1 n2_* xc xunit rtti pcrel bodysel abs disp reach reachlink gpa are red `inputs` for lift/rt: the root is a non-game product root. L1 to define their universe for such roots.
- fnvl.h:99 and fnvl_x87.h:77 carry the attribute `fnvnx_f2i`, which no plugin of the compiler knows (the plugin is `fnvnx_ftoi`). The compiler ignores it with a warning, so float-to-int sites in the rt headers may not be under the ftoi rule (class C). L3a owns these headers.
- Answered:
  - L3a takes set_emit and fnvlift puregate to 0 (its w3).
  - TAB_BIAS becomes a rule: the bias comes from the dispatch's own guard bound, and an unguarded site is red.
  - The undef-flag liveness gate is L3b's rule, built by L3a w5.
  - DSL coverage order: int, then sys, x87, simd. Split agreed: L3b w4 owns every DSL builder in lift/proof; L3a w7 owns fnvlift (f_dsl/core) and the CS-store raise through FNVL_RAISE_AV (Wine 9.0 trap 13). w7 rebases its forms.py hunks onto w4 d7c7d612.
  - f2i_check is folded into L2's fnvnx_ftoi (mode native|lifted from cxx_origin, output <unit>.f2i.json, registers attribute fnvnx_f2i) on claude/fnv-l2-source-w1, commit pending. Until then, units that include fnvl.h are red under w14's -Werror=attributes.
  - The ftoi stage-doc reader is on L1 w17 cdc0041 (MERGER #4).
  - L1 w14 adds admit.json `imports:` and z3 in EXTERNAL (ASM-009).
- L3a (fnvlift @8ec81459):
  - FNVL_EMIT is an undeclared environment input; set_emit is proposed.
  - puregate dynamic globals, __init__.py:21-22.
  - TAB_BIAS has an assumption with no ASSUMPTIONS.json id.
  - DSL does not cover 7 136 shapes; 425 emit errors.
  - undef-flag disagreement in 17 (mnemonic, flag) pairs; a flag-liveness rule is proposed.
- L1:
  - gate_pure cross-root imports (`imports:` in admit.json proposed);
  - z3 on the EXTERNAL list;
  - EMLINK in the unit view (w14, doing).
- Orchestrator: under L1 w14 a product root can't be admitted while any REQUIRED_PRODUCT gate is missing from gates.json. Missing today: d3d9total, r7, namegate, abi, datamodel (L1 w17), order/eh/addr32 (L3a w6), exepin. forms and fpexact are ours, via w2g. This blocks every product root, dslemit included (w3t a1a996b1).
  - Owners per the orchestrator: d3d9total L6 w4; r7, namegate and abi L2 w5; datamodel L1 w17; exepin L1; order, eh and addr32 L3a w6. Merge order: L1 w16, L2 w4, L1 w14, w17, L2 w5, L1 w10, L6 w4, L3a w6, L3b w2g, exepin.
  - Cycle: the lift/rt admission needs forms and fpexact (w2g), and w2g needed rt/fnvl.h, because fnvlift read it at import. Broken by L3a 82b45c2c (PRELUDE gone; prelude(rt_dir); import reads no rt file; puregate 0). w2g takes it byte for byte.

## Global numbers (as handed over; re-measured by the tasks above)

| quantity | value | commit |
|---|---|---|
| lift test/bench programs with doubles | 9 of 35; 19 distinct symbols (6 collide with rt units, 13 boundary), 67 (file, symbol) pairs | 6385fff6 (w1) |
| direct toolchain sites in lift/ | 46 (45 subprocess, 1 recipe naming stock g++-13 in liftgen.py:181); 0 use msvcmodel | 6385fff6 (w1) |
| symbols lift/rt declares that no unit defines (ELF gap) | 16: ExitProcess, UnhandledExceptionFilter, fnvl_call, fnvl_disp_table, fnvl_g, fnvl_phf_params, fnvl_host_ticks_ns, fnvl_host_tls_slot, fnvl_trace, fnvl_world_stop, fnvl_world_resume, fnvnx_lift_cpu_bytes, fnvnx_lift_raise_backend, fnvnx_win32_lookup_export, fnvnx_win32_set_raise_backend, cxx_frame_handler | 6385fff6 (w1) |
| forms + fpexact gate selftests | 70/70 | eb6f0b2b |
| encodings certified by the shape prover (gate forms) | 0 / 26 500 (DSL builds 19 481) | eb6f0b2b |
| golden translation | 101 590 / 101 590, 0 errors (universe of L3a's rule) | 26ab4c2 (w3) |
| gate_order on lifted objects | not measured on the whole universe (only a 400-entry sample: no result). Known gap: x87 stack-slot stores via W-copy of x27 classed guest; rule agreed (W-copy of x27 + non-wrapping bounded W-add stays CPU, ASM-007 + ASM-201), L3a w5 implements; universe = lifted roots only | 26ab4c2 |
| dsl_emit_test on shapes.tsv | 11 511 encodings, 11 438 emitted, 8 738 compile | c265b22d |
| memclass plain accesses | analysis only; emission deleted (every access ordered) | 26ab4c2 |
| assumption records open | 12 of 41 | brief 2026-11-11 |
| A-FPMASK sites unproven | 55 / 830 | brief 2026-11-11 |
