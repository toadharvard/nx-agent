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
//                        clobber (asm_may_read_float). A cross-check only: nothing rests on this classification. Gate fpexact check 3c
//                        decodes the bytes of EVERY inline asm of the object (see asm_mark_pass), whatever its operands are.
//            Types and GCC's function codes decide; no name, no text of source or assembly is looked at.
// Report (-fplugin-arg-fnvnx_ftoi-out=FILE, required, both modes), JSON "fnvnx-f2i/1" for gate fpexact: unit (main input file), sites
// (conversion sites, conv and builtin, seen in every analysed function, helpers included), helpers (printable names of the analysed
// helpers), findings [{function, file, line, kind}] (what is outside a helper and is not rewritten: in lifted mode the errors, in native
// mode the builtin and asm kinds, and a conv the rewrite does not cover), both sorted; asm (accounting for check 3c: functions
// {assembler name: [GIMPLE asm at the first pass, GIMPLE asm given to the expander, asm insns right after expand, asm insns marked before
// final, asm insns after final without markers]}, toplevel (top-level asm statements of the unit; each has a range with function "<toplevel>")).
// Besides the report the plugin writes the section .fnvnx.asmrange into the object (see asm_mark_pass).
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
#include "rtl.h"
#include "memmodel.h"
#include "emit-rtl.h"
#include "cgraph.h"
#include "varasm.h"
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <vector>

int plugin_is_GPL_compatible;   // the plugin interface asks for it; the code is MIT (LICENSE), which is compatible

static bool verbose;
static bool lifted;
static unsigned long total;
static std::string outpath;
static unsigned long sites;
static std::set<std::string> helpers;
static std::set<std::tuple<std::string, std::string, int, std::string>> findings;   // function, file, line, kind

// Asm accounting (gate fpexact check 3c). Every inline asm that reaches `final` is bracketed by two zero-byte markers (see asm_mark_pass) and
// recorded in the non-alloc section .fnvnx.asmrange; the counts below are the compiler's own, per function (assembler name).
struct asm_count { unsigned early = 0, expand = 0, rtl0 = 0, marked = 0, unrecorded = 0; };
static std::map<std::string, asm_count> asm_counts;
typedef std::tuple<std::string, int, int> locus_t;   // file, line, column of the asm statement (GIMPLE location, carried by the insn)
struct asm_row { std::string func, b, e; locus_t loc; };
static std::vector<asm_row> asm_rows;
static std::set<int> marker_names;   // the labels this plugin created: any asm insn at final without them around it is unrecorded
static std::string fn_asm_name(function *fun) { return IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fun->decl)); }
static locus_t locus_of(location_t l) { expanded_location x = expand_location(l); return locus_t(x.file ? x.file : "", x.line, x.column); }

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
// extended-asm contract an asm reads only the operands it is given, so FP/SIMD state it was not given has no defined value for it
// (ASM-001: the pinned compiler is correct). This test only classifies a statement for the early finding; it decides nothing about
// what an asm contains. The bytes of EVERY inline asm are decoded by gate fpexact check 3c, through the ranges this plugin records.
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
        if (is_a<gasm *>(stmt)) asm_counts[fn_asm_name(fun)].early++;
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


// ---- Asm accounting: GIMPLE counts, markers around every asm insn that reaches final, the check after final ----
static bool asm_insn_p(rtx_insn *insn) {
  if (!NONDEBUG_INSN_P(insn)) return false;
  rtx b = PATTERN(insn);
  return asm_noperands(b) >= 0 || GET_CODE(b) == ASM_INPUT;   // ASM_OPERANDS (alone, in a SET or a PARALLEL), asm goto (a jump insn) and basic asm
}

// Before expand: the asm statements of the function body the back end is given (inlining, cloning, unrolling and DCE are done).
static const pass_data asm_gimple_data = {GIMPLE_PASS, "fnvnx_asm_gimple", OPTGROUP_NONE, TV_NONE, PROP_cfg, 0, 0, 0, 0};
struct asm_gimple_pass : gimple_opt_pass {
  asm_gimple_pass(gcc::context *ctx) : gimple_opt_pass(asm_gimple_data, ctx) {}
  unsigned int execute(function *fun) final override {
    asm_count &c = asm_counts[fn_asm_name(fun)];
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi))
        if (is_a<gasm *>(gsi_stmt(gsi))) c.expand++;
    return 0;
  }
};

// Right after expand: the asm insns the expander produced from those statements.
static const pass_data asm_rtl0_data = {RTL_PASS, "fnvnx_asm_rtl0", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0};
struct asm_rtl0_pass : rtl_opt_pass {
  asm_rtl0_pass(gcc::context *ctx) : rtl_opt_pass(asm_rtl0_data, ctx) {}
  unsigned int execute(function *fun) final override {
    asm_count &c = asm_counts[fn_asm_name(fun)];
    for (rtx_insn *i = get_insns(); i; i = NEXT_INSN(i))
      if (asm_insn_p(i)) c.rtl0++;
    return 0;
  }
};

// Immediately before `final`: after sched2, block reordering and every pass that moves code, so no instruction is changed. Each asm insn gets
// a zero-byte marker before and after it: a NOTE_INSN_DELETED_LABEL with a fresh label number, which final prints as the assembler-local label `.L<number>:`
// and which no alignment or branch-shortening code looks at (they look at CODE_LABELs). The pair is recorded in .fnvnx.asmrange.
static std::string new_marker(rtx_insn *at, bool before) {
  int num = CODE_LABEL_NUMBER(gen_label_rtx());   // a fresh label number: final prints the note as `.L<num>:`, so it cannot collide with another label
  std::string name = ".L" + std::to_string(num);
  // emit_note refuses to create this kind (it is made only by patching out an insn), so the note is made as a plain NOTE_INSN_DELETED and given
  // the kind and the label number of a deleted label: the same state delete_insn leaves a named label in.
  rtx_insn *n = before ? emit_note_before(NOTE_INSN_DELETED, at) : emit_note_after(NOTE_INSN_DELETED, at);
  NOTE_KIND(n) = NOTE_INSN_DELETED_LABEL;
  CODE_LABEL_NUMBER(n) = num;
  marker_names.insert(num);
  return name;
}
static const pass_data asm_mark_data = {RTL_PASS, "fnvnx_asm_mark", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0};
struct asm_mark_pass : rtl_opt_pass {
  asm_mark_pass(gcc::context *ctx) : rtl_opt_pass(asm_mark_data, ctx) {}
  unsigned int execute(function *fun) final override {
    std::string fn = fn_asm_name(fun);
    asm_count &c = asm_counts[fn];
    std::vector<rtx_insn *> todo;
    for (rtx_insn *i = get_insns(); i; i = NEXT_INSN(i))
      if (asm_insn_p(i)) todo.push_back(i);
    for (rtx_insn *i : todo) {
      std::string b = new_marker(i, true), e = new_marker(i, false);
      locus_t loc = locus_of(INSN_LOCATION(i));
      asm_rows.push_back({fn, b, e, loc});
      c.marked++;
    }
    return 0;
  }
};

// After final: every asm insn that is still in the stream must have its marker notes next to it. One that has none reached final unrecorded.
static const pass_data asm_verify_data = {RTL_PASS, "fnvnx_asm_verify", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0};
struct asm_verify_pass : rtl_opt_pass {
  asm_verify_pass(gcc::context *ctx) : rtl_opt_pass(asm_verify_data, ctx) {}
  static bool marker(rtx_insn *i, bool before) {
    for (; i; i = before ? PREV_INSN(i) : NEXT_INSN(i)) {
      if (NOTE_P(i) && NOTE_KIND(i) == NOTE_INSN_DELETED_LABEL) return marker_names.count(CODE_LABEL_NUMBER(i)) != 0;
      if (!NOTE_P(i)) return false;   // another insn first (a CODE_LABEL, a barrier, an instruction): no marker adjacent
    }
    return false;
  }
  unsigned int execute(function *fun) final override {
    asm_count &c = asm_counts[fn_asm_name(fun)];
    for (rtx_insn *i = get_insns(); i; i = NEXT_INSN(i))
      if (asm_insn_p(i) && !(marker(PREV_INSN(i), true) && marker(NEXT_INSN(i), false))) c.unrecorded++;
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

static unsigned toplevel;   // top-level asm statements of the unit
// Every top-level asm statement is bracketed like an asm insn: its text becomes `begin label, original text, end label` (assembler-local labels in the section
// the text starts and ends in; a text that ends in another section than it began leaves a range the gate cannot resolve: a finding), and the range is recorded in
// .fnvnx.asmrange with the function name "<toplevel>". Done at ALL_IPA_PASSES_START: the nodes are dropped when the unit is finished, but not yet output.
static void count_toplevel(void *, void *) {
  for (asm_node *a = symtab->first_asm_symbol(); a; a = a->next) {
    std::string b = ".Lfnvnx_tl" + std::to_string(toplevel) + "b", e = ".Lfnvnx_tl" + std::to_string(toplevel) + "e";
    std::string text = b + ":\n" + TREE_STRING_POINTER(a->asm_str) + "\n" + e + ":";
    a->asm_str = build_string(text.size(), text.c_str());
    asm_rows.push_back({"<toplevel>", b, e, std::make_tuple(std::string(main_input_filename ? main_input_filename : ""), 0, 0)});
    toplevel++;
  }
}

static void finish(void *, void *) {
  if (verbose) inform(UNKNOWN_LOCATION, "%<fnvnx_ftoi%>: %lu conversions in this unit", total);
  // .fnvnx.asmrange: one entry per asm insn that reached final: .long BEGIN, .long END (relocated against the section the code is in), .long line,
  // .long column, function assembler name, file (NUL-terminated), padded to 4. Always written, empty when the unit has no asm insn.
  fprintf(asm_out_file, "\t.pushsection .fnvnx.asmrange,\"\",%%progbits\n");
  for (auto &r : asm_rows) {
    fprintf(asm_out_file, "\t.long %s\n\t.long %s\n\t.long %d\n\t.long %d\n\t.asciz \"", r.b.c_str(), r.e.c_str(), std::get<1>(r.loc), std::get<2>(r.loc));
    for (unsigned char ch : r.func) { if (ch == '"' || ch == '\\') fputc('\\', asm_out_file); fputc(ch, asm_out_file); }
    fprintf(asm_out_file, "\"\n\t.asciz \"");
    for (unsigned char ch : std::get<0>(r.loc)) { if (ch == '"' || ch == '\\') fputc('\\', asm_out_file); fputc(ch, asm_out_file); }
    fprintf(asm_out_file, "\"\n\t.p2align 2\n");
  }
  fprintf(asm_out_file, "\t.popsection\n");
  FILE *f = fopen(outpath.c_str(), "w");
  if (!f) { error("%<fnvnx_ftoi%>: cannot write %s", outpath.c_str()); return; }
  fprintf(f, "{\"asm\": {\"functions\": {");
  bool af = true;
  for (auto &kv : asm_counts) {
    fprintf(f, "%s", af ? "" : ", "); js(f, kv.first);
    fprintf(f, ": [%u, %u, %u, %u, %u]", kv.second.early, kv.second.expand, kv.second.rtl0, kv.second.marked, kv.second.unrecorded);
    af = false;
  }
  fprintf(f, "}, \"toplevel\": %u}, \"findings\": [", toplevel);
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
  struct register_pass_info pg, p0, pm, pv;
  pg.pass = new asm_gimple_pass(g); pg.reference_pass_name = "optimized"; pg.ref_pass_instance_number = 1; pg.pos_op = PASS_POS_INSERT_AFTER;
  p0.pass = new asm_rtl0_pass(g); p0.reference_pass_name = "vregs"; p0.ref_pass_instance_number = 1; p0.pos_op = PASS_POS_INSERT_BEFORE;
  pm.pass = new asm_mark_pass(g); pm.reference_pass_name = "final"; pm.ref_pass_instance_number = 1; pm.pos_op = PASS_POS_INSERT_BEFORE;
  pv.pass = new asm_verify_pass(g); pv.reference_pass_name = "final"; pv.ref_pass_instance_number = 1; pv.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pg);
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &p0);
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pm);
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pv);
  register_callback(info->base_name, PLUGIN_ATTRIBUTES, reg_attrs, NULL);
  register_callback(info->base_name, PLUGIN_ALL_IPA_PASSES_START, count_toplevel, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  return 0;
}
