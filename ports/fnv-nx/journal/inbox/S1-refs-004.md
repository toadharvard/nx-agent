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
