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
// Runs on each function body before it is lowered (PLUGIN_PRE_GENERICIZE), so -fsyntax-only suffices.
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "tree.h"
#include "cp/cp-tree.h"
#include "stringpool.h"
#include "tree-iterator.h"
#include <stdio.h>
#include <string>

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

static tree walk(tree *tp, int *walk_subtrees, void *)
{
  tree t = *tp;
  if (TYPE_P(t)) {
    *walk_subtrees = 0;
    return NULL_TREE;
  }
  if (TREE_CODE(t) != CALL_EXPR)
    return NULL_TREE;
  tree fn = get_callee_fndecl(t);
  // `f(...)` has no argument-type list at all (stdarg_p says no); in C++ every declaration is a prototype
  if (!fn || !(stdarg_p(TREE_TYPE(fn)) || !TYPE_ARG_TYPES(TREE_TYPE(fn))))
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
  walk_tree_without_duplicates(&DECL_SAVED_TREE(fn), walk, NULL);
  current = NULL_TREE;
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version)
{
  if (!plugin_default_version_check(version, &gcc_version))
    return 1;
  const char *path = NULL;
  for (int i = 0; i < info->argc; i++)
    if (!strcmp(info->argv[i].key, "out"))
      path = info->argv[i].value;
  if (!path)
    return 1;
  out = fopen(path, "a");
  if (!out)
    return 1;
  register_callback(info->base_name, PLUGIN_PRE_GENERICIZE, body, NULL);
  return 0;
}
