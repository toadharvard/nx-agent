/* fnvnx_addr - ADDR32: an address computed as a 32-bit integer is used as that 32-bit value.
 *
 * The game computes addresses in 32-bit integers (`*(T *)(i * 4 + 0x011f0000)`, `*(int *)(x + 0x40u)`); on x86 the
 * sum wraps modulo 2^32 and the wrapped value is the address. GCC for AArch64 ILP32 keeps the integer sum in GIMPLE
 * (`_1 = x + 64; _2 = (int *) _1`) but, expanding the memory access, distributes the zero extension over the sum
 * and uses the addressing mode `[zext(x), #64]`: for x >= 0xffffffc0 that is an address above 4 GiB where x86 reads
 * a low one. -fwrapv, -fwrapv-pointer and -fno-strict-overflow do not change it (found by L3 LIFT).
 *
 * Rule: just before RTL expansion every conversion of an integer value to a pointer goes through an empty asm
 * statement (`asm("" : "=r"(p) : "0"(i))`): the expander then sees a register that holds the finished 32-bit
 * value and has no sum to take apart. No instruction is added by the statement itself; the addition that the
 * addressing mode would have absorbed stays a 32-bit add. Every such conversion is rewritten, constants excepted
 * (a constant address has no sum).
 */
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "context.h"
#include "tree.h"
#include "tree-pass.h"
#include "function.h"
#include "basic-block.h"
#include "gimple-expr.h"
#include "gimple.h"
#include "gimple-iterator.h"
#include "ssa.h"
#include "tree-ssanames.h"
#include "stringpool.h"
#include "diagnostic-core.h"

int plugin_is_GPL_compatible;

namespace {
const pass_data addr_data = { GIMPLE_PASS, "fnvnx_addr", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
struct addr_pass : gimple_opt_pass {
  addr_pass(gcc::context *c) : gimple_opt_pass(addr_data, c) {}
  unsigned int execute(function *fun) override
  {
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (!is_gimple_assign(st) || !CONVERT_EXPR_CODE_P(gimple_assign_rhs_code(st))) continue;
        tree lhs = gimple_assign_lhs(st), rhs = gimple_assign_rhs1(st);
        if (TREE_CODE(lhs) != SSA_NAME || !POINTER_TYPE_P(TREE_TYPE(lhs))) continue;
        if (TREE_CODE(rhs) != SSA_NAME || !INTEGRAL_TYPE_P(TREE_TYPE(rhs))) continue;
        if (TYPE_PRECISION(TREE_TYPE(rhs)) != TYPE_PRECISION(TREE_TYPE(lhs)))
          { error_at(gimple_location(st), "ADDR32: an integer of another width converted to a pointer"); continue; }
        /* v = asm("" : "=r"(v) : "0"(rhs)); lhs = (T *) v */
        tree v = make_ssa_name(TREE_TYPE(rhs));
        vec<tree, va_gc> *in = NULL, *out = NULL;
        vec_safe_push(out, build_tree_list(build_tree_list(NULL_TREE, build_string(2, "=r")), v));
        vec_safe_push(in, build_tree_list(build_tree_list(NULL_TREE, build_string(1, "0")), rhs));
        gasm *a = gimple_build_asm_vec("", in, out, NULL, NULL);
        SSA_NAME_DEF_STMT(v) = a;
        gimple_set_location(a, gimple_location(st));
        gsi_insert_before(&gsi, a, GSI_SAME_STMT);
        update_stmt(a);
        gimple_assign_set_rhs1(st, v);
        update_stmt(st);
      }
    return 0;
  }
};
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) return 1;
  register_pass_info p;
  p.pass = new addr_pass(g);
  p.reference_pass_name = "optimized";
  p.ref_pass_instance_number = 1;
  p.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &p);
  return 0;
}
