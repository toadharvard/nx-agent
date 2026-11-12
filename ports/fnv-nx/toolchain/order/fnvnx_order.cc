// fnvnx_order.cc - GCC plugin: MSVC x86 evaluation order and TSO memory ordering for decomp units on AArch64
// (docs/ORDERING.md).
//
//   g++ ... -fplugin=<dir>/fnvnx_order.so [-fplugin-arg-fnvnx_order-verbose[=sites]] [-fplugin-arg-fnvnx_order-args=rtl|ltr]
//           [-fplugin-arg-fnvnx_order-object=first|last|msvc] [-fplugin-arg-fnvnx_order-operator=cpp17|rtl]
//           [-fplugin-arg-fnvnx_order-assign=cpp17|msvc] unit.cpp
//
// Memory ordering (R9 TSO-MAP) has one rule and no option: every load and store of memory that is not a proven local
// of the function (an automatic variable whose address is never taken) is ordered; the hardware is x86 TSO, the
// Cortex-A57 reorders freely. volatile is no mark: it adds nothing, every shared access is ordered.
//
//   load  of shared memory  -> __atomic_load_N (p, ACQUIRE)   = ldar / ldarh / ldarb
//   store to shared memory  -> __atomic_store_N(p, v, RELEASE) = stlr / stlrh / stlrb
//
// Objects that are not a naturally aligned 1/2/4/8-byte scalar (bit-fields, packed or misaligned fields - ldar
// faults on misalignment where x86 does not -, aggregates, vectors) keep their plain access with a fence:
// load; dmb ishld (acquire), and dmb ish; store (release). Interlocked* keep the full x86 lock semantics (inline
// ldaxr/stlxr loops and dmb). The one object not ordered is a constant the compiler itself places in a read-only
// section (readonly_object): nothing ever writes it.
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
#include "varasm.h"
#include "output.h"
#include "attr-fnspec.h"
#include "gimple-walk.h"
#include <set>
#include <string>
#include <vector>
#include <utility>

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

static bool verbose, verbose_sites;
struct counts { unsigned long ldar, stlr, fence_ld, fence_st; };
static counts unit_total;
static bool args_rtl = true;
static unsigned long calls_reordered, calls_skipped;
static bool object_last = false;                         // object=first (default) | last | msvc: ORDER-OBJECT
static bool object_msvc = false;                         // object=msvc: MSVC 2008's table (tests/s11/order/order.msvc)
static bool assign_msvc = false;                         // assign=cpp17 (default) | msvc: built-in compound assignment
static bool operator_rtl = false;                        // operator=cpp17 (default) | rtl: ORDER-OPERATOR
static unsigned long calls_object_last, calls_operator, calls_prvalue_virtual, calls_fnptr;

// ---- what counts as shared memory

static bool local_unshared(tree base) {
  if (!base) return true;
  if (TREE_CODE(base) == VAR_DECL || TREE_CODE(base) == PARM_DECL || TREE_CODE(base) == RESULT_DECL)
    return !TREE_STATIC(base) && !DECL_EXTERNAL(base) && !TREE_ADDRESSABLE(base) && !DECL_THREAD_LOCAL_P(base);
  if (TREE_CODE(base) == CONST_DECL || TREE_CODE(base) == STRING_CST) return true;
  return false;
}

// L-RO (MASTER_PLAN4 R9): an object this unit defines and the compiler itself places in a read-only section
// (a constant with a constant initializer and no relocation; varasm's own decision). Nothing ever writes it, so a
// load of it has no store to be ordered against.
static bool readonly_object(tree base) {
  return base && TREE_CODE(base) == VAR_DECL && TREE_STATIC(base) && !DECL_EXTERNAL(base) && TREE_READONLY(base) &&
         !TREE_THIS_VOLATILE(base) && DECL_INITIAL(base) && DECL_INITIAL(base) != error_mark_node &&
         decl_readonly_section(base, 0);
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

static bool shared_ordered(tree t) {
  if (!t || !is_memory_ref(t)) return false;
  tree base = get_base_address(t);
  return !local_unshared(base) && !readonly_object(base);
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


static counts *cur;   // the counters of the function being ordered

// totality (R9): `ref = ...` / `... = ref` forms ordered scalar-wise where expressible, fenced otherwise
static void order_single(gimple_stmt_iterator *gsi);

// a fence after a statement; one that ends its block (a trapping load) gets it on the fall-through edge
static void fence_after(gimple_stmt_iterator *gsi, enum memmodel m) {
  gimple *stmt = gsi_stmt(*gsi);
  if (stmt_ends_bb_p(stmt)) {
    edge e = find_fallthru_edge(gimple_bb(stmt)->succs);
    if (!e) { error_at(gimple_location(stmt), "%<fnvnx_order%>: cannot fence after a block-ending statement"); return; }
    gsi_insert_on_edge_immediate(e, fence(m));
  } else {
    gsi_insert_after(gsi, fence(m), GSI_SAME_STMT);
  }
  (m == MEMMODEL_ACQUIRE ? cur->fence_ld : cur->fence_st)++;
}

// an operand that is a shared memory reference but sits where only a value can (a call argument, a return value, a
// condition, an arithmetic operand): read it into a local first with an ordinary assignment, ordered as any other
// load, and use the local
static void hoist_read(gimple_stmt_iterator *gsi, gimple *stmt, unsigned i) {
  tree op = gimple_op(stmt, i);
  tree tmp = create_tmp_var(TREE_TYPE(op), "order_a");
  gimple *as = gimple_build_assign(tmp, op);
  gimple_set_location(as, gimple_location(stmt));
  gsi_insert_before(gsi, as, GSI_SAME_STMT);
  gimple_stmt_iterator g2 = gsi_for_stmt(as);
  order_single(&g2);
  gimple_set_op(stmt, i, tmp);
}

static bool operand_shared(gimple *, tree, tree op, void *) { return shared_ordered(op); }

// a pointer argument that cannot be shared memory: the address of a constant the compiler keeps read-only
static bool pointer_to_constant(tree a) {
  if (TREE_CODE(a) != ADDR_EXPR) return false;
  tree base = get_base_address(TREE_OPERAND(a, 0));
  return base && (TREE_CODE(base) == STRING_CST || readonly_object(base));
}

// a call GCC may expand inline that reads or writes memory through its pointer arguments (memcpy, memset, memcmp,
// strlen, ...): the memory is no GIMPLE operand. GCC's own fnspec of the callee says which pointer arguments it may
// read or write (unknown spec: all, both ways). A write gets a release fence before, a read an acquire fence after.
// The atomic and sync builtins carry their own ordering and are not touched.
static void order_builtin_call(gimple_stmt_iterator *gsi, gcall *call) {
  tree fn = gimple_call_fndecl(call);
  if (!fn || !fndecl_built_in_p(fn, BUILT_IN_NORMAL)) return;
  built_in_function code = DECL_FUNCTION_CODE(fn);
  if (code >= BUILT_IN_SYNC_FETCH_AND_ADD_N && code <= BUILT_IN_ATOMIC_FERAISEEXCEPT) return;
  if (gimple_call_flags(call) & ECF_CONST) return;
  attr_fnspec spec = gimple_call_fnspec(call);
  bool rd = false, wr = false;
  for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
    tree a = gimple_call_arg(call, i);
    if (!POINTER_TYPE_P(TREE_TYPE(a)) || pointer_to_constant(a)) continue;
    bool known = spec.known_p() && spec.arg_specified_p(i);
    if (!known || spec.arg_maybe_read_p(i)) rd = true;
    if (!known || spec.arg_maybe_written_p(i)) wr = true;
  }
  if (wr) { gsi_insert_before(gsi, fence(MEMMODEL_RELEASE), GSI_SAME_STMT); cur->fence_st++; }
  if (rd) fence_after(gsi, MEMMODEL_ACQUIRE);
}

// one statement. Every memory operand is classified (local or read-only: plain; else ordered or fenced); a statement
// kind with a memory operand that no rule here covers is a compile error, never a silent plain access.
static void order_stmt(gimple_stmt_iterator *gsi) {
  gimple *stmt = gsi_stmt(*gsi);
  switch (gimple_code(stmt)) {
    case GIMPLE_ASSIGN:
      if (gimple_assign_single_p(stmt)) { order_single(gsi); return; }
      // unary/binary/ternary operations: value operands in GIMPLE, but any memory one is read first
      for (unsigned i = 1; i < gimple_num_ops(stmt); i++)
        if (gimple_op(stmt, i) && shared_ordered(gimple_op(stmt, i))) hoist_read(gsi, stmt, i);
      if (shared_ordered(gimple_assign_lhs(stmt))) {
        tree lhs = gimple_assign_lhs(stmt);
        if (!stmt_ends_bb_p(stmt) && is_gimple_reg_type(TREE_TYPE(lhs))) {
          tree tmp = create_tmp_var(TREE_TYPE(lhs), "order_r");
          gimple_assign_set_lhs(stmt, tmp);
          gsi_insert_after(gsi, gimple_build_assign(lhs, tmp), GSI_SAME_STMT);   // visited next
        } else {
          gsi_insert_before(gsi, fence(MEMMODEL_RELEASE), GSI_SAME_STMT);
          cur->fence_st++;
        }
      }
      return;
    case GIMPLE_CALL: {
      // arguments that are memory (an aggregate passed by value, the static chain): read first
      for (unsigned i = 1; i < gimple_num_ops(stmt); i++)
        if (gimple_op(stmt, i) && shared_ordered(gimple_op(stmt, i))) hoist_read(gsi, stmt, i);
      order_builtin_call(gsi, as_a<gcall *>(stmt));
      // `ref = f()` storing into an ordered object: route the result through a temporary, and order that store
      tree lhs = gimple_call_lhs(stmt);
      if (lhs && shared_ordered(lhs) && !stmt_ends_bb_p(stmt) && is_gimple_reg_type(TREE_TYPE(lhs))) {
        tree tmp = create_tmp_var(TREE_TYPE(lhs), "order_r");
        gimple_call_set_lhs(stmt, tmp);
        gsi_insert_after(gsi, gimple_build_assign(lhs, tmp), GSI_SAME_STMT);   // visited next
      } else if (lhs && shared_ordered(lhs)) {
        // a return slot (aggregate): release fence before the call that stores into it
        gsi_insert_before(gsi, fence(MEMMODEL_RELEASE), GSI_SAME_STMT);
        cur->fence_st++;
      }
      return;
    }
    case GIMPLE_RETURN: case GIMPLE_COND: case GIMPLE_SWITCH:
      // the value operands: read a memory one first
      for (unsigned i = 0; i < gimple_num_ops(stmt); i++)
        if (gimple_op(stmt, i) && shared_ordered(gimple_op(stmt, i))) hoist_read(gsi, stmt, i);
      return;
    case GIMPLE_ASM:
      // the memory operands of an asm are the asm's own business: a function with asm is never a native body
      return;
    case GIMPLE_DEBUG:
      return;
    default:
      // every other kind: no memory operand allowed to escape
      if (walk_stmt_load_store_addr_ops(stmt, NULL, operand_shared, operand_shared, NULL))
        error_at(gimple_location(stmt), "%<fnvnx_order%>: cannot order a memory operand of a %s statement",
                 gimple_code_name[gimple_code(stmt)]);
      return;
  }
}

static void order_single(gimple_stmt_iterator *gsi) {
  counts &c = *cur;
  gimple *stmt = gsi_stmt(*gsi);
  tree lhs = gimple_assign_lhs(stmt), rhs = gimple_assign_rhs1(stmt);
  bool st = shared_ordered(lhs), ld = shared_ordered(rhs);
  if (!st && !ld) return;
  if (gimple_clobber_p(stmt)) return;
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
    gsi_insert_seq_before(gsi, seq, GSI_SAME_STMT);
    tree ty = TREE_TYPE(rhs);
    tree v = SCALAR_FLOAT_TYPE_P(ty) ? build1(VIEW_CONVERT_EXPR, ty, bits) : build1(NOP_EXPR, ty, bits);
    if (!useless_type_conversion_p(TREE_TYPE(lhs), ty)) {
      tree t2 = create_tmp_var(ty, "order_v");
      gsi_insert_before(gsi, gimple_build_assign(t2, v), GSI_SAME_STMT);
      v = build1(NOP_EXPR, TREE_TYPE(lhs), t2);
    }
    gimple_assign_set_rhs_from_tree(gsi, v);
    c.ldar++;
    return;
  }
  if (st && !ld && si >= 0 && is_gimple_val(rhs)) {
    // ref = v  ->  __atomic_store_N(&ref, (uintN) v, RELEASE)
    gimple_seq seq = NULL;
    tree p = address_of(lhs, &seq);
    tree bits = to_bits(rhs, uint_type(si), &seq);
    gcall *call = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_STORE_1 + si)), 3, p,
                                    bits, build_int_cst(integer_type_node, MEMMODEL_RELEASE));
    gimple_set_location(call, gimple_location(stmt));
    gsi_insert_seq_before(gsi, seq, GSI_SAME_STMT);
    gsi_replace(gsi, call, false);
    c.stlr++;
    return;
  }
  // everything else keeps its (volatile) access, fenced: dmb ish before a store, dmb ishld after a load
  if (st) { gsi_insert_before(gsi, fence(MEMMODEL_RELEASE), GSI_SAME_STMT); c.fence_st++; }
  if (ld) fence_after(gsi, MEMMODEL_ACQUIRE);
}

struct order_pass : gimple_opt_pass {
  order_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}

  unsigned int execute(function *fun) final override {
    counts c = {0, 0, 0, 0};
    cur = &c;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) order_stmt(&gsi);
    unit_total.ldar += c.ldar; unit_total.stlr += c.stlr;
    unit_total.fence_ld += c.fence_ld; unit_total.fence_st += c.fence_st;
    if (verbose && (c.ldar || c.stlr || c.fence_ld || c.fence_st))
      inform(DECL_SOURCE_LOCATION(fun->decl), "%<fnvnx_order%>: %lu acquire loads, %lu release stores, %lu+%lu fenced",
             c.ldar, c.stlr, c.fence_ld, c.fence_st);
    cur = NULL;
    return 0;
  }
};

static const pass_data order_data = {
    GIMPLE_PASS, "fnvnx_order", OPTGROUP_NONE, TV_NONE, PROP_gimple_any | PROP_cfg, 0, 0, 0, 0,
};

static void finish(void *, void *) {
  if (verbose)
    inform(UNKNOWN_LOCATION, "%<fnvnx_order%>: unit: %lu ldar, %lu stlr, %lu fenced loads, %lu fenced stores; "
           "%lu calls with arguments right to left, %lu left (class-type argument with side effects)",
           unit_total.ldar, unit_total.stlr, unit_total.fence_ld, unit_total.fence_st, calls_reordered, calls_skipped);
  if (verbose && (object_last || operator_rtl))
    inform(UNKNOWN_LOCATION, "%<fnvnx_order%>: unit: %lu calls with the object after the arguments, "
           "%lu overloaded operators right to left, %lu prvalue virtual calls with the object first, "
           "%lu calls through a computed pointer with the arguments first",
           calls_object_last, calls_operator, calls_prvalue_virtual, calls_fnptr);
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
    if (DECL_CONSTRUCTOR_P(d) || DECL_DESTRUCTOR_P(d)) return 0;
    // `make().f(a())` with f virtual: the front end calls a prvalue's virtual function directly (it knows the
    // type), MSVC 2008 still treats it as a virtual call and evaluates the object first (V2's P1)
    if (object_msvc && DECL_VIRTUAL_P(d) && TREE_CODE(call) == CALL_EXPR) {
      tree o = *arg_slot(call, 0);
      while (CONVERT_EXPR_P(o) || TREE_CODE(o) == NON_LVALUE_EXPR) o = TREE_OPERAND(o, 0);
      if (TREE_CODE(o) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(o, 0)) == TARGET_EXPR) { calls_prvalue_virtual++; return 0; }
    }
    return 1;
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
  // `fp()(a())`: a call through a computed function pointer evaluates its arguments before the callee expression
  // under MSVC 2008 (V2's P2): the argument chain goes in front of the callee (an indirect call anyway)
  bool fnptr = false;
  if (!first && object_msvc && TREE_CODE(call) == CALL_EXPR && fn && TREE_SIDE_EFFECTS(fn) &&
      !(TREE_CODE(fn) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(fn, 0)) == FUNCTION_DECL) && TREE_CODE(fn) != OBJ_TYPE_REF &&
      fntype && TREE_CODE(fntype) == FUNCTION_TYPE) {
    okind = 3;
    fnptr = true;
  }
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
  if (fnptr) calls_fnptr++;
  calls_reordered++;
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
  if (args_rtl && (TREE_CODE(*tp) == CALL_EXPR || TREE_CODE(*tp) == AGGR_INIT_EXPR)) reorder_call(*tp);
  return NULL_TREE;
}

static void pre_genericize(void *gcc_data, void *) {
  tree fndecl = (tree)gcc_data;
  if (!fndecl || TREE_CODE(fndecl) != FUNCTION_DECL || !DECL_SAVED_TREE(fndecl)) return;
  // compiler and C library headers (arm_neon.h, <string>, ...) are not decomp code: their order is GCC's to keep
  if (in_system_header_at(DECL_SOURCE_LOCATION(fndecl))) return;
  walk_tree_without_duplicates(&DECL_SAVED_TREE(fndecl), order_args_walk, NULL);
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version) {
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_order%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  for (int i = 0; i < info->argc; i++) {
    const char *k = info->argv[i].key, *v = info->argv[i].value;
    if (!strcmp(k, "verbose")) { verbose = true; if (v && !strcmp(v, "sites")) verbose_sites = true; }
    else if (!strcmp(k, "args") && v) {
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
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, pre_genericize, NULL);
  return 0;
}
