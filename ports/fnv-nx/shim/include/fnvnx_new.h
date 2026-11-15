/* fnvnx_new.h - the standard placement operator new / delete for units that carry their own copy of MSVC's
 * <exception> (class std::exception, std::bad_alloc, ...), which libstdc++'s <new> would define a second time.
 * prepare.py (rules DECL1, NEW2, CTOR1) includes this instead of <new> in such files. It stands in for <new>
 * (same include guard), so <new> must not follow in the unit; the decomp's MSVC STL copies never include it.
 */
#ifndef _NEW
#define _NEW
#ifndef FNVNX_NEW_H
#define FNVNX_NEW_H
inline void *operator new(__SIZE_TYPE__, void *p) noexcept { return p; }
inline void *operator new[](__SIZE_TYPE__, void *p) noexcept { return p; }
inline void operator delete(void *, void *) noexcept {}
inline void operator delete[](void *, void *) noexcept {}
#endif
#endif
