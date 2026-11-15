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
| `pop/popscan.py ABI OUT` | every `pop-mismatch` pair of a link run read against the pristine objects: per call site the callee's real words (`ret N`), the words its code reads, and for a callee that takes more than its declaration pushes, the earlier call of the same caller that the missing words were pushed for (stack simulated with every direct callee's real pop count); families `more`, `conv-only`, `fewer-unread`, `fewer-donor`, `fewer-other`, `nocaller` |
| `pop/popgen.py POP ABI OUTDIR [--exclude SRC,..]` | candidate patches for the `fewer-donor` chains `D(.., x).R(..)` -> `D(..).R(.., x)` with overloads declared next to the decomp's; everything not mechanical goes to `OUTDIR/manual.tsv` with the reason |
| `pop/popdis.py ADDR.. [--pop POP]` | a caller's x86 code from the pristine objects with every relocation resolved, each call's real pop count and the stack words the callee reads, and popscan's rows for it: what a hand-written pop patch is read from |
| `pop/fuzztriage.py HANDOFFS ABI POP OUT` | S1b's rows of S13b's fuzz hand-off list (`uninit-arm`, `native-garbage-arg`, `callee-saved`) classified by the link's ABI rows and popscan: patched, pop-caller, pop-callee, bridge-callee / bridge-self (S2c's bridge gaps), xinc-callee, other |
| `pop/abiclass.py ABI POP OUT` | S1b verdict for every unexplained x86-inconsistent and manual pair of a link run, with the call distance of the calling function from the boot roots (WinMain 0x0086a850 and the dynamic initializers) |
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
| XINC1 pop-mismatch | farm link/31971038913d: 2,136 pop-mismatch pairs, 2,939 call sites; 967 `fewer-donor` calls. Pilot `xinc1_pop_changeflags.s1b` (2 sites); 173 callers generated (`xinc1_pop_gen.s1b`: 292 calls, 170 sites in 97 files); 3 boot-path callers of WinMain's loop by hand (`xinc1_pop_boot.s1b`) | all identical (191 site functions of the 97 files; main.cpp 3); GCC census 0 -> 0 errors in all 98 files | see "Pop-mismatch families" |
| FTOL1 | 17 arg-less helper calls in 10 units (+1 dead copy) | all identical; whole units identical (1,535 functions) | the "~37" of J-0009 counted 20 declarations nobody calls |
| FTOL1 exact | the 17 sites now call the inline `fnvnx_ftol2_sse` (was the out-of-line int64 `fnvnx_ftol2`); 5 casts in 0x00a1b450 / 0x006912a0 (`ftol1_casts.s1b`) | whole units identical (11 units, 1,690 functions) | NaN / out of int32 -> 0x80000000 as on an SSE2 PC |
| RET1 | 11 functions | identical (whole units, 1,395 functions) | 6 trailing statements, 5 with the callee's return type restored |
| RET1 round 2 | 6 functions S12 found trapping (`ret1_fall1_r2.s1b`) | 4 identical, 2 differ by design | 3 made void, 1 callee return restored, 2 return the x86 eax |
| DBUG1 | 1 (`dbug1_crt.s1b`) | identical (whole unit, 64 functions) | the S4b items were merged by S1 (`FUN_00ec78f1_t` 12 bytes, J-0041); S1-crt: 0x00b01510 calls FindFirstFileA with the find data the x86 code left on the stack (one-parameter decomp declaration of the two-parameter import) |
| DBUG1 round 2 | 1 (`dbug1_w6_paramcount.s1b`; EQ fuzz row param-count 0x00d32b40) | MSVC byte check not run where written (no Visual C++ in that container): to run on a farm | the hkConvexList agent table callback 0x00d32b40 reads five stack words (unused, object, two floats, an integer); the decomp declared three ints and reached the rest through the address of the second parameter. Now five parameters, as the neighbouring table entry 0x00d34d80 is declared; a `def` and a `ref` row in `units_renames.tsv` carry the new decoration `?FUN_00d32b40@@YAXHPAXMMH@Z` |

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
With FTOI2 global (S12's plugin in compile.sh, ca2dcce, J-0110: every int / short / signed char conversion as
`__ftol2_sse` = cvttsd2si) the helper and the 5 casts of `ftol1_casts.s1b` are redundant and harmless: test_ftol.py
passes with the plugin loaded (same 48 values, still inline, 15 instructions). The operand restoration of the 17
arg-less sites stays needed: the plugin sees no conversion where the decomp calls the helper without its operand.

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

## Pop-mismatch families

A `pop-mismatch` pair (S2's resolve) is a reference whose declaration pushes a different number of stack words than
the definition pops. On x86 the pushes are the same either way, so the code ran; natively a callee that takes more
words than its call passes reads argument registers nobody set (S13b's `native-garbage-arg`). The byte-exact MSVC
code of both sides says which call pops which words (`popscan.py`).

Families at link/31971038913d (2,136 pairs, 2,939 call sites):

| family | calls | native effect |
|---|---:|---|
| `more`: the call passes more words than the callee takes | 1,386 | none (extra arguments); usually the donor of a `fewer-donor` call |
| `fewer-donor`: the missing words were pushed for an earlier call of the same caller | 967 | garbage arguments: the decomp gave the pushes to the wrong call |
| `fewer-other`: the missing words come from elsewhere (caller's own stack arguments, pushes no declaration covers, an indirect call) | 456 | garbage arguments; read by hand |
| `fewer-unread`: the callee never reads the missing words | 70 | none |
| `nocaller`: no call in the pristine object (address taken, declaration only) | 50 | none at a call |
| `conv-only`: same words, only who pops differs | 10 | none (the link's moves) |

Most `fewer-donor` calls are chains: `D(a.., x..)->R(r..)` where D is a getter that pops only its own arguments and
R pops the words x. The patch moves them, `D(a..)->R(r.., x..)`, and declares the two overloads next to the decomp's
(same class, result and qualifiers; the moved parameters keep the donor's spelling, so MSVC pushes the same words in
the same order). `popgen.py` generates a caller only when every chain of the pair in it is a flagged site, all argument
counts match their declarations, no declaration is virtual, static, inline-defined or defaulted, no other overload of
the same arity exists, and the moved arguments have no side effects (x86 evaluates them before D, GCC's C++17 order
evaluates D first). A moved `T*` whose class is declared after the receiver's class is spelled `struct T*` (it
declares T at namespace scope, as the later definition does); a one-line class body gets the overload inside the
braces.

- `xinc1_pop_gen.s1b`: 173 callers, 292 calls, 97 files. MSVC: all 191 site functions identical; GCC census: no
  new errors.
- Nested spelling `R(.., D(a.., x..), ..)` -> `R(.., D(a..), x.., ..)`: D's call is a whole argument of R, and the
  words pushed for D follow D's result on R's stack (the boot callers 0x00448420 / 0x0086c010 by hand had this shape).
  Regenerated `xinc1_pop_gen.s1b` (`popgen.py --exclude <the three> --skip-patched patches`): caller sites 169 -> 222,
  files 97 -> 123 (39 files touched). MSVC: all 4,990 functions of the 39 units identical; GCC census 0 -> 0;
  units_renames +116. abiclass at link/31971038913d: fix-donor 471 -> 408 pairs, fix-other 352 -> 348, patched 233 ->
  301. A moved parameter named like one of the receiver's keeps its type only; a moved class-template parameter
  declared after the receiver's class is left manual.
  `flameprojectile.cpp`, `highprocess.cpp` and `tesrace.cpp` are left out: their hunks overlap other patches.
- `xinc1_pop_boot.s1b` (by hand): WinMain's per-frame body 0x0086ff70 (distance 2 from WinMain; four getter/receiver
  pairs and the nested `0x00874b90(0x004e3270(core, 0x004dc310(), 0))`, which is
  `0x00874b90(0x004e3270(), core, 0x004dc310(), 0)`), 0x00877430 and 0x00871a50. MSVC identical.
- Not generated (popgen's `manual.tsv`, 420 callers): the chain is not spelled as one (210: the donor's result is
  stored first, or it is nested as an argument); the declarations live in a `_decls.h` header or more than one class
  body has them (76, among them the `GetChangeFlags` family of the pilot in 27 files); free functions (38); the donor's
  surplus differs from the receiver's deficit (30: one call shares its words with two others); side effects in moved
  arguments (19); conflicting hunks (26); paths with blanks (10).
- POP2 (S2-bodies, tools/rules/s2_bodies.py; a prepare POST hook, no patch text): the chain fix as a rule, decided
  from units.json's decorated names (the reference's words against the definition's) instead of popscan, with the
  declarations found by MSVC's scopes in the unit or a local header (the `GetChangeFlags` family and the per-function
  namespaces popgen left manual), and the S1b patches already applied when it runs. It also takes the explicit
  `.operator bool()` spelling and literal type / name back-references in the moved codes. THIS2 is XINC1's shapes
  A-C for the callers listed in `THIS2_SITES` (exe evidence: ecx untouched up to the call).

S13b's `native-garbage-arg` hand-offs (131 functions) are mostly these: 30 are sites of these patches, 52 are
`fewer-donor` / `fewer-other` callers still open, 4 call only `more` / `fewer-unread` callees, and 45 have no
pop-mismatch row (indirect callees, or callers in units the link did not compare). Of the 175 `uninit-arm`
hand-offs, 158 have no pop-mismatch row.

## ABI verdicts (`pop/abiclass.py`)

`tools/gates/ready.py` counts an x86-inconsistent pair as explained only when its classes are all `pop-mismatch`.
At link/31971038913d 1,875 rows are not (1,262 pairs once a unit's `#vtables` copy folds into it); 1,529 of the rows
are `pop-mismatch,ref-passes-fewer`, the same calls as popscan's `fewer` families. Per pair, by its worst call:

| verdict | pairs | meaning |
|---|---:|---|
| `fix-donor` | 481 | `fewer-donor` calls not generated (see above) |
| `fix-other` | 352 | `fewer-other` calls |
| `patched` | 222 | the caller is an S1b site; the next link drops the row |
| `ecx` | 179 | this / ecx / result-pointer classes: XINC1 rounds 1-4 (calls through thiscall casts and addresses taken are by design there) |
| `benign-unread` / `-more` / `-nocall` | 37 / 34 / 30 | no native effect |
| `bridge-gap` (manual rows) | 2,221 | varargs, aggregates by value, struct results: shapes the link does not bridge (S2c) |

By boot distance (direct calls and the tables a function refers to, from WinMain and the 6,266 dynamic initializers;
29,034 functions reached): of the open callers, `fix-donor` 53 and `fix-other` 17 are within 4 calls, and the three
main.cpp callers among them are patched in `xinc1_pop_boot.s1b`. The two at distance 1 are patched there too:
0x00448420 (three `D(item.FUN_00559450(0))` calls: 0x00559450 is a plain-`ret` getter, the 0 is 0x0044ac40's second
word, `ret 8`) and 0x0086c010 (`push value; push 0xc` before the image-space manager getter 0x004e3270: 0xc is
0x004ebbc0's index, value is 0x005b9f80's float, each `ret 4`); MSVC identical (both units, 417 functions).

## By-design MSVC differences (S11 gate)

Every S1b site whose MSVC code is not identical to the pristine build, and why. Each one keeps the x86 semantics;
the bytes differ because a per-unit MSVC cannot reproduce the exe's whole-program register allocation, or because
the patch gives a value explicitly that the exe left in a register. S11's gate (`tools/equiv`, J-0113) can mark
these rows as expected; any other S1b difference is a regression.

| Rule | Source | Function | Difference |
|---|---|---|---|
| XINC1 | `src/gamebryo2.2/corelibs/nimain/bssegmentedtrishape.cpp` | 0x00a87120 | `mov ecx,esi` before the second call (FinalizeEntries keeps ecx in the exe) |
| XINC1 | `src/gamebryo2.2/corelibs/nimain/bssegmentedtrishape.cpp` | 0x00a87130 | as 0x00a87120 |
| XINC1 | `src/gamebryo2.2/corelibs/niconsolerenderer/nivbset.cpp` | 0x00e94490 | ecx reload after RemoveNode |
| XINC1 | `src/havok/physics/utilities/weapons/hkpprojectilegun.cpp` | 0x00e44f30 | ecx reload after ClearProjectileFlags |
| XINC1 | `src/gamebryo2.2/corelibs/nianimation/nicontrollersequence.cpp` | 0x00a30c80 | this and arg kept in saved registers across IsNotInChain (the exe keeps them in ecx / edx) |
| XINC1 | `src/gamebryo2.2/corelibs/nimain/nicamera.cpp` | 0x00a712f0 | `mov ecx,esi` before UpdateWorldBound (UpdateWorldFrustum keeps ecx) |
| XINC1 | `src/havok/physics/vehicle/hkpvehicleinstance.cpp` | 0x00e385e0 | `mov ecx,esi` before 0x00e38080 (0x00e376e0 keeps ecx) |
| XINC1 | `src/gamebryo2.2/corelibs/niparticle/nipsysmodifierctlr.cpp` | 0x00c315b0 | one register choice after the member call (`ebx` for `eax`), same value |
| XINC1 | `src/havok/common/internal/collide/tree/aabbtree/hkaabbtree.cpp` | 0x00d9be50 | one `push` scheduled later, same values |
| XINC1 | `src/staging/blk_00ba0000_o2.cpp` | 0x00bb7f90 | `enabledData` read again (3 bytes) for the DynamicCast member call |
| XINC1 | `src/fallout/ai/actor.cpp` | 0x0089d900 | `xinc1_pop_gen.s1b`: the KillSoundHandle89D9* argument moves from KillView::FUN_005d43c0 (exe: `ret`, pops nothing) to the ExtraList calls on its result (0x00418a00, 0x004189c0, 0x0041a540, 0x0041a3e0: `ret 4`, verified in the exe), as the exe's stack use has it; the decorations of those calls change and the push belongs to the call that pops it (EQ, checked against FalloutNV.exe) |
| XINC1 | `src/staging/blk_00c80000_o2.cpp` | 0x00c90030 | pattern (d): the copy reads the sum through `lea` instead of operator+'s returned pointer (the same buffer) |
| RET1 | `src/bssystemutilities/console/bsdlcsystemutility_console.cpp` | 0x00af28f0 | `xor eax,eax` for the 0 that was already in eax |
| RET1 | `src/bsshader/bsdistanttreeshader.cpp` | 0x00bb62b0 | `lea eax,[edx-0x237]` for the `a - 0x237` the switch arithmetic left in eax |

FTOL1 has none: with `fnvnx_ftol2_sse(x)` = `(int)(x)` under MSVC (`msvc/s1b_check_fi.h`, `tools/equiv/msvc/fnvnx_equiv_fi.h`)
the patched calls are `_ftol2_sse` calls as in the exe; the gate's 9 "other helper" FTOL1 functions came from the
earlier `fnvnx_ftol2(x)` = `(__int64)(x)`, which calls `_ftol2`.

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
| `<va>` | BSNiAllocator (ctor <va>) | 7 (InitHolder's no-argument call), 8, 9 | bsniallocator.cpp <va>..<va>, called through slots 1-6 with these signatures in nimemorydefines.cpp |
| `0x010a24fc` | NiStandardAllocator (FUN_00aa2200) | 0 (scalar deleting destructor), 9 | nistandardallocator.cpp (360 source path of 0x00aa3090..0x00aa3560): 1-6 in slot order with BSNiAllocator's signatures, 3 calls 1 and 2 itself; 7 = 0x00aa3430 creates the small-object allocator (Initialize), 8 = 0x00aa3460 deletes it (Shutdown) |
| `0x0109bf4c` | A660A0Map, the NiStream loader map DAT_011f4784 (FUN_00a660a0) | 1-6: KeyToHashIndex, IsKeysEqual, SetValue, ClearValue, NewItem, DeleteItem (Gamebryo's NiTMapBase order; SetAt 0x00a642a0 calls 1-3) | slot 0 only, the destructor the decomp declares; no unit binds 0x0109bf4c with more slots, and the decomp's string-map slot functions belong to other vftables |
| `0x0101772c`, `0x0104e6ac`, `0x01082ed8`, FUN_004de530's | INISettingCollection-like setting collections | all ten (no traps added; S5) | none yet: the decomp's interface is the 10-slot INISettingCollection of `src/bsmain/setting_declarations.h`: 0 scalar deleting destructor, 1 Add, 2 Remove, 3 GetViewerStrings, 4 WriteSetting, 5 ReadSetting, 6 Open, 7 Close, 8 WriteAllSettings, 9 ReadAllSettings |

## Patch class MT1: gated Switch behaviour changes (mt_cull; decided by the orchestrator, S15-threads)

**Purpose.** Every other class here restores what MSVC built from the decomp (byte parity). MT1 changes behaviour on
purpose. It makes the engine's 360-era multithreaded culling path (`bMTRendering:Display`, docs/PERF.md 6.8) work
on the PC code, so cull and accumulation can leave the main thread. The changes:
* each accumulator built with two batch renderers while MT is on (FUN_0086c160, the shadow-accumulator pool in
  bsshadermanager, teswater x2);
* per-thread current-accumulator globals (`DAT_011f95ec`, `DAT_011f4748+8`);
* BSFadeNode registration through the culler's accumulator instead of the global.

**Gate.**
* Switch build only, and every MT1 hunk is behind `FNVNX_MT1_ON` (S1b's tooling, below).
* `fnvnx_mt1_on()` (toolchain/game/boot.cpp) reads bMTRendering from the INI setting object (0x011c710c, the value at
  +4), not from the engine's copy `DAT_011f94b4`. WinMain builds the accumulators (FUN_0086c160) before the renderer
  init copies the setting, and the gate has to give the same answer at both.
* Off by default: ini.cpp's Switch defaults do not set bMTRendering until the checks below pass.
* With `bMTRendering=0` the output must be byte-identical to the build without MT1. The fuzz / equivalence farms keep
  running with it off.

**Checks, in place of the MSVC byte check** (MT1 hunks are exempt from `msvc_check.py`; S1b adds the
"exempt, gated" class to the .s1b tooling):
1. GCC census clean on every patched unit.
2. tests/s15 mtqemu on the real culling and accumulator objects (not the `stages` protocol model).
3. The hb.c race checker: no race on the accumulators, batch renderers or the current-accumulator globals.
4. Frame equivalence: the same scene with MT1 on and with it off gives the same accumulator contents and the same
   draw list.

**Owners.** S1b owns patches/ and the tooling; S15-threads writes the MT1 patches and their tests.

## Gated classes in the .s1b tooling (MT1)

The tooling side of S15-threads' class MT1 (deliberate Switch behaviour changes for multithreaded culling; the class
itself, its gate and its checks are described by S15-threads). `tools/rules/s1b_patches.py`:

* `MSVC_RULES` (XINC1, FTOL1, DBUG1, RET1) are the byte-parity classes; `GATED` (MT1) are exempt from the MSVC byte
  check. `msvc_sites()` is what `msvc_check.py` checks, so MT1 sites are never compiled against the pristine
  objects. `tools/equiv/equiv.py` classifies MT1 as `platform`: no neutral MSVC build applies it. prepare (the Switch
  build) runs `r_mt1` after the other S1b rules. `gcc_check.py` and `test_patches.py` cover MT1 like any class.
* Files: `patches/mt1_*.s1b`, sites `@@ MT1 <path> <0xaddr|file>`, the same format and evidence rule as the others.
* Every MT1 hunk is behind `FNVNX_MT1_ON`. `shim/include/fnvnx_compat.h`, which the Switch build force-includes,
  declares `fnvnx_mt1_on()` weak and defines the macro as `fnvnx_mt1_on != 0 && fnvnx_mt1_on() != 0`. While nothing
  defines `fnvnx_mt1_on()`, the gate is off. S15 defines it in the platform layer to read the engine's copy of
  bMTRendering (`DAT_011f94b4`).
* Hunk modes (the `===` line), MT1 only:
  | mode | emitted | check |
  |---|---|---|
  | `===` (statements) | `if (FNVNX_MT1_ON) { new } else { <matched old> }`: with the gate off, the decomp's code | the old text is whole statements |
  | `=== guarded` | the new text as written | it must test `FNVNX_MT1_ON` itself |
  | `=== inert` | the new text as written | it only adds text: the old text occurs in it verbatim (declarations the gated code uses) |
  A mode on a byte-parity class, or an unknown mode, is a parse error.
* Test: `tests/s1b/test_mt1.py`. It covers parsing and emission, the refusals, the MSVC exemption, the equiv class,
  and prepare's rule list. It also compiles and runs emitted code with the gate block: the decomp's path while
  `fnvnx_mt1_on()` is undefined, the MT1 path once it returns 1.
