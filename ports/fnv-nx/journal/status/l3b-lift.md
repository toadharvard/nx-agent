# L3b LIFT-CERT status

Lead: session_01KmtZKSQpF5Su9oG96Kbe4A (Opus 5.5), branch claude/fnv-l3b-lift. Orchestrator: session_01KmtZKRGpFka9ShREBTYbTx (since 22:48 UTC). Took over from session_01KmtZKQYtMYtB5pgYaAbehV
on 2026-11-11 22:35 UTC. Handoff read: `journal/inbox/l3b-lift-handoff-opus.md` at fd8ef066. The predecessor's workers were in-session subagents: none survive; their WIP is in fnv-nx-build `l3b-lift/handoff` @9d250b2a (bundles l3b-r9, l3b-fpm).

## Tasks

## Path plan 2026-11-14 (binding; integration 21b10f1aa)

Workers: 3 at most (w4, w3, w9), plus the cert farms 05/07.

| # | Item | Worker | Branch | Status |
|---|---|---|---|---|
| 2.6 | Certify the pack's shape set. Universe = pack l3a-lift/pack/7b2faae276b2 @ 7ef692b5 until L3a's relift publishes its list. Steps: pinned queue artifact; int keys on farms; x87/SIMD/sys builders; cpuid; DEF0/DEFPRIOR | w4 session_01KmtZKSmVSGEpAznLZh6oF7; farms 05/07 | claude/fnv-l3b-lift-w4 | doing: queue artifact (E) first; farms round 2b on 4a9eabc9 |
| 2.8 | Lift test doubles 9/35 -> 0, with L3a w6; Unicorn harnesses deleted once replaced | w3 session_01KmtZKSe6CHsWxfNeCn25nG | claude/fnv-l3b-lift-w3b | doing: static census first |
| 4.5 | SEH continue restores the whole CONTEXT (A-CTX = ASM-220), then EH/SEH conformance against the exe's tables and Wine 9.0 | w9 session_01KmtZKSzH8X2mn5RPxcpuLZ | claude/fnv-l3b-lift-w9 | doing: field table vs Wine 9.0 |

| # | Task (goal-path item) | Worker | Branch | Status |
|---|---|---|---|---|
| 0 | Trunk CLAUDE.md into claude/fnv-l3b-lift | lead | claude/fnv-l3b-lift | done 6385fff6 (trunk 58a79b3e) |
| 1 | Census: direct toolchain calls and doubles over all of lift/ (item 2 measurement) | w1 session_01KmtZKSxmMppxYbrn72CCBH | claude/fnv-l3b-lift-w1 | done 1fb13833 (source-level: clang AST of the harness sources, no cross nm in the worker container) |
| 2 | lift/rt as a guest product root on verify/recipe.py (msvcmodel b4057704, plugins), flags = the pack flags (item 1) | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2 | first result be36b59a reviewed (11 units, cold builds equal 2d247c043666, warm run=0); root handed to L3a (orchestrator decision: L3a owns lift/rt), result sent to L3a; fnvnx_order edits dropped for L2 w4 ORDER-CONT; w2 now writes the test migration plan (tests -> recipe objects + boot.py test ELFs) |
| 2g | Gates forms + fpexact into the trunk gates.json (REQUIRED_PRODUCT; universe over admitted roots, pack read from the lifted root's recipe, inputs from the build gate's GATE_CACHE outputs), to MERGER via L1 | w2 session_01KmtZKUEwDammyW56Dx37m8 | claude/fnv-l3b-lift-w2g (from trunk) | doing: w2g 84e26812: verify/ byte-equal to trunk except its own edits; verdict 29/30 (309 s). Red: pure (lift/proof/formgate.py, gate_fpexact.py import verify/; old directory-argv rule). Not accepted as red; it clears with L1 w14's closure rule. rcpcert per image by ELF symtab references. Merge order (L1): w16 → w10 → w14 → L3a lift-gates → our unit (w3a). Next: fpexact f2i (conv/asm/missing red, builtin green), 4h fixtures on w16 de8ed638; next: forms/fpexact write findings, and lift/proof/fixtures are admitted as fixture-class roots per L1 w16 2a22cf37; w3 warm verdict on w3b 9d2c2cc9 (tree 9abaa1d9): 38/38 green, 569.0 s, over_budget True, same as the base 862b0708 cold (567.5 s); selftests 512.5 s is not reused warm: L1 cache defect, not this change; successor w3b 867456118: stale oracle entries gone, lift/proof back in selftests, test_formgate_real holds only its referenced case; gate_oracle 92/92, assumptions 119/119, lift selftests 4/4 |
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

| 4i | Assumption records: the 28 unmapped of the 41 L3b ids plus I-FPMASK and I-FPSTICKY become aliases or REMOVE records in verify/ASSUMPTIONS.json (L1 w10 format); 13 are already aliased | w3 session_01KmtZKSe6CHsWxfNeCn25nG | claude/fnv-l3b-lift-w3t | done: the unit is contained in claude/fnv-first-root-next 862b0708; L1 verdict green 38/38 (built tree f6d8b50f, cold 567.5 s, env set up by farm/setup_verdict_env.sh), assumptions 119/119, 79 entries (9 OWNER / 70 REMOVE), 24 retired; w2g v2 9d2c2cc9 (findings in formgate/fpexact) goes on top as w3b, warm verdict pending; int3 ids placed by L3a w6 2b87b2b8f (gate_fpexact → ASM-320/321/801, README sdm → ASM-331, README:100 → ASM-006 by L3a): 0 findings left in gate_fpexact/README; gate on that head 83 entries, 24 retired, 131/148 mentions certified, 17 findings outside L3b files |
| 3f | Shape certificates: the full int queue, 19 494 shapes (census l3b-lift/shapes 77524556, 7 images, 26 500 keys: int 19 817 / x87 2 175 / simd 4 369 / sys 139) on IR 70c8dc39 (w4 f46bf37d; targeted 373: 296 proved / 1 dsl / 76 model-gap / 0 timeout) | farms 01-16 (orchestrator 03:46), 3 shards each of 48: session_01KmtZKSnvm9jrrTYaoptCJW, ..SefBeDTfGWugKwboj, ..SegBBQ8CvZMubkzSw, ..V2SuYrJqpG7qJyPdw, ..UBY5ZEEEGao5bC2UJ, ..VHAgiqQieeiENKXvQ, ..R7AKACexBq9odtH7c, ..SrJuinKTE4BAjcDcK, ..U9ey85VJzq7pnYnAd, ..SeBec48FmqpwVciZQ, ..S5baoGoYcGwQpmLWy, ..VG4DH1gd66hzmPPfk, ..V8hCV5PMeMNRYmZ3r, ..S5ugow1y3T2otBX2w, ..Qfis7dLbMYPoj7apH, ..TZF2T3GmwY4rxDuy2 | fnv-nx-build l3b-lift/shapeprove/<IR12>-<queue12>/Kof48, folded by certs_build.py into l3b-lift/certs | round 2 (f46bf37d) published nothing: farms 05/07 and w4 hit the weekly limit (reset 11-14 18:00 UTC); round 2b restarted 11-14 23:20 on w4 4a9eabc9, 05 K=1-3, 07 K=4-6; published so far 6/48 shards, 2 409 records |
| 3g | Shape certificates, stage 2 and sys: forms_sys taken byte for byte from L3a e9124b30 (w4 75b6e9ab; global shape_sha over 19 817 int keys: 19 494 same / 166 error in both / 157 new Form (arpl) / 0 changed). Model census (w4 8c049c68): pushaw/popaw proved (60/61/6660/6661); push sreg is supported by the model but needs selector variables in XIN (L3a dsl.py; hunk isolated on w4, sent to L3a); model gaps: all 27 forms_sys encodings, pop/mov sreg, les/lds, lcall/ljmp/retf, enter, daa/das/aaa/aas/aam/aad; salc is #UD in the model | w4 session_01KmtZKSmVSGEpAznLZh6oF7 | claude/fnv-l3b-lift-w4 | paused: w4 pushes its state and records its queue (F2 segment-override class, G1-G4 farm findings, D ACL2 table, B selector output, E pinned queue, census on w7's facts via lift/tools/lifted.py) in its inbox; resume at step 2 |
| 3h | Undefined-flag values (orchestrator decision 08:48): every live read of an architecturally undefined flag takes the reference CPU's value, a per-(form, flag) fact characterised on the pinned rcp farm host with a coverage argument, pinned as a cert in the ASM-320 class; L3a's gate_flags reads it | L3b (farm job); L3a lifter emits | -- | DEF0 in the prover (w4 4a9eabc9), emission in L3a w5 83f85a243 (a0f3d8e: 17 276 Forms, 3 444 with a free output, 5 270 DEF0 flag outputs, DEFPRIOR 0); certs: farm round 2b on 4a9eabc9 (11-14 23:20) |
| 3i | cpuid Form: (eax..edx) <- CPUREF(eax,ecx) of rt/cpumodel.c, all other state unchanged, certified against the pinned model with the leaf answer as one uninterpreted function; unblocks gate_flags at 01441246, 0141479c, 01ceb353, 00ed193c | w4 session_01KmtZKSmVSGEpAznLZh6oF7 | w4 branch | doing: cpuid is a model gap in IR 70c8dc39 (ACL2 8.5 NIL dispatch; master 3ccb342c has x86-cpuid at instructions/cpuid.lisp:75). Order: (1) closure-keyed certs K, (2) one IR bump with the G1 fixes + x86-cpuid (transcriptions, ASM-009), (3) cert "agree-except-declared eax..edx". 34 cpuid sites over 7 images at shapes 77524556, one key 0fa2. ETA 3-4 h |

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

- 2026-11-14 23:35: K accepted (w4 a5063e58f, closure over executed fns; migrate 2,409 later by farm). (E) queue still open, sole w4 item. w9 f12fc303 accepted (RF/TF under debug-trap fact); round-trip test waits for product ELF on bootmachine; w9 on 4g static EH/SEH oracle. Awaiting L3a struct cpu sha.
- 2026-11-14 23:40: 4g w9 34fec49e EH/SEH oracle (Wine 9.0 except_i386.c rules): 8 images, 4,643 FuncInfo, 218 scope tables, 11 ThrowInfo, 235,261/235,261 decision rows agree, 0 findings. Next (w9): universe by frame-handler thunks (magic scan as cross-check), throw-site ThrowInfo resolution, V4 cookie, gate_eh wiring + pinned Wine external. rt/msvcrt_eh.cc vs oracle -> product ELF (step 3).
- 2026-11-14 23:38: 2.8 w3 695ca62c7 (merge w4 a5063e58f): gate_oracle 96/107; 9 lift files decided P/D by rule (see inbox w3), 2 foreign reported to L1/L6.
- 2026-11-14 23:48: 2.8 w3 8ec49f4c2: gate_oracle 103/105 (red: contract test_nativerows (L3a), test_census (B, CODEMAP.md L3a)). x86_proof.ir pinned external x86ir (fnv-nx-build tools/x86-proof-ir baf8bc80, tree_sha256 2dc6263d). Farm 07 run at 8ec49f4c2: test_irdeps 6/6, test_model_patches 10/10. CERTS.md has the K closure rule.
- 2026-11-14 23:53: 2.6 (E) w4 queue v1 9950e57e (prover a5063e58f/99d6300ab): pack universe 25,233 shapes (int 18,750 / simd 4,291 / x87 2,063 / sys 129), int with Form 18,584, 166 int without Form (L3a). Decision: requeue in full (v2, 18,584 int), no migration; old 2,409 superseded. Farms 05/07 round 2b stopped. New farms l3bcert-11..16 (session_01KmtZKUDJVSt45UA2vcvdQA, _01KmtZKTMkiCcjRf9Bze9Y9u, _01KmtZKUrZbQ7MdJ3hDUKMZR, _01KmtZKTdpbuo3QK3btysAKT, _01KmtZKUhpVAyQ3yPdHS2kij, _01KmtZKQqk87Ds5ALTXCTZmU) building toolchain; START on queue v2. w4 next: IR bump, then x87/simd/sys builders (design note first).
- 2026-11-14 23:56: 2.6 queue v2 f0dc641c (queue_id 29f53c484d24, 18,584 int, prover 99d6300ab/890bd2861) STARTED on 8 farms, N=24: 05 K1-3, 07 K4-6, 11 K7-9, 12 K10-12, 13 K13-15, 14 K16-18, 15 K19-21, 16 K22-24. noform.tsv (166 shapes, 2,279 addrs) -> L3a.
- 2026-11-14 23:58: 4g w9 7cb86dd5: EH oracle gate wired (gate_eh <image>/eh-conformance, Wine 9.0 pinned tools/wine-9.0-msvcrt f3e6148c). FuncInfo universe by thunk rule 4,643 = facts; 601 throw sites resolved; 2 findings to L3a (site 0xec449c ti None; 12th ThrowInfo 0x0117fc10). V4 cookie: none, per Wine 9.0 (:946). Blocked: ehoracle.json into l3a-lift/facts (L3a), struct cpu sha (L3a).
- 2026-11-14 23:58: 2.6 builders: no formal x87 model (pinned IR / ACL2 lack x87; 2,063 shapes). Decision (a) SDM transcription under new OWNER record vs (c) model-gap requested from orchestrator; recommended (a). w4 order: IR bump, sys 129, simd-int/MMX + SSE FP via ACL2 transcriptions (ASM-009, OWNER 8).
- 2026-11-15 00:03: OWNER 10 decided (SDM for parts without a pinned formal model = x87 machinery; CLAUDE.md integ 140d23355 merged 882529d2). x87 spec -> w9 (count distinct x87 instructions of pack 7ef692b5, then lift/proof/x87spec.py shared machinery + per instruction, every line cited; test vs SDM table rows). w4: prover hook for x87, and SIMD partition by declared CPU (MMX/SSE/SSE2 proved; cleared-feature sites -> L3a unreachability certs).
- 2026-11-15 00:10: w3b 211f7d14b (merge w4 85e77c01b): CERTS.md K item 4 computed (both IRs fetched); farm 07 at 211f7d14b: test_irdeps 6/6, test_model_patches 10/10, test_certs_build 11/11; gate_oracle 103/105. w4: bump hunks checked vs ACL2 3ccb342c (rol/ror c0, sar CF, cmps, cpuid) + bsf/bsr from bit.lisp added before pin; cpuid cert agree-except-declared (declared.json contract); SIMD partition 3,960 proved / 331 cleared (being re-derived from SDM feature rows over all families; L3a census 2,601 cleared sites incl. 26 fisttp).
- 2026-11-15 00:25: queue v2 running on all 8 farms, 24 shards (05 K1-3, 07 K4-6, 11 K7-9, 12 K10-12, 13 K13-15, 14 K16-18, 15 K19-21, 16 K22-24), all at prover 890bd2861, IR 70c8dc39. No shard published yet.
- 2026-11-15 00:30: bump IR (w4 7c097d301, ca469d14) adds bsf/bsr from ACL2 3ccb342c bit.lisp; shapes 0fbcc1, 0fbcc8, 0fbd0b, 0fbd00 and 66 0fbcc1 prove. Accepted. Before the pin, w4 adds F3 0FBC/BD as BSF/BSR from the CPUREF BMI1/ABM bits. Sys: 111 of 112 shapes with a Form are model-gap. Design accepted: sysspec (SDM + Wine 9.0 trap codes, declared CPL3/IOPL0/UMIP facts); 8E needs the declared selector table. The DSL raise interface and the SIMD Form state have been asked of L3a.
- 2026-11-15 00:35: #UD class final, 2,730 sites (pack 7b2faae276b2 @7ef692b5b, all 5,007,758 sites; w4 ca53ec4aa; fnv-nx-build l3b-lift/sdm-partition/... 1df9aed): 3DNow 2,574, Group2 /6 119, Group3 /1 13, fisttp 21, AVX 1, x87 mod=3 with no SDM semantics 2. Sent to L3a for certificates and the join against their 2,600 (fisttp 21 vs 26 open). Raise interface = Form.fault (dsl.py:239) with the seh.cc kind table. The SIMD/x87 Form state interface is frozen; L3a w1 is implementing it.
- 2026-11-15 00:40: x87spec (w9 04307b36/5bea5b9e/fec57651) covers 122 of the 122 listed forms; 120 have a spec and 2 are cleared-class (FSTP1, FFREEP). w9's x87 tests pass (12/12); the oracle gate was not run. Decided: transcendentals = RN_T(f) over the shared real symbol, with axioms justified as theorems. The FPTAN/FSIN* NaN/inf/range cases are encoded, not excluded. FPREM D>=64 = refinement of N in [32,63] over the full range. FCMOVcc on an empty ST(i) follows the SDM line or a nondeterministic refinement. The 8-shape gap waits for w4's x87_keys.tsv.
- 2026-11-15 00:45: bump IR pinned. x86_proof.ir 7c307464 (w4 0034e98e5, merged at 024dcff0): bsf/bsr from ACL2 bit.lisp; F3 0FBC/BD run as BSF/BSR from the CPUREF BMI1/LZCNT bits; the 7 bs shapes prove. The pinned-IR external is to be published by w4. The changed-closure requeue waits for the v2 records. L3a w1 has the zero-source bs Form (f8cf8ed2); it needs a fix (CF/OF/SF/AF/PF undefined unconditionally) and waits for this pin. The proof tests on the merge were not run here: the lead container has no capstone or z3, so w4 runs them.
- 2026-11-15 00:50: CLAUDE.md merged from integration 4004c3451 (orders: defects impossible by construction; parallel loops/deviations). The by-construction inventory of L3b (hand-written sets, second copies, second paths, list universes, restated targets) went to w3, one bounded pass with no farm. Conversion of PATH rows follows the inventory.
- 2026-11-15 00:55: the lead line fast-forwarded to w3b 7c47edbc0 (CERTS.md, codeuniverse, eaaudit). Pinned IR published: fnv-nx-build tools/sail-ir-7c3074644653 295aa620 (tree cc427662). Proof suite on 024dcff0 (w4): 61 tests, 59 pass, 2 import errors (gatefile, cas: trunk-only modules). x87spec d9bbbfd5 (w9): 18 tests pass; all 7 decisions applied. The x87 universe is 2,063 shapes / 392,864 sites from x87_keys.tsv, so the 8-shape gap is closed. Oracle gate: 92 of 94 test files certified; the 2 uncertified are not L3b's (test_nativerows: L3a; test_d3d9total: L6). sysspec v1 (w4 6fb0f0214) covers 74 of 112 sys shapes; the other 38 wait for the declared-machine record (asked of the orchestrator; f_sys and seh.cc vector sets disagree, sent to L3a).
- 2026-11-15 01:00: by-construction inventory (w3 d45032c67, journal/inbox/l3b-w3-byconstruction.md, on tree 7c47edbc0): 23 rows (R1 11, R2 10, R3 3, R4 1, R5 4, R6 1).
  - PATH, converting now:
    - w3: #13 (defect: the x86ir external still points at IR 70c8dc39 while the pin is 7c307464, so re-pin to tools/sail-ir-7c3074644653 and keep one copy of the sha); #1 (symform probe sampling becomes symbolic builder extraction); #3 (faults.py generated from fnvl.h); #2 (PRIV_MN deleted, read from sysspec); #8 (DEFECTS duplicates of IR patches deleted); #16 (reference rows generated from the pinned SDM/SMT-LIB).
    - w4: #5 (declared.json generated from cpumodel.c + SDM); #17 (OPMAP from SDM Appendix A).
  - Second paths, deleted now (rule 3), by w3: #19/#20 (prove.py table mode, universe.py, enc_table.py, backends.py) and #21 (isla, sail cache as externals under GATE_CACHE; farm scripts from the next queue).
  - Blocked: #4 (L3a w7 CODEMAP.md).
  - After the first frame: #6 (becomes the L5 record reader), #7, #9, #10, #11, #12, #14, #15, #18, #22 (#22 by w3 now, small).
- 2026-11-15 01:00: rule-5 decision (orchestrator). Selectors, IDT user vectors and sgdt/sldt/str go in one L5 declared-machine record, sourced from Windows (MS docs, else the Windows 7 x86 trap handler), keyed to the declared-OS record. The 38 remaining sys shapes are specified against it by w4.
- 2026-11-15 01:10: #UD class = 2,731 (w4 d12dfc498, opmap from SDM Appendix A table cells; fnv-nx-build .../7b2faae276b2-opmap 796346c). The +1 is 0F 1E /r at d3dx9_38:017d20e0, a blank A-3 cell; cause: a hand row lumped it with 0F 1F, now closed by the cell rule. Sent to L3a. Audit #13 done (w3 8c2da433d: IR sha derived from the x86ir external, loads 7c307464; formgate 31/31, certs_build 11/11). Audit #1 reworded: soundness comes from the exhaustive symcheck, which had no gate; symcheck becomes part of gate forms (w3). #5 done (w4 04102380b, declared.json generated). Open: tree_sha of the IR external (MANIFEST cc427662 vs loader ebbc7a42), so w4 uses the loader's hash. x87 carrier: fr is the 80-bit image (L3a); the x87 spec is being revised to exact-vs-binary64 classes from SDM Vol. 1 8.3.
- 2026-11-15 01:20: audit rows done by w3: #13, #3 (faults.py; switching to L3a's faults.json f67ed0cc), #2 (sdmpriv, SDM-derived privileged set of 53 rows), #8 (ACL2 citation pending), #16 ea16 part, #19/#20 (second prover path deleted, a5af25537), #21 (isla/cache under GATE_CACHE), #22. #1 becomes symcheck-as-gate (in progress). gate_oracle at w3 f55a9405a: 104/110 (missing entries: test_census w3; test_bsfbsr_closed_form, test_declared_gen, test_sdmfeat, test_sysspec w4; test_nativerows L3a).
  - After the first frame: #16 rest (shape/stack/smtlib/model_patch reference rows; a generator would be a second SDM decoder); #21 isla as a prebuilt external (next-queue farm, FULL=1); #6, #7, #9-#12, #14, #15, #18.
- 2026-11-15 01:20: L5 published the declared machine record x86sys.json (claude/fnv-l5-platform-w14-osrec 9d6f6d4bd: selectors, ring-3 int vectors 03/04/2a-2e). sgdt/sidt/sldt are undecided; CR4/IOPL are to follow. sysspec readers: w4. x87spec: FYL2X infinity defect fixed (w9 4df05f34); FSCALE, FPATAN and FYL2X tables are now cell tests.
- 2026-11-15 01:30: lead line e9bb4cbac = merge of w3b 37424079c.
  - Done: x86ir re-pinned to 8b711fba (tree fdf3f3fd); L3b faults.py replaced by L3a's faults.json reader; symcheck in gate forms (waits for the relift pack's instrs.tsv, since pack 7ef692b5 has none); rep-prefix-ignored cited from ACL2 string.lisp 162-165/213-220; test_rtroot green (it was L3a's pin flags).
  - gate_oracle at 37424079c: 105/112.
  - The w4 merge is pending (sail_setup.sh conflict, resolved by w4).
- 2026-11-15 01:30: L5 x86sys.json 28bbec363: cr4.umip=0 is derived (CPUREF max leaf 4); the opcode copy is deleted. The int-vector source conflict (Wine set with 0x29 against Windows 7) went to the orchestrator; I recommended admitting ReactOS.

## 2026-11-15 04:10Z — resume after the limit reset (integ d65489ae9)
- Cert farms 05, 07 and 11-16: queue v2 is STOPPED, and nothing from it was published. Cause: the v2 header pins IR 70c8dc39, but the bump pin 7c3074644653 superseded it (w4 0034e98e5; external tools/sail-ir-7c3074644653 8b711fba). The farms wait for v3.
- w4 (doing):
  1. merge the lead branch (one IR fetch path in sail_setup.sh);
  2. queue v3 on IR 7c307464 for pack 7b2faae276b2;
  3. the bs shapeprove against w1 426d95f8;
  4. the oracle.json entries;
  5. SIMD slice 1 (reference).
  - The sysspec readers landed at 8ed832c07.
- w3 (doing): the count of sites that depend on undecided x86sys rows, at the L5 head; then audit rows #7, #9-#12, #14 and #15.
- w9 (doing): the carrier revision; nd.f2xm1.out; fault_kind from faults.py; the SDM tables F2XM1/FPTAN/FSIN/FSINCOS/FPREM; the NaN rule against Table 4-8.
- Blocked:
  - the 2.6 certs wait for queue v3 (w4);
  - symcheck waits for the relift instrs.tsv (L3a w1);
  - the #UD certificates wait for L3a w6;
  - row #4 waits for CODEMAP.md (L3a w7).

## 2026-11-15 09:15Z
- All 8 farms confirm: the 24 v2 shards ran to the 2 h background limit, emitted no SHAPEPROVE line, published nothing, and no process survived.
  - Cause (rule 8): the runner keeps results only at the end of a shard.
  - Class fix, given to w4 for v3: per-shape results keyed by (shape_sha, IR pin, prover hash), published incrementally, with skip on restart; the shard count is derived from the measured per-shape rate.
  - Orchestrator rule 8 fix, also in v3: the queue reads the IR pin from proof/sail_pins.txt at its head, and the runner checks it against the pin at its own head before the first shard.
- bs class proof (w4, w1 426d95f8, IR 7c307464):
  - 96 class shapes, 48 proved and 48 no-form. The no-form ones are F3 tzcnt/lzcnt, which have no builder. Sent to L3a: register them on bs() by decode under CPUREF.
  - The pack universe (25,233 shapes) holds 0 bs shapes.
- sysspec readers done (w4 8ed832c07). IN/OUT/INS/OUTS are uncertified until L5 adds the tss_io_bitmap row (asked of w14).
- L5 w14 2895b30fe: 0x29 is in int_ring3.excluded as undecided; vectors 3, 4 and 0x2d come from Wine 9.0. w3 counts the dependent sites at this head.

## 2026-11-15 09:20Z — the weekly budget binds (integ 9ea2d5403)
- Workers kept: w4 (queue v3, the 2.6 certs, sysspec) and w9 (the x87 carrier). w3 finishes its dependent-site count, then it is archived; its audit rows #7, #9-#12, #14 and #15 wait until after the first frame.
- Farms kept: 05, 07, 11 and 12. Archived: 13, 14, 15 and 16. Their branches stay.
- tss_io_bitmap: L5 w14 3646ebab3 holds "no port permitted" with no opcode copy. The I/O instruction set stays in sdm_priv_rows.tsv.
- L3a takes the tzcnt/lzcnt class into img.dec: a feature-dependent encoding is decoded under CPUREF (tzcnt→bsf, popcnt→#UD, pause→nop).

## 2026-11-15 09:25Z — queue v3 started (2.6)
- Queue v3: fnv-nx-build l3b-lift/queue/7b2faae276b2-c4ba6f912412-v3 at 20ffd0c3.
  - queue.tsv sha 48fddcc0, queue_id f3eb5096ad36.
  - 18,649 integer rows. Pack 7ef692b5b, prover w4 c4ba6f912, IR 7c307464.
  - Out of this queue: x87 2,063, SIMD 4,291, sys 129, and 101 integer shapes without a Form.
- Farms 05/07/11/12 run K=1-6, 7-12, 13-18 and 19-24 of 24, three at a time.
  - Resume rule: a shard killed at the 2 h limit is rerun with the same command; the appended jsonl skips shapes already proved.
- w4 merged the lead branch (cdd9ce1b1): one IR fetch path. Next: the oracle.json entries, then SIMD slice 1 (prover state for xmm/mm/mxcsr/fr/fptr).
- Sent to L3a w1: the pinsrw/pextrw/pshufw lane index must be an ite over the masked immediate.

## 2026-11-15 09:30Z
- Undecided x86sys rows, dependent sites (w3 330074b8a, proof/undecided_sites.py).
  - Inputs: pack 7b2faae276b2 @ 7ef692b5b, L5 x86sys 3646ebab3. The universe is 5,007,758 pack addresses, all decoded.
  - Full site list: fnv-nx-build l3b-lift/undecided-sites @ 10f8792.
  - Sites (markers) per row:
    - cr4.pce 0
    - idtr_limit 156
    - int other vectors 154
    - int 0x29 0
    - selectors.gs 5,305 (3,015 addresses, 2,988 of them in d3dx9_38)
    - sgdt 5
    - sidt_base 0
    - sldt 35
  - These counts are an upper bound: almost all the gs and int sites are data decoded as code. The cut is L3a's code-range facts (CODEMAP, w7); after it, each remaining site needs a reach certificate or a decided row.
- w3 1d42a4550 (#12): certbasis.py is the one source of OBLIGATIONS and platform_sha. Merged at 29939d7b4. w3 is archived (budget).
- Queue v3 is BLOCKED on the 4 farms.
  - Cause (rule 3): farm_shapeprove.sh still read the IR pin from sail_pins.txt after #13 moved it to the external x86ir.
  - w4 fixes it: the IR comes only through the external, and the sha comes from certs_build --model-sha.
- w9: the x87 tables, Table 4-8 and loads/stores/BCD are done (d826016e, d0a72b87; 44 tests). The merge into the lead branch waits for w9 to resolve the CERTS.md/oracle conflicts.
- w4 SIMD: Sail has no SIMD semantics, so the reference comes from ACL2 transcriptions, with SDM specs under OWNER 10 where ACL2 has no instruction. The cr0/cr4 OSFXSR and MXCSR rows are asked of L5.

## 2026-11-15 09:35Z
- Queue v3 is blocked by 3 defects, all given to w4 as one commit:
  1. The farm read the IR pin from sail_pins.txt (rule 3; the fix is the external x86ir only).
  2. prove.py post_of fails on w1's Form.post() lists/tuples (fr/xmm/fptr), so 0 integer shapes are provable. The fix is the prover state extension in prove.py, by the d935ecda widths.
  3. Fake success: a shard whose shapes all crashed published an empty jsonl with "shapes=0". The fix: a crash is an error record; a shard with missing or error records exits 1 and publishes nothing; "already published" requires a full record count.
- Farm 11 published 3 empty branches (…/7c3074644653-f3eb5096ad36/{13,14,15}of24) through SAIL_IR; it is deleting them.
- Merged w9 into the lead branch (d920f6633). w9 693a75a9: ou_pre names the L3a fact x87-masked-sites (asked of L3a); FBLD digits above 9 are nd.fbld.img; 45 tests.
- gate_oracle: 113/116. The 3 missing tests are L3a w1's (test_census, test_dsl_xsimd, test_forms_simd), asked of L3a.

## 2026-11-15 09:50Z
- w4 8d6e362c8+, the queue v3 blockers fixed:
  - one IR pin, through the external x86ir and certs_build --model-sha;
  - incremental publish every 600 s and on signal, with resume from the published file;
  - prove.py has the `xstate` obligation, so integer Forms prove again; x87/SIMD Forms are a model-gap until the spec path covers them.
- Rate: about 45 s per shape over a spread sample, so 777 shapes per shard is about 9 h. That explains why every v2 shard hit the 2 h limit. Queue v3 gets its shard count from proof/shardcount.py (ceil of rows × p95 / 90 min). Not published yet.
- sysspec on x86sys 3646ebab3, over the 129 system-family shapes: proved 74, uncertified 42, not covered 13, failed 0.
  - The uncertified ones wait on the descriptor readable bit, int other_vectors, sgdt and sldt. The descriptor, TR and lar/lsl rows are asked of L5 w14.
  - Not covered (13): iretd, str, rdtsc, cpuid, lar, mov. These go to w4 after v3.
- The 3 empty branches …/7c3074644653-f3eb5096ad36/{13,14,15}of24 cannot be deleted, because the proxy refuses deletes (403). Resume from the published file makes them harmless.
- SIMD split: w9 owns the spec path (ACL2/SDM specs; Sail has none), w4 owns prove.py.
- w9 7d4c17d6: the x87 spec is complete over the registry (47 tests), merged at 32654c699. (D), the seh CONTEXT test, waits for L3a's lift/rt recipe root.

## 2026-11-15 09:58Z
- SIMD spec (w9 d1ce5545, merged at 60abe266a). simdcheck over all 4,290 pack SIMD shape keys:
  - proved 2,659, proved by cases 98, FAILED 0;
  - no-form 773: SSE FP arithmetic, sent to L3a w1 as one parameterized builder;
  - no spec row 760: the integer remainder plus cmpps, comis, cvt, rcp/rsqrt and ld/stmxcsr. w9 does these next.
  - Rules given: one name for the FP-mask fact (ASM-204's removing fact); DAZ from CPUREF mxcsr_mask (L3a); rcp/rsqrt from the reference table; the host comparison is not a proof.
- Queue v3 is ready at w4 0e643a0a7 with N=54 (p95 15.46 s, 90 min budget), queue f3eb5096ad36.
  - The merge into the lead branch conflicts with certbasis (prove.py and others). Since prover_sha covers those files, w4 merges the lead first; the farms START on the merged lead commit.
- Orchestrator ruling: an x86sys row is the ring-3 observable. So cr0.ts=0, cr0.em=0, osfxsr=1 and osxmmexcpt=1.
  - mxcsr_mask is CPUREF's (L3a).
  - The GDT descriptor contents stay undecided. Their 37 sites wait for CODEMAP, then get reach certificates.

## 2026-11-15 10:05Z — queue v3 STARTED (2.6)
- The lead branch holds w4 cbcc5a545 at b2300680f: certbasis is the one source of OBLIGATIONS (def0, xstate) and of prover_sha.
- The farms run on lead b2300680f with N=54, queue f3eb5096ad36 (18,649 rows, IR 7c307464). Shards: 05 K=1-14, 07 15-28, 11 29-41, 12 42-54. Each farm runs P = min(nproc, mem/6 GB) at once, with resume at the time limit.
- Freeze until v3 finishes: no edit to prove.py, model_sail.py, symform.py or certbasis.py (they are in prover_sha).
- w4 next: the 13 not-covered sysspec shapes, then aggregating v3 into certs.tsv (shardok branches only).
- w9 next: the SIMD remainder; a rerun of the subject on L3a 0bfa4a518 (the SSE FP class); MXCSR_MASK from CPUREF (0xFFBF, no DAZ); and removing the Unicorn and host comparison from the tests.

## 2026-11-15 10:10Z
- sysspec (w4 609381591, merged), on x86sys 1625dfd84, over the 129 system shapes:
  - proved 79, uncertified 43, not covered 7, FAILED 0;
  - rdtsc, str and cpuid are proved;
  - not covered: iretd 6 (no Form; L3a w1 queue, the SDM IRET same-privilege rule) and 1 mov.
- Shard count: the final timing sample (132/132) gives N=59, but the farms keep N=54; resume covers any overrun.
- No v3 shard has been published yet.

## 2026-11-15 10:20Z
- SIMD (w9 acc73e7d, merged at ac173e928). simdcheck over 4,290 shapes:
  - proved 2,828, by cases 122, no-form 1,005, no-row 335, FAILED 0.
  - rcp/rsqrt come from rt/rcptab.c (exhaustive 2^32 certificate rcptab.cert.json).
  - The FP-mask fact has one name, ASM-204.
- w9 next:
  - rerun the subject on L3a 0bfa4a518 (the SSE FP class);
  - route 3DNow and VEX to the CPUREF #UD class by derivation;
  - pause and cvtpi2ps rows;
  - the ACL2-vs-SDM deviations cited per row.
- L3a is asked for FNVL_CPUREF_MXCSR_MASK (0xFFBF) in cpumodel.c. Until then the SSE FP certificates are uncertified.

## 2026-11-15 10:35Z
- Owner order (integration 76e450faf): the L4 boot loop runs now. A boot stop routed to L3b comes before every other L3b item.
- The first stop is ELF 509809b3: 0xC000001D (illegal instruction) after `init d3dx9_38.dll`. L4 is classifying it. If it is a #UD-class site (CPUREF cleared feature, no-SDM encoding), it is L3b's together with L3a.
- "Call economy" (integration 0e4c31db2) is passed to w4 and w9.
- 10:40Z: the boot-loop order is WITHDRAWN (integration 4674ce3e9), so no measurement-run stops are routed to L3b. The path items stand as before.

## 2026-11-15 13:00Z — queue v3 progress
- 4/54 shards complete (15, 29, 30, 42): 1,349 proved, 31 model-gap, 1 timeout, 0 failed. Running aggregate: l3b-lift/certsagg/7c3074644653-f3eb5096ad36/54-4d4b7448fed5 (w4, rerun every 45 min).
- Checked: shards 29, 30 and 42 hold disjoint shape sets (345 each, overlap 0).
- Farm 05 has been disconnected since 10:08 and none of K=1-14 has started; it is asked to restart. Farms 07/11/12 each run 2-3 at once (min(nproc, mem/6 GB) on 4 cores / 16 GB), about 22 h for the rest.

## 2026-11-15 13:10Z
- SIMD stage merged (d85062eb3 = w9 6047f67e; subject L3a 47b68e51c), 4,290 shapes: proved 3,155 (122 by cases), ud-class 331, FAILED 108, timeout 566, no-form 130. Frozen prover files untouched.
- FAILED 108 = two L3a Form defects (NaN+denormal sets DE: 96; zero divisor + denormal dividend sets DE with ZE: 12). Assigned by L3a to w7 (session_01KmtZKV5F6B2PqGD9piX26f, claude/fnv-l3a-lift-w7-simd): one flag-priority function in dsl.fp, SDM §11.5.2.1 order, plus the no-form mnemonics. blocked (L3a w7) -> rerun on w7's commit.
- Decision: SDM over ACL2 where they contradict (ACL2 = transcription source; trust roots OWNER 9/10). Recorded in CERTS.md by w9.
- w9: doing — 566 timeouts by per-lane x operand-class split (simdcheck/ssefp only, completeness stated).
- v3: 4/54 complete (15,29,30,42); farms running; w4 aggregates at 54/54.
- 13:20Z v3 shard 17/54 done (farm 07): 346 rows, proved 336, model-gap 10, failed 0. Complete: 5/54 (15,17,29,30,42).
- 13:30Z L3a w7 ready (claude/fnv-l3a-lift-w7-simd after cfe7049b: flag-priority fn + 17 Forms). Rerun over the full 4,290 universe assigned to w9, with spec rows prefetch (0F18 /0..3) and the mem_width None/8 Concat rule. w7's 62-shape local run is not a result.
- 13:40Z w7 subject now df8cfeda7 (supersedes 46357156; hand handlers deleted for Form-covered mnemonics). w9: SIMD rerun at df8cfeda7. w4: far call/jmp sysspec rows (SDM Vol 2, same privilege), selector validity from x86sys only; non-decided selectors -> undecided_sites.
- 13:50Z merged w4 2314e07ed (far CALL/JMP sysspec). Sys universe at w7 df8cfeda7 + L5 1625dfd84, 145 shapes: proved 79, uncertified 59, not-covered 7 (iretd 6, mov 1), FAILED 0. Far sites: 202 labelled sites (upper bound, pack 7ef692b5), all undecided until x86sys decides descriptors / CODEMAP reach certs.
- Decision: CALL far's CS push: the spec constrains only the low 16 bits; the upper half stays unspecified, as SDM PUSH Sreg allows (zero-extend or unmodified). A Form that zero-extends is admissible. No new fact needed.
- Defect to L3a w7: symform.symbuild AttributeError (SymIns has no modrm; forms_flow.is_far) on FF /3 and FF /5 memory forms, so no certificate is possible for them.
- 13:55Z sys+far at w7 9c97f84bc (L5 1625dfd84), 145 shapes: proved 79, uncertified 59, not-covered 7, FAILED 0. The far memory Form defect is closed (all 10 build). w4: prove the cs/NULL cases of the memory forms with the EA from the Sail decode (no Form-side EA). w9: SIMD rerun at 9c97f84bc.
- 14:00Z merged w4 7870c2fc9: 16 far shapes, cs-selector and NULL cases proved (EA from the FF /6 Form of the same ModRM), uncertified 16 on undecided descriptor rows, FAILED 0. Far certs depend on the FF /6 cert of the same ModRM shape, checked mechanically in the aggregation.
- 14:05Z merged w4 944219378: ea-basis rule (far memory case counts only with its FF /6 basis in certs.tsv; basis in far.tsv). ESP-based far CALL (ff5c2c00) uncertified: SDM far CALL text does not state EA-before-decrement.
- 14:00Z v3 shard 16/54 done (farm 07, resumed once): 346 rows, proved 334, model-gap 12, failed 0. Complete: 6/54 (15,16,17,29,30,42).
- 14:10Z v3 shard 31/54 done (farm 11): 345 rows, proved 338, model-gap 6, timeout 1, failed 0. Complete: 7/54 (15,16,17,29,30,31,42).
