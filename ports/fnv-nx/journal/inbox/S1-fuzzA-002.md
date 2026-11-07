### J-XXXX | 2026-11-04 | stream: S1-fuzzA | status: wip | parent: S1-fuzzA-001

* **did** - `-fcheck-new` in compile.sh and census.sh (`b6b411d`). It is a semantics flag, so every compile shard
  rebuilds once.
  - MSVC 2008 tests the pointer of every new-expression before it constructs (`test eax, eax; je`), for the
    placement form `new (p) T(x)` and for a throwing `new T` alike. GCC drops that test.
  - So the decomp's array appends (`new (m_data + m_iElementCount) T(t)`, hkArray-style) construct at address 0
    where the exe constructs nothing.
  - The flag keeps the test (GCC 13 emits a `cbz`). It also removes S13b's new-null shape (a throwing new's null
    test) at its source.
  - Fresh fuzz, same rules in both arms: 8 functions diverge without the flag, 0 with it. The functions are
    006601f0, 00c68e30, 00d1bcb0, 00ddecd0, 00ddecf0, 00de30a0, 00d56880, 00e5c440.
  - For 00e5c440 a `mem-write` signedness? divergence remains; it is not in this metric.
* **measured** - Re-fuzz of the open native-null-read functions of 27 units, with the current rules (STAND2 + STAND3
  + everything merged since link 0bd144). It was run as a one-unit prepare, compile and subset link per unit, with
  40 iterations of 8 s.
  - Clean now: 008fc210, 00887b40, 008b0bd0, 008bd630, 0065a840, 005a3a30, 00ad3e60, 005cd4b0.
  - Also clean, or no longer null-read, without `-fcheck-new`: 00d470f0, 00dc9f20, 00c77460, 00877be0 (00877be0
    is now bool-repr only).
  - Expected metric after the farms re-fuzz: 475 -> about 445.
    - STAND3: -9.
    - `-fcheck-new`: -8.
    - Other rules: -12.
    - The 30 records that no longer reproduce may lower it further.
* **found** - More routing among the open functions. Lists are on `reports/s1-fuzz-a`.
  - **13 functions, for S13: secondary-base overrides**, confirmed from the exe's RTTI. The function sits only in
    vftables with a non-zero COL offset, so MSVC's `this` is that subobject and the native `this` is the full
    object.
    - 00409ce0, 00885140, 008b9b70, 008c3650, 008c4ab0, 0093ac20, 0093ad30, 0093ad60, 00cad820, 00d089c0,
      00d64020, 00d9a480, 00df6b10.
    - 0092d050 and 00980110 have a +4 shift too but are not virtual overrides. They stay open.
  - **3 functions, for S13: a bool global with a background value.** The global at 0x011d8909 holds 0xd6, 0x14 or
    0x02; GCC tests bit 0, MSVC tests the byte. Functions: 00723890, 007646d0, 007a3ac0.
    - Fix: plant 0/1 for globals the unit declares bool, as the harness already does for bool arguments.
  - **For S2c-vt: a null slot in a paired native vtable.** 0080dca0 calls slot 0x22c: the exe vftable has a function
    there, the native copy has 0, and the native calls address 0. More `fault:x:0` records of this shape: 00649ca0,
    00649ce0, 00c84c50, 00c85790, 00c9d620, 00ca0540.
  - **17 records now replay as a call to a named stub, so they leave this cause at the next re-fuzz.** 11 call
    `name:_ZN...` (native-undefined); 6 call `__s13_vtpad_*` (vptr-unpaired).
  - Only 3 of the open functions differ in call order (0044d600, 00440dc0, 006041d0). The 0bd144 objects predate
    S12's ORDER-OBJECT and ORDER-OPERATOR modes; the next compile has them.
* **note (tools)** - The current compile.sh runs a whole-program `-fsyntax-only` key discovery over all units by
  default (ORDER_KEYS). A one-unit local compile takes more than 20 minutes unless `ORDER_KEYS=unit` is set.
* **next** - Wait for the farms' re-fuzz with STAND2, STAND3 and `-fcheck-new`. Re-derive the open list from the new
  caches, then take the next family.
