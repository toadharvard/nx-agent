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
#include "varasm.h"
#include "output.h"
#include "attr-fnspec.h"
#include "gimple-walk.h"
#include "rtl.h"
#include "emit-rtl.h"
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
struct counts { unsigned long ldar, stlr, fence_ld, fence_st, fence_resume; };
static counts unit_total;
static bool args_rtl = true;
static unsigned long calls_reordered, calls_skipped;
static bool object_last = false;                         // object=first (default) | last | msvc: ORDER-OBJECT
static bool object_msvc = false;                         // object=msvc: MSVC 2008's table (tests/s11/order/order.msvc)
static bool assign_msvc = false;                         // assign=cpp17 (default) | msvc: built-in compound assignment
static bool operator_rtl = false;                        // operator=cpp17 (default) | rtl: ORDER-OPERATOR
static unsigned long calls_object_last, calls_operator, calls_prvalue_virtual, calls_fnptr;

// ---- what counts as shared memory

// LOCAL: ordering exists only for memory another thread can observe. Two facts, both decided by the declaration alone:
//  (1) a decl base that is not aliased (an automatic variable or parameter that no operand of this function's GIMPLE takes
//      the address of, a gimple temporary) is visible to one thread only: no access to it is ordered and its address is never
//      taken (local_unshared).  "Takes the address of" is decided on the GIMPLE of the function by address_taken_scan, NOT
//      by TREE_ADDRESSABLE: the front end sets that flag whenever it formed `&x` at any point (for a trivial constructor or
//      copy it later folds away: every aggregate local of C++ carries it), so it says nothing about a pointer that exists.
//      PROOF.  A GIMPLE object that is neither static, external nor thread-local is named only by its own decl; another
//      thread can reach it only through an address, and an address of a decl comes into being by exactly these operations:
//      an ADDR_EXPR whose base is the decl (an rvalue operand: assignment, call argument, call function, static chain,
//      constructor element, switch/cond/return operand; an ADDR_EXPR as the base of a MEM_REF/TARGET_MEM_REF is an access
//      form, no pointer value); the hidden result pointer of a call whose result is returned in memory (aggregate_value_p)
//      and whose lhs has the decl as base; an operand of an asm statement (a memory constraint takes the address implicitly).
//      address_taken_scan collects the decls of all three over every statement; the scan covers every operand of every
//      statement kind, so a decl outside the set has no pointer anywhere (ASM-001: the compiler's GIMPLE semantics).
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
static std::set<tree> *addr_set;

static void scan_operand(tree t, std::set<tree> &out) {
  if (!t) return;
  switch (TREE_CODE(t)) {
    case ADDR_EXPR: {
      tree b = get_base_address(TREE_OPERAND(t, 0));
      if (b && DECL_P(b)) out.insert(b);
      scan_operand(TREE_OPERAND(t, 0), out);
      return;
    }
    case MEM_REF: case TARGET_MEM_REF: {
      // MEM_REF[&x + c] is an access to x, not a pointer value: its ADDR_EXPR base takes no address
      tree b = TREE_OPERAND(t, 0);
      scan_operand(TREE_CODE(b) == ADDR_EXPR ? TREE_OPERAND(b, 0) : b, out);
      for (int i = 1; i < TREE_OPERAND_LENGTH(t); i++) scan_operand(TREE_OPERAND(t, i), out);
      return;
    }
    case CONSTRUCTOR: {
      unsigned long i;
      tree idx, val;
      FOR_EACH_CONSTRUCTOR_ELT(CONSTRUCTOR_ELTS(t), i, idx, val) { scan_operand(idx, out); scan_operand(val, out); }
      return;
    }
    case TREE_LIST:
      for (; t; t = TREE_CHAIN(t)) scan_operand(TREE_VALUE(t), out);
      return;
    default:
      if (EXPR_P(t) || handled_component_p(t))
        for (int i = 0; i < TREE_OPERAND_LENGTH(t); i++) scan_operand(TREE_OPERAND(t, i), out);
      return;
  }
}

static void address_taken_scan(function *fun, std::set<tree> &out) {
  basic_block bb;
  FOR_EACH_BB_FN(bb, fun)
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
      gimple *st = gsi_stmt(gsi);
      // NOESCAPE: an address passed to a builtin for an argument that GCC's own fnspec of the builtin says does not escape
      // (the callee neither stores the pointer nor lets it outlive the call: memcpy, memset, memcmp, strlen, ...) forms no
      // pointer another thread can hold, so it does not make its object shared (ASM-001: the compiler's builtin fnspecs).
      gcall *bc = dyn_cast<gcall *>(st);
      tree bfn = bc ? gimple_call_fndecl(bc) : NULL_TREE;
      bool nb = bfn && fndecl_built_in_p(bfn, BUILT_IN_NORMAL) && gimple_call_fnspec(bc).known_p();
      for (unsigned i = 0; i < gimple_num_ops(st); i++) {
        if (nb && i >= 3 && i - 3 < gimple_call_num_args(bc)) {
          attr_fnspec sp = gimple_call_fnspec(bc);
          tree a = gimple_call_arg(bc, i - 3);
          if (TREE_CODE(a) == ADDR_EXPR && sp.arg_specified_p(i - 3) && sp.arg_noescape_p(i - 3)) continue;
        }
        scan_operand(gimple_op(st, i), out);
      }
      if (gcall *call = dyn_cast<gcall *>(st)) {
        scan_operand(gimple_call_chain(call), out);
        tree lhs = gimple_call_lhs(call);
        if (lhs && aggregate_value_p(TREE_TYPE(lhs), gimple_call_fntype(call))) {
          tree b = get_base_address(lhs);
          if (b && DECL_P(b)) out.insert(b);   // the hidden result pointer
        }
      } else if (gasm *as = dyn_cast<gasm *>(st)) {
        auto asm_ops = [&](unsigned n, tree (*get)(const gasm *, unsigned)) {
          for (unsigned i = 0; i < n; i++) {
            tree v = TREE_VALUE(get(as, i));
            tree b = get_base_address(v);
            if (b && DECL_P(b)) out.insert(b);   // a memory constraint takes the address implicitly
            scan_operand(v, out);
          }
        };
        asm_ops(gimple_asm_noutputs(as), [](const gasm *a, unsigned i) { return gimple_asm_output_op(a, i); });
        asm_ops(gimple_asm_ninputs(as), [](const gasm *a, unsigned i) { return gimple_asm_input_op(a, i); });
      }
    }
}

static bool local_unshared(tree base) {
  if (!base) return true;
  if (hard_register(base)) return true;
  if (TREE_CODE(base) == VAR_DECL || TREE_CODE(base) == PARM_DECL || TREE_CODE(base) == RESULT_DECL)
    // a result or parameter passed by invisible reference is the object the caller named (a return slot: `g = f()`
    // may construct f's result in g itself): a pointer dereference, not a frame variable of this function
    return !memory_result(base) && !TREE_STATIC(base) && !DECL_EXTERNAL(base) && !DECL_THREAD_LOCAL_P(base) &&
           !(addr_set ? addr_set->count(base) != 0 : TREE_ADDRESSABLE(base));
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
      return !hard_register(t) && (TREE_STATIC(t) || DECL_EXTERNAL(t) || TREE_ADDRESSABLE(t));
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
  tree base = get_base_address(t);
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

static tree uint_type(int idx) {
  static const int bits[] = {8, 16, 32, 64};
  return build_nonstandard_integer_type(bits[idx], 1);
}

static gimple *fence(enum memmodel m) {
  return gimple_build_call(builtin_decl_explicit(BUILT_IN_ATOMIC_THREAD_FENCE), 1, build_int_cst(integer_type_node, m));
}

// force_gimple_operand replaces its sequence; gimplify into a fresh one and append it
// mark a declaration as living in memory: its address is about to be taken. A gimplifier temporary of a register type is
// a gimple register (it must never be addressable), so that flag goes too
static void force_addressable(tree d) {
  if (!d || !DECL_P(d)) return;
  DECL_NOT_GIMPLE_REG_P(d) = 1;
  ::mark_addressable(d);
}

static tree gimplify_to(tree e, gimple_seq *seq) {
  gimple_seq s = NULL;
  tree v = force_gimple_operand(e, &s, true, NULL_TREE);
  gimple_seq_add_seq(seq, s);
  return v;
}

static tree address_of(tree ref, gimple_seq *seq) {
  tree base = get_base_address(ref);
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

// ORDER-CONT: where the acquire fence after a load goes when the statement ends its block.
//
// The acquire fence (dmb ishld) after a load L orders L before every later access of the same thread. A later access
// after a block-ending statement S is reached only along S's continuation:
//   (a) S's fall-through edge, or
//   (b) a resumption point: an EH landing pad, or the point after a returns-twice call (the setjmp family,
//       __builtin_setjmp_receiver, a nonlocal-goto receiver) in this function or in another frame (longjmp, unwinding).
// Rule: (1) a block-ending S with a fall-through edge gets the fence on that edge. (2) Every resumption point of every
// function this plugin orders begins with the same acquire fence (add_resume_fences): the head of every block that is
// the target of an EDGE_EH edge; right AFTER a returns-twice call or __builtin_setjmp_receiver (execution resumes after
// the call, not before it); the head of every other block that is the target of an EDGE_ABNORMAL edge (a nonlocal
// label). (3) A block-ending S without a fall-through edge (a noreturn call such as __builtin_longjmp, a resx) needs
// nothing on its other paths: every continuation of S starts at a resumption point or does not exist. No error path.
// Proof: TSO orders a thread's accesses in program order; every program-order successor of S lies on the fall-through
// edge or after a resumption point; each of those begins with the fence; extra fences on other incoming paths of a
// resumption point only add order. Scope: native code that loads fnvnx_order. Nonlocal entries land at native
// receivers (which begin with the fence above), and every lifted access carries its own ordering (L3a, lifter rule R9).
static void fence_after(gimple_stmt_iterator *gsi, enum memmodel m) {
  gimple *stmt = gsi_stmt(*gsi);
  if (stmt_ends_bb_p(stmt)) {
    edge e = find_fallthru_edge(gimple_bb(stmt)->succs);
    if (!e) return;   // no fall-through edge: covered by ORDER-CONT (2), counts nothing
    gsi_insert_on_edge_immediate(e, fence(m));
  } else {
    gsi_insert_after(gsi, fence(m), GSI_SAME_STMT);
  }
  (m == MEMMODEL_ACQUIRE ? cur->fence_ld : cur->fence_st)++;
}

static bool resume_call_p(gimple *stmt) {
  gcall *call = dyn_cast<gcall *>(stmt);
  if (!call) return false;
  if (gimple_call_flags(call) & ECF_RETURNS_TWICE) return true;
  tree fn = gimple_call_fndecl(call);
  return fn && fndecl_built_in_p(fn, BUILT_IN_SETJMP_RECEIVER);
}

// FENCE-MERGE: a resume fence is not inserted where the statement right next to it, in the same block with nothing between,
// already is the identical acquire fence (dmb ishld): two equal fences in a row order exactly what one orders.
static bool acquire_fence_p(gimple *stmt) {
  gcall *call = stmt ? dyn_cast<gcall *>(stmt) : NULL;
  if (!call || !gimple_call_builtin_p(call, BUILT_IN_ATOMIC_THREAD_FENCE)) return false;
  tree m = gimple_call_arg(call, 0);
  return TREE_CODE(m) == INTEGER_CST && tree_to_uhwi(m) == MEMMODEL_ACQUIRE;
}

// ORDER-CONT (2): the fence at every resumption point of the function. Run after the per-statement walk (the fences
// are then not visited as accesses).
static void add_resume_fences(function *fun) {
  std::vector<gimple *> after;
  std::vector<basic_block> heads;
  basic_block bb;
  FOR_EACH_BB_FN(bb, fun) {
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi))
      if (resume_call_p(gsi_stmt(gsi))) after.push_back(gsi_stmt(gsi));
    bool head = false;
    edge e; edge_iterator ei;
    FOR_EACH_EDGE(e, ei, bb->preds) {
      if (e->flags & EDGE_EH) head = true;
      else if (e->flags & EDGE_ABNORMAL) {
        gimple_stmt_iterator g = gsi_after_labels(bb);
        if (gsi_end_p(g) || !resume_call_p(gsi_stmt(g))) head = true;   // else the fence after that call covers it
      }
    }
    if (head) heads.push_back(bb);
  }
  for (gimple *s : after) {
    gimple_stmt_iterator g = gsi_for_stmt(s);
    if (stmt_ends_bb_p(s)) {
      edge e = find_fallthru_edge(gimple_bb(s)->succs);
      if (!e) continue;
      gsi_insert_on_edge_immediate(e, fence(MEMMODEL_ACQUIRE));
    } else {
      gimple_stmt_iterator nx = g;
      gsi_next(&nx);
      if (!gsi_end_p(nx) && acquire_fence_p(gsi_stmt(nx))) continue;   // FENCE-MERGE
      gsi_insert_after(&g, fence(MEMMODEL_ACQUIRE), GSI_SAME_STMT);
    }
    cur->fence_resume++;
  }
  for (basic_block b : heads) {
    gimple_stmt_iterator g = gsi_after_labels(b);
    if (!gsi_end_p(g) && acquire_fence_p(gsi_stmt(g))) continue;   // FENCE-MERGE
    gsi_insert_before(&g, fence(MEMMODEL_ACQUIRE), GSI_SAME_STMT);
    cur->fence_resume++;
  }
}

// a temporary of any type: create_tmp_var refuses a type that must live in memory (TREE_ADDRESSABLE: a C++ class with a
// non-trivial copy), the raw form takes it
static tree make_tmp(tree ty, const char *name) {
  if (!TREE_ADDRESSABLE(ty)) return create_tmp_var(ty, name);
  tree d = create_tmp_var_raw(ty, name);
  gimple_add_tmp_var(d);
  return d;
}

// an operand that is a shared memory reference but sits where only a value can (a call argument, a return value, a
// condition, an arithmetic operand): read it into a local first with an ordinary assignment, ordered as any other
// load, and use the local
static void hoist_read(gimple_stmt_iterator *gsi, gimple *stmt, unsigned i) {
  tree op = gimple_op(stmt, i);
  tree tmp = make_tmp(TREE_TYPE(op), "order_a");
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
  tree base = get_base_address(TREE_OPERAND(a, 0));
  return base && (TREE_CODE(base) == STRING_CST || readonly_object(base));
}

// FOLDSET-BEGIN  generated by gen_foldset.py from the pinned gcc-13.3.0.tar.xz (sha256 0845e9621c9543a13f484e94584a49ffc0129970e9914624235fc1d061a0c083)
// gcc/gimple-fold.cc sha256 f999480bbdeef63afc3679ae59cd3134a83ece89ec477b5c9b4464c6fb1e2875, gcc/builtins.def sha256 bbd89bafc02c73b4ec6b15b4322fc517fa8fe58ded45a747892b0e4f12d78d0b; do not edit
static const struct { built_in_function code; const char *where; } fold_set[] = {
  {BUILT_IN_BCMP, "gimple_fold_builtin gimple-fold.cc:5004"},
  {BUILT_IN_BCOPY, "gimple_fold_builtin gimple-fold.cc:5006"},
  {BUILT_IN_BZERO, "gimple_fold_builtin gimple-fold.cc:5008"},
  {BUILT_IN_MEMSET, "gimple_fold_builtin gimple-fold.cc:5011"},
  {BUILT_IN_MEMCPY, "gimple_fold_builtin gimple-fold.cc:5015"},
  {BUILT_IN_MEMPCPY, "gimple_fold_builtin gimple-fold.cc:5016"},
  {BUILT_IN_MEMMOVE, "gimple_fold_builtin gimple-fold.cc:5017"},
  {BUILT_IN_SPRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5020"},
  {BUILT_IN_VSPRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5021"},
  {BUILT_IN_STRCAT_CHK, "gimple_fold_builtin gimple-fold.cc:5023"},
  {BUILT_IN_STRNCAT_CHK, "gimple_fold_builtin gimple-fold.cc:5025"},
  {BUILT_IN_STRLEN, "gimple_fold_builtin gimple-fold.cc:5027"},
  {BUILT_IN_STRCPY, "gimple_fold_builtin gimple-fold.cc:5029"},
  {BUILT_IN_STRNCPY, "gimple_fold_builtin gimple-fold.cc:5033"},
  {BUILT_IN_STRCAT, "gimple_fold_builtin gimple-fold.cc:5038"},
  {BUILT_IN_STRNCAT, "gimple_fold_builtin gimple-fold.cc:5041"},
  {BUILT_IN_INDEX, "gimple_fold_builtin gimple-fold.cc:5043"},
  {BUILT_IN_STRCHR, "gimple_fold_builtin gimple-fold.cc:5044"},
  {BUILT_IN_RINDEX, "gimple_fold_builtin gimple-fold.cc:5046"},
  {BUILT_IN_STRRCHR, "gimple_fold_builtin gimple-fold.cc:5047"},
  {BUILT_IN_STRSTR, "gimple_fold_builtin gimple-fold.cc:5049"},
  {BUILT_IN_STRCMP, "gimple_fold_builtin gimple-fold.cc:5051"},
  {BUILT_IN_STRCMP_EQ, "gimple_fold_builtin gimple-fold.cc:5052"},
  {BUILT_IN_STRCASECMP, "gimple_fold_builtin gimple-fold.cc:5053"},
  {BUILT_IN_STRNCMP, "gimple_fold_builtin gimple-fold.cc:5054"},
  {BUILT_IN_STRNCMP_EQ, "gimple_fold_builtin gimple-fold.cc:5055"},
  {BUILT_IN_STRNCASECMP, "gimple_fold_builtin gimple-fold.cc:5056"},
  {BUILT_IN_MEMCHR, "gimple_fold_builtin gimple-fold.cc:5058"},
  {BUILT_IN_FPUTS, "gimple_fold_builtin gimple-fold.cc:5060"},
  {BUILT_IN_FPUTS_UNLOCKED, "gimple_fold_builtin gimple-fold.cc:5063"},
  {BUILT_IN_MEMCPY_CHK, "gimple_fold_builtin gimple-fold.cc:5066"},
  {BUILT_IN_MEMPCPY_CHK, "gimple_fold_builtin gimple-fold.cc:5067"},
  {BUILT_IN_MEMMOVE_CHK, "gimple_fold_builtin gimple-fold.cc:5068"},
  {BUILT_IN_MEMSET_CHK, "gimple_fold_builtin gimple-fold.cc:5069"},
  {BUILT_IN_STPCPY, "gimple_fold_builtin gimple-fold.cc:5076"},
  {BUILT_IN_STRCPY_CHK, "gimple_fold_builtin gimple-fold.cc:5078"},
  {BUILT_IN_STPCPY_CHK, "gimple_fold_builtin gimple-fold.cc:5079"},
  {BUILT_IN_STRNCPY_CHK, "gimple_fold_builtin gimple-fold.cc:5085"},
  {BUILT_IN_STPNCPY_CHK, "gimple_fold_builtin gimple-fold.cc:5086"},
  {BUILT_IN_SNPRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5093"},
  {BUILT_IN_VSNPRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5094"},
  {BUILT_IN_FPRINTF, "gimple_fold_builtin gimple-fold.cc:5097"},
  {BUILT_IN_FPRINTF_UNLOCKED, "gimple_fold_builtin gimple-fold.cc:5098"},
  {BUILT_IN_VFPRINTF, "gimple_fold_builtin gimple-fold.cc:5099"},
  {BUILT_IN_FPRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5109"},
  {BUILT_IN_VFPRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5110"},
  {BUILT_IN_PRINTF, "gimple_fold_builtin gimple-fold.cc:5120"},
  {BUILT_IN_PRINTF_UNLOCKED, "gimple_fold_builtin gimple-fold.cc:5121"},
  {BUILT_IN_VPRINTF, "gimple_fold_builtin gimple-fold.cc:5122"},
  {BUILT_IN_PRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5129"},
  {BUILT_IN_VPRINTF_CHK, "gimple_fold_builtin gimple-fold.cc:5130"},
  {BUILT_IN_ACC_ON_DEVICE, "gimple_fold_builtin gimple-fold.cc:5137"},
  {BUILT_IN_REALLOC, "gimple_fold_builtin gimple-fold.cc:5140"},
  {BUILT_IN_CLEAR_PADDING, "gimple_fold_builtin gimple-fold.cc:5143"},
};
// builtins with no library function that take a pointer (builtins.def line)
static const struct { built_in_function code; int line; } nolib_ptr[] = {
  {BUILT_IN_NONE, 287},
  {BUILT_IN_SADD_OVERFLOW, 919},
  {BUILT_IN_SADDL_OVERFLOW, 920},
  {BUILT_IN_SADDLL_OVERFLOW, 921},
  {BUILT_IN_SSUB_OVERFLOW, 922},
  {BUILT_IN_SSUBL_OVERFLOW, 923},
  {BUILT_IN_SSUBLL_OVERFLOW, 924},
  {BUILT_IN_SMUL_OVERFLOW, 925},
  {BUILT_IN_SMULL_OVERFLOW, 926},
  {BUILT_IN_SMULLL_OVERFLOW, 927},
  {BUILT_IN_UADD_OVERFLOW, 928},
  {BUILT_IN_UADDL_OVERFLOW, 929},
  {BUILT_IN_UADDLL_OVERFLOW, 930},
  {BUILT_IN_USUB_OVERFLOW, 931},
  {BUILT_IN_USUBL_OVERFLOW, 932},
  {BUILT_IN_USUBLL_OVERFLOW, 933},
  {BUILT_IN_UMUL_OVERFLOW, 934},
  {BUILT_IN_UMULL_OVERFLOW, 935},
  {BUILT_IN_UMULLL_OVERFLOW, 936},
  {BUILT_IN_AGGREGATE_INCOMING_ADDRESS, 941},
  {BUILT_IN_APPLY, 943},
  {BUILT_IN_APPLY_ARGS, 944},
  {BUILT_IN_DWARF_CFA, 970},
  {BUILT_IN_EH_RETURN, 972},
  {BUILT_IN_ASSUME_ALIGNED, 983},
  {BUILT_IN_EXTEND_POINTER, 984},
  {BUILT_IN_EXTRACT_RETURN_ADDR, 985},
  {BUILT_IN_FRAME_ADDRESS, 991},
  {BUILT_IN_FROB_RETURN_ADDR, 994},
  {BUILT_IN_INIT_DWARF_REG_SIZES, 997},
  {BUILT_IN_LONGJMP, 1029},
  {BUILT_IN_NEXT_ARG, 1032},
  {BUILT_IN_PREFETCH, 1042},
  {BUILT_IN_RETURN, 1044},
  {BUILT_IN_RETURN_ADDRESS, 1045},
  {BUILT_IN_SAVEREGS, 1046},
  {BUILT_IN_SETJMP, 1047},
  {BUILT_IN_UPDATE_SETJMP_BUF, 1054},
  {BUILT_IN_VA_COPY, 1055},
  {BUILT_IN_VA_END, 1056},
  {BUILT_IN_VA_START, 1057},
  {BUILT_IN_INIT_TRAMPOLINE, 1064},
  {BUILT_IN_INIT_HEAP_TRAMPOLINE, 1065},
  {BUILT_IN_ADJUST_TRAMPOLINE, 1066},
  {BUILT_IN_INIT_DESCRIPTOR, 1067},
  {BUILT_IN_ADJUST_DESCRIPTOR, 1068},
  {BUILT_IN_NONLOCAL_GOTO, 1069},
  {BUILT_IN_SETJMP_SETUP, 1072},
  {BUILT_IN_SETJMP_RECEIVER, 1073},
  {BUILT_IN_STACK_SAVE, 1076},
  {BUILT_IN_STACK_RESTORE, 1077},
  {BUILT_IN_ALLOCA_WITH_ALIGN, 1078},
  {BUILT_IN_ALLOCA_WITH_ALIGN_AND_MAX, 1079},
  {BUILT_IN_MEMCMP_EQ, 1083},
  {BUILT_IN_STRCMP_EQ, 1087},
  {BUILT_IN_STRNCMP_EQ, 1088},
  {BUILT_IN_OBJECT_SIZE, 1091},
  {BUILT_IN_DYNAMIC_OBJECT_SIZE, 1092},
  {BUILT_IN_SPECULATION_SAFE_VALUE_PTR, 1148},
  {BUILT_IN_UNWIND_RESUME, 1164},
  {BUILT_IN_CXA_END_CLEANUP, 1165},
  {BUILT_IN_EH_POINTER, 1166},
  {BUILT_IN_EH_FILTER, 1167},
  {BUILT_IN_EH_COPY_VALUES, 1168},
};
// the memory-order parameters (bit i = argument i) of the __atomic builtins, sync-builtins.def line
static const struct { built_in_function code; unsigned mask; int line; } atomic_order[] = {
  {BUILT_IN_ATOMIC_TEST_AND_SET, 0x2, 262},
  {BUILT_IN_ATOMIC_CLEAR, 0x2, 265},
  {BUILT_IN_ATOMIC_EXCHANGE, 0x10, 268},
  {BUILT_IN_ATOMIC_EXCHANGE_1, 0x4, 274},
  {BUILT_IN_ATOMIC_EXCHANGE_2, 0x4, 277},
  {BUILT_IN_ATOMIC_EXCHANGE_4, 0x4, 280},
  {BUILT_IN_ATOMIC_EXCHANGE_8, 0x4, 283},
  {BUILT_IN_ATOMIC_EXCHANGE_16, 0x4, 286},
  {BUILT_IN_ATOMIC_LOAD, 0x8, 290},
  {BUILT_IN_ATOMIC_LOAD_1, 0x2, 297},
  {BUILT_IN_ATOMIC_LOAD_2, 0x2, 300},
  {BUILT_IN_ATOMIC_LOAD_4, 0x2, 303},
  {BUILT_IN_ATOMIC_LOAD_8, 0x2, 306},
  {BUILT_IN_ATOMIC_LOAD_16, 0x2, 309},
  {BUILT_IN_ATOMIC_COMPARE_EXCHANGE, 0x30, 313},
  {BUILT_IN_ATOMIC_COMPARE_EXCHANGE_1, 0x30, 320},
  {BUILT_IN_ATOMIC_COMPARE_EXCHANGE_2, 0x30, 324},
  {BUILT_IN_ATOMIC_COMPARE_EXCHANGE_4, 0x30, 328},
  {BUILT_IN_ATOMIC_COMPARE_EXCHANGE_8, 0x30, 332},
  {BUILT_IN_ATOMIC_COMPARE_EXCHANGE_16, 0x30, 336},
  {BUILT_IN_ATOMIC_STORE, 0x8, 341},
  {BUILT_IN_ATOMIC_STORE_1, 0x4, 347},
  {BUILT_IN_ATOMIC_STORE_2, 0x4, 350},
  {BUILT_IN_ATOMIC_STORE_4, 0x4, 353},
  {BUILT_IN_ATOMIC_STORE_8, 0x4, 356},
  {BUILT_IN_ATOMIC_STORE_16, 0x4, 359},
  {BUILT_IN_ATOMIC_ADD_FETCH_1, 0x4, 366},
  {BUILT_IN_ATOMIC_ADD_FETCH_2, 0x4, 369},
  {BUILT_IN_ATOMIC_ADD_FETCH_4, 0x4, 372},
  {BUILT_IN_ATOMIC_ADD_FETCH_8, 0x4, 375},
  {BUILT_IN_ATOMIC_ADD_FETCH_16, 0x4, 378},
  {BUILT_IN_ATOMIC_SUB_FETCH_1, 0x4, 385},
  {BUILT_IN_ATOMIC_SUB_FETCH_2, 0x4, 388},
  {BUILT_IN_ATOMIC_SUB_FETCH_4, 0x4, 391},
  {BUILT_IN_ATOMIC_SUB_FETCH_8, 0x4, 394},
  {BUILT_IN_ATOMIC_SUB_FETCH_16, 0x4, 397},
  {BUILT_IN_ATOMIC_AND_FETCH_1, 0x4, 404},
  {BUILT_IN_ATOMIC_AND_FETCH_2, 0x4, 407},
  {BUILT_IN_ATOMIC_AND_FETCH_4, 0x4, 410},
  {BUILT_IN_ATOMIC_AND_FETCH_8, 0x4, 413},
  {BUILT_IN_ATOMIC_AND_FETCH_16, 0x4, 416},
  {BUILT_IN_ATOMIC_NAND_FETCH_1, 0x4, 423},
  {BUILT_IN_ATOMIC_NAND_FETCH_2, 0x4, 426},
  {BUILT_IN_ATOMIC_NAND_FETCH_4, 0x4, 429},
  {BUILT_IN_ATOMIC_NAND_FETCH_8, 0x4, 432},
  {BUILT_IN_ATOMIC_NAND_FETCH_16, 0x4, 435},
  {BUILT_IN_ATOMIC_XOR_FETCH_1, 0x4, 442},
  {BUILT_IN_ATOMIC_XOR_FETCH_2, 0x4, 445},
  {BUILT_IN_ATOMIC_XOR_FETCH_4, 0x4, 448},
  {BUILT_IN_ATOMIC_XOR_FETCH_8, 0x4, 451},
  {BUILT_IN_ATOMIC_XOR_FETCH_16, 0x4, 454},
  {BUILT_IN_ATOMIC_OR_FETCH_1, 0x4, 461},
  {BUILT_IN_ATOMIC_OR_FETCH_2, 0x4, 464},
  {BUILT_IN_ATOMIC_OR_FETCH_4, 0x4, 467},
  {BUILT_IN_ATOMIC_OR_FETCH_8, 0x4, 470},
  {BUILT_IN_ATOMIC_OR_FETCH_16, 0x4, 473},
  {BUILT_IN_ATOMIC_FETCH_ADD_1, 0x4, 480},
  {BUILT_IN_ATOMIC_FETCH_ADD_2, 0x4, 483},
  {BUILT_IN_ATOMIC_FETCH_ADD_4, 0x4, 486},
  {BUILT_IN_ATOMIC_FETCH_ADD_8, 0x4, 489},
  {BUILT_IN_ATOMIC_FETCH_ADD_16, 0x4, 492},
  {BUILT_IN_ATOMIC_FETCH_SUB_1, 0x4, 499},
  {BUILT_IN_ATOMIC_FETCH_SUB_2, 0x4, 502},
  {BUILT_IN_ATOMIC_FETCH_SUB_4, 0x4, 505},
  {BUILT_IN_ATOMIC_FETCH_SUB_8, 0x4, 508},
  {BUILT_IN_ATOMIC_FETCH_SUB_16, 0x4, 511},
  {BUILT_IN_ATOMIC_FETCH_AND_1, 0x4, 518},
  {BUILT_IN_ATOMIC_FETCH_AND_2, 0x4, 521},
  {BUILT_IN_ATOMIC_FETCH_AND_4, 0x4, 524},
  {BUILT_IN_ATOMIC_FETCH_AND_8, 0x4, 527},
  {BUILT_IN_ATOMIC_FETCH_AND_16, 0x4, 530},
  {BUILT_IN_ATOMIC_FETCH_NAND_1, 0x4, 537},
  {BUILT_IN_ATOMIC_FETCH_NAND_2, 0x4, 540},
  {BUILT_IN_ATOMIC_FETCH_NAND_4, 0x4, 543},
  {BUILT_IN_ATOMIC_FETCH_NAND_8, 0x4, 546},
  {BUILT_IN_ATOMIC_FETCH_NAND_16, 0x4, 549},
  {BUILT_IN_ATOMIC_FETCH_XOR_1, 0x4, 556},
  {BUILT_IN_ATOMIC_FETCH_XOR_2, 0x4, 559},
  {BUILT_IN_ATOMIC_FETCH_XOR_4, 0x4, 562},
  {BUILT_IN_ATOMIC_FETCH_XOR_8, 0x4, 565},
  {BUILT_IN_ATOMIC_FETCH_XOR_16, 0x4, 568},
  {BUILT_IN_ATOMIC_FETCH_OR_1, 0x4, 576},
  {BUILT_IN_ATOMIC_FETCH_OR_2, 0x4, 579},
  {BUILT_IN_ATOMIC_FETCH_OR_4, 0x4, 582},
  {BUILT_IN_ATOMIC_FETCH_OR_8, 0x4, 585},
  {BUILT_IN_ATOMIC_FETCH_OR_16, 0x4, 588},
  {BUILT_IN_ATOMIC_THREAD_FENCE, 0x1, 601},
  {BUILT_IN_ATOMIC_SIGNAL_FENCE, 0x1, 605},
  {BUILT_IN_ATOMIC_FERAISEEXCEPT, 0x1, 612},
};
// FOLDSET-END

static bool in_fold_set(built_in_function c) {
  for (const auto &e : fold_set) if (e.code == c) return true;
  return false;
}
static bool in_nolib_ptr(built_in_function c) {
  for (const auto &e : nolib_ptr) if (e.code == c) return true;
  return false;
}

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

// REDIRECT RECORD (.fnvnx.redir, no SHF_ALLOC): one line `symbol<TAB>kind` per library function that a builtin call of
// this unit was redirected to. kind `string-op` when the builtin is in the fold set (gimple-fold.cc's folder rewrites it:
// the mem*/str* family, their _chk forms and the printf family that folds into them); kind `libcall` for every other
// redirected library builtin (a math function with an output pointer, for example). Every redirected call is
// `dmb ish; bl <symbol>; dmb ish`.
static std::set<std::string> redir_lines;

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

// a call GCC may expand or fold into plain accesses that reads or writes memory through its pointer arguments: the memory
// is no GIMPLE operand. GCC's own fnspec of the callee says which pointer arguments it may read or write (unknown spec:
// all, both ways). Pointer arguments that cannot be shared memory (the address of a constant, of an unshared local) are
// out of scope.
static void order_builtin_call(gimple_stmt_iterator *gsi, gcall *call) {
  tree fn = gimple_call_fndecl(call);
  if (!fn || !fndecl_built_in_p(fn, BUILT_IN_NORMAL)) return;
  built_in_function code = DECL_FUNCTION_CODE(fn);
  if (code >= BUILT_IN_SYNC_FETCH_AND_ADD_N && code <= BUILT_IN_ATOMIC_FERAISEEXCEPT) {
    raise_atomic_orders(call, code);
    return;
  }
  if (gimple_call_flags(call) & ECF_CONST) return;
  attr_fnspec spec = gimple_call_fnspec(call);
  bool rd = false, wr = false;
  for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
    tree a = gimple_call_arg(call, i);
    if (!POINTER_TYPE_P(TREE_TYPE(a)) || pointer_to_constant(a)) continue;
    // the address of an unshared local (the scan left it unshared because this argument does not escape)
    if (TREE_CODE(a) == ADDR_EXPR && local_unshared(get_base_address(TREE_OPERAND(a, 0)))) continue;
    bool known = spec.known_p() && spec.arg_specified_p(i);
    if (!known || spec.arg_maybe_read_p(i)) rd = true;
    if (!known || spec.arg_maybe_written_p(i)) wr = true;
  }
  if (!rd && !wr) return;
  const char *asmname = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fn));
  if (strncmp(asmname, "__builtin_", 10) != 0) {
    // REDIRECT. Every GCC rewrite of a call into memory accesses is keyed on the callee being a built-in declaration
    // (DECL_BUILT_IN_CLASS: gimple_fold_builtin, match.pd, expand_builtin); the library declaration has none, so the call
    // survives to the object file as a call. A call is no plain access; it is bracketed by full fences (dmb ish before and
    // after), the order the x86 locked/string instructions give the accesses it contains.
    tree lib = library_decl(fn);
    gimple_call_set_fndecl(call, lib);
    gimple_call_set_fntype(call, TREE_TYPE(lib));
    redir_lines.insert(std::string(asmname) + (in_fold_set(code) ? "\tstring-op" : "\tlibcall"));
    gsi_insert_before(gsi, fence(MEMMODEL_SEQ_CST), GSI_SAME_STMT);
    fence_after(gsi, MEMMODEL_SEQ_CST);
    cur->fence_st++;
    return;
  }
  // NO LIBRARY FUNCTION (builtins.def: nolib_ptr). The builtin reads or writes through a pointer and cannot be redirected;
  // GCC's own flags say whether it touches memory at all (ECF_NOVOPS: no virtual operands). Otherwise each pointer
  // operand that may be shared is bounced through an unshared temporary of its pointee type: read in before the call
  // when the spec says it may be read, written back after when it may be written, both created ordered.
  if (gimple_call_flags(call) & ECF_NOVOPS) return;
  if (!in_nolib_ptr(code)) {
    error_at(gimple_location(call), "fnvnx_order: builtin %qs reads or writes shared memory and is neither in the generated "
             "table of builtins without a library function nor redirectable", asmname);
    return;
  }
  // OPAQUE. A pointer operand whose pointee has no size (void *: the jmp_buf of __builtin_setjmp/longjmp, the argument block
  // of __builtin_apply, ...) cannot be bounced. The builtin is the compiler's inline form of a library function (setjmp,
  // longjmp, ...) whose accesses the exe makes inside that function; so it is bracketed like a call: dmb ish before and
  // after, recorded with kind `inline-builtin`. A builtin that ends a basic block (longjmp, a nonlocal goto) is treated
  // the same way: it cannot be followed by a copy-out.
  bool opaque = false;
  for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
    tree a = gimple_call_arg(call, i);
    if (!POINTER_TYPE_P(TREE_TYPE(a)) || pointer_to_constant(a)) continue;
    if (TREE_CODE(a) == ADDR_EXPR && local_unshared(get_base_address(TREE_OPERAND(a, 0)))) continue;
    tree pt = TREE_TYPE(TREE_TYPE(a));
    if (VOID_TYPE_P(pt) || !COMPLETE_TYPE_P(pt) || !TYPE_SIZE_UNIT(pt) || !tree_fits_uhwi_p(TYPE_SIZE_UNIT(pt))) opaque = true;
  }
  if (opaque || stmt_ends_bb_p(call)) {
    redir_lines.insert(std::string(asmname) + "\tinline-builtin");
    gsi_insert_before(gsi, fence(MEMMODEL_SEQ_CST), GSI_SAME_STMT);
    fence_after(gsi, MEMMODEL_SEQ_CST);
    cur->fence_st++;
    return;
  }
  gimple_stmt_iterator after = *gsi;
  for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
    tree a = gimple_call_arg(call, i);
    if (!POINTER_TYPE_P(TREE_TYPE(a)) || pointer_to_constant(a)) continue;
    if (TREE_CODE(a) == ADDR_EXPR && local_unshared(get_base_address(TREE_OPERAND(a, 0)))) continue;
    tree pt = TREE_TYPE(TREE_TYPE(a));
    bool known = spec.known_p() && spec.arg_specified_p(i);
    bool r = !known || spec.arg_maybe_read_p(i), w = !known || spec.arg_maybe_written_p(i);
    tree tmp = make_tmp(pt, "order_b");
    force_addressable(tmp);
    tree ref = build2(MEM_REF, pt, a, build_int_cst(TREE_TYPE(a), 0));
    if (r) {
      gimple *in = gimple_build_assign(tmp, ref);
      gimple_set_location(in, gimple_location(call));
      gsi_insert_before(gsi, in, GSI_SAME_STMT);
      gimple_stmt_iterator g = gsi_for_stmt(in);
      order_single(&g);
      *gsi = gsi_for_stmt(call);
    }
    gimple_call_set_arg(call, i, build_fold_addr_expr(tmp));
    if (w) {
      gimple *out = gimple_build_assign(unshare_expr(ref), tmp);
      gimple_set_location(out, gimple_location(call));
      gsi_insert_after(&after, out, GSI_SAME_STMT);
      gimple_stmt_iterator g = gsi_for_stmt(out);
      order_single(&g);
      *gsi = gsi_for_stmt(call);
      after = *gsi;
    }
  }
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
          tree tmp = make_tmp(TREE_TYPE(lhs), "order_r");
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
        tree tmp = make_tmp(TREE_TYPE(lhs), "order_r");
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
static std::set<basic_block> generated_bbs;   // blocks this pass created: their accesses are already ordered, never revisited

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
  tree rb = get_base_address(rhs);
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
  tree dp = address_of(dst, &pre), sp = address_of(src, &pre);
  unsigned al = MIN(get_object_alignment(dst), get_object_alignment(src));
  unsigned unit = 8;
  while (unit > 1 && (size % unit || al < unit * 8)) unit >>= 1;
  unsigned HOST_WIDE_INT n = size / unit;
  int idx = unit == 1 ? 0 : unit == 2 ? 1 : unit == 4 ? 2 : 3;
  tree ity = uint_type(idx);
  tree aty = build_aligned_type(ity, unit * 8);
  tree zero = build_int_cst(ptr_type_node, 0);
  // the statements of one unit at the pointers (dpp, spp): the load into v, then the store; the store is returned
  auto one = [&](gimple_seq *seq, tree dpp, tree spp, gimple **last) {
    tree v = create_tmp_var(ity, "order_w");
    if (src_sh) {
      gcall *l = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_LOAD_1 + idx)), 2, spp,
                                   build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
      gimple_call_set_lhs(l, v);
      gimple_set_location(l, loc);
      gimple_seq_add_stmt(seq, l);
      c.ldar++;
    } else {
      gimple *l = gimple_build_assign(v, build2(MEM_REF, aty, spp, zero));
      gimple_set_location(l, loc);
      gimple_seq_add_stmt(seq, l);
    }
    gimple *s;
    if (st) {
      s = gimple_build_call(builtin_decl_explicit((built_in_function)(BUILT_IN_ATOMIC_STORE_1 + idx)), 3, dpp, v,
                            build_int_cst(integer_type_node, MEMMODEL_RELEASE));
      c.stlr++;
    } else {
      s = gimple_build_assign(build2(MEM_REF, aty, dpp, zero), v);
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
      tree dpp = k ? at(&pre, dp, off) : dp, spp = k ? at(&pre, sp, off) : sp;
      one(&pre, dpp, spp, k + 1 == n ? &last : NULL);
    }
    gsi_insert_seq_before(gsi, pre, GSI_SAME_STMT);
    gsi_insert_before(gsi, last, GSI_SAME_STMT);
    // the original statement goes (gsi_replace wants the same lhs, so a nop; the register destination gets its copy)
    gsi_replace(gsi, fin ? gimple_build_assign(fin, dst) : gimple_build_nop(), true);
    return;
  }
  // more than STRAIGHT units: a loop over units 0..n-2 (do { dst[i] = src[i]; i += unit; } while (i < (n-1)*unit)), then the
  // last unit in place of the statement: the code does not grow with the object
  tree iv = make_tmp(sizetype, "order_i");
  gimple_seq_add_stmt(&pre, gimple_build_assign(iv, size_zero_node));
  gsi_insert_seq_before(gsi, pre, GSI_SAME_STMT);
  basic_block bb = gsi_bb(*gsi);
  gimple_stmt_iterator g = *gsi;
  gsi_prev(&g);
  edge e = split_block(bb, gsi_stmt(g));   // the setup stays in bb, the statement starts the tail
  basic_block tail = e->dest;
  basic_block body = create_empty_bb(bb);
  redirect_edge_succ(e, body);
  gimple_seq bs = NULL;
  tree dpp = at(&bs, dp, iv), spp = at(&bs, sp, iv);
  one(&bs, dpp, spp, NULL);
  gimple_seq_add_stmt(&bs, gimple_build_assign(iv, fold_build2(PLUS_EXPR, sizetype, iv, size_int(unit))));
  gimple_seq_add_stmt(&bs, gimple_build_cond(LT_EXPR, iv, size_int((n - 1) * unit), NULL_TREE, NULL_TREE));
  gimple_stmt_iterator bsi = gsi_start_bb(body);
  gsi_insert_seq_after(&bsi, bs, GSI_CONTINUE_LINKING);
  edge back = make_edge(body, body, EDGE_TRUE_VALUE), out = make_edge(body, tail, EDGE_FALSE_VALUE);
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
  one(&ls, at(&ls, dp, off), at(&ls, sp, off), &last);
  gimple_stmt_iterator ts = gsi_for_stmt(stmt);
  gsi_insert_seq_before(&ts, ls, GSI_SAME_STMT);
  gsi_insert_before(&ts, last, GSI_SAME_STMT);
  gimple *fin_stmt = fin ? gimple_build_assign(fin, dst) : gimple_build_nop();
  gsi_replace(&ts, fin_stmt, true);
  *gsi = gsi_for_stmt(fin_stmt);
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
      tree t2 = make_tmp(ty, "order_v");
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
  // a bit-field access is a read-modify-write of its containing unit and no unit of its own: it keeps its (volatile)
  // access, fenced: dmb ish before a store, dmb ishld after a load, and the hidden load of a store gets the acquire
  // fence a load gets (one rule: every load, written or hidden, is followed by dmb ishld)
  if ((ld && bitfield_ref(rhs)) || (st && bitfield_ref(lhs))) {
    if (st) { gsi_insert_before(gsi, fence(MEMMODEL_RELEASE), GSI_SAME_STMT); c.fence_st++; }
    if (ld || bitfield_ref(lhs)) fence_after(gsi, MEMMODEL_ACQUIRE);
    return;
  }
  // everything else (an aggregate, a scalar ldar/stlr cannot reach, a copy of one shared object into another) is copied
  // unit-wise, each shared unit created ordered
  unit_copy(gsi, lhs, rhs, st, ld);
}

struct order_pass : gimple_opt_pass {
  order_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}

  unsigned int execute(function *fun) final override {
    counts c = {0, 0, 0, 0, 0};
    cur = &c;
    basic_block bb;
    // the decls whose address this function takes (address_taken_scan: operands, the hidden result pointer of a call that
    // returns in memory, asm operands) are the shared locals; they are made addressable before any access is classified, so no
    // earlier statement keeps a plain access to them.  Every other local is unshared (local_unshared).
    std::set<tree> taken;
    address_taken_scan(fun, taken);
    // PARAMETER COPY.  A by-value parameter whose address is taken is a shared object, but the inliner (after this pass)
    // initializes the callee's parameter variable with a plain store when it inlines the body, an access this pass cannot
    // order. So the body works on a local copy instead: `copy = parm` at entry, every use of the parameter (the address
    // included) replaced by the copy. The parameter itself then has no address and no memory (the inliner's init store is
    // an SSA copy), and the copy is the shared object: its entry store is created here and ordered like any other.
    {
      std::map<tree, tree> repl;
      for (tree d : taken)
        if (TREE_CODE(d) == PARM_DECL && !DECL_BY_REFERENCE(d) && !hard_register(d) && !TREE_STATIC(d) && !DECL_EXTERNAL(d))
          repl[d] = make_tmp(TREE_TYPE(d), "order_p");
      if (!repl.empty()) {
        struct W { static tree sub(tree *tp, int *ws, void *data) {
          std::map<tree, tree> *m = (std::map<tree, tree> *)((struct walk_stmt_info *)data)->info;
          auto it = m->find(*tp);
          if (it != m->end()) *tp = it->second;
          else if (TYPE_P(*tp)) *ws = 0;
          return NULL_TREE;
        } };
        basic_block bb2;
        FOR_EACH_BB_FN(bb2, fun)
          for (gimple_stmt_iterator g = gsi_start_bb(bb2); !gsi_end_p(g); gsi_next(&g)) {
            struct walk_stmt_info wi;
            memset(&wi, 0, sizeof wi);
            wi.info = &repl;
            walk_gimple_op(gsi_stmt(g), W::sub, &wi);
            update_stmt(gsi_stmt(g));
          }
        gimple_seq init = NULL;
        for (auto &r : repl) {
          gimple_seq_add_stmt(&init, gimple_build_assign(r.second, r.first));
          taken.erase(r.first);
          taken.insert(r.second);
        }
        gsi_insert_seq_on_edge_immediate(single_succ_edge(ENTRY_BLOCK_PTR_FOR_FN(fun)), init);
      }
    }
    addr_set = &taken;
    for (tree d : taken)
      if ((VAR_P(d) || TREE_CODE(d) == PARM_DECL || TREE_CODE(d) == RESULT_DECL) && !hard_register(d) && !TREE_STATIC(d) && !DECL_EXTERNAL(d))
        force_addressable(d);
    FOR_EACH_BB_FN(bb, fun)
      if (!generated_bbs.count(bb))
        for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) order_stmt(&gsi);
    add_resume_fences(fun);
    unit_total.ldar += c.ldar; unit_total.stlr += c.stlr;
    unit_total.fence_ld += c.fence_ld; unit_total.fence_st += c.fence_st; unit_total.fence_resume += c.fence_resume;
    if (verbose && (c.ldar || c.stlr || c.fence_ld || c.fence_st || c.fence_resume))
      inform(DECL_SOURCE_LOCATION(fun->decl), "%<fnvnx_order%>: %lu acquire loads, %lu release stores, %lu+%lu fenced, %lu resume fences",
             c.ldar, c.stlr, c.fence_ld, c.fence_st, c.fence_resume);
    cur = NULL;
    addr_set = NULL;
    generated_bbs.clear();
    return 0;
  }
};

static const pass_data order_data = {
    GIMPLE_PASS, "fnvnx_order", OPTGROUP_NONE, TV_NONE, PROP_gimple_any | PROP_cfg, 0, 0, 0, 0,
};


// ---- the frame record (.fnvnx.frame), rule R1 of class B
//
// For every function the unit emits: the frame ranges of its address-taken variables (TREE_ADDRESSABLE after the
// optimizers, with a stack slot), as `offset:size` pairs relative to the CFA (the stack pointer at function entry, the
// value DW_CFA_def_cfa names), or the word `dynamic` when the frame has no constant layout (alloca, a variable-length
// array, a variable whose slot is not a constant frame offset). The record is emitted from an RTL pass after
// pro_and_epilogue, where the frame is laid out, so the offsets are the final ones. The gate (link/gate_order.py)
// certifies a plain sp- or x29-based access only when it overlaps none of these ranges.
static std::map<std::string, std::string> frame_lines;
static std::map<tree, std::vector<tree> > frame_vars;   // the local variables at the last GIMPLE point (expand frees the list)

struct frame_vars_pass : gimple_opt_pass {
  frame_vars_pass(gcc::context *ctx, const pass_data &d) : gimple_opt_pass(d, ctx) {}
  unsigned int execute(function *fun) final override {
    unsigned ix;
    tree d;
    std::vector<tree> &v = frame_vars[fun->decl];
    FOR_EACH_LOCAL_DECL(fun, ix, d) v.push_back(d);
    return 0;
  }
};
static const pass_data frame_vars_data = {
    GIMPLE_PASS, "fnvnx_frame_vars", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0,
};

struct frame_pass : rtl_opt_pass {
  frame_pass(gcc::context *ctx, const pass_data &d) : rtl_opt_pass(d, ctx) {}

  // the CFA-relative offset of a frame address (frame_pointer + const) after elimination
  static bool slot_offset(rtx addr, long *off) {
    long base = 0;
    if (GET_CODE(addr) == PLUS && CONST_INT_P(XEXP(addr, 1))) { base = INTVAL(XEXP(addr, 1)); addr = XEXP(addr, 0); }
    if (!REG_P(addr) || REGNO(addr) != FRAME_POINTER_REGNUM) return false;
    poly_int64 fp = 0, ap = 0;
    INITIAL_ELIMINATION_OFFSET(FRAME_POINTER_REGNUM, STACK_POINTER_REGNUM, fp);
    INITIAL_ELIMINATION_OFFSET(ARG_POINTER_REGNUM, STACK_POINTER_REGNUM, ap);
    long f, a;
    if (!fp.is_constant(&f) || !ap.is_constant(&a)) return false;
    *off = base + f - a;
    return true;
  }

  unsigned int execute(function *fun) final override {
    std::string line;
    bool dynamic = fun->calls_alloca || fun->calls_setjmp || fun->has_nonlocal_label;
    std::vector<std::pair<long, long> > rs;
    auto one = [&](tree v) {
      if (!VAR_P(v) && TREE_CODE(v) != PARM_DECL) return;
      if (TREE_STATIC(v) || DECL_EXTERNAL(v) || !TREE_ADDRESSABLE(v)) return;
      rtx m = DECL_RTL_IF_SET(v);
      if (!m || !MEM_P(m)) return;
      long off;
      tree sz = DECL_SIZE_UNIT(v);
      if (!sz || !tree_fits_uhwi_p(sz) || !slot_offset(XEXP(m, 0), &off)) { dynamic = true; return; }
      rs.push_back(std::make_pair(off, (long)tree_to_uhwi(sz)));
    };
    for (tree v : frame_vars[fun->decl]) one(v);
    for (tree p = DECL_ARGUMENTS(fun->decl); p; p = DECL_CHAIN(p)) one(p);
    if (dynamic) line = "dynamic";
    else {
      std::sort(rs.begin(), rs.end());
      for (auto &r : rs) line += (line.empty() ? "" : " ") + std::to_string(r.first) + ":" + std::to_string(r.second);
    }
    frame_lines[IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fun->decl))] = line;
    return 0;
  }
};

static const pass_data frame_data = {
    RTL_PASS, "fnvnx_frame", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0,
};

static void emit_redirect_section() {
  if (redir_lines.empty()) return;
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.redir,\"\",%%progbits\n");
  for (auto &l : redir_lines) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", l.c_str());
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
  std::map<std::string, std::string> pending;
  FOR_EACH_FUNCTION(n) {
    if (!n->definition && !n->alias) continue;
    if (n->inlined_to) continue;
    const char *name = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(n->decl));
    if (frame_lines.count(name)) continue;
    // an alias is the body of its target (same bytes, same frame): the same line; a thunk has no frame of its own
    std::string line;
    if (n->alias) {
      cgraph_node *t = n->ultimate_alias_target();
      auto it = t ? frame_lines.find(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(t->decl))) : frame_lines.end();
      if (it != frame_lines.end()) line = it->second;
    }
    pending[name] = line;
  }
  for (auto &p : pending) frame_lines[p.first] = p.second;
  if (frame_lines.empty()) return;
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.frame,\"\",%%progbits\n");
  for (auto &r : frame_lines) fprintf(asm_out_file, "\t.ascii\t\"%s\\t%s\\n\"\n", r.first.c_str(), r.second.c_str());
  fprintf(asm_out_file, "\t.popsection\n");
}

static void finish(void *, void *) {
  emit_frame_section();
  emit_readonly_section();
  emit_redirect_section();
  if (verbose)
    inform(UNKNOWN_LOCATION, "%<fnvnx_order%>: unit: %lu ldar, %lu stlr, %lu fenced loads, %lu fenced stores, %lu resume fences; "
           "%lu calls with arguments right to left, %lu left (class-type argument with side effects)",
           unit_total.ldar, unit_total.stlr, unit_total.fence_ld, unit_total.fence_st, unit_total.fence_resume, calls_reordered, calls_skipped);
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
  struct register_pass_info vpass;
  vpass.pass = new frame_vars_pass(g, frame_vars_data);
  vpass.reference_pass_name = "*warn_function_noreturn";
  vpass.ref_pass_instance_number = 1;
  vpass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &vpass);
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
