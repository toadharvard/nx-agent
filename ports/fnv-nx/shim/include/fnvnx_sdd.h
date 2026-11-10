/* fnvnx_sdd.h - SDD1: a virtual destructor takes ONE vtable slot, as under MSVC.
 *
 * MSVC puts one function in the slot of a virtual destructor: the scalar deleting destructor ??_G(flags), which runs
 * the destructor and, when flags bit 0 is set, frees the object with the class's operator delete. GCC puts two (D1,
 * D0), which shifts every later slot. Under FNVNX_SDD the prepare rule SDD1 (rules/sdd1.py) rewrites
 *
 *     virtual ~C();             ->  FNVNX_VDTOR(C, bases...) FNVNX_SDD_VIRTUAL ~C();
 *     ~D();   (polymorphic D)   ->  FNVNX_VDTOR_O(D, bases...) ~D();
 *     (no destructor declared)  ->  FNVNX_SDD_IMPLICIT(D, bases...)   before the closing brace
 *     p->~C()  /  x.~C()        ->  FNVNX_DESTROY(p, C)  /  FNVNX_DESTROY_REF(x, C)
 *
 * and here the destructor becomes non-virtual, `virtual void* __sdd(unsigned flags)` takes its slot and is ??_G, and a
 * C++20 destroying operator delete makes every `delete p` that virtual call with flags 1. Nothing else changes: a
 * delete-expression, a destructor definition and a derived class's implicit destructor stay as they are written.
 * Under MSVC the macros give back the original text (tools/equiv/msvc/fnvnx_equiv_fi.h).
 *
 * Which operator delete ??_G calls: the lookup of `operator delete` in the class's scope. A class's destroying
 * delete would hide its bases' operator delete, so the lookup is spelled out: the class's own non-destroying
 * operator delete(void*) or (void*, size_t), else the bases in declaration order (the macro's list), else the global
 * one. */
#ifndef FNVNX_SDD_H
#define FNVNX_SDD_H
#if defined(__cplusplus) && defined(FNVNX_SDD)
#include <new>
#include <cstddef>
#ifndef __cpp_lib_destroying_delete
namespace std { struct destroying_delete_t { explicit destroying_delete_t() = default; }; }
#endif

struct fnvnx_sdd {
    /* C's own (or, without a destroying delete in C, inherited) usual deallocation function, by exact type */
    template <class C> static auto own(void *p, std::size_t, int)
        -> decltype(static_cast<void (*)(void *)>(&C::operator delete), true)
    { static_cast<void (*)(void *)>(&C::operator delete)(p); return true; }
    template <class C> static auto own(void *p, std::size_t n, long)
        -> decltype(static_cast<void (*)(void *, std::size_t)>(&C::operator delete), true)
    { static_cast<void (*)(void *, std::size_t)>(&C::operator delete)(p, n); return true; }
    template <class C> static bool own(void *, std::size_t, ...) { return false; }
    /* through base B: B's own spelled-out lookup when B is an SDD class, else the plain lookup in B */
    template <class B> static auto via(void *p, std::size_t n, int) -> decltype(B::__fnvnx_free(p, n))
    { return B::__fnvnx_free(p, n); }
    template <class B> static bool via(void *p, std::size_t n, long) { return own<B>(p, n, 0); }
    template <class C, class... B> static bool free_(void *p, std::size_t n)
    { return own<C>(p, n, 0) || (via<B>(p, n, 0) || ... || false); }
    /* ??_G's tail: free a complete object of class C */
    template <class C> static void del(C *p)
    { if (!C::__fnvnx_free((void *)p, sizeof(C))) ::operator delete((void *)p); }
    /* an explicit destructor call names class C: virtual (flags 0) when C's destructor was */
    template <class C> static auto destroy(C *p, int) -> decltype((void)p->__sdd(0u)) { p->__sdd(0u); }
    template <class C> static void destroy(C *p, long) { p->~C(); }
};
template <class C> struct fnvnx_sdd_named { typedef C type; };
#define FNVNX_SDD_COMMON_(C, ...) \
    friend struct ::fnvnx_sdd; \
    static bool __fnvnx_free(void *fnvnx_p, std::size_t fnvnx_n) { return ::fnvnx_sdd::free_<C, ##__VA_ARGS__>(fnvnx_p, fnvnx_n); } \
    static void operator delete(C *fnvnx_p, std::destroying_delete_t) { if (fnvnx_p) fnvnx_p->__sdd(1u); }
#define FNVNX_SDD_BODY_(C) { this->C::~C(); if (fnvnx_f & 1u) ::fnvnx_sdd::del<C>(this); return this; }
/* a destructor declared virtual: __sdd takes its place in the declaration order (a new slot, or the base's) */
#define FNVNX_VDTOR(C, ...) FNVNX_SDD_COMMON_(C, ##__VA_ARGS__) virtual void *__sdd(unsigned fnvnx_f) FNVNX_SDD_BODY_(C)
/* a destructor that is virtual through a base: `override` makes a wrong guess of the rule a compile error */
#define FNVNX_VDTOR_O(C, ...) FNVNX_SDD_COMMON_(C, ##__VA_ARGS__) void *__sdd(unsigned fnvnx_f) override FNVNX_SDD_BODY_(C)
/* no destructor declared, virtual through a base: the implicit one (public) */
#define FNVNX_SDD_IMPLICIT(C, ...) public: FNVNX_VDTOR_O(C, ##__VA_ARGS__)
/* `virtual ~C() = 0;`: the destructor itself is not pure any more (MSVC's slot holds ??_G all the same) */
#define FNVNX_SDD_PURE(...)
#define FNVNX_SDD_VIRTUAL
/* the class name comes last: it may hold commas (`~Map<K, V>()`) */
#define FNVNX_DESTROY(p, ...) ::fnvnx_sdd::destroy<typename ::fnvnx_sdd_named<__VA_ARGS__>::type>((p), 0)
#define FNVNX_DESTROY_REF(x, ...) ::fnvnx_sdd::destroy<typename ::fnvnx_sdd_named<__VA_ARGS__>::type>(&(x), 0)
#elif defined(__cplusplus)
/* without FNVNX_SDD: the text as it was before the rule */
#define FNVNX_VDTOR(C, ...)
#define FNVNX_VDTOR_O(C, ...)
#define FNVNX_SDD_IMPLICIT(C, ...)
#define FNVNX_SDD_PURE(...) __VA_ARGS__
#define FNVNX_SDD_VIRTUAL virtual
#define FNVNX_DESTROY(p, ...) (p)->~__VA_ARGS__()
#define FNVNX_DESTROY_REF(x, ...) (x).~__VA_ARGS__()
#endif
#endif
