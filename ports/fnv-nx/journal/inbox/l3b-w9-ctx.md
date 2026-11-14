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

## RF: exact by unobservability (lead's item b; cited argument, texts not re-fetched here)

EFlags.RF (bit 16) is taken out of the defect list. Argument: (1) RF only matters to the CPU as "suppress the instruction breakpoint (Dr0-3 under Dr7) and the code-breakpoint #DB on the next instruction" (Intel SDM vol. 3, "Resume flag (RF)" in the debug chapter; IRET/POPF can set it). With Dr7 = 0 no breakpoint is armed, so RF has no effect. (2) Dr7 = 0 on the declared machine (rt thread start: `c->dr` zero, header of seh.cc), and the model never arms a breakpoint from Dr0-3 (no code-breakpoint exception exists in the model: STATUS_SINGLE_STEP arises only from `icebp`). (3) PUSHF/PUSHFD (SDM vol. 2, PUSHF/PUSHFD: "the VM and RF flags (bits 16 and 17) are not copied; the values are cleared in the EFLAGS image stored on the stack") so `EFL_PUSH` storing RF = 0 is the architectural image, and no guest instruction can read RF back. Stated limit of the argument: a handler that writes a nonzero Dr7 through the same CONTEXT arms a breakpoint that the model does not implement; that is the debug-register model's own gap (Dr0-3, Dr7 are state, their trigger is not), not an RF one. Class: exact by unobservability. Open defects: 7 (FOP, FCS, FDS in both images counted as 3 fields, ErrorSelector, DataSelector, FSW ES/B, EFlags TF/NT -- by field: FloatSave SW, ErrSel, DataSel; Ext FSW, FOP, FCS, FDS; EFlags = 8 fields before, 7 after with RF out of the EFlags field's defect bits only if EFlags has no other open bit; EFlags still has TF and NT, so the field count stays 8 and the open BIT count is what drops).

Correction of the count: RF was one of three open bits of the single field EFlags. The field stays D (TF and NT remain), so the field count is still 8 D; the open bits go from 3 to 2 in EFlags (open bits total: FSW ES/B x2 fields, FOP/FCS/FDS x 2 images, TF, NT). The lead's "7" counts RF as its own defect; by field it is 8, by distinct defect (ES/B, FOP, FCS, FDS, TF, NT = 6 bit-groups, each present in one or two images) it is 6.

## Step 2

No gap of the table is closable in seh.cc alone (the 8 D fields need `struct cpu` state, above). The seh.cc header comment said "the exact inverse of ctx_capture, field for field"; it now states the 8-field limit and points here. Other arms (R 48) already match Wine's flag rules, `StatusWord &= ControlWord | 0xff80` and fpux_to_fpu / fpu_to_fpux (line-for-line against thread.c 95-160).

## Step 3 (ASM-220 / L3b:A-CTX)

`removed_by` is now `planned: ...` naming the rule, the field table, this note, the 8 open fields and the open test; status REMOVE, id and aliases kept. Two deviations from the brief, both forced by gate_assumptions: (1) the owner is `L3a+L3b`, not `L3b`: the id ASM-220 lies in L3a's range 200..299 (finding `id-range` for a plain L3b owner; the alias ASM-328 is in L3b's range, but the id cannot change); L3a owns the missing struct cpu state, L3b the rule and test; (2) the form must be `gate:`, `fact:`, `planned:`, `no gate yet:` or `OPEN:` (finding `removed-by-form`): `planned:` is the honest one while no gate or test exists. The gate is red at the base with 31 findings (mention-no-id etc.) and is red with 31 findings after this change (no finding added, none about ASM-220 or seh.cc). It was NOT turned green: the 31 are not mine. The statement "restores a reachable control state" is replaced by the table above, because the table shows 8 fields the restore cannot take back.

## Step 4: blocked

The lift/rt root is not buildable here. `python3 -I verify/recipe.py build --root ports/fnv-nx/lift/rt --store S --out S/out` prints `blocked: env` and lists every pinned package as `have not installed` (binutils-aarch64-linux-gnu 2.42-4ubuntu2.10, g++-13-aarch64-linux-gnu 13.3.0-6ubuntu2~24.04.1cross1, gcc-13-plugin-dev, libc6-dev-arm64-cross, ... ; farm/setup_verdict_env.sh installs them), and the external compiler `gcc-msvcmodel` (fnv-nx-build `tools/gcc-ilp32-msvcmodel`) is not present (no fnv-nx-build checkout in this container). The test needs a farm (or this container after setup_verdict_env.sh and `add_repo toadharvard/fnv-nx-build`), and its reference (oracle.json) should cite NtSetContextThread 926-1035 and thread.c 95-160 above. It must also cover the 8 D fields, so it is red until the state exists.
