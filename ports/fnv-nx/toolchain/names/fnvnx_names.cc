/* fnvnx_names - NAMES1: the MSVC 2008 (x86) name of every external symbol of a unit, from the compiler's own trees.
 *
 *   g++ ... -fplugin=<dir>/fnvnx_names.so unit.cpp
 *
 * The exe's contract names things the way its compiler did. The same declarations compiled by GCC get Itanium
 * names, so each object carries the pairing in its own section .fnvnx.sympair (not loaded), one text line per
 * symbol of the object's symbol table that has external linkage:
 *
 *     <symbol>\t<MSVC name>\t<kind>\t<def|ref>
 *
 * kind  func, data            the MSVC name is the name cl gives the same declaration
 *       ctor.c1 / ctor.c2     GCC's complete / base constructor: both are cl's one ??0 (c1 = "most derived" flag 1)
 *       dtor.d2               the base destructor ??1
 *       dtor.d1               the complete destructor: ??1, or ??_D when the class has virtual bases
 *       dtor.d0               the deleting destructor ??_G
 *       dtor.sdd              the SDD2 slot function <entry>.fnvnx_sdd: ??_E
 *       dtor.sdd.thunk        the same for a secondary table: cl's adjustor thunk to ??_E (`W7AE`: this -= 8)
 *       internal              cl gives it internal linkage (a user-written member function of a function's local
 *                             class, and what it declares): not in the contract, the MSVC name is empty
 *       thunk                 a GCC this/result adjusting thunk; the name is its target's (the exe adjusts `this`
 *                             in the callee or in its own thunks, which are the contract's, not this object's)
 *       vtable                a GCC table group; its address points are named in .fnvnx.vtpair
 *       gcc                   an Itanium ABI object with no counterpart in the exe (VTT, construction table,
 *                             typeinfo, guard variable): the MSVC name is empty
 *       unnamed:<reason>      no rule here names it (the gate counts these; 0 is the gate)
 *
 * .fnvnx.names: `<symbol>\t<MSVC name>` of every function and object the unit named, also those the object has no
 * symbol for (inlined everywhere): for gates that follow a call into cl's code, not for the contract.
 *
 * .fnvnx.vtpair: `<_ZTV symbol>\t<offset of the address point>\t<??_7 name>\t<offset of the vfptr in the object>`,
 * for every table group the unit defines or names. SDD2's symbol of an address point, `<_ZTV symbol>.fnvnx_ap<offset>`,
 * is that row's (it has no row of its own in .fnvnx.sympair).
 *
 * The name is computed from the declaration's trees by MSVC's rules (checked against the pinned cl's own symbol
 * tables: test_names.py, and tools/link/namegate.py over every pristine object). Calling conventions are the
 * declaration's: CC1 keeps __cdecl / __stdcall / __fastcall / __thiscall as the type attribute fnvnx_cc, which
 * this plugin registers. Names with internal linkage (static, unnamed namespace) are not in the contract and get
 * no row.
 *
 * What the trees do not hold (each shows as a difference in the gate, never silently):
 *   - the top-level const of a pointer parameter, and an array or function parameter's own form, inside a function
 *     TYPE that is not a declaration (GCC's function types drop them; a declaration's parameters keep them);
 *   - size_t / ptrdiff_t as types of their own (the compiler must be configured with the exe's: see build notes).
 */
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "cp/cp-tree.h"
#include "context.h"
#include "stringpool.h"
#include "attribs.h"
#include "cgraph.h"
#include "varasm.h"
#include "output.h"
#include "diagnostic-core.h"
#include "stor-layout.h"
#include "fold-const.h"
#include "tree-iterator.h"
#include "intl.h"
#include "pretty-print.h"
#include "diagnostic.h"
#include "opts.h"
#include <cstdarg>
#include <map>
#include <set>
#include <string>
#include <vector>

int plugin_is_GPL_compatible;

enum Mode { DROP, MANGLE, ESCAPE, RESULT };
enum DtorKind { DT_NONE, DT_BASE, DT_VBASE, DT_SCALAR, DT_VECTOR };

/* GC invariant: trees live in garbage-collected memory and ggc_collect runs between functions, so a tree this file
 * keeps across one may be freed and its address reused by another tree. No container here is keyed by an address:
 * each is keyed by a UID (DECL_UID, TYPE_UID of the main variant), which the front end allocates from a counter and
 * never reuses, so a stale entry can only be unreachable, never wrong. The two UID spaces are separate, hence the
 * `type` flag in the key. A mapped value holds no tree that is read after a collection except through its owner
 * (vpaths_memo's bases are reachable from the class's own binfo while the class lives, and the entry is looked
 * up only through the class). */
typedef std::pair<bool, unsigned> UidKey;
static UidKey uid_decl(tree d) { return UidKey(false, DECL_UID(d)); }
static UidKey uid_type(tree t) { t = TYPE_MAIN_VARIANT(t); return UidKey(true, TYPE_UID(t)); }
static UidKey uid_of(tree x) { return TYPE_P(x) ? uid_type(x) : uid_decl(x); }

static std::map<UidKey, int> scope_no;    /* a function-local static or class -> its MSVC scope number */

/* ---- the class key in a name (U struct, V class)
 *
 * cl takes the key of the class's definition, or of its first declaration when the unit never defines it
 * (test_names.expect: Fwd1..Fwd9). GCC's flag holds the key seen last. The two differ only where a declaration's
 * key differs from that reference, which is exactly what -Wmismatched-tags reports, with the same reference; the
 * plugin turns the warning on and reads the class and the offending key off each report as it is formatted (the
 * reference key is the other one). The assumption: the warning is not suppressed for a declaration (system
 * header, #pragma GCC diagnostic); tools/link/namegate.py compares every name with cl's.
 */
static std::map<UidKey, bool> ref_class_key;      /* class (type uid) -> the reference key is `class` */
static std::map<UidKey, bool> def_class_key;      /* class (type uid) -> its definition had the key `class` */
static printer_fn old_decoder;

static bool key_decoder(pretty_printer *pp, text_info *text, const char *spec, int precision, bool wide,
                        bool set_locus, bool hash, bool *quoted, const char **buffer_ptr)
{
  static const char *fmt = _("%qT declared with a mismatched class-key %qs");
  if (*spec == 'T' && text->format_spec && !strcmp(text->format_spec, fmt)) {
    va_list ap;
    va_copy(ap, *text->args_ptr);
    tree td = va_arg(ap, tree);
    const char *k = va_arg(ap, const char *);
    va_end(ap);
    tree t = td && TREE_CODE(td) == TYPE_DECL ? TREE_TYPE(td) : td;
    if (t && TYPE_P(t) && k) ref_class_key[uid_type(t)] = strcmp(k, "class") != 0;
  }
  return old_decoder(pp, text, spec, precision, wide, set_locus, hash, quoted, buffer_ptr);
}

static bool declared_class(tree t)
{
  t = TYPE_MAIN_VARIANT(t);
  auto it = ref_class_key.find(uid_type(t));
  if (it != ref_class_key.end()) return it->second;
  if (CLASSTYPE_TEMPLATE_INFO(t) && TREE_CODE(CLASSTYPE_TI_TEMPLATE(t)) == TEMPLATE_DECL) {
    tree pat = TREE_TYPE(most_general_template(CLASSTYPE_TI_TEMPLATE(t)));
    if (pat && (it = ref_class_key.find(uid_type(pat))) != ref_class_key.end()) return it->second;
  }
  /* The key of the definition, read when the definition finished: GCC's flag is rewritten later by a
   * `typename ::C` elaborated specifier (FNVNX_MSVC_BASE_END(typename ::Base)), which clears it with no report. */
  auto d = def_class_key.find(uid_type(t));
  if (d != def_class_key.end()) return d->second;
  return CLASSTYPE_DECLARED_CLASS(t);
}

static void start_unit(void *, void *)
{
  if (global_dc->dc_inhibit_warnings)
    error("NAMES1: %<-w%> hides the class-key reports the names are computed from");
  warn_mismatched_tags = 1;
  old_decoder = pp_format_decoder(global_dc->printer);
  pp_format_decoder(global_dc->printer) = key_decoder;
}

static char qual_letter(int q, bool member)
{
  bool c = q & TYPE_QUAL_CONST, v = q & TYPE_QUAL_VOLATILE;
  return member ? (c && v ? 'T' : v ? 'S' : c ? 'R' : 'Q') : (c && v ? 'D' : v ? 'C' : c ? 'B' : 'A');
}
static char ptr_letter(int q)
{
  bool c = q & TYPE_QUAL_CONST, v = q & TYPE_QUAL_VOLATILE;
  return c && v ? 'S' : v ? 'R' : c ? 'Q' : 'P';
}

/* The parameters' top-level qualifiers and array form are those of the function's FIRST declaration (checked against cl: a
 * declaration `f(int *p)` and a definition `f(int *const p)` are `?f@@YAXPAH@Z`, the reverse `QAH`, and a third declaration
 * does not matter). GCC keeps only the last declaration's PARM_DECLs, so the first is recorded when it is declared, by the
 * uid of the declaration that survives the merge (a template instance: of its most general template's pattern). A function
 * whose first declaration is its definition, or that was never declared before (no record), has its own. */
struct ParmForm { int quals; bool array; };
static std::map<UidKey, std::vector<ParmForm>> first_parms;
static UidKey parm_key(tree f)
{
  f = DECL_ORIGIN(f);
  if (DECL_LANG_SPECIFIC(f) && DECL_TEMPLATE_INFO(f) && TREE_CODE(DECL_TI_TEMPLATE(f)) == TEMPLATE_DECL) {
    tree g = DECL_TEMPLATE_RESULT(most_general_template(DECL_TI_TEMPLATE(f)));
    if (g) f = g;
  }
  return uid_decl(f);
}
static std::vector<ParmForm> parm_forms(tree f)
{
  std::vector<ParmForm> v;
  for (tree p = DECL_ARGUMENTS(f) ? FUNCTION_FIRST_USER_PARM(f) : NULL_TREE; p; p = DECL_CHAIN(p))
    v.push_back({cp_type_quals(TREE_TYPE(p)), (bool)DECL_ARRAY_PARAMETER_P(p)});
  return v;
}
struct ArgKey { tree type; int quals; bool array; };

struct M {
  std::string out;
  std::vector<std::string> names;
  std::vector<ArgKey> args;
  std::string why;                     /* non-empty: no rule names it */
  bool internal = false;               /* a name with internal linkage is part of it */
  HOST_WIDE_INT thunk = -1;            /* >= 0: the name of the adjustor thunk that subtracts this from `this` */

  void fail(const std::string &s) { if (why.empty()) why = s; }

  void number(unsigned HOST_WIDE_INT v, bool neg = false)
  {
    if (neg) out += '?';
    if (v == 0) out += "A@";
    else if (v <= 10) out += (char)('0' + v - 1);
    else {
      std::string s;
      for (; v; v >>= 4) s.insert(s.begin(), (char)('A' + (v & 15)));
      out += s + "@";
    }
  }
  void snumber(HOST_WIDE_INT v) { if (v < 0) number(-(unsigned HOST_WIDE_INT)v, true); else number(v); }

  void source_name(const std::string &n)
  {
    for (size_t i = 0; i < names.size(); i++)
      if (names[i] == n) { out += (char)('0' + i); return; }
    out += n + "@";
    if (names.size() < 10) names.push_back(n);
  }

  /* `?$name@args` mangled on its own (its back-references are its own) */
  std::string template_id(const std::string &base, bool special, tree targs)
  {
    M sub;
    sub.out = "?$";
    if (special) sub.out += base; else sub.source_name(base);
    for (int i = 0; i < TREE_VEC_LENGTH(targs); i++) sub.targ(TREE_VEC_ELT(targs, i));
    if (!sub.why.empty()) fail(sub.why);
    internal |= sub.internal;
    return sub.out;
  }

  static bool class_template_id(tree t)
  {
    return CLASS_TYPE_P(t) && CLASSTYPE_TEMPLATE_INFO(t) && TREE_CODE(CLASSTYPE_TI_TEMPLATE(t)) == TEMPLATE_DECL
        && PRIMARY_TEMPLATE_P(CLASSTYPE_TI_TEMPLATE(t));
  }
  static bool fn_template_id(tree d)
  {
    return DECL_LANG_SPECIFIC(d) && DECL_TEMPLATE_INFO(d) && TREE_CODE(DECL_TI_TEMPLATE(d)) == TEMPLATE_DECL
        && PRIMARY_TEMPLATE_P(DECL_TI_TEMPLATE(d));
  }

  void unqualified_type(tree t)
  {
    t = TYPE_MAIN_VARIANT(t);
    tree id = TYPE_IDENTIFIER(t);
    if (class_template_id(t))
      source_name(template_id(IDENTIFIER_POINTER(DECL_NAME(CLASSTYPE_TI_TEMPLATE(t))), false,
                              INNERMOST_TEMPLATE_ARGS(CLASSTYPE_TI_ARGS(t))));
    else if (!id || IDENTIFIER_ANON_P(id)) source_name("<unnamed-tag>");
    else source_name(IDENTIFIER_POINTER(id));
  }

  /* the enclosing scopes of an entity, innermost first, without the final '@' */
  void contexts(tree ctx, tree entity)
  {
    while (ctx && ctx != global_namespace && TREE_CODE(ctx) != TRANSLATION_UNIT_DECL) {
      if (TREE_CODE(ctx) == NAMESPACE_DECL) {
        if (!DECL_NAME(ctx)) { internal = true; source_name("?A0x00000000"); }
        else source_name(IDENTIFIER_POINTER(DECL_NAME(ctx)));
        ctx = CP_DECL_CONTEXT(ctx);
      } else if (TYPE_P(ctx)) {
        unqualified_type(ctx);
        entity = TYPE_MAIN_VARIANT(ctx);
        ctx = CP_TYPE_CONTEXT(ctx);
      } else if (TREE_CODE(ctx) == FUNCTION_DECL) {
        auto it = scope_no.find(uid_of(entity));
        if (it == scope_no.end()) { fail("local scope number"); return; }
        out += '?'; number(it->second); out += '?';
        decl(ctx, DT_NONE);
        if (!TREE_PUBLIC(ctx)) internal = true;
        return;
      } else { fail("scope"); return; }
    }
  }

  void type_name(tree t)
  {
    t = TYPE_MAIN_VARIANT(t);
    unqualified_type(t);
    contexts(CP_TYPE_CONTEXT(t), t);
    out += '@';
  }

  void array(tree t)
  {
    std::vector<unsigned HOST_WIDE_INT> dims;
    for (; TREE_CODE(t) == ARRAY_TYPE; t = TREE_TYPE(t)) {
      tree dom = TYPE_DOMAIN(t);
      if (dom && TYPE_MAX_VALUE(dom) && tree_fits_shwi_p(TYPE_MAX_VALUE(dom))) dims.push_back(tree_to_shwi(TYPE_MAX_VALUE(dom)) + 1);
      else dims.push_back(0);
    }
    out += 'Y'; number(dims.size());
    for (auto d : dims) number(d);
    type(t, ESCAPE);
  }

  void builtin(tree t)
  {
    tree mv = TYPE_MAIN_VARIANT(t);
    if (mv == void_type_node) out += 'X';
    else if (mv == boolean_type_node) out += "_N";
    else if (mv == char_type_node) out += 'D';
    else if (mv == signed_char_type_node) out += 'C';
    else if (mv == unsigned_char_type_node) out += 'E';
    else if (mv == short_integer_type_node) out += 'F';
    else if (mv == short_unsigned_type_node) out += 'G';
    else if (mv == integer_type_node) out += 'H';
    else if (mv == unsigned_type_node) out += 'I';
    else if (mv == long_integer_type_node) out += 'J';
    else if (mv == long_unsigned_type_node) out += 'K';
    else if (mv == long_long_integer_type_node) out += "_J";
    else if (mv == long_long_unsigned_type_node) out += "_K";
    else if (mv == wchar_type_node) out += "_W";
    else if (mv == float_type_node) out += 'M';
    else if (mv == double_type_node) out += 'N';
    else if (mv == long_double_type_node) out += 'O';
    else fail("a type MSVC 2008 does not have");
  }

  void type(tree t, Mode m, int q = -1)
  {
    if (q < 0) q = cp_type_quals(t);
    bool restrict_p = (q & TYPE_QUAL_RESTRICT) != 0;   /* MSVC's `I` after the pointer letter */
    q &= TYPE_QUAL_CONST | TYPE_QUAL_VOLATILE;
    if (TREE_CODE(t) == ARRAY_TYPE) {
      if (m == MANGLE) out += 'A'; else if (m == ESCAPE || m == RESULT) out += "$$B";
      array(t);
      return;
    }
    bool memfn = TYPE_PTRMEMFUNC_P(t);
    bool isptr = INDIRECT_TYPE_P(t) || memfn || TREE_CODE(t) == OFFSET_TYPE;
    bool tag = !memfn && (RECORD_OR_UNION_TYPE_P(t) || TREE_CODE(t) == ENUMERAL_TYPE || TREE_CODE(t) == VECTOR_TYPE);
    switch (m) {
      case DROP: break;
      case MANGLE:
        if (FUNC_OR_METHOD_TYPE_P(t)) { out += '6'; function_type(t, NULL_TREE, DT_NONE); return; }
        out += qual_letter(q, false);
        break;
      case ESCAPE:
        if (FUNC_OR_METHOD_TYPE_P(t)) { out += "$$A6"; function_type(t, NULL_TREE, DT_NONE); return; }
        if (!isptr && q) { out += "$$C"; out += qual_letter(q, false); }
        break;
      case RESULT:
        if ((!isptr && q) || tag) { out += '?'; out += qual_letter(q, false); }
        break;
    }
    if (memfn) {
      out += ptr_letter(q); out += '8';
      type_name(TYPE_PTRMEMFUNC_OBJECT_TYPE(t));
      function_type(TREE_TYPE(TYPE_PTRMEMFUNC_FN_TYPE(t)), NULL_TREE, DT_NONE);
      return;
    }
    switch (TREE_CODE(t)) {
      case VOID_TYPE: case BOOLEAN_TYPE: case INTEGER_TYPE: case REAL_TYPE: builtin(t); break;
      case ENUMERAL_TYPE: out += "W4"; type_name(t); break;
      case RECORD_TYPE: out += declared_class(t) ? 'V' : 'U'; type_name(t); break;
      case UNION_TYPE: out += 'T'; type_name(t); break;
      case POINTER_TYPE: out += ptr_letter(q); if (restrict_p) out += 'I'; type(TREE_TYPE(t), MANGLE); break;
      case REFERENCE_TYPE: out += 'A'; type(TREE_TYPE(t), MANGLE); break;   /* cl ignores a qualifier on a reference */
      case OFFSET_TYPE:
        out += ptr_letter(q); out += qual_letter(cp_type_quals(TREE_TYPE(t)), true);
        type_name(TYPE_OFFSET_BASETYPE(t));
        type(TREE_TYPE(t), DROP);
        break;
      case VECTOR_TYPE: {
        /* the exe's SSE types (xmmintrin.h / emmintrin.h of cl: union __m128, union __m128i, struct __m128d) are
         * the vectors the target's intrinsics header gives them: 4 x float, 2 x 64-bit integer, 2 x double */
        tree e = TREE_TYPE(t);
        unsigned HOST_WIDE_INT n = TYPE_VECTOR_SUBPARTS(t).to_constant();
        const char *key = "T", *nm = NULL;
        if (SCALAR_FLOAT_TYPE_P(e) && TYPE_PRECISION(e) == 32 && n == 4) nm = "__m128";
        else if (INTEGRAL_TYPE_P(e) && TYPE_PRECISION(e) == 64 && n == 2) nm = "__m128i";
        else if (SCALAR_FLOAT_TYPE_P(e) && TYPE_PRECISION(e) == 64 && n == 2) nm = "__m128d", key = "U";
        else fail("a vector type that is not __m128, __m128i or __m128d");
        /* a tag name like any other: the second `__m128` of a name is a back-reference to the first */
        if (nm) { out += key; source_name(nm); out += '@'; }
        break;
      }
      default: fail(std::string("type ") + get_tree_code_name(TREE_CODE(t)));
    }
  }

  static const char *convention(tree ft)
  {
    if (tree a = lookup_attribute("fnvnx_cc", TYPE_ATTRIBUTES(ft))) return TREE_STRING_POINTER(TREE_VALUE(TREE_VALUE(a)));
    return NULL;
  }
  static bool variadic(tree ft)
  {
    tree p = TYPE_ARG_TYPES(ft);
    if (!p) return true;
    for (; p; p = TREE_CHAIN(p)) if (p == void_list_node) return false;
    return true;
  }
  /* A cdecl, E thiscall, G stdcall, I fastcall; a variadic function is cdecl whatever it says */
  static char conv_letter(tree ft)
  {
    const char *c = convention(ft);
    if (variadic(ft)) return 'A';
    if (!c) return TREE_CODE(ft) == METHOD_TYPE ? 'E' : 'A';
    return !strcmp(c, "this") ? 'E' : !strcmp(c, "std") ? 'G' : !strcmp(c, "fast") ? 'I' : 'A';
  }

  void arg(tree t, int q, bool is_array)
  {
    q &= TYPE_QUAL_CONST | TYPE_QUAL_VOLATILE | TYPE_QUAL_RESTRICT;
    if (TYPE_REF_P(t)) q &= ~TYPE_QUAL_RESTRICT;      /* cl ignores a qualifier on a reference, also as a back-reference key */
    for (size_t i = 0; i < args.size(); i++)
      if (args[i].quals == q && args[i].array == is_array && same_type_p(args[i].type, t)) { out += (char)('0' + i); return; }
    size_t before = out.size();
    if (is_array) { out += 'Q'; type(TREE_TYPE(t), MANGLE); }
    else type(t, DROP, q);
    if (out.size() - before > 1 && args.size() < 10) args.push_back({TYPE_MAIN_VARIANT(t), q, is_array});
  }

  void function_type(tree ft, tree d, DtorKind dk)
  {
    if (TREE_CODE(ft) == METHOD_TYPE) out += qual_letter(type_memfn_quals(ft), false);
    out += conv_letter(ft);
    if (dk == DT_SCALAR || dk == DT_VECTOR) { out += "PAXI@Z"; return; }
    if (dk == DT_VBASE) { out += "XXZ"; return; }
    if (d && (DECL_CONSTRUCTOR_P(d) || DECL_DESTRUCTOR_P(d))) out += '@';
    else if (VOID_TYPE_P(TREE_TYPE(ft))) out += 'X';
    else type(TREE_TYPE(ft), RESULT);
    tree p = d ? FUNCTION_FIRST_USER_PARMTYPE(d) : TYPE_ARG_TYPES(ft);
    if (!d && TREE_CODE(ft) == METHOD_TYPE && p) p = TREE_CHAIN(p);
    tree parm = d && DECL_ARGUMENTS(d) ? FUNCTION_FIRST_USER_PARM(d) : NULL_TREE;
    const std::vector<ParmForm> *first = NULL;
    if (d && parm) {
      auto fp = first_parms.find(parm_key(d));
      if (fp != first_parms.end() && fp->second.size() == parm_forms(d).size()) first = &fp->second;
    }
    size_t pi = 0;
    bool var = variadic(ft), any = false;
    for (; p && p != void_list_node; p = TREE_CHAIN(p)) {
      any = true;
      tree t = TREE_VALUE(p);
      int q = 0; bool is_array = false;
      if (parm) {
        q = cp_type_quals(TREE_TYPE(parm));
        is_array = DECL_ARRAY_PARAMETER_P(parm);
        if (first) { q = (*first)[pi].quals; is_array = (*first)[pi].array; }
        pi++;
        parm = DECL_CHAIN(parm);
      }
      arg(t, q, is_array);
    }
    if (!any && !var) out += 'X';
    else out += var ? 'Z' : '@';
    out += 'Z';
  }

  static const char *op_code(tree d)
  {
    static const struct { const char *it, *ms; } tab[] = {
      {"nw", "?2"}, {"dl", "?3"}, {"aS", "?4"}, {"rs", "?5"}, {"ls", "?6"}, {"nt", "?7"}, {"eq", "?8"}, {"ne", "?9"},
      {"ix", "?A"}, {"pt", "?C"}, {"ml", "?D"}, {"de", "?D"}, {"pp", "?E"}, {"mm", "?F"}, {"mi", "?G"}, {"ng", "?G"},
      {"pl", "?H"}, {"ps", "?H"}, {"ad", "?I"}, {"an", "?I"}, {"pm", "?J"}, {"dv", "?K"}, {"rm", "?L"}, {"lt", "?M"},
      {"le", "?N"}, {"gt", "?O"}, {"ge", "?P"}, {"cm", "?Q"}, {"cl", "?R"}, {"co", "?S"}, {"eo", "?T"}, {"or", "?U"},
      {"aa", "?V"}, {"oo", "?W"}, {"mL", "?X"}, {"pL", "?Y"}, {"mI", "?Z"}, {"dV", "?_0"}, {"rM", "?_1"}, {"rS", "?_2"},
      {"lS", "?_3"}, {"aN", "?_4"}, {"oR", "?_5"}, {"eO", "?_6"}, {"na", "?_U"}, {"da", "?_V"},
    };
    const char *it = IDENTIFIER_OVL_OP_INFO(DECL_NAME(d))->mangled_name;
    for (auto &e : tab) if (!strcmp(e.it, it)) return e.ms;
    return NULL;
  }

  /* the whole name of a function or variable: `?name@scopes@@encoding` */
  void decl(tree d, DtorKind dk)
  {
    out += '?';
    std::string base; bool special = true;
    if (TREE_CODE(d) == FUNCTION_DECL && DECL_CONSTRUCTOR_P(d)) base = "?0";
    else if (TREE_CODE(d) == FUNCTION_DECL && DECL_DESTRUCTOR_P(d))
      base = dk == DT_SCALAR ? "?_G" : dk == DT_VECTOR ? "?_E" : dk == DT_VBASE ? "?_D" : "?1";
    else if (TREE_CODE(d) == FUNCTION_DECL && IDENTIFIER_CONV_OP_P(DECL_NAME(d))) base = "?B";
    else if (TREE_CODE(d) == FUNCTION_DECL && IDENTIFIER_ANY_OP_P(DECL_NAME(d))) {
      const char *c = op_code(d);
      if (!c) { fail("operator"); return; }
      base = c;
    } else if (!DECL_NAME(d)) { fail("a declaration without a name"); return; }
    else { base = IDENTIFIER_POINTER(DECL_NAME(d)); special = false; }

    if (TREE_CODE(d) == FUNCTION_DECL && fn_template_id(d)) {
      out += template_id(base, special, INNERMOST_TEMPLATE_ARGS(DECL_TI_ARGS(d)));
      out += '@';
    } else if (special) out += base;
    else source_name(base);
    contexts(CP_DECL_CONTEXT(d), d);
    out += '@';
    if (!why.empty()) return;

    if (TREE_CODE(d) == FUNCTION_DECL) {
      if (DECL_CONTEXT(d) && TYPE_P(DECL_CONTEXT(d))) {
        int acc = TREE_PRIVATE(d) ? 0 : TREE_PROTECTED(d) ? 1 : 2;
        bool virt = DECL_VIRTUAL_P(d);
        if (DECL_DESTRUCTOR_P(d)) {
          tree dt = CLASSTYPE_DESTRUCTOR(DECL_CONTEXT(d));
          virt = dk != DT_VBASE && dt && DECL_VIRTUAL_P(dt);
        }
        if (thunk >= 0) { out += "GOW"[acc]; number(thunk); }
        else out += TREE_CODE(TREE_TYPE(d)) != METHOD_TYPE ? "CKS"[acc] : virt ? "EMU"[acc] : "AIQ"[acc];
      } else out += 'Y';
      function_type(TREE_TYPE(d), d, dk);
    } else {
      if (DECL_CLASS_SCOPE_P(d)) out += TREE_PRIVATE(d) ? '0' : TREE_PROTECTED(d) ? '1' : '2';
      else out += DECL_FUNCTION_SCOPE_P(d) ? '4' : '3';
      tree t = TREE_TYPE(d);
      int q = cp_type_quals(t);
      if (TYPE_PTRMEMFUNC_P(t) || TREE_CODE(t) == OFFSET_TYPE) {
        type(t, DROP);
        out += qual_letter(TYPE_PTRMEMFUNC_P(t) ? 0 : cp_type_quals(TREE_TYPE(t)), true);
        type_name(TYPE_PTRMEMFUNC_P(t) ? TYPE_PTRMEMFUNC_OBJECT_TYPE(t) : TYPE_OFFSET_BASETYPE(t));
      } else if (INDIRECT_TYPE_P(t)) {
        type(t, DROP);
        if (q & TYPE_QUAL_RESTRICT) out += 'I';
        out += qual_letter(cp_type_quals(TREE_TYPE(t)), false);
      } else if (TREE_CODE(t) == ARRAY_TYPE) {
        tree e = TREE_TYPE(t);
        out += ptr_letter(cp_type_quals(e));
        type(e, MANGLE);
        out += TREE_CODE(e) == ARRAY_TYPE ? 'A' : qual_letter(q, false);
      } else {
        type(t, DROP);
        out += qual_letter(q, false);
      }
    }
  }

  void targ(tree a)
  {
    if (TYPE_P(a)) { type(a, ESCAPE); return; }
    if (TREE_CODE(a) == TEMPLATE_DECL) {
      tree t = TREE_TYPE(a);
      if (!t || !CLASS_TYPE_P(t)) { fail("template template argument"); return; }
      out += TREE_CODE(t) == UNION_TYPE ? 'T' : declared_class(t) ? 'V' : 'U';
      source_name(IDENTIFIER_POINTER(DECL_NAME(a)));
      contexts(CP_DECL_CONTEXT(a), a);
      out += '@';
      return;
    }
    bool ref = false;
    for (;;) {
      if (CONVERT_EXPR_P(a) || TREE_CODE(a) == NON_LVALUE_EXPR || TREE_CODE(a) == VIEW_CONVERT_EXPR) a = TREE_OPERAND(a, 0);
      else if (TREE_CODE(a) == INDIRECT_REF) { a = TREE_OPERAND(a, 0); ref = true; }
      else break;
    }
    if (TREE_CODE(a) == INTEGER_CST) {
      out += "$0";
      if (TYPE_UNSIGNED(TREE_TYPE(a))) number(tree_to_uhwi(a)); else snumber(tree_to_shwi(a));
    } else if (TREE_CODE(a) == ADDR_EXPR && DECL_P(TREE_OPERAND(a, 0))) {
      out += ref ? "$E" : "$1";
      decl(TREE_OPERAND(a, 0), DT_NONE);
      if (!TREE_PUBLIC(TREE_OPERAND(a, 0))) internal = true;
    } else if (VAR_OR_FUNCTION_DECL_P(a)) {
      out += "$E";
      decl(a, DT_NONE);
      if (!TREE_PUBLIC(a)) internal = true;
    } else if (TREE_CODE(a) == PTRMEM_CST) {
      tree m = PTRMEM_CST_MEMBER(a);
      if (TREE_CODE(m) == FIELD_DECL) { out += "$0"; snumber(tree_to_shwi(byte_position(m))); }
      else if (TREE_CODE(m) == FUNCTION_DECL && !DECL_VIRTUAL_P(m)) { out += "$1"; decl(m, DT_NONE); }
      else fail("pointer to a virtual member as a template argument");
    } else fail(std::string("template argument ") + get_tree_code_name(TREE_CODE(a)));
  }
};

/* ---- extern "C" names: _name, _name@N (stdcall), @name@N (fastcall); N the arguments' bytes on the x86 stack */

static std::string c_name(tree d)
{
  std::string n = IDENTIFIER_POINTER(DECL_NAME(d));
  if (TREE_CODE(d) != FUNCTION_DECL) return "_" + n;
  tree ft = TREE_TYPE(d);
  char c = M::conv_letter(ft);
  if (c != 'G' && c != 'I') return "_" + n;
  unsigned HOST_WIDE_INT bytes = 0;
  for (tree p = TYPE_ARG_TYPES(ft); p && p != void_list_node; p = TREE_CHAIN(p)) {
    HOST_WIDE_INT s = int_size_in_bytes(TREE_VALUE(p));
    bytes += s < 0 ? 4 : (s + 3) & ~(HOST_WIDE_INT)3;
  }
  return (c == 'G' ? "_" : "@") + n + "@" + std::to_string((unsigned long)bytes);
}

/* ---- rows */

struct Row { std::string sym, ms, kind; bool def; };
static std::vector<Row> rows;

static std::string asmname(tree d)
{
  const char *s = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
  return s[0] == '*' ? s + 1 : s;
}

/* cl gives a member function the user wrote in a function's local class internal linkage, and so everything
 * declared inside it (test_names.expect: only the class's implicit members and its vftable are external). */
static bool cl_internal_p(tree d)
{
  for (tree x = d; x && x != global_namespace && TREE_CODE(x) != TRANSLATION_UNIT_DECL;) {
    if (TREE_CODE(x) == FUNCTION_DECL) {
      if (DECL_CONTEXT(x) && TYPE_P(DECL_CONTEXT(x)) && !DECL_ARTIFICIAL(x)) {
        for (tree c = DECL_CONTEXT(x); c && TYPE_P(c); c = TYPE_CONTEXT(c))
          if (TYPE_CONTEXT(c) && TREE_CODE(TYPE_CONTEXT(c)) == FUNCTION_DECL) return true;
      }
      x = DECL_CONTEXT(x);
    } else if (TYPE_P(x)) x = TYPE_CONTEXT(x);
    else if (DECL_P(x)) x = DECL_CONTEXT(x);
    else break;
  }
  return false;
}

static void name_decl(tree d, bool def)
{
  Row r; r.sym = asmname(d); r.def = def;
  M m;
  if (cl_internal_p(d) && !(VAR_P(d) && DECL_VTABLE_OR_VTT_P(d)) && !(VAR_P(d) && DECL_ARTIFICIAL(d))) {
    r.kind = "internal"; rows.push_back(r); return;
  }
  if (VAR_P(d) && DECL_VTABLE_OR_VTT_P(d)) {
    bool vt = r.sym.compare(0, 4, "_ZTV") == 0;
    r.kind = vt ? "vtable" : "gcc";
  } else if (VAR_P(d) && DECL_ARTIFICIAL(d)) r.kind = "gcc";     /* an object the compiler made (typeinfo, guard, ...): no declaration */
  else if (TREE_CODE(d) == FUNCTION_DECL && lookup_attribute("fnvnx_sdd", DECL_ATTRIBUTES(d))) {
    tree a = TREE_VALUE(lookup_attribute("fnvnx_sdd", DECL_ATTRIBUTES(d)));
    tree d1 = TREE_VALUE(a);
    HOST_WIDE_INT fixed = tree_to_shwi(TREE_VALUE(TREE_CHAIN(a)));
    bool virt = integer_onep(TREE_VALUE(TREE_CHAIN(TREE_CHAIN(a))));
    /* the adjustment is an offset in the object: the exe's when the class is laid out as in the exe (layoutgate),
     * which a class with virtual bases never is here (the exe's has vbptrs, and vtordisp thunks of its own) */
    if (virt || (fixed && CLASSTYPE_VBASECLASSES(DECL_CONTEXT(d1)))) r.kind = "unnamed:destructor thunk in a class with virtual bases";
    else if (fixed) { m.thunk = -fixed; m.decl(d1, DT_VECTOR); r.kind = "dtor.sdd.thunk"; }
    else { m.decl(d1, DT_VECTOR); r.kind = "dtor.sdd"; }
  } else if (TREE_CODE(d) == FUNCTION_DECL && DECL_THUNK_P(d)) {
    tree t = THUNK_TARGET(d);
    while (TREE_CODE(t) == FUNCTION_DECL && DECL_THUNK_P(t)) t = THUNK_TARGET(t);
    m.decl(t, DECL_DESTRUCTOR_P(t) ? (DECL_DELETING_DESTRUCTOR_P(t) ? DT_SCALAR : DT_BASE) : DT_NONE);
    r.kind = "thunk";
  } else if (DECL_LANG_SPECIFIC(d) && DECL_EXTERN_C_P(d)) {
    m.out = c_name(d); r.kind = TREE_CODE(d) == FUNCTION_DECL ? "func" : "data";
  } else if (TREE_CODE(d) == FUNCTION_DECL && DECL_CONSTRUCTOR_P(d)) {
    m.decl(d, DT_NONE);
    r.kind = DECL_COMPLETE_CONSTRUCTOR_P(d) ? "ctor.c1" : DECL_BASE_CONSTRUCTOR_P(d) ? "ctor.c2" : "unnamed:constructor variant";
  } else if (TREE_CODE(d) == FUNCTION_DECL && DECL_DESTRUCTOR_P(d)) {
    bool vb = CLASSTYPE_VBASECLASSES(DECL_CONTEXT(d)) != NULL;
    if (DECL_DELETING_DESTRUCTOR_P(d)) { m.decl(d, DT_SCALAR); r.kind = "dtor.d0"; }
    else if (DECL_COMPLETE_DESTRUCTOR_P(d)) { m.decl(d, vb ? DT_VBASE : DT_BASE); r.kind = "dtor.d1"; }
    else if (DECL_BASE_DESTRUCTOR_P(d)) { m.decl(d, DT_BASE); r.kind = "dtor.d2"; }
    else r.kind = "unnamed:destructor variant";
  } else if (!DECL_LANG_SPECIFIC(d) && !DECL_NAME(d)) r.kind = "unnamed:no declaration";
  else if (!DECL_LANG_SPECIFIC(d) && TREE_CODE(d) == FUNCTION_DECL && fndecl_built_in_p(d)) {
    m.out = c_name(d); r.kind = "func";          /* a C library function the compiler knows (C linkage) */
  } else if (!DECL_LANG_SPECIFIC(d) && TREE_CODE(d) == FUNCTION_DECL && DECL_ARTIFICIAL(d)) {
    r.kind = "gcc";                              /* the C++ runtime's (personality, ...) */
  } else {
    m.decl(d, DT_NONE); r.kind = TREE_CODE(d) == FUNCTION_DECL ? "func" : "data";
  }
  if (r.kind == "thunk" && !m.why.empty()) m.why.clear(), m.out.clear();
  if (!m.why.empty()) { r.kind = "unnamed:" + m.why; m.out.clear(); }
  else if (m.internal) { r.kind = "unnamed:external symbol in an internal scope"; m.out.clear(); }
  r.ms = m.out;
  rows.push_back(r);
}

/* ---- vftables: which of the exe's vftables each address point of a GCC table group is
 *
 * MSVC names a class's vftables by the bases that lead to each vfptr (??_7C@@6B@ when there is one, ??_7C@@6BBase@@@
 * otherwise). The paths below are MSVC's own rule (the vfptr of a class: its own if it is polymorphic and no
 * non-virtual base has one it can extend, then every base's, in declaration order, a virtual base's once; paths
 * that would name two tables alike are extended by the next base on the way down). A vfptr and an address point
 * are the same table when they sit at the same offset in the object; that needs the class laid out as in the exe
 * (layoutgate's subject): a class whose vfptr offsets differ from GCC's vptr offsets gets `unnamed:` rows.
 */
struct VPath { std::vector<tree> path; tree next; HOST_WIDE_INT off; std::vector<tree> vbases; HOST_WIDE_INT full; };
static std::map<UidKey, std::vector<VPath>> vpaths_memo;
static std::map<UidKey, int> own_memo;

static bool has_own_vfptr(tree c);
static tree primary_nv_base(tree c);

static bool extendable(tree c) { if (has_own_vfptr(c)) return true; tree p = primary_nv_base(c); return p && extendable(p); }

static tree primary_nv_base(tree c)
{
  tree binfo = TYPE_BINFO(c), b;
  for (int i = 0; BINFO_BASE_ITERATE(binfo, i, b); i++)
    if (!BINFO_VIRTUAL_P(b) && extendable(BINFO_TYPE(b))) return BINFO_TYPE(b);
  return NULL_TREE;
}

static bool overrides_in(tree binfo, tree fn)
{
  tree b;
  for (int i = 0; BINFO_BASE_ITERATE(binfo, i, b); i++) {
    for (tree f = TYPE_FIELDS(BINFO_TYPE(b)); f; f = DECL_CHAIN(f))
      if (TREE_CODE(f) == FUNCTION_DECL && DECL_VIRTUAL_P(f) && !DECL_CLONED_FUNCTION_P(f)
          && (DECL_DESTRUCTOR_P(f) ? DECL_DESTRUCTOR_P(fn) : DECL_NAME(f) == DECL_NAME(fn) && !DECL_DESTRUCTOR_P(fn))
          && same_signature_p(fn, f))
        return true;
    if (overrides_in(b, fn)) return true;
  }
  return false;
}

static bool has_own_vfptr(tree c)
{
  c = TYPE_MAIN_VARIANT(c);
  auto it = own_memo.find(uid_type(c));
  if (it != own_memo.end()) return it->second;
  bool r = false;
  if (TYPE_POLYMORPHIC_P(c)) {
    tree binfo = TYPE_BINFO(c), b;
    bool polybase = false;
    for (int i = 0; BINFO_BASE_ITERATE(binfo, i, b); i++) polybase |= TYPE_POLYMORPHIC_P(BINFO_TYPE(b));
    if (!polybase) r = true;
    else if (!primary_nv_base(c)) {
      for (tree f = TYPE_FIELDS(c); f && !r; f = DECL_CHAIN(f))
        if (TREE_CODE(f) == FUNCTION_DECL && DECL_VIRTUAL_P(f) && !DECL_CLONED_FUNCTION_P(f) && !overrides_in(binfo, f)) r = true;
      /* SDD2 clears DECL_VIRTUAL_P of nothing a class declares; the destructor's own declaration stays virtual */
    }
  }
  return own_memo[uid_type(c)] = r;
}

static const std::vector<VPath> &vpaths(tree c)
{
  c = TYPE_MAIN_VARIANT(c);
  auto it = vpaths_memo.find(uid_type(c));
  if (it != vpaths_memo.end()) return it->second;
  std::vector<VPath> ps;
  if (has_own_vfptr(c)) ps.push_back({{}, c, 0, {}, 0});
  std::set<tree> seen;
  tree binfo = TYPE_BINFO(c), b;
  for (int i = 0; BINFO_BASE_ITERATE(binfo, i, b); i++) {
    tree base = TYPE_MAIN_VARIANT(BINFO_TYPE(b));
    bool virt = BINFO_VIRTUAL_P(b);
    if (virt && seen.count(base)) continue;
    for (const VPath &bp : vpaths(base)) {
      bool hit = false;
      for (tree vb : bp.vbases) hit |= seen.count(vb) != 0;
      if (hit) continue;
      VPath q = bp;
      if (q.path.empty() || q.path.back() != base) q.next = base;
      if (virt) q.vbases.push_back(base);
      else if (q.vbases.empty()) q.off += tree_to_shwi(BINFO_OFFSET(b));
      q.full = q.off;
      if (!q.vbases.empty()) q.full += tree_to_shwi(BINFO_OFFSET(binfo_for_vbase(q.vbases.front(), c)));
      ps.push_back(q);
    }
    if (virt) seen.insert(base);
    tree vb; unsigned j;
    FOR_EACH_VEC_SAFE_ELT(CLASSTYPE_VBASECLASSES(base), j, vb) seen.insert(TYPE_MAIN_VARIANT(BINFO_TYPE(vb)));
  }
  for (bool changed = true; changed;) {
    changed = false;
    std::vector<bool> amb(ps.size(), false);
    for (size_t i = 0; i < ps.size(); i++)
      for (size_t j = 0; j < ps.size(); j++) if (j != i && ps[j].path == ps[i].path) amb[i] = true;
    for (size_t i = 0; i < ps.size(); i++)
      if (amb[i] && ps[i].next) { ps[i].path.push_back(ps[i].next); ps[i].next = NULL_TREE; changed = true; }
  }
  return vpaths_memo[uid_type(c)] = ps;
}

struct VtRow { std::string sym, ms; HOST_WIDE_INT ap, off; };
static std::vector<VtRow> vtrows;

static void collect_aps(tree binfo, tree vt, std::map<HOST_WIDE_INT, HOST_WIDE_INT> &aps, std::set<tree> &seen, bool &ok)
{
  if (!seen.insert(binfo).second) return;
  tree e = BINFO_VTABLE(binfo);
  if (e && TREE_CODE(e) == POINTER_PLUS_EXPR && TREE_CODE(TREE_OPERAND(e, 0)) == ADDR_EXPR
      && TREE_OPERAND(TREE_OPERAND(e, 0), 0) == vt && tree_fits_shwi_p(TREE_OPERAND(e, 1))) {
    HOST_WIDE_INT ap = tree_to_shwi(TREE_OPERAND(e, 1)), off = tree_to_shwi(BINFO_OFFSET(binfo));
    auto it = aps.find(ap);
    if (it == aps.end()) aps[ap] = off; else if (it->second != off) ok = false;
  }
  tree b;
  for (int i = 0; BINFO_BASE_ITERATE(binfo, i, b); i++) collect_aps(b, vt, aps, seen, ok);
}

static void name_vtable(tree vt)
{
  tree c = DECL_CONTEXT(vt);
  if (!c || !CLASS_TYPE_P(c) || !COMPLETE_TYPE_P(c) || !TYPE_BINFO(c)) return;   /* the C++ runtime's own classes */
  std::string sym = asmname(vt);
  std::map<HOST_WIDE_INT, HOST_WIDE_INT> aps; std::set<tree> seen; bool ok = true;
  collect_aps(TYPE_BINFO(c), vt, aps, seen, ok);
  const std::vector<VPath> &ps = vpaths(c);
  std::set<HOST_WIDE_INT> a, b;
  for (auto &p : aps) a.insert(p.second);
  for (auto &p : ps) b.insert(p.full);
  bool same = ok && a == b && a.size() == aps.size() && b.size() == ps.size();
  for (auto &ap : aps) {
    VtRow r; r.sym = sym; r.ap = ap.first; r.off = ap.second;
    if (ps.empty()) r.ms = "";                    /* no virtual function: the exe has no vftable for it */
    else if (!same) r.ms = "unnamed:vfptr offsets differ from the exe's rule";
    else for (auto &p : ps) if (p.full == ap.second) {
      M m; m.out = "??_7"; m.type_name(c); m.out += "6B";
      for (tree base : p.path) m.type_name(base);
      m.out += '@';
      r.ms = !m.why.empty() ? "unnamed:" + m.why : m.internal ? "unnamed:internal scope" : m.out;
    }
    vtrows.push_back(r);
  }
}

/* ---- the unit */

static std::map<UidKey, Row> early;     /* by decl uid: named while parameters and bodies are still there */

static void ipa_start(void *, void *)
{
  symtab_node *n;
  FOR_EACH_SYMBOL(n) {
    tree d = n->decl;
    if (!TREE_PUBLIC(d) || !VAR_OR_FUNCTION_DECL_P(d)) continue;
    size_t before = rows.size();
    name_decl(d, false);
    early[uid_decl(d)] = rows.back();
    rows.resize(before);
    if (VAR_P(d) && DECL_VTABLE_OR_VTT_P(d) && asmname(d).compare(0, 4, "_ZTV") == 0) name_vtable(d);
  }
}

static void finish_unit(void *, void *)
{
  symtab_node *n;
  std::set<std::string> emitted;
  FOR_EACH_SYMBOL(n) {
    tree d = n->decl;
    if (!TREE_PUBLIC(d) || !VAR_OR_FUNCTION_DECL_P(d)) continue;
    bool def = n->definition && !DECL_EXTERNAL(d) && (TREE_ASM_WRITTEN(d) || n->alias);
    bool ref = TREE_SYMBOL_REFERENCED(DECL_ASSEMBLER_NAME(d));
    if (!def && !ref) continue;
    auto it = early.find(uid_decl(d));
    if (it != early.end()) { rows.push_back(it->second); rows.back().def = def; }
    else name_decl(d, def);
    emitted.insert(rows.back().sym);
  }
  /* the SDD2 slot functions: that plugin writes them as assembly in the object that defines the table, and they
   * are named only by the table's entries */
  varpool_node *v;
  FOR_EACH_DEFINED_VARIABLE(v) {
    tree vt = v->decl, init = DECL_INITIAL(vt);
    if (!DECL_VTABLE_OR_VTT_P(vt) || !init || TREE_CODE(init) != CONSTRUCTOR) continue;
    unsigned HOST_WIDE_INT ix; tree val;
    FOR_EACH_CONSTRUCTOR_VALUE(CONSTRUCTOR_ELTS(init), ix, val) {
      STRIP_NOPS(val);
      if (TREE_CODE(val) != ADDR_EXPR || TREE_CODE(TREE_OPERAND(val, 0)) != FUNCTION_DECL) continue;
      tree f = TREE_OPERAND(val, 0);
      if (!lookup_attribute("fnvnx_sdd", DECL_ATTRIBUTES(f)) || emitted.count(asmname(f))) continue;
      name_decl(f, true);
      emitted.insert(rows.back().sym);
    }
  }
  if (rows.empty() && vtrows.empty()) return;
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.sympair,\"\",%%progbits\n");
  for (auto &r : rows)
    fprintf(asm_out_file, "\t.ascii\t\"%s\\t%s\\t%s\\t%s\\n\"\n", r.sym.c_str(), r.ms.c_str(), r.kind.c_str(), r.def ? "def" : "ref");
  /* .fnvnx.names: every function and object the unit named, whether or not the object has a symbol for it (a
     function inlined everywhere has none): `<symbol>\t<MSVC name>`. Read by gates that follow a call the compiler
     inlined (tools/link/argordergate.py); the contract's pairs are .fnvnx.sympair's. */
  fprintf(asm_out_file, "\t.popsection\n\t.pushsection\t.fnvnx.names,\"\",%%progbits\n");
  {
    std::set<std::string> done;
    for (auto &e : early)
      if (!e.second.ms.empty() && done.insert(e.second.sym).second)
        fprintf(asm_out_file, "\t.ascii\t\"%s\\t%s\\n\"\n", e.second.sym.c_str(), e.second.ms.c_str());
    for (auto &r : rows)
      if (!r.ms.empty() && done.insert(r.sym).second)
        fprintf(asm_out_file, "\t.ascii\t\"%s\\t%s\\n\"\n", r.sym.c_str(), r.ms.c_str());
  }
  fprintf(asm_out_file, "\t.popsection\n\t.pushsection\t.fnvnx.vtpair,\"\",%%progbits\n");
  for (auto &r : vtrows)
      fprintf(asm_out_file, "\t.ascii\t\"%s\\t%ld\\t%s\\t%ld\\n\"\n", r.sym.c_str(), (long)r.ap, r.ms.c_str(), (long)r.off);
  fprintf(asm_out_file, "\t.popsection\n");
}

/* ---- local scopes
 *
 * A static variable or a class of a function has the number of its block in the name: the function's outermost
 * block is 2 for cl (written `?1?`; test_names.expect). The number of a deeper block counts every block opened before it,
 * including those that declare nothing; the trees do not keep those, so a deeper entity has no number here
 * (`unnamed:local scope number`). The depth is read from the front end's own scope stack when the entity is
 * declared: a block directly in the function's parameter scope (a constructor or destructor has the front end's
 * own body block in between).
 */
static bool outermost_block_p()
{
  if (!current_function_decl) return false;
  int blocks = 0;
  for (cp_binding_level *b = current_binding_level; b; b = b->level_chain) {
    if (b->kind == sk_cleanup) continue;
    if (b->kind == sk_function_parms)
      return blocks == (DECL_CONSTRUCTOR_P(current_function_decl) || DECL_DESTRUCTOR_P(current_function_decl) ? 2 : 1);
    if (b->kind != sk_block) return false;
    blocks++;
  }
  return false;
}

static void finish_decl(void *gcc_data, void *)
{
  tree d = (tree)gcc_data;
  if (d && TREE_CODE(d) == FUNCTION_DECL && DECL_ARGUMENTS(d)) first_parms.emplace(parm_key(d), parm_forms(d));   /* the first wins */
  if (d && VAR_P(d) && TREE_STATIC(d) && DECL_FUNCTION_SCOPE_P(d) && outermost_block_p()) scope_no[uid_decl(d)] = 2;
}

static void finish_type(void *gcc_data, void *)
{
  tree t = (tree)gcc_data;
  if (t && TYPE_P(t) && CLASS_TYPE_P(t) && TREE_CODE(t) == RECORD_TYPE) def_class_key[uid_type(t)] = CLASSTYPE_DECLARED_CLASS(t);
  if (t && TYPE_P(t) && (CLASS_TYPE_P(t) || TREE_CODE(t) == ENUMERAL_TYPE) && TYPE_CONTEXT(t)
      && TREE_CODE(TYPE_CONTEXT(t)) == FUNCTION_DECL && outermost_block_p())
    scope_no[uid_type(t)] = 2;
}

/* ---- CC1: the convention attribute */

static tree handle_cc(tree *node, tree name, tree args, int, bool *no_add_attrs)
{
  tree v = args ? TREE_VALUE(args) : NULL_TREE;
  if (!v || TREE_CODE(v) != STRING_CST) { error("%qE takes one string", name); *no_add_attrs = true; return NULL_TREE; }
  /* MSVC accepts the keyword on a declaration that is no function (it has no meaning there) */
  if (TREE_CODE(*node) != FUNCTION_TYPE && TREE_CODE(*node) != METHOD_TYPE) *no_add_attrs = true;
  return NULL_TREE;
}

static struct attribute_spec cc_attr = { "fnvnx_cc", 1, 1, false, true, true, false, handle_cc, NULL };
static struct attribute_spec sdd_attr = { "fnvnx_sdd", 3, 3, true, false, false, false, NULL, NULL };

static void register_attrs(void *, void *) { register_attribute(&cc_attr); register_attribute(&sdd_attr); }

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version)
{
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_names%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  if (info->argc) {   /* no silent modes: this plugin has no options */
    error("%<fnvnx_names%>: unknown plugin argument %qs", info->argv[0].key);
    return 1;
  }
  register_callback(info->base_name, PLUGIN_ATTRIBUTES, register_attrs, NULL);
  register_callback(info->base_name, PLUGIN_START_UNIT, start_unit, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_DECL, finish_decl, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_TYPE, finish_type, NULL);
  register_callback(info->base_name, PLUGIN_ALL_IPA_PASSES_START, ipa_start, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit, NULL);
  return 0;
}
