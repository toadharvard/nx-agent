### J-XXXX | 2026-11-04 | stream: S1-refs | status: wip | parent: S1-refs-003

* **task** - S13b's hand-offs (private fuzz/causes/0bd144db1464, handoffs_S1.tsv: 1,481 S1 rows). Excluded are the
  rows of S1-fuzzA's causes (native-null-read, native-trap) and S1-fuzzB's (falloff?, float, signedness).
* **causes S1-refs takes** (817 rows):

| cause | rows |
|---|---:|
| native-undefined | 457 |
| after-native-undefined | 270 |
| native-empty-callee | 36 |
| layout-offset | 20 |
| native-calls-data | 12 |
| native-empty-body | 10 |
| native-zero-stub | 8 |
| native-missing-free | 4 |

* **grouping (native-undefined)**
  - 456 of the 457 reproducers are still in fuzz/cache. They name 318 distinct undefined callees, grouped by
    decomp construct:

| construct | rows | names |
|---|---:|---:|
| body-less conversion operator of a unit-local wrapper | 165 | 96 |
| other | 94 | 79 |
| destructor declared but defined nowhere | 87 | 55 |
| `__fnvnx_bdvc_*` | 76 | 66 |
| member / free arithmetic operator | 23 | 15 |
| raw addresses, ctors, vector deleting dtor | 11 | 7 |

  - The conversion operators by class: NiPointer 39, BSStringT 29, StrOp 6, NiPtr 4, ModelPtr0050fbf0 4, ...
* **method** - Each group's body is proven against the exe: tools/build_and_match.py compares the unit's MSVC 2008
  build function by function with the owner's FalloutNV.exe (agent-only). A rule must make the MSVC build match
  where the pristine build does not, then the reproducers are replayed.
* **measured (native-undefined, 456 rows found in fuzz/cache)** - fnvlink's lookup re-run with the current
  integration code (unit index, then the global defs/refs as classify_ref does) on every callee name:
  - 159 rows (35 %) resolve now: 148 through the unit's own recipe, 11 globally. Almost all are the conversion
    operators (NiPointer, BSStringT, ...), which the recipe binds to the exe's folded getter 0x00559450. S2d's
    8c2cfc2 ("sigs.parse_msvc reads conversion operators to pointer types", 15:31) fixed them. The fuzz link
    0bd144db1464 (12:53) predates it, so these rows drop at the next link + fuzz. Not S1 work.
  - Check: tools/build_and_match.py with the owner's exe on modelloader.cpp gives 492 / 492 functions and
    66,338 / 66,338 bytes. The pristine unit is the exe, so these were link misses, not decomp bugs.
  - 297 rows remain:

| construct | rows | names | where the fix belongs |
|---|---:|---:|---|
| other member / free functions (NiAVObject::v03, Map_adc910::KeyToHashIndex, ...) | 91 | 76 | not bound in any recipe: per unit, a GCC-only reference or a placeholder |
| destructors `C::~C()` | 78 | 54 | link: 68 rows have the unit binding `??_GC` (see below) |
| `__fnvnx_bdvc_*` | 76 | 66 | S2 (devirtualized-call bridges) |
| arithmetic / compare operators | 23 | 15 | per unit |
| conversion operators not covered by 8c2cfc2 | 22 | 16 | per unit |
| raw addresses, vector deleting dtor | 7 | 4 | per unit |

* **destructor proposal (S2-link)** - MSVC compiles `delete p` as the scalar deleting destructor `??_GC` (bound in
  the unit, e.g. TESConditionItem -> 0x007b3fa0). The exe has no separate `~C`: its body is folded into `??_G`
  (0x007b3fa0's native definition is `Date::FUN_007b3fa0(flags)`: `this->~Date(); if (flags & 1) Deallocate(this)`).
  GCC compiles the same `delete p` as D1 + operator delete, so D1 is undefined.
  - D1(this) is `??_G(this, 0)`: destroy, do not free.
  - A link bridge for an unbound D1/D2 of a class without virtual bases, whose unit binds `??_GC`, fixes 68 rows.
  - A prepare rule cannot: the body exists only inside `??_G`, and calling an MSVC-generated `??_G` from C++ needs
    a name the link binds.
* **next** - after-native-undefined (270) follows these callees and is re-measured when the link changes land.
  S1-refs takes the per-unit groups (the 91 + 23 + 22 + 7 rows). For each one, build_and_match against the exe
  decides whether the call is GCC-only or a placeholder.
