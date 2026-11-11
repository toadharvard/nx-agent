// GCC plugin for objstub.py `terminators`: prints the assembler name of every FUNCTION_DECL the compiler itself
// marks noreturn (TREE_THIS_VOLATILE on a FUNCTION_DECL is GCC's noreturn flag; it is set by the attribute, by
// [[noreturn]] and by _Noreturn). Declarations are collected as they finish; flags are read at end of unit, so
// a later redeclaration that adds noreturn to the same decl is seen. Output: one name per line, sorted, to the file
// given as -fplugin-arg-objstub_plugin-out=FILE.
#include "gcc-plugin.h"
#include "tree.h"
#include "plugin-version.h"
#include "langhooks.h"
#include "builtins.h"
#include <cstring>
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
  // Terminators: noreturn functions that END THE PROCESS. Unwinders (printed as "~name"): calls that continue the propagation of an
  // exception: (1) the EH-resume routines GCC itself emits for a RESX (the explicit builtins __builtin_unwind_resume and
  // __builtin_cxa_end_cleanup: whatever symbol the target's EH lowering uses), (2) in a C++ TU a noreturn function the compiler does not
  // flag nothrow: the language lets it raise an exception, so its call is a transfer of control (EH class), not process end.
  // In a C TU every noreturn function is a terminator (C has no exceptions). TREE_NOTHROW/TREE_THIS_VOLATILE are GCC's own flags.
  bool cxx = strncmp(lang_hooks.name, "GNU C++", 7) == 0;
  std::set<std::string> term, unw;
  for (int b : {(int)BUILT_IN_UNWIND_RESUME, (int)BUILT_IN_CXA_END_CLEANUP}) {
    tree d = builtin_decl_explicit((built_in_function)b);
    if (d && DECL_NAME(d)) unw.insert(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d)));
  }
  for (tree d : decls)
    if (TREE_THIS_VOLATILE(d) && DECL_NAME(d)) {
      std::string n = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
      (cxx && !TREE_NOTHROW(d) ? unw : term).insert(n);
    }
  FILE *f = fopen(outpath.c_str(), "w");
  if (!f) return;
  for (auto &n : term) fprintf(f, "%s\n", n.c_str());
  for (auto &n : unw) if (!term.count(n)) fprintf(f, "~%s\n", n.c_str());
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
