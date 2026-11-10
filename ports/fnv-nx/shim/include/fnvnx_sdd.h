/* fnvnx_sdd.h - SDD1: a virtual destructor takes ONE vtable slot, as under MSVC.
 *
 * MSVC puts one function in the slot of a virtual destructor: the scalar deleting destructor ??_G(flags), which runs
 * the destructor and, when flags bit 0 is set, frees the object with the class's operator delete. GCC puts two (D1,
 * D0), which shifts every later slot. Under FNVNX_SDD the prepare rule SDD1 (rules/sdd1.py) places, by syntax only:
 *
 *     virtual ~C();                      ->  FNVNX_VDTOR(C, bases...) FNVNX_SDD_VIRTUAL ~C();
 *     class D : B { ... }  (no `virtual ~D`)  ->  class D : B { ... FNVNX_SDD_DERIVED(D, B) }
 *     p->~C()  /  x.~C()                 ->  FNVNX_DESTROY(p, C)  /  FNVNX_DESTROY_REF(x, C)
 *     C::operator delete(p[, n])         ->  FNVNX_OPDELETE1(p, C)  /  FNVNX_OPDELETE2(p, n, C)
 *
 * and the compiler decides the rest:
 *   - the destructor is not virtual; `virtual void* __sdd(unsigned flags)` takes its slot and is ??_G;
 *   - a derived class declares `__sdd(X)`, where X is `unsigned` when one of its bases has a virtual __sdd (so it
 *     overrides it, as an implicit or non-virtual-spelled destructor does) and an unusable type otherwise (an
 *     ordinary member that is never called and never emitted). No list of polymorphic classes exists anywhere;
 *   - each such class has a C++20 destroying operator delete, so `delete p` is the virtual call __sdd(1) for a
 *     polymorphic class and "destructor, then operator delete" as before for any other. Every class with a base
 *     has its own, so the static type of a delete-expression is always the class whose function runs.
 * Nothing else changes: a delete-expression, a destructor definition and a derived class's implicit destructor
 * stay as written. Under MSVC the macros give the original text back.
 *
 * Which operator delete ??_G calls: the lookup of `operator delete` in the class's scope. A class's destroying
 * delete would hide its bases' operator delete, so the lookup is spelled out: the class's own non-destroying
 * operator delete(void*) or (void*, size_t), else the bases in declaration order (the macro's list), else the global
 * one. */
#ifndef FNVNX_SDD_H
#define FNVNX_SDD_H
#if defined(_MSC_VER) && !defined(__clang__) || defined(FNVNX_STATIC_MSVC)
/* MSVC (the equivalence build and the layout gate's MSVC side): the decomp's own text */
#define FNVNX_VDTOR(C, ...)
#define FNVNX_SDD_DERIVED(C, ...)
#define FNVNX_SDD_PURE(...) __VA_ARGS__
#define FNVNX_SDD_VIRTUAL virtual
#define FNVNX_DESTROY(p, ...) (p)->~__VA_ARGS__()
#define FNVNX_DESTROY_REF(x, ...) (x).~__VA_ARGS__()
#define FNVNX_OPDELETE1(p, ...) __VA_ARGS__::operator delete(p)
#define FNVNX_OPDELETE2(p, n, ...) __VA_ARGS__::operator delete(p, n)
#elif defined(__cplusplus) && defined(FNVNX_SDD)
#include <new>
#include <cstddef>
#ifndef __cpp_lib_destroying_delete
namespace std { struct destroying_delete_t { explicit destroying_delete_t() = default; }; }
#endif

/* a call that must not survive: an explicit C::operator delete(...) for which no such function is found */
void fnvnx_sdd_no_operator_delete() __attribute__((error("SDD1: no matching operator delete in the class or its bases")));

/* the parameter type of a derived class's __sdd: `unsigned` overrides a base's virtual __sdd, the other never matches */
struct fnvnx_sdd_never { fnvnx_sdd_never() = delete; };
template <bool> struct fnvnx_sdd_arg { typedef unsigned type; };
template <> struct fnvnx_sdd_arg<false> { typedef const fnvnx_sdd_never &type; };

struct fnvnx_sdd {
    /* has<B...>::value: some base has a virtual __sdd(unsigned) (its destructor is virtual in the source) */
    template <class B> static auto has1(int) -> decltype((void)static_cast<B *>(nullptr)->__sdd(0u), char());
    template <class B> static long has1(long);
    template <class... B> struct has { static const bool value = (false || ... || (sizeof(has1<B>(0)) == 1)); };
    /* ??_G: the destructor, then (flags bit 0) operator delete */
    template <class C> static void *run(C *p, unsigned f) { p->C::~C(); if (f & 1u) del<C>(p); return p; }
    template <class C> static void *run(C *, const fnvnx_sdd_never &) { return 0; }
    /* a delete-expression on a C*: virtual when C has __sdd(unsigned), else the destructor and operator delete */
    template <class C> static auto kill(C *p, int) -> decltype((void)p->__sdd(1u)) { p->__sdd(1u); }
    template <class C> static void kill(C *p, long) { p->C::~C(); del<C>(p); }
    /* C's own (or, without a destroying delete in C, inherited) usual deallocation function, by exact type.
     * how: 0 = as a delete-expression picks it ((void*) first, then (void*, size_t)); 1 = (void*) only;
     * 2 = (void*, size_t) only (an explicit call with that many arguments) */
    template <class C> static auto own1(void *p, int)
        -> decltype(static_cast<void (*)(void *)>(&C::operator delete), true)
    { static_cast<void (*)(void *)>(&C::operator delete)(p); return true; }
    template <class C> static bool own1(void *, long) { return false; }
    template <class C> static auto own2(void *p, std::size_t n, int)
        -> decltype(static_cast<void (*)(void *, std::size_t)>(&C::operator delete), true)
    { static_cast<void (*)(void *, std::size_t)>(&C::operator delete)(p, n); return true; }
    template <class C> static bool own2(void *, std::size_t, long) { return false; }
    template <class C> static bool own(void *p, std::size_t n, int how)
    { return (how != 2 && own1<C>(p, 0)) || (how != 1 && own2<C>(p, n, 0)); }
    /* through base B: B's own spelled-out lookup when B is an SDD class, else the plain lookup in B */
    template <class B> static auto via(void *p, std::size_t n, int how, int) -> decltype(B::__fnvnx_free(p, n, how))
    { return B::__fnvnx_free(p, n, how); }
    template <class B> static bool via(void *p, std::size_t n, int how, long) { return own<B>(p, n, how); }
    template <class C, class... B> static bool free_(void *p, std::size_t n, int how)
    { return own<C>(p, n, how) || (via<B>(p, n, how, 0) || ... || false); }
    /* ??_G's tail: free a complete object of class C */
    template <class C> static void del(C *p)
    { if (!C::__fnvnx_free((void *)p, sizeof(C), 0)) ::operator delete((void *)p); }
    /* an explicit `C::operator delete(p)` / `C::operator delete(p, n)` */
    template <class C> static void opdelete(void *p) { if (!via<C>(p, 0, 1, 0)) fnvnx_sdd_no_operator_delete(); }
    template <class C> static void opdelete(void *p, std::size_t n) { if (!via<C>(p, n, 2, 0)) fnvnx_sdd_no_operator_delete(); }
    /* an explicit destructor call names class C: virtual (flags 0) when C's destructor was */
    template <class C> static auto destroy(C *p, int) -> decltype((void)p->__sdd(0u)) { p->__sdd(0u); }
    template <class C> static void destroy(C *p, long) { p->~C(); }
};
template <class C> struct fnvnx_sdd_named { typedef C type; };
#define FNVNX_SDD_COMMON_(C, ...) \
    friend struct ::fnvnx_sdd; \
    static bool __fnvnx_free(void *fnvnx_p, std::size_t fnvnx_n, int fnvnx_h) { return ::fnvnx_sdd::free_<C, ##__VA_ARGS__>(fnvnx_p, fnvnx_n, fnvnx_h); } \
    static void operator delete(C *fnvnx_p, std::destroying_delete_t) { if (fnvnx_p) ::fnvnx_sdd::kill<C>(fnvnx_p, 0); }
/* a destructor declared virtual: __sdd takes its place in the declaration order (a new slot, or the base's) */
#define FNVNX_VDTOR(C, ...) FNVNX_SDD_COMMON_(C, ##__VA_ARGS__) \
    virtual void *__sdd(unsigned fnvnx_f) { return ::fnvnx_sdd::run<C>(this, fnvnx_f); }
/* a class with bases and no destructor declared virtual: before its closing brace */
#define FNVNX_SDD_DERIVED(C, ...) public: FNVNX_SDD_COMMON_(C, ##__VA_ARGS__) \
    void *__sdd(typename ::fnvnx_sdd_arg< ::fnvnx_sdd::has<__VA_ARGS__>::value>::type fnvnx_f) { return ::fnvnx_sdd::run<C>(this, fnvnx_f); }
/* `virtual ~C() = 0;`: the destructor itself is not pure any more (MSVC's slot holds ??_G all the same) */
#define FNVNX_SDD_PURE(...)
#define FNVNX_SDD_VIRTUAL
/* the class name comes last: it may hold commas (`~Map<K, V>()`) */
#define FNVNX_DESTROY(p, ...) ::fnvnx_sdd::destroy<typename ::fnvnx_sdd_named<__VA_ARGS__>::type>((p), 0)
#define FNVNX_DESTROY_REF(x, ...) ::fnvnx_sdd::destroy<typename ::fnvnx_sdd_named<__VA_ARGS__>::type>(&(x), 0)
#define FNVNX_OPDELETE1(p, ...) ::fnvnx_sdd::opdelete<typename ::fnvnx_sdd_named<__VA_ARGS__>::type>((p))
#define FNVNX_OPDELETE2(p, n, ...) ::fnvnx_sdd::opdelete<typename ::fnvnx_sdd_named<__VA_ARGS__>::type>((p), (n))
#elif defined(__cplusplus)
/* without FNVNX_SDD: the text as it was before the rule */
#define FNVNX_VDTOR(C, ...)
#define FNVNX_SDD_DERIVED(C, ...)
#define FNVNX_SDD_PURE(...) __VA_ARGS__
#define FNVNX_SDD_VIRTUAL virtual
#define FNVNX_DESTROY(p, ...) (p)->~__VA_ARGS__()
#define FNVNX_DESTROY_REF(x, ...) (x).~__VA_ARGS__()
#define FNVNX_OPDELETE1(p, ...) __VA_ARGS__::operator delete(p)
#define FNVNX_OPDELETE2(p, n, ...) __VA_ARGS__::operator delete(p, n)
#endif
#endif
