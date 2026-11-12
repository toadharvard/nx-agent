// face.h: the guest face of our COM objects (contract with L1/L5/L3a: contract/iat.py, loader/include/fnvnx_importkey.h).
//
// Lifted guest code calls a COM method as `call [[obj] + 4 * slot]` and the dispatcher maps the 32-bit value it finds there to an entry, so the
// table a guest reads must hold IMPORT KEYS (bridge rows dll `!<Interface>`, name = method, ordinal = slot), not addresses of native code.
// Rule: every object is allocated with NX_FACE_SIZE bytes in front of it (NxObj::operator new). The pointer the guest holds is the FACE = object - 8;
// the face's first word is the address of the key table of the object's class (one table per interface of the spec, d9bridge.gen.cpp).
//   native -> guest  nx_face(o):    writes the key table address into the face and returns the face (idempotent; null -> null)
//   guest -> native  nx_unface(f):  face + 8 (null -> null)
// Every interface pointer that crosses the boundary is converted by the generated thunk of the method it crosses in (gen/gen_bridge.py: the spec's
// `handle` items, in and out), and by the exports of d3d9_exports.cpp; nothing else converts. All interfaces of one object share one address (the
// D3D9 interfaces are single-inheritance chains and the interface is the first base of the generated class), so one face serves all of them.
// A guest never hands us an interface pointer that is not one of our objects in a `handle` item (D3D9 contract), so unface needs no check.
#pragma once
#include "obj.h"
// d9bridge.gen.cpp: the key table of a class, 0 when the import table of this build lacks one of its rows (the build gate forbids that state).
uint32_t nx_bridge_keytab(const NxClassInfo* ci);
inline void* nx_unface(void* f) { return f ? (uint8_t*)f + NX_FACE_SIZE : nullptr; }
inline void* nx_unface_this(void* f) { return (uint8_t*)f + NX_FACE_SIZE; }   // the `this` of a method call is never null (D3D9 contract: a null this faults on Windows too); no branch, so no null path for the analyzer (tests/l6/oom gate N) to follow
inline void* nx_face(IUnknown* o) {
    if (!o) return nullptr;
    uint32_t* f = (uint32_t*)((uint8_t*)o - NX_FACE_SIZE);
    f[0] = nx_bridge_keytab(nx_obj_of(o)->ci);
    return f;
}
