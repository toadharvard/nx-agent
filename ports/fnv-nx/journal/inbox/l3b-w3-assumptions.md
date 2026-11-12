# w3: assumption records of L3b (brief item 4)

Inputs: lift/ASSUMPTIONS.l3b.json on claude/fnv-l3b-lift (41 ids, checked by `git grep` at that branch head for used_by);
registry verify/ASSUMPTIONS.json on claude/fnv-l1-contract-w10 (format fnvnx-assumptions/1, aliases written as `L3b:<id>`).

## Count (differs from the brief)
- Already aliased in the registry: 12 L3b ids, not the 13 the brief says. The brief's own list has 12 ids (A-SAILMODEL, A-PROVER, A-TSO-MAP -> ASM-009; A-FLAT, A-SYS -> ASM-003; A-EIPRANGE -> ASM-013; A-SEGLIM, A-NOSTRADDLE -> ASM-006; A-CAPSTONE-X86, A-CAPSTONE-A64 -> ASM-043; A-IEEE-X87FLAGS -> ASM-008; A-CALLEE -> ASM-010), and the registry has exactly these 12 `L3b:` aliases.
- To record: 41 - 12 = 29 records, plus I-FPMASK and I-FPSTICKY, which are the code names of A-FPMASK and A-FPSTICKY. They are aliases of those two entries.
- Result: 31 ids = 2 aliases (I-FPMASK, I-FPSTICKY) + 29 REMOVE entries. 0 aliases to an existing ASM entry: no record's text says the same as an existing entry (checked against ASM-010..ASM-045), so rule (a) was not used for the 29. used_by sites: 84 (file x entry pairs).

## Rules used
- Text = the `statement` of the l3b.json record, unchanged.
- removed_by: `gate:fpexact` only where fpexact is on the trunk (A-F2IHELPERS, A-RCPHOST); `no gate yet: ...` where the record's disposition is `open`; `planned: ...` where it is a `gate`/`fact` that is not wired yet. The text after the colon is the record's own disposition.
- owner (layer): by file of the record: lift/proof and lift/README = L3b, else lift/fnvlift, lift/rt, lift/tools = L3a; overrides from the disposition: A-F2INATIVE = L2, A-NATIVE = L2+L3a.
- used_by: every file of ports/fnv-nx that cites the id (word-boundary grep over the whole tree of claude/fnv-l3b-lift), the handoff file lift/ASSUMPTIONS.l3b.json itself excluded; for A-FPMASK and A-FPSTICKY also the files citing I-FPMASK / I-FPSTICKY.
- Ids are placeholders ASM-L3B-01..29 until L1 assigns a range. No entry is OWNER.

## Table
| id | l3b ids | alias/REMOVE | removed_by | owner | used_by |
|---|---|---|---|---|---|
| ASM-L3B-01 | A-EIP | REMOVE | planned: mode to delete; A-EIPRANGE (symbolic eip) is the only product | L3b | 4 |
| ASM-L3B-02 | A-SHAPEFAMILY | REMOVE | planned: same decoder-agreement gate, run on every occurrence (not the | L3b | 1 |
| ASM-L3B-03 | A-GROUPS | REMOVE | planned: same decoder-agreement gate, extension class compared with XE | L3a | 2 |
| ASM-L3B-04 | A-SDMREF | REMOVE | no gate yet: no machine reference yet; host comparison is exhaustive o | L3b | 1 |
| ASM-L3B-05 | A-UNDEFFLAGS | REMOVE | no gate yet: needs the reference-CPU table (same host as rcp), then a  | L3a | 1 |
| ASM-L3B-06 | A-FPMASK,I-FPMASK | REMOVE | planned: every control-word write (fldcw, ldmxcsr, fninit, frstor, fld | L3a | 10 |
| ASM-L3B-07 | A-FPSTICKY,I-FPSTICKY | REMOVE | planned: test-only; product path maintains the flags | L3a | 6 |
| ASM-L3B-08 | A-ORDER | REMOVE | no gate yet: not compiler correctness: GCC is allowed to reorder. Clos | L3a | 3 |
| ASM-L3B-09 | A-NATIVE | REMOVE | planned: rt/imp.h parks FPSR on imports; native bodies need the same b | L2+L3a | 2 |
| ASM-L3B-10 | A-RESUME | REMOVE | no gate yet: closable by canonicalising on resume in rt/seh.c (L3a) | L3a | 3 |
| ASM-L3B-11 | A-STALE | REMOVE | no gate yet: the CPU value is defined (old bits); closing = write back | L3a | 2 |
| ASM-L3B-12 | A-EMPTY | REMOVE | no gate yet: the CPU stores the kept 80-bit image; fv_free keeps it, s | L3a | 5 |
| ASM-L3B-13 | A-ES | REMOVE | planned: follows the A-FPMASK fact, or is implemented with the unmaske | L3a | 3 |
| ASM-L3B-14 | A-FDP | REMOVE | no gate yet: pin to the reference CPU by a host table, then fact | L3a | 4 |
| ASM-L3B-15 | A-FDS | REMOVE | no gate yet: wrong for FS operands on the CPU; closing = keep the sele | L3a | 4 |
| ASM-L3B-16 | A-FOP | REMOVE | planned: code is read-only (W^X of the lifted image) so the bytes are  | L3a | 3 |
| ASM-L3B-17 | A-RES | REMOVE | no gate yet: pin to the reference CPU by host comparison (exhaustive o | L3a | 3 |
| ASM-L3B-18 | A-BCD | REMOVE | no gate yet: pin to the reference CPU; per-digit-position independence | L3a | 5 |
| ASM-L3B-19 | A-DECODEFLOW | REMOVE | planned: universe closure gate of L3a | L3a | 2 |
| ASM-L3B-20 | A-STACKPRIVATE | REMOVE | planned: escape analysis is in memclass; verify_r9 (deferred until fir | L3a | 1 |
| ASM-L3B-21 | A-RCPHOST | REMOVE | gate:fpexact | L3b | 2 |
| ASM-L3B-22 | A-RCPPURE | REMOVE | no gate yet: checked on 20e6 inputs x 8 MXCSR values = sampling. Closi | L3a | 2 |
| ASM-L3B-23 | A-F2IHELPERS | REMOVE | gate:fpexact | L3b | 2 |
| ASM-L3B-24 | A-F2IKEY | REMOVE | planned: recipe cache key binds both once the plugin is a recipe stage | L3b | 2 |
| ASM-L3B-25 | A-F2IASM | REMOVE | planned: check 3b covers units with no site; closing fully = forbid in | L3a | 2 |
| ASM-L3B-26 | A-F2ICONST | REMOVE | planned: closable in the plugin: constant operand of a conversion = fi | L3b | 2 |
| ASM-L3B-27 | A-F2INATIVE | REMOVE | planned: L2 gate | L2 | 2 |
| ASM-L3B-28 | A-START | REMOVE | planned: set by the runtime at thread entry (rt/enter.c); being verifi | L3a | 2 |
| ASM-L3B-29 | A-CTX | REMOVE | no gate yet: a handler may write the FP control words in CONTEXT; bein | L3a | 3 |

## JSON fragment (entries to append to verify/ASSUMPTIONS.json)
```json
{
 "entries": [
  {
   "id": "ASM-L3B-01",
   "text": "With --eip fixed: semantics is translation-invariant in eip.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/README.md",
    "ports/fnv-nx/lift/proof/model_sail.py",
    "ports/fnv-nx/lift/proof/shapeprove.py"
   ],
   "aliases": [
    "L3b:A-EIP"
   ],
   "status": "REMOVE",
   "removed_by": "planned: mode to delete; A-EIPRANGE (symbolic eip) is the only product path",
   "owner": "L3b"
  },
  {
   "id": "ASM-L3B-02",
   "text": "Decoding (operand structure, family) is a function of the shape: masked imm/disp fields never change it.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md"
   ],
   "aliases": [
    "L3b:A-SHAPEFAMILY"
   ],
   "status": "REMOVE",
   "removed_by": "planned: same decoder-agreement gate, run on every occurrence (not the representative): operand structure of each occurrence equals that of its shape",
   "owner": "L3b"
  },
  {
   "id": "ASM-L3B-03",
   "text": "capstone group tags classify the instruction-set extension of a form correctly.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_cpufeat.py"
   ],
   "aliases": [
    "L3b:A-GROUPS"
   ],
   "status": "REMOVE",
   "removed_by": "planned: same decoder-agreement gate, extension class compared with XED isa-set on every instruction",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-04",
   "text": "For shapes outside the Sail model (x87 control/state, cpuid, rdtsc, part of SIMD: 7 019 shapes have no DSL builder yet) the reference is a transcription of the SDM compared with the host.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md"
   ],
   "aliases": [
    "L3b:A-SDMREF"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: no machine reference yet; host comparison is exhaustive only for 16/32-bit input spaces",
   "owner": "L3b"
  },
  {
   "id": "ASM-L3B-05",
   "text": "17 (mnemonic, undefined flag) pairs: the value the reference CPU produces is not yet pinned.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md"
   ],
   "aliases": [
    "L3b:A-UNDEFFLAGS"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: needs the reference-CPU table (same host as rcp), then a fact",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-06",
   "text": "No x87/SSE exception is ever unmasked (I-FPMASK); fwait and FP arithmetic never raise.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-002.md",
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/journal/status/l3b-lift.md",
    "ports/fnv-nx/lift/fnvlift/f_x87.py",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/fnvlift/fpfacts.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h",
    "ports/fnv-nx/lift/test/instest.py",
    "ports/fnv-nx/lift/test/mmxtest.py",
    "ports/fnv-nx/lift/tools/fpstate.py"
   ],
   "aliases": [
    "L3b:A-FPMASK",
    "L3b:I-FPMASK"
   ],
   "status": "REMOVE",
   "removed_by": "planned: every control-word write (fldcw, ldmxcsr, fninit, frstor, fldenv, fxrstor, _controlfp/_control87 with operand) found statically and proved to keep the masks; if not proved, exact unmasked-exception semantics before the first frame (orchestrator decision 2026-11-11; owner of the work L3b)",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-07",
   "text": "Test harness only: sticky flags not observed (I-FPSTICKY set in instest ASSUME).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/fpfacts.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h",
    "ports/fnv-nx/lift/test/instest.py",
    "ports/fnv-nx/lift/test/mmxtest.py",
    "ports/fnv-nx/lift/tools/fpstate.py"
   ],
   "aliases": [
    "L3b:A-FPSTICKY",
    "L3b:I-FPSTICKY"
   ],
   "status": "REMOVE",
   "removed_by": "planned: test-only; product path maintains the flags",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-08",
   "text": "GCC keeps an FP operation before the FPSR read that follows it in program order (GCC has no FENV_ACCESS).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/rt/fnvl_x87.h",
    "ports/fnv-nx/lift/test/fpflags.py"
   ],
   "aliases": [
    "L3b:A-ORDER"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: not compiler correctness: GCC is allowed to reorder. Closing rule: FPSR read through an operation the compiler must order (asm with FP operand dependency), then gate on objects",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-09",
   "text": "Host code between guest instructions (imports, native bodies) runs with the guest FPSR parked or raises nothing the guest sees.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-NATIVE"
   ],
   "status": "REMOVE",
   "removed_by": "planned: rt/imp.h parks FPSR on imports; native bodies need the same by rule (L2/L3a)",
   "owner": "L2+L3a"
  },
  {
   "id": "ASM-L3B-10",
   "text": "A handler resuming by ContinueExecution does not change the FP flag owner nor leave the x87/MMX register file in another carrier format.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-RESUME"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: closable by canonicalising on resume in rt/seh.c (L3a)",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-11",
   "text": "An MMX read of a register popped by a static-stack function, with no MMX write since, reads data undefined on the CPU too.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-STALE"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: the CPU value is defined (old bits); closing = write back on pop or prove no such read statically",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-12",
   "text": "fnsave stores the real indefinite for an empty register; fldenv/frstor treat an empty-tagged register per the tag only.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-001.md",
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h",
    "ports/fnv-nx/lift/test/instest_extra.py"
   ],
   "aliases": [
    "L3b:A-EMPTY"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: the CPU stores the kept 80-bit image; fv_free keeps it, so this can be made exact and the assumption removed",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-13",
   "text": "ES (bit 7) and B (bit 15) of FSW are 0.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-ES"
   ],
   "status": "REMOVE",
   "removed_by": "planned: follows the A-FPMASK fact, or is implemented with the unmasked semantics",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-14",
   "text": "FDP after an instruction without memory operand is unchanged (SDM leaves it unspecified).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-001.md",
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-FDP"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: pin to the reference CPU by a host table, then fact",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-15",
   "text": "FDS is DS whenever FDP != 0 (FS-relative operand reported with DS).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-001.md",
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-FDS"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: wrong for FS operands on the CPU; closing = keep the selector per operand (1 store)",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-16",
   "text": "FOP is recomputed from the opcode bytes at FIP when an image is written.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-FOP"
   ],
   "status": "REMOVE",
   "removed_by": "planned: code is read-only (W^X of the lifted image) so the bytes are those executed; needs the read-only fact from L3a universe",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-17",
   "text": "Reserved bits of the fnstenv/fnsave image are 0.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87_ctl.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h"
   ],
   "aliases": [
    "L3b:A-RES"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: pin to the reference CPU by host comparison (exhaustive over the state that feeds them)",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-18",
   "text": "fbld with a nibble above 9 gives the arithmetic sum nibble*10^i (SDM: undefined).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-001.md",
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/fnvlift/f_x87.py",
    "ports/fnv-nx/lift/rt/fnvl_x87.h",
    "ports/fnv-nx/lift/rt/x87.c"
   ],
   "aliases": [
    "L3b:A-BCD"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: pin to the reference CPU; per-digit-position independence would make a host table exhaustive",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-19",
   "text": "Only instructions of the lifter decode transfer control (constant-address proof for lock/xchg within a block).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/journal/inbox/l3b-w3-r9.md"
   ],
   "aliases": [
    "L3b:A-DECODEFLOW"
   ],
   "status": "REMOVE",
   "removed_by": "planned: universe closure gate of L3a",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-20",
   "text": "A stack slot at a proved coordinate of a frame whose address does not escape is accessed by its thread only (class PLAIN).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md"
   ],
   "aliases": [
    "L3b:A-STACKPRIVATE"
   ],
   "status": "REMOVE",
   "removed_by": "planned: escape analysis is in memclass; verify_r9 (deferred until first frame) is the independent check",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-21",
   "text": "rt/rcptab.cert.json was produced by farm/rcpcert.sh on a CPU with the reference identity.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/gate_fpexact.py"
   ],
   "aliases": [
    "L3b:A-RCPHOST"
   ],
   "status": "REMOVE",
   "removed_by": "gate:fpexact",
   "owner": "L3b"
  },
  {
   "id": "ASM-L3B-22",
   "text": "RCPSS/RSQRTSS are pure functions of the input bits: MXCSR does not influence them.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/gate_fpexact.py"
   ],
   "aliases": [
    "L3b:A-RCPPURE"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: checked on 20e6 inputs x 8 MXCSR values = sampling. Closing: rerun the 2^32 sweep under each MXCSR RC/FTZ/DAZ combination on the farm (8 x 189 s)",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-23",
   "text": "The helpers sse_f2i and fv_i are correct (range guard, rounding mode, integer indefinite).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/gate_fpexact.py"
   ],
   "aliases": [
    "L3b:A-F2IHELPERS"
   ],
   "status": "REMOVE",
   "removed_by": "gate:fpexact",
   "owner": "L3b"
  },
  {
   "id": "ASM-L3B-24",
   "text": "An object and its .f2i.json come from the same compile.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/gate_fpexact.py"
   ],
   "aliases": [
    "L3b:A-F2IKEY"
   ],
   "status": "REMOVE",
   "removed_by": "planned: recipe cache key binds both once the plugin is a recipe stage",
   "owner": "L3b"
  },
  {
   "id": "ASM-L3B-25",
   "text": "An inline asm without FP/vector operand and without FP clobber does no float->int conversion.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/gate_fpexact.py"
   ],
   "aliases": [
    "L3b:A-F2IASM"
   ],
   "status": "REMOVE",
   "removed_by": "planned: check 3b covers units with no site; closing fully = forbid inline asm outside listed rt files by the plugin",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-26",
   "text": "No float->int conversion of a C literal occurs in lifted code (GCC folds an out-of-range constant to the saturated value, not the x86 indefinite).",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/gate_fpexact.py"
   ],
   "aliases": [
    "L3b:A-F2ICONST"
   ],
   "status": "REMOVE",
   "removed_by": "planned: closable in the plugin: constant operand of a conversion = finding",
   "owner": "L3b"
  },
  {
   "id": "ASM-L3B-27",
   "text": "Native bodies compiled with fnvnx_ftoi are judged by L2 rules, not by fpexact.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/proof/gate_fpexact.py"
   ],
   "aliases": [
    "L3b:A-F2INATIVE"
   ],
   "status": "REMOVE",
   "removed_by": "planned: L2 gate",
   "owner": "L2"
  },
  {
   "id": "ASM-L3B-28",
   "text": "Every guest thread starts with FCW 0x027F and MXCSR 0x1F80.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/lift/tools/fpstate.py"
   ],
   "aliases": [
    "L3b:A-START"
   ],
   "status": "REMOVE",
   "removed_by": "planned: set by the runtime at thread entry (rt/enter.c); being verified against the code with the FP-mask fact",
   "owner": "L3a"
  },
  {
   "id": "ASM-L3B-29",
   "text": "The CONTEXT image handed to guest SEH handlers is only read back by the runtime restore, which restores a reachable control state.",
   "used_by": [
    "ports/fnv-nx/journal/inbox/l3b-lift-handoff-opus.md",
    "ports/fnv-nx/journal/status/l3b-lift.md",
    "ports/fnv-nx/lift/tools/fpstate.py"
   ],
   "aliases": [
    "L3b:A-CTX"
   ],
   "status": "REMOVE",
   "removed_by": "no gate yet: a handler may write the FP control words in CONTEXT; being checked against rt/seh.c with the FP-mask fact",
   "owner": "L3a"
  }
 ]
}
```
