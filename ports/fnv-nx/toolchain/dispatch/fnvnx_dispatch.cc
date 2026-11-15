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
 * Every indirect call is rewritten, except a host-ABI call (HOST1, below); there is no list of exceptions. A call the
 * pass cannot rewrite or describe is an error.
 *
 * HOST1: a host address is not an exe code address. The platform's host API (FnvnxHostApi) holds the addresses of the
 * host's functions in slots; their type is `fnvnx_hslot`, a uint64_t that carries the type attribute `fnvnx_host`
 * (registered here). They are not keys of the dispatcher: calling one through fnvnx_dispatch would be a violation of
 * the dispatcher's domain. So, in the function, at the last GIMPLE:
 *   1. An indirect call is a host-ABI call iff its callee value comes, through conversions only (NOP, CONVERT,
 *      VIEW_CONVERT, a plain copy), from a load of a field whose type carries fnvnx_host. It is not rewritten.
 *   2. Containment, which closes the class: it is an error if a value loaded from such a field, or any conversion
 *      of it, is used other than as the callee of an indirect call (a store, an argument, a return, a comparison,
 *      arithmetic, a PHI, an asm operand, a switch); if anything is stored into such a field; if the address of such a
 *      field is taken; and if an aggregate that contains such a field is copied, loaded, stored, passed or returned
 *      by value (its slots would travel as plain data). So a host address never reaches fnvnx_dispatch, and the
 *      callee of a host-ABI call is always a value the host wrote.
 * The unit's section `.fnvnx.dispatch` (not loaded) has one line `symbol<TAB>rewritten<TAB>host` for every function with
 * an indirect call: the number of call sites rewritten and the number of host-ABI calls.
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
#include "output.h"
#include "gimple-walk.h"
#include "ssa-iterators.h"
#include "tree-dfa.h"
#include <map>
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

/* ---- HOST1: the host slot type, host-ABI calls and containment */

static tree handle_host(tree *node, tree name, tree args, int, bool *no_add_attrs)
{
  tree t = *node;
  if (TREE_CODE(t) != INTEGER_TYPE || !TYPE_UNSIGNED(t) || TYPE_PRECISION(t) != 64) {
    error("%qE applies to a 64-bit unsigned integer type (a slot of the host API holds a host address)", name);
    *no_add_attrs = true;
  }
  return NULL_TREE;
}
static struct attribute_spec host_attr = { "fnvnx_host", 0, 0, false, true, false, false, handle_host, NULL };
static void register_attrs(void *, void *) { register_attribute(&host_attr); }

static bool host_type(tree t)
{
  return t && TYPE_P(t) && lookup_attribute("fnvnx_host", TYPE_ATTRIBUTES(t));
}

/* a slot, or an array of slots (the array field's elements are slots) */
static bool host_elem_type(tree t)
{
  while (t && TREE_CODE(t) == ARRAY_TYPE) t = TREE_TYPE(t);
  return host_type(t);
}

/* a type that is, or holds by member or element, a host slot */
static bool contains_host(tree t)
{
  if (!t || !TYPE_P(t)) return false;
  if (host_type(t)) return true;
  if (TREE_CODE(t) == ARRAY_TYPE) return contains_host(TREE_TYPE(t));
  if (TREE_CODE(t) == RECORD_TYPE || TREE_CODE(t) == UNION_TYPE)
    for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
      if (TREE_CODE(f) == FIELD_DECL && contains_host(TREE_TYPE(f))) return true;
  return false;
}

/* a reference that names a field of host slot type (at any depth of the access path) */
static bool host_ref(tree r)
{
  if (!r) return false;
  while (handled_component_p(r)) {
    if (TREE_CODE(r) == COMPONENT_REF && host_elem_type(TREE_TYPE(TREE_OPERAND(r, 1)))) return true;
    r = TREE_OPERAND(r, 0);
  }
  return false;
}

static bool conv_code(enum tree_code c)
{
  return c == NOP_EXPR || c == CONVERT_EXPR || c == VIEW_CONVERT_EXPR || c == SSA_NAME;
}

/* the SSA name holds the address of a host slot (or of a place inside a host-slot array): `&p->slot`, or such an
 * address plus an offset, or a conversion of one. GCC itself writes `&p->slot` and loads through it at the last GIMPLE. */
static bool host_addr(tree n, int guard = 0)
{
  if (guard > 32 || !n || TREE_CODE(n) != SSA_NAME) return false;
  gimple *d = SSA_NAME_DEF_STMT(n);
  if (!is_gimple_assign(d)) return false;
  enum tree_code c = gimple_assign_rhs_code(d);
  tree r = gimple_assign_rhs1(d);
  if (gimple_assign_single_p(d)) return TREE_CODE(r) == ADDR_EXPR && host_ref(TREE_OPERAND(r, 0));
  if (c == POINTER_PLUS_EXPR) return host_addr(r, guard + 1);
  if (c == NOP_EXPR || c == CONVERT_EXPR) return host_addr(r, guard + 1);
  return false;
}

/* a memory reference that reads a host slot: a field access of host slot type, or a dereference of a host address */
static bool host_load(tree r)
{
  if (host_ref(r)) return true;
  if (TREE_CODE(r) != MEM_REF) return false;
  tree b = TREE_OPERAND(r, 0);
  return TREE_CODE(b) == SSA_NAME ? host_addr(b) : TREE_CODE(b) == ADDR_EXPR && host_ref(TREE_OPERAND(b, 0));
}

static bool atomic_load_fn(gcall *c)
{
  if (!gimple_call_builtin_p(c, BUILT_IN_NORMAL)) return false;
  switch (DECL_FUNCTION_CODE(gimple_call_fndecl(c))) {
  case BUILT_IN_ATOMIC_LOAD_1: case BUILT_IN_ATOMIC_LOAD_2: case BUILT_IN_ATOMIC_LOAD_4: case BUILT_IN_ATOMIC_LOAD_8: case BUILT_IN_ATOMIC_LOAD_16:
    return true;
  default: return false;
  }
}

static bool atomic_store_fn(gcall *c)
{
  if (!gimple_call_builtin_p(c, BUILT_IN_NORMAL)) return false;
  switch (DECL_FUNCTION_CODE(gimple_call_fndecl(c))) {
  case BUILT_IN_ATOMIC_STORE_1: case BUILT_IN_ATOMIC_STORE_2: case BUILT_IN_ATOMIC_STORE_4: case BUILT_IN_ATOMIC_STORE_8: case BUILT_IN_ATOMIC_STORE_16:
    return true;
  default: return false;
  }
}

/* the ordering plugin turns a load into `__atomic_load_N (address, order)`: this call reads a host slot */
static bool host_atomic_load(gimple *st)
{
  gcall *c = dyn_cast<gcall *>(st);
  if (!c || !atomic_load_fn(c) || gimple_call_num_args(c) < 1) return false;
  tree a = gimple_call_arg(c, 0);
  return TREE_CODE(a) == SSA_NAME ? host_addr(a) : TREE_CODE(a) == ADDR_EXPR && host_ref(TREE_OPERAND(a, 0));
}

/* the callee value comes from a host slot load through conversions only */
static bool host_callee(tree fn)
{
  for (int guard = 0; guard < 64 && fn && TREE_CODE(fn) == SSA_NAME; guard++) {
    gimple *d = SSA_NAME_DEF_STMT(fn);
    if (host_atomic_load(d)) return true;
    if (!is_gimple_assign(d)) return false;
    enum tree_code c = gimple_assign_rhs_code(d);
    tree r = gimple_assign_rhs1(d);
    if (gimple_assign_single_p(d) && host_load(r)) return true;
    if (!conv_code(c)) return false;
    fn = c == VIEW_CONVERT_EXPR ? TREE_OPERAND(r, 0) : r;
  }
  return false;
}

static void host_error(gimple *st, const char *form)
{
  error_at(gimple_location(st), "HOST1: %s", form);
}

/* every use of the host value V (or of a conversion of it) is the callee of an indirect call */
static void check_uses(tree v, std::vector<tree> &seen)
{
  for (tree s : seen) if (s == v) return;
  seen.push_back(v);
  gimple *use;
  imm_use_iterator it;
  FOR_EACH_IMM_USE_STMT(use, it, v) {
    if (is_gimple_debug(use)) continue;
    if (gcall *c = dyn_cast<gcall *>(use)) {
      bool callee = gimple_call_fn(c) == v, arg = false;
      for (unsigned i = 0; i < gimple_call_num_args(c); i++) arg |= gimple_call_arg(c, i) == v;
      if (callee && !arg && !gimple_call_internal_p(c)) continue;
      bool st_val = atomic_store_fn(c) && gimple_call_num_args(c) >= 2 && gimple_call_arg(c, 1) == v;   /* the ordering plugin's store builtin */
      host_error(use, st_val ? "a host slot value is stored" : "a host slot value is passed as an argument");
      continue;
    }
    if (gassign *a = dyn_cast<gassign *>(use)) {
      if (conv_code(gimple_assign_rhs_code(a)) && gimple_num_ops(a) == 2 &&
          (gimple_assign_rhs_code(a) == VIEW_CONVERT_EXPR ? TREE_OPERAND(gimple_assign_rhs1(a), 0) : gimple_assign_rhs1(a)) == v &&
          TREE_CODE(gimple_assign_lhs(a)) == SSA_NAME) { check_uses(gimple_assign_lhs(a), seen); continue; }
      host_error(use, TREE_CODE(gimple_assign_lhs(a)) != SSA_NAME ? "a host slot value is stored"
                 : TREE_CODE_CLASS(gimple_assign_rhs_code(a)) == tcc_comparison ? "a host slot value is compared or switched on"
                 : "a host slot value is used in arithmetic or a selection");
      continue;
    }
    if (is_a<gphi *>(use)) { host_error(use, "a host slot value is merged by a PHI"); continue; }
    if (is_a<gcond *>(use) || is_a<gswitch *>(use)) { host_error(use, "a host slot value is compared or switched on"); continue; }
    if (is_a<greturn *>(use)) { host_error(use, "a host slot value is returned"); continue; }
    if (is_a<gasm *>(use)) { host_error(use, "a host slot value is an operand of an asm"); continue; }
    host_error(use, "a host slot value is used other than as the callee of an indirect call");
  }
}

/* every use of a host address A is the address of a load (`*A`), or yields another host address */
static void check_addr_uses(tree a, std::vector<tree> &seen)
{
  for (tree s : seen) if (s == a) return;
  seen.push_back(a);
  gimple *use;
  imm_use_iterator it;
  FOR_EACH_IMM_USE_STMT(use, it, a) {
    if (is_gimple_debug(use)) continue;
    if (gcall *c = dyn_cast<gcall *>(use)) {
      if (gimple_call_num_args(c) >= 1 && gimple_call_arg(c, 0) == a) {
        bool other = false;
        for (unsigned i = 1; i < gimple_call_num_args(c); i++) other |= gimple_call_arg(c, i) == a;
        if (!other && atomic_load_fn(c)) continue;   /* a load: its value is checked where the loads are */
        if (!other && atomic_store_fn(c)) { host_error(use, "a host slot is stored into"); continue; }
      }
    } else if (gassign *g = dyn_cast<gassign *>(use)) {
      tree l = gimple_assign_lhs(g);
      if (TREE_CODE(l) != SSA_NAME) { host_error(use, "a host slot is stored into"); continue; }
      enum tree_code c = gimple_assign_rhs_code(g);
      if (gimple_assign_single_p(g) && TREE_CODE(gimple_assign_rhs1(g)) == MEM_REF && TREE_OPERAND(gimple_assign_rhs1(g), 0) == a)
        continue;   /* a load: its value is checked where the loads are */
      if ((c == POINTER_PLUS_EXPR && gimple_assign_rhs1(g) == a && gimple_assign_rhs2(g) != a) ||
          ((c == NOP_EXPR || c == CONVERT_EXPR) && gimple_assign_rhs1(g) == a)) { check_addr_uses(l, seen); continue; }
    }
    host_error(use, "the address of a host slot is taken");
  }
}

static void check_operand(gimple *st, tree op, bool lhs)
{
  if (!op) return;
  if (TREE_CODE(op) == ADDR_EXPR && host_ref(TREE_OPERAND(op, 0))) {
    gassign *g = dyn_cast<gassign *>(st);   /* `a = &p->slot` is checked by the uses of a; any other place the address is written is an error */
    if (g && gimple_assign_single_p(g) && gimple_assign_rhs1(g) == op && TREE_CODE(gimple_assign_lhs(g)) == SSA_NAME) {
      std::vector<tree> seen;
      check_addr_uses(gimple_assign_lhs(g), seen);
    } else
      host_error(st, "the address of a host slot is taken");
  }
  if (lhs && host_load(op)) host_error(st, "a host slot is stored into");
}

static const char *aggregate_form = "an aggregate with host slots is copied, loaded, stored, passed or returned by value";

static void aggregate_operand(gimple *st, tree op)
{
  if (!op) return;
  if (TREE_CODE(op) == TREE_LIST) op = TREE_VALUE(op);
  if (TREE_TYPE(op) && TYPE_P(TREE_TYPE(op)) && AGGREGATE_TYPE_P(TREE_TYPE(op)) && contains_host(TREE_TYPE(op))) host_error(st, aggregate_form);
}

/* a statement of the lowered GIMPLE (before the memory-order rewrite splits an aggregate copy into word accesses): an access to, or a copy of, a whole
 * aggregate that holds host slots, and a memory builtin whose pointer operands point to one */
static tree aggregate_stmt(gimple_stmt_iterator *gsi, bool *handled, struct walk_stmt_info *)
{
  gimple *st = gsi_stmt(*gsi);
  if (is_gimple_debug(st)) return NULL_TREE;
  if (gassign *a = dyn_cast<gassign *>(st)) {
    for (unsigned i = 0; i < gimple_num_ops(a); i++) aggregate_operand(st, gimple_op(a, i));
  } else if (gcall *c = dyn_cast<gcall *>(st)) {
    if (tree l = gimple_call_lhs(c)) aggregate_operand(st, l);
    for (unsigned i = 0; i < gimple_call_num_args(c); i++) aggregate_operand(st, gimple_call_arg(c, i));
    if (gimple_call_builtin_p(c, BUILT_IN_NORMAL))
      switch (DECL_FUNCTION_CODE(gimple_call_fndecl(c))) {
      case BUILT_IN_MEMCPY: case BUILT_IN_MEMMOVE: case BUILT_IN_MEMPCPY: case BUILT_IN_MEMSET:
      case BUILT_IN_MEMCPY_CHK: case BUILT_IN_MEMMOVE_CHK: case BUILT_IN_MEMPCPY_CHK: case BUILT_IN_MEMSET_CHK:
        for (unsigned i = 0; i < gimple_call_num_args(c) && i < 2; i++) {
          tree t = TREE_TYPE(gimple_call_arg(c, i));
          if (POINTER_TYPE_P(t) && contains_host(strip_array_types(TREE_TYPE(t)))) host_error(st, aggregate_form);
        }
        break;
      default: break;
      }
  } else if (greturn *r = dyn_cast<greturn *>(st))
    aggregate_operand(st, gimple_return_retval(r));
  else if (gasm *x = dyn_cast<gasm *>(st)) {
    for (unsigned i = 0; i < gimple_asm_noutputs(x); i++) aggregate_operand(st, gimple_asm_output_op(x, i));
    for (unsigned i = 0; i < gimple_asm_ninputs(x); i++) aggregate_operand(st, gimple_asm_input_op(x, i));
  }
  *handled = false;
  return NULL_TREE;
}

/* HOST1 containment over the function; the SSA names that hold a host slot value are checked for their uses */
static void host_containment(function *fun)
{
  basic_block bb;
  FOR_EACH_BB_FN(bb, fun)
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
      gimple *st = gsi_stmt(gsi);
      if (is_gimple_debug(st)) continue;
      if (gassign *a = dyn_cast<gassign *>(st)) {
        tree lhs = gimple_assign_lhs(a);
        check_operand(st, lhs, true);
        for (unsigned i = 1; i < gimple_num_ops(a); i++) check_operand(st, gimple_op(a, i), false);
        if (gimple_assign_single_p(a) && host_load(gimple_assign_rhs1(a)) && TREE_CODE(lhs) == SSA_NAME) {
          std::vector<tree> seen;
          check_uses(lhs, seen);
        } else if (gimple_assign_single_p(a) && host_load(gimple_assign_rhs1(a)))
          host_error(st, "a host slot is stored");
      } else if (gcall *c = dyn_cast<gcall *>(st)) {
        if (tree l = gimple_call_lhs(c)) check_operand(st, l, true);
        bool ld = host_atomic_load(st);
        for (unsigned i = 0; i < gimple_call_num_args(c); i++)
          if (!(ld && i == 0)) check_operand(st, gimple_call_arg(c, i), false);
        if (ld) {
          tree l = gimple_call_lhs(c);
          if (l && TREE_CODE(l) == SSA_NAME) { std::vector<tree> seen; check_uses(l, seen); }
          else if (l) host_error(st, "a host slot is stored");
        }
      } else if (gasm *x = dyn_cast<gasm *>(st)) {
        for (unsigned i = 0; i < gimple_asm_noutputs(x); i++) check_operand(st, gimple_asm_output_op(x, i), true);
        for (unsigned i = 0; i < gimple_asm_ninputs(x); i++) check_operand(st, gimple_asm_input_op(x, i), false);
      } else if (greturn *r = dyn_cast<greturn *>(st))
        check_operand(st, gimple_return_retval(r), false);
    }
}

/* The provenance of a callee is a fact of the early GIMPLE (after SSA is built): a slot is read through its field, and a later pass may write the address of
 * the slot as plain pointer arithmetic. So the early pass (host_pass) marks every host-ABI call: its callee value C becomes `m = fnvnx_host_ptr (C)`, a
 * const artificial call that the dispatch pass at the last GIMPLE recognises and replaces by `m = C`. A marker is a fact of the pass pair; it never reaches
 * an object. */
static bool marker_call(gimple *st)
{
  gcall *c = dyn_cast<gcall *>(st);
  tree d = c ? gimple_call_fndecl(c) : NULL_TREE;
  return d && DECL_ARTIFICIAL(d) && DECL_EXTERNAL(d) && DECL_NAME(d) && !strcmp(IDENTIFIER_POINTER(DECL_NAME(d)), "fnvnx_host_ptr");
}

static tree marker_decl(tree ptr_type)
{
  tree t = build_function_type_list(ptr_type, ptr_type, NULL_TREE);
  tree d = build_fn_decl("fnvnx_host_ptr", t);
  DECL_EXTERNAL(d) = 1; TREE_PUBLIC(d) = 1; DECL_ARTIFICIAL(d) = 1; TREE_NOTHROW(d) = 1; TREE_READONLY(d) = 1; DECL_PURE_P(d) = 0;
  return d;
}

/* per function: [rewritten, host] */
static std::map<std::string, std::pair<unsigned, unsigned>> records;

static void finish_unit(void *, void *)
{
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.dispatch,\"\",%%progbits\n");
  for (auto &r : records)
    fprintf(asm_out_file, "\t.ascii\t\"%s\\t%u\\t%u\\n\"\n", r.first.c_str(), r.second.first, r.second.second);
  fprintf(asm_out_file, "\t.popsection\n");
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
    std::vector<gimple *> calls, markers;
    unsigned host = 0;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (marker_call(st)) markers.push_back(st);
        else if (indirect_call(st)) {
          tree fn = gimple_call_fn(st);
          gimple *d = TREE_CODE(fn) == SSA_NAME ? SSA_NAME_DEF_STMT(fn) : NULL;
          if (d && marker_call(d)) host++; else calls.push_back(st);
        }
      }
    /* a marker value is only ever a callee; then the marker is a plain copy */
    for (gimple *m : markers) {
      tree l = gimple_call_lhs(m);
      if (l) {
        std::vector<tree> seen;
        check_uses(l, seen);
      }
      gimple_stmt_iterator gsi = gsi_for_stmt(m);
      gsi_replace(&gsi, gimple_build_assign(l ? l : make_ssa_name(TREE_TYPE(gimple_call_arg(m, 0))), gimple_call_arg(m, 0)), true);
    }
    if (calls.empty() && !host) return 0;
    records[IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fun->decl))] = { (unsigned)calls.size(), host };
    if (calls.empty()) return 0;
    for (size_t i = 0; i < calls.size(); i++) rewrite(fun, calls[i]);
    free_dominance_info(CDI_DOMINATORS);
    mark_virtual_operands_for_renaming(fun);
    return 0;
  }
};

const pass_data agg_data = { GIMPLE_PASS, "fnvnx_hostagg", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0 };
/* HOST1 containment of aggregates, on the lowered GIMPLE (no CFG yet) */
struct agg_pass : gimple_opt_pass {
  agg_pass(gcc::context *c) : gimple_opt_pass(agg_data, c) {}
  unsigned int execute(function *fun) override
  {
    struct walk_stmt_info wi;
    memset(&wi, 0, sizeof wi);
    walk_gimple_seq(gimple_body(fun->decl), aggregate_stmt, NULL, &wi);
    return 0;
  }
};

const pass_data host_data = { GIMPLE_PASS, "fnvnx_host", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
/* HOST1 at the early GIMPLE (after the SSA form is built): containment, and a marker on every host-ABI call */
struct host_pass : gimple_opt_pass {
  host_pass(gcc::context *c) : gimple_opt_pass(host_data, c) {}
  unsigned int execute(function *fun) override
  {
    host_containment(fun);
    std::vector<gimple *> calls;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi))
        if (indirect_call(gsi_stmt(gsi))) {
          tree fn = gimple_call_fn(gsi_stmt(gsi));
          if (TREE_CODE(fn) == SSA_NAME && host_callee(fn)) calls.push_back(gsi_stmt(gsi));
        }
    for (gimple *call : calls) {
      tree fn = gimple_call_fn(call);
      tree m = make_ssa_name(TREE_TYPE(fn));
      gcall *mk = gimple_build_call(marker_decl(TREE_TYPE(fn)), 1, fn);
      gimple_call_set_lhs(mk, m);
      gimple_set_location(mk, gimple_location(call));
      gimple_stmt_iterator gsi = gsi_for_stmt(call);
      gsi_insert_before(&gsi, mk, GSI_SAME_STMT);
      gimple_call_set_fn(as_a<gcall *>(call), m);
      update_stmt(call);
    }
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
  register_callback(info->base_name, PLUGIN_ATTRIBUTES, register_attrs, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  register_pass_info ap;
  ap.pass = new agg_pass(g);
  ap.reference_pass_name = "eh";
  ap.ref_pass_instance_number = 1;
  ap.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &ap);
  register_pass_info hp;
  hp.pass = new host_pass(g);
  hp.reference_pass_name = "ssa";
  hp.ref_pass_instance_number = 1;
  hp.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &hp);
  register_pass_info p;
  p.pass = new dispatch_pass(g);
  p.reference_pass_name = "optimized";
  p.ref_pass_instance_number = 1;
  p.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &p);
  return 0;
}
