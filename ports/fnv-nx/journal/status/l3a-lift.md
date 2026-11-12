# L3a LIFT status

Lead: Opus 5.5, branch `claude/fnv-l3a-lift`. Handoff read: 422c3d39 (`journal/inbox/l3a-lift-handoff-opus.md`).

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | Lifted pack as a product root (merged de49b884: admit lift/pack + lift/tools [pure, selftests], build.json, one flag set PACK_CXX_FLAGS shared by recipe and tests, no -fpermissive/-w): LIFTGEN at lifter 7b2faae2 (= 160438e lift tree + the trunk env.json; the 160438e env.json mismatched the declared environment on /usr/include) (exe 64, d3dx9_38 32, binkw32/libvorbis/libvorbisfile 1), pack tree, `lift/pack/build.json` with the msvcmodel external (tools/gcc-ilp32-msvcmodel b4057704, tree b60eafe7a5b0...), admit.json entry, `recipe.py build` x2 cold + warm in GATE_CACHE | w1 session_01KmtZKSJcRwWUskQJfty9T7, branch claude/fnv-l3a-lift-w1 | doing. Dry run at 7b2faae276b2: LIFTGEN 64+32+1+1+1 shards, 0 failed. PACKGEN fnv-nx-build l3a-lift/pack/7b2faae276b2 7ef692b5: images 5, universe [101590, 38853, 988, 984, 685], entries 143100, imports 1549 (needed 426, blocked 0), units 4098, text 758 781 595 bytes, forms 1098 (certified 0: measurement build), gate_liftgen green. tree_sha256 728f4303...; build.json a8b6191b (msvcmodel external). Recipe check valid (admit adds root lift/tools, class tool). Cold build on trunk 08f6ea30: BLOCKED by L1, EMLINK. The unitview name-complete PP views hard-link the placeholder about 4100 times per unit, O(units^2) for 4098 units, and hit the ext4 limit of 65000 links in 55 s; nothing compiled. The OSError also escapes as rc=1 with no verdict. L1 w14 (session_01KmtZKS82vxSbxtKufqbp4i) fixes it by a shared placeholder base plus per-unit read-only bind mounts of the closure, O(names) once plus O(closure) per unit, with store errors as a red verdict; w1 retests. Exe entries excluded from the pack by any table: 0 (pack entries 143 100 = sum of universes; no crt_symbols input). Product relift waits for w3's batch and the L1 w18 modules.tsv (GalaxyWrp; bases may move); w1 03:43: cold 1 on L1 w14 2d8176ae runs with no EMLINK (about 8 217 stage keys, max links < 1 000, 3 382 s and running); cold 2 and warm follow. The dry-run pack has no shapes.tsv (predates 8d6de5c9); the relift pack will. TRUNK ADMISSION BLOCKED: admit rule 3, since 13 REQUIRED_PRODUCT gates are missing in trunk gates.json, and the lifter tree (lift/tools, fnvlift, rt, proof) is not on the trunk. Both are with the orchestrator |
| 2 | liftall.py only generates C++ | lead | done: liftall.py is deleted (73a708c6); liftgen.py calls no compiler. Stale comments naming liftall.py in rt/enter.c, rt/msvcrt_ci.c, rt/rt.c go with task 1's follow-up |
| 3 | Gates B/F/align on the whole pack (tools/gate_order.py over every object of the recipe build plus the runtime objects) | - | blocked by task 1 (pack objects) and task 3a |
| 3a | Runtime `lift/rt` as a product root (orchestrator: L3a owns it; L3b w2 b4d9c61a is ahead: green cold x2 + warm, 11 objects, determinism and objstub green; w3 merges b4d9c61a with toolchain/order and the fnvnx_order plugin tests reverted to the trunk (L2 w4 owns them), plus L3b w3 e0b4abbe (form A deleted, explicit RMW in seh/msvcrt_eh; reviewed, accepted); stale fnvnx_f2i attribute closed by rule: -Werror=attributes in every guest root (lift/rt now, recipe.py requirement asked of L1); linkstrict 16 undefined) (msvcmodel + plugins, cxx_origin native, on the L1 w4 recipe machinery), build x2 cold + warm, gate_order on the rt objects | w3 session_01KmtZKSV4DsNmobaEQ2Fzvq, branch claude/fnv-l3a-lift-w3 | blocked by L2: fnvnx_order "cannot fence after a block-ending statement" (enter.cc:100, longjmp operand); L2 accepted, rule ORDER-CONT (acquire fence at every resumption point) in L2 w4; L3a confirmed lifted frames need no resumption fences (each lifted access self-ordered, nonlocal entries land at native rt receivers); L1 asked for a recipe that judges every unit. Cold build on trunk d7fac9b5: 5/11 units built (C), 6 C++ units unjudged |
| 4 | ELF: link of all lifted roots plus platform, L1 and L5 gates | - | blocked by tasks 1, 3 and the platform root (L5, L1) |
| 5 | Gate D (eh): rules R1 ThrowInfo through caller arguments (interprocedural, address-taken = red), R2 callee-saved registers proven per callee (removes the REMOVE assumption), R3 SEH helper scope argument by dataflow (replaces the `helpers` heuristic), R4 trylevel/table length; gate_eh.py + test on the new facts; facts rerun; gate on the 160438e5 pack | w2 session_01KmtZKSUoqsFSfuD18vvg8X, branch claude/fnv-l3a-lift-w2 | doing; rules amended after L3b review: (a) fs:[0] handler values, (b) ThrowInfo by cone dataflow, (c) FuncInfo in eax at the frame-handler jmp, (d) scope length = 1 + max constant trylevel; byte windows deleted, an unresolved site is red; split agreed: L3a coverage, L3b dispatch conformance against Wine traces. w2 4f59ac9c/549f289c: ehflow.py dataflow, byte windows gone, gate_eh on the new schema (24 tests). libvorbis 35 v4 tables (the false cookie table removed by rule), binkw32 9. Open: __SEH_prolog4/__EH_epilog4 pairs. Rule given: transformer summaries plus save-slot persistence, by a code check and ASM-006. Until then those sites stay red: libvorbis 18, binkw32 4. Prolog-pair rule done (ehflow_test 8 cases). R2 proven ebx/esi/edi/ebp: libvorbis 311/288/289/288 of 984 entries, binkw32 471/440/440/446 of 988. Still red: indirect calls through GetProcAddress-decoded pointers, until L1 resolves the dynamic-name sites (libvorbis 85 entries, binkw32 115). The module engine lacks jump-table edges; w2 is closing that by the guard rule. gate_eh port f9642480 goes to w6 |
| 5b | Jump-table edges (jtab.py), one rule: a value-interval domain mod 2^32 with branch refinement, joins over all closure predecessors and widening. It replaces the pattern guards and the exe universe.table() fallback. The basis is closure completeness, not ASM-006 | w2 session_01KmtZKSUoqsFSfuD18vvg8X | doing; jtab publishes the extents in the facts, and liftgen tab_tails reads them; the universe fragment split (exe 22, DLLs 77 red) goes too. Pattern version a7c1eae9: libvorbis 39 sites, 9 resolved / 30 red; binkw32 46, 12 / 34 (red = CRT memcpy-family tails); exit 142->87, 189->120; liftgen side (core.tab_tails reads jtab_tables, TAB_BIAS gone): w3 1292383f, lands with w2; jtab with w3's cases at 6dc5d47a (18 cases pass); the pack manifest records its generation inputs: w3 1f36351f |
| 7 | Galaxy: not lifted (owner order, trunk CLAUDE.md 9e5856f: declared-machine module, platform bodies; GalaxyWrp is platform by bridge rows) | - | closed: galaxy packs and the codemap analysis dropped; w4 reassigned to item 17 |
| 18 | Closure completeness for code addresses a program computes from its own pc. Census (w4, facts bba4826 / cc3380e): reached `call $+5` sites are 0 in all 5 lifted images (exe 970 byte matches, all mid-instruction; d3dx9_38 33, binkw32 0, libvorbis 3, libvorbisfile 0), and the harness finds 23 in galaxy. That census does not close the class. Rule: in universe.py/modlift.py, data uses of call-pushed return addresses and loads of FPU-environment FIP/FDP are typed sources; a control-transfer consumer must be a universe entry, a memory operand is named data, an escape is red | w4 | doing: pcsrc.py (w4 88e7e06a, 15 tests), facts cc3380eb. RED: binkw32 136, libvorbis 238, libvorbisfile 154. Most come from entry modelling (block entries given an ra slot); fixed by rule, with block entries in their predecessors' context, unseen predecessors meaning possibly-ra, and scratch registers dead only if every caller writes them first. exe, d3dx9_38 and galaxy pending |
| 19 | Facts for the image set of L1 w18 (l1-contract/mp4 0772c61). New: atimgpud 01410000, d3dx9_31 014b0000, gdffalloutnv 01af0000. Rebased: libvorbis 01ce0000, libvorbisfile 01e20000. Not lifted: galaxy, galaxywrp, ssce5432 | w3 session_01KmtZKSV4DsNmobaEQ2Fzvq | done, fnv-nx-build l3a-lift/facts bc9f12d7. Codemap green: atimgpud 660 entries / 56 778 reached bytes, gdffalloutnv 322 / 24 204, libvorbis 988 / 158 234, libvorbisfile 696 / 73 338. d3dx9_31: 18 945 / 2 106 100, codemap RED (270 undecodable runs, 62 707 bytes) -> item 22. Image set (L1 w18, 02:51Z): lifted are exe, d3dx9_38, binkw32, libvorbis, libvorbisfile and atimgpud (LoadLibraryA constant at exe 0x4db2b8). gdffalloutnv is out. d3dx9_31 is on hold while 12 dynamic LoadLibrary sites are open. The bases are final only after w18 sends them, and the relift happens at those bases only |
| 17 | G4 (L3b test plan): one dispatcher generator, lift/tools/dispgen.py (entries file -> disp.S), a recipe generated-stage tool; liftgen --finish calls it; byte-equal on pack 7b2faae276b2. G7 answered: test driver uses fnvnx_lift_attach + fnvnx_guest_invoke (rt/enter.c), no new symbol | w4 session_01KmtZKRAqKi3LXmngGKy2eJ, branch claude/fnv-l3a-lift-w4 | done, merged addd8828 (w4 4f583ac6): disp.S byte-equal on pack 7b2faae276b2 (144 652 entry lines, 524 288 slots), 12 tests; README spelling against RECIPE.md follows |
| 16 | Gates order (B), eh (D), addr32 (F) in trunk gates.json (universe 0 green, rules per VERDICT.md, selftests); tool root lift/gates | w6 session_01KmtZKR34WmRCfdDoE7ajAt, branch claude/fnv-l3a-lift-gates (trunk-based) | done 9abecfb, merged cca6a023: L1 green (26 gates, 229.8 s, not over budget); order/eh/addr32 green at universe 0 (no product root yet); selftests 55/55, oracle 55/55, pure 37/37, admit 431/431. lift/gates is now the one home of gate_order.py and gate_eh.py. Open: the runtime object comes from the image's link.roots (rule given), and the EH facts layout. To trunk via the orchestrator and L1. w5 358dc7ec removes the ASM-043/045 citations (rule (a) from the Form's writes); being cherry-picked, then the verdict rerun, then MERGER. Head 0a28d38c (ASM-043/045 removed) is with MERGER (orchestrator, 03:17); verdict at 0be860f47: L1 green (1038fd56, 262.4 s, 26 gates, not over budget), sent to MERGER; L1 has MERGER take lift-gates (ASM-200..202) before L3b's unit |
| 8 | A4 LOCK-SPLIT deleted: the misaligned locked path stops the world | w3 (rt/fnvl.h, in item 13's batch); L5 has fnvl_host_world_stop/resume (claude/fnv-l5-platform 112c04ce) | done in ae2438b6: the misaligned locked path calls fnvl_host_world_stop/resume (L5), fnvnx_split_lock deleted; reentrant stop sent to L5 as a requirement |
| 9 | ASSUMPTIONS records A1 = ASM-001 for compiler output, ASM-200 (.S, REMOVE by gate_order), ASM-201 A2 (REMOVE, loader layout, L4+L5), ASM-202 A3 (REMOVE, fnvnx-built/1 + object rule); records in verify/ASSUMPTIONS.json in the same merge | w5 | doing (accepted by L1 22:43Z) |
| 10 | gate_order: rows() for L1; per-object check of no write to x19..x27 outside the guest-register rule and no jump tables; .S callee-saved check; ORDERING.md A1/A3/A4 | w5 session_01KmtZKTakZKNZHndJajh1Qf, branch claude/fnv-l3a-lift-w5 | done: ported into lift/gates as w5 198e283c (13 tests, pure green), cherry-picked by w6 into the gates branch; L3a's ASM-200..202 go to L1 w10's registry, the copies are not taken |
| 11 | pure-gate violations in L3a files (104 in lift, by L3b's count) and test doubles (instest/mmxtest rt.S, sehcodes, imptest/msvcrttest native.c) to 0 | w3 session_01KmtZKSV4DsNmobaEQ2Fzvq, branch claude/fnv-l3a-lift-w3-tests | doing. Before (1292383f): 18 double definitions in 4 files (instest rt.S, sehcodes, imptest/msvcrttest NATIVE); puregate over lift/test 78 in 19 of 24 files. lift/test -> lift/tests (the gate's test class) waits for L1. fnvl_call/fnvl_trace are not product symbols (only the trace variant and a test override); the trace is a machine observation (L4 rule, L4 item 13: boot.py --watch-log plus the kind-2 records of .fnvl.sites); -DFNVL_TRACE, TRACE/FSWV and the sinks are deleted together with the reader switch (w3); moved to w8 (session_01KmtZKR4JRJLXpi1LcSzp9b): remaining doubles in citest, imptest, msvcrttest and instest, counted with L1's mechanical check; w3 keeps the fpfacts.ASSUME injections; pure-gate violations in the L3a lift tree: w5 (164 over all of lift; the proof/ share to L3b); w8 0b8493df: test doubles 28 -> 0 in 5 lift/test files (regex count, L1 tool pending), tests BLOCKED until the platform links. Returned: not run (py_compile only), w32_raise named from a comment, rdtsc check weakened. Lost coverage goes to L4's shared model |
| 6 | Gate order host classes (orchestrator 22:41Z): CPU = x27 never written + offset inside sizeof(struct cpu); IMAGE only into read-only sections (no SHF_WRITE), else guest | w5 | doing |
| 12 | shapes.tsv in the pack (liftgen shard + finish, shape_key of lift/proof), for L3b's formgate; then relift + repack | w1 | merged 8d6de5c9 (review: shape_key/family imported, finish fails on a missing shapes_K or a key with two families); the product relift waits for item 13's batch (every unit changes) |
| 13 | SEH state, one lifter/prelude batch: table-driven ctx_capture/ctx_restore (restore = inverse, reads ContextFlags back; ContextFlags as Wine 9.0 signal_i386.c sets them), C0/C2/C3 and EFlags flushed at every explicit raise site and reloaded on continue, dead struct cpu fields st/ftag deleted, misaligned locked path under fnvl_world_stop/resume | w3 | done, merged ae2438b6 (w3 8bf82e32 + 086326b4): table-driven CONTEXT capture and restore with ContextFlags as Wine 9.0 sets them; EFLAGS and C0/C2/C3 flushed at the raise emitters (a raw fnvl_raise from a lifted unit does not compile); dead st/ftag deleted, so ra is at 248 and sizeof is 488 (relift needed); int3, int 2d and bad-selector restore exactly as Wine 9.0 (cited); MXCSR_MASK from the reference cpuid leaf 1 (SDM vol. 1 11.6.6). Nothing compiled yet: blocked by the L2 plugin commit and the L1 EMLINK fix |
| 15 | Asynchronous fault delivery at an arbitrary guest access: recovery of the lifter's live state (flags, x87 C bits) at the faulting pc | lead | open class, reported to the orchestrator |
| 14 | Undefined symbols of lift/rt (16 by L3b's census): owner per symbol by contract (OS/kernel service = L5, guest state only = rt), agreed with L5 | lead + w3 (the exact set from objects.json) | L5 (platform ce3b1faf) confirms all 6 rt→platform symbols; 7-9 (dispatcher table) are the pack's. Rule: one declaration per symbol, so rt includes the platform's declaring headers, waiting on L5's fnvl_host.h and L1's spelling for a cross-root include. The exact list comes from objects.json at the first rt build |

## Global numbers
| what | number | commit |
|---|---|---|
| exe universe | 101 590 entries (l3a-lift/facts bba4826) | facts bba4826 |
| module universes | d3dx9_38 38 853, binkw32 988, libvorbis 984, libvorbisfile 685 | facts bba4826 |
| complete R9 source packs | af437959 (exe 64/64 + 4 modules); 160438e: not lifted yet | - |
| pack trees / built objects of an R9 head | 0 / 0 | - |
| gate order/addrwrap/align on the whole pack | not measured (only the 599-unit smoke pack, which is no result) | - |
| 20 | Lifter hygiene (L3b dslemit review): set_emit instead of FNVL_EMIT, puregate 0 on fnvlift and tools (z3 on EXTERNAL under ASM-009), TAB_BIAS replaced by the bounds the dispatch guard proves | w3 | d6c4c948 reviewed; split into C1 and C2. C1 done: 82b45c2c, on the lead as 3c0a0821 (PRELUDE as an explicit function + puregate for __init__/core/f_dsl, which breaks the w2g import cycle) is critical, to L3b. C2 done: cddbeebb, on the lead as 7788747f: set_emit, puregate over lift/ 143->133 (fnvlift+tools 14->4: z3, which waits for L1 EXTERNAL). The tabguard analysis is held as a duplicate of w2's jtab; one analysis on the facts side |
| 21 | Undefined flags carry no value; a liveness gate over every image is red at any read reached by an undefined value (17 DSL vs hand pairs agree by construction) | w5 | steps 0-3 done 358dc7ec (Form-only flag sets, the rotate OF rule, interprocedural entry). 8 images: 437 408 reads, 16 807 red, 474 742 no-form insts (item 23 closes those); exe 675 red. Steps 4 (pushf bit dataflow) and 7 (wiring) are doing; steps 4 and 7 done (w5 5b308371 pushf/lahf mask dataflow, b7b04d53 gate `flags` wired, branch w5 only): 8 images, 163 042 entries, 5 081 533 insns, 437 408 flag reads, red 16 787 (jcc 15 875, carry 437, pushf 347, other 120, setcc 7, cmov 1), no-form 474 720, undecodable 3 722; exe red 655. Held off the trunk (orchestrator asked). Step 8 doing: red split by cause (no-form, the old ASM-L3B-05 record, entry state, other); orchestrator 03:28: lands like order/eh/addr32 (judges admitted roots, trunk 0/0 green), REQUIRED for the lifted-pack class in gate_admit (L1), recount on the w18 set of 6 images; w5 133b9c92 (fix 81883a8f: entries with in-state 0 were treated as unreached, so all earlier counts are superseded). Per admitted lifted root; no root = 0/0 green. 6 images: entries 143 775, insns 5 868 370, flag reads 481 249, red 12 336 = (i) no-form 7 988 + (ii) the old ASM-L3B-05 record 1 461 (9 pairs) + (iii) entry state 2 832 + (iv) after a call into data 55 (item 22 class); no-form insns 571 528; undecodable 4 382. It lands with the lift sources as the pack root; w5 2f4572b7: flags as verify/gates.d/flags.json (w10 format) on w10 6bcd7d2; w6 moves order/eh/addr32 to gates.d and restores the VERDICT paragraphs; tests to be rerun in the declared env |
| 22 | Codemap of d3dx9_31: 270 runs, 62 707 B. w7 measured the reasons: undecodable 262 / 43 609 B, reloc-not-operand 5 / 17 836 B, no-end 3 / 1 262 B. Rule: a byte is code iff proven flow reaches it; under a complete closure every other byte is data. With the closure open the run is red "closure-open" and the unresolved indirect sites are listed. A closure-proven entry whose first byte does not decode gets a #UD body. Byte-shape classes were rejected | w7 session_01KmtZKV5F6B2PqGD9piX26f | (b) done e9124b3: unent 0 on all six (R3 holds); residue red s4-unchecked: atimgpud 29 984 B, binkw32 282, libvorbis 6 049, libvorbisfile 882, d3dx9_38 164 572. 22c doing: the exe codemap.py sweep residual is replaced by the one rule. CS-store: 69 store shapes fault (#GP->AV), 24 CS loads unchanged; Wine 9.0 citation being checked against the source; 22c accepted (w7 4a5e4ba): tools/residue.py is the one residual rule for modlift and codemap, and the sweep and universe deadops are deleted. Exe: unent 0, fixups 351 931 (67 370 code targets, all entries), entries 101 590, text 12 440 971 B, residue 14 616 B in 2 125 runs, red s4-unchecked until w4. CS-store Wine 9.0 citation verified (dlls/ntdll/unix/signal_i386.c segv_handler); w4 2716ea68 (pcsrc s2/s4, facts cc3380eb, 27 tests; 5 of 6 images, NOT global: atimgpud unmeasured because w4 missed the bc9f12d7 facts, measured in the rerun): s4 red 0 on exe, d3dx9_38, binkw32, libvorbis, libvorbisfile; ra/pc red, proven entries: exe 165, binkw32 18, libvorbis 59, libvorbisfile 56, d3dx9_38 169 (plus candidate entries 42/37/41/9 696). Ordered: entry set = residue.py only, no step cap, callee args joined over callers (unknown caller = possibly code), escaping s4 values red unless followed, a chkstk summary from its code, atimgpud from the bc9f12d7 facts; MERGED w7 55fd7035 and 1de9bdba (ddbeef4a): LK in the DSL, CS-store guard, residue.py, sources.tsv (7 rows per image plus combined; paths to L1 w14). Measured: r3_reloc/export/tls/entry/eh closed, pcsrc and no_s4_arith open until w4's files. The modlift ordering (pcsrc before residue) goes to w4's rebase; w4 63cabce1 (facts bc9f12d, atimgpud from pinned gamedata/exe 0948da1): no step cap, the codemap entry set, args joined over callers, escapes red. s4 red: binkw32 1 318, libvorbis 970, libvorbisfile 782, atimgpud 512 (all escapes: data-pointer arithmetic on args); s4 reaching a transfer target: 0 on all four; exe and d3dx9_38 running. Laundering through memory has NO closing rule yet: reported to the orchestrator, proposed rule = whole-program points-to over abstract objects. no_s4_arith stays OPEN, so the closure_key certificates are measurement-only; OPEN (orchestrator 03:41): s4 laundering through memory. Closing rule accepted: whole-program points-to (static words per R3, stack per frame, heap per allocation site from L1 w18's fact file). Owner L3a, scheduled after the first boot. Escapes at 63cabce1: binkw32 1 318, libvorbis 970, libvorbisfile 782, atimgpud 512; exe 9 868 at b4de8542 (passed 9 067, returned 373, stored 428); d3dx9_38 pending. no_s4_arith OPEN, so the certificates are measurement-only. w4 next: an affine esp domain for chkstk |
| 23 | DSL coverage, plan of record: int (segment 232, 16-bit form variables 190, 3 asserts), then sys 385, x87 2145, simd 4344 (L3b at shapes 77524556: 7 136 of 26 500 not covered) | w7 session_01KmtZKV5F6B2PqGD9piX26f, branch claude/fnv-l3a-lift-w7 | int done: e6edb411, merged. Over all 26 500 shapes, emit errors went 425 -> 0; emitted 18 939 -> 19 364; uncovered 7 136 (x87/simd/sys, unchanged). Unicorn differential 936/936 equal. Next is the sys class. The real compile check is w1's recipe pack build. sys stage 1 bab6030: emitted 19 682, uncovered 6 818, emit errors 0, Unicorn 0 diff; the LK defect is fixed (DSL M32 inside locked instructions). Its proof/ and dsl parts go to L3b w4 (split: proof/ is L3b's, fnvlift is L3a's); the fnvlift part is merged after the rebase onto L3b w4 d7c7d612. Stage 2 builders are L3b's |
| 24 | Pack build on the L1 w14 EMLINK fix (2d8176a): 2x cold + warm; failed units classified | w1 | doing |
| 25 | Delete difffuzz (35 files, with its tests and references) from the integration branch: crt_symbols.tsv is deleted, and selecting by fuzz results is forbidden by the 11-10 order. It is on neither the trunk nor this branch | w6, branch claude/fnv-l3a-lift-w6-difffuzz | done; merged into integration b84a2c0 (07c7b05) |
| 26 | fnvnx_dispatch (DISPATCH1, target of the L2 dispatch plugin), undefined (L6 w5 census 56f5ac6b). Rule: an exe address resolves to the native body if a native root defines it, else to a bridge into lifted code, and the table is complete over the closure: the generated stage turns a missing entry red, and a miss at run time calls fnvnx_reach_violation with the closure certificate (option iii; the lifted miss path fnvl_nobody is changed the same way). One static bridge per descriptor that occurs in the admitted objects; a missing bridge is a build error | w8 session_01KmtZKR4JRJLXpi1LcSzp9b, branch claude/fnv-l3a-lift-w8 | first version 32bf31a: dispatch.c, bridgegen (one bridge per descriptor, exact FNV-1a phf), 14 real-object cases pass, 17 bridges in the test. Corrections ordered: no #UD (reach violation), the plugin diff goes to L2 for review, the MSVC aggregate-return rule from a document plus the non-POD rule, descriptor facts from L1 (descs.tsv), test_plugins run. 2a9138eb: dispgen --closure (a missing key is red). The miss paths wait for L1 w14's closure_key (one FNVNX_REACH id per dispatcher; header contract/include/fnvnx/reach.h, L1 w16; body L5 platstat.cpp); L2 holds the plugin for the HFA defect (classes come from the backend's function_arg); TLS slot: L5 f26193ad defines fnvnx_call_fp once (thread.cpp, declared in fnvl_host.h); w8 deletes the rt/dispatch.c definition and has dispatch.c and bridgegen include the header; w8 1d7a3990 accepted: classes from the backend (hfa() deleted), non-POD rule from MS docs and Agner table 7 (`x` flag), descclasstest 40/40, test_plugins 39 PASS, disptest 14/14. Open: TLS dedupe confirmation; union/vector cases into disptest; closure_key certificate kind asked of L1; w8 afb354d9: TLS dedupe done (rt/dispatch.c definition deleted, fnvl_host.h included, bridges IE TLS 17+17 relocations). Returned: disptest native.cc defines 3 platform symbols (fnvnx_call_fp, fnvl_host_tls_slot, fnvl_host_ticks_ns), a double; the rule is to link the real platform files, or the test is blocked. ASM-207 (A-NATIVE, now L3a-only): the FPCR/FPSR rule note is being written first; closure_key from L1 w14 b04f3ec7: FNVNX_REACH_CLOSURE_KEY_NATIVE/_LIFTED via fnvnx/reach.h plus the generated reach_facts.h (recipe `uses`); measurement-only until sources closed, dispgen --closure and w18 open = 0; w8 cd04ef49: disptest 26/26 guest cases (27 descriptors). Still returned: the 3 doubles and the copied fnvl_host.h (use the headers tree); w8 c71d7010: 7 platform-symbol doubles deleted from disptest, which reports BLOCKED until platform thread.cpp links (the 26/26 run predates this). ASM-207 note (inbox l3a-w8-native-fp.md): rule R-FP, guest code runs under the guest FPCR/FPSR. Defect 1 (the bridge entry restores stale parked FP state) is being fixed by static per-kind entries. Defect 2 (native -> import does not park) is with the orchestrator/L5 as a declared-machine question; w8 56a41e09: copied fnvl_host.h deleted, rt build.json includes the shim/include headers tree. Miss paths BLOCKED by L1: the named w14 b04f3ec7 lacks closure_key, reach.h and the recipe `uses`; the correct commit has been asked for; L1: closure_key in gate_reach 58a2984b; reach.h at w16 0b0d08b3 (needs MERGER); `uses` at w15 3a6ec1b2 (not merged); reach_facts.h and the ids not done (w14, ETA about 1 h after w15); w8 37e3f069: defect 1 fixed statically (invoke_core<FROM_IMPORT>; the bridge entry does not touch FP), fsw/frm tests fail on the old code and pass on the new (scratch run, since disptest is BLOCKED). Next: the lifted -> native register save in dispatch.c, shared with fnvl_native_in; w8 139e6eeb: fnvl_native_enter/leave (register save without park), with in/out composing the park; not built yet (a -w syntax check is not admissible). Item 26b NEW: the X_<A> exit-bridge generator (lifted -> native body), from the L2 descriptors, sharing one parser with bridgegen; a missing descriptor is a build error. L1 w14 closure_key at 2e71c913; the macros wait for reach_facts.h and `uses` |
| 27 | Lock atomicity, a class gap that w6 confirmed. Records: .fnvl.lk (lock) and .fnvl.fsw (x87 status-word sites, for L4), 12-byte records (x86 pc, host start, host end), several per pc allowed. gate_order: exclusive-only inside lock ranges, completeness both ways. Generator: while inside a lock, any guest access other than through LKA is an error. 202 distinct locked addresses (exe 69, binkw32 8, d3dx9_38 98, d3dx9_31 26, libvorbisfile 1) | gate w6, emitter w3 | emitter kind 1 done 289ff54b (the record is emitted from inside the LKA commit asm); generator rule and kind 2 doing (w3); gate (w6) doing; w3 cdac170b (LK-ONLY) and 56c30f4c (kind 2 `.fnvl.fsw`, one instruction per range, ranges_test 1 lk + 6 fsw records) on w3-core. Kind-2 sites over all universes: 20 283 (exe 11 882, d3dx9_38 4 500, d3dx9_31 3 576, libvorbis 303, libvorbisfile 20, binkw32 2). .fnvl.fsw accepted. LK-ONLY returned: a text regex is not closure, so the accessor emitter refuses inside LK by construction; FINAL layout = w6 0be860f47 (two sections, 12-byte records; `.fnvl.sites` withdrawn, L4 told). w3 7e18a12b goes back to it and keeps the one-path status-word store (dmb ishst; str); facts/sites.tsv from locksites.py with the same decoder predicate (w3); w6 96e7c4037: gate_order accepts `dmb ishst; str` for every plain guest store (fixtures for 4- and 2-byte stores, a second store red); lift-gates trunk-merge head d2ada89af, verdict vd15 running; ACCEPTED w3 b853a1f3 (two sections), cd1008c8 (LK-ONLY at compile time: a plain accessor inside LK_BEGIN does not compile, the regex is deleted), bb3a9fe0 (fnvlift/sites.py one predicate; 202 + 20 283), 7945d1a6. Facts 2eeea27b: sites.tsv 20 485 lines plus manifest.json pinning all 8 images (gamedata/exe 0948da1e, gamedata/redist c53729e3). Integration of lead + w2 + w3-core: w3, branch int1; int1 also takes the entries.tsv kind by producer set (code/import/runtime), replacing the `I_` prefix and the address-threshold test in liftgen --finish |
| 28 | Assumption records owned by L3a (L3b w3 table, ASM-L3B-xx). A-STACKPRIVATE (ASM-L3B-20) is removed by deletion: no PLAIN class has been emitted since c0d18b76, and every guest access is GM/LKA. The dead memclass tools (tools/memclass.py, test/memclass_det.py, verify_r9/compare.py, verify_r9/hunt.py) are deleted after a consumer check. The removed_by lines of the other 18 L3a records are checked against the code. The fnvlift citations move to the L1 w10 ids once they are issued | w3 session_01KmtZKSV4DsNmobaEQ2Fzvq (dead code, removed_by lines); w9 session_01KmtZKSN8ztLNERy9161gg7, branch claude/fnv-l3a-lift-w9 (citations) | ids fixed by L1: the 3xx ids map 1:1 from ASM-L3B-nn, A-STACKPRIVATE = ASM-319, which L3b corrected. L1 03:28: no renumbering (the range binds the minting layer, so 3xx stays L3b-minted with owner L3a). w9 721e7d6: 49 citations -> 0, 12 files; being checked against the w3a registry aliases; A-STACKPRIVATE dropped. Reserved: ASM-200..202. w3's part is queued after its current item; L1 03:29:24 final: ranges keyed by owner, L3a records = ASM-203..220 (3xx kept as aliases), A-F2INATIVE = ASM-801 (L2), A-STACKPRIVATE dropped. w9 cb837327 maps from the w3a 326f2c8e aliases: 49 citations -> 0 in 12 files, every id in the registry. MERGED into the lead branch; w3 (a) 5e1f7529: memclass tools and verify_r9 deleted (grep 41 -> 21, every remaining hit outside code); (b) b0d33a0d: of 18 records, 14 still true, 205 FPSTICKY wrong (the product keeps the flags; 2 test injections to delete), 217 DECODEFLOW has no consumer (drop), 207 and 219 partly closed; lines sent to L3b; L3b's unit carries fnvlift at 82b45c2c, so w9 makes a citation-only commit on that parent (branch w9-unit, map from w3a a909a355); w9 100ec83b, the unit commit on parent 82b45c2c (4 fnvlift files, 16 lines, citation tokens only, map from a909a355); next: core.py TAB_BIAS -> new ASM-221 (L3b mints it) and fpfacts 'assumed' -> ASM-204; w9 783aca41 (core.py ASM-221, fpfacts ASM-204): gate_assumptions at w3a c1f9100e gives 0 errors in lift/fnvlift; 100ec83b + 783aca41 are with L3b w3 to cherry-pick |
| 29 | FP exceptions and sticky flags by rule. Review found that fnvlift/fpfacts.py OWNER_ASSUMED accepts the unproven I-FPMASK as an owner assumption: A-FPMASK (ASM-305) and A-FPSTICKY (ASM-306) are REMOVE records, not OWNER. Unproven control-word loads: exe 55, libvorbis 15, libvorbisfile 2, binkw32 1, d3dx9_38 51. The facts path also falls back to /home/user/l3facts. Rule: sticky flags maintained, unmasked x87 -> ES plus #MF at the next waiting instruction, unmasked SSE -> #XM, through SEH; every x87/IEEE flag difference has its own rule; OWNER_ASSUMED, require() and the default path are deleted | w7 session_01KmtZKV5F6B2PqGD9piX26f | step 1 doing: the rule note for review; orchestrator 03:30: static fact first. FPMASK = proven mask bits at every control-word load; FPSTICKY = sticky bits maintained only where a read observes them; trap semantics only for what stays open. w7 runs the census over the 6 images; plus the native_fpflags gate (from w8's note): no sticky reader reachable from a native exit without a clear; ASM-207 decided (orchestrator 03:37): Windows model at the import boundary for both callers; w7's census adds (c) the rounding mode at every import call site and (d) sticky reads after an import returns. All-nearest with 0 reads means imp.h's park is deleted; otherwise the boundary sets RMode and ORs FPSR |
| 30 | Value ranges for L1's open LoadLibrary class (orchestrator rule (b)): intervals of the index sources of the indexed stores left open by the certified extents, with the jtab domain (join over callers, widening, no cap, unknown stays unknown, memory loads unknown unless a constant static word). Output: fnv-nx-build l3a-lift/facts <image>/idxrange.tsv `store_va index_src lo hi why`, cited in manifest.json | w2 session_01KmtZKSUoqsFSfuD18vvg8X | doing (moved ahead of the jtab counts); w18 sends the store list |


Note (L1/orchestrator 03:32): lift/pack and lift/rt take exceptions and unwind tables ON (conservative) until w17's fact (host C++ exception across a lifted frame, by the call graph) proves otherwise. L3a sent the edge inputs (dispatch table, bridges, imports, enter.cc). Guest MSVC EH never becomes a host exception (rt/seh).
Update 2026-11-12, at 698adefb:
- L3b's range 00e7c100-00e7c1d9 (decoding stops at 00e7c11c) is with w7. Rule: a byte is code iff proven flow reaches it. An undecodable byte that flow reaches is the #UD point. If the facts are wrong, the codemap or residue.py rule is fixed, and every same-shape range in all 6 images is counted.
- L1 w17 397d7feb flags.semantics: w1 strips every semantic flag from lift/pack and lift/rt build.json (keep -D, -W, -O), coordinating with w8 on rt. It is queued after cold 2 and warm. `recipe.py check` is the proof.
- w5 pure-gate (a08ef74c), the lead's decisions:
  - census_doubles and the farm scripts are already resolved by 7788747f;
  - mp.Pool only with a pinned interpreter;
  - the test/ → tests/ move is declined (a directory name is not the class), and the test-root classification is asked of L1;
  - bench/ is deleted if it has no consumer.
- d3dx9_31.dll joins the image set (orchestrator; 7 images, gamedata/redist c53729e). Assigned: w7 (manifest pin, unent, codemap, return-address census), w4 (s4 escapes), w5 (flags universe), w1 (the pack covers 7 images). From now on, global numbers read "7 images @ commit".
- Trunk plan (orchestrator): the lifter tree is one trunk unit (lift/tools, proof, fnvlift, rt, plus the flags gate). It is cut once int1 is reviewed, and goes to MERGER after lift-gates (w6 0be860f47) and before the pack root. The pack root is red on forms until L3b's 16 farms finish (certs.tsv has 0 rows), which is expected.
- w6 moved lift-gates onto gates.d: head d6147475 on L1 w10 92d1d875 (order and addr32 cover lifted and product-guest, eh covers lifted; ASM-200..202 kept; gates.json removed). Verdict vd17 is running. envcheck is required with it, and the setup script's pip defect has gone to L1. w5 rebases flags onto it.
- 00e7c100 (L3b): a switch jump table plus byte index tables in .text, which no flow reaches. Defect: R3 took relocated non-lea memory displacements as entries. Exe @ facts 2eeea27b: 155 'ud' entries (133 on data tables, 22 real #UD points); modules 0. Rule R3 refined: a relocated value is an entry iff it can become a run-time value. A displacement consumed by address generation never does. w7 applies it in the universe/ranges producer and the codemap through one function. Invariant gate: universe entries over class-3 bytes = 0.
- w8 01df9e5a (the 0b8493df fixes): the w32_raise citation (L5 thread.cpp:642), the rdtsc monotonic check, and the lost-coverage note to L4 are done. Four tests are BLOCKED with their undefined lists; instest has no exe here. msvcrt_eh.cc:414 P() is fixed (lost in 0febaeb5). Returned: (1) build through recipe.py with L5 as an external, not by hand; (2) gmpy2 must be pinned in env.json (to L1); (3) the rdtsc ratio becomes one generated fact.
- w5: the lift and gates suites pass (26 and 52) with envcheck OK at 2f4572b7+a08ef74c. setup_verdict_env.sh `pip -r -` defect → L1.
- w18 allocsites/1 (l1-contract/mp4 841d1b5; 70 sites, 5 constant) is kept for the s4 heap points-to after boot. idxopen_all.tsv (216) → w2, item 30, now unblocked. w18 is asked to extend both to d3dx9_31.
- L5 asks: fnvnx_call_fp relocations over all pack objects are IE only (TLSDESC and TLSGD = 0). w1 adds the counts to the pack report; a static gate is proposed to L1.
- RED, owned by L3a: the facts external has no ranges.tsv for atimgpud or d3dx9_31, so the L1 censuses count them as "image without facts". w7 publishes ranges and universe for all 7 images after the R3 refinement in one facts commit (with tree_sha256) → w18 reruns loadlib, allocsites and idxopen. L1 relayed idxopen_all (4843d9f) to w2.
- L1: the `tlsmodel` link gate (w10) covers every TLS symbol: TLSGD and TLSDESC = 0, IE for host TLS, LE only for same-output definitions. w1's counts serve as a cross-check. The setup `-r -` fix is L1 w5 d7cfe2a2/c44308c0 (pending MERGER), and workers merge it locally. gmpy2 is pinned by L1 w17 (w8 names the importer).
- L3b: dsl.py gets XIN selector variables sel_es..gs (1:1 with c->sel) for push/pop/mov sreg. L3a takes L3b w4's isolated commit byte for byte (the model decides the width on push). L3b w4 75b6e9ab took f_sys/forms_sys/dsl from e9124b30, with a shape_sha diff of 0. L3b re-pins its census universe to w7's new 7-image facts commit.
- lift-gates: vd17 on d6147475 was RED, on assumptions only (gate_eh.py:31 docstring), with 31 of 32 gates green and 375.7 s over budget. w6 dc71c31c rewords it; vd18 is running as a measurement because the env is not rebuilt by the script: L1 w5's setup fix needs its new env.json snapshot, and L1 is asked for the merge order. Lead review asked for: the before and after of the docstring (cite the ASM id if it is an assumption), and the cold-vs-incremental time.
- w5 c40135c6: flags is rebased on lift-gates d6147475 (VERDICT flags paragraph; test_gate_flags 7/7). A counted rerun is BLOCKED on L1 w5's install_mirror KeyError 'autoconf' (forwarded to L1). bench/ is being deleted (no consumer). The mp.Pool pin is env.json python 3.13.18.
- w2 97d304a4 item 30 code: idxrange.py, an interprocedural jtab join over call sites, with unknown reasons. Lead asked for the premises cited by their facts (R3 refined, no PIC, pcsrc; escape-open is unknown), a rerun on w7's refined 7-image facts, and long runs moved to farms.
- Merged w5 pure (7aa52e42 + 07638de5): bench/ deleted (5 files, no importer); phf.py references fixed; lift/test admitted as a root of class test (selftests gate), per L1's classification. The README's claim of a farm equivalence bench was removed: no such check exists, and lifted-body correctness is by L3b form certificates. L1's merge order: L1 w5 (snapshot + script) → w14 → w17 → toolchain-build → w16 → w19 → lift-gates re-verdict → L3b.
- Merged w8 through 1cd6911a (items 26 and 26b: DISPATCH1 rt, bridgegen, and exitgen X_<A> sharing the bridgegen parser).
  - exitbridgetest: 98 descriptors, 294 frames, 0 differ; mutations are caught (114 and 69).
  - disptest has 29 cases and is BLOCKED on fnvnx_call_fp (L5).
  - The tests no longer use -w.
  - Open on 26b:
    - the `pop` fact per native address (from the exe's ret N; source being assigned);
    - per-definition descriptors from the L2 dispatch plugin (asked of L2);
    - test objects through recipe.py (after the headers tree).
- w2 ddfbd301: idxrange premises are stated. Closed intervals carry cond:no_s4_arith until s4 closes, and are counted apart from unconditional ones.
- 26b facts sourced: w7 adds facts/<img>/retn.tsv (pop per entry from the ret-site set by flow; mixed and noreturn explicit; removes the "ret n" assumption). The L2 lead is asked for per-definition descriptors from the dispatch plugin. w2: escape_open = w4's pcsrc s4 targets; closed intervals are counted as unconditional / cond:no_s4_arith / open.
- L1 allocsites e39c200 (l1-contract/mp4): 203 sites over 5 images, 25 constant, 27 allocators (7 pure, 20 mixed), 3 address-taken. Open and recorded as an L3a dependency: the exe's CRT malloc/new (pool pointers loaded from memory) and the indirect callers of the address-taken allocators. Closed by the whole-program points-to (the no_s4_arith item), scheduled after the first boot. pefile: no importer on L3a or the trunk (L5 pemanifest needs it); keep or drop is L1's call.
- L2 asks for w8's dispatch plugin work (L1 descs w20 9692df48 waits on it). tm_p.h and the HFA cases are already in (3d991b28, cd04ef49, 34fac8ab); msvcrt_eh.cc:414 is fixed in 01df9e5a. w8 rebases the plugin commits alone onto L2 w4-cc1 fada875 (branch claude/fnv-l3a-lift-w8-disp) as first priority.
- Item 16 (L4): the five lift tests move to the boot machine as a guest test root. L3a's choice: a `generated` stage of the lift product root (tracked x86 test sources → pinned keystone → liftall.py), a guest main per driver, then the Unicorn and keystone harnesses deleted. Owner w8, after the dispatch rebase. w8 b4ac9388: the rdtsc ratio has one source (fnvl_abi.h FNVL_TSC_NUM/DEN); the gmpy2 importers (fmathtest:13, citest:15) went to L1. Until then the test runs do not count (not built by the recipe).
- L2 accepted per-definition descriptors: a `.fnvnx.defdisp` section, `<asm name> <descriptor>` lines (sorted, unique; same grammar and backend classification). exitgen joins on asm name via `.fnvnx.sympair` to the N1 exe VA from bind. w8 implements it in the dispatch rebase onto L2 w4-cc1 fada875.
- L1's ruling for item 16: (ii). The generated stage runs in the declared env, and its key holds the interpreter digest plus the installed_files_sha256 of every pinned Python module. keystone and capstone come in with w17's pins; L1 w14 adds the key component. There is no second keystone build.
- L3b w4 e2975877 (XIN selectors, push sreg, 7 encodings proved) is accepted in content, but does not apply here (our proof/ is behind L3b's). w4 rebases it onto claude/fnv-l3a-lift with its prerequisite commits. pop/mov sreg: w4 prepares the selector-output slot as a second hunk. The pinned model lacks both instructions, so reached sites are red. Asked for: the reached count over 7 images, and a pinned model patch if >0.
- Correction: an SDM-prose model patch for pop/mov sreg is withdrawn. It would be an informal step, so a new assumption. L3b escalated it to the orchestrator as "no class-closing reference known". Candidate formal source: ACL2 x86isa (the source of sail-x86-from-acl2). Reached sites stay red until it is ruled.
- L3b asks for one lifted-set predicate. w5 factors out lift/tools lifted(img, facts) / is_lifted(...): the universe entries → lift_one's decoder, which liftgen itself calls. Check: equal byte for byte to liftgen's shapes. L3b drops its DATA_CLASSES list.
- w8 7a85cf41 (claude/fnv-l3a-lift-w8-disp, on L2 fada8750): the dispatch plugin plus descclasstest (40 ok, run with copied files, not counted). test_plugins and cc1 are BLOCKED by the env. Returned: defdisp missing?; pod_type_p must be conditioned on the language, not on dlsym success; the branch must be self-contained for its test. Then item 16.
- Correction: facts 2eeea27b already holds ranges and universe for atimgpud (b3ef19d) and d3dx9_31 (15c914d), with all 7 images pinned in manifest.json. w18's external was stale; it re-pins and reruns. New defect: universe.tsv `extent` means SUM in the exe and SPAN in the modules. w7 unifies it (SPAN) in the refined commit. L5 is pointed at ranges.tsv for function boundaries.
- Merged L3b w4's 13-commit stack (claude/fnv-l3b-lift-w4-l3a) as d40bcf6c: the XIN selectors, push sreg, a16 ea, segment prefixes, pusha/popa by size, the segment_bases model patch, forms_sys. The semantics of the forms.py and forms_move.py conflict hunks were reviewed and accepted. Returned to L3b: implicit memory operands (xlat, the string source) skip the segment override; one address function for all loads and stores, with a census. Also a stale fault-code comment.
- w8 f59b5327: .fnvnx.defdisp is done (defdisptest 0 failed), with L2 reviewing. Item 16 is BLOCKED on L1: (1) recipe py_modules keyed by installed_files_sha256 (L1 w14), (2) keystone/capstone pins (L1 w17). w8 meanwhile admits lift/tools as a tool root and writes the x86 sources and guest mains. The generated stage lives in L4's tests/guest_lift.
- L1: tlsmodel gate at w10 6645c214 (L2 asked for a `.fnvnx.tls` note to close the IE-declared-but-LE gap). L1 w22 71d8cafb is green 28/28 with envcheck OK on a fresh container (the setup fix), pending MERGER.
- 08:30: L3a workers w1, w7 (and L1 w14) had stopped on the 5-hour usage limit (reset 07:30). All L3a workers (w1–w8) were sent their queues to resume, each reporting its state first.
- w6 4eefcdb1: lift-gates is green (L1 44f1630e, 32 gates) but over budget at 361.6 s: selftests alone is 351.9 s (not incremental, L1 w19's), the same breach as the trunk base; order/eh/addr32 are each under 1 s. Returned: the ASM-203..205 records collide with ASM-204/205 (A-FPMASK, A-FPSTICKY); L1 is asked for ids. The env is still a measurement.
- w7 46fd85f: item 29 tools (fpstate, fpcensus, fpreach); census on 6 images, the exe fpreach is being rerun. d3dx9_31 recomputed: 18 945 entries, unent 0, 344 unexplained runs pending w4's pcsrc merge. Now on R3 → SPAN → retn → the 7-image facts commit.
- w5: on lifted()/is_lifted() at c5e175dc.
- 08:31 states:
  - w1: no pack result yet; restarts killed cold 1, and the full sequence was restarted under setsid (cold 1, cold 2, warm; about 2.5 h).
  - w2: the idxrange exe run was restarted (about 25 min).
  - w4 f1ff8d4a: pcsrc runs before the residue (with a stale-file test); fresh stack space for chkstk/alloca. Its aliasing premise is asked for by record. The 7-image runs are going.
  - L1 item 16 order: w22 (env) → w16-int (py_modules) → w17 (keystone 0.9.2, capstone 5.0.9, gmpy2, pefile pins).
  - Two farms requested from the orchestrator for the long L3a runs.
- MERGED int1 (w3 03d5b2ca: lead + w2 b58341df + w3-core) as 37a40991.
  - It brings:
    - gate_eh on w2's dataflow facts schema;
    - jtab_tables plus residue.py as one rule;
    - LK-ONLY as one rule for both paths;
    - entries.tsv kinds from the producer sets (no address test, fb17b71b).
  - Green: 15 tests, including universe_r3_check with 67 370 entries, missing 0, codemap 12 440 971/12 440 971 B.
  - Open:
    - modlift_closure_test (timed out at 1800 s, not rerun; goes to a farm);
    - tools fail under python -I (universe.py cannot import ehtables);
    - facts 2eeea27b has no jtab_tables (w7's commit must carry them);
    - the liftgen dry run on binkw32.
- w5 b7b04616: lift/tools/lifted.py, lifted()/is_lifted(), the one lift step shared with liftgen; binkw32 61 774 instructions, 3 104 shapes, equal byte for byte to liftgen's shapes. L3b has the path.
- L1: gate_eh ids are ASM-225/226/227 (L1 minted). w19 owns the selftests key (ETA 2–3 h).

## 08:40 update (head b8172736)
- w5 lifted() merged as b8172736. The jtab is an input of the lifted set. binkw32 with the real modules.tsv 0772c61: 61 060 instructions, 3 092 shapes, equal to shapes_0.tsv byte for byte. Liftgen shard: 988 entries, 947 lifted, 41 missing. The jtab case waits for w7's facts. Env from L1 w22: envcheck OK. Counted suites are running (doing).
- w6 df9cb8d0 (lift-gates): ids ASM-225..227, one meaning for ASM-203, 77 registry entries, w22 env fresh with envcheck OK. vd18 on dc71c31c: L1 green, 32 gates, over_budget (selftests, L1 w19). vd20 cold is running (doing). ASM-006/009 texts: waiting on L3b's confirmation.
- Merged base int2 (lift-gates df9cb8d0 + lift b8172736, 7 conflicts resolved by stated rules): w6 (doing). w8's item 16 builds on int2. w8 extracts citest on w8-t16 meanwhile.
- w7: a new facts invariant. Flow-reached bytes or dataflow-named sites outside ranges must be 0 over 7 images (L1 w18: atimgpud 0x100092d4; exe 0xda284a and atimgpud 0x10001608 covered). Doing, as part of the 7-image facts commit.
- w4 9d71e6cd: premise F (fresh stack space) is conditional. Totals are reported as unconditional, cond:no_s4_arith, cond:fresh and open, over 7 images at 9d71e6cd on facts 2eeea27b (doing). REMOVE record for F requested from L1, owner L3a.

## 08:45 update (head 7b10290c)
- w3-i merged as 7b10290c.
  - (a) Tools import their siblings from their own root. 25 of 25 entry points start under python3 -I; before the fix, 3 failed with ImportError. Only imports are tested; full runs are queued for w3.
  - (b) lift/rt/build.json keeps only per-root flags: -O2, -Werror=attributes, -std=gnu++17, -DFNVNX_SDD=1. The admit.json "target" field is L1's.
  - (d) modlift_closure_test is on farm 2 (3 h timeout).
  - (c) The binkw32 liftgen --jtab run is in progress (doing, w3).
- Farms (orchestrator):
  - farm 1, session_01KmtZKU7i9iyCYMN5hkeoJW: lifted pack builds, from w1.
  - farm 2, session_01KmtZKSeQ4EmE5d595KdsK1: idxrange (w2), pcsrc (w4), modlift_closure (lead).
  - Plugin rule: claude/fnv-first-root-next 58c113f0.
- The gate_eh ids are ASM-222/223/224, the orchestrator's. L1 withdrew 225..227. w6 renumbers on lift-gates and int2. L3b confirms ASM-006/009 = the w10 text; used_by is derived by the gate.
- L1 order: L1 now works only on the budget, guest_flags, the toolchain profile and the first-root merge. The py_modules key (w14 62623608) and the pins (w17) wait until after that merge. So item 16's keystone/capstone stage waits on L1 (blocked).

## 08:50 update (head b00170ad)
- L1 minted ASM-225 for premise F: REMOVE, owner L3a (w4). Results resting on F are cond:fresh. w4 cites it in pcsrc.py.
- w4: the 7-image pcsrc run at 9d71e6cd stays local, because a farm would need the whole gamedata set installed first. binkw32, libvorbis, libvorbisfile and atimgpud are done; d3dx9_31 is running; d3dx9_38 and exe are next (doing).
- w2: the idxrange RUN line (2a326b21, run_idxrange.sh) is on farm 2 (doing).
- Farm 2 is ready, with envcheck OK. Defect in farm/setup_verdict_env.sh: `pip -r -` fails, so the farm installed the pinned z3 wheel by hand. Reported to L1.
- w8 d800d540 (claude/fnv-l3a-lift-w8-t16): citest as a guest root. lift/tools/mpfr_oracle.py is the one oracle definition; gen.py is a generated stage that needs gmpy2 in py_modules; main.cc is the guest main. Drivers converted: 1. The gmpy2 and MPFR pins were asked of L1, and they wait until after the first-root merge (blocked, L1). Next: imptest, msvcrttest, disptest, instest.
- w5 c40135c6 (lift-gates d6147475 + flags, L1 w22 env, envcheck OK): gate_lift 28 PASS / 0 FAIL, test_gates 52 / 0, gate_flags 7 OK. Done. The flags go into int2 through w6; the 7-image flags run waits for w7's facts.
- w4 5c60b01e: ASM-225 cited in pcsrc.py. Comments and an attribute only, so the code is identical to 9d71e6cd.
- int2 a62f47f5 (w6) = lift-gates 662f4d9f + lift 7b10290c.
  - Registry: 71 entries; used_by is derived from the citing files; gate_eh uses ASM-222/223/224.
  - The six L3b records come back with L3b's files.
  - Cold verdict vd23 is running (doing). Next into int2: w5-flags and 800efa95.
- 800efa95: deleted a stray host ELF, lift/--help/g (from w3's census run).
- Farm 2 is running modlift_closure binkw32 at 7b10290c (3 h timeout) and w2's idxrange at 2a326b21 (doing).

## 08:55 update
- Flags census, binkw32 (w5, code c40135c6): RED.
  - Global for the image: 988 entries (0 unreached), 61 561 instructions, 4 126 flag reads.
  - Violations: 7 genuine flag reads and 7 992 no-form rows.
  - The 7 reads by cause: 2 read a flag a certified form leaves undefined (asked of L3b); 4 are entries of kind ptr (split jtab / call, waits on w7's jtab facts); 1 path crosses a no-form row.
  - The 7 992 no-form rows are x87, MMX/SSE-int and rare system ops. Form coverage is L3b's (blocked, L3b).
  - The json field `certified` is negative because of a reporting defect. w5 splits reads from no-form rows (doing).
- w8 bf936c0c: item 16 has 2 of 5 drivers converted (citest, imptest); 5 of 5 Unicorn harnesses remain.
  - Decision: a test's lifted units form a lifted sub-root with LIFTED_ARGS, and its natives and main form a native test root that links it (origin rule 58c113f0).
  - disptest is done with the lift/bridges wiring on int2. msvcrttest is next.
  - instest is parked: Unicorn is not a reference. Sail through isla is proposed to L3b.
- L1: farm/ on the trunk bee3dbfc already uses `-r FILE`. Farm 2 re-runs setup from the trunk after its current runs, dropping the hand-installed wheel. The gmpy2, MPFR and GMP pins are queued in w17 after the first-root merge (blocked, L1).
- L3b's form coverage, global over 7 images (l3b-lift/shapes 77524556): 26 500 shape keys = int 19 817, x87 2 175, simd 4 369, sys 139.
  - int: 2 370 of 19 494 certificates proved.
  - x87, simd and sys have no certified builder yet. That is L3b w4's stage 2, paused until the packs are roots (blocked, L3b and the orchestrator).
- L3b's rule for undefined flags: every reaching definition of a read flag bit must define it. The exceptions are dataflow facts only (dead read, opaque masked propagation). w5 implements it in the flags gate (doing).
- instest: its reference is the pinned Sail model through isla, as a generated stage (OWNER 9), agreed with L3b.
- w1 d6235c0f:
  - Pack flags are stripped to per-root classes, with the full plugin set read from recipe.py. `recipe.py check` gives "recipe valid" on 58c113f0 plus the overlay.
  - The RUN lines are on farm 1 (doing). There are no local numbers: the local run failed by w1's own error and was cleaned up.
  - Asked of L1: a LIFTED semantic set with -ffreestanding, -fno-exceptions and -fno-asynchronous-unwind-tables, and a recipe.py constant for plugin_cxx.
- w6: first-root-next 58c113f0 is queued into int2 after vd23.
- L1 decided the lifted flag set. flags.semantics becomes origin-keyed: lifted = common + -ffreestanding -fno-exceptions -fno-asynchronous-unwind-tables -fno-unwind-tables.
  - recipe.py gets a constant for the plugin compiler, derived from env.json, and a hand-typed compiler name becomes red. Both are L1 w17's, after the first-root merge.
  - New condition, owned by L1 with L2's facts: every lifted→native edge must target a callee proven not to throw. Until that gate exists, the pack is a measurement build on that edge.
  - w1 rebuilds the pack with the origin set when w17 lands. The current farm 1 run is labelled a measurement.
- w3 d77f58ef, merged as 6bed89ae: tools/cli.py. Every entry point declares its usage. --help exits 0, a bad argument exits 2, and nothing undeclared reaches the file system. 11 tools were fixed: rcpgen had written the stray --help/g, and 10 others crashed or ran on --help. The lead spot-checked rcpgen and packcopy: rc 0 and 2, no files created.
- w8 643c058e: item 16 has 3 of 5 drivers converted (citest, imptest, msvcrttest); 5 Unicorn harnesses remain. msvcrttest's 3 terminal cases run one invocation each, checked against terminal.json; asked of L4.

## 09:00 update (head ba908b36)
- L3b F1/F2 cb32e9e4 merged as ba908b36. proof/forms.py has one family of address functions (ea, ssa, esa, dsa), and an untagged access is refused.
  - 0 raw load/store outside forms.py; the AST test passes here.
  - Of the 19 817 int shape keys, 77 changed and were re-proved.
  - Model defect: the pinned ACL2 x86-cmps computes [edi]-[esi]. L3b fixes it with a transcription of the master hunk (ASM-009), in one IR bump after w4's G1.
- Orchestrator's base: claude/fnv-first-root-next 862b0708, not int2. Records go in with the commit that cites them.
  - w6 builds int3 = 862b0708 + lift-gates + lead ba908b36 + w5-flags (doing).
  - w4 makes an ASM-225 + pcsrc.py commit on 862b0708 (doing).
  - w5 rebases the flags work onto 862b0708 (doing).
- w5 30da5cec: gate_flags now reports reads and no-form rows separately, with an assertion that certified ≥ 0. binkw32's 4 entry-seeded reads are unknown-consumer 4 at the present facts.
  - Next: a jump-table target's incoming flags come from the dispatching jmp.
  - The 2 undefined-flag sites, 01441246 and 01457d65, go to L3b's rule.
- w8: takes int2's instest.py; item 16 rebases onto int3.
- gate_flags is L3a's gate for "every read of a flag bit has only defining reaching definitions" (agreed with L3b). w5 implements the two dataflow exceptions and classifies 01441246 and 01457d65 (doing).
- Fact requests from L1 / L5 w15 (manifest e15d385):
  - the extent of exe 1269a9c for the 10 dbghelp GPA rows: w2, idxrange (queued);
  - the write set for exe 19345408/19345418: no writer in 7 images;
  - the write set for d3dx9_38 [793b20+4]: only the ctor 56b74b writes it, so d3dx9_31 is reached.
  - The two write-set facts are w4's, from pcsrc address sources, after its totals (queued).
- w4 7bd3abbd (claude/fnv-l3a-lift-w4-asm225, on 862b0708): pcsrc.py with the ASM-225 record. gate_assumptions is green: 120/120, 80 entries, 9 OWNER, 71 REMOVE. Sent to the orchestrator for the candidate. Done.
- w8 ffb33e01: one build.json pair per test (lifted/ + native/) for citest, imptest and msvcrttest, with no admit rows. recipe.py check runs on int3.
- w2 62c1bcd2 / 96cdc4eb: the 1269a9c request is an object, not an array. da2840 stores 11 GPA results at this+[4, 0x2c]. idxrange now joins register state over the call sites of a pure direct-call entry. "Pure" is a fact from the code: the producer set, no relocated reference, no jump predecessor, at least one call site. The exe run is going (doing); entry counts come with it.
- w8 f7d3c182 (claude/fnv-l3a-lift-w8-t16b on 862b0708): 6 test roots (3 pairs) with admit rows. recipe.py check finds 4 blockers:
  - a pack-less lifted origin is needed (L1, asked); w8's stand-in value is removed;
  - the link script (L1, asked);
  - lift/rt is missing at 862b0708 (int3, w6);
  - link/ldcheck.py and py_modules (L1, after the first-root merge).
- w8-disp 13a5a195: the MSVC return-in-registers POD predicate judges only the returned class; cl VC 2008 verifies it. Sent to L2.
- w5 d5a90fed: the undefined-flag rule is in gate_flags, with an identity rule for opaque copies that the lead checked as sound.
  - binkw32 (facts bc9f12d): red reads 7 → 6; certified 4 120/4 126; no-form rows 7 992. The cpuid probe 01441246 is green.
  - All 6 remaining reads lie in 3 `ptr candidate` entries with reloc-misfits: 0144ddec, 01457d50, 0145e388.
- New universe rule (w7, in the 7-image facts commit): a decoding is code only if every relocation in the span lands on an imm32/disp32 field. A misfit refuses the candidate (doing).

## 09:10 update
- int3 7ca2a51a2 (w6) = 862b0708 + lift-gates 662f4d9f + lift ba908b36 + w5-flags 30da5cec. Cold verdict vd24 is running (doing). vd23 on int2 never landed because of container restarts.
  - Known red, gate_assumptions: 51 findings. Registry ids for L3a's records are missing from 862b0708, and there are mention blocks without an id.
  - w6 fixes it by rule: union with the L3a records 203..221 from w3a 500b1f03, ASM-225 from w4, and the alias map for the rest. Blocks without an alias go to the lead (doing).
- L1:
  - A lifted test root's source may be a generated stage, `source: {"stage": …}` (L1 w14, a path item).
  - Test roots link with the product's link/guest.ld; verify/tests/guest/guest.ld is a double (L1 raises it with L4).
- Orchestrator: an undefined flag that is live in proven code takes the reference CPU's value, from a per-(form, flag) cert (L3b, after the first-root merge). It applies only after w7's universe rules (reloc consistency, unproven ptr candidates) have removed misdecoded data.
- L2 on 13a5a195: the direction is accepted. Before L2 takes it, the predicate must match w4's full cl matrix with 0 mismatches, rebased onto 862b0708 (w8 doing, w4 matrix).
- Facts a0f3d8e (w7; fnv-nx-build l3a-lift/facts; tree 2a5bd36e…bb8dd; rule lift-facts/2; producer f12c75b), done:
  - R3 refinement, residue.disp_only as one function. Entries: exe 101 590 → 96 839; d3dx9_31 18 945 → 17 879; d3dx9_38 38 853 → 36 688; small modules −29 to −44 each.
  - ud entries: exe 155 → 0, small modules → 0; d3dx9_31 510 → 432, d3dx9_38 719 → 559 (candidates).
  - Entries over class-3 bytes: exe 1 451 → 3, modules → 0. The 3 exe entries are data-word pointers into tables in .text. Lead decision: such a pointer is no entry iff a proven data consumer covers it and no proven flow reaches it; otherwise red.
  - extent = SPAN everywhere.
  - retn.tsv over 8 images, 154 898 entries: caller 77 147, callee 44 379, mixed 122, noreturn 19 332, open 13 918.
  - r3_refine_test: 32 checks, 0 failed.
  - Open for the next facts commit: jtab_tables, the ranges invariant (L1's 3 sites), reloc consistency and unproven ptr candidates, pcsrc.run_facts (w4), and the 3 class-3 entries.
- w5: the identity rule is replaced by a product invariance check (two runs, v=0/1). 01441246 is red until it lands. A read below ESP is red (ASM-003).
- w8: b6324d18 (disp2) states the dispatch rule from the MSVC documentation and C++03; cl 15.00.30729.01 verifies it. defdisptest: 0 failed. test_plugins and test_cc1_plugins are queued on farm 2. The 144-case cl table is pending from w4.

## 09:20 update (head a6b94e4c)
- The trunk is 11187daf. The ASM-225 commit 7bd3abbd is in the candidate claude/fnv-first-root-next 83f7504f (verdict v6 running). w5 rebases the flags onto 83f7504f without the identity rule (doing).
- w4 a3fcd7fc, the write-set census over 7 images on facts 2eeea27b: 1 893 456 stores = exact 38 925, stack 1 227 422, open or ranged 627 109, plus 8 240 import call sites.
  - Both L5 facts are open by form. They close with a points-to over abstract objects, now on the path for w4 after the pcsrc counts.
  - pcsrc so far: exe red 9 928 (s4 9 801, non-s4 127); d3dx9_31 and d3dx9_38 are running.
- Facts a0f3d8e lacks jtab_tables (found by w3). w7's next commit (jtab, reloc consistency, unproven candidates, class-3 pointers, ranges invariant) is priority 1. Every consumer re-pins once, on it.
- int3 84ac7e2be (w6): 35 files lost in the w5 merge were restored. gate_assumptions 51 → 32 findings, 81 entries. A word rule covers the SMT sense of "assume". The remaining blocks are listed for lead decisions. vd26 is running (doing).
- The identity rule is withdrawn (L3b): 01441246 is red until the invariance proof lands.
- w4 0f90ce65: the points-to design note (journal/inbox/l3a-lift-w4-001.md) is accepted in structure. Lead correction: TOP comes only from facts that do not bound a value.
  - OWNER 6: a derived pointer stays in its object.
  - Relocation completeness: an image address comes only from a relocated word.
  - OWNER 3: an import writes only through its argument objects within the documented extent; a missing contract is a counted gap.
  - An unresolved indirect call goes to the escaped universe entries.
  - TOP stores are reported per source. Coding starts after the pcsrc counts (queued).
- int3 122ce8e18: 31 findings → 29 after the lead's decisions (status file a6b94e4c; flagcause citation removed). The SMT word rule and the inbox file of the rest are doing (w6).
- int3 d1eb5fb5b: gate_assumptions 51 → 29. The lead decided the 28 blocks of journal/inbox/l3a-int3-assumption-blocks.md:
  - 5 negations reworded to positive checks;
  - 3 cite OWNER ids (ASM-003, ASM-006);
  - 7 are SMT identifiers; L1 is asked for a prose-only gate rule (blocked, L1);
  - 3 are L3b's (README A3, gate_fpexact);
  - 2 are L2's (f2i_check);
  - 5 real L3a assumptions: REMOVE ids requested from L1 (R1 unhandled CONTEXT, R2 IP-to-state consumer, R3 callee eax reads, R4 _XcptFilter code, R5 fmath table bound). The removal work is owned by L3a;
  - LOCK-SPLIT's id was asked of L1.
- w5 07ff95cd (claude/fnv-l3a-lift-w5-flags-83f, on 83f7504f, no identity rule): test_gate_flags 9/9, test_gate_lift 28/0. Sent to the orchestrator for the candidate. Done.
- Flags census, 8 images @ facts a0f3d8e, gate 07ff95cd:
  - Totals: 154 898 entries, 6 568 999 instructions, 573 267 flag reads, 15 630 red, 557 637 certified, 680 128 no-form rows, 3 919 undecodable ranges.
  - Red per image: exe 14, binkw32 1 (the cpuid probe), atimgpud 3, gdffalloutnv 2, libvorbis 9, libvorbisfile 3, d3dx9_31 4 355, d3dx9_38 11 243.
  - The d3dx9 counts wait on w7's next facts. Next: the cause table, the exe's 14 first.
- int3 c482f3c4e: gate_assumptions 29 → 20. vd28 is running (doing).
  - L1 minted ASM-226..231 (REMOVE, owner L3a): unhandled CONTEXT, IP-to-state consumer, callee eax reads, _XcptFilter code, fmath bound (+ fmathtest), LOCK-SPLIT.
  - L3b: ASM-320/321, ASM-801, ASM-331 (the SDM-prose reference A3).
  - L1's prose-only gate rule clears the 7 SMT blocks.
  - ehtables:268 (reloc consistency) waits on w7's PE table: the rule is sound only for images Windows may relocate.
- Flags cause table (a0f3d8e, 07ff95cd): of 15 630 red reads, i no-form 10 582, ii undefined 2 037, iii entry 2 992 (all unknown-consumer, no jtab), iv call 17.
  - The exe's 14 red reads:
    - 10 zf-je reads after calls: rule = interprocedural flag summary (join over the callee's ret sites), to be implemented by w5;
    - 2 adc cf at the class-3 data-pointer entries (w7's rule);
    - 2 pushfd AF (the invariance proof).
  - The no-form mnemonics on those paths are next (x87 fnstsw/sahf expected).
- L1 w14 f4c04b92: lifted roots from a generated stage (cxx_origin {lifted_by, stage}). w8 applies it to the 3 test pairs (doing).
- int3 bbb12d43c: gate_assumptions has 10 findings: 7 SMT blocks (cleared by L1's prose rule), ehtables:268 (w7's PE table), f2i_check ×2 (L2). vd29 is running (doing).
  - New L1 record asked for fmath's "n ≤ 2^15 limbs".
  - ASM-231 is to be retired: LOCK-SPLIT is a rule, not an assumption.
- w8 6b3cb220 (claude/fnv-l3a-lift-w8-t16c, on w14 f4c04b92): 6 test roots, with no error in the generated stages or plugins. Open errors:
  - lift/rt not admitted (int3);
  - link.script outside the root (L1);
  - ldcheck tool-root check (L1, recipe.py:589).

## 2026-11-12 09:05
- w8 disptest on lift/bridges: blocked. The root does not exist yet, and its three base lines conflict in L1 files: w14-src, w20-descs (recipe.py/admit.json/RECIPE.md) and int3 (gate_lift.py). Owner: L1, asked for one merged line. The root design is accepted (a bridgegen stage `uses` descs, forbid_sections .fnvnx.disp). fnvl_host.h comes from L5's platform root through a cross-root include, not as a copy. w8 has moved on to the L2 cl-matrix comparison.
- w8 done: gen.py stdcall/fastcall/thiscall rows d9c91cc0 (disp2), t16c 6b3cb220.

## 2026-11-12 09:10
- Item 29 (w7, census bb80c6a on facts a0f3d8e, 7 images):
  - Global counts:
    - FCW loads: 934 proven-masked / 0 unmasking / 167 unproven;
    - MXCSR loads: 1 / 0 / 9;
    - sticky-bit readers: 3 109;
    - RC at import call sites: 8 505;
    - import-return start points that reach an escaping reader: 19 897 of 20 566.
  - Decision: delete OWNER_ASSUMED, the default path and the I-FPMASK / I-FPSTICKY keys. The default is the full x87/SSE guest state:
    - FCW, the status word with its sticky bits, and MXCSR;
    - #MF/#XM raised through SEH.
  - The memory-word value flow and fown/fpreach become static narrowing only, after the first frame unless the budget requires them. The next facts commit stays w7's priority 1.
- The 3 exe class-3 entries: decided by the class-3 data-pointer rule, in the next facts commit.
- int3 + w14-src cefa39a3: w6 merges it (6 gate files conflict; verify/gate_lift.py goes to L1). The link.script fix (a) has been asked of w14. w6 has L1's gate_assumptions edits (prove.py :15/:76, shapeprove.py:15).
- Blocked: w7 waits on pcsrc.run_facts (w4, chased).

## 2026-11-12 09:12
- Next facts commit (w7, ETA about 90 min), in this order:
  - the jtab regeneration for 8 images;
  - the ranges invariant, with the 3 L1 sites;
  - the reloc-consistency rule;
  - the ptr-candidate refusal;
  - the class-3 rule.
  - The PE facts: the exe has no DYNAMIC_BASE, and all DLLs carry a .reloc. A misfit under proven flow is red in every class.
  - Added for L5 (blocked): data_in_code.tsv per image, and the runs split around those ranges. L5 measured 16 714 relocated words in 1 452 runs over 6 images at a0f3d8e; the target is 0, each attributed to a rule.
  - The gpa/loadlib A7 and ret-predicate facts come in the commit after.
- ehtables.py:268: a real assumption. L1 has been asked for a REMOVE id; the removing fact is reloc-consistency plus data_in_code (w7). w6 cites the id.
- w4: pcsrc.run_facts is 2b0797ff, sent to w7. The d3dx9 pcsrc runs are in progress; the points-to coding starts in parallel.
- Bridges: L1 w20 builds claude/fnv-l1-contract-int-l3a (6d3a2800 + w14-src2 + w20-descs + int3). The cross-root include of fnvl_host.h is legal under the header-home rule.

## 2026-11-12 09:15
- Rule DIC (data in code) for L1/L5, implemented by w7 in the next facts commit.
  - Data ranges and entries are two independent over-approximations of the constant image bytes, not a partition.
  - Data range: a range that a proven consumer reads. Its kind and extent come from the consumer: jtab via idxrange, a pointer table, a string or struct by import contract (OWNER 3), a constant, an EH table.
  - R3 entries from a relocated pointer stay candidates while the value's flow is unknown. The points-to removes those whose value reaches no control transfer.
  - Reloc consistency in all images: refuse the candidate, and red under proven flow.
  - Gate: in a proven-flow run every relocation is an operand.
  - Open and counted: VirtualProtect/WriteProcessMemory/FlushInstructionCache sites that may target image ranges. The points-to removes them.
- Input: L1/L5 measured 16 714 non-operand relocated words in 1 452 runs (6 images, facts 2eeea27), plus d3dx9_38 strings decoded as code. L5's gpa_names, pemanifest and argflow are red until the commit.

## 2026-11-12 09:18
- ASM-233 (REMOVE, L3a), minted by L1 for the ehtables.py:268 premise. w6 cites it, and w7's facts commit removes it. The next free id is 234.
- With L1, on DIC: (1) ranges with kind and derivation, and (2) 0 in-run non-operand relocations over all runs are accepted. (3) "R3 refuses entries at data referents" is declined as unproven: R3 refuses only when the points-to proves no control transfer. L1 is asked whether imagerec needs no overlap structurally; if so, it goes to the orchestrator as an open class.

## 2026-11-12 09:20
- L1 accepted DIC.
- New id: ASM-234 (REMOVE, L3a), for the VirtualProtect/WPM/FlushIC sites on image ranges. Its removing fact is w4's points-to. w6 adds the record; w7 counts the sites in the facts commit.
- data_in_code.tsv will be pinned in the facts manifest (same tree_sha256) for contract/imagerec. The next free id is 235.

## 2026-11-12 09:24
- w5's flags call summary 2f94d41d is accepted (callee summaries joined over the exits, least fixpoint, C for unknown). Census @ a0f3d8e:
  - red 15 614 of 573 267 reads (was 15 630);
  - exe 14 → 4: 2 adc cf at class-3 entries, which w7's class-3 rule removes, and 2 pushfd AF, which wait on the invariance proof.
  - Per image: binkw32 1, atimgpud 3, gdffalloutnv 2, libvorbis 3, libvorbisfile 3, d3dx9_31 4 355, d3dx9_38 11 243.
- L1 w20: claude/fnv-l1-contract-int-l3a 2d215f16a (6d3a2800 + w14-src + w20-descs + int3 3b31bdc5).
  - w6 runs the verdict there and feeds int3's newer head (4115a256a + w5 + ASM-233/234) back.
  - w8 builds t16c and the lift/bridges root on it; w14-src2 445e551d carries the link.script fix.

## 2026-11-12 09:27
- int3 4115a256a (w6):
  - prove.py and shapeprove.py edits done.
  - Correction: formgate.py, gate_fpexact.py and formgate_inputs.json are now the trunk's 862b0708 versions (model_sha256 70c8dc39), and ASM-322/323/325 are back in the registry.
  - The gate_lift fixtures carry cli.py.
  - w14 merge test 7424c1f5b: test_gate_lift all pass, flags 9/9, order and eh OK. gate_assumptions 132/143. It is superseded by int-l3a.
- Being deleted on int3 (doubles): lift/proof/tests/test_formgate.py, test_fpexact.py and fakeext.py. test_rtroot will read the lifted flags through recipe.py.
- Reported to L1: the run_tool key hashes only the gate file, not its import closure (cli.py), so a stale cached result is possible. Proposed rule: the key covers the tool root's tree.

## 2026-11-12 09:29
- Item 16 (w8): claude/fnv-l3a-lift-w8-t16e 6810a4e9f = t16c + disp2 on int-l3a 2d215f16a.
  - recipe.py check: lifted test roots 3/3 valid (citest, imptest, msvcrttest); native roots 0/3, because link.script needs w14-src2 445e551d.
  - w20 is re-merging int-l3a with 445e551d and the new int3 head. lift/bridges is in progress on t16e.
- L3b agrees to the deletion of the 3 lift double tests. Their 83 tests (61 double sites) are being rewritten onto the real files on claude/fnv-l3b-lift-w2g-rewrite; that waits on L2 649a2960.

## 2026-11-12 09:33
- Facts commit (w7): the code is done, and all 8 images are being regenerated.
  - jtab: atimgpud has 35 sites, 34 tables, 1 red (a0f3d8e had none).
  - Ranges invariant: the atimgpud sites are code no entry reaches, so they are data-unreached by the residue rule (ASM-225). exe 0xda284a is already in range.
  - New: rangecheck.py, and the producer invariant "explore reaches nothing outside ranges = 0".
  - Reloc consistency: DLL strict, exe weak, with a fixed point with R3. The exe's 3 class-3 entries retire by the misfit rule.
  - Withdrawn: "refuse every no-flow ptr candidate". It contradicts DIC (b), so those candidates stay until the points-to.
  - No-flow ptr candidates, per image: atimgpud 292/621, binkw32 429/944, d3dx9_31 10 672/17 879, d3dx9_38 24 765/36 688, libvorbis 333/955, libvorbisfile 159/667, GDF 50/305. This is w4's work list.
- L1 w17 takes the cache-key defect: a static import closure, with dynamic imports red.

## 2026-11-12 09:35
- The lift/bridges root is in: w8 t16e 135da9286, recipe-valid. It is a bridgegen stage with `uses` descs, forbid_sections .fnvnx.disp, and an include edge to platform/include (fnvl_host.h, no copy). Root status:
  - roots valid: lifted 3/3, bridges, descs;
  - native 0/3, waiting on 445e551d in int-l3a.
- The bridges build, disptest and global descriptor count are a farm 1 job (RUN from w8).
- L1 agrees that a site counts only inside the entry ranges. w18 re-pins to rangecheck in the next facts commit; at w18 b9944d38, 0 of 43 dynsites rows lie in unreached bytes.

## 2026-11-12 09:37
- int3 901c3c54e (w6) contains:
  - 4115a256a and w5's 2f94d41d (cherry-picked);
  - 49457db77;
  - ASM-233 cited in ehtables real();
  - the 3 double tests deleted;
  - test_rtroot reading through recipe.py. This found and fixed a real defect: lift/rt fnvnx_ftoi args {} → {"mode":"native"}.
- gate_assumptions: 9 findings. 7 are solver-term, waiting on L1's lexer fix; 2 are f2i_check (L2).
- ASM-234 lands in w7's facts-side commit together with its citing file.
- The verdict on int-l3a 2d215f16a is running (w6).

## 2026-11-12 09:41
- L2's 3f85ad5aa (fpexact asm ranges) does not apply to int3, because its base predates the trunk gate_fpexact restoration. L2 is asked to rebase onto 901c3c54e as check 3d.
- w6 on int3:
  - drop -mabi=ilp32 from lift/pack/build.json (flags.semantics supplies it);
  - point rt's include path from shim/include, which has no tracked file, to the header home.
- Top-level asm (enter.cc, unhandled.cc): no change until L2 says whether 3d needs function-level asm.

## 2026-11-12 09:46
- int3 90f52d98e (w6):
  - The liftgen.recipe() generator now emits lift/pack's build.json by rule. Per-root flag classes only; semantic flags come from flags.semantics; the full plugin set uses LIFTED_ARGS.
  - lift/rt includes from platform/include.
  - recipe check: lift/pack valid on int3; lift/rt valid on int-l3a 2d215f16a (on int3 alone it waits on the platform root).
- Open before acceptance:
  - proof that -mabi=ilp32 stays in the resolved pack flags (readelf ELF32 ILP32), per L3b's check;
  - lift tests compile with their own PACK_CXX_FLAGS list. That is a second flag source, so they move to the recipe's resolved flags.
- Top-level asm: L2/L3b close it by class (the plugin brackets top-level asm, so every executable byte lies in a function or asm range). No conversion.

## 2026-11-12 09:50
- w8's env passes envcheck. Recipe builds on t16e 6a6c2d265:
  - imptest/lifted green (3 objects);
  - msvcrttest/lifted green (3 objects);
  - citest/lifted red at its generate stage, because the gmpy2.libs mount is missing (an L1 class defect: the mount must cover the wheel's whole RECORD). Routed to L1.
- ILP32 for lift/pack: flags.semantics on int3's base starts with -mabi=ilp32. w8 builds one pack unit for the readelf proof.
- ASM-234: the record lands in w6's merge of w7-merge into int3, together with dic.py, its citing file. used_by is re-derived per id (ASM-233's status per L1's rule).

## 2026-11-12 09:53
- L2 asmfp 029d6bb9 is rebased onto int3 901c3c54e.
  - Check 3d (asm ranges) sits beside the trunk's 3c, and ASM-218 is removed by gate:fpexact.
  - Two gate_fpexact fixes: the /src/ unit prefix and the f2i.json path.
  - test_fpexact_asm 20/20 on real recipe-built units.
- Declined: "convert top-level asm to function-level". It is not semantics-preserving for the enter.cc WRAP entry wrappers (it adds a compiler prologue, and aarch64 gcc 13 has no naked functions). L2 is asked to have the plugin bracket toplevel asm nodes as recorded ranges (L3b's class rule), or report an open class.
- The gmpy2 mount defect goes to L1 w18: the module file set = the wheel RECORD, which drives the mount, the key and the digest.

## 2026-11-12 09:56
- int3 262f4d060 (w6): one flag source. liftgen.resolved_flags = recipe semantics + pack root flags. PACK_CXX/C_FLAGS are deleted, 13 test files moved, and 0 private lists remain.
- BLOCKED, owner L1 w17: lift/pack codegen. The pin's flags.semantics lacks -ffreestanding, -fno-exceptions and -fno-asynchronous-unwind-tables (and -fno-unwind-tables), which are semantic for lifted code (L3's own EH). lift/pack is held: recipe-valid, but not to be merged as a product root until w17's origin-keyed semantics are on the base. L1 is asked to land w17 now.
- w3: the -I proof moves to libvorbisfile (fast); the binkw32 runs continue in the background.

## 2026-11-12 09:58
- ILP32 proof (w8), on int3 90f52d98e:
  - recipe.py build of a lift/pack unit is green; the object is ELF32 AArch64.
  - The resolved argv leads with -mabi=ilp32, then the semantics, then 8 plugins with ftoi mode=lifted.
- t30 on int-l3a 2d215f16a: test_gate_lift, flags, order and eh all pass. vd29 and vd30 are still running.
- L1 w17 (7ecf2e25) is accepted and goes into the next candidate (ETA one verdict cycle). The pack hold lifts when it is on int3's base.

## 2026-11-12 10:00
- Reproducibility PASS (w3): universe.py and ehtables.py under python3 -I at the producer f12c75b9d (clean worktree) give 10 of 10 outputs on libvorbisfile bit-equal to facts a0f3d8e. The exe and the larger modules are not run (their runs are long; that is a farm candidate if needed).
- The -I proof at d77f58ef passes on libvorbisfile. The codemap gate is red there only on the 882 s4-unchecked bytes, which w4's pcsrc closes in the next facts.
- liftall = liftgen.py finish/emit (there is no liftall.py on any L3a branch).

## 2026-11-12 10:05
- Invariance proof (w5 ecdcd89a, lift/gates/invariance.py) is accepted, and w6 cherry-picks it into int3.
  - Two runs with free values for the undefined flag. A site proves green only when the states coincide; every escape is red.
  - Tests: flags 9/9, gate_lift 28/0.
  - Result @ a0f3d8e: 0 real pushfd sites proved. They fall in two classes, both owned by L3b:
    1. cpuid has no Form (01441246, 0141479c, 01ceb353, 00ed193c...);
    2. the CRT context capture (exe 00ecf3c8, atimgpud 01412ad8/01422276, libvorbis 01ce3aff): the undefined AF leaves through a CONTEXT into a callee. By the orchestrator's decision, it is defined by L3b's per-(form, flag) reference-CPU cert.
  - w5 sends the global (form, flag) work list.
- w4: pointsto.py v1 is written (TOP correction) and in its first smoke run on atimgpud. The pcsrc d3dx9 counts are still running.

## 2026-11-12 10:08
- cpuid Form (L3b, ETA about 2 h): (eax, ebx, ecx, edx) ← CPUREF(eax, ecx) from rt/cpumodel.c, everything else unchanged, certified against Sail with the leaf as an uninterpreted function. w5 wires it into invariance.py.
- Undefined flags in proven code: L3b proposes refinement against the pinned model (ASM-009); a reference-CPU characterisation would be sampling. L3a endorses it with rule DEF0: one constant, 0, for every architecturally undefined flag output of every Form, with no per-pair table. This is sent to the orchestrator for a decision. w5 prepares the check over all Forms (emission ≠ 0 count); the emission is unchanged until accepted.
- 10:10: the orchestrator accepted DEF0, and the per-(form, flag) reference-CPU cert is withdrawn. w5 implements the DEF0 emission in the lifter by rule, plus the all-Forms check. L3b certifies in the next forms cohort.
- 10:12: companion rule DEFPRIOR. An architecturally undefined non-flag output (e.g. bsf/bsr dest with a zero source) keeps its prior value. It is a refinement certified with witness = pre-state, and it matches the reference CPUs. Sent to the orchestrator. w5's all-Forms check covers DEF0 and DEFPRIOR, counted separately; w4 (L3b) counts the class.
- 10:14: the orchestrator accepted DEFPRIOR. The final set is DEF0 (flags) + DEFPRIOR (non-flag outputs); an uncovered undefined output is red. The basis is refinement of the pinned model only. w5 records it in lift/README.md with global counts.
- cpuid cert: the pinned IR 70c8dc39 lacks cpuid (0f a2). L3b does closure-keyed certs plus one batched IR bump (ACL2 x86-cpuid), ETA 3-4 h. Global: 34 cpuid sites over 7 images, one shape key.
