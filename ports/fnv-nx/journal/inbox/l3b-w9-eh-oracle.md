# l3b-w9: EH / SEH conformance oracle (path plan 4.5, second half; status item 4g)

Code: `ports/fnv-nx/lift/proof/ehoracle.py`, a pure function of the image bytes and Wine 9.0's rules; it imports nothing of rt/, the lifter or tools/ehtables.py (only pefile). Rules are transcriptions of Wine 9.0 `dlls/msvcrt/except_i386.c` and `cppexcept.h` (file:line in every docstring and comment):
- `cxx_frame_handler` 555-649 (magic check 562, SYNCHRONOUS/C-exception skip 566-569, unwinding pass 570-574, no tryblocks 576-580);
- `call_catch_block` 374-447 (tryblock window 395-396, catch(...) only for a C exception 418-420, first match, `cxx_local_unwind(frame, descr, start_level)` 429, new trylevel end_level + 1 at 430, continue_after_catch 445);
- `find_caught_type` 248-268 (pointer, else strcmp of `type_info.mangled`, CONST/VOLATILE flag rules);
- `cxx_local_unwind` 305-326 (invalid level -> terminate());
- `_except_handler3` 883-928 and `_except_handler4_common` 951-990, `msvcrt_local_unwind2/4` 768-817 (TRYLEVEL_END -1, V4 -2; level with no filter runs its handler), SCOPETABLE_V4 header layout 134-141, structure layouts 46-96, cppexcept.h 60-117.

Decision function: FuncInfo -> table {(state, kind): row}. State = every trylevel the frame can have (-1 .. the largest level any table names). Kind = a C exception, or each of the 11 ThrowInfos of the image set. Row = (catch block i, j, handler, unwind sequence to the tryblock's start level, trylevel after) or ContinueSearch; plus the unwinding-pass sequence per state. SEH: per scope level, the hunt chain (level, filter, handler) and the cleanup sequence.

Inputs: exe and the 7 images' bytes (gamedata/exe, gamedata/redist of fnv-nx-build, private, not in this repo), relocated to the assigned bases (derived from each image's text start in the facts); the lifter's side is the facts of l3a-lift/facts (`ehtables.json` per image, what lift/gates/gate_eh.py reads). The oracle's FuncInfo universe is its own: every 4-aligned dword of any section with a FuncInfo magic that parses strictly (counts, pointers inside the image, handlers in .text), compared with the facts' set (this checks ASM-222, "every FuncInfo is reached from a stub"). ThrowInfo and scope-table ADDRESSES and the scope table lengths are taken from the facts (code-flow facts, R1/R3/R4: not derivable from bytes alone); their contents are parsed from the bytes.

## Result (global, all 8 images, facts l3a-lift/facts at fnv-nx-build fd59184, oracle at the commit of this note)
- tables compared: 4,643 FuncInfo (exe 4,346; d3dx9_31 157; d3dx9_38 140), 218 SEH scope tables (75 + 30 + 9 + 3 + 7 + 20 + 36 + 38; exe: 73 V4 and 2 V3), 11 ThrowInfo (exe 10, d3dx9_38 1);
- states 18,325 (FuncInfo trylevels + scope levels), type (catch-block) entries 260 plus ThrowInfo type entries, exception kinds 12 (C + 11 ThrowInfos), decision rows 235,261, fields compared 43,210;
- agreement: 235,261 of 235,261 rows (100%); findings 0.
- the oracle's own FuncInfo scan found no FuncInfo that the facts miss; it also found 5 FuncInfo-magic-shaped words in the .data of atimgpud, binkw32, gdffalloutnv, libvorbis, libvorbisfile (the C-only modules) with unwind_count = 0 and tryblock_count = 0: an empty decision table (Wine returns ContinueSearch for every state and kind, :576-580). They are counted, not findings: `empty_descriptors_not_in_facts` = 5.
- the comparison bites: a mutation run (one wrong catch address, one wrong unwind prev, one wrong scope filter in a copy of the facts) gives 22 findings (funcinfo_field 2, funcinfo_decision 18, scope_field 1, scope_decision 1) and 235,242 agreeing rows; the real facts give 0. A self-test of the rule functions on a hand-made descriptor runs at the start of every run.
- type match: ThrowInfo.custom_handler = 0 in all 11 (a non-zero value would be a `throwinfo_custom_handler` finding: Wine :612-617 calls it, the lifter has no field for it).

## What the oracle does not cover (stated, not claimed)
- throw sites and ThrowInfo selection per site (R1, code flow), catch continuations (`conts`, code flow), frame-handler registrations and stubs (R3, code flow), the filter functions' own results (code), copy_exception/destructors (data movement, not dispatch), the V4 cookie header fields (gs_cookie_offset, eh_cookie_offset: read, not decided).
- the scope table length is a fact of the code (R4) taken from the facts; the oracle checks the entries it covers (prev chain valid, filter/handler in .text), not that no further entry exists.
- the oracle compares the lifter's TABLES with the bytes under Wine's rules; it does not run rt/msvcrt_eh.cc (the runtime's use of the tables is the boot machine test of w3/w9 CONTEXT spec).
The report JSON holds table addresses derived from the exe and is not committed (private); rerun: `python3 -I lift/proof/ehoracle.py --facts F --game G --redist R --out report.json`, exit 0 iff no finding.
