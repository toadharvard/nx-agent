/* fnvnx_dispatch - DISPATCH1: every indirect call of native code goes through "exe code address -> guest entry".
 *
 * Code pointer values in the image stay the exe's addresses (vftable slots, function pointers in data, pointers to
 * member functions, callbacks): they are data of the exe and are never rewritten. So a value called through a
 * pointer is an exe address, not something the processor can jump to. Just before RTL expansion, when the calls of
 * the function are final, every call whose target is not a named function becomes
 *
 *     static fnvnx_site_t site;                    one per call site, in the unit's data, 0 at start; the record the plugin builds
 *                                                  (never looked up by name): { unsigned int fp; <native entry type> entry; }, size 8, alignment 8
 *     r = site;                                    ONE 64-bit acquire load of the whole record (`ldar x`): fp = the exe address last called here,
 *     entry = r.fp == (unsigned) callee            entry = its native entry (pointers are 32 bits)
 *                 ? r.entry : fnvnx_dispatch(callee, &site, desc);
 *     ((call's own function-pointer type) entry)(arguments...)   a pointer-to-function conversion; no integer becomes an entry or back
 *
 * fnvnx_dispatch(exe code address, site, descriptor) is the runtime's (L3 LIFT): it returns the entry (type fnvnx_entry_fn, ENTRY1), which is the
 * call site's own AAPCS signature - a native body, or a bridge into a lifted one - and stores the site's record in one 64-bit release store, so a
 * reader never sees the key of one target with the entry of another. An address that is no entry does not return. The prototype and the record
 * are declared by platform/include/fnvl_host.h; the plugin's own `.fnvnx.decltype` row of the prototype is compared with the header's by the
 * layout gate. The hit path is a load, a compare, a branch and the call.
 *
 * A bridge is one function per descriptor, so it is not told which exe address it stands for. The call sequence
 * therefore stores the address in the thread-local word `fnvnx_call_fp` (shim/include/fnvl_host.h) just before the call, on
 * the hit path and the miss path alike; a bridge reads it first thing, a native body ignores it.
 *
 * The descriptor is the call's shape for a bridge, a string `conv:ret:arg,arg,...`:
 *   conv   this   the first argument is the object, in ecx on x86 (a member function, MSVC's default)
 *                 (the object is listed among the arguments, first)
 *          fast   __fastcall: the first two integer arguments in ecx, edx
 *          stack  everything on the stack (__cdecl and __stdcall alike: the callee's own `ret N` tells them apart)
 *   ret    v | i (32-bit integer or pointer) | q (64-bit integer) | f | d | an aggregate  a<N><X><Y>
 *            the x86 callee and the native (AAPCS64) body choose how they pass the result independently, and the descriptor carries both choices:
 *          X  R  the x86 callee returns it in registers (eax / edx:eax): msvc_pod holds and N is 1, 2, 4 or 8 (Microsoft x86 convention; Agner Fog,
 *                Calling conventions, table 7)
 *             H  the x86 callee returns it through the hidden pointer on the stack
 *          Y  g  the native body returns it in general registers (x0, x1)
 *             h<elt>  in floating-point registers, a homogeneous aggregate, elt = f d q h v8 v16 as the backend's part mode says
 *             m  in memory the caller provides (x8): targetm.calls.function_value / aggregate_value_p of the pinned compiler
 *          The four combinations are the four bridge actions: R/g,h copy the registers; H/m pass the pointer through; H/g,h store the native registers
 *          through the x86 pointer; R/m pass a temporary through x8 and load eax:edx from it.
 *   BNF    ret := "v" | "i" | "q" | "f" | "d" | "a" N X Y ;  X := "R" | "H" ;  Y := "g" | "m" | "h" elt ;  N := digit+
 *   arg    i | q | f | d | s<N>g (an N-byte aggregate in general registers or on the stack) | s<N>h<elt> (a
 *          homogeneous aggregate) | s<N>r (passed as a reference to a copy)
 * (A homogeneous aggregate names its element type: {float, float} and {double} are both 8 bytes and travel in
 * different registers, so the size alone does not say how a bridge receives or returns it.)
 *
 * The descriptors of a unit are the objects' own fact: section .fnvnx.disp (not loaded), one text line per distinct
 * descriptor of the unit. lift/tools/bridgegen.py reads it from every object of every admitted root.
 * The convention is the declaration's own: CC1 keeps MSVC's __cdecl / __stdcall / __fastcall / __thiscall as the
 * type attribute fnvnx_cc (shim/include/fnvnx_compat.h), which the fnvnx_names plugin registers.
 *
 * Every indirect call is rewritten, except a host-ABI call (HOST1, below); there is no list of exceptions. A call the
 * pass cannot rewrite or describe is an error.
 *
 * HOST1: a host address is not an exe code address. The platform's host API (FnvnxHostApi) holds the addresses of the
 * host's functions in slots; their type is `fnvnx_hslot`, a uint64_t that carries the type attribute `fnvnx_host`
 * (registered here). They are not keys of the dispatcher: calling one through fnvnx_dispatch would be a violation of
 * the dispatcher's domain. So, in the function, at the last GIMPLE:
 *   1. An indirect call is a host-ABI call iff its callee value comes, through conversions only (NOP, CONVERT,
 *      VIEW_CONVERT, a plain copy), from a load of a field whose type carries fnvnx_host. It is not rewritten.
 *   2. Containment, which closes the class: it is an error if a value loaded from such a field, or any conversion
 *      of it, is used other than as the callee of an indirect call (a store, an argument, a return, a comparison,
 *      arithmetic, a PHI, an asm operand, a switch); if anything is stored into such a field; if the address of such a
 *      field is taken; and if an aggregate that contains such a field is copied, loaded, stored, passed or returned
 *      by value (its slots would travel as plain data). So a host address never reaches fnvnx_dispatch, and the
 *      callee of a host-ABI call is always a value the host wrote.
 *   3. CONT-P, at PLUGIN_PRE_GENERICIZE (conversions of C and C++ are still explicit there, as NOP / CONVERT /
 *      VIEW_CONVERT): a conversion from a pointer (or reference) to a record that holds an fnvnx_host field,
 *      recursively through members and arrays, to an integer or to a pointer (reference) to another type is an
 *      error; qualifier changes and a conversion to the same record are not. An opaque callee (memcpy, memset,
 *      a `void *` parameter) cannot receive the table, and a callee that takes a `const FnvnxHostApi *` is guest
 *      code, built with this plugin, so its loads are checked by 2.
 * The unit's section `.fnvnx.dispatch` (not loaded) has one line `symbol<TAB>rewritten<TAB>host` for every function with
 * an indirect call: the number of call sites rewritten and the number of host-ABI calls. Each host-ABI call has one more line
 * `symbol<TAB>host<TAB><record tag>.<field>`: the slot field the callee was loaded from (for a slot array, the array member), in the
 * spelling of .fnvnx.decltype (the record `struct:TAG{...}` and the slot's type prefixed `host:`). Rows are sorted; a call per row.
 */
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "context.h"
#include "tree.h"
#include "tree-pass.h"
#include "function.h"
#include "basic-block.h"
#include "gimple-expr.h"
#include "gimple.h"
#include "gimple-iterator.h"
#include "ssa.h"
#include "cp/cp-tree.h"
#include "langhooks.h"
/* ONE .so SERVES BOTH FRONT ENDS (rule CC1PLUS-SYMS, as in fnvnx_names.cc): what only cc1plus defines is weak, null under cc1, and used only behind
 * fnv_cxx_fe(). This plugin reads the C++ front end's class declarations for one thing, msvc_pod; DECL_OVERLOADED_OPERATOR_IS reads ovl_op_info. */
#define FNVNX_CXX_ONLY(sym) extern __typeof__(sym) sym __attribute__((weak))
FNVNX_CXX_ONLY(ovl_op_info);
static inline bool fnvnx_cxx_fe() { return !strncmp(lang_hooks.name, "GNU C++", 7); }
#include "tree-ssanames.h"
#include "tree-phinodes.h"
#include "cfghooks.h"
#include "tree-cfg.h"
#include "cgraph.h"
#include "stringpool.h"
#include "stor-layout.h"
#include "diagnostic-core.h"
#include "tree-into-ssa.h"
#include "dominance.h"
#include "cfgloop.h"
#include "attribs.h"
#include "memmodel.h"
#include "builtins.h"
#include "rtl.h"
#include "target.h"
#include "tm_p.h"            /* the backend's own prototypes (aarch64_init_cumulative_args, behind INIT_CUMULATIVE_ARGS) */
#include "calls.h"
#include "output.h"
#include "gimple-walk.h"
#include "ssa-iterators.h"
#include "tree-dfa.h"
#include <algorithm>
#include <map>
#include "langhooks.h"
#include <string.h>
#include <set>
#include <string>
#include <vector>

int plugin_is_GPL_compatible;

static tree dispatch_decl;
static tree entry_generic, site_record;
static unsigned site_count;
static bool fe_declares_dispatch;                   /* a declaration of fnvnx_dispatch in this unit: the layout plugin spells it */

/* The site record, built here and never looked up by name:
 *     struct fnvnx_site_t { uint32_t fp; void (*entry)(void) /+ fnvnx_entry/; }   size 8, alignment 8
 * `entry` is the generic native entry type (ENTRY1): the call target is a conversion of it to the call's own
 * function-pointer type (pointer to function to pointer to function), and no integer ever becomes an entry or back. */
static tree get_entry_generic()
{
  if (!entry_generic) {
    tree pt = build_pointer_type(build_function_type_list(void_type_node, NULL_TREE));
    entry_generic = build_type_attribute_variant(pt, tree_cons(get_identifier("fnvnx_entry"), NULL_TREE, NULL_TREE));
  }
  return entry_generic;
}
static tree site_field(tree rec, const char *name, tree type)
{
  tree f = build_decl(UNKNOWN_LOCATION, FIELD_DECL, get_identifier(name), type);
  DECL_CONTEXT(f) = rec;
  return f;
}
static tree get_site_record()
{
  if (!site_record) {
    tree rec = make_node(RECORD_TYPE);
    tree f_fp = site_field(rec, "fp", unsigned_type_node), f_entry = site_field(rec, "entry", get_entry_generic());
    DECL_CHAIN(f_fp) = f_entry;
    TYPE_FIELDS(rec) = f_fp;
    SET_TYPE_ALIGN(rec, 64); TYPE_USER_ALIGN(rec) = 1;
    TYPE_NAME(rec) = get_identifier("fnvnx_site");   /* the C tag, as the header spells it */
    layout_type(rec);
    site_record = rec;
  }
  return site_record;
}
static tree site_f_fp() { return TYPE_FIELDS(get_site_record()); }
static tree site_f_entry() { return DECL_CHAIN(TYPE_FIELDS(get_site_record())); }

/* fnvnx_dispatch(fp, &site, descriptor) -> the entry, typed */
static tree get_dispatch()
{
  if (!dispatch_decl) {
    tree t = build_function_type_list(get_entry_generic(), ptr_type_node, build_pointer_type(get_site_record()), const_ptr_type_node, NULL_TREE);
    dispatch_decl = build_fn_decl("fnvnx_dispatch", t);
    DECL_EXTERNAL(dispatch_decl) = 1; TREE_PUBLIC(dispatch_decl) = 1; DECL_ARTIFICIAL(dispatch_decl) = 1;
    TREE_NOTHROW(dispatch_decl) = 1;
  }
  return dispatch_decl;
}

/* The exe address of the call in progress, for a bridge: a bridge is one function per descriptor and gets only the
 * call's own arguments, so the target travels in this thread-local word, which the call sequence stores
 * immediately before the call (the platform defines it, platform/win32/thread.cpp; declared by shim/include/fnvl_host.h). */
static tree fp_var;
static tree get_fp_var()
{
  if (!fp_var) {
    fp_var = build_decl(UNKNOWN_LOCATION, VAR_DECL, get_identifier("fnvnx_call_fp"), unsigned_type_node);
    DECL_EXTERNAL(fp_var) = 1; TREE_PUBLIC(fp_var) = 1; DECL_ARTIFICIAL(fp_var) = 1; TREE_USED(fp_var) = 1;
    set_decl_tls_model(fp_var, TLS_MODEL_INITIAL_EXEC);
    varpool_node::get_create(fp_var);
  }
  return fp_var;
}

static tree new_site()
{
  char name[64];
  snprintf(name, sizeof name, "fnvnx_site.%u", site_count++);
  tree v = build_decl(UNKNOWN_LOCATION, VAR_DECL, get_identifier(name), get_site_record());
  TREE_STATIC(v) = 1; TREE_PUBLIC(v) = 0; DECL_ARTIFICIAL(v) = 1; DECL_IGNORED_P(v) = 1; TREE_USED(v) = 1;
  TREE_ADDRESSABLE(v) = 1; DECL_CONTEXT(v) = NULL_TREE;
  DECL_INITIAL(v) = NULL_TREE;                       /* .bss: 0, and no exe code address is 0 */
  layout_decl(v, 0);
  varpool_node::finalize_decl(v);
  return v;
}

/* ---- the call's shape */

/* Is `t` returned through the hidden pointer by the x86 callee? The statement is the convention's own ("Structures that are not PODs will not be
 * returned in registers", learn.microsoft.com, Argument Passing and Naming Conventions; the C++03 aggregate of ISO/IEC 14882:2003 8.5.1 and 9/4 that
 * VC9 uses; Agner Fog, Calling conventions, table 7), and the VC 2008 reading of it that cl 15.00.30729.01 shows on all 245 cases of
 * lift/test/clpod (table.tsv, a MEASUREMENT; the table verifies the rule, it does not define it) splits the properties in two:
 *   own (the returned class only): a user-declared constructor (copy constructor included), a base class, a private or protected non-static
 *                                             data member (the class is not an aggregate);
 *   propagated through members (the front end's own class flags, named at the use): a non-trivial copy assignment, a non-trivial destructor,
 *                                             a vptr, a reference member.
 * Read from the C++ front end's own declarations of the class; every C++ front-end symbol used is declared weak at the top of this file and sits
 * behind fnvnx_cxx_fe(). A C unit has no such type: its aggregate is a POD by the language. */
static bool msvc_pod(tree t)
{
  if (!fnvnx_cxx_fe()) return true;
  while (TREE_CODE(t) == ARRAY_TYPE) t = TREE_TYPE(t);
  if (!CLASS_TYPE_P(t)) return true;
  if (TYPE_HAS_USER_CONSTRUCTOR(t)) return false;                                      /* a user constructor, copy constructor included */
  if (TYPE_BINFO(t) && BINFO_N_BASE_BINFOS(TYPE_BINFO(t)) > 0) return false;           /* a base class */
  for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
    if (TREE_CODE(f) == FIELD_DECL && (TREE_PRIVATE(f) || TREE_PROTECTED(f))) return false;   /* not an aggregate */
  /* the front end propagates these through the members itself (finish_struct): TYPE_HAS_COMPLEX_COPY_ASSIGN (a user or non-trivial copy assignment,
   * also implied by a member), TYPE_HAS_NONTRIVIAL_DESTRUCTOR, TYPE_POLYMORPHIC_P (a vptr), CLASSTYPE_REF_FIELDS_NEED_INIT (a reference member) */
  return !(TYPE_HAS_COMPLEX_COPY_ASSIGN(t) || TYPE_HAS_NONTRIVIAL_DESTRUCTOR(t) || TYPE_POLYMORPHIC_P(t) || CLASSTYPE_REF_FIELDS_NEED_INIT(t));
}

/* How the target passes a value is asked of the pinned compiler's own backend, never classified here (ASM-001): pass_by_reference, then
 * targetm.calls.function_arg on a fresh cumulative state (the argument is the first of the call), and for a result targetm.calls.function_value.
 * The rtx says where the value lives: a REG or a PARALLEL of vector registers is a homogeneous aggregate, and the part mode names the element;
 * general registers, or no register (the stack), are `g`. A part mode that has no element code is an error: no guess. */
static bool elem_code(machine_mode m, std::string &c)
{
  if (m == SFmode) c = "hf"; else if (m == DFmode) c = "hd"; else if (m == TFmode) c = "hq"; else if (m == HFmode) c = "hh";
  else if (VECTOR_MODE_P(m) && GET_MODE_SIZE(m).to_constant() == 8) c = "hv8";
  else if (VECTOR_MODE_P(m) && GET_MODE_SIZE(m).to_constant() == 16) c = "hv16";
  else return false;
  return true;
}

/* "" (general registers or stack), or the element code of the vector registers rtx `r` lives in; false: a mode with no code */
static std::string why_detail;                       /* the detail of the last shape failure (a mode name, a bit count): wording only */
static bool reg_class(rtx r, std::string &c)
{
  c = "";
  if (!r) return true;
  rtx first = NULL_RTX;
  if (GET_CODE(r) == PARALLEL) {
    for (int i = 0; i < XVECLEN(r, 0); i++) { rtx e = XVECEXP(r, 0, i); if (GET_CODE(e) == EXPR_LIST) e = XEXP(e, 0); if (e && REG_P(e)) { first = e; break; } }
  } else if (REG_P(r)) first = r;
  if (!first || !FP_REGNUM_P(REGNO(first))) return true;
  if (!elem_code(GET_MODE(first), c)) { why_detail = GET_MODE_NAME(GET_MODE(first)); return false; }
  return true;
}

static std::set<std::string> descs;                  /* every descriptor this unit uses: .fnvnx.disp */

static const char *conv_of(tree ft);

/* shape(): NULL when the type has a descriptor shape (appended to OUT), else the REASON it has none: i128, f<bits> (f128, f16), noshape, nosize,
 * fastnonpod, vreg. Whether a missing shape is an error is the caller's question: at an indirect call site it is (fnvnx_dispatch receives the
 * descriptor); a definition only needs one when it is joined to an exe address (exitgen), so it records the reason (`-<reason>`). */
static std::string reason_buf;                       /* the reason string of a float width: f<bits> (f128, f16) */
static const char *shape(tree t, bool ret, tree fntype, std::string &out)
{
  why_detail.clear();
  if (VOID_TYPE_P(t)) { out += "v"; return ret ? NULL : "noshape"; }
  if (INTEGRAL_TYPE_P(t) || POINTER_TYPE_P(t) || TREE_CODE(t) == OFFSET_TYPE || TREE_CODE(t) == NULLPTR_TYPE) {
    unsigned p = TREE_CODE(t) == OFFSET_TYPE ? POINTER_SIZE : TYPE_PRECISION(t);
    if (p > 64) { why_detail = std::to_string(p); return "i128"; }
    out += p > 32 ? "q" : "i"; return NULL;
  }
  if (SCALAR_FLOAT_TYPE_P(t)) {
    if (TYPE_PRECISION(t) != 32 && TYPE_PRECISION(t) != 64) { why_detail = std::to_string((int)TYPE_PRECISION(t)); reason_buf = "f" + why_detail; return reason_buf.c_str(); }
    out += TYPE_PRECISION(t) == 32 ? "f" : "d"; return NULL;
  }
  if (!AGGREGATE_TYPE_P(t) && TREE_CODE(t) != COMPLEX_TYPE && TREE_CODE(t) != VECTOR_TYPE) return "noshape";
  if (!TYPE_SIZE_UNIT(t) || !tree_fits_uhwi_p(TYPE_SIZE_UNIT(t))) return "nosize";
  std::string n = std::to_string((unsigned long)tree_to_uhwi(TYPE_SIZE_UNIT(t)));
  machine_mode mode = TYPE_MODE(t);
  std::string he;
  if (ret) {
    bool nat_mem = aggregate_value_p(t, fntype);
    if (!nat_mem && !reg_class(targetm.calls.function_value(t, fntype, false), he)) return "vreg";
    unsigned long sz = (unsigned long)tree_to_uhwi(TYPE_SIZE_UNIT(t));
    bool pod = !(TREE_CODE(t) == RECORD_TYPE || TREE_CODE(t) == UNION_TYPE) || msvc_pod(t);
    bool x86_reg = pod && (sz == 1 || sz == 2 || sz == 4 || sz == 8);
    /* a hidden pointer for a non-POD under __fastcall is in a register that no document names (Agner Fog, table 7): no descriptor, no guess */
    if (!x86_reg && !pod && !strcmp(conv_of(fntype), "fast")) return "fastnonpod";
    out += "a" + n + (x86_reg ? "R" : "H") + (nat_mem ? "m" : he.empty() ? "g" : he); return NULL;
  }
  CUMULATIVE_ARGS cum;
  INIT_CUMULATIVE_ARGS(cum, fntype, NULL_RTX, NULL_TREE, 0);
  cumulative_args_t pack = pack_cumulative_args(&cum);
  function_arg_info arg(t, mode, true);
  if (TREE_ADDRESSABLE(t) || targetm.calls.pass_by_reference(pack, arg)) { out += "s" + n + "r"; return NULL; }
  if (!reg_class(targetm.calls.function_arg(pack, arg), he)) return "vreg";
  out += "s" + n + (he.empty() ? "g" : he);
  return NULL;
}

/* the error of an indirect call site whose type has no shape (the call site is where the descriptor is needed) */
static void shape_error(const char *why, tree t, location_t loc)
{
  if (!strcmp(why, "i128")) error_at(loc, "DISPATCH1: a %s-bit integer in an indirect call", why_detail.c_str());
  else if (why[0] == 'f' && why[1] != 'a') error_at(loc, "DISPATCH1: a floating type of %s bits in an indirect call", why_detail.c_str());
  else if (!strcmp(why, "noshape")) error_at(loc, "DISPATCH1: type %qT in an indirect call has no shape", t);
  else if (!strcmp(why, "nosize")) error_at(loc, "DISPATCH1: type %qT has no constant size", t);
  else if (!strcmp(why, "fastnonpod")) error_at(loc, "DISPATCH1: type %qT is a non-POD result under __fastcall: the register of the hidden pointer is not documented", t);
  else error_at(loc, "DISPATCH1: the backend passes a value in vector registers of mode %s, which has no element code", why_detail.c_str());
}

/* conv of a function type: the declaration's own (fnvnx_cc), METHOD_TYPE is this */
static const char *conv_of(tree ft)
{
  const char *conv = TREE_CODE(ft) == METHOD_TYPE ? "this" : "stack";
  if (tree a = lookup_attribute("fnvnx_cc", TYPE_ATTRIBUTES(ft))) {
    const char *v = TREE_STRING_POINTER(TREE_VALUE(TREE_VALUE(a)));
    conv = !strcmp(v, "fast") ? "fast" : !strcmp(v, "this") ? "this" : "stack";
  }
  return conv;
}

static void need_names_plugin()
{
  static bool checked;
  if (!checked) {
    checked = true;
    if (!lookup_attribute_spec(get_identifier("fnvnx_cc")))
      error("DISPATCH1: the fnvnx_names plugin is not loaded: the calling conventions of the declarations are lost");
  }
}

static tree descriptor(gcall *call)
{
  location_t loc = gimple_location(call);
  need_names_plugin();
  tree ft = gimple_call_fntype(call);
  if (!ft) { error_at(loc, "DISPATCH1: an indirect call without a function type"); return NULL_TREE; }
  std::string d = std::string(conv_of(ft)) + ":";
  if (const char *w = shape(TREE_TYPE(ft), true, ft, d)) { shape_error(w, TREE_TYPE(ft), loc); return NULL_TREE; }
  d += ":";
  /* A parameter's type is the function type's (a class that is passed as a reference to a copy is still the
   * class there: the exe takes it by value on its stack); an argument past the declared ones (`...`) has the type
   * of the value passed. */
  tree parm = TYPE_ARG_TYPES(ft);
  for (unsigned i = 0; i < gimple_call_num_args(call); i++) {
    if (i) d += ",";
    tree t = parm && parm != void_list_node ? TREE_VALUE(parm) : TREE_TYPE(gimple_call_arg(call, i));
    if (parm && parm != void_list_node) parm = TREE_CHAIN(parm);
    if (const char *w = shape(t, false, ft, d)) { shape_error(w, t, loc); return NULL_TREE; }
  }
  descs.insert(d);
  return build_string_literal(d.size() + 1, d.c_str());
}

/* The descriptor of a function DEFINITION, from its own type with the same shape() as the call sites (a native body
 * that lifted code reaches through an exit bridge). The parameters are the declared ones: the list is the
 * definition's own, `this` first for a method. A type with no shape gives the row `-<reason>` (DEFDISP-NS): no x86 caller can reach a body that has
 * no x86 shape, and exitgen, which joins a definition to its exe address, is red for a joined `-` row. */
static std::string def_descriptor(tree decl)
{
  tree ft = TREE_TYPE(decl);
  std::string d = std::string(conv_of(ft)) + ":";
  if (const char *w = shape(TREE_TYPE(ft), true, ft, d)) return std::string("-") + w;
  d += ":";
  bool first = true;
  for (tree parm = TYPE_ARG_TYPES(ft); parm && parm != void_list_node; parm = TREE_CHAIN(parm)) {
    if (!first) d += ",";
    first = false;
    if (const char *w = shape(TREE_VALUE(parm), false, ft, d)) return std::string("-") + w;
  }
  return d;
}

static void emit_lines(const char *section, const std::set<std::string> &lines)
{
  if (lines.empty()) return;
  fprintf(asm_out_file, "\t.pushsection\t%s,\"\",%%progbits\n", section);
  for (auto &d : lines) {
    fprintf(asm_out_file, "\t.ascii\t\"");
    for (char c : d) { if (c == '"' || c == '\\') fputc('\\', asm_out_file); fputc(c, asm_out_file); }
    fprintf(asm_out_file, "\\n\"\n");
  }
  fprintf(asm_out_file, "\t.popsection\n");
}

/* .fnvnx.defdisp: `<asm name> <descriptor>\n` per emitted definition (any linkage, clones under their own asm name), sorted, unique.
 * The exe address is not here: exitgen joins by asm name with .fnvnx.sympair and the bound exe VA. */
/* The row of the dispatcher's own prototype in `.fnvnx.decltype` (C units): the layout plugin spells what the front end declares, and this decl is
 * not one, so the plugin spells it with the SAME function (layout/decltype_spell.inc, dl_type). A unit that declares fnvnx_dispatch itself (the runtime,
 * through fnvl_host.h) already has its row from the layout plugin: the two rows are one function on two decls, and the layout gate compares them. */
#include "../layout/decltype_spell.inc"
#include "../names/plugsym.inc"

static void finish_unit(void *, void *)
{
  plugsym_writer_scope plugsym_scope;   /* RULE DECLTYPE-PLUGINSYM: reports on every way out */
  /* RULE DECLTYPE-PLUGINSYM: fnvnx_dispatch is a symbol this plugin makes (a unit that declares it has its row from the front end's declaration). With the layout
   * plugin loaded the registry gives it its line (a C unit: this prototype's row; a C++ unit: the reason `plugin`); without it (this plugin alone) the line is written here. */
  if (fp_var) {   /* the thread-local word of the call sequence (an artificial declaration of this plugin) */
    std::vector<tree> stk;
    plugsym_note("fnvnx_call_fp", dl_type(TREE_TYPE(fp_var), stk));
  }
  if (site_count && !fe_declares_dispatch) {
    std::vector<tree> stk;
    std::string ty = fnvnx_cxx_fe() ? "" : dl_type(TREE_TYPE(get_dispatch()), stk);
    if (fnvnx_plugsym().reader) plugsym_note("fnvnx_dispatch", ty);
    else if (!fnvnx_cxx_fe()) {
      std::set<std::string> row;
      row.insert("fnvnx_dispatch\t" + ty);
      emit_lines(".fnvnx.decltype", row);
    }
  }
  emit_lines(".fnvnx.disp", descs);
  need_names_plugin();
  std::set<std::string> defs;
  cgraph_node *node;
  FOR_EACH_DEFINED_FUNCTION(node) {
    tree decl = node->decl;
    if (!node->alias && !node->thunk && !TREE_ASM_WRITTEN(decl)) continue;
    std::string d = def_descriptor(decl);
    const char *name = targetm.strip_name_encoding(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(decl)));
    defs.insert(std::string(name) + " " + d);
  }
  emit_lines(".fnvnx.defdisp", defs);
}

static bool entry_type(tree t);

static bool indirect_call(gimple *st)
{
  if (!is_gimple_call(st) || gimple_call_internal_p(st)) return false;
  tree fn = gimple_call_fn(st);
  if (!fn) return false;
  if (entry_type(TREE_TYPE(fn))) return false;       /* ENTRY1: a call through a native entry address is no call of exe code */
  return !(TREE_CODE(fn) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(fn, 0)) == FUNCTION_DECL);
}

/* ---- HOST1: the host slot type, host-ABI calls and containment */

static tree handle_host(tree *node, tree name, tree args, int, bool *no_add_attrs)
{
  tree t = *node;
  if (TREE_CODE(t) != INTEGER_TYPE || !TYPE_UNSIGNED(t) || TYPE_PRECISION(t) != 64) {
    error("%qE applies to a 64-bit unsigned integer type (a slot of the host API holds a host address)", name);
    *no_add_attrs = true;
  }
  return NULL_TREE;
}
static struct attribute_spec host_attr = { "fnvnx_host", 0, 0, false, true, false, false, handle_host, NULL };

/* ENTRY1: `fnvnx_entry` marks a pointer-to-function type whose values are NATIVE entry addresses (what the dispatcher returns for an exe address: the
 * native function itself, which the call reaches directly). A call through such a value is not a call of exe code, so it is not rewritten and has no
 * descriptor; the type is closed under conversion (ENTRY1-CONT, below), so a value of it is only ever the address of a function, a load of entry-typed
 * storage, a call result of entry type or a copy of those. */
static tree handle_entry(tree *node, tree name, tree args, int, bool *no_add_attrs)
{
  tree t = *node;
  if (TREE_CODE(t) != POINTER_TYPE || (TREE_CODE(TREE_TYPE(t)) != FUNCTION_TYPE && TREE_CODE(TREE_TYPE(t)) != METHOD_TYPE)) {
    error("%qE applies to a pointer-to-function type (its values are native entry addresses)", name);
    *no_add_attrs = true;
  }
  return NULL_TREE;
}
static struct attribute_spec entry_attr = { "fnvnx_entry", 0, 0, false, true, false, true, handle_entry, NULL };
static void register_attrs(void *, void *) { register_attribute(&host_attr); register_attribute(&entry_attr); }

static bool entry_attr_on(tree t) { return t && TYPE_P(t) && lookup_attribute("fnvnx_entry", TYPE_ATTRIBUTES(t)); }
static bool entry_type(tree t)
{
  if (entry_attr_on(t)) return true;
  tree n = t && TYPE_P(t) ? TYPE_NAME(t) : NULL_TREE;
  return n && TREE_CODE(n) == TYPE_DECL && entry_attr_on(DECL_ORIGINAL_TYPE(n));
}


/* HOST1: the attribute is on the type itself or on the typedef that names it (`typedef unsigned long long __attribute__((fnvnx_host)) hslot`: GCC
 * keeps the typedef's own variant in DECL_ORIGINAL_TYPE of the TYPE_NAME, and the variant a field gets can be either of the two) */
static bool host_attr_on(tree t)
{
  return t && TYPE_P(t) && lookup_attribute("fnvnx_host", TYPE_ATTRIBUTES(t));
}
static bool host_type(tree t)
{
  if (host_attr_on(t)) return true;
  tree n = t && TYPE_P(t) ? TYPE_NAME(t) : NULL_TREE;
  return n && TREE_CODE(n) == TYPE_DECL && host_attr_on(DECL_ORIGINAL_TYPE(n));
}

/* a slot, or an array of slots (the array field's elements are slots) */
static bool host_elem_type(tree t)
{
  while (t && TREE_CODE(t) == ARRAY_TYPE) t = TREE_TYPE(t);
  return host_type(t);
}

/* a type that is, or holds by member or element, a marked type (a host slot, an entry pointer): one walker for both attributes */
static bool contains_marked(tree t, bool (*marked)(tree))
{
  if (!t || !TYPE_P(t)) return false;
  if (marked(t)) return true;
  if (TREE_CODE(t) == ARRAY_TYPE) return contains_marked(TREE_TYPE(t), marked);
  if (TREE_CODE(t) == RECORD_TYPE || TREE_CODE(t) == UNION_TYPE)
    for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
      if (TREE_CODE(f) == FIELD_DECL && contains_marked(TREE_TYPE(f), marked)) return true;
  return false;
}
static bool contains_host(tree t) { return contains_marked(t, host_type); }
static bool contains_entry(tree t) { return contains_marked(t, entry_type); }

/* ENTRY1-CONT: a value of type SRC (the expression OPERAND) flows into type DST (a conversion, an initialisation or assignment, a call argument, a result): an
 * entry type meets only itself; the address of a function is the one other source of an entry value. A result that is discarded or tested for truth moves no
 * value, so it is no flow. The front end keeps no conversion node between types it counts as one type: the flow sites are checked as well as the conversions. */
static std::set<tree> copy_args;                     /* the pointer conversions of a same-type builtin copy: accepted (entry_flow's CALL_EXPR case) */
static void entry_flow(tree src, tree dst, tree operand, location_t loc)
{
  if (!src || !dst || !(entry_type(src) || entry_type(dst))) return;
  tree o = operand;
  while (o && (CONVERT_EXPR_P(o) || TREE_CODE(o) == NON_LVALUE_EXPR)) o = TREE_OPERAND(o, 0);
  bool same = entry_type(src) && entry_type(dst) && TYPE_MAIN_VARIANT(TREE_TYPE(src)) == TYPE_MAIN_VARIANT(TREE_TYPE(dst));
  bool fnaddr = entry_type(dst) && !entry_type(src) && o && TREE_CODE(o) == ADDR_EXPR && TREE_CODE(TREE_OPERAND(o, 0)) == FUNCTION_DECL;
  bool discard = entry_type(src) && (VOID_TYPE_P(dst) || TREE_CODE(dst) == BOOLEAN_TYPE);
  if (!same && !fnaddr && !discard)
    error_at(loc, "ENTRY1-CONT: a conversion between %qT and %qT: a native entry type converts only to itself", src, dst);
}

/* CONT-P: the explicit conversions of a function body, walked before genericization */
static tree contp_walk(tree *tp, int *walk_subtrees, void *)
{
  tree e = *tp;
  if (!e) return NULL_TREE;
  if (TYPE_P(e)) { *walk_subtrees = 0; return NULL_TREE; }
  const enum tree_code c = TREE_CODE(e);
  if (c == DECL_EXPR && TREE_CODE(DECL_EXPR_DECL(e)) == VAR_DECL && DECL_INITIAL(DECL_EXPR_DECL(e)))
    entry_flow(TREE_TYPE(DECL_INITIAL(DECL_EXPR_DECL(e))), TREE_TYPE(DECL_EXPR_DECL(e)), DECL_INITIAL(DECL_EXPR_DECL(e)), EXPR_LOCATION(e));
  if (c == INIT_EXPR || c == MODIFY_EXPR) { entry_flow(TREE_TYPE(TREE_OPERAND(e, 1)), TREE_TYPE(TREE_OPERAND(e, 0)), TREE_OPERAND(e, 1), EXPR_LOCATION(e)); return NULL_TREE; }
  if (c == CALL_EXPR && CALL_EXPR_FN(e)) {
    /* __builtin_memcpy/memmove/mempcpy between storage of ONE type that holds entry values copies entry values: its two pointer conversions to `void *` are no flow */
    if (tree fd = get_callee_fndecl(e))
      if (fndecl_built_in_p(fd, BUILT_IN_NORMAL)) switch (DECL_FUNCTION_CODE(fd)) {
        case BUILT_IN_MEMCPY: case BUILT_IN_MEMMOVE: case BUILT_IN_MEMPCPY:
          if (call_expr_nargs(e) >= 2) {
            tree a0 = CALL_EXPR_ARG(e, 0), a1 = CALL_EXPR_ARG(e, 1), o0 = a0, o1 = a1;
            std::vector<tree> chain;
            for (tree *o : { &o0, &o1 }) while (CONVERT_EXPR_P(*o) || TREE_CODE(*o) == NON_LVALUE_EXPR) { chain.push_back(*o); *o = TREE_OPERAND(*o, 0); }
            tree t0 = TREE_TYPE(o0), t1 = TREE_TYPE(o1);
            if (t0 && t1 && POINTER_TYPE_P(t0) && POINTER_TYPE_P(t1) && contains_entry(TREE_TYPE(t0)) &&
                TYPE_MAIN_VARIANT(strip_array_types(TREE_TYPE(t0))) == TYPE_MAIN_VARIANT(strip_array_types(TREE_TYPE(t1)))) {
              for (tree x : chain) copy_args.insert(x);
            }
          }
          break;
        default: break;
      }
    tree ft = TREE_TYPE(TREE_TYPE(CALL_EXPR_FN(e)));
    tree parm = ft && (TREE_CODE(ft) == FUNCTION_TYPE || TREE_CODE(ft) == METHOD_TYPE) ? TYPE_ARG_TYPES(ft) : NULL_TREE;
    for (int i = 0; i < call_expr_nargs(e) && parm && parm != void_list_node; i++, parm = TREE_CHAIN(parm))
      entry_flow(TREE_TYPE(CALL_EXPR_ARG(e, i)), TREE_VALUE(parm), CALL_EXPR_ARG(e, i), EXPR_LOCATION(e));
    return NULL_TREE;
  }
  if (c != NOP_EXPR && c != CONVERT_EXPR && c != VIEW_CONVERT_EXPR) return NULL_TREE;
  tree src = TREE_TYPE(TREE_OPERAND(e, 0)), dst = TREE_TYPE(e);
  if (src && dst && (entry_type(src) || entry_type(dst))) { entry_flow(src, dst, TREE_OPERAND(e, 0), EXPR_LOCATION(e)); return NULL_TREE; }
  if (src && dst && POINTER_TYPE_P(src) && contains_entry(TREE_TYPE(src))) {
    if (copy_args.count(e)) return NULL_TREE;      /* an argument of a builtin copy between storage of one type (see the CALL_EXPR case) */
    /* a pointer to storage that holds entry values (memcpy and memset take it as `void *`): to the same type only; an array decays to its element */
    tree a = TREE_TYPE(src), b = POINTER_TYPE_P(dst) ? TREE_TYPE(dst) : NULL_TREE;
    while (a && TREE_CODE(a) == ARRAY_TYPE) a = TREE_TYPE(a);
    while (b && TREE_CODE(b) == ARRAY_TYPE) b = TREE_TYPE(b);
    if (!(b && TYPE_MAIN_VARIANT(a) == TYPE_MAIN_VARIANT(b)))
      error_at(EXPR_LOCATION(e), "ENTRY1-CONT: a pointer to storage that holds entry values (%qT) is converted to %qT: it converts only to a pointer to the same type", src, dst);
    return NULL_TREE;
  }
  if (!src || !dst || !(POINTER_TYPE_P(src))) return NULL_TREE;
  tree sp = TREE_TYPE(src);
  if (!contains_host(sp)) return NULL_TREE;
  if (POINTER_TYPE_P(dst)) {
    if (TYPE_MAIN_VARIANT(TREE_TYPE(dst)) == TYPE_MAIN_VARIANT(sp)) return NULL_TREE;   /* the same record: a qualifier change */
    error_at(EXPR_LOCATION(e), "HOST1: CONT-P: a pointer to a record that holds a host slot is converted to a pointer to another type");
  } else if (INTEGRAL_TYPE_P(dst)) {
    error_at(EXPR_LOCATION(e), "HOST1: CONT-P: a pointer to a record that holds a host slot is converted to an integer");
  }
  return NULL_TREE;
}

/* ENTRY1-UNION: a union that is, or holds, an entry type would let another member forge an entry value. Seen once per completed type. */
static void entry_union(void *gcc_data, void *)
{
  tree t = (tree)gcc_data;
  if (!t || TREE_CODE(t) != UNION_TYPE || !TYPE_FIELDS(t)) return;
  for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
    if (TREE_CODE(f) == FIELD_DECL && contains_entry(TREE_TYPE(f))) {
      error_at(DECL_SOURCE_LOCATION(f), "ENTRY1-UNION: a union member of type %qT holds a native entry type: writing another member would forge an entry value", TREE_TYPE(f));
      return;
    }
}

/* ENTRY1-CONT of a static initialiser: the value of an element flows into the element's type (a CONSTRUCTOR is walked to its elements) */
static void entry_init(tree val, tree to, location_t loc)
{
  if (!val || !to) return;
  if (TREE_CODE(val) == CONSTRUCTOR) {
    unsigned long i; tree idx, v;
    FOR_EACH_CONSTRUCTOR_ELT(CONSTRUCTOR_ELTS(val), i, idx, v) {
      tree et = idx && TREE_CODE(idx) == FIELD_DECL ? TREE_TYPE(idx) : TREE_CODE(to) == ARRAY_TYPE ? TREE_TYPE(to) : NULL_TREE;
      if (et) entry_init(v, et, loc);
    }
    return;
  }
  tree o = val;
  while (CONVERT_EXPR_P(o) || TREE_CODE(o) == NON_LVALUE_EXPR) o = TREE_OPERAND(o, 0);
  if (entry_type(to) && TREE_CODE(o) == INTEGER_CST) { error_at(loc, "ENTRY1-CONT: a constant is no native entry address"); return; }
  entry_flow(TREE_TYPE(val), to, val, loc);
}
static void entry_decl(void *gcc_data, void *)
{
  tree d = (tree)gcc_data;
  if (d && TREE_CODE(d) == FUNCTION_DECL && DECL_NAME(d) && !strcmp(IDENTIFIER_POINTER(DECL_NAME(d)), "fnvnx_dispatch")) fe_declares_dispatch = true;
  if (!d || TREE_CODE(d) != VAR_DECL || !DECL_INITIAL(d)) return;
  if (DECL_CONTEXT(d) && TREE_CODE(DECL_CONTEXT(d)) == FUNCTION_DECL && !TREE_STATIC(d)) return;   /* an automatic variable: its DECL_EXPR is walked in the body */
  entry_init(DECL_INITIAL(d), TREE_TYPE(d), DECL_SOURCE_LOCATION(d));
}

static void contp_fn(void *gcc_data, void *)
{
  tree fn = (tree)gcc_data;
  if (DECL_SAVED_TREE(fn)) walk_tree_without_duplicates(&DECL_SAVED_TREE(fn), contp_walk, NULL);
}

/* a reference that names a field of host slot type (at any depth of the access path): that field (for a slot array, the array member) */
static tree host_ref_field(tree r)
{
  if (!r) return NULL_TREE;
  while (handled_component_p(r)) {
    if (TREE_CODE(r) == COMPONENT_REF && host_elem_type(TREE_TYPE(TREE_OPERAND(r, 1)))) return TREE_OPERAND(r, 1);
    r = TREE_OPERAND(r, 0);
  }
  return NULL_TREE;
}
static bool host_ref(tree r) { return host_ref_field(r) != NULL_TREE; }

static bool conv_code(enum tree_code c)
{
  return c == NOP_EXPR || c == CONVERT_EXPR || c == VIEW_CONVERT_EXPR || c == SSA_NAME;
}

/* the SSA name holds the address of a host slot (or of a place inside a host-slot array): `&p->slot`, or such an
 * address plus an offset, or a conversion of one. GCC itself writes `&p->slot` and loads through it at the last GIMPLE. */
static tree host_addr_field(tree n, int guard = 0)
{
  if (guard > 32 || !n || TREE_CODE(n) != SSA_NAME) return NULL_TREE;
  gimple *d = SSA_NAME_DEF_STMT(n);
  if (!is_gimple_assign(d)) return NULL_TREE;
  enum tree_code c = gimple_assign_rhs_code(d);
  tree r = gimple_assign_rhs1(d);
  if (gimple_assign_single_p(d)) return TREE_CODE(r) == ADDR_EXPR ? host_ref_field(TREE_OPERAND(r, 0)) : NULL_TREE;
  if (c == POINTER_PLUS_EXPR) return host_addr_field(r, guard + 1);
  if (c == NOP_EXPR || c == CONVERT_EXPR) return host_addr_field(r, guard + 1);
  return NULL_TREE;
}
static bool host_addr(tree n) { return host_addr_field(n) != NULL_TREE; }

/* a memory reference that reads a host slot: a field access of host slot type, or a dereference of a host address */
static tree host_load_field(tree r)
{
  if (tree f = host_ref_field(r)) return f;
  if (TREE_CODE(r) != MEM_REF) return NULL_TREE;
  tree b = TREE_OPERAND(r, 0);
  return TREE_CODE(b) == SSA_NAME ? host_addr_field(b) : TREE_CODE(b) == ADDR_EXPR ? host_ref_field(TREE_OPERAND(b, 0)) : NULL_TREE;
}
static bool host_load(tree r) { return host_load_field(r) != NULL_TREE; }

static bool atomic_load_fn(gcall *c)
{
  if (!gimple_call_builtin_p(c, BUILT_IN_NORMAL)) return false;
  switch (DECL_FUNCTION_CODE(gimple_call_fndecl(c))) {
  case BUILT_IN_ATOMIC_LOAD_1: case BUILT_IN_ATOMIC_LOAD_2: case BUILT_IN_ATOMIC_LOAD_4: case BUILT_IN_ATOMIC_LOAD_8: case BUILT_IN_ATOMIC_LOAD_16:
    return true;
  default: return false;
  }
}

static bool atomic_store_fn(gcall *c)
{
  if (!gimple_call_builtin_p(c, BUILT_IN_NORMAL)) return false;
  switch (DECL_FUNCTION_CODE(gimple_call_fndecl(c))) {
  case BUILT_IN_ATOMIC_STORE_1: case BUILT_IN_ATOMIC_STORE_2: case BUILT_IN_ATOMIC_STORE_4: case BUILT_IN_ATOMIC_STORE_8: case BUILT_IN_ATOMIC_STORE_16:
    return true;
  default: return false;
  }
}

/* the ordering plugin turns a load into `__atomic_load_N (address, order)`: this call reads a host slot */
static tree host_atomic_load_field(gimple *st)
{
  gcall *c = dyn_cast<gcall *>(st);
  if (!c || !atomic_load_fn(c) || gimple_call_num_args(c) < 1) return NULL_TREE;
  tree a = gimple_call_arg(c, 0);
  return TREE_CODE(a) == SSA_NAME ? host_addr_field(a) : TREE_CODE(a) == ADDR_EXPR ? host_ref_field(TREE_OPERAND(a, 0)) : NULL_TREE;
}
static bool host_atomic_load(gimple *st) { return host_atomic_load_field(st) != NULL_TREE; }

/* the callee value comes from a host slot load through conversions only */
static tree host_callee_field(tree fn)
{
  for (int guard = 0; guard < 64 && fn && TREE_CODE(fn) == SSA_NAME; guard++) {
    gimple *d = SSA_NAME_DEF_STMT(fn);
    if (tree f = host_atomic_load_field(d)) return f;
    if (!is_gimple_assign(d)) return NULL_TREE;
    enum tree_code c = gimple_assign_rhs_code(d);
    tree r = gimple_assign_rhs1(d);
    if (gimple_assign_single_p(d)) if (tree f = host_load_field(r)) return f;
    if (!conv_code(c)) return NULL_TREE;
    fn = c == VIEW_CONVERT_EXPR ? TREE_OPERAND(r, 0) : r;
  }
  return NULL_TREE;
}

/* `<record tag>.<field>`: the record that declares the slot (for a slot array, the array member) and its name. The tag is spelled as the
 * layout plugin's .fnvnx.decltype spells it (a typedef name is not a tag); a slot in a record or member without a name has no such spelling. */
static std::string host_slot_name(tree f, location_t loc)
{
  tree rec = DECL_CONTEXT(f), n = rec && TYPE_P(rec) ? TYPE_NAME(rec) : NULL_TREE;
  if (n && TREE_CODE(n) == TYPE_DECL) n = DECL_ARTIFICIAL(n) ? DECL_NAME(n) : NULL_TREE;
  if (!n || TREE_CODE(n) != IDENTIFIER_NODE || !DECL_NAME(f)) {
    error_at(loc, "HOST1: a host slot in a record or member without a name has no tag-dot-field row");
    return "-";
  }
  return std::string(IDENTIFIER_POINTER(n)) + "." + IDENTIFIER_POINTER(DECL_NAME(f));
}

static void host_error(gimple *st, const char *form)
{
  error_at(gimple_location(st), "HOST1: %s", form);
}

/* every use of the host value V (or of a conversion of it) is the callee of an indirect call */
static void check_uses(tree v, std::vector<tree> &seen)
{
  for (tree s : seen) if (s == v) return;
  seen.push_back(v);
  gimple *use;
  imm_use_iterator it;
  FOR_EACH_IMM_USE_STMT(use, it, v) {
    if (is_gimple_debug(use)) continue;
    if (gcall *c = dyn_cast<gcall *>(use)) {
      bool callee = gimple_call_fn(c) == v, arg = false;
      for (unsigned i = 0; i < gimple_call_num_args(c); i++) arg |= gimple_call_arg(c, i) == v;
      if (callee && !arg && !gimple_call_internal_p(c)) continue;
      bool st_val = atomic_store_fn(c) && gimple_call_num_args(c) >= 2 && gimple_call_arg(c, 1) == v;   /* the ordering plugin's store builtin */
      host_error(use, st_val ? "a host slot value is stored" : "a host slot value is passed as an argument");
      continue;
    }
    if (gassign *a = dyn_cast<gassign *>(use)) {
      if (conv_code(gimple_assign_rhs_code(a)) && gimple_num_ops(a) == 2 &&
          (gimple_assign_rhs_code(a) == VIEW_CONVERT_EXPR ? TREE_OPERAND(gimple_assign_rhs1(a), 0) : gimple_assign_rhs1(a)) == v &&
          TREE_CODE(gimple_assign_lhs(a)) == SSA_NAME) { check_uses(gimple_assign_lhs(a), seen); continue; }
      host_error(use, TREE_CODE(gimple_assign_lhs(a)) != SSA_NAME ? "a host slot value is stored"
                 : TREE_CODE_CLASS(gimple_assign_rhs_code(a)) == tcc_comparison ? "a host slot value is compared or switched on"
                 : "a host slot value is used in arithmetic or a selection");
      continue;
    }
    if (is_a<gphi *>(use)) { host_error(use, "a host slot value is merged by a PHI"); continue; }
    if (is_a<gcond *>(use) || is_a<gswitch *>(use)) { host_error(use, "a host slot value is compared or switched on"); continue; }
    if (is_a<greturn *>(use)) { host_error(use, "a host slot value is returned"); continue; }
    if (is_a<gasm *>(use)) { host_error(use, "a host slot value is an operand of an asm"); continue; }
    host_error(use, "a host slot value is used other than as the callee of an indirect call");
  }
}

/* every use of a host address A is the address of a load (`*A`), or yields another host address */
static void check_addr_uses(tree a, std::vector<tree> &seen)
{
  for (tree s : seen) if (s == a) return;
  seen.push_back(a);
  gimple *use;
  imm_use_iterator it;
  FOR_EACH_IMM_USE_STMT(use, it, a) {
    if (is_gimple_debug(use)) continue;
    if (gcall *c = dyn_cast<gcall *>(use)) {
      if (gimple_call_num_args(c) >= 1 && gimple_call_arg(c, 0) == a) {
        bool other = false;
        for (unsigned i = 1; i < gimple_call_num_args(c); i++) other |= gimple_call_arg(c, i) == a;
        if (!other && atomic_load_fn(c)) continue;   /* a load: its value is checked where the loads are */
        if (!other && atomic_store_fn(c)) { host_error(use, "a host slot is stored into"); continue; }
      }
    } else if (gassign *g = dyn_cast<gassign *>(use)) {
      tree l = gimple_assign_lhs(g);
      if (TREE_CODE(l) != SSA_NAME) { host_error(use, "a host slot is stored into"); continue; }
      enum tree_code c = gimple_assign_rhs_code(g);
      if (gimple_assign_single_p(g) && TREE_CODE(gimple_assign_rhs1(g)) == MEM_REF && TREE_OPERAND(gimple_assign_rhs1(g), 0) == a)
        continue;   /* a load: its value is checked where the loads are */
      if ((c == POINTER_PLUS_EXPR && gimple_assign_rhs1(g) == a && gimple_assign_rhs2(g) != a) ||
          ((c == NOP_EXPR || c == CONVERT_EXPR) && gimple_assign_rhs1(g) == a)) { check_addr_uses(l, seen); continue; }
    }
    host_error(use, "the address of a host slot is taken");
  }
}

static void check_operand(gimple *st, tree op, bool lhs)
{
  if (!op) return;
  if (TREE_CODE(op) == ADDR_EXPR && host_ref(TREE_OPERAND(op, 0))) {
    gassign *g = dyn_cast<gassign *>(st);   /* `a = &p->slot` is checked by the uses of a; any other place the address is written is an error */
    if (g && gimple_assign_single_p(g) && gimple_assign_rhs1(g) == op && TREE_CODE(gimple_assign_lhs(g)) == SSA_NAME) {
      std::vector<tree> seen;
      check_addr_uses(gimple_assign_lhs(g), seen);
    } else
      host_error(st, "the address of a host slot is taken");
  }
  if (lhs && host_load(op)) host_error(st, "a host slot is stored into");
}

/* ENTRY1-CONT on the lowered GIMPLE: a whole-aggregate copy into storage that holds entry values from another type, and a memory builtin whose destination
 * points to such storage and whose source is not the same type (memset has no source: a written byte pattern is no entry address) */
static void entry_aggregate(gimple *st)
{
  const char *form = "ENTRY1-CONT: a raw copy or aggregate store into storage that holds entry values from another type";
  if (gassign *a = dyn_cast<gassign *>(st)) {
    if (gimple_assign_single_p(a) && TREE_TYPE(gimple_assign_lhs(a)) && AGGREGATE_TYPE_P(TREE_TYPE(gimple_assign_lhs(a))) && contains_entry(TREE_TYPE(gimple_assign_lhs(a)))
        && TYPE_MAIN_VARIANT(TREE_TYPE(gimple_assign_lhs(a))) != TYPE_MAIN_VARIANT(TREE_TYPE(gimple_assign_rhs1(a))))
      error_at(gimple_location(st), "%s", form);
  } else if (gcall *c = dyn_cast<gcall *>(st)) {
    if (!gimple_call_builtin_p(c, BUILT_IN_NORMAL)) return;
    switch (DECL_FUNCTION_CODE(gimple_call_fndecl(c))) {
    case BUILT_IN_MEMCPY: case BUILT_IN_MEMMOVE: case BUILT_IN_MEMPCPY: case BUILT_IN_MEMCPY_CHK: case BUILT_IN_MEMMOVE_CHK: case BUILT_IN_MEMPCPY_CHK: {
      tree d = TREE_TYPE(gimple_call_arg(c, 0)), s2 = TREE_TYPE(gimple_call_arg(c, 1));
      if (POINTER_TYPE_P(d) && contains_entry(strip_array_types(TREE_TYPE(d))) &&
          !(POINTER_TYPE_P(s2) && TYPE_MAIN_VARIANT(strip_array_types(TREE_TYPE(s2))) == TYPE_MAIN_VARIANT(strip_array_types(TREE_TYPE(d)))))
        error_at(gimple_location(st), "%s", form);
      break;
    }
    case BUILT_IN_MEMSET: case BUILT_IN_MEMSET_CHK: {
      tree d = TREE_TYPE(gimple_call_arg(c, 0));
      if (POINTER_TYPE_P(d) && contains_entry(strip_array_types(TREE_TYPE(d)))) error_at(gimple_location(st), "%s", form);
      break;
    }
    default: break;
    }
  }
}

static const char *aggregate_form = "an aggregate with host slots is copied, loaded, stored, passed or returned by value";

static void aggregate_operand(gimple *st, tree op)
{
  if (!op) return;
  if (TREE_CODE(op) == TREE_LIST) op = TREE_VALUE(op);
  if (TREE_TYPE(op) && TYPE_P(TREE_TYPE(op)) && AGGREGATE_TYPE_P(TREE_TYPE(op)) && contains_host(TREE_TYPE(op))) host_error(st, aggregate_form);
}

/* a statement of the lowered GIMPLE (before the memory-order rewrite splits an aggregate copy into word accesses): an access to, or a copy of, a whole
 * aggregate that holds host slots, and a memory builtin whose pointer operands point to one */
static tree aggregate_stmt(gimple_stmt_iterator *gsi, bool *handled, struct walk_stmt_info *)
{
  gimple *st = gsi_stmt(*gsi);
  if (is_gimple_debug(st)) return NULL_TREE;
  entry_aggregate(st);
  if (gassign *a = dyn_cast<gassign *>(st)) {
    for (unsigned i = 0; i < gimple_num_ops(a); i++) aggregate_operand(st, gimple_op(a, i));
  } else if (gcall *c = dyn_cast<gcall *>(st)) {
    if (tree l = gimple_call_lhs(c)) aggregate_operand(st, l);
    for (unsigned i = 0; i < gimple_call_num_args(c); i++) aggregate_operand(st, gimple_call_arg(c, i));
    if (gimple_call_builtin_p(c, BUILT_IN_NORMAL))
      switch (DECL_FUNCTION_CODE(gimple_call_fndecl(c))) {
      case BUILT_IN_MEMCPY: case BUILT_IN_MEMMOVE: case BUILT_IN_MEMPCPY: case BUILT_IN_MEMSET:
      case BUILT_IN_MEMCPY_CHK: case BUILT_IN_MEMMOVE_CHK: case BUILT_IN_MEMPCPY_CHK: case BUILT_IN_MEMSET_CHK:
        for (unsigned i = 0; i < gimple_call_num_args(c) && i < 2; i++) {
          tree t = TREE_TYPE(gimple_call_arg(c, i));
          if (POINTER_TYPE_P(t) && contains_host(strip_array_types(TREE_TYPE(t)))) host_error(st, aggregate_form);
        }
        break;
      default: break;
      }
  } else if (greturn *r = dyn_cast<greturn *>(st))
    aggregate_operand(st, gimple_return_retval(r));
  else if (gasm *x = dyn_cast<gasm *>(st)) {
    for (unsigned i = 0; i < gimple_asm_noutputs(x); i++) aggregate_operand(st, gimple_asm_output_op(x, i));
    for (unsigned i = 0; i < gimple_asm_ninputs(x); i++) aggregate_operand(st, gimple_asm_input_op(x, i));
  }
  *handled = false;
  return NULL_TREE;
}

/* HOST1 containment over the function; the SSA names that hold a host slot value are checked for their uses */
static void host_containment(function *fun)
{
  basic_block bb;
  FOR_EACH_BB_FN(bb, fun)
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
      gimple *st = gsi_stmt(gsi);
      if (is_gimple_debug(st)) continue;
      if (gassign *a = dyn_cast<gassign *>(st)) {
        tree lhs = gimple_assign_lhs(a);
        check_operand(st, lhs, true);
        for (unsigned i = 1; i < gimple_num_ops(a); i++) check_operand(st, gimple_op(a, i), false);
        if (gimple_assign_single_p(a) && host_load(gimple_assign_rhs1(a)) && TREE_CODE(lhs) == SSA_NAME) {
          std::vector<tree> seen;
          check_uses(lhs, seen);
        } else if (gimple_assign_single_p(a) && host_load(gimple_assign_rhs1(a)))
          host_error(st, "a host slot is stored");
      } else if (gcall *c = dyn_cast<gcall *>(st)) {
        if (tree l = gimple_call_lhs(c)) check_operand(st, l, true);
        bool ld = host_atomic_load(st);
        for (unsigned i = 0; i < gimple_call_num_args(c); i++)
          if (!(ld && i == 0)) check_operand(st, gimple_call_arg(c, i), false);
        if (ld) {
          tree l = gimple_call_lhs(c);
          if (l && TREE_CODE(l) == SSA_NAME) { std::vector<tree> seen; check_uses(l, seen); }
          else if (l) host_error(st, "a host slot is stored");
        }
      } else if (gasm *x = dyn_cast<gasm *>(st)) {
        for (unsigned i = 0; i < gimple_asm_noutputs(x); i++) check_operand(st, gimple_asm_output_op(x, i), true);
        for (unsigned i = 0; i < gimple_asm_ninputs(x); i++) check_operand(st, gimple_asm_input_op(x, i), false);
      } else if (greturn *r = dyn_cast<greturn *>(st))
        check_operand(st, gimple_return_retval(r), false);
    }
}

/* The provenance of a callee is a fact of the early GIMPLE (after SSA is built): a slot is read through its field, and a later pass may write the address of
 * the slot as plain pointer arithmetic. So the early pass (host_pass) marks every host-ABI call: its callee value C becomes `m = fnvnx_host_ptr (C)`, a
 * const artificial call that the dispatch pass at the last GIMPLE recognises and replaces by `m = C`. A marker is a fact of the pass pair; it never reaches
 * an object. */
static bool marker_call(gimple *st)
{
  gcall *c = dyn_cast<gcall *>(st);
  tree d = c ? gimple_call_fndecl(c) : NULL_TREE;
  return d && DECL_ARTIFICIAL(d) && DECL_EXTERNAL(d) && DECL_NAME(d) && !strcmp(IDENTIFIER_POINTER(DECL_NAME(d)), "fnvnx_host_ptr");
}

static tree marker_decl(tree ptr_type)
{
  tree t = build_function_type_list(ptr_type, ptr_type, integer_type_node, NULL_TREE);
  tree d = build_fn_decl("fnvnx_host_ptr", t);
  DECL_EXTERNAL(d) = 1; TREE_PUBLIC(d) = 1; DECL_ARTIFICIAL(d) = 1; TREE_NOTHROW(d) = 1; TREE_READONLY(d) = 1; DECL_PURE_P(d) = 0;
  DECL_SOURCE_LOCATION(d) = BUILTINS_LOCATION;   /* a compiler-internal declaration, as an undeclared builtin: no plugin that lists the unit's declarations sees it */
  return d;
}

/* the slots the host-ABI calls were loaded from, `<record tag>.<field>`; a marker carries its index */
static std::vector<std::string> host_slots;

struct fn_record { unsigned rewritten = 0; std::vector<std::string> host; };
/* per function: the number of call sites rewritten, and one slot per host-ABI call */
static std::map<std::string, fn_record> records;

static void finish_records(void *, void *)
{
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.dispatch,\"\",%%progbits\n");
  for (auto &r : records) {
    fprintf(asm_out_file, "\t.ascii\t\"%s\\t%u\\t%u\\n\"\n", r.first.c_str(), r.second.rewritten, (unsigned)r.second.host.size());
    std::vector<std::string> rows = r.second.host;
    std::sort(rows.begin(), rows.end());
    for (auto &h : rows) fprintf(asm_out_file, "\t.ascii\t\"%s\\thost\\t%s\\n\"\n", r.first.c_str(), h.c_str());
  }
  fprintf(asm_out_file, "\t.popsection\n");
}

static void rewrite(function *fun, gimple *st)
{
  gcall *call = as_a<gcall *>(st);
  tree fn = gimple_call_fn(call);
  tree fp = TREE_CODE(fn) == OBJ_TYPE_REF ? OBJ_TYPE_REF_EXPR(fn) : fn;
  if (TREE_CODE(fp) != SSA_NAME && !is_gimple_min_invariant(fp))
    { error_at(gimple_location(st), "DISPATCH1: an indirect call whose target is not a value"); return; }
  tree desc = descriptor(call);
  if (!desc) return;
  location_t loc = gimple_location(st);
  tree site = new_site();
  tree u64 = long_long_unsigned_type_node, u32 = unsigned_type_node;
  gimple_stmt_iterator gsi = gsi_for_stmt(st);
  auto before = [&](gimple *g) { gimple_set_location(g, loc); gsi_insert_before(&gsi, g, GSI_SAME_STMT); };

  /* head: k = (unsigned) fp; w = site (one 64-bit acquire load); r = the record viewed over w; if (r.fp == k) */
  tree rec = get_site_record(), entry_t = get_entry_generic();
  tree k = make_ssa_name(u32), w = make_ssa_name(u64), lo = make_ssa_name(u32), e1 = make_ssa_name(entry_t);
  before(gimple_build_assign(k, NOP_EXPR, fp));
  /* the slot is shared memory: its load is created ordered (R9, docs/ORDERING.md; fnvnx_order runs before this pass and cannot see it) */
  tree pa = make_ssa_name(build_pointer_type(u64));
  before(gimple_build_assign(pa, NOP_EXPR, build_fold_addr_expr(site)));
  gcall *ld = gimple_build_call(builtin_decl_explicit(BUILT_IN_ATOMIC_LOAD_8), 2, pa,
                                build_int_cst(integer_type_node, MEMMODEL_ACQUIRE));
  gimple_call_set_lhs(ld, w);
  before(ld);
  /* The fields are read out of the loaded word w itself, as BIT_FIELD_REFs at the fields' own bit positions: no aggregate variable is made. A local
     record would be a memory object, and the order plugin (the last GIMPLE pass, after every optimizer) keeps a private local in its own stack slot, so the
     hit path would store w and reload a half. PROOF: the record is exactly 64 bits (layout_type above) and w holds all of them, so the field at bit
     position P of size S is bits [P, P+S) of w, which is what BIT_FIELD_REF<w, S, P> reads, on any endianness (bit_position is the target's). */
  tree fp_bits = bitsize_int(TREE_INT_CST_LOW(DECL_SIZE(site_f_fp())));
  tree lo_ref = build3(BIT_FIELD_REF, u32, w, fp_bits, bit_position(site_f_fp()));
  tree en_ref = build3(BIT_FIELD_REF, entry_t, w, bitsize_int(TREE_INT_CST_LOW(DECL_SIZE(site_f_entry()))), bit_position(site_f_entry()));
  before(gimple_build_assign(lo, lo_ref));
  gcond *cond = gimple_build_cond(EQ_EXPR, lo, k, NULL_TREE, NULL_TREE);
  before(cond);

  basic_block head = gimple_bb(st);
  edge fall = split_block(head, cond);               /* head | tail (the call) */
  basic_block tail = fall->dest;
  basic_block hit = create_empty_bb(head), miss = create_empty_bb(hit);
  if (current_loops) { add_bb_to_loop(hit, head->loop_father); add_bb_to_loop(miss, head->loop_father); }
  remove_edge(fall);
  edge e_hit = make_edge(head, hit, EDGE_TRUE_VALUE), e_miss = make_edge(head, miss, EDGE_FALSE_VALUE);
  e_hit->probability = profile_probability::very_likely(); e_miss->probability = e_hit->probability.invert();
  edge hit_tail = make_edge(hit, tail, EDGE_FALLTHRU), miss_tail = make_edge(miss, tail, EDGE_FALLTHRU);
  hit->count = e_hit->count(); miss->count = e_miss->count();

  /* hit: e1 = r.entry */
  tree e2 = make_ssa_name(entry_t), e = make_ssa_name(entry_t);
  gimple_stmt_iterator hgsi = gsi_start_bb(hit);
  gimple *g = gimple_build_assign(e1, en_ref);
  gimple_set_location(g, loc); gsi_insert_after(&hgsi, g, GSI_NEW_STMT);
  /* miss: e2 = fnvnx_dispatch(fp, &site, desc) */
  gimple_stmt_iterator mgsi = gsi_start_bb(miss);
  tree fpv = make_ssa_name(ptr_type_node);
  g = gimple_build_assign(fpv, NOP_EXPR, fp);
  gimple_set_location(g, loc); gsi_insert_after(&mgsi, g, GSI_NEW_STMT);
  gcall *d = gimple_build_call(get_dispatch(), 3, fpv, build_fold_addr_expr(site), desc);
  gimple_call_set_lhs(d, e2); gimple_set_location(d, loc);
  gsi_insert_after(&mgsi, d, GSI_NEW_STMT);
  /* tail: e = PHI <e1 (hit), e2 (miss)>; the call through it, converted from the entry to the call's own function-pointer type */
  gphi *phi = create_phi_node(e, tail);
  add_phi_arg(phi, e1, hit_tail, loc); add_phi_arg(phi, e2, miss_tail, loc);
  tree target = make_ssa_name(TREE_TYPE(fp));
  gsi = gsi_for_stmt(st);
  before(gimple_build_assign(get_fp_var(), k));
  before(gimple_build_assign(target, NOP_EXPR, e));
  gimple_call_set_fn(call, target);
  update_stmt(call);
}

namespace {
const pass_data dispatch_data = { GIMPLE_PASS, "fnvnx_dispatch", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0,
                                  TODO_update_ssa_only_virtuals | TODO_cleanup_cfg };
struct dispatch_pass : gimple_opt_pass {
  dispatch_pass(gcc::context *c) : gimple_opt_pass(dispatch_data, c) {}
  unsigned int execute(function *fun) override
  {
    std::vector<gimple *> calls, markers;
    std::vector<std::string> host;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
        gimple *st = gsi_stmt(gsi);
        if (marker_call(st)) markers.push_back(st);
        else if (indirect_call(st)) {
          tree fn = gimple_call_fn(st);
          gimple *d = TREE_CODE(fn) == SSA_NAME ? SSA_NAME_DEF_STMT(fn) : NULL;
          if (d && marker_call(d)) {
            tree ix = gimple_call_arg(d, 1);
            host.push_back(host_slots[tree_to_uhwi(ix)]);
          } else calls.push_back(st);
        }
      }
    /* a marker value is only ever a callee; then the marker is a plain copy */
    for (gimple *m : markers) {
      tree l = gimple_call_lhs(m);
      if (l) {
        std::vector<tree> seen;
        check_uses(l, seen);
      }
      gimple_stmt_iterator gsi = gsi_for_stmt(m);
      gsi_replace(&gsi, gimple_build_assign(l ? l : make_ssa_name(TREE_TYPE(gimple_call_arg(m, 0))), gimple_call_arg(m, 0)), true);
    }
    if (calls.empty() && host.empty()) return 0;
    records[IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fun->decl))] = { (unsigned)calls.size(), host };
    if (calls.empty()) return 0;
    for (size_t i = 0; i < calls.size(); i++) rewrite(fun, calls[i]);
    free_dominance_info(CDI_DOMINATORS);
    mark_virtual_operands_for_renaming(fun);
    return 0;
  }
};

const pass_data agg_data = { GIMPLE_PASS, "fnvnx_hostagg", OPTGROUP_NONE, TV_NONE, 0, 0, 0, 0, 0 };
/* HOST1 containment of aggregates, on the lowered GIMPLE (no CFG yet) */
struct agg_pass : gimple_opt_pass {
  agg_pass(gcc::context *c) : gimple_opt_pass(agg_data, c) {}
  unsigned int execute(function *fun) override
  {
    struct walk_stmt_info wi;
    memset(&wi, 0, sizeof wi);
    walk_gimple_seq(gimple_body(fun->decl), aggregate_stmt, NULL, &wi);
    return 0;
  }
};

const pass_data host_data = { GIMPLE_PASS, "fnvnx_host", OPTGROUP_NONE, TV_NONE, PROP_cfg | PROP_ssa, 0, 0, 0, 0 };
/* HOST1 at the early GIMPLE (after the SSA form is built): containment, and a marker on every host-ABI call */
struct host_pass : gimple_opt_pass {
  host_pass(gcc::context *c) : gimple_opt_pass(host_data, c) {}
  unsigned int execute(function *fun) override
  {
    host_containment(fun);
    std::vector<gimple *> calls;
    basic_block bb;
    FOR_EACH_BB_FN(bb, fun)
      for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi))
        if (indirect_call(gsi_stmt(gsi))) {
          tree fn = gimple_call_fn(gsi_stmt(gsi));
          if (TREE_CODE(fn) == SSA_NAME && host_callee_field(fn)) calls.push_back(gsi_stmt(gsi));
        }
    for (gimple *call : calls) {
      tree fn = gimple_call_fn(call);
      tree m = make_ssa_name(TREE_TYPE(fn));
      std::string slot = host_slot_name(host_callee_field(fn), gimple_location(call));
      host_slots.push_back(slot);
      gcall *mk = gimple_build_call(marker_decl(TREE_TYPE(fn)), 2, fn, build_int_cst(integer_type_node, host_slots.size() - 1));
      gimple_call_set_lhs(mk, m);
      gimple_set_location(mk, gimple_location(call));
      gimple_stmt_iterator gsi = gsi_for_stmt(call);
      gsi_insert_before(&gsi, mk, GSI_SAME_STMT);
      gimple_call_set_fn(as_a<gcall *>(call), m);
      update_stmt(call);
    }
    return 0;
  }
};
}

/* the trees this plugin keeps between functions are GC roots */
static const struct ggc_root_tab keep_roots[] = {
  { &dispatch_decl, 1, sizeof(tree), &gt_ggc_mx_tree_node, &gt_pch_nx_tree_node },
  { &entry_generic, 1, sizeof(tree), &gt_ggc_mx_tree_node, &gt_pch_nx_tree_node },
  { &site_record, 1, sizeof(tree), &gt_ggc_mx_tree_node, &gt_pch_nx_tree_node },
  { &fp_var, 1, sizeof(tree), &gt_ggc_mx_tree_node, &gt_pch_nx_tree_node },
  LAST_GGC_ROOT_TAB
};

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *ver)
{
  if (!plugin_default_version_check(ver, &gcc_version)) {
    error("%<fnvnx_dispatch%>: built for GCC %s, loaded by %s", gcc_version.basever, ver->basever);
    return 1;
  }
  if (info->argc) {   /* no silent modes: this plugin has no options */
    error("%<fnvnx_dispatch%>: unknown plugin argument %qs", info->argv[0].key);
    return 1;
  }
  register_callback(info->base_name, PLUGIN_REGISTER_GGC_ROOTS, NULL, (void *)keep_roots);
  register_callback(info->base_name, PLUGIN_ATTRIBUTES, register_attrs, NULL);
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, contp_fn, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_TYPE, entry_union, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_DECL, entry_decl, NULL);
  plugsym_writer();
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  register_pass_info ap;
  ap.pass = new agg_pass(g);
  ap.reference_pass_name = "eh";
  ap.ref_pass_instance_number = 1;
  ap.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &ap);
  register_pass_info hp;
  hp.pass = new host_pass(g);
  hp.reference_pass_name = "ssa";
  hp.ref_pass_instance_number = 1;
  hp.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &hp);
  register_pass_info p;
  p.pass = new dispatch_pass(g);
  p.reference_pass_name = "optimized";
  p.ref_pass_instance_number = 1;
  p.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &p);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_records, NULL);
  return 0;
}
