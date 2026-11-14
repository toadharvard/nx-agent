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
// AArch64 target builtins (native mode): a call of a SIGNED float -> integer builtin of GCC's own table (f2i_builtins.inc, see md_row) is
// rewritten lane by lane, the same ok/select shape on vectors: lanes whose rounded value fits the lane's integer type keep the builtin's own
// conversion, NaN and out-of-range lanes give INT_MIN of the lane (x86 cvttps2dq/cvtps2dq "integer indefinite"; proof at bounds_of). Unsigned
// result builtins have no SSE2 counterpart and stay findings.
//
// One plugin, one class (C, float -> integer), two modes (-fplugin-arg-fnvnx_ftoi-mode=native|lifted, required: the recipe passes it
// from the root's cxx_origin, so there is no free choice; a missing or other value is a compile error):
//   native   the rewrite above (and the table-driven builtin rewrite). A function carrying the attribute fnvnx_f2i (registered here, functions only; the rt headers spell it
//            FNVL_F2I) is a conversion helper: its body is the one place where a conversion is written with its own semantics, so
//            it is not rewritten and is counted as a helper.
//   lifted   no rewrite: lifted code emits its conversions itself, inside helpers. Every conversion outside an fnvnx_f2i function is a
//            hard error, of three kinds:
//              conv      a GIMPLE assign with FIX_TRUNC_EXPR (scalar or vector: every cast, implicit conversion and compound
//                        assignment float -> integer, and __builtin_convertvector, which GCC lowers to it);
//              builtin   a call of a conversion builtin of the lround/lrint/llround/llrint/iround/irint/{i,l,ll}{ceil,floor} families
//                        (all three float widths), or IFN_VEC_CONVERT with a float operand and an integer result;
//              mdbuiltin a target (BUILT_IN_MD) builtin with a float-typed argument and an integer-typed (scalar or vector) result:
//                        the machine's own conversion (saturating, NaN gives 0), not the semantics of a C type;
//              asm       an inline asm that could read a float: a float, vector or complex-typed operand or an FP/SIMD register
//                        clobber (asm_may_read_float).
//            Types and GCC's function codes decide; no name, no text of source or assembly is looked at.
// Report (-fplugin-arg-fnvnx_ftoi-out=FILE, required, both modes), JSON "fnvnx-f2i/2" for gate fpexact: unit (main input file); sites (every
// site seen in every analysed function, helpers included: conversions, builtins, mdbuiltins and asm), rewritten (sites rewritten here), helper
// (sites inside fnvnx_f2i functions), helpers (their printable names), ranged (always empty: every site is rewritten, none is left on a GCC range
// fact), rewrites and findings [{function, file, line, kind}] (what is outside a helper and not rewritten: in lifted mode the errors, in native
// mode a site the rewrite does not cover, which is also a compile error), all sorted; sites == rewritten + len(ranged) + helper + len(findings).
// kind: conv-scalar | conv-vector (FIX_TRUNC_EXPR by its operand; IFN_VEC_CONVERT is conv-vector) | builtin | mdbuiltin | asm. The extra fields of a
// rewrite ("how": what/rounding, "code": the AArch64 function code of a target builtin) are for the human reader.
#include <vector>
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
#include "target.h"
#include "output.h"
#include "config/aarch64/aarch64-protos.h"
#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <string>
#include <tuple>

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

static bool verbose;
static bool lifted;
static unsigned long total;
static std::string outpath;
static unsigned long sites, helper_sites;
static std::set<std::string> helpers;
static std::vector<std::tuple<std::string, std::string, int, std::string>> findings;   // function, file, line, report kind (one per site: a site is counted once)

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

// ---- float -> integer conversions that C does not fully specify (native mode) -----------------------------------------------------------
// RULE. For a conversion  r = CONV(y)  with y = x * 2^n (n = 0 unless a fixed-point builtin names it) of a float type F to an integer type I
// of P bits (signed or unsigned, scalar or lane by lane), where CONV rounds with a rounding R (trunc, nearest-away, ceil, floor, nearest-even or
// the current mode):
//    R(y) in [Imin, Imax]  ->  r = R(y), the builtin's or the operator's own conversion;
//    otherwise, and for NaN  ->  r = the x86 integer indefinite: the bit pattern 0x80..0 of P bits (cvttps2dq, cvtps2dq, cvttsd2si; MSVC's _ftol).
// The code is `ok = in_range(x); xs = ok ? x : 0; t = CONV(xs); r = ok ? t : INDEFINITE`, lanes by VEC_COND_EXPR on vectors. A lane of x that is
// out of range or NaN is replaced by 0 before CONV (so CONV never sees a value on which the machine and x86 differ) and its result is replaced
// after it. in_range is EXACT for every type, by this argument: let M = 2^(P-1) (signed) or 2^P (unsigned), L = -M or 0 (Imin), so Imax = M-1; for
// the rounding R the set of y with R(y) in [L, M-1] is
//    trunc  (L-1, M)         floor  [L, M)          ceil  (L-1, M-1]        away  (L-1/2, M-1/2)        even  [L-1/2, M-1/2)
// (an interval of the reals: R is monotone, R(y) >= L iff y is >= the least y with that value, and the half-integer cases follow the tie rule:
// away rounds +-1/2 away from zero, even rounds to the even neighbour, L and M-1... are checked: L is even or 0, M-1 is odd, M is even). The bounds
// scaled by 2^-n are dyadic reals; a bound c that F cannot hold is replaced by the neighbour of c in F in the inside direction (x > c is x >= the
// least F value above c; x < c is x <= the greatest F value below c; both by real_convert and real_nextafter on the exact REAL_VALUE_TYPE, which
// has more than 64 significand bits, so 2^64-1/2 is exact in it). NaN fails every comparison, an infinity fails the one it is beyond. For the
// current rounding mode (lrint, irint, llrint: native code does not change it, but nothing here relies on that) the four IEEE modes give the same
// bounds when F is narrow enough for P (f32 -> 32 or 64 bits, f64 -> 64 bits: then they are the representable integers' exact bounds), and the
// rewrite uses them; otherwise the 64-bit llrint family is called in place of the call (its bounds are the same in every mode) and the 64-bit
// result is tested against the P-bit range, which is exact in any mode. A site not covered (mixed lane widths, a non-constant fixed-point shift,
// a target builtin that converts with no rounding in the table) is a compile error; nothing is passed through. Fixed point (fcvtz*, n fraction bits,
// a constant or a run-time value in 0..64): the tested value is y = x * 2^n; a multiplication by a power of two is exact in F unless it overflows
// to infinity (then y is out of range, as the true value is) and n >= 0 never underflows, so the predicates above apply to y; the builtin itself
// still converts the unscaled in-range lane with its own n.
// frint followed by vcvt is covered by construction: the frint is float -> float and the vcvt after it is rewritten like any other.
//
// THE CLASS. Scalar FIX_TRUNC_EXPR has the existing MSVC rewrite below. Everything else is `classify`:
//   vector FIX_TRUNC_EXPR, IFN_VEC_CONVERT     truncation;
//   BUILT_IN_NORMAL                            the lrint/llrint/irint (current mode), lround/llround/iround (away), {i,l,ll}ceil, {i,l,ll}floor families;
//   BUILT_IN_MD (AArch64)                      GCC's own table of target builtins: f2i_builtins.inc, generated by gen_f2i_builtins.py from gcc-13.3.0
//       gcc/config/aarch64/aarch64-simd-builtins.def (every row of the conversion patterns l<btrunc|round|ceil|floor|frintn>, fix[uns]_trunc,
//       fcvtz[su], each with its .def line), aarch64-builtins.cc (the registered name) and iterators.md (the rounding, the modes). At the first
//       use the plugin walks the builtin decls the backend registered (targetm.builtin_decl over the general class's codes, aarch64-builtins.cc:2026),
//       takes each decl's DECL_MD_FUNCTION_CODE and keys the table by it: a call is in the class iff its callee's code is a row's code. Nothing of
//       the user's text is read. Every other BUILT_IN_MD call with a float argument and an integer result (conv_builtin) is a compile error.
enum Rnd { RND_TRUNC, RND_AWAY, RND_CEIL, RND_FLOOR, RND_EVEN, RND_CURRENT };
static const char *const rnd_name[] = {"trunc", "away", "ceil", "floor", "even", "current"};
struct F2iRow { const char *name; Rnd rnd; bool uns; bool shift; int line; };
static const F2iRow f2i_rows[] = {
#include "f2i_builtins.inc"
};
static std::map<unsigned, const F2iRow *> f2i_by_code;
static bool f2i_resolved;
static unsigned long f2i_registered;

static void f2i_resolve() {
  if (f2i_resolved) return;
  f2i_resolved = true;
  std::map<std::string, const F2iRow *> byname;
  for (const F2iRow &r : f2i_rows) byname[r.name] = &r;
  for (unsigned n = 0;; n++) {
    tree d = targetm.builtin_decl((n << AARCH64_BUILTIN_SHIFT) | AARCH64_BUILTIN_GENERAL, true);
    if (d == error_mark_node) break;
    if (!d || TREE_CODE(d) != FUNCTION_DECL || !DECL_NAME(d)) continue;
    auto it = byname.find(IDENTIFIER_POINTER(DECL_NAME(d)));
    if (it != byname.end()) { f2i_by_code[DECL_MD_FUNCTION_CODE(d)] = it->second; f2i_registered++; }
  }
}

struct Conv {
  Rnd rnd;
  tree shift;              // fixed-point fraction bits (fcvtz*): the call's second operand, a constant or a run-time value; else NULL_TREE
  const char *what;        // for the report: "vector", "vec_convert", "normal", "md"
  unsigned code;           // DECL_MD_FUNCTION_CODE (md) or DECL_FUNCTION_CODE (normal), else 0
  bool uns_row;            // an md row with an unsigned result (the lane type must agree)
};

enum Cls { CLS_NONE, CLS_OK, CLS_ERROR };

// What the statement is, for the rewrite: CLS_OK with X (the float operand, the call's or the assignment's), CV filled; CLS_ERROR for a member of the
// class the table cannot map (a message in WHY); CLS_NONE for anything else (scalar FIX_TRUNC_EXPR, asm, other calls).
static Cls classify(gimple *stmt, tree *x, tree *lhs, Conv *cv, const char **why) {
  if (is_gimple_assign(stmt)) {
    if (gimple_assign_rhs_code(stmt) != FIX_TRUNC_EXPR || !VECTOR_TYPE_P(TREE_TYPE(gimple_assign_rhs1(stmt)))) return CLS_NONE;
    *x = gimple_assign_rhs1(stmt); *lhs = gimple_assign_lhs(stmt);
    *cv = {RND_TRUNC, NULL_TREE, "vector", 0, false};
    return CLS_OK;
  }
  gcall *c = dyn_cast<gcall *>(stmt);
  if (!c || !gimple_call_lhs(c) || !conv_builtin(c)) return CLS_NONE;
  *lhs = gimple_call_lhs(c);
  if (gimple_call_num_args(c) < 1) { *why = "a conversion builtin without an operand"; return CLS_ERROR; }
  *x = gimple_call_arg(c, 0);
  if (gimple_call_internal_p(c)) {
    if (gimple_call_internal_fn(c) != IFN_VEC_CONVERT) return CLS_NONE;
    *cv = {RND_TRUNC, NULL_TREE, "vec_convert", 0, false};
    return CLS_OK;
  }
  tree fn = gimple_call_fndecl(c);
  if (!fn) return CLS_NONE;
  if (DECL_BUILT_IN_CLASS(fn) == BUILT_IN_NORMAL) {
    Rnd r;
    switch (DECL_FUNCTION_CODE(fn)) {
      CASE_FLT_FN(BUILT_IN_IROUND): CASE_FLT_FN(BUILT_IN_LROUND): CASE_FLT_FN(BUILT_IN_LLROUND): r = RND_AWAY; break;
      CASE_FLT_FN(BUILT_IN_IRINT): CASE_FLT_FN(BUILT_IN_LRINT): CASE_FLT_FN(BUILT_IN_LLRINT): r = RND_CURRENT; break;
      CASE_FLT_FN(BUILT_IN_ICEIL): CASE_FLT_FN(BUILT_IN_LCEIL): CASE_FLT_FN(BUILT_IN_LLCEIL): r = RND_CEIL; break;
      CASE_FLT_FN(BUILT_IN_IFLOOR): CASE_FLT_FN(BUILT_IN_LFLOOR): CASE_FLT_FN(BUILT_IN_LLFLOOR): r = RND_FLOOR; break;
      default: *why = "a conversion builtin of a family the rewrite does not know"; return CLS_ERROR;
    }
    *cv = {r, NULL_TREE, "normal", (unsigned)DECL_FUNCTION_CODE(fn), false};
    return CLS_OK;
  }
  if (DECL_BUILT_IN_CLASS(fn) != BUILT_IN_MD) return CLS_NONE;
  f2i_resolve();
  auto it = f2i_by_code.find(DECL_MD_FUNCTION_CODE(fn));
  if (it == f2i_by_code.end()) { *why = "a target builtin with a float operand and an integer result that GCC's conversion table does not list"; return CLS_ERROR; }
  const F2iRow *row = it->second;
  tree sh = NULL_TREE;
  if (row->shift) {
    sh = gimple_call_num_args(c) == 2 ? gimple_call_arg(c, 1) : NULL_TREE;
    if (!sh || !INTEGRAL_TYPE_P(TREE_TYPE(sh))) { *why = "a fixed-point conversion without an integer fraction-bits operand"; return CLS_ERROR; }
  } else if (gimple_call_num_args(c) != 1) { *why = "a conversion builtin with an unexpected operand count"; return CLS_ERROR; }
  *cv = {row->rnd, sh, "md", (unsigned)DECL_MD_FUNCTION_CODE(fn), row->uns};
  return CLS_OK;
}

struct Pred { tree_code lo_op, hi_op; tree lo, hi; };

// y > c (incl false) or y >= c (incl true), exactly, for the values of the float type FE, in ONE canonical form `y >= s` with s the least value of FE
// that satisfies it (so equal sets give equal constants): s = c rounded to FE if c is in FE and incl, else the least FE value above c.
static void lower_pred(tree fe, const REAL_VALUE_TYPE &c, bool incl, tree_code *op, tree *cst) {
  REAL_VALUE_TYPE rc, n;
  real_convert(&rc, TYPE_MODE(fe), &c);
  bool exact = real_equal(&rc, &c);
  if (!(exact && incl) && !real_less(&c, &rc)) { real_nextafter(&n, TYPE_MODE(fe), &rc, &dconstinf); rc = n; }   // rc is <= c: the next value up
  *op = GE_EXPR;
  *cst = build_real(fe, rc);
}

// y < c (incl false) or y <= c (incl true): canonical `y <= s`, s the greatest value of FE that satisfies it
static void upper_pred(tree fe, const REAL_VALUE_TYPE &c, bool incl, tree_code *op, tree *cst) {
  REAL_VALUE_TYPE rc, n, ninf = real_value_negate(&dconstinf);
  real_convert(&rc, TYPE_MODE(fe), &c);
  bool exact = real_equal(&rc, &c);
  if (!(exact && incl) && !real_less(&rc, &c)) { real_nextafter(&n, TYPE_MODE(fe), &rc, &ninf); rc = n; }   // rc is >= c: the next value down
  *op = LE_EXPR;
  *cst = build_real(fe, rc);
}

// the exact in-range predicate of rounding R for P-bit integers (signed or unsigned) on the operand y (the caller scales x by 2^n)
static Pred pred_of(tree fe, unsigned P, bool uns, Rnd r) {
  REAL_VALUE_TYPE M, L, one = dconst1, half = dconsthalf, lc, hc;
  real_2expN(&M, uns ? P : P - 1, DFmode);
  if (uns) L = dconst0; else L = real_value_negate(&M);
  bool lincl, hincl;
  switch (r) {
    case RND_TRUNC: real_arithmetic(&lc, MINUS_EXPR, &L, &one); lincl = false; hc = M; hincl = false; break;
    case RND_FLOOR: lc = L; lincl = true; hc = M; hincl = false; break;
    case RND_CEIL: real_arithmetic(&lc, MINUS_EXPR, &L, &one); lincl = false; real_arithmetic(&hc, MINUS_EXPR, &M, &one); hincl = true; break;
    case RND_AWAY: real_arithmetic(&lc, MINUS_EXPR, &L, &half); lincl = false; real_arithmetic(&hc, MINUS_EXPR, &M, &half); hincl = false; break;
    default: real_arithmetic(&lc, MINUS_EXPR, &L, &half); lincl = true; real_arithmetic(&hc, MINUS_EXPR, &M, &half); hincl = false; break;   // RND_EVEN
  }
  Pred p;
  lower_pred(fe, lc, lincl, &p.lo_op, &p.lo);
  upper_pred(fe, hc, hincl, &p.hi_op, &p.hi);
  return p;
}

static bool same_pred(const Pred &a, const Pred &b) {
  return a.lo_op == b.lo_op && a.hi_op == b.hi_op && real_identical(&TREE_REAL_CST(a.lo), &TREE_REAL_CST(b.lo)) && real_identical(&TREE_REAL_CST(a.hi), &TREE_REAL_CST(b.hi));
}

static tree sel_const(tree it, unsigned P, bool vec) {   // the indefinite: bit P-1 set, as a constant of the (scalar or vector) integer type IT
  tree ie = vec ? TREE_TYPE(it) : it;
  tree c = wide_int_to_tree(ie, wi::set_bit_in_zero(P - 1, TYPE_PRECISION(ie)));
  return vec ? build_vector_from_val(it, c) : c;
}

static std::vector<std::tuple<std::string, std::string, int, std::string, std::string, unsigned>> rewritten;   // function, file, line, kind, "what/round", code

// rewrite the statement STMT (at GSI) per the RULE; false if the site is not covered
static bool rewrite(gimple_stmt_iterator *gsi, gimple *stmt, tree x, tree lhs, const Conv &cv) {
  tree ft = TREE_TYPE(x), it = TREE_TYPE(lhs);
  bool vec = VECTOR_TYPE_P(ft);
  if (vec != VECTOR_TYPE_P(it)) return false;
  if (vec && maybe_ne(TYPE_VECTOR_SUBPARTS(ft), TYPE_VECTOR_SUBPARTS(it))) return false;
  tree fe = vec ? TREE_TYPE(ft) : ft, ie = vec ? TREE_TYPE(it) : it;
  if (!SCALAR_FLOAT_TYPE_P(fe) || TREE_CODE(ie) != INTEGER_TYPE || TYPE_PRECISION(ie) > 64) return false;
  if (vec && !tree_int_cst_equal(TYPE_SIZE(fe), TYPE_SIZE(ie))) return false;   // mixed lane widths need a mask conversion: not covered
  if (!strcmp(cv.what, "md") && cv.uns_row != TYPE_UNSIGNED(ie)) return false;   // an md row's signedness is the lane type's
  unsigned P = TYPE_PRECISION(ie);
  bool uns = TYPE_UNSIGNED(ie);
  Pred pr;
  bool wide = false;
  if (cv.rnd == RND_CURRENT) {
    Pred a = pred_of(fe, P, uns, RND_EVEN), b = pred_of(fe, P, uns, RND_TRUNC),
         c = pred_of(fe, P, uns, RND_FLOOR), d = pred_of(fe, P, uns, RND_CEIL);
    if (same_pred(a, b) && same_pred(a, c) && same_pred(a, d)) pr = a;
    else {
      // the modes disagree at the edge: test the 64-bit llrint result against the P-bit range (see the RULE); vectors and fixed point have no such form
      if (vec || cv.shift || P >= 64 || strcmp(cv.what, "normal")) return false;
      Pred a6 = pred_of(fe, 64, false, RND_EVEN), b6 = pred_of(fe, 64, false, RND_TRUNC), c6 = pred_of(fe, 64, false, RND_FLOOR), d6 = pred_of(fe, 64, false, RND_CEIL);
      if (!(same_pred(a6, b6) && same_pred(a6, c6) && same_pred(a6, d6))) return false;
      pr = a6; wide = true;
    }
  } else pr = pred_of(fe, P, uns, cv.rnd);
  tree mt = vec ? truth_type_for(ft) : boolean_type_node;
  tree zero = build_zero_cst(ft);
  tree clo = pr.lo, chi = pr.hi;
  if (vec) { clo = build_vector_from_val(ft, clo); chi = build_vector_from_val(ft, chi); }
  tree lo = create_tmp_reg(mt, "ftoi_lo"), hi = create_tmp_reg(mt, "ftoi_hi"), ok = create_tmp_reg(mt, "ftoi_ok");
  tree xs = create_tmp_reg(ft, "ftoi_x");
  tree_code sel = vec ? VEC_COND_EXPR : COND_EXPR;
  gimple_seq pre = NULL, post = NULL;
  tree y = x;   // the value that is rounded: x * 2^n for the fixed-point builtins (an exact scaling or an overflow to infinity, which is out of range)
  if (cv.shift) {
    tree scale;
    if (TREE_CODE(cv.shift) == INTEGER_CST) {
      if (!tree_fits_uhwi_p(cv.shift) || tree_to_uhwi(cv.shift) > 64 || (TYPE_PRECISION(fe) < 32 && tree_to_uhwi(cv.shift) > 14)) return false;
      REAL_VALUE_TYPE two_n;
      real_2expN(&two_n, tree_to_uhwi(cv.shift), TYPE_MODE(fe));
      scale = build_real(fe, two_n);
    } else {
      // 2^n = 2^(n - n/2) * 2^(n/2), each an exact conversion of a power of two below 2^64
      if (TYPE_PRECISION(fe) < 32) return false;
      tree ull = long_long_unsigned_type_node, one = build_int_cst(ull, 1);
      tree nn = create_tmp_reg(ull, "ftoi_n"), h = create_tmp_reg(ull, "ftoi_h"), r1 = create_tmp_reg(ull, "ftoi_r1");
      tree sa = create_tmp_reg(ull, "ftoi_sa"), sb = create_tmp_reg(ull, "ftoi_sb"), fa = create_tmp_reg(fe, "ftoi_fa"), fb = create_tmp_reg(fe, "ftoi_fb");
      scale = create_tmp_reg(fe, "ftoi_scale");
      gimple_seq_add_stmt(&pre, gimple_build_assign(nn, NOP_EXPR, cv.shift));
      gimple_seq_add_stmt(&pre, gimple_build_assign(h, RSHIFT_EXPR, nn, build_int_cst(ull, 1)));
      gimple_seq_add_stmt(&pre, gimple_build_assign(r1, MINUS_EXPR, nn, h));
      gimple_seq_add_stmt(&pre, gimple_build_assign(sa, LSHIFT_EXPR, one, r1));
      gimple_seq_add_stmt(&pre, gimple_build_assign(sb, LSHIFT_EXPR, one, h));
      gimple_seq_add_stmt(&pre, gimple_build_assign(fa, FLOAT_EXPR, sa));
      gimple_seq_add_stmt(&pre, gimple_build_assign(fb, FLOAT_EXPR, sb));
      gimple_seq_add_stmt(&pre, gimple_build_assign(scale, MULT_EXPR, fa, fb));
    }
    tree sv = scale;
    if (vec) {
      if (TREE_CODE(scale) == REAL_CST) sv = build_vector_from_val(ft, scale);
      else { sv = create_tmp_reg(ft, "ftoi_sv"); gimple_seq_add_stmt(&pre, gimple_build_assign(sv, build_vector_from_val(ft, scale))); }
    }
    y = create_tmp_reg(ft, "ftoi_y");
    gimple_seq_add_stmt(&pre, gimple_build_assign(y, MULT_EXPR, x, sv));
  }
  gimple_seq_add_stmt(&pre, gimple_build_assign(lo, pr.lo_op, y, clo));
  gimple_seq_add_stmt(&pre, gimple_build_assign(hi, pr.hi_op, y, chi));
  gimple_seq_add_stmt(&pre, gimple_build_assign(ok, BIT_AND_EXPR, lo, hi));
  gimple_seq_add_stmt(&pre, gimple_build_assign(xs, sel, ok, x, zero));
  tree res = create_tmp_reg(it, "ftoi_r"), t;
  if (!wide) {
    t = create_tmp_reg(it, "ftoi_t");
    gsi_insert_seq_before(gsi, pre, GSI_SAME_STMT);
    if (is_gimple_assign(stmt)) { gimple_assign_set_rhs1(stmt, xs); gimple_assign_set_lhs(stmt, t); }
    else { gimple_call_set_arg(as_a<gcall *>(stmt), 0, xs); gimple_call_set_lhs(as_a<gcall *>(stmt), t); }
    gimple_seq_add_stmt(&post, gimple_build_assign(res, sel, ok, t, sel_const(it, P, vec)));
    gimple_seq_add_stmt(&post, gimple_build_assign(lhs, res));
    gsi_insert_seq_after(gsi, post, GSI_NEW_STMT);
  } else {
    // r = (it) t64 when ok and t64 fits P bits, else the indefinite; t64 = llrint(xs), the same rounding as the original call in any mode
    tree fn64 = NULL_TREE;
    tree mv = TYPE_MAIN_VARIANT(fe);
    if (mv == float_type_node) fn64 = builtin_decl_explicit(BUILT_IN_LLRINTF);
    else if (mv == double_type_node) fn64 = builtin_decl_explicit(BUILT_IN_LLRINT);
    else if (mv == long_double_type_node) fn64 = builtin_decl_explicit(BUILT_IN_LLRINTL);
    if (!fn64) return false;
    tree t64 = create_tmp_reg(long_long_integer_type_node, "ftoi_t64");
    tree f1 = create_tmp_reg(boolean_type_node, "ftoi_f1"), f2 = create_tmp_reg(boolean_type_node, "ftoi_f2"), fit = create_tmp_reg(boolean_type_node, "ftoi_fit");
    tree ok2 = create_tmp_reg(boolean_type_node, "ftoi_ok2"), nar = create_tmp_reg(it, "ftoi_n");
    gimple_seq_add_stmt(&pre, gimple_build_call(fn64, 1, xs)); gimple_call_set_lhs(gimple_seq_last_stmt(pre), t64);
    gimple_seq_add_stmt(&pre, gimple_build_assign(f1, GE_EXPR, t64, build_int_cst(long_long_integer_type_node, -((long long)1 << (P - 1)))));
    gimple_seq_add_stmt(&pre, gimple_build_assign(f2, LE_EXPR, t64, build_int_cst(long_long_integer_type_node, ((long long)1 << (P - 1)) - 1)));
    gimple_seq_add_stmt(&pre, gimple_build_assign(fit, BIT_AND_EXPR, f1, f2));
    gimple_seq_add_stmt(&pre, gimple_build_assign(ok2, BIT_AND_EXPR, ok, fit));
    gimple_seq_add_stmt(&pre, gimple_build_assign(nar, NOP_EXPR, t64));
    gimple_seq_add_stmt(&pre, gimple_build_assign(res, COND_EXPR, ok2, nar, sel_const(it, P, false)));
    gsi_insert_seq_before(gsi, pre, GSI_SAME_STMT);
    gsi_replace(gsi, gimple_build_assign(lhs, res), false);   // the original call is gone: llrint answers in its place
  }
  return true;
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
        else if (gcall *c = dyn_cast<gcall *>(stmt)) {
          // kind by GCC's builtin class: the generic families (BUILT_IN_NORMAL, IFN_VEC_CONVERT) have the semantics of their C type; a
          // target builtin (BUILT_IN_MD, for example the NEON vcvtq_s32_f32) has the machine's own (saturating, NaN to 0), which is not x86's
          if (conv_builtin(c)) {
            tree cf = gimple_call_fndecl(c);
            kind = (cf && fndecl_built_in_p(cf) && DECL_BUILT_IN_CLASS(cf) == BUILT_IN_MD) ? "mdbuiltin" : "builtin";
            conv = true;
          }
        }
        else if (gasm *as = dyn_cast<gasm *>(stmt)) { if (asm_may_read_float(as)) kind = "asm"; }
        if (!kind) continue;
        // the report kind of the site (fnvnx-f2i/2): a conversion is scalar or vector by its operand, a builtin by its class
        const char *rk = kind;
        if (!strcmp(kind, "conv")) rk = VECTOR_TYPE_P(TREE_TYPE(gimple_assign_rhs1(stmt))) ? "conv-vector" : "conv-scalar";
        else if (gcall *vc = dyn_cast<gcall *>(stmt)) { if (gimple_call_internal_p(vc) && gimple_call_internal_fn(vc) == IFN_VEC_CONVERT) rk = "conv-vector"; }
        sites++;   // every site: conversions, builtins and asm; sites == rewritten + ranged + helper + findings
        if (helper) { helper_sites++; continue; }
        if (lifted) {
          expanded_location l = expand_location(gimple_location(stmt));
          findings.push_back({name, l.file ? l.file : "", l.line, rk});
          error_at(gimple_location(stmt), "FTOI (lifted): a float -> integer conversion (%s) outside a function carrying fnvnx_f2i", kind);
          continue;
        }
        {
          tree cx = NULL_TREE, clhs = NULL_TREE;
          Conv cv;
          const char *why = NULL;
          Cls cls = strcmp(kind, "asm") ? classify(stmt, &cx, &clhs, &cv, &why) : CLS_NONE;
          expanded_location l = expand_location(gimple_location(stmt));
          if (cls == CLS_OK) {
            if (rewrite(&gsi, stmt, cx, clhs, cv)) {
              rewritten.push_back({name, l.file ? l.file : "", l.line, rk, std::string(cv.what) + "/" + rnd_name[cv.rnd], cv.code});
              n++;
              continue;
            }
            why = "a conversion of a shape the rewrite does not cover (mixed lane widths, a type wider than 64 bits, no exact form)";
            cls = CLS_ERROR;
          }
          if (cls == CLS_ERROR) {
            findings.push_back({name, l.file ? l.file : "", l.line, rk});
            error_at(gimple_location(stmt), "FTOI: %s", why);
            continue;
          }
        }
        if (strcmp(kind, "conv")) {   // native: the other builtins and asm are not rewritten
          expanded_location l = expand_location(gimple_location(stmt));
          findings.push_back({name, l.file ? l.file : "", l.line, rk});
          // __builtin_convertvector is a vector float -> integer conversion (IFN_VEC_CONVERT at this point of the pipeline): the same
          // shape as a vector FIX_TRUNC_EXPR, which the rewrite does not cover, so rule (c) makes it a compile error too.
          if (gcall *c = dyn_cast<gcall *>(stmt))
            if (gimple_call_internal_p(c) && gimple_call_internal_fn(c) == IFN_VEC_CONVERT)
              error_at(gimple_location(stmt), "FTOI: vector conversion %qT to %qT is not covered by the MSVC rewrite",
                       TREE_TYPE(gimple_call_arg(c, 0)), TREE_TYPE(gimple_call_lhs(c)));
          continue;
        }
        tree lhs = gimple_assign_lhs(stmt), x = gimple_assign_rhs1(stmt);
        tree it = TREE_TYPE(lhs), ft = TREE_TYPE(x);
        // RULE (c): every FIX_TRUNC_EXPR of a function is rewritten here, or inside an fnvnx_f2i helper, or a compile error. A shape the
        // rewrite does not cover (a vector, a floating type wider than 64 bits, an integer type wider than 64 bits) names its types and fails.
        if ((TREE_CODE(it) != INTEGER_TYPE && TREE_CODE(it) != ENUMERAL_TYPE) || !SCALAR_FLOAT_TYPE_P(ft) || TYPE_PRECISION(ft) > 64
            || TYPE_PRECISION(it) > 64) {
          expanded_location l = expand_location(gimple_location(stmt));
          findings.push_back({name, l.file ? l.file : "", l.line, VECTOR_TYPE_P(it) ? "conv-vector" : "conv-scalar"});
          error_at(gimple_location(stmt), "FTOI: conversion %qT to %qT is not covered by the MSVC rewrite (vector, or a type wider than 64 bits)", ft, it);
          continue;
        }
        unsigned p = TYPE_PRECISION(it);
        // A floating value converted straight to an enum is not the exe's language: MSVC 2008 rejects it (C2440,
        // "conversions between enumeration and floating point values are no longer allowed"; tests/s12 ftoi_enum
        // has the compiler's own answer), so the exe's source named an integer type in between and that type chose
        // the conversion. A text that omits it does not say which, and nothing here picks one.
        if (TREE_CODE(it) == ENUMERAL_TYPE) {
          { expanded_location l = expand_location(gimple_location(stmt)); findings.push_back({name, l.file ? l.file : "", l.line, rk}); }
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
        { expanded_location l = expand_location(gimple_location(stmt)); rewritten.push_back({name, l.file ? l.file : "", l.line, rk, "msvc/ftol", 0}); }
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
  std::sort(findings.begin(), findings.end());
  std::sort(rewritten.begin(), rewritten.end());
  fprintf(f, "{\"findings\": [");
  bool first = true;
  for (auto &t : findings) {
    fprintf(f, "%s{\"file\": ", first ? "" : ", "); js(f, std::get<1>(t));
    fprintf(f, ", \"function\": "); js(f, std::get<0>(t));
    fprintf(f, ", \"kind\": "); js(f, std::get<3>(t));
    fprintf(f, ", \"line\": %d}", std::get<2>(t));
    first = false;
  }
  fprintf(f, "], \"format\": \"fnvnx-f2i/2\", \"helper\": %lu, \"helpers\": [", helper_sites);
  first = true;
  for (auto &h : helpers) { if (!first) fprintf(f, ", "); js(f, h); first = false; }
  fprintf(f, "], \"ranged\": [], \"rewrites\": [");
  first = true;
  for (auto &t : rewritten) {
    fprintf(f, "%s{\"code\": %u, \"file\": ", first ? "" : ", ", std::get<5>(t)); js(f, std::get<1>(t));
    fprintf(f, ", \"function\": "); js(f, std::get<0>(t));
    fprintf(f, ", \"how\": "); js(f, std::get<4>(t));
    fprintf(f, ", \"kind\": "); js(f, std::get<3>(t));
    fprintf(f, ", \"line\": %d}", std::get<2>(t));
    first = false;
  }
  fprintf(f, "], \"rewritten\": %zu, \"sites\": %lu, \"unit\": ", rewritten.size(), sites);
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
