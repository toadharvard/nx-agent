/* cpweak.h - ONE plugin binary for both front ends (CC stage of a .c unit runs cc1, of a .cc unit cc1plus).
 *
 * RULE (CC1PLUS-SYMS): a plugin is loaded with RTLD_NOW, so every undefined dynamic symbol of it must be resolved by the compiler binary that loads
 * it. cc1 and cc1plus export the middle end (GIMPLE, RTL, cgraph, varpool, passes, tree.h, attributes ...); the symbols of the C++ front end
 * (cp/cp-tree.h: its global trees, scope_chain, operator tables, mangler, lookup, template and type predicates) are exported by cc1plus alone.
 * Each of them is declared weak here: a weak undefined symbol resolves to the real one under cc1plus and to null under cc1. It is only
 * ever used where the C++ front end's trees exist: every use sits behind `fnvnx_cxx_fe()` (a plugin registers its C++ callbacks only then;
 * pi tests it at each C++-only call).
 * PROOF of the class: toolchain/tests (link/tests/test_cc1_plugins.py) takes the undefined dynamic symbols of each built plugin and checks that
 * every one that is not weak is exported by the pinned cc1 AND the pinned cc1plus (nm -D of the compiler binaries): a new C++-only use that is not
 * listed here is red. The list below is the closure of what the plugins use.
 * Include AFTER "cp/cp-tree.h". */
#ifndef FNVNX_CPWEAK_H
#define FNVNX_CPWEAK_H
#include "langhooks.h"

#pragma weak cp_global_trees
#pragma weak scope_chain
#pragma weak ovl_op_info
#pragma weak ovl_op_mapping
#pragma weak cp_type_quals
#pragma weak binfo_for_vbase
#pragma weak same_signature_p
#pragma weak type_memfn_quals
#pragma weak most_general_template
#pragma weak cp_build_qualified_type
#pragma weak get_class_binding_direct
#pragma weak skip_artificial_parms_for
#pragma weak get_innermost_template_args
#pragma weak comptypes
#pragma weak lookup_fnfields
#pragma weak mangle_type_string
#pragma weak mark_used
#pragma weak cp_walk_subtrees
#pragma weak uses_template_parms
#pragma weak cp_get_callee_fndecl_nofold

/* the front end that loaded the plugin is the C++ one (what lang_GNU_CXX () of c-family says, without c-family) */
static inline bool fnvnx_cxx_fe() { return !strncmp(lang_hooks.name, "GNU C++", 7); }
#endif
