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
- 14:10Z v3 shards 43 (proved 337, gap 7, timeout 1) and 44 (proved 337, gap 8) done (farm 12), failed 0. Complete: 9/54 (15,16,17,29,30,31,42,43,44).
- 15:15Z v3 shard 18/54 (farm 07): proved 337, gap 8, FAILED(model-defect) 1 (66f3ab rep stosw, defect rep-prefix-ignored). Decision: declared-model-defect shapes are certified against an SDM transcription (OWNER 10, citations per line), never passed; stringspec.py assigned to w4 after the aggregation. Complete: 10/54.
- 15:25Z merged w4 c94188eaa: stringspec.py (SDM REP/REPE/REPNE x MOVS/STOS/LODS/CMPS/SCAS, 8/16/32, DF symbolic, ECX=0), 60 shapes proved; F2 on MOVS/STOS/LODS uncertified (SDM defines REPNE for CMPS/SCAS only). defectspec.py dispatches every declared-defect row by id (no-spec reported). At 13/54: 1 defect row (66f3ab), proved by stringspec.
- 16:10Z v3 shards 32 (proved 339, gap 4, timeout 1, declared-defect 1) and 33 (proved 339, gap 6) done (farm 11), failed 0. Declared-defect rows go to defectspec. Complete: 12/54.
- 16:10Z v3 shards 45 (proved 336, gap 9) and 46 (proved 337, gap 8) done (farm 12), failed 0. Complete: 14/54.
- 17:10Z v3 shard 20/54 done (farm 07): proved 339, gap 6, failed 0. Complete: 15/54 (15-18,20,29-33,42-46); in flight 19,34,35,47,48. Farm 05 (K=1-14): container reclaimed twice (detached setsid lanes die with it), 0 published; restarted with run_in_background; if nothing by 17:54Z, 1-14 is reassigned to farms 07/11/12 and farm 05 archived.
- 17:30Z farm 05 producing: K=1 increment on origin (1of54-a4fcfcf45590, 119 records); K=2 running. Cause of the one-at-a-time runs: a flock fd 9 in farm_shapeprove.sh held past flock -u (inherited lock). Fix by rule assigned to w4 (close fd 9 for children, or a per-K work dir); farm 05 uses a per-K FNVNX_WORK until then. Check at 17:54Z cancelled.
- 17:35Z merged w4 2d6fe094d: the farm lock defect's cause was flock -u inside the non-queue else branch, so with --queue the lock was held to shard exit. Fix: staging is a { flock 9 ...; } 9>lock group, so no child holds fd 9. Farm 05 moves to it for K>=3.
- 17:35Z v3 shard 19/54 done (farm 07): proved 342, gap 4, failed 0. Complete: 16/54.
- 18:05Z v3 shard 1/54 done (farm 05): proved 337, gap 9, failed 0. Complete: 17/54. Farm 05 K=3 started on the fixed script (76cf2acca).
- 18:10Z v3 shards 34 (proved 336, gap 9) and 35 (proved 334, gap 11) done (farm 11), failed 0. Complete: 19/54.
- 18:10Z v3 shards 47 (proved 336, gap 9) and 48 (proved 337, gap 8) done (farm 12), failed 0. Complete: 21/54.
- 18:30Z v3 shard 2/54 done (farm 05): proved 337, gap 9, failed 0. Complete: 22/54. Farm 05 K=3 proving on the fixed script; K=4 started beside it (two-shard lock test).
- 18:35Z v3 shard 21/54 done (farm 07): proved 339, gap 5, declared model-defect 1 (to defectspec), failed 0. Complete: 23/54.
- 19:10Z v3 shard 3/54 done (farm 05): proved 335, gap 11, failed 0. Complete: 24/54. Lock fix verified: K=4 got past the lock and published while K=3 ran on the same machine (76cf2acca).
- 19:20Z orchestrator: test_gate_fpexact.py (on claude/fnv-l2-source-plugins, 3b15d1138) calls aarch64-linux-gnu-gcc directly (rule 3, second compiler path). Assigned to w4: compile through verify/recipe.py (pinned external + plugins); commit to L1 (session_01KmtZKUn5UJpiHYPtNRT6p9). Must land before L1 cand3 adds its compiler-execve check.
- 19:25Z merged w4 1c445868e: test_gate_fpexact gets its object from the recipe's toy root via gate_build.ensure, with no compiler execve. Not yet run (the recipe env check fails on w4's machine); L1 runs it in cand3.
- 19:30Z v3 shard 4/54 done (farm 05): proved 337, gap 9, failed 0. Complete: 25/54.
- 20:00Z workers (cap now 4): w4 (sysspec, v3 aggregation, fpexact test), w9 (SIMD rerun at w7 9c97f84b), w10 NEW session_01KmtZKS2tNWVFthT6Fj3JQU (566 SIMD FP timeouts, lane x operand-class split, simdsplit.py). Farms 05/07/11/12 on v3.
- bottleneck: farm proving time. v3 is 25/54 shards after ~10 h; each shard takes 3,400-4,400 s, 2 at a time per farm, 4 farms, so about 4 h are left. The 4-farm cap limits it. Second: L3a (CODEMAP / SITES.tsv for the undecided and far sites; the lift/rt recipe root for the SEH CONTEXT test).
- 20:10Z v3 shards 36 (proved 338, gap 7) and 37 (proved 336, gap 8, timeout 1) done (farm 11), failed 0. Complete: 27/54.
- 20:15Z farms raised to 8 (orchestrator, integ c25091f47). New: farm 17 session_01KmtZKQsNbUFJo5YLf99XPh (K 9-11), 18 session_01KmtZKS6JztAqdT1BzKcjPM (12-14), 19 session_01KmtZKRrFezD7nFUYe49K4K (26-28), 20 session_01KmtZKTosbJBST86GQV375j (40,41,53,54). The old farms keep 05: 5-8, 07: 22-25, 11: 38-39, 12: 49-52. Each farm stops when its list is done; then it is archived.
- 20:15Z v3 shard 50/54 done (farm 12): proved 334, gap 10, timeout 1, failed 0. Complete: 28/54.
- 20:20Z v3 shard 5/54 done (farm 05): proved 335, gap 11, failed 0. Complete: 29/54.
- 20:20Z L3a udcert (bd94fd02, facts 6944080) found 3 defects in our #UD site list. Causes:
  - d3dx9_31 missing (2,577 sites): hand-written module lists in undecided_sites.py:97 and ehoracle.py:765 (rule 1);
  - 66 U-NOGATE: opmap does not read the SDM x87 escape tables, so D8-DF forms look undefined (e.g. fist m16int);
  - 87 U-NODEC: our own sweep, not the lift facts' code universe (rule 2).
  Assigned to the new worker w11 (session_01KmtZKTzdEzraX9VWyyDcG3). Workers: w4, w9, w10, w11 (4/4).
- 20:25Z owner order (integ 5c3e8e289), no rework without progress. Checked: all open L3b items block step 2 (the pack root enters the trunk only when its forms are a subset of the certified ones): v3 integer, SIMD rerun and timeouts, sys/far, #UD list. The fpexact test blocks L1 cand3. Nothing open is polishing; no task stopped.
- 20:30Z L3a pre-announces a pack density rule (758.9 MB C++ for 7.79 M instructions). Our position on liveness-pruned EFLAGS: (A) per (shape, live_mask), a projection lemma from the full xstate certificate (elided Form = full Form minus the dead-output assignments, checked by z3, no farm re-proof); (B) a per-function liveness certificate from the CODEMAP CFG, with boundaries and exception points as readers of all flags, which the gate re-checks. Written statement once L3a sends the concrete rule.
- 20:40Z merged w11 ee6b58d01 (#UD list). Measured at facts 6944080, L3a udcert bd94fd02, SDM a4a62e6a:
  - images: the 2 hand lists are deleted; imageset.py reads modules.tsv; d3dx9_31 now has 2,577 + 11 rows;
  - opmap reads the 16 x87 escape tables (405 cells);
  - U-NODEC 87 -> 0 by construction: factsites.py takes the sites from L3a's facts graph (5,694,413 instructions over 8 images), and packuniverse's SITES path is deleted;
  - cleared sites 5,199 (5,192 undefined opcode, 7 SSE3);
  - U-NOGATE 66 -> 44: Grp2 /6 'sal' 36, Grp3 /1 'test' 7, D9 DF 1, all SDM-reserved blank cells.
- Decision on the 44: the existing declared-CPU rule applies. A reserved encoding is #UD on the declared CPU, so each site needs a reach certificate (unreachable), the same as a cleared-feature site; any that cannot be certified goes to the orchestrator. No new assumption.
- Open: ehoracle's --modules change has not been run (pefile missing). lift/tools/udcert.py is L3a's file at bd94fd02, carried byte-identical until L3a's branch merges.
- 20:45Z correction: the 44 reserved-encoding sites get no assumed #UD. L3a points out, rightly, that the declared-CPU rule clears features, not reserved encodings, and that real parts execute Grp2 /6 as SHL and Grp3 /1 as TEST. They stay their own class with no semantics; the source will be the Sail model's decode (OWNER 9). L3a and L3b decide it after the first frame (udcert is parked under the owner order of integ 5c3e8e289).
- 20:45Z v3 shard 6/54 done (farm 05): proved 333, gap 13, failed 0. Complete: 30/54.
- 20:45Z v3 shard 22/54 done (farm 07): proved 339, gap 6, failed 0. Complete: 31/54.
- 20:45Z v3 shard 23/54 done (farm 07): proved 341, gap 4, failed 0. Complete: 32/54.
- 20:50Z correction: the pack's d3dx9_38 bytes are correct (w7: 0 of 3,426 encs rows mismatch the relocated image). w11 compared raw file bytes, not pemod.load at the assigned base. w11 moves every image read to pemod.load and reruns; the 66->44 split and the cleared totals are provisional until then.
- 20:55Z merged w11 7beb11a4b. ehoracle runs over the real image set (8 images; rows 235,208 agree; fields_checked 43,051; wine_findings 0), with 2 FalloutNV findings, the same as before the change, sent to L3a. udcert.py is pinned by l3a_pins.json plus a test. Sent back to w11: the cleared-feature (3DNow, #UD by CPUREF) and reserved-encoding classes are separate; L5's real x86sys 1625dfd84, not a synthetic record; image reads through pemod.load.
- 21:00Z merged w11 2c4ebfdc7. Every image read in lift/proof goes through pemod.load at the module table's base. sdmfeat: 787 forms, 0 problems, 5,704,065 sites. L3a's udcert.Pe still reads raw file bytes (an L3a defect, sent by w11); with a relocated Pe: U-NODEC 0, U-DEAD 0, U-NOGATE 92 (sal 84, test 7, D9 DF 1), U-ROOT 5,205. Still open with w11: cleared-feature vs reserved-encoding classes; L5's real x86sys pin.
- 21:05Z merged w11 e014de1eb. L5 x86sys is pinned at 1625dfd84 in lift/proof/pins.json, with test_pins.py. Measured at that commit: 5,704,065 sites decoded; sdmfeat clears 5,297 (cleared-feature 57 SSE3; reserved-encoding 5,240). On the real record, undecided_sites gives selectors.gs 4,024 / 344 shapes, idtr_limit 88, int_ring3.other_vectors 87, sldt 23, sgdt 3. Defect (rule 1): 5,148 of the reserved-encoding sites are 3DNow, gated only by L3a's hand-written f_cpufeat row; the Intel SDM has no 3DNow. Decision: pin the AMD APM (vol 3: CPUID Fn8000_0001 and the 0F 0F opcode map) as the vendor source. cleared-feature is derived from the SDM and the APM together, and f_cpufeat's feature bits are generated from those pins (owner L3a). w11 does the pin and the class.
- 21:12Z v3 shard 26/54 (farm 19): shapes 345, proved 338, model-gap 7, failed 0, 3,150 s. Six checkpoint branches; aggregation takes the one with 345 rows. 33/54 complete.
- 21:13Z v3 shard 27/54 (farm 19): shapes 345, proved 338, model-gap 7, failed 0, 3,178 s. Its counts equal shard 26's, so I checked the content: the closure_sha and dsl_sha differ, so these are distinct results. 34/54 complete.
- 21:16Z merged w11 2919259af; frozen files untouched. cleared-feature now comes from the pinned vendor maps: Intel SDM first, then AMD APM vol 3 rev 3.14 (fnv-nx-build tools/amd-apm aa490c484c89; the sha256 and the mirror source are in MANIFEST.json). No feature name appears in code. Over 8 images and 5,704,065 sites (facts 6944080): cleared-feature 5,205 (0F 0F 4,890 + FEMMS 258 + SSE3 57); reserved-encoding 92 (sal /6 84, test /1 7, D9 DF 1), no semantics. Feature → cpuid bit table: fnv-nx-build l3b-lift/cpuid-feature-bits/2919259af d7dd09d041a9 cpuid_feature_bits.tsv, which L3a w12 makes f_cpufeat read. Open: sdmfeat forms went 787 → 665 with sites unchanged; I asked w11 for the explanation.
- 21:16Z the 787 → 665 drop is explained: the 124 3DNow 'undefined' forms (5,148 sites) became 2 vendor rows (0F 0F 4,890, 0F 0E 258). Shapes stay 24,179 and sites 5,704,065, and no site lost its class (mapping in journal/inbox/l3b-w11-form-mapping.tsv at edc5c550e). The w11 #UD-list item is done; w11 archived.
- 21:22Z v3 shard 7/54 (farm 05): shapes 346, proved 336, model-gap 10, failed 0, 3,892 s. 35/54 complete.
- 21:23Z v3 shards 38 and 39 (farm 11): 345+345 shapes, proved 334+336, model-gap 10+8. Each has 1 declared model defect, rep-prefix-ignored: f3aa rep stosb and f3ab rep stosd, both stringspec's class, so neither is a pass until stringspec certifies it. Farm 11 is done and archived. 37/54 complete.
- 21:23Z v3 shard 12/54 (farm 18): shapes 346, proved 335, model-gap 11, failed 0, 3,736 s. 38/54 complete.
- 21:24Z v3 shard 13/54 (farm 18): shapes 346, proved 337, model-gap 9, failed 0, 3,758 s. Shard 12's final branch is 12of54-277dcdf2ce02. 39/54 complete.
- 21:26Z v3 shards 9 and 10 (farm 17): 346+346 shapes, proved 335+335, model-gap 11+11, failed 0. 41/54 complete.
- 21:30Z v3 shard 41/54 (farm 20): shapes 345, proved 339, model-gap 6, failed 0, 4,067 s. 42/54 complete.
- 21:30Z v3 shard 40/54 (farm 20): shapes 345, proved 340, model-gap 5, failed 0, 4,105 s. 43/54 complete. Still running: 8, 11, 14, 24, 25, 28, 49, 51, 52, 53, 54.
- 21:42Z v3 shard 8/54 (farm 05): shapes 346, proved 335, model-gap 11, failed 0, 3,846 s. Farm 05 is done and archived. 44/54 complete. Still running: 11, 14, 24, 25, 28, 49, 51, 52, 53, 54.
- 21:43Z v3 shard 24/54 (farm 07): shapes 345, proved 341, model-gap 4, failed 0, 3,803 s. 45/54 complete.
- 22:01Z w9's SIMD rerun at w7 9c97f84b is partial: 1,724 of 4,290 shapes; proved 1,288, ud-class 330, timeout 106, FAILED 0. About 50 shapes/h; the rest are FP shapes at the 240 s budget. Decision: stop the run and publish a checkpoint. The FP remainder and the timeouts go to w10's simdsplit; the non-FP remainder goes to the farm queue.
- 22:03Z merged w9 a69e29d8 (simdcheck.touches_fp: an FP shape is one whose Res.fpl is non-empty). Checkpoint at fnv-nx-build l3b-lift/simdrerun/9c97f84b/partial c18ff53a, over 4,290 SIMD shapes: done 1,618 (proved 1,288, ud-class 330), timeout 106, remaining FP 761, non-FP 1,682 queued (queue_nonfp.tsv), no-form 123 (symbuild TypeError 122, KeyError 1; sent to L3a), FAILED 0. The 867 FP + timeout keys go to w10's simdsplit. The 1,682 non-FP keys go to farms after w4 adds a simdcheck mode to farm_shapeprove.sh. w9 archived.
- 22:03Z v3 shard 28/54 (farm 19): shapes 345, proved 339, model-gap 6, failed 0, 3,023 s. Farm 19 is done (26-28) and kept for the SIMD non-FP queue. 46/54 complete. Still running: 11, 14, 25, 49, 51, 52, 53, 54.
- 22:08Z L3a w13 88f691c09 fixes the SIMD noform class (a symbolic immediate in pick/pextrw/pinsrw): 122 of 123 shapes now have Forms, and the 3,739 existing SIMD Forms keep their sha. It touches the frozen symform.py, so I merge it after v3 54/54, once Form.sha is shown identical over the 18,649 v3 integer keys as well. The 98 cmpps/cmpss FieldDecodeError shapes go to L3a. The vpavgw misclass is L3b's (ud-class must come before noform); w10 fixes it. Bottleneck: farm proving time (8 v3 shards left); the SIMD FP split (w10).
- 22:10Z merged w4 a13af25a8: the farm reads its mode from the queue header; mode=simd runs simdshard.py (simdcheck, 60 s budget, qcache seeded); a shape_sha mismatch is an error record. SIMD non-FP queue: fnv-nx-build l3b-lift/queue/simd-9c97f84b-4479068c2de4, 1,682 rows, N=29, sized by the worst-case rule ceil(rows × 60 s / 3600). Farm tree claude/fnv-l3b-lift-simdfarm (9c97f84b + lead) is being built by w4; farms start after its 0-mismatch check.
- 22:09Z SIMD non-FP farms started on claude/fnv-l3b-lift-simdfarm 0bcc5af13. That tree is merge(w7 9c97f84bc, lead 70c156906); shape_sha matches the queue for all 1,682 rows (0 mismatches, 0 exceptions), and test_pins passes. Queue simd-9c97f84b-4479068c2de4, N=29: farm 19 runs K 1-6, farm 21 (session_01KmtZKUjG4My1CKEESvHbPk) runs 7-17, farm 22 (session_01KmtZKVG5Tr9ctp8xmy8E9w) runs 18-29. That makes 8 farms, the cap.
- 22:12Z v3 shards 49 and 52 (farm 12): 345+345 shapes, proved 336+335, model-gap 9+10, failed 0. 48/54 complete. Still running: 11, 14, 25, 51, 53, 54.
- 22:13Z L3a w13 0dd2ae53f: the 98 cmpps/cmpss shapes now build (imm8 0..7; imm8>7 is SDM-reserved, so those sites are reserved-encoding; w10 counts them). w13 confirmed all 331 KeyError SIMD shapes are ud-class by f_cpufeat.missing: 330 3DNow, 1 vpavgw AVX. Both w13 commits merge after v3 54/54 (symform freeze).
- 22:15Z L3a w12 bf89c7b36 (no absolute paths or env defaults in tools: model_sail.configure is required, shapeprove gets --sail-work/--sail-ir/--isla, the farm script passes them). It touches the frozen model_sail.py, so it merges after v3 54/54 together with w13 88f691c09 and 0dd2ae53f. certbasis.py is tracked on claude/fnv-l3b-lift; w12's base 2c3591efc lacks it because that base predates the L3b merge.
- 22:17Z w13 88f691c09 checked over the 18,649 v3 integer keys: Form.sha changed 0; built 18,643 on both sides with the same 6 failures; canon changed 172 keys (the ite constant-selector fold). Accepted: the fold is a definitional identity, and canon is used only by symform and symcheck, not by any cert key. Key 0f0010's queue shape_sha differs from the tree's (pre-existing); w4 closes the cause in the aggregation.
- 22:18Z the 6 v3 keys with no integer Form (sldt 0f0000, sgdt 0f0100/0f0102, rdtsc 0f31, int cd00/3ecd00) go to the sys+far universe check in w4's aggregation. Rule: every system shape that symbuild rejects is in the sys universe, so a key in neither universe is a hole.
- 22:18Z L3a's f_cpufeat diff against cpuid_feature_bits.tsv: 26 of 30 rows agree. Two defects in my table: (1) 3DNOWEXT, SSE4A, XOP and FMA4 have no row, because the parser reads only APM Table 3-1, not Appendix E, and rev 3.14 predates XOP/FMA4; (2) FXSR is mislabelled leaf 1 EDX25. New worker w14 (session_01KmtZKQXdD3Fu4k9DJoAGj3): pin the current APM vol 3, derive every bit row from Appendix E, fix the FXSR cause with a uniqueness test, and republish. Workers: w4, w10, w14 (3 of 4).
- 22:19Z cause of the 0f0010 (lldt m16) mismatch: the v3 queue was built on prover tree eb1e41a47, and L3a 00068cd10 (privileged set read from the record) moved its Form afterwards. The queue row is the stale side. Closed by rule: shapeprove.py (w4 075feb852) makes a row whose shape_sha differs from the tree's an error record, as simdshard already does; the queue header pins the prover tree, and the farm tree must equal it. After 54/54: the next tree (lead + L3a w13/w12 + 00068cd10) is re-queued by packqueue, and only keys whose shape_sha moved are proved again; everything else is reused by sha.
- 22:21Z SIMD farm shards 7 and 8 of 29: 57/58 proved each, 1 error each (FileNotFoundError lift/rt/rcpref.h, rcp/rsqrt shapes). Cause (rule 2): rcpref.h is generated by L3a's tools/gen_cpuref.py since 1e4a51c2d/00068cd10, but simdcheck and gate_fpexact still open the old file path. w4 fixes both to read the generator's output and adds a test; erroring shards rerun on the fixed tree.
- 22:22Z the 6 keys have no universe hole: all are in the sys universe (the pack's sys family, 145 shapes). rdtsc 0f31 is proved (SDM over the declared timer). sldt 0f0000, sgdt 0f0100/0f0102 and int cd00/3ecd00 are uncertified: no pinned source gives a process's LDTR, the GDT base/limit, or a ring-3 gate at another vector. The KeyErrors were base3-only; the farm tree builds all six.
- 22:23Z SIMD shard 10/29: 58 proved, 0 error (published). Shard 9/29: 57 proved, 1 rcpref error; it goes on the rerun list with 7 and 8. Farm script defect: concurrent shards race on git fetch in the shared clone; w4 puts the fetch under the staging lock.
- 22:25Z simdfarm fixed at 04d2aaaad: ssefp.rcp_tables reads rcpref.h through fnvlift/rtgen.cpuref_dir() (the gen_cpuref.py stage, run once per process). test_generated_readers.py fails on any lift/proof read of a generated rt name. shape_sha matches all 1,682 rows. Rows proved on 0bcc5af13 stay valid: the diff touches only the generated-header read path, and their shape_sha is identical. Farms switch at the next shard boundary and rerun shards 1, 2, 7, 8, 9. SIMD so far: 10, 11, 18, 19 have 58/58 proved each. sdmfeat's include of the generated cpumodel_tab.inc goes to w14.
- 22:26Z SIMD shards 20 and 21 of 29: 58/58 proved each. SIMD clean so far: 10, 11, 18, 19, 20, 21. L3a w13 99b0218fd (with the cmp Forms): Form.sha changed 0 over the 18,649 integer keys. Concrete cmp imm8>7 sites over the 8 images: 0 of 5,703,260.
- 22:27Z SIMD non-FP: shards 1, 2 and 10-23 have 58/58 proved each; 16/29 done, 0 errors. Running: 3-9 and 24-29. L3a's choice of w13 commit is 99b0218fd, not 0dd2ae53f: 0dd2 mis-registered SSE2 cmpsd over string cmpsd. Over the 18,649 integer keys 0 sha changed; over the 3,829 built SIMD keys 0 sha changed. It merges after v3 54/54.
- 22:28Z SIMD non-FP: 26/29 done, all 58/58 proved, 0 errors. Only the reruns of 7, 8 and 9 remain (farm 21). Farms 19 and 22 are done and archived.
- 22:29Z SIMD non-FP queue complete: 29/29 shards, 1,682/1,682 proved, 0 errors (tree simdfarm 0bcc5af13 and 04d2aaaad, subject w7 9c97f84b). SIMD over the 4,290 shapes: proved 1,288 + 1,682 = 2,970, ud-class 330 (331 with vpavgw), w10's FP split 867 (761 FP + 106 timeouts), noform 123, now fixed by L3a w13 (122 built + vpavgw). Farm 21 archived. Open farm defect: resume reused a stale error row from another tree; w4 keys resume on the tree commit.
- 22:30Z L1 789ef219c makes the pack a uses root (admit.json pack = lift/liftsrc), which turns formgate.py red: it still resolves cxx_origin.pack through fetch_externals. New worker w15 (session pending) moves formgate onto gate_build.published_output(cache, root, PACK) and deletes the external path. This blocks cand3. Workers: w4, w10, w14, w15 (4 of 4).
- 22:31Z merged w4 d09b9c39f into the lead:
  - every git command on the shared clone runs under one flock;
  - records carry tree, and treeid.resume keeps only complete, non-error records of the running tree (test_treeid);
  - aggregation takes exactly one complete content per shard, and two different ones are a failure.
  The rcpref reader fix (04d2aaaad) is not applicable to the lead: the lead still has rcpref.h checked in and lacks L3a's rtgen/gen_cpuref. It joins with the post-v3 merge of L3a's line.
- 22:33Z v3 shard 14/54 (farm 18): shapes 346, proved 336, model-gap 10, failed 0, 3,811 s. Farm 18 is done and archived. 49/54 complete. Still running: 11, 25, 51, 53, 54.
- 22:34Z v3 shard 51/54 (farm 12): shapes 345, proved 335, model-gap 10, failed 0. Farm 12 is done and archived. 50/54 complete. Still running: 11 (farm 17), 25 (farm 07), 53, 54 (farm 20).
- 22:35Z v3 shard 11/54 (farm 17): shapes 346, proved 334, model-gap 12, failed 0. Farm 17 is done and archived. 51/54 complete. Still running: 25 (farm 07), 53, 54 (farm 20).
- 22:37Z w14 d55c2367e: the cpuid bit table now comes from APM vol 3 rev 3.39 Appendix E (fnv-nx-build tools/amd-apm cdd079761116). Table 66d8109dfc33 has 764 rows and the 4 missing features. FXSR's cause: a vendor typo in rev 3.14 Table 3-1. Partition: FEMMS (258 sites) moved to reserved-encoding, because rev 3.39 Table D-1 drops it. Decision: pin APM vol 5 and take the union of AMD volumes. Not merged yet: that run used capstone 5.0.7 against the pinned 5.0.9, so it is a measurement; rerun with the pin. The generated-include item is still open.
- 22:38Z w14 4816a5615: sdmfeat compiles cpumodel.c with -I rtgen.cpuref_dir(), and test_generated_readers now covers compiled includes. The branch carries simdfarm 04d2aaaad, i.e. w7's line with its symform change, so it merges after v3 54/54, in this order: L3a line (w7, w13 99b0218fd, w12) → simdfarm → w14. Still open with w14: the APM vol 5 union for FEMMS, and a rerun with the pinned capstone.
- 22:39Z w14 964510be8 done.
  - APM vol 5 rev 3.16 pinned (tools/amd-apm bd63d66d5cc8); vendormap takes the union over every pinned volume, and a conflict between volumes is an error.
  - Partition over 8 images (facts 6944080, 5,704,065 sites, 665 forms, 0 problems): cleared-feature 5,205 (0F 0F 4,890 + FEMMS 258 + SSE3 57), reserved-encoding 92.
  - Feature table: fnv-nx-build l3b-lift/cpuid-feature-bits/964510be8, 764 rows.
  - The capstone pin was met: wheel 5.0.9, digest matches env.json; 5.0.7 was the library's own version string.
  Merges after v3 54/54, last in the order above. L3a w12 takes this table commit.
- 22:40Z v3 shard 54/54 (farm 20): shapes 345, proved 335, model-gap 10, failed 0, 3,905 s. 52/54 complete. Still running: 25 (farm 07), 53 (farm 20).
- 22:44Z v3 shard 25/54 (farm 07): shapes 345, proved 341, model-gap 4, failed 0. Farm 07 is done and archived. 53/54 complete. Only 53 remains (farm 20).
- 22:46Z v3 COMPLETE, 54/54. Shard 53 (farm 20): proved 332, model-gap 12, timeout 1. Farm 20 archived; all v3 farms are done. The freeze on prove.py, model_sail.py, symform.py and certbasis.py lifts once w4's aggregation has run on the v3 tree.
- 22:48Z post-v3 merge trial: simdfarm (w7 9c97f84b) merges into the lead cleanly; w13 99b0218fd then conflicts in 16 files, because it is on L3a base3. I asked L3a for one integrated L3a commit (w7 + w13 + w12 + 00068cd10) to merge, taking L3a's side in L3a-owned files. Waiting on w4's v3 aggregation.
- 22:49Z w14 ffb79d19d: canonical column, 648 rows, one per (leaf, sub, reg, bit), with sdm_name/apm_name and citations. Table() raises unless address → canonical is a function and canonical → address is one-to-one, except the 17 AMD mirrored Fn1/Fn80000001 bits (w14 is confirming they come from the APM text). Partition unchanged: 5,205 / 92.
- 22:50Z w14 137fc92db: the mirror pairs are now derived from the APM's 'Same as CPUID Fn...' text, and unify() errors when one disagrees with the Appendix E rows. There are 18 mirrors, not 17: name pairing had missed PSE36/PSE_36. Table fnv-nx-build l3b-lift/cpuid-feature-bits/137fc92db; partition unchanged at 5,205 / 92. w14 is done; its branch merges after the L3a base3 head (w8).
- 22:51Z L3a's single commit: claude/fnv-l3a-lift-base3 d7c7a822f (w7 9c97f84bc + w13 99b0218fd + w12 bf89c7b36 + cpufeat c02c82812d49 on table /2). Merge held until w8's Form.sha check. L3a defect: on w7's line, segment-register mov/push/pop and mov CRn/DRn fell to h_default = #UD. L3a now handles them by f_sys rows, and w8 makes h_default red for any undecided #UD. Impact on L3b: v3 ran on eb1e41a47, before w7, so it is unaffected. Results produced on the w7 line (simdfarm, sys+far run at 9c97f84b) are re-derived on the merged tree; only keys whose shape_sha moved are re-proved. rt/rcptab.c is reverted to its certified bytes.

### 2026-11-15 23:10Z: merged L3a base3 @ 80832d8b5
- L3a's Form.sha check over INT: 18,645 of 18,649 keys build (+2: cd00 and 3ecd00, 0 lost). Exactly 10 sha changes, all far FF m16:32, from w7's intended rule 9c97f84bc (proved by L3a at that commit and its parent). SIMD: 3,959 of 4,290 build (+130, 0 lost, 0 sha changes).
- Conflicts resolved by rule (worker): L3a's no-environment-default rule for the Sail toolchain inputs (model_sail.configure from --sail-work/--sail-ir/--isla), with every caller and farm script passing them; farm/sail_toolchain.sh is the one place that derives the paths. HEAD's closure/treeid/migrate/publish logic is kept. symform: symins split plus the cmp alias. x86sys.json is taken at L5 ad3af10c5 and pins.json is updated.
- Next: re-certify the 10 far FF keys at their new shas (when the FREEZE lifts), then merge simdfarm and w14, then a packqueue re-queue on the merged tree.
- bottleneck: w4's v3 aggregation (the FREEZE).

### 2026-11-15 23:35Z: jtab-guard certificate (orchestrator, integ 11db7476e)
- Review: blocked, because L3a w13 has not landed the certificate yet (L3a a8dbed24f says R-A is decided and the kind is being implemented). I sent L3a the acceptance checks: same cell with no ebp write and no store between guard and reload; the guard dominates the jmp, with the unsigned ja/jae condition from the opcode (signed only with an index ≥ 0 proof); the index transforms, two-level byte tables included, applied to the extent; T read-only and every entry with a dispatcher entry; recomputed over every certificate.
- bottleneck: w4's v3 aggregation (FREEZE); the jtab-guard commit (L3a w13).

### 2026-11-15 23:45Z: v3 done, FREEZE lifted, lead at 814542a8b
- v3 aggregation (w4): prover tree d09b9c39f, queue 7c3074644653-f3eb5096ad36, 18,649 keys, REQUIRE_ALL=1. Published at fnv-nx-build l3b-lift/certsagg/7c3074644653-f3eb5096ad36/54-6d6b7707b04c; the 54-e192c6eb32f1 branch is a stale-prover run and is ignored.
  - proved 18,186; model-gap 451; timeout 7 (its own class); FAILED(model-defect) 4, all certified by defectspec as rep-prefix-ignored; agree-except-declared 1.
  - far (ea basis, 16 shapes): 14 uncertified (selector other than cs/NULL); 2 (ff5c2c00, ff5cfd00) uncertified because their basis shapes ff742c00 and ff74fd00 were never queued. Rule: every far shape's basis shapes go in the queue.
- FREEZE on prove/model_sail/symform/certbasis is lifted.
- Merged into the lead: L3a base3 80832d8b5 (23af7a758), simdfarm (89b6a07f5: rtgen stays deleted; the generated rt headers are read only through proof/genout.gen_dir), w14 (814542a8b: sdmfeat reads the declared CPU through cpumodel_bits.model_words, the generator's own reader; its direct gcc compile of cpumodel.c is deleted).
- Doing:
  - w4: post-v3 queue on 814542a8b: keys whose sha moved (the 10 far FF keys), the 7 timeouts, new keys and far-basis keys; up to 4 farms.
  - worker: host recipe root for rt/rcptab.c, so that test_simdspec and farm/rcpcert.sh build through recipe.py and their direct gcc calls are deleted.
  - jtab-guard: w13 emitter 159d25924 accepted in shape, with C4 (read-only section, dispatcher entries) required in the certificate. The real certificates are reviewed after w7's memory fix.
- Open: 451 model-gap keys block the pack root (forms ⊆ certified). The class rule is still to be decided; next item. gate_oracle: 10 test files have no oracle entry (census, defectspec, forms_cmpimm, forms_far, forms_immsel, forms_simdclass, forms_ssefp, shardok, stringspec, treeid). genout reads GATE_CACHE from the environment until L1 789ef219c (published_output) is merged.
- bottleneck: the model-gap class rule (L3b); w7's memory fix (L3a), for jtab.

### 2026-11-16 00:20Z: lead at d5da8d769
- Merged: w4 x86sys rule (d68f125e1, required --x86sys; x86sys_sha256 in records and queue header; stale-x86sys class); L1 789ef219c + genout over published_output with explicit cache (46c8a0296, d560f39b6); L1 82ff5eb86 recipe `<root>:<path>` sources and the host roots lift/rcphost, lift/rcpcert, lift/rcpgen (841ed136c, base3 copy af133dc17). Every direct gcc call in lift/ and farm/ is deleted; lift/tools/hostbuild.py is the one way to get a host program. rcptab.c restored to its certified bytes d2a3920e (799f3a4e2).
- Post-v3 re-prove: queue 257a93722adb, 515 rows (fnv-nx-build l3b-lift/queue/7ef692b5b853-d68f125e11f7): 448 moved (v3 model-gap), 53 new, 7 timeout, 4 model-defect, 1 agree-except-declared, 2 ea-basis (queue_basis rule). Farms 1-4 run it on d68f125e1, TIMEOUT 2400: session_01KmtZKSgdjmxZmMJzUMdaXb, session_01KmtZKQyxVKpPFhmLgjYGAx, session_01KmtZKSDBz6cCM5DWzNskf3, session_01KmtZKSxPmy8gMyPEiagxM8. w4 aggregates.
- rcpgen acceptance (rcpgen.exe output byte-equal to d2a3920e): queued on farm 1 after its shard, at base3 b39f131e4.
- gate_oracle at 9bd64e12b: lift/proof has 0 reasons. Whole tree: 67 of 204 test files have a reference; the rest are in contract/link/verify (L1, green on trunk fb9dd044f). SDM references are extracted from the pinned SDM (sdm_pages.py --cites → tests/sdm_oracle_cites.txt); CERTS.md "Oracle rules" 1-3.
- Defect found (d5da8d769, test_forms_immsel over the decoder universe: 688 symbolic-immediate shapes, 321,376 instances): forms_sys.int_ branches in Python on `v in INT_KIND`, so the shape Form of cd00 and its 66/f2/f3 forms differs from the concrete Form at int 3, 4 and 0x2d (12 instances). The class (a Python decision on a symbolic field value) goes to L3a: SV raises SymbolicBranch from __bool__/__eq__/__hash__/__contains__, and int_ becomes an exact mux. Until then those 4 keys are withheld-form-defect at aggregation (w4, as a rule fed from immsel's failure list). For 2/4/6-byte immediates the test checks byte lanes. That is a check, not a proof: the equivalence follows from the no-branch rule once SV enforces it.
- Decision: the trunk claude/fnv-nx-clean is NOT merged into the L3b lead. Trunk fb9dd044f deletes the roots it cut (platform, render, rt/guest, loader, lib, tests; 463 files) and those live on their leads' branches, so a merge here would delete other layers' work (55 conflicts). gate_oracle for L3b roots is judged on the lead's own tree.
- lift/proof tests run bare (37 files): 13 fail, none for x86sys. Environment: certs_build/irdeps/model_patches need the Sail inputs, ehoracle needs pefile, gate_fpexact needs elftools. Open: immsel (the int defect); declared_gen (committed rows sha); forms_ssefp (exit 5); formgate (DATAMODEL.json missing in the checkout); opmap_sdm; rtroot (KeyError 'cc'); simdcheck test_wrong_lane_op_is_refuted (timeout where FAILED is expected); x87spec citation pages. Next worker item.
- bottleneck: farms 1-4 (post-v3 queue); the L3a int_/SV class fix.

## 2026-11-16 00:45Z
- 2.6 integer re-prove (queue 257a93722adb, 515 rows, fnv-nx-build l3b-lift/queue/7ef692b5b853-d68f125e11f7 @5e78d3544): the first 4 farms never ran. Farms 2-4: short SHA ref_not_found. Farm 1: empty checkout. All 4 archived. New farms l3b-int 1-4 started at nx-agent claude/fnv-l3b-lift, shards K/4: session_01KmtZKS324Mh9CWZ3APuCUm, session_01KmtZKUJTJJy6aiVcibniH8, session_01KmtZKSmRUeqtQGLhvG1YkA, session_01KmtZKTRYAYnFcNiHPpfXLr. Farm 4 also runs the rcpgen acceptance check (sha d2a3920e).
- Defect closed, 48ef39ad8: at d68f125 the farms could not load the pinned IR. formgate.ir_path validated the whole forms-gate document, so the liftpack external, which is not declared yet, made the IR unloadable. Fix: load_externals (format, keys, externals, x86ir) for the prover; load_inputs adds the pack keys for the forms gate. Farms rerun at 48ef39ad8. x86sys sha 55964ce7 is unchanged, so the queue header still matches.
- 2.8 lift/proof tests, 451c3d4c5 (worker aef9b4b70f184e145): all 37 files exit 0 when run bare. Not run here, because a declared input is missing from this container: certs_build, model_patches, irdeps (Sail), gate_fpexact (pinned env), opmap_sdm (FNVNX_BUILD). Defects fixed: formgate configures x86sys; forms_sys int_ raises SymbolicBranch on a symbolic vector, so `cd n` has no shape Form; sdm_pages SPECS is the one key source; simdcheck refuted queries no longer read as timeout. Open: the simdcheck tactic, smt only on sat=unknown, keeps the fast path (worker doing). Open: the int vector 0x29 row is undecided, which blocks a `cd n` mux.

## 2026-11-16 02:30Z
- Toolchain on the recipe (L1 rulings: isla is built by a recipe root, no farm-built binaries; no wall clock in a stage; a timeout is never a verdict):
  - lift/proof/isla (ISLA, Z3LIB): fb0f3c211 / 407114cb3. Its input is tools/sail-mirror b4cf9ee3 (tree 1ba45588). It uses the rust debs of env.json (mirror e37c2692). It passes `check` and is not yet built: no pinned machine here.
  - lift/proof/sailmodel (MODEL): the patched model. It goes through the one patch script sail_patches/apply.py, which sail_build.sh also calls. diff -r against the old patch section: identical.
  - The lead takes verify/recipe.py, RECIPE.md, env.json and the verify tests whole from L1 w28 a081cba96 (eec789cf5, 1c81f6c6a). formgate goes through recipe.ext_dir. The lead-only recipe.py commits were sent to L1 to place on w28. 17 of 45 roots are red under the new gen and guest rules; the owners are L3a and L6, routed by L1.
  - simdcheck eb53b3aa7: sat stays the fast path; only an unknown goes to smt.
- Next for L3b: move the prover's wall-clock TIMEOUT to a pinned deterministic bound (z3 rlimit, isla step bound). Today's farm runs are measurements: only `proved` certifies.
- 2.6 integer queue 257a93722adb: shard 1/4 is final, 129 rows: proved 1, model-defect 1, model-gap 126, timeout 1 (3450 s). The v3 model-gap keys stay model-gap. Next: w4 counts the 451 gaps by opcode, and a model patch follows from the pinned ACL2 source. Shards 2-4 are running. SIMD shard 1/4 runs on farm 1 at eb53b3aa7.
- udsail (L3a w13 183e1ebef): rule accepted with 4 conditions. Farm 2 runs it after its shard, as a classifier check, not as an input.

## 2026-11-16 03:25Z (claude/fnv-l3b-lift 472616f5f)
- done (f1db9564f, dc9511205, fc6f17a43, e6ed32b7e, 472616f5f, worker a4fefc3884cbfe194): gate_fpexact check 0 certifies only what is proven.
  - Universe: one source, lift/proof/helpercalls.universe(repo, cache, objects). It reads the Form -> helper calls from the lifter's emitter, plus the fnvnx_ftoi helpers record.
  - Widths: only from the DWARF formal_parameter types of the defining object. Underived is red (helper-width-underived).
  - Narrow (<= 32 bits): red helper-no-exhaustive-cert until lift/proof/fpchost (w13) reports 0 mismatches per helper over 2^32 inputs, with the Form term compiled to C by one op table as the reference.
  - Wide: REMOVE record ASM-329 (owner L3b). Removal: isla over the host x86_64 object after the SSE FP model patch, with the four static conditions of integ e8faa4266. ASM-322 retired, replaced by 329.
  - gate_assumptions findings: 23 at 4865c26a1, 23 at e6ed32b7e (pre-existing red, not raised).
  - Nothing run end to end: the pinned env is absent here. The universe needs the liftpack declared in formgate_inputs.json.
- blocked: lift/rt needs -g for the DWARF widths (asked the L3a lead).
- blocked: w13's compare stage; fpcuniverse.py is to be deleted as a second copy.
- doing (farm 4, session_01KmtZKTRYAYnFcNiHPpfXLr): at 4865c26a1:
  - envcheck with the env.json rust profile (L1 bc9de3d83 -> 04a66bb77);
  - the rcpgen check;
  - the recipe builds of lift/proof/isla and lift/proof/sailmodel (isla debs = the closure of python3 and cargo).
- doing: integer shards 2 and 3 (resuming), SIMD shard 1 (resuming).

## 2026-11-16 03:20Z (claude/fnv-l3b-lift c31ed7f26)
- done (9de00f15d, worker a4fefc3884cbfe194): check 0 under the one-method ruling (integ a172a44b7).
  - Every helper in helpercalls.universe is covered by rcpcert (its defining unit is rcptab.c's object, decided from the symbol table) or by ASM-329; a helper covered by neither is red.
  - fnvl_tab_* are inline in fnvl.h, so they fall under ASM-329. The width derivation is deleted.
  - gate_assumptions findings: 23 = 23.
- done (8abd1ab02, b0b2af12e, c31ed7f26, worker a3994d45035f1f412): toolchain roots on the recipe.
  - lift/rcpgen GREEN (farm 4): rcptab.c sha256 d2a3920e...58f5, equal to the committed file.
  - lift/proof/sailmodel GREEN (farm 4): MODEL sha256 ea88b550d8a7f88d90c56ca8eb4d3a202a203e592353c5218373b5d824a42f27.
  - Fixes: exec /usr/bin/python3.12; debs by closure; the render/total unused import dropped; the isla vendor layout; the patch-script modules mounted as inputs and loaded by SourceFileLoader.
- blocked (owner L1): lift/proof/isla red. verify/sandbox.py:110-118 returns ENOSYS for getrandom; OpenSSL in cargo's libgit2 needs it. The rule (constant stream for every entropy interface) was sent to w28.
- open (owner L1): recipe.py _deb_tree copies symlink targets from the host (alternatives), not from the deb.
- doing: integer shards 2 and 3, SIMD shard 1.

## 2026-11-16 04:10Z (claude/fnv-l3b-lift 60525f7a6)
- done (farm 4, session_01KmtZKTRYAYnFcNiHPpfXLr): the three toolchain roots are green on the recipe.
  - lift/proof/isla GREEN at 60525f7a6 (288 s cold): ISLA sha256 9d5432cdeed0365a882d22e90625e565d64f06b9cfe428f7f665c990b775b5e5, Z3LIB sha256 63f20f0e05ca7eda917ebac77cfdab7530c88bf70d553b159950794c10fa4455. `--help` runs against Z3LIB.
  - lift/proof/sailmodel GREEN at 103d1f743: MODEL sha256 ea88b550...2f27.
  - lift/rcpgen GREEN at 8abd1ab02: rcptab.c sha256 d2a3920e...58f5 = committed.
  - Fixes taken from L1: debs are each deb's own data (3de3eee4e, 08109b9ce) and getrandom gets the constant stream (c071d7e1b).
  - Our fixes: TMPDIR=/scratch; linker, CC and CXX = the root's host compiler.
- done (farms 1-4): integer re-prove of queue 257a93722adb, 515 rows at 48ef39ad8. The farm counts are proved 2, model-gap 501, model-defect 4, timeout 7, agree-except-declared 1. w4 is aggregating (REQUIRE_ALL, WITHHELD) and writing the census by opcode; the next step is the ACL2-derived model patch.
- red (owner L3a w13): udsail at 183e1ebef. All 6 baddecode sequences are GAP: isla returns no path on the ctrs vector spelling. w13 is to read the IR via formgate.ir_path (8b711fba) and rerun once.
- doing: SIMD shards 1 and 2 (farms 1 and 3).
- next: the prover as a recipe stage over ISLA/MODEL, with a pinned deterministic bound in place of the TIMEOUT wall clock.

## 2026-11-16 04:35Z (claude/fnv-l3b-lift 38b25ee43)
- done (7fc353a5e, 6ca68da31, 38b25ee43, worker a263326f7dfe45d17): no clock decides any prover verdict.
  - z3 rlimit is pinned in lift/proof/bounds.py (Z3_RLIMIT = 2^32-1, which bounds only non-termination; REFINE 2^24 only splits the bisection).
  - The class `timeout` becomes `bound` (red). isla runs with no clock, and a run that never ends is killed with its stage (no verdict).
  - The SIMD path is the same.
  - lift/proof/tests: 39 files, 216 ran, 39 skipped, 0 failed.
- done (825bf19d6, worker a3994d45035f1f412): the x86ir pin lives only in lift/proof/sailmodel/build.json; formgate.ir_file reads it through the recipe. external_of and include_of taken from L1 (987dbde31, 94058b16b).
- done (w4, efef73cbe): integer aggregation of the 4 new shards, 3 certificates. Model gaps: 501, of which 444 are inside the pack universe (SHAPES union l3b-lift/shapes @77524556, 26,500 shapes) and 368 of those are unimplemented in x86isa.
  - v3 migration: stamped 0 of 18,187 (prover_sha differs). All go to one re-prove on the recipe stage.
- blocked (owner L1 w28): foreach reading a stage-output table. The prover queue is derived from the exe, so it cannot be a tracked file. It blocks the prover root and the re-prove.
- asked (L3a lead): reachability of the 368 unimplemented-opcode keys (arpl, bound, ins/outs, mov sreg, call far). If unreachable, the code-range rule removes them; otherwise the orchestrator rules on the SDM source.
- doing: farm 2 runs L3a facts jobs (w7, w13); SIMD shards 1 and 2 (farms 1 and 3, prover eb53b3aa7, wall clock: a measurement).

### 2026-11-16 04:40Z: reachability of the 368 model-gap keys; prover identity
- Reachability census (w7): fnv-nx-build l3a-lift/udreach/f5d49d6fa, facts @f5d49d6fa. Covers the exe, atimgpud, binkw32, libvorbis, libvorbisfile and gdffalloutnv; d3dx9_31/38 pending.
  - 333 of the 368 keys have no site in the entry ranges.
  - 1,667 sites of 35 keys are in the ranges.
  - 87 sites are under a proven root: rdtsc 3, int3 5, xlat 1, mov r/m16 from a segment register (66 8c) 78.
  - 1,580 sites have only a candidate root. They are owned by L3a's code-range rule.
- The source of semantics for the 4 proven-reachable classes (OWNER 10 SDM spec) is asked of the orchestrator. Blocked: orchestrator ruling.
- prover_sha is the import closure of the prover modules (w4 73445881c, 25 files). Under review, not taken: relative imports, the importlib load in x87spec, data files, and the catch-all in stale_reason are sent back to w4.
- The re-prove queue (w4) has 18,697 rows. It is not published and is rebuilt as a recipe stage output.
  - Its universe is taken from the facts stage's shapes_<img>.tsv.
  - Blocked: L1 w28, foreach over a stage-output table.

### 2026-11-16 05:00Z: OWNER 10 system specs (orchestrator integ b4aad1930)
- The 35 model-gap keys in the code universe (1,667 sites, facts @f5d49d6fa; d3dx9 pending), measured at bec2c4e74 (w-sys bb4a0cf4d merged):
  - 33 are proved by sysspec, sysspec_gap, sysspec_int and stringspec.
  - 2 are uncertified:
    - cd00 int n: 255 of 256 vectors; row int_ring3.excluded.41 is undecided.
    - ea ljmp far: the descriptor rows (sgdt) are undecided.
    - Blocked: L5 rows, sent to the L5 lead at 04:50Z.
- Form defect: mov r/m16 and r32 from Sreg read a free variable instead of sel_<reg>. Fixed in forms_move. L3a relifts through shape_sha.
  - The class rule (every free variable of a Form is a declared DSL input) is with worker a2633, together with the stringspec prefix scan.
- lift/proof/tests at bec2c4e74, every tests/*.py by exit status: 46 files, 45 exit 0. The failing file is fpc_host.py, which base3 deletes (5297ac9d0); it is deleted in the base3 merge.
- prover_sha is the import closure plus data files, with exclusions derived from the recipe roots: w4 c2c3d9e04, merged in c7400a5fd.
- shapeprove root (w4): steps closure → queue → prove (foreach) → certs.
  - It is being re-merged with base3 30030c1e8 hunk by hunk; the first merge, 156930ac2, dropped base3 work.
  - Then the 3-row build on farm 4.

- 2026-11-16: density rule 3 projection lemma and liveness certificate written: lift/proof/FLAGLIVE.md (Lemma 1, soundness, P1-P3, gate flaglive). Next: L3b check of rules 1-2 by shape_sha + formgate when L3a lands them.
- 2026-11-16: sys3 merged (w-sys3 0b0a4901a -> 82a488a89). Sreg write class: Form.sel effect, MOV/POP/PUSH Sreg Forms from x86sys MOV_TO_SREG (L5 w35 894e5a68); seg family 52 keys: 45 proved, 7 uncertified (prefix 26/36/67 claim -> sys4). Form build over the universe: 23,833 ok, 2,664 KeyError, 3 SymbolicBranch, 0 UndecidedRow. int n cd00 252/256 vectors proved, 4 OutsideDomain. far jmp/call all classes proved over jmp_far, 115 undecided GDT indexes OutsideDomain. Tests: lift/proof/tests 43 files, 43 exit 0 at 82a488a89. Doing: sys4 prefix-scope claim (a263326f7dfe45d17).
- 2026-11-16: sys4 merged (8e97a6d8b -> 34c41822d). Segment-override prefixes claimed by SDM 1:3.7.4 scope from Form.access (no mnemonic list): 4 keys uncertified->proved (2607 261e 3606 3607); seg family 49/52 proved. 0x67 on non-memory push/pop is SDM-reserved (2A:2.1.1): 3 keys (6707 671e 671f) stay uncertified, no Form; recheck after L3a value-occurrence universe, report to orchestrator if reachable. Universe build unchanged 23,833/2,664/3/0. Tests 43 files, 0 fail (worker run at 8e97a6d8b). Doing: sys5 gs row (L5 4ec8cd2c1).
- 2026-11-16: sys5 merged (405ffb6d1). gs read from x86sys selectors.gs (L5 4ec8cd2c1, =0); null-seg access is a Form fault from x86sys NULL_SEG (333 Forms carry it). Universe build 23,833/2,664/3/0 unchanged; tests 44 files 0 fail (worker). Rules 1+2 (L3a R12 e0e078d1e) checked Form-neutral: 16,032 (A corrected) + 26,500 (B) keys, 0 sha/outcome change. FLAGLIVE: DEF0 kills ef07f250e, P2 suspension-point rule 58ce47b65. X87FORMS design 445074634 (x87 Forms generated from x87spec). Doing: x87form (a4fefc3884cbfe194), sys6 store-through-code-segment class (a263326f7dfe45d17).
- 2026-11-16: intsys merged (0334d8b8d): legacy BCD/salc/les/lds/sldt/retf Forms proved by SDM spec; CLEARED_UD shift /6 and F6/F7 /1 (opmap). cpufeat merged (5c43afbb8): cleared-feature #UD Forms from CPUREF (3DNow 86/330, AVX 1, SSE3 fisttp 10 keys in A/B), MPX hint space NOP (SDM 1:E.4.1). Form build: A 16,032 keys 14,499 built / 1,524 KeyError / 7 UndecidedRow; B 26,500: 24,325 / 2,165. 7 keys wait on L5 rows (w38 9659524: lar_lsl, iret decided; sgdt.base, iret NT undecided -> reach question to L3a). x87 slice 1 (w-x87f 65c90edb0): 21/92 classes, 125/1,421 keys proved; slice 1b doing. Tests: 47 files per file, 46 exit 0, test_forms_simdclass red at base (worker run, both inputs set).
- 2026-11-16: sys6/sys6b/sys7 merged (w-sys3 1003f82e0, merge 13849b5c7).
  - Segment Forms are tied to the x86sys descriptors in the Sail model.
  - A store through a code-descriptor selector is #GP, by SDM 3A:3.4.5.1. test_codestore_universe proves it over every Form whose access set names a code store: 6,254 proved, 0 differ, 0 missing, in universe B (26,500 keys). By register: ds 5,644, es 33, cs 71, gs 289, fs 217.
  - Form build over B, worker run at 0334d8b8d and at the merge: 23,983 ok, 2,507 KeyError, 7 UndecidedRow, 3 SymbolicBranch. This differs from the 24,325/2,165 recorded at 0334d8b8d above. The inputs of the two runs differ; to be settled by one recipe run.
  - Tests at the merge, per file: 46 of 47 exit 0. test_forms_simdclass is red at base: aaa…salc/retf/iret/les/lds are in both Forms and L3a hand handlers; worker a3994d45035f1f412 owns it.
  - The sys6b prover smoke keys go to the recipe re-prove (w4 shapeprove).
