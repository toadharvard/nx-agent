/* fnvnx_dispatch - DISPATCH1: every indirect call of native code goes through "exe code address -> guest entry".
 *
 * Code pointer values in the image stay the exe's addresses (vftable slots, function pointers in data, pointers to
 * member functions, callbacks): they are data of the exe and are never rewritten. So a value called through a
 * pointer is an exe address, not something the processor can jump to. Just before RTL expansion, when the calls of
 * the function are final, every call whose target is not a named function becomes
 *
 *     static unsigned long long site;             one per call site, in the unit's data, 0 at start
 *     w = site;                                    one 64-bit load: low half = the exe address last called here,
 *     entry = (unsigned) w == fp ? w >> 32         high half = its entry (pointers are 32 bits)
 *                                : fnvnx_dispatch(fp, &site, desc);
 *     entry(arguments...)                          the call itself is unchanged
 *
 * fnvnx_dispatch(exe code address, site, descriptor) is the runtime's (L3 LIFT): it returns the entry to call with
 * the call site's own AAPCS signature - a native body, or a bridge into a lifted one - and stores the site's word
 * in one 64-bit store, so a reader never sees the key of one target with the entry of another. An address that is
 * no entry does not return. The hit path is a load, a compare, a branch and a shift.
 *
 * The descriptor is the call's shape for a bridge, a string `conv:ret:arg,arg,...`:
 *   conv   this   the first argument is the object, in ecx on x86 (a member function, MSVC's default)
 *                 (the object is listed among the arguments, first)
 *          fast   __fastcall: the first two integer arguments in ecx, edx
 *          stack  everything on the stack (__cdecl and __stdcall alike: the callee's own `ret N` tells them apart)
 *   ret    v | i (32-bit integer or pointer) | q (64-bit integer) | f | d | a<N> (an N-byte aggregate returned in
 *          registers) | m<N> (returned in memory the caller provides)
 *   arg    i | q | f | d | s<N>g (an N-byte aggregate in general registers or on the stack) | s<N>h (a homogeneous
 *          floating-point aggregate) | s<N>r (passed as a reference to a copy)
 * The convention is the declaration's own: CC1 keeps MSVC's __cdecl / __stdcall / __fastcall / __thiscall as the
 * type attribute fnvnx_cc (shim/include/fnvnx_compat.h), which the fnvnx_names plugin registers.
 *
 * Every indirect call is rewritten; there is no list of exceptions. A call the pass cannot rewrite or describe is
 * an error.
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
#include "attribs.h"
#include "builtins.h"
#include "calls.h"
#include <string>
#include <vector>

int plugin_is_GPL_compatible;

static tree dispatch_decl;
static unsigned site_count;

static tree get_dispatch()
{
  if (!dispatch_decl) {
    tree t = build_function_type_list(ptr_type_node, ptr_type_node, ptr_type_node, const_ptr_type_node, NULL_TREE);
    dispatch_decl = build_fn_decl("fnvnx_dispatch", t);
    DECL_EXTERNAL(dispatch_decl) = 1; TREE_PUBLIC(dispatch_decl) = 1; DECL_ARTIFICIAL(dispatch_decl) = 1;
    TREE_NOTHROW(dispatch_decl) = 1;
  }
  return dispatch_decl;
}

static tree new_site()
{
  char name[64];
  snprintf(name, sizeof name, "fnvnx_site.%u", site_count++);
  tree v = build_decl(UNKNOWN_LOCATION, VAR_DECL, get_identifier(name), long_long_unsigned_type_node);
  TREE_STATIC(v) = 1; TREE_PUBLIC(v) = 0; DECL_ARTIFICIAL(v) = 1; DECL_IGNORED_P(v) = 1; TREE_USED(v) = 1;
  TREE_ADDRESSABLE(v) = 1; DECL_CONTEXT(v) = NULL_TREE;
  DECL_INITIAL(v) = NULL_TREE;                       /* .bss: 0, and no exe code address is 0 */
  layout_decl(v, 0);
  varpool_node::finalize_decl(v);
  return v;
}

/* ---- the call's shape */

static bool hfa(tree t, tree *elt, int *n)
{
  if (SCALAR_FLOAT_TYPE_P(t)) {
    if (*elt && TYPE_MAIN_VARIANT(*elt) != TYPE_MAIN_VARIANT(t)) return false;
    *elt = t; return ++*n <= 4;
  }
  if (TREE_CODE(t) == ARRAY_TYPE && TYPE_DOMAIN(t) && TYPE_MAX_VALUE(TYPE_DOMAIN(t)) && tree_fits_shwi_p(TYPE_MAX_VALUE(TYPE_DOMAIN(t)))) {
    for (HOST_WIDE_INT i = 0; i <= tree_to_shwi(TYPE_MAX_VALUE(TYPE_DOMAIN(t))); i++)
      if (!hfa(TREE_TYPE(t), elt, n)) return false;
    return true;
  }
  if (TREE_CODE(t) != RECORD_TYPE) return false;
  for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
    if (TREE_CODE(f) == FIELD_DECL && !hfa(TREE_TYPE(f), elt, n)) return false;
  return true;
}

static bool shape(tree t, bool ret, tree fntype, std::string &out, location_t loc)
{
  if (VOID_TYPE_P(t)) { out += "v"; return ret; }
  if (INTEGRAL_TYPE_P(t) || POINTER_TYPE_P(t) || TREE_CODE(t) == OFFSET_TYPE || TREE_CODE(t) == NULLPTR_TYPE) {
    unsigned p = TREE_CODE(t) == OFFSET_TYPE ? POINTER_SIZE : TYPE_PRECISION(t);
    if (p > 64) { error_at(loc, "DISPATCH1: a %u-bit integer in an indirect call", p); return false; }
    out += p > 32 ? "q" : "i"; return true;
  }
  if (SCALAR_FLOAT_TYPE_P(t)) {
    if (TYPE_PRECISION(t) != 32 && TYPE_PRECISION(t) != 64) { error_at(loc, "DISPATCH1: a floating type of %d bits in an indirect call", (int)TYPE_PRECISION(t)); return false; }
    out += TYPE_PRECISION(t) == 32 ? "f" : "d"; return true;
  }
  if (!AGGREGATE_TYPE_P(t) && TREE_CODE(t) != COMPLEX_TYPE && TREE_CODE(t) != VECTOR_TYPE)
    { error_at(loc, "DISPATCH1: type %qT in an indirect call has no shape", t); return false; }
  if (!TYPE_SIZE_UNIT(t) || !tree_fits_uhwi_p(TYPE_SIZE_UNIT(t))) { error_at(loc, "DISPATCH1: type %qT has no constant size", t); return false; }
  std::string n = std::to_string((unsigned long)tree_to_uhwi(TYPE_SIZE_UNIT(t)));
  if (ret) { out += (aggregate_value_p(t, fntype) ? "m" : "a") + n; return true; }
  tree elt = NULL_TREE; int cnt = 0;
  out += "s" + n + (TREE_ADDRESSABLE(t) || tree_to_uhwi(TYPE_SIZE_UNIT(t)) > 16 ? "r" : AGGREGATE_TYPE_P(t) && hfa(t, &elt, &cnt) && cnt ? "h" : "g");
  return true;
}

static tree descriptor(gcall *call)
{
  location_t loc = gimple_location(call);
  static bool checked;
  if (!checked) {
    checked = true;
    if (!lookup_attribute_spec(get_identifier("fnvnx_cc")))
      error("DISPATCH1: the fnvnx_names plugin is not loaded: the calling conventions of the declarations are lost");
  }
  tree ft = gimple_call_fntype(call);
  if (!ft) { error_at(loc, "DISPATCH1: an indirect call without a function type"); return NULL_TREE; }
  const char *conv = TREE_CODE(ft) == METHOD_TYPE ? "this" : "stack";
  if (tree a = lookup_attribute("fnvnx_cc", TYPE_ATTRIBUTES(ft))) {
    const char *v = TREE_STRING_POINTER(TREE_VALUE(TREE_VALUE(a)));
    conv = !strcmp(v, "fast") ? "fast" : !strcmp(v, "this") ? "this" : "stack";
  }
  std::string d = std::string(conv) + ":";
  if (!shape(TREE_TYPE(ft), true, ft, d, loc)) return NULL_TREE;
  d += ":";
  /* A parameter's type is the function type's (a class that is passed as a reference to a copy is still the
   * class there: the exe takes it by value on its stack); an argument past the declared ones (`...`) has the type
   * of the value passed. */
  tree parm = TYPE_ARG_TYPES(ft);
  for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
    if (i) d += ",";
    tree t = parm && parm != void_list_node ? TREE_VALUE(parm) : TREE_TYPE(gimple_call_arg(call, i));
    if (parm && parm != void_list_node) parm = TREE_CHAIN(parm);
    if (!shape(t, false, ft, d, loc)) return NULL_TREE;
  }
  return build_string_literal(d.size() + 1, d.c_str());
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
  tree desc = descriptor(call);
  if (!desc) return;
  location_t loc = gimple_location(st);
  tree site = new_site();
  tree u64 = long_long_unsigned_type_node, u32 = unsigned_type_node;
  gimple_stmt_iterator gsi = gsi_for_stmt(st);
  auto before = [&](gimple *g) { gimple_set_location(g, loc); gsi_insert_before(&gsi, g, GSI_SAME_STMT); };

  /* head: k = (unsigned) fp; w = site; if ((unsigned) w == k) */
  tree k = make_ssa_name(u32), w = make_ssa_name(u64), lo = make_ssa_name(u32);
  before(gimple_build_assign(k, NOP_EXPR, fp));
  before(gimple_build_assign(w, build2(MEM_REF, u64, build_fold_addr_expr(site), build_int_cst(build_pointer_type(u64), 0))));
  before(gimple_build_assign(lo, NOP_EXPR, w));
  gcond *cond = gimple_build_cond(EQ_EXPR, lo, k, NULL_TREE, NULL_TREE);
  before(cond);

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

  /* hit: e1 = (unsigned) (w >> 32) */
  tree hi = make_ssa_name(u64), e1 = make_ssa_name(u32), e2p = make_ssa_name(ptr_type_node), e2 = make_ssa_name(u32),
       e = make_ssa_name(u32);
  gimple_stmt_iterator hgsi = gsi_start_bb(hit);
  gimple *g = gimple_build_assign(hi, RSHIFT_EXPR, w, build_int_cst(unsigned_type_node, 32));
  gimple_set_location(g, loc); gsi_insert_after(&hgsi, g, GSI_NEW_STMT);
  g = gimple_build_assign(e1, NOP_EXPR, hi);
  gimple_set_location(g, loc); gsi_insert_after(&hgsi, g, GSI_NEW_STMT);
  /* miss: e2 = fnvnx_dispatch(fp, &site, desc) */
  gimple_stmt_iterator mgsi = gsi_start_bb(miss);
  tree fpv = make_ssa_name(ptr_type_node);
  g = gimple_build_assign(fpv, NOP_EXPR, fp);
  gimple_set_location(g, loc); gsi_insert_after(&mgsi, g, GSI_NEW_STMT);
  gcall *d = gimple_build_call(get_dispatch(), 3, fpv, build_fold_addr_expr(site), desc);
  gimple_call_set_lhs(d, e2p); gimple_set_location(d, loc);
  gsi_insert_after(&mgsi, d, GSI_NEW_STMT);
  g = gimple_build_assign(e2, NOP_EXPR, e2p);
  gimple_set_location(g, loc); gsi_insert_after(&mgsi, g, GSI_NEW_STMT);
  /* tail: e = PHI <e1 (hit), e2 (miss)>; the call through it */
  gphi *phi = create_phi_node(e, tail);
  add_phi_arg(phi, e1, hit_tail, loc); add_phi_arg(phi, e2, miss_tail, loc);
  tree target = make_ssa_name(TREE_TYPE(fp));
  gsi = gsi_for_stmt(st);
  before(gimple_build_assign(target, NOP_EXPR, e));
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
  if (!plugin_default_version_check(ver, &gcc_version)) {
    error("%<fnvnx_dispatch%>: built for GCC %s, loaded by %s", gcc_version.basever, ver->basever);
    return 1;
  }
  if (info->argc) {   /* no silent modes: this plugin has no options */
    error("%<fnvnx_dispatch%>: unknown plugin argument %qs", info->argv[0].key);
    return 1;
  }
  register_pass_info p;
  p.pass = new dispatch_pass(g);
  p.reference_pass_name = "optimized";
  p.ref_pass_instance_number = 1;
  p.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &p);
  return 0;
}
