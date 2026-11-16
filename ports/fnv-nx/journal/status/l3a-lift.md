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
| 28 | Assumption records owned by L3a (L3b w3 table, ASM-L3B-xx). A-STACKPRIVATE (ASM-L3B-20) is removed by deletion: no PLAIN class has been emitted since c0d18b76, and every guest access is GM/LKA. The dead memclass tools (tools/memclass.py, test/memclass_det.py, verify_r9/compare.py, verify_r9/hunt.py) are deleted after a consumer check. The removed_by lines of the other 18 L3a records are checked against the code. The fnvlift citations move to the L1 w10 ids once they are issued | w3 session_01KmtZKSV4DsNmobaEQ2Fzvq (dead code, removed_by lines); w9 session_01KmtZKSN8ztLNERy9161gg7, branch claude/fnv-l3a-lift-w9 (citations) | ids fixed by L1: the 3xx ids map 1:1 from ASM-L3B-nn, A-STACKPRIVATE = ASM-319, which L3b corrected. L1 03:28: no renumbering (the range binds the minting layer, so 3xx stays L3b-minted with owner L3a). w9 721e7d6: 49 citations -> 0, 12 files; being checked against the w3a registry aliases; A-STACKPRIVATE dropped. Reserved: ASM-200..202. w3's part is queued after its current item; L1 03:29:24 final: ranges keyed by owner, L3a records = ASM-203..220 (3xx kept as aliases), A-F2INATIVE = ASM-801 (L2), A-STACKPRIVATE dropped. w9 cb837327 maps from the w3a 326f2c8e aliases: 49 citations -> 0 in 12 files, every id in the registry. MERGED into the lead branch; w3 (a) 5e1f7529: memclass tools and verify_r9 deleted (grep 41 -> 21, every remaining hit outside code); (b) b0d33a0d: of 18 records, 14 still true, 205 FPSTICKY wrong (the product keeps the flags; 2 test injections to delete), 217 DECODEFLOW has no consumer (drop), 207 and 219 partly closed; lines sent to L3b; L3b's unit carries fnvlift at 82b45c2c, so w9 makes a citation-only commit on that parent (branch w9-unit, map from w3a a909a355); w9 100ec83b, the unit commit on parent 82b45c2c (4 fnvlift files, 16 lines, citation tokens only, map from a909a355); next: the core.py TAB_BIAS citation (record later deleted, TAB_BIAS replaced by jtab facts) and fpfacts -> ASM-204; w9 783aca41 (fpfacts ASM-204 kept, the TAB_BIAS record deleted by w8 on base3 3e035ada1): gate_assumptions at w3a c1f9100e gives 0 errors in lift/fnvlift; 100ec83b + 783aca41 are with L3b w3 to cherry-pick |
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

## 2026-11-12 10:17
- int-l3a 380e3cde3 (with w14-src2): the L1 verdict is red, 1136.7 s, 40 gates; the product roots don't build ("missing result file … build.json"). w8 triages per root build (with w6), with a table by root cause; L1 gate defects go to L1 by name. lift/pack is still held on w17.
- int3 8e40a0e54 = int3 + w5's invariance ecdcd89a.
  - test_gate_lift is all pass.
  - test_gate_flags has 1 red: pf_prove_no_form picked its "no Form" example by mnemonic (cpuid), and int3's forms now cover cpuid. That is measure-and-pick, so w5 derives the example from the real Form set by rule. Not parked.

## 2026-11-12 10:22
- int-l3a 380e3cde3 has none of int3's recipe fixes (901c3c54e..8e40a0e54), so the lift/rt and lift/pack reds are refused recipes. w20 is asked to merge int3 8e40a0e54. w8 triages the other roots.
- L1 w19 derives the selftests universe from admit.json. lift/fnvlift, lift/test and lift/tools declare selftests and have no tests/ dir.
  - Decision: each lift/test test file moves to the tests/ of the root it imports (static import graph; ambiguous cases go to the lead).
  - lift/test keeps only harness and drops selftests, or is removed if empty.
  - w6 on int3.

## 2026-11-12 10:26
- Triage of int-l3a 380e3cde3 (w8, per-root recipe.py builds on t16f claude/fnv-l3a-lift-w8-t16f):
  - GREEN:
    - platform (114 objects, full plugins);
    - shim, hostabi, translator (headers only);
    - the lift/bridges check;
    - the lifted test roots 3/3.
  - RED:
    1. lift/rt, lift/pack: int3 fixes not merged (w20 merges 8e40a0e54); pack is still held on w17.
    2. lib/sha256: a stale recipe form (semantic flags per root, no plugins). Owner L1 (402b50fdb).
    3. plugin_errors judges a host variant by its guest base root: an L1 gate defect, reported.
    4. Native test roots 0/3: rt/guest not admitted. Owner L5, asked.
- DEF0 is in L3b's prover (w4 4a9eabc91). At the shapes pin, 3 850 int keys (360 131 sites) have undefined flags. w5 sends w4 the context bytes for the CRT sites.

## 2026-11-12 10:30
- int-l3a 55287b9f4 (w20) = w14-src2 4de423f0f + int3 8e40a0e54 + w8's 4 bridge files. test_recipe, admit, plugins and gate_lift pass; the verdict is not run.
- rt/guest is admitted on L5 claude/fnv-l5-platform-root (head e2152adf5). w8 builds t16g = int-l3a 55287b9f4 + L5 e2152adf5, then checks the 7 test roots and builds lift/rt.
- Selftests split (w6). Rule: direct imports.
  - lift/tools/tests gets 13 files;
  - lift/fnvlift/tests gets 6, plus the 2 mixed ones (module under test = fnvlift);
  - lift/test keeps the instest harness and 14 others, and keeps selftests.
  - "Reference" is per L1 w19's gate definition.

## 2026-11-12 10:33
- Item 16: all 7 test roots are recipe-valid on w8 t16g f0c65c004 (int-l3a + L5 e2152adf5, rt/guest admitted): bridges, the 3 lifted, the 3 native.
- Next for w8: merge int-l3a 55287b9f4, build lift/rt, build the native test roots plus platform and rt/guest (local or farm 1), then run the 6 guest tests with references.
- Selftests split: L1's closed rule replaces my hand choice. A multi-root test goes to the top of the root dependency order; a diamond or cycle is red and gets split per root.

## 2026-11-12 10:38
- Test line t16h 37d4e27df (w8) = next3b 224af83e + int-l3a 55287b9f4 + L5 e2152adf5 + w8's commits.
  - recipe check: 9/9 valid (the 7 test roots, lift/rt, lift/pack).
  - lift/rt builds green: 12 objects.
  - The native test roots are building.
- Dispatch-plugin def-descriptor fix (no row for a shapeless definition): not accepted as stated, because a native definition whose address escapes would get no row. Tightened rule: no row only when address_taken is false (static); otherwise DISPATCH1 stays an error. w8 adds the 2 cases.

## 2026-11-14 23:10 (resumed after the usage limit; path only)
- The candidate claude/fnv-next3b 141b07580 carries L1 w17's lifted flags (7ecf2e25), so the lift/pack hold can lift.
- w6: merge 141b07580 into int3 (a3e8f7e4f, which has the selftests split: 13 files to lift/tools/tests, 8 to lift/fnvlift/tests). Show the lifted flags in the resolved pack argv, then the rt/pack checks.
- w8: t16k = 141b07580 + int3 + the t16h commits. Then the tightened def-descriptor rule, builds of lift/pack (product root), lift/rt and the 7 test roots, and the 6 guest tests against references.
- w5: the pf_prove_no_form fix and DEF0/DEFPRIOR. w7: the facts commit status was requested.

## 2026-11-14 23:20 int3 ec3509b19 (w6): lift/pack can lift
- ec3509b19 = a3e8f7e4f + closed-rule moves + w5 2aedc634 + merge of next3b 141b07580 + recipe adaptation. recipe.py: lift/rt valid, lift/pack valid. plugin_cxx removed; plugin args from recipe.plugin_args('lifted').
- The resolved lift/pack flags have 42 tokens from one source (flags.semantics, origin=lifted): -mabi=ilp32 first, -ffreestanding -fno-exceptions -fno-asynchronous-unwind-tables -fno-unwind-tables, then only -std=gnu++17 -O2 -Werror=attributes. The hold on lift/pack is lifted.
- Selftests at ec3509b19 (global): lift/tools/tests 15, lift/fnvlift/tests 6, red 4 (fnvlift↔proof import cycle), lift/test 16.
  - Decision: break the cycle (fnvlift must not import proof; the edge proof→fnvlift.dsl stays). The acyclic root graph becomes a check.
  - instest goes to tools by the rule.
- References: 21/21 moved tests have no oracle.json entry. w6 gives a table of the independent source per test; "none" rows go to L1. No invented references.
- Baseline: 8/21 moved tests fail, the same as in the old layout. Causes:
  - 3 use stock g++ without plugins (a second build path); they move to recipe.py.
  - 1 has a missing PRELUDE import.
  - 3 use undeclared inputs (a home path, FalloutNV.exe); these become externals.
  - 1 has an argv defect.
  - Owner: w6.
- w8 builds t16k on ec3509b19.

## 2026-11-14 23:20 t16k acd4c7500 (w8)
- t16k = t16j + next3b 141b07580 + int3 ec3509b19.
- recipe.py marks 12/12 roots valid: lift/bridges, lift/descs, lift/rt, lift/pack, platform, rt/guest, and the 3 lifted and 3 native test roots.
- Tightened defdisp rule (b0198f906): no row only when the definition is not public and its address is not taken; every other shapeless definition is DISPATCH1. defdisptest: 245/245 green, plus 3 new cases.
- int3 test lines: gate_lift, gate_flags (10), lock_only and ranges (5 records) all pass. ranges had a latent parser bug; it now parses by section header.
- Blocked: the 3 native test roots stop at link. libgcc unwind-dw2-fde needs pthread_mutex_lock/unlock, and nothing defines them. Owner: L5 (rt/guest mutexes over its real threads). A single-thread libgcc is rejected.
- Running: the builds of lift/rt, descs, bridges, the 3 lifted roots and lift/pack, then the guest tests against their references.

## 2026-11-14 23:25 t16k 420a459ba (w8)
- Green: lift/rt (12 objects), citest/lifted, imptest/lifted (3 objects), msvcrttest/lifted (3 objects).
- Red: lift/descs, lift/bridges, lift/pack. The stale lib/sha256 recipe (L1 w15c) stops the producer chain.
- The 3 native roots link with sysbase (L5's single library set, w10) and the pinned crti/crtn. 7 loader hooks stay undefined. Blocker: loader/ (claude/fnv-l1-contract) is built by a Makefile/build.sh, not as a product root. Sent to L1.
- int3 d206ba904 (w6): the fnvlift↔proof and gates↔proof cycles are broken by moving code. L1 takes the acyclicity check into gate_tests (w19), and our test_root_graph is then deleted.
- The 6 guest tests cannot run before the native roots link.

## 2026-11-14 23:30 path plan 2026-11-14 (integration 21b10f1aa; CLAUDE.md merged at ccee6a5fd)
Workers are capped at 3. Assignment by plan item:
- Active:
  - w8: 2.1 t16k, then 3.4, the product ELF root (undefined closure ∅; L1 link/guest.json).
  - w6: 2.2 (acyclic graph done at d206ba904; instest, references, 8 build-path defects), then the DEF0/DEFPRIOR merge from w5, then 2.5 synchronous exceptions and the masked-exceptions fact.
  - w7: 2.4 facts commit, then stops.
- Queued: w1, 2.3 product relift of 7 images. It starts on w7's facts commit, and its shape-key list goes to L3b at once.
- Stopped (capped): w5 (DEF0 done; handed to w6), w4 (pointsto v1 and pcsrc counts, off the path now), w3 (binkw32 jtab goes into w1's relift).
- Blockers: the loader as a product root (L1 w24); lib/sha256 recipe (L1 w15d, ETA ~23:55).
- w5 DEF0 counts at facts a0f3d8e on the 83f base: Forms 17276, with free outputs 3444, DEF0 5270, DEFPRIOR 0, uncovered 0. Not yet re-measured on int3.
- Stop states, with commits:
  - w5: claude/fnv-l3a-lift-w5-def0-int3r 2daa52e62. Gate tests 11/11 + 3/3 pass; def_check reports free outputs as uncertified and red, owner L3b. w6 merges it and measures the counts.
  - w3: claude/fnv-l3a-lift-w3-i d77f58ef. libvorbisfile -I reproducibility is 10/10 at f12c75b9d; that is 1 of 8 images, not a global result.
  - w4: claude/fnv-l3a-lift-w4 10226a84. pointsto v1 is a draft, not wired to a gate; the pcsrc global counts were never finished.
  - w1: holding at claude/fnv-l3a-lift-w1 d6235c0f5.

## 2026-11-14 23:35 int3 62b8a2c39 (w6) and the item 2.8 split with L3b
- int3 commits:
  - 0e8692e85: instest and 6 others moved to lift/tools/tests; lift/test keeps 9 files.
  - 62b8a2c39: reference table, 34 rows (fnvlift 3, gates 4, proof 10, tools 17). 9 rows have pinned, checkable sources; oracle entries for them follow in one commit, and the other 25 stay red with their gap named.
  - universe_r3_check reads declared exe+facts and passes (67370 targets, 0 missing).
- Decisions:
  - modules.tsv becomes a recipe generated stage from contract/modules.py over the declared inputs; the home-path defaults are removed.
  - imp, msvcrt, exitbridge and disp take their objects from admitted test roots only (w8 builds them) and run on verify/bootmachine.py. No temp-repo build and no Unicorn; they stay red until then.
- Item 2.8 is split with L3b:
  - w6: imp, msvcrt, exitbridge, disp.
  - L3b w3: sehcodes, test_gate_lift, modlift_closure_test.
  - The 6 Unicorn instruction tests (instest, instest_extra, mmxtest, citest, x87dyn, x87test) are replaced by certified gate forms and deleted once their shape keys are certified. No second harness.

## 2026-11-14 23:40 SEH CONTEXT (item 4.5) with L3b w9 8a661c5
- The table has 70 fields: 48 round-trip, 14 ignored by Wine too, 8 defects in our restore.
- Agreed: struct cpu gets appended fields (full FSW with ES/B by SDM rule, FOP/FCS/FDS, EFLAGS NT). Owner w6, queued after 2.5.
- RF/TF and Dr0-7 are one class, "guest-written debug-trap state", closed by a site-value fact. It is the same analysis as 2.5's masked-exceptions fact, so w6 produces both, with global counts per image and class. Undecided sites go to reach_violation under the certificate rule.

## 2026-11-14 23:45 DEF0 defect: conditionally undefined flags
- L3b's w4 prover (9d25bb24f) fails shl/shr/sar cl and shld/shrd cl on AF at count 0. Our DSL declared AF unconditionally undefined, so DEF0 wrote 0 where the CPU keeps the prior value.
- Class rule: F.undefined(when=cond), with cond cited from the SDM; emission is cond ? 0 : prior. The prover checks both sides, so the condition is not trusted.
- Owner w6, moved ahead of the rest of 2.2 because w1's relift must carry correct DEF0.
- bsf/bsr with a zero source stay model gaps until the IR bump (L3b).

## 2026-11-14 23:50 process entry decided; the loader root is ready
- Loader root: L1 w24 claude/fnv-l1-contract-w24 528394ba, 9/9 units green. w8 adds it to link.roots.
- One entry path: rt/guest main (L5) → fnvnx_loader_main (boot.c's main, renamed by w24) → map/imports/TLS/DllMain/TEB → fnvnx_start_enter → fnvnx_lift_enter(AddressOfEntryPoint), defined by lift/rt.
  - fnvnx_exe_entry is withdrawn as a second path into the entry point.
  - main.c moves to its own root rt/guest/main, so test roots link rt/guest without it (the recipe has no exclude).
- int3 60980045d (w6):
  - The fault map is built from the enum, with codes ⊆ map checked; test_dsl_emit compiles 11574/11574.
  - def_check over 5 of 7 images (facts 163ec389): Forms 18572, free-output Forms 3828, DEF0 5895, DEFPRIOR 0, uncovered 0, when= 255; no_form 6464 shapes (544389 sites).
  - Open questions: OF of shifts and rotates at count > 1, 16-bit shld/shrd at count > 16, and the split of no_form by builder.

## 2026-11-14 23:55 int3 b6f5e6fec / 5c89f9c5c
- w6 b6f5e6fec:
  - shld/shrd w=16 (count > 16: dest DEFPRIOR, flags DEF0, both with when=).
  - cpufeat #UD is read from CPUREF: 339 shapes / 2601 sites over 5 images.
  - sreg is implemented by hand handlers (#GP on an unknown selector); its Form waits for L3b's IR bump.
  - bndldx is a NOP; its SDM anchor is pending (no pinned SDM text yet).
- w8 f41da7e10, 5c89f9c5c:
  - oracle 94/94, with test_nativerows certified through verify/TOOLS.md.
  - The merge-defect sweep restored next3b's shim/include/d3d9types.h, verify/recipe.py and unitstage.py (sandbox_src).
- Sent to the orchestrator:
  - CPUREF's feature set (SSE2-era, implementation-defined) to record as a declared-machine fact;
  - the logical-processor count: 1 in cpuid vs real parallelism. One count must hold for cpuid and L5.
- test_census needs lift/tools/CODEMAP.md. Owner w7 (DIC), in the facts commit.

## 2026-11-15 00:00 declared CPU (CLAUDE.md 99285b5fe, merged 23dc061fc)
- w8 44fab5e34 (int3): cpumodel has 3 processors (leaf 1 EBX[23:16]=3 + HTT; leaf 4 cores−1=2, L3 shared by 3). formgate_real, gate_flags and gate_lift pass.
- recipe check: int3 5c89f9c5c 6/6, t16k 11dcdf688 14/14 valid. loader (9 objects) and lib/sha256 (1 object) built. descs, bridges, pack and the native roots are building.
- test_rtroot is red on t16k (pin semantics vs the recipe flags); w8 is finding the cause.
- #UD certificate (w6, facts 163ec389, 5 of 7 images): 2601 sites (3DNow 2574 in d3dx9_38, SSE3 26, AVX 1) over 99 entries, 86 reached only through pointers. Orchestrator rule: a cell is decided when every writer depends only on cpuid/IPFP and constants, and its contents are evaluated under CPUREF. It is built as ctlstate on w4's points-to, the same analysis as the 59 undecided exe control-word writers. The gate stays red until it lands.

## 2026-11-15 00:10 int3 4681c9634 / 3f875c252 / 97fc6e77c
- w6 4681c9634 (item 4.5):
  - struct cpu appends fsw/fop/fcs/fds (sizeof 504, offsets static-asserted) and EFLAGS NT. Sent to L3b w9.
  - ES/B are computed at FSW-image points; the exactness argument goes into the docstring.
  - ASM-213/214 may be removable (asked L1).
  - The recipe build of lift/rt goes to w8.
- w8:
  - 97fc6e77c: cpumodel takes FNVNX_NPROC from hostabi/placement.h.
  - 3f875c252: L2 exitgen merged (16/16); test_exitbridge/disp/imp/msvcrt deleted with Fake.
  - Compiler-call census: 97 files, over-counted, by owner (inbox note l3a-w8-compilecalls.md). L3a's are moving onto recipe roots.
  - 6 x86 reference-host programs wait on L1's host-variant root.
- w7: the facts commit now also carries the EH ThrowInfo union rule (L3b w9: 1 site, 1 missing ThrowInfo) and CODEMAP.md.
- PERFDEBT PD-LIFT-ORDERED: accepted, scheduled after the relift.

## 2026-11-15 00:05 — host programs (L1 answer relayed to w8)
- The host x86-64 root exists (admit target "host", pinned g++-13-x86-64-linux-gnu). rcptest, rcpgen, rothost, bthost and fmathtest become host roots (w8, queue item e). Owner: L3a w8.
- proof/backends.py host C++ becomes the variant root@ubsan. libubsan1 is pinned by L1 through setup_verdict_env.sh.
- Blocked: running a built program as a stage (`run` plus host_cpu CPUREF). Owner: L1 + L6 w6. Until it lands, rcp tables from a direct run are measurements, not inputs.
- The L1 machinery files in the compile-call census are L1's to move.

## 2026-11-15 00:08: w8 item 1 done (int3 c1d4b6ad4)
- test_descclass: recipe-built (plugbuild stages, the recipe object as the reference), 0 failed over 28 types × argument/result.
- test_defdisp: recipe-built; the duplicate pairs are dropped, and clpod has moved to tools/tests/clpod. Direct compiler calls in both files: 0.
- test_defdisp is red on one defect: the covariant-return thunk has no pi early record (fnvnx_pi.cc:651, N3). Blocked on L2; the class was sent to the L2 lead. The rest of the test is green: POD matrix 245 cases, 0 mismatches.
- Census inbox note: journal/inbox/l3a-w8-compilecalls.md.

## 2026-11-15 00:12
- w8 item 2 done (int3 71adaa530): test_ranges (5 records) and test_lock_only are recipe-built, 0 direct calls. Next (w8): an admitted lift/tools/tests root holding descclass, defdisp, ranges and lock_only, after t16k.
- pi thunk defect: L2 w1 is fixing it on claude/fnv-l2-source-w1-pithunk (rule: every body that reaches SSA gets its record).
- L1 accepted 4681c9634's mechanism. Retiring ASM-213/214 is one commit with 3 points (stale text, a static_assert that FNVL_MAX_BASIC < 7 per SDM 8.1.8, the registry retire). Assigned to w6, after its ctlstate step.
- verify/cpuref.json (host_cpu pin for the `run` stage; microcode null = line absent on the VM host) plus a leaf-1 EAX identity check. Assigned to w6.
- 00:15 w8 (int3 00ef4468d): lift/rt is recipe-built on 4681c9634, green with 12 objects; test_rtroot is ok. L2's dispatch definition rule (37d4e27df, b0198f906) is carried onto int3. test_defdisp: 291 ok, 1 fail (the pi thunk; blocked on L2 w1). Store correction: GATE_CACHE only.
- 00:20 w6 (int3 286fdc939): ASM-213/214 retired, with aliases A-FDS and A-FOP. Registry 93 → 91. static_assert FNVL_MAX_BASIC < 7 (SDM 8.1.8). verify/cpuref.json (microcode null = no line) and the leaf-1 identity static_asserts; test_cpuref passes. Open: the lift/rt recipe build at this commit and a gate_assumptions re-run (base: 30 findings); both running in the background. pointsto: binkw32 hit OOM after pass 1, so w6 is moving it to interned cells and sparse sets.
- 00:20 w8 t16k 5673d80f8: int3 00ef4468d and loader 36713265 merged; the recipe check passes on all 12 roots; the run7 relink in GATE_CACHE is in progress.
- L3b cleared_sites (pack 7ef692b5): 2,575 SIMD sites, plus 26 fisttp in our census = 2,601. The certificate is w6's. The single AVX site in FalloutNV goes to w7's facts as a code-range question.
- 00:25 w6: lift/rt at 286fdc939 is green (12 objects; the static_asserts compile with msvcmodel). gate_assumptions after c09b32e87: L3a findings 4 → 0; 24 remain, all other layers' (L5 22, toolchain/f2i_check 2), forwarded through the orchestrator.
- 00:28 f2icheck (L2 1c9f04063, deletes toolchain/f2i_check): w8 merges it into int3. Expected gate_assumptions: 22, all L5.
- 00:28 The AVX "site" FalloutNV 0xe3d1f0 is jump-table data that pack 7ef692b5 decodes as code (L3b). Class rule given to w7: a table extent from its jmp bound is data_in_code, never an entry. w7 reports the global count of extents that intersect entries (must be 0). #UD set: 2,600.
- 00:30 int3 1c9f04063 (f2icheck merged): gate_assumptions 22 findings, all L5's. w7: facts regeneration done for 5 of 8 images; CODEMAP.md pushed at 38f447b4.
- 00:40 The first boot link (L4 bootguest) has 3 undefined symbols: fnvnx_dispatch, fnvnx_guest_current, fnvnx_lift_enter. All 3 are defined by the admitted root lift/rt. Its closure is lift/bridges, lift/pack and fnvnx_native_disp_table. The native table is the one gap: w8 adds it as a dispgen generated stage over nativerows. Next, w8 computes the undefined closure of bootguest+rt+bridges+pack via link/guest.json (3.4). Top of the queue after run7.
- t16k 4e66f6436: pi thunk fix f092a268e on int3 5e894bd3a; run7 restarted from scratch (the pi .so is in every key).
- 00:45 BLOCKED: fnvnx_native_disp_table needs native.tsv. Its inputs (obj/bind/bodysel from the exe facts and the pristine MSVC objects) have no recipe source. Owner: L1, proposed tool root contract/facts with output native. w8 writes the lift/rt dispgen stage when it exists. No placeholder table.
- 00:47 The native.tsv producer is assigned by L1 to L1 w22 (claude/fnv-l1-contract-w22-facts): the tool root contract/facts, output native. The objects come from native roots derived from admit.json (no cycle). Acceptance: byte-equal to verdict.py. ETA: today.
- 00:55 L3b asks (blocks sys/SIMD certification):
  (1) raise in a Form: answered. F.fault(kind, cond) exists; NTSTATUS/Eip per kind come from the one table in seh.cc (sehcodes.py).
  (2) x87/MMX/SSE Form state: the interface is frozen (fr0-7, ftop, ftag, fcw, fsw, pointers, xmm0-7, mxcsr; MMX aliasing by SDM 9.5.1). Assigned to w1, doing: the dsl bump in parallel with the relift, before the pack pin. Then the sse/x87 fallback shapes (3,894 + 2,034) become Forms class by class.
- 01:00 The final #UD class from L3b (1df9aed): 2,730 sites = 3DNow! 2,574 + Group 2 /6 119 + Group 3 /1 13 + fisttp 21 + AVX 1 (data) + x87 no-row 2. w6: join against our 2,600, explain the 5 fisttp sites, certify all of them; w7's facts decide the 135 first. Rule: the 134 no-SDM-row encodings get #UD only where certified unreachable or data; a reachable one goes to the orchestrator.
- w1: the bsf/bsr zero-source Form (L3b w4 IR: dest unchanged, ZF=1) comes first, then the x87/SIMD state.
- 01:05 UNBLOCKED: contract/facts (L1 w22 b3cc6f3b9) is green. Output native: 0 rows, derived (no admitted decomp unit binds a native symbol), byte-equal to the hand chain. w8 writes the lift/rt uses + dispgen stage.
- 01:10 w8: t16l 8569a4dc0 is on the L1/L2 recipe line; 14 roots are recipe-valid. run8 is a cold build in GATE_CACHE (keys changed). Blocked: the cycle lift/rt→facts→bridges→descs→lift/rt. Owner: L1 w22. Rule: transitive exclusion plus a check that excluded roots bind no exe address. recipe.origin_class fix sent to L1 to take.
- 01:15 facts cycle cleared (w22 c13ffa61b, transitive exclusion). Excluded roots bind no exe address: a downstream L1 gate, red on any bound symbol (proposed to L1). It cannot be checked in-stage without a cycle. Accepted.
- 01:20 w1 (int3 fafbdeaa, d935ecda): the DSL x87/MMX/SSE state interface is in, with per-NTSTATUS FP fault kinds 12..18. test_dsl_xsimd is ok. Corrections given: fpc_host.py → a host root without shims (a measurement until it is a root); the lift/rt recipe build at fafbdeaa; cite the tininess rule (SDM + SoftFloat; Sail decides); route the 2 test_symform base failures. Next: the fallback census, then the builders. Interface commit sent to L3b.
- 01:25 w6 join (7299b4153, facts 163ec389): both sides 2,590; 3DNow! equal (2,574). One-sided sites come from our per-range decode over overlapping facts ranges (d3dx9_38 17adfc0/17adfc1). New facts class for w7: disjoint ranges, starts on instruction boundaries. Census moves to per-entry decode. pointsto binkw32 still OOM (3 GB, pass 2); representation fix in progress.

## 2026-11-15 01:35: rules 1-3 pass (owner order "defects impossible by construction"), lift/ at int3 0ee1f9268
One bounded read-only pass over lift/. Each item gives its replacement.

### On the path: converting now
| # | item | cause | replacement | owner |
|---|---|---|---|---|
| P1 | pack/build.json has `plugins` twice (:19, :76) and a stale `plugin_cxx` (:71) | the generated file was hand-edited | regenerate from liftgen.recipe; a selftest runs liftgen and diffs | w8 |
| P2 | hard-coded workdirs and facts paths in pack generation: liftgen.py:145,258,266; modlift.py:31,376; impgen.py:130; pemod.py:7-8; lifted.py:63; fpmodes.py:13; fnvlift/universe.py:9; fpfacts.py:27; f_x87_ctl.py:17 | a second facts cache | recipe externals (/ext/…) and GATE_CACHE only | w8 |
| P3 | fault set: kinds in dsl.FAULTS (12..18) vs FNVL_* (13..19); NTSTATUS in seh.cc, dsl.FLT_STATUS, sehcodes.py ROWS and fnvl.h comments; int n vectors in f_sys.INT_VECTORS, forms_sys.INT_KIND and instest_extra | enumerated in several places from memory | ONE fault/vector table, cited per row (Windows 7 trap table as transcribed by ReactOS, Wine 9.0), which generates the seh.cc switch, the FNVL_* defines, dsl.FAULTS/FLT_STATUS and the test expectations; one numbering | w1 (with the int-vector item) |
| P4 | CPUREF restated: fnvl_abi.h:10-15, rcpref.h:7-10, cpumodel.c:5/47/55 vendor and brand | copies of verify/cpuref.json | generate fnvl_abi.h, rcpref.h and cpumodel's leaf tables by a recipe stage from cpuref.json plus a pinned cpuid dump | w1 |
| P5 | census vs lifter decode (the cause of the fisttp mismatch) | a second decoder | the census reads img.dec, the lifter's decode | w6 (doing) |
| P6 | second build/test paths: test/rothost.py, bthost.py, rcptest.py, x87test.py; tools/tests/fmathtest.py, instest.py, citest.py, sehcodes.py, x87dyn.py, mmxtest.py (Unicorn harnesses, direct gcc/ld); proof/tests/fpc_host.py; proof/backends.py host g++; test/golden.py (a second runner); tools/tests/clpod/gen.py `wine cl`; own caches in gates/tests/test_gate_lift.py and proof/gate_fpexact.py | second compiler paths, runners and caches | deleted (rule 3). Their checks return only as recipe roots: host roots, guest test roots on the bootmachine, and L3b's Sail certificates for the semantics. clpod's `wine cl` becomes a recipe stage with the pinned wine-debs, or is deleted | w8, after run8 |

### After the first frame
- A: hand-written mnemonic and opcode classes (core.py:289, fpmodes, x86flow, pcsrc, writeset, jtab, f_x87 tables, f_cpufeat FEATURES, gate_order A64 sets, gate_fpexact A64 sets, enc_table, tools/universe.py sets, codemap API arg kinds / noreturn, ctlstate CTX_IMPORTS, fpstate import names, entry/EH-kind sets in 6 variants). Derive them from the Sail model / DSL Form effects, the pinned SDM tables, the Arm ARM (capstone groups) and Wine .spec files.
- B6-B11, B13, B14: register/flag tuples, EFLAGS bit table, PRIV_MN, prefix set, EH keys, the gate fixture compiler name, raw capstone ids. Each gets one definition and imports.
- C18/C19: three universe modules, several recursive-descent walkers, three flag tools. One decoder/CFG (x86flow over img.dec) and one census; the others read their facts.
- C15: tools/reconcile.py reads fnv-nx-build through git show. Pin it as an external, or delete it.
- 01:40 w1: census at int3 0ee1f926, 7 images, facts a0f3d8e: 23,985 shapes / 6,188,060 sites; Form 17,538 / 5,547,503; no Form 6,447 / 640,557 (sse 3,951/122,064; x87 2,036/510,173; cpufeat-UD 332/5,155; f_int 48; f_sys 39; f_sse ops 35; f_x87 ops 4; emms 1; bndldx 1). Tininess → after rounding (SoftFloat 8086-SSE specialize.h:48); Sail decides. test_symform red since 1b4f04ea (L3a) → w1. w1 order: P3, P4, symform, tininess, then the SIMD-int builders.
- 01:55 w1 int3 4157be4e: the MMX/SSE2 integer SIMD Form class (449 encodings) is in. Corrections given: the Unicorn oracle half is deleted (a measurement only; the certificate is L3b's Sail requeue); no direct g++ -fsyntax-only; P3/P4 come before the SSE FP class. #UD list now 2,731 (L3b 796346c; adds 0F 1E /r at d3dx9_38:017d20e0, no-row).
- 02:00 RTTI BCD facts for L2 S23 (orchestrator request): w7, in parallel with its facts runs. Full RTTI set. Size and emptiness are not RTTI facts; reported to the orchestrator and L2. bsf/bsr fix at int3 426d95f8 sent to L3b w4. w1 now on P3 (reads L5 x86sys.json).
- 02:10 w7 RTTI BCD done: fnv-nx-build l3a-lift/rtti-bcd c11deaf35, tree_sha256 fa3a313f…7505, rtti_bcd.tsv; exe sha256 89e70204…0dd. Producer lift/tools/rttibcd.py 373c4100 via contract/exefacts.rtti_vftables. Global: vftables 3,269; COLs 3,269; classes 2,683; BCDs 18,630; virtual-base BCDs (pdisp != -1) 15; multiple-inheritance CHDs 827; malformed 0. Size/emptiness not derived (CodeView, L2). Sent to w3 and the L2 lead. Accepted.
- 02:10 w7 rangeinv.py e8747468 (ranges disjoint, starts and ends on instruction boundaries): new facts 0 violations on atimgpud, binkw32, libvorbis, libvorbisfile. Old facts a0f3d8e: d3dx9_31 19,784 overlapping decodes + 24 mid-instruction ends; d3dx9_38 101,469 + 3,895 (the cause of the census overlap). Exe, d3dx9_31, d3dx9_38 regeneration stalled ~50 min in the jtab resolve → w7 moves it to a farm (rule: >10 min) and reports the cause.
- 02:15 w1 P3 at int3 f67ed0cc: ONE fault record lift/fnvlift/faults.json (cited: ReactOS KiTrap, Wine 9.0 signal_i386.c); Form kind = runtime code (1..18 unchanged; runtime-only LOCK=19, DEOVF=20); dsl.FAULTS/FLT_STATUS, lift_dsl.FAULT_MAP and forms_sys kinds read it; the hand copies in fnvl.h, seh.cc, dsl.py and lift_dsl.py are deleted. Correction: the committed fnvl_faults.h and seh_faults.inc become a recipe `generated` stage of lift/rt (no committed copy). Compile evidence comes only from the recipe. Added to P3: f_sys.PRIV_MN deleted, read from L3b sdm_priv_rows.tsv (w3 d634aa82d, 53 rows); cond class from the declared CPU record. Open: INT_VECTORS/INT_KIND/SEL_* wait for L5 x86sys.json (not on the remote, blocked: L5). w1 → P4 meanwhile.
- 02:20 w1 P4 at int3 1e4a51c2: one CPUREF record. Identity in verify/cpuref.json, raw leaves and masks in verify/cpuid_model.json. lift/tools/gen_cpuref.py generates FNVL_CPUREF_*/FNVL_MAX_*/CPUID1, rcpref FNVL_RCP_REF_* and the cpumodel leaf tables; the restated constants are deleted. Corrections: the generated files become recipe `generated` stages (not committed); the declared masks are derived from the SDM feature→instruction table joined with Form coverage, not hand bits; the raw leaves are a transcription until a host-root `run` on host_cpu CPUREF (L1/L6 w6) exists. w1 order: symform, then the corrections, then SSE FP.
- 02:25 w7 PARTIAL facts commit fnv-nx-build l3a-lift/facts 6944080c (atimgpud, binkw32, libvorbis, libvorbisfile, GDFFalloutNV), tree_sha256 cd4fc60f…466da, producer e85a3b56. Counts over these 5: entries 3,490; candidates 1,121; refused 96; proven misfits 0; jtab ranges 183 (3,576 bytes); non-operand relocs in runs 0; protect sites 1; rangeinv 0/0. ASM-233 removed. Remaining d3dx9_31, d3dx9_38, exe: the jtab resolve recomputes each site's backward-cone dataflow per pass (sites × cone × passes). Rule given: one fixpoint per pass over the whole CFG (same equations, same answer), checked by identical output on the 5 images. The farm (L3a farm 1) runs the 3 images at e85a3b56 meanwhile.
- 02:30 L5 w14 x86sys.json at claude/fnv-l5-platform-w14-osrec 9d6f6d4bd (WDK 7.1 sources): the selectors, the ring-3 int n vectors and the idtr limit are decided. Undecided: sgdt/sidt/sldt values and es/ds/fs/gs←0x1b. → w1: finish P3 by generating these from the record; FNVL_GDTR/IDTR/LDTR (no source) deleted; global count of the sites that need the undecided values; any site goes to the orchestrator.
- 02:35 w1 int3 5dc881f7: test_symform green 15/15 (f0caf48c; the cause was normalize not substituting uwhen). Tininess after rounding (SoftFloat 8086-SSE specialize.h:48); the fx_tiny_after helper is exact in the tiny range, and the native op stays on the normal path. z3 vs helper: 103,481 cases, 0 disagreements (a measurement, host shim). ReactOS is out of the trust chain (L5): faults.json is re-cited from SDM/Wine 9.0/WDK; fnvl_seg_ok 0x1b becomes a record read. w1 order: P3 remainder, generated stages + derived masks, lift/rt recipe build, census, SSE FP.
- 02:40 w7 EH ThrowInfo fix f3af47b4 (ehtables.resolve_slice): exe 0xec449c was unresolved because two stack roots join (function body plus catch funclet 0xec44b5). Rule: with no single esp, a backward walk over the decoded predecessors finds the slot's defining push or store; the result is the union over paths ({0x0116cdb8, 0x0117fc10}); anything unplaceable is red. Condition added: an intervening call with an escaped stack address, and a store not proven disjoint, are red. The farm's exe run restarts at ≥ f3af47b4.
- 02:45 x86sys.json re-done without ReactOS (L5 w14 28bbec363): ring-3 int vectors are 3, 4, 0x29, 0x2d; 0x2a/0x2b/0x2c/0x2e, idtr_limit and gs-initial are undecided; cr4.umip 0 (asked to pin it to cpuid_model.json, not cpumodel.c). Orchestrator ruling: MS docs/WDK 7.1, then Wine 9.0; ReactOS is out. seh.cc trap rows read x86sys.json; sites that depend on undecided rows need a reach certificate (w6) or the build is red. w1 count pending.
- 02:50 w1 093def1c: SSE min/max/CMPcc/COMIS class (dsl.fp ops 5..16). Not yet accepted: it went out of order, the Unicorn test and the direct compile are not evidence, and the census ran on old facts and was OOM-killed. Order restated: P3 remainder → generated stages/masks → lift/rt recipe build → census per image on facts 6944080c + farm → builders. Fault-path rule given: an unmasked SIMD FP exception sets the MXCSR flag before #XM (SDM Vol 1 11.5.2); x87 is deferred to the next waiting instruction with FSW flag/ES/B and FIP/FOP (SDM Vol 1 8.6).
- 09:15 Resumed after the 5-hour limit (orchestrator, integ d65489ae9). w1 a3217cd2c deleted the Unicorn oracle (test_forms_simd.py and the emulator half of test_forms_ssefp.py). Resumed: w1 (P3 remainder → generated stages → lift/rt recipe → census → fault path), w7 (farm RUN ≥ f3af47b4, then the 8-image facts commit and 164 verdicts), farm 1 (d3dx9_31/38 and exe facts; its container had disconnected), w8 (native-disp wiring, then the 10 bootguest symbols via the recipe → L4), w6 (P5 state, the #UD certificate over 2,731, then the undecided-field sites).
- 09:20 w7 b462277d (claude/fnv-l3a-lift-w7-merge): resolve_slice makes any call on the walk RED, and any store not provably disjoint RED (only absolute image addresses and esp+offset dword stores pass). ehslice_test 5/5. Accepted. Farm 1 runs d3dx9_31/38 and exe at b462277d.
- 09:25 w6 int3 48922c8c2: P5 per-entry census done (8b3ab109a; totals not rerun yet). pointsto: three canonical-form defects fixed (norm memo 1.3 GB cfd208a13; a join returning the raw operand 1921b38ba; (o,c)/(o,None) regrowth 48922c8c2). binkw32 cells/entries stable (974/648), but the global loop doesn't stop (66+ passes). Rule given: monotonicity assertion new ⊒ old per update; any unbounded component gets the widening rule, no pass cap. No representation rework.
- 09:30 w1 sysites (undecided-x86sys-field sites): not accepted. It used old facts 7c90ab0, the old record 28bbec363, and a linear decode of ranges.tsv (the second decoder; 672 'pop es' data bytes in the exe). To redo: facts 6944080c + farm, osrec 2895b30fe, per-entry decode (def_check.instructions 8b3ab109a). Open question to w1 (on the path): does the lifter lift a linear decode of the ranges? If so, the lift universe becomes the per-entry decode minus data_in_code.
- 09:40 L1 asked about thiscall: confirmed at impgen.py:49-61 (this in ecx; args = stack slots; callee pops); bridgetable.py is L5's. L3b defect in 426d95f8: F3 0F BC/BD (tzcnt/lzcnt) has no builder (48 shapes, 0 pack sites). Cause: builders keyed by capstone mnemonic. Rule → w1: feature-dependent encodings mapped to their CPUREF meaning in img.dec (SDM opcode-map cells + cpuid_model.json), then the builder table checked by that rule.
- 09:45 Weekly budget order (integ 9ea2d5403): kept w8 (lift/rt native-disp, the 10 bootguest symbols, ELF) and w7 (8-image facts, then the lift-universe decode question); farm 1 only. Stopped, branches kept: w1 (claude/fnv-l3a-lift-int3; open: lifter decode, x86sys reads, re-citation, generated stages, decode under CPUREF, fault path) and w6 (open: pointsto stop condition, re-join, #UD certificate over 2,731).
- 09:50 x86sys.json final at L5 osrec 3646ebab3. Queued for the next free worker: delete INT_VECTORS/INT_KIND/SEL_*, have seh.cc and the Forms read the record (w1's stopped list).
- 09:55 w8 item (1) done, claude/fnv-l3a-lift-w8-t16l 29598c8e9: lift/rt green, 13 objects (12 + native_disp.S), stages 15 run / 348 hit; facts/native c4544e18b, rows 0, sha256 e3b0c442…b855; fnvnx_native_disp_table/_g/_phf_params each defined once (nm on the recipe objects). Defects closed on t16l: L1 recipe.py lost 7cece903a (pinned python *.libs; reported to L1); plugin entries are names only (liftgen → P1); origin_class re-applied. Next: descs/bridges/pack plus 6 tests (run8) → symbols to L4.
- 10:00 L3b defect in w1 forms_simd: pinsrw/pextrw/pshufw (every builder whose immediate selects a lane or shuffle) index with a Python index on the masked immediate, so it raises TypeError under a symbolic immediate. Class rule: lane choice = ite over the immediate. Queued on w1's stopped list (SIMD certification needs it; queue v3 is not blocked).
- 10:05 Path 2.8 gate_oracle (L3b 2877d259b): 111/114. The 3 uncertified are lift tests without oracle.json entries (test_census, test_dsl_xsimd, test_forms_simd) → w8 after the L4 handoff; a test with no external reference gets reported, then deleted or rewritten.
- 10:15 w1 int3 00068cd10 (in-flight work, then stopped): lift/rt recipe-built green (12 objects, 4 run / 46 hit, GATE_CACHE). Selectors, int gates and TR are read from x86sys.json; generated stages gen_faults/gen_cpuref/gen_sys write rt/gen/* with no committed copies; FNVL_GDTR/IDTR/LDTR/CR0 deleted. faults.json re-cited from Wine 9.0 + SDM/WDK (LOCK/ASSERT/DEOVF/INTO_AT1 removed, XM added). Privileged set from L3b privset (no hand list). Defects recorded for the resume: an undefined fnvl_undecided_selector (forbidden) → fnvnx_reach_violation with a certificate fact; "Unsupported" must mean no Form, not a run-time trap. Edit to L5 platform/build.json (gen_copy X86SYS) → L5 notified.
- 10:15 w6 stopped at int3 701f9cb2b: pointsto stops on binkw32 (9 passes, 14 s, 124 MB), libvorbis (34 s, 333 MB), libvorbisfile (12 s, 158 MB). Cause of the non-stop: the stack-slot table grew one slot per pass around a call cycle, closed by a window (slots beyond 256 bytes join one summary DEEP slot, a widening). Also fixed a soundness hole: argument pointers now reach the callee's loads. Open: the monotonicity assertion, d3dx9_38/exe pointsto, the re-join (cleared_join.py, run alone), the #UD certificate (waits for w7's facts and ctlstate R1-R3).
- 10:20 L5 takes the x86sys export into the platform root (output name X86SYS unchanged; tool platform/declared/gen_export.py, so platform takes no dependency on L3a). w1's platform/build.json hunk is dropped at the resume, or superseded by L5 w10's root commit.
- 10:25 w1 kept working after STOP (54d556fb2, 0bfa4a518): rtgen.py makes every reader of the rt headers go through the generator stages; Form.fault_xm sets the MXCSR flags before #XM (SDM 11.5.2, cited, not model-proved; SSE FP certificates go stale until recertified). The sysites census is not accepted: the total of 5,004,896 is unchanged, so it is still the linear decode. Interrupted, and told again to stop.
- 10:30 w1 stopped, head 0bfa4a518 (no background jobs). Resume list: the lifter-decode question (first), decode under CPUREF, defects (a) and (b), the census on the per-entry decode, the x87 deferred exception, the cvt/rcp/ldmxcsr/emms builders, the lane-immediate ite, the oracle entries (now w8's). x86sys reads are on the final 3646ebab3.
- 10:35 L4: entries.tsv is written by liftgen.py:216 outside any stage, and boot_job reads a hand copy (rules 2 and 3). → w8 with P1: entries.tsv becomes a declared output of the pack root's generator stage; commit and output name go to L4.
- 10:40 L1 w12: do the dispatch generators accept an empty lifted set (test images)? dispgen yes (29598c8e9, 0 rows). Miss rule unchanged (rule 3, #UD via SEH), no new certificate. liftgen/bridgegen over 0 entries → w8 checks with a 0-entry recipe build.
- 10:45 Path 2.5 x87-masked-sites (L3b's x87 certificates wait on it): → w7 after the 8-image facts commit. Every fldcw/fldenv/frstor/fxrstor (and ldmxcsr) value is derived from the code; undecided sites go to the orchestrator. Message discipline (integ 933d7dc5f) passed to the workers.
- 10:50 L1: the pack exports entries.tsv as output ENTRIES (consumer root ports/fnv-nx/boot, link/milestones.py); table_id = sha256 of L1's stage-A bridge.tsv (contract/bridgegen.py, w12L2 8ed5b3747), taken as an input, with no second computation. → w8.
- 10:55 L5 asked for MXCSR_MASK in cpuid_model.json (Wine 9.0 derives PF_SSE_DAZ_MODE_AVAILABLE from it; platform gen_pfh red on it). Rule: declared mask = the host's fxsave MXCSR_MASK ∩ the bits the SSE Forms implement (DAZ only if every SSE FP Form implements DAZ); the same field feeds the lifted FXSAVE. BLOCKED: the host run stage on CPUREF (L1/L6 w6) and DAZ in dsl.fp (L3a w1, stopped).
- 11:00 L1 w12 8968eaa30: the stage-A bridge table is derived in the loader recipe (output BRIDGE_TSV, plus KEYS_TSV, MODULES_TSV, IMPORT_KEYS_H, IAT_DATA_TSV); byte-identical to the pinned table (1554 lines, sha256 e8aaba62…11d4), so table_id is unchanged. The pack takes BRIDGE_TSV through uses → w8 with ENTRIES.
- 11:05 Orchestrator ruling (via L3b): mxcsr_mask is a CPUREF fact owned by L3a, from the same pinned capture as the cpuid answer; L5 x86sys and L3b's SIMD spec read it without restating it. Same item as 10:55, still blocked on the CPUREF host run (L1/L6 w6); the commit goes to L5 and L3b.
- 11:10 mxcsr_mask (blocks L5 gen_pfh row 11): two decisions asked of the orchestrator. (1) Which host is CPUREF for a new capture (no earlier capture of MXCSR_MASK exists; the run stage is L1/L6 w6). (2) Declared bit 6 (DAZ) = host ∩ lifted; recommended: clear it until dsl.fp implements DAZ (w1 is stopped).
- 11:15 Orchestrator rulings on mxcsr_mask: declared = host ∩ lifted = 0xFFBF (DAZ cleared; SDM Vol 1 §11.6.6 plus the CPUREF SSE2 row; no host run); PF_SSE_DAZ_MODE_AVAILABLE = 0. → w8 on int3: cpuid_model.json field, FNVL_MXCSR_MASK generated, FXSAVE/ldmxcsr/fxrstor read it (#GP on bits outside the mask); commit and sha to L5. → w7 2.5: every ldmxcsr/fxrstor value also keeps bit 6 clear.
- 11:20 L3b SIMD spec check (L3b 60abe266a) over all 4,290 pack SIMD shape keys: proved 2,659, by cases 98, FAILED 0, no-form 773 (SSE FP arithmetic), no spec row yet 760 (L3b's). Asked L3b to rerun the 773 on int3 0bfa4a518, which has the SSE FP builder; any remaining no-form goes on w1's list.
- 11:25 w8: mxcsr_mask on int3 47b68e51c (cpuid_model.json sha256 fbb63bff…1800, sent to L5); P1 plus the ENTRIES output at fcd76d874; run8 on t16l compiling descs cold. Next for w8: ENTRIES table_id from BRIDGE_TSV, the L4 handoff, merge int3 into t16l (one L3a line; lift/rt recipe verdict), the 0-entry check, gate_oracle.
- 11:30 L3b: iretd has no builder (6 pack shapes, likely data decoded as code). Decision: the SDM IRET same-privilege Form (CS checked against x86sys, CPL3/IOPL0 EFLAGS rules) → w1's list; the per-entry lift universe removes data shapes separately.
- 11:35 L1 w12 needs a complete facts pin and a universe.tsv schema: the pin comes with w7's 8-image commit; the schema is generated per producer into the tree (exe universe.py PRI/FEATS, modules modlift.py:284). The two producers are the C18 defect (after the first frame).
- 11:45 BLOCKER, farm 1: the container restarts about every hour and kills the universe.py runs (no resume); d3dx9_31 lost, exe needs >1 h. Likely cause: reclamation after the session goes idle → the farm waits inside a turn (Monitor until-loop) and logs uptime. Fix for the class: w7 adds a per-pass checkpoint to universe.py, keyed by (producer, image, inputs), shown byte-identical on a kill and resume.
- 11:55 w7 fb856ca9: universe.py resume via an append-only memo of jtab.resolve (the only expensive step). Key: sha256(jtab.py source, capstone version, image bytes) + site + the backward cone (x, dec[x], seeded) + maxwords; flushed per call. resolve is a pure function of its key, so outputs are byte-identical: binkw32 kill and resume gives diff -r empty. Accepted (the whole-state pickle would be ~10 GB for the exe). Farm 1 runs the 3 images with it.
- 12:00 Owner order (integ 76e450faf): the L4 boot loop runs now; a stop routed to L3a comes before every other item. First stop: ELF 509809b3, 0xC000001D (illegal instruction) after init d3dx9_38.dll; L4 is classifying it. Keep the pack head linkable.
- 12:05 The boot-loop order is withdrawn (reverted in integ 4674ce3e9; rejected by the owner). No stops from measurement runs; path items as before.
- 12:20 w8: the cold descs build (objects_of every admitted root after the L1/L2 key change) overran the 2 h background limit on 4 cores; it resumes from GATE_CACHE in w8's container (no farm holds this line's store). It waits inside its turn (Monitor). Meanwhile: ENTRIES table_id from BRIDGE_TSV and the int3→t16l merge (code). Bridges, pack and the 6 tests (the L4 handoff) wait for descs.
- 12:25 w8 t16l 0282d2316: merge of L1 w12L2 8968eaa30 (pinned compiler b274ec06, loader BRIDGE_TSV, link.entry) + ENTRIES. All 12 roots 'recipe valid'. The native test images declare their own link.entry; every root carries the pinned compiler external. lift/pack uses loader BRIDGE_TSV; stage packentries.py writes entries.tsv with table_id = sha256 of those bytes (test_packentries, test_liftgen_recipe pass). The compiler pin changed, so a cold build of the 12 roots is running in w8's container. Then the L4 handoff, then the int3 merge.
- 12:30 w8 t16l 802ba8215: int3 47b68e51c merged (mxcsr mask); ENTRIES via packentries (BRIDGE_TSV); machine on env.json (setup_verdict_env.sh, envcheck OK). loader green (12 objects). Every other root is blocked (owners outside L3a):
  - B1 (L2): fnvnx_layout (toolchain/layout/fnvnx_layout.cc:446) has no grammar rule for vector_type; lib/sha256/sha256.c:83 (NEON uint32x4_t) is red now that every guest root loads the full plugin set. Red through uses: contract/facts, lift/descs, lift/bridges, lift/pack, lift/rt. Closing rule: a grammar case for GCC vector types (element type × lane count), not a sha256 edit.
  - B2 (L1): RECIPE `include` cannot name another root's generated output. lift/rt generates fnvl_cpuref.h/fnvl_faults.h/fnvl_sys.h, fnvl_abi.h includes fnvl_cpuref.h, so every consumer root (native tests, pack, bridges, lifted tests) fails PP. Closing rule: an include form over a `uses` output directory.
  - w8 forwarded B1 to L2 and B2 to L1 w12; meanwhile code-only items (0-entry check, gate_oracle entries, P7/P2/P6).
- 12:35 B1 resolved by an existing rule: L2 VEC1 (944ca7412), carried at claude/fnv-l2-source-w3-complex 8f7217f4d (fnvnx_layout.cc:458 VECTOR_TYPE, row vec:N:size:align:elem, poly N/size covers NEON and SVE; test_layout.py:139-144). t16l had the pre-VEC1 plugin from L1 pluginfix b707cd5c4. w8 takes toolchain/layout + link/ctypes_compat.py from 8f7217f4d together; L1 w15 carries the same commit into cand3. B2 still open (L1).
- 12:55 w8 t16l 370ff9993 (L1 d89a762c0 merged). Cold build at that commit: loader, contract/facts, lift/descs green; bridges, rt, pack, 3 native tests, lifted tests red. Fixed in L3a: lifted-test generators read undeclared rt/fnvl_abi.h + cpumodel.c; the cpuid feature words now come once from fnvlift/cpumodel_bits.py over verify/cpuid_model.json (used by gen_cpuref and f_cpufeat), and the stages declare it. Open:
  - B2' (L1): unitstage:160 rejects include_uses of rt/GEN (bridges, 3 native tests).
  - B3 (L2/L1): rt `forbid:` on the descriptor `stack:v:` from enter.cc `fnvl_dispatch(t)()` (an indirect call through a returned pointer).
  - P-pack (L3a): packentries refuses the external pack. Its trailer has table_id e8aaba62…, but the loader's BRIDGE_TSV is now 2f20d75e…. Cause: the pack external holds bytes derived from the loader's table, a second copy (rule 2). Closing rule: every table-dependent byte goes into lift/pack's generated stage from BRIDGE_TSV, and the external holds only table-independent lifted bodies. First, w8 lists statically the emitter sites that read the table.
- 13:00 w8 t16l 70f94925b (L1 06561f2c7 merged, so the include_uses store rejection is fixed). gate_oracle in L3a: test_dsl_xsimd entry added, dangling test_formgate/test_fpexact keys removed (deleted in 901c3c54e); test_formgate_real entry next. Whole tree at 70f94925b: 29/155 test files certified; the rest are other layers', and the dangling verify/tests test_coffnorm.py is L1's. P2 census at the same commit: 25 lifter/tool files read 17 FNVNX_* environment variables with /home/user defaults. Rule accepted: one Inputs record from explicit CLI paths, no environment, no default path. It comes after P6.
- 13:05 w8 P6 batch 1, t16l cdd01d5be. Deleted the host-g++ and host-run harnesses: rothost, bthost, x87test, golden, rcptest, fmathtest, citest, sehcodes, proof/backends.py, prove --backends. gate_fpexact and test_gate_lift read GATE_CACHE, so there is no second cache. Decided:
  - instest, instest_extra, x87dyn, mmxtest and fpflags are deleted together.
  - L3b's proof/universe.py reads /home/user/l3work/instest/result.json. Sent to the L3b lead: its universe comes from the def_check.instructions fact.
  - clpod is kept: table.tsv gets a provenance header. It is a measured reference without a pinned producer, because no fnv-nx-build ref holds cl.exe. It closes when cl.exe is mirrored to tools/ and gen.py becomes a recipe stage (L3a, after the first frame).
  - rcpgen waits for the L1/L6 `run` stage.
- 13:10 Correction: the instest/result.json reader is lift/proof/universe.py on L3a's t16l, a stale copy from old branches (no L3b head has it; L3b confirmed). t16l prove.py reads it too. w8 deletes universe.py and takes prove.py from L3b's head, in the commit that deletes instest; afterwards git grep instest gives 0. t16l lacks 63 of L3b's lift/proof files; they arrive through L1's candidate, not by copying.
- 13:15 w8 P2 t16l 09b777c31: fnvlift/inputs.py, an explicit configuration; an unconfigured name raises at its use. No FNVNX_* and no /home/user in fnvlift {universe,fpfacts,f_x87_ctl,x86sys,image} or tools {liftgen,universe,modlift,pemod,impgen,lifted,fpmodes,x86flow,fpstate}. rtgen.py (a second rt/gen generator) is deleted. Correction ordered: no None form for an empty universe or depth; an empty file goes on the same path. Next: run test_lock_only/test_ranges; then the lift tests; then the x86flow retn memo (a second cache) as a stage output or recomputed; then reconcile/formcert/ehflow/pointsto. proof/* is L3b's.
- 13:20 Pack table_id, cause found (w8 step 1). The key rule of impgen.py:28-43 (0x00100000 + 16 × the index of the sorted non-data rows) feeds imp.cpp, imp_entries.tsv and disp.S. liftgen.finish wrote all three into the external, and guestgen_imptest/msvcrttest hold copies of the key rule. Lifted bodies hold no key or index (they reach imports through the IAT and dispatch), so they are table-independent. Rule accepted: imp.cpp, imp entries and disp.S become lift/pack generated stages from loader BRIDGE_TSV. The external holds only table-independent lift output; finish is re-run once with no relift, and the u/*.cpp tree_sha256 must be identical before and after. The test generators import impgen.rows.
- 13:25 w8 t16l cef5bc406:
  - 723ff9415: instest family, fpflags and proof/universe.py deleted; prove.py taken from L3b's head. git grep instest outside journal gives 0. L3b's prove.py imports proof.certbasis, which arrives with L3b's files through L1's candidate.
  - 8faec7074: no None forms.
  - cef5bc406: oracle entry for test_formgate_real. Gap: no pinned SDM reference, so test_dsl_xsimd cites only VERDICT.md (owner L3b/L1). gate_oracle at cef5bc406: 29/155 certified.
  - test_lock_only, test_ranges and test_defdisp fail in toolchain/tests/plugbuild.py, a stale caller of recipe.py PLUGIN_SOURCES (names only). w8 makes the one-file fix and L2 takes the same commit.
  - ENTRY1 (L2 a94f2e11c) conflicts with t16l: it needs a blessed entry constructor at rt.c:32 and the dispatch.c site word. Asked of L2.
- 13:30 w8 P2 steps 2-4, t16l ba497fce7..02d9bba2c. The lift tests take their paths from argv. The x86flow retn memo is removed and recomputed in process; the cost will be timed once. reconcile reads the L1 facts.pkl/eh.pkl directly: no git-show path, no 'skipped' try/except. ehflow/formcert/pointsto use explicit inputs. Counts over lift/ excluding proof/*: FNVNX_ outside -D macros and the L1 FNVNX_INPUT protocol is 0; /home/user is 0. Remaining: 3 FNVNX_ and 7 /home/user, all in L3b's proof/*. Open: X86SYS is not a declared test input, so test_lifted_vs_shapes and test_universe_r3_check are red (owner L1 w12, requested). The farm lift scripts take X86SYS/GEN as arguments.
- 13:40 mxcsr_mask: 47b68e51c was not merged. Orchestrator ruling: the value is CPUREF's (the pinned host's FXSAVE) restricted to the declared feature set. Finding (git log --all -S): no commit has held a CPUREF FXSAVE MXCSR_MASK, FNVL_CPUREF_MXCSR_MASK was never defined, and tools/cpuid_dump.c (named in cpuid_model.json _doc, 1e4a51c2d) does not exist; that line is false and is fixed. Proposed: raw_fxsave_mxcsr_mask captured on a 6/207 host (cpuid_dump.c as a run stage), with mxcsr_mask = raw & ~DAZ derived by gen_cpuref, value 0xFFBF unchanged. Asked: (A) the 6/207 host, or (B) the SDM 0xFFFF transcription until then. Blocks L5's platform root.
- 13:50 mxcsr_mask final (orchestrator (B)): int3 61e59c490 changes provenance only. SDM Vol 1 §11.6.6 with CPUREF 6/207 (DAZ-capable) gives 0xFFFF; restricted to host ∩ lifted (no DAZ) gives 0xFFBF. The false cpuid_dump.c line is removed. The value and the generated header are unchanged. Handed to L1 for cand3 and to L5. Noted to the orchestrator: the 'OWNER 10' its ruling cites is not in ASSUMPTIONS.json (9 OWNER records).
- 13:55 int3 ffd74aee1: mxcsr_mask_doc cites ASM-055 (OWNER 10, the SDM-documented ISA; it is on cand2 d523d1367). Handed to L1 in place of 61e59c490.
- 14:05 L3b SIMD certification of 47b68e51c over all 4,290 pack SIMD shapes: proved 3,155 (122 by cases), #UD class 331, FAILED 108, timeout 566 (L3b's), no-form 130. FAILED cause: per-mnemonic flag code violates the SDM §11.5.2.1 priority, (a) NaN + denormal sets DE (96) and (b) div by 0 with a denormal dividend sets DE+ZE (12). Rule: one priority function in dsl.fp. no-form: 17 mnemonics, Forms from L3b's spec rows. Assigned to w7 (session_01KmtZKV5F6B2PqGD9piX26f), branch claude/fnv-l3a-lift-w7-simd from int3 ffd74aee1. Done means FAILED 0 and no-form 0 on L3b's rerun.
- 14:30 w7 SIMD on claude/fnv-l3a-lift-w7-simd (after cfe7049b). One SDM §11.5.2.1 pre-computation flag function, dsl._pre with its C twin fx_pre, used by every SSE FP op; the per-op flag code is deleted. The 17 no-form mnemonics (plus cvt(t)ss2si, cvt(t)ps2dq/pd2dq, movntpd/dq, prefetcht0/1/2) get DSL Forms: dsl.cvt, and dsl.tab over the pinned rcptab gated by the cert sha. fpc_host C vs z3: 307,420 cases, 0 disagree. Corrections ordered: delete the now-duplicate hand handlers in f_sse.py (one path); the result is only L3b's full rerun. Debt: fpc_host compiles with the host compiler outside the recipe and moves to a recipe test root when the run stage lands. L3b: simdspec has no prefetch row, and simdcheck breaks on mem_width None/8.
- 14:35 w8 pack (a)-(e) on t16l (c740e1f13). External l3a-lift/pack/7b2faae276b2-notable 632538cd3, tree ffaca415…; u/ tree b843576c… is identical before and after (no body changed); it holds no imp.cpp, disp.S, import rows or table_id. lift/pack has 4 generated stages (packstage.py: copy/imp/entries/disp). test_packentries passes with two BRIDGE inputs over one external. The test generators read the key rule from impgen.rows. Open: need.tsv is empty, so impgen's needed-row completeness check does not run; ordered: the needed rows through uses from the import-facts root. The 4096-unit pack build is running. test_defdisp passes; test_lock_only/test_ranges fail on 'i' asm constraints in rt/fnvl.h (to be fixed by a rule that holds at any flags).
- 14:40 w7 SIMD corrections at 463571562: one path. Hand handlers of every Form-covered mnemonic are deleted across fnvlift (335 mnemonics, f_string.py gone); the hand ∩ Form set is {call, jmp}, held by far CALL/JMP m16:32 (FF /3, /5). A latent miscompile is fixed: supported() routed far forms to the near Form. emit='' is removed. Decided: a far-transfer Form from the SDM pseudocode over the x86sys descriptors, and f_sys.far_mem deleted. Pending: L3b's full SIMD rerun at 463571562.
- 14:45 w7 df8cfeda7: one far-transfer Form (FF /3 /5, 9A, EA) over the x86sys cs. An invalid selector faults before any effect; CALL pushes CS then EIP. f_sys/f_flow far and call/jmp hand handlers are deleted, so the hand ∩ Form set is empty (asserted); test_forms_far passes 43 checks. Defect fixed: far FF /3 /5 shared the Form key of near FF /2 /4. f_cpufeat reads the generated cpuref header (switching to the rt GEN input when merged with t16l).
- 14:50 L4 found lift/rt/build.json failing L1's rules on its line (an unpinned compiler, plugin fields). Answered: t16l 022c5d5f2 already carries the pin b274ec06 and names-only plugins, so L4 takes lift/rt from t16l and makes no second port. The bootguest link waits on w8's asm-constraint fix.
- 14:55 w8 f22105a07: an 'i' asm operand takes only a value constant by construction (PC and static addresses as template parameters; GMK/MK* accessors; the __builtin_constant_p selection is deleted); core.static_ea is a displacement alone. The stage builds at -O2. test_sites (fsw 73, lock 8, none 1732), test_lock_only and test_ranges pass with the 0x29 vector temporarily removed from x86sys.json; that edit is not committed. Gap found by check_int_vectors: L5's x86sys.json lists int vector 0x29 (__fastfail) and faults.json had no row. Ordered: a fastfail row per the MS docs (immediate termination with 0xC0000409, no handlers; the Wine 9.0 dispatch contradicts them), through the process-exit path, with L5 naming the exit entry. Then w8 sends L4 the rt commit.
- 15:00 w8 18c2b9cde: faults.json FASTFAIL (int 0x29, code 22, 0xC0000409, param ECX, dispatch none). The source is the MS __fastfail docs first and Wine 9.0 second, with the reason the docs win. seh.cc ends the process through the platform's TerminateProcess(-1, 0xC0000409) entry, with no handlers and no module detach. test_lock_only and test_ranges pass with the unedited x86sys.json. lift/pack uses loader IMPORTED_TSV: need.tsv and --need are gone, so the completeness check runs; the external is db2aaf6d4a08. Hash sent to L4. The 4096-unit pack build is running.
- 15:05 u/ hash method corrected: cas.py ingest of 'git archive <commit> u' gives 6ca1db4c… for both derived externals (632538cd3 and db2aaf6d4a08). The b843576c… quoted in c740e1f13 does not reproduce (it hashed a different input) and is withdrawn. Pending: the same ingest on the original pinned 7b2faae276b2 pack, which must give 6ca1db4c…, delivered with the 4096-unit result.
- 15:10 x86flow retn/thunk/alloca measured in process: 54.7 s per run on the exe (96839-row universe; 4-process pool, with the pack build running alongside): RETN 96805, THUNK 66, ALLOCA 64, so 64 shards × ~55 s. Decided: a facts-stage output (pure function of exe + universe, in GATE_CACHE), a required input of shard/fpmodes/fpstate; the in-process recompute is deleted. Fix 776b0d3a9: M32 is a module constant again (09b777c31 had raised NameError in every worker).
- 15:30 w8 2c3591efc: retn facts stage. Admitted root lift/retn (externals: exe and facts 6944080c). Its generated stage retnfacts.py writes RETN retn.tsv (fnvnx-retn/1). x86flow.prepare is deleted (compute is a pure function plus load_retn); fpmodes and fpstate require it. Row for row against the old in-process code at facts 6944080c: RETN 96805, THUNK 66, ALLOCA 64 and IATADDR equal, diff 0. The second run is a cache hit with identical bytes. The pack build is being checked (its log is empty).
- 15:40 Pack build (4096 units) is alive in w8's container. A superseded duplicate build from 12:22 was killed (8 compilers had been on 4 cores). Lifted units take 10-35 min of compile each. Per-stage CPU time is not recorded in the recipe stage docs: asked L1 for cpu_s (a machinery gap for the build-time budget). The unit-split rule will be static, from the size distribution of all 4096 units (bytes, lines, functions, largest function's instructions).
- 15:50 w8 typed dispatch prep ae7417004 (local, unpushed): typed fnvnx_entry_fn tables (phf.table_c; disp.cc and native_disp.cc replace the asm tables); one 64-bit release store of the site record. Blocked: the ENTRY1 decls need L2's newer plugin (the fnvnx_entry attribute); t16l has the old toolchain (64 files differ). Decided: a verbatim take of a pinned L2 toolchain/ commit plus the ENTRY1 include files and L2's plugin-root admit lines, with no edits; ae7417004 is pushed only when facts, rt and the tests are green. Pack re-key cost: every rt header change re-keys 4096 units (10-35 min each), so the next cold build runs once on farm 1 after ENTRY1, the asm rule and fnvl_host.h are all in. Open in L1: link/gate_disp.py and contract/gate_reach.py read the old asm table layout.
- 15:55 L1: gate_disp and gate_reach are rewritten for cand3 (L1 w12), checking the linked ELF typed tables against the generator's rows, through the relocations. L3a supplies one declared rows file per dispatch table (table, slot, guest address, entry symbol), format agreed with w12; assigned to w8. ae7417004 is held from the trunk until those gates are in.
- 16:00 Static size of all 4096 u/*.cpp (external db2aaf6d4a08): 758,888,091 bytes, 22,330,279 lines, 143,100 functions, 7,789,358 x86 instructions. Per unit, p50/p99/max: functions 35/50/56; largest function 491/3,646/39,600 instructions. The outliers are b3c (one 39,600-instruction function, 5.33 MB) and 9 units at 5-9k. Compile times: 36/4096 sampled (partial, not a rule input). Decided: no unit re-layout now (the cost is single huge functions, which no unit split divides); a rule follows once L1 records cpu_s per stage. The cold build runs on farm 1 in parallel.
- 16:05 L1 w12 c61259c85: per-stage cpu_s in result.json. It arrives with cand3, together with a sandbox_id change and one cold run. The single cold pack build (farm 1) waits for cand3, so one run gives the new keys, ENTRY1, the asm rule, fnvl_host.h and the complete cpu_s distribution.
- 16:10 w8 took L2 66353d9ea toolchain/ verbatim (ff03c5e61, 63 files, diff empty) on top of ae7417004. facts was red: the plugin stage ingests only PLUGIN_INPUTS (L1's recipe.py), which lacked decltype_spell.inc and crossing_terms.inc. Decided: take that 3-entry hunk verbatim from 66353d9ea, with w12 carrying the same bytes in cand3; push only if green. Pack build pid 643 is killed (its fnvl.h is f22105a07 state, stale against ENTRY1 and fnvl_host.h).
- 16:15 w8 d9a62c9de: the PLUGIN_INPUTS hunk taken verbatim from L2 66353d9ea; the plugin stage passes. Pack build pid 643 killed. facts is still red: L2's fnvl_host.h:37 uses _Static_assert/_Alignof, which g++ 13 rejects in C++ (platform/os/os.h:121 includes it from C++ units). Owner L2 (forwarded); w8 takes L2's fix verbatim. Nothing pushed past 2c3591efc.
- 16:30 w8 36523eaa9 took L2 761be1f13 fnvl_host.h verbatim; the platform units compile (facts 220 run, 328 hit). Next red: contract/l2sections.py parse_sympair has no kind 'noemit', which L2's new fnvnx_names emits (L1's reader, sent to w12). Decided: no more piecemeal takes. The L2 plugin + L1 reader join is cand3's job; t16l merges cand3, then rebases the typed dispatch (ae7417004) and the rows file (c516f9628) on it. Until then those stay local; t16l pushed stays at 2c3591efc (green).
- 16:35 w7 3b11d1185 (claude/fnv-l3a-lift-w7-jtab): the d3dx9_31 farm stall was a defect. jtab's worklist never converged because St.__eq__ compared fresh alias-class ids, so every loop with a register copy changed its head state (farm py-spy: site 0x15682f3). Equality now compares the alias partition, the termination argument is stated, and test_jtab_fixpoint fails on the old code and passes now. Decided: all 8 images recomputed at 3b11d1185 (the memo key includes the jtab source); diff counts old vs new per image. The single-fixpoint rewrite is not justified by cost so far.
- 18:00 L4 asked for a lift/pack store at 18c2b9cde (its cold build is 4547 keys in 3.6 h, about 7 h left). None exists in L3a. Told L4 that cand3 re-keys every stage (sandbox_id) and the rt prelude changes again (ENTRY1, fnvl_host.h). L3a's single cold pack build runs on farm 1 after cand3, and its store is published to fnv-nx-build for L4.
- 20:00 Owner order (up to 4 workers per lead, path items only). Archived the finished or waiting workers w1-w6 and w9. Active: w8 (session_01KmtZKR4JRJLXpi1LcSzp9b, typed dispatch + rows file, waiting on cand3), w7 (session_01KmtZKV5F6B2PqGD9piX26f, jtab rerun and SIMD Forms, waiting on L3b and farm 1). New, both based on t16l 2c3591efc:
  - w10 (session_01KmtZKUqwPwEsidQGrcqrbQ, claude/fnv-l3a-lift-w10-udcert): #UD shape-site certificates over the 2,731 sites (declared CPU, cpuid dominance), udcert.tsv as a recipe stage. Status: doing.
  - w11 (session_01KmtZKTuKWSeSe7NQxs2Nz3, claude/fnv-l3a-lift-w11-fpmask): masked-exceptions fact for every fldcw/ldmxcsr/fldenv/frstor/fxrstor site, fpmask.tsv as a recipe stage. Status: doing.
- bottleneck: waiting on L1 cand3 (L2 plugin + l2sections 'noemit' join, cpu_s, gate_disp/gate_reach) for typed dispatch and the single farm-1 cold pack build (4096 units, 10-35 min each); workers 4 (w7, w8, w10, w11).
- 20:20 w10 bd94fd02 (measurement, local run; recipe root lift/udcert admitted, check passes, test_udcert 7/7): #UD sites over all 7 images = 5,308 (L3b's 2,731 lacks d3dx9_31). Certified 0; U-ROOT 5,154 (5,151 3DNow routines selected through function-pointer tables indexed by the stored cpuid answer, plus 3 SSE3 fisttp), U-NOGATE 66, U-NODEC 87, U-DEAD 1. Decided rule for U-ROOT: interprocedural sparse conditional constant propagation seeded with the declared cpuid constants, through registers, stack slots and static cells; indirect targets are value sets; a top target makes all address-taken roots reachable (sound default); a candidate pointer root counts only if its value reaches a reachable indirect transfer. Assigned to w10. U-NODEC, U-NOGATE (fist DF /2 misclassified) and the missing d3dx9_31 rows were sent to L3b.
- 20:20 Pack farm brief (c48c02968): 8 farms, `recipe.py build --shard i/8` (L1 w26 ca3a256), then store-merge and an all-hit build. Waiting on the cand3 build commit. w8 typed dispatch read copy: claude/fnv-l3a-lift-w8-typed 9ea4c78c1. Rows: null slots implicit under `# hash ... slots=N`, accepted, since the gate checks all N.
- bottleneck: waiting on L1 cand3 for typed dispatch and the 8-farm cold pack build; #UD certificates need the whole-program constant/points-to fixpoint (w10); workers 4.
- 20:25 Owner order (integ 5c3e8e289, no rework without progress). Each item was checked by "which step does it unblock". The #UD certificates (w10) and the FP-mask fact (w11) block no step: no trunk gate reads them, the lifted #UD is the exact CPU behaviour, and I-FPMASK is a registered REMOVE record. Both are "after the first frame". w10 and w11 push their WIP and stop. Path items kept:
  - w8: typed dispatch, then the product ELF root (cand3 → ELF);
  - w7: the 8-image facts with the jtab fix, plus the d3dx9_31 facts the pack needs (packs);
  - the 8-farm cold pack build (packs → ELF).
- After the first frame: udcert SCCP (w10 bd94fd02 and later), fpmask (w11), the fnvl_seg_ok certificate, gate_oracle 29/155.
- bottleneck: waiting on L1 cand3 for typed dispatch and the 8-farm cold pack build; workers 2 (w7, w8).
- 20:25 Parked and archived (after the first frame): w10 at claude/fnv-l3a-lift-w10-udcert 74294bd0 (WIP note journal/inbox/l3a-lift-w10-udcert-wip.md; 0 of 5,308 certified); w11 at claude/fnv-l3a-lift-w11-fpmask e22f2c2 (lift/fpmask/WIP.md; 935 of 1,111 load sites proved masked, 176 undecided, none clears a mask bit).
- 20:25 Owner top priority (orchestrator): pack compile time (~36 core-s per unit, ~10 h cold on one farm) is unacceptable. Ahead of the other L3a items:
  - w12 (session_01KmtZKUVVL6cV1kisqKEof7, claude/fnv-l3a-lift-w12-prof): profile through recipe.py with a profiling flags variant (-ftime-report, plugin timings) of the 20 largest units by largest function plus the 10 nearest the median; superlinearity fit. Status: doing.
  - w13 (session_01KmtZKT9VGwj9Youb1vJwUR): incrementality census. Units changed between the relifts 7b2faae276b2 / 632538cd3 / db2aaf6d4a08; the function-to-unit assignment rule and its stability (file:line); the inputs common to every CC key. Status: doing.
  - The unit-size rule (item 2) and the stable-boundary rule (item 3) follow from these two.
- bottleneck: pack compile time (owner priority, w12/w13 measuring); L1 cand3 for typed dispatch; workers 4 (w7, w8, w12, w13).
- 20:30 Pack timing records (w8, store only, no new run). The killed pid 643 build (4 cores, jobs 4, about 46 min) finished 66 of 4096 CC stages (1.77% of the bytes) and 555 PP stages. The records hold no timing and no cpu_s. Six samples took 291-1,220 s per unit; CPU tracks unit bytes at r=0.89 over 25 units. The orchestrator estimates about 1.6 CPU-s per KB, so the cold pack is a few hundred CPU-hours. The cause is volume: 97 B of C++ per x86 instruction.
- 20:30 Orchestrator, owner priority, added:
  1. bytes by emit category;
  2. one density rule for the largest category (needs L3b's written certificate coverage; no -O drop without a PERFDEBT entry);
  3. a B/insn and CPU-hours target;
  4. stable unit boundaries.
  Assigned: (1) to w13 as stage 2, by the emitter's own provenance tag (off by default; output byte-identical with it off), relift in the background. (4) is w13 stage 1. L3b was told the density rule is coming.
- 20:35 w13 item 4, read at 2c3591efc plus pack blobs, no build:
  - Units are already address-keyed and stable: liftgen.py:46 gives bucket = (a>>4)&0xfff, 4096 fixed names, no size limit. A function change touches only its own unit and those of its callers, tail sources and range sharers.
  - 7b2faae276b2 -> 632538cd3 -> db2aaf6d4a08: 4096 of 4096 units blob-identical.
  - Unit bytes: 20 KB min, 148 KB median, 5.33 MB max.
  - Full re-runs come from a recipe key defect:
    - recipe.py:2112-2119 makes the whole liftpack external an input of every PP stage, via include /ext/liftpack/inc;
    - the loader's uses outputs (BRIDGE_TSV, IMPORTED_TSV) are PP inputs of every unit.
  - Sent to the L1 lead (subtree includes, per-stage uses), for cand3 or right after it.
  - The 52 KB prelude, rt/fnvl.h inlined, is in every .ii, so an rt header change re-keys all 4096 CC stages.
- 20:35 L3b's position on liveness-pruned EFLAGS:
  - (A) a per-shape projection lemma. v3 stays valid if the elided Form only drops assignments to dead outputs.
  - (B) a per-function liveness certificate from L3a, as live masks:
    - readers include partial writers;
    - ret, indirect jmp, unclosed calls and dispatcher entries read all flags;
    - so does every synchronous exception point (CONTEXT EFlags), unless a rule proves no handler reads it.
- bottleneck: pack compile volume (97 B/insn; w12 profile, w13 emit categories); the PP key defect is with L1; cand3; workers 4 (w7, w8, w12, w13).
- 20:40 L3b w11 (claude/fnv-l3b-lift-w11 ee6b58d01618), fixes of the #UD list:
  - The hand image lists are deleted.
  - The opmap reads the SDM x87 escape tables. U-NOGATE 66 -> 44, all SDM-undefined encodings: 36 sal /6, 7 test /1, 1 D9 DF.
  - U-NODEC 87 -> 0, since the site list is now our Graph's decoded instructions (5,694,413 over 8 images).
  - Provenance of the 87: 61 outside every range, 15 mid-instruction, 4 in a range with no instruction, 7 where linear decode finds a start that the flow graph does not.
  - These belong to udcert, which waits until after the first frame.
- Path item found: pack 7b2faae276b2 encs.tsv bytes differ from the image as modules.tsv maps it (d3dx9_38 01721e9d: `df 88 01` against `DF 56 00`). Assigned to w7 at once: per-image mismatch counts and the cause (base, file or keying). The rule: the lifter takes the base and layout from contract/modules.py.
- 20:45 w7: the pack's encs.tsv agrees with the loader's relocated images, 0 of 3,426 rows mismatching. L3b's `DF 56 00` came from unrelocated file bytes (HIGHLOW delta 0x01320000), so L3b's comparator gets pemod.load. Open: 25 rows and 1,669 entries sit at libvorbis/libvorbisfile bases 0x450000 above w7's scratch module table, which fits the Galaxy modules still being in the table at lift time. w7 is checking against contract/modules.py at the L1 trunk. If the pack is stale, the rule is: the lifter reads modules.py, the pack stamps the table id, and lift/pack checks the stamp.
- 20:45 L1 b3a4dad01 (cand3): PP keys only the unit, the compiler, include subtrees and include_uses. The 4096-PP re-key class is closed.
- 20:45 One-source defect, assigned to w8. The pack's units and imp.cpp compile against inc/fnvl_prelude.h, snapshotted inside the external at lifter 7b2faae27, while lift/rt compiles against the live rt/fnvl.h and GEN. Nothing checks that the two are equal. Rule: the prelude becomes a generated stage of lift/pack from include_uses lift/rt GEN, the external carries no prelude, and packstage rejects one that does.
- 20:50 Path defect (w7, facts 6944080, trunk a5c8f427e): pack 7b2faae276b2 / db2aaf6d4a08 is stale against the loader.
  - Galaxy was still in its static module set (aligned 0x450000), so libvorbis and libvorbisfile were lifted 0x450000 high: 955/955 and 667/667 entries.
  - atimgpud (619), gdffalloutnv (305) and d3dx9_31 (17,879) are not lifted.
  - exe, binkw32 and d3dx9_38 are byte-correct. d3dx9_38 has 5 candidate tail rows missing, under review.
  - Cause class: the pack carried no module-table stamp.
  - Rule (w7): the lifter reads modules.tsv; the pack stamps its sha256; lift/pack is red unless it equals the loader's table; the image set is the table's modules with code.
  - Then one relift of 8 images at the t16l lifter with the 3b11d118 facts, which also carries w8's generated prelude.
  - The cold build waits for that external as well as cand3.
- 20:50 L3b: comparator moved to pemod.load, the pack-bytes claim withdrawn. ehoracle over 8 images: 235,208 rows agree, wine 0, 2 FalloutNV findings on our side (throw-site TI unresolved, throwinfo set). Addresses come from L3b w11.
- bottleneck: relift of the stale pack (w7, facts on farm 1) plus cand3 before the cold build; workers 4 (w7, w8, w12, w13).
- 20:55 Decision, 2026-11-15 (orchestrator ruling, accepted): the stale-pack relift is a full re-key in any case.
  - If the EFLAGS liveness rule is ready before cand3 merges, it goes into this relift and the 8-farm cold build compiles the smaller code once. Ready means the emitter only drops dead assignments, L3b's (A) projection lemma, and L3a's (B) live-mask certificate, with exception points and boundaries as all-flag readers.
  - The same holds for the labels-only-at-control-targets rule (b), which changes no Form.
  - If either is not ready by then, the relift is not held: it goes with the stamp fix and the generated prelude, and the density rules come in the next relift.
  - Owner of the implementation: the first worker free after the w12/w13 measurements. Order by the measured shares.
- 20:55 L1 confirmed: generated stages still mount every uses output (recipe.py:2235). Only unit PP/CC drops them.
- 21:05 Owner order (integ 4363f94e9): every generator is a recipe stage. The relift is done as stages facts -> universe -> fpfacts -> liftsrc -> pack, all based on t16l 2c3591efc. At that commit the lifter inputs are already explicit (fnvlift/inputs.py, P2); what is left is the stages and proof/model_sail.py's /home/user and env defaults.
  - w7 (session_01KmtZKV5F6B2PqGD9piX26f, claude/fnv-l3a-lift-w7-facts): root lift/facts, with codemap and jtab per image from the gamedata external and the loader's MODULES_TSV, then universe; lift/retn moved onto it. Status: doing.
  - w13 (session_01KmtZKT9VGwj9Youb1vJwUR, claude/fnv-l3a-lift-w13-fpfacts): root lift/fpfacts (fpmodes, fpstate). The emitcat relift is stopped. Status: doing.
  - w8 (session_01KmtZKR4JRJLXpi1LcSzp9b, claude/fnv-l3a-lift-w8-liftsrc): liftgen shards and finish as stages, the stamp check (w7 33a330868), the generated prelude, and lift/pack taking its units by uses. Status: doing.
  - w12 (session_01KmtZKUVVL6cV1kisqKEof7, claude/fnv-l3a-lift-w12-paths): after the profile, the path and env-default sweep over lift/. Status: doing.
  - Target: the pack compiled by about 04:00Z on up to 16 farms (brief 217cb3ce0, N=16) once the chain is green. EFLAGS liveness is in only if ready before cand3; no worker is free for it now.
- bottleneck: the lift chain as stages (w7 -> w13 -> w8); cand3; workers 4 (w7, w8, w12, w13).
- 21:10 w7 07512aba0 (claude/fnv-l3a-lift-w7-modtable, on t16l): the module-table stamp.
  - liftgen finish requires --modules and runs check_images: every table row has an image from its pinned file at its assigned base, and the universe lies inside it.
  - The packentries stage is red on a missing, double, non-last or mismatching stamp against the loader's MODULES_TSV.
  - Tests: 13 checks plus the packentries extension, 0 fail. It goes into w8's liftsrc.
  - Worker containers lack the pinned verdict env (`blocked: env`), so w7 runs farm/setup_verdict_env.sh.
- 21:10 Farm 1 (session_01KmtZKU7i9iyCYMN5hkeoJW) archived. It was disconnected since 17:42Z, its 3b11d118 regen is superseded by the lift/facts stage, and only d3dx9_31 was reported, 2 h 40 min into pcsrc. Risk for the 04:00Z target: pcsrc on d3dx9_31 alone runs for hours, so the facts stage cost sets the schedule. w7 reports per-image stage times.
- 21:10 Tail rows: universe.py tail_pass admits an instruction start of the global decode outside the source entry's extent. The pack, older than a0f3d8e, lacked them; by the rule the universe is right.
- 21:15 One shared base, claude/fnv-l3a-lift-base3 (w8): t16l merged with L1 cand3 c0f504e2a. sandbox.py and cas.py are frozen there until the pack lands, since sandbox.py's hash is in every stage key. Merge rule for the 10 conflicts outside lift/:
  - cand3 wins where t16l held only verbatim upstream takes;
  - L3a hunks that cand3 lacks are kept and listed.
  w7 and w13 cherry-pick onto it. w7 bea44f836: x86flow.Image is now the one relocating loader (fpmodes, fpstate and locksites had read unrelocated bytes), and every base comes from MODULES_TSV.
- Pack farm brief: N up to 16, built at base3 plus the lift stage chain.
- 21:25 L1 cand3 is claude/fnv-l1-contract-cand3 41a8855d4: the w12L2 line (include_uses, link.tables, derived link.roots, PP keying b3a4dad01, shard-16, cpu_s), generator dirs, and sandbox.py/cas.py = c0f504e2a. c0f504e2a alone lacked the w12L2 line; L1 confirmed. base3 (w8) is built from 41a8855d4. w7 merges the jtab facts rules (dic.py, reloc-consistency, 3b11d118) onto it, keeping t16l's inputs.py form (L1 abspath gate at 0 in lift/).
- 21:30 base3 = claude/fnv-l3a-lift-base3 49848fd9e (w8): t16l 2c3591efc + cand3 41a8855d4. sandbox.py and cas.py equal c0f504e2a.
  - Taken from cand3: link/ctypes_compat, gate_disp, the link tests, the dispatch/layout plugins, plugbuild, platform/build.json.
  - Kept L3a hunks: loader IMPORTED_TSV and its shim include; platform INCLUDE output.
  - Dropped: the L3a dispatch-plugin shape_quiet hunk (an L2 file). It becomes a request to L2 if the native build needs it.
  - Adopted from cand3: "profile":"guest" in the lift roots; lift/pack uses bridgefacts BRIDGE.
  - recipe.py check: 24 of 30 roots valid. The 6 red are the lift/tests/guest roots, which include lift/rt outside `uses`; the fix is moving the rt headers into lift/rt/include (w8, next).
  - The lift tool tests run in the background. w7 and w13 build on base3.

## 21:10 base3 d9d208c61: 30 of 30 roots valid (w8)
- claude/fnv-l3a-lift-base3 @ d9d208c61. `recipe.py check` over all 30 admitted roots: 30 valid, 0 red. sandbox.py and cas.py are identical to c0f504e2a.
- All lift roots use "profile":"guest", pinned by verify/guest_compiler.json. The gcc-msvcmodel b274ec06 external is gone.
- Out-of-root includes come only through `uses`:
  - rt and bridges use platform INCLUDE and hostabi INCLUDE;
  - the tests use shim INCLUDE and rt GEN.
- lift/rt republishes its 7 headers into GEN by gen_copy stages. They are content-keyed copies of one tracked source, so this is not a second source. Accepted.
- The native test roots have no link.entry under cand3's single guest entry rule. L4's harness calls fnvl_{citest,imptest,msvcrttest}_entry by symbol. L4 was copied and asks if it needs a per-test entry.
- Not run yet: the lift tool tests (now in the background, w8) and the facts/native builds on this base (w7, w13).
- bottleneck: the lift chain as stages (w7 facts -> w13 fpfacts -> w8 liftsrc) on d9d208c61; workers 4 (w7, w8, w12, w13).

## 21:12 w7 facts2 3f008ed82: jtab rules merged
- claude/fnv-l3a-lift-w7-facts2 @ 3f008ed82 = base3 49848fd9e + bea44f836 + 3433aa159 + the jtab facts rules. It brings in DIC, the reloc-consistency fixed point, REF refusals and protect_sites, and removes ASM-233.
- Deleted:
  - the jtab persistent memo store, which was a second cache outside the key;
  - FNVNX_CLOSURE_TRACE;
  - the FNVNX_FACTS env read.
- The universe now checks its `# base=` header against the given base.
- gate_abspath at 9c7c14b92 finds 0 issues in lift/. The 28 it reports are all in verify/ and render/.
- Lead conditions:
  - rebase onto d9d208c61;
  - the reloc fixed-point cap (12 rounds) is red when the fixed point is not reached, or the loop is bounded by monotonicity alone;
  - one stage per image with pcsrc inside it, and no cut unless the timing shows a reason.
- bottleneck: lift/facts build.json (w7) -> fpfacts (w13) -> liftsrc (w8); workers 4.

## 21:15 lift tool tests on base3 d9d208c61 (w8)
- 19 test files ran with no inputs given. 1 file has failures: test_defdisp, 2 cases (static u128 with its address taken; external u128 should be DISPATCH1).
  - Cause: cand3's dispatch plugin (an L2 file) lacks the shapeless-definition rule of 37d4e27df/b0198f906. That rule makes a shapeless definition quiet only when it is static and its address is never taken.
  - The decision is with L2: (a) put the rule in the plugin, or (b) state cand3's rule, and the test follows it. The test is unchanged until then.
- Pass: bridgegen, cpuref, dispgen, ehflow, jtab, liftgen_recipe, packentries, root_graph.
  - liftgen_recipe: pack build.json equals liftgen.recipe.
  - root_graph: acyclic.
- Not run, because they need declared inputs: test_dsl_emit, lifted_vs_shapes, universe_r3_check, lock_only, ranges, modules_check, modlift_closure.
  - Three of them read FNVNX_INPUT_EXE/FACTS from the environment. They take explicit arguments once facts is a stage. That is not on the path before then.
- liftsrc chain: the recipe is valid on w8's local branch, and a cold build is running to measure the shard time.
- 21:17 L2 decision (a): the shapeless-definition rule (static and never address-taken, by GCC's own fact, is quiet; anything else is DISPATCH1) goes into the dispatch plugin on cand3's line. L2 w2 (session_01KmtZKUiDYYhRvaS8TJ2kGu) ports 37d4e27df/b0198f906 onto plugins-cand3. test_defdisp stays unchanged and turns green when that lands.
- 21:20 Revised: cand3 already closes the shapeless-definition class with L2's DEFDISP-NS (065136142). Under it, a shapeless definition gets a `<asm> -<reason>` row, exitgen is red for a JOINED `-` row, and call sites stay errors. It decides reachability by the join with the exe, not by linkage, so it is stronger than our 37d4e27df rule. Request (a) is withdrawn, and the plugin is unchanged. w8 changes test_defdisp to follow DEFDISP-NS: rc==0 with a `-` row, plus a joined `-` row that is red through exitgen.

## 21:25 Blockers on base3 d9d208c61
- **Platform red on cand3 itself.** Stage platform gen:12 (gen_combridge.py) does `import spec_extract`, which L1 8968eaa30 moved into bridgefacts/com/.
  - It blocks lift/rt (X86SYS), liftsrc and the pack: 28 stages ran with 1064 hits, then the stop.
  - Rule: a generator takes another root's product only as a declared output through `uses`, never by importing its code.
  - Routed to L1 (cand3) and L5 (the owner of gen_combridge). There is no fixture workaround.
- **Loader red.** pp:guest/boot.c cannot find fnvnx_win32_ext.h, because the loader does not take platform INCLUDE through `uses` (reported by L4). w8 fixes it on base3, and the hunk goes to L1 w12 for cand3.
- **lift/descs objects_of red, which makes lift/bridges red.** w8 finds the cause.
- test_defdisp is rewritten to DEFDISP-NS (w8).
- bottleneck: cand3 platform gen:12 (L1/L5); then liftsrc cold (w8); facts on d9d208c61 (w7); fpfacts (w13).
- 21:27 Platform gen:12 has a fix by the rule: L5 0608004f5 (claude/fnv-l5-platform-w10-objstub, on w12L2 5fb4374ee). gen_combridge takes COM_SPEC, COM_ROWS and COM_WORDS from bridgefacts outputs through `uses`, with no import of another root's code; combridge.gen.cpp is byte-equal. L1 w12 takes it into cand3. w8 merges it into base3 now.
- 21:28 L1 adds a static check that makes any generator importing another root's code a check error. I asked L1 that w12 take L5's 0608004f5 into cand3 rather than write a second fix.

## 21:32 base3 393e79d8c; the second cand3 blocker
- base3 @ 393e79d8c adds test_defdisp following DEFDISP-NS: POD matrix 245 cases, 0 mismatches, 0 failed.
- Loader red, also in cand3 41a8855d4: boot.c includes platform's fnvnx_win32_ext.h, and platform already uses the loader, so a `uses` would make a cycle.
  - lift/descs objects_of is red only because the loader is red, and lift/bridges follows from it.
  - Rule: an interface shared by two roots lives in a root below both. Proposed to L5 (owner of the header) and L1 (owner of the loader): the image-record and fnvnx_declared_fonts declarations move into hostabi/include, and the loader uses hostabi INCLUDE.
- bottleneck: cand3 heads for gen:12 (L5 0608004f5, via w12) and the loader interface (L5/L1). Then the liftsrc cold build (w8). Facts (w7) and fpfacts (w13) go on meanwhile.
- 21:34 L3b w11 2919259af (facts 6944080, 5,704,065 sites over 8 images): cleared-feature is now derived from the pinned vendor maps (Intel SDM plus AMD APM, tools/amd-apm aa490c484c89). There are 5,205 cleared-feature sites (5,148 3DNow!, 57 SSE3) and 92 reserved-encoding sites (84 sal /6, 7 test /1, 1 D9 DF).
  - The generated table is fnv-nx-build d7dd09d041a9 cpuid_feature_bits.tsv. w12's queue is unchanged: profile, then the path sweep, then f_cpufeat reads the table, the hand rows are deleted and the diff is reported.
  - For the parked udcert (w10, after the first frame): udcert.Pe must read through pemod.load / the relocated Image, never raw file bytes. Raw bytes gave U-NODEC 30 / U-NOGATE 112 / U-DEAD 1; the mapped image gives 0 / 92 / 0.
- 21:36 w13 lift/fpfacts GREEN: claude/fnv-l3a-lift-w13-fpfacts @ 8366ff4e7 on base3 d9d208c61.
  - Outputs: FPDEPTH and FPSTATE. It uses lift/retn RETN, and for now the l3a-lift/facts fixture external.
  - Three builds are byte-identical. Re-run: 4/4 hits in 5.6 s. Cold: 3 min 40 s.
  - Exe only: depth.tsv 18,539 rows. ASM-204 and ASM-205 are unproven: 51 unknown fcw loads, 3 unknown mxcsr loads, 2,454 sticky observers.
  - Modules were blocked by the PE-preferred-base Image, which is the same defect w7 fixed in bea44f836. w13 cherry-picks bea44f836, runs fpfacts and retn once per image with bases from MODULES_TSV, takes the program-level ASM-204/205 as the AND over all images, and produces per-module RETN.

## 21:40 facts as roots (w7); compile time is GCC scheduling (w12)
- **w7 (claude/fnv-l3a-lift-w7-facts2, on base3 393e79d8c).**
  - Structure: lift/factsimg/<image> is 8 admitted roots with one stage each. lift/facts uses all 8 plus loader MODULES_TSV; factsassemble is red unless the stage set equals the table rows. lift/retn uses lift/facts FACTS.
  - 5 of 7 module images are byte-identical to facts 6944080, 16 files each: binkw32 28 s, atimgpud 9 s, libvorbisfile 11 s, libvorbis 24 s, gdffalloutnv 3 s. pcsrc is under 20 % of each.
  - Running: d3dx9_31, d3dx9_38 and the exe, with py-spy at 10 min.
  - The fixed point has no cap; a repeated state is red (test_fixpoint_cycle).
  - modules.tsv sha256 bafdbc7b…, 7 rows, equals the derivation.
  - The facts producers exit 0 once the facts are written. Their gate JSONs (codemap, pcsrc) become the lift/facts GATES output, and L1 is asked for a verdict gate on it. Example: libvorbis has 6,499 uncertified bytes.
- **w12 (pack db2aaf6d4a08, lift/pack@prof, 27 of 30 units).**
  - The GCC insn scheduler is 70–92 % of CC time. Plugins cost under 0.3 s per unit.
  - Medians: 13–30 s CC. Big units: 65–700 s.
  - Decision: the pack drops sched1 (-fno-schedule-insns) and keeps sched2. The A57 is out-of-order, and instructions per frame do not change under scheduling. This is a flag decision, not PERFDEBT.
  - w12 checks that the semantic flags do not need sched1, then measures @prof2.
  - 6 units fail FTOI in the stale prelude snapshot. They are recompiled against the generated prelude.
- 21:42 L1 accepted the verdict gate `liftfacts` (cand4; written by L1 w28, claude/fnv-l1-contract-w28). It is red on any non-green image, on uncertified bytes, entries_on_data or pcsrc s4 rows above 0, or on a pack-set image missing from GATES. w7 sends the format sample to w28.
- 21:45 The loader/platform cycle is closed by L5 w9 a9cefdbec (hostabi/include/fnvnx/loader_iface.h; the loader has 0 platform includes). The loader (12 objects), platform (113) and rt/guest (6) are green. w8 merges a9cefdbec and 0608004f5 into base3 and resumes the liftsrc cold build.
- bottleneck: the liftsrc cold build on the new base3 (w8); the d3dx9 and exe facts stages (w7); per-image fpfacts (w13); @prof2 (w12).
- 21:55 abspath gate (L1 w28 e8caf06f): 6 findings in lift/fnvlift (f_x87_ctl, fpfacts, universe), measured at L6 a02e400ba. They come from the old jtab copies. On w7-facts2 3159647ea the same gate gives 0 findings in lift/, over a 205-file closure that includes universe.py, funcsfacts, retnfacts and factsassemble. The fix commit went to L1 w12 for cand4.
- 22:00 base3 @ f86299a57 = cand3 41a8855d4 + L3a hunks + 0608004f5 + a9cefdbec. 30 of 30 roots valid. The loader is green (12 objects); gen:12 and the loader cycle are gone.
  - lift/descs and lift/bridges are red from render/nxgpu: pp:nxgpu4_check.c cannot find nxgpu4_check.h. c58c0a4f3 moved the headers into render/nxgpu/include, and build.json still includes the root dir. This is in cand3 too.
  - w8 makes the one-line include fix in its own commit and sends it to L6 and L1 w12, then resumes the liftsrc cold build.
- 22:02 w12 profile done: nx-agent claude/fnv-l3a-lift-w12-prof b242fd21654a; data in fnv-nx-build l3a-lift/prof/b242fd21654a.
  - Selection: the 20 units with the largest maximum function plus the 10 nearest the median 491. It reproduces p50 491, p99 3646 and max 39600.
  - 22 units compiled. CC took 5,664 s usr, and scheduling is 86 % of it (68–92 % per unit). Plugins cost under 1 % and PP 0.15–0.58 s per unit.
  - Fit: CC ~ max_insns^1.24 and ~ bytes^1.95. At 4.5k insns the time varies by a factor of 18 (65–1,195 s), so the region size after CFG drives the cost, not the function size.
  - b3c (39,600 insns) was stopped after 38 min at 5.2 GB RSS, and ec2 (9,081) after 24 min: the largest units are the compile-time and memory problem.
  - Next, as ordered at 21:40: @prof2 with -fno-schedule-insns (a recipe flag class, declared in the inbox), after checking the semantic flags and the order of the ldar/stlr plugin; then the 6 FTOI units against the generated prelude.
- 22:08 w13 fpfacts2 @ 32c1787d0, on w7-facts2 3a5531e1d.
  - x86flow.image_base(path, table, name): the exe uses its own ImageBase, every other image the modules.tsv row; there is no default.
  - lift/retn runs once per image (RETN/<image>.tsv for 8 images).
  - lift/fpfacts runs once per image and combines the results. It is red unless the image set equals the exe plus the module-table rows. The program-level ASM-204/205 is the AND over all images.
  - The build is blocked: lift/factsimg/exe gen:1 (universe.py) exits 1 with empty stderr. Sent to w7, together with the rule that every non-zero exit of a producer prints its reason.
- base3 @ e92e86825 (L5 adba10d8e merged as one head). render/nxgpu include fix: w8.
- 22:12 w7 measurement at facts 3159647ea:
  - d3dx9_31: py-spy at 10 min shows 99 % of the time in pcsrc.analyse, in the state-join loop (pcsrc.py:598-607).
  - d3dx9_38 was OOM-killed at 9.6 GB RSS while running concurrently with another stage; d3dx9_31 needs 4–5 GB.
  - The exe stage's "empty stderr" comes from the universe.py round driver, which merges stdout and stderr and prints them only at the fixed point.
  - Decisions:
    - pcsrc is unchanged now; it is a cold cost paid once per facts change.
    - w7 makes one commit in which every round's stderr streams to the stage and every non-zero exit prints its reason, finds the exe exit-1 cause, and makes no other change to the facts tools before the farms.
    - Then 3 facts farms (exe, d3dx9_31, d3dx9_38), one root each with --jobs 1, on 15 GB containers. The 5 small images stay with w7.
- 22:16 w7 53583fa77: the silent red was the round process OOM-killed (status 247, SIGKILL) while running beside d3dx9_31, with its output held back. Now every failed round writes its output and the reason to stderr, and every exit carries a message. test_exit_messages is an AST rule over the generated-tool closure (14 files, 0 violations).
  - Facts farms on 53583fa77, one root each with --jobs 1, publishing to fnv-nx-build l3a-lift/factsstore/53583fa77-<image>:
    - exe: session_01KmtZKSCxbwkhbjPyVKontV (doing);
    - d3dx9_31: session_01KmtZKSXJfEfpDazdv43Dkf (doing);
    - d3dx9_38: session_01KmtZKTC7Y1r6PNDNnBLoET (doing).
  - w7 builds the 5 small images, then store-merges and builds lift/facts (all hits). w13 rebases onto 53583fa77 and builds retn and fpfacts on the merged FACTS.
- bottleneck: the facts farms (d3dx9_31 about 3 h); the nxgpu include (w8), then the liftsrc cold build.
- 22:19 The facts build commit is now w7 e7b12ab35 (53583fa77 plus the round driver streaming its stderr live). The 3 farms are moved to it, with stores at l3a-lift/factsstore/e7b12ab35-<image>. w7 stops its own d3dx9_31 run, which duplicates the farm and risks OOM, and builds the 5 small images. universe.py, modlift.py, pcsrc.py and cli.py are frozen until the farms finish.
- 22:24 base3 @ 1b1abc42c = e92e86825 + the render/nxgpu include fix (its own commit, sent to L6 and L1). render/nxgpu and lift/descs are green (descs: 48 run, 555 hit).
  - lift/bridges is red from an L1 contract tool: l2sections.py opens /tool/../toolchain/names/sympair_kinds.inc, a path that leaves its mount. Sent to L1 w12: declare the file as an input, and have the abspath gate / require_under reject `..` escapes.
  - The liftsrc cold build is restarted (it does not wait on bridges). w8 freed 11 GB of stale recipe temps.
- 22:26 w7: the 5 small images are green at the e7b12ab35 tree (branch head b9261cd85). Cold, --jobs 1: atimgpud 19 s, binkw32 43 s, gdffalloutnv 13 s, libvorbis 35 s, libvorbisfile 21 s. Next: the 3 farm stores, store-merge, lift/facts.
- 22:33 The exe facts farm is RED at e7b12ab35: gen:factsimg/exe:1, round 0 exits 1 (454 s, no OOM). There are 17 JTAB_RED lines: 14 unbounded index registers and 3 non-relocated table words (00da4f39, 00da60ab, 00dc1c82).
  - Sent to w7. First establish how facts 6944080 handled these sites. If it was green, the merge lost something and is fixed until the exe reproduces 6944080. Otherwise this is a gap in the jump-table rule: the classes are reported and a class rule proposed, with no site list.
  - The farm waits for the fixed commit.
- bottleneck: the exe jump-table red (w7); the d3dx9 farms; lift/bridges l2sections (L1 w12).
- 22:38 liftsrc cold build (base3 1b1abc42c): all 154 shard stages ran and all are red. faults.check_int_vectors expects [3,4,41,45] (faults.json) and gets [3,4,45] from platform X86SYS, whose record still cites ReactOS and drops int 0x29.
  - Decision: take 28bbec363 (x86sys.json on WDK, SDM and Wine 9.0; vectors from Wine 9.0 handle_interrupt) into base3 as its own commit, and send it to L5 for its head.
  - An undecided field (gs, other vectors) that makes a consumer red is reported, not filled in.
  - w8's shard stages now declare lift/proof/{__init__,shape}.py as inputs.
- 22:41 The 28bbec363 cherry-pick conflicts with L5's later rows (thread_fp_initial, tss_io_bitmap, source precedence) in 13 hunks, so it was aborted and base3 stays at 1b1abc42c. L5 is asked to rebase the ReactOS-free record with [3,4,41,45] onto its head. If there is no answer within 30 min, w8 resolves it: L5's rows are kept and x86sys.json is regenerated by gen_x86sys.py. A local diagnostic shard with the vector check bypassed (not committed) finds any other liftsrc red.
- 22:44 L5 accepted 28bbec363 on its sources (no ReactOS; the chain is WDK 7.1, SDM, Wine 9.0; undecided where no pinned source exists). w8 rebases it onto L5 adba10d8e as one commit, keeping L5's later rows; x86sys.json is checked byte-equal to gen_x86sys and test_gen_x86sys passes. The commit goes to L1 w12 as the single L5 head for cand3. Then base3 and the liftsrc rerun.
- 22:48 Correction. L5 adba10d8e already contains 28bbec363, and its x86sys.json has no ReactOS source. My earlier claim came from diffing against an older parent.
  - Precedence (2895b30fe): (1) MSDN/WDK for Win7 SP1 7601, (2) Wine 9.0 only where it contradicts neither (1) nor the declared OS, (3) nothing else. __fastfail exists from Windows 8, so vector 41 is undecided on 7601.
  - The defect is ours: check_int_vectors uses a constant, and faults.json has a FASTFAIL row.
  - Rule now (w8): int n semantics come from the record. A gate vector gets its trap semantics; an excluded vector raises #GP; an undecided vector makes the lift red at the site. The FASTFAIL row is deleted.
  - Static counts over the full image set: int 0x29, other int n, and gs reads. A non-zero count goes to the orchestrator as a blocker.
- 22:52 Local exe shard 0/95 (w8, diagnostic with a temporary X86SYS): 985 lifted, 35 missing.
  - 34 of the 35 are jump tables with no jtab facts: the old facts external 6944080c has no jtab_tables. liftsrc takes UNIVERSE, FACTS and JTAB from w7-facts2 b9261cd85, and FPSTATE, FPDEPTH and RETN from w13 (once rebased onto e7b12ab35), all through `uses`; the 6944080c external is deleted.
  - 1 missing: int 0x6 in the exe, where the vector is undecided. L5 is asked to decide every non-gate vector by its precedence, from Wine 9.0's #GP path in signal_i386.c segv_handler, with lines cited, as one row with no list.
  - The consumer rule (int n semantics from the record) is done by w8 now.
- 22:55 L5 decided other_vectors by precedence (2), from Wine 9.0 signal_i386.c @ c8373ee49fb6 (segv_handler PROTFLT L1925-1940, handle_interrupt default L1804, setup_exception_record L1449). Result: AV 0xC0000005 with info [0, 0xffffffff] at the int instruction, continuable. Vector 41 stays undecided.
  - L5 w15 writes it into gen_x86sys.py (claude/fnv-l5-platform-w15).
  - The lifter's three cases (gate, other, undecided) all come from the record (w8).
  - Counts requested: int 0x29, int 0x2a-0x2e (direct system calls: a non-zero count goes to L5 and the orchestrator), other int n, gs.
- 22:58 base3 @ eb9a3c2e6: 18c2b9cde reverted (the FASTFAIL row and a stray lift/--check/ dir). check_int_vectors reads the record, and {3,4,45} equals the record. Local shard 0/95: 985 lifted, 35 missing (34 for want of jtab facts, 1 int 0x6).
  - Counts over the census byte ranges (5,703,260 insns over 8 images): int 0x29 0; other int n 107; mov r/m16,gs 22; gs-prefixed 3,960, mostly in d3dx9. This is a byte census that includes data; it is not the lifted set.
  - Recount over UNIVERSE with the reaching edge per site. A data-as-code edge goes to w7; real code is covered by L5 w15's AV row.
  - w8 merges w7-facts2 b9261cd85 for jtab, facts and universe.
- 23:01 base3 @ 909e0452f = eb9a3c2e6 + w7-facts2 b9261cd85.
  - liftsrc branch fa8075b5c takes FACTS (universe, ehtables with jtab_tables, per-module dirs), MODULES_TSV and X86SYS through `uses`. The 6944080c external is deleted. The FP inputs stay a fixture until w13 lands.
  - int 0x29–0x2e: 0 sites.
  - Shard rule: a fixed N per image (exe 64, d3dx9 16 each, small images 1), and shard j takes bucket(entry) % N == j, with no stage set derived from facts. finish requires the union of the shards to equal UNIVERSE.
- 23:03 L5 head f1da9b3e9 (claude/fnv-l5-platform-w15): the other_vectors row is #GP, error vector*8+2, AV 0xC0000005, flags 0, params [0, 0xffffffff], address = the int, eip_advance 0. Wine facts are checked against the pinned text; test_gen_x86sys passes 8/8. w8 merges it into base3 and writes the faults consumer against the row fields.
- 23:08 Census over the lifted set (w8; each UNIVERSE entry's own explore(); exe + 7 modules; facts 6944080; 155,896 entries; 0 crashes).
  - int 0x29–0x2e: 0.
  - Other int n: 90 sites (int 6 32, in the exe and libvorbis; the rest in d3dx9). All are reached by fallthrough from a universe entry; one is an entry itself (014d3c95).
  - gs: 24 `mov m16, gs` and 4,001 gs-prefixed accesses (exe 1, d3dx9_31 1,020, d3dx9_38 2,980).
  - The universe is a sound may-execute superset, so every decoded instruction gets the CPU's semantics, and the universe is unchanged. int n is covered by L5's f1da9b3e9 row.
  - gs is undecided in the record. While it stays undecided, all 4,025 sites are red and the pack is incomplete. L5 is asked for one row for the class: either the initial gs plus #GP(0) through null-selector access, or the Wine 9.0 check_invalid_gs path if precedence admits it.
- bottleneck: the exe jump-table red (w7); the gs record (L5); the facts farms; lift/bridges l2sections (L1 w12).
- 23:12 base3 @ 212f252ff = 909e0452f + L5 f1da9b3e9 + w13 fpfacts2 42217f3b2 + the int n consumer.
  - int semantics are read from the record (INT_EXCLUDED, INT_OTHER). A gate gets its trap row; vector 41 is red at the site; every other vector gets FNVL_RAISE_AV with the record's parameters, after each row field is checked.
  - test_int_vectors covers all 256 vectors: 3 gate, 1 excluded, 252 other.
  - liftsrc 69f0497a9: a fixed stage set (exe 64; images of 2 MiB or more 16; others 1, from the loader row only), so 101 shards plus finish. FP and facts enter through `uses`, and the only externals left are the gamedata exe and redist. Local exe shard 3/64: 1,469 entries, 1,410 lifted, 59 missing (jtab and int, old facts).
  - The cold run waits for lift/facts (farms), retn and fpfacts.
- 23:15 gs, L5 ruling: Wine is ruled out, because its initial gs is the host's TLS selector, and check_invalid_gs does not apply at game code addresses. L5 w16 (session_01KmtZKR4HBYhA1ppA6GgxeF) takes the initial gs from the WDK 7.1 debugger documentation and cross-checks it against the ks386 selectors, plus a row `null_seg_access`: #GP(0), giving PRIV_INSTRUCTION or AV [0, 0xffffffff] by Wine's err==0 path. w8 prepares the consumer: gs reads, gs memory operands, no-memory prefixes, and gs loads.
- 23:17 Exe shard 0/64 (fixed rule, real record f1da9b3e9, facts 6944080): 1,517 entries, 1,477 lifted, 40 missing, all for want of jtab facts. The old tree has no jtab_tables, so the next measurement uses w7's FACTS.
- 23:22 gs stays undecided: 0 of 70 WDK 7.1 register displays match the record, and Wine's value is host state. L5 reported it to the orchestrator.
  - L3a sent the orchestrator the impact (24 reads and 4,001 prefixed accesses, which keep the pack's finish red) and the two class rules:
    - (A) a declared-machine decision gs = 0, giving SDM #GP(0) and L5's null_seg_access row; recommended;
    - (B) certified unreachability, which is off the path.
  - blocked: pack completeness on the gs decision (orchestrator).
- 23:25 L3b: 123 SIMD shapes have no Form at 9c97f84bc (w7-simd). symbuild gives TypeError on 122 and KeyError on 1 (fnv-nx-build c18ff53a nonfp_noform_norow.tsv). They block certification and so the pack root's trunk admission. Given to w13 while FACTS waits: a fix by class in the Form builder, then 0 shapes without a Form, and the others byte-identical.
- 23:30 Orchestrator decision (integ CLAUDE.md 81864b90b), gs on 7601:
  - (1) The /GS failure branch is unreachable under OWNER 6. It is found from the load-config SecurityCookie VA, and the sites reached only through it go to fnvnx_reach_violation.
  - (2) Bytes reached from no entry get the call-graph certificate.
  - (3) Fallthrough into data is a universe defect, fixed in this relift's facts (w7).
  - w7 first proposes the class rule: which entry kinds admit data (ptr, tail, slot span), and which facts prove code or data. The residue (sites neither rule closes) is reported with the relift.
  - The facts farms keep running on e7b12ab35 as measurement; the universe change will re-key them.
- 23:34 L5 briefs on gs and null_seg_access handed over (WDK 7.1 ISO: 0 of 70 displays pass; Wine's gs is host state). L5 adds `null_seg_access` as an x86sys.json row.
  - w8's consumer: the null-selector access row for ds, es, fs and gs; reads of the initial gs stay red unless certified; reach certificates produce `fnvnx_reach_violation(fact_id)`.
  - Kind 1 (the /GS failure path, from the load-config SecurityCookie) is L1's; kind 2 is the call graph.
  - The residue goes to the orchestrator with the relift.
- 22:06 (11-15) Orchestrator, integ CLAUDE.md 1b0541249: the gs row is derived by L5 from the pinned Win7 SP1 x86 kernel binary and its public PDB (the KTRAP_FRAME.SegGs store, address cited). w8's consumer stays as prepared.
  - The 4,001 data-decoded gs operands are not certified. They are removed by w7's universe rule against fallthrough into data, in the facts stage.
  - /GS failure-path certificates: L1 w28 format `fnvnx-gsfail/1` is accepted unchanged. L3a publishes it as the declared output `GSCERTS` of lift/facts, generated from the facts and the load-config SecurityCookie (w8). There is no hand-written list.
  - bottleneck: w7's exe JTAB_RED (17 sites) and the universe rule; FACTS gates the relift.
- 22:08 w13 noform: claude/fnv-l3a-lift-w13-noform @ 88f691c09 accepted. One class, the symbolic immediate used as a Python int, closed by an exact mux (selidx/setidx/byteshift).
  - Over the 4290-key SIMD universe (L3b c18ff53a), built Forms go from 3739 to 3861, with 0 previous shas changed. 122 of the 123 now build; vpavgw is an AVX ud-class case (L3b).
  - w8 merges it into base3.
  - Next for w13: the cmp* predicate-immediate class (98 or more shapes) and a ud-class check of the 3DNow shapes.
- 22:08 w7 findings:
  - The exe has no green reference in 6944080, because the exe was never regenerated there.
  - The exe has 326 JTAB_RED rows: 180 unbounded index, 146 non-relocated table word.
  - The exe facts round's real failure is memory growth at the R3 loop: 10.7 GB and climbing to the 15 GB kill. w7 is profiling it.
  - Universe rule, class 1: an instruction has a fall-through iff its x86sys.json row has eip_advance > 0, so int 6 at 00ed641a has none.
  - Class 2: a ptr/tail entry stays unless it has a data-proof. The census runs over all ptr/tail entries of both d3dx9 images, before any code.
  - bottleneck: the exe facts memory growth (w7).
- 22:13 w13 cmp class: 0dd2ae53f accepted. The predicate immediate is selected exactly over 0..7; per the SDM, imm8 bits 7:3 are reserved, so there is no Form above 7.
  - Built 3731 → 3829 (+98) on base3, 0 shas changed. The 330 3DNow shapes and vpavgw are ud-class by f_cpufeat (L3b).
  - Open: the count of concrete cmp* instructions with imm > 7 in the lifted set, and L3b's v3 integer-universe check (18,649 keys). That check gates the merge into base3.
- 22:15 w8 GSCERTS in base3 f5e8bc692 (lift/facts gen stage tools/gscerts.py; edge-cut dominance; roots = non-call/tail entries).
  - Facts 6944080: 6 certs and 262 sites. No cert for the exe (no load config) or binkw32 (no load config); d3dx9_31 has 1 cert with 0 sites (data-decoded universe, re-measured after w7).
  - Of the 24 `mov m16, gs`, 5 are in /GS failure regions. The other 19 (CONTEXT captures and the exe) read the initial gs and are closed by L5's kernel-derived gs row, not by a certificate.
  - L1 gate contract/gsfail.py is not pushed yet (w28 asked).
- 22:16 GSCERTS withdrawn. Integ 1b0541249 deleted the /GS reach rule and L1 cancelled the gate. The /GS failure paths are lifted as ordinary code; w8 removes the stage from base3.
- 22:16 w12 prof2: bb4256c9ef8c accepted.
  - -fno-schedule-insns as a PER_ROOT flag class: pack CC usr 5664 s → 821 s (×0.145) on the 22 units that build in both runs; .text ×1.0148.
  - ec2 now compiles (804 s, dominated by "expand vars"). b3c is still running at 47 min, 5.2 GB.
  - Red, sent to L1: contract/objfacts.py does not know L2's sympair kind 'noemit' (66353d9ea), which breaks the pack's `uses` chain and blocks the 6 FTOI units.
  - Next for w12: the cause of expand vars, then a lifter-side rule proposal.
  - bottleneck: the exe facts memory growth (w7); L1 objfacts 'noemit'.
- 22:18 w8 null_seg_access consumer: base3 e53c09106 accepted (prefixed operands guarded by the row; gs reads stay red until L5's row).
  - Rule given for default-segment operands: a guard iff segment S may be null, decided per image from the declared initial row and every write to S in the universe. ss and cs are never null in ring 3.
  - fnvl_seg_init: every selector comes from the declared row through a generated header; no implicit zero.
- 22:18 w12 paths: bf89c7b36 accepted.
  - N2 (f_cpufeat from L3b's cpuid table) waits on L3b for 4 AMD rows (3DNOWEXT, SSE4A, XOP, FMA4) and a check of the FXSR leaf.
- 22:19 w8 lift/pack on the liftsrc PACK output (claude/fnv-l3a-lift-w8-packuses @ 09182012b) is red in L1's machinery.
  - recipe.py cxx_origin "pack" must accept a `uses` root (3 stages: gen:0, gen:1, gen:3).
  - gate_lift.py fetches the pack only as an external.
  - Both are sent to the L1 lead.
  - GSCERTS removal: base3 7abf794a6.
  - bottleneck: the exe facts memory growth (w7); L1's pack-from-uses change.
- 22:20 L5 gs row landed: claude/fnv-l5-platform-w17 @ ad3af10c5. selectors.gs = 0, from the 7601 kernel and PDB (KeContextToKframes stores 0 to SegGs for cs 0x1b), together with null_seg_access.
  - w8 merges it into base3. The 24 `mov m16, gs` reads become ordinary code; gs-prefixed operands raise the null_seg row.
  - The gs residue is only the data-decoded operands, which w7's universe rule removes.
- 22:21 w13 99b0218fd accepted; it replaces 0dd2ae53f, which broke string cmpsd.
  - v3: 18,649 integer keys, 0 sha changed, the same 6 failures.
  - SIMD universe: 3829 built, 0 sha changed.
  - cmp* instructions with imm > 7: 0 of 5,703,260 lifted instructions, on fixture lift-facts/2; re-run on the merged FACTS.
  - w8 merges it into base3 with L5 ad3af10c5.
  - The exe JTAB_RED classification (326 rows) moves to w13; w7 stays on the facts memory growth.
- 22:25 base3 92b616340: L5 ad3af10c5 (gs = 0, null_seg row), w13 99b0218fd, gs reads ordinary, SEL_* from the row, the SEGWRITES fact (a lift/facts stage).
  - packuses 38c6e711a: `recipe.py check lift/pack` valid on L1 789ef219c (pack from the liftsrc `uses` root). formgate.py is relayed to L3b.
  - Segment write sites at facts 6944080:
    - exe: es 2, ds 1;
    - d3dx9_31: es 77, ds 20, fs 1, gs 1;
    - d3dx9_38: es 146, ds 42;
    - the other 6 images: 0.
  - The guard rule is refined to a selector value rule (a write is non-null iff its source is a proven non-null selector) so the exe's default accesses are not guarded without proof.
- 22:27 w12 cause of "expand vars": 3 address-taken stack objects per memory-access site (the GM<T> proxy `a_`, a temporary, and fnvnx_order's `order_p`). expand_used_vars is superlinear in that count.
  - Whole pack (2c3591efc tree): 4096 units, 143,100 functions, 5,523,891 sites. 657 functions have more than 1000 sites. b3c has 28,576 sites, about 85k stack vars.
  - Decision: measure rule 1, always_inline value access functions with no proxy object and the same memory order, as lift/pack@valfn. Checks: equal counts of ordering instructions and their per-function multiset, CC time, RSS, .text.
  - A per-function size bound is rejected.
- 22:29 Revision: the root cause of the 3 stack objects per site is in fnvnx_order (L2). address_taken_scan makes every asm operand address-taken, whatever its constraint. valfn is dropped.
  - Rule sent to L2: an operand is address-taken iff its constraint allows memory, by GCC's parse_*_constraint `allows_mem`.
  - w12 measures a local patch through recipe.py: CC, RSS, stack vars, and the multiset of ordering instructions.
- 22:30 w8 selector value rule accepted: base3 b5fda5e65 (segwrites/2).
  - pop and mov sources are proven by the same walk. Every cut-off gives possibly-null, so it is sound, with the argument in the docstring and 4 new tests.
  - On 6944080, the exe's 3 possibly-null writes are data decoded as code (00ee6f84, 00ee7104, 00ee77f4), so they are removed by w7's universe rule.
- 22:34 w12's prof/prof2/dump units were built with a stale fnvnx_order (blob 017758d563ca at 2c3591efc), not with L2's pin 84f0883e. The expand-vars cause is re-measured at 84f0883e by L2 w2 (ORDER-STACK).
  - Second candidate: the guest semantic flag -fstack-reuse=none. If it is a correctness switch-off, it needs a PERFDEBT entry and a class rule; sent to L2.
  - base3 takes the current toolchain through L1 cand3 (w8).
  - The L3b cpuid table with the AMD rows is at fnv-nx-build 66d8109dfc33; w12 finishes N2 on it.
- 22:41 w12 cpufeat: claude/fnv-l3a-lift-w12-cpufeat @ b80698ee1e0b accepted. There is no name map.
  - FEATURES is derived from L3b's table, an fnv-nx-build external of lift/liftsrc: b30137748, tree f6ccfbb6…, passed as --cpufeat to 101 shards. The gating is unchanged.
  - w8 merges it into base3.
  - L3b adds a canonical-name column, because one bit has two spellings (SSE41/SSE4_1, AES/AESNI).
- 22:47 base3 428d94d9e: cpufeat in, all roots valid.
  - L3b v3 is 54/54. L3b needs one L3a commit, so w8 builds the next base3 head: base3 + w7 simd 9c97f84b + w12 paths bf89c7b36. Form.sha must be unchanged over 4290 SIMD and 18,649 integer keys.
  - The toolchain is still cand2 (720fe5403), waiting on the L1 cand3 head.
- 22:51 base3 d7c7a822f: w7 simd 9c97f84bc + w12 paths bf89c7b36 + cpufeat c02c82812d49 (/2 table).
  - Regression found and fixed: w7's line sent segment mov/push/pop and CRn/DRn moves to h_default (#UD). h_segshapes now handles them by the f_sys rows.
  - Rule: h_default emits #UD only for a decided #UD (ud-class by f_cpufeat, or an undefined encoding); any other shape is a lift error. The census runs over the merged FACTS.
  - rcptab.c is reverted to its certified bytes (d2a3920e…).
  - The Form.sha check against 99b0218fd is pending (w13's harness), and gates L3b's merge.
- 22:55 base3 743de8cb2: rcptab.c certified bytes back.
  - h_default emits #UD only when it is decided (CPUID ud-class, or SDM UD0/1/2); every other shape is red at the shard. int is the Form alone. 41/41 roots valid at d7c7a822f.
  - L1 cand3 8fc4c58b9 still carries the cand2 toolchain (3c1ad1b10, pin 56a4d09c) and not 789ef219c. I asked L1 for the head with L2's current pin.
  - bottleneck: the exe facts memory growth (w7); the toolchain pin in L1 cand3.
- 22:57 Correction: base3 a964e90b3 has the trunk's fnvnx_order.cc (blob 300bfce80b52, L2 pin). Only w12's old 2c3591efc worktree was stale. ORDER-STACK is measured on base3 once L2 w2's commit lands.
  - The cand3 head (L5, the sympair `uses`, 789ef219c, sched1) comes from L1 w15 to w8 within about an hour.
  - Form.sha run on a964e90b3 against 99b0218fd: in progress (w8).
- 22:59 base3 80832d8b5 (SHAPE_HANDLERS, no "fallback" in the lifter).
  - Form.sha, a964e90b3 against 99b0218fd: INT 18,645/18,649 built (+2, 0 lost); SIMD 3,959/4,290 (+130, 0 lost, 0 sha changed).
  - 10 far call/jmp m16:32 keys changed sha, probably w7's 9c97f84bc far-FF rule; w8 is proving it. L3b re-certifies those 10.
- 23:00 The 10 far FF sha changes are proven to be w7's intended rule, 9c97f84bc (at 9c97f84bc they equal the head; at its parent none of the 10 builds). L3b merges base3 80832d8b5 and re-certifies those 10.
- 23:28 w13 JTAB analysis of the exe (326 red of 1005 sites, w7 facts2 b9261cd85). There is one cause: the MSVC /Od switch on a frame slot whose guard does not reach the jmp.
  - (B) A join with different esp depth kills ebp cells. This is a defect; the fix is sound and is being implemented.
  - (A) Cells are off for the whole cone whenever a frame address forms: the /GS cookie, the SEH record, &local.
  - Replica: 320 → 31 red with A off + B, 18 of them truly unexplained.
  - Sent to the orchestrator for a decision on A: (i) precise escapes only, or (ii) OWNER 6 applied to the table index. My recommendation is (ii).
  - w13 implements B + A(i) now. The real-tool run over 1005 sites waits on w7's memory fix (the run OOMs at 13.9 GB).
  - bottleneck: the exe facts memory growth (w7); the orchestrator's R-A decision.
- 23:29 Orchestrator, integ 11db7476e: R-A (ii). The jtab index is bounded by OWNER 6 to the N+1 entries of a dominating guard on the same cell, with no ebp redefinition between them.
  - Certificate kind "jtab-guard" goes in FACTS; L3b certifies the kind and L1's gate reads it.
  - w13 also implements B and the precise escapes.
  - Sites without a guard and the 18 go to the orchestrator after the real-tool run.
- 23:32 w13 jtab: claude/fnv-l3a-lift-w13-jtab @ 4b0961d2c accepted. R-B, plus R-A(i) precise escapes (8-byte SEH record per winnt.h; /GS xor; the prologue). 29 cases, 5 new positives.
  - Next: the (ii) switch and the jtab-guard certificate emitter to L3b's 5 conditions.
  - The real counts over 1005 sites wait on w7's memory fix.
- 23:35 w13 jtab-guard certificate at 07ebbe3ef: provenance tag on the cell, extended through add/sub and movzx; field shape confirmed. Next: ASM-006, the read-only and dispatcher check in the emitter, and per-image JTABCERT into FACTS.
- 23:35 Farm d3dx9_38 at e7b12ab35, --jobs 1: the universe stage did not finish within the 2 h harness cap (no OOM, no red). It is the same growth class as the exe's R3 loop; w7's fix must bound time and RSS for all images.
  - The farm is standing by.
  - recipe.py defects sent to L1: an absolute --root gives KeyError, and there is no per-stage progress line.
- 23:37 w7 memory cause: ehtables.EH.dis memoises a full capstone CsInsn, native detail included, for all ~3.3M exe instructions (about 9 GB). d3dx9_38 has the same cache (9.6 GB).
  - Rule: memoise a compact record of the fields that are read (about 0.35 GB for the exe). The memo stays complete and deterministic, and FACTS must be byte-identical.
  - Second cost: the pcsrc join loop, the d3dx9_38 2 h wall; w7 profiles it next.
- 23:38 w13 jtabcert wiring at fc89c31b4.
  - C4 read-only check from the PE section flags. ASM-006 is read from the registry.
  - Per-image jtabcert.json goes to FACTS JTABCERT/<image>.json; residue carries its reason. A merged modlift table that needs OWNER 6 is residue.
  - facts_commit is dropped from the format. The real run waits on w7.
- 23:38 w12: b3c prof2 left no result (no timereport). prof2 has 23 units: b32 16 s, 072 159 s, ec2 812 s. The old store is deleted. b3c is measured again with ORDER-STACK.
- 23:45 ORDER-STACK closed by measurement (L2 w2): at the current pin 84f0883e, 072 takes 41.5 s with the plugin and 40.7 s without (9 private slots). The 20k stack vars came from the stale blob 017758d5.
  - b3c is slow in GCC's own passes (FRE, RTL dataflow at 6 GB). It is measured on the idle farm with -ftime-report.
  - w12 back on the 6 FTOI units at base3 a964e90b3.
- 23:45 L3b moves rcpcert.c and rcpgen.c into host recipe roots, deleting the direct gcc calls; w8 merges it.

## 2026-11-15 00:00
- base3 b39f131e4 (w8): accepted. It holds the hostc af133dc17 merge, and lift/rt/rcptab.c is restored to its certified bytes d2a3920e. L3b was asked not to edit it without a re-cert, and to run the rcpgen output byte-equality check, which has not run yet.
- RED: `recipe.py check lift/liftsrc` gives 3,060 gen:foreign-import lines (L1 cand3 8fc4c58b9). The cause is that liftgen.py's import closure includes lift/fnvlift, lift/proof and verify/recipe.
  - Rule: the shard tool imports no verify/* code; the build.json writer is a separate tool. The lifter's packages become declared inputs of every liftsrc stage.
  - Declaration form: asked of the L1 lead.
  - Owner: w8 (doing), blocked on L1's answer about the form.
  - Check: every root is valid, and one shard is byte-identical to 80832d8b5's.
- Open: w7 (memo fix and pcsrc), w13 (jtab), w12 (FTOI and the b3c farm brief), L1 w15 (cand3 head to w8).
- bottleneck: the liftsrc root is red on gen:foreign-import (L3a w8 + L1), then the relift at the w7 memo fix.

## 2026-11-15 00:50
- base3 6d84f3a10 (w8): the recipe writer moves from tools/liftgen.py to the new tools/liftrecipe.py, so liftgen's stage code no longer imports verify/recipe. test_liftgen_recipe passes; test_prelude and test_pack_modtable have 0 failures.
  - The shard's import closure is lift/tools, lift/fnvlift and lift/proof, all of them declared inputs.
- The rest of the liftsrc red (2,652 gen:foreign-import lines) is an L1 bug: in recipe.py foreign_imports (line 691, `or True`), the imported NAME of a relative from-import is always taken as a submodule path. I sent the rule and the check to the L1 lead; w8 rechecks when the fix lands. Status: blocked on L1.
- rcptab.c: L3b also holds the certified bytes (799f3a4e2). The rcpgen byte-equality check runs on L3b farm 1 at b39f131e4.
- bottleneck: the L1 foreign_imports fix, then the liftsrc check, then the relift at w7's memo fix.

## 2026-11-15 01:00
- base3 a857c9852 (w8): the recipe check on all admitted roots gives 44/44 valid.
  - factsimg/exe, retn (8 stages) and fpfacts (10 stages) declare lift/fnvlift and take it only through `--lib`, with no implicit sys.path entries. tools/libarg.py is new.
  - The guest native test roots declare link.entry. This rests on L1's foreign_imports fix, w28 094b62a1e.
- Doing (w8):
  - The three test entries get crt0's ABI, `uint64_t f(uint64_t)`, held by a shared extern "C" declaration so a mismatch is a compile error.
  - Byte-equality of the re-keyed d3dx9_31 factsimg stage against fnv-nx-build l3a-lift/facts 6944080; then retn.
- bottleneck: the relift at w7's memo fix, which brings new keys for FACTS, RETN and FPFACTS.

## 2026-11-16 00:05
- base3 abbf9662a (w8):
  - cand3b 4c38d7265 is merged. The toolchain tree is now a2dc4ecf4, L2's new pin.
  - msvcrttest is split into 4 generated images (one per terminal case, no argv). I sent the run list to L4.
  - The test entries have crt0's ABI, held by lift/rt/fnvl_entry.h.
- recipe check: 44 roots valid, 1 red, ports/fnv-nx/toolchain. That root is red on cand3b itself: L2's tests/sdd/vbase_bounds.cpp meets L1's admit rule. Owner: L1 and L2, sent to both.
- hostapi.h now takes the trunk's side, so FNVNX_HOST_HAS is dropped; nothing uses it. L5 was told.
- Running (w8): the re-keyed d3dx9_31 factsimg against farm facts 6944080, and the compiles of the native test images.
- bottleneck: the toolchain root's admit (L1/L2), then the relift at w7's memo fix.

## 2026-11-16 00:10, correction
- The ports/fnv-nx/toolchain red is not L2's vbase_bounds.cpp, and a2dc4ecf4 is not an L2 pin (L2's pin is 84f0883e). L2 measured it on cand3b 4c38d7265 and on base3 abbf9662a: 61 errors, made of 60 translation units (8 plugin sources and 52 test fixtures) plus the missing cxx_origin.
- Cause: cand3b 8b0d9073c made toolchain/ a generated-only root by moving names/build.json up a level. L2 sent L1 the rule: the export stage moves to contract/facts with a `sources` line, and toolchain/build.json is deleted. Owner: L1.
- bottleneck: L1's cand3b fixes (toolchain root, gen:file-escape for l2sections), then the relift at w7's memo fix.

## 2026-11-16 00:25
- w7 facts2 06504e91e:
  - The EH memo is now a compact record (b0c003fa6), and the 'ti' KeyError is fixed (60e0b5ada).
  - The exe round's peak memory falls from 14 GB to 4.07 GB, 880 s locally; libvorbisfile's output is byte-identical.
  - The exe exit-1 cause was the missing fnvlift --lib.
- Doing (w7):
  - Merge base3 abbf9662a, keeping one --lib mechanism (libarg); then the RUN line goes to the 3 facts farms on the merged head.
  - Then a pcsrc class proposal.
- exe facts at 296875f6c, local and not a farm run:
  - codemap red: 15,098 of 12,440,971 entries uncertified, and 5 bad-decode entries;
  - pcsrc red: 34,368, over 4,146 sources and 68,632 rows;
  - entries_on_data: 4;
  - JTAB_RED: 326 (w13's class report).
- Doing (w13): close the class "Python branch on a symbolic field" (L3b immsel: 12 of 321,376 instances fail, int 3/4/0x2d).
- bottleneck: the 3 facts farms on the merged facts2 head, then pcsrc and codemap to 0, then the relift.

## 2026-11-16 00:55
- w13 symbranch 80bd1328d: done, and w8 merges it into base3.
  - The symbolic value types (dsl.N, symform.SV) raise SymbolicBranch on every Python decision.
  - The decoder universe has 2,548 imm/disp keys: 1,974 build, 570 have no builder (as before), and 4 raise SymbolicBranch. Those 4 are `enter`, which correctly has no shape Form for nesting ≠ 0.
  - int_ is now an exact mux over the x86sys gate rows, with the domain v ∉ int_ring3.excluded.
  - L3b immsel: 688 shapes and 321,376 instances, 0 fails. Concrete Form.sha: 0 changed over 1,604,968 instances.
  - Canon changed for the 32 far 9a/ea instances (fault rows that fold to 0 are dropped). L3b states its cert key.
- w7 pcsrc ruling: an entry's stack is the join over its arrivals (call, in-image jmp state, external callback = call per OWNER 3, the EH funclet frame from the tables), computed to a fixed point. No kind list.
  - Batch with this: entries_on_data through the gate's own data predicate; bad-decode entries lift to exact #UD, and only undecided ones are red.
- Waiting for the merged FACTS of all 8 images: w13's counts of int 0x29 / excluded vectors and of enter with nesting ≠ 0.
- bottleneck: w7's facts batch (pcsrc, codemap) on the exe, then one farm round, then the relift.

## 2026-11-16 01:25
- w7 facts2 69a92a527, exe:
  - entries_on_data: 4 → 0, with 5 drops carrying proof ids.
  - pcsrc red: 34,368 → 10,115 (sources 4,146 → 40). The cause was a universe reader that read the primary kind only; there is now one reader with one PRI vocabulary.
  - s4 escapes: 9,987, of which 8,174 are seh-frame entries under ALLCODE_INPUT.
- Rulings:
  - The arrival join must be computed, not assumed: jmp arrivals use the jump site's esp state.
  - Typed arguments for external arrivals come from the documented prototype of the callback slot of a proven registration (EXCEPTION_ROUTINE, callback parameters of imports), one source, under the join.
  - The module drop goes into modlift too.
- Bad decode: 4 exe entries are undecided. w13 is assigned the #UD decision via the pinned Sail model's decoder on the declared features, not a hand table.
- Farms at 5c12589f5: exe facts green, 2,171 s; the universe stage 2,132 s; peak RSS ≤ 5.95 GB, summed over the container. d3dx9_31 and d3dx9_38 are pending.
- bottleneck: w7's join, typed arguments and module drop, then one farm round, then the relift.

## 2026-11-16 01:55: lift/rt / lift/disp split (w8, merge3)
- w8: split done. lift/disp is a new root (dispgen only). lift/rt no longer uses contract/facts. The universe rule includes lift/rt automatically.
- Both roots are red on a real defect that the forbid gate had hidden: ENTRY1-CONT at lift/rt dispatch.c:69 (`__atomic_store` on `fnvnx_site_t*`, converted to `volatile void*`).
- Ruling: this is a class fix in the L2 plugin (toolchain/dispatch contp_walk). The generic and sized `__atomic_*` / `__sync_*` builtins whose pointer arguments are all of one entry-holding type T count as a same-type copy, like memcpy. Every other flow stays red. The test has positive and negative cases. No source rewrite. Owner: L2.
- Next for w8: the CC errors of enter.cc and unhandled.cc, then the 7-symbol definer table to L1 after the rebuild. bare `_CxxThrowException`: waiting for w8's nm -u of the referencing object.
- bottleneck: the L2 ENTRY1-CONT atomic rule, which blocks lift/rt → the 7-symbol table → the ELF.

## 2026-11-16 01:56: file-escape in liftsrc closed (w13 ccfcffd72)
- The lifter takes cpuid_model.json as the declared input `--cpumodel`. liftgen derives no path from `__file__` (`--lift` is required). Recipe check: lift/sdm and lift/liftsrc are valid, with 0 file-escape findings over the whole tree (was 510). Fixture tests pass.
- The shard byte-identity check runs in the next farm round with w7's batch, which needs the exe and FACTS. w8 merges ccfcffd72 together with sdmgate.
- udsail stage: waiting for the isla pin form (w28, with L3b's pins). w28 sends it to w13 directly.
- bottleneck: unchanged (the L2 ENTRY1-CONT atomic rule).

## 2026-11-16 02:00: lift/rt at base3 8579f3baf (split pushed)
- lift/rt: 9 of 12 units built, 3 red, in two classes. Both are rulings to L2 (fnvnx_order and dispatch plugins); the source stays.
  - dispatch.c: ENTRY1-CONT on the generic `__atomic_store`. The rule: atomic builtins over one entry-holding type T are a same-type copy.
  - enter.cc and unhandled.cc: fnvnx_order refuses `__builtin_setjmp`/`__builtin_longjmp`. The rule: RTL-expanded builtins with pointer memory effects get fences as for an unknown fnspec (release before a write, acquire after a read, none after noreturn). This is the existing order_builtin_call rule, extended to the class.
- Bare `_CxxThrowException`, referenced by platform/crt/cxx_bodies.cpp:16 and :104. Owner: L3a, lift/rt; worker w8.
  - Rule: one core and two ABI entries. The bare C entry raises at the guest call site of the import bridge that runs the native body, then leaves the native frames through the imp.h nframe non-local exit.
  - Test: a msvcrttest image, try/catch(bad_alloc), built over the real roots.
- bottleneck: L2's two plugin rules, which block lift/rt → the 7-symbol table → the ELF.

## 2026-11-16 02:05: bare _CxxThrowException (w8, base3 f891b0c5a)
- One body, `cxx_throw`, with two entries: the guest stdcall import and the bare C ABI. The bare entry raises at the import's guest call site (`fnvl_native_guest`, factored out of fnvl_backend_main) and leaves through the single non-local exit `fnvl_native_exit` (enter.cc).
- A latent ABI defect is closed: fnvl_raise_rec was defined with 9 parameters and called with 7. Now there is one declaration in rt/fnvl.h, and `fnvl_raise_rec_ctx` is the full form.
- The test, cx8 (new(0xFFFFFFF0) in a catch(bad_alloc&)), is being written. msvcrttest native takes the real platform/crt root. It builds once lift/rt is green.
- base3: w28's head (the exec form) is being merged, for lift/sdm.
- bottleneck: L2's two plugin rules (dispatch atomics, order setjmp/longjmp), which block lift/rt → the 7-symbol table → the ELF.

## 2026-11-16 02:10: test images, the second lifting path (w8, base3 36e071499)
- cx8 and the invariant comment are in.
- Finding: guestgen_{imptest,citest,msvcrttest} call the lifter directly, without its inputs (x86sys, sdmrows, cpumodel, image facts). They fail at run time on base3 (`inputs: x86sys was not configured`), so none of the 3 guest test images builds.
- Ruling: a generator that lifts is a second path. A generator emits only the PE, the expected trace and the bridge rows. The image is lifted by the pack's own stages (the fact tools over the PE, then liftgen). One PE emitter is shared with w7's synthetic-PE modlift test.
- bottleneck: L2's two plugin rules (lift/rt); the test images, through w8's pipeline rework.

## 2026-11-16 02:15: udsail stage inputs (w13 b4b7bd58c)
- proof/udsail.py takes --sdmrows and --cpumodel and no longer parses SDM text.
- Its inputs are assigned to the facts stage (w7):
  - BADDECODE: per-image refused sequences, from f_sys.decode_gap.
  - SHAPES: the decoded-shape universe per image, published with the relift for L3b.
- The udsail stage computes SHAPES minus sdmrows itself, with sdmgate's match_rows.
- isla: L3b's root, waiting on w28's recipe.py. The sail tarball and patches are extracted in the stage driver.
- bottleneck: L2's two plugin rules (lift/rt).

## 2026-11-16 02:20: L2 plugin rules landed (cand4b 0a0615edf)
- ENTRY1-ATOMIC: the atomic and sync value movers over one entry type are a same-type copy. ORDER-RTLBUILTIN: fences for the RTL-expanded builtins, and __builtin_apply stays an error.
- Next: the K2 plugin stamp's farm rebuild and pin (L2). Then w8 merges the pin into base3 and rebuilds lift/rt, lift/disp and the test images, and sends the 7-symbol table.
- bottleneck: the K2 pin rebuild (L2 farm).

## 2026-11-16 02:25: arrival join (w7 facts2 8fa62a67a)
- The arrival join is computed and sound. The esp model now takes the callee pop from retn.tsv and the import pop from ehflow.import_pop.
- Exe: esp-lost events 55,257 → 8,339; bad indirect jmp sites 84 → 13 of 133.
- feed() could deepen keys without bound on recursive call chains. It now has a widening at ARG_LIMIT (ALLCODE_INPUT = top); accepted.
- pcsrc red on the exe 10,115 → 75,171, because the 13 non-clean indirect jmps each get the whole ptr set as targets. The 5 images: 1,279 / 3,015 / 277 / 2,170 / 1,199.
- Ruling: not the end state. Next, in order:
  1. a register-relative stack domain, which closes the `mov esp,reg` and frame-restore classes;
  2. an indirect jmp to the function's RA symbol is a return;
  3. a reason for every residue site.
  Then the residue with its code. Then (B), the ehtables handler and push-site column, with callback prototypes from the keys.tsv function-pointer types.
- bottleneck: the K2 pin rebuild (L2 farm), which blocks lift/rt; pcsrc red, which blocks the pack root.

## 2026-11-16 02:30: base3 4967bb436, with w28's verify/ 5c7b36289
- recipe check over all 46 roots: 38 valid. All lift/ roots are valid (factsimg ×8, retn, liftsrc, fpfacts, disp, rt, sdm, tests/guest ×6).
- The 8 reds are outside lift/: hostabi and lib/sha256 (the per-target rule), hostabi/product and platform (admit.json unused-import of contract, owner L1, blocks contract/facts → lift/disp), and render ×5 (L6).
- Test images: the pack shard stages are generated by one function, image_shards (d29ad43cc, exe build.json byte-identical). The facts side is next, with manifest/pefixture.py as the one PE emitter.
- bottleneck: the K2 pin (L2 farm) for lift/rt; the L1 admit.json unused-import for lift/disp.

## 2026-11-16 02:40: export records in lift/rt (w13 a032be733)
- 32 FNVNX_EXPORT records. The recipe-built objects of lift/rt (pinned env, --artifacts fnv-nx-build): 11 of 12 units built, and dispatch.c waits on L2's pin.
- exportgen: 32 exports, each with one definer. The diff against bridgetable's constructed set is empty both ways, so L1 can delete L3_MSVCRT, L3_NAMES, L3_KERNEL32 and the _CI* rule.
- A header-only root `contract` (INCLUDE) was added, because the recipe check required it. Waiting on L1 to confirm the form. w8 merges it into base3.
- A first check on direct-compiler scratch objects was rejected as a second path and redone through the recipe.
- bottleneck: L2's K2 pin (dispatch.c).

## 2026-11-16 02:45: w7 facts2 b845407b8, baddecode and the drop test
- Exe baddecode: fnv-nx-build l3a-lift/baddecode/exe ea7832b, 6 undecodable addresses for 4 entries.
- The drop test (manifest/pefixture build_pe, the real universe.py --module) found that modlift's entries_on_data was vacuously 0: class data-reloc was never emitted. Fix: in the closure-complete residue, relocated slots are class 5 data-reloc (the exe's class-3 predicate). The 5 small images are unchanged.
- Order restated to w7: the esp domain (register-relative stack, return through an RA jmp, reasons for every site) → residue → (B). The decode_gap merge into base3 goes through w8, with the BADDECODE/SHAPES outputs in the same batch. A synthetic exe-mode R3 drop case is added.
- Bottlenecks: L2's K2 pin (lift/rt, contract/facts, lift/disp); pcsrc red at 75,171 (the esp domain, w7).

## 2026-11-16 02:50: pcsrc indirect-jmp residue (w7 ddbabe680)
- 133 exe indirect jmps: 120 clean, 13 not clean. Every site has a reason in pcsrc_ijmp.tsv. Red is 75,171 (the RA-jmp return matches 0 sites; the register-relative stack domain already existed).
- Residue: 7 esp-lost after an indirect call; 5 esp-fresh (3 after `call reg; add esp`, 2 EH continuations `mov esp,[ebx-k]`); 1 thunk at ec3cdd.
- Ruling, RULE T: indirect jmp and call target sets come from the value domain. That domain has origin-carrying values (the same change as (B)'s option (i)), interprocedural stack-slot inputs, and loads from identified tables. The whole ptr set is used only for TOP. The pop of an indirect call is the join of retn over the callee set. A vptr-slot residue, if it remains, gets its own ruling.
- w7 order: decode_gap/BADDECODE/SHAPES → origin values (EH tables byte-identical, L3b told) → RULE T → recompute.
- Bottlenecks: L2's K2 pin (lift/rt, contract/facts, lift/disp); pcsrc red at 75,171 (RULE T, w7).

## 2026-11-16 02:55: base3 9405ea3bf, recipe check 53/53
- base3 holds w28 2872f3ac8, w13 9a5206d73 (the contract/include header root) and L6 w6-root 7a75284ec (render/ and bridgefacts/ with their admit rows).
- recipe check over all roots: 53 of 53 valid. render/gl4 builds green (17 objects, libnxgl4.so linked).
- Red: only lift/rt, contract/facts and lift/disp, all on the old compiler pin (ENTRY1-CONT). The fix is L2's ENTRY1-ATOMIC 8b50252e7; waiting for the K2 pin rebuild.
- Open: w13 on the abspath ×6 findings in lift/fnvlift (L1 275771a6f); w7 on decode_gap/BADDECODE/SHAPES → origin values → RULE T; w8 on the facts stages by image input.
- bottleneck: L2's K2 pin.

## 2026-11-16 03:05: test second paths deleted; the fpc root (w13)
- w13 6b50d628e (abspath 0, accepted by L1) and 5297ac9d0 are going into base3 through w8:
  - fpc_host.py is deleted (a direct g++ build plus the removed rtgen);
  - test_dsl_emit.py is deleted (emission only, superseded by the pack root build).
- L3b: shape certification stops at the call. rcptab.c is covered by rcpcert (2^32 on the reference CPU). The fnvl.h FP and conversion helpers are NOT covered; gate_fpexact check 0 falsely says they are.
- w13: a recipe host-test root over lift/rt's fnvl.h output, with the helper universe derived from the Forms and 32-bit inputs exhaustive. The 64-bit helpers' proof method gets a ruling before it is built. Then L3b points check 0 at that root.
- bottleneck: L2's K2 pin.

## 2026-11-16 03:10: the fnvl.h FP helpers, no known rule for 64-bit
- Ruling for w13: the helper test root is built through the recipe for the product target (aarch64 ILP32 with msvcmodel, run under the boot machine). The helper universe comes from the Forms. 32-bit inputs are exhaustive. The 64-bit helpers (sse_f2i(double), fv_i, fx_dsl fp/cvt w=64) are an explicit open list. gate_fpexact check 0 reads that list instead of "forms".
- Reported to the orchestrator as a step with no known rule. A non-sampled 64-bit proof needs a tool beyond OWNER 9. Recommendation: isla with the Arm Sail model over the product object (an extension of OWNER 9). The alternatives are CBMC, or OWNER 5 (tested).
- bottleneck: L2's K2 pin.

## 2026-11-16 03:12: orchestrator decision (integ CLAUDE.md 37da2f792)
- The rule for the 32-bit-input FP helpers is w13's exhaustive test root through the recipe. The f64-input helpers are a REMOVE record owned by L3b until after the first frame. The relift is unchanged.
- bottleneck: L2's K2 pin (lift/rt, contract/facts, lift/disp).

## 2026-11-16 03:20: fpchost root (w13 9f7950c7d)
- The pure FP helpers moved to rt/fnvl_fpc.h (a lift/rt GEN output). The host test root lift/proof/fpchost is recipe-built and green (1 object).
- The helper universe is derived from the Forms:
  - 32-bit: fx_dsl_cvt i2f and f2i sw=32, sse_f2d;
  - wider: f2i sw=64, fx_dsl_fp w=32/64, sse_f2i, sse_d2f, fv_i, fx_tiny_after;
  - fnvl_tab_*: rcpcert.
- An exhaustive 32-bit run against the z3 Form terms is infeasible (months of z3), and an executable re-implementation would be a third implementation. Proposed to the orchestrator: one method for every width, isla with the pinned x86 Sail model over the host object, with the four same-meaning conditions, in L3b's REMOVE record after the first frame. The universe file is the record's open list, and gate_fpexact check 0 reads it.
- w13 changed recipe.py (a host-profile root with no plugins is valid, per RECIPE.md). The diff went to L1.
- bottleneck: L2's K2 pin.

## 2026-11-16 03:25: FP helpers decided (integ CLAUDE.md a172a44b7)
- One method for every width: isla with the pinned x86 Sail model over the x86-64 host object, plus the four transfer conditions as a static check. All Form-called helpers are in one REMOVE record (owner L3b, after the first frame). fnvl_tab_* stay under rcpcert. The 32-bit exhaustive root is dropped.
- lift/proof/fpchost publishes the derived helper universe as the open list, which gate_fpexact check 0 reads (w13, last step).
- The path is unchanged: relift as stages → pack external (commit + tree_sha256).
- bottleneck: L2's K2 pin (lift/rt, contract/facts, lift/disp); pcsrc red (w7 RULE T).

## 2026-11-16 03:30: fpchost closed (w13 c5ff8fed7)
- lift/proof/fpchost keeps libfpchost.so and its exports; it is recipe-green. fpcuniverse.py and the HELPERS output are deleted: L3b's lift/proof/helpercalls.py is the one helper universe.
- No 2^32 compare step, because orchestrator a172a44b7 sets isla for every width. L3b's request for -g in lift/rt is declined: check 0 needs no width under the ruling ("every helpercalls helper is under ASM-329 or rcpcert"), and -g would rekey every product stage downstream.
- bottleneck: L2's K2 pin; pcsrc red (w7 RULE T).

## 2026-11-16 03:35: base3 d3c287860, 55/55 valid; rcpgen green
- recipe check over all roots: 55 of 55 valid, with lift/proof/fpchost new. render/gl4 is green (17 objects).
- rcpgen (L3b farm 4, 8abd1ab02, fresh store): its output is byte-identical to the GENERATED region of lift/rt/rcptab.c. rcptab.c sha256 is d2a3920e…a58f5, equal to the committed file.
- Red: only lift/rt, contract/facts and lift/disp, on the old K2 pin. L2 was asked to start the farm rebuild.
- bottleneck: L2's K2 pin; pcsrc red (w7 RULE T).

## 2026-11-16 03:55: one decoder, BADDECODE and SHAPES (w7 30757c325); the K2 measurement pin
- tools/gapmd.py (capstone plus f_sys.decode_gap) is the one decoder for universe.py and modlift.py.
- Every image stage outputs baddecode_<img>.tsv and shapes_<img>.tsv. The universes are byte-identical. The EH tables gain the `push` key: 6,239 of 6,239 registrations have a push site.
- Shape keys: exe 13,532; atimgpud 1,239; binkw32 2,990; gdffalloutnv 898; libvorbis 3,710; libvorbisfile 1,951. The d3dx9 images are pending (farm).
- Exe baddecode: 6 addresses; 0 in the 5 small images.
- RULE T measurement over the exe:
  - jmp operands: 1,297 ld, 87 const global, 25 TOP, 2 arg;
  - call operands: 36,505 ld, 7,023 const global, 2,301 TOP, 6 arg.
  - The next rule is scoped to the 13 residue sites. w7 measures their operand classes and the store sets of their globals first.
- K2: a measurement compiler d1a92e26 (old path) carries ENTRY1-ATOMIC and ORDER-RTLBUILTIN. w8 measures lift/rt, contract/facts and lift/disp on it. The real pin is L2 w3's recipe-path build.
- bottleneck: L2's recipe-path K2 pin; pcsrc red (RULE T scope).

## 2026-11-16 04:00: measurement at the farm compiler d1a92e26 (w8, local tree)
- lift/rt: 18 of 21 objects. Red:
  - (a) dispatch.c (C): ENTRY1-CONT remains, because ENTRY1-ATOMIC covers only the C++ front end. Owner: L2, for both front ends, with a C fixture.
  - (b) msvcrt_ci.cc and msvcrt_eh.cc: `.fnvnx.decltype` has no declaration line for the FNVNX_EXPORT symbol operand. The owner is the L1 macro / L2 writer boundary, and they decide which side closes it as a class.
- contract/facts and lift/disp are red behind lift/rt.
- render/gl4: the ld stage needs L2's recipe-path toolchain root (w3).
- bottleneck: L2 ENTRY1-ATOMIC for C, the L1/L2 decltype export line, and the L2 recipe-path pin.

## 2026-11-16 04:05: the 13 pcsrc residue sites classified (w7, facts at 30757c325)
- 10 are tail calls through a vptr after an epilogue (`jmp [[this]+k]`). Before them, esp is lost at an indirect call: 4 `call reg` (TOP), 6 vptr calls, 2 earlier losses.
- 2 are `jmp [IAT slot]` (LeaveCriticalSection, InterlockedIncrement). 3 are EH continuations.
- mem[const global] over the exe: 7,110 sites, of which 7,022 are IAT slots. No store-set rule is needed.
- Rulings:
  - `jmp [IAT]` is a tail call to the import, with the pop from keys.tsv. The proof is the loader mapping the IAT read-only plus OWNER 6.
  - vtable slot: the callee set is slot k of every vtable in the exe's RTTI (CompleteObjectLocator-anchored, structural, no names), and the pop is the join. Measure the join per offset before building it.
  - The TOP register calls: measure their value sources.
- w8: one recipe emitter, lift/tools/imagechain.py (the pack chain per image). The guestgen_* scripts are reduced to PE emitters.
- bottleneck: L2 ENTRY1-ATOMIC for C, the L1 w28 export-record form, the L2 recipe-path pin; pcsrc (the vtable join measurement).

## 2026-11-16 04:00 — pcsrc: IAT register rule accepted, vptr sites go to class narrowing
- Measured by w7 on exe facts 30757c325 / efdd8262f (retn.tsv of the exe, 96,873 entries):
  - The IAT is read-only by the loader's layout (loader/src/start.c:137-146). All 280 exe IAT slots are in .rdata (0x40000040, no WRITE). (ii) stands.
  - The all-vtables join gives no single pop: 3,274 RTTI vtables, 56,416 slots. Slots 1, 2, 14, 35, 38, 42, 43 and 47 have between 4 and 11 distinct pop values each. That join is rejected as the rule.
  - The 4 "TOP" register calls (a3072f, a34b80, a357fe, a48019) are registers loaded from IAT slot 0xfdf1c4.
- Ruled:
  - (C) The IAT register rule: a register loaded from a read-only IAT slot holds the import, with the same proof as (ii). `call reg` takes the import's pop. This closes the 4 register calls and the 2 import-slot jmps.
  - (B) The 7 vptr sites go to RTTI-structural class narrowing. The receiver's origin must reach a dominating vptr store, which gives the static class. The callee set is that class and its derived classes, per the CHD BaseClassArray at the COL offset, and the pop is the join of retn over that set. Otherwise the site stays red and is reported with a reason. No names, no frame-balance assumption.
- w7: (C), then the recompute of the exe and the 5 images, then (B).
- bottleneck: L2 ENTRY1-ATOMIC for C, the L1 w28 export-record form, the L2 recipe-path pin; pcsrc (the 7 vptr sites).

## 2026-11-16 04:05 — entry return: typed ownership of ra, one transfer primitive
- w28 08109b9ce (FNVNX_EXPORT = the relocated `&symbol` plus a kind) is merged into base3 by w8 (2d1fc6f80).
- Ruled with L1 ruling A:
  - The ra field is writable only by the bridge TU, so an ra write from any entry definer is a compile error.
  - Class A, plain returns: the return lines are deleted (CI1/CI2, fp, EH, seh RtlUnwind), and the bridge does the return from the reference conv and args.
  - Class B, non-local transfers: 5 sites (msvcrt_eh.cc:149, :338, longjmp :529, seh.cc:144, :213). Each calls the single `fnvl_native_transfer(eip, esp)` in the bridge TU, which leaves through fnvl_native_exit; the bridge's return code is skipped.
  - Every FNVNX_EXPORT follows its symbol's definition.
- w8 (doing): the rework on base3 through the recipe; counts per kind with the commit to L1.
- bottleneck: L2 ENTRY1-ATOMIC for C, the L2 recipe-path pin; w8's entry rework; pcsrc (the 7 vptr sites, w7).

## 2026-11-16 04:08 — lift/rt on base3: 18 of 21 units
- w8 measured local 02e5b2732 (08109b9ce merged; 32 records kind entry, 1 data record _adjust_fdiv): 18 of 21 objects built.
- Red, all of it owned by L2:
  - dispatch.c: ENTRY1-ATOMIC for C.
  - msvcrt_ci.cc and msvcrt_eh.cc: `.fnvnx.decltype: a referenced symbol has no declaration line`, at the end of the TU, with no symbol named. It is new with cand4b b51ad22ee. L2 was asked to print the symbol and to close the gap.
- bottleneck: L2 (dispatch.c, the decltype diagnostic, the recipe-path pin); w8 class A/B rework; pcsrc 7 vptr sites (w7).

## 2026-11-16 04:12 — IAT register rule built; ra writer set accepted by L1
- w7 (done, claude/fnv-l3a-lift-w7-facts2, the commit after efdd8262f): `mov r,[IAT]` gives ('imp', slot). A call through it takes the import's pop, and an import call preserves ebx/esi/edi/ebp (Win32 ABI, OWNER 3).
  - Exe: esp-lost goes from 8,339 to 8,213. Red goes from 75,171 to 75,192, because the preserved registers expose reads that were hidden.
  - The not-clean jmps stay at 11, which are 8 vptr and 3 EH sites. The 4 InterlockedDecrement functions also contain an earlier slot-1 vptr call on a path that merges before the tail jmp.
  - Next: RTTI class narrowing (ruled 04:00).
- L1 accepted the ra writer set: the generated bridges plus `fnvl_native_transfer`, both in the bridge TU. w8 proceeds with class A and class B.
- bottleneck: L2 (dispatch.c, the decltype diagnostic, the recipe-path pin); w8 rework; pcsrc 8 vptr + 3 EH (w7).

## 2026-11-16 04:15 — udsail stage root accepted
- w13 (done, claude/fnv-l3a-lift-w13-udsailstage ff086e540): the root lift/udsail is a recipe tool with a python run step.
  - Inputs: the 8 facts roots' baddecode/shapes, isla + z3lib, sailmodel, the x86ir external (fnv-nx-build tools/sail-ir 8b711fba9b93), X86SYS, sdmrows and cpuid_model.json.
  - Output: UDSAIL. The recipe check is valid. It has not been executed yet.
- w8 (doing): merge w28 322698c53, then ff086e540, into base3; where the isla/sailmodel files differ, take L3b's.
- w13 next: the farm run via L3b, then udsail.tsv as an fnv-nx-build external, then w7 replaces udsail_input.tsv.
- bottleneck: L2 w2 (3 lift/rt units); w8 rework and merges; pcsrc 8 vptr + 3 EH (w7); the udsail farm run.

## 2026-11-16 04:20 — pcsrc after the IAT rules; vptr narrowing is not decidable locally
- w7 (done, claude/fnv-l3a-lift-w7-facts2 after e34e81730; exe facts 30757c325): red per image after (ii) + (C), with the earlier value in brackets:

  | Image | Red | Before |
  |---|---|---|
  | exe | 75,192 | 75,171 |
  | atimgpud | 1,242 | 1,279 |
  | binkw32 | 3,008 | 3,015 |
  | gdffalloutnv | 240 | 277 |
  | libvorbis | 2,175 | 2,170 |
  | libvorbisfile | 1,212 | 1,199 |

  Not-clean indirect jmps: exe 11, small images 1/1/0/1/1.
- The 8 vptr tail-jmp functions contain no dominating vptr store, because the receiver is an incoming `this` or a loaded member. Local class narrowing closes none of them.
- Ruled:
  - The slot-k arrival set (slot k of every RTTI vtable) holds only if every vptr store in the image stores an RTTI vtable; w7 checks this per image.
  - The pop by an esp bound is rejected (frame balance).
  - Next to measure: interprocedural origin, the caller join of the receiver class and the constructors of the field objects.
  - A site still without a single pop goes to the orchestrator as a gap.
- bottleneck: L2 w2 (3 lift/rt units); w8 rework and merges; pcsrc vptr pop (w7); the udsail farm run.

## 2026-11-16 04:24 — udsail first farm run: measurement only
- The farm run used IR 48ef39ad (not the pinned x86ir 8b711fba9b93), model 0c6c023dccc7, and w13 183e1ebef (not base3).
- Result: 6 of 6 baddecode sequences "isla returned no path", #UD 0, not decided 6.
- Rejected as a certificate input, for two reasons:
  - It was not the recipe target with the pinned externals.
  - "no path" is a stage failure, not a refusal.
- w13 (doing): find the cause, make "no path" a red stage error rather than a GAP row, reconcile the baddecode count (6 vs 4) globally, then the farm run on base3 via L3b.
- bottleneck: L2 w2 (3 lift/rt units); w8 rework and merges; pcsrc vptr pop (w7); udsail stage (w13).

## 2026-11-16 04:28 — vptr arrival set: the RTTI-only set is unsound
- w7, exe facts 30757c325: 8,687 vptr-like `mov [mem], imm32` stores. 8,105 of them store RTTI vtables. 582 stores (268 runs) store code-pointer runs without a COL (/GR- classes). Slot k over the RTTI vtables alone is therefore rejected.
- Ruled: the sound arrival set is t+4k over every relocation target t, code or data, that lands in a run of relocated code pointers. It holds only if the no-PIC fact "every in-image address is relocated" is a gate; until then the set is the whole ptr set. The pop is still open; next is (2), the interprocedural origin on the 8 sites.
- bottleneck: L2 w2 (3 lift/rt units); w8 rework and merges; pcsrc vptr pop (w7); udsail stage (w13).

## 2026-11-16 04:33 — virtual-call pop: gap reported to the orchestrator
- w7 (2), facts 30757c325: the origin is lost at all 8 vptr tail-jmp functions. The receiver is an incoming this, a pass-through argument or a container element. None of the 8 functions is address-taken.
- The last refinement inside the domain (w7, doing): esp after a virtual call is a finite offset set over the callee retn join. It collapses at `mov esp,ebp`, `leave` and `lea esp,[ebp-n]`.
- Gap reported: the pop of a virtual call has no class rule. The options are (a) the frame-balance assumption (owner), (b) whole-program typing (no rule) and (c) permanent red facts.
- bottleneck: L2 w2 (3 lift/rt units); w8 rework and merges; the orchestrator's decision on the virtual-call pop; udsail cause (w13).

## 2026-11-16 04:40 — esp at unproven sites: general form (orchestrator ruling (d))
- Orchestrator ruling (d), CLAUDE.md integ c29de9188: a static esp fact is used only where it is proven. Everywhere else the general lowering applies, and it is not red.
- Confirmed in lift/fnvlift/f_flow.py:
  - esp is the run-time register;
  - an indirect call goes through fnvl_dispatch with the full guest state;
  - an indirect jmp goes through the non-local loop;
  - EH continuations take esp from the exe's saved frame, via fnvl_native_transfer.
  Nothing is missing.
- w7: the offset-set domain is not built (it closes 0 of the 8 sites, 853 of 36,505 straight-line collapses). pcsrc becomes `esp: proven | general(reason)`, with red only where the lifter cannot lower the site (expected 0). Recompute the exe and the 5 images.
- Scope of ruling A sent to L1: the generated lifted units also write c->ra (the transfer protocol), so the ra accessor is for generated code plus fnvl_native_transfer.
- Farm 2 (session_01KmtZKUJTJJy6aiVcibniH8) takes the L3a queue: udsail (w13), the d3dx9 facts, shard identity and exe facts (w7).
- bottleneck: w8 (cpumodel.c push for L1, then class A/B, merges); L2 w2 (3 lift/rt units); w7 pcsrc reclassification.

## 2026-11-16 04:46 — entry return rework complete locally; ra writer gate form
- w8: base3 330e873c7 pushed (the cpumodel.c record fix). Local stack: class A/B complete and compiling, with only the 3 L2 reds left.
  - 15 class-A deletions.
  - 5 class-B sites through fnvl_native_transfer.
  - 11 entry early-returns through fnvl_native_exit.
  - Local merge3 0dedb050e: 60 roots recipe valid. One red, the toolchain host root, which loads plugins (L2).
- Ruled:
  - fnvl_raise_resume(eip) is accepted: the hardware-raise path from lifted code, part of the generated transfer protocol. Both primitives live in enter.cc, the single hand-written writer of ra.
  - L1 takes the checked form: w28 writes the PP-fact gate, so the ra accessor header is allowed only in generator outputs and enter.cc.
  - The fixed EH keys 0xfffd0/0xfffe0 get bridges from bridgegen, which reads EH_KEYS from its one definition. They land in the same stack as the deletions.
  - The push excludes the local farm-pin commits once they are shown to be redundant with the L2 merge.
  - w7 facts2 dd2e506e9 is merged into base3, and the farm runs on base3 only.
- bottleneck: w8 base3 push; L2 (3 lift/rt units, the toolchain host root); w7 pcsrc reclassification.

## 2026-11-16 04:52 — base3 8c8d9b247: entry return, the fixed-key EH bridges, merges
- w8 (done, claude/fnv-l3a-lift-base3 8c8d9b247, old pin 84f0883e): pushed on top of 330e873c7.
  - Merged in: class A/B; w28 322698c53 and 08109b9ce; w13 ff086e540; w7 facts2 dd2e506e9.
  - The fixed-key EH bridges have one definition, tools/ehkeys.py, from which impgen emits thunks I_000fffd0 / I_000fffe0. The liftgen list and the guestgen copy are deleted.
- Counts:
  - class A: 15 deleted;
  - class B: 5 via fnvl_native_transfer;
  - fnvl_native_exit: 11;
  - fnvl_raise_resume: 2.
- Exports in lift/rt: entry 32, data 1, func 0, plus 2 ehkeys bridge-only entries.
- lift/rt at 8c8d9b247 is red: every unit is blocked at the plugin:fnvnx_names stage, because the cand4b plugin sources do not build against the old 84f0883e compiler headers. On the farm pin (local, the same code before the EH-bridge commit) it is 18 of 21, and the 3 reds are L2's.
- bottleneck: the L2 recipe-path pin (it blocks every lift/rt unit on base3); L2 w2 (3 units); w7 pcsrc reclassification; the farm-2 runs on 8c8d9b247.

## 2026-11-16 05:00 — base3 f96997661: 61 of 61 roots valid
- w8 (done, base3 f96997661, old pin): merged cand4b 3f1036631 (the host-root plugin fix).
  - The EH nested handlers are FNVNX_EXPORT records in pseudo-module '!rt', keyed by fnvnx_import_key per L1's rule. tools/ehkeys.py is deleted. L1 declares '!rt' in contract/modules.py.
  - Recipe check over all admitted roots: 61 valid, 0 red.
- lift/rt: on the old pin every unit is blocked at plugin:fnvnx_names. On the farm pin (an uncommitted overlay) 11 of 12 units build, and only dispatch.c (L2 ENTRY1-CONT) is red; the decltype reds are gone.
- bottleneck: the L2 recipe-path pin; L2 dispatch.c; w7 pcsrc reclassification; the farm-2 runs.

## 2026-11-16 05:06 — lift/rt 12 of 12 on the farm pin; the definer table
- w8 (base3 fee899583 = f96997661 + cand4b 4e40b5970): measured through the recipe with the farm pin d1a92e26 as an uncommitted overlay.
  - lift/rt is green: 12 of 12 objects, 109 defined, 52 undefined, 0 red stages.
- The 7-symbol definer table, each with one definer:
  - fnvnx_dispatch: dispatch.c;
  - fnvnx_lift_enter, fnvnx_guest_current and fnvnx_guest_invoke: enter.cc;
  - fnvl_msvcrt_adjust_fdiv: cpumodel.c;
  - _CxxThrowException: msvcrt_eh.cc;
  - fnvnx_lift_imports_init: the pack's imp.cpp, generated by impgen.
- L5 bind-l3 has 32 native symbols, and all 32 are defined by lift/rt.
- On the committed old pin lift/rt stays red (plugin:fnvnx_names). It turns green when L2 w3's recipe-path pin lands.
- w8 next: imagechain.py and the test-generator rework.
- bottleneck: the L2 recipe-path pin; w7 pcsrc reclassification and the udreach census; the farm-2 runs.

## 2026-11-16 05:15 — model-gap keys: candidate roots stay in the universe
- w7's census, fnv-nx-build l3a-lift/udreach/f5d49d6fa (exe + 5 images; d3dx9_31/38 pending the farm):
  - 333 of the 368 keys have no site.
  - 1,667 sites of 35 keys are inside the ranges: 87 under a proven root, 1,580 with a candidate (R3) root only.
  - ranges.tsv is the reached closure of the entries.
- Ruled to L3b: candidate (R3) roots are in the universe. Every general transfer's arrival set is the whole ptr set while ASM-012 has no gate. Removing them would need a proof that their relocated word reaches no transfer. L3b covers all 1,667 sites.
- Semantics on the declared machine:
  - ordinary user-mode instructions take ordinary semantics;
  - privileged instructions at CPL 3 / IOPL 0 raise #GP via SEH (OWNER 3).
- w7 (doing): per-key counts for exactly the x86isa-unimplemented rows, and the split by root kind. Decomp-only roots are not a rule source.
- bottleneck: the L2 recipe-path pin; the w7 census split; the farm-2 runs.

## 2026-11-16 05:22 — universe roots: recount by property
- w7, udreach/f5d49d6fa @96ecded:
  - The x86isa-unimplemented rows are exactly w4's 368 keys.
  - In the current universe: arpl, bound, out e6 and call far have 0 sites; ins/outs 6 (exe); mov sreg 90; other 1,571.
- Roots by discovery label (excluding `decomp`) leave 66.5 % of exe range bytes (7.89 MB of 11.86 MB) reached only through non-rooted entries. Rejected as a measurement: the roots must be taken by property (entry, every relocation target in code, exports, EH/SEH, TLS, import thunks) with the full closure (fallthrough, direct branches, call return, proven tables). The decomp list is not used.
- w7 (doing): the recount by property.
- bottleneck: the L2 recipe-path pin; the universe root recount (w7); the farm-2 runs.

## 2026-11-16 05:30 — assumptions gate for lift/
- w8 (base3 3e035ada1, w28 7c0cf9e3f merged):
  - 3 mention-no-id findings were descriptive negations, now reworded.
  - The TAB_BIAS record is deleted, because jump-table extents are jtab facts.
  - lift/liftsrc and certs README files renamed to .md.
  - The ASM-234 record (VirtualProtect/WriteProcessMemory/FlushInstructionCache protect sites stay OPEN until the points-to) is added as REMOVE, owner L3a.
  - Left for others: prove.py (L3b) and ASM-617/619/621 (L6).
- bottleneck: the L2 recipe-path pin; the universe root recount (w7); the farm-2 runs.
