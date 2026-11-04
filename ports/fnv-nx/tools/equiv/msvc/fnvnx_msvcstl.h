/* fnvnx_msvcstl.h - MSVC 2008 stand-in for shim/include/fnvnx_msvcstl.h in S11 equivalence builds (tools/equiv).
 *
 * The target header re-creates the MSVC 2008 (Dinkumware) library internals the decomp calls by name
 * (_SCL_SECURE_VALIDATE, std::_Checked_base, std::_Ptr_cat, the iterator tags); under MSVC they are <xutility>'s own.
 * STL1 includes it where a unit uses them without defining them. With FNVNX_MSVCSTL_AUX (a unit deriving from
 * std::_Container_base_aux_alloc_real) the target header also has the container base, _Destroy_range,
 * stdext::unchecked_uninitialized_copy and the checked vector iterators: <vector> and <memory> here. */
#ifndef FNVNX_EQUIV_MSVCSTL_H
#define FNVNX_EQUIV_MSVCSTL_H
#ifdef __cplusplus
#include <xutility>
#ifdef FNVNX_MSVCSTL_AUX
#include <memory>
#include <vector>
#endif
#endif
#endif
