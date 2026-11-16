## L3a LIFT: handoff to the new lead (2026-11-11 ~23:00Z)

Branch `claude/fnv-l3a-lift`, head 160438e plus this note (and the gate-D worker's last push, see below). Lifter dir
`ports/fnv-nx/lift`. Earlier notes: l3a-lift-001..004.

### Sessions
| who | session | branch |
|---|---|---|
| orchestrator | session_01KmtZKURkbywQNqjAdqyELR | claude/port-nsw-un6t9s |
| L4 VERIFY (farms) | session_01KmtZKTAZGR8huv8L6r19DK | fnv-nx-build l4-verify/..., writes l3a-lift/src/... for LIFTGEN |
| L1 | session_01KmtZKUSQnbJKPPsvtTQXf7 | claude/fnv-l1-contract |
| L3b | session_01KmtZKQYtMYtB5pgYaAbehV | merged into l3a at 2b46ec5 |
| gate-D worker | in-process subagent of the old lead (ends with it); told to push its state of tools/ehtables.py, tools/gate_eh.py, test/gate_eh_test.py to this branch as "wip" | claude/fnv-l3a-lift |

### Queue
| item | status |
|---|---|
| R9: every guest access ordered, lock forms as CAS loops (rt/fnvl.h GM/LK templates) | done, 160438e; accepted by the orchestrator at 1bb8d0d, the templates of 160438e are the same rule in asm form |
| Gates B / F / align on objects (`tools/gate_order.py`, test `test/gate_order_test.py` 50 cases OK) | done on the 599-unit smoke pack; NOT yet run on the full pack objects. Handed to L1 (message sent) |
| ORDERING.md "Lifted code": exempt-set rule + proof, assumptions A1-A4 (A4 = LOCK-SPLIT), lock counts | done, 160438e |
| Orchestrator's answer on the two exemptions wider than "sp/x29 + pc literals" (CPU class: x27 + offset proven inside struct cpu; IMAGE bounded table read) | OPEN, asked 22:50Z, no answer yet. If refused: make struct cpu accesses ldar/stlr in fnvl.h and drop the classes from the gate |
| LIFTGEN at 160438e (exe 64 shards, d3dx9_38 32, binkw32 / libvorbis / libvorbisfile 1 each) | REQUESTED from L4 22:48Z, no reply yet. Branches will be fnv-nx-build `l3a-lift/src/160438e.....[-module]/KofN` |
| Gate D four rules (ThrowInfo = constant 2nd argument of every _CxxThrowException call; SEH v3 table length = 1 + largest constant trylevel stored to [ebp-4]; frame stub live iff its address is a constant in universe code/data; empty scope table valid iff no trylevel >= 0 stored) | IN PROGRESS by the worker. Last numbers (d3dx9_38): 347 throw sites, 42 with a non-constant ThrowInfo still unresolved by the dataflow of tools/ehtables.py, SEH funcs 33 / tables 7 / non-constant stores 0, stubs live 140 / unreferenced 0. Not finished: the 42 must become constants by rule or be red. Then regenerate ehtables.json (FNVNX_FACTS_OUT), run `tools/gate_eh.py PACK EHFACTS` on the real pack, hand to L1 |
| After LIFTGEN: `tools/farm_packgen.sh SHA12 N BRIDGE IAT [MODULE:N ...]` (bridge 13c4573c49d7 per L4 handoff), `liftgen.py --recipe REF COMMIT TREE`, commit lift/pack/build.json, recipe build twice, gate_order on all pack objects + runtime objects | not started |
| Recipe compiler: lift/pack/build.json still names g++-13 (liftgen.py `recipe()`); must be the msvcmodel external (fnv-nx-build `tools/gcc-ilp32-msvcmodel` commit b4057704c0babd012e466cc89181132e02542289, tree_sha256 b60eafe7a5b0ae3d..., full value in L1's recipe; recipe.py form: `"compiler": {"external": NAME, "cc": ..., "cxx": ...}` + an `external` entry) | not started; I did not find the full tree_sha256 in any pushed build.json, ask L1 |
| Class C lifted half (no FP->int outside the _ftol rule; rcp table hash) | not started |
| Own share of `pure` gate violations; own numpy pin removal | not started |
| instest pre-existing diffs: 371 forms, 17 not equal (faults: div m32, idiv r32, int3, mov seg,m16, pop seg; movzx r16,m8 0fb6 upper-half diff; 12 x87 precision lines, allowed by the plain-IEEE decision but not yet separated by rule) | open; equal line for line between 2b46ec5 and 160438e |
| Multi-thread atomicity test for the lock rule; class (a) plain stack slots (waits for L3b memclass; rt/fnvl_mem.h still uses the removed P()) ; giant body L_018ebaba; A6 msvcrt imports | later |

### Packs and shards in fnv-nx-build
| branch family | lifter head | valid? |
|---|---|---|
| `l3a-lift/src/af437959275a[-module]/KofN` (64 + 32 + 1 + 1 + 1, complete) | af43795, contains R9 1bb8d0d | R9-ordered texts. Before the L3b lifter merge (2b46ec5: cpu features, MMX on x87) and before the asm templates; the prelude is written at `--finish`, so a finish with the 160438e tree gives the new accessors on these texts, but the texts lack the 2b46ec5 lifter changes. Prefer the 160438e relift |
| `l3a-lift/src/16ad274b2498...` (complete), `l3a-lift/obj/*`, `l3a-lift/pack/f764786639b5`, `l3a-lift/pack/trial-0of64`, `l3a-lift/src/73a708c624ad...` (partial) | before R9 | NOT valid (unordered guest accesses); never build into objects |
| `l3a-lift/facts` | universe / ranges / ehtables facts | valid; ehtables.json must be regenerated after the gate-D rules land |
No pack tree (`l3a-lift/pack/<sha12>`) exists yet for any R9 head.

### Numbers (commit 160438e)
- Gate on the 599-unit smoke pack: order 30384 / 30384, addrwrap 30384 / 30384, align 15090 / 15090; 331905 instructions; 16038 ordered instructions, 14346 plain accesses with dmb (misaligned path); host accesses 24263 cpu, 8497 frame, 67 image, 5 image-table; 0 rule violations.
- Locked instructions (tools/locksites.py): exe 69 (63 xchg r,m; lock add 1, and 1, not 1, sbb 1, sub 2), all through the CAS loop (69 / 69); 34 byte operands; 35 with alignment not proven statically (run-time test). d3dx9_38 98 (43 unproven, 1 constant misaligned), binkw32 8 / 8 unproven, libvorbis 0, libvorbisfile 1 / 1.
- Universe: exe 101591 entries (l3facts6/universe.tsv); gate eh (af43795) as in l3a-lift-004.
- instest: 371 forms, 354 equal.

### How to rerun
- Flags: `test/instest.py` FLAGS (with `-fno-jump-tables -fno-ipa-ra -ffixed-x19..x27`).
- Gate: `GATE_OUT=dir python3 -I tools/gate_order.py RTOBJ OBJDIR...` (RTOBJ = compiled rt/enter.c). Every object of the lifted image must be given (assumption A3).
- Counts: `python3 -I tools/locksites.py IMAGE RANGES [BASE]`.
- Facts on the old lead's container (lost with it; regenerate with tools/universe.py or take `l3a-lift/facts`): /home/user/l3facts6 (exe), /home/user/l3facts7/<module>.

### Rules in force
Commits carry no trailers at all (orchestrator 21:29Z). Decomp / exe-derived artifacts only in fnv-nx-build. No PRs, no JOURNAL.md edits.

### Gate D: the worker's final state (commit e51bc9e, "wip")
Only `lift/tools/ehtables.py` (the facts side) changed. `tools/gate_eh.py` and `test/gate_eh_test.py` are NOT updated to
the new facts schema, so the EH gate and its test do not match the facts at this head. None of the four rules is closed.
- Facts now produced: `throw_sites[{site,ti,obj,tail}]`, `failed_throwinfo`, `thrower_escapes`, `funcinfo[].stubs` with
  `live`, `scopes[]` with `trylevel_max / nonconst_stores / err / records`, `failed_funcinfo[{addr,err,live}]`. The keys
  `frame_regs` and `frame_handlers` are gone.
- The thrower is found by rule (the function that references the cell holding 0xE06D7363, or the IAT slot
  `_CxxThrowException`). The ThrowInfo argument is resolved by forward constant dataflow over the backward cone of the
  call site. It relies on callees preserving ebx/esi/edi/ebp: that is the callee-saved REMOVE assumption, to be checked
  from the exe's code, not accepted.
- Numbers before the last two fixes (facts in the old container, /home/user/l3facts7): d3dx9_38 347 throw sites, 42
  non-constant, 33 SEH functions, 0 non-constant trylevel stores, stubs 140 live / 0 unreferenced; binkw32 0 throw sites,
  31 SEH functions; libvorbis 80 and libvorbisfile 84 SEH functions, each with 1 empty table reported invalid. The exe
  was not recomputed with this ehtables.py. The d3dx9_38 rerun after the esp-relative fix did not finish: its count is
  unknown.
- Known defect: in libvorbis / libvorbisfile the "invalid empty table" is a false table candidate (the operand is the
  /GS cookie pushed before a `_local_unwind4`-like call) that comes from the `helpers` name-free heuristic in
  `scan_seh`. That heuristic must be replaced by a rule; until then the gate is red there, which is the correct side.
- Next: rerun the four modules and the exe (universe.py, about 25 minutes: a farm job), rewrite gate_eh.py and its test
  for the schema above (red on a throw site without a constant ThrowInfo except rethrow, on `failed_throwinfo`, on
  `thrower_escapes`, on `nonconst_stores`, on `len(records) != 1 + trylevel_max`), update the ehtables.py docstring and
  the README EH paragraph.
