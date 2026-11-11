/* fnvnx_sdd - SDD2: one vtable slot per virtual destructor, done in the compiler (no source edit).
 *
 * MSVC gives a virtual destructor one vftable slot, the scalar deleting destructor ??_G(this, flags): run the
 * destructor, and free the object when flags bit 0 is set. GCC gives it two (the complete destructor D1 and the
 * deleting destructor D0), so every later slot is one further.
 *
 * GCC already has a destructor variant that is not virtual: when it clones a destructor, the base-object variant D2
 * gets DECL_VIRTUAL_P = 0 and no index (cp/class.cc build_clone). This plugin does the same to D0, for every class,
 * at the one point where that is still possible: finish_struct_1 calls the target hook key_method_may_be_inline for
 * each class with a vptr after its destructor variants exist (check_bases_and_members) and before its virtual
 * functions get slots (layout_class_type). From there on the front end itself builds everything with one slot:
 * BINFO_VIRTUALS, virtual indexes, vtables, thunks, VTTs, construction vtables, pointers to virtual members,
 * constant folding and devirtualization all agree, because none of them ever saw a second slot.
 *
 * What is left is to give that slot MSVC's meaning:
 *
 *   tables   every table entry that is D1 (or a this-adjusting thunk to D1) becomes `<entry>.fnvnx_sdd`(this, flags),
 *            MSVC's ??_E, which returns this:
 *              flags 0   entry(this)                                     the destructor
 *              flags 1   D0(object)                                      ??_G: destroy and operator delete
 *              flags 2/3 for the n elements before which the word n lies, last first: D1(element);
 *                        with bit 0, operator delete[](block)            the vector deleting destructor
 *            (object = this adjusted as the thunk adjusts it; operator delete[] = the one the class's scope lookup
 *            finds, asked of the front end when the class is completed). A COMDAT function emitted in each unit
 *            that emits such a table. D0 is kept wherever its body exists.
 *            The array cookie is GCC's own for a class aligned to at most 4: one word, the element count, right
 *            before the first element, and the block starts at that word.
 *   calls    a virtual call of the destructor's slot passes flags 0. A delete-expression, which the front end now
 *            compiles to a direct call of the static type's D0, becomes a call through the object's slot with
 *            flags 1 (for an object of exactly the static type that is the same function).
 *   address points (VTSYM1)
 *            every `&table + off` in code and in VTTs becomes `&table.fnvnx_ap<off>`, a symbol defined next to the
 *            table, so each sub-table has its own symbol and relocation. Done just before RTL expansion: GIMPLE
 *            keeps the address an invariant until then, and GCC's own analyses read the original form.
 *
 *   table map (for the link, L1 CONTRACT)
 *            every emitted `_ZTV<C>` is described in the object's own section .fnvnx.vtmap (not loaded), one text
 *            line per address point:  _ZTV<C> <TAB> offset <TAB> vptr offset in C <TAB> C <TAB> base path
 *            where C and the path (from C down to the base subobject whose table it is, `;`-separated) are Itanium
 *            type strings. The MSVC name of the same sub-table (??_7C@@6B...@) is paired with it by class and vptr
 *            offset from the MSVC-side dump of the same source.
 *
 * Anything this plugin sees and cannot place is a compile error; a D0 found in a table is one (the hook was missed).
 */
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "cp/cp-tree.h"
#include "context.h"
#include "tree-pass.h"
#include "function.h"
#include "basic-block.h"
#include "gimple-expr.h"
#include "gimple.h"
#include "gimple-iterator.h"
#include "gimple-walk.h"
#include "ssa.h"
#include "tree-ssa-operands.h"
#include "tree-ssanames.h"
#include "stringpool.h"
#include "attribs.h"
#include "cgraph.h"
#include "varasm.h"
#include "output.h"
#include "diagnostic-core.h"
#include "fold-const.h"
#include "stor-layout.h"
#include "tree-cfg.h"
#include "target.h"
#include <map>
#include <set>
#include <string>
#include <vector>

int plugin_is_GPL_compatible;

struct sdd_fn {
  std::string entry, d0, d1;          /* the table entry, the class's deleting and complete destructors */
  HOST_WIDE_INT fixed; bool virt; HOST_WIDE_INT voff;   /* the entry's this adjustment, when it is a thunk */
  HOST_WIDE_INT size;                 /* sizeof the class: the element size of an array of it */
  std::string vdel; bool vdel_sized;  /* the class's operator delete[] */
};
static std::map<tree, tree> array_delete;        /* class -> the operator delete[] its scope lookup finds */
static std::map<std::string, tree> ap_decls;
static std::map<std::string, sdd_fn> thunks;     /* <entry>.fnvnx_sdd -> what it calls */

/* the symbol as the assembler sees it (a user label `__asm__("x")` is stored as "*x") */
static const char *asmname(tree d)
{
  const char *n = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
  return *n == '*' ? n + 1 : n;
}

static std::set<tree> ap_set;                        /* the `<table>.fnvnx_ap<off>` declarations */

static bool is_table(tree v)
{
  /* by symbol, not by DECL_VIRTUAL_P: a hand-declared `extern void* const t[] __asm__("_ZTV...")` is the table too */
  if (!v || !VAR_P(v) || !DECL_ASSEMBLER_NAME_SET_P(v)) return false;
  if (ap_set.count(v)) return false;                 /* an address point's own symbol, already final */
  const char *n = asmname(v);
  return !strncmp(n, "_ZTV", 4) || !strncmp(n, "_ZTC", 4);
}

/* e is `&table + off` in any of the front end's or GIMPLE's forms: the table and the byte offset, else null */
static tree table_ref(tree e, HOST_WIDE_INT *off)
{
  STRIP_NOPS(e);
  if (TREE_CODE(e) == POINTER_PLUS_EXPR && tree_fits_shwi_p(TREE_OPERAND(e, 1))) {
    HOST_WIDE_INT o;
    tree t = table_ref(TREE_OPERAND(e, 0), &o);
    if (t) { *off = (o < 0 ? 0 : o) + tree_to_shwi(TREE_OPERAND(e, 1)); return t; }
    return NULL_TREE;
  }
  if (TREE_CODE(e) != ADDR_EXPR) return NULL_TREE;
  tree op = TREE_OPERAND(e, 0);
  if (TREE_CODE(op) == MEM_REF && TREE_CODE(TREE_OPERAND(op, 0)) == ADDR_EXPR
      && is_table(TREE_OPERAND(TREE_OPERAND(op, 0), 0)) && tree_fits_shwi_p(TREE_OPERAND(op, 1))) {
    *off = tree_to_shwi(TREE_OPERAND(op, 1));
    return TREE_OPERAND(TREE_OPERAND(op, 0), 0);
  }
  if (TREE_CODE(op) == ARRAY_REF && is_table(TREE_OPERAND(op, 0)) && tree_fits_shwi_p(TREE_OPERAND(op, 1))) {
    *off = tree_to_shwi(TREE_OPERAND(op, 1)) * int_size_in_bytes(TREE_TYPE(op));
    return TREE_OPERAND(op, 0);
  }
  if (is_table(op)) { *off = -1; return op; }
  return NULL_TREE;
}

static tree ap_decl(tree vt, HOST_WIDE_INT off)
{
  std::string n = std::string(asmname(vt)) + ".fnvnx_ap" + std::to_string((long long)off);
  tree &d = ap_decls[n];
  if (!d) {
    d = build_decl(UNKNOWN_LOCATION, VAR_DECL, get_identifier(n.c_str()), TREE_TYPE(TREE_TYPE(vt)));
    SET_DECL_ASSEMBLER_NAME(d, get_identifier(n.c_str()));
    DECL_EXTERNAL(d) = 1; TREE_PUBLIC(d) = 1; DECL_ARTIFICIAL(d) = 1; DECL_IGNORED_P(d) = 1; TREE_READONLY(d) = 1;
    DECL_VISIBILITY(d) = DECL_VISIBILITY(vt); DECL_VISIBILITY_SPECIFIED(d) = DECL_VISIBILITY_SPECIFIED(vt);
    TREE_USED(d) = 1;
    ap_set.insert(d);
  }
  /* the table itself is no longer named: keep it written out where it is defined */
  mark_decl_referenced(vt);
  if (varpool_node *n2 = varpool_node::get(vt)) n2->force_output = 1;
  return d;
}

/* ---------------------------------------------------------------------------------------------- one slot */

static bool (*orig_key_method_may_be_inline)(void);

/* finish_struct_1, between check_bases_and_members and layout_class_type: the class's deleting destructor is not
   virtual, like its base-object destructor */
static bool key_method_hook(void)
{
  tree t = current_class_type;
  if (t && CLASS_TYPE_P(t))
    for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
      if (TREE_CODE(f) == FUNCTION_DECL && DECL_DELETING_DESTRUCTOR_P(f) && DECL_VINDEX(f)) {
        DECL_VIRTUAL_P(f) = 0;
        DECL_VINDEX(f) = NULL_TREE;
        DECL_PRESERVE_P(f) = 1;          /* written out wherever its body is: the slot function calls it by name */
        /* ??_E frees an array as cl's does (toolchain/sdd/test_sdd.py has the pinned cl's own code for each shape):
           with the usual operator delete[] of the class's own scope - (void*), else (void*, size_t), which gets
           sizeof(the class) - looked up now by the front end (the class is not complete yet, so by name in its
           members and bases); and when the scope has none, or only placement forms (Havok's allocator macro),
           with the GLOBAL operator delete(void*), ??3@YAXPAX@Z: not delete[], and not the class's own scalar
           operator delete. null = that global one */
        if (!array_delete.count(t)) {
          tree found = NULL_TREE, sized = NULL_TREE;
          tree fns = lookup_fnfields(t, ovl_op_identifier(false, VEC_DELETE_EXPR), 1, tf_none);
          if (fns == error_mark_node) error("SDD2: %qT: operator delete[] is ambiguous", t);
          else if (fns)
            for (lkp_iterator it(BASELINK_FUNCTIONS(fns)); it; ++it) {
              tree fn = *it;
              if (TREE_CODE(fn) != FUNCTION_DECL) continue;
              int n = list_length(TYPE_ARG_TYPES(TREE_TYPE(fn)));
              if (n == 2) found = fn;
              else if (n == 3 && same_type_p(TREE_VALUE(TREE_CHAIN(TYPE_ARG_TYPES(TREE_TYPE(fn)))), size_type_node)) sized = fn;
            }
          if (!found) found = sized;
          array_delete[t] = found;
          if (found) mark_used(found);
        }
      }
  return orig_key_method_may_be_inline();
}

/* COOKIE1: the count before an array made by new[] is one size_t for every element type, as in the exe (cl:
   `mov eax, [esi-4]` / `lea edi, [esi-4]` in ??_E of a class aligned to 8). The Itanium default pads the cookie to
   the element's alignment, so the block of such an array would start 8 bytes before it, not 4. */
static tree cookie_size_hook(tree) { return size_in_bytes(sizetype); }

static void start_unit(void *, void *)
{
  orig_key_method_may_be_inline = targetm.cxx.key_method_may_be_inline;
  targetm.cxx.key_method_may_be_inline = key_method_hook;
  targetm.cxx.get_cookie_size = cookie_size_hook;
}

/* the complete / deleting variant of the destructor that fn is a variant of */
static tree dtor_variant(tree fn, bool deleting)
{
  tree abs = DECL_CLONED_FUNCTION_P(fn) ? DECL_CLONED_FUNCTION(fn) : fn, c;
  FOR_EACH_CLONE(c, abs)
    if (deleting ? DECL_DELETING_DESTRUCTOR_P(c) : DECL_COMPLETE_DESTRUCTOR_P(c)) return c;
  return NULL_TREE;
}

/* ---------------------------------------------------------------------------------------------- tables */

static tree entry_fn(tree v)
{
  STRIP_NOPS(v);
  if (TREE_CODE(v) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(v, 0)) == FUNCTION_DECL) return TREE_OPERAND(v, 0);
  return NULL_TREE;
}

/* the entry `e` (D1 or a thunk to it) as MSVC's ??_G */
static tree sdd_decl(tree vt, tree e)
{
  tree d1 = DECL_THUNK_P(e) ? THUNK_TARGET(e) : e;
  tree d0 = dtor_variant(d1, true);
  if (!d0) { error("SDD2: table %qD: destructor %qD has no deleting variant", vt, d1); return NULL_TREE; }
  tree cls = DECL_CONTEXT(d1);
  sdd_fn t;
  t.entry = asmname(e); t.d0 = asmname(d0); t.d1 = asmname(d1); t.fixed = 0; t.virt = false; t.voff = 0;
  t.size = tree_to_shwi(TYPE_SIZE_UNIT(cls));
  std::map<tree, tree>::iterator ad = array_delete.find(TYPE_MAIN_VARIANT(cls));
  if (ad == array_delete.end())
    { error("SDD2: table %qD: class %qT was never seen being completed", vt, cls); return NULL_TREE; }
  t.vdel = ad->second ? asmname(ad->second) : "_ZdlPv";                        /* ::operator delete(void*), as cl */
  t.vdel_sized = ad->second && list_length(TYPE_ARG_TYPES(TREE_TYPE(ad->second))) > 2;   /* (void*, size_t), void */
  if (DECL_THUNK_P(e)) {
    if (!DECL_THIS_THUNK_P(e)) { error("SDD2: table %qD: a result-adjusting thunk to a destructor", vt); return NULL_TREE; }
    t.fixed = THUNK_FIXED_OFFSET(e);
    if (tree vo = THUNK_VIRTUAL_OFFSET(e)) {
      if (TREE_CODE(vo) != INTEGER_CST) { error("SDD2: table %qD: thunk %qD is not finished", vt, e); return NULL_TREE; }
      t.virt = true; t.voff = tree_to_shwi(vo);
    }
  }
  std::string n = t.entry + ".fnvnx_sdd";
  thunks[n] = t;
  tree d = build_decl(UNKNOWN_LOCATION, FUNCTION_DECL, get_identifier(n.c_str()), TREE_TYPE(e));
  SET_DECL_ASSEMBLER_NAME(d, get_identifier(n.c_str()));
  /* for the fnvnx_names plugin: whose ??_E this is, and the thunk's adjustment */
  DECL_ATTRIBUTES(d) = tree_cons(get_identifier("fnvnx_sdd"),
      tree_cons(NULL_TREE, d1, tree_cons(NULL_TREE, build_int_cst(integer_type_node, t.fixed),
      build_tree_list(NULL_TREE, t.virt ? integer_one_node : integer_zero_node))), NULL_TREE);
  DECL_EXTERNAL(d) = 1; TREE_PUBLIC(d) = 1; DECL_ARTIFICIAL(d) = 1; DECL_IGNORED_P(d) = 1; TREE_USED(d) = 1;
  /* GCC's walks over table entries expect a member function of the class, with a node */
  DECL_CONTEXT(d) = DECL_CONTEXT(d1);
  cgraph_node::get_create(d);
  return d;
}

static void retarget_table(tree vt)
{
  tree init = DECL_INITIAL(vt);
  if (!init || TREE_CODE(init) != CONSTRUCTOR) return;
  unsigned HOST_WIDE_INT ix; constructor_elt *ce;
  FOR_EACH_VEC_SAFE_ELT(CONSTRUCTOR_ELTS(init), ix, ce) {
    tree e = entry_fn(ce->value);
    if (!e) continue;
    tree f = DECL_THUNK_P(e) ? THUNK_TARGET(e) : e;
    if (DECL_DELETING_DESTRUCTOR_P(f)) { error("SDD2: table %qD entry %d is a deleting destructor", vt, (int)ix); continue; }
    if (!DECL_COMPLETE_DESTRUCTOR_P(f)) continue;
    if (tree d = sdd_decl(vt, e)) ce->value = fold_convert(TREE_TYPE(ce->value), build_fold_addr_expr(d));
  }
}

/* VTT entries: `&table + off` -> `&table.fnvnx_ap<off>` */
static void retarget_vtt(tree vtt)
{
  tree init = DECL_INITIAL(vtt);
  if (!init || TREE_CODE(init) != CONSTRUCTOR) return;
  unsigned HOST_WIDE_INT ix; constructor_elt *ce;
  FOR_EACH_VEC_SAFE_ELT(CONSTRUCTOR_ELTS(init), ix, ce) {
    HOST_WIDE_INT off;
    tree vt = table_ref(ce->value, &off);
    if (!vt || off < 0) { error("SDD2: VTT %qD entry %d is not a table address point", vtt, (int)ix); continue; }
    ce->value = fold_convert(TREE_TYPE(ce->value), build_fold_addr_expr(ap_decl(vt, off)));
  }
}

static void ipa_start(void *, void *)
{
  varpool_node *n;
  std::vector<tree> tabs, vtts;
  FOR_EACH_DEFINED_VARIABLE(n) {
    tree v = n->decl;
    if (is_table(v)) tabs.push_back(v);
    else if (VAR_P(v) && DECL_ASSEMBLER_NAME_SET_P(v) && !strncmp(asmname(v), "_ZTT", 4)) vtts.push_back(v);
  }
  for (tree v : vtts) retarget_vtt(v);
  for (tree v : tabs) retarget_table(v);
}

/* ---------------------------------------------------------------------------------------------- code */

static tree walk_op(tree *tp, int *walk_subtrees, void *data)
{
  if (TREE_CODE(*tp) == ADDR_EXPR) {
    HOST_WIDE_INT off;
    tree vt = table_ref(*tp, &off);
    if (vt) {
      if (off < 0) error("SDD2: a reference to the base of table %qD, not to an address point", vt);
      else {
        tree n = build1(ADDR_EXPR, TREE_TYPE(*tp), ap_decl(vt, off));
        recompute_tree_invariant_for_addr_expr(n);
        *tp = n;
        *(bool *)((walk_stmt_info *)data)->info = true;
      }
      *walk_subtrees = 0;
    }
  } else if (VAR_P(*tp) && is_table(*tp))
    error("SDD2: table %qD used other than by an address point", *tp);
  return NULL_TREE;
}

/* a call through the destructor's slot: fp(obj, flags), fp's type MSVC's ??_G */
static void slot_call(gimple_stmt_iterator *gsi, gcall *call, tree fp, tree obj, int flags)
{
  tree fnt = build_function_type_list(ptr_type_node, TREE_TYPE(obj), unsigned_type_node, NULL_TREE);
  /* ??_E is a member function: __thiscall in the exe's contract (CC1, read by the fnvnx_dispatch plugin) */
  fnt = build_type_attribute_variant(fnt, tree_cons(get_identifier("fnvnx_cc"),
          build_tree_list(NULL_TREE, build_string(5, "this")), TYPE_ATTRIBUTES(fnt)));
  tree p = make_ssa_name(build_pointer_type(fnt));
  gimple *g = gimple_build_assign(p, NOP_EXPR, fp);
  gimple_set_location(g, gimple_location(call));
  gsi_insert_before(gsi, g, GSI_SAME_STMT);
  gcall *nc = gimple_build_call(p, 2, obj, build_int_cst(unsigned_type_node, flags));
  gimple_set_location(nc, gimple_location(call));
  gimple_call_set_nothrow(nc, gimple_call_nothrow_p(call));
  gimple_move_vops(nc, call);
  gsi_replace(gsi, nc, true);
}

namespace {
/* early, on SSA: destructor calls get MSVC's form before any pass looks at them */
const pass_data calls_data = { GIMPLE_PASS, "fnvnx_sdd_calls", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
struct calls_pass : gimple_opt_pass {
  calls_pass(gcc::context *c) : gimple_opt_pass(calls_data, c) {}
  unsigned int execute(function *fun) override
  {
    basic_block bb;
    HOST_WIDE_INT psz = POINTER_SIZE / BITS_PER_UNIT;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (!is_gimple_call(st) || gimple_call_internal_p(st)) continue;
        gcall *call = as_a<gcall *>(st);
        tree fn = gimple_call_fn(call);
        if (fn && TREE_CODE(fn) == OBJ_TYPE_REF) {
          /* a virtual call: of the destructor's slot? */
          tree cls = obj_type_ref_class(fn);
          tree v = BINFO_VIRTUALS(TYPE_BINFO(TYPE_MAIN_VARIANT(cls)));
          if (!tree_fits_uhwi_p(OBJ_TYPE_REF_TOKEN(fn))) { error_at(gimple_location(st), "SDD2: a virtual call without a slot"); continue; }
          for (unsigned HOST_WIDE_INT i = tree_to_uhwi(OBJ_TYPE_REF_TOKEN(fn)); v && i; i--) v = TREE_CHAIN(v);
          if (!v) { error_at(gimple_location(st), "SDD2: a virtual call whose slot is not in its class %qT", cls); continue; }
          if (!DECL_COMPLETE_DESTRUCTOR_P(BV_FN(v))) continue;
          if (gimple_call_num_args(call) != 1) { error_at(gimple_location(st), "SDD2: a virtual destructor call with %d arguments", (int)gimple_call_num_args(call)); continue; }
          slot_call(&gsi, call, OBJ_TYPE_REF_EXPR(fn), gimple_call_arg(call, 0), 0);
          continue;
        }
        tree d0 = gimple_call_fndecl(call);
        if (!d0 || !DECL_LANG_SPECIFIC(d0) || !DECL_DELETING_DESTRUCTOR_P(d0)) continue;
        tree d1 = dtor_variant(d0, false);
        if (!d1 || !DECL_VINDEX(d1)) continue;                       /* not a virtual destructor */
        if (TREE_CODE(DECL_VINDEX(d1)) != INTEGER_CST || gimple_call_num_args(call) != 1)
          { error_at(gimple_location(st), "SDD2: a deleting destructor call that cannot be placed"); continue; }
        /* a delete-expression: through the object's own slot, flags 1 */
        tree obj = gimple_call_arg(call, 0);
        tree vp = make_ssa_name(ptr_type_node);
        gimple *g1 = gimple_build_assign(vp, build2(MEM_REF, ptr_type_node, obj, build_int_cst(ptr_type_node, 0)));
        gimple_set_vuse(g1, gimple_vuse(st)); gimple_set_location(g1, gimple_location(st));
        gsi_insert_before(&gsi, g1, GSI_SAME_STMT);
        tree fp = make_ssa_name(ptr_type_node);
        gimple *g2 = gimple_build_assign(fp, build2(MEM_REF, ptr_type_node, vp,
                                                    build_int_cst(ptr_type_node, tree_to_shwi(DECL_VINDEX(d1)) * psz)));
        gimple_set_vuse(g2, gimple_vuse(st)); gimple_set_location(g2, gimple_location(st));
        gsi_insert_before(&gsi, g2, GSI_SAME_STMT);
        slot_call(&gsi, call, fp, obj, 1);
      }
    return 0;
  }
};

/* late, just before expansion: address points by their own symbols */
const pass_data ap_data = { GIMPLE_PASS, "fnvnx_sdd_ap", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
struct ap_pass : gimple_opt_pass {
  ap_pass(gcc::context *c) : gimple_opt_pass(ap_data, c) {}
  unsigned int execute(function *fun) override
  {
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (is_gimple_assign(st) && gimple_assign_rhs_code(st) == POINTER_PLUS_EXPR
            && TREE_CODE(gimple_assign_rhs1(st)) == ADDR_EXPR && is_table(TREE_OPERAND(gimple_assign_rhs1(st), 0))
            && tree_fits_shwi_p(gimple_assign_rhs2(st))) {
          tree a = build1(ADDR_EXPR, TREE_TYPE(gimple_assign_lhs(st)),
                          ap_decl(TREE_OPERAND(gimple_assign_rhs1(st), 0), tree_to_shwi(gimple_assign_rhs2(st))));
          recompute_tree_invariant_for_addr_expr(a);
          gimple_assign_set_rhs_from_tree(&gsi, a);
          update_stmt(gsi_stmt(gsi));
          continue;
        }
        bool changed = false;
        walk_stmt_info wi; memset(&wi, 0, sizeof wi); wi.info = &changed;
        walk_gimple_op(st, walk_op, &wi);
        if (changed) update_stmt(st);
      }
    return 0;
  }
};
}

/* ---------------------------------------------------------------------------------------------- output */

/* the base subobjects of t whose table is the sub-table of vt at byte offset off: the first in the base graph */
static tree ap_binfo(tree binfo, tree vt, HOST_WIDE_INT off, std::set<tree> &seen)
{
  if (!seen.insert(binfo).second) return NULL_TREE;
  HOST_WIDE_INT o;
  tree e = BINFO_VTABLE(binfo);
  if (e && table_ref(e, &o) == vt && o == off) return binfo;
  tree b;
  for (unsigned i = 0; BINFO_BASE_ITERATE(binfo, i, b); i++)
    if (tree r = ap_binfo(b, vt, off, seen)) return r;
  return NULL_TREE;
}

static void vtmap_line(tree vt, HOST_WIDE_INT off)
{
  if (strncmp(asmname(vt), "_ZTV", 4)) return;
  tree t = DECL_CONTEXT(vt);
  std::set<tree> seen;
  tree b = ap_binfo(TYPE_BINFO(t), vt, off, seen);
  if (!b) { error("SDD2: table %qD: no base subobject owns the address point at %d", vt, (int)off); return; }
  std::string path;
  for (tree p = b; p; p = BINFO_INHERITANCE_CHAIN(p))
    path = std::string(mangle_type_string(BINFO_TYPE(p))) + (path.empty() ? "" : ";") + path;
  fprintf(asm_out_file, "\t.ascii\t\"%s\\t%ld\\t%ld\\t%s\\t%s\\n\"\n", asmname(vt), (long)off,
          (long)tree_to_shwi(BINFO_OFFSET(b)), mangle_type_string(t), path.c_str());
}

static void finish_unit(void *, void *)
{
  varpool_node *n;
  FOR_EACH_DEFINED_VARIABLE(n) {
    tree vt = n->decl;
    if (!is_table(vt) || !TREE_ASM_WRITTEN(vt) || !DECL_INITIAL(vt) || TREE_CODE(DECL_INITIAL(vt)) != CONSTRUCTOR) continue;
    const char *base = asmname(vt);
    HOST_WIDE_INT esz = int_size_in_bytes(TREE_TYPE(TREE_TYPE(vt)));
    unsigned HOST_WIDE_INT ix; tree idx, val; bool after_rtti = false;
    /* an address point is the entry after a sub-table's RTTI slot */
    std::vector<HOST_WIDE_INT> aps;
    FOR_EACH_CONSTRUCTOR_ELT(CONSTRUCTOR_ELTS(DECL_INITIAL(vt)), ix, idx, val) {
      if (after_rtti) {
        aps.push_back(ix * esz);
        std::string s = std::string(base) + ".fnvnx_ap" + std::to_string((long long)(ix * esz));
        fprintf(asm_out_file, "\t%s\t%s\n\t.type\t%s, %%object\n\t.set\t%s, %s+%ld\n",
                DECL_WEAK(vt) ? ".weak" : TREE_PUBLIC(vt) ? ".globl" : ".local", s.c_str(), s.c_str(), s.c_str(), base, (long)(ix * esz));
        if (DECL_VISIBILITY(vt) == VISIBILITY_HIDDEN) fprintf(asm_out_file, "\t.hidden\t%s\n", s.c_str());
      }
      tree v = val;
      STRIP_NOPS(v);
      after_rtti = TREE_CODE(v) == ADDR_EXPR && VAR_P(TREE_OPERAND(v, 0)) && DECL_ASSEMBLER_NAME_SET_P(TREE_OPERAND(v, 0))
                   && !strncmp(asmname(TREE_OPERAND(v, 0)), "_ZTI", 4);
    }
    fprintf(asm_out_file, "\t.pushsection\t.fnvnx.vtmap,\"\",%%progbits\n");
    for (HOST_WIDE_INT a : aps) vtmap_line(vt, a);
    fprintf(asm_out_file, "\t.popsection\n");
  }
  for (std::map<std::string, sdd_fn>::iterator t = thunks.begin(); t != thunks.end(); ++t) {
    const char *nm = t->first.c_str();
    const sdd_fn &f = t->second;
    /* x19 = this as passed (the result), x20 = the complete object, w21 = flags, w22 = elements left */
    fprintf(asm_out_file,
            "\t.section\t.text.%s,\"axG\",%%progbits,%s,comdat\n\t.align\t2\n\t.weak\t%s\n\t.type\t%s, %%function\n%s:\n"
            "\t.cfi_startproc\n\tstp\tx29, x30, [sp, -48]!\n\t.cfi_def_cfa_offset 48\n\t.cfi_offset 29, -48\n\t.cfi_offset 30, -40\n"
            "\tmov\tx29, sp\n\tstp\tx19, x20, [sp, 16]\n\tstp\tx21, x22, [sp, 32]\n"
            "\t.cfi_offset 19, -32\n\t.cfi_offset 20, -24\n\t.cfi_offset 21, -16\n\t.cfi_offset 22, -8\n"
            "\tmov\tx19, x0\n\tmov\tw21, w1\n\ttst\tw1, #3\n\tbne\t1f\n"
            "\tbl\t%s\n\tb\t9f\n1:\n", nm, nm, nm, nm, nm, f.entry.c_str());
    /* flags 1 or 2: the complete object, as the thunk would hand it on */
    if (f.fixed) fprintf(asm_out_file, "\tldr\tw9, =%ld\n\tadd\tw0, w0, w9\n", (long)f.fixed);
    if (f.virt) fprintf(asm_out_file, "\tldr\tw9, [x0]\n\tldr\tw10, =%ld\n\tadd\tw9, w9, w10\n\tldr\tw9, [x9]\n\tadd\tw0, w0, w9\n", (long)f.voff);
    fprintf(asm_out_file,
            "\tmov\tw20, w0\n\ttbnz\tw21, #1, 2f\n\tbl\t%s\n\tb\t9f\n"
            /* ??_E: an array made by new[]. The element count is the word before it (MSVC and GCC alike); destroy
               from the last element down, then operator delete[] on the block when flags bit 0 is set */
            "2:\tldr\tw22, [x20, -4]\n3:\tcbz\tw22, 4f\n\tsub\tw22, w22, #1\n\tldr\tw9, =%ld\n\tmadd\tw0, w22, w9, w20\n"
            "\tbl\t%s\n\tb\t3b\n4:\ttbz\tw21, #0, 9f\n",
            f.d0.c_str(), (long)f.size, f.d1.c_str());
    if (f.vdel_sized)
      fprintf(asm_out_file, "\tldr\tw1, =%ld\n", (long)f.size);          /* cl passes the element's size */
    fprintf(asm_out_file,
            "\tsub\tw0, w20, #4\n\tbl\t%s\n"
            "9:\tmov\tx0, x19\n\tldp\tx19, x20, [sp, 16]\n\tldp\tx21, x22, [sp, 32]\n\tldp\tx29, x30, [sp], 48\n"
            "\t.cfi_restore 30\n\t.cfi_restore 29\n\t.cfi_restore 19\n\t.cfi_restore 20\n\t.cfi_restore 21\n\t.cfi_restore 22\n"
            "\t.cfi_def_cfa_offset 0\n\tret\n\t.ltorg\n\t.cfi_endproc\n\t.size\t%s, .-%s\n",
            f.vdel.c_str(), nm, nm);
    /* its N3 line (toolchain/pi): written by rule, with the vector branch, so `ok+E` */
    fprintf(asm_out_file, "\t.pushsection\t.fnvnx.n3,\"\",%%progbits\n\t.ascii\t\"%s\\tok+E\\t\\tsdd\\t\\n\"\n\t.popsection\n", nm);
  }
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) return 1;
  register_pass_info pc;
  pc.pass = new calls_pass(g);
  pc.reference_pass_name = "ssa";
  pc.ref_pass_instance_number = 1;
  pc.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pc);
  register_pass_info pa;
  pa.pass = new ap_pass(g);
  pa.reference_pass_name = "optimized";
  pa.ref_pass_instance_number = 1;
  pa.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pa);
  register_callback(info->base_name, PLUGIN_START_UNIT, start_unit, NULL);
  register_callback(info->base_name, PLUGIN_ALL_IPA_PASSES_START, ipa_start, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  return 0;
}
