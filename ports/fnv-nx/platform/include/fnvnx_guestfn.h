/* fnvnx_guestfn.h - the type of a code pointer the GAME gave the platform, and the only way to call it.
 *
 * A window procedure, thread start, timer callback, enumeration callback, qsort comparator, exception filter, mmio I/O
 * procedure, waveOut callback, hook procedure, DllMain ... that a game passes to a Win32 import is a 32-bit GUEST
 * address in the product (lifted x86 code, or the import key of a platform function): calling it as a native function
 * pointer crashes. The platform therefore holds every such value as fnvnx_guestfn<R(Args...)>, a pointer-sized value
 * with NO conversion to a function pointer and NO call operator but operator(), which marshals the arguments into x86
 * stack words by type and calls fnvnx_bnd_guest_invoke (fnvnx_boundary.h). A direct call `p(a, b)` of a callback
 * therefore is the only call syntax there is, and it goes through the boundary; `((R (*)(A))p.value())(a)` is the
 * only way around it and is a deliberate cast (tests/s4 `make gfn-nocompile` shows that the plain call forms do not
 * compile).
 *
 * Rule of the types (class closing: the template decides by type, no list of callbacks exists):
 *   argument  pointer, integral or enum of <= 32 bits, long/unsigned long (the Win32 LONG/DWORD: 32 bits in the
 *             game, one word on the LP64 test host as well), bool        -> 1 word (sign- or zero-extended)
 *             long long / unsigned long long (a 64-bit integer)          -> 2 words, low first
 *             double                                                     -> 2 words (its bit pattern), low first
 *             float                                                      -> 1 word (its bit pattern)
 *             function pointers (a native address of the platform must never reach the game: use
 *             fnvnx_bnd_export_value), classes, unions, references, member pointers, any other integer width
 *                                                                        -> static_assert failure
 *   result    void, pointer, integral/enum <= 32 bits, long, unsigned long, bool    -> kind FNVNX_BND_RET_INT (eax)
 *             long long, unsigned long long                              -> kind FNVNX_BND_RET_I64 (edx:eax)
 *             float, double                                              -> kind FNVNX_BND_RET_ST0
 *             anything else                                              -> static_assert failure
 * The runtime does not distinguish stdcall from cdecl (the caller of a boundary call pops its own words), so there is
 * no convention tag. At most FNVNX_BND_MAX_WORDS words per call (static_assert).
 * On the LP64 test host a word is pointer-wide and the callee is a native function taking integer arguments: a
 * callback with a double or 64-bit integer parameter has no native counterpart there (the marshalling is checked by
 * tests/s4/test_guestfn.cpp through the recording test double of guest_invoke).
 *
 * Locks: the runtime may not return from a guest call (a game exception unwinding past the import discards the native
 * frames without cleanup), so no platform lock may be held across a call through this type. Every platform lock goes
 * through os_lock/os_trylock/os_unlock, which the host test backend counts per thread (os.h); the host test boundary records
 * a violation whenever a call arrives here with a count other than 0, and the test mains require it to stay 0 (the product's boundary does not check). */
#ifndef FNVNX_GUESTFN_H
#define FNVNX_GUESTFN_H

#include "fnvnx_boundary.h"

#ifdef __cplusplus
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <type_traits>

namespace fnvnx_gfn {

template <class T> struct dependent_false : std::false_type {};

enum class cls { w1s, w1u, w1p, w1f, w2i, w2d, bad };

template <class T> constexpr cls classify()
{
    using U = typename std::remove_cv<T>::type;
    if constexpr (std::is_pointer<U>::value) {
        return std::is_function<typename std::remove_pointer<U>::type>::value ? cls::bad : cls::w1p;
    } else if constexpr (std::is_same<U, double>::value) {
        return cls::w2d;
    } else if constexpr (std::is_same<U, float>::value) {
        return cls::w1f;
    } else if constexpr (std::is_same<U, long long>::value || std::is_same<U, unsigned long long>::value) {
        return cls::w2i;
    } else if constexpr (std::is_same<U, bool>::value) {
        return cls::w1u;
    } else if constexpr (std::is_integral<U>::value || std::is_enum<U>::value) {
        if constexpr (!(sizeof(U) <= 4 || std::is_same<U, long>::value || std::is_same<U, unsigned long>::value))
            return cls::bad;
        else if constexpr (std::is_enum<U>::value)
            return std::is_signed<typename std::underlying_type<U>::type>::value ? cls::w1s : cls::w1u;
        else
            return std::is_signed<U>::value ? cls::w1s : cls::w1u;
    } else {
        return cls::bad;
    }
}

template <class T> constexpr unsigned nwords() { return classify<T>() == cls::w2i || classify<T>() == cls::w2d ? 2 : 1; }

template <class T> inline void put(fnvnx_gword *w, T v)
{
    constexpr cls c = classify<T>();
    static_assert(c != cls::bad, "fnvnx_guestfn: argument type has no x86 stack-word marshalling");
    if constexpr (c == cls::w1p) {
        w[0] = (fnvnx_gword)(uintptr_t)v;
    } else if constexpr (c == cls::w1s) {
        w[0] = (fnvnx_gword)(intptr_t)(long)v;
    } else if constexpr (c == cls::w1u) {
        w[0] = (fnvnx_gword)(uintptr_t)(unsigned long)v;
    } else if constexpr (c == cls::w1f) {
        uint32_t b;
        memcpy(&b, &v, 4);
        w[0] = b;
    } else if constexpr (c == cls::w2d) {
        uint64_t b;
        memcpy(&b, &v, 8);
        w[0] = (uint32_t)b;
        w[1] = (uint32_t)(b >> 32);
    } else {   /* w2i */
        uint64_t b = (uint64_t)v;
        w[0] = (uint32_t)b;
        w[1] = (uint32_t)(b >> 32);
    }
}

template <class R> constexpr uint32_t ret_kind()
{
    if constexpr (std::is_void<R>::value) {
        return FNVNX_BND_RET_INT;
    } else {
        constexpr cls c = classify<R>();
        static_assert(c != cls::bad, "fnvnx_guestfn: result type has no x86 return-register marshalling");
        return c == cls::w2i ? FNVNX_BND_RET_I64 : (c == cls::w1f || c == cls::w2d) ? FNVNX_BND_RET_ST0 : FNVNX_BND_RET_INT;
    }
}

template <class R> inline R get(const struct fnvnx_bnd_ret &r)
{
    if constexpr (std::is_void<R>::value) {
        (void)r;
    } else {
        constexpr cls c = classify<R>();
        if constexpr (c == cls::w1p)
            return (R)(uintptr_t)r.eax;
        else if constexpr (std::is_same<typename std::remove_cv<R>::type, bool>::value)
            return (r.eax & 0xffu) != 0;
        else if constexpr (c == cls::w1s || c == cls::w1u)
            return (R)r.eax;
        else if constexpr (c == cls::w1f)
            return (R)r.st0;
        else if constexpr (c == cls::w2d)
            return (R)r.st0;
        else
            return (R)(((uint64_t)(uint32_t)r.edx << 32) | (uint32_t)r.eax);
    }
}

template <class R, class... A> inline R invoke(fnvnx_gaddr addr, uint32_t ecx, A... a)
{
    constexpr unsigned n = (0u + ... + nwords<A>());
    static_assert(n <= FNVNX_BND_MAX_WORDS, "fnvnx_guestfn: too many argument words");
    fnvnx_gword w[n ? n : 1] = {};
    unsigned i = 0;
    ((put<A>(w + i, a), i += nwords<A>()), ...);
    (void)i;
    struct fnvnx_bnd_ret r = {0, 0, 0.0};
    fnvnx_bnd_guest_invoke(addr, ecx, n, w, ret_kind<R>(), &r);
    return get<R>(r);
}

}   // namespace fnvnx_gfn

template <class Sig> class fnvnx_guestfn;

template <class R, class... A> class fnvnx_guestfn<R(A...)> {
    uintptr_t v_ = 0;

public:
    typedef R result_type;
    constexpr fnvnx_guestfn() = default;
    constexpr fnvnx_guestfn(decltype(nullptr)) {}
    explicit constexpr fnvnx_guestfn(uintptr_t v) : v_(v) {}
    explicit fnvnx_guestfn(const volatile void *p) : v_((uintptr_t)p) {}

    constexpr uintptr_t value() const { return v_; }
    explicit constexpr operator uintptr_t() const { return v_; }
    explicit operator void *() const { return (void *)v_; }
    explicit constexpr operator bool() const { return v_ != 0; }

    /* The only way to call it: the arguments become x86 stack words by type, the call goes through the boundary. */
    R operator()(A... a) const { return fnvnx_gfn::invoke<R, A...>(v_, 0, a...); }

    friend constexpr bool operator==(fnvnx_guestfn a, fnvnx_guestfn b) { return a.v_ == b.v_; }
    friend constexpr bool operator!=(fnvnx_guestfn a, fnvnx_guestfn b) { return a.v_ != b.v_; }
    friend constexpr bool operator==(fnvnx_guestfn a, decltype(nullptr)) { return a.v_ == 0; }
    friend constexpr bool operator!=(fnvnx_guestfn a, decltype(nullptr)) { return a.v_ != 0; }
    friend constexpr bool operator==(decltype(nullptr), fnvnx_guestfn a) { return a.v_ == 0; }
    friend constexpr bool operator!=(decltype(nullptr), fnvnx_guestfn a) { return a.v_ != 0; }
};

/* The one place the properties the rule rests on are checked (every instantiation shares the layout of the value). */
namespace fnvnx_gfn {
typedef fnvnx_guestfn<void(void)> probe;
static_assert(sizeof(probe) == sizeof(void *) && alignof(probe) == alignof(void *), "a guestfn is exactly a pointer");
static_assert(sizeof(probe) == sizeof(uintptr_t), "a guestfn holds one boundary word");
static_assert(std::is_trivially_copyable<probe>::value && std::is_trivially_destructible<probe>::value,
              "a guestfn is passed like its pointer");
static_assert(!std::is_convertible<probe, void (*)(void)>::value && !std::is_convertible<probe, void *>::value &&
                  !std::is_convertible<probe, uintptr_t>::value && !std::is_convertible<void (*)(void), probe>::value &&
                  !std::is_convertible<void *, probe>::value && !std::is_convertible<uintptr_t, probe>::value,
              "conversions are explicit");
}   // namespace fnvnx_gfn

/* A method of an object the GAME implements (a COM interface the platform was handed: slot `slot` of the object's
 * vtable, this = obj as the first stack word, as every COM method; ecx carries it too, so a thiscall-compiled
 * implementation sees it as well -- note a thiscall callee then pops nothing and reads its arguments after `this`: COM
 * interfaces are stdcall, which this helper is for). The vtable pointer and the slot entries are pointer-sized values
 * of the guest address space (32 bits in the product, native on the host).
 * Use: fnvnx_guest_com_call<HRESULT>(obj, 3, arg1, arg2) with the exact argument types of the method. */
template <class R, class... A> inline R fnvnx_guest_com_call(const void *obj, unsigned slot, A... a)
{
    const uintptr_t *vt = *(const uintptr_t *const *)obj;
    return fnvnx_gfn::invoke<R, const void *, A...>(vt[slot], (uint32_t)(uintptr_t)obj, obj, a...);
}

#endif /* __cplusplus */
#endif
