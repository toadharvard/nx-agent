// GCC plugin for objstub.py `terminators`: prints the assembler name of every FUNCTION_DECL the compiler itself
// marks noreturn (TREE_THIS_VOLATILE on a FUNCTION_DECL is GCC's noreturn flag; it is set by the attribute, by
// [[noreturn]] and by _Noreturn). Declarations are collected as they finish; flags are read at end of unit, so
// a later redeclaration that adds noreturn to the same decl is seen. Output: one name per line, sorted, to the file
// given as -fplugin-arg-objstub_plugin-out=FILE.
#include "gcc-plugin.h"
#include "tree.h"
#include "plugin-version.h"
#include <set>
#include <string>
#include <cstdio>
int plugin_is_GPL_compatible;
static std::set<tree> decls;
static std::string outpath;
static void on_decl(void *gcc_data, void *) {
  tree d = (tree)gcc_data;
  if (d && TREE_CODE(d) == FUNCTION_DECL) decls.insert(d);
}
static void on_unit(void *, void *) {
  std::set<std::string> names;
  for (tree d : decls)
    if (TREE_THIS_VOLATILE(d) && DECL_NAME(d)) names.insert(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d)));
  FILE *f = fopen(outpath.c_str(), "w");
  if (!f) return;
  for (auto &n : names) fprintf(f, "%s\n", n.c_str());
  fclose(f);
}
int plugin_init(plugin_name_args *a, plugin_gcc_version *v) {
  if (!plugin_default_version_check(v, &gcc_version)) return 1;
  for (int i = 0; i < a->argc; i++)
    if (std::string(a->argv[i].key) == "out") outpath = a->argv[i].value;
  if (outpath.empty()) return 1;
  register_callback(a->base_name, PLUGIN_FINISH_DECL, on_decl, NULL);
  register_callback(a->base_name, PLUGIN_FINISH_UNIT, on_unit, NULL);
  return 0;
}
