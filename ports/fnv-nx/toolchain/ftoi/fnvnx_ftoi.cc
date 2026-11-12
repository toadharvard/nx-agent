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

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

static bool verbose;
static unsigned long total;

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
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun) {
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *stmt = gsi_stmt(gsi);
        if (!is_gimple_assign(stmt) || gimple_assign_rhs_code(stmt) != FIX_TRUNC_EXPR) continue;
        tree lhs = gimple_assign_lhs(stmt), x = gimple_assign_rhs1(stmt);
        tree it = TREE_TYPE(lhs), ft = TREE_TYPE(x);
        // RULE: every FIX_TRUNC_EXPR of the function is rewritten here or is a compile error, so a unit that this plugin compiled
        // holds no unbounded float -> integer conversion (the load of the plugin is the proof, gate fpexact). A shape the rewrite does
        // not cover (a vector, a floating type wider than 64 bits, an integer type wider than 64 bits) names its types and fails.
        if (TREE_CODE(it) != INTEGER_TYPE && TREE_CODE(it) != ENUMERAL_TYPE) {
          error_at(gimple_location(stmt), "FTOI: conversion %qT to %qT is not covered by the MSVC rewrite", ft, it);
          continue;
        }
        if (!SCALAR_FLOAT_TYPE_P(ft) || TYPE_PRECISION(ft) > 64) {
          error_at(gimple_location(stmt), "FTOI: conversion %qT to %qT is not covered by the MSVC rewrite (source wider than 64 bits)", ft, it);
          continue;
        }
        unsigned p = TYPE_PRECISION(it);
        if (p > 64) {
          error_at(gimple_location(stmt), "FTOI: conversion %qT to %qT is not covered by the MSVC rewrite (target wider than 64 bits)", ft, it);
          continue;
        }
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

static void finish(void *, void *) {
  if (verbose) inform(UNKNOWN_LOCATION, "%<fnvnx_ftoi%>: %lu conversions in this unit", total);
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version) {
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_ftoi%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  for (int i = 0; i < info->argc; i++)
    if (!strcmp(info->argv[i].key, "verbose")) verbose = true;
    else {
      error("%<fnvnx_ftoi%>: unknown plugin argument %qs", info->argv[i].key);
      return 1;
    }
  struct register_pass_info pass;
  pass.pass = new ftoi_pass(g);
  pass.reference_pass_name = "cfg";
  pass.ref_pass_instance_number = 1;
  pass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pass);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  return 0;
}
