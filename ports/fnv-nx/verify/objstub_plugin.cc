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
#include "options.h"
#include <libgen.h>
#include <cstring>
#include <set>
#include <vector>
#include <string>
#include <cstdio>
int plugin_is_GPL_compatible;
static std::set<tree> decls, defs, declared_noreturn;   // declared_noreturn: noreturn as the source DECLARES it (read while parsing)
static std::string outpath, retpath;
static std::vector<std::string> sjdirs;   // directories of the PINNED libc/runtime headers: only a <setjmp.h> inside one of them is ISO C 7.13's
static void on_decl(void *gcc_data, void *) {
  tree d = (tree)gcc_data;
  if (d && TREE_CODE(d) == FUNCTION_DECL) {
    decls.insert(d);
    if (TREE_THIS_VOLATILE(d)) declared_noreturn.insert(d);
  }
}
static void on_def(void *gcc_data, void *) {
  tree d = (tree)gcc_data;   // PLUGIN_FINISH_PARSE_FUNCTION: a function definition has been parsed
  if (d && TREE_CODE(d) == FUNCTION_DECL) {
    defs.insert(d);
    if (TREE_THIS_VOLATILE(d)) declared_noreturn.insert(d);
  }
}
static void on_unit(void *, void *) {
  // Terminators: noreturn functions that END THE PROCESS. Unwinders (printed as "~name"): calls that continue the propagation of an
  // exception: (1) the EH-resume routines GCC itself emits for a RESX (the explicit builtins __builtin_unwind_resume and
  // __builtin_cxa_end_cleanup: whatever symbol the target's EH lowering uses), (2) in a C++ TU a noreturn function the compiler does not
  // flag nothrow: the language lets it raise an exception, so its call is a transfer of control (EH class), not process end.
  // In a C TU, and in a C++ TU compiled with -fno-exceptions (flag_exceptions == 0: no handler exists, a throw ends the process), every noreturn
  // function is a terminator. TREE_NOTHROW/TREE_THIS_VOLATILE and flag_exceptions are GCC's own flags.
  // (3) A function declared by <setjmp.h> (ISO C 7.13: longjmp, siglongjmp, _longjmp) is a control transfer to the caller of a setjmp, not process end.
  bool cxx = strncmp(lang_hooks.name, "GNU C++", 7) == 0 && flag_exceptions;
  std::set<std::string> term, unw;
  for (int b : {(int)BUILT_IN_UNWIND_RESUME, (int)BUILT_IN_CXA_END_CLEANUP}) {
    tree d = builtin_decl_explicit((built_in_function)b);
    if (d && DECL_NAME(d)) unw.insert(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d)));
  }
  for (tree d : decls)
    if (declared_noreturn.count(d) && DECL_NAME(d)) {   // DECLARED noreturn: the flag GCC's own inference sets later (pure-const) is not a declaration
      std::string n = IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d));
      const char *src = DECL_SOURCE_FILE(d);
      std::string path = src ? src : "", base = path.substr(path.find_last_of('/') + 1);
      bool sj = false;   // declared by the pinned <setjmp.h>: the file is named setjmp.h AND lies inside a pinned header directory
      if (base == "setjmp.h")
        for (auto &dir : sjdirs) if (path.compare(0, dir.size() + 1, dir + "/") == 0) sj = true;
      ((cxx && !TREE_NOTHROW(d)) || sj ? unw : term).insert(n);
    }
  FILE *f = fopen(outpath.c_str(), "w");
  if (!f) return;
  for (auto &n : term) fprintf(f, "%s\n", n.c_str());
  for (auto &n : unw) if (!term.count(n)) fprintf(f, "~%s\n", n.c_str());
  fclose(f);
  if (!retpath.empty()) {
    // The typedef name of the return type of every function DEFINITION (`FT_Error f(...) {..}`: TYPE_NAME of the return type is a TYPE_DECL), one
    // `assembler-name<TAB>typedef` line, sorted. It is the compiler's own type, not a text match; the error convention of a library
    // (recipe external_errors) is read against it.
    std::set<std::string> rows;
    for (tree d : defs)
      if (DECL_NAME(d) && DECL_ASSEMBLER_NAME_SET_P(d)) {   // a template pattern has no assembler name (mangling it would be wrong): only emitted definitions
        tree t = TREE_TYPE(TREE_TYPE(d)), n = t ? TYPE_NAME(t) : NULL_TREE;
        if (n && TREE_CODE(n) == TYPE_DECL && DECL_NAME(n))
          rows.insert(std::string(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d))) + "\t" + IDENTIFIER_POINTER(DECL_NAME(n)));
      }
    FILE *g = fopen(retpath.c_str(), "w");
    if (!g) return;
    for (auto &r : rows) fprintf(g, "%s\n", r.c_str());
    fclose(g);
  }
}
int plugin_init(plugin_name_args *a, plugin_gcc_version *v) {
  if (!plugin_default_version_check(v, &gcc_version)) return 1;
  for (int i = 0; i < a->argc; i++)
    if (std::string(a->argv[i].key) == "out") outpath = a->argv[i].value;
    else if (std::string(a->argv[i].key) == "ret") retpath = a->argv[i].value;
    else if (std::string(a->argv[i].key) == "setjmp-dirs") {
      std::string v = a->argv[i].value;
      for (size_t p = 0, q; p <= v.size(); p = q + 1) { q = v.find(':', p); if (q == std::string::npos) q = v.size(); if (q > p) sjdirs.push_back(v.substr(p, q - p)); }
    }
  if (outpath.empty()) return 1;
  register_callback(a->base_name, PLUGIN_FINISH_DECL, on_decl, NULL);
  register_callback(a->base_name, PLUGIN_FINISH_PARSE_FUNCTION, on_def, NULL);
  register_callback(a->base_name, PLUGIN_FINISH_UNIT, on_unit, NULL);
  return 0;
}
