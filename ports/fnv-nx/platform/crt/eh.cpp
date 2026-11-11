/* eh.cpp - _CxxThrowException(object, ThrowInfo) as a GCC (Itanium) throw that typed handlers can catch
 * (S4b-crt).
 *
 * MSVC raises SEH exception 0xE06D7363 with the object (still in the thrower's frame) and its ThrowInfo; the
 * catch blocks run before the frame is unwound. GCC unwinds the thrower first, so the object is copied into
 * __cxa_allocate_exception storage and thrown with an Itanium std::type_info. The type comes from, in order:
 *
 *  1. a registration by ThrowInfo address (fnvnx_eh_register_throw: GCC type_info, size, copy, destructor -
 *     the native-type path; fnvnx_eh_register_msvc_std: "this ThrowInfo throws MSVC's std::bad_alloc", ...);
 *  2. the ThrowInfo itself, read from the data image (x86 layout, 32-bit absolute addresses): the most-derived
 *     CatchableType whose TypeDescriptor is registered with fnvnx_rtti_register (S2's ??_R0 -> _ZTI table) is
 *     thrown as that GCC type, copied with the CatchableType's copy constructor and destroyed with the
 *     ThrowInfo's pmfnUnwind (code addresses go through the code resolver); otherwise the first TypeDescriptor
 *     whose name is an MSVC standard exception (".?AVlength_error@std@@", ...) is translated to the libstdc++
 *     exception of the same name with the MSVC object's message;
 *  3. otherwise fnvnx_msvc_exception{object, throw_info} is thrown: catch (...) only.
 *
 * MSVC standard exceptions become catchable as the standard types: exception / bad_alloc / bad_cast / bad_typeid /
 * bad_exception as fnvnx::msvc::exc<std::X> (MSVC what()), logic_error and runtime_error families as the
 * libstdc++ classes constructed from the MSVC string _Str. */
#include "fnvnx_msvcrt.h"
#include "fnvnx_msvc_cpp.h"

#include <cxxabi.h>
#include <exception>
#include <ios>
#include <stdexcept>
#include <string>

#include "../os/os.h"

using namespace fnvnx::msvc;

const std::type_info *fnvnx_rtti_lookup(const void *msvc_type_descriptor); /* helpers.cpp */

namespace {
enum kind { K_NATIVE, K_STD };
struct reg {
    const void *ti_addr;
    kind k;
    const std::type_info *ti;
    size_t size;
    fnvnx_eh_copy_fn copy;
    fnvnx_eh_dtor_fn dtor;
    const char *std_name;
};
reg *g_regs;
size_t g_nregs, g_capregs;
os_lock_t g_lock;
void *(*g_code_resolver)(uint32_t);
uint32_t g_image_lo = 0x10000, g_image_hi = 0xFFFFFFFFu; /* plausible VAs until the loader says better */

bool add(const reg &r)
{
    os_lock(&g_lock);
    for (size_t i = 0; i < g_nregs; i++)
        if (g_regs[i].ti_addr == r.ti_addr) {
            g_regs[i] = r;
            os_unlock(&g_lock);
            return true;
        }
    if (g_nregs == g_capregs) {
        size_t cap = g_capregs ? g_capregs * 2 : 32;
        reg *n = (reg *)realloc(g_regs, cap * sizeof *n);
        if (!n) {
            os_unlock(&g_lock);
            return false;
        }
        g_regs = n;
        g_capregs = cap;
    }
    g_regs[g_nregs++] = r;
    os_unlock(&g_lock);
    return true;
}

bool find(const void *ti_addr, reg *out)
{
    bool ok = false;
    os_lock(&g_lock);
    for (size_t i = 0; i < g_nregs; i++)
        if (g_regs[i].ti_addr == ti_addr) {
            *out = g_regs[i];
            ok = true;
            break;
        }
    os_unlock(&g_lock);
    return ok;
}

/* ---- MSVC standard exceptions -> libstdc++ */
enum std_kind { S_NONE, S_EXCEPTION, S_BAD_ALLOC, S_BAD_CAST, S_BAD_TYPEID, S_BAD_EXCEPTION, S_LOGIC, S_DOMAIN,
                S_INVALID_ARG, S_LENGTH, S_OUT_OF_RANGE, S_RUNTIME, S_RANGE, S_OVERFLOW, S_UNDERFLOW, S_FAILURE };
struct std_name { const char *name; std_kind k; };
const std_name g_std_names[] = {
    { ".?AVexception@std@@", S_EXCEPTION },         { ".?AVbad_alloc@std@@", S_BAD_ALLOC },
    { ".?AVbad_cast@std@@", S_BAD_CAST },           { ".?AVbad_typeid@std@@", S_BAD_TYPEID },
    { ".?AVbad_exception@std@@", S_BAD_EXCEPTION }, { ".?AVlogic_error@std@@", S_LOGIC },
    { ".?AVdomain_error@std@@", S_DOMAIN },         { ".?AVinvalid_argument@std@@", S_INVALID_ARG },
    { ".?AVlength_error@std@@", S_LENGTH },         { ".?AVout_of_range@std@@", S_OUT_OF_RANGE },
    { ".?AVruntime_error@std@@", S_RUNTIME },       { ".?AVrange_error@std@@", S_RANGE },
    { ".?AVoverflow_error@std@@", S_OVERFLOW },     { ".?AVunderflow_error@std@@", S_UNDERFLOW },
    { ".?AVfailure@ios_base@std@@", S_FAILURE },
};

std_kind std_kind_of(const char *name)
{
    if (name)
        for (const std_name &s : g_std_names)
            if (!strcmp(name, s.name))
                return s.k;
    return S_NONE;
}

template <class B> [[noreturn]] void throw_exc(const char *what)
{
    throw exc<B>(what, what != nullptr);
}

[[noreturn]] void throw_std(std_kind k, const void *obj)
{
    const exc_fields *e = (const exc_fields *)obj;
    const logic_error_fields *le = (const logic_error_fields *)obj;
    switch (k) {
    case S_EXCEPTION: throw_exc<std::exception>(e->_m_what);
    case S_BAD_ALLOC: throw_exc<std::bad_alloc>(e->_m_what);
    case S_BAD_CAST: throw_exc<std::bad_cast>(e->_m_what);
    case S_BAD_TYPEID: throw_exc<std::bad_typeid>(e->_m_what);
    case S_BAD_EXCEPTION: throw_exc<std::bad_exception>(e->_m_what);
    default: break;
    }
    /* logic_error / runtime_error families: what() is _Str */
    std::string msg(le->_Str.c_str(), le->_Str._Mysize);
    switch (k) {
    case S_LOGIC: throw std::logic_error(msg);
    case S_DOMAIN: throw std::domain_error(msg);
    case S_INVALID_ARG: throw std::invalid_argument(msg);
    case S_LENGTH: throw std::length_error(msg);
    case S_OUT_OF_RANGE: throw std::out_of_range(msg);
    case S_RUNTIME: throw std::runtime_error(msg);
    case S_RANGE: throw std::range_error(msg);
    case S_OVERFLOW: throw std::overflow_error(msg);
    case S_UNDERFLOW: throw std::underflow_error(msg);
    default: throw std::ios_base::failure(msg);
    }
}

[[noreturn]] void throw_native(const void *obj, const std::type_info *ti, size_t size, fnvnx_eh_copy_fn copy,
                               fnvnx_eh_dtor_fn dtor)
{
    void *p = __cxxabiv1::__cxa_allocate_exception(size ? size : 1);
    if (copy) {
        try {
            copy(p, obj);
        } catch (...) {
            __cxxabiv1::__cxa_free_exception(p);
            throw;
        }
    } else {
        memcpy(p, obj, size);
    }
    __cxxabiv1::__cxa_throw(p, const_cast<std::type_info *>(ti), dtor);
}

/* ---- the ThrowInfo in the image (MSVC x86: absolute 32-bit addresses) */
bool plausible(uint32_t va, uint32_t align)
{
    return va >= g_image_lo && va < g_image_hi && !(va & (align - 1));
}

const void *va_ptr(uint32_t va) { return (const void *)(uintptr_t)va; }

/* x86 .text of FalloutNV.exe 1.4.0.525: a code pointer there must be resolved to the native function */
void *code(uint32_t va)
{
    if (!va)
        return nullptr;
    if (va >= 0x00401000u && va < 0x00fdf000u) {
        void *p = g_code_resolver ? g_code_resolver(va) : nullptr;
        if (!p) {
            static int warned;
            if (warned++ < 8)
                os_debug_print("[fnvnx] _CxxThrowException: x86 code address in the ThrowInfo has no native "
                               "function; bitwise copy / no destructor");
        }
        return p;
    }
    return g_code_resolver ? g_code_resolver(va) : (void *)(uintptr_t)va; /* already a native address */
}

const fnvnx_msvc_catchable_type *catchable(const fnvnx_msvc_throw_info *ti, int i)
{
    const fnvnx_msvc_catchable_type_array *a =
        (const fnvnx_msvc_catchable_type_array *)va_ptr(ti->pCatchableTypeArray);
    uint32_t ct = a->arrayOfCatchableTypes[i];
    return plausible(ct, 4) ? (const fnvnx_msvc_catchable_type *)va_ptr(ct) : nullptr;
}

const char *td_name(const fnvnx_msvc_catchable_type *ct)
{
    if (!ct || !plausible(ct->pType, 4))
        return nullptr;
    return ((const fnvnx_msvc_type_descriptor *)va_ptr(ct->pType))->name;
}

/* number of catchable types, 0 if the ThrowInfo does not look like one */
int ncatchable(const void *throw_info)
{
    uintptr_t t = (uintptr_t)throw_info;
    if (t > 0xFFFFFFFFu || !plausible((uint32_t)t, 4))
        return 0;
    const fnvnx_msvc_throw_info *ti = (const fnvnx_msvc_throw_info *)throw_info;
    if (!plausible(ti->pCatchableTypeArray, 4))
        return 0;
    int n = ((const fnvnx_msvc_catchable_type_array *)va_ptr(ti->pCatchableTypeArray))->nCatchableTypes;
    return n > 0 && n <= 64 ? n : 0;
}

[[noreturn]] void throw_from_image(const void *obj, const void *throw_info, int n)
{
    const fnvnx_msvc_throw_info *ti = (const fnvnx_msvc_throw_info *)throw_info;
    const fnvnx_msvc_catchable_type *ct0 = catchable(ti, 0);
    if (ct0 && plausible(ct0->pType, 4)) {
        const std::type_info *gti = fnvnx_rtti_lookup(va_ptr(ct0->pType));
        if (gti) {
            /* properties & 1: simple type (bitwise copy); & 4: has a virtual base (copy ctor takes a flag) */
            fnvnx_eh_copy_fn copy = (ct0->properties & 1) ? nullptr : (fnvnx_eh_copy_fn)code(ct0->copyFunction);
            if (ct0->properties & 4)
                os_debug_print("[fnvnx] _CxxThrowException: copy constructor of a class with a virtual base");
            throw_native(obj, gti, (size_t)ct0->sizeOrOffset, copy, (fnvnx_eh_dtor_fn)code(ti->pmfnUnwind));
        }
    }
    for (int i = 0; i < n; i++) {
        std_kind k = std_kind_of(td_name(catchable(ti, i)));
        if (k != S_NONE)
            throw_std(k, obj);
    }
    throw fnvnx_msvc_exception{ const_cast<void *>(obj), const_cast<void *>(throw_info) };
}
} // namespace

extern "C" int fnvnx_eh_register_throw(const void *throw_info, const std::type_info *ti, size_t size,
                                       fnvnx_eh_copy_fn copy, fnvnx_eh_dtor_fn dtor)
{
    return add(reg{ throw_info, K_NATIVE, ti, size, copy, dtor, nullptr }) ? 0 : -1;
}

extern "C" int fnvnx_eh_register_msvc_std(const void *throw_info, const char *msvc_type_name)
{
    std_kind k = std_kind_of(msvc_type_name);
    if (k == S_NONE)
        return -1;
    for (const std_name &s : g_std_names)
        if (s.k == k)
            return add(reg{ throw_info, K_STD, nullptr, 0, nullptr, nullptr, s.name }) ? 0 : -1;
    return -1;
}

/* The ThrowInfos the decomp passes to _CxxThrowException, typed from the code around the calls (no image):
 * 0x01140ef8 - exception(&null) + vftable 0x010176f8 in the allocators' overflow paths (std::_Allocate):
 *              std::bad_alloc;
 * 0x01140cb0 - a logic_error(const string &) object with vftable 0x010173dc thrown by an inline "ThrowLen":
 *              std::length_error (medium confidence; the image's TypeDescriptor settles it);
 * 0x01170384 - SpeedTree's own exception class (string, int): needs its GCC type (fnvnx_eh_register_type) once
 *              S1 gives the per-unit placeholder names (CIdvException, ExceptionB07, ...) one class.
 * Call this when the image's EH tables are not read; registrations win over the image. */
extern "C" void fnvnx_eh_register_fnv_known(void)
{
    fnvnx_eh_register_msvc_std((const void *)(uintptr_t)0x01140ef8u, ".?AVbad_alloc@std@@");
    fnvnx_eh_register_msvc_std((const void *)(uintptr_t)0x01140cb0u, ".?AVlength_error@std@@");
}

extern "C" void fnvnx_eh_set_code_resolver(void *(*resolve)(uint32_t va)) { g_code_resolver = resolve; }

extern "C" void fnvnx_eh_set_image_range(uint32_t lo, uint32_t hi)
{
    g_image_lo = lo ? lo : 0x10000;
    g_image_hi = hi ? hi : 0xFFFFFFFFu;
}

/* the type name a ThrowInfo would be thrown as (registration or image), for diagnostics and tests */
extern "C" const char *fnvnx_eh_describe(const void *throw_info)
{
    reg r;
    if (find(throw_info, &r))
        return r.k == K_STD ? r.std_name : r.ti->name();
    int n = ncatchable(throw_info);
    return n ? td_name(catchable((const fnvnx_msvc_throw_info *)throw_info, 0)) : nullptr;
}

/* _CxxThrowException(obj, ThrowInfo); obj == NULL is "throw;" */
extern "C" void fnvnx_cxx_throw_exception(void *obj, void *throw_info)
{
    if (!obj)
        throw; /* rethrows the exception being handled; std::terminate when there is none, as on MSVC */
    reg r;
    if (find(throw_info, &r)) {
        if (r.k == K_NATIVE)
            throw_native(obj, r.ti, r.size, r.copy, r.dtor);
        throw_std(std_kind_of(r.std_name), obj);
    }
    int n = ncatchable(throw_info);
    if (n)
        throw_from_image(obj, throw_info, n);
    static int warned;
    if (warned++ < 8)
        os_debug_print("[fnvnx] _CxxThrowException: unknown ThrowInfo, only catch (...) will see this exception");
    throw fnvnx_msvc_exception{ obj, throw_info };
}

/* std::uncaught_exception() of MSVC's libcpmt (0x00ec3241, crt_symbols.tsv): the game's throws are GCC throws
 * (_CxxThrowException above), so the Itanium count of exceptions in flight is the answer. */
extern "C" bool fnvnx_uncaught_exception(void) { return std::uncaught_exceptions() > 0; }
