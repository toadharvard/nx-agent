### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-013

* **LAYOUT gate artifacts removed** (45548a45, 6c9a8ee0):
  - The MSVC side no longer sees the shim's GCC layout fillers. FNVNX_MSVC_BASE_END(_PACK), FNVNX_EBO2 and
    FNVNX_VFPAD are empty there.
    - clang's MS layout opens a new unit at a zero-width bit-field after an empty base, which cl does not.
    - Example: tes.cpp FUN_0045ae50 had m_pkArray at 8, but the exe reads [ecx+4].
  - `-D__aarch64__ -D__ILP32__` had slipped into a comment in msvc_flags(). They are back.
* **TAIL1-PACK** (3cb31c66): under an active `#pragma pack(N)` with N < 8, the base filler is
  FNVNX_MSVC_BASE_END_PACK(N, base). MSVC places a base subobject at min(alignof(base), N), and GCC's filler had raised
  the class's alignment past the pack: hkReferencedObject.h's pack(1) classes were align 4 natively and 1 under MSVC.
* **The "other" bucket split** (6c9a8ee0), with new causes in the gate:
  - **vfpad**: MSVC pads a class's own vfptr (no base) up to the class's alignment.
    - In a 16-aligned class the first member sits at 16; GCC puts it at 4.
    - Example: hkaabbtree FUN_00d9c110_cls, members named m10 / m14 / ... / m40, has m10 at 16 under MSVC and at 4
      natively.
  - **vfpad-src**: the same pattern, but the members' offset names agree with the native layout.
    - Example: hkpSphereMotionD1E4 has hkReferencedObject's members flattened in, with unk10 at 0x10.
    - The exe's vfptr belongs to a 4-aligned base there. The source needs a per-class base, not a native change.
  - **memfnptr**: a pointer to member function is 4 bytes under MSVC (single inheritance) and 8 natively. The probe
    reports them with std::is_member_function_pointer.
  - On the 4 local Havok units: other 73 -> vfpad 16 + vfpad-src 51 + memfnptr 6.
* **VFPAD1**, a new rule (layout class; EQUIVALENCE row; tests in test_rules.py):
  - What it does: FNVNX_VFPAD(member types) goes first in a class with no base, a virtual function and data members.
    The filler is a zero-width bit-field aligned like the most aligned member type, so GCC moves the first member
    to MSVC's offset; it changes nothing when every member is 4-aligned or less.
  - Pack handling: FNVNX_VFPAD_PACK(N, ...) under pack(8); nothing under pack(N) with N <= 4.
  - Held, counted as VFPAD-HELD:
    - a member type nested in the class;
    - an anonymous aggregate;
    - the declarators after a nested `struct {...}`.
  - VFPAD1_SRC lists the vfpad-src classes.
  - Prepared head prep_k: VFPAD1 1,136 classes in 552 files, VFPAD-HELD 15, VFPAD-SRC 5. On the 4 units, vfpad goes
    from 16 rows to 0.
  - Compile: all 484 changed .cpp units of prep_k pass `-fsyntax-only` with the native flags.
* **memfnptr fix**, recon/s1_hkobjectresource_listener (4a6c81d8, fnv-nx-build):
  - The site: ListenerVTable_00e04f80 models the listener's vtable as six pointers to member functions, so
    onResourceRemoved sits at offset 20 under MSVC and 40 natively.
  - What the exe does: 0x00e04f80 at e04fb9..e04fd0 reads vtable slot 5 (+0x14) and makes a thiscall with (owner, name).
  - Before the recon, the native code read 8 bytes at offset 40 of a 4-byte-slot table.
  - With the recon, GCC calls through FNVNX_D26_SLOT(5): the compiled code reads [vt, #24]. MSVC keeps the original.
  - It is the only pointer-to-member field in the tree. The 6 other PMF typedefs are locals.
* **START (farms)**: please run `layoutgate` again on a head that has 6c9a8ee0 and VFPAD1. The whole-tree report
  cf66957439a9 predates the filler fix, TAIL1-PACK and the new causes.
* Next: vfpad-src classes (per-class base recons), and the remaining whole-tree causes from the new report.
