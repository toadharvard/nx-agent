/* fnvnx_layout - R7 (MASTER_PLAN4): the layout GCC gives every complete record type of a unit, keyed by the name
 * MSVC 2008's own debug info gives the same type.
 *
 * One text line per complete record type (class, struct, union) the unit lays out, in the object's own section
 * .fnvnx.layout (not loaded):
 *
 *     key <TAB> size <TAB> align <TAB> fields <TAB> bases <TAB> flags <TAB> itanium
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

static void add_type(tree t)
{
  while (t && (POINTER_TYPE_P(t) || TREE_CODE(t) == ARRAY_TYPE || TREE_CODE(t) == TYPE_DECL)) {
    if (TREE_CODE(t) == TYPE_DECL) t = TREE_TYPE(t); else t = TREE_TYPE(t);
  }
  if (!t || !TYPE_P(t) || !RECORD_OR_UNION_TYPE_P(t)) return;
  t = TYPE_MAIN_VARIANT(t);
  /* a pointer to member function is a record of the compiler's own making: a member's size, no layout to compare */
  if (!CLASS_TYPE_P(t) && TREE_CODE(t) != UNION_TYPE) return;
  if (found_set.insert(t).second) {
    found.push_back(t);
    keep = tree_cons(NULL_TREE, t, keep);
  }
}

static void finish_type_cb(void *gcc_data, void *) { add_type((tree)gcc_data); }

static void finish_decl_cb(void *gcc_data, void *)
{
  tree d = (tree)gcc_data;
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
  M m;
  m.type_name(t);
  if (!m.why.empty()) { why = m.why; return std::string(); }
  char c = TREE_CODE(t) == UNION_TYPE ? 'T' : declared_class(t) ? 'V' : 'U';
  return std::string(1, c) + m.out;
}

static std::string itanium(tree t) { return mangle_type_string(t); }

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
    if (!DECL_NAME(f) && RECORD_OR_UNION_TYPE_P(TREE_TYPE(f)) && ANON_AGGR_TYPE_P(TREE_TYPE(f)) && !DECL_C_BIT_FIELD(f)) {
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
  vec<tree, va_gc> *vbs = CLASS_TYPE_P(t) ? CLASSTYPE_VBASECLASSES(t) : NULL;
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

static void finish_unit_cb(void *, void *)
{
  std::vector<std::string> lines;
  for (size_t i = 0; i < found.size(); i++) close_over(found[i]);   /* found grows while it is walked */
  for (size_t i = 0; i < found.size(); i++) {
    tree t = found[i];
    if (!COMPLETE_TYPE_P(t) || !RECORD_OR_UNION_TYPE_P(t)) continue;
    if (CLASS_TYPE_P(t) && uses_template_parms(t)) continue;
    if (!tree_fits_uhwi_p(TYPE_SIZE_UNIT(t))) continue;
    std::string why;
    std::string key = key_of(t, why);
    std::set<std::string> flags;
    if (!why.empty()) flags.insert("nokey:" + why);
    if (TYPE_PACKED(t)) flags.insert("packed");
    if (TYPE_USER_ALIGN(t)) flags.insert("useralign");
    if (alignment_altered(t)) flags.insert("altered");
    std::vector<std::string> fields, bases;
    int index = 0;
    fields_of(t, 0, fields, flags, index);
    if (CLASS_TYPE_P(t) && TYPE_BINFO(t)) {
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
                    + join(fields, ',') + "\t" + join(bases, ',') + "\t" + join(fl, ',') + "\t" + itanium(t));
  }
  fprintf(asm_out_file, "\t.pushsection\t.fnvnx.layout,\"\",%%progbits\n");
  for (size_t i = 0; i < lines.size(); i++) fprintf(asm_out_file, "\t.ascii\t\"%s\\n\"\n", escape(lines[i]).c_str());
  fprintf(asm_out_file, "\t.popsection\n");
}

extern "C" int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version)
{
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_layout%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  if (!fnvnx_cxx_fe()) return 0;   /* cc1 (C): the key is MSVC's name of a C++ record; C has none of its trees (cpweak.h) */
  /* the name rules need the class-key reports and the scope numbers of toolchain/names */
  register_callback(info->base_name, PLUGIN_REGISTER_GGC_ROOTS, NULL, (void *)keep_roots);
  register_callback(info->base_name, PLUGIN_START_UNIT, start_unit, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_DECL, finish_decl_cb, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_TYPE, (plugin_callback_func)+[](void *d, void *u) { finish_type(d, u); finish_type_cb(d, u); }, NULL);
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, pre_genericize_cb, NULL);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish_unit_cb, NULL);
  return 0;
}
