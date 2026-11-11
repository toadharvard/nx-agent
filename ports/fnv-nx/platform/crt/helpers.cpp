/* helpers.cpp - MSVC library helpers that the decomp source calls by name (see docs/RUNTIME.md): security cookie check
 * and std::_Lockit, std::_Xlen, std::_Xran.
 * The MSVC EH / RTTI helpers (`eh vector constructor/destructor/copy constructor iterator', __RTDynamicCast, the
 * TypeDescriptor -> type_info table, _CxxThrowException, std::uncaught_exception) are not here: in the product the game's
 * own translated EH code and the lifter runtime perform C++ EH and RTTI (the import bridge binds msvcrt!_CxxThrowException,
 * __CxxFrameHandler, _except_handler3, ?terminate, type_info's destructor to the lifter runtime), and a native
 * reimplementation that called the game's constructors, destructors and ThrowInfo code as native function pointers could
 * not be reached from translated code and would have been wrong if it were.
 * Helpers the compiler emits implicitly (_ftol2 on casts, _allmul, _alloca_probe, _CIsin, EH handlers, ...)
 * have no counterpart here: GCC generates its own code for those. */
#include "fnvnx_msvcrt.h"

#include <cxxabi.h>
#include <stdlib.h>
#include <exception>
#include <stdexcept>
#include <typeinfo>

#include "../os/os.h"

extern "C" void fnvnx_security_check_cookie(uintptr_t cookie)
{
    (void)cookie; /* /GS frames are not reproduced by GCC; there is nothing to compare against */
}

/* ---- std::_Lockit: the MSVC library's global recursive locks, indexed by kind (0..7 in VC9) */
namespace {
struct rlock { os_lock_t l; volatile uint32_t owner; int depth; };
rlock g_lockit[8];
uint32_t self_token()
{
    static __thread uint32_t tok;
    static volatile uint32_t next = 1;
    if (!tok)
        tok = __atomic_fetch_add(&next, 1, __ATOMIC_RELAXED);
    return tok;
}
} // namespace

extern "C" void fnvnx_lockit_ctor(void *self, int kind)
{
    unsigned k = (unsigned)kind & 7;
    *(int *)self = (int)k;
    rlock &r = g_lockit[k];
    uint32_t me = self_token();
    if (__atomic_load_n(&r.owner, __ATOMIC_RELAXED) == me) {
        r.depth++;
        return;
    }
    os_glock(&r.l);   /* the game's own _Lockit: held across its callbacks (os.h) */
    __atomic_store_n(&r.owner, me, __ATOMIC_RELAXED);
    r.depth = 1;
}

extern "C" void fnvnx_lockit_dtor(void *self)
{
    rlock &r = g_lockit[(unsigned)*(int *)self & 7];
    if (--r.depth == 0) {
        __atomic_store_n(&r.owner, 0u, __ATOMIC_RELAXED);
        os_gunlock(&r.l);
    }
}

extern "C" void fnvnx_xlength_error(void) { throw std::length_error("string too long"); }
extern "C" void fnvnx_xout_of_range(void) { throw std::out_of_range("invalid string position"); }
