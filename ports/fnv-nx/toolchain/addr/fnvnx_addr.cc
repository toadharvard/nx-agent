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
 * Constants (rule C). An INTEGER_CST converted to a pointer is not routed: a constant is a finished 32-bit value, it has no
 * run-time sum to take apart. A constant displacement from a pointer is not routed when its magnitude, as a signed 32-bit
 * number, is below LIMIT = 2^16 (0x10000); anything else is routed (class PLUS below). The bound is the one the hardware
 * gives: the displacement the expander folds into an addressing mode is an immediate of at most 4095 * 16 = 65520 bytes
 * (the largest A64 scaled load/store immediate, `ldr q`) or 504 (`ldp`) or -256 (`ldur`), so what stays unrouted is a
 * `[zext(p), #d]` with |d| < 2^16, which equals the wrapped sum p + d unless p + d crosses 0 or 2^32. A larger constant is
 * not folded into the addressing mode: GCC computes `uxtw x0, w0; add x0, x0, #d` (a 64-bit sum, found by compiling
 * `p->f` at offset 0x20000), which is why a displacement of 2^16 or more is routed: the 32-bit add `add w0, w0, #d`
 * that follows the barrier wraps as x86 does. What remains is a window of 2^16 bytes at each end of the address space:
 * the checked fact LAYOUT of gate_addr.py says that every byte of [0, 2^16) and [2^32 - 2^16, 2^32) is a guard region
 * (no access, no object), so an address p + d with |d| < 2^16 that crosses 0 or 2^32 starts in or ends in a guard (it
 * would fault on x86 and it faults here: above 2^32 nothing is mapped). The two bounds are one number, LIMIT, here
 * and as GUARD in gate_addr.py.
 *   The displacement of a memory reference is the constant part of base pointer + MEM_REF offset + the path through
 * fields and constant array indexes (get_inner_reference); a run-time array index through a pointer base is a
 * run-time displacement. A reference through a pointer (MEM_REF or non-indexed TARGET_MEM_REF whose base is not the
 * address of a declaration) with a run-time displacement or a constant one of 2^16 or more is rewritten to
 * MEM_REF[q, 0] with q = pointer p+ displacement, a POINTER_PLUS routed like every other (class PLUS). A bit-field or
 * BIT_FIELD_REF access with such a displacement is a compile error (no address of it can be formed).
 *
 * Totality. Every assignment whose result is a pointer and whose operand computes it from an integer is of class
 * CONV or PLUS or has a constant operand; any other form that makes a pointer from a non-pointer value
 * (a TARGET_MEM_REF with an index, a result that cannot be rewritten) is a compile error. Memory operands that
 * go through a pointer are covered by the displacement rule above; a variable index into a declared array (the base is
 * a declaration: a global, a local) is in bounds by C++ semantics (Assumption ASM-006: the exe makes no wild accesses;
 * array indices in bounds).
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

/* the bound of rule C: a constant displacement whose magnitude is below LIMIT (as a signed 32-bit number) is not routed;
   it is the guard size of gate_addr.py */
const HOST_WIDE_INT LIMIT = 0x10000;

bool large_const(tree cst)
{
  HOST_WIDE_INT v = (int32_t)TREE_INT_CST_LOW(cst);
  return v >= LIMIT || v <= -LIMIT;
}

/* a memory reference that goes through a pointer: the pointer, the constant displacement in bytes, the run-time part of
   the displacement (or NULL), whether the access is a whole object at a byte position (not a bit-field), the alias
   pointer type of the base */
struct thru { tree ptr; HOST_WIDE_INT k; tree var; bool simple; tree alias; };

bool through_pointer(tree t, thru &r)
{
  if (!t || !(handled_component_p(t) || TREE_CODE(t) == MEM_REF || TREE_CODE(t) == TARGET_MEM_REF)) return false;
  poly_int64 bitsize, bitpos;
  tree offset;
  machine_mode mode;
  int uns, rev, vol;
  tree base = get_inner_reference(t, &bitsize, &bitpos, &offset, &mode, &uns, &rev, &vol);
  HOST_WIDE_INT off0, bp, bs;
  if (TREE_CODE(base) == MEM_REF) {
    r.ptr = TREE_OPERAND(base, 0);
    poly_int64 po = mem_ref_offset(base).force_shwi();
    if (!po.is_constant(&off0)) return false;
    r.alias = TREE_TYPE(TREE_OPERAND(base, 1));
  } else if (TREE_CODE(base) == TARGET_MEM_REF && !TMR_INDEX(base) && !TMR_INDEX2(base) && TMR_BASE(base)) {
    r.ptr = TMR_BASE(base);
    off0 = TMR_OFFSET(base) ? (int32_t)TREE_INT_CST_LOW(TMR_OFFSET(base)) : 0;
    r.alias = TREE_TYPE(TMR_OFFSET(base) ? TMR_OFFSET(base) : build_int_cst(ptr_type_node, 0));
  } else
    return false;
  if (TREE_CODE(r.ptr) == ADDR_EXPR) return false;      /* the address of a declaration: a link-time or frame address */
  if (!bitpos.is_constant(&bp) || !bitsize.is_constant(&bs)) bp = 0, bs = -1;
  tree ty = TREE_TYPE(t);
  r.k = off0 + (bp >> 3);
  r.var = offset;
  r.simple = bs >= 0 && (bp & 7) == 0 && TYPE_SIZE(ty) && tree_fits_uhwi_p(TYPE_SIZE(ty)) && (HOST_WIDE_INT)tree_to_uhwi(TYPE_SIZE(ty)) == bs &&
             TREE_CODE(t) != BIT_FIELD_REF && !(TREE_CODE(t) == COMPONENT_REF && DECL_BIT_FIELD(TREE_OPERAND(t, 1))) && !rev;
  return true;
}

/* a reference that must be rewritten: a run-time displacement, or a constant one of LIMIT or more */
bool needs_route(const thru &r)
{
  return r.var != NULL_TREE || r.k >= LIMIT || r.k <= -LIMIT;
}

/* the class of a statement; an unhandled way of making a pointer is an error */
cls classify(gimple *st)
{
  if (!is_gimple_assign(st)) return C_NONE;
  tree lhs = gimple_assign_lhs(st);
  if (!POINTER_TYPE_P(TREE_TYPE(lhs))) return C_NONE;
  enum tree_code code = gimple_assign_rhs_code(st);
  tree rhs = gimple_assign_rhs1(st);
  if (code == POINTER_PLUS_EXPR) {
    tree off = gimple_assign_rhs2(st);
    return TREE_CODE(off) == INTEGER_CST && !large_const(off) ? C_NONE : C_PLUS;
  }
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

/* q = ptr p+ (var + k), as a gimple value inserted before gsi */
tree address_sum(const thru &r, gimple_stmt_iterator *gsi)
{
  tree off = build_int_cst(sizetype, r.k);
  if (r.var) off = fold_build2(PLUS_EXPR, sizetype, fold_convert(sizetype, r.var), off);
  tree e = fold_build2(POINTER_PLUS_EXPR, TREE_TYPE(r.ptr), r.ptr, off);
  gimple_seq seq = NULL;
  tree q = force_gimple_operand(e, &seq, true, NULL_TREE);
  gsi_insert_seq_before(gsi, seq, GSI_SAME_STMT);
  return q;
}

void route_refs(gimple *st)
{
  gimple_stmt_iterator gsi = gsi_for_stmt(st);
  location_t loc = gimple_location(st);
  switch (gimple_code(st)) {
    case GIMPLE_ASSIGN: case GIMPLE_CALL: case GIMPLE_RETURN: break;
    default: return;
  }
  for (unsigned i = 0; i < gimple_num_ops(st); i++) {
    tree *op = gimple_op_ptr(st, i);
    if (!op || !*op) continue;
    tree t = *op;
    if (TREE_CODE(t) == ADDR_EXPR) {
      thru r;
      if (i != 1 || !is_gimple_assign(st) || !through_pointer(TREE_OPERAND(t, 0), r) || !needs_route(r)) continue;
      if (TREE_CODE(gimple_assign_lhs(st)) != SSA_NAME) continue;
      /* &ref: the same sum, as a pointer sum */
      tree q = address_sum(r, &gsi);
      gimple_assign_set_rhs_from_tree(&gsi, q);
      update_stmt(gsi_stmt(gsi));
      continue;
    }
    thru r;
    if (!through_pointer(t, r) || !needs_route(r)) continue;
    if (!r.simple) {
      error_at(loc, "%<fnvnx_addr%>: ADDR32 does not handle a bit-field access through a pointer at a run-time or large displacement");
      continue;
    }
    tree q = address_sum(r, &gsi);
    tree nr = build2(MEM_REF, TREE_TYPE(t), q, build_int_cst(r.alias, 0));
    TREE_THIS_VOLATILE(nr) = TREE_THIS_VOLATILE(t);
    TREE_SIDE_EFFECTS(nr) = TREE_SIDE_EFFECTS(t);
    TREE_THIS_NOTRAP(nr) = 0;
    *op = nr;
    update_stmt(st);
  }
}

struct addr_pass : gimple_opt_pass {
  addr_pass(gcc::context *c) : gimple_opt_pass(addr_data, c) {}
  unsigned int execute(function *fun) override
  {
    basic_block bb;
    std::vector<gimple *> work;
    /* memory references through a pointer with a run-time displacement or a constant one of LIMIT or more: the address
       becomes an explicit pointer sum, which class PLUS routes below */
    std::vector<gimple *> stmts;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) stmts.push_back(gsi_stmt(gsi));
    for (gimple *st : stmts) route_refs(st);
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
        if (is_gimple_assign(st) || is_gimple_call(st) || gimple_code(st) == GIMPLE_RETURN)
          for (unsigned i = 0; i < gimple_num_ops(st); i++) {
            thru r;
            tree o = gimple_op(st, i);
            if (o && TREE_CODE(o) == ADDR_EXPR) o = TREE_OPERAND(o, 0);
            if (o && through_pointer(o, r) && needs_route(r))
              error_at(gimple_location(st), "%<fnvnx_addr%>: ADDR32: a memory reference through a pointer at a run-time or large displacement is not routed");
          }
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
  /* always written, empty for a unit without a function: the section is the evidence that the pass ran */
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
