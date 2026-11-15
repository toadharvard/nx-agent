/* decltype_spell.inc - the spelling of a type in `.fnvnx.decltype` (grammar: link/ctypes_compat.py), THE one implementation. Included by the layout
 * plugin (what the front end declares) and by the dispatch plugin (the dispatcher's own prototype, which no front end declares), so a type is spelled
 * by one function whoever owns the declaration. Needs tree.h, stor-layout.h, diagnostic-core.h, <string>, <vector>, <algorithm>. */
static bool dl_failed = false;
static void dl_error(const char *what, const std::string &name)
{
  error("%<.fnvnx.decltype%>: %s (%s)", what, name.c_str());
  dl_failed = true;
}

/* RULE (DECLTYPE-LANG): one spelling for a C and a C++ unit. In a C++ unit the tag of a class is its name qualified by its scopes (namespaces and enclosing
 * classes, outermost first, joined by `__`, a name the C++ standard reserves in user code, so two scopes never collide), a global class is its bare name, exactly
 * as in C. A class template specialization (its arguments are part of the type, a tag cannot carry them), or a name outside [A-Za-z0-9_] (an anonymous namespace) has no row form; an anonymous class is spelled as in C (no tag, `-`): an error with the name, never a
 * skip. The members of a class are the FIELD_DECLs the front end makes (base-class subobjects and the vptr are artificial fields, spelled `-`: the layout the
 * C view sees); member functions and static members are not part of the object. A C unit's rows are unchanged (its TYPE_CONTEXT is not read). */
static bool dl_cxx() { return !strncmp(lang_hooks.name, "GNU C++", 7); }
static bool dl_ident(const char *s)
{
  if (!*s) return false;
  for (; *s; s++) if (!ISALNUM(*s) && *s != '_') return false;
  return true;
}

static std::string dl_tag(tree t)
{
  tree n = TYPE_NAME(t);
  if (n && TREE_CODE(n) == TYPE_DECL) n = DECL_ARTIFICIAL(n) ? DECL_NAME(n) : NULL_TREE;   /* a typedef name is not a tag */
  if (!n || TREE_CODE(n) != IDENTIFIER_NODE) return "-";
  std::string tag = IDENTIFIER_POINTER(n);
  if (!dl_cxx()) return tag;
  if (IDENTIFIER_ANON_P(n) && !IDENTIFIER_LAMBDA_P(n)) return "-";   /* an anonymous class: GCC's own name (._anon_N) is no source name; the row form is the C one, no tag (a typedef name for linkage is no tag either, above) */
  if (IDENTIFIER_LAMBDA_P(n)) { dl_error("a lambda closure type has no tag form", tag); return "?"; }
  if (CLASS_TYPE_P(t) && CLASSTYPE_TEMPLATE_INFO(t)) { dl_error("a class template specialization has no tag form (its arguments are part of the type)", tag); return "?"; }
  if (!dl_ident(tag.c_str())) { dl_error("a class name outside the grammar", tag); return "?"; }
  for (tree c = TYPE_CONTEXT(t); c; ) {
    tree id, up;
    if (TYPE_P(c) && CLASS_TYPE_P(c) && CLASSTYPE_TEMPLATE_INFO(c)) { dl_error("a class nested in a class template specialization has no tag form", tag); return "?"; }
    if (TYPE_P(c)) { id = TYPE_NAME(c); id = id && TREE_CODE(id) == TYPE_DECL ? DECL_NAME(id) : NULL_TREE; up = TYPE_CONTEXT(c); }
    else if (TREE_CODE(c) == NAMESPACE_DECL) { if (!DECL_CONTEXT(c)) break; id = DECL_NAME(c); up = DECL_CONTEXT(c); }   /* the global namespace has no context */
    else break;
    if (!id || TREE_CODE(id) != IDENTIFIER_NODE || !dl_ident(IDENTIFIER_POINTER(id))) { dl_error("a scope name outside the grammar (an anonymous or template scope)", tag); return "?"; }
    tag = std::string(IDENTIFIER_POINTER(id)) + "__" + tag;
    c = up;
  }
  return tag;
}

static std::string dl_word(const std::string &s)
{
  std::string w = s;
  for (char &c : w) if (c == ' ') c = '_';
  return w;
}

static std::string dl_type(tree t, std::vector<tree> &stk)
{
  if (!t || t == error_mark_node) { dl_error("an erroneous type", "?"); return "?"; }
  int q = TYPE_QUALS(t);
  const int code = TREE_CODE(t);
  std::string pre, host_entry;
  /* HOST1: a scalar that carries the type attribute fnvnx_host, on the type or on the original type of the typedef that names it, is spelled with the
   * prefix `host:` after the qualifiers. It is read before the main variant is taken: an attribute makes a variant. */
  bool host = false;
  if (code == INTEGER_TYPE) {
    host = lookup_attribute("fnvnx_host", TYPE_ATTRIBUTES(t)) != NULL_TREE;
    tree n = TYPE_NAME(t);
    if (!host && n && TREE_CODE(n) == TYPE_DECL && DECL_ORIGINAL_TYPE(n))
      host = lookup_attribute("fnvnx_host", TYPE_ATTRIBUTES(DECL_ORIGINAL_TYPE(n))) != NULL_TREE;
  }
  /* ENTRY1: a pointer that carries the type attribute fnvnx_entry (on the type, or on the original type of the typedef that names it) is spelled `entry:*...` */
  if (code == POINTER_TYPE) {
    bool entry = lookup_attribute("fnvnx_entry", TYPE_ATTRIBUTES(t)) != NULL_TREE;
    tree n = TYPE_NAME(t);
    if (!entry && n && TREE_CODE(n) == TYPE_DECL && DECL_ORIGINAL_TYPE(n))
      entry = lookup_attribute("fnvnx_entry", TYPE_ATTRIBUTES(DECL_ORIGINAL_TYPE(n))) != NULL_TREE;
    if (entry) host_entry = "entry:";
  }
  if (code != ARRAY_TYPE && code != FUNCTION_TYPE) {   /* an array's qualifiers are its element's: spelled there */
    if (q & TYPE_QUAL_CONST) pre += "const:";
    if (q & TYPE_QUAL_VOLATILE) pre += "volatile:";
    if (q & TYPE_QUAL_RESTRICT) pre += "restrict:";
    if (q & TYPE_QUAL_ATOMIC) pre += "atomic:";   /* C11 6.7.3p10: _Atomic is a qualifier of the type; 6.7.3p10 and 6.2.7p1: compatible types have equal qualifiers */
    t = TYPE_MAIN_VARIANT(t);
  }
  switch (code) {
  case POINTER_TYPE:
    return pre + host_entry + "*" + dl_type(TREE_TYPE(t), stk);
  case ARRAY_TYPE: {
    tree dom = TYPE_DOMAIN(t), mx = dom ? TYPE_MAX_VALUE(dom) : NULL_TREE;
    std::string n;
    if (mx && TREE_CODE(mx) == INTEGER_CST && tree_fits_uhwi_p(mx)) n = std::to_string(tree_to_uhwi(mx) + 1);
    return "[" + n + "]" + dl_type(TREE_TYPE(t), stk);
  }
  case FUNCTION_TYPE: {
    std::string r = dl_type(TREE_TYPE(t), stk);
    tree args = TYPE_ARG_TYPES(t);
    if (!args) return "(?)" + r;
    std::string ps;
    bool va = true;
    for (; args; args = TREE_CHAIN(args)) {
      tree a = TREE_VALUE(args);
      if (a == void_type_node) { va = false; break; }
      if (TREE_CODE(a) != ARRAY_TYPE && TREE_CODE(a) != FUNCTION_TYPE) a = TYPE_MAIN_VARIANT(a);   /* 6.7.6.3p15 */
      ps += (ps.empty() ? "" : ",") + dl_type(a, stk);
    }
    if (va) ps += (ps.empty() ? "" : ",") + std::string("...");
    return pre + "(" + ps + ")" + r;
  }
  case RECORD_TYPE: case UNION_TYPE: {
    std::string head = std::string(code == UNION_TYPE ? "union:" : "struct:") + dl_tag(t);
    if (!COMPLETE_TYPE_P(t) || std::find(stk.begin(), stk.end(), t) != stk.end()) return pre + head;
    stk.push_back(t);
    std::string ms;
    for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f)) {
      if (TREE_CODE(f) != FIELD_DECL) continue;
      bool bf = DECL_C_BIT_FIELD(f);
      const char *mn = DECL_NAME(f) && !(dl_cxx() && DECL_ARTIFICIAL(f)) ? IDENTIFIER_POINTER(DECL_NAME(f)) : "-";   /* a base subobject or the vptr has no source name */
      if (dl_cxx() && strcmp(mn, "-") && !dl_ident(mn)) { dl_error("a member name outside the grammar", mn); return "?"; }
      std::string m = std::string(mn) + ":"
                    + dl_type(bf && DECL_BIT_FIELD_TYPE(f) ? DECL_BIT_FIELD_TYPE(f) : TREE_TYPE(f), stk);
      if (bf) m += ":" + std::to_string(tree_to_uhwi(DECL_SIZE(f)));
      ms += (ms.empty() ? "" : ";") + m;
    }
    stk.pop_back();
    return pre + head + "{" + ms + "}";
  }
  case ENUMERAL_TYPE:
    if (!TREE_TYPE(t)) { dl_error("an enumeration without a compatible integer type", dl_tag(t)); return "?"; }
    return pre + "enum:" + dl_tag(t) + "=" + dl_type(TREE_TYPE(t), stk);
  case VOID_TYPE: case INTEGER_TYPE: case REAL_TYPE: case BOOLEAN_TYPE: {
    tree n = TYPE_NAME(t);
    if (n && TREE_CODE(n) == TYPE_DECL) n = DECL_NAME(n);
    if (!n && (code == BOOLEAN_TYPE || code == INTEGER_TYPE))   /* an element type the target makes without a name (the SVE predicate's 1-bit boolean): its precision and signedness are its identity */
      return pre + (code == BOOLEAN_TYPE ? "bool_bits_" : TYPE_UNSIGNED(t) ? "uint_bits_" : "int_bits_") + std::to_string(TYPE_PRECISION(t));
    if (!n || TREE_CODE(n) != IDENTIFIER_NODE) { dl_error("a scalar type without a name", "?"); return "?"; }
    return pre + (host ? "host:" : "") + dl_word(IDENTIFIER_POINTER(n));
  }
  case NULLPTR_TYPE:   /* C23 nullptr_t (c/c-decl.cc 4653 records the builtin type): one node, identified by its name */
    return pre + "nullptr_t";
  case VECTOR_TYPE: {
    /* VEC1: a vector type is spelled by the facts that GCC's own compatibility check reads: c/c-typeck.cc comptypes_internal case VECTOR_TYPE (1279-1283:
     * known_eq of TYPE_VECTOR_SUBPARTS and compatible element types; cp/typeck.cc structural_comptypes likewise) plus the layout the ABI sees (size, alignment).
     * Row `vec:<N>:<size>:<align>:` element.  N and size are poly_ints (a variable-length SVE vector has N = c0 + c1*x, x the runtime vector-length factor): the
     * decimal c0, or `c0+c1x` when c1 is not 0 (known_eq compares coefficient-wise).
     * TYPE_VECTOR_OPAQUE is not spelled: an opaque vector is a VARIANT of its non-opaque main type (tree.cc build_opaque_vector_type 10162-10180: a variant copy;
     * tree.cc 13366: "vector types may differ by TYPE_VECTOR_OPAQUE"), the main variant is taken above, and comptypes_internal ignores it.  It is made by vector
     * comparison results (c/c-typeck.cc 12725, 12886; cp/typeck.cc 6050; cp/call.cc 5674): tested with __typeof__ of such a comparison. */
    auto ps = [](poly_uint64 v) {
      std::string r = std::to_string(v.coeffs[0]);
      for (unsigned i = 1; i < NUM_POLY_INT_COEFFS; i++) if (v.coeffs[i]) r += "+" + std::to_string(v.coeffs[i]) + (i == 1 ? "x" : "y" + std::to_string(i));
      return r;
    };
    poly_uint64 size;
    if (!poly_int_tree_p(TYPE_SIZE_UNIT(t), &size)) { dl_error("a vector type whose size is not a poly_int constant", "?"); return "?"; }
    return pre + "vec:" + ps(TYPE_VECTOR_SUBPARTS(t)) + ":" + ps(size) + ":" + std::to_string(TYPE_ALIGN_UNIT(t)) + ":" + dl_type(TREE_TYPE(t), stk);
  }
  case COMPLEX_TYPE: {
    /* CPLX1: a C99 `_Complex T` / GNU `_Complex int` is two consecutive elements of T (stor-layout.cc layout_type case COMPLEX_TYPE 2416-2423: the mode is
     * GET_MODE_COMPLEX_MODE of the element's, TYPE_SIZE_UNIT its size; the alignment is the mode's, set by finalize_type_size).  Row `complex:<size>:<align>:` element,
     * size and alignment in bytes read off the node (TYPE_SIZE_UNIT, TYPE_ALIGN_UNIT), not computed from a formula.  Compatibility (c/c-typeck.cc
     * comptypes_internal: the default case compares the codes and TREE_TYPE): the same element type.  size and align are the ABI facts of that type. */
    poly_uint64 size;
    if (!poly_int_tree_p(TYPE_SIZE_UNIT(t), &size) || !size.is_constant()) { dl_error("a complex type whose size is not a constant", "?"); return "?"; }
    return pre + "complex:" + std::to_string(size.to_constant()) + ":" + std::to_string(TYPE_ALIGN_UNIT(t)) + ":" + dl_type(TREE_TYPE(t), stk);
  }
  case REFERENCE_TYPE: {
    /* REF1: a reference type has C callers (c-family/c-common.cc:4709 the builtin va_list prototypes, c-attribs.cc:2164 the mode attribute), so it is a row:
     * `ref:<size>:<align>:` pointee, size and alignment of the reference as GCC laid it out (a reference is laid out as a pointer of its mode,
     * stor-layout.cc layout_type case REFERENCE_TYPE shares the POINTER_TYPE arm). Compatibility: the same pointee (comptypes_internal compares the codes and TREE_TYPE). */
    poly_uint64 size;
    if (!poly_int_tree_p(TYPE_SIZE_UNIT(t), &size) || !size.is_constant()) { dl_error("a reference type whose size is not a constant", "?"); return "?"; }
    return pre + "ref:" + std::to_string(size.to_constant()) + ":" + std::to_string(TYPE_ALIGN_UNIT(t)) + ":" + dl_type(TREE_TYPE(t), stk);
  }
  default:
    dl_error("a type outside the grammar", get_tree_code_name((enum tree_code)code));
    return "?";
  }
}
