# w6: reference table of the L3a selftests (item c), int3 at the commit that holds this file

Columns: test | subject | assertion | independent reference candidate | status (P = pinned and checkable now, C = candidate, needs a pin or a checker, N = none independent: the test compares the subject with itself)
Total 34 test files in lift roots (fnvlift 3, gates 4, proof 12, tools 15 + root_graph = 34 rows, counted by ls */tests/test_*.py at the commit); 0 oracle entries written by this note (see the end).

| test | subject | assertion | candidate reference | st |
|---|---|---|---|---|
| fnvlift/test_cs_store | core.gs_guard, f_sys.gp_cond | a store through CS raises #GP(0) = AV {0,0xFFFFFFFF}; a load does not | Intel SDM vol.3 5.1 (CS is not writable); Wine 9.0 dlls/ntdll/signal_i386.c segv_handler TRAP_x86_PROTFLT | C (SDM pdf/Wine tag not yet pinned in verify/pins) |
| fnvlift/test_jtab_facts | core.tab_tails | slots only from jtab_tables facts; no facts -> Unsupported | contract: tools/jtab.py rule (interval rule) | N (a contract of the lifter with itself) |
| fnvlift/test_sites | sites.site vs the emitter | for all D8..DF opcodes and ModRM bytes the emitter emits `.fnvl.fsw` iff site says so | SDM x87 opcode map (Table A-6..A-9), or capstone's group of the instruction (pinned in env.json?) | C (capstone version pin) |
| gates/test_gate_eh | gate_eh on synthetic packs | each synthetic EH table gets the stated colour | MSVC EH tables layout: the exe's own tables (FuncInfo/UnwindMap of FalloutNV.exe) | C (exe-derived, private: fnv-nx-build) |
| gates/test_gate_flags | gate_flags + flagagree | hand-assembled x86 bodies give the stated flag liveness verdict | SDM vol.2 per-instruction flags section; capstone EFLAGS fields (flagagree compares) | C (SDM table extraction, no pin) |
| gates/test_gate_lift | gate_lift.py: order, addr32, eh, flags | real tiny product roots built through recipe.py get the stated colour | VERDICT.md state of the gates (contract) | N (contract with itself) |
| gates/test_gate_order | gate_order on AArch64 asm | stated colour per object rule | Arm ARM (LDAR/STLR) + the x86-TSO to Arm mapping theorem (ASM-009 / formal) | C (published theorem, no checker) |
| proof/test_certs_build | proof/certs_build.py | shard records -> certs of three plain encodings | the Sail model result for those encodings (prove.prove_one) | P (the Sail model at its pinned patch is ASM-owner item 9) |
| proof/test_dsl_int_classes | proof/lift_dsl.py varc / emit_form | one rule per class (segment, bx/bp/si/di, 16-bit pop) "checked from the SDM" | SDM vol.2 | C |
| proof/test_dsl_lock | proof/lift_dsl emit under macc LK | every access in a locked instruction is the LK accessor | R9 (lock rule) | N |
| proof/test_formgate_real | proof/formgate.py on the real inputs | gate colour on the real certs external | the certs external's own tree_sha256 | N (compares with the fetched pinned data, not an independent derivation) |
| proof/test_forms_mem | forms.ea/addoff, static + dynamic | memory addresses go through one function; segment and a16 applied | SDM vol.1 3.7.5 (effective address, segment base, address size) | C |
| proof/test_model_patches | sail_patches/ series | an encoding of the patched behaviour proved against the DSL on the Sail model | the Sail x86 model at its pin, SDM section named per test | P |
| proof/test_rewrite | proof/rewrite.py, model_sail.zext_add_pairs | each rewrite is a bit-vector identity (z3, 8/16-bit, all inputs) | z3 at its pin (ASM item 9) with the identity stated in SMT-LIB theory of bit-vectors (QF_BV) | P (z3 pinned) |
| proof/test_rtroot | lift/rt/build.json via recipe.py | the build of the runtime is admitted and valid under recipe.guest_errors | recipe.py (contract) | N |
| proof/test_shape | proof/shape.py | shape keys of synthetic bytes | capstone decode of the same bytes | C |
| proof/test_symform | proof/symform.py | the symbolic Form instantiated = the concrete Form of the instruction | none independent: both sides are ours; the independent check is symcheck against the Sail model | N (covered by prove) |
| tools/test_bridgegen | tools/bridgegen.py | grammar of descriptors, accepted/rejected | DISPATCH1 spec (contract) | N |
| tools/test_descclass | tools/ descriptor classes | classes of args/results equal the backend's pass_by_reference of the pinned compiler | the pinned g++ itself (msvcmodel) | P (compiler pinned in verify/env.json; class asked of the compiler) |
| tools/test_disp | rt/dispatch.c + bridges | native indirect calls reach bodies; bridges into lifted code | AAPCS64 / the Arm procedure call standard, the exe's calling conventions (MSVC doc) | C |
| tools/test_dispgen | tools/dispgen.py | disp.S is a pure function of the entry list; lookup finds all keys | contract | N |
| tools/test_dsl_emit | proof/lift_dsl on all exe shapes | each emitted shape compiles (-fsyntax-only) | the pinned compiler | P for "compiles" (needs the exe: private input) |
| tools/test_ehflow | tools/ehflow.py (callee-saved) | proofs on real x86 code from ehflow_test.asm | the MSVC calling convention doc (callee-saved EBX/ESI/EDI/EBP) | C |
| tools/test_exitbridge | tools/exitgen.py | exit bridges in the three conventions | MSVC calling convention doc, pinned compiler | C |
| tools/test_imp | impgen entries, callbacks, unwinding through a native frame | behaviour on hand-assembled guest functions | Wine 9.0 / Win32 documented semantics of the imports | C |
| tools/test_jtab | tools/jtab.py | edges of real jump tables incl. CRT memcpy tails | the PE image's actual table contents (exe) | C (private) |
| tools/test_jtab_cases | tools/jtab.py | stated facts on raw bytes | none (hand cases) | N |
| tools/test_lifted_vs_shapes | lifted.py vs liftgen shapes | per-shape counts equal | liftgen output of the same image | N (two views of one lifter) |
| tools/test_lock_only | LK-ONLY rule | accesses in a locked instruction reach memory only through LKA | the compiled objects' relocations (readelf of the pinned binutils) | P |
| tools/test_modules_check | gate M | module universes | the PE files' own tables (exe/DLL) | C (private) |
| tools/test_msvcrt | rt/msvcrt_eh.cc, msvcrt_fp.cc entries | behaviour of _except_handler3, __CxxFrameHandler, _controlfp ... | MSVC CRT documented behaviour; Wine 9.0 dlls/msvcrt sources per function | C |
| tools/test_ranges | `.fnvl.sites` records | each record is exact (ldaxr..cbnz) | readelf of pinned binutils on the object | P |
| tools/test_root_graph | root import DAG | acyclic | none needed: a graph property of the tree (checked by the tree's own imports) | P |
| tools/test_universe_r3_check | gate R3 | relocated pointer targets | PE .reloc of the exe | C (private) |

## What I wrote in oracle.json files
Nothing in this note. gates/tests/oracle.json holds the four gate tests with the reference VERDICT.md "## State of the product gates" (kind spec): that is a contract of the gate with itself, rows N. I did not add entries for the P rows because each needs the anchor form of the checkable source (what the oracle checker accepts as "pinned": the Sail pin, the z3 pin, env.json compiler, readelf) and I could not find that form written on int3; tell me the accepted `kind`/`anchor` for them (or point me at the checker on the w19 branch) and I add the 9 P rows in one commit.
