// fnvnx_ftoi.cc - GCC plugin: every float -> integer conversion of a unit with MSVC 2008 x86 semantics (S12 FTOI2,
// docs/STATIC_CHECKS.md "float -> integer").
//
//   g++ ... -fplugin=<dir>/fnvnx_ftoi.so [-fplugin-arg-fnvnx_ftoi-verbose] unit.cpp
//
// What MSVC 2008 SP1 compiles for (T)x, x float or double (the same at /Od and /O2; tests/s12 ftoi):
//   int, long, short, signed char      call __ftol2_sse   - cvttsd2si on every SSE2 PC: int32, 0x80000000 for NaN
//                                                           and anything outside int32 (truncated)
//   unsigned short, unsigned char      inline fistp DWORD - x87 int32, the same 0x80000000
//   an enum                            not C++ for this compiler (error C2440): an error here too with enum=error
//                                      (the semantics flag set; without it the old int path)
//   unsigned int, unsigned long        inline fistp QWORD - x87 int64, low 32 bits; 0x8000000000000000 for NaN and
//                                                           anything outside int64 (so 0)
//   __int64, unsigned __int64          call __ftol2       - x87 int64, 0x8000000000000000 likewise
// then the result's low bits for a narrower T. AArch64 fcvtzs / fcvtzu saturate instead (NaN -> 0, too large ->
// the type's maximum, negative -> 0 for unsigned). The pass rewrites each `lhs = (T) x` (GIMPLE FIX_TRUNC_EXPR, so
// casts, implicit conversions and compound assignments alike) into
//   xd = (double) x; ok = xd > LO && xd < HI; t = (S) (ok ? xd : 0.0); r = ok ? t : S_MIN; lhs = (T) r
// with S int32 (LO -2^31-1, exclusive) or int64 (LO -2^63, inclusive), HI 2^31 or 2^63. In-range values convert as
// before; GCC folds the checks of operands it can bound. Runs right after the CFG is built (before SSA).
//
// One plugin, one class (C, float -> integer), two modes (-fplugin-arg-fnvnx_ftoi-mode=native|lifted, required: the recipe passes it
// from the root's cxx_origin, so there is no free choice; a missing or other value is a compile error):
//   native   the rewrite above. A function carrying the attribute fnvnx_f2i (registered here, functions only; the rt headers spell it
//            FNVL_F2I) is a conversion helper: its body is the one place where a conversion is written with its own semantics, so
//            it is not rewritten and is counted as a helper.
//   lifted   no rewrite: lifted code emits its conversions itself, inside helpers. Every conversion outside an fnvnx_f2i function is a
//            hard error, of three kinds:
//              conv      a GIMPLE assign with FIX_TRUNC_EXPR (scalar or vector: every cast, implicit conversion and compound
//                        assignment float -> integer, and __builtin_convertvector, which GCC lowers to it);
//              builtin   a call of a conversion builtin of the lround/lrint/llround/llrint/iround/irint/{i,l,ll}{ceil,floor} families
//                        (all three float widths), or IFN_VEC_CONVERT with a float operand and an integer result, or a target
//                        (BUILT_IN_MD) builtin with a float-typed argument and an integer-typed (scalar or vector) result;
//              asm       an inline asm that could read a float: a float, vector or complex-typed operand or an FP/SIMD register
//                        clobber (asm_may_read_float).
//            Types and GCC's function codes decide; no name, no text of source or assembly is looked at.
// Report (-fplugin-arg-fnvnx_ftoi-out=FILE, required, both modes), JSON "fnvnx-f2i/1" for gate fpexact: unit (main input file), sites
// (conversion sites, conv and builtin, seen in every analysed function, helpers included), helpers (printable names of the analysed
// helpers), findings [{function, file, line, kind}] (what is outside a helper and is not rewritten: in lifted mode the errors, in native
// mode the builtin and asm kinds, and a conv the rewrite does not cover), both sorted.
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
#include "real.h"
#include "diagnostic-core.h"
#include "builtins.h"
#include "langhooks.h"
#include "attribs.h"
#include "tm.h"
#include "output.h"
#include <set>
#include <string>
#include <tuple>

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

static bool verbose;
static bool lifted;
static unsigned long total;
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
// FP/SIMD register (v0..v31 and their s/d/q/h/b names, decoded by GCC's decode_reg_name) clobbered. Fact used: by GCC's documented
// extended-asm contract an asm reads only the operands it is given, so FP/SIMD state it was not given has no defined value for it;
// an asm that reads such state breaks that contract (ASM-001: the pinned compiler is correct). The object-level check 3b of the gate
// backs this up: FCVT* instructions in an object whose unit has no conversion site at all are a finding, whatever put them there.
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

static tree real_cst(double v) {
  REAL_VALUE_TYPE r;
  char buf[64];
  snprintf(buf, sizeof buf, "%.1f", v);
  real_from_string3(&r, buf, TYPE_MODE(double_type_node));
  return build_real(double_type_node, r);
}

static const pass_data ftoi_data = {
    GIMPLE_PASS, "fnvnx_ftoi", OPTGROUP_NONE, TV_NONE, PROP_gimple_any | PROP_cfg, 0, 0, 0, 0,
};

struct ftoi_pass : gimple_opt_pass {
  ftoi_pass(gcc::context *ctx) : gimple_opt_pass(ftoi_data, ctx) {}

  unsigned int execute(function *fun) final override {
    unsigned n = 0;
    bool helper = lookup_attribute("fnvnx_f2i", DECL_ATTRIBUTES(fun->decl)) != NULL_TREE;
    std::string name = lang_hooks.decl_printable_name(fun->decl, 2);
    if (helper) helpers.insert(name);
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun) {
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *stmt = gsi_stmt(gsi);
        const char *kind = NULL;
        bool conv = false;
        if (is_gimple_assign(stmt) && gimple_assign_rhs_code(stmt) == FIX_TRUNC_EXPR) { kind = "conv"; conv = true; }
        else if (gcall *c = dyn_cast<gcall *>(stmt)) { if (conv_builtin(c)) { kind = "builtin"; conv = true; } }
        else if (gasm *as = dyn_cast<gasm *>(stmt)) { if (asm_may_read_float(as)) kind = "asm"; }
        if (!kind) continue;
        if (conv) sites++;
        if (helper) continue;
        if (lifted) {
          expanded_location l = expand_location(gimple_location(stmt));
          findings.insert({name, l.file ? l.file : "", l.line, kind});
          error_at(gimple_location(stmt), "FTOI (lifted): a float -> integer conversion (%s) outside a function carrying fnvnx_f2i", kind);
          continue;
        }
        if (strcmp(kind, "conv")) {   // native: builtin and asm are not rewritten
          expanded_location l = expand_location(gimple_location(stmt));
          findings.insert({name, l.file ? l.file : "", l.line, kind});
          continue;
        }
        tree lhs = gimple_assign_lhs(stmt), x = gimple_assign_rhs1(stmt);
        tree it = TREE_TYPE(lhs), ft = TREE_TYPE(x);
        // RULE (c): every FIX_TRUNC_EXPR of a function is rewritten here, or inside an fnvnx_f2i helper, or a compile error. A shape the
        // rewrite does not cover (a vector, a floating type wider than 64 bits, an integer type wider than 64 bits) names its types and fails.
        if ((TREE_CODE(it) != INTEGER_TYPE && TREE_CODE(it) != ENUMERAL_TYPE) || !SCALAR_FLOAT_TYPE_P(ft) || TYPE_PRECISION(ft) > 64
            || TYPE_PRECISION(it) > 64) {
          expanded_location l = expand_location(gimple_location(stmt));
          findings.insert({name, l.file ? l.file : "", l.line, "conv"});
          error_at(gimple_location(stmt), "FTOI: conversion %qT to %qT is not covered by the MSVC rewrite (vector, or a type wider than 64 bits)", ft, it);
          continue;
        }
        unsigned p = TYPE_PRECISION(it);
        // A floating value converted straight to an enum is not the exe's language: MSVC 2008 rejects it (C2440,
        // "conversions between enumeration and floating point values are no longer allowed"; tests/s12 ftoi_enum
        // has the compiler's own answer), so the exe's source named an integer type in between and that type chose
        // the conversion. A text that omits it does not say which, and nothing here picks one.
        if (TREE_CODE(it) == ENUMERAL_TYPE) {
          error_at(gimple_location(stmt), "FTOI: %qT to enum %qT without the integer type in between (MSVC C2440)", ft, it);
          continue;
        }
        bool uns = TYPE_UNSIGNED(it);
        bool wide = p > 32 || (p == 32 && uns);                  // x87 int64 (__ftol2, fistp QWORD)
        tree st = wide ? long_long_integer_type_node : integer_type_node;
        gimple_seq seq = NULL;
        tree xd = x;
        if (TYPE_MAIN_VARIANT(ft) != double_type_node) {
          xd = create_tmp_var(double_type_node, "ftoi_d");
          gimple_seq_add_stmt(&seq, gimple_build_assign(xd, NOP_EXPR, x));
        }
        tree lo = create_tmp_var(boolean_type_node, "ftoi_lo");
        tree hi = create_tmp_var(boolean_type_node, "ftoi_hi");
        tree ok = create_tmp_var(boolean_type_node, "ftoi_ok");
        tree xs = create_tmp_var(double_type_node, "ftoi_x");
        tree t = create_tmp_var(st, "ftoi_t");
        tree r = create_tmp_var(st, "ftoi_r");
        if (wide) {
          gimple_seq_add_stmt(&seq, gimple_build_assign(lo, GE_EXPR, xd, real_cst(-9223372036854775808.0)));
          gimple_seq_add_stmt(&seq, gimple_build_assign(hi, LT_EXPR, xd, real_cst(9223372036854775808.0)));
        } else {
          gimple_seq_add_stmt(&seq, gimple_build_assign(lo, GT_EXPR, xd, real_cst(-2147483649.0)));
          gimple_seq_add_stmt(&seq, gimple_build_assign(hi, LT_EXPR, xd, real_cst(2147483648.0)));
        }
        gimple_seq_add_stmt(&seq, gimple_build_assign(ok, BIT_AND_EXPR, lo, hi));
        gimple_seq_add_stmt(&seq, gimple_build_assign(xs, COND_EXPR, ok, xd, real_cst(0.0)));
        gimple_seq_add_stmt(&seq, gimple_build_assign(t, FIX_TRUNC_EXPR, xs));
        gimple_seq_add_stmt(&seq, gimple_build_assign(r, COND_EXPR, ok, t, TYPE_MIN_VALUE(st)));
        gsi_insert_seq_before(&gsi, seq, GSI_SAME_STMT);
        gimple_assign_set_rhs_with_ops(&gsi, NOP_EXPR, r);
        n++;
      }
    }
    total += n;
    if (verbose && n)
      inform(DECL_SOURCE_LOCATION(fun->decl), "%<fnvnx_ftoi%>: %u floating-point to integer conversions with MSVC semantics", n);
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
  if (verbose) inform(UNKNOWN_LOCATION, "%<fnvnx_ftoi%>: %lu conversions in this unit", total);
  FILE *f = fopen(outpath.c_str(), "w");
  if (!f) { error("%<fnvnx_ftoi%>: cannot write %s", outpath.c_str()); return; }
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
    error("%<fnvnx_ftoi%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  const char *mode = NULL;
  for (int i = 0; i < info->argc; i++)
    if (!strcmp(info->argv[i].key, "verbose")) verbose = true;
    else if (!strcmp(info->argv[i].key, "mode") && info->argv[i].value) mode = info->argv[i].value;
    else if (!strcmp(info->argv[i].key, "out") && info->argv[i].value) outpath = info->argv[i].value;
    else {
      error("%<fnvnx_ftoi%>: unknown plugin argument %qs", info->argv[i].key);
      return 1;
    }
  if (!mode || (strcmp(mode, "native") && strcmp(mode, "lifted"))) {
    error("%<fnvnx_ftoi%>: the plugin argument %<mode=native|lifted%> is required");
    return 1;
  }
  lifted = !strcmp(mode, "lifted");
  if (outpath.empty()) { error("%<fnvnx_ftoi%>: the plugin argument %<out=FILE%> is required"); return 1; }
  struct register_pass_info pass;
  pass.pass = new ftoi_pass(g);
  pass.reference_pass_name = "cfg";
  pass.ref_pass_instance_number = 1;
  pass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pass);
  register_callback(info->base_name, PLUGIN_ATTRIBUTES, reg_attrs, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  return 0;
}
