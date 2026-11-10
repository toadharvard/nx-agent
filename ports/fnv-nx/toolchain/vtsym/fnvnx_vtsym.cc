/* fnvnx_vtsym - VTSYM1: every vtable address point has its own symbol and its own relocation.
 *
 * GCC addresses a class's sub-tables as the vtable's base plus a constant: under -fPIC one GOT load of `_ZTV<C>` and
 * an immediate `add` per address point, the primary one included. The link (L1 CONTRACT) retargets vptr stores to
 * the exe's vftables per sub-table, and can only do that on relocations. So:
 *
 *   - in code, every `&_ZTV<C> + off` becomes `&_ZTV<C>.fnvnx_ap<off>`, an external symbol of its own (one GOT
 *     relocation per address point, no immediate);
 *   - where `_ZTV<C>` is defined, each address point is defined next to it:
 *         _ZTV<C>.fnvnx_ap<off> = _ZTV<C> + off      (same binding, same section, so the same COMDAT group)
 *     An address point is the entry after a sub-table's RTTI slot, read from the table's own initializer, so the
 *     set is the compiler's, the same in every unit that emits the table.
 *
 * A vtable reference of any other shape is an error (no silent fallback).
 * Data initializers (VTTs, construction vtables) already carry `symbol + addend` relocations and are not changed.
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
#include "gimple-walk.h"
#include "stringpool.h"
#include "cgraph.h"
#include "varasm.h"
#include "output.h"
#include "diagnostic-core.h"
#include "fold-const.h"
#include "tree-cfg.h"
#include "ssa.h"
#include "gimple-ssa.h"
#include "tree-ssa-operands.h"
#include <map>
#include <string>

int plugin_is_GPL_compatible;

static std::map<std::string, tree> ap_decls;

static bool is_vtable(tree v)
{
  return v && VAR_P(v) && DECL_VIRTUAL_P(v) && DECL_ASSEMBLER_NAME_SET_P(v)
         && !strncmp(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(v)), "_ZTV", 4);
}

static std::string ap_name(tree vt, HOST_WIDE_INT off)
{
  return std::string(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(vt))) + ".fnvnx_ap" + std::to_string((long long)off);
}

static tree ap_decl(tree vt, HOST_WIDE_INT off)
{
  std::string n = ap_name(vt, off);
  tree &d = ap_decls[n];
  if (!d) {
    d = build_decl(UNKNOWN_LOCATION, VAR_DECL, get_identifier(n.c_str()), TREE_TYPE(TREE_TYPE(vt)));
    SET_DECL_ASSEMBLER_NAME(d, get_identifier(n.c_str()));
    DECL_EXTERNAL(d) = 1; TREE_PUBLIC(d) = 1; DECL_ARTIFICIAL(d) = 1; DECL_IGNORED_P(d) = 1; TREE_READONLY(d) = 1;
    DECL_VISIBILITY(d) = DECL_VISIBILITY(vt); DECL_VISIBILITY_SPECIFIED(d) = DECL_VISIBILITY_SPECIFIED(vt);
    TREE_USED(d) = 1;
  }
  /* the table itself is no longer named in the code: keep it written out where it is defined */
  mark_decl_referenced(vt);
  if (varpool_node *n = varpool_node::get(vt)) n->force_output = 1;
  return d;
}

/* *tp is an ADDR_EXPR: when it is `&vtable + off`, return the vtable and the byte offset */
static tree vt_ref(tree addr, HOST_WIDE_INT *off)
{
  tree op = TREE_OPERAND(addr, 0);
  if (TREE_CODE(op) == MEM_REF && TREE_CODE(TREE_OPERAND(op, 0)) == ADDR_EXPR
      && is_vtable(TREE_OPERAND(TREE_OPERAND(op, 0), 0)) && tree_fits_shwi_p(TREE_OPERAND(op, 1))) {
    *off = tree_to_shwi(TREE_OPERAND(op, 1));
    return TREE_OPERAND(TREE_OPERAND(op, 0), 0);
  }
  if (TREE_CODE(op) == ARRAY_REF && is_vtable(TREE_OPERAND(op, 0)) && tree_fits_shwi_p(TREE_OPERAND(op, 1))) {
    *off = tree_to_shwi(TREE_OPERAND(op, 1)) * int_size_in_bytes(TREE_TYPE(op));
    return TREE_OPERAND(op, 0);
  }
  if (is_vtable(op)) { *off = -1; return op; }
  return NULL_TREE;
}

static tree walk_op(tree *tp, int *walk_subtrees, void *data)
{
  if (TREE_CODE(*tp) == ADDR_EXPR) {
    HOST_WIDE_INT off;
    tree vt = vt_ref(*tp, &off);
    if (vt) {
      if (off < 0)
        error("VTSYM1: a reference to the base of vtable %qD, not to an address point", vt);
      else {
        tree n = build1(ADDR_EXPR, TREE_TYPE(*tp), ap_decl(vt, off));
        recompute_tree_invariant_for_addr_expr(n);
        *tp = n;
        *(bool *)((walk_stmt_info *)data)->info = true;
      }
      *walk_subtrees = 0;
    }
  } else if (VAR_P(*tp) && is_vtable(*tp))
    error("VTSYM1: vtable %qD used other than by an address point", *tp);
  return NULL_TREE;
}

namespace {
const pass_data vtsym_data = { GIMPLE_PASS, "fnvnx_vtsym", OPTGROUP_NONE, TV_NONE, PROP_cfg, 0, 0, 0, 0 };
struct vtsym_pass : gimple_opt_pass {
  vtsym_pass(gcc::context *c) : gimple_opt_pass(vtsym_data, c) {}
  unsigned int execute(function *fun) override
  {
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        bool changed = false;
        gimple *st = gsi_stmt(gsi);
        /* `&_ZTV<C> + off` as a pointer addition (the form the front end leaves) */
        if (is_gimple_assign(st) && gimple_assign_rhs_code(st) == POINTER_PLUS_EXPR
            && TREE_CODE(gimple_assign_rhs1(st)) == ADDR_EXPR && is_vtable(TREE_OPERAND(gimple_assign_rhs1(st), 0))
            && tree_fits_shwi_p(gimple_assign_rhs2(st))) {
          tree a = build1(ADDR_EXPR, TREE_TYPE(gimple_assign_lhs(st)),
                          ap_decl(TREE_OPERAND(gimple_assign_rhs1(st), 0), tree_to_shwi(gimple_assign_rhs2(st))));
          recompute_tree_invariant_for_addr_expr(a);
          gimple_assign_set_rhs_from_tree(&gsi, a);
          update_stmt(gsi_stmt(gsi));
          continue;
        }
        walk_stmt_info wi; memset(&wi, 0, sizeof wi); wi.info = &changed;
        walk_gimple_op(gsi_stmt(gsi), walk_op, &wi);
        if (changed) update_stmt(gsi_stmt(gsi));
      }
    return 0;
  }
};
}

/* where a vtable is written out, define its address points */
static void finish_unit(void *, void *)
{
  varpool_node *n;
  FOR_EACH_DEFINED_VARIABLE(n) {
    tree vt = n->decl;
    if (!is_vtable(vt) || !TREE_ASM_WRITTEN(vt) || !DECL_INITIAL(vt) || TREE_CODE(DECL_INITIAL(vt)) != CONSTRUCTOR) continue;
    const char *base = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(vt));
    HOST_WIDE_INT esz = int_size_in_bytes(TREE_TYPE(TREE_TYPE(vt)));
    unsigned HOST_WIDE_INT ix; tree idx, val; bool after_rtti = false;
    FOR_EACH_CONSTRUCTOR_ELT(CONSTRUCTOR_ELTS(DECL_INITIAL(vt)), ix, idx, val) {
      HOST_WIDE_INT i = idx && tree_fits_shwi_p(idx) ? tree_to_shwi(idx) : (HOST_WIDE_INT)ix;
      if (after_rtti) {
        std::string s = ap_name(vt, i * esz);
        fprintf(asm_out_file, "\t%s\t%s\n\t.type\t%s, %%object\n\t.set\t%s, %s+%ld\n",
                DECL_WEAK(vt) ? ".weak" : TREE_PUBLIC(vt) ? ".globl" : ".local", s.c_str(), s.c_str(), s.c_str(), base,
                (long)(i * esz));
        if (DECL_VISIBILITY(vt) == VISIBILITY_HIDDEN) fprintf(asm_out_file, "\t.hidden\t%s\n", s.c_str());
      }
      tree v = val;
      STRIP_NOPS(v);
      after_rtti = TREE_CODE(v) == ADDR_EXPR && VAR_P(TREE_OPERAND(v, 0)) && DECL_ASSEMBLER_NAME_SET_P(TREE_OPERAND(v, 0))
                   && !strncmp(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(TREE_OPERAND(v, 0))), "_ZTI", 4);
    }
  }
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) return 1;
  register_pass_info pi;
  pi.pass = new vtsym_pass(g);
  /* late: the IPA passes (devirtualization, type-change detection) read vptr stores in the front end's form; GIMPLE
     keeps `&_ZTV<C> + off` an invariant to the end, the base-plus-immediate form is made at RTL expansion */
  pi.reference_pass_name = "optimized";
  pi.ref_pass_instance_number = 1;
  pi.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pi);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  return 0;
}
