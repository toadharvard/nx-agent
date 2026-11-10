/* fnvnx_dispatch - DISPATCH1: every indirect call of native code goes through "exe code address -> guest entry".
 *
 * Code pointer values in the image stay the exe's addresses (vftable slots, function pointers in data, pointers to
 * member functions, callbacks): they are data of the exe and are never rewritten. So a value called through a
 * pointer is an exe address, not something the processor can jump to. Just before RTL expansion, when the calls of
 * the function are final, every call whose target is not a named function becomes
 *
 *     static void *site[2];                                   one per call site, in the unit's data, zero at start
 *     entry = site[0] == fp ? site[1] : fnvnx_dispatch(fp, site);
 *     entry(arguments...)                                     the call itself is unchanged
 *
 * fnvnx_dispatch(exe code address, site) is the runtime's (L3 LIFT): it returns the entry to call with the call
 * site's own AAPCS signature - a native body, or a bridge into a lifted one - and fills the site's two words. The
 * hit path is a load, a compare, a branch and a load.
 *
 * Every indirect call is rewritten; there is no list of exceptions. A call the pass cannot rewrite is an error.
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
#include "tree-phinodes.h"
#include "cfghooks.h"
#include "tree-cfg.h"
#include "cgraph.h"
#include "stringpool.h"
#include "stor-layout.h"
#include "diagnostic-core.h"
#include "tree-into-ssa.h"
#include "dominance.h"
#include "cfgloop.h"
#include <vector>

int plugin_is_GPL_compatible;

static tree dispatch_decl, site_type;
static unsigned site_count;

static tree get_dispatch()
{
  if (!dispatch_decl) {
    tree t = build_function_type_list(ptr_type_node, ptr_type_node, ptr_type_node, NULL_TREE);
    dispatch_decl = build_fn_decl("fnvnx_dispatch", t);
    DECL_EXTERNAL(dispatch_decl) = 1; TREE_PUBLIC(dispatch_decl) = 1; DECL_ARTIFICIAL(dispatch_decl) = 1;
    TREE_NOTHROW(dispatch_decl) = 1;
    site_type = build_array_type(ptr_type_node, build_index_type(size_int(1)));
  }
  return dispatch_decl;
}

static tree new_site()
{
  get_dispatch();
  char name[64];
  snprintf(name, sizeof name, "fnvnx_site.%u", site_count++);
  tree v = build_decl(UNKNOWN_LOCATION, VAR_DECL, get_identifier(name), site_type);
  TREE_STATIC(v) = 1; TREE_PUBLIC(v) = 0; DECL_ARTIFICIAL(v) = 1; DECL_IGNORED_P(v) = 1; TREE_USED(v) = 1;
  TREE_ADDRESSABLE(v) = 1; DECL_CONTEXT(v) = NULL_TREE;
  DECL_INITIAL(v) = NULL_TREE;                       /* .bss: both words zero, and no exe code address is 0 */
  layout_decl(v, 0);
  varpool_node::finalize_decl(v);
  return v;
}

static bool indirect_call(gimple *st)
{
  if (!is_gimple_call(st) || gimple_call_internal_p(st)) return false;
  tree fn = gimple_call_fn(st);
  if (!fn) return false;
  return !(TREE_CODE(fn) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(fn, 0)) == FUNCTION_DECL);
}

static void rewrite(function *fun, gimple *st)
{
  gcall *call = as_a<gcall *>(st);
  tree fn = gimple_call_fn(call);
  tree fp = TREE_CODE(fn) == OBJ_TYPE_REF ? OBJ_TYPE_REF_EXPR(fn) : fn;
  if (TREE_CODE(fp) != SSA_NAME && !is_gimple_min_invariant(fp))
    { error_at(gimple_location(st), "DISPATCH1: an indirect call whose target is not a value"); return; }
  location_t loc = gimple_location(st);
  tree site = new_site();
  HOST_WIDE_INT psz = POINTER_SIZE / BITS_PER_UNIT;
  gimple_stmt_iterator gsi = gsi_for_stmt(st);

  /* head: fpv = (void *) fp; key = site[0]; if (key == fpv) */
  tree fpv = make_ssa_name(ptr_type_node), key = make_ssa_name(ptr_type_node);
  gimple *g = gimple_build_assign(fpv, NOP_EXPR, fp);
  gimple_set_location(g, loc); gsi_insert_before(&gsi, g, GSI_SAME_STMT);
  g = gimple_build_assign(key, build2(MEM_REF, ptr_type_node, build_fold_addr_expr(site), build_int_cst(ptr_type_node, 0)));
  gimple_set_location(g, loc); gsi_insert_before(&gsi, g, GSI_SAME_STMT);
  gcond *cond = gimple_build_cond(EQ_EXPR, key, fpv, NULL_TREE, NULL_TREE);
  gimple_set_location(cond, loc); gsi_insert_before(&gsi, cond, GSI_SAME_STMT);

  basic_block head = gimple_bb(st);
  edge fall = split_block(head, cond);               /* head | tail (the call) */
  basic_block tail = fall->dest;
  basic_block hit = create_empty_bb(head), miss = create_empty_bb(hit);
  if (current_loops) { add_bb_to_loop(hit, head->loop_father); add_bb_to_loop(miss, head->loop_father); }
  remove_edge(fall);
  edge e_hit = make_edge(head, hit, EDGE_TRUE_VALUE), e_miss = make_edge(head, miss, EDGE_FALSE_VALUE);
  e_hit->probability = profile_probability::very_likely(); e_miss->probability = e_hit->probability.invert();
  edge hit_tail = make_edge(hit, tail, EDGE_FALLTHRU), miss_tail = make_edge(miss, tail, EDGE_FALLTHRU);
  hit->count = e_hit->count(); miss->count = e_miss->count();

  /* hit: e1 = site[1] */
  tree e1 = make_ssa_name(ptr_type_node), e2 = make_ssa_name(ptr_type_node), e = make_ssa_name(ptr_type_node);
  gimple_stmt_iterator hi = gsi_start_bb(hit);
  g = gimple_build_assign(e1, build2(MEM_REF, ptr_type_node, build_fold_addr_expr(site), build_int_cst(ptr_type_node, psz)));
  gimple_set_location(g, loc); gsi_insert_after(&hi, g, GSI_NEW_STMT);
  /* miss: e2 = fnvnx_dispatch(fpv, &site) */
  gimple_stmt_iterator mi = gsi_start_bb(miss);
  gcall *d = gimple_build_call(get_dispatch(), 2, fpv, build_fold_addr_expr(site));
  gimple_call_set_lhs(d, e2); gimple_set_location(d, loc);
  gsi_insert_after(&mi, d, GSI_NEW_STMT);
  /* tail: e = PHI <e1 (hit), e2 (miss)>; the call through it */
  gphi *phi = create_phi_node(e, tail);
  add_phi_arg(phi, e1, hit_tail, loc); add_phi_arg(phi, e2, miss_tail, loc);
  tree target = make_ssa_name(TREE_TYPE(fp));
  gsi = gsi_for_stmt(st);
  g = gimple_build_assign(target, NOP_EXPR, e);
  gimple_set_location(g, loc); gsi_insert_before(&gsi, g, GSI_SAME_STMT);
  gimple_call_set_fn(call, target);
  update_stmt(call);
}

namespace {
const pass_data dispatch_data = { GIMPLE_PASS, "fnvnx_dispatch", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0,
                                  TODO_update_ssa_only_virtuals | TODO_cleanup_cfg };
struct dispatch_pass : gimple_opt_pass {
  dispatch_pass(gcc::context *c) : gimple_opt_pass(dispatch_data, c) {}
  unsigned int execute(function *fun) override
  {
    std::vector<gimple *> calls;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi))
        if (indirect_call(gsi_stmt(gsi))) calls.push_back(gsi_stmt(gsi));
    if (calls.empty()) return 0;
    for (size_t i = 0; i < calls.size(); i++) rewrite(fun, calls[i]);
    free_dominance_info(CDI_DOMINATORS);
    mark_virtual_operands_for_renaming(fun);
    return 0;
  }
};
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) return 1;
  register_pass_info p;
  p.pass = new dispatch_pass(g);
  p.reference_pass_name = "optimized";
  p.ref_pass_instance_number = 1;
  p.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &p);
  return 0;
}
