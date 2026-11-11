/* fnvnx_vc9.h - MSVC 2008 stand-in for shim/include/fnvnx_vc9.h in S11 equivalence builds (STL2). On the target the
 * header is the VC9 containers in their x86 layout under the namespace fnvnx_vc9; here those containers are MSVC's
 * own, so STL2's `std` -> `fnvnx_vc9` is the identity: the namespace is std (the macro undoes the rename, so a
 * unit's own `namespace fnvnx_vc9 { ... }` reopens std as the decomp's `namespace std { ... }` did), and
 * `fnvnx_vc9::stdext` (STL2's spelling of `stdext`) is the global stdext. STL2 replaced the unit's first container
 * include by this header and commented out the others: the library headers it names are included here. */
#ifndef FNVNX_VC9_H
#define FNVNX_VC9_H
#include <string>
#include <vector>
#include <map>
#include <xstring>
#include <xtree>
#include <xmemory>
#include <xutility>
/* STL2 spells the `size_t` of a unit's own code in namespace std as VC9's _Sizet (shim: unsigned int): MSVC's size_t */
namespace std { namespace stdext = ::stdext; typedef size_t _Sizet; }
#define fnvnx_vc9 std
#endif
