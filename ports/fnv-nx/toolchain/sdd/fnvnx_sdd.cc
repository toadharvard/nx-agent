/* fnvnx_sdd - SDD2: one vtable slot per virtual destructor, done in the compiler (no source edit).
 *
 * MSVC gives a virtual destructor one vftable slot, the scalar deleting destructor ??_G(this, flags): run the
 * destructor, and free the object when flags bit 0 is set. GCC gives it two (the complete destructor D1 and the
 * deleting destructor D0), so every later slot is one further. This plugin keeps the front end's own classes,
 * destructors, operator delete lookup and lazy definition of implicit destructors, and changes only the table and
 * the calls through it:
 *
 *   tables   in every _ZTV / _ZTC initializer the D1 / D0 pair of entries becomes ONE entry, the function
 *            `<D1 entry>.fnvnx_sdd`(this, flags): flags & 1 ? D0(this) : D1(this); returns this. Emitted here as a
 *            COMDAT function next to the table. The table shrinks by one slot per pair.
 *   calls    every virtual call (OBJ_TYPE_REF) loads slot `k - (deleting destructors before k)` of its class's
 *            table; a virtual destructor call passes flags 0, a delete-expression flags 1. The slot number comes
 *            from the class's own BINFO_VIRTUALS, the list the table was built from.
 *   address points
 *            every `&table + off` in code and in VTTs becomes `&table.fnvnx_ap<off>` (off = the front end's
 *            offset), a symbol defined next to the table at the address point's place in the shrunk table. So each
 *            sub-table has its own symbol and relocation (VTSYM1), and nothing in the compiler computes with a
 *            stale offset: the optimizers cannot look through the symbol.
 *
 * Rewritten calls are plain indirect calls and -fno-devirtualize is required (GCC's devirtualization reads tables
 * by the front end's slot numbers; MSVC 2008 did not devirtualize either); calls the front end resolved statically
 * stay direct calls of D1 / D0 or of the method.
 * Anything this plugin sees and cannot place is a compile error.
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
#include "cgraph.h"
#include "varasm.h"
#include "output.h"
#include "diagnostic-core.h"
#include "fold-const.h"
#include "stor-layout.h"
#include "tree-cfg.h"
#include <map>
#include <set>
#include <string>
#include <vector>

int plugin_is_GPL_compatible;

struct table {
  std::vector<std::pair<long, long> > aps;       /* address points: front-end byte offset -> byte offset now */
};
static std::map<std::string, table> tables;      /* tables defined in this unit, by assembler name */
static std::map<std::string, tree> ap_decls;
static std::map<std::string, std::pair<std::string, std::string> > thunks;   /* name -> (D1 entry, D0 entry) */

static const char *asmname(tree d) { return IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d)); }

static bool is_table(tree v)
{
  if (!v || !VAR_P(v) || !DECL_VIRTUAL_P(v) || !DECL_ASSEMBLER_NAME_SET_P(v)) return false;
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
  }
  /* the table itself is no longer named: keep it written out where it is defined */
  mark_decl_referenced(vt);
  if (varpool_node *n2 = varpool_node::get(vt)) n2->force_output = 1;
  return d;
}

/* ---------------------------------------------------------------------------------------------- tables */

enum kind { K_OTHER, K_D1, K_D0, K_RTTI };

static tree entry_fn(tree v)
{
  STRIP_NOPS(v);
  if (TREE_CODE(v) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(v, 0)) == FUNCTION_DECL) return TREE_OPERAND(v, 0);
  return NULL_TREE;
}

static kind entry_kind(tree v)
{
  STRIP_NOPS(v);
  if (TREE_CODE(v) == ADDR_EXPR && VAR_P(TREE_OPERAND(v, 0)) && DECL_ASSEMBLER_NAME_SET_P(TREE_OPERAND(v, 0))
      && !strncmp(asmname(TREE_OPERAND(v, 0)), "_ZTI", 4)) return K_RTTI;
  tree f = entry_fn(v);
  if (!f) return K_OTHER;
  if (DECL_THUNK_P(f)) f = THUNK_TARGET(f);
  if (DECL_COMPLETE_DESTRUCTOR_P(f)) return K_D1;
  if (DECL_DELETING_DESTRUCTOR_P(f)) return K_D0;
  return K_OTHER;
}

/* the slots of deleting destructors in class table vt, from the class's base graph */
static void binfo_d0(tree binfo, tree vt, std::set<long> &rm, std::set<tree> &seen, HOST_WIDE_INT esz)
{
  if (!seen.insert(binfo).second) return;
  HOST_WIDE_INT off;
  tree e = BINFO_VTABLE(binfo);
  if (e && table_ref(e, &off) == vt && off >= 0) {
    long i = off / esz;
    for (tree v = BINFO_VIRTUALS(binfo); v; v = TREE_CHAIN(v), i++)
      if (DECL_DELETING_DESTRUCTOR_P(BV_FN(v))) rm.insert(i);
  }
  tree b;
  for (unsigned i = 0; BINFO_BASE_ITERATE(binfo, i, b); i++) binfo_d0(b, vt, rm, seen, esz);
}

static tree thunk_decl(tree e1, tree e0)
{
  std::string n = std::string(asmname(e1)) + ".fnvnx_sdd";
  thunks[n] = std::make_pair(std::string(asmname(e1)), std::string(asmname(e0)));
  tree d = build_decl(UNKNOWN_LOCATION, FUNCTION_DECL, get_identifier(n.c_str()), TREE_TYPE(e1));
  SET_DECL_ASSEMBLER_NAME(d, get_identifier(n.c_str()));
  DECL_EXTERNAL(d) = 1; TREE_PUBLIC(d) = 1; DECL_ARTIFICIAL(d) = 1; DECL_IGNORED_P(d) = 1; TREE_USED(d) = 1;
  for (tree e : { e1, e0 })
    if (cgraph_node *c = cgraph_node::get(e)) { c->force_output = 1; mark_decl_referenced(e); }
  return d;
}

static void shrink_table(tree vt)
{
  tree init = DECL_INITIAL(vt);
  if (!init || TREE_CODE(init) != CONSTRUCTOR) return;
  HOST_WIDE_INT esz = int_size_in_bytes(TREE_TYPE(TREE_TYPE(vt)));
  std::vector<tree> vals;
  unsigned HOST_WIDE_INT ix; tree idx, val;
  FOR_EACH_CONSTRUCTOR_ELT(CONSTRUCTOR_ELTS(init), ix, idx, val) {
    if (idx && (!tree_fits_uhwi_p(idx) || tree_to_uhwi(idx) != ix)) { error("SDD2: table %qD has a sparse initializer", vt); return; }
    vals.push_back(val);
  }
  std::set<long> rm;
  bool own = !strncmp(asmname(vt), "_ZTV", 4);
  if (own) {
    std::set<tree> seen;
    binfo_d0(TYPE_BINFO(DECL_CONTEXT(vt)), vt, rm, seen, esz);
    /* the two views of the table must agree wherever an entry names its destructor */
    for (size_t i = 0; i < vals.size(); i++)
      /* (an entry that names no function says nothing: pure virtual, or the zero of a lost primary base) */
      if ((entry_kind(vals[i]) == K_D0 && !rm.count((long)i))
          || (rm.count((long)i) && entry_fn(vals[i]) && entry_fn(vals[i]) != abort_fndecl && entry_kind(vals[i]) != K_D0))
        { tree f = entry_fn(vals[i]);
          error("SDD2: table %qD entry %d (%s): the class's base graph says %s, the initializer %s", vt, (int)i,
                f ? asmname(f) : "not a function", rm.count((long)i) ? "deleting destructor" : "other",
                entry_kind(vals[i]) == K_D0 ? "deleting destructor" : "other"); return; }
  } else {
    for (size_t i = 0; i < vals.size(); i++) {
      if (entry_kind(vals[i]) == K_D0) rm.insert((long)i);
      if (entry_fn(vals[i]) == abort_fndecl) { error("SDD2: construction table %qD has a pure virtual entry", vt); return; }
    }
  }
  table &t = tables[asmname(vt)];
  vec<constructor_elt, va_gc> *ne = NULL;
  long removed = 0;
  for (size_t i = 0; i < vals.size(); i++) {
    if (rm.count((long)i)) { removed++; continue; }
    tree v = vals[i];
    if (i > 0 && entry_kind(vals[i - 1]) == K_RTTI) t.aps.push_back(std::make_pair((long)(i * esz), (long)((i - removed) * esz)));
    if (rm.count((long)i + 1)) {
      tree e1 = entry_fn(v), e0 = entry_fn(vals[i + 1]);
      if (!e1 != !e0 || (e1 == abort_fndecl) != (e0 == abort_fndecl))
        { error("SDD2: table %qD entry %d: a destructor pair of two different kinds", vt, (int)i); return; }
      if (e1 && e1 != abort_fndecl)
        v = fold_convert(TREE_TYPE(v), build_fold_addr_expr(thunk_decl(e1, e0)));
    }
    CONSTRUCTOR_APPEND_ELT(ne, size_int(i - removed), v);
  }
  if (!removed) return;
  tree at = build_array_type(TREE_TYPE(TREE_TYPE(vt)), build_index_type(size_int(vals.size() - removed - 1)));
  TREE_TYPE(vt) = at;
  DECL_INITIAL(vt) = build_constructor(at, ne);
  TREE_CONSTANT(DECL_INITIAL(vt)) = 1; TREE_STATIC(DECL_INITIAL(vt)) = 1;
  DECL_SIZE(vt) = NULL_TREE; DECL_SIZE_UNIT(vt) = NULL_TREE;
  layout_decl(vt, 0);
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
    if (!vt) { error("SDD2: VTT %qD entry %d is not a table address point", vtt, (int)ix); continue; }
    ce->value = fold_convert(TREE_TYPE(ce->value), build_fold_addr_expr(ap_decl(vt, off)));
  }
}

static void ipa_start(void *, void *)
{
  /* GCC's own devirtualization reads tables by the front end's slot numbers */
  if (flag_devirtualize) { error("SDD2: compile with %<-fno-devirtualize%>"); return; }
  varpool_node *n;
  std::vector<tree> tabs, vtts;
  FOR_EACH_DEFINED_VARIABLE(n) {
    tree v = n->decl;
    if (is_table(v)) tabs.push_back(v);
    else if (VAR_P(v) && DECL_ASSEMBLER_NAME_SET_P(v) && !strncmp(asmname(v), "_ZTT", 4)) vtts.push_back(v);
  }
  for (tree v : vtts) retarget_vtt(v);
  for (tree v : tabs) shrink_table(v);
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

/* slot `tok` of class cls: the slot after the deleting destructors before it are gone; *flags 0 / 1 for a
   complete / deleting destructor, else -1 */
static bool new_slot(tree cls, unsigned HOST_WIDE_INT tok, unsigned HOST_WIDE_INT *slot, int *flags)
{
  unsigned HOST_WIDE_INT i = 0, d0 = 0;
  for (tree v = BINFO_VIRTUALS(TYPE_BINFO(TYPE_MAIN_VARIANT(cls))); v; v = TREE_CHAIN(v), i++) {
    tree f = BV_FN(v);
    if (i == tok) {
      *flags = DECL_COMPLETE_DESTRUCTOR_P(f) ? 0 : DECL_DELETING_DESTRUCTOR_P(f) ? 1 : -1;
      *slot = tok - d0 - (*flags == 1 ? 1 : 0);
      return true;
    }
    if (DECL_DELETING_DESTRUCTOR_P(f)) d0++;
  }
  return false;
}

namespace {
const pass_data sdd_data = { GIMPLE_PASS, "fnvnx_sdd", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
struct sdd_pass : gimple_opt_pass {
  sdd_pass(gcc::context *c) : gimple_opt_pass(sdd_data, c) {}
  unsigned int execute(function *fun) override
  {
    basic_block bb;
    HOST_WIDE_INT psz = POINTER_SIZE / BITS_PER_UNIT;
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
        if (!is_gimple_call(st) || !gimple_call_fn(st) || TREE_CODE(gimple_call_fn(st)) != OBJ_TYPE_REF) continue;
        tree ref = gimple_call_fn(st);
        tree cls = obj_type_ref_class(ref);
        unsigned HOST_WIDE_INT slot; int flags;
        if (!tree_fits_uhwi_p(OBJ_TYPE_REF_TOKEN(ref)) || !new_slot(cls, tree_to_uhwi(OBJ_TYPE_REF_TOKEN(ref)), &slot, &flags)) {
          error_at(gimple_location(st), "SDD2: a virtual call whose slot is not in its class %qT", cls);
          continue;
        }
        gcall *call = as_a<gcall *>(st);
        tree obj = OBJ_TYPE_REF_OBJECT(ref);
        tree vp = make_ssa_name(ptr_type_node);
        gimple *g1 = gimple_build_assign(vp, build2(MEM_REF, ptr_type_node, obj, build_int_cst(ptr_type_node, 0)));
        gimple_set_vuse(g1, gimple_vuse(st)); gimple_set_location(g1, gimple_location(st));
        gsi_insert_before(&gsi, g1, GSI_SAME_STMT);
        tree fnt = gimple_call_fntype(call);
        if (flags >= 0)
          fnt = build_function_type_list(ptr_type_node, TREE_TYPE(obj), unsigned_type_node, NULL_TREE);
        tree fp = make_ssa_name(build_pointer_type(fnt));
        gimple *g2 = gimple_build_assign(fp, build2(MEM_REF, TREE_TYPE(fp), vp, build_int_cst(ptr_type_node, slot * psz)));
        gimple_set_vuse(g2, gimple_vuse(st)); gimple_set_location(g2, gimple_location(st));
        gsi_insert_before(&gsi, g2, GSI_SAME_STMT);
        if (flags < 0) {
          gimple_call_set_fn(call, fp);
          update_stmt(call);
        } else {
          if (gimple_call_num_args(call) != 1) { error_at(gimple_location(st), "SDD2: a virtual destructor call with %d arguments", (int)gimple_call_num_args(call)); continue; }
          gcall *nc = gimple_build_call(fp, 2, gimple_call_arg(call, 0), build_int_cst(unsigned_type_node, flags));
          gimple_set_location(nc, gimple_location(st));
          gimple_call_set_nothrow(nc, gimple_call_nothrow_p(call));
          gimple_move_vops(nc, call);
          gsi_replace(&gsi, nc, true);
        }
      }
    return 0;
  }
};
}

/* ---------------------------------------------------------------------------------------------- output */

static void finish_unit(void *, void *)
{
  varpool_node *n;
  FOR_EACH_DEFINED_VARIABLE(n) {
    tree vt = n->decl;
    if (!is_table(vt) || !TREE_ASM_WRITTEN(vt)) continue;
    const char *base = asmname(vt);
    std::map<std::string, table>::iterator it = tables.find(base);
    if (it == tables.end()) { error("SDD2: table %qD was written out but never seen", vt); continue; }
    for (std::pair<long, long> ap : it->second.aps) {
      std::string s = std::string(base) + ".fnvnx_ap" + std::to_string(ap.first);
      fprintf(asm_out_file, "\t%s\t%s\n\t.type\t%s, %%object\n\t.set\t%s, %s+%ld\n",
              DECL_WEAK(vt) ? ".weak" : TREE_PUBLIC(vt) ? ".globl" : ".local", s.c_str(), s.c_str(), s.c_str(), base, ap.second);
      if (DECL_VISIBILITY(vt) == VISIBILITY_HIDDEN) fprintf(asm_out_file, "\t.hidden\t%s\n", s.c_str());
    }
  }
  for (std::map<std::string, std::pair<std::string, std::string> >::iterator t = thunks.begin(); t != thunks.end(); ++t) {
    const char *nm = t->first.c_str();
    fprintf(asm_out_file,
            "\t.section\t.text.%s,\"axG\",%%progbits,%s,comdat\n\t.align\t2\n\t.weak\t%s\n\t.type\t%s, %%function\n%s:\n"
            "\t.cfi_startproc\n\tstp\tx29, x30, [sp, -32]!\n\t.cfi_def_cfa_offset 32\n\t.cfi_offset 29, -32\n\t.cfi_offset 30, -24\n"
            "\tmov\tx29, sp\n\tstr\tx19, [sp, 16]\n\t.cfi_offset 19, -16\n\tmov\tx19, x0\n\ttbnz\tw1, #0, 1f\n"
            "\tbl\t%s\n\tb\t2f\n1:\tbl\t%s\n2:\tmov\tx0, x19\n\tldr\tx19, [sp, 16]\n\tldp\tx29, x30, [sp], 32\n"
            "\t.cfi_restore 30\n\t.cfi_restore 29\n\t.cfi_restore 19\n\t.cfi_def_cfa_offset 0\n\tret\n\t.cfi_endproc\n\t.size\t%s, .-%s\n",
            nm, nm, nm, nm, nm, t->second.first.c_str(), t->second.second.c_str(), nm, nm);
  }
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) return 1;
  register_pass_info pi;
  pi.pass = new sdd_pass(g);
  pi.reference_pass_name = "ssa";
  pi.ref_pass_instance_number = 1;
  pi.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pi);
  register_callback(info->base_name, PLUGIN_ALL_IPA_PASSES_START, ipa_start, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  return 0;
}
