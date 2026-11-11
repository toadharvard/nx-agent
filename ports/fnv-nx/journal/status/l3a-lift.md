# L3a LIFT status

Lead: Opus 5.5, branch `claude/fnv-l3a-lift`. Handoff read: 422c3d39 (`journal/inbox/l3a-lift-handoff-opus.md`).

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | Lifted pack as a product root: LIFTGEN at lifter 7b2faae2 (= 160438e lift tree + the trunk env.json; the 160438e env.json mismatched the declared environment on /usr/include) (exe 64, d3dx9_38 32, binkw32/libvorbis/libvorbisfile 1), pack tree, `lift/pack/build.json` with the msvcmodel external (tools/gcc-ilp32-msvcmodel b4057704, tree b60eafe7a5b0...), admit.json entry, `recipe.py build` x2 cold + warm in GATE_CACHE | w1 session_01KmtZKSJcRwWUskQJfty9T7, branch claude/fnv-l3a-lift-w1 | doing |
| 2 | liftall.py only generates C++ | lead | done: liftall.py is deleted (73a708c6); liftgen.py calls no compiler. Stale comments naming liftall.py in rt/enter.c, rt/msvcrt_ci.c, rt/rt.c go with task 1's follow-up |
| 3 | Gates B/F/align on the whole pack (tools/gate_order.py over every object of the recipe build plus the runtime objects) | - | blocked by task 1 (pack objects) and task 3a |
| 3a | Runtime `lift/rt` as a product root (msvcmodel + plugins, cxx_origin native, on the L1 w4 recipe machinery), build x2 cold + warm, gate_order on the rt objects | w3 session_01KmtZKSV4DsNmobaEQ2Fzvq, branch claude/fnv-l3a-lift-w3 | doing (plugin stages are on L1 w4, not yet on the trunk: owner L1) |
| 4 | ELF: link of all lifted roots plus platform, L1 and L5 gates | - | blocked by tasks 1, 3 and the platform root (L5, L1) |
| 5 | Gate D (eh): rules R1 ThrowInfo through caller arguments (interprocedural, address-taken = red), R2 callee-saved registers proven per callee (removes the REMOVE assumption), R3 SEH helper scope argument by dataflow (replaces the `helpers` heuristic), R4 trylevel/table length; gate_eh.py + test on the new facts; facts rerun; gate on the 160438e5 pack | w2 session_01KmtZKSUoqsFSfuD18vvg8X, branch claude/fnv-l3a-lift-w2 | doing |
| 7 | GalaxyWrp.dll, Galaxy.dll (PE-import closure, L1): facts + LIFTGEN at 7b2faae2, then a re-pack with them | w4 session_01KmtZKRAqKi3LXmngGKy2eJ, branch claude/fnv-l3a-lift-w4 | doing |
| 8 | A4 LOCK-SPLIT deleted: the misaligned locked path stops the world (fnvl_world_stop/resume) | L3b (rt/fnvl.h), L5 (host functions) | requested 22:43Z |
| 9 | ASSUMPTIONS records A1 (OWNER 1 for compiler output; REMOVE for .S by a gate_order check), A2 (REMOVE: loader layout fact), A3 (REMOVE: L1 build manifest) | lead | sent to L1 22:43Z |
| 10 | gate_order: rows() for L1; per-object check of no write to x19..x27 outside the guest-register rule and no jump tables; .S callee-saved check; ORDERING.md A1/A3/A4 | w5 session_01KmtZKTakZKNZHndJajh1Qf, branch claude/fnv-l3a-lift-w5 | doing |
| 11 | pure-gate violations in L3a files (104 in lift, by L3b's count) and test doubles (instest/mmxtest rt.S, sehcodes, imptest/msvcrttest native.c) to 0 | - | queued |
| 6 | Orchestrator answer on the CPU-class and IMAGE-table exemptions of gate order | - | blocked by the orchestrator (asked 22:50Z by the previous lead) |

## Global numbers
| what | number | commit |
|---|---|---|
| exe universe | 101 590 entries (l3a-lift/facts bba4826) | facts bba4826 |
| module universes | d3dx9_38 38 853, binkw32 988, libvorbis 984, libvorbisfile 685 | facts bba4826 |
| complete R9 source packs | af437959 (exe 64/64 + 4 modules); 160438e: not lifted yet | - |
| pack trees / built objects of an R9 head | 0 / 0 | - |
| gate order/addrwrap/align on the whole pack | not measured (only the 599-unit smoke pack, which is no result) | - |
