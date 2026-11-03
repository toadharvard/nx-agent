/* fnvnx_new.h - MSVC 2008 stand-in for shim/include/fnvnx_new.h in S11 equivalence builds (tools/equiv).
 *
 * The target header gives the standard placement new / delete to units that carry their own copy of MSVC's
 * <exception> (where <new> would define std::exception again). The MSVC meaning is what MSVC 2008's <new> defines
 * for them, with the same guards (so a later <new> does not define them twice).
 */
#ifndef FNVNX_EQUIV_NEW_H
#define FNVNX_EQUIV_NEW_H
#include <crtdefs.h>
#ifndef __PLACEMENT_NEW_INLINE
#define __PLACEMENT_NEW_INLINE
inline void *__cdecl operator new(size_t, void *_Where) throw() { return (_Where); }
inline void __cdecl operator delete(void *, void *) throw() { }
#endif
#ifndef __PLACEMENT_VEC_NEW_INLINE
#define __PLACEMENT_VEC_NEW_INLINE
inline void *__cdecl operator new[](size_t, void *_Where) throw() { return (_Where); }
inline void __cdecl operator delete[](void *, void *) throw() { }
#endif
#endif
