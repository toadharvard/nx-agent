# L3a LIFT status

Lead: Opus 5.5, branch `claude/fnv-l3a-lift`. Handoff read: 422c3d39 (`journal/inbox/l3a-lift-handoff-opus.md`).

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | Lifted pack as a product root: LIFTGEN at lifter 7b2faae2 (= 160438e lift tree + the trunk env.json; the 160438e env.json mismatched the declared environment on /usr/include) (exe 64, d3dx9_38 32, binkw32/libvorbis/libvorbisfile 1), pack tree, `lift/pack/build.json` with the msvcmodel external (tools/gcc-ilp32-msvcmodel b4057704, tree b60eafe7a5b0...), admit.json entry, `recipe.py build` x2 cold + warm in GATE_CACHE | w1 session_01KmtZKSJcRwWUskQJfty9T7, branch claude/fnv-l3a-lift-w1 | doing |
| 2 | liftall.py only generates C++ | lead | done: liftall.py is deleted (73a708c6); liftgen.py calls no compiler. Stale comments naming liftall.py in rt/enter.c, rt/msvcrt_ci.c, rt/rt.c go with task 1's follow-up |
| 3 | Gates B/F/align on the whole pack (tools/gate_order.py over every object of the recipe build plus the runtime objects) | - | blocked by task 1 (pack objects) and task 3a |
| 3a | Runtime `lift/rt` as a product root (orchestrator: L3a owns it; L3b w2 b4d9c61a is ahead: green cold x2 + warm, 11 objects, determinism and objstub green; w3 merges b4d9c61a with toolchain/order and the fnvnx_order plugin tests reverted to the trunk (L2 w4 owns them), plus L3b w3 e0b4abbe (form A deleted, explicit RMW in seh/msvcrt_eh; reviewed, accepted); stale fnvnx_f2i attribute closed by rule: -Werror=attributes in every guest root (lift/rt now, recipe.py requirement asked of L1); linkstrict 16 undefined) (msvcmodel + plugins, cxx_origin native, on the L1 w4 recipe machinery), build x2 cold + warm, gate_order on the rt objects | w3 session_01KmtZKSV4DsNmobaEQ2Fzvq, branch claude/fnv-l3a-lift-w3 | blocked by L2: fnvnx_order "cannot fence after a block-ending statement" (enter.cc:100, longjmp operand); L2 accepted, rule ORDER-CONT (acquire fence at every resumption point) in L2 w4; L3a confirmed lifted frames need no resumption fences (each lifted access self-ordered, nonlocal entries land at native rt receivers); L1 asked for a recipe that judges every unit. Cold build on trunk d7fac9b5: 5/11 units built (C), 6 C++ units unjudged |
| 4 | ELF: link of all lifted roots plus platform, L1 and L5 gates | - | blocked by tasks 1, 3 and the platform root (L5, L1) |
| 5 | Gate D (eh): rules R1 ThrowInfo through caller arguments (interprocedural, address-taken = red), R2 callee-saved registers proven per callee (removes the REMOVE assumption), R3 SEH helper scope argument by dataflow (replaces the `helpers` heuristic), R4 trylevel/table length; gate_eh.py + test on the new facts; facts rerun; gate on the 160438e5 pack | w2 session_01KmtZKSUoqsFSfuD18vvg8X, branch claude/fnv-l3a-lift-w2 | doing |
| 7 | GalaxyWrp.dll, Galaxy.dll (PE-import closure, L1) | w4 session_01KmtZKRAqKi3LXmngGKy2eJ | galaxy.dll: facts l3a-lift/facts 7c90ab0d (28 783 entries), lifted 3461ebb3 galaxy:23, 0 failed; codemap RED 65 runs / 32 135 bytes unexplained, analysis by rule doing. GalaxyWrp.dll: blocked by L1 (no modules.tsv row; asked 22:51Z) |
| 16 | Gates order (B), eh (D), addr32 (F) in trunk gates.json (universe 0 green, rules per VERDICT.md, selftests); tool root lift/gates | w6 session_01KmtZKR34WmRCfdDoE7ajAt, branch claude/fnv-l3a-lift-gates (trunk-based) | doing |
| 8 | A4 LOCK-SPLIT deleted: the misaligned locked path stops the world | w3 (rt/fnvl.h, in item 13's batch); L5 has fnvl_host_world_stop/resume (claude/fnv-l5-platform 112c04ce) | doing |
| 9 | ASSUMPTIONS records A1 = ASM-001 for compiler output, ASM-200 (.S, REMOVE by gate_order), ASM-201 A2 (REMOVE, loader layout, L4+L5), ASM-202 A3 (REMOVE, fnvnx-built/1 + object rule); records in verify/ASSUMPTIONS.json in the same merge | w5 | doing (accepted by L1 22:43Z) |
| 10 | gate_order: rows() for L1; per-object check of no write to x19..x27 outside the guest-register rule and no jump tables; .S callee-saved check; ORDERING.md A1/A3/A4 | w5 session_01KmtZKTakZKNZHndJajh1Qf, branch claude/fnv-l3a-lift-w5 | doing: rows() done e9c717a (5/5 tests); 2(a)(b)(c) next, br rule given by the lead; added (8) W-form copy of x27 + non-wrapping bounded add stays CPU (ASM-007, ASM-201), (9) universe = objects of roots with lifted cxx_origin only (native rt closed by fnvnx_order) |
| 11 | pure-gate violations in L3a files (104 in lift, by L3b's count) and test doubles (instest/mmxtest rt.S, sehcodes, imptest/msvcrttest native.c) to 0 | - | queued |
| 6 | Gate order host classes (orchestrator 22:41Z): CPU = x27 never written + offset inside sizeof(struct cpu); IMAGE only into read-only sections (no SHF_WRITE), else guest | w5 | doing |
| 12 | shapes.tsv in the pack (liftgen shard + finish, shape_key of lift/proof), for L3b's formgate; then relift + repack | w1 | merged 8d6de5c9 (review: shape_key/family imported, finish fails on a missing shapes_K or a key with two families); the product relift waits for item 13's batch (every unit changes) |
| 13 | SEH state, one lifter/prelude batch: table-driven ctx_capture/ctx_restore (restore = inverse, reads ContextFlags back; ContextFlags as Wine 9.0 signal_i386.c sets them), C0/C2/C3 and EFlags flushed at every explicit raise site and reloaded on continue, dead struct cpu fields st/ftag deleted, misaligned locked path under fnvl_world_stop/resume | w3 | doing (one commit, lead review before any relift) |
| 15 | Asynchronous fault delivery at an arbitrary guest access: recovery of the lifter's live state (flags, x87 C bits) at the faulting pc | lead | open class, reported to the orchestrator |
| 14 | Undefined symbols of lift/rt (16 by L3b's census): owner per symbol by contract (OS/kernel service = L5, guest state only = rt), agreed with L5 | lead + w3 (the exact set from objects.json) | doing |

## Global numbers
| what | number | commit |
|---|---|---|
| exe universe | 101 590 entries (l3a-lift/facts bba4826) | facts bba4826 |
| module universes | d3dx9_38 38 853, binkw32 988, libvorbis 984, libvorbisfile 685 | facts bba4826 |
| complete R9 source packs | af437959 (exe 64/64 + 4 modules); 160438e: not lifted yet | - |
| pack trees / built objects of an R9 head | 0 / 0 | - |
| gate order/addrwrap/align on the whole pack | not measured (only the 599-unit smoke pack, which is no result) | - |
