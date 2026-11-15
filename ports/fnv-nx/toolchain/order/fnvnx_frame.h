// fnvnx_frame.h - the one writer of the `.fnvnx.frame` records (rule R1 of class B), shared by the plugins that emit a function's frame
// facts: fnvnx_order (every compiled function) and fnvnx_sdd (the thunks it writes as assembler). Included by both and listed as an input of both
// in verify/recipe.py PLUGIN_INPUTS; no plugin writes a row any other way.
//
// Row (link/framesec.py): SYMBOL TAB RANGES TAB args:N, LF-terminated.
//   RANGES  empty | `dynamic` | pairs OFFSET:SIZE or OFFSET:SIZE:n (sorted), then `unk:CAUSE` tokens (sorted, each once), one blank between tokens;
//   args:N  N = crtl->args.size: the bytes of incoming stack arguments at [CFA, CFA+N), decimal, no sign, no leading zeros.
// A pair without `:n` is a variable with escapes=1 (another thread may hold its address: every access to it is ordered); `:n` is escapes=0 (GCC's points-to
// solution of the final GIMPLE says the address stays in this thread: see frame_vars_pass in fnvnx_order.cc for the rule). A slot shared by variables is `:n` only
// when every one of them is. OFFSET is relative to the CFA (the stack pointer at function entry). `unk:CAUSE` records an address-taken variable whose slot is not a
// constant offset of this frame (reg: DECL_RTL is a register; mem: a MEM through a pseudo; size: no constant size).
#ifndef FNVNX_FRAME_H
#define FNVNX_FRAME_H
#include <cstdio>
#include <map>
#include <string>

struct fnvnx_frame_row {
  std::string ranges;   // the RANGES field, complete
  long args;            // crtl->args.size
};

// writes the rows of one unit as one `.fnvnx.frame` chunk of the assembler output
static inline void fnvnx_emit_frame_rows(FILE *out, const std::map<std::string, fnvnx_frame_row> &rows) {
  if (rows.empty()) return;
  fprintf(out, "\t.pushsection\t.fnvnx.frame,\"\",%%progbits\n");
  for (auto &r : rows) fprintf(out, "\t.ascii\t\"%s\\t%s\\targs:%ld\\n\"\n", r.first.c_str(), r.second.ranges.c_str(), r.second.args);
  fprintf(out, "\t.popsection\n");
}
#endif
