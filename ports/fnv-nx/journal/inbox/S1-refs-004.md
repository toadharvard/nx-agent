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
* **did (layout-offset)** - 70379cf + 8a6a983 EBO2 (layout, new).
  - Problem: a class that introduces its own vfptr, with an empty base B. MSVC puts the vfptr at 0 and B at +4; the
    Itanium ABI puts B at 0, under the vptr. The members are unchanged, but B's address differs.
  - The case: effectitemlist.cpp's `EffectItemList : BSSimpleList_Standin`. Its base methods are bound to
    BSSimpleList's code in the exe, which receives this+4 (x86) vs this+0, the vptr (native), on
    IsEmpty 0x008256d0. build_and_match: the pristine unit equals the exe, 29 / 29 functions.
  - Fix: `FNVNX_EBO2` = `char fnvnx_ebo2[0];` (GCC) as the first member of B, so B is not empty but still 0
    bytes. Empty under MSVC, so the MSVC code is identical by construction.
  - Gates: .cpp only; B is used only as a base, `B*`, `B&` or `B::`; every derived class has B as its only base.
  - Verified: an ILP32 probe on the prepared unit gives base at +4, member at +4, sizeof 8.
  - Decomp-wide: 143 edits in 127 .cpp units, 0 -> 0 -O2 errors in them.
  - Of the 20 layout-offset rows it covers effectitemlist's 3 and fires in blk_00720000_od and
    blk_00c40000_od_ob1_variant_rtm_variant (1 row each). The rest are other layout causes, next on the list.
* **S5-boot's three wrong decomp bodies (s5-002.md)**
  - 0x00c3c460 `new MH(false)` with an empty `MH::MH`: **already fixed** by STAND2 (S1-fuzzB 57fe267) in the current
    rules. STAND2 drops the untagged local `MH::MH(bool) {}`, and the recipe binds `??0MH@@QAE@_N@Z` to <va>
    (MessageHandler's constructor). With the current link code, GCC's `_ZN2MHC1Eb` / `C2Eb` resolve to 0x00c3bcf0
    in the unit's index. S5 measured on older guests. Its stand-in for 0x00c3c460 in tests/s5/standins.c can go
    after a boot on a guest linked from a tree with STAND2.
  - 0x00aa3cc0 CreateMemoryPool: **not a wrong body**.
    - Evidence: tools/build_and_match.py with the exe on the pristine src/bscore/memorymanager.cpp gives 21 / 21
      functions and 2,371 / 2,371 bytes, including 0x00aa3cc0.
    - Why MSVC matches: the unit's `delete pnewPool` reaches MemoryManager::Deallocate because
      memorymanager_memory.h defines the game's global `operator new` / `operator delete` inline
      (`g_memoryManager.Allocate` / `.Deallocate`), and MSVC inlines them.
    - Why the guest differs: ALLOC1 has to drop those inline replacements for GCC, so the guest's `delete` is
      whatever global delete the guest links: free under GAME_NEW=runtime (toolchain/game/global_new.cpp), or the
      NiFree S5 saw.
    - The faithful fix is guest-wide, for S5 / toolchain: global new / delete (and the array and sized forms) =
      MemoryManager::Allocate (0x00aa3e40) / Deallocate (0x00aa4060), the definition every decomp unit inlines.
      S5's boot shows the game's allocators now work. The stand-in can go then.
  - 0x00aa78a0 MemoryHeap::MemoryHeap: **missing from the decomp**. No unit's recipe has 0x00aa78a0; it is the gap
    between 0x00aa7870 + 17 and ReserveAndCommitMemory 0x00aa79a0. 0x00aa7930, a second MemoryHeap constructor
    (it calls 0x00aa8ea0 and sets +0x20f1 = 1), is missing too.
    - S1-refs wrote a reconstruction and checked it: added to src/bscore/memoryheap.cpp (a namespaced
      AbstractHeap base with a virtual destructor and the constructor), with the recipe entry
      `??0MemoryHeap@fnv_00aa78a0@@QAE@IIPBD_N@Z` (0x00aa78a0, 136 bytes) and bindings for 0x00aa8ca0, 0x00aa8dd0,
      0x010a28fc, its __ehhandler 0x00f1eda8 and ___security_cookie 0x011c16bc. build_and_match then gives
      18 / 18 functions and 1,144 / 1,144 bytes against the exe.
    - **Not committed.** Writing the reconstructed body into this public repository was refused by the session's
      permission check (provenance: the code is derived from the exe). It stays in the S1-refs container only.
    - Decision needed (owner / orchestrator): where reconstructed bodies of functions the decomp lacks may live. For
      example, the private artifacts repository, with a rule that applies them when present, or upstream in the
      decomp. Until then, heap_standin.c stays.
* **D20 done (MemoryHeap)**
  - Private: fnv-nx-build `recon/bscore_memoryheap` (d3da8fd), the constructor 0x00aa78a0 and the destructor
    0x00aa7930 (not a second constructor). build_and_match: 19 / 19 functions, 1,246 / 1,246 bytes.
  - nx-agent: the rule REC1 (2d859ea), which applies recon bodies when FNVNX_RECON is set.
  - Waiting on: prepare_job / full_job must fetch the recon/* branches into one directory and export FNVNX_RECON.
  - Before heap_standin.c goes: the native MemoryHeap vtable has only the destructor slots, so S5 must boot-check
    that the link pairs it with the exe vftable at the full slot count.
* **layout-offset, the 17 rows left** - every one is a call-args divergence: the same object, another offset.

| shape | rows | units |
|---|---:|---|
| x86 passes a base or member at +N, native at +0 | 7 | extradataobjects (+0xc / +0x10), tesregionpointlist (+0x6c), bgszonetargetlistener (+0x10), actor, archive (+0x160 / +0x158), bgsprimitive (+0x24), blk_00720000_od (EBO2 fires there) |
| native passes +N, x86 +0 | 5 | tesflora (+0xc), tesattackdamageform (+0x30), blk_006a0000_od (+4), hkpcharacterproxy (+0xc), blk_00c40000 rtm (+0x14 / +0x10, EBO2 fires there) |
| a pointer into the data image 0x80 apart, all to FUN_00403e20 | 3 | sky, bstreemanager, blk_00800000_od (likely one global, to check against S2c's split-global) |
| other | 2 | blk_00480000_od (p0 +0x14), statsmenu (p0 0 vs a value) |

  - The diverging call sits in a callee of the fuzzed function (0x004f81e0's own text has no BSSimpleList call), so
    each row needs its call site found first: the fuzz record's x86 / arm traces, then build_and_match on that unit.
