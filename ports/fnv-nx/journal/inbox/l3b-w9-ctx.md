# l3b-w9: CONTEXT capture / restore review (path plan item 4.5)

Base: claude/fnv-l3a-lift-int3 (80cc1b8). Subject: `ports/fnv-nx/lift/rt/seh.cc` `ctx_capture` / `ctx_restore` (the field table: enums `C_*`, `FS_*`, `X_*`, `fx_capture`, `fx_restore`, `fpux_to_fpu`, `fpu_to_fpux`).
Wine 9.0 (tag wine-9.0), files read in full for the citations:
- `dlls/ntdll/unix/signal_i386.c`: `save_context` 792-843, `restore_context` 850-896 (signal return: no flags, everything), `NtSetContextThread` 926-1035 (the flag-driven restore that `NtContinue` / `ExceptionContinueExecution` uses: DR 953-973, INTEGER 978-983, CONTROL 987-992, SEGMENTS 996-999, EXTENDED_REGISTERS 1001-1007, FLOATING_POINT 1008-1017, `restore_flags` 1033).
- `dlls/ntdll/unix/thread.c`: `fpux_to_fpu` 95-134, `fpu_to_fpux` 141-160.

Note on the brief: CLAUDE.md on this base (80cc1b8) has no "Owner order of 2026-11-14"; the decision text was taken from the task prompt.

## Result in numbers (global: all 716 bytes / 70 fields of the i386 CONTEXT, at the base 80cc1b8)

Fields = the named scalar fields of the i386 CONTEXT, with FloatSave.RegisterArea, ExtendedRegisters ST0-7, XMM0-7, XMM8-15 counted per register and Reserved1-4 of XSAVE_FORMAT as 4.

| class | meaning | fields |
|---|---|---|
| R | captured under its flag and restored under the same flag, round trips (also where Wine itself narrows it: TagWord, see below) | 48 |
| D | captured, and the Wine restore reads it back into CPU state, our restore does not (defect) | 8 |
| W | captured, and Wine's restore does not read it into CPU state either (reserved / ignored by the CPU) | 14 |
| M | missing from capture | 0 |
| restored without a flag | restore reads a group whose flag is not set | 0 |
| total | | 70 |

ContextFlags bits: CONTROL 1, INTEGER 2, SEGMENTS 4, FLOATING_POINT 8, DEBUG_REGISTERS 0x10, EXTENDED_REGISTERS 0x20: every bit has a capture arm and a restore arm in seh.cc (6 of 6). CONTEXT_XSTATE (0x40) is not set and ignored on restore: the reference CPU has no AVX (rt/cpumodel.c leaf 1 ECX = 0), as in Wine (`cpu_info.ProcessorFeatureBits & CPU_FEATURE_AVX`, signal_i386.c 835, 939).

## The table

| off | field | flag | ctx_capture writes (seh.cc) | ctx_restore reads back into guest state | Wine 9.0 | class |
|---|---|---|---|---|---|---|
| 0 | ContextFlags | - | `M32(x+C_FLAGS)=flags` (0x1003F / 0x10007) | selects the arms (`fl`, bit 0x10000 masked) | save_context 799; NtSetContextThread 929 (`& ~CONTEXT_i386`) | R |
| 4,8,12,16 | Dr0-Dr3 | DEBUG | `c->dr[0..3]` (loop, `C_DR+4*i`) | `c->dr[0..3]` | save_context 816-819 ; restore_context 856-859 ; NtSetContextThread 953-973 | R (4) |
| 20,24 | Dr6, Dr7 | DEBUG | `c->dr[4..5]` | `c->dr[4..5]` | 820-821 ; 860-861 | R (2) |
| 28 | FloatSave.ControlWord | FP | `fpux_to_fpu`: `FS_CW = FCW` | `fpu_to_fpux` -> `X_FCW` -> `FLDCW` (fx_restore) | thread.c 99 ; 145 | R |
| 32 | FloatSave.StatusWord | FP | `FS_SW = fsw` (TOP, C0-C3 from `fc`, flags from `fex`/FPSR) | `fpu_to_fpux` -> `X_FSW` -> `ftop`, `fc`, `fex & FEX_X87` | thread.c 100 ; 146 | **D**: bits ES (7) and B (15) have no state (capture writes 0, restore drops them) |
| 36 | FloatSave.TagWord | FP | per register valid/zero/special/empty (`fpux_to_fpu`) | abridged (empty / not empty) -> `fx[p].k` | thread.c 111-132 ; 150-154 | R (Wine narrows 2-bit to 1-bit the same way) |
| 40 | FloatSave.ErrorOffset | FP | `FIP` | `c->fip` | thread.c 102 ; 147 | R |
| 44 | FloatSave.ErrorSelector | FP | FCS (low 16) \| FOP (high 16), both derived (ASM-213/214) | not read: FCS, FOP are derived values | thread.c 103 ; 148-149 | **D** |
| 48 | FloatSave.DataOffset | FP | `FDP` | `c->fdp` | thread.c 104 ; 150 | R |
| 52 | FloatSave.DataSelector | FP | FDS (DS when FDP != 0, derived, ASM-213) | not read | thread.c 105 ; 151 | **D** |
| 56..135 | FloatSave.RegisterArea (8 x 10 bytes, ST order) | FP | `fv_x(fx[(ftop+i)&7])` 80-bit images | `fx[p] = fvmk(tag?M80:E, m, se)` | thread.c 117 ; 156 | R |
| 136 | FloatSave.Cr0NpxState | FP | `fsw | 0xffff0000` | not read | thread.c 101 ; fpu_to_fpux does not read it | W |
| 140,144,148,152 | SegGs, SegFs, SegEs, SegDs | SEGMENTS | `fnvl_seg_get` | `fnvl_seg_set` (a selector the GDT lacks: Wine's bad-segment fault, header comment) | save_context 811-814 ; NtSetContextThread 996-999 | R (4) |
| 156,160,164,168,172,176 | Edi, Esi, Ebx, Edx, Ecx, Eax | INTEGER | the registers | the registers | 800-805 ; 978-983 | R (6) |
| 180 | Ebp | CONTROL | `ebp` | `ebp` | 806 ; 988 | R |
| 184 | Eip | CONTROL | `eip` argument | `c->ra` | 808 ; 989 | R |
| 188 | SegCs | CONTROL | `fnvl_seg_get(SEG_CS)` | `fnvl_seg_set(SEG_CS)` | 810 ; 991 | R |
| 192 | EFlags | CONTROL | `ef` argument (`c->eflags` or `SW_EFL`) | `c->eflags`; the lifted `RAISE_IN` takes CF PF AF ZF SF DF OF and AC, ID (`c->eflx`) from it | 807 ; 990 | **D**: TF (8), NT (14), RF (16) have no state (user mode can set them: iret); the model has no single step |
| 196 | Esp | CONTROL | `sp` argument | `esp` | 809 ; 987 | R |
| 200 | SegSs | CONTROL | `fnvl_seg_get(SEG_SS)` | `fnvl_seg_set(SEG_SS)` | 815 ; 992 | R |
| 204 | Ext.FCW | EXT | `c->fcw` | `FLDCW` | memcpy 833 ; NtSetContextThread 1003 | R |
| 206 | Ext.FSW | EXT | as FloatSave.StatusWord | `& (FCW | 0xff80)` then `ftop`, `fc`, `fex` | 1005 | **D** (ES, B) |
| 208 | Ext.FTW | EXT | abridged tag | abridged tag | 1003 | R |
| 210 | Ext.FOP | EXT | `fx_fop()` derived (ASM-214) | not read | 1003 (CPU loads it) | **D** |
| 212 | Ext.FIP | EXT | `c->fip` | `c->fip` | 1003 | R |
| 216 | Ext.FCS | EXT | `sel[CS]` if FIP != 0 (derived) | not read | 1003 | **D** |
| 220 | Ext.FDP | EXT | `c->fdp` | `c->fdp` | 1003 | R |
| 224 | Ext.FDS | EXT | `sel[DS]` if FDP != 0 (derived, ASM-213) | not read | 1003 | **D** |
| 228 | Ext.MXCSR | EXT | `c->mxcsr | sse_live()` | `c->mxcsr`, `sse_fpcr` | 1003 | R |
| 232 | Ext.MXCSR_MASK | EXT | `FNVL_MXCSR_MASK` | not read (hardware ignores it on FXRSTOR) | 1003 | W |
| 236..363 | Ext.ST0-ST7 (8 x 16 bytes; 10 value + 6 reserved) | EXT | 80-bit image | same as RegisterArea | 1003 | R (8) |
| 364..491 | Ext.XMM0-XMM7 | EXT | `c->x[i]` | `c->x[i]` | 1003 | R (8) |
| 492..619 | Ext.XMM8-XMM15 | EXT | 0 | not read (32-bit mode has no XMM8-15) | 1003 | W (8) |
| 620..715 | Ext.Reserved1-4 (4 groups) | EXT | 0 | not read | 1003 | W (4) |

Counts: R 48 = ContextFlags 1 + Dr 6 + FloatSave 5 (CW, TW, ErrOff, DataOff, RegisterArea) + segments 4 + integer 6 + control 5 (Ebp, Eip, Cs, Esp, Ss) + Ext 21 (FCW, FTW, FIP, FDP, MXCSR, ST 8, XMM0-7 8). D 8 = FloatSave 3 (SW, ErrSel, DataSel) + EFlags + Ext 4 (FSW, FOP, FCS, FDS). W 14 = Cr0NpxState + MXCSR_MASK + XMM8-15 (8) + Reserved (4).

Wine's two restore routes agree where checked: `restore_context` (signal return, 850) restores everything with no flags, `NtSetContextThread` (the `ExceptionContinueExecution` path) is flag-driven; seh.cc follows the second, which is the one the SEH continue decision names. Order of arms (EXT before FP: `else if`) and `StatusWord &= ControlWord | 0xff80` (1005) are the same as Wine; the FP-only arm without the mask (1008-1017) is the same.

## Defects (8 fields, reported; not fixable inside seh.cc)

All eight are "derived or unrepresented in struct cpu": capture writes a value computed from other state (or 0), and the restore of a handler-written value has nowhere to go. A restore that is the exact inverse needs state for them, not a change of seh.cc:
1. FOP, FCS, FDS (Ext.FOP/FCS/FDS, FloatSave.ErrorSelector high+low, DataSelector): ASM-213/214 of rt/fnvl_x87.h make them derived (FOP from the opcode bytes at FIP, FCS = CS, FDS = DS). Wine's restore puts the handler's value into the FPU environment, visible by FNSTENV/FNSAVE after the continue.
2. FSW bits ES (7) and B (15): no bit in `c->fex`/`fc` (`FEX_X87` = 0x27f, `fc` = 0x4500); the lifted FNSTSW never reports them.
3. EFlags TF (8), NT (14), RF (16): `EFL_POP` (fnvl.h) ignores them; the model has no single-step trap (STATUS_SINGLE_STEP arises only from `icebp`).
A class-closing rule would add the state to `struct cpu` (appended at the end, offsets hold) and make the x87 writers and `RAISE_OUT/RAISE_IN` carry it: owner L3a (lifter, rt/fnvl*.h). Decision needed from the lead: either (a) that work, or (b) an OWNER-registered limit of the declared machine (no unmasked x87 exceptions, no TF) with its record. I did NOT invent either; nothing in seh.cc was changed for them.

## RF and TF: exact by the debug-trap fact (decision of the orchestrator and L3a, replaces my earlier RF argument)

Dr7 = 0 is not a given: a CONTEXT restored with CONTEXT_DEBUG_REGISTERS, or SetThreadContext / NtSetContextThread, can write Dr0-Dr7. RF and TF therefore form one class, "guest-written debug-trap state". Status: exact by the debug-trap fact, owner L3a w6, the same site-value analysis as the masked-exceptions fact (2.5). The fact: at every reachable writer of TF (popfd, iret, CONTEXT.EFlags writers in reachable handlers and filters), of Dr0-Dr7 (CONTEXT.Dr* under CONTEXT_DEBUG_REGISTERS, SetThreadContext, NtSetContextThread) and of the ContextFlags that enable them, the value written is 0 or the flag is clear; an undecided site goes to `fnvnx_reach_violation` under the call-graph certificate. A site that really sets it means #DB is implemented. Given the fact RF is unobservable (no debug fault can arise; PUSHFD stores RF = 0, SDM vol. 2 PUSHF/PUSHFD, cited from memory, not re-fetched).

Agreed layout of the other defects: FSW ES/B (full 16-bit FSW, ES = B = (FSW & ~FCW & 0x3f) != 0 on status updates), FOP/FCS/FDS next to FIP/FDP, EFlags NT: "state agreed, struct cpu from L3a w6 (layout to come)". seh.cc's field table is extended when that commit lands. Not done yet: no struct cpu commit on this base.

## Step 2

No gap of the table is closable in seh.cc alone (the 8 D fields need `struct cpu` state, above). The seh.cc header comment said "the exact inverse of ctx_capture, field for field"; it now states the 8-field limit and points here. Other arms (R 48) already match Wine's flag rules, `StatusWord &= ControlWord | 0xff80` and fpux_to_fpu / fpu_to_fpux (line-for-line against thread.c 95-160).

## Step 3 (ASM-220 / L3b:A-CTX)

`removed_by` is now `planned: ...` naming the rule, the field table, this note, the 8 open fields and the open test; status REMOVE, id and aliases kept. Two deviations from the brief, both forced by gate_assumptions: (1) the owner is `L3a+L3b`, not `L3b`: the id ASM-220 lies in L3a's range 200..299 (finding `id-range` for a plain L3b owner; the alias ASM-328 is in L3b's range, but the id cannot change); L3a owns the missing struct cpu state, L3b the rule and test; (2) the form must be `gate:`, `fact:`, `planned:`, `no gate yet:` or `OPEN:` (finding `removed-by-form`): `planned:` is the honest one while no gate or test exists. The gate is red at the base with 31 findings (mention-no-id etc.) and is red with 31 findings after this change (no finding added, none about ASM-220 or seh.cc). It was NOT turned green: the 31 are not mine. The statement "restores a reachable control state" is replaced by the table above, because the table shows 8 fields the restore cannot take back.

## Step 4: the root builds, the no-double round trip test cannot be linked from this root

Environment (lead's item c): `farm/setup_verdict_env.sh` ran in this container (`envcheck: OK`, 47 packages from the mirror, 316 already at the pin); `toadharvard/fnv-nx-build` attached (read) and cloned at fd59184.
Build: `recipe.py build --root ports/fnv-nx/lift/rt --store S --out S/out --ext S/ext` -> `green: 12 objects, stages run 32 hit 0` at b2720d29 (cold; the external gcc-msvcmodel b4057704 fetched by commit, tree hash matched).
The test: seh.cc's `ctx_capture` / `ctx_restore` are static and only reached through `fnvl_raise_rec`, whose handler call goes through `fnvl_dispatch` (dispatch.c) to guest code. Linking the 12 objects leaves 18 undefined symbols, all defined by OTHER product roots, none by this one: ExitProcess UnhandledExceptionFilter fnvl_disp_table fnvl_g fnvl_phf_params (lifted packs) fnvnx_bridge_g fnvnx_bridge_phf_params fnvnx_bridge_table fnvnx_native_disp_table fnvnx_native_g fnvnx_native_phf_params fnvnx_call_fp fnvnx_win32_lookup_export fnvnx_win32_set_raise_backend (platform/bridge) fnvl_host_ticks_ns fnvl_host_tls_slot (host) _Unwind_Resume __gxx_personality_v0 (libgcc / libstdc++ of the guest runtime). Defining any of them in a test is a double under the owner order "no doubles in tests" (symbols the test defines intersect symbols a product root defines), so a no-double round trip needs the product ELF (or the L4 boot machine's link of these roots) and a machine that runs AArch64 (this container is x86_64; the old bench ran the objects under Unicorn AArch64, lift/bench exists on 21b10f1aa, not on this base). I wrote no test that would pass only through doubles. The test's design when that link exists: a guest handler (built as a test product root, only new symbols) writes every field; the expected state is derived from Wine 9.0 NtSetContextThread 926-1035 (cited in oracle.json), per ContextFlags value 0x1003F, 0x10007 and each single flag; the report is round trips passed / red per field (expected red on the open D fields).

## Test spec (to be written on the product ELF run by verify/bootmachine.py, one test together with w3's sehcodes replacement; no code with doubles, no lift/bench)

Subject: the guest-visible effect of `ExceptionContinueExecution` on the guest state (seh.cc `ctx_restore`), on the product ELF, hosted by the L4 boot machine's shared model.
Inputs, per case:
- a guest frame whose registered handler (a guest function of a test product root, new symbols only) receives (record, frame, context, dispatcher), writes EVERY CONTEXT field named in the table above to a case-specific distinct value (seeded: each field gets `0x5A000000 | field_offset`-derived values, FSW written with TOP and sticky bits, FCW with all exceptions masked, tags consistent with the registers, selectors from the model GDT, ST registers 80-bit patterns, XMM0-7 patterns), and returns ExceptionContinueExecution (0) with Context.Eip = a known continue label;
- the exception trigger: a hardware fault of each kind the rt raises (#DE via div by zero, int3) so ContextFlags = 0x1003F, and RtlUnwind-style capture for 0x10007;
- the ContextFlags the handler SETS before returning (the handler may change them): 0x1003F, 0x10007, and each single flag 0x10001 (CONTROL), 0x10002 (INTEGER), 0x10004 (SEGMENTS), 0x10008 (FLOATING_POINT), 0x10010 (DEBUG_REGISTERS), 0x10020 (EXTENDED_REGISTERS), plus every pair that is a Wine arm interaction: 0x10028 (FP and EXT together: EXT wins, `else if`).
Expected guest state after the continue, per case, computed by an oracle that is NOT seh.cc: a Python function of (state before the exception, the handler's writes, ContextFlags) implementing Wine 9.0 NtSetContextThread (dlls/ntdll/unix/signal_i386.c:926-1035): INTEGER 978-983 (eax ebx ecx edx esi edi), CONTROL 987-992 (esp ebp eip eflags cs ss), SEGMENTS 996-999 (ds es fs gs), EXTENDED_REGISTERS 1001-1007 (memcpy of the 512 bytes, then `StatusWord &= ControlWord | 0xff80` at 1005), else FLOATING_POINT 1008-1017 (fpu_to_fpux over the current image, thread.c:141-160), DEBUG_REGISTERS 953-973; and the rest of the state unchanged (every register, eflags arithmetic bits, x87 and SSE state, segments, debug registers not named by the flags). Values that the CPU does not keep (ignored fields W of the table: Cr0NpxState, MXCSR_MASK, XMM8-15, reserved bytes; FloatSave high halves of CW/SW/TW) are compared as "unchanged".
Verdict: per field, round trip passed / red (the 6 open defects red until the L3a struct cpu state lands, as the lead said), global count over cases x fields with the commit.
The oracle's reference entry in oracle.json cites NtSetContextThread 926-1035 and thread.c 95-160 of Wine 9.0 (wine-9.0 tag).

## Second test on the product ELF: rt/msvcrt_eh.cc against the EH oracle (step 3)

Same machine (verify/bootmachine.py, product ELF, no doubles). Subject: the runtime's use of the tables, `msvcrt_eh.cc` (`cxx_frame_handler`, `call_catch_block`, `cxx_local_unwind`, `except3/4`) as lifted code reaches them.
Inputs, per FuncInfo of the oracle's universe (lift/proof/ehoracle.py, 4,643 tables) and per (state, exception kind) row of its decision table (235,261 rows over 12 kinds): a guest frame whose registration carries that FuncInfo, a raised exception of that kind (a C exception, each ThrowInfo) at that state (the trylevel field set to the state), and the guest handler recording the observed decision.
Expected: the oracle's row: catch block (i, j) or ContinueSearch, the unwind-action sequence run to the tryblock's start level, the trylevel after the catch (end_level + 1); for SEH tables the hunt chain (level, filter, handler) and the cleanup sequence. The oracle computes it from the image bytes under the pinned Wine 9.0 rules (wine_pin.json, dlls/msvcrt/except_i386.c), so the expected values never come from msvcrt_eh.cc. The rows are produced once by the oracle (report JSON, private) and replayed; the verdict is rows agreeing / rows, globally, with the commit.
