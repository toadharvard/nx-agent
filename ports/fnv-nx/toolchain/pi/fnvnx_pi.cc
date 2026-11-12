/* fnvnx_pi - N3: per function, is its code inside the portable subset Π, what is inlined into it, which record
 * types it touches (MASTER_PLAN4 R1 N3 / N4, R7).
 *
 * Two readings of every function:
 *   early   right after the CFG is built, before any optimization and before the other plugins of the flag set
 *           (ordering, float-to-int) rewrite accesses: the function's own body as the source wrote it. The verdict
 *           and the types come from here; locals in registers are read once more as soon as SSA exists (an
 *           optimizer may delete a use of an unassigned local, which is exactly what the verdict is about).
 *   late    just before RTL expansion: what is emitted. The inlined bodies come from here: GCC keeps the scope of
 *           every inlined function in the block tree of the function it was inlined into - but drops the scope of a
 *           body with nothing left in it, so the list is the union of three sources: the block tree, the callgraph
 *           removal hook (every inline clone, einline included, is removed with inlined_to set) and the inline
 *           edges still present at expansion; closed transitively over the unit.
 *
 * One text line per function body, in the object's own section .fnvnx.n3 (not loaded):
 *
 *     symbol <TAB> verdict <TAB> inlined <TAB> kind <TAB> types
 *
 *   symbol   the assembler name of the function; for a GCC clone (`f.constprop.0`, `.isra`, `.part`) the name of
 *            the function it was made from
 *   verdict  `ok`, or the first reason the function's own body is outside Π, in this fixed order:
 *              asm       an asm statement
 *              seh       uses structured exception handling (__try / __except / __finally / __leave); generally:
 *                        the word of a fnvnx_lifted callee (below)
 *              int3      executes an x86 breakpoint instruction (__debugbreak): the body is lifted
 *              setjmp    calls setjmp
 *              varargs   the definition takes variable arguments
 *              falloff   control can reach the end of the function, which must return a value (FALL2: the front
 *                        end's own mark, in a block the CFG reaches). The exe returns what eax holds there
 *              volatile  a volatile access
 *              argorder  a call with effects in two or more arguments, always (the right-to-left lemma does
 *                        not hold for the class; "effect" is defined under "argument order" below); also any call of
 *                        two or more arguments with a side-effecting argument of non-scalar type, which the order
 *                        plugin leaves in GCC order
 *              div       an integer division or remainder by a non-constant (x86 traps where AArch64 gives 0)
 *              shift     a shift or rotate by a non-constant count (x86 masks the count, C leaves it undefined)
 *              uninit    a use of a local that no path has assigned
 *              uninit-mem  a read of a local that lives in memory (address taken, or an aggregate) that is not
 *                        dominated by a store to the whole variable or to exactly the bytes read. Conservative:
 *                        a call given the variable's address proves nothing (a flow analysis with out-parameter
 *                        summaries will narrow this)
 *            The verdict of the emitted code is this one and those of everything in `inlined` (N4's closure).
 *   inlined  `,`-separated assembler names of every function whose body is inlined into this one, transitively
 *   kind     empty for an emitted function; the clone suffix for an emitted clone; `thunk` for a this-adjusting
 *            thunk; `alias` for another symbol of an emitted body (same line otherwise); `inline` for a body this unit saw but did not emit on its own (its verdict is needed by the
 *            functions it is inlined into); `sdd` for a destructor-slot function (written by toolchain/sdd)
 *   types    `,`-separated Itanium type strings of the record types whose fields the body accesses or that its
 *            parameters and locals have
 *
 * fnvnx_lifted("word"): an attribute on a function declaration (registered here). A body that calls a function whose
 * declaration carries it gets the verdict `word`, whatever the callee's name. The declaration has no body anywhere:
 * a native body that calls it cannot link (shim/include/fnvnx_compat.h declares the SEH forms so).
 *
 * Not decided here: facts of the exe (EH tables at the address) and layouts against cl (R7 is checked on `types`).
 * The evaluation order of call arguments is fixed at MSVC's by the fnvnx_order plugin; the sites its lemma is
 * checked on are written to section .fnvnx.argorder.
 */
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "cp/cp-tree.h"
#include "context.h"
#include "stringpool.h"
#include "attribs.h"
#include "tree-pass.h"
#include "function.h"
#include "basic-block.h"
#include "gimple-expr.h"
#include "gimple.h"
#include "gimple-iterator.h"
#include "gimple-walk.h"
#include "ssa.h"
#include "tree-ssanames.h"
#include "cgraph.h"
#include "output.h"
#include "calls.h"
#include "tree-inline.h"
#include "dominance.h"
#include "tree-dfa.h"
#include "tree-cfg.h"
#include "fold-const.h"
#include <map>
#include <set>
#include <string>
#include <vector>

int plugin_is_GPL_compatible;

struct facts { std::string verdict; std::set<std::string> types; bool emitted, mem; };
static std::map<std::string, facts> early;       /* by assembler name of the function */
static std::vector<std::string> lines;
static std::set<std::string> all_thunks;          /* every thunk of the unit, listed before code generation */
static std::set<std::string> thunk_rows;          /* thunks that went through the passes */
static std::map<std::string, std::string> emitted_rest;   /* emitted function -> "verdict\tinlined", for its aliases */

static const char *asmname(tree d)
{
  const char *n = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
  return *n == '*' ? n + 1 : n;
}

/* The ultimate source function of a declaration: through DECL_ABSTRACT_ORIGIN, the function a clone (constprop, isra,
   part, an inline clone) was copied from. Its assembler name is the name the other tables of this file use. */
static tree root_origin(tree d)
{
  while (DECL_ABSTRACT_ORIGIN(d) && DECL_ABSTRACT_ORIGIN(d) != d && TREE_CODE(DECL_ABSTRACT_ORIGIN(d)) == FUNCTION_DECL
         && DECL_ASSEMBLER_NAME_SET_P(DECL_ABSTRACT_ORIGIN(d)))
    d = DECL_ABSTRACT_ORIGIN(d);
  return d;
}

/* GCC's own inlining bookkeeping. Every inlining (early inliner, IPA inliner, always_inline, into a clone) makes
   the callee's node an inline clone with inlined_to = the function it goes into, and removes that node once the
   body is copied in. The removal hook sees every one of them, whatever the block tree later looks like.
   inl_direct: asmname of the receiving function as GCC names it at that point (a clone keeps its own name) ->
   origin names of the bodies inlined into it. */
static std::map<std::string, std::set<std::string> > inl_direct;
/* inl_walk: the same records with the callee under its own name. A callee that is a clone (a constructor's C2, a
   constprop copy) had its own inlinings recorded under that name while the list names its origin, so the chain must
   continue by the own name or the clone's inlinees are lost. */
static std::map<std::string, std::set<std::string> > inl_walk;
/* Soundness of the column: every record must reach a written row, or its receiver must be dead.
   inl_used: receivers whose record inlined_book() read into some function's list (a row's own chain or a callee reached
   from it). inl_dead: functions whose node the callgraph removed as a node of its own (inlined_to == NULL) - the
   removal of a function whose every caller took its body, or of one nobody reaches. */
static std::set<std::string> inl_used, inl_dead;
/* inl_gone: bodies that were inlined into a root function the callgraph had already removed (see inline_clone_removed):
   their copy is in no emitted code. */
static std::set<std::string> inl_gone;

static void note_inlined(tree into, tree callee)
{
  if (!DECL_ASSEMBLER_NAME_SET_P(into) || !DECL_ASSEMBLER_NAME_SET_P(callee)) return;
  inl_direct[asmname(into)].insert(asmname(root_origin(callee)));
  inl_walk[asmname(into)].insert(asmname(callee));
}

/* What the hook may read (GCC 13 cgraph_node::remove, cgraph.cc: it calls the removal hooks first, then
   memset()s the node to zero): the node being removed is intact (decl, inlined_to). Its inlined_to node, however,
   may have been removed already - remove_unreachable_nodes (ipa.cc) deletes unreachable nodes in list order, an
   inline clone after the function it was inlined into - and a removed node is all zero, decl == NULL. That
   receiver was removed as a node of its own (its own hook call put it in inl_dead), so the copy of n inside it is in no
   emitted code: n goes to inl_gone, the receiver is never dereferenced. */
static void inline_clone_removed(cgraph_node *n, void *)
{
  if (!n->decl) return;
  if (n->inlined_to) {
    if (n->inlined_to->decl) note_inlined(n->inlined_to->decl, n->decl);
    else if (DECL_ASSEMBLER_NAME_SET_P(n->decl)) inl_gone.insert(asmname(n->decl));
  }
  else if (DECL_ASSEMBLER_NAME_SET_P(n->decl)) inl_dead.insert(asmname(n->decl));
}

/* inline clones still attached to a function that is about to be expanded: its callee edges with inline_failed ==
   CIF_OK, recursively */
static void inlined_edges(cgraph_node *n, tree into, std::set<cgraph_node *> &seen)
{
  if (!seen.insert(n).second) return;
  for (cgraph_edge *e = n->callees; e; e = e->next_callee)
    if (e->inline_failed == CIF_OK) {
      note_inlined(into, e->callee->decl);
      inlined_edges(e->callee, into, seen);
    }
}

/* `out` (block tree, edges) plus everything the hook saw inlined into the function `d`, into the copies it descends
   from, or into any body in the set: closed transitively over the unit */
static void inlined_book(tree d, std::set<std::string> &out)
{
  std::vector<std::string> work;
  for (tree o = d;; o = DECL_ABSTRACT_ORIGIN(o)) {
    if (DECL_ASSEMBLER_NAME_SET_P(o)) work.push_back(asmname(o));
    if (!DECL_ABSTRACT_ORIGIN(o) || DECL_ABSTRACT_ORIGIN(o) == o || TREE_CODE(DECL_ABSTRACT_ORIGIN(o)) != FUNCTION_DECL) break;
  }
  work.insert(work.end(), out.begin(), out.end());   /* the block tree's and the edges' callees: what went into them */
  std::set<std::string> seen;
  while (!work.empty()) {
    std::string f = work.back(); work.pop_back();
    if (!seen.insert(f).second) continue;
    std::map<std::string, std::set<std::string> >::iterator m = inl_direct.find(f);
    if (m == inl_direct.end()) continue;
    inl_used.insert(f);
    for (std::set<std::string>::iterator c = m->second.begin(); c != m->second.end(); ++c) {
      out.insert(*c);
      work.push_back(*c);
    }
    std::map<std::string, std::set<std::string> >::iterator w = inl_walk.find(f);
    if (w != inl_walk.end())
      for (std::set<std::string>::iterator c = w->second.begin(); c != w->second.end(); ++c) work.push_back(*c);
  }
}

static void inlined_blocks(tree block, std::set<std::string> &out)
{
  for (; block; block = BLOCK_CHAIN(block)) {
    if (inlined_function_outer_scope_p(block)) {
      tree fn = block_ultimate_origin(block);
      if (fn && TREE_CODE(fn) == FUNCTION_DECL) out.insert(asmname(fn));
    }
    inlined_blocks(BLOCK_SUBBLOCKS(block), out);
  }
}

static void note_type(tree t, std::set<std::string> &types)
{
  while (t && (POINTER_TYPE_P(t) || TREE_CODE(t) == ARRAY_TYPE)) t = TREE_TYPE(t);
  if (!t || !RECORD_OR_UNION_TYPE_P(t)) return;
  t = TYPE_MAIN_VARIANT(t);
  if (!TYPE_NAME(t) || !CLASS_TYPE_P(t)) { types.insert("<unnamed>"); return; }
  types.insert(mangle_type_string(t));
}

static tree walk_types(tree *tp, int *, void *data)
{
  std::set<std::string> *types = (std::set<std::string> *)((walk_stmt_info *)data)->info;
  if (TREE_CODE(*tp) == COMPONENT_REF) note_type(TREE_TYPE(TREE_OPERAND(*tp, 0)), *types);
  else if (TREE_CODE(*tp) == MEM_REF) note_type(TREE_TYPE(*tp), *types);
  return NULL_TREE;
}

/* a volatile access, read off the operands (before SSA the statement's own flag is not computed yet) */
static tree walk_volatile(tree *tp, int *walk_subtrees, void *)
{
  if (TYPE_P(*tp)) { *walk_subtrees = 0; return NULL_TREE; }
  if (TREE_CODE(*tp) == ADDR_EXPR) { *walk_subtrees = 0; return NULL_TREE; }      /* taking the address is no access */
  if ((REFERENCE_CLASS_P(*tp) || DECL_P(*tp)) && TREE_THIS_VOLATILE(*tp)) return *tp;
  return NULL_TREE;
}

static bool uninit_use(tree op)
{
  /* a variable of the source: a temporary the compiler or a plugin made has no source-level value to miss, and a
     copy of an unassigned source variable into one shows the source variable's own use. An object of an empty
     class has no bits to be unassigned. */
  if (TREE_CODE(op) != SSA_NAME || !SSA_NAME_IS_DEFAULT_DEF(op)) return false;
  tree v = SSA_NAME_VAR(op);
  return v && VAR_P(v) && DECL_NAME(v) && !DECL_ARTIFICIAL(v) && !DECL_HARD_REGISTER(v) && !is_empty_type(TREE_TYPE(op));
}

/* ---- argument order (the lemma of rule ORDER-ARGS, toolchain/order): every call whose arguments (the object of a
   member call included) have an effect (EFFECT, below) in two or more of them is written down before genericization, as the
   source has it:
       function <TAB> callee <TAB> a0|a1|... <TAB> k/n
   each a_k: the `,`-separated callees of the calls inside argument k; k/n: this call is number k (from 0) of the
   function's n calls of that callee, in the order of the source. In section .fnvnx.argorder. The gate reads the same calls out of the MSVC object of the function and checks that
   their order there is the one the order plugin makes GCC use. Every such site, readable or not, puts the
   function outside Π (`argorder`): a static rule, no site is skipped; the rows are only a measurement for the gate. */
static std::vector<std::string> arg_sites;
/* per function, while it is walked: how many calls of each callee were met so far (source order), and the sites
   found, each with the number its call has among the calls of its callee */
static std::map<std::string, int> call_count;
struct site_row { std::string head, callee; int ordinal; };
static std::vector<site_row> fn_sites;
static std::set<std::string> arg_unreadable;      /* functions (the declaration's symbol) with such a site */

/* the function a call or an aggregate-initializing call names, else null */
static tree callee_of(tree t)
{
  if (TREE_CODE(t) == CALL_EXPR) return cp_get_callee_fndecl_nofold(t);
  tree f = AGGR_INIT_EXPR_FN(t);
  if (f && TREE_CODE(f) == ADDR_EXPR) f = TREE_OPERAND(f, 0);
  return f && TREE_CODE(f) == FUNCTION_DECL ? f : NULL_TREE;
}


/* ---- EFFECT: the rule that says which arguments make the order of evaluation matter. An expression has an effect iff
   it contains
     - a store (an assignment, an initialization, an increment or decrement) to anything but a private object: a
       compiler temporary made inside the expression (the slot of a TARGET_EXPR), or, in the body of a function being
       summarized, a non-static local, parameter or result object of that function reached without a dereference;
       no other code can see such an object;
     - a volatile access;
     - a throwing or non-returning construct: a throw, an allocation or deallocation expression, a call of a function
       that never returns or may not return (GCC's noreturn, looping const/pure), asm;
     - a call of a function that is not known side-effect-free by the compiler's own facts at the point the plugin
       decides: the callee's own ECF_CONST / ECF_PURE (the declaration or its type, `__attribute__((const/pure))`
       included; for a built-in, the flags GCC gives the built-in's declaration), or an always_inline function whose
       body was already judged free by this same rule (its summary, memoized by DECL_UID, is made when its definition
       is finished, so a callee defined later, an indirect or virtual callee, or a recursive one has no summary and is an
       effect). Names are never looked at.
   Assumption (ASM-001, ASM-005): the contract GCC itself relies on for const and pure (no store, no throw, termination); the arguments of
   a free call are walked by the same rule.
   What the rule decides: two arguments without an effect commute with each other (each only reads); the verdict
   `argorder` is for a call with two or more arguments that have one, whose relative order the exe's code fixes and no
   rule here proves. An argument with an effect against one that only reads is the case of the order plugin's
   right-to-left rule, as before.
   A body summary also treats a loop, a goto or label, a try block and a statement expression as effects: termination
   and control flow are not judged. */
static std::map<unsigned, bool> body_free;        /* DECL_UID of an always_inline function -> its body has no effect */

struct eff_ctx { tree self; std::vector<tree> priv; bool effect; };

/* the object a store writes, through components and indices but no dereference; null when it goes through memory */
static tree store_base(tree t)
{
  for (;;) {
    switch (TREE_CODE(t)) {
      case COMPONENT_REF: case ARRAY_REF: case BIT_FIELD_REF: case REALPART_EXPR: case IMAGPART_EXPR: case VIEW_CONVERT_EXPR:
      case NOP_EXPR: case NON_LVALUE_EXPR:
        t = TREE_OPERAND(t, 0); continue;
      case VAR_DECL: case PARM_DECL: case RESULT_DECL:
        return t;
      default:
        return NULL_TREE;
    }
  }
}

static bool private_store(tree lhs, eff_ctx *c)
{
  tree b = store_base(lhs);
  if (!b || TREE_THIS_VOLATILE(b) || TREE_STATIC(b) || DECL_EXTERNAL(b) || TREE_CODE(TREE_TYPE(b)) == REFERENCE_TYPE) return false;
  for (size_t i = 0; i < c->priv.size(); i++) if (c->priv[i] == b) return true;
  if (!c->self) return false;
  if (TREE_CODE(b) == RESULT_DECL) return b == DECL_RESULT(c->self);      /* no context is set on it yet */
  if (TREE_CODE(b) == PARM_DECL) {
    for (tree a = DECL_ARGUMENTS(c->self); a; a = DECL_CHAIN(a)) if (a == b) return true;
    return false;
  }
  return DECL_CONTEXT(b) == c->self;
}

static bool call_free(tree t)
{
  tree fn = callee_of(t);
  int flags;
  if (fn) flags = flags_from_decl_or_type(fn);
  else {
    tree f = TREE_CODE(t) == CALL_EXPR ? CALL_EXPR_FN(t) : AGGR_INIT_EXPR_FN(t);
    tree ft = f && POINTER_TYPE_P(TREE_TYPE(f)) ? TREE_TYPE(TREE_TYPE(f)) : NULL_TREE;
    flags = ft ? flags_from_decl_or_type(ft) : 0;
  }
  if (flags & (ECF_NORETURN | ECF_LOOPING_CONST_OR_PURE)) return false;
  if (flags & (ECF_CONST | ECF_PURE)) return true;
  if (fn && lookup_attribute("always_inline", DECL_ATTRIBUTES(fn))) {
    std::map<unsigned, bool>::iterator m = body_free.find(DECL_UID(fn));
    return m != body_free.end() && m->second;
  }
  return false;
}

static tree walk_effect(tree *tp, int *walk_subtrees, void *data)
{
  eff_ctx *c = (eff_ctx *)data;
  tree t = *tp;
  if (TYPE_P(t)) { *walk_subtrees = 0; return NULL_TREE; }
  switch (TREE_CODE(t)) {
    case CALL_EXPR: case AGGR_INIT_EXPR:
      if (!call_free(t)) { c->effect = true; return t; }
      return NULL_TREE;
    case TARGET_EXPR:
      c->priv.push_back(TARGET_EXPR_SLOT(t));
      return NULL_TREE;
    case MODIFY_EXPR: case INIT_EXPR:
      if (!private_store(TREE_OPERAND(t, 0), c)) { c->effect = true; return t; }
      return NULL_TREE;
    case PREINCREMENT_EXPR: case PREDECREMENT_EXPR: case POSTINCREMENT_EXPR: case POSTDECREMENT_EXPR:
      if (!private_store(TREE_OPERAND(t, 0), c)) { c->effect = true; return t; }
      return NULL_TREE;
    case DECL_EXPR: {
      tree d = DECL_EXPR_DECL(t);
      if (c->self && VAR_P(d) && !TREE_STATIC(d) && !DECL_EXTERNAL(d) && DECL_CONTEXT(d) == c->self) {
        if (tree i = DECL_INITIAL(d)) cp_walk_tree_without_duplicates(&i, walk_effect, c);
        return c->effect ? t : NULL_TREE;
      }
      c->effect = true; return t;
    }
    case THROW_EXPR: case NEW_EXPR: case VEC_NEW_EXPR: case DELETE_EXPR: case VEC_DELETE_EXPR: case VA_ARG_EXPR:
    case STMT_EXPR: case ASM_EXPR: case TRY_BLOCK: case CLEANUP_STMT: case GOTO_EXPR: case LABEL_EXPR: case FOR_STMT:
    case WHILE_STMT: case DO_STMT: case RANGE_FOR_STMT: case LOOP_EXPR: case EXIT_EXPR: case EH_SPEC_BLOCK:
      c->effect = true; return t;
    default:
      if (TREE_THIS_VOLATILE(t)) { c->effect = true; return t; }
      if (TREE_CODE(t) == ADDR_EXPR && DECL_P(TREE_OPERAND(t, 0))) *walk_subtrees = 0;   /* an address is no access */
      return NULL_TREE;
  }
}

static bool arg_has_effect(tree arg)
{
  eff_ctx c; c.self = NULL_TREE; c.effect = false;
  cp_walk_tree_without_duplicates(&arg, walk_effect, &c);
  return c.effect;
}

/* the summary of an always_inline function, made when its definition is finished (before any caller that follows it) */
static void summarize(tree fn)
{
  if (!lookup_attribute("always_inline", DECL_ATTRIBUTES(fn))) return;
  eff_ctx c; c.self = fn; c.effect = false;
  tree body = DECL_SAVED_TREE(fn);
  cp_walk_tree_without_duplicates(&body, walk_effect, &c);
  body_free[DECL_UID(fn)] = !c.effect;
}

struct arg_effects { std::vector<std::string> calls; bool other; };

static tree walk_effects(tree *tp, int *walk_subtrees, void *data)
{
  arg_effects *e = (arg_effects *)data;
  tree t = *tp;
  if (TYPE_P(t)) { *walk_subtrees = 0; return NULL_TREE; }
  switch (TREE_CODE(t)) {
    case CALL_EXPR: case AGGR_INIT_EXPR: {
      tree fn = callee_of(t);
      if (fn && DECL_NAME(fn) && !fndecl_built_in_p(fn)) e->calls.push_back(asmname(fn));
      else e->other = true;
      break;
    }
    case MODIFY_EXPR: case INIT_EXPR: case PREINCREMENT_EXPR: case PREDECREMENT_EXPR: case POSTINCREMENT_EXPR:
    case POSTDECREMENT_EXPR: case THROW_EXPR: case NEW_EXPR: case VEC_NEW_EXPR: case DELETE_EXPR: case VEC_DELETE_EXPR:
    case TARGET_EXPR: case VA_ARG_EXPR: case STMT_EXPR:
      /* a TARGET_EXPR is the temporary a call's result is built in: its initializer is walked, it is no effect */
      if (TREE_CODE(t) != TARGET_EXPR) e->other = true;
      break;
    default:
      if (TREE_THIS_VOLATILE(t) && !DECL_P(t)) e->other = true;
  }
  return NULL_TREE;
}

static tree walk_calls(tree *tp, int *walk_subtrees, void *data)
{
  tree t = *tp;
  if (TYPE_P(t)) { *walk_subtrees = 0; return NULL_TREE; }
  if (TREE_CODE(t) != CALL_EXPR && TREE_CODE(t) != AGGR_INIT_EXPR) return NULL_TREE;
  std::vector<tree> args;
  if (TREE_CODE(t) == CALL_EXPR) for (int i = 0; i < call_expr_nargs(t); i++) args.push_back(CALL_EXPR_ARG(t, i));
  else for (int i = 0; i < aggr_init_expr_nargs(t); i++) args.push_back(AGGR_INIT_EXPR_ARG(t, i));
  tree fn = callee_of(t);
  std::string callee = fn && DECL_NAME(fn) ? asmname(fn) : "*";
  int ordinal = call_count[callee]++;          /* every call counts, a site or not */
  int with = 0;
  std::vector<bool> effect(args.size());
  for (size_t i = 0; i < args.size(); i++) if ((effect[i] = args[i] && arg_has_effect(args[i]))) with++;
  /* A call the order plugin leaves in GCC's order: at least one argument (after `this` of a method call) has side
     effects and is not of scalar (register) type, and the call has two or more arguments. This condition must stay
     equal to the one in toolchain/order/fnvnx_order.cc reorder_call (skip on `!scalar_arg(a)` with TREE_SIDE_EFFECTS
     and the CALL_EXPR_REVERSE_ARGS exemption); scalar_arg there is: integral, pointer, scalar float, offset, vector,
     nullptr type. Such a call has no readable order: argorder, whatever the other arguments are. */
  if (args.size() >= 2 && !(TREE_CODE(t) == CALL_EXPR && CALL_EXPR_REVERSE_ARGS(t))) {
    tree fnptr = TREE_CODE(t) == CALL_EXPR ? CALL_EXPR_FN(t) : AGGR_INIT_EXPR_FN(t);
    tree ft = fnptr && POINTER_TYPE_P(TREE_TYPE(fnptr)) ? TREE_TYPE(TREE_TYPE(fnptr)) : NULL_TREE;
    for (size_t i = ft && TREE_CODE(ft) == METHOD_TYPE ? 1 : 0; i < args.size(); i++) {
      tree a = args[i], ty = a ? TREE_TYPE(a) : NULL_TREE;
      bool scalar = ty && (INTEGRAL_TYPE_P(ty) || POINTER_TYPE_P(ty) || SCALAR_FLOAT_TYPE_P(ty)
                           || TREE_CODE(ty) == OFFSET_TYPE || TREE_CODE(ty) == VECTOR_TYPE || TREE_CODE(ty) == NULLPTR_TYPE);
      if (a && TREE_SIDE_EFFECTS(a) && !scalar) { arg_unreadable.insert((const char *)data); break; }
    }
  }
  if (with < 2) return NULL_TREE;
  /* ORDER-ARGS: the lemma "cl evaluates the arguments right to left" does not hold for the whole class (the gate found
     contradictions against cl's code), so no function may rely on it: two or more arguments with an effect, readable
     or not, put the function outside Π. The row below is still written, as a measurement for the gate. */
  arg_unreadable.insert((const char *)data);
  std::string row = std::string((const char *)data) + "\\t" + callee + "\\t";
  bool readable = true;
  std::set<std::string> seen;
  seen.insert(callee);                         /* the callee itself inside an argument: its calls cannot be told apart */
  for (size_t i = 0; i < args.size(); i++) {
    arg_effects e; e.other = false;
    if (effect[i]) {
      cp_walk_tree_without_duplicates(&args[i], walk_effects, &e);
      if (e.other || e.calls.empty()) readable = false;
      for (size_t k = 0; k < e.calls.size(); k++) if (!seen.insert(e.calls[k]).second) readable = false;
    }
    if (i) row += "|";
    for (size_t k = 0; k < e.calls.size(); k++) row += (k ? "," : "") + e.calls[k];
  }
  if (readable) fn_sites.push_back({row, callee, ordinal});
  return NULL_TREE;
}

static void pre_genericize(void *gcc_data, void *)
{
  tree fn = (tree)gcc_data;
  if (!fn || TREE_CODE(fn) != FUNCTION_DECL || !DECL_SAVED_TREE(fn)) return;
  summarize(fn);
  std::string name = asmname(fn);
  call_count.clear(); fn_sites.clear();
  cp_walk_tree_without_duplicates(&DECL_SAVED_TREE(fn), walk_calls, (void *)name.c_str());
  /* the in-charge form of a constructor or destructor is never emitted: its clones (C1/C2, D0/D1/D2) are, and each
     clone with a body of its own comes here itself. The form is still walked: the verdict of its clones is looked
     up by its name (source_name). */
  if (DECL_LANG_SPECIFIC(fn) && DECL_MAYBE_IN_CHARGE_CDTOR_P(fn)) return;
  for (size_t i = 0; i < fn_sites.size(); i++)
    arg_sites.push_back(fn_sites[i].head + "\\t" + std::to_string(fn_sites[i].ordinal) + "/" + std::to_string(call_count[fn_sites[i].callee]));
}

/* a local of the source that lives in memory (its address is taken, or it is an aggregate) */
static tree memory_local(tree ref)
{
  tree b = get_base_address(ref);
  return b && VAR_P(b) && !is_global_var(b) && !is_gimple_reg(b) && DECL_NAME(b) && !DECL_ARTIFICIAL(b)
         && !is_empty_type(TREE_TYPE(b)) ? b : NULL_TREE;
}

struct mem_access { gimple *st; tree ref; };
struct mem_walk { std::vector<mem_access> *reads; gimple *st; };

static tree walk_reads(tree *tp, int *walk_subtrees, void *data)
{
  walk_stmt_info *wi = (walk_stmt_info *)data;
  mem_walk *w = (mem_walk *)wi->info;
  if (TREE_CODE(*tp) == ADDR_EXPR || TYPE_P(*tp)) { *walk_subtrees = 0; return NULL_TREE; }   /* &x reads nothing */
  if (wi->is_lhs) return NULL_TREE;
  if ((REFERENCE_CLASS_P(*tp) || DECL_P(*tp)) && memory_local(*tp)) {
    mem_access a = { w->st, *tp };
    w->reads->push_back(a);
    *walk_subtrees = 0;
  }
  return NULL_TREE;
}

/* R-MEMLOCAL (conservative): a read of a memory local must be dominated by a store to the whole variable or to
   exactly the bytes read. A call that is handed the variable's address proves nothing here. */
static bool unproven_memory_read(function *fun)
{
  std::vector<mem_access> reads, writes;
  basic_block bb;
  renumber_gimple_stmt_uids(fun);
  FOR_EACH_BB_FN(bb, fun)
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
      gimple *st = gsi_stmt(gsi);
      if (is_gimple_debug(st) || gimple_clobber_p(st)) continue;
      tree lhs = gimple_get_lhs(st);
      if (lhs && TREE_CODE(lhs) != SSA_NAME && memory_local(lhs)) { mem_access a = { st, lhs }; writes.push_back(a); }
      mem_walk w = { &reads, st };
      walk_stmt_info wi; memset(&wi, 0, sizeof wi); wi.info = &w;
      walk_gimple_op(st, walk_reads, &wi);
    }
  if (reads.empty()) return false;
  calculate_dominance_info(CDI_DOMINATORS);
  for (size_t r = 0; r < reads.size(); r++) {
    tree var = memory_local(reads[r].ref);
    bool proven = false;
    for (size_t w = 0; w < writes.size() && !proven; w++) {
      if (memory_local(writes[w].ref) != var) continue;
      if (writes[w].ref != var && !operand_equal_p(writes[w].ref, reads[r].ref, 0)) continue;
      basic_block wb = gimple_bb(writes[w].st), rb = gimple_bb(reads[r].st);
      proven = wb == rb ? gimple_uid(writes[w].st) < gimple_uid(reads[r].st) : dominated_by_p(CDI_DOMINATORS, rb, wb);
    }
    if (!proven) return true;
  }
  return false;
}

/* the symbol of the declaration a body was parsed under (a constructor / destructor variant: its declaration) */
static std::string source_name(tree fn)
{
  if (DECL_LANG_SPECIFIC(fn) && DECL_CLONED_FUNCTION_P(fn)) fn = DECL_CLONED_FUNCTION(fn);
  return asmname(fn);
}

static std::string join(const std::set<std::string> &v)
{
  std::string r;
  for (std::set<std::string>::const_iterator i = v.begin(); i != v.end(); ++i) r += (i == v.begin() ? "" : ",") + *i;
  return r;
}

namespace {
/* the function's own body, twice: right after the CFG is built, before any other plugin of the flag set has touched
   it (they are loaded before this one, so their passes at the same place run after this one), and once more when
   SSA exists, only for locals in registers */
const pass_data cfg_data = { GIMPLE_PASS, "fnvnx_pi_cfg", OPTGROUP_NONE, TV_NONE, PROP_cfg, 0, 0, 0, 0 };
struct cfg_pass : gimple_opt_pass {
  cfg_pass(gcc::context *c) : gimple_opt_pass(cfg_data, c) {}
  unsigned int execute(function *fun) override
  {
    const char *r_lift = NULL;
    bool r_asm = false, r_vol = false, r_div = false, r_shift = false, r_fall = false;
    facts f; f.emitted = false;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (is_gimple_debug(st) || gimple_clobber_p(st)) continue;      /* an end-of-life mark is no access */
        if (gimple_code(st) == GIMPLE_ASM) r_asm = true;
        if (is_gimple_call(st) && !r_lift)
          if (tree cd = gimple_call_fndecl(st))
            if (tree la = lookup_attribute("fnvnx_lifted", DECL_ATTRIBUTES(cd)))
              r_lift = TREE_STRING_POINTER(TREE_VALUE(TREE_VALUE(la)));
        /* FALL2: the front end's own mark of "control reaches the end of a non-void function" (it has no source
           position), in a block the function's CFG reaches. The exe returns whatever eax holds there; C++ gives
           no value and GCC lets the code run on, so the body is not portable. (Blocks no path reaches are already
           gone from the CFG: a function whose every path returns has no mark left.) */
        if ((gimple_call_builtin_p(st, BUILT_IN_UNREACHABLE) || gimple_call_builtin_p(st, BUILT_IN_UNREACHABLE_TRAP))
            && LOCATION_LOCUS(gimple_location(st)) <= BUILTINS_LOCATION && !VOID_TYPE_P(TREE_TYPE(TREE_TYPE(fun->decl))))
          r_fall = true;
        if (gimple_has_volatile_ops(st) || walk_gimple_op(st, walk_volatile, NULL)) r_vol = true;
        if (is_gimple_assign(st)) {
          tree_code c = gimple_assign_rhs_code(st);
          bool cst2 = gimple_num_ops(st) > 2 && TREE_CODE(gimple_assign_rhs2(st)) == INTEGER_CST;
          if ((c == TRUNC_DIV_EXPR || c == TRUNC_MOD_EXPR || c == EXACT_DIV_EXPR || c == FLOOR_DIV_EXPR
               || c == CEIL_DIV_EXPR || c == ROUND_DIV_EXPR || c == FLOOR_MOD_EXPR || c == CEIL_MOD_EXPR
               || c == ROUND_MOD_EXPR) && INTEGRAL_TYPE_P(TREE_TYPE(gimple_assign_lhs(st))) && !cst2) r_div = true;
          if ((c == LSHIFT_EXPR || c == RSHIFT_EXPR || c == LROTATE_EXPR || c == RROTATE_EXPR) && !cst2) r_shift = true;
        }
        walk_stmt_info wi; memset(&wi, 0, sizeof wi); wi.info = &f.types;
        walk_gimple_op(st, walk_types, &wi);
      }
    tree fn = fun->decl;
    for (tree p = DECL_ARGUMENTS(fn); p; p = DECL_CHAIN(p)) note_type(TREE_TYPE(p), f.types);
    unsigned ix; tree v;
    FOR_EACH_LOCAL_DECL(fun, ix, v) note_type(TREE_TYPE(v), f.types);
    /* `uninit` (a local in a register) is decided on SSA and comes before uninit-mem in the order of reasons */
    f.verdict = r_asm ? "asm" : r_lift ? r_lift : fun->calls_setjmp ? "setjmp" : stdarg_p(TREE_TYPE(fn)) ? "varargs"
              : r_fall ? "falloff" : r_vol ? "volatile" : arg_unreadable.count(source_name(fn)) ? "argorder"
              : r_div ? "div" : r_shift ? "shift" : "";
    f.mem = unproven_memory_read(fun);
    free_dominance_info(CDI_DOMINATORS);
    early[asmname(fn)] = f;
    return 0;
  }
};

const pass_data early_data = { GIMPLE_PASS, "fnvnx_pi_early", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
struct early_pass : gimple_opt_pass {
  early_pass(gcc::context *c) : gimple_opt_pass(early_data, c) {}
  unsigned int execute(function *fun) override
  {
    std::map<std::string, facts>::iterator e = early.find(asmname(fun->decl));
    if (e == early.end()) { error("N3: function %qD reached SSA but was never read after the CFG", fun->decl); return 0; }
    if (!e->second.verdict.empty()) return 0;
    bool r_uninit = false;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun) {
      for (gphi_iterator pi = gsi_start_phis(bb); !gsi_end_p(pi); gsi_next(&pi))
        for (unsigned i = 0; i < gimple_phi_num_args(pi.phi()); i++)
          if (uninit_use(gimple_phi_arg_def(pi.phi(), i))) r_uninit = true;
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        ssa_op_iter it; tree use;
        FOR_EACH_SSA_TREE_OPERAND(use, gsi_stmt(gsi), it, SSA_OP_USE)
          if (uninit_use(use)) r_uninit = true;
      }
    }
    e->second.verdict = r_uninit ? "uninit" : e->second.mem ? "uninit-mem" : "ok";
    return 0;
  }
};

const pass_data late_data = { GIMPLE_PASS, "fnvnx_pi_late", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
struct late_pass : gimple_opt_pass {
  late_pass(gcc::context *c) : gimple_opt_pass(late_data, c) {}
  unsigned int execute(function *fun) override
  {
    std::set<std::string> inl;
    inlined_blocks(DECL_INITIAL(fun->decl), inl);
    if (cgraph_node *cn = cgraph_node::get(fun->decl)) {
      std::set<cgraph_node *> seen;
      inlined_edges(cn, fun->decl, seen);
    }
    inlined_book(fun->decl, inl);
    /* the function itself, or - for a copy the optimizer made of one (constprop, isra, part, ...) - the function
       it was copied from: by the declaration's origin, never by the spelling of the symbol (a static
       initializer's own symbol has the file name in it, dots and all) */
    std::string name = asmname(fun->decl), clone;
    std::map<std::string, facts>::iterator e = early.find(name);
    for (tree o = fun->decl; e == early.end() && DECL_ABSTRACT_ORIGIN(o) && DECL_ABSTRACT_ORIGIN(o) != o
         && TREE_CODE(DECL_ABSTRACT_ORIGIN(o)) == FUNCTION_DECL;) {
      o = DECL_ABSTRACT_ORIGIN(o);
      if (!DECL_ASSEMBLER_NAME_SET_P(o)) continue;
      std::string on = asmname(o);
      if ((e = early.find(on)) != early.end()) {
        clone = name.compare(0, on.size() + 1, on + ".") == 0 ? name.substr(on.size() + 1) : name;
        name = on;
      }
    }
    if (e == early.end()) {
      /* a this-adjusting thunk: made by the compiler, an adjustment and a jump */
      if (!DECL_LANG_SPECIFIC(fun->decl) || !DECL_THUNK_P(fun->decl))
        { error("N3: function %qD is emitted but was never read early", fun->decl); return 0; }
      lines.push_back(name + "\\tok\\t" + join(inl) + "\\tthunk\\t");
      thunk_rows.insert(name);
      return 0;
    }
    e->second.emitted = true;
    inl.erase(name);
    lines.push_back(name + "\\t" + e->second.verdict + "\\t" + join(inl) + "\\t" + clone + "\\t" + join(e->second.types));
    if (clone.empty()) emitted_rest[name] = e->second.verdict + "\\t" + join(inl) + "\\talias\\t" + join(e->second.types);
    return 0;
  }
};
}

static void start_unit(void *, void *)
{
  symtab->add_cgraph_removal_hook(inline_clone_removed, NULL);
}

static void ipa_start(void *, void *)
{
  cgraph_node *n;
  FOR_EACH_FUNCTION(n)
    if (n->thunk && n->definition) all_thunks.insert(asmname(n->decl));
}

/* A record (receiver R, callee C) that no written row read means C's body may sit in emitted code under a name this
   file does not list. Decided by callgraph facts. Every node that holds a body ends one of three ways: expanded
   (its row is written and inlined_book() read its record: inl_used), removed as a node of its own (inl_dead), or
   inlined into a root function X (the hook recorded (X, R), so X's chain reaches R: inl_used when X is written).
   A record left over therefore belongs to a body whose every copy sits inside functions that were removed as nodes of
   their own: a dead X, or a body inlined into a dead X, at any depth (the in-place inline of a function into its
   last caller keeps the receiver's name for the inlinee records while the function itself is gone with its caller).
   `inside` is that closure: from each dead X or inl_gone body, its callees by their own names (inl_walk) and by origin names
   (inl_direct), transitively. A record whose receiver is used, dead or inside dead code is consumed or in no emitted
   code. Anything else is an error.
   Third seed of `inside`: a node still in the symbol table whose body is not output in this unit. At FINISH_UNIT
   every body the unit writes is expanded and GCC has set TREE_ASM_WRITTEN on its decl (final.cc, rest_of_handle_final);
   a function node without it (and, for an alias, whose ultimate target has none) writes no code: an extern
   (DECL_EXTERNAL) body that was only inlined from, or a comdat body every user inlined and remove_unreachable_nodes
   released but kept as an abstract origin (definition == false). Such a body is in no emitted code, so the copies of its
   inlinees in it are neither. Inline clones still attached to a root (inlined_to) are no seeds: their code is the root's. */
static void check_records()
{
  std::set<std::string> inside(inl_dead.begin(), inl_dead.end());
  inside.insert(inl_gone.begin(), inl_gone.end());
  cgraph_node *fn;
  FOR_EACH_FUNCTION(fn)
    if (!fn->inlined_to && DECL_ASSEMBLER_NAME_SET_P(fn->decl) && !TREE_ASM_WRITTEN((fn->alias ? fn->ultimate_alias_target() : fn)->decl))
      inside.insert(asmname(fn->decl));
  std::vector<std::string> work(inside.begin(), inside.end());
  while (!work.empty()) {
    std::string f = work.back(); work.pop_back();
    for (int k = 0; k < 2; k++) {
      std::map<std::string, std::set<std::string> > &mp = k ? inl_walk : inl_direct;
      std::map<std::string, std::set<std::string> >::iterator m = mp.find(f);
      if (m == mp.end()) continue;
      for (std::set<std::string>::iterator c = m->second.begin(); c != m->second.end(); ++c)
        if (inside.insert(*c).second) work.push_back(*c);
    }
  }
  for (std::map<std::string, std::set<std::string> >::iterator m = inl_direct.begin(); m != inl_direct.end(); ++m) {
    if (inl_used.count(m->first) || inside.count(m->first)) continue;
    for (std::set<std::string>::iterator c = m->second.begin(); c != m->second.end(); ++c)
      error("N3: %qs inlined into %qs, which reached no row and was not removed from the callgraph", c->c_str(), m->first.c_str());
  }
}

static void finish_unit(void *, void *)
{
  check_records();
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.n3,\"\",%%progbits\n");
  for (size_t i = 0; i < lines.size(); i++) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", lines[i].c_str());
  /* another symbol of the same body (the complete-object constructor / destructor of a class without virtual
     bases is its base-object one): the body's line again, under the alias */
  cgraph_node *n;
  FOR_EACH_FUNCTION(n)
    if (n->alias && n->get_alias_target()) {
      std::map<std::string, std::string>::iterator r = emitted_rest.find(asmname(n->ultimate_alias_target()->decl));
      if (r != emitted_rest.end())
        fprintf(asm_out_file, "\t.ascii\t\"%s\\t%s\\n\"\n", asmname(n->decl), r->second.c_str());
    }
  /* a thunk the back end wrote directly (an adjustment and a jump, no GIMPLE body) */
  for (std::set<std::string>::iterator t = all_thunks.begin(); t != all_thunks.end(); ++t)
    if (!thunk_rows.count(*t))
      fprintf(asm_out_file, "\t.ascii\t\"%s\\tok\\t\\tthunk\\t\\n\"\n", t->c_str());
  for (std::map<std::string, facts>::iterator e = early.begin(); e != early.end(); ++e)
    if (!e->second.emitted)
      fprintf(asm_out_file, "\t.ascii\t\"%s\\t%s\\t\\tinline\\t%s\\n\"\n", e->first.c_str(), e->second.verdict.c_str(),
              join(e->second.types).c_str());
  fprintf(asm_out_file, "\t.popsection\n");
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.argorder,\"\",%%progbits\n");
  for (size_t i = 0; i < arg_sites.size(); i++) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", arg_sites[i].c_str());
  fprintf(asm_out_file, "\t.popsection\n");
}

static struct attribute_spec lifted_attr = { "fnvnx_lifted", 1, 1, true, false, false, false, NULL, NULL };
static void register_attrs(void *, void *) { register_attribute(&lifted_attr); }

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) {
    error("%<fnvnx_pi%>: built for GCC %s, loaded by %s", gcc_version.basever, ver->basever);
    return 1;
  }
  if (info->argc) {   /* no silent modes: this plugin has no options */
    error("%<fnvnx_pi%>: unknown plugin argument %qs", info->argv[0].key);
    return 1;
  }
  register_pass_info pc;
  pc.pass = new cfg_pass(g);
  pc.reference_pass_name = "cfg";
  pc.ref_pass_instance_number = 1;
  pc.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pc);
  register_pass_info pe;
  pe.pass = new early_pass(g);
  pe.reference_pass_name = "ssa";
  pe.ref_pass_instance_number = 1;
  pe.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pe);
  register_pass_info pl;
  pl.pass = new late_pass(g);
  pl.reference_pass_name = "optimized";
  pl.ref_pass_instance_number = 1;
  pl.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pl);
  register_callback(info->base_name, PLUGIN_ATTRIBUTES, register_attrs, NULL);
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, pre_genericize, NULL);
  register_callback(info->base_name, PLUGIN_START_UNIT, start_unit, NULL);
  register_callback(info->base_name, PLUGIN_ALL_IPA_PASSES_START, ipa_start, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  return 0;
}
