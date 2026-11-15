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
#include "cfgloop.h"
#include "dominance.h"
#include "tree-cfg.h"
#include "ssa.h"
#include "gimple-pretty-print.h"
#include "tree-into-ssa.h"
#include "varasm.h"
#include "output.h"
#include "attr-fnspec.h"
#include "gimple-walk.h"
#include "rtl.h"
#include "emit-rtl.h"
#include "target.h"
#include "fnvnx_frame.h"
#include "regs.h"
#include "cgraph.h"
#include "tm_p.h"
#include <set>
#include <map>
#include <algorithm>
#include <string>
#include <vector>
#include <utility>

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

static bool verbose, verbose_sites;
struct counts { unsigned long ldar, stlr; };
static counts unit_total;
static bool args_rtl = true;
static unsigned long calls_reordered, calls_skipped;
static bool object_last = false;                         // object=first (default) | last | msvc: ORDER-OBJECT
static bool object_msvc = false;                         // object=msvc: MSVC 2008's table (tests/s11/order/order.msvc)
static bool assign_msvc = false;                         // assign=cpp17 (default) | msvc: built-in compound assignment
static bool operator_rtl = false;                        // operator=cpp17 (default) | rtl: ORDER-OPERATOR
static unsigned long calls_object_last, calls_operator, calls_prvalue_virtual, calls_fnptr;

// ---- what counts as shared memory

// LOCAL: ordering exists only for memory another thread can observe. (1) A local or parameter that is not one of the SHARED LOCALS is
//      visible to one thread only: no access to it is ordered. The shared locals are the addressable locals and parameters that GCC's
//      points-to solution of THIS body does not prove private (frame_escapes_compute, computed once before this pass adds a single
//      atomic; the same fact is the `:n` of the frame record). A non-addressable decl has no pointer anywhere; a private addressable
//      one has no pointer another thread can hold. A temporary this pass makes addressable is accessed plainly, so it is private by
//      construction and recorded as such (force_addressable). ASM-001: the compiler's GIMPLE and points-to semantics.
//  (2) a RESULT_DECL with DECL_BY_REFERENCE is the hidden result POINTER of the function (its type is the reference
//      type; the object is at *<retval>): the decl itself is a value of this thread, never memory. Reading it (passing it
//      on, `UVf::UVf (<retval>, x)`) is no load, and its address is never taken (&<retval> is the ICE in
//      expand_expr_addr_expr_1). The result object is reached through MEM_REF / COMPONENT_REF on that pointer value, and
//      those accesses are ordered as any indirect access.
// Only a result that the ABI returns in memory without DECL_BY_REFERENCE (an aggregate through x8, memory_result) is an
// object that is caller memory: the object a function's result is constructed in is the caller's memory, which can be any
// object (copy elision constructs the result in the final object), so a pointer dereference and never a frame variable
static bool memory_result(tree d) {
  return TREE_CODE(d) == RESULT_DECL && !DECL_BY_REFERENCE(d) && aggregate_value_p(TREE_TYPE(d), current_function_decl);
}

// HARDREG: an object whose DECL has a hard register (DECL_HARD_REGISTER: `register T v asm("xN")`, global or local) is a
// machine register: it has no memory location and no address. It is never a memory reference for this plugin: not
// ordered, no address taken (taking one is the ICE in expand_expr_addr_expr_1). Every classification below (is_memory_ref,
// local_unshared, the addressable marks) applies this one rule, so a component or element of such an object is plain too.
static bool hard_register(tree base) {
  return base && TREE_CODE(base) == VAR_DECL && DECL_HARD_REGISTER(base);
}

// the decls whose address the function takes (see LOCAL above); valid between execute()'s scan and its end
static std::set<tree> *shared_locals;
static void frame_escapes_compute(function *fun);
static std::map<tree, std::set<tree> > frame_private;   // per function: the address-taken locals with escapes=0, plus the temporaries order creates

static bool local_unshared(tree base) {
  if (!base) return true;
  if (hard_register(base)) return true;
  if (TREE_CODE(base) == VAR_DECL || TREE_CODE(base) == PARM_DECL || TREE_CODE(base) == RESULT_DECL)
    // a result or parameter passed by invisible reference is the object the caller named (a return slot: `g = f()`
    // may construct f's result in g itself): a pointer dereference, not a frame variable of this function
    return !memory_result(base) && !TREE_STATIC(base) && !DECL_EXTERNAL(base) && !DECL_THREAD_LOCAL_P(base) &&
           !shared_locals->count(base);
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

// The base object of a reference. GCC's get_base_address returns NULL for MEM[&obj.field] (the operand of the ADDR_EXPR is a component
// reference, not the decl): strip the components of that operand too, so `MEM[(T *)&d.f + k]` has the base `d`.
static tree ref_base(tree t) {
  for (;;) {
    while (handled_component_p(t)) t = TREE_OPERAND(t, 0);
    if ((TREE_CODE(t) == MEM_REF || TREE_CODE(t) == TARGET_MEM_REF) && TREE_CODE(TREE_OPERAND(t, 0)) == ADDR_EXPR) {
      t = TREE_OPERAND(TREE_OPERAND(t, 0), 0);
      continue;
    }
    if (TREE_CODE(t) == SSA_NAME || DECL_P(t) || TREE_CODE(t) == STRING_CST || CONSTANT_CLASS_P(t) || TREE_CODE(t) == MEM_REF || TREE_CODE(t) == TARGET_MEM_REF)
      return t;
    return NULL_TREE;
  }
}

static bool is_memory_ref(tree t) {
  switch (TREE_CODE(t)) {
    case MEM_REF: case TARGET_MEM_REF: case COMPONENT_REF: case ARRAY_REF: case ARRAY_RANGE_REF:
    case BIT_FIELD_REF: case REALPART_EXPR: case IMAGPART_EXPR: case VIEW_CONVERT_EXPR: {
      // a component reference is memory only when its base is: a lane of a vector value, a part of a complex register or a
      // view-conversion of an SSA name has an SSA_NAME (or constant) at the bottom and no location
      tree b = ref_base(t);
      return !b || !(TREE_CODE(b) == SSA_NAME || CONSTANT_CLASS_P(b) && TREE_CODE(b) != STRING_CST);
    }
    case VAR_DECL:
      return !hard_register(t) && (TREE_STATIC(t) || DECL_EXTERNAL(t) || TREE_ADDRESSABLE(t));
    case PARM_DECL:
      // an aggregate parameter that lives in memory: its address is taken, or it is passed by invisible reference (the object is the caller's copy)
      return !hard_register(t) && (TREE_ADDRESSABLE(t) || DECL_BY_REFERENCE(t));
    case RESULT_DECL:
      return memory_result(t);
    default:
      return false;
  }
}

// READONLY RECORD (.fnvnx.ro, no SHF_ALLOC). One line per distinct (symbol, byte range) of a read-only object that an
// access of this unit left plain because readonly_object judged the base read-only: `symbol<TAB>offset:size`, bytes
// within the object. A range with a non-constant index or extent is the whole object (0:size of the decl). It is a
// claim, not a verdict: the gate (link/gate_order.py) certifies a plain load against it only after checking from the
// linked object that the symbol's section has SHF_ALLOC and not SHF_WRITE. The record is per unit, not per function:
// the optimizers inline bodies, so an access reaches the object file in a function other than the one it was written in.
static std::set<std::string> ro_lines;
static void record_readonly(tree t, tree base) {
  poly_int64 bitsize, bitpos;
  tree off;
  machine_mode mode;
  int unsignedp, reversep, volatilep;
  HOST_WIDE_INT lo = 0, size = 0;
  tree inner = get_inner_reference(t, &bitsize, &bitpos, &off, &mode, &unsignedp, &reversep, &volatilep);
  (void)inner;
  tree dsz = DECL_SIZE_UNIT(base);
  HOST_WIDE_INT whole = dsz && tree_fits_shwi_p(dsz) ? tree_to_shwi(dsz) : -1;
  if (!off && bitpos.is_constant(&lo) && bitsize.is_constant(&size) && size >= 0) {
    lo /= BITS_PER_UNIT;
    size = (size + BITS_PER_UNIT - 1) / BITS_PER_UNIT;
  } else { lo = 0; size = whole; }
  char buf[64];
  snprintf(buf, sizeof buf, "\t%ld:%ld", (long)lo, (long)size);
  ro_lines.insert(std::string(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(base))) + buf);
}

static bool shared_ordered(tree t) {
  if (!t || !is_memory_ref(t)) return false;
  tree base = ref_base(t);
  if (readonly_object(base)) { record_readonly(t, base); return false; }
  return !local_unshared(base);
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

static bool bitfield_ref(tree t) {
  return TREE_CODE(t) == BIT_FIELD_REF || (TREE_CODE(t) == COMPONENT_REF && DECL_BIT_FIELD(TREE_OPERAND(t, 1)));
}

// BIT-FIELD UNIT. A possibly-shared bit-field access is an access of the field's allocation unit: DECL_BIT_FIELD_REPRESENTATIVE
// (the layout's own representative, a naturally aligned integer field that contains the bit-field and nothing outside the record).
// This is the x86 code's own access, which loads and stores the unit. Result: the representative's reference, the bit offset of
// the field in it and its width. false when the field has no representative, or the unit is no ldar/stlr unit: compile error.
static bool bitfield_unit(tree t, tree *ur, HOST_WIDE_INT *off, HOST_WIDE_INT *sz) {
  if (TREE_CODE(t) != COMPONENT_REF) return false;
  tree f = TREE_OPERAND(t, 1), r = DECL_BIT_FIELD_REPRESENTATIVE(f);
  if (!r || !tree_fits_shwi_p(bit_position(f)) || !tree_fits_shwi_p(bit_position(r)) || !DECL_SIZE(f) || !tree_fits_uhwi_p(DECL_SIZE(f)) ||
      !DECL_SIZE(r) || !tree_fits_uhwi_p(DECL_SIZE(r)))
    return false;
  *off = tree_to_shwi(bit_position(f)) - tree_to_shwi(bit_position(r));
  *sz = tree_to_uhwi(DECL_SIZE(f));
  if (*off < 0 || *sz <= 0 || *sz > 64 || *off + *sz > (HOST_WIDE_INT)tree_to_uhwi(DECL_SIZE(r))) return false;
  *ur = build3(COMPONENT_REF, TREE_TYPE(r), unshare_expr(TREE_OPERAND(t, 0)), r, NULL_TREE);
  return atomic_index(*ur) >= 0;
}

static tree uint_type(int idx) {
  static const int bits[] = {8, 16, 32, 64};
  return build_nonstandard_integer_type(bits[idx], 1);
}


// force_gimple_operand replaces its sequence; gimplify into a fresh one and append it
// mark a declaration as living in memory: its address is about to be taken. A gimplifier temporary of a register type is
// a gimple register (it must never be addressable), so that flag goes too
static void force_addressable(tree d) {
  if (!d || !DECL_P(d)) return;
  DECL_NOT_GIMPLE_REG_P(d) = 1;
  ::mark_addressable(d);
  // PRIVATE BY CONSTRUCTION: a decl order makes addressable that is not one of the shared locals (shared_locals) is accessed plainly
  // by this pass, so the frame record must say `:n` for it
  if (shared_locals && !shared_locals->count(d) && !TREE_STATIC(d) && !DECL_EXTERNAL(d)) frame_private[current_function_decl].insert(d);
}

static tree gimplify_to(tree e, gimple_seq *seq) {
  gimple_seq s = NULL;
  tree v = force_gimple_operand(e, &s, true, NULL_TREE);
  gimple_seq_add_seq(seq, s);
  return v;
}

static tree address_of(tree ref, gimple_seq *seq) {
  tree base = ref_base(ref);
  if (base && DECL_P(base) && !hard_register(base)) force_addressable(base);
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

// ONE FORM. Every possibly-shared access leaves this pass as an atomic builtin (__atomic_load_N acquire, __atomic_store_N release; ldar
// and stlr) and nothing else: there is no fence around a plain access. ldar is an acquire of its own instruction, so it orders every later
// access of the thread on every path, the exception edge and a setjmp resumption included; no fence at a resumption point is needed.

// a temporary of any type: create_tmp_var refuses a type that must live in memory (TREE_ADDRESSABLE: a C++ class with a
// non-trivial copy), the raw form takes it
static tree make_tmp(tree ty, const char *name) {
  if (!TREE_ADDRESSABLE(ty)) return create_tmp_var(ty, name);
  tree d = create_tmp_var_raw(ty, name);
  gimple_add_tmp_var(d);
  return d;
}

// a temporary holding a value (the pass runs on SSA form): an SSA name for a register type, a memory temporary otherwise
static tree make_val(tree ty, const char *name) {
  if (is_gimple_reg_type(ty) && !TREE_ADDRESSABLE(ty)) return make_ssa_name(ty);
  return make_tmp(ty, name);
}

// an operand that is a shared memory reference but sits where only a value can (a call argument, a return value, a
// condition, an arithmetic operand): read it into a local first with an ordinary assignment, ordered as any other
// load, and use the local
static void hoist_read(gimple_stmt_iterator *gsi, gimple *stmt, unsigned i) {
  tree op = gimple_op(stmt, i);
  tree tmp = make_val(TREE_TYPE(op), "order_a");
  gimple *as = gimple_build_assign(tmp, op);
  gimple_set_location(as, gimple_location(stmt));
  gsi_insert_before(gsi, as, GSI_SAME_STMT);
  gimple_stmt_iterator g2 = gsi_for_stmt(as);
  order_single(&g2);
  *gsi = gsi_for_stmt(stmt);
  gimple_set_op(stmt, i, tmp);
}

static bool operand_shared(gimple *, tree, tree op, void *) { return shared_ordered(op); }

// a pointer argument that cannot be shared memory: the address of a constant the compiler keeps read-only
static bool pointer_to_constant(tree a) {
  if (TREE_CODE(a) != ADDR_EXPR) return false;
  tree base = ref_base(TREE_OPERAND(a, 0));
  return base && (TREE_CODE(base) == STRING_CST || readonly_object(base));
}

#include "atomic_order.inc"   // generated by gen_atomic_order.py from the pinned GCC 13.3.0 tarball (sha256 inside)

// the non-builtin declaration of a builtin's library function (same name, same type, no built-in class), one per name.
// GCC rewrites a call into memory accesses only for a call whose callee is a built-in declaration (gimple_fold_builtin,
// match.pd and expand_builtin all start from DECL_BUILT_IN_CLASS), so the same call with this callee stays a call.
static tree library_decl(tree fn) {
  static std::map<std::string, tree> made;
  const char *name = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fn));
  tree &d = made[name];
  if (!d) {
    d = build_fn_decl(name, TREE_TYPE(fn));
    DECL_ARTIFICIAL(d) = 1;
  }
  return d;
}

// ATOMICS (R9: the x86 mapping). Every memory-order argument of an __atomic builtin is raised to seq_cst: on this target
// ldar serves acquire and seq_cst loads, stlr release and seq_cst stores, and a read-modify-write or compare-exchange of
// any order below seq_cst would be weaker than x86's locked instruction. The parameter positions come from
// sync-builtins.def (atomic_order, generated). The __sync builtins are full barriers by definition.
static void raise_atomic_orders(gcall *call, built_in_function code) {
  for (const auto &e : atomic_order) {
    if (e.code != code) continue;
    for (unsigned i = 0; i < gimple_call_num_args(call); i++)
      if (e.mask & (1u << i)) gimple_call_set_arg(call, i, build_int_cst(integer_type_node, MEMMODEL_SEQ_CST));
    return;
  }
}

static void order_single(gimple_stmt_iterator *gsi);

static std::set<gimple *> generated_stmts;   // the last unit of a loop copy, which stays in the original block's tail: already ordered or plain by rule
static std::set<basic_block> generated_bbs;   // blocks this pass created: their accesses are already ordered, never revisited

// CAS EXPECTED. `__atomic_compare_exchange(ptr, expected, desired, ...)` reads `*expected` and, on failure only, writes the observed value
// to it: two plain accesses inside the builtin when `expected` points to possibly shared memory. The builtin works on a private
// temporary instead: `tmp = *expected` (an ordered load), the call on &tmp, and on failure (result 0) `*expected = tmp` (an ordered
// store). On success nothing is stored, as on x86. An `expected` that is the address of an unshared local stays as it is. The generic
// form (`desired` is a pointer too) is not an __atomic_compare_exchange_N: a compile error.
static void cas_expected(gimple_stmt_iterator *gsi, gcall *call, built_in_function code) {
  if (code == BUILT_IN_ATOMIC_COMPARE_EXCHANGE) {
    error_at(gimple_location(call), "%<fnvnx_order%>: the generic __atomic_compare_exchange reads and writes through two pointers; use the sized form");
    return;
  }
  tree e = gimple_call_arg(call, 1);
  if (pointer_to_constant(e) || (TREE_CODE(e) == ADDR_EXPR && local_unshared(ref_base(TREE_OPERAND(e, 0))))) return;
  tree pt = TREE_TYPE(TREE_TYPE(e));
  tree tmp = make_tmp(pt, "order_e");
  force_addressable(tmp);
  location_t loc = gimple_location(call);
  tree ref = build2(MEM_REF, pt, e, build_int_cst(TREE_TYPE(e), 0));
  gimple *in = gimple_build_assign(tmp, ref);
  gimple_set_location(in, loc);
  gsi_insert_before(gsi, in, GSI_SAME_STMT);
  gimple_stmt_iterator g = gsi_for_stmt(in);
  order_single(&g);
  *gsi = gsi_for_stmt(call);
  gimple_call_set_arg(call, 1, build_fold_addr_expr(tmp));
  tree res = gimple_call_lhs(call);
  if (!res) { res = make_ssa_name(boolean_type_node); gimple_call_set_lhs(call, res); }
  update_stmt(call);
  // on failure: *expected = tmp, in a block of its own after the call
  basic_block bb = gimple_bb(call);
  edge fe = split_block(bb, call);
  basic_block tail = fe->dest, then = create_empty_bb(bb);
  gimple_stmt_iterator last = gsi_last_bb(bb);
  gsi_insert_after(&last, gimple_build_cond(EQ_EXPR, res, build_zero_cst(TREE_TYPE(res)), NULL_TREE, NULL_TREE), GSI_NEW_STMT);
  fe->flags = (fe->flags & ~EDGE_FALLTHRU) | EDGE_FALSE_VALUE;
  edge te = make_edge(bb, then, EDGE_TRUE_VALUE);
  make_single_succ_edge(then, tail, EDGE_FALLTHRU);
  te->probability = profile_probability::unlikely();
  fe->probability = te->probability.invert();
  then->count = bb->count.apply_probability(te->probability);
  generated_bbs.insert(then);
  if (current_loops) add_bb_to_loop(then, bb->loop_father);
  free_dominance_info(CDI_DOMINATORS);
  free_dominance_info(CDI_POST_DOMINATORS);
  gimple *out = gimple_build_assign(unshare_expr(ref), tmp);
  gimple_set_location(out, loc);
  gimple_stmt_iterator ts = gsi_start_bb(then);
  gsi_insert_after(&ts, out, GSI_NEW_STMT);
  gimple_stmt_iterator og = gsi_for_stmt(out);
  order_single(&og);
}

// a call GCC may expand or fold into plain accesses that reads or writes memory through its pointer arguments: the memory
// is no GIMPLE operand. GCC's own fnspec of the callee says which pointer arguments it may read or write (unknown spec:
// all, both ways). Pointer arguments that cannot be shared memory (the address of a constant, of an unshared local) are
// out of scope.
static void store_after(gimple_stmt_iterator *gsi, gimple *stmt, gimple *store);
static void order_builtin_call(gimple_stmt_iterator *gsi, gcall *call) {
  tree fn = gimple_call_fndecl(call);
  if (!fn || !fndecl_built_in_p(fn, BUILT_IN_NORMAL)) return;
  built_in_function code = DECL_FUNCTION_CODE(fn);
  // GCC's own fallback: the three `_eq` builtins are expanded inline by pieces and otherwise changed into the call of the library
  // function (builtins.cc expand_builtin: MEMCMP_EQ -> BUILT_IN_MEMCMP; STRCMP_EQ, STRNCMP_EQ likewise). They are that library call here:
  // the pieces would be plain accesses
  switch (code) {
    case BUILT_IN_MEMCMP_EQ: fn = builtin_decl_explicit(BUILT_IN_MEMCMP); break;
    case BUILT_IN_STRCMP_EQ: fn = builtin_decl_explicit(BUILT_IN_STRCMP); break;
    case BUILT_IN_STRNCMP_EQ: fn = builtin_decl_explicit(BUILT_IN_STRNCMP); break;
    default: break;
  }
  if (code >= BUILT_IN_SYNC_FETCH_AND_ADD_N && code <= BUILT_IN_ATOMIC_FERAISEEXCEPT) {
    raise_atomic_orders(call, code);
    if (code >= BUILT_IN_ATOMIC_COMPARE_EXCHANGE && code <= BUILT_IN_ATOMIC_COMPARE_EXCHANGE_16) cas_expected(gsi, call, code);
    return;
  }
  if (gimple_call_flags(call) & ECF_CONST) return;
  // THE va_list BUILTINS. va_end expands to nothing but its operand's side effects (builtins.cc expand_builtin_va_end): no access. va_start
  // fills the va_list object it is given (builtins.cc expand_builtin_va_start: plain stores into it, expanded at RTL); when that object
  // may be shared it fills a private temporary, which is then copied into it as any ordered aggregate copy. The temporary is private by
  // construction (force_addressable) and its address goes to this one call, which keeps no pointer (GCC's own description of the builtin)
  if (code == BUILT_IN_VA_END) return;
  // THE STACK-POINTER BUILTINS. __builtin_stack_save returns the stack pointer and __builtin_stack_restore(p) sets it (builtins.cc
  // expand_builtin_stack_save / expand_stack_restore: emit_stack_save / emit_stack_restore, a move of the stack pointer register); the pointer
  // operand is a stack-pointer VALUE that the builtin never dereferences, so it is no memory access (a VLA or alloca scope boundary).
  if (code == BUILT_IN_STACK_SAVE || code == BUILT_IN_STACK_RESTORE) return;
  if (code == BUILT_IN_VA_START && gimple_call_num_args(call) >= 1) {
    tree a = gimple_call_arg(call, 0);
    if (POINTER_TYPE_P(TREE_TYPE(a)) && !(TREE_CODE(a) == ADDR_EXPR && local_unshared(ref_base(TREE_OPERAND(a, 0))))) {
      tree vt = TREE_TYPE(TREE_TYPE(a));
      tree t = make_tmp(vt, "order_va");
      force_addressable(t);
      gimple_call_set_arg(call, 0, fold_convert(TREE_TYPE(a), build_fold_addr_expr(t)));
      update_stmt(call);
      store_after(gsi, call, gimple_build_assign(build_simple_mem_ref(a), t));
    }
    return;
  }
  attr_fnspec spec = gimple_call_fnspec(call);
  bool rd = false, wr = false;
  for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
    tree a = gimple_call_arg(call, i);
    if (!POINTER_TYPE_P(TREE_TYPE(a)) || pointer_to_constant(a)) continue;
    // the address of an unshared local (the scan left it unshared because this argument does not escape)
    if (TREE_CODE(a) == ADDR_EXPR && local_unshared(ref_base(TREE_OPERAND(a, 0)))) continue;
    bool known = spec.known_p() && spec.arg_specified_p(i);
    if (!known || spec.arg_maybe_read_p(i)) rd = true;
    if (!known || spec.arg_maybe_written_p(i)) wr = true;
  }
  if (!rd && !wr) return;
  const char *asmname = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fn));
  if (strncmp(asmname, "__builtin_", 10) != 0) {
    // LIBRARY CALL. Every GCC rewrite of a call into memory accesses is keyed on the callee being a built-in declaration
    // (DECL_BUILT_IN_CLASS: gimple_fold_builtin, match.pd, expand_builtin); the library declaration has none, so the call
    // survives to the object file as a plain call. The callee is a function of the runtime (newlib, libstdc++, libgcc), which
    // is compiled with this plugin: it orders its own accesses, and a call is no access of the caller.
    tree lib = library_decl(fn);
    gimple_call_set_fndecl(call, lib);
    gimple_call_set_fntype(call, TREE_TYPE(lib));
    return;
  }
  // NO LIBRARY FUNCTION. The builtin has no callee to re-point to and GCC expands it inline at RTL expansion, after this pass, with
  // plain accesses through its pointer operands. GCC's own flags say whether it touches memory at all (ECF_NOVOPS: no virtual
  // operands); one that does and reaches this point is a build error naming the builtin. (A builtin GCC expands before this pass is
  // ordinary GIMPLE here and is ordered as any access.)
  if (gimple_call_flags(call) & ECF_NOVOPS) return;
  error_at(gimple_location(call), "%<fnvnx_order%>: builtin %qs has no library function, is expanded at RTL expansion, and reads or "
           "writes possibly shared memory through a pointer operand", asmname);
}

// one statement. Every memory operand is classified (local or read-only: plain; else ordered or fenced); a statement
// kind with a memory operand that no rule here covers is a compile error, never a silent plain access.
// STORE-AFTER: `lhs = tmp` after the statement whose result went into tmp. After a statement that ends its block (a call that may
// throw) the store goes on the fall-through edge; it is then ordered at once, as the walk does not see a block it did not reach.
static void store_after(gimple_stmt_iterator *gsi, gimple *stmt, gimple *store) {
  if (stmt_ends_bb_p(stmt)) {
    edge e = find_fallthru_edge(gimple_bb(stmt)->succs);
    if (!e) return;   // the statement never completes: the store is never executed
    gsi_insert_on_edge_immediate(e, store);
    gimple_stmt_iterator g = gsi_for_stmt(store);
    order_single(&g);
  } else {
    gsi_insert_after(gsi, store, GSI_SAME_STMT);   // visited next
  }
}

static void order_stmt(gimple_stmt_iterator *gsi) {
  if (generated_stmts.count(gsi_stmt(*gsi))) return;
  gimple *stmt = gsi_stmt(*gsi);
  switch (gimple_code(stmt)) {
    case GIMPLE_ASSIGN:
      if (gimple_assign_single_p(stmt)) { order_single(gsi); return; }
      // unary/binary/ternary operations: value operands in GIMPLE, but any memory one is read first
      for (unsigned i = 1; i < gimple_num_ops(stmt); i++)
        if (gimple_op(stmt, i) && shared_ordered(gimple_op(stmt, i))) hoist_read(gsi, stmt, i);
      if (shared_ordered(gimple_assign_lhs(stmt))) {
        // an operation whose result goes to shared memory: into a value first, then stored ordered (the store is a single-rhs
        // assignment, created ordered by order_single)
        tree lhs = gimple_assign_lhs(stmt);
        tree tmp = make_val(TREE_TYPE(lhs), "order_r");
        gimple_assign_set_lhs(stmt, tmp);
        update_stmt(stmt);
        store_after(gsi, stmt, gimple_build_assign(lhs, tmp));
      }
      return;
    case GIMPLE_CALL: {
      // arguments that are memory (an aggregate passed by value, the static chain): read first
      for (unsigned i = 1; i < gimple_num_ops(stmt); i++)
        if (gimple_op(stmt, i) && shared_ordered(gimple_op(stmt, i))) hoist_read(gsi, stmt, i);
      if (gimple_call_internal_p(stmt)) {
        // an atomic internal function (a folded __atomic builtin): every memory-order argument is seq_cst, as for the builtin
        for (const auto &e : ifn_order)
          if (e.code == gimple_call_internal_fn(stmt))
            for (unsigned i = 0; i < gimple_call_num_args(stmt); i++)
              if ((e.mask & (1u << i)) && TREE_CODE(gimple_call_arg(stmt, i)) == INTEGER_CST)
                gimple_call_set_arg(stmt, i, build_int_cst(TREE_TYPE(gimple_call_arg(stmt, i)), MEMMODEL_SEQ_CST));
        return;
      }
      // __builtin_va_copy: GCC expands it as the assignment of the two va_list objects (builtins.cc expand_builtin_va_copy: MODIFY_EXPR dst,
      // src), a plain access through its pointer operand. It is lowered to that assignment here, and ordered as any aggregate copy.
      if (tree vf = gimple_call_fndecl(stmt)) {
        // (the destination is the address of the va_list object; the source is the va_list value, or its address when va_list is an array)
        if (fndecl_built_in_p(vf, BUILT_IN_VA_COPY) && gimple_call_num_args(stmt) == 2 && POINTER_TYPE_P(TREE_TYPE(gimple_call_arg(stmt, 0))) &&
            TREE_CODE(TREE_TYPE(TREE_TYPE(gimple_call_arg(stmt, 0)))) == RECORD_TYPE &&
            TREE_TYPE(gimple_call_arg(stmt, 1)) == TREE_TYPE(TREE_TYPE(gimple_call_arg(stmt, 0)))) {
          gimple *as = gimple_build_assign(build_simple_mem_ref(gimple_call_arg(stmt, 0)), gimple_call_arg(stmt, 1));
          gimple_set_location(as, gimple_location(stmt));
          gsi_replace(gsi, as, true);
          order_single(gsi);
          return;
        }
      }
      order_builtin_call(gsi, as_a<gcall *>(stmt));
      // `ref = f()` storing into an ordered object: route the result through a temporary, and order that store
      tree lhs = gimple_call_lhs(stmt);
      if (lhs && shared_ordered(lhs)) {
        // the result goes into a value (a register-type result) or a memory temporary (an aggregate: the callee writes it
        // through the hidden pointer, which is the callee's own ordered access), then is copied ordered
        tree tmp = make_val(TREE_TYPE(lhs), "order_r");
        gimple_call_set_lhs(stmt, tmp);
        update_stmt(stmt);
        store_after(gsi, stmt, gimple_build_assign(lhs, tmp));
      }
      return;
    }
    case GIMPLE_RETURN: case GIMPLE_COND: case GIMPLE_SWITCH:
      // the value operands: read a memory one first. `return <retval>` of a result held in memory reads nothing: the
      // object already is where the caller expects it
      for (unsigned i = 0; i < gimple_num_ops(stmt); i++)
        if (gimple_op(stmt, i) && shared_ordered(gimple_op(stmt, i)) &&
            !(gimple_code(stmt) == GIMPLE_RETURN && TREE_CODE(gimple_op(stmt, i)) == RESULT_DECL))
          hoist_read(gsi, stmt, i);
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

// UNIT COPY (aggregate copies, R9). `lhs = rhs` where a side is shared memory that no single ldar/stlr covers (an aggregate,
// a long double, a complex, a misaligned or odd-sized scalar, a copy of one shared object into another) becomes a
// sequence of aligned scalar units copied in ascending order, every shared unit created ordered (`__atomic_load_N` acquire,
// `__atomic_store_N` release) and every unshared side a plain access. A value that is no memory reference (a constant, a
// constructor, a register value) is first stored into an unshared temporary and copied from there.
// PROOF. The unit is the largest of 8/4/2/1 bytes that divides the object size and fits the alignment of both sides, so
// the units tile the object exactly and no unit access is misaligned; the bytes of the object are exactly those the copy
// moves (padding included, as a memcpy-style aggregate copy does); the order is the program order of the loads and stores of
// the copy, ascending: x86 TSO orders loads with loads and stores with stores, and ldar/stlr give that order on Arm.
// Up to 16 units the copy is straight-line code (2 statements per unit); beyond that it is a loop over the units.

// Replace the statement at GSI by S (the last unit of a copy). gsi_replace requires an assignment to keep its lhs; when the new unit is an
// assignment to another reference (an unshared destination) the original assignment is rewritten in place, which also keeps its virtual
// definition. A call (the ordered store) has no lhs and replaces it as is.
static void replace_with(gimple_stmt_iterator *gsi, gimple *s) {
  gimple *o = gsi_stmt(*gsi);
  gassign *oa = dyn_cast<gassign *>(o), *sa = dyn_cast<gassign *>(s);
  if (oa && sa && gimple_assign_lhs(oa) != gimple_assign_lhs(sa)) {
    gimple_assign_set_lhs(oa, gimple_assign_lhs(sa));
    gimple_assign_set_rhs_from_tree(gsi, gimple_assign_rhs1(sa));
    gimple_set_location(gsi_stmt(*gsi), gimple_location(s));
    update_stmt(gsi_stmt(*gsi));
    generated_stmts.insert(gsi_stmt(*gsi));
    return;
  }
  gsi_replace(gsi, s, true);
  generated_stmts.insert(gsi_stmt(*gsi));
}

static void unit_copy(gimple_stmt_iterator *gsi, tree lhs, tree rhs, bool st, bool ld) {
  counts &c = *cur;
  gimple *stmt = gsi_stmt(*gsi);
  location_t loc = gimple_location(stmt);
  tree ty = TREE_TYPE(lhs);
  if (!TYPE_SIZE_UNIT(ty) || !tree_fits_uhwi_p(TYPE_SIZE_UNIT(ty))) {
    error_at(loc, "fnvnx_order: a shared copy of an object of no constant size");
    return;
  }
  unsigned HOST_WIDE_INT size = tree_to_uhwi(TYPE_SIZE_UNIT(ty));
  if (size == 0) return;   // no bytes: no access
  gimple_seq pre = NULL;
  tree src = rhs;
  bool src_sh = ld;
  tree rb = ref_base(rhs);
  if (!is_memory_ref(rhs) || (rb && hard_register(rb)) || TREE_CODE(rhs) == CONSTRUCTOR) {
    tree tmp = make_tmp(ty, "order_u");
    force_addressable(tmp);
    gimple *as = gimple_build_assign(tmp, rhs);
    gimple_set_location(as, loc);
    gimple_seq_add_stmt(&pre, as);
    src = tmp;
    src_sh = false;
  }
  // a destination that is no memory (a register variable: the load `D.1 = *p` of a gimplifier temporary) is not given an
  // address: the units go to a memory temporary, which the statement then copies into the register
  tree dst = lhs, fin = NULL_TREE;
  if (!st && !is_memory_ref(lhs)) {
    dst = make_tmp(ty, "order_d");
    force_addressable(dst);
    fin = lhs;
  }
  // pointers to the two objects, taken only where a unit needs one (a shared unit is an atomic builtin on a pointer; an unshared unit at a
  // constant offset is a direct reference MEM[&obj + k], which takes no pointer value)
  tree dp = NULL_TREE, sp = NULL_TREE;
  auto getp = [&](tree &p, tree obj) { if (!p) p = address_of(obj, &pre); return p; };
  unsigned al = MIN(get_object_alignment(dst), get_object_alignment(src));
  unsigned unit = 8;
  while (unit > 1 && (size % unit || al < unit * 8)) unit >>= 1;
  unsigned HOST_WIDE_INT n = size / unit;
  int idx = unit == 1 ? 0 : unit == 2 ? 1 : unit == 4 ? 2 : 3;
  tree ity = uint_type(idx);
  tree aty = build_aligned_type(ity, unit * 8);
  tree zero = build_int_cst(ptr_type_node, 0);
  // a reference to the unit at byte offset k of an unshared object, without a pointer value; NULL when the object's address is no valid
  // MEM_REF base (a reference with a variable part)
  auto direct = [&](tree obj, unsigned HOST_WIDE_INT k) -> tree {
    tree base = ref_base(obj);
    if (!base || !DECL_P(base)) return NULL_TREE;
    // CANONICAL FORM (what GCC's own build_ref_for_offset makes): the unit is MEM[&decl + (offset of obj in decl + k)], never a MEM_REF
    // over the address of a component or array reference (RTL expansion of that form is not supported: it crashes on an array element)
    poly_int64 bsz, bpos;
    tree voff;
    machine_mode mm;
    int uns, rev, vol;
    tree inner = get_inner_reference(obj, &bsz, &bpos, &voff, &mm, &uns, &rev, &vol);
    HOST_WIDE_INT bp;
    if (inner != base || voff || !bpos.is_constant(&bp) || bp % BITS_PER_UNIT) return NULL_TREE;
    force_addressable(base);
    return build2(MEM_REF, aty, build_fold_addr_expr(base), build_int_cst(ptr_type_node, bp / BITS_PER_UNIT + (HOST_WIDE_INT)k));
  };
  // one unit: the load into v, then the store; the store is returned. dpp/spp: a pointer for a shared side, the unit's memory reference
  // for an unshared one
  auto one = [&](gimple_seq *seq, tree dpp, tree spp, gimple **last) {
    tree v = make_ssa_name(ity);
    if (src_sh) {
      gcall *l = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_LOAD_1 + idx)), 2, spp,
                                   build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
      gimple_call_set_lhs(l, v);
      gimple_set_location(l, loc);
      gimple_seq_add_stmt(seq, l);
      c.ldar++;
    } else {
      gimple *l = gimple_build_assign(v, spp);
      generated_stmts.insert(l);   // a plain unit of an unshared side (ORD-T: private)
      gimple_set_location(l, loc);
      gimple_seq_add_stmt(seq, l);
    }
    gimple *s;
    if (st) {
      s = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_STORE_1 + idx)), 3, dpp, v,
                            build_int_cst(integer_type_node, MEMMODEL_RELEASE));
      c.stlr++;
    } else {
      s = gimple_build_assign(dpp, v);
      generated_stmts.insert(s);
    }
    gimple_set_location(s, loc);
    if (last) *last = s; else gimple_seq_add_stmt(seq, s);
  };
  auto at = [&](gimple_seq *seq, tree base, tree off) { return gimplify_to(fold_build_pointer_plus(base, off), seq); };
  gimple *last = NULL;
  const unsigned HOST_WIDE_INT STRAIGHT = 16;
  if (n <= STRAIGHT) {
    for (unsigned HOST_WIDE_INT k = 0; k < n; k++) {
      tree off = size_int(k * unit);
      auto unit_at = [&](tree &p, tree obj, bool shared) -> tree {
        if (!shared)
          if (tree d = direct(obj, k * unit)) return d;
        tree b = k ? at(&pre, getp(p, obj), off) : getp(p, obj);
        return shared ? b : build2(MEM_REF, aty, b, zero);
      };
      tree dpp = unit_at(dp, dst, st), spp = unit_at(sp, src, src_sh);
      one(&pre, dpp, spp, k + 1 == n ? &last : NULL);
    }
    gsi_insert_seq_before(gsi, pre, GSI_SAME_STMT);
    // the last unit's store takes the original statement's place (SSA: it inherits the statement's virtual definition); a
    // register destination gets its value from the memory temporary afterwards
    if (fin) { gsi_insert_before(gsi, last, GSI_SAME_STMT); gsi_replace(gsi, gimple_build_assign(fin, dst), true); }
    else replace_with(gsi, last);
    return;
  }
  // more than STRAIGHT units: a loop over units 0..n-2 (do { dst[i] = src[i]; i += unit; } while (i < (n-1)*unit)), then the
  // last unit in place of the statement: the code does not grow with the object
  getp(dp, dst);
  getp(sp, src);   // the loop addresses both objects through pointers
  gsi_insert_seq_before(gsi, pre, GSI_SAME_STMT);
  basic_block bb = gsi_bb(*gsi);
  gimple_stmt_iterator g = *gsi;
  gsi_prev(&g);
  edge e = split_block(bb, gsi_stmt(g));   // the setup stays in bb, the statement starts the tail
  basic_block tail = e->dest;
  basic_block body = create_empty_bb(bb);
  redirect_edge_succ(e, body);
  // the induction variable is an SSA name merged by a PHI at the head of the body: 0 from the setup, the incremented value from the
  // back edge
  tree iv = make_ssa_name(sizetype), iv1 = make_ssa_name(sizetype);
  edge back = make_edge(body, body, EDGE_TRUE_VALUE), out = make_edge(body, tail, EDGE_FALSE_VALUE);
  gphi *phi = create_phi_node(iv, body);
  add_phi_arg(phi, size_zero_node, e, UNKNOWN_LOCATION);
  add_phi_arg(phi, iv1, back, UNKNOWN_LOCATION);
  gimple_seq bs = NULL;
  tree dpp = at(&bs, getp(dp, dst), iv), spp = at(&bs, getp(sp, src), iv);
  if (!st) dpp = build2(MEM_REF, aty, dpp, zero);
  if (!src_sh) spp = build2(MEM_REF, aty, spp, zero);
  one(&bs, dpp, spp, NULL);
  gimple_seq_add_stmt(&bs, gimple_build_assign(iv1, PLUS_EXPR, iv, size_int(unit)));
  gimple_seq_add_stmt(&bs, gimple_build_cond(LT_EXPR, iv1, size_int((n - 1) * unit), NULL_TREE, NULL_TREE));
  gimple_stmt_iterator bsi = gsi_start_bb(body);
  gsi_insert_seq_after(&bsi, bs, GSI_CONTINUE_LINKING);
  back->probability = profile_probability::very_likely();
  out->probability = back->probability.invert();
  e->probability = profile_probability::always();
  body->count = bb->count;
  generated_bbs.insert(body);
  // the block joins the loop of the original block; the loop it forms itself is found by the next loop fix-up
  if (current_loops) {
    add_bb_to_loop(body, bb->loop_father);
    loops_state_set(LOOPS_NEED_FIXUP);
  }
  free_dominance_info(CDI_DOMINATORS);
  free_dominance_info(CDI_POST_DOMINATORS);
  // the last unit replaces the statement, now the first one of the tail
  gimple_seq ls = NULL;
  tree off = size_int((n - 1) * unit);
  tree ldp = at(&ls, getp(dp, dst), off), lsp = at(&ls, getp(sp, src), off);
  if (!st) ldp = build2(MEM_REF, aty, ldp, zero);
  if (!src_sh) lsp = build2(MEM_REF, aty, lsp, zero);
  one(&ls, ldp, lsp, &last);
  gimple_stmt_iterator ts = gsi_for_stmt(stmt);
  gsi_insert_seq_before(&ts, ls, GSI_SAME_STMT);
  gimple *fin_stmt = last;
  if (fin) { gsi_insert_before(&ts, last, GSI_SAME_STMT); generated_stmts.insert(last); fin_stmt = gimple_build_assign(fin, dst); }
  replace_with(&ts, fin_stmt);
  *gsi = ts;
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
    tree bits = make_ssa_name(ity);
    gcall *call = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_LOAD_1 + li)), 2, p,
                                    build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
    gimple_call_set_lhs(call, bits);
    gimple_seq_add_stmt(&seq, call);
    gsi_insert_seq_before(gsi, seq, GSI_SAME_STMT);
    tree ty = TREE_TYPE(rhs);
    tree v = SCALAR_FLOAT_TYPE_P(ty) ? build1(VIEW_CONVERT_EXPR, ty, bits) : build1(NOP_EXPR, ty, bits);
    if (!useless_type_conversion_p(TREE_TYPE(lhs), ty)) {
      tree t2 = make_val(ty, "order_v");
      gsi_insert_before(gsi, gimple_build_assign(t2, v), GSI_SAME_STMT);
      v = build1(NOP_EXPR, TREE_TYPE(lhs), t2);
    }
    if (!is_gimple_reg(lhs) && !is_gimple_val(v)) {
      // the destination is memory: only a single-operand right-hand side is GIMPLE there, so the converted value goes through a register
      tree rv = make_ssa_name(TREE_TYPE(lhs));
      gsi_insert_before(gsi, gimple_build_assign(rv, v), GSI_SAME_STMT);
      v = rv;
    }
    gimple_assign_set_rhs_from_tree(gsi, v);
    update_stmt(gsi_stmt(*gsi));
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
  // BIT_FIELD_REF of a shared object (a part of an aggregate or vector that GCC reads as one reference: a folded memcmp, a lane): an access
  // of the naturally aligned 1/2/4/8-byte block of the object that holds all the bits, an acquire load of the block and an extraction. A
  // reference whose bits no such block holds (it spans blocks, or the block would leave the object) is a compile error.
  if (ld && !st && TREE_CODE(rhs) == BIT_FIELD_REF) {
    tree obj = TREE_OPERAND(rhs, 0);
    location_t loc = gimple_location(stmt);
    bool ok = tree_fits_uhwi_p(TREE_OPERAND(rhs, 1)) && tree_fits_uhwi_p(TREE_OPERAND(rhs, 2)) && TYPE_SIZE(TREE_TYPE(obj)) &&
              tree_fits_uhwi_p(TYPE_SIZE(TREE_TYPE(obj)));
    unsigned HOST_WIDE_INT size = ok ? tree_to_uhwi(TREE_OPERAND(rhs, 1)) : 0, pos = ok ? tree_to_uhwi(TREE_OPERAND(rhs, 2)) : 0;
    unsigned HOST_WIDE_INT osz = ok ? tree_to_uhwi(TYPE_SIZE(TREE_TYPE(obj))) : 0;
    unsigned al = ok ? get_object_alignment(obj) : 0;
    int ui = -1;
    for (int k = 0; ok && k < 4 && ui < 0; k++) {
      unsigned HOST_WIDE_INT blk = 8u << k;
      if (size && al >= blk && pos / blk == (pos + size - 1) / blk && (pos / blk + 1) * blk <= osz) ui = k;
    }
    if (ui < 0) {
      error_at(loc, "%<fnvnx_order%>: a shared BIT_FIELD_REF that no aligned 1/2/4/8-byte block of the object holds");
      return;
    }
    unsigned HOST_WIDE_INT blk = 8u << ui, bstart = pos / blk * blk;
    gimple_seq seq = NULL;
    tree p = address_of(obj, &seq);
    if (bstart) p = gimplify_to(fold_build_pointer_plus(p, size_int(bstart / 8)), &seq);
    tree bits = make_ssa_name(uint_type(ui));
    gcall *call = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_LOAD_1 + ui)), 2, p,
                                    build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
    gimple_call_set_lhs(call, bits);
    gimple_set_location(call, loc);
    gimple_seq_add_stmt(&seq, call);
    tree bty = build_nonstandard_integer_type(size, 1);
    tree fld = make_ssa_name(bty);
    gimple_seq_add_stmt(&seq, gimple_build_assign(fld, build3(BIT_FIELD_REF, bty, bits, bitsize_int(size), bitsize_int(pos - bstart))));
    gsi_insert_seq_before(gsi, seq, GSI_SAME_STMT);
    tree bv = fld;
    if (!useless_type_conversion_p(TREE_TYPE(lhs), bty))
      bv = (INTEGRAL_TYPE_P(TREE_TYPE(lhs)) || POINTER_TYPE_P(TREE_TYPE(lhs))) ? (tree)build1(NOP_EXPR, TREE_TYPE(lhs), fld)
                                                                             : (tree)build1(VIEW_CONVERT_EXPR, TREE_TYPE(lhs), fld);
    if (!is_gimple_reg(lhs) && !is_gimple_val(bv)) {
      tree rv = make_ssa_name(TREE_TYPE(lhs));
      gsi_insert_before(gsi, gimple_build_assign(rv, bv), GSI_SAME_STMT);
      bv = rv;
    }
    gimple_assign_set_rhs_from_tree(gsi, bv);
    update_stmt(gsi_stmt(*gsi));
    c.ldar++;
    return;
  }
  // a bit-field access is an access of its unit (bitfield_unit): a load is the acquire load of the unit and an extraction, a store
  // the acquire load of the unit, an insertion and the release store of the unit (the read-modify-write of the x86 code)
  if ((ld && bitfield_ref(rhs)) || (st && bitfield_ref(lhs))) {
    if (st && ld && (bitfield_ref(lhs) || bitfield_ref(rhs))) {
      // shared to shared: the value goes through a register first
      tree tmp = make_val(TREE_TYPE(rhs), "order_f");
      gimple *as = gimple_build_assign(tmp, rhs);
      gimple_set_location(as, gimple_location(stmt));
      gsi_insert_before(gsi, as, GSI_SAME_STMT);
      gimple_stmt_iterator g2 = gsi_for_stmt(as);
      order_single(&g2);
      *gsi = gsi_for_stmt(stmt);
      gimple_assign_set_rhs1(stmt, tmp);
      update_stmt(stmt);
      rhs = tmp;
      ld = false;
      if (!(st && bitfield_ref(lhs))) { order_single(gsi); return; }
    }
    tree ur;
    HOST_WIDE_INT off, sz;
    location_t loc = gimple_location(stmt);
    if (ld && bitfield_ref(rhs)) {
      if (!bitfield_unit(rhs, &ur, &off, &sz)) {
        error_at(loc, "fnvnx_order: a shared bit-field access without a usable allocation unit (representative)");
        return;
      }
      int ui = atomic_index(ur);
      gimple_seq seq = NULL;
      tree p = address_of(ur, &seq);
      tree bits = make_ssa_name(uint_type(ui));
      gcall *call = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_LOAD_1 + ui)), 2, p,
                                      build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
      gimple_call_set_lhs(call, bits);
      gimple_set_location(call, loc);
      gimple_seq_add_stmt(&seq, call);
      tree bty = build_nonstandard_integer_type(sz, TYPE_UNSIGNED(TREE_TYPE(rhs)));
      tree fld = make_ssa_name(bty);
      gimple_seq_add_stmt(&seq, gimple_build_assign(fld, build3(BIT_FIELD_REF, bty, bits, bitsize_int(sz), bitsize_int(off))));
      gsi_insert_seq_before(gsi, seq, GSI_SAME_STMT);
      tree bv = useless_type_conversion_p(TREE_TYPE(lhs), bty) ? (tree)fld : build1(NOP_EXPR, TREE_TYPE(lhs), fld);
      if (!is_gimple_reg(lhs) && !is_gimple_val(bv)) {
        tree rv = make_ssa_name(TREE_TYPE(lhs));
        gsi_insert_before(gsi, gimple_build_assign(rv, bv), GSI_SAME_STMT);
        bv = rv;
      }
      gimple_assign_set_rhs_from_tree(gsi, bv);
      update_stmt(gsi_stmt(*gsi));
      c.ldar++;
      return;
    }
    if (!bitfield_unit(lhs, &ur, &off, &sz)) {
      error_at(loc, "fnvnx_order: a shared bit-field access without a usable allocation unit (representative)");
      return;
    }
    int ui = atomic_index(ur);
    tree uty = uint_type(ui);
    gimple_seq seq = NULL;
    tree p = address_of(ur, &seq);
    tree old = make_ssa_name(uty), nw = make_ssa_name(uty);
    gcall *ldc = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_LOAD_1 + ui)), 2, p,
                                   build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
    gimple_call_set_lhs(ldc, old);
    gimple_set_location(ldc, loc);
    gimple_seq_add_stmt(&seq, ldc);
    tree bty = build_nonstandard_integer_type(sz, 1);
    tree v1 = gimplify_to(fold_convert(bty, rhs), &seq);
    gimple_seq_add_stmt(&seq, gimple_build_assign(nw, BIT_INSERT_EXPR, old, v1, bitsize_int(off)));
    gcall *stc = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_STORE_1 + ui)), 3, p, nw,
                                   build_int_cst(integer_type_node, MEMMODEL_RELEASE));
    gimple_set_location(stc, loc);
    gsi_insert_seq_before(gsi, seq, GSI_SAME_STMT);
    gsi_replace(gsi, stc, false);
    c.ldar++;
    c.stlr++;
    return;
  }
  // everything else (an aggregate, a scalar ldar/stlr cannot reach, a copy of one shared object into another) is copied
  // unit-wise, each shared unit created ordered
  unit_copy(gsi, lhs, rhs, st, ld);
}



// ORD-T (totality). After the rewrite every GIMPLE memory access of the function is either an ordered atomic (a call: it has no memory
// operand left) or a plain operand with exactly one reason from a closed set; a plain access with no reason is a compile error.
//   private    a decl of this function that is not a shared local (the escapes fact: no pointer another thread can hold), or an access
//              order itself generated through a pointer to an unshared side (its loop copies)
//   readonly   a static TREE_READONLY object that GCC places in a read-only section (readonly_object: decl_readonly_section)
//   byref      through the pointer parameter of an aggregate passed by invisible reference (AAPCS64: pass_by_reference): the caller's
//              copy made for this call, the callee's own object
//   hardreg    an object with a hard register: no memory
//   notmem     an operand with no location (a lane of a vector value, a part of a register)
// The operands are exactly those walk_stmt_load_store_addr_ops reports (GCC's own list of a statement's memory operands). The reason of
// each is printed to GCC's dump of this pass (-fdump-tree-fnvnx_order); the per-function counts go to `.fnvnx.ordt`.
struct ordt_counts { unsigned long reason[5]; };
static const char *ordt_name[5] = {"private", "readonly", "byref", "hardreg", "notmem"};
static std::map<std::string, ordt_counts> ordt_lines;

struct ordt_ctx { function *fun; ordt_counts c; bool gen; };

static bool ordt_visit(gimple *stmt, tree, tree op, void *data) {
  ordt_ctx *x = (ordt_ctx *)data;
  int why = -1;
  tree base = ref_base(op);
  if (base && hard_register(base)) why = 3;
  else if (gimple_code(stmt) == GIMPLE_RETURN && TREE_CODE(op) == RESULT_DECL) why = 4;   // `return <retval>`: the result object is returned in place, no access
  else if (!is_memory_ref(op) && !(DECL_P(op) && !TREE_STATIC(op) && !DECL_EXTERNAL(op))) why = 4;
  else if (base && DECL_P(base)) {
    if (readonly_object(base)) why = 1;
    else if (local_unshared(base)) why = (TREE_CODE(base) == PARM_DECL && DECL_BY_REFERENCE(base)) ? 2 : 0;
  } else if (base && (TREE_CODE(base) == MEM_REF || TREE_CODE(base) == TARGET_MEM_REF)) {
    tree p = TREE_OPERAND(base, 0);
    if (x->gen) why = 0;
    else if (TREE_CODE(p) == SSA_NAME && SSA_NAME_IS_DEFAULT_DEF(p) && SSA_NAME_VAR(p) && TREE_CODE(SSA_NAME_VAR(p)) == PARM_DECL &&
             DECL_BY_REFERENCE(SSA_NAME_VAR(p)))
      why = 2;
  } else if (base && TREE_CODE(base) == STRING_CST) why = 1;
  if (why < 0) {
    // the statement as GCC prints it, then the diagnostic (diagnostic output only)
    {
      // up to 6 statements before it, then it: the context GCC would show in a dump (a tail of 20 lines survives the build log)
      std::vector<gimple *> pre;
      for (gimple_stmt_iterator g = gsi_for_stmt(stmt); !gsi_end_p(g) && pre.size() < 7; gsi_prev(&g)) if (!is_gimple_debug(gsi_stmt(g))) pre.push_back(gsi_stmt(g));
      fprintf(stderr, "ORD-T block %d:\n", gimple_bb(stmt)->index);
      for (size_t k = pre.size(); k-- > 0;) print_gimple_stmt(stderr, pre[k], 1, TDF_SLIM);
    }
    error_at(gimple_location(stmt), "%<fnvnx_order%>: ORD-T: a plain memory access with no reason (%s)", get_tree_code_name(TREE_CODE(op)));
    return false;
  }
  x->c.reason[why]++;
  if (dump_file) {
    fprintf(dump_file, "ORDT %s: ", ordt_name[why]);
    print_gimple_stmt(dump_file, stmt, 0, TDF_VOPS);
  }
  return false;
}

static void ordt_check(function *fun) {
  ordt_ctx x;
  memset(&x, 0, sizeof x);
  x.fun = fun;
  basic_block bb;
  FOR_EACH_BB_FN(bb, fun) {
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
      gimple *st = gsi_stmt(gsi);
      if (is_gimple_debug(st) || gimple_code(st) == GIMPLE_ASM || gimple_clobber_p(st)) continue;   // a clobber marks the end of a lifetime: no access
      x.gen = generated_bbs.count(bb) || generated_stmts.count(st);
      walk_stmt_load_store_addr_ops(st, &x, ordt_visit, ordt_visit, NULL);
    }
  }
  ordt_lines[IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fun->decl))] = x.c;
}


// NO-MIX (private variables never share a stack slot). The frame record has one flag per stack slot and a slot shared by two variables is
// escaping if either escapes, so a private variable that GCC gives the slot of a shared one would make every plain access to it a plain
// access to a recorded-escaping range. GCC shares slots only between variables that do not conflict, and cfgexpand.cc add_scope_conflicts
// derives the conflicts from the first mention of a variable (until its death clobber): a variable that has no clobber and is mentioned by
// the first statement of the entry block is live in every block, so every other variable, mentioned later, conflicts with it. This pass
// creates no clobber for its temporaries and a user variable's clobbers are death points after its last use, so the mention is placed in
// the first statement of the entry block, as an empty asm with the variable as a memory input: no instruction, no memory effect other
// than that read. Cost: private variables keep their own slot (frame size), PD-STACK-REUSE.
static void pin_private_slots(function *fun) {
  std::set<tree> &priv = frame_private[fun->decl];
  std::vector<tree> ts;
  unsigned ix;
  tree d;
  FOR_EACH_LOCAL_DECL(fun, ix, d)
    // every private variable that lives in memory: not a shared local (shared_locals) and not a gimple register. An addressable one that
    // the escapes fact proves private, and an aggregate that is no gimple register and has no address in the IL (no pointer to it can
    // exist, whatever the escapes fact says of the others); both are `:n` in the frame record and own their slot
    if (VAR_P(d) && !TREE_STATIC(d) && !DECL_EXTERNAL(d) && !hard_register(d) && !is_gimple_reg(d) && !(shared_locals && shared_locals->count(d))) ts.push_back(d);
  if (ts.empty()) return;
  gimple_stmt_iterator gi = gsi_after_labels(single_succ(ENTRY_BLOCK_PTR_FOR_FN(fun)));
  for (tree t : ts) {
    if (!TREE_ADDRESSABLE(t)) force_addressable(t);   // a memory aggregate without an address: gets a (private) frame row too
    priv.insert(t);
    vec<tree, va_gc> *in = NULL;
    vec_safe_push(in, build_tree_list(build_tree_list(NULL_TREE, build_string(2, "m")), t));
    gasm *a = gimple_build_asm_vec("", in, NULL, NULL, NULL);
    gimple_asm_set_volatile(a, true);
    gsi_insert_before(&gi, a, GSI_SAME_STMT);
  }
}

struct order_pass : gimple_opt_pass {
  order_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}

  unsigned int execute(function *fun) final override {
    counts c = {0, 0};
    cur = &c;
    basic_block bb;
    // the decls whose address this function takes (address_taken_scan: operands, the hidden result pointer of a call that
    // returns in memory, asm operands) are the shared locals; they are made addressable before any access is classified, so no
    // earlier statement keeps a plain access to them.  Every other local is unshared (local_unshared).
    std::set<tree> taken;
    {
      // Late position (SSA, after every pass that creates address forms): the shared locals are the frame variables that GCC's points-to
      // solution of THIS body, taken before this pass adds a single atomic, does not prove private (frame_escapes_compute). The same fact
      // is the `:n` of the frame record, so a plain access to a recorded variable without `:n` cannot arise.
      frame_escapes_compute(fun);
      std::set<tree> &priv = frame_private[fun->decl];
      unsigned ix;
      tree d;
      FOR_EACH_LOCAL_DECL(fun, ix, d) if (TREE_ADDRESSABLE(d) && !priv.count(d)) taken.insert(d);
      for (tree p = DECL_ARGUMENTS(fun->decl); p; p = DECL_CHAIN(p)) if (TREE_ADDRESSABLE(p) && !priv.count(p)) taken.insert(p);
    }
    shared_locals = &taken;
    for (tree d : taken)
      if ((VAR_P(d) || TREE_CODE(d) == PARM_DECL || TREE_CODE(d) == RESULT_DECL) && !hard_register(d) && !TREE_STATIC(d) && !DECL_EXTERNAL(d))
        force_addressable(d);
    FOR_EACH_BB_FN(bb, fun)
      if (!generated_bbs.count(bb))
        for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) order_stmt(&gsi);
    ordt_check(fun);
    pin_private_slots(fun);
    if (!generated_bbs.empty() && current_loops) loops_state_set(LOOPS_NEED_FIXUP);
    // the statements this pass created carry virtual operands that are not yet in SSA form (TODO_update_ssa renames them)
    mark_virtual_operands_for_renaming(fun);
    unit_total.ldar += c.ldar; unit_total.stlr += c.stlr;
    if (verbose && (c.ldar || c.stlr))
      inform(DECL_SOURCE_LOCATION(fun->decl), "%<fnvnx_order%>: %lu acquire loads, %lu release stores", c.ldar, c.stlr);
    cur = NULL;
    shared_locals = NULL;
    generated_bbs.clear();
    generated_stmts.clear();
    return 0;
  }
};

static const pass_data order_data = {
    GIMPLE_PASS, "fnvnx_order", OPTGROUP_NONE, TV_NONE, PROP_ssa | PROP_cfg, 0, 0, 0, TODO_update_ssa | TODO_cleanup_cfg,
};


// ---- the frame record (.fnvnx.frame), rule R1 of class B
//
// For every function the unit emits: the frame ranges of its address-taken variables (TREE_ADDRESSABLE after the
// optimizers, with a stack slot), as `offset:size` pairs relative to the CFA (the stack pointer at function entry, the
// value DW_CFA_def_cfa names), or the word `dynamic` when the frame has no constant layout (alloca, a variable-length
// array, a variable whose slot is not a constant frame offset). The record is emitted from an RTL pass after
// pro_and_epilogue, where the frame is laid out, so the offsets are the final ones. The gate (link/gate_order.py)
// certifies a plain sp- or x29-based access only when it overlaps none of these ranges.
static std::map<std::string, fnvnx_frame_row> frame_lines;
static std::map<tree, std::vector<tree> > frame_vars;   // the local variables at the last GIMPLE point (expand frees the list)

// ESCAPES (per variable of the frame row). Rule: an address-taken local has escapes=0 only when GCC's own points-to solution of the
// final GIMPLE says no other thread can hold its address; every other variable has escapes=1. The solution is recomputed at this pass
// (TODO_rebuild_alias before it runs: execute_function_todo, passes.cc, calls compute_may_aliases, tree-ssa-structalias.cc, i.e.
// compute_points_to_sets / find_what_var_points_to), so it describes exactly the code that is compiled, not a state left by an earlier
// pass. escapes=1 when ANY of:
//   (1) the function has no points-to solution: flag_tree_pta is off (-O0, -fno-tree-pta) or the body is not in SSA form;
//   (2) the decl is in the ESCAPED solution, local or IPA (pt_solution_includes on a solution with `escaped`/`ipa_escaped` set:
//       pt_solution_includes_1 asks cfun->gimple_df->escaped and ipa_escaped_pt by DECL_PT_UID). ESCAPED is the closure of what a call
//       argument (handle_rhs_call, make_escape_constraint), a store into non-local memory, a return (compute_points_to_sets: escapes
//       through returns) and an asm operand make reachable;
//   (3) the decl is in the points-to set (pt_solution_includes on the SSA name's own pi->pt) of a pointer that this function passes as a
//       call argument or static chain, returns, stores to non-local memory (a global, or through a pointer whose set is not only local),
//       or names ADDR_EXPR in such a position or in an asm operand; a pointer without points-to info points anywhere;
// (3) restates (2) from the statements, so a solution that missed a use cannot make a variable private. Pointers carried in integers are
// covered by (2) only (the PTA models the conversion). ASM-001: the compiler's GIMPLE and points-to semantics.
static std::set<tree> frame_private_done;   // functions whose escapes fact is already computed: it is computed ONCE, by whichever pass first needs it (order's rewrite, before it adds its atomics), and never recomputed on the rewritten body   // per function: the address-taken locals with escapes=0

struct frame_vars_pass : gimple_opt_pass {
  frame_vars_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}

  static bool only_local(struct pt_solution *pt) {
    return !(pt->anything || pt->nonlocal || pt->escaped || pt->ipa_escaped || pt->vars_contains_escaped || pt->vars_contains_escaped_heap);
  }
  static bool store_nonlocal(tree lhs) {
    tree b = ref_base(lhs);
    if (!b) return true;
    if (DECL_P(b)) return is_global_var(b) || DECL_EXTERNAL(b);
    if (TREE_CODE(b) == MEM_REF || TREE_CODE(b) == TARGET_MEM_REF) {
      tree ptr = TREE_OPERAND(b, 0);
      if (TREE_CODE(ptr) == ADDR_EXPR) return store_nonlocal(TREE_OPERAND(ptr, 0));
      if (TREE_CODE(ptr) != SSA_NAME || !SSA_NAME_PTR_INFO(ptr)) return true;
      return !only_local(&SSA_NAME_PTR_INFO(ptr)->pt);
    }
    return true;
  }

  static void compute(function *fun) {
    unsigned ix;
    tree d;
    if (frame_private_done.count(fun->decl)) return;
    std::vector<tree> &v = frame_vars[fun->decl];
    FOR_EACH_LOCAL_DECL(fun, ix, d) v.push_back(d);
    std::vector<tree> cand = v;
    for (tree p = DECL_ARGUMENTS(fun->decl); p; p = DECL_CHAIN(p)) cand.push_back(p);
    std::set<tree> &priv = frame_private[fun->decl];
    if (!flag_tree_pta || !gimple_in_ssa_p(fun) || !fun->gimple_df) return;          // (1): nothing is private
    frame_private_done.insert(fun->decl);
    compute_may_aliases();   // the solution of THIS body now, whichever pass calls (TODO_rebuild_alias is only the frame_vars pass's own request)
    std::set<tree> esc;
    bool all = false;
    struct pt_solution q;
    memset(&q, 0, sizeof q);
    q.escaped = 1;
    q.ipa_escaped = fun->gimple_df->ipa_pta;   // ipa_escaped_pt starts as `anything`: it is a solution only when IPA PTA ran (compute_may_aliases returns early then)
    for (tree c : cand) if (pt_solution_includes(&q, c)) esc.insert(c);                // (2)
    auto note = [&](tree val) {                                                        // (3)
      if (!val) return;
      if (TREE_CODE(val) == ADDR_EXPR) {
        tree b = ref_base(TREE_OPERAND(val, 0));
        if (b && DECL_P(b)) esc.insert(b);
        return;
      }
      if (TREE_CODE(val) != SSA_NAME || !POINTER_TYPE_P(TREE_TYPE(val))) return;
      struct ptr_info_def *pi = SSA_NAME_PTR_INFO(val);
      if (!pi) { all = true; return; }
      for (tree c : cand) if (pt_solution_includes(&pi->pt, c)) esc.insert(c);
    };
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (gcall *call = dyn_cast<gcall *>(st)) {
          // NOESCAPE: an argument that the callee, by GCC's own description of the builtin, does not retain forms no pointer another thread
          // can hold: the va_list builtins (they store frame-derived pointers into the va_list object and keep nothing) and every normal
          // builtin whose fnspec says the argument does not escape (memcpy, memset, strlen, ...). ASM-001: the compiler's builtin semantics.
          tree bf = gimple_call_fndecl(call);
          bool nb = bf && fndecl_built_in_p(bf, BUILT_IN_NORMAL);
          bool va = nb && (DECL_FUNCTION_CODE(bf) == BUILT_IN_VA_START || DECL_FUNCTION_CODE(bf) == BUILT_IN_VA_END ||
                           DECL_FUNCTION_CODE(bf) == BUILT_IN_VA_COPY);
          attr_fnspec sp = gimple_call_fnspec(call);
          for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
            if (va) continue;
            if (nb && sp.known_p() && sp.arg_specified_p(i) && sp.arg_noescape_p(i)) continue;
            note(gimple_call_arg(call, i));
          }
          note(gimple_call_chain(call));
        } else if (greturn *r = dyn_cast<greturn *>(st)) {
          note(gimple_return_retval(r));
        } else if (gasm *as = dyn_cast<gasm *>(st)) {
          for (unsigned i = 0; i < gimple_asm_ninputs(as); i++) note(TREE_VALUE(gimple_asm_input_op(as, i)));
          for (unsigned i = 0; i < gimple_asm_noutputs(as); i++) {
            tree o = TREE_VALUE(gimple_asm_output_op(as, i));
            tree b = ref_base(o);
            if (b && DECL_P(b)) esc.insert(b);
          }
        } else if (gassign *a = dyn_cast<gassign *>(st)) {
          if (gimple_assign_single_p(a) && !is_gimple_reg(gimple_assign_lhs(a)) && store_nonlocal(gimple_assign_lhs(a)))
            note(gimple_assign_rhs1(a));
        }
      }
    if (all) return;
    for (tree c : cand) if (!esc.count(c)) priv.insert(c);
    return;
  }
  unsigned int execute(function *fun) final override {
    compute(fun);
    // the frame row lists every local at the LAST GIMPLE point: also the ones a pass after the escapes fact created (order's copies of
    // by-value parameters, its temporaries); they are not in `priv`, so they are recorded as escaping
    if (frame_private_done.count(fun->decl)) {
      std::vector<tree> &v = frame_vars[fun->decl];
      v.clear();
      unsigned ix;
      tree d;
      FOR_EACH_LOCAL_DECL(fun, ix, d) v.push_back(d);
    }
    return 0;
  }
};
static void frame_escapes_compute(function *fun) { frame_vars_pass::compute(fun); }
static const pass_data frame_vars_data = {
    GIMPLE_PASS, "fnvnx_frame_vars", OPTGROUP_NONE, TV_NONE, PROP_ssa, 0, 0, TODO_rebuild_alias, 0,
};

struct frame_pass : rtl_opt_pass {
  frame_pass(gcc::context *ctx, const pass_data &d) : rtl_opt_pass(d, ctx) {}

  // The CFA-relative offset of a frame address, after elimination: the CFA is the argument pointer (the stack pointer at entry); a frame
  // register's value is its elimination offset from the stack pointer minus the argument pointer's. One rule for every register a DECL_RTL
  // address is built on after instantiate_virtual_regs and pro_and_epilogue: the frame pointer (sfp), virtual-stack-vars (sfp +
  // STARTING_FRAME_OFFSET), the argument pointer, virtual-incoming-args (ap + FIRST_PARM_OFFSET), the stack pointer, the hard frame
  // pointer; plus constants, nested. Anything else (a pseudo, a register of another meaning) is not a frame address.
  static bool reg_cfa(unsigned regno, tree fndecl, long *v) {
    poly_int64 fp = 0, ap = 0, hf = 0;
    INITIAL_ELIMINATION_OFFSET(FRAME_POINTER_REGNUM, STACK_POINTER_REGNUM, fp);
    INITIAL_ELIMINATION_OFFSET(ARG_POINTER_REGNUM, STACK_POINTER_REGNUM, ap);
    long f, a, h;
    if (!fp.is_constant(&f) || !ap.is_constant(&a)) return false;
    if (regno == FRAME_POINTER_REGNUM) { *v = f - a; return true; }
    if (regno == VIRTUAL_STACK_VARS_REGNUM) { *v = f - a + (long)targetm.starting_frame_offset(); return true; }
    if (regno == ARG_POINTER_REGNUM) { *v = 0; return true; }
    if (regno == VIRTUAL_INCOMING_ARGS_REGNUM) { *v = (long)FIRST_PARM_OFFSET(fndecl); return true; }
    if (regno == STACK_POINTER_REGNUM) { *v = -a; return true; }
    if (regno == HARD_FRAME_POINTER_REGNUM) {
      INITIAL_ELIMINATION_OFFSET(FRAME_POINTER_REGNUM, HARD_FRAME_POINTER_REGNUM, hf);   // sfp - hard fp
      if (!hf.is_constant(&h)) return false;
      *v = f - a - h;
      return true;
    }
    return false;
  }
  static bool slot_offset(rtx addr, tree fndecl, long *off) {
    if (REG_P(addr)) return reg_cfa(REGNO(addr), fndecl, off);
    if (GET_CODE(addr) == PLUS && CONST_INT_P(XEXP(addr, 1))) {
      long b;
      if (!slot_offset(XEXP(addr, 0), fndecl, &b)) return false;
      *off = b + INTVAL(XEXP(addr, 1));
      return true;
    }
    return false;
  }

  unsigned int execute(function *fun) final override {
    // `dynamic` only where the stack pointer has no constant offset in the body: alloca or a variable-length array (calls_alloca). A setjmp or a
    // non-local label does not move the frame: the layout stays constant, the jmp_buf is an address-taken local of it. Every function without
    // alloca has `OFFSET:SIZE` ranges, and an address-taken variable that has no constant frame slot is its own `unk:CAUSE` token, never a
    // reason to give up the whole function.
    bool dynamic = fun->calls_alloca;
    std::map<std::pair<long, long>, bool> rs;           // (offset, size) -> every variable in the slot has escapes=0
    std::set<std::string> unk;
    std::set<tree> &priv = frame_private[fun->decl];
    auto one = [&](tree v) {
      if (!VAR_P(v) && TREE_CODE(v) != PARM_DECL) return;
      if (TREE_STATIC(v) || DECL_EXTERNAL(v) || !TREE_ADDRESSABLE(v)) return;
      rtx m = DECL_RTL_IF_SET(v);
      if (!m) return;                                  // no storage (the variable was optimized out)
      if (!MEM_P(m)) { unk.insert("reg"); return; }    // lives in a register
      long off;
      tree sz = DECL_SIZE_UNIT(v);
      if (!sz || !tree_fits_uhwi_p(sz)) { unk.insert("size"); return; }
      if (!slot_offset(XEXP(m, 0), fun->decl, &off)) { unk.insert("mem"); return; }   // through a pseudo: not a slot of this frame
      std::pair<long, long> k = std::make_pair(off, (long)tree_to_uhwi(sz));
      bool pv = priv.count(v) != 0;
      std::map<std::pair<long, long>, bool>::iterator it = rs.find(k);
      if (it == rs.end()) rs[k] = pv; else it->second = it->second && pv;   // a slot shared by variables: escaping wins
    };
    for (tree v : frame_vars[fun->decl]) one(v);
    for (tree p = DECL_ARGUMENTS(fun->decl); p; p = DECL_CHAIN(p)) one(p);
    fnvnx_frame_row row;
    if (dynamic) row.ranges = "dynamic";
    else {
      for (auto &r : rs)
        row.ranges += (row.ranges.empty() ? "" : " ") + std::to_string(r.first.first) + ":" + std::to_string(r.first.second) + (r.second ? ":n" : "");
      for (auto &u : unk) row.ranges += (row.ranges.empty() ? "" : " ") + ("unk:" + u);
    }
    poly_int64 as = crtl->args.size;
    long args = 0;
    if (!as.is_constant(&args)) args = 0;
    row.args = args;
    frame_lines[IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fun->decl))] = row;
    return 0;
  }
};

static const pass_data frame_data = {
    RTL_PASS, "fnvnx_frame", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0,
};

static void emit_ordt_section() {
  if (ordt_lines.empty()) return;
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.ordt,\"\",%%progbits\n");
  for (auto &l : ordt_lines) {
    std::string t = l.first;
    for (int i = 0; i < 5; i++) t += std::string("\t") + ordt_name[i] + ":" + std::to_string(l.second.reason[i]);
    fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", t.c_str());
  }
  fprintf(asm_out_file, "\t.popsection\n");
}

static void emit_readonly_section() {
  if (ro_lines.empty()) return;
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.ro,\"\",%%progbits\n");
  for (auto &l : ro_lines) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", l.c_str());
  fprintf(asm_out_file, "\t.popsection\n");
}

static void emit_frame_section() {
  cgraph_node *n;
  std::map<std::string, fnvnx_frame_row> pending;
  FOR_EACH_FUNCTION(n) {
    if (!n->definition && !n->alias) continue;
    if (n->inlined_to) continue;
    const char *name = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(n->decl));
    if (frame_lines.count(name)) continue;
    // an alias is the body of its target (same bytes, same frame): the same row; a thunk has no frame of its own (no variable, no stack argument)
    fnvnx_frame_row line = {"", 0};
    if (n->alias) {
      cgraph_node *t = n->ultimate_alias_target();
      auto it = t ? frame_lines.find(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(t->decl))) : frame_lines.end();
      if (it != frame_lines.end()) line = it->second;
    }
    pending[name] = line;
  }
  for (auto &p : pending) frame_lines[p.first] = p.second;
  fnvnx_emit_frame_rows(asm_out_file, frame_lines);
}

static void finish(void *, void *) {
  emit_frame_section();
  emit_readonly_section();
  emit_ordt_section();
  if (verbose)
    inform(UNKNOWN_LOCATION, "%<fnvnx_order%>: unit: %lu ldar, %lu stlr; "
           "%lu calls with arguments right to left, %lu left (class-type argument with side effects)",
           unit_total.ldar, unit_total.stlr, calls_reordered, calls_skipped);
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
  struct register_pass_info vpass;
  vpass.pass = new frame_vars_pass(g, frame_vars_data);
  vpass.reference_pass_name = "*warn_function_noreturn";
  vpass.ref_pass_instance_number = 1;
  vpass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &vpass);
  // the rewrite is the LAST GIMPLE pass (passes.def:435 pass_warn_function_noreturn, right before pass_expand:437): after every pass
  // that creates or changes an access. Insert-after puts the last registered pass first, so it runs before frame_vars_pass, which
  // then records the variables of the code that is compiled
  pass.reference_pass_name = "*warn_function_noreturn";
  pass.ref_pass_instance_number = 1;
  pass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pass);
  struct register_pass_info fpass;
  fpass.pass = new frame_pass(g, frame_data);
  fpass.reference_pass_name = "pro_and_epilogue";
  fpass.ref_pass_instance_number = 1;
  fpass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &fpass);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, pre_genericize, NULL);
  return 0;
}
