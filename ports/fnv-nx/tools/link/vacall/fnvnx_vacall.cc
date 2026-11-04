// fnvnx_vacall.cc - GCC plugin: the argument types of every call through a variadic declaration (S2c-abi,
// docs/LINK.md "Variadic pairs").
//
//   g++ ... -fsyntax-only -fplugin=<dir>/fnvnx_vacall.so -fplugin-arg-fnvnx_vacall-out=<file> unit.cpp
//
// A call through `int f(...)` (the decomp's unprototyped declaration) or another variadic declaration passes its
// anonymous arguments as the call site's own types after the default promotions. The definition at the address
// may be fixed, so whether the pair needs a bridge depends on those types (V1 VA-U1..U4: a long long or double is
// two x86 words and one AArch64 register). For every such call the plugin appends one line to <file>:
//
//   <callee assembler name> TAB <caller assembler name> TAB <named> TAB <class of argument 0> ...
//
// <named>: the arguments before the anonymous ones (a member function's `this` included), so the anonymous
// arguments are the classes from index <named> on.
//
// classes: I8 I16 I32 I64 P F32 F64 F128 for scalars (after promotion), A<size>:<k> for an aggregate by value with
// k = H<n>x<w> (a homogeneous float/double aggregate of n members) or G (anything else), ? otherwise.
// For every variadic function the unit defines it also writes `VADEF TAB <name> TAB <state> TAB <va_arg class>...`:
// state `none` (no va_start: the anonymous arguments are never read), `escape` (its va_list is passed to another
// function, vsnprintf-like: the reads are elsewhere) or `reads` (the classes of its va_arg expressions; V2 VA2-VA4:
// a fixed reference's tail must have their width).
// With -fplugin-arg-fnvnx_vacall-want=<file> (callee assembler names, one per line) it also writes, for every call
// of such a callee, `FMT TAB <callee> TAB <caller> (TAB <argument index>:<bytes in hex>)...` with every argument
// that is a string literal or a readonly array initialized with one: the format strings of calls into definitions that hand
// their va_list to vsnprintf (vacall.py parses the conversions).
// Runs on each function body before it is lowered (PLUGIN_PRE_GENERICIZE), so -fsyntax-only suffices.
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "tree.h"
#include "cp/cp-tree.h"
#include "stringpool.h"
#include "tree-iterator.h"
#include <stdio.h>
#include <string>
#include <set>

int plugin_is_GPL_compatible;

static FILE *out;

static bool hfa(tree t, tree *elem, int *n)
{
  // float/double leaves of a record or array, all of one type; at most 4 of them
  switch (TREE_CODE(t)) {
  case REAL_TYPE:
    if (*elem && TYPE_MAIN_VARIANT(*elem) != TYPE_MAIN_VARIANT(t))
      return false;
    *elem = t;
    return ++*n <= 4;
  case ARRAY_TYPE: {
    tree d = TYPE_DOMAIN(t);
    if (!d || !TYPE_MAX_VALUE(d) || !tree_fits_shwi_p(TYPE_MAX_VALUE(d)))
      return false;
    HOST_WIDE_INT cnt = tree_to_shwi(TYPE_MAX_VALUE(d)) + 1;
    for (HOST_WIDE_INT i = 0; i < cnt; i++)
      if (!hfa(TREE_TYPE(t), elem, n))
        return false;
    return true;
  }
  case RECORD_TYPE:
    for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f))
      if (TREE_CODE(f) == FIELD_DECL && !hfa(TREE_TYPE(f), elem, n))
        return false;
    return *n > 0;
  default:
    return false;
  }
}

static std::string cls(tree t)
{
  t = TYPE_MAIN_VARIANT(t);
  switch (TREE_CODE(t)) {
  case POINTER_TYPE: case REFERENCE_TYPE: case NULLPTR_TYPE:
    return "P";
  case INTEGER_TYPE: case ENUMERAL_TYPE: case BOOLEAN_TYPE: {
    HOST_WIDE_INT b = tree_to_shwi(TYPE_SIZE(t));
    return b <= 8 ? "I8" : b <= 16 ? "I16" : b <= 32 ? "I32" : "I64";
  }
  case REAL_TYPE: {
    HOST_WIDE_INT b = tree_to_shwi(TYPE_SIZE(t));
    return b <= 32 ? "F32" : b <= 64 ? "F64" : "F128";
  }
  case RECORD_TYPE: case UNION_TYPE: case ARRAY_TYPE: {
    if (!TYPE_SIZE_UNIT(t) || !tree_fits_shwi_p(TYPE_SIZE_UNIT(t)))
      return "?";
    char buf[64];
    tree e = NULL_TREE;
    int n = 0;
    if (TREE_CODE(t) != UNION_TYPE && hfa(t, &e, &n))
      snprintf(buf, sizeof buf, "A%ld:H%dx%ld", (long)tree_to_shwi(TYPE_SIZE_UNIT(t)), n,
               (long)tree_to_shwi(TYPE_SIZE_UNIT(e)));
    else
      snprintf(buf, sizeof buf, "A%ld:G", (long)tree_to_shwi(TYPE_SIZE_UNIT(t)));
    return buf;
  }
  default:
    return "?";
  }
}

static tree current;
static std::set<std::string> want;   // -fplugin-arg-fnvnx_vacall-want=<file>: callees whose literal arguments matter

static tree string_of(tree a)
{
  // a string literal argument, or a readonly variable initialized with one (`static const char fmt[] = "..."`)
  for (;;) {
    STRIP_NOPS(a);
    if (TREE_CODE(a) == ADDR_EXPR || TREE_CODE(a) == ARRAY_REF)
      a = TREE_OPERAND(a, 0);
    else
      break;
  }
  if (TREE_CODE(a) == STRING_CST)
    return a;
  if (VAR_P(a) && TREE_READONLY(a) && DECL_INITIAL(a) && TREE_CODE(DECL_INITIAL(a)) == STRING_CST)
    return DECL_INITIAL(a);
  return NULL_TREE;
}

static void literals(tree call, tree fn)
{
  // FMT <callee> <caller> [<argument index>:<bytes in hex>]...: one line per call of a wanted callee, with every
  // argument that is a string literal (the format strings of calls into vsnprintf-forwarding definitions; a call
  // whose format is not among them has a non-literal format)
  std::string name = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fn));
  if (!want.count(name))
    return;
  std::string line = "FMT\t" + name + "\t" + (current ? IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(current)) : "-");
  for (int i = 0; i < call_expr_nargs(call); i++) {
    tree st = string_of(CALL_EXPR_ARG(call, i));
    if (!st)
      continue;
    line += "\t" + std::to_string(i) + ":";
    const char *p = TREE_STRING_POINTER(st);
    for (int k = 0; k < TREE_STRING_LENGTH(st) && p[k]; k++) {
      char b[3];
      snprintf(b, sizeof b, "%02x", (unsigned char)p[k]);
      line += b;
    }
  }
  fprintf(out, "%s\n", line.c_str());
}

static bool cur_va_start, cur_escape;
static std::string cur_reads;

static bool is_va_list(tree ty)
{
  if (!ty)
    return false;
  if (TREE_CODE(ty) == POINTER_TYPE || TREE_CODE(ty) == REFERENCE_TYPE)
    ty = TREE_TYPE(ty);
  return TYPE_MAIN_VARIANT(ty) == TYPE_MAIN_VARIANT(va_list_type_node);
}

static tree walk(tree *tp, int *walk_subtrees, void *)
{
  tree t = *tp;
  if (TYPE_P(t)) {
    *walk_subtrees = 0;
    return NULL_TREE;
  }
  if (TREE_CODE(t) == VA_ARG_EXPR) {
    cur_reads += '\t' + cls(TREE_TYPE(t));
    return NULL_TREE;
  }
  if (TREE_CODE(t) != CALL_EXPR)
    return NULL_TREE;
  tree fn = get_callee_fndecl(t);
  if (fn && !want.empty())
    literals(t, fn);
  if (fn && fndecl_built_in_p(fn, BUILT_IN_VA_START))
    cur_va_start = true;
  else if (!(fn && (fndecl_built_in_p(fn, BUILT_IN_VA_END) || fndecl_built_in_p(fn, BUILT_IN_VA_COPY))))
    for (int i = 0; i < call_expr_nargs(t); i++)
      if (is_va_list(TREE_TYPE(CALL_EXPR_ARG(t, i))))
        cur_escape = true;   // the va_list goes on to another function (vsnprintf): its reads are not here
  // `f(...)` has no argument-type list at all (stdarg_p says no); in C++ every declaration is a prototype
  if (!fn || fndecl_built_in_p(fn) || !(stdarg_p(TREE_TYPE(fn)) || !TYPE_ARG_TYPES(TREE_TYPE(fn))))
    return NULL_TREE;
  int named = 0;
  for (tree a = TYPE_ARG_TYPES(TREE_TYPE(fn)); a && TREE_VALUE(a) != void_type_node; a = TREE_CHAIN(a))
    named++;
  std::string line = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fn));
  line += '\t';
  line += current ? IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(current)) : "-";
  line += '\t' + std::to_string(named);
  for (int i = 0; i < call_expr_nargs(t); i++) {
    // the C++ front end has applied the default promotions; a non-trivially-copyable class through `...` is
    // conditionally supported and passed by invisible reference (it shows as a pointer)
    line += '\t' + cls(TREE_TYPE(CALL_EXPR_ARG(t, i)));
  }
  fprintf(out, "%s\n", line.c_str());
  return NULL_TREE;
}

static void body(void *gcc_data, void *)
{
  tree fn = (tree)gcc_data;
  if (!out || !fn || TREE_CODE(fn) != FUNCTION_DECL || !DECL_SAVED_TREE(fn))
    return;
  current = fn;
  cur_va_start = cur_escape = false;
  cur_reads.clear();
  walk_tree_without_duplicates(&DECL_SAVED_TREE(fn), walk, NULL);
  if (stdarg_p(TREE_TYPE(fn)) || !TYPE_ARG_TYPES(TREE_TYPE(fn)))
    // VADEF <name> <state> <va_arg classes in source order>; state none (no va_start) / escape / reads
    fprintf(out, "VADEF\t%s\t%s%s\n", IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(fn)),
            !cur_va_start ? "none" : cur_escape ? "escape" : "reads", cur_reads.c_str());
  current = NULL_TREE;
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version)
{
  if (!plugin_default_version_check(version, &gcc_version))
    return 1;
  const char *path = NULL;
  for (int i = 0; i < info->argc; i++) {
    if (!strcmp(info->argv[i].key, "out"))
      path = info->argv[i].value;
    if (!strcmp(info->argv[i].key, "want") && info->argv[i].value) {
      FILE *w = fopen(info->argv[i].value, "r");
      char line[4096];
      while (w && fgets(line, sizeof line, w)) {
        line[strcspn(line, "\r\n")] = 0;
        if (*line)
          want.insert(line);
      }
      if (w)
        fclose(w);
    }
  }
  if (!path)
    return 1;
  out = fopen(path, "a");
  if (!out)
    return 1;
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, body, NULL);
  return 0;
}
