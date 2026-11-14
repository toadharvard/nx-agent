// fnvnx_f2i_check.cc - GCC plugin, rule "every float -> integer conversion is in a certified helper" (gate fpexact, check 3).
//
//   aarch64-linux-gnu-g++ ... -fplugin=<dir>/fnvnx_f2i_check.so -fplugin-arg-fnvnx_f2i_check-out=UNIT.o.f2i.json unit.cpp
//
// Rule. A function carrying the attribute fnvnx_f2i (registered here; the rt headers spell it FNVL_F2I) is a conversion helper: its
// body is the one place a float -> integer conversion with x86 semantics (out of range / NaN -> integer indefinite, rounding by
// MXCSR.RC / FCW.RC) is written. In every other function of the unit, any of the following is a finding:
//   conv      a GIMPLE assign with FIX_TRUNC_EXPR (scalar or vector: every C/C++ cast, implicit conversion and compound assignment
//             float -> integer, and __builtin_convertvector, which GCC lowers to it);
//   builtin   a call of a conversion builtin of the lround/lrint/llround/llrint/iround/irint/{i,l,ll}{ceil,floor} families (GCC's
//             CASE_FLT_FN family, all three float widths), or of IFN_VEC_CONVERT with a float operand and an integer result,
//             or of a target (BUILT_IN_MD) builtin with a float-typed argument and an integer-typed (scalar or vector) result;
//   asm       an inline asm that could read a float: one with a float, vector or complex-typed operand or an FP/SIMD register clobber
//             (see asm_may_read_float for the stated assumption).
// The pass runs right after the CFG is built (before SSA, early inlining and every optimisation), so the check is on what the
// source says: inlining a helper into its callers cannot hide or add a site. Types and GCC's own function codes decide; no name,
// no text of source or assembly is looked at (the asm test uses operand types and the register class of the clobbers).
// Output (-fplugin-arg-fnvnx_f2i_check-out=FILE), JSON "fnvnx-f2i/1": unit (main input file), sites (conversion sites seen in
// every analysed function, helpers included), helpers (assembler-visible printable names of analysed helpers), findings
// [{function, file, line, kind}], both sorted. A unit without the output argument is a compile error.
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
#include "stringpool.h"
#include "builtins.h"
#include "langhooks.h"
#include "diagnostic-core.h"
#include "attribs.h"
#include "tm.h"
#include "output.h"
#include <set>
#include <string>
#include <tuple>

int plugin_is_GPL_compatible;

static std::string outpath;
static unsigned long sites;
static std::set<std::string> helpers;
static std::set<std::tuple<std::string, std::string, int, std::string>> findings;   // function, file, line, kind

static bool is_int_like(tree t) {
  if (!t) return false;
  if (TREE_CODE(t) == VECTOR_TYPE) t = TREE_TYPE(t);
  return INTEGRAL_TYPE_P(t);
}

static bool conv_builtin(gcall *c) {
  if (gimple_call_internal_p(c)) {
    if (gimple_call_internal_fn(c) != IFN_VEC_CONVERT) return false;
    tree lt = gimple_call_lhs(c) ? TREE_TYPE(gimple_call_lhs(c)) : NULL_TREE;
    return is_int_like(lt) && gimple_call_num_args(c) >= 1 && FLOAT_TYPE_P(TREE_TYPE(gimple_call_arg(c, 0)));
  }
  tree fn = gimple_call_fndecl(c);
  if (!fn || !fndecl_built_in_p(fn)) return false;
  if (DECL_BUILT_IN_CLASS(fn) == BUILT_IN_NORMAL) {
    switch (DECL_FUNCTION_CODE(fn)) {
      CASE_FLT_FN(BUILT_IN_IROUND):
      CASE_FLT_FN(BUILT_IN_LROUND):
      CASE_FLT_FN(BUILT_IN_LLROUND):
      CASE_FLT_FN(BUILT_IN_IRINT):
      CASE_FLT_FN(BUILT_IN_LRINT):
      CASE_FLT_FN(BUILT_IN_LLRINT):
      CASE_FLT_FN(BUILT_IN_ICEIL):
      CASE_FLT_FN(BUILT_IN_LCEIL):
      CASE_FLT_FN(BUILT_IN_LLCEIL):
      CASE_FLT_FN(BUILT_IN_IFLOOR):
      CASE_FLT_FN(BUILT_IN_LFLOOR):
      CASE_FLT_FN(BUILT_IN_LLFLOOR):
        return true;
      default:
        return false;
    }
  }
  if (DECL_BUILT_IN_CLASS(fn) == BUILT_IN_MD) {
    tree lt = gimple_call_lhs(c) ? TREE_TYPE(gimple_call_lhs(c)) : NULL_TREE;
    if (!is_int_like(lt)) return false;
    for (unsigned i = 0; i < gimple_call_num_args(c); i++)
      if (FLOAT_TYPE_P(TREE_TYPE(gimple_call_arg(c, i)))) return true;
  }
  return false;
}

// An inline asm "can read a float" when a float, vector or complex-typed C object is passed to or from it, or when it declares an
// FP/SIMD register (v0..v31 and their s/d/q/h/b names, decoded by GCC's decode_reg_name) clobbered. Assumption (stated in the gate):
// an asm statement without such an operand or clobber does not read FP/SIMD state it was not given; an asm that does breaks its own
// constraint contract. The object-level check 3b of the gate backs this up: FCVT* instructions in an object whose unit has no
// conversion site at all are a finding, whatever put them there.
static bool asm_may_read_float(gasm *a) {
  for (unsigned i = 0; i < gimple_asm_ninputs(a); i++) {
    tree t = TREE_TYPE(TREE_VALUE(gimple_asm_input_op(a, i)));
    if (FLOAT_TYPE_P(t) || VECTOR_TYPE_P(t)) return true;
  }
  for (unsigned i = 0; i < gimple_asm_noutputs(a); i++) {
    tree t = TREE_TYPE(TREE_VALUE(gimple_asm_output_op(a, i)));
    if (FLOAT_TYPE_P(t) || VECTOR_TYPE_P(t)) return true;
  }
  for (unsigned i = 0; i < gimple_asm_nclobbers(a); i++) {
    int r = decode_reg_name(TREE_STRING_POINTER(TREE_VALUE(gimple_asm_clobber_op(a, i))));
    if (r >= 0 && FP_REGNUM_P(r)) return true;
  }
  return false;
}

static const pass_data f2i_data = {
    GIMPLE_PASS, "fnvnx_f2i_check", OPTGROUP_NONE, TV_NONE, PROP_gimple_any | PROP_cfg, 0, 0, 0, 0,
};

struct f2i_pass : gimple_opt_pass {
  f2i_pass(gcc::context *ctx) : gimple_opt_pass(f2i_data, ctx) {}
  unsigned int execute(function *fun) final override {
    bool helper = lookup_attribute("fnvnx_f2i", DECL_ATTRIBUTES(fun->decl)) != NULL_TREE;
    std::string name = lang_hooks.decl_printable_name(fun->decl, 2);
    if (helper) helpers.insert(name);
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun) {
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *s = gsi_stmt(gsi);
        const char *kind = NULL;
        bool conv = false;
        if (is_gimple_assign(s) && gimple_assign_rhs_code(s) == FIX_TRUNC_EXPR) { kind = "conv"; conv = true; }
        else if (gcall *c = dyn_cast<gcall *>(s)) { if (conv_builtin(c)) { kind = "builtin"; conv = true; } }
        else if (gasm *a = dyn_cast<gasm *>(s)) { if (asm_may_read_float(a)) kind = "asm"; }
        if (!kind) continue;
        if (conv) sites++;
        if (helper) continue;
        expanded_location l = expand_location(gimple_location(s));
        findings.insert({name, l.file ? l.file : "", l.line, kind});
      }
    }
    return 0;
  }
};

static void js(FILE *f, const std::string &s) {
  fputc('"', f);
  for (unsigned char ch : s) {
    if (ch == '"' || ch == '\\') { fputc('\\', f); fputc(ch, f); }
    else if (ch < 0x20 || ch >= 0x7f) fprintf(f, "\\u%04x", ch);
    else fputc(ch, f);
  }
  fputc('"', f);
}

static void finish(void *, void *) {
  FILE *f = fopen(outpath.c_str(), "w");
  if (!f) { error("%<fnvnx_f2i_check%>: cannot write %s", outpath.c_str()); return; }
  fprintf(f, "{\"findings\": [");
  bool first = true;
  for (auto &t : findings) {
    fprintf(f, "%s{\"file\": ", first ? "" : ", "); js(f, std::get<1>(t));
    fprintf(f, ", \"function\": "); js(f, std::get<0>(t));
    fprintf(f, ", \"kind\": "); js(f, std::get<3>(t));
    fprintf(f, ", \"line\": %d}", std::get<2>(t));
    first = false;
  }
  fprintf(f, "], \"format\": \"fnvnx-f2i/1\", \"helpers\": [");
  first = true;
  for (auto &h : helpers) { if (!first) fprintf(f, ", "); js(f, h); first = false; }
  fprintf(f, "], \"sites\": %lu, \"unit\": ", sites);
  js(f, main_input_filename ? main_input_filename : "");
  fprintf(f, "}\n");
  fclose(f);
}

static tree handle_attr(tree *node, tree name, tree, int, bool *no_add) {
  if (TREE_CODE(*node) != FUNCTION_DECL) { warning(0, "%qE attribute only applies to functions", name); *no_add = true; }
  return NULL_TREE;
}
static struct attribute_spec f2i_attr = {"fnvnx_f2i", 0, 0, true, false, false, false, handle_attr, NULL};
static void reg_attrs(void *, void *) { register_attribute(&f2i_attr); }

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version) {
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_f2i_check%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  for (int i = 0; i < info->argc; i++)
    if (!strcmp(info->argv[i].key, "out")) outpath = info->argv[i].value;
  if (outpath.empty()) { error("%<fnvnx_f2i_check%>: the plugin argument %<out=FILE%> is required"); return 1; }
  struct register_pass_info pass;
  pass.pass = new f2i_pass(g);
  pass.reference_pass_name = "cfg";
  pass.ref_pass_instance_number = 1;
  pass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pass);
  register_callback(info->base_name, PLUGIN_ATTRIBUTES, reg_attrs, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  return 0;
}
