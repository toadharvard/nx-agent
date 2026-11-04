// fnvnx_order.cc - GCC plugin: MSVC x86 memory-ordering semantics for decomp units on AArch64 (S12-ordering,
// docs/ORDERING.md).
//
//   g++ ... -fplugin=<dir>/fnvnx_order.so [-fplugin-arg-fnvnx_order-verbose]
//           [-fplugin-arg-fnvnx_order-mode=volatile|tso|off] [-fplugin-arg-fnvnx_order-keys=<file>]
//           [-fplugin-arg-fnvnx_order-object=first|last] [-fplugin-arg-fnvnx_order-operator=cpp17|rtl] unit.cpp
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
//
// object=first (default) | last (ORDER-OBJECT, open until S11's MSVC 2008 run of tests/s12/order/evalorder.cpp):
// MSVC's thiscall pushes the arguments and probably computes the object (ecx) last. With object=last (and args=rtl)
// a member call whose object and an argument can see each other (one has side effects, the other is not invariant)
// evaluates the arguments right to left, then the object, then the callee: for a direct call `this` joins the chain
// as its last element (the callee stays an ADDR_EXPR, so the call stays direct); for a virtual call the chain goes
// in front of the OBJ_TYPE_REF's object, which GCC gimplifies before the vtable load. Constructors, destructors and
// calls through pointers to members keep the object first.
// operator=cpp17 (default) | rtl (ORDER-OPERATOR, open the same way): C++17 evaluates overloaded `<<`, `>>`, `[]`,
// `->*` left to right (CALL_EXPR_ORDERED_ARGS), which the pass leaves alone. With operator=rtl they lose the flag
// and are ordered like any call (a free operator's left operand included; a member operator's object as object=
// says). `=` and `@=` (CALL_EXPR_REVERSE_ARGS) already evaluate the right operand first, as MSVC does: left alone.
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
#include "ssa.h"
#include <set>
#include <string>
#include <vector>
#include <utility>
#include <map>
#include <fstream>

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

enum order_mode { MODE_OFF, MODE_VOLATILE, MODE_TSO };
static order_mode mode = MODE_VOLATILE;
static bool verbose, verbose_sites;
static std::set<std::string> key_fields, key_vars;   // "Class::member", "name"
static std::set<tree> interlocked_decls;             // FIELD_DECLs / VAR_DECLs this unit hands to Interlocked*
static std::set<tree> poll_decls;                    // FIELD_DECLs / VAR_DECLs a wait-only loop of this unit polls
// Interlocked forwarders: functions that hand (the pointee of) their parameter i to Interlocked* or to another
// forwarder (`void NiAtomicInc(long &x) { InterlockedIncrement(&x); }`), and the calls that bind a field or variable
// to a parameter of some function. Resolved once every body of the unit has passed PRE_GENERICIZE.
struct param_call { tree caller; int caller_parm; tree callee; int arg; };   // f(p) inside caller, p its parameter
struct decl_call { tree callee; int arg; tree decl; };                       // f(&obj.m) / f(obj.m) by reference
static std::set<std::pair<tree, int>> forwarders;
static std::vector<param_call> param_calls;
static std::vector<decl_call> decl_calls;
static bool forwarders_resolved;
static const char *discover_path;                    // discover=<file>: write this unit's keys
static unsigned long polls_plain;
struct counts { unsigned long ldar, stlr, fence_ld, fence_st; };
static counts unit_total;
static bool args_rtl = true;
static unsigned long calls_reordered, calls_skipped;
static bool object_last = false;                         // object=first (default) | last | msvc: ORDER-OBJECT
static bool object_msvc = false;                         // object=msvc: MSVC 2008's table (tests/s11/order/order.msvc)
static bool assign_msvc = false;                         // assign=cpp17 (default) | msvc: built-in compound assignment
static bool operator_rtl = false;                        // operator=cpp17 (default) | rtl: ORDER-OPERATOR
static unsigned long calls_object_last, calls_operator;
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

// The record a field belongs to, by name. A member of an anonymous union or struct is named by the nearest named
// record that contains it (`struct U { union { LONG lk; struct { short lo, hi; }; }; }`: U::lk, U::lo), so it
// matches across units; a field with no named record around it has no cross-unit name.
static std::string record_name(tree field) {
  tree ctx = DECL_CONTEXT(field);
  for (int depth = 0; ctx && TYPE_P(ctx) && depth < 8; depth++, ctx = TYPE_CONTEXT(ctx)) {
    tree n = TYPE_NAME(ctx);
    if (n && TREE_CODE(n) == TYPE_DECL) n = DECL_NAME(n);
    if (n && TREE_CODE(n) == IDENTIFIER_NODE && !IDENTIFIER_ANON_P(n)) return IDENTIFIER_POINTER(n);
    if (!RECORD_OR_UNION_TYPE_P(ctx)) break;
  }
  return "";
}

// pointer fields whose pointee some unit hands to Interlocked* (`InterlockedIncrement(c->m_pRef)`): a store or load
// through the field's value (`*c->m_pRef = 1`) is an access to the Interlocked operand (adversarial review, p5)
static std::set<tree> pfield_decls;
static std::set<std::string> key_pfields;
static std::set<tree> pfield_temps;   // this function's temporaries loaded from such a field
static bool pfield_keyed(tree f) {
  return pfield_decls.count(f) ||
         (DECL_NAME(f) && key_pfields.count(record_name(f) + "::" + IDENTIFIER_POINTER(DECL_NAME(f))));
}

static void resolve_forwarders();
static void write_discovered();
static bool field_keyed(tree f);
// local pointers set from the address of a field or variable (`int *q = &w->done;`), for every function of the unit:
// a store or load through such a pointer (`*q = 1`) is an access to its target (V1's K8)
static std::map<tree, std::vector<tree>> alias_targets;

static bool target_keyed(tree d) {
  if (TREE_CODE(d) == FIELD_DECL) return field_keyed(d);
  return interlocked_decls.count(d) || poll_decls.count(d) ||
         (DECL_NAME(d) && key_vars.count(IDENTIFIER_POINTER(DECL_NAME(d))));
}

static bool overlaps_keyed(tree bfr);

static bool keyed(tree t) {
  if (!forwarders_resolved) resolve_forwarders();
  if (key_fields.empty() && key_vars.empty() && interlocked_decls.empty() && poll_decls.empty() && pfield_temps.empty())
    return false;
  for (tree r = t; r; r = handled_component_p(r) ? TREE_OPERAND(r, 0) : NULL_TREE) {
    if (TREE_CODE(r) == COMPONENT_REF && field_keyed(TREE_OPERAND(r, 1))) return true;
    // the front end folds bit-field tests and adjacent narrow compares into a BIT_FIELD_REF over the record
    // (`!f->done`, `c->x == 0 && c->y == 0`): ordered when the bits cover a keyed field (adversarial review 3, 4)
    if (TREE_CODE(r) == BIT_FIELD_REF && overlaps_keyed(r)) return true;
    if (TREE_CODE(r) == VAR_DECL && (interlocked_decls.count(r) || poll_decls.count(r) ||
                                     (DECL_NAME(r) && key_vars.count(IDENTIFIER_POINTER(DECL_NAME(r))))))
      return true;
    if ((TREE_CODE(r) == MEM_REF || TREE_CODE(r) == INDIRECT_REF) && !pfield_temps.empty() &&
        pfield_temps.count(TREE_OPERAND(r, 0)))
      return true;
    if ((TREE_CODE(r) == MEM_REF || TREE_CODE(r) == INDIRECT_REF) && !alias_targets.empty()) {
      tree p = TREE_OPERAND(r, 0);
      auto it = alias_targets.find(p);
      if (it != alias_targets.end())
        for (tree d : it->second)
          if (target_keyed(d)) return true;
    }
    if (!handled_component_p(r)) break;
  }
  return false;
}

// a record (or array of records) with a keyed field somewhere inside: a copy of the whole object stores that field
// too (`*p = *n` over a polled flag), so it is fenced like any ordered access that ldar/stlr cannot do
static bool field_keyed_direct(tree f) {
  if (interlocked_decls.count(f) || poll_decls.count(f)) return true;
  return DECL_NAME(f) && key_fields.count(record_name(f) + "::" + IDENTIFIER_POINTER(DECL_NAME(f)));
}
// a member of a union is the same memory as its keyed sibling (adversarial review 17)
static bool field_keyed(tree f) {
  if (field_keyed_direct(f)) return true;
  tree u = DECL_CONTEXT(f);
  if (u && TREE_CODE(u) == UNION_TYPE)
    for (tree g = TYPE_FIELDS(u); g; g = DECL_CHAIN(g))
      if (TREE_CODE(g) == FIELD_DECL && g != f && field_keyed_direct(g)) return true;
  return false;
}

// the fields of the record a BIT_FIELD_REF reads whose bits it covers
static void overlapped_fields(tree bfr, std::vector<tree> &out) {
  tree base = TREE_OPERAND(bfr, 0), ty = TREE_TYPE(base);
  if (!RECORD_OR_UNION_TYPE_P(ty) || !tree_fits_uhwi_p(TREE_OPERAND(bfr, 1)) || !tree_fits_uhwi_p(TREE_OPERAND(bfr, 2)))
    return;
  unsigned HOST_WIDE_INT sz = tree_to_uhwi(TREE_OPERAND(bfr, 1)), pos = tree_to_uhwi(TREE_OPERAND(bfr, 2));
  for (tree f = TYPE_FIELDS(ty); f; f = DECL_CHAIN(f)) {
    if (TREE_CODE(f) != FIELD_DECL || !DECL_SIZE(f) || !tree_fits_uhwi_p(DECL_SIZE(f)) ||
        !tree_fits_shwi_p(bit_position(f)))
      continue;
    unsigned HOST_WIDE_INT fp = int_bit_position(f), fs = tree_to_uhwi(DECL_SIZE(f));
    if (fp < pos + sz && pos < fp + fs) out.push_back(f);
  }
}
static bool overlaps_keyed(tree bfr) {
  std::vector<tree> fs;
  overlapped_fields(bfr, fs);
  for (tree f : fs)
    if (field_keyed(f)) return true;
  return false;
}
static bool type_keyed(tree ty, int depth = 0) {
  static std::map<tree, bool> memo;
  if (!ty || depth > 8) return false;
  ty = TYPE_MAIN_VARIANT(ty);
  if (TREE_CODE(ty) == ARRAY_TYPE) return type_keyed(TREE_TYPE(ty), depth + 1);
  if (!RECORD_OR_UNION_TYPE_P(ty)) return false;
  auto it = memo.find(ty);
  if (it != memo.end()) return it->second;
  memo[ty] = false;   // recursion guard
  bool k = false;
  for (tree f = TYPE_FIELDS(ty); f && !k; f = DECL_CHAIN(f))
    if (TREE_CODE(f) == FIELD_DECL) k = field_keyed(f) || type_keyed(TREE_TYPE(f), depth + 1);
  memo[ty] = k;
  return k;
}

static bool shared_ordered(tree t) {
  if (!t || !is_memory_ref(t)) return false;
  if (local_unshared(get_base_address(t))) return false;
  if (mode == MODE_TSO) return true;
  if (TREE_THIS_VOLATILE(t) || TYPE_VOLATILE(TREE_TYPE(t)) || keyed(t)) return true;
  if (!AGGREGATE_TYPE_P(TREE_TYPE(t)) || (key_fields.empty() && interlocked_decls.empty() && poll_decls.empty()))
    return false;
  if (type_keyed(TREE_TYPE(t))) return true;
  // a copy the front end turned into bytes (`MEM <unsigned char[6]> [(struct D1 *)a]`, a class with a base): the
  // record type is the MEM_REF's alias pointer type (adversarial review 9)
  if (TREE_CODE(t) == MEM_REF && POINTER_TYPE_P(TREE_TYPE(TREE_OPERAND(t, 1))))
    return type_keyed(TREE_TYPE(TREE_TYPE(TREE_OPERAND(t, 1))));
  return false;
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

static void zero_memory_locals(function *fun);

struct order_pass : gimple_opt_pass {
  order_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}

  unsigned int execute(function *fun) final override {
    zero_memory_locals(fun);
    if (mode == MODE_OFF) return 0;
    pfield_temps.clear();
    if (!pfield_decls.empty() || !key_pfields.empty()) {
      basic_block b0;
      FOR_EACH_BB_FN(b0, fun)
        for (gimple_stmt_iterator g = gsi_start_bb(b0); !gsi_end_p(g); gsi_next(&g)) {
          gimple *st = gsi_stmt(g);
          if (!gimple_assign_single_p(st)) continue;
          tree l = gimple_assign_lhs(st), r = gimple_assign_rhs1(st);
          if ((VAR_P(l) || TREE_CODE(l) == SSA_NAME) && POINTER_TYPE_P(TREE_TYPE(l)) && TREE_CODE(r) == COMPONENT_REF &&
              pfield_keyed(TREE_OPERAND(r, 1)))
            pfield_temps.insert(l);
        }
    }
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

// ---- uninit=zero (default) | keep: W-UNINIT (docs/ORDERING.md "Uninitialized locals")
//
// The decomp reads locals it never wrote (matching devices: `unsigned char qq; f(qq);`, a struct copied from an
// unwritten one, a flag passed through). MSVC passed whatever the register or stack slot held. GCC treats the value
// as undefined and folds through it: `int x; if (c) x = 5; return x == 5;` becomes `return 1`. Right after SSA is
// built, every use of the default definition of a local scalar (a read before any write on some path) gets the
// constant 0 instead: one fixed value, which is a valid stand-in for the junk, and no undefined value left for the
// optimizers. Parameters, the result and memory (addressable) locals are not affected.
static bool uninit_zero = true;
static unsigned long uninit_zeroed;

struct uninit_pass : gimple_opt_pass {
  uninit_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}
  unsigned int execute(function *fun) final override {
    if (!uninit_zero) return 0;
    unsigned i;
    tree name;
    unsigned long c = 0;
    FOR_EACH_SSA_NAME(i, name, fun) {
      if (!SSA_NAME_IS_DEFAULT_DEF(name) || virtual_operand_p(name) || has_zero_uses(name)) continue;
      tree var = SSA_NAME_VAR(name);
      if (!var || TREE_CODE(var) != VAR_DECL || DECL_HARD_REGISTER(var)) continue;
      if (SSA_NAME_OCCURS_IN_ABNORMAL_PHI(name) || !is_gimple_reg_type(TREE_TYPE(name))) continue;
      imm_use_iterator it;
      gimple *use;
      use_operand_p up;
      // an asm operand (the "+r" scratch register of an inline Interlocked loop) must stay a register
      bool in_asm = false;
      FOR_EACH_IMM_USE_FAST(up, it, name)
        if (gimple_code(USE_STMT(up)) == GIMPLE_ASM) in_asm = true;
      if (in_asm) continue;
      tree zero = build_zero_cst(TREE_TYPE(name));
      FOR_EACH_IMM_USE_STMT(use, it, name) {
        FOR_EACH_IMM_USE_ON_STMT(up, it) SET_USE(up, zero);
        if (gimple_code(use) != GIMPLE_PHI) {
          // a use inside &p->b changed: the ADDR_EXPR's invariant flag must follow (-fchecking, review 18)
          for (unsigned k = 0; k < gimple_num_ops(use); k++) {
            tree op = gimple_op(use, k);
            if (op && TREE_CODE(op) == ADDR_EXPR) recompute_tree_invariant_for_addr_expr(op);
          }
          update_stmt(use);
        }
      }
      c++;
    }
    uninit_zeroed += c;
    if (verbose && c)
      inform(DECL_SOURCE_LOCATION(fun->decl), "%<fnvnx_order%>: %lu uninitialized locals read as 0", c);
    return 0;
  }
};

// Memory locals (an aggregate, an address-taken scalar, a small array) are not SSA values yet when the SSA pass
// above runs: SRA and the promotion of address-taken locals make them registers later, with the same undefined value
// (V1's U1-U3: `S s; if (c) s.a = 5; return s.a == 5;` folds to `return 1`). Before SSA, every such local of at most
// 64 bytes that the function reads gets `x = {}` at its entry; dead-store elimination removes it where every read is
// preceded by a write. Its end-of-scope clobbers go too, so a loop-body local keeps its value from one iteration to
// the next, as MSVC's stack slot does (V1's U5). Larger buffers keep their junk: GCC does not scalarize them.
static unsigned long uninit_mem_zeroed;

static tree mem_local_read(tree *tp, int *walk_subtrees, void *data) {
  if (TYPE_P(*tp)) { *walk_subtrees = 0; return NULL_TREE; }
  if (TREE_CODE(*tp) == ADDR_EXPR) { *walk_subtrees = 0; return NULL_TREE; }   // &x is not a read of x
  if (VAR_P(*tp)) ((std::set<tree> *)data)->insert(*tp);
  return NULL_TREE;
}

static bool zero_candidate(function *fun, tree v) {
  if (!VAR_P(v) || TREE_STATIC(v) || DECL_EXTERNAL(v) || DECL_HARD_REGISTER(v) || DECL_CONTEXT(v) != fun->decl) return false;
  if (TREE_THIS_VOLATILE(v) || DECL_THREAD_LOCAL_P(v)) return false;
  tree ty = TREE_TYPE(v);
  if (!(TREE_ADDRESSABLE(v) || AGGREGATE_TYPE_P(ty))) return false;   // registers: the SSA pass
  if (!TYPE_SIZE_UNIT(ty) || !tree_fits_uhwi_p(TYPE_SIZE_UNIT(ty))) return false;
  unsigned HOST_WIDE_INT sz = tree_to_uhwi(TYPE_SIZE_UNIT(ty));
  return sz > 0 && sz <= 64;
}

static void zero_memory_locals(function *fun) {
  if (!uninit_zero) return;
  std::set<tree> read;
  basic_block bb;
  FOR_EACH_BB_FN(bb, fun) {
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
      gimple *st = gsi_stmt(gsi);
      if (gimple_clobber_p(st)) continue;
      if (is_gimple_assign(st)) {
        for (unsigned i = 1; i < gimple_num_ops(st); i++) {
          tree op = gimple_op(st, i);
          if (op) walk_tree(&op, mem_local_read, &read, NULL);
        }
        tree lhs = gimple_assign_lhs(st);   // a store into part of x through x.f[i] reads nothing of x
        if (lhs && handled_component_p(lhs))
          for (tree r = lhs; handled_component_p(r); r = TREE_OPERAND(r, 0))
            for (int k = 1; k < TREE_OPERAND_LENGTH(r); k++)
              if (TREE_OPERAND(r, k)) walk_tree(&TREE_OPERAND(r, k), mem_local_read, &read, NULL);
      } else {
        for (unsigned i = 0; i < gimple_num_ops(st); i++) {
          tree op = gimple_op(st, i);
          if (!op) continue;
          if (is_gimple_call(st) && op == gimple_call_lhs(st)) continue;
          walk_tree(&op, mem_local_read, &read, NULL);
        }
      }
    }
  }
  std::vector<tree> zero;
  for (tree v : read)
    if (zero_candidate(fun, v)) zero.push_back(v);
  if (zero.empty()) return;
  std::set<tree> zs(zero.begin(), zero.end());
  FOR_EACH_BB_FN(bb, fun) {
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi);) {
      gimple *st = gsi_stmt(gsi);
      if (gimple_clobber_p(st) && zs.count(gimple_assign_lhs(st))) gsi_remove(&gsi, true);
      else gsi_next(&gsi);
    }
  }
  basic_block entry = single_succ(ENTRY_BLOCK_PTR_FOR_FN(fun));
  if (!single_pred_p(entry))   // the first block is also a loop header: zero once, on the way in
    entry = split_edge(single_succ_edge(ENTRY_BLOCK_PTR_FOR_FN(fun)));
  gimple_stmt_iterator gsi = gsi_after_labels(entry);
  for (tree v : zero) {
    tree ty = TREE_TYPE(v);
    tree init = AGGREGATE_TYPE_P(ty) ? build_constructor(ty, NULL) : build_zero_cst(ty);
    gimple *g = gimple_build_assign(v, init);
    gsi_insert_before(&gsi, g, GSI_SAME_STMT);
  }
  uninit_mem_zeroed += zero.size();
}

static const pass_data uninit_data = {
    GIMPLE_PASS, "fnvnx_uninit", OPTGROUP_NONE, TV_NONE, PROP_ssa | PROP_cfg, 0, 0, 0, TODO_update_ssa | TODO_cleanup_cfg,
};

static const pass_data order_data = {
    GIMPLE_PASS, "fnvnx_order", OPTGROUP_NONE, TV_NONE, PROP_gimple_any | PROP_cfg, 0, 0, 0, 0,
};

// PLUGIN_FINISH: runs under -fsyntax-only too (FINISH_UNIT does not), so keys.sh can discover without code generation
static void finish_all(void *, void *) {
  if (discover_path) write_discovered();
}

static void finish(void *, void *) {
  if (verbose)
    inform(UNKNOWN_LOCATION, "%<fnvnx_order%>: unit: %lu ldar, %lu stlr, %lu fenced loads, %lu fenced stores; "
           "%lu calls with arguments right to left, %lu left (class-type argument with side effects); "
           "Interlocked: %lu refcount increments relaxed, %lu refcount decrements release, %lu full; "
           "%lu Interlocked operands (%lu forwarders), %lu plain polls",
           unit_total.ldar, unit_total.stlr, unit_total.fence_ld, unit_total.fence_st, calls_reordered, calls_skipped,
           ilk_inc_weak, ilk_dec_weak, ilk_full, (unsigned long)interlocked_decls.size(),
           (unsigned long)forwarders.size(), polls_plain);
  if (verbose && (object_last || operator_rtl))
    inform(UNKNOWN_LOCATION, "%<fnvnx_order%>: unit: %lu calls with the object after the arguments, "
           "%lu overloaded operators right to left", calls_object_last, calls_operator);
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

// object=last: how the object of a member call can move after its arguments. 1: a direct call; the object is
// argument 0 and joins the argument chain as its last element. 2: a virtual call; the chain goes in front of the
// OBJ_TYPE_REF's object, so the arguments run before the object and its vtable load. 0: neither (object=first, a
// constructor or destructor, a call through a pointer to member or another computed callee). The callee itself is
// never replaced: a COMPOUND_EXPR there would make a direct call indirect.
// object=msvc follows MSVC 2008 as recorded in all five configurations (S11, tests/s11/order/order.msvc): a direct
// member call (member operators included) evaluates its arguments, then the object (1); a virtual call evaluates the
// object first (0: GCC's order); a call through a pointer to member evaluates its arguments, then object and member
// pointer (3: the chain goes in front of the computed callee, which is an indirect call anyway).
static int object_kind(tree call, tree fn) {
  if (!(object_last || object_msvc) || !fn || arg_count(call) < 1) return 0;
  if (TREE_CODE(call) == AGGR_INIT_EXPR && AGGR_INIT_VIA_CTOR_P(call)) return 0;
  if (TREE_CODE(fn) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(fn, 0)) == FUNCTION_DECL) {
    tree d = TREE_OPERAND(fn, 0);
    return DECL_CONSTRUCTOR_P(d) || DECL_DESTRUCTOR_P(d) ? 0 : 1;
  }
  if (TREE_CODE(fn) == OBJ_TYPE_REF) return object_msvc ? 0 : 2;
  return object_msvc && TREE_CODE(call) == CALL_EXPR ? 3 : 0;
}

// an object no argument can change: a constant, or the address of a declaration
static bool invariant_object(tree t) {
  while (CONVERT_EXPR_P(t) || TREE_CODE(t) == NON_LVALUE_EXPR) t = TREE_OPERAND(t, 0);
  return TREE_CONSTANT(t) || (TREE_CODE(t) == ADDR_EXPR && DECL_P(TREE_OPERAND(t, 0)));
}

static void reorder_call(tree call) {
  int n = arg_count(call);
  int first = 0;
  // `this` (first argument of a method call, and the object of a constructor call) stays first, unless object=last
  tree fntype = NULL_TREE;
  tree fn = TREE_CODE(call) == CALL_EXPR ? CALL_EXPR_FN(call) : AGGR_INIT_EXPR_FN(call);
  if (fn && POINTER_TYPE_P(TREE_TYPE(fn))) fntype = TREE_TYPE(TREE_TYPE(fn));
  bool op = false;
  if (TREE_CODE(call) == CALL_EXPR) {
    // `=` and `@=` (CALL_EXPR_REVERSE_ARGS): C++17 evaluates the right operand, then the left one (a member
    // operator's object). That is MSVC's push order too, with either object= setting: left as it is.
    if (CALL_EXPR_REVERSE_ARGS(call)) return;
    // `<<`, `>>`, `[]`, `->*` (CALL_EXPR_ORDERED_ARGS): C++17 left to right; operator=rtl orders them as any call
    if (CALL_EXPR_ORDERED_ARGS(call)) {
      if (!operator_rtl) return;
      op = true;
    }
  }
  if (fntype && TREE_CODE(fntype) == METHOD_TYPE) first = 1;
  int okind = first ? object_kind(call, fn) : 0;
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
  if (okind == 3) {
    // pointer to member: the callee holds the object and the member pointer; the arguments go in front of it
    if (!TREE_SIDE_EFFECTS(fn) && !effects) okind = 0;
  } else if (okind) {
    // the object moves only where it and an argument can see each other: one has side effects, the other is live
    tree obj = *arg_slot(call, 0);
    if (!scalar_arg(obj) || !((TREE_SIDE_EFFECTS(obj) && live) || (!invariant_object(obj) && effects))) okind = 0;
  }
  if (!okind && (!effects || live < 2)) return;
  int lead = -1;
  for (int i = okind == 1 ? 0 : first; i < n; i++) {
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
  if (okind == 3) {
    CALL_EXPR_FN(call) = build2(COMPOUND_EXPR, TREE_TYPE(fn), chain, fn);
  } else if (okind == 2) {
    // GCC gimplifies the callee before the arguments, and an OBJ_TYPE_REF's object before its vtable load: with
    // the chain in front of that object, the arguments, the object and the vtable load run in this order
    tree &o = OBJ_TYPE_REF_OBJECT(fn);
    o = build2(COMPOUND_EXPR, TREE_TYPE(o), chain, o);
  } else {
    *ls = chain;
  }
  if (op) {
    CALL_EXPR_ORDERED_ARGS(call) = 0;   // gimplified as a plain call now: the chain decides the order
    calls_operator++;
  }
  if (okind) calls_object_last++;
  calls_reordered++;
}

// ---- Interlocked operands of this unit

static tree current_fn;   // the body PRE_GENERICIZE is walking

static int parm_index(tree fndecl, tree parm) {
  int i = 0;
  for (tree p = DECL_ARGUMENTS(fndecl); p; p = DECL_CHAIN(p), i++)
    if (p == parm) return i;
  return -1;
}

// What a pointer argument designates: a field (`&obj.m`, `&p->m[2]`, or `obj.m` bound to a reference parameter), a
// variable (`&g`), or a parameter of the current function (`p`, `&*r` for a reference r); NULL_TREE otherwise.
static bool local_pointer(tree a) {
  return a && TREE_CODE(a) == VAR_DECL && !TREE_STATIC(a) && !DECL_EXTERNAL(a) && POINTER_TYPE_P(TREE_TYPE(a));
}

// local pointers of the function being walked: what they were set to (`long *p = &o->m;`, `p = q` from a parameter)
static std::map<tree, std::vector<tree>> local_addr;
static std::map<tree, int> local_parm;
struct local_use { tree local; tree callee; int arg; };   // callee NULL_TREE: handed to Interlocked*
static std::vector<local_use> local_uses;
static tree operand_local;   // set by operand_of when the argument is such a local pointer

static tree operand_of(tree a, int *parm) {
  *parm = -1;
  operand_local = NULL_TREE;
  while (CONVERT_EXPR_P(a) || TREE_CODE(a) == NON_LVALUE_EXPR || TREE_CODE(a) == VIEW_CONVERT_EXPR) a = TREE_OPERAND(a, 0);
  // `o->cnt + i`, `g_cnt + i`, `&arr[i]` on a pointer parameter (folded to p + i*4): the base designates it (review 7, 12)
  while (TREE_CODE(a) == POINTER_PLUS_EXPR) {
    a = TREE_OPERAND(a, 0);   // the front end wraps a declaration in a VIEW_CONVERT_EXPR location wrapper here
    while (CONVERT_EXPR_P(a) || TREE_CODE(a) == NON_LVALUE_EXPR || TREE_CODE(a) == VIEW_CONVERT_EXPR) a = TREE_OPERAND(a, 0);
  }
  if (local_pointer(a)) { operand_local = a; return NULL_TREE; }
  if (TREE_CODE(a) == PARM_DECL && current_fn) {
    if (POINTER_TYPE_P(TREE_TYPE(a))) *parm = parm_index(current_fn, a);
    return NULL_TREE;
  }
  if (TREE_CODE(a) != ADDR_EXPR) return NULL_TREE;
  tree r = TREE_OPERAND(a, 0);
  while (TREE_CODE(r) == ARRAY_REF || TREE_CODE(r) == VIEW_CONVERT_EXPR) r = TREE_OPERAND(r, 0);
  if (TREE_CODE(r) == COMPONENT_REF) return TREE_OPERAND(r, 1);
  if (TREE_CODE(r) == VAR_DECL) return r;
  if ((TREE_CODE(r) == INDIRECT_REF || TREE_CODE(r) == MEM_REF) && current_fn) {
    tree p = TREE_OPERAND(r, 0);
    while (CONVERT_EXPR_P(p)) p = TREE_OPERAND(p, 0);
    if (TREE_CODE(p) == PARM_DECL) *parm = parm_index(current_fn, p);
  }
  return NULL_TREE;
}

// the field at offset 0 of the record a pointer of another type points to: `InterlockedCompareExchange((LONG *)this,
// 1, 0)` locks the object's first member, which its own `m_state = 0` then unlocks (adversarial review 8)
static tree first_field_of(tree a) {
  tree inner = a;
  while (CONVERT_EXPR_P(inner) || TREE_CODE(inner) == NON_LVALUE_EXPR) inner = TREE_OPERAND(inner, 0);
  if (inner == a || !POINTER_TYPE_P(TREE_TYPE(inner))) return NULL_TREE;
  tree rec = TREE_TYPE(TREE_TYPE(inner));
  for (int depth = 0; rec && RECORD_OR_UNION_TYPE_P(rec) && depth < 8; depth++) {
    tree f = TYPE_FIELDS(rec);
    while (f && (TREE_CODE(f) != FIELD_DECL || !integer_zerop(bit_position(f)))) f = DECL_CHAIN(f);
    if (!f) return NULL_TREE;
    if (!DECL_FIELD_IS_BASE(f) && !RECORD_OR_UNION_TYPE_P(TREE_TYPE(f))) return f;
    rec = TREE_TYPE(f);   // a base subobject or nested record at offset 0: its first member
  }
  return NULL_TREE;
}

static void ilk_operand(tree a, int depth) {
  tree s0 = a;
  while (CONVERT_EXPR_P(s0) || TREE_CODE(s0) == NON_LVALUE_EXPR) s0 = TREE_OPERAND(s0, 0);
  if (TREE_CODE(s0) == COND_EXPR && depth < 4) {   // c ? &a->m : &b->m (review 14)
    ilk_operand(TREE_OPERAND(s0, 1), depth + 1);
    ilk_operand(TREE_OPERAND(s0, 2), depth + 1);
    return;
  }
  if (tree f = first_field_of(a)) interlocked_decls.insert(f);
  if (TREE_CODE(s0) == COMPONENT_REF && POINTER_TYPE_P(TREE_TYPE(s0))) pfield_decls.insert(TREE_OPERAND(s0, 1));
  int parm;
  tree d = operand_of(a, &parm);
  if (d) interlocked_decls.insert(d);
  else if (parm >= 0) forwarders.insert(std::make_pair(current_fn, parm));
  else if (operand_local) local_uses.push_back({operand_local, NULL_TREE, 0});
}

static void note_interlocked(tree call) {
  tree fn = get_callee_fndecl(call);
  if (!fn || !DECL_NAME(fn) || call_expr_nargs(call) < 1) return;
  const char *n = IDENTIFIER_POINTER(DECL_NAME(fn));
  bool ilk = !strncmp(n, "Interlocked", 11) || !strncmp(n, "_Interlocked", 12);
  if (ilk) {
    ilk_operand(CALL_EXPR_ARG(call, 0), 0);
    return;
  }
  // any other direct call: remember which fields / variables / own parameters it binds to which argument, in case
  // the callee turns out to be a forwarder (decided after the whole unit is seen)
  if (DECL_IS_UNDECLARED_BUILTIN(fn) || fndecl_built_in_p(fn)) return;
  for (int i = 0; i < call_expr_nargs(call); i++) {
    tree a = CALL_EXPR_ARG(call, i);
    if (!POINTER_TYPE_P(TREE_TYPE(a)) && TREE_CODE(TREE_TYPE(a)) != REFERENCE_TYPE) continue;
    int parm;
    tree d = operand_of(a, &parm);
    if (d) decl_calls.push_back({fn, i, d});
    else if (parm >= 0) param_calls.push_back({current_fn, parm, fn, i});
    else if (operand_local) local_uses.push_back({operand_local, fn, i});
  }
}

// `p = &o->m` / `long *p = &o->m;` / `p = param` for a local pointer p
static void note_local_set(tree lhs, tree rhs) {
  if (!local_pointer(lhs) || !rhs) return;
  int parm;
  tree d = operand_of(rhs, &parm);
  if (d) { local_addr[lhs].push_back(d); alias_targets[lhs].push_back(d); }
  else if (parm >= 0) local_parm[lhs] = parm;
  else if (operand_local && operand_local != lhs) {   // p = q: q's targets
    for (tree x : local_addr[operand_local]) { local_addr[lhs].push_back(x); alias_targets[lhs].push_back(x); }
    auto it = local_parm.find(operand_local);
    if (it != local_parm.end()) local_parm[lhs] = it->second;
  }
}

static void resolve_locals() {
  for (const local_use &u : local_uses) {
    for (tree d : local_addr[u.local]) {
      if (!u.callee) interlocked_decls.insert(d);
      else decl_calls.push_back({u.callee, u.arg, d});
    }
    auto it = local_parm.find(u.local);
    if (it != local_parm.end()) {
      if (!u.callee) forwarders.insert(std::make_pair(current_fn, it->second));
      else param_calls.push_back({current_fn, it->second, u.callee, u.arg});
    }
  }
  local_addr.clear();
  local_parm.clear();
  local_uses.clear();
}

static void resolve_forwarders() {
  forwarders_resolved = true;
  if (forwarders.empty()) return;
  for (bool grew = true; grew;) {   // forwarders of forwarders
    grew = false;
    for (const param_call &c : param_calls)
      if (forwarders.count(std::make_pair(c.callee, c.arg)) &&
          forwarders.insert(std::make_pair(c.caller, c.caller_parm)).second)
        grew = true;
  }
  for (const decl_call &c : decl_calls)
    if (forwarders.count(std::make_pair(c.callee, c.arg))) interlocked_decls.insert(c.decl);
}

// ---- POLL-PLAIN: a loop that only waits for plain shared memory to change (docs/ORDERING.md)
//
// `while (!m_bDone) Sleep(0);`, `while (g_count < n) {}`: the condition reads fields or non-const globals, has no call
// or side effect of its own, and the body does nothing but wait (empty, Sleep / SleepEx / SwitchToThread /
// YieldProcessor / _mm_pause / WaitFor* / fnvnx_yield). Under x86 TSO and MSVC such a loop works: the store of another
// thread becomes visible and MSVC rereads memory after a call. GCC may hoist the load out of an empty loop, and the
// A57 gives the flag no order with the data it guards. The polled fields and variables are ordered like volatiles
// (ldar in the loop, stlr wherever they are stored: in other units through the keys file, discover=).

static bool wait_call(tree fn) {
  if (!fn || !DECL_NAME(fn)) return false;
  const char *n = IDENTIFIER_POINTER(DECL_NAME(fn));
  return !strcmp(n, "Sleep") || !strcmp(n, "SleepEx") || !strcmp(n, "SwitchToThread") || !strcmp(n, "YieldProcessor") ||
         !strcmp(n, "_mm_pause") || !strncmp(n, "WaitFor", 7) || !strcmp(n, "fnvnx_yield");
}

struct poll_scan { bool waited, other, vol; std::vector<tree> shared; };

static tree poll_body_walk(tree *tp, int *walk_subtrees, void *data) {
  poll_scan *s = (poll_scan *)data;
  tree t = *tp;
  switch (TREE_CODE(t)) {
    case CALL_EXPR:
      if (wait_call(get_callee_fndecl(t))) s->waited = true; else s->other = true;
      break;
    case AGGR_INIT_EXPR: case MODIFY_EXPR: case INIT_EXPR: case PREINCREMENT_EXPR: case PREDECREMENT_EXPR:
    case POSTINCREMENT_EXPR: case POSTDECREMENT_EXPR: case ASM_EXPR: case TARGET_EXPR: case DECL_EXPR:
    case RETURN_EXPR: case THROW_EXPR:
      s->other = true;
      break;
    default: break;
  }
  if (s->other) return t;   // stop
  (void)walk_subtrees;
  return NULL_TREE;
}

static tree poll_cond_walk(tree *tp, int *walk_subtrees, void *data) {
  poll_scan *s = (poll_scan *)data;
  tree t = *tp;
  if (TYPE_P(t)) { *walk_subtrees = 0; return NULL_TREE; }
  if (TREE_THIS_VOLATILE(t) && (TREE_CODE(t) == COMPONENT_REF || TREE_CODE(t) == VAR_DECL || TREE_CODE(t) == INDIRECT_REF ||
                                TREE_CODE(t) == MEM_REF))
    s->vol = true;
  switch (TREE_CODE(t)) {
    case COMPONENT_REF:
      if (TREE_CODE(TREE_OPERAND(t, 1)) == FIELD_DECL) s->shared.push_back(TREE_OPERAND(t, 1));
      break;
    case VAR_DECL:
      if ((TREE_STATIC(t) || DECL_EXTERNAL(t)) && !TREE_READONLY(t) && !DECL_THREAD_LOCAL_P(t)) s->shared.push_back(t);
      break;
    case ADDR_EXPR:   // &x is not a read of x
      *walk_subtrees = 0;
      break;
    case BIT_FIELD_REF: {   // a folded bit-field test: the fields its bits cover
      std::vector<tree> fs;
      overlapped_fields(t, fs);
      for (tree f : fs) s->shared.push_back(f);
      break;
    }
    case INDIRECT_REF: case MEM_REF: {
      // `*(volatile BOOL *)&f->m_done` reads f->m_done; `*q` reads what the local pointer / reference q was set to
      tree p = TREE_OPERAND(t, 0);
      while (CONVERT_EXPR_P(p) || TREE_CODE(p) == NON_LVALUE_EXPR || TREE_CODE(p) == VIEW_CONVERT_EXPR) p = TREE_OPERAND(p, 0);
      if (TREE_CODE(p) == ADDR_EXPR) {
        tree r = TREE_OPERAND(p, 0);
        while (TREE_CODE(r) == ARRAY_REF) r = TREE_OPERAND(r, 0);
        if (TREE_CODE(r) == COMPONENT_REF) s->shared.push_back(TREE_OPERAND(r, 1));
        else if (VAR_P(r) && (TREE_STATIC(r) || DECL_EXTERNAL(r))) s->shared.push_back(r);
      } else if (local_pointer(p)) {
        auto it = local_addr.find(p);
        if (it != local_addr.end())
          for (tree d : it->second) s->shared.push_back(d);
      }
      break;
    }
    default: break;
  }
  return poll_body_walk(tp, walk_subtrees, data);
}

// `if (flag) break;` as a statement of an endless loop's body: the break's condition
static tree break_cond(tree t) {
  if (TREE_CODE(t) != IF_STMT || ELSE_CLAUSE(t)) return NULL_TREE;
  tree th = THEN_CLAUSE(t);
  while (th && TREE_CODE(th) == STATEMENT_LIST && tsi_one_before_end_p(tsi_start(th))) th = tsi_stmt(tsi_start(th));
  return th && TREE_CODE(th) == BREAK_STMT ? IF_COND(t) : NULL_TREE;
}

static void poll_endless(tree loop, tree body);

static void poll_loop(tree loop, tree cond, tree body, tree incr = NULL_TREE) {
  if (!cond || (TREE_CODE(cond) == INTEGER_CST && !integer_zerop(cond))) {
    if (!incr) poll_endless(loop, body);
    return;
  }
  poll_scan c = {false, false, false, {}};
  walk_tree_without_duplicates(&cond, poll_cond_walk, &c);
  // a scan or a call: not a poll. A volatile poll (POLL-VOLATILE) is ordered on the reader's side already, but its
  // writer may be another unit's plain store: its fields are keyed too (adversarial review 2)
  if (c.other || c.waited || c.shared.empty()) return;
  poll_scan b = {false, false, false, {}};
  if (body) walk_tree_without_duplicates(&body, poll_body_walk, &b);
  if (incr) walk_tree_without_duplicates(&incr, poll_body_walk, &b);   // for (; !flag; Sleep(0)); (review 11)
  if (b.other) return;
  for (tree d : c.shared) poll_decls.insert(d);
  polls_plain++;
  if (verbose_sites)
    inform(EXPR_LOCATION(loop) != UNKNOWN_LOCATION ? EXPR_LOCATION(loop) : DECL_SOURCE_LOCATION(current_fn),
           "%<fnvnx_order%>: plain poll (%s): %qD ordered", b.waited ? "wait" : "spin", c.shared[0]);
}

// `for (;;) { if (flag) break; Sleep(0); }`: the same poll with its test inside an endless loop. Every statement of
// the body is either such a break test or a wait; the break conditions are judged like a loop condition.
static void poll_endless(tree loop, tree body) {
  if (!body) return;
  while (TREE_CODE(body) == BIND_EXPR) body = BIND_EXPR_BODY(body);
  std::vector<tree> conds, rest;
  if (TREE_CODE(body) == STATEMENT_LIST) {
    for (tree_stmt_iterator i = tsi_start(body); !tsi_end_p(i); tsi_next(&i)) {
      tree st = tsi_stmt(i);
      if (TREE_CODE(st) == CLEANUP_POINT_EXPR) st = TREE_OPERAND(st, 0);
      tree c = break_cond(st);
      if (c) conds.push_back(c); else rest.push_back(st);
    }
  } else {
    tree c = break_cond(body);
    if (!c) return;
    conds.push_back(c);
  }
  if (conds.empty()) return;
  poll_scan c = {false, false, false, {}};
  for (tree &x : conds) walk_tree_without_duplicates(&x, poll_cond_walk, &c);
  if (c.other || c.waited || c.vol || c.shared.empty()) return;
  poll_scan b = {false, false, false, {}};
  for (tree &x : rest) walk_tree_without_duplicates(&x, poll_body_walk, &b);
  if (b.other) return;
  for (tree d : c.shared) poll_decls.insert(d);
  polls_plain++;
  if (verbose_sites)
    inform(EXPR_LOCATION(loop) != UNKNOWN_LOCATION ? EXPR_LOCATION(loop) : DECL_SOURCE_LOCATION(current_fn),
           "%<fnvnx_order%>: plain poll (%s, break test): %qD ordered", b.waited ? "wait" : "spin", c.shared[0]);
}

static void poll_contexts(tree t) {
  switch (TREE_CODE(t)) {
    case WHILE_STMT: poll_loop(t, WHILE_COND(t), WHILE_BODY(t)); break;
    case DO_STMT: poll_loop(t, DO_COND(t), DO_BODY(t)); break;
    case FOR_STMT:
      if (!FOR_INIT_STMT(t)) poll_loop(t, FOR_COND(t), FOR_BODY(t), FOR_EXPR(t));
      break;
    default: break;
  }
}

// ---- discover=<file>: this unit's keys, for the whole-program key list (toolchain/order/keys.sh)

static std::string key_of(tree d) {
  if (!DECL_NAME(d)) return "";
  const char *n = IDENTIFIER_POINTER(DECL_NAME(d));
  if (TREE_CODE(d) == FIELD_DECL) {
    std::string r = record_name(d);
    if (!r.empty() && !IDENTIFIER_ANON_P(DECL_NAME(d))) return "field " + r + "::" + n;
  } else if (TREE_CODE(d) == VAR_DECL && (TREE_STATIC(d) || DECL_EXTERNAL(d)) && !DECL_THREAD_LOCAL_P(d)) {
    return std::string("var ") + n;
  }
  return "";
}
static void key_line(std::set<std::string> &out, tree d) {
  std::string k = key_of(d);
  if (!k.empty()) out.insert(k);
}
static std::string fn_id(tree fn) {
  return fn && DECL_ASSEMBLER_NAME(fn) ? IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fn)) : "";
}

// Besides the unit's own keys, the facts another unit may complete (toolchain/order/keys_merge.py resolves them
// over the whole program): a forwarder defined here and called elsewhere, a call here to a forwarder defined
// elsewhere.  fwd <fn> <i>            fn hands (the pointee of) parameter i to Interlocked*
//         pcall <f> <j> <g> <i>     f passes its parameter j to g's parameter i
//         call <g> <i> <key>        a call of g binds <key> (field ... / var ...) to parameter i
static void write_discovered() {
  if (!forwarders_resolved) resolve_forwarders();
  std::set<std::string> lines;
  for (tree d : interlocked_decls) key_line(lines, d);
  for (tree d : poll_decls) key_line(lines, d);
  for (tree d : pfield_decls) {
    std::string k = key_of(d);
    if (k.compare(0, 6, "field ") == 0) lines.insert("pfield " + k.substr(6));
  }
  for (const auto &f : forwarders)
    if (TREE_PUBLIC(f.first) || DECL_COMDAT(f.first)) lines.insert("fwd " + fn_id(f.first) + " " + std::to_string(f.second));
  for (const param_call &c : param_calls)
    if (!forwarders.count(std::make_pair(c.callee, c.arg)) && (TREE_PUBLIC(c.callee) || DECL_COMDAT(c.callee)))
      lines.insert("pcall " + fn_id(c.caller) + " " + std::to_string(c.caller_parm) + " " + fn_id(c.callee) + " " +
                   std::to_string(c.arg));
  for (const decl_call &c : decl_calls) {
    if (forwarders.count(std::make_pair(c.callee, c.arg)) || !(TREE_PUBLIC(c.callee) || DECL_COMDAT(c.callee))) continue;
    std::string k = key_of(c.decl);
    if (!k.empty()) lines.insert("call " + fn_id(c.callee) + " " + std::to_string(c.arg) + " " + k);
  }
  std::ofstream out(discover_path, std::ios::trunc);
  if (!out) { error("%<fnvnx_order%>: cannot write %s", discover_path); return; }
  for (const std::string &l : lines) out << l << "\n";
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

// assign=msvc: a built-in compound assignment `*p() += a()` evaluates its left operand first under MSVC 2008 (all five
// recorded configurations: "pa"); C++17 makes GCC evaluate the right one first. The front end has already put the
// left operand's side effects into SAVE_EXPRs that the right side shares (the `lhs = lhs + rhs` form): they are
// evaluated in front of the assignment. A plain `=` is left alone: MSVC's own order differs between /O2 and /Od.
static std::set<tree> assigns_done;
static unsigned long assigns_reordered;

static tree collect_saves(tree *tp, int *walk_subtrees, void *data) {
  if (TYPE_P(*tp)) { *walk_subtrees = 0; return NULL_TREE; }
  if (TREE_CODE(*tp) == SAVE_EXPR) {
    if (TREE_SIDE_EFFECTS(TREE_OPERAND(*tp, 0))) ((std::vector<tree> *)data)->push_back(*tp);
    *walk_subtrees = 0;
  }
  return NULL_TREE;
}
static tree find_tree(tree *tp, int *, void *data) { return *tp == (tree)data ? *tp : NULL_TREE; }

static void compound_assign(tree *tp) {
  tree t = *tp;
  if (!assign_msvc || TREE_CODE(t) != MODIFY_EXPR || assigns_done.count(t)) return;
  tree lhs = TREE_OPERAND(t, 0), rhs = TREE_OPERAND(t, 1);
  if (!TREE_SIDE_EFFECTS(lhs) || !TREE_SIDE_EFFECTS(rhs) || !scalar_arg(lhs)) return;
  std::vector<tree> saves;
  walk_tree_without_duplicates(&lhs, collect_saves, &saves);
  tree chain = NULL_TREE;
  for (tree sv : saves)
    if (walk_tree_without_duplicates(&rhs, find_tree, sv))   // shared by both sides: the compound form
      chain = chain ? build2(COMPOUND_EXPR, void_type_node, chain, sv) : sv;
  if (!chain) return;
  assigns_done.insert(t);
  *tp = build2(COMPOUND_EXPR, TREE_TYPE(t), chain, t);
  assigns_reordered++;
}

static tree order_args_walk(tree *tp, int *, void *) {
  compound_assign(tp);
  refcount_contexts(*tp);
  poll_contexts(*tp);
  if (TREE_CODE(*tp) == MODIFY_EXPR || TREE_CODE(*tp) == INIT_EXPR) note_local_set(TREE_OPERAND(*tp, 0), TREE_OPERAND(*tp, 1));
  else if (TREE_CODE(*tp) == DECL_EXPR && TREE_CODE(DECL_EXPR_DECL(*tp)) == VAR_DECL)
    note_local_set(DECL_EXPR_DECL(*tp), DECL_INITIAL(DECL_EXPR_DECL(*tp)));
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
  current_fn = fndecl;
  walk_tree_without_duplicates(&DECL_SAVED_TREE(fndecl), order_args_walk, NULL);
  resolve_locals();
  current_fn = NULL_TREE;
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
    } else if (line.compare(0, 7, "pfield ") == 0) {
      std::string k = line.substr(7);
      size_t last = k.rfind("::");
      if (last == std::string::npos) continue;
      size_t prev = last == 0 ? std::string::npos : k.rfind("::", last - 1);
      key_pfields.insert(prev == std::string::npos ? k : k.substr(prev + 2));
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
    else if (!strcmp(k, "discover") && v) discover_path = xstrdup(v);
    else if (!strcmp(k, "uninit") && v) {
      if (!strcmp(v, "zero")) uninit_zero = true;
      else if (!strcmp(v, "keep")) uninit_zero = false;
      else { error("%<fnvnx_order%>: unknown uninit mode %s", v); return 1; }
    }
    else if (!strcmp(k, "refcount") && v) {
      if (!strcmp(v, "weak")) refcount_weak = true;
      else if (!strcmp(v, "full")) refcount_weak = false;
      else { error("%<fnvnx_order%>: unknown refcount mode %s", v); return 1; }
    } else if (!strcmp(k, "args") && v) {
      if (!strcmp(v, "rtl")) args_rtl = true;
      else if (!strcmp(v, "ltr")) args_rtl = false;
      else { error("%<fnvnx_order%>: unknown argument order %s", v); return 1; }
    } else if (!strcmp(k, "object") && v) {
      if (!strcmp(v, "first")) object_last = object_msvc = false;
      else if (!strcmp(v, "last")) { object_last = true; object_msvc = false; }
      else if (!strcmp(v, "msvc")) { object_msvc = true; object_last = false; }
      else { error("%<fnvnx_order%>: unknown object order %s", v); return 1; }
    } else if (!strcmp(k, "operator") && v) {
      if (!strcmp(v, "cpp17")) operator_rtl = false;
      else if (!strcmp(v, "rtl")) operator_rtl = true;
      else { error("%<fnvnx_order%>: unknown operator order %s", v); return 1; }
    } else if (!strcmp(k, "assign") && v) {
      if (!strcmp(v, "cpp17")) assign_msvc = false;
      else if (!strcmp(v, "msvc")) assign_msvc = true;
      else { error("%<fnvnx_order%>: unknown assignment order %s", v); return 1; }
    }
    else { error("%<fnvnx_order%>: unknown argument %s", k); return 1; }
  }
  struct register_pass_info pass;
  pass.pass = new order_pass(g, order_data);
  pass.reference_pass_name = "cfg";
  pass.ref_pass_instance_number = 1;
  pass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pass);
  struct register_pass_info upass;
  upass.pass = new uninit_pass(g, uninit_data);
  upass.reference_pass_name = "early_uninit";   // SSA is complete; GCC's own uninit warning and every optimizer come later
  upass.ref_pass_instance_number = 1;
  upass.pos_op = PASS_POS_INSERT_BEFORE;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &upass);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  register_callback(info->base_name, PLUGIN_FINISH, finish_all, NULL);
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, pre_genericize, NULL);
  return 0;
}
