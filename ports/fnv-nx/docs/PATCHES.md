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
| `gcc_check.py DECOMP BASE_TREE WORK` | patched files prepared with all rules into an overlay of a prepared tree, compiled with the census flags before/after; reports new errors and errors on patched lines |

`msvc/s1b_check_fi.h` gives `fnvnx_ftol2(x)` its MSVC meaning for the check only: the compiler's own
float -> `__int64` conversion, i.e. a call of `_ftol2_sse` with x in ST0.

## Results (decomp `87eb9373`)

| Rule | Sites | MSVC check | Notes |
|---|---:|---|---|
| XINC1 | all 68 x86-inconsistent pairs of S2's latest run (a 69th of an earlier run is bridged by S2 now): 66 functions, 49 files | 62 identical, 4 differ by design | see below |
| FTOL1 | 17 arg-less helper calls in 10 units (+1 dead copy) | all identical; whole units identical (1,535 functions) | the "~37" of J-0009 counted 20 declarations nobody calls |
| RET1 | 11 functions | identical (whole units, 1,395 functions) | 6 trailing statements, 5 with the callee's return type restored |
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

**FTOL1.** The decomp reaches the bytes by declaring the function that leaves the float in ST0 as void and calling
the helper with no operand. Sites name that function from the code and its definition: Tile::GetFloat (0x00a011b0,
6 sites), GetDistanceToReference (0x005723b0, 2), floor (0x00406cc0), FloatValueProvider::FUN_008be7a0, the two
Interface floats (tileimage 0x00a1fd50, whose constructor also takes 13 arguments, not 12), and the `_CIsqrt` of
blk_00e40000's dot product (0x00e54ad0 returns it, 0x00e54ae0 is a thiscall `length()`). Three sites also moved
pushed arguments to the call that pops them (inventorymenu 0x0077ffd0, sleepwaitmenu 0x007c0220): the same
argument-attribution pattern as XINC1 (d), which S2's `ref-passes-fewer` verdicts list elsewhere.

**RET1.** Of the 861 FALL1 sites on decomp code (after S1's two FALL1 fixes): 419 end only in jumps (unreachable
trap, fine), about 220 end in a switch / loop / if without else / try (the x86 return value is not evident from the
statement; left trapped), 177 have no `// @fnv` address (inline header copies), 75 are empty bodies. Candidates
(trailing expression statement on every fall-through path -> `return stmt;`) were generated automatically and kept
only where MSVC compiles them to identical code. Not done: `HasValidTriangle` 0x004ff7e0 (S13's example) ends with
`__assume(0)` while its x86 code returns eax = this after storing a vtable pointer (a destructor-like body under a
bool name; needs a look at its callers), bsfacegenmanager 0x00aaef90 (returns a local, not the trailing call),
blk_00ac0000 0x00ac5540 / 0x00acc9f0, tesdatahandler 0x009938d0, blk_00440000 0x00458440 (callee result types
need more than the declaration change).

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
