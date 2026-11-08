### J-XXXX | 2026-11-07 | stream: R6-recon (claude/fnv-recon6) | status: wip | parent: J-0156

* **shard 9 (taken over from R10)** - causes.tsv bf0b0eb, `$9==9`: 89 rows (80 trap stubs).
  - 73 closed, in 30 more `recon/<unit>` branches. Every recon passes the MSVC whole-unit match and the native
    aarch64 compile.
  - 28 extern-text names: one body at 0x00401460, a memcpy wrapper to memmove 0x00ec44d0.
  - 11 conversion operators: alias bodies of the shared /Od getter 0x00559450, generated mechanically (cast of the
    first dword, generic member plus explicit instantiation; explicit class specializations defined directly).
  - 2 transform products: aliases of NiTransform::operator* 0x0062c250, built in /O2 units through
    `#pragma optimize("", off)`.
    - The point type needs a declared constructor (non-POD, as NiPoint3), or MSVC adds a copy of the returned
      value.
  - Others: NiPoint2 scale aliases; about 25 bindings, each checked by hand against its exe call site or unwind
    funclet; native-only forwarders for 3 `__vecDelDtor` / decltype names; 5 inline bodies with their sites.
  - One binding rests on an inlined copy rather than a call, so fuzz should look at it:
    `List_b4::Deallocate` -> 0x0045cee0, the pool free that ClearLights 0x00b71300 inlines.
* **open, 16 rows (15 traps)** - table on fnv-nx-build `recon-notes/r6` (HANDOFF.md, shard 9 section).
  - code-defmissing 6: adjustor thunks 4, a `??_G` tag on a constructor 1, a native operator* without alias 1.
  - exe-shape conflicts in units.json 2: a FaceGen operator() and an operator* bound to addresses of another arity.
  - wrong decomp caller bodies 4 (array SetAtGrow / AddFirstEmpty written as list appends, an inverted refcount, a
    sized delete without its size).
  - harness stand-ins 4 (S5).
* **metric** - shard 9: 89 -> 16 rows without a body (trap stubs 80 -> 15).
  - Shards 6 and 5+3 are unchanged (18 and 23, all routed).
  - Next: the rows still open in shard 2 (R3) or 7 (R2), after asking them which.
