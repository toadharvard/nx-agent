# Per-site source patches (stream S1b-patches)

Some decomp sites cannot be fixed by a rule because each one needs a fact read from the original x86 code: a
caller that relies on ecx = this, the float that an arg-less `_ftol2_sse` call converts, the value a function returns
when it falls off its end. D12 gives us that code without the exe: the pristine decomp built with MSVC 2008 is
byte-identical to the exe (S11's objects, `tools/equiv/README.md`). Every site here was read from that code, fixed
in the source, and checked twice: MSVC compiles the patched function to the same bytes, and GCC (census flags)
still compiles the unit.

## Mechanism

`tools/rules/s1b_patches.py` (rules `XINC1`, `FTOL1`, `DBUG1`, `RET1`) applies `patches/*.s1b` after the other
rules. A site is one function, keyed by source path and `// @fnv` address; its hunks replace old text with new text:

    @@ XINC1 src/bsshader/bsshadersdm.cpp 0x00bc28f0
    # evidence: what the x86 code shows (instructions described, no decomp code copied)
    <<<
    old text (verbatim from the decomp)
    ===
    new text
    >>>

The old text must occur exactly once between the `// @fnv` tag before the site's tag and the tag after it (`file`
sites: the whole file); the match is exact, then whitespace-tolerant. Hunks avoid text that earlier rules rewrite
(`throw()` (EXC1), `extern "C"` on data (LINK1), the closing `}` of a function (FALL1)), so the same hunk applies to
the pristine decomp (MSVC check) and to the prepared tree (GCC). A hunk that does not apply is counted
`<RULE>-MISS` and reported; the file stays as it was for that hunk. Line numbers are kept except where a hunk needs
preprocessor lines (computersmenu.cpp, blk_00e40000_o2.cpp).

**units.json renames.** A fix that turns a free function into a member changes its MSVC decoration, so the recipe
name of that definition (and the decorated names the patched unit now uses for its callees) change too.
`patches/units_renames.tsv` lists them as measured from the MSVC objects (`tests/s1b/msvc_check.py --renames`);
prepare applies it to the prepared `build/units.json` through the module's `POST` hook (prepare.py runs stream
`POST` hooks since J-0046), so S2 binds the renamed definitions and checks the new references exactly. Shell
methods are named after their callee's address (`FUN_<addr>`), so S2's name-address fallback also binds them.

## Checks (`tests/s1b/`)

| Script | What |
|---|---|
| `test_patches.py DECOMP` | every site parses, has evidence, applies exactly once to the pristine and to the rule-prepared text; renames well-formed |
| `msvc_check.py DECOMP WORK [--all-functions] [--patches DIR]` | patches on the pristine text, each unit compiled with its recipe compiler/flags (S11 setup), every function fingerprinted against `$S11/obj/pristine` with S11's bindings (literals by content); renamed definitions and references followed; `--renames` writes `units_renames.tsv` |
| `xinc1/xinc_enum.py`, `analyze.py`, `gen.py` | round-2 pipeline: x86-inconsistent references of a link run -> callers in the pristine objects -> ecx at each reference (`ecxflow.py`) -> candidate XINC1 patches for shapes A/B/C, the rest listed for reading by hand; `preserves.py ADDR`: does a function keep ecx / edx at every return |
| `test_ftol.py` | FTOL1's inline `fnvnx_ftol2_sse` equals `cvttsd2si` on 48 values (qemu-aarch64 against the host's SSE2), stays inline, compiles for ILP32 |
| `gcc_check.py DECOMP BASE_TREE WORK` | patched files prepared with all rules into an overlay of a prepared tree, compiled with the census flags before/after; reports new errors and errors on patched lines |

`msvc/s1b_check_fi.h` gives `fnvnx_ftol2_sse(x)` its MSVC meaning for the check only: the compiler's own
float -> `int` conversion, i.e. a call of `_ftol2_sse` with x in ST0 (`fnvnx_ftol2(x)`: the `__int64` one).
`test_ftol.py` checks the target helper against the host's `cvttsd2si` (48 values: NaN payloads, infinities, int32
bounds and neighbours, beyond int32 / int64 / float range, float operands) under qemu-aarch64, and that it stays
inline.

## Results (decomp `87eb9373`)

| Rule | Sites | MSVC check | Notes |
|---|---:|---|---|
| XINC1 | all 68 x86-inconsistent pairs of S2's latest run (a 69th of an earlier run is bridged by S2 now): 66 functions, 49 files | 62 identical, 4 differ by design | see below |
| XINC1 round 2 | S2c run f12: 295 x86-inconsistent references, 295 callers in 174 files; 264 callers patched in 152 files (`xinc1_r2_*.s1b`) | 259 identical, 5 differ by design | 26 need no patch, 4 open, 1 patched in round 1 (see "Round 2") |
| XINC1 round 3 | S2c run f13 (resolve 1, tree 0420d45, before round 2): 456 caller rows, 266 covered by rounds 1-2; 128 of the 188 open generated (`xinc1_r3_*.s1b`: A 21, B 15, C 92; 27 files) | identical (all 156 site functions of the 27 files) | 51 callers left to read by hand, about 30 of them round 2's no-patch / open ones |
| XINC1 round 4 | farm link/aedcf247e999 (tree 9a0243c, C-named references now compared): 757 this / ecx / result-pointer / cast-conflict rows, 576 caller rows, 388 covered by rounds 1-3; 66 generated + 9 by hand (`xinc1_r4_*.s1b`, 59 files) | 74 identical, 1 differs by design (+ 2 pattern (d) rewrites: 1 identical, 1 by design) | 182 open read: see "Round 4" |
| FTOL1 | 17 arg-less helper calls in 10 units (+1 dead copy) | all identical; whole units identical (1,535 functions) | the "~37" of J-0009 counted 20 declarations nobody calls |
| FTOL1 exact | the 17 sites now call the inline `fnvnx_ftol2_sse` (was the out-of-line int64 `fnvnx_ftol2`); 5 casts in 0x00a1b450 / 0x006912a0 (`ftol1_casts.s1b`) | whole units identical (11 units, 1,690 functions) | NaN / out of int32 -> 0x80000000 as on an SSE2 PC |
| RET1 | 11 functions | identical (whole units, 1,395 functions) | 6 trailing statements, 5 with the callee's return type restored |
| RET1 round 2 | 6 functions S12 found trapping (`ret1_fall1_r2.s1b`) | 4 identical, 2 differ by design | 3 made void, 1 callee return restored, 2 return the x86 eax |
| DBUG1 | 0 | - | the S4b items were merged by S1 (`FUN_00ec78f1_t` 12 bytes, J-0041) |

**XINC1 patterns.** (a) `jmp <callee>` forwarders declared `void f()`: they hand ecx and the caller's stack
arguments to a thiscall callee, so they are thiscall members with the callee's parameters and result (22 pairs).
(b) stdcall free functions that never write ecx before calling a thiscall callee: members passing their own this
(13). (c) members, constructors and (deleting) destructors calling a base/body function declared free while ecx is
still this (25). (d) result pointers: flameprojectile 0x009b36f0 and bgsautowater 0x0049e410 gave the hidden result
pointer and the pushed argument of one call to the neighbouring call (2). (e) computersmenu: three member functions
stored into a 4-byte handler table that HostF calls as thiscall pointers to members; on the target the table holds
function pointers taking this explicitly (G++ PMF conversion), MSVC keeps the decomp's spelling (`#ifdef _MSC_VER`; 3 pairs).
(f) navmesh / tesclimate called a cast function address; only the declaration changes (2). nimainsdm: the reference
was right, the definition (0x00a928b0, only statics) is made static.

**The 4 MSVC differences** are the exe's whole-program register allocation: after `call FinalizeEntries` /
`RemoveNode` / `ClearProjectileFlags` the original reuses ecx because that callee never writes it; a per-unit MSVC
must reload ecx (`mov ecx,esi`, 2 bytes): bssegmentedtrishape 0x00a87120 / 0x00a87130, nivbset 0x00e94490,
hkpprojectilegun 0x00e44f30. Not expressible in C++ without that knowledge; the patched calls are the semantics.

**Round 2 (S2c run f12, 2026-11-04).** f12 compiled many units the first round never saw: 295 x86-inconsistent
references (419 rows; the `#vtables` copy of an object folds into it), 295 calling functions in 174 files. For
each caller the pristine MSVC object gives the instruction at the reference and, by a must/may data flow of the
incoming ecx through moves and calls, whether ecx still holds the caller's own ecx there. Three shapes are mechanical and were
generated (`tests/s1b/xinc1/gen.py`; the shell names the callee by address as in round 1, the caller keeps its own
identifier so `msvc_check.py` follows the rename):

* (A) 89 free functions whose whole code is one `jmp callee` (5 bytes) with ecx untouched: thiscall shells with the
  definition's parameters in portable types (pointers and references `void*`, scalars kept) and its result; the
  decomp's own parameters of these forwarders are ignored (often wrong: `(int, int)` for one `NiStream*`).
* (B) 39 free functions (stdcall / cdecl) that never write ecx before the call or tail jump: shell members with the
  same parameters, calls on this - mostly the debug-string overrides that call the base version first, and the Havok
  shape forwarders that store a constant into their second stack slot and jump.
* (C) 125 members, constructors and destructors calling with ecx = this: `((S*)this)->FUN_<callee>(..)` with the
  reference's own parameter types, so the pushes stay the same.

11 more read by hand (`xinc1_r2_manual.s1b`): ecx holds another object (0x00d54f90 `&kOutput.m_vHitNormal`, also the
second argument; 0x00dcc130 the argument `value`; 0x00b7c190 the slot element), ecx survives a callee that never
writes it (0x00a712f0 after UpdateWorldFrustum, 0x00e385e0 after 0x00e376e0, 0x00a30c80 after IsNotInChain,
which also keeps edx = arg - the decomp's `__int64` high-half trick is garbage on the target and goes), a block-scope
`extern` that would hide the shell (0x00b86db0), a pointer the decomp typed `char` (0x00a51210), a spurious second
parameter of the callee (0x00a4d1d0: 0x00a57c10 jumps to IsCompatibleWith, `ret 4`), and hidden result buffers
(0x00cb2d70 returns the one-byte CD4AF0Bool of 0x00cd4af0; 0x00e3e8e0 returns the hkRefPtr by value).

MSVC: 259 identical, 5 differ by design - 0x00a712f0, 0x00e385e0, 0x00a30c80 reload ecx / keep arg in a saved
register (the whole-program allocation above; 0x00a712f0's shell keeps the callee's no-throw hint as
`__declspec(nothrow)`, or MSVC adds an EH frame), 0x00c315b0 and 0x00d9be50 allocate one register / schedule one push
differently after the member call (same values).

No patch for 26 callers: the callee never reads ecx (the three CreateShader of 0x00b55560: `push ecx` only reserves
the EH slot; 0x00bbbe30 and 0x00a75ec0, V1's false positives: ecx written first; Release 0x00a8f730 and
InitializePoint3 0x00e92e60), calls through a cast to a thiscall pointer (12: `((void(__thiscall*)(void*))f)(p)`
passes p in ecx on x86 and in x0 on the target, as the definition wants; S2 sees only the declaration), and
addresses taken, not called (9: vector constructor / destructor iterators - the native ones call the element
function with the object first -, stored callbacks, Havok's pair callback called as `callback(this, ...)`). V1's
register-use buckets miss ecx passed by `push ecx` as an argument (0x00e89ad0, 0x00a5d450), behind a jump table
(0x00e3dfb0) and through `jmp` forwarders declared free (0x00a43330, 0x00a50b10, 0x00a98eb0, 0x00a8a9a0): those are
real and patched. Open: 4 result-pointer callers (pattern (d): 0x005762b0, 0x009ab390, 0x008133f0, 0x00c90030) whose
pushes the decomp attributed to the wrong calls (0x005762b0 computes `*out = *vf_1f4() + (vf_1d8() + vf_1dc()) / c`
through NiPoint3 operator+ 0x00439e90 and operator/ 0x0053d280): they need a rewrite of the function. Not in abi.tsv:
the 76 C-named pairs V1 found unflagged (resolve does not compare C names yet, V1 defect 3).

**Round 4 (farm link/aedcf247e999).** Resolve now compares C-named references: 73 of V1's 74 C-named addresses are
flagged there (the 74th, 0x00c1ad80, is called by the forwarder 0x00c21900 through an `extern "C"
__declspec(noreturn)` declaration that resolve does not parse - patched by hand, the `noreturn` is wrong as well).
`gen.py` takes a C-named reference's signature from the unit's `extern "C"` declaration. Of the 182 open caller rows:
66 generated, 9 by hand (`xinc1_r4_manual.s1b`: DynamicCast on `enabledData` and GrowBuffer on the output buffer,
`extern "C"` / `noreturn` / declaration-split forwarders, the base constructor 0x00a5d3a0 on this instead of the
decomp's temporary), MSVC 74 identical and 0x00bb7f90 by design (`enabledData` read again for the member call). No patch:
43 rows call through a thiscall cast, 10 of them through a typedef'd pointer type (`reinterpret_cast<AlphaFlag>(f)`,
`((FUN_00653180_type)f)`), which resolve's cast reader does not see; 30 rows take addresses (iterators, callbacks).
0x006eac70 passes the hidden result buffer of a `__fastcall` struct return in ecx (consistent on x86; resolve's model
misses it). Open: 6 `cast-conflict` rows of the varargs 0x005b5e40 / 0x00a29680, and 8 callers still to read
(0x00c6d7e0, 0x00cc26c0, 0x00e3deb0, 0x00e8dd70, 0x00a4fb10, 0x00a60850, 0x00ccb160, 0x00ccb180).

**Pattern (d) rewrites** (`xinc1_r4_resultptr.s1b`). Counting the stack words each callee pops (`ret n`, hidden
result included) shows which pushes belong to which call. 0x005762b0 is `NiPoint3 f() { return *vf125() + (vf118()
+ vf119()) / c; }` (NiPoint3 operator+ 0x00439e90 and operator/ 0x0053d280 as members of the unit's Vec3f, slots 118
/ 119 returning the point by value, 125 its address): MSVC identical. 0x00c90030 is `source->m68 = (outer->m68 +
source->m34) + this->m2C` (ThreeByThreeMatrix::operator+ 0x0043f8d0 twice): differs by design, the copy reads the
sum through `lea` instead of operator+'s returned pointer (the same buffer). Still open: 16 result-pointer rows, among
them 0x008133f0 and 0x009ab390 - in both, 0x00461130 / 0x00524c40's caller is a getter of the transform at +0x68
(no stack argument, `ret`), and the pushed point and buffer belong to the transform-by-point call 0x004b4500 /
0x00524c40 after it; the word left over belongs to the enclosing call, whose arity has to be rebuilt too.

**FTOL1.** The decomp reaches the bytes by declaring the function that leaves the float in ST0 as void and calling
the helper with no operand. Sites name that function from the code and its definition: Tile::GetFloat (0x00a011b0,
6 sites), GetDistanceToReference (0x005723b0, 2), floor (0x00406cc0), FloatValueProvider::FUN_008be7a0, the two
Interface floats (tileimage 0x00a1fd50, whose constructor also takes 13 arguments, not 12), and the `_CIsqrt` of
blk_00e40000's dot product (0x00e54ad0 returns it, 0x00e54ae0 is a thiscall `length()`). Three sites also moved
pushed arguments to the call that pops them (inventorymenu 0x0077ffd0, sleepwaitmenu 0x007c0220): the same
argument-attribution pattern as XINC1 (d), which S2's `ref-passes-fewer` verdicts list elsewhere.

**FTOL1 semantics (S13 J-0087).** libcmt's `_ftol2_sse` (ftol2.obj of the SP1 CRT) tests `__sse2_available` and
then runs `cvttsd2si eax` on the operand stored as a double; only without SSE2 does it fall back to `_ftol2` (x87
`fistp` into 64 bits, eax the low half). The CRT sets `__sse2_available` at startup on any SSE2 machine, so the
game's int conversions give 0x80000000 for NaN and for anything outside int32 (truncated). AArch64 `fcvtzs` gives
0 for NaN and 0x7fffffff above the range (below it, 0x80000000 as on x86). `(int32)(int64)x` would model the x87
fallback, which no SSE2 PC runs (NaN -> 0, 3e9 -> 0xb2d05e00). `fnvnx_ftol2_sse(x)` (inline, defined per unit as
`static inline int (fnvnx_ftol2_sse)(double)` so the MSVC macro leaves the definition alone) is the SSE2 path:
`ok = (x > -2147483649.0) & (x < 2147483648.0); ok ? (int)x : INT_MIN` - fcmpe, fccmpe, cset, cbz, fcvtzs plus two
constants, no call. S13's x86 oracle takes the x87 path because its image leaves `___sse2_available` at 0: 0x00691260
(a coordinate below -2^31, ClampInt intercepted) gives 0x80000000 on an SSE2 PC and natively, so it is no divergence
once the oracle sets the flag; 0x00a1b450 (a height above 2^31) is 0x80000000 on the PC and 0x7fffffff natively
before this patch. Option taken: the narrow one - the arg-less helper sites and the casts S13 found. The pristine
objects hold 1,253 compiler-made `__ftol2_sse` calls (228 units, inline copies counted) and 10 `__ftol2` (8 units);
routing every int conversion (S12's F2S site list, 536 "runtime" sites) through this helper is S12's FTOI2 rule,
which should use this helper verbatim (and the unsigned ones the int64 truncation of FTOI1).

**RET1.** Of the 861 FALL1 sites on decomp code (after S1's two FALL1 fixes): 419 end only in jumps (unreachable
trap, fine), about 220 end in a switch / loop / if without else / try (the x86 return value is not evident from the
statement; left trapped), 177 have no `// @fnv` address (inline header copies), 75 are empty bodies. Candidates
(trailing expression statement on every fall-through path -> `return stmt;`) were generated automatically and kept
only where MSVC compiles them to identical code. Not done: bsfacegenmanager 0x00aaef90 (returns a local, not the
trailing call), blk_00ac0000 0x00ac5540 / 0x00acc9f0, blk_00440000 0x00458440 (callee result types need more than
the declaration change). HasValidTriangle 0x004ff7e0 and tesdatahandler 0x009938d0: round 2 below.

**RET1 round 2** (S12's reachable FALL1 traps, `ret1_fall1_r2.s1b`). FALL1 runs before S1b, so each fixed path ends
in a return in front of its trap. Read from the MSVC objects, eax at the fall-off:

| Function | x86 at the fall-off | Fix | MSVC |
|---|---|---|---|
| 0x004ff7e0 `HasValidTriangle` | stores the base vptr; eax = this only as the store's address. All 95 references are destructors (`??1..@@QAE@XZ`); HasValidTriangle proper is 0x006dd6a0 | void (declaration in pathinglocation_decls.h too), `return;` for the `__assume(0)` | identical |
| 0x009938d0 `AddAtFirstFreeSlot` | eax = result of 0x00470000 (NiTArray::SetAtGrow, returns the index) | callee `unsigned int`, `return SetAt(..)` | identical |
| 0x00c25f40 `PopRecord` | unrelated values on the two late paths (a call's eax, last * 32); `return p` is the decrement's address register; no unit names the address | void | identical |
| 0x00cf6f10 (border-listener pair callback) | al = a node type over the entry eax, or a v24 result; the last path tail-jumps to v24 | void (tail jump kept) | identical |
| 0x00af28f0 | eax = 0 (the null SteamFriends / SteamUtils just tested) | `return 0;` | differs by design: /Od adds `xor eax,eax` |
| 0x00bb62b0 | default pass: eax = a - 0x237 (switch arithmetic), returned as the reference | `return *(T*)(a - 0x237)` (the same value) | differs by design: `lea eax,[edx-0x237]` |

The other 5 functions of S12's list have no FALL1 trap: InitWinsock 0x00da2ff0, 0x00dd1f30 and 0x00e66290 keep a
`brk` only after `bl __stack_chk_fail` (noreturn, unreachable). ActorValue::RegisterActorValue 0x0066efe0 traps
whenever `count > 0`: GCC replaces `va_arg(args, Index)` (an enum, promoted through `...`) by a trap - for S1 (VA:
`(Index)va_arg(args, int)`). QuadRenderer::UploadQuads 0x00e723a0 has a reachable `brk` (`cbnz w1` falls into it)
of another origin - for S12 / S1.

## CRITICAL_SECTION on PC (the CS28 question)

The x86 code decides it for the classes whose code touches the members after the lock: the PC image has a plain
24-byte `CRITICAL_SECTION` with **no** 4 bytes after it.

* BSThread: ctor 0x00aa6360 calls InitializeCriticalSection(this+4) and zeroes this+0x1c..0x28 and the byte at
  +0x2c; Initialize 0x00aa6430 reads the thread handle at +0x1c, stores GetCurrentThread at +0x20,
  &createdThreadId = this+0x24, GetCurrentThreadId at +0x28, started flag at +0x2c; StopAndCloseHandle 0x00aa64f0
  uses a 24-byte CS at +4 and the handle at +0x1c. BSThread is 0x30 bytes; aitaskmanager's derived thread
  (0x008c9bd0) has its first member at +0x30.
* AbstractHeap: GetHeapStats 0x00aaa0e0 stores the struct size 0x20f0; the lock is at +0x20d8, so it is 0x18 bytes,
  the last member. Every lock use in abstractheap.cpp passes only this+0x20d8 to Enter/LeaveCriticalSection.
* RecursiveLock::Leave as called by ActorTaskManager (0x0082f1f0) is LeaveCriticalSection(this) and nothing else.
* NiCriticalSection (NiGlobalStringTable +0x2080): AddString 0x00a5b690 only enters/leaves it; no PC code read here
  touches owner / lock count, so the code cannot place them.

So the decomp's 28-byte `_RTL_CRITICAL_SECTION` and the layouts asserted with it (RecursiveLock 0x28, BSThread 0x40,
IO_Thread 0x40, AbstractHeap 0x2100, NiCriticalSection owner at 0x1C in 128 bytes) are the Xbox 360 types; on PC
BSThread is 0x30, AbstractHeap 0x20f0 (fields before the lock agree), and the lock members are CRITICAL_SECTION.
CS28's explicit 4-byte tail keeps the Xbox offsets: harmless for code that never touches those members, wrong for
object sizes (an image-resident AbstractHeap constructed in place would write 16 bytes past its PC size) and for any
PC member after a BSThread base. Recommendation for S1: replace the CS28 model by the PC layouts above (and the PC
NiCriticalSection of Gamebryo, CS + owner + lock count = 32 bytes, until code shows otherwise).

## Stand-in vftables whose slots need the player's exe (S1)

S1's VTBL1 (`tools/rules/s1_compile.py`) gives a stand-in polymorphic class its full interface in MSVC slot order
and forwards each slot to the decomp function it is; a slot the decomp has no function for is declared only and
stays unresolved (S2's trap) until the exe's vftable gives the address. No slot is bound on a guess.

| vftable | class | slots without a function in the decomp | evidence for the bound slots |
|---|---|---|---|
| `0x010a2528` | BSNiAllocator (ctor 0x00aa2230) | 7 (InitHolder's no-argument call), 8, 9 | bsniallocator.cpp 0x00aa2240..0x00aa24a0, called through slots 1-6 with these signatures in nimemorydefines.cpp |
| `0x010a24fc` | NiStandardAllocator (FUN_00aa2200) | 0 (scalar deleting destructor), 9 | nistandardallocator.cpp (360 source path of 0x00aa3090..0x00aa3560): 1-6 in slot order with BSNiAllocator's signatures, 3 calls 1 and 2 itself; 7 = 0x00aa3430 creates the small-object allocator (Initialize), 8 = 0x00aa3460 deletes it (Shutdown) |
| `0x0101772c`, `0x0104e6ac`, `0x01082ed8`, FUN_004de530's | INISettingCollection-like setting collections | all ten (no traps added; S5) | none yet: the decomp's interface is the 10-slot INISettingCollection of `src/bsmain/setting_declarations.h`: 0 scalar deleting destructor, 1 Add, 2 Remove, 3 GetViewerStrings, 4 WriteSetting, 5 ReadSetting, 6 Open, 7 Close, 8 WriteAllSettings, 9 ReadAllSettings |
