/* fnvnx_addr - ADDR32: an address computed as a 32-bit integer is used as that 32-bit value.
 *
 * The game computes addresses in 32-bit integers (`*(T *)(i * 4 + 0x011f0000)`, `*(int *)(x + 0x40u)`); on x86 the
 * sum wraps modulo 2^32 and the wrapped value is the address. GCC for AArch64 ILP32 keeps the integer sum in GIMPLE
 * (`_1 = x + 64; _2 = (int *) _1`) but, expanding the memory access, distributes the zero extension over the sum
 * and uses the addressing mode `[zext(x), #64]`: for x >= 0xffffffc0 that is an address above 4 GiB where x86 reads
 * a low one. -fwrapv, -fwrapv-pointer and -fno-strict-overflow do not change it (found by L3 LIFT).
 *
 * RULE ADDR32. Just before RTL expansion (a pass inserted after the last GIMPLE pass, `*warn_function_noreturn`; `expand` follows it directly) every
 * statement of the classes below gets its value from an empty asm statement (`asm("" : "=r"(v) : "0"(i))`): the
 * expander then sees a register that holds the finished 32-bit value and has no sum to take apart. The statement
 * itself adds no instruction; the addition that the addressing mode would have absorbed stays a 32-bit add.
 *
 *   class CONV  `lhs = (T *) x` and `lhs = VIEW_CONVERT_EXPR<T *>(x)` where x is an SSA name of an integral type
 *               of any width (the value is first converted to the pointer's width, with the signedness of x, as C
 *               converts), or of another scalar type of the pointer's size (a VIEW_CONVERT). lhs is any lvalue.
 *   class PLUS  `lhs = p + off` (POINTER_PLUS_EXPR) whose offset is not an INTEGER_CST: the sum of a pointer and a
 *               value computed at run time is an address computed in a 32-bit integer. lhs gets its value from
 *               the barrier, the sum goes to a new SSA name that only the barrier uses.
 *
 * Constants (rule C). An INTEGER_CST converted to a pointer, and a POINTER_PLUS_EXPR with an INTEGER_CST offset, are
 * not routed: a constant is a finished 32-bit value, it has no run-time sum to take apart; a constant displacement
 * folded into an addressing mode is `[zext(p), #d]` with |d| below 2^16 (the immediate field of the A64 loads and
 * stores), and that equals the wrapped sum whenever p + d does not cross 0 or 2^32 (assumption A-EDGE: no guest
 * object, hence no guest pointer, lies within 64 KiB of address 0 or of 2^32; the guest is a 32-bit Windows process
 * whose user space ends below 0xc0000000 and whose first 64 KiB are never mapped).
 *
 * Totality. Every assignment whose result is a pointer and whose operand computes it from an integer is of class
 * CONV or PLUS or has a constant operand; any other form that makes a pointer from a non-pointer value
 * (a TARGET_MEM_REF with an index, a result that cannot be rewritten) is a compile error. Memory operands that
 * are `MEM_REF[p + constant]` are covered by A-EDGE; ARRAY_REF with a variable index of a declared array is
 * in bounds by C++ semantics (assumption A-BOUNDS).
 *
 * Verification (last GIMPLE point). After the rewrite the pass scans the function again: every statement of class
 * CONV must have an operand defined by an asm statement (or a constant), every statement of class PLUS must have a
 * result that only asm statements use. A statement that does not is a compile error. The scan counts the statements
 * of both classes (seen) and those that satisfy the condition (routed).
 *
 * Record. The object gets a section `.fnvnx.addr` (not loaded): one LF-terminated line per function the unit
 * emits, sorted by symbol, TAB-separated:
 *
 *     <assembler symbol>\t<seen>\t<routed>
 *
 * (decimal numbers; seen == routed in every line that the compiler accepts). The functions are those that reach
 * the last GIMPLE point (every body GCC compiles, ipa clones included), and every other function symbol the unit
 * defines: thunks and aliases (constructor and destructor aliases, `.set`) have no GIMPLE body, so no statement of
 * class CONV or PLUS; they get a line `<symbol>\t0\t0`.
 *
 *   g++ ... -fplugin=<dir>/fnvnx_addr.so unit.cpp
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
#include "gimplify.h"
#include "gimplify-me.h"
#include "ssa.h"
#include "tree-ssanames.h"
#include "tree-ssa-operands.h"
#include "stringpool.h"
#include "diagnostic-core.h"
#include "cgraph.h"
#include "output.h"
#include <map>
#include <set>
#include <string>
#include <vector>

int plugin_is_GPL_compatible;

namespace {

std::map<std::string, std::pair<unsigned, unsigned>> records;   // assembler symbol -> (seen, routed)

const pass_data addr_data = { GIMPLE_PASS, "fnvnx_addr", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };

enum cls { C_NONE, C_CONV, C_PLUS };

/* the class of a statement; an unhandled way of making a pointer is an error */
cls classify(gimple *st)
{
  if (!is_gimple_assign(st)) return C_NONE;
  tree lhs = gimple_assign_lhs(st);
  if (!POINTER_TYPE_P(TREE_TYPE(lhs))) return C_NONE;
  enum tree_code code = gimple_assign_rhs_code(st);
  tree rhs = gimple_assign_rhs1(st);
  if (code == POINTER_PLUS_EXPR) return TREE_CODE(gimple_assign_rhs2(st)) == INTEGER_CST ? C_NONE : C_PLUS;
  if (CONVERT_EXPR_CODE_P(code) || code == VIEW_CONVERT_EXPR) {
    if (code == VIEW_CONVERT_EXPR) rhs = TREE_OPERAND(rhs, 0);
    tree ty = TREE_TYPE(rhs);
    if (TREE_CODE(rhs) == INTEGER_CST) return C_NONE;                    /* rule C */
    if (POINTER_TYPE_P(ty)) return C_NONE;                               /* pointer to pointer */
    if (TREE_CODE(rhs) == SSA_NAME && (INTEGRAL_TYPE_P(ty) || (code == VIEW_CONVERT_EXPR && SCALAR_FLOAT_TYPE_P(ty))))
      return C_CONV;
    /* a pointer made from a value that is in memory (a union member, a field): the 32-bit value was stored as such
       and is loaded as it is; no sum exists in a register */
    if (code == VIEW_CONVERT_EXPR && !is_gimple_val(rhs)) return C_NONE;
    error_at(gimple_location(st), "%<fnvnx_addr%>: ADDR32 does not handle this conversion to a pointer (%s from %qT)",
             get_tree_code_name(code), ty);
    return C_NONE;
  }
  return C_NONE;
}

/* a statement that makes an address with an indexed TARGET_MEM_REF: the index is added by the addressing mode */
bool has_indexed_tmr(gimple *st)
{
  for (unsigned i = 0; i < gimple_num_ops(st); i++) {
    tree op = gimple_op(st, i);
    while (op && handled_component_p(op)) op = TREE_OPERAND(op, 0);
    if (op && TREE_CODE(op) == TARGET_MEM_REF && (TMR_INDEX(op) || TMR_INDEX2(op))) return true;
  }
  return false;
}

/* v = asm("" : "=r"(v) : "0"(in)) before/after gsi; returns v */
tree barrier(tree in, gimple_stmt_iterator *gsi, bool after, location_t loc)
{
  tree v = make_ssa_name(TREE_TYPE(in));
  vec<tree, va_gc> *ins = NULL, *outs = NULL;
  vec_safe_push(outs, build_tree_list(build_tree_list(NULL_TREE, build_string(2, "=r")), v));
  vec_safe_push(ins, build_tree_list(build_tree_list(NULL_TREE, build_string(1, "0")), in));
  gasm *a = gimple_build_asm_vec("", ins, outs, NULL, NULL);
  SSA_NAME_DEF_STMT(v) = a;
  gimple_set_location(a, loc);
  if (after) gsi_insert_after(gsi, a, GSI_SAME_STMT);
  else gsi_insert_before(gsi, a, GSI_SAME_STMT);
  update_stmt(a);
  return v;
}

bool opaque(tree t)
{
  if (TREE_CODE(t) != SSA_NAME) return false;
  gimple *d = SSA_NAME_DEF_STMT(t);
  return d && gimple_code(d) == GIMPLE_ASM;
}

bool only_asm_uses(tree t)
{
  if (TREE_CODE(t) != SSA_NAME) return false;
  imm_use_iterator it;
  gimple *u;
  FOR_EACH_IMM_USE_STMT(u, it, t)
    if (gimple_code(u) != GIMPLE_ASM) return false;
  return true;
}

struct addr_pass : gimple_opt_pass {
  addr_pass(gcc::context *c) : gimple_opt_pass(addr_data, c) {}
  unsigned int execute(function *fun) override
  {
    basic_block bb;
    std::vector<gimple *> work;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (has_indexed_tmr(st))
          error_at(gimple_location(st), "%<fnvnx_addr%>: ADDR32 does not handle an indexed target memory reference");
        if (classify(st) != C_NONE) work.push_back(st);
      }
    for (gimple *st : work) {
      gimple_stmt_iterator gsi = gsi_for_stmt(st);
      location_t loc = gimple_location(st);
      if (classify(st) == C_CONV) {
        tree rhs = gimple_assign_rhs1(st), lhs = gimple_assign_lhs(st);
        bool vc = gimple_assign_rhs_code(st) == VIEW_CONVERT_EXPR;
        if (vc) rhs = TREE_OPERAND(rhs, 0);
        /* the value in the pointer's width: C's conversion of an integer to a pointer converts to that width first */
        tree ity = build_nonstandard_integer_type(TYPE_PRECISION(TREE_TYPE(lhs)), 1);
        if (!INTEGRAL_TYPE_P(TREE_TYPE(rhs)) || !useless_type_conversion_p(ity, TREE_TYPE(rhs))) {
          tree conv = INTEGRAL_TYPE_P(TREE_TYPE(rhs)) ? fold_convert(ity, rhs) : build1(VIEW_CONVERT_EXPR, ity, rhs);
          tree t = make_ssa_name(ity);
          gassign *g = gimple_build_assign(t, conv);
          gimple_set_location(g, loc);
          gsi_insert_before(&gsi, g, GSI_SAME_STMT);
          update_stmt(g);
          rhs = t;
        }
        tree v = barrier(rhs, &gsi, false, loc);
        gimple_assign_set_rhs_with_ops(&gsi, NOP_EXPR, v);
        update_stmt(gsi_stmt(gsi));
      } else {                                                      /* C_PLUS */
        tree lhs = gimple_assign_lhs(st);
        if (TREE_CODE(lhs) != SSA_NAME || SSA_NAME_OCCURS_IN_ABNORMAL_PHI(lhs)) {
          error_at(loc, "%<fnvnx_addr%>: ADDR32 cannot route this pointer sum (result is not a plain SSA name)");
          continue;
        }
        tree tmp = make_ssa_name(TREE_TYPE(lhs));
        gimple_assign_set_lhs(st, tmp);
        update_stmt(st);
        tree v = barrier(tmp, &gsi, true, loc);
        gsi_next(&gsi);                                              /* now on the asm */
        gassign *cp = gimple_build_assign(lhs, v);                  /* lhs = v (defines lhs) */
        gimple_set_location(cp, loc);
        gsi_insert_after(&gsi, cp, GSI_SAME_STMT);
        update_stmt(cp);
      }
    }
    /* verification at the last GIMPLE point, and the count */
    unsigned seen = 0, routed = 0;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        cls c = classify(st);
        if (c == C_NONE) continue;
        seen++;
        bool ok;
        if (c == C_CONV) {
          tree rhs = gimple_assign_rhs1(st);
          if (gimple_assign_rhs_code(st) == VIEW_CONVERT_EXPR) rhs = TREE_OPERAND(rhs, 0);
          ok = opaque(rhs);
        } else {
          ok = only_asm_uses(gimple_assign_lhs(st));
        }
        if (ok) routed++;
        else error_at(gimple_location(st), "%<fnvnx_addr%>: ADDR32: a %s is not routed through the barrier",
                      c == C_CONV ? "conversion of an integer to a pointer" : "pointer sum with a run-time offset");
      }
    records[IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fun->decl))] = std::make_pair(seen, routed);
    return 0;
  }
};

/* every other function symbol the unit defines: thunks, aliases */
void finish_unit(void *, void *)
{
  cgraph_node *n;
  FOR_EACH_FUNCTION(n) {
    if (!n->definition && !n->alias) continue;
    if (n->inlined_to) continue;
    const char *name = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(n->decl));
    if (!records.count(name)) records[name] = std::make_pair(0u, 0u);
  }
  if (records.empty()) return;
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.addr,\"\",%%progbits\n");
  for (auto &r : records) fprintf(asm_out_file, "\t.ascii\t\"%s\\t%u\\t%u\\n\"\n", r.first.c_str(), r.second.first, r.second.second);
  fprintf(asm_out_file, "\t.popsection\n");
}

}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) return 1;
  register_pass_info p;
  p.pass = new addr_pass(g);
  p.reference_pass_name = "*warn_function_noreturn";   /* the last GIMPLE pass: `expand` follows it directly */
  p.ref_pass_instance_number = 1;
  p.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &p);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  return 0;
}
