# l3b-w3-r9: R9 on one form (L3a's GM/LKA), worker w3

Branch claude/fnv-l3b-lift-w3 (from fd8ef066 + lead status 32a4e23).

## Merged
- origin/claude/fnv-l3a-lift at 6660ae2 (newer head than 160438e; it contains 160438e). Clean merge, no conflicts (35a408f): fnvl.h took L3a's GM/LKA templates, no
  include of fnvl_mem.h any more. The l3b-r9 bundle (c05d6152) was used for reference only.

## Deleted
- rt/fnvl_mem.h; fnvlift/memclass.py (the whole emission module: FNVL_R9 / FNVL_PLAIN wrappers, PlainSet, from_env / env FNVL_MEMCLASS, const_address, lock path apply);
  test/r9test.py; test/addrwrap.py; test/r9equal.py; tools/r9report.py. Nothing in fnvlift/core.py referenced the memclass hook (L3a's merge had already left it unwired),
  so the lifter emits only L3a's form: every access ordered. test/instest.py: removed the two fnvl_world_stop/resume rt.S symbols (nothing references them; their comment pointed at fnvl_mem.h).
- Kept unchanged: tools/memclass.py, test/memclass_det.py, verify_r9/ (analysis, report, determinism). Emission of the plain class onto GM<T> is a separate later task.
- Fixed because they did not compile with the GM form: rt/seh.c:161 (`M32(..) |= ..`) and rt/msvcrt_eh.c:301 (`&=`), :414 (P()): explicit read-modify-write, unlocked as before.
  All 11 rt/*.c now compile with test/instest.py FLAGS (aarch64-linux-gnu-g++-13 of env.json).

## Coverage of the deleted checks by tools/gate_order.py (gates order / addrwrap / align, plus test/gate_order_test.py, 4 tests OK)
| deleted | what it checked | covered by |
|---|---|---|
| r9test.py: every guest access ordered (ldar/stlr when aligned, ldr+dmb ishld / dmb ishst+str when not), exclusive loops, 16-byte dmb form | order (B) on the objects: every guest access is ordered or has the dmb in its block; align: every ordered wide access has the tst/branch |
| addrwrap.py: address = result of a 32-bit op, offset 0, no index, no writeback, no relocation, on every path | addrwrap (F): operand is the base register alone, base proven 32-bit on every path; negative controls are in gate_order_test.py |
| r9report.py: instruction counts per class | gate_order detail line (guest_ordered, guest_plain_load_dmb/_store_dmb, host_*, instructions) |
| r9equal.py: lift equality PRE vs R9 text, every lock/xchg site lifts | nothing needed (form A text rewrite is gone); "every site lifts" = golden 0 errors |

## NOT covered by gate_order.py (for the lead)
1. r9test/addrwrap probe suites for each accessor path at widths 8..64 and constant offsets (0x40 0xfffffffc 0x1183000 0) with and without an index term, with a coverage check that each probe shows the instructions of its path. gate_order runs on the real objects, so a path that no lifted unit uses is unchecked. A GM/LKA probe object (every width, aligned/unaligned/constant/non-constant, LKA load+commit) through gate_order is not written.
2. The world-stop window check of r9report (no longer exists: LKA uses the spin lock fnvnx_split_lock). A-LOCK-SPLIT stays open: plain store of another thread vs the locked split path (the open question to L3a).
3. Plain-vs-ordered class counts per memclass class (class_totals) are not tied to emitted code any more.
4. lift/ASSUMPTIONS.l3b.json A-DECODEFLOW still names lift/fnvlift/memclass.py (deleted); the property (constant-address proof for lock/xchg within a block) no longer exists. The record needs removing or re-pointing by whoever owns the registry; I did not edit it.

## Numbers (commit aef9b84 plus nothing after; measured here, whole sets where stated)
- golden (whole universe, FNVNX_EXE = fnv-nx-build gamedata/exe FalloutNV.exe, FNVNX_UNIVERSE = l3a-lift/facts universe.tsv at its current content): **entries 101590, lifted 101590, failed 0** (digest sha256 8f72dc91163e787d2d3c96a319ebc510c3a9129f6db70a664e853f800ef6b533, on the merge commit before the rt fix; the lifter files are unchanged since). The universe is 101 590 entries (L3a rule), not the old 99 927.
- gate_order_test: 4 / 4 OK.
- Compile: NOT the whole universe (about 3 min per 400 entries on one core; farm / w2's recipe build). Sample of 400 universe entries (every 253rd) compiles with 0 errors, 2.6 MB object (a sample, not a result).
- gate_order on that sample object + enter.o (the lifted code alone): order 41200/41264, addrwrap 41204/41264 RED, align 20425/20425 green. All 50-64 uncertified are x87 register-stack slot accesses, e.g. `mov w1,w27; ubfiz w2,w0,#4,#3; add w2,w2,w1; str wzr,[x2,#340]`: a cpu slot indexed by ftop&7 (<=112, +340 < 536, inside struct cpu) which the gate classes as GUEST (the W-form copy of x27 / the register index sum is not proven CPU). Likely a gate precision gap (tools/gate_order.py CPU class with a W-form mov of x27 plus a bounded register); not touched, for L3a / the lead.
- gate_order on the runtime objects (rt/*.c compiled, native code): red in cpumodel.o (11) and fmath.o (39): native C stores through pointer arguments that the gate treats as guest accesses; 0 in the other runtime objects. The runtime is native, not lifted; whether it belongs to the gate's universe is for the lead / L3a.
- Not done: the second build path (tests still call the cross gcc directly; w2 removes it); no new direct calls added to the repo, my compile runs were local and not committed.
