# L3a LIFT status

Lead: Opus 5.5, branch `claude/fnv-l3a-lift`. Handoff read: 422c3d39 (`journal/inbox/l3a-lift-handoff-opus.md`).

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | Lifted pack as a product root: LIFTGEN at lifter 160438e (exe 64, d3dx9_38 32, binkw32/libvorbis/libvorbisfile 1), pack tree, `lift/pack/build.json` with the msvcmodel external (tools/gcc-ilp32-msvcmodel b4057704, tree b60eafe7a5b0...), admit.json entry, `recipe.py build` x2 cold + warm in GATE_CACHE | w1 session_01KmtZKSJcRwWUskQJfty9T7, branch claude/fnv-l3a-lift-w1 | doing |
| 2 | liftall.py only generates C++ | lead | done: liftall.py is deleted (73a708c6); liftgen.py calls no compiler. Stale comments naming liftall.py in rt/enter.c, rt/msvcrt_ci.c, rt/rt.c go with task 1's follow-up |
| 3 | Gates B/F/align on the whole pack (tools/gate_order.py over every object of the recipe build plus the runtime objects) | - | blocked by task 1 (objects) and by the runtime root `lift/rt` (needs the plugin stages of L1 w4 in recipe.py, owner L1) |
| 4 | ELF: link of all lifted roots plus platform, L1 and L5 gates | - | blocked by tasks 1, 3 and the platform root (L5, L1) |
| 5 | Gate D (eh): rules R1 ThrowInfo through caller arguments (interprocedural, address-taken = red), R2 callee-saved registers proven per callee (removes the REMOVE assumption), R3 SEH helper scope argument by dataflow (replaces the `helpers` heuristic), R4 trylevel/table length; gate_eh.py + test on the new facts; facts rerun; gate on the 160438e5 pack | w2 session_01KmtZKSUoqsFSfuD18vvg8X, branch claude/fnv-l3a-lift-w2 | doing |
| 6 | Orchestrator answer on the CPU-class and IMAGE-table exemptions of gate order | - | blocked by the orchestrator (asked 22:50Z by the previous lead) |

## Global numbers
| what | number | commit |
|---|---|---|
| exe universe | 101 590 entries (l3a-lift/facts bba4826) | facts bba4826 |
| module universes | d3dx9_38 38 853, binkw32 988, libvorbis 984, libvorbisfile 685 | facts bba4826 |
| complete R9 source packs | af437959 (exe 64/64 + 4 modules); 160438e: not lifted yet | - |
| pack trees / built objects of an R9 head | 0 / 0 | - |
| gate order/addrwrap/align on the whole pack | not measured (only the 599-unit smoke pack, which is no result) | - |
