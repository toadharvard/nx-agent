# l3b-w2: lift/rt as a guest product root (one build path)

Branch `claude/fnv-l3b-lift-w2`. Built only by `verify/recipe.py`, in GATE_CACHE-style stores, msvcmodel compiler (pin of `verify/guest_compiler.json`,
commit b4057704, tree b60eafe7...), all 8 plugins (the native set of recipe.py: ftoi order sdd pi dispatch addr names layout, PLUGIN_ARGS).

## Result
- `lift/rt/build.json` (fnvnx-recipe/1), `verify/admit.json`: `{"path": "ports/fnv-nx/lift/rt", "class": "product", "target": "guest", "cxx_origin": "native", "recipe": ...}`.
- `recipe.py check` valid. Cold build into S1 and into fresh S2: green, 11 objects, stages run 30 hit 0 each. Object sha256 (and plugin .so sha256) equal
  bit for bit; objects.json differs only in the absolute `object` paths of the two --out dirs. Warm rebuild into S1: run=0 hit=30.
- Objects (sha12): cpumodel.c 0d9f8cdb39d1, enter.cc 7aadb73f0ebf, fmath.c b96c217bc9e9, msvcrt_ci.cc 0c7c8cba9ff6, msvcrt_eh.cc 5190a9cacfa2,
  msvcrt_fp.cc 0dfd05c2f92e, rcptab.c 2c25cec63bd6, rt.c 327f370d2af3, seh.cc de5162f356de, unhandled.cc 52d1ee1504d7, x87.c 1b0d89735b3c.
- Tests (python3 -I): `lift/proof/tests/test_rtroot.py` (flags of build.json vs liftgen), `verify/tests/test_plugins.py` (two new cases).

## Flags and their one source
Single definition: `lift/tools/liftgen.py` `pack_flags(instest.FLAGS)` (new function; `recipe()` now calls it, same output as before) = test/instest.py FLAGS
minus `-c` and `-DFNVL_FLAGS_IO`. The two sources (liftgen / instest) agree (liftgen derives from instest). The root differs from the pack list in three
places the machinery forces (listed here, the test asserts exactly these):
1. cxx: `-w` removed (fnvnx_names NAMES1: "'-w' hides the class-key reports"); `-DFNVNX_SDD=1 -fno-devirtualize` appended (recipe.py SEMANTICS_FLAGS, required of native C++, the pack list lacks them).
2. c: pack c = `-mabi=ilp32` (no -O); rt's C units are freestanding AAPCS code with the build lines of rt.c / instest.py, so
   c = `-mabi=ilp32 -O2 -fPIC -ffreestanding -fno-stack-protector -fno-asynchronous-unwind-tables -fno-builtin`. (The lifted -ffixed-x19..x27 are only in cxx; rt.c is documented to be compiled without them.)
3. asm = pack asm `-mabi=ilp32 -fPIC` (no .S/.s units in rt today).
cxx list = `-mabi=ilp32 -std=gnu++17 -O2 -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -fcheck-new
-fno-sized-deallocation -fno-lifetime-dse -ffp-contract=off -fPIC -fpermissive -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-stack-protector -fno-stack-clash-protection
-mno-outline-atomics -U_FORTIFY_SOURCE -ffreestanding -fno-exceptions -fno-asynchronous-unwind-tables -fno-math-errno -ffixed-x19..-ffixed-x27 -DFNVNX_SDD=1 -fno-devirtualize`.
Note for the lead: the lifted PACK root (lift/pack, no plugins) keeps `-w` and lacks the SEMANTICS_FLAGS; fine for a lifted root, but the rt's cxx flags are no longer
the pack's byte for byte.

## Renames (git mv .c -> .cc, a unit's language is its extension)
rt/seh.c, enter.c, unhandled.c, msvcrt_ci.c, msvcrt_eh.c, msvcrt_fp.c -> .cc. References fixed by a rule (name replaced everywhere under lift/):
lift/ASSUMPTIONS.l3b.json, README.md, bench/ehtest.py, bench/harness.S, fnvlift/f_sys.py, fnvlift/fpfacts.py, rt/*.{cc,c,h} comments, test/citest.py imptest.py instest.py
msvcrttest.py sehcodes.py (the py tests build these paths: they now pass the .cc names), tools/fpstate.py. Unchanged .c units (plain C): rt.c x87.c fmath.c rcptab.c cpumodel.c.
Other test harnesses (instest, imptest, ...) still compile rt files by their own commands: that is the old path, to be moved onto this root (not done here).

## Machinery extensions (each by a rule, with a test in verify/tests/test_plugins.py)
- fnvnx_order: a noreturn call (`__builtin_longjmp`, enter.cc) gets no acquire fence after it (error "cannot fence after a block-ending statement": the call has no fall-through edge, nothing runs after it). Test: `lj` compiles.
- fnvnx_order: a global register variable (`register u32 eax asm("x19")`, `register cpu *c asm("x27")` of fnvl.h) is a machine register, not memory (is_memory_ref: !DECL_HARD_REGISTER). Before: ICE in expand_expr_addr_expr_1 (the plugin took its address for an atomic store). Test: `hrw` has no ldar/stlr/dmb.
## Source fixes (they did not compile against fnvl_mem even with stock g++-13 and the same flags)
- rt/msvcrt_eh.cc:301 `M32(rec+4) &= ~EH_UNWINDING` and rt/seh.cc:161 `M32(rec+4) |= ...` -> `M32(x) = M32(x) op ...` (the proxy has no compound operators). Whether the old msvcrt/sehcodes tests ever built these lines is unknown to me; they cannot at this head.
- Warnings still printed (no -w), not errors: `fnvnx_f2i attribute directive ignored` (fnvl.h:99, fnvl_x87.h:77: attribute for a plugin not in the compiler's set `fnvnx_f2i`; the plugin there is `fnvnx_ftoi`, so the attribute name is stale: lead/L3a to decide), narrowing at msvcrt_eh.cc:364.

## Verdict on this branch (l1.py verdict, commit 8245352, 194 s, 23 gates), what concerns the root
green: build 11/11, determinism 11/11, objstub 149/149, oracle 49/49, argordergate 6/6.
red:
- linkstrict 132/148: 16 undefined or unmatched symbols of the root (it is not linked: no ELF; names it expects to be defined elsewhere): fnvl_host_ticks_ns, fnvl_host_tls_slot, fnvnx_dispatch, fnvnx_win32_lookup_export,
  fnvnx_win32_set_raise_backend, ExitProcess, UnhandledExceptionFilter (platform / host side), fnvl_disp_table, fnvl_g, fnvl_phf_params (the dispatcher table of the pack's disp.S).
  Expected: they are defined by the platform and pack roots; needs the link step of the ELF.
- n1 n2_data n2_call n2_vcall xc xunit rtti pcrel bodysel abs disp reach reachlink gpa: all "0/1, uncertified: ports/fnv-nx/lift/rt:inputs": the root has no gate inputs (decomp/exe facts: game dir, contract inputs). The root is lift runtime, not a decomp tree; these product gates have no input for it. I did not invent one.
- admit 0/562: lift/ has no admitted tool root for its other 560 files (only lift/rt is a root). The lead must decide the admit entries for lift/ (class tool, gates pure+selftests).
- pure 95/122: 27 lift files uncertified (bench/*, proof/*, test/*, tools/liftgen.py ...: they are not pure by puregate; they were not in a gated root before).
- selftests 46/49: verify/tests/test_argorder.py, test_guest.py, test_plugins.py uncertified (not investigated beyond: test_plugins FAILS one case on this branch, "external commit change: PP re-runs, CC runs nothing" ({'pp':2,'cc':1} vs {'pp':3,'cc':2}); it is about external pins, not about the changes above, I did not run it at the w4 head to compare; the others probably exceed the 300 s file timeout).
No gate for forms / eh / oracle-of-this-root exists for lift/rt here; nothing stubbed.
