// fnvnx_order.cc - GCC plugin: MSVC x86 memory-ordering semantics for decomp units on AArch64 (S12-ordering,
// docs/ORDERING.md).
//
//   g++ ... -fplugin=<dir>/fnvnx_order.so [-fplugin-arg-fnvnx_order-verbose]
//           [-fplugin-arg-fnvnx_order-mode=volatile|tso|off] [-fplugin-arg-fnvnx_order-keys=<file>] unit.cpp
//
// MSVC 2008 compiles for x86 with /volatile:ms (the only behaviour it has): a volatile read has acquire semantics,
// a volatile write release semantics, and the x86 hardware (TSO) keeps that order. GCC gives volatile no ordering
// with respect to other memory accesses, and the Cortex-A57 reorders loads and stores freely. The engine's spin
// locks, task queues and "done" flags are written against MSVC's contract, so this pass restores it:
//
//   load  of a volatile object  -> __atomic_load_N (p, ACQUIRE)   = ldar / ldarh / ldarb
//   store to a volatile object  -> __atomic_store_N(p, v, RELEASE) = stlr / stlrh / stlrb
//
// Objects that are not a naturally aligned 1/2/4/8-byte scalar (bit-fields, packed or misaligned fields - ldar
// faults on misalignment where x86 does not -, aggregates, vectors) keep their plain volatile access with a fence:
// load; dmb ishld (acquire), and dmb ish; store (release). Automatic variables whose address is never taken are
// left alone: no other thread can see them.
//
// mode=tso (measurement only, docs/ORDERING.md): every load and store to memory that is not such a local is treated
// like a volatile one, i.e. full x86 TSO emulation (stronger than TSO: stlr -> ldar is ordered too).
// keys=<file>: lines "field Class::member" / "var name" (the static gate's ATOMIC-USE keys, tools/static/run.sh
// writes clang/atomic.keys): accesses to these fields and variables are treated as volatile too. They are operands
// of Interlocked* somewhere in the program; a plain x86 store to them is the release half of a lock (ATOMIC-PLAIN-
// STORE), a plain load the acquire half.
//
// refcount=weak (default) | full: refcount sites of Interlocked* (docs/ORDERING.md "Refcounts"). A call is
// redirected to fnvnx_win32.h's fnvnx_refcount_inc (relaxed) or fnvnx_refcount_dec (release, acquire fence when the
// count reaches 0) only when all of these hold: the callee is InterlockedIncrement / InterlockedDecrement; its operand
// is &<object>.<field> with the field's name in refcount_fields, a 32-bit integer, not a bit-field, aligned to 4 in
// its record; an increment's result is discarded; a decrement's result is discarded or only compared with 0
// (== 0, != 0, !, or an if condition); and the unit defines the helpers (it includes fnvnx_win32.h). Anything else
// keeps the full x86 semantics of the inline Interlocked*.
//
// Interlocked operands of the unit itself are found without a key file: every field or variable whose address a
// call of Interlocked* / _Interlocked* takes in any function of the unit is ordered in every function of the unit
// (all bodies pass PLUGIN_PRE_GENERICIZE before the first one is lowered, so the order does not matter).
//
// Runs right after the CFG is built (before SSA, so no virtual operands to maintain), like fnvnx_ftoi.
//
// args=rtl (default) | ltr: call arguments in MSVC's order (ORDER-ARGS). MSVC x86 evaluates the arguments of every
// call right to left (it pushes them); GCC evaluates them left to right on AArch64 (its gimplifier reverses only for
// targets with push instructions, PUSH_ARGS_REVERSED, a compile-time 0 here). Before a function body is lowered
// (PLUGIN_PRE_GENERICIZE), every call or constructor call with at least one side-effecting argument and another
// non-constant one gets its scalar arguments wrapped in SAVE_EXPRs, and the first of them becomes the chain
// (s_n, ..., s_2, s_1): GCC evaluates the first argument first, so all of them are evaluated last to first, while the
// call itself (and so return slots and constructor calls) stays as it was. `this` and the callee stay first, as now
// (ORDER-OBJECT is a separate class). A call with a side-effecting argument of class type is left alone.
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "context.h"
#include "tree.h"
#include "tree-pass.h"
#include "function.h"
#include "basic-block.h"
#include "tree-ssa-alias.h"
#include "internal-fn.h"
#include "gimple-expr.h"
#include "gimple.h"
#include "gimple-iterator.h"
#include "gimplify.h"
#include "gimplify-me.h"
#include "stringpool.h"
#include "memmodel.h"
#include "builtins.h"
#include "tree-iterator.h"
#include "cp/cp-tree.h"
#include "diagnostic-core.h"
#include "cfghooks.h"
#include "tree-cfg.h"
#include <set>
#include <string>
#include <fstream>

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

enum order_mode { MODE_OFF, MODE_VOLATILE, MODE_TSO };
static order_mode mode = MODE_VOLATILE;
static bool verbose, verbose_sites;
static std::set<std::string> key_fields, key_vars;   // "Class::member", "name"
static std::set<tree> interlocked_decls;             // FIELD_DECLs / VAR_DECLs this unit hands to Interlocked*
struct counts { unsigned long ldar, stlr, fence_ld, fence_st; };
static counts unit_total;
static bool args_rtl = true;
static unsigned long calls_reordered, calls_skipped;
static bool refcount_weak = true;
static tree refcount_inc_fn, refcount_dec_fn;            // fnvnx_win32.h helpers, seen at their PRE_GENERICIZE
static unsigned long ilk_inc_weak, ilk_dec_weak, ilk_full;
static const char *const refcount_fields[] = {
    "m_uiRefCount", "m_iRefCount", "m_lRefCount", "m_lRef", "m_ref", "refCount", "refs", "references",
    "referenceCount", "ref_count", "m_iObjectReferenceCount", "m_lObjectReferenceCount", nullptr};

// ---- what counts as a shared volatile access

static bool local_unshared(tree base) {
  if (!base) return true;
  if (TREE_CODE(base) == VAR_DECL || TREE_CODE(base) == PARM_DECL || TREE_CODE(base) == RESULT_DECL)
    return !TREE_STATIC(base) && !DECL_EXTERNAL(base) && !TREE_ADDRESSABLE(base) && !DECL_THREAD_LOCAL_P(base);
  if (TREE_CODE(base) == CONST_DECL || TREE_CODE(base) == STRING_CST) return true;
  return false;
}

static bool is_memory_ref(tree t) {
  switch (TREE_CODE(t)) {
    case MEM_REF: case TARGET_MEM_REF: case COMPONENT_REF: case ARRAY_REF: case ARRAY_RANGE_REF:
    case BIT_FIELD_REF: case REALPART_EXPR: case IMAGPART_EXPR: case VIEW_CONVERT_EXPR:
      return true;
    case VAR_DECL:
      return TREE_STATIC(t) || DECL_EXTERNAL(t) || TREE_ADDRESSABLE(t);
    default:
      return false;
  }
}

static std::string record_name(tree field) {
  tree ctx = DECL_CONTEXT(field);
  if (!ctx || !TYPE_P(ctx)) return "";
  tree n = TYPE_NAME(ctx);
  if (n && TREE_CODE(n) == TYPE_DECL) n = DECL_NAME(n);
  return n && TREE_CODE(n) == IDENTIFIER_NODE ? IDENTIFIER_POINTER(n) : "";
}

static bool keyed(tree t) {
  if (key_fields.empty() && key_vars.empty() && interlocked_decls.empty()) return false;
  for (tree r = t; r; r = handled_component_p(r) ? TREE_OPERAND(r, 0) : NULL_TREE) {
    if (TREE_CODE(r) == COMPONENT_REF) {
      tree f = TREE_OPERAND(r, 1);
      if (interlocked_decls.count(f)) return true;
      if (DECL_NAME(f) && key_fields.count(record_name(f) + "::" + IDENTIFIER_POINTER(DECL_NAME(f)))) return true;
    }
    if (TREE_CODE(r) == VAR_DECL && (interlocked_decls.count(r) ||
                                     (DECL_NAME(r) && key_vars.count(IDENTIFIER_POINTER(DECL_NAME(r))))))
      return true;
    if (!handled_component_p(r)) break;
  }
  return false;
}

static bool shared_ordered(tree t) {
  if (!t || !is_memory_ref(t)) return false;
  if (local_unshared(get_base_address(t))) return false;
  if (mode == MODE_TSO) return true;
  return TREE_THIS_VOLATILE(t) || TYPE_VOLATILE(TREE_TYPE(t)) || keyed(t);
}

// a naturally aligned scalar of 1/2/4/8 bytes that ldar/stlr can access: the size index 0..3, or -1
static int atomic_index(tree t) {
  tree ty = TREE_TYPE(t);
  if (!(INTEGRAL_TYPE_P(ty) || POINTER_TYPE_P(ty) || SCALAR_FLOAT_TYPE_P(ty) || TREE_CODE(ty) == OFFSET_TYPE)) return -1;
  if (TREE_CODE(t) == BIT_FIELD_REF) return -1;
  if (TREE_CODE(t) == COMPONENT_REF && DECL_BIT_FIELD(TREE_OPERAND(t, 1))) return -1;
  if (TREE_CODE(t) == REALPART_EXPR || TREE_CODE(t) == IMAGPART_EXPR) return -1;
  if (!TYPE_SIZE_UNIT(ty) || !tree_fits_uhwi_p(TYPE_SIZE_UNIT(ty))) return -1;
  unsigned HOST_WIDE_INT sz = tree_to_uhwi(TYPE_SIZE_UNIT(ty));
  if (INTEGRAL_TYPE_P(ty) && TYPE_PRECISION(ty) != sz * BITS_PER_UNIT && TREE_CODE(ty) != BOOLEAN_TYPE) return -1;
  int idx = sz == 1 ? 0 : sz == 2 ? 1 : sz == 4 ? 2 : sz == 8 ? 3 : -1;
  if (idx < 0) return -1;
  if (get_object_alignment(t) < sz * BITS_PER_UNIT) return -1;   // packed / misaligned: ldar would fault
  return idx;
}

static tree uint_type(int idx) {
  static const int bits[] = {8, 16, 32, 64};
  return build_nonstandard_integer_type(bits[idx], 1);
}

static gimple *fence(enum memmodel m) {
  return gimple_build_call(builtin_decl_explicit(BUILT_IN_ATOMIC_THREAD_FENCE), 1, build_int_cst(integer_type_node, m));
}

// force_gimple_operand replaces its sequence; gimplify into a fresh one and append it
static tree gimplify_to(tree e, gimple_seq *seq) {
  gimple_seq s = NULL;
  tree v = force_gimple_operand(e, &s, true, NULL_TREE);
  gimple_seq_add_seq(seq, s);
  return v;
}

static tree address_of(tree ref, gimple_seq *seq) {
  tree base = get_base_address(ref);
  if (base && DECL_P(base)) mark_addressable(base);
  return gimplify_to(build_fold_addr_expr(ref), seq);
}

// value of type T held in an unsigned integer of the same size, and back
static tree to_bits(tree v, tree ity, gimple_seq *seq) {
  tree ty = TREE_TYPE(v);
  tree e = SCALAR_FLOAT_TYPE_P(ty) ? fold_build1(VIEW_CONVERT_EXPR, ity, v) : fold_convert(ity, v);
  return gimplify_to(e, seq);
}

struct order_pass : gimple_opt_pass {
  order_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}

  unsigned int execute(function *fun) final override {
    if (mode == MODE_OFF) return 0;
    counts c = {0, 0, 0, 0};
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun) {
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *stmt = gsi_stmt(gsi);
        if (gimple_code(stmt) == GIMPLE_CALL) {
          // `ref = f()` storing into an ordered object: route the result through a temporary, and order that store
          tree lhs = gimple_call_lhs(stmt);
          if (lhs && shared_ordered(lhs) && !stmt_ends_bb_p(stmt) && is_gimple_reg_type(TREE_TYPE(lhs))) {
            tree tmp = create_tmp_var(TREE_TYPE(lhs), "order_r");
            gimple_call_set_lhs(stmt, tmp);
            gsi_insert_after(&gsi, gimple_build_assign(lhs, tmp), GSI_SAME_STMT);   // visited next
          } else if (lhs && shared_ordered(lhs)) {
            gsi_insert_before(&gsi, fence(MEMMODEL_RELEASE), GSI_SAME_STMT);
            c.fence_st++;
          }
          continue;
        }
        if (!gimple_assign_single_p(stmt)) continue;
        tree lhs = gimple_assign_lhs(stmt), rhs = gimple_assign_rhs1(stmt);
        bool st = shared_ordered(lhs), ld = shared_ordered(rhs);
        if (!st && !ld) continue;
        if (gimple_clobber_p(stmt)) continue;
        int li = ld ? atomic_index(rhs) : -1, si = st ? atomic_index(lhs) : -1;
        if (ld && !st && li >= 0 && is_gimple_reg_type(TREE_TYPE(rhs))) {
          // lhs = ref  ->  bits = __atomic_load_N(&ref, ACQUIRE); lhs = (T) bits
          gimple_seq seq = NULL;
          tree p = address_of(rhs, &seq);
          tree ity = uint_type(li);
          tree bits = create_tmp_var(ity, "order_ld");
          gcall *call = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_LOAD_1 + li)), 2, p,
                                          build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
          gimple_call_set_lhs(call, bits);
          gimple_seq_add_stmt(&seq, call);
          gsi_insert_seq_before(&gsi, seq, GSI_SAME_STMT);
          tree ty = TREE_TYPE(rhs);
          tree v = SCALAR_FLOAT_TYPE_P(ty) ? build1(VIEW_CONVERT_EXPR, ty, bits) : build1(NOP_EXPR, ty, bits);
          if (!useless_type_conversion_p(TREE_TYPE(lhs), ty)) {
            tree t2 = create_tmp_var(ty, "order_v");
            gsi_insert_before(&gsi, gimple_build_assign(t2, v), GSI_SAME_STMT);
            v = build1(NOP_EXPR, TREE_TYPE(lhs), t2);
          }
          gimple_assign_set_rhs_from_tree(&gsi, v);
          c.ldar++;
          continue;
        }
        if (st && !ld && si >= 0 && is_gimple_val(rhs)) {
          // ref = v  ->  __atomic_store_N(&ref, (uintN) v, RELEASE)
          gimple_seq seq = NULL;
          tree p = address_of(lhs, &seq);
          tree bits = to_bits(rhs, uint_type(si), &seq);
          gcall *call = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_STORE_1 + si)), 3, p,
                                          bits, build_int_cst(integer_type_node, MEMMODEL_RELEASE));
          gimple_set_location(call, gimple_location(stmt));
          gsi_insert_seq_before(&gsi, seq, GSI_SAME_STMT);
          gsi_replace(&gsi, call, false);
          c.stlr++;
          continue;
        }
        // everything else keeps its (volatile) access, fenced: dmb ish before a store, dmb ishld after a load
        if (st) { gsi_insert_before(&gsi, fence(MEMMODEL_RELEASE), GSI_SAME_STMT); c.fence_st++; }
        if (ld && !stmt_ends_bb_p(stmt)) {
          gsi_insert_after(&gsi, fence(MEMMODEL_ACQUIRE), GSI_SAME_STMT);   // gsi now on the fence
          c.fence_ld++;
        }
      }
    }
    unit_total.ldar += c.ldar; unit_total.stlr += c.stlr;
    unit_total.fence_ld += c.fence_ld; unit_total.fence_st += c.fence_st;
    if (verbose && (c.ldar || c.stlr || c.fence_ld || c.fence_st))
      inform(DECL_SOURCE_LOCATION(fun->decl), "%<fnvnx_order%>: %lu acquire loads, %lu release stores, %lu+%lu fenced",
             c.ldar, c.stlr, c.fence_ld, c.fence_st);
    return 0;
  }
};

static const pass_data order_data = {
    GIMPLE_PASS, "fnvnx_order", OPTGROUP_NONE, TV_NONE, PROP_gimple_any | PROP_cfg, 0, 0, 0, 0,
};

static void finish(void *, void *) {
  if (verbose)
    inform(UNKNOWN_LOCATION, "%<fnvnx_order%>: unit: %lu ldar, %lu stlr, %lu fenced loads, %lu fenced stores; "
           "%lu calls with arguments right to left, %lu left (class-type argument with side effects); "
           "Interlocked: %lu refcount increments relaxed, %lu refcount decrements release, %lu full",
           unit_total.ldar, unit_total.stlr, unit_total.fence_ld, unit_total.fence_st, calls_reordered, calls_skipped,
           ilk_inc_weak, ilk_dec_weak, ilk_full);
}

// ---- argument order (args=rtl)

static bool scalar_arg(tree t) {
  tree ty = TREE_TYPE(t);
  return ty && (INTEGRAL_TYPE_P(ty) || POINTER_TYPE_P(ty) || SCALAR_FLOAT_TYPE_P(ty) || TREE_CODE(ty) == OFFSET_TYPE ||
                TREE_CODE(ty) == VECTOR_TYPE || TREE_CODE(ty) == NULLPTR_TYPE);
}

static int arg_count(tree t) {
  return TREE_CODE(t) == CALL_EXPR ? call_expr_nargs(t) : aggr_init_expr_nargs(t);
}
static tree *arg_slot(tree t, int i) {
  return TREE_CODE(t) == CALL_EXPR ? &CALL_EXPR_ARG(t, i) : &AGGR_INIT_EXPR_ARG(t, i);
}

static void reorder_call(tree call) {
  int n = arg_count(call);
  int first = 0;
  // `this` (first argument of a method call, and the object of a constructor call) stays first
  tree fntype = NULL_TREE;
  if (TREE_CODE(call) == CALL_EXPR) {
    tree fn = CALL_EXPR_FN(call);
    if (fn && POINTER_TYPE_P(TREE_TYPE(fn))) fntype = TREE_TYPE(TREE_TYPE(fn));
    if (CALL_EXPR_ORDERED_ARGS(call) || CALL_EXPR_REVERSE_ARGS(call)) return;   // C++17 fixes this order
  } else {
    tree fn = AGGR_INIT_EXPR_FN(call);
    if (fn && POINTER_TYPE_P(TREE_TYPE(fn))) fntype = TREE_TYPE(TREE_TYPE(fn));
  }
  if (fntype && TREE_CODE(fntype) == METHOD_TYPE) first = 1;
  int effects = 0, live = 0;
  for (int i = first; i < n; i++) {
    tree a = *arg_slot(call, i);
    if (TREE_SIDE_EFFECTS(a)) {
      if (!scalar_arg(a)) {
        calls_skipped++;
        if (verbose_sites)
          inform(EXPR_LOCATION(call), "%<fnvnx_order%>: arguments left in GCC order (argument %d: %qT with side effects)",
                 i, TREE_TYPE(a));
        return;
      }
      effects++;
    }
    if (!TREE_CONSTANT(a)) live++;
  }
  if (!effects || live < 2) return;
  int lead = -1;
  for (int i = first; i < n; i++) {
    tree *slot = arg_slot(call, i);
    if (TREE_CONSTANT(*slot) || !scalar_arg(*slot)) continue;
    *slot = save_expr(*slot);
    if (lead < 0) lead = i;
  }
  if (lead < 0) return;
  tree *ls = arg_slot(call, lead);
  tree chain = *ls;
  for (int i = lead + 1; i < n; i++) {
    tree a = *arg_slot(call, i);
    if (TREE_CODE(a) == SAVE_EXPR) chain = build2(COMPOUND_EXPR, TREE_TYPE(chain), a, chain);
  }
  *ls = chain;
  calls_reordered++;
}

// ---- Interlocked operands of this unit

static void note_interlocked(tree call) {
  tree fn = get_callee_fndecl(call);
  if (!fn || !DECL_NAME(fn) || call_expr_nargs(call) < 1) return;
  const char *n = IDENTIFIER_POINTER(DECL_NAME(fn));
  if (strncmp(n, "Interlocked", 11) && strncmp(n, "_Interlocked", 12)) return;
  tree a = CALL_EXPR_ARG(call, 0);
  while (CONVERT_EXPR_P(a) || TREE_CODE(a) == NON_LVALUE_EXPR || TREE_CODE(a) == VIEW_CONVERT_EXPR) a = TREE_OPERAND(a, 0);
  if (TREE_CODE(a) != ADDR_EXPR) return;
  tree r = TREE_OPERAND(a, 0);
  while (TREE_CODE(r) == ARRAY_REF || TREE_CODE(r) == VIEW_CONVERT_EXPR) r = TREE_OPERAND(r, 0);
  if (TREE_CODE(r) == COMPONENT_REF) interlocked_decls.insert(TREE_OPERAND(r, 1));
  else if (TREE_CODE(r) == VAR_DECL) interlocked_decls.insert(r);
}

// ---- refcount sites (refcount=weak)

static tree strip_value_casts(tree a) {
  while (a && (CONVERT_EXPR_P(a) || TREE_CODE(a) == NON_LVALUE_EXPR || TREE_CODE(a) == VIEW_CONVERT_EXPR))
    a = TREE_OPERAND(a, 0);
  return a;
}

// 1 InterlockedIncrement, 2 InterlockedDecrement, 3 another Interlocked*, 0 none
static int interlocked_kind(tree call) {
  if (!call || TREE_CODE(call) != CALL_EXPR) return 0;
  tree fn = get_callee_fndecl(call);
  if (!fn || !DECL_NAME(fn)) return 0;
  const char *n = IDENTIFIER_POINTER(DECL_NAME(fn));
  if (!strcmp(n, "InterlockedIncrement")) return 1;
  if (!strcmp(n, "InterlockedDecrement")) return 2;
  return strncmp(n, "Interlocked", 11) && strncmp(n, "_Interlocked", 12) ? 0 : 3;
}

static bool refcount_operand(tree call) {
  if (call_expr_nargs(call) != 1) return false;
  tree a = strip_value_casts(CALL_EXPR_ARG(call, 0));
  if (!a || TREE_CODE(a) != ADDR_EXPR) return false;
  tree r = TREE_OPERAND(a, 0);
  if (TREE_CODE(r) != COMPONENT_REF) return false;
  tree f = TREE_OPERAND(r, 1), rec = DECL_CONTEXT(f);
  if (!DECL_NAME(f) || DECL_BIT_FIELD(f) || DECL_PACKED(f) || DECL_ALIGN(f) < 32) return false;
  if (TREE_CODE(TREE_TYPE(f)) != INTEGER_TYPE || TYPE_PRECISION(TREE_TYPE(f)) != 32) return false;
  if (!rec || TYPE_PACKED(rec) || TYPE_ALIGN(rec) < 32) return false;
  const char *n = IDENTIFIER_POINTER(DECL_NAME(f));
  for (const char *const *k = refcount_fields; *k; k++)
    if (!strcmp(n, *k)) return true;
  return false;
}

// *slot holds a value used as `ctx`: 0 discarded, 1 compared with 0 / truth value
static void refcount_site(tree *slot, int ctx) {
  if (!refcount_weak || !refcount_inc_fn || !refcount_dec_fn) return;
  tree call = strip_value_casts(*slot);
  int k = interlocked_kind(call);
  if (k != 1 && k != 2) return;
  if (k == 1 && ctx != 0) return;          // an increment whose value matters is not the refcount pattern
  if (!refcount_operand(call)) return;
  note_interlocked(call);                  // its operand stays an Interlocked operand for the GIMPLE pass
  tree fn = k == 1 ? refcount_inc_fn : refcount_dec_fn;
  if (!useless_type_conversion_p(TREE_TYPE(TREE_TYPE(fn)), TREE_TYPE(call))) return;
  CALL_EXPR_FN(call) = build_fold_addr_expr(fn);
  TREE_USED(fn) = 1;
  mark_used(fn);
  (k == 1 ? ilk_inc_weak : ilk_dec_weak)++;
}

static void refcount_contexts(tree t) {
  switch (TREE_CODE(t)) {
    case EXPR_STMT: refcount_site(&EXPR_STMT_EXPR(t), 0); break;
    case CLEANUP_POINT_EXPR: refcount_site(&TREE_OPERAND(t, 0), 0); break;
    case COMPOUND_EXPR: refcount_site(&TREE_OPERAND(t, 0), 0); break;
    case CONVERT_EXPR: case NOP_EXPR:
      if (VOID_TYPE_P(TREE_TYPE(t))) refcount_site(&TREE_OPERAND(t, 0), 0);
      break;
    case STATEMENT_LIST:
      for (tree_stmt_iterator i = tsi_start(t); !tsi_end_p(i); tsi_next(&i)) refcount_site(tsi_stmt_ptr(i), 0);
      break;
    case BIND_EXPR: refcount_site(&BIND_EXPR_BODY(t), 0); break;
    case EQ_EXPR: case NE_EXPR:
      if (integer_zerop(TREE_OPERAND(t, 1))) refcount_site(&TREE_OPERAND(t, 0), 1);
      else if (integer_zerop(TREE_OPERAND(t, 0))) refcount_site(&TREE_OPERAND(t, 1), 1);
      break;
    case TRUTH_NOT_EXPR: refcount_site(&TREE_OPERAND(t, 0), 1); break;
    case IF_STMT: refcount_site(&IF_COND(t), 1); break;
    default: break;
  }
}

static tree order_args_walk(tree *tp, int *, void *) {
  refcount_contexts(*tp);
  if (TREE_CODE(*tp) == CALL_EXPR) {
    note_interlocked(*tp);
    if (interlocked_kind(*tp)) ilk_full++;
  }
  if (args_rtl && (TREE_CODE(*tp) == CALL_EXPR || TREE_CODE(*tp) == AGGR_INIT_EXPR)) reorder_call(*tp);
  return NULL_TREE;
}

static void pre_genericize(void *gcc_data, void *) {
  tree fndecl = (tree)gcc_data;
  if (!fndecl || TREE_CODE(fndecl) != FUNCTION_DECL || !DECL_SAVED_TREE(fndecl)) return;
  if (DECL_NAME(fndecl)) {
    const char *n = IDENTIFIER_POINTER(DECL_NAME(fndecl));
    if (!strcmp(n, "fnvnx_refcount_inc")) { refcount_inc_fn = fndecl; return; }
    if (!strcmp(n, "fnvnx_refcount_dec")) { refcount_dec_fn = fndecl; return; }
  }
  // compiler and C library headers (arm_neon.h, <string>, ...) are not decomp code: their order is GCC's to keep
  if (in_system_header_at(DECL_SOURCE_LOCATION(fndecl))) return;
  walk_tree_without_duplicates(&DECL_SAVED_TREE(fndecl), order_args_walk, NULL);
}

static void read_keys(const char *path) {
  std::ifstream in(path);
  if (!in) { error("%<fnvnx_order%>: cannot read keys file %s", path); return; }
  std::string line;
  while (std::getline(in, line)) {
    if (line.compare(0, 6, "field ") == 0) {
      std::string k = line.substr(6);
      // the gate prints qualified names (ns::Class::member); the pass compares Class::member
      size_t last = k.rfind("::");
      if (last == std::string::npos) continue;
      size_t prev = last == 0 ? std::string::npos : k.rfind("::", last - 1);
      key_fields.insert(prev == std::string::npos ? k : k.substr(prev + 2));
    } else if (line.compare(0, 4, "var ") == 0) {
      std::string k = line.substr(4);
      size_t last = k.rfind("::");
      key_vars.insert(last == std::string::npos ? k : k.substr(last + 2));
    }
  }
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version) {
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_order%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  for (int i = 0; i < info->argc; i++) {
    const char *k = info->argv[i].key, *v = info->argv[i].value;
    if (!strcmp(k, "verbose")) { verbose = true; if (v && !strcmp(v, "sites")) verbose_sites = true; }
    else if (!strcmp(k, "mode") && v) {
      if (!strcmp(v, "volatile")) mode = MODE_VOLATILE;
      else if (!strcmp(v, "tso")) mode = MODE_TSO;
      else if (!strcmp(v, "off")) mode = MODE_OFF;
      else { error("%<fnvnx_order%>: unknown mode %s", v); return 1; }
    } else if (!strcmp(k, "keys") && v) read_keys(v);
    else if (!strcmp(k, "refcount") && v) {
      if (!strcmp(v, "weak")) refcount_weak = true;
      else if (!strcmp(v, "full")) refcount_weak = false;
      else { error("%<fnvnx_order%>: unknown refcount mode %s", v); return 1; }
    } else if (!strcmp(k, "args") && v) {
      if (!strcmp(v, "rtl")) args_rtl = true;
      else if (!strcmp(v, "ltr")) args_rtl = false;
      else { error("%<fnvnx_order%>: unknown argument order %s", v); return 1; }
    }
    else { error("%<fnvnx_order%>: unknown argument %s", k); return 1; }
  }
  struct register_pass_info pass;
  pass.pass = new order_pass(g, order_data);
  pass.reference_pass_name = "cfg";
  pass.ref_pass_instance_number = 1;
  pass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pass);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, pre_genericize, NULL);
  return 0;
}
