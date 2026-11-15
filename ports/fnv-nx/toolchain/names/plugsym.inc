/* plugsym.inc - THE registry of the symbols a plugin writes into the output as assembly text (RULE DECLTYPE-PLUGINSYM).
 *
 * A symbol the object has comes from one of three places: a declaration the front end made (the symbol table, which the layout plugin reads: sym_emitted), a
 * library call the back end makes (the layout plugin hooks targetm.asm_out.external_libcall), or text a plugin wrote with fprintf(asm_out_file, ...): the SDD2
 * slot functions and address-point symbols (fnvnx_sdd), the calls inside those functions, the dispatcher's calls (fnvnx_dispatch). The third place is invisible to
 * the layout plugin, so every writer says what it wrote through plugsym_note, and the layout plugin gives every registered symbol its `.fnvnx.decltype` line
 * (a row of the spelling the writer gives, or the reason `plugin`; `mangled` for a `_Z` name). A writer that does not register is found by the check "symbol table
 * = lines" (toolchain/tests/test_layout.py, dt_all; the verdict's gate of the same name).
 *
 * The registry is one object in the process: the plugins are separate shared objects loaded RTLD_GLOBAL by one cc1/cc1plus, and the function-local static of an
 * inline function with default visibility is a STB_GNU_UNIQUE symbol, which the dynamic linker binds once for all of them (the same mechanism that gives
 * every shared object the one std::cout). */
#include <map>
#include <string>
struct plugsym_registry {
  std::map<std::string, std::string> syms;   /* symbol -> its `.fnvnx.decltype` type spelling, "" when the writer gives none */
  bool reader = false;                       /* the layout plugin is loaded: it writes the lines of syms */
  /* GCC runs the PLUGIN_FINISH_UNIT callbacks in an order the plugins do not control, so the reader cannot assume the writers have finished: the lines are written
   * by whoever finishes LAST. A writer counts itself at plugin_init (plugsym_writer) and reports at the end of its finish_unit (plugsym_writer_done); the reader
   * hands its emitter over at its own finish_unit (plugsym_reader_ready). The emitter runs once, when the reader is ready and every writer is done. */
  int writers = 0, done = 0;
  bool ready = false, flushed = false;
  void (*flush)() = nullptr;
};
__attribute__((visibility("default"))) inline plugsym_registry &fnvnx_plugsym()
{
  static plugsym_registry r;
  return r;
}
static inline void plugsym_note(const std::string &sym, const std::string &spelling = "")
{
  if (sym.empty()) return;
  auto &s = fnvnx_plugsym().syms;
  auto it = s.find(sym);
  if (it == s.end()) s.emplace(sym, spelling);
  else if (it->second.empty()) it->second = spelling;
}
static inline void plugsym_try_flush()
{
  auto &r = fnvnx_plugsym();
  if (r.reader && r.ready && r.done >= r.writers && !r.flushed) { r.flushed = true; r.flush(); }
}
static inline void plugsym_writer() { fnvnx_plugsym().writers++; }
static inline void plugsym_writer_done() { fnvnx_plugsym().done++; plugsym_try_flush(); }
static inline void plugsym_reader_ready(void (*emit)()) { auto &r = fnvnx_plugsym(); r.flush = emit; r.ready = true; plugsym_try_flush(); }
/* the writer's finish_unit holds one of these: it reports on every way out of the function */
struct plugsym_writer_scope { ~plugsym_writer_scope() { plugsym_writer_done(); } };
