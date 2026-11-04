### J-XXXX | 2026-11-04 | stream: S1-layout | status: wip | parent: S1-refs-004

* **task** - The layout-offset bucket and native-empty-body, taken over from S1-refs. Lists: private
  fuzz/causes/0bd144db1464 handoffs_S1.tsv (link 0bd144db1464, before EBO2, S12's rtl argument order and STAND2/3).
* **metric** - layout-offset + native-empty-body fuzz records in those lists: 27 + 10 = 37. A measured "after"
  needs a re-fuzz on a head with the change (farms). Per row, the cause read from the reproducer, the decomp source,
  build_and_match with the owner's exe on the pristine unit (all 37 units match the exe) and the GCC ILP32 output:

| rows | cause | status |
|---:|---|---|
| 5 | empty base under a class's own vfptr (effectitemlist 3, 007308e0, 00c4a360) | EBO2 (merged, S1-refs) |
| 4 | argument evaluation order (FUN_00403e20 getters: sky 0063d1d0, bstreemanager 006652e0, statsmenu 007dfd20, blk_00800000 00800900): the native evaluated the first argument first | S12's args=rtl (92f3b4d6): the current GCC output evaluates right to left, as the exe |
| 1 | implicit destructor the recipe binds (extradataobjects 00434dc0) | IDTOR1 (below) |

* **did** - IDTOR1 (neutral, new; tools/rules/s1_compile.py, shim FNVNX_IDTOR1, docs/EQUIVALENCE.md).
  - Problem: a class C without a destructor of its own but with a member that has one gets an implicit `C::~C`.
    MSVC calls it by name and the recipe binds it to the exe (extradataobjects.cpp `List_009e03e0` ->
    0x0046ffb0, BSSimpleList's destructor, called with this+0xc). GCC defines the implicit destructor inline
    from the decomp's placeholder members: it destroyed `ListX m_uiX` at this+0x10.
  - Fix: `FNVNX_IDTOR1(C)` at the end of C's body: `public: ~C();` under GCC, nothing under MSVC. A real declaration
    under MSVC renumbers the unit's `$SG` string literals the recipe binds (build_and_match: 16 of 36 units broke).
  - Gates: .cpp; C at namespace or class scope, no template, no anonymous namespace, defined once, no `~C` in its
    body; the unit binds `??1C@<scopes>@@QAE@XZ` (non-virtual) to an address some unit decompiles.
  - Decomp-wide: 50 edits in 36 units. build_and_match with the exe on the 36 units with the rule (macro empty):
    35 match fully, blk_00440000_od's one EH-tail difference is in the pristine build too. -O2 errors in the 36
    units: 0 -> 0. GCC output: `~FUN_00434dc0` calls `List_009e03e0::~List_009e03e0` (bound) instead of
    `ListX::~ListX` at +0x10.
  - tests/s1: 0 failed (new IDTOR1 checks with an ILP32 compile). tests/s11: ok.
