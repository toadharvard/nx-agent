/* fnvnx_layout - R7 (MASTER_PLAN4): the layout GCC gives every complete record type of a unit, keyed by the name
 * MSVC 2008's own debug info gives the same type.
 *
 * One text line per complete record type (class, struct, union) the unit lays out, in the object's own section
 * .fnvnx.layout (not loaded):
 *
 *     key <TAB> size <TAB> align <TAB> fields <TAB> bases <TAB> flags <TAB> itanium <TAB> linkage
 *
 *   key      MSVC's decorated type name as cl writes it into CodeView as the "unique name" of the record
 *            (`UP@@` for struct P, `VB@@` for class B, `T...@@` for a union, `UIn@Q@@` for Q::In, `U?$TT@H@@` for
 *            TT<int>), computed by the same rules and the same code as the symbol names (toolchain/names, included
 *            below: the class key U/V as cl takes it, templates, scopes). When no rule there names the type, the key
 *            is `!` + the Itanium type string and `flags` holds `nokey:<reason>`.
 *   size     sizeof in bytes; align  alignof in bytes
 *   fields   `,`-separated, in declaration order, every non-static data member of the class itself (not of a base):
 *              name:byteoffset:size                    an ordinary member
 *              name:byteoffset:bitoffset:bitsize:size  a bit-field: bit position = byteoffset*8 + bitoffset
 *                                                      (0..7), size = sizeof of its declared type
 *              <vptr>:byteoffset:size                  the vtable pointer the class introduces
 *            a member without a name (an unnamed bit-field) is `#<index>` (its position among the fields); the
 *            members of an anonymous struct or union member are the class's own, at their offsets in it (cl's
 *            CodeView and ISO C++ scoping do the same), nested to any depth.
 *   bases    `,`-separated, `key:offset:flag`; flag `n` a direct non-virtual base, `v` a direct virtual base,
 *            `i` an indirect virtual base; offset in the complete object (a virtual base's: where GCC puts it).
 *   flags    `,`-separated: nokey:<reason>, packed (the type or a member has attribute packed), useralign (an
 *            alignment the source asked for), altered (the record's alignment is not the natural one: pack, packed,
 *            alignas / __declspec(align) on it or on a member)
 *   itanium  the Itanium type string (the key of the `types` column of .fnvnx.n3)
 *   linkage  `none` | `internal` | `external` | `c`: the linkage of the record type, C++ [basic.link], decided by GCC 13.3's own predicates
 *            (linkage_of below), not by names:
 *              none      cp/tree.cc no_linkage_check(t, false) names a type: a local class, an unnamed class without a typedef name for
 *                        linkage at namespace scope, a lambda without mangling scope, a class nested in those (the offending type
 *                        is the record itself or a type it is a member of)
 *              internal  not none, and the type's declaration is not public: cp/tree.cc decl_anon_ns_mem_p (a member of an unnamed
 *                        namespace, however deeply, also through an enclosing class) or TREE_PUBLIC cleared by the front end's
 *                        determine_visibility / constrain_visibility_for_template (cp/decl2.cc: a template instantiation with an
 *                        argument of internal linkage)
 *              external  everything else
 *            An unnamed class with a typedef name for linkage purposes is named for the front end (TYPE_UNNAMED_P is false) and takes
 *            the linkage of that name's scope.       c         every record of a C unit (cc1), whatever its scope: C11 6.2.2p6 gives an identifier that is not an object or a function
 *                        (a struct, union or enum tag, a typedef name) no linkage, and C has no ODR for tags; agreement across units is
 *                        the compatibility (6.2.7) of the declarations of external objects and functions, not the tag's name. So none of
 *                        the C++ predicates applies and the value says so.
 *
 * Declared types (section .fnvnx.decltype, every unit: the external symbols of a C unit, the C-linkage symbols of a C++ unit; not loaded): see the block comment before emit_decltype: one line `symbol <TAB> type` per
 * external symbol the unit declares or defines, in the grammar of link/ctypes_compat.py (C11 6.2.7p2).
 *
 * Boundary-forbidden types (section .fnvnx.crossing, every unit, not loaded): written by the names plugin (the block comment before cross_holds in toolchain/names/fnvnx_names.cc): one line
 * `symbol <TAB> terms` per external function the unit defines or references, the terms being ids of DATAMODEL.json crossing_forbidden.
 *
 * Objects (section .fnvnx.objects, not loaded): one line per variable definition with static storage the unit
 * makes (a global, a file-scope static, a static data member, a function-local static; not a declaration alone):
 *
 *     asm <TAB> size <TAB> align <TAB> elem <TAB> itanium <TAB> flags
 *
 *   asm      the assembler name of the object (the symbol of the object file)
 *   size     TYPE_SIZE_UNIT of the declared type (an array's size is its element size times its extent, from its type)
 *   align    TYPE_ALIGN_UNIT of the declared type (the optimiser may align the object more; that is not the type's)
 *   elem     the record key (as in the layout lines) of the innermost element type when that is a class, struct or
 *            union, else `-`; the layout and abi gates certify that record, the data-model gate certifies a scalar
 *   itanium  the Itanium type string of the declared type
 *   flags    `,`-separated: internal (not external linkage), local (function-local static)
 *  A type without a constant size (an incomplete array, a dependent type) gets no line.
 *
 * Which types: every record type the unit completes by a definition (PLUGIN_FINISH_TYPE), every record type that
 * occurs in a declaration or in the body of a function of the unit (instantiated templates, local classes), and
 * the closure of those under members and bases. The types of .fnvnx.n3 `types` are in the set.
 */
#define plugin_init fnvnx_names_plugin_init
#define plugin_is_GPL_compatible fnvnx_names_gpl
#include "../names/fnvnx_names.cc"
#undef plugin_init
#undef plugin_is_GPL_compatible

#include "stor-layout.h"
#include "cgraph.h"
#include "output.h"
#include "tree-iterator.h"
#include "tree-inline.h"

int plugin_is_GPL_compatible;

FNVNX_CXX_ONLY(no_linkage_check);
FNVNX_CXX_ONLY(decl_anon_ns_mem_p);

/* the linkage column of a layout line, by the front end's own predicates (see the header comment) */
static const char *linkage_of(tree t)
{
  t = TYPE_MAIN_VARIANT(t);
  if (!is_cxx()) return "c";   /* C11 6.2.2p6, 6.2.7: see the header comment */
  if (no_linkage_check(t, false)) return "none";
  tree d = TYPE_MAIN_DECL(t);
  if (!TREE_PUBLIC(d) || decl_anon_ns_mem_p(d)) return "internal";
  return "external";
}

static std::vector<tree> found;
static std::set<tree> found_set;

/* GCC invariant: trees live in garbage-collected memory, and ggc_collect runs between functions, freeing every tree
   no root reaches (a function-local class dies with its function body; its memory is then reused for other nodes).
   `found` is read at FINISH_UNIT, so each recorded type is also chained from `keep`, a GC root (registered at
   PLUGIN_REGISTER_GGC_ROOTS), which keeps it and everything it reaches alive until then. */
static tree keep = NULL_TREE;
static const struct ggc_root_tab keep_roots[] = {
  { &keep, 1, sizeof(keep), &gt_ggc_mx_lang_tree_node, NULL },
  LAST_GGC_ROOT_TAB
};

/* A C unit (cc1) lays out plain records and unions: no classes, bases or templates, and none of the cp/ lang flags exist (CLASS_TYPE_P and
 * ANON_AGGR_TYPE_P read cp lang flags, which mean something else in C). The key of a C record is cl's: `U<tag>@@` for a struct, `T<tag>@@` for a
 * union; a record without a tag has no name here (nokey). */
static bool cxx_class(tree t) { return is_cxx() && CLASS_TYPE_P(t); }
static bool anon_aggr(tree t) { return is_cxx() ? ANON_AGGR_TYPE_P(t) : !TYPE_NAME(t); }

static void add_type(tree t)
{
  while (t && (POINTER_TYPE_P(t) || TREE_CODE(t) == ARRAY_TYPE || TREE_CODE(t) == TYPE_DECL)) {
    if (TREE_CODE(t) == TYPE_DECL) t = TREE_TYPE(t); else t = TREE_TYPE(t);
  }
  if (!t || !TYPE_P(t) || !RECORD_OR_UNION_TYPE_P(t)) return;
  t = TYPE_MAIN_VARIANT(t);
  /* a pointer to member function is a record of the compiler's own making: a member's size, no layout to compare */
  if (is_cxx() && !CLASS_TYPE_P(t) && TREE_CODE(t) != UNION_TYPE) return;
  if (found_set.insert(t).second) {
    found.push_back(t);
    keep = tree_cons(NULL_TREE, t, keep);
  }
}

static void finish_type_cb(void *gcc_data, void *) { add_type((tree)gcc_data); }

/* variable definitions with static storage, chained from the GC root `keep_vars` until FINISH_UNIT */
static std::vector<tree> vars;
static std::set<tree> vars_set;
static tree keep_vars = NULL_TREE;
static const struct ggc_root_tab keep_vars_roots[] = {
  { &keep_vars, 1, sizeof(keep_vars), &gt_ggc_mx_lang_tree_node, NULL },
  LAST_GGC_ROOT_TAB
};

static void add_var(tree d)
{
  if (!d || TREE_CODE(d) != VAR_DECL || !TREE_STATIC(d) || DECL_EXTERNAL(d)) return;
  if (DECL_HARD_REGISTER(d)) return;   /* a global register variable is no memory: no extent */
  if (is_cxx() && (processing_template_decl || uses_template_parms(TREE_TYPE(d)))) return;   /* (cc1: no templates) */
  if (vars_set.insert(d).second) { vars.push_back(d); keep_vars = tree_cons(NULL_TREE, d, keep_vars); }
}

static void finish_decl_cb(void *gcc_data, void *)
{
  tree d = (tree)gcc_data;
  add_var(d);
  /* a compiler-made object (typeinfo, vtable, guard) carries compiler-made record types: not the source's */
  if (d && DECL_P(d) && !DECL_ARTIFICIAL(d)) add_type(TREE_TYPE(d));
  if (d) { finish_decl(gcc_data, NULL); }
}

static tree walk_body(tree *tp, int *, void *)
{
  tree t = *tp;
  if (!t) return NULL_TREE;
  if (TYPE_P(t)) add_type(t);
  else if (EXPR_P(t) || DECL_P(t) || TREE_CODE(t) == CONSTRUCTOR) {
    if (TREE_TYPE(t)) add_type(TREE_TYPE(t));
    if (TREE_CODE(t) == COMPONENT_REF) add_type(TREE_TYPE(TREE_OPERAND(t, 0)));
  }
  return NULL_TREE;
}

static void pre_genericize_cb(void *gcc_data, void *)
{
  tree fn = (tree)gcc_data;
  if (!fn || TREE_CODE(fn) != FUNCTION_DECL) return;
  add_type(TREE_TYPE(TREE_TYPE(fn)));
  for (tree p = DECL_ARGUMENTS(fn); p; p = DECL_CHAIN(p)) add_type(TREE_TYPE(p));
  if (DECL_SAVED_TREE(fn)) cp_walk_tree_without_duplicates(&DECL_SAVED_TREE(fn), walk_body, NULL);
}

/* the record's decorated name; empty and *why set when no rule names it */
static std::string record_key(tree t, std::string &why)
{
  if (!is_cxx()) {   /* C: the tag, no scopes */
    tree n = TYPE_NAME(t);
    if (n && TREE_CODE(n) == TYPE_DECL) n = DECL_NAME(n);
    if (!n || TREE_CODE(n) != IDENTIFIER_NODE) { why = "record without a tag"; return std::string(); }
    return std::string(1, TREE_CODE(t) == UNION_TYPE ? 'T' : 'U') + IDENTIFIER_POINTER(n) + "@@";
  }
  M m;
  m.type_name(t);
  if (!m.why.empty()) { why = m.why; return std::string(); }
  char c = TREE_CODE(t) == UNION_TYPE ? 'T' : declared_class(t) ? 'V' : 'U';
  return std::string(1, c) + m.out;
}

static std::string itanium(tree t)
{
  if (is_cxx()) return mangle_type_string(t);
  tree n = TYPE_NAME(t);   /* C has no mangling: the tag */
  if (n && TREE_CODE(n) == TYPE_DECL) n = DECL_NAME(n);
  return n && TREE_CODE(n) == IDENTIFIER_NODE ? IDENTIFIER_POINTER(n) : "-";
}

static std::string key_of(tree t, std::string &why)
{
  std::string k = record_key(t, why);
  if (k.empty()) k = "!" + itanium(t);
  return k;
}

static bool is_vptr(tree f)
{
  return DECL_NAME(f) && !strncmp(IDENTIFIER_POINTER(DECL_NAME(f)), "_vptr", 5);
}

static unsigned HOST_WIDE_INT size_unit(tree type)
{
  tree s = type ? TYPE_SIZE_UNIT(type) : NULL_TREE;
  return s && tree_fits_uhwi_p(s) ? tree_to_uhwi(s) : 0;
}

static void fields_of(tree t, HOST_WIDE_INT base_bits, std::vector<std::string> &out, std::set<std::string> &flags, int &index)
{
  for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f)) {
    if (TREE_CODE(f) != FIELD_DECL) continue;
    if (DECL_FIELD_IS_BASE(f)) continue;
    if (!tree_fits_shwi_p(bit_position(f))) { flags.insert("nokey:variable field offset"); continue; }
    HOST_WIDE_INT bp = base_bits + tree_to_shwi(bit_position(f));
    if (DECL_PACKED(f)) flags.insert("packed");
    if (DECL_USER_ALIGN(f)) flags.insert("useralign");
    int my = index++;
    if (DECL_ARTIFICIAL(f)) {
      if (is_vptr(f)) out.push_back("<vptr>:" + std::to_string(bp / 8) + ":" + std::to_string(size_unit(TREE_TYPE(f))));
      continue;
    }
    if (!DECL_NAME(f) && RECORD_OR_UNION_TYPE_P(TREE_TYPE(f)) && anon_aggr(TREE_TYPE(f)) && !DECL_C_BIT_FIELD(f)) {
      fields_of(TYPE_MAIN_VARIANT(TREE_TYPE(f)), bp, out, flags, index);
      continue;
    }
    std::string name = DECL_NAME(f) ? IDENTIFIER_POINTER(DECL_NAME(f)) : "#" + std::to_string(my);
    if (DECL_C_BIT_FIELD(f)) {
      tree ty = DECL_BIT_FIELD_TYPE(f) ? DECL_BIT_FIELD_TYPE(f) : TREE_TYPE(f);
      out.push_back(name + ":" + std::to_string(bp / 8) + ":" + std::to_string(bp % 8) + ":"
                    + std::to_string(tree_fits_uhwi_p(DECL_SIZE(f)) ? tree_to_uhwi(DECL_SIZE(f)) : 0) + ":"
                    + std::to_string(size_unit(ty)));
    } else
      out.push_back(name + ":" + std::to_string(bp / 8) + ":" + std::to_string(size_unit(TREE_TYPE(f))));
  }
}

/* Does the source change the alignment the record would get by the natural rule (the largest alignment of its
   members, bases, and vtable pointer)? #pragma pack, attribute packed, an alignment attribute / __declspec(align) on
   the type or a member all do; the natural alignment is what cl's CodeView lets a reader derive, the rest is not in it. */
static bool alignment_altered(tree t)
{
  unsigned nat = BITS_PER_UNIT;
  for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f)) {
    if (TREE_CODE(f) != FIELD_DECL) continue;
    if (DECL_PACKED(f) || DECL_USER_ALIGN(f)) return true;
    tree ft = DECL_C_BIT_FIELD(f) && DECL_BIT_FIELD_TYPE(f) ? DECL_BIT_FIELD_TYPE(f) : TREE_TYPE(f);
    nat = MAX(nat, TYPE_ALIGN(ft));
  }
  vec<tree, va_gc> *vbs = cxx_class(t) ? CLASSTYPE_VBASECLASSES(t) : NULL;
  unsigned ix; tree vb;
  FOR_EACH_VEC_SAFE_ELT(vbs, ix, vb) nat = MAX(nat, TYPE_ALIGN(BINFO_TYPE(vb)));
  return TYPE_ALIGN(t) != nat;
}

static std::string join(const std::vector<std::string> &v, char sep)
{
  std::string s;
  for (size_t i = 0; i < v.size(); i++) { if (i) s += sep; s += v[i]; }
  return s;
}

static std::string escape(const std::string &s)
{
  std::string o;
  char buf[8];
  for (unsigned char c : s) {
    if (c == '\\' || c == '"') { o += '\\'; o += (char)c; }
    else if (c < 32 || c > 126) { snprintf(buf, sizeof buf, "\\%03o", c); o += buf; }
    else o += (char)c;
  }
  return o;
}

/* closure under members and bases */
static void close_over(tree t)
{
  for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
    if (TREE_CODE(f) == FIELD_DECL) {
      tree ft = TREE_TYPE(f);
      size_t before = found.size();
      add_type(ft);
      (void)before;
    }
  if (TYPE_BINFO(t)) {
    tree b; int i;
    for (i = 0; BINFO_BASE_ITERATE(TYPE_BINFO(t), i, b); i++) add_type(BINFO_TYPE(b));
  }
}

/* the .fnvnx.objects lines: one per variable definition with static storage and a constant size */
static void emit_objects()
{
  std::set<std::string> seen;
  std::vector<std::string> lines;
  for (tree d : vars) {
    tree type = TREE_TYPE(d);
    if (!type || !COMPLETE_TYPE_P(type) || !tree_fits_uhwi_p(TYPE_SIZE_UNIT(type))) continue;
    if (!DECL_ASSEMBLER_NAME_SET_P(d) && !DECL_NAME(d)) continue;
    std::string name = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
    if (name.empty() || !seen.insert(name).second) continue;
    tree e = type;
    while (TREE_CODE(e) == ARRAY_TYPE) e = TREE_TYPE(e);
    std::string elem = "-";
    if (RECORD_OR_UNION_TYPE_P(e) && (!is_cxx() || CLASS_TYPE_P(e) || TREE_CODE(e) == UNION_TYPE) && COMPLETE_TYPE_P(e)) {
      std::string why;
      elem = key_of(TYPE_MAIN_VARIANT(e), why);
    }
    std::vector<std::string> fl;
    if (!TREE_PUBLIC(d)) fl.push_back("internal");
    if (decl_function_context(d)) fl.push_back("local");
    lines.push_back(name + "\t" + std::to_string(tree_to_uhwi(TYPE_SIZE_UNIT(type))) + "\t" + std::to_string(TYPE_ALIGN_UNIT(type)) + "\t"
                    + elem + "\t" + itanium(type) + "\t" + join(fl, ','));
  }
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.objects,\"\",%%progbits\n");
  for (size_t i = 0; i < lines.size(); i++) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", escape(lines[i]).c_str());
  fprintf(asm_out_file, "\t.popsection\n");
}


/* ---- Declared types of the external symbols of a C unit (section .fnvnx.decltype) ----------------------------------------------------------
 * C records have no linkage, so C's cross-unit rule is C11 6.2.7p2: every declaration of one object or function with external linkage has a
 * compatible type. One line per external symbol the unit declares or defines (every VAR_DECL and FUNCTION_DECL of the unit with TREE_PUBLIC that
 * the front end finished, or that the symbol table holds: an implicitly declared function is in the second set only):  symbol <TAB> type.
 * Grammar (link/ctypes_compat.py, which parses it): `int`, `unsigned_int` (a scalar is the front end's name of the type with `_` for blanks);
 * `*T`; `[N]T` / `[]T` (an array of known / unknown extent; a variable-length array is `[]`: 6.7.6.2p6 makes it compatible with any extent);
 * `(P,P,...)R` with `...` for varargs, `(?)R` without a prototype; `struct:TAG{m:T;m:T:W}` complete, `struct:TAG` incomplete or already under
 * expansion (a recursive type is written by its tag, not unrolled), `-` for an unnamed tag or member, `union:` likewise; `enum:TAG=T`
 * (T the enumeration's compatible integer type); qualifiers are a prefix `const:` `volatile:` `restrict:` in that order. Typedef names are
 * looked through (a typedef is not a type); the top-level qualifiers of a parameter are dropped (6.7.6.3p15).
 * A type outside this grammar (an atomic qualifier, a complex type, an incomplete enumeration) is an error of the compilation, and
 * so is a symbol declared with two different spellings in one unit (every declaration of it is compatible with the others, but the gate that
 * reads this section needs one spelling to be the symbol's type in the unit). */
#include "decltype_spell.inc"   /* dl_type: the spelling of .fnvnx.decltype, shared with fnvnx_dispatch */

static void emit_decltype()
{
  /* RULE SYMSET (names.cc sym_emitted): one row per symbol the object has (defined or referenced), spelled from the symbol table's own declaration, which is the
   * declaration the front end composed from all of the unit's declarations of it (C11 6.2.7p3). A declaration the headers bring in whose symbol the object does
   * not have is not a row: the gate compares symbols across objects, so it would add nothing but size. */
  std::vector<tree> decls;
  symtab_node *sn;
  FOR_EACH_SYMBOL(sn) {
    bool def;
    tree d = sn->decl;
    if (!sym_emitted(sn, def) || DECL_IS_UNDECLARED_BUILTIN(d) || !DECL_NAME(d)) continue;
    /* RULE (DECLTYPE-LANG): the symbols of a C unit and the symbols with C language linkage of a C++ unit (extern "C": the names C code can declare); a C++ name
     * has the ODR (.fnvnx.odrdef) and the layout gates, not C's 6.2.7 */
    if (is_cxx() && !(DECL_LANG_SPECIFIC(d) && DECL_EXTERN_C_P(d))) continue;
    /* RULE (DECLTYPE-MANGLED): a symbol whose assembler name is in the Itanium mangled-name space carries its type in its name, so it gets no row. The space is
     * fixed by two citations: the Itanium C++ ABI 5.1 (a mangled name begins with `_Z`) and C11 7.1.3p1 / C++ [lex.name] (an identifier that begins with `_` and an
     * uppercase letter is reserved to the implementation, so no user C symbol has such a name). It is decided on the assembler name, the string sym_emitted uses,
     * not on the source spelling: an extern "C" declaration of a mangled name (libstdc++'s cow-stdexcept.cc declares the std::string constructors so) is in it
     * too. Its cross-object comparison is .fnvnx.sympair / .fnvnx.odrdef, which cover mangled names. */
    const char *an = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
    if (an[0] == '_' && an[1] == 'Z') continue;
    decls.push_back(d);
  }
  std::map<std::string, std::string> rows;
  for (tree d : decls) {
    const char *nm = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
    std::vector<tree> stk;
    std::string ty = dl_type(TREE_TYPE(d), stk);
    auto it = rows.find(nm);
    if (it == rows.end()) rows[nm] = ty;
    else if (it->second != ty) dl_error("one symbol is declared with two spellings in one unit", std::string(nm) + ": " + it->second + " / " + ty);
  }
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.decltype,\"\",%%progbits\n");
  for (auto &r : rows) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", escape(r.first + "\t" + r.second).c_str());
  fprintf(asm_out_file, "\t.popsection\n");
}

/* `.fnvnx.crossing` is written by the names plugin, from the rows of `.fnvnx.sympair` (toolchain/names/fnvnx_names.cc): one universe. */

static void finish_unit_cb(void *, void *)
{
  std::vector<std::string> lines;
  for (size_t i = 0; i < found.size(); i++) close_over(found[i]);   /* found grows while it is walked */
  for (size_t i = 0; i < found.size(); i++) {
    tree t = found[i];
    if (!COMPLETE_TYPE_P(t) || !RECORD_OR_UNION_TYPE_P(t)) continue;
    if (cxx_class(t) && uses_template_parms(t)) continue;
    if (!tree_fits_uhwi_p(TYPE_SIZE_UNIT(t))) continue;
    std::string why;
    std::string key = key_of(t, why);
    /* a lambda's closure type has no name in cl's key grammar (cl: <lambda_hash>): its key `U<unnamed-tag>@..@` is shared by every
       unnamed class of the scope, so it is not a key. Named by its Itanium string instead, and `nokey` (as a local lambda already is). */
    if (is_cxx() && LAMBDA_TYPE_P(t) && why.empty()) { key = "!" + itanium(t); why = "lambda closure type"; }
    std::set<std::string> flags;
    if (!why.empty()) flags.insert("nokey:" + why);
    if (TYPE_PACKED(t)) flags.insert("packed");
    if (TYPE_USER_ALIGN(t)) flags.insert("useralign");
    if (alignment_altered(t)) flags.insert("altered");
    std::vector<std::string> fields, bases;
    int index = 0;
    fields_of(t, 0, fields, flags, index);
    if (cxx_class(t) && TYPE_BINFO(t)) {
      std::set<tree> direct;
      tree b; int k;
      for (k = 0; BINFO_BASE_ITERATE(TYPE_BINFO(t), k, b); k++) {
        tree bt = TYPE_MAIN_VARIANT(BINFO_TYPE(b));
        direct.insert(bt);
        HOST_WIDE_INT off = 0;
        if (BINFO_VIRTUAL_P(b)) {
          unsigned ix; tree vb;
          vec<tree, va_gc> *vbs = CLASSTYPE_VBASECLASSES(t);
          FOR_EACH_VEC_SAFE_ELT(vbs, ix, vb)
            if (TYPE_MAIN_VARIANT(BINFO_TYPE(vb)) == bt && tree_fits_shwi_p(BINFO_OFFSET(vb))) off = tree_to_shwi(BINFO_OFFSET(vb));
        } else if (tree_fits_shwi_p(BINFO_OFFSET(b))) off = tree_to_shwi(BINFO_OFFSET(b));
        std::string w;
        bases.push_back(key_of(bt, w) + ":" + std::to_string(off) + ":" + (BINFO_VIRTUAL_P(b) ? "v" : "n"));
        if (!w.empty()) flags.insert("nokey:base " + w);
      }
      unsigned ix; tree vb;
      vec<tree, va_gc> *vbs = CLASSTYPE_VBASECLASSES(t);
      FOR_EACH_VEC_SAFE_ELT(vbs, ix, vb) {
        tree bt = TYPE_MAIN_VARIANT(BINFO_TYPE(vb));
        if (direct.count(bt)) continue;
        std::string w;
        bases.push_back(key_of(bt, w) + ":" + std::to_string(tree_fits_shwi_p(BINFO_OFFSET(vb)) ? tree_to_shwi(BINFO_OFFSET(vb)) : 0) + ":i");
        if (!w.empty()) flags.insert("nokey:base " + w);
      }
    }
    std::vector<std::string> fl(flags.begin(), flags.end());
    lines.push_back(key + "\t" + std::to_string(tree_to_uhwi(TYPE_SIZE_UNIT(t))) + "\t" + std::to_string(TYPE_ALIGN_UNIT(t)) + "\t"
                    + join(fields, ',') + "\t" + join(bases, ',') + "\t" + join(fl, ',') + "\t" + itanium(t) + "\t" + linkage_of(t));
  }
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.layout,\"\",%%progbits\n");
  for (size_t i = 0; i < lines.size(); i++) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", escape(lines[i]).c_str());
  fprintf(asm_out_file, "\t.popsection\n");
  emit_objects();
  emit_decltype();
}

extern "C" int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version)
{
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_layout%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  if (info->argc) {   /* no silent modes: this plugin has no options */
    error("%<fnvnx_layout%>: unknown plugin argument %qs", info->argv[0].key);
    return 1;
  }
  /* the name rules need the class-key reports and the scope numbers of toolchain/names */
  register_callback(info->base_name, PLUGIN_REGISTER_GGC_ROOTS, NULL, (void *)keep_roots);
  register_callback(info->base_name, PLUGIN_REGISTER_GGC_ROOTS, NULL, (void *)keep_vars_roots);
  if (is_cxx()) {
    register_callback(info->base_name, PLUGIN_START_UNIT, start_unit, NULL);
    register_callback(info->base_name, PLUGIN_FINISH_DECL, finish_decl_cb, NULL);
    register_callback(info->base_name, PLUGIN_FINISH_TYPE, (plugin_callback_func)+[](void *d, void *u) { finish_type(d, u); finish_type_cb(d, u); }, NULL);
    register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, pre_genericize_cb, NULL);
  } else {   /* cc1: records come from the types the front end finishes and the declarations it finishes */
    register_callback(info->base_name, PLUGIN_FINISH_DECL, +[](void *d, void *) { add_var((tree)d); if (d && DECL_P((tree)d) && !DECL_ARTIFICIAL((tree)d)) add_type(TREE_TYPE((tree)d)); }, NULL);
    register_callback(info->base_name, PLUGIN_FINISH_TYPE, finish_type_cb, NULL);
  }
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit_cb, NULL);
  return 0;
}
