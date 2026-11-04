### J-XXXX | 2026-11-04 | stream: S1-refs | status: wip | parent: S1-refs-005

D21 classes B (after), E and F, measured. They follow S1-refs-005.

* **Class B: an `@fnv` body the link has no native for.** Measured on a full prepare with the current S1 rules,
  then defcheck on every owner unit (201, the owners of the "NO native" rows on link defb010). Each unit is compiled
  -O2 with compile.sh's flags, and each recipe function is matched to the object's natives with fnvlink's own
  KeyIndex lookup.
  - Before (link defb010): 1,138 tags (71 in .inl/.h, 11 in non-unit .cpp, 1,056 in units).
  - After: 0 compile failures. **60 of the 8,751 recipe functions** in those units are missing. Their causes:
    - 39 are a `??_G` whose tag sits on a helper constructor that makes MSVC emit the vftable. The class's virtual
      destructor is declared but not defined, and the unit's recipe binds its `??1` to an exe address.
      - Under GCC that destructor is the key function, so the unit emits neither the vtable nor D0.
      - No source body exists for these: MSVC generates the function. The link closes them with its `??_G`
        deleting-destructor stub (GDEL_SYM) and vtable synthesis (S2c-vt).
      - Binding the tag would be wrong: it would bind the constructor in place of a deleting destructor.
    - 8 are a `??_G` whose body is a hand-written `(this, flags)` deleter under another name, the link's FLAG_SYM
      shape.
    - 11 are a body under another name (STAND1): the unit's tag sits on the real body, and the recipe name is only
      declared.
      - fnvlink binds natives by recipe name only. Tags are read only for vtable-storing functions.
      - The mechanism proposed to S2: when no native matches the recipe name, bind the definition the unit's
        `@fnv <addr>` tag sits on, unless that definition is a constructor at a `??_G` address. This also covers
        the 8 above.
    - 2 have no tag anywhere in the unit (tesscriptfunctions 0x005c4940 and 0x005d4400). They are undecompiled,
      which is D20 territory.
  - No `@fnv` body in a .inl or variant file is left unbound by a compile failure or by an instantiation GCC never
    emits. The residue is link binding (58) and undecompiled functions (2).
* **Class E: `__xc` initializers with no native.** The exe's __xc span is 00fdf39c-01011414, 17,797 slots,
  against link defb010.
  - 606 slots have no native, and none of them has a recipe function: they are undecompiled.
  - Shapes: 199 full functions, 111 that begin with `push imm`, and 5 that are `mov ecx, obj; jmp ctor`. The rest
    load globals or floats first. 89 are in the CRT range.
  - Two possible mechanisms:
    (a) a build-time generator that emits natives for the trivial shapes from the owner's exe (local output, S10
        startup);
    (b) a D20 reconstruction per function (recon/ plus REC1).
  - Reported to the orchestrator and S5-boot.
* **Class F: a call bound to another instantiation of the same template** (RegisterCreator 0x00e93e70 -> SetAt
  0x00a642a0).
  - build_and_match of the pristine nid3dshaderprogramfactory.cpp: 26/26 functions, 3020/3020 bytes. The exe
    really calls 0x00a642a0.
  - 0x00a642a0 is one SetAt that COMDAT folding shares among several instantiations.
  - The native the address map gives that address belongs to a single instantiation. This is a link class (S2c):
    bind each caller to its own instantiation's native rather than to the folded address's single native.
