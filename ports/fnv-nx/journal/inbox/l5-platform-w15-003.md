# l5-platform-w15-003: ssce5432.dll reach, galaxy.dll negative test, module-base tables

Branch claude/fnv-l5-platform-w15, rebased on origin/claude/fnv-l5-platform. Facts: fnv-nx-build l1-contract/mp4 @ 0772c61 (modules.tsv, modules_declared.tsv) and
l5-platform/manifest @ 3f6c5a60 (imports.tsv, dynsites.tsv, closure.tsv).

## 1. ssce5432.dll: not reached (from facts)
- imports.tsv (every module of the closure: exe + 6 modules, 1041 rows): 0 rows with dll ssce5432.dll (and 0 with comdlg32.dll).
- dynsites.tsv (265 sites): 0 rows whose operand names ssce5432 or comdlg32. closure.tsv: ssce5432.dll = not-in-static-closure.
- No module reaches it by import. The dynamic lookups that remain unresolved in the facts (operand `dynamic`): LoadLibraryA at falloutnv.exe 0x00da284a,
  atimgpud.dll 0x1000160e, d3dx9_38.dll 0x0056b719; GetProcAddress at falloutnv.exe 0x00ed134e, atimgpud.dll 0x10001608, d3dx9_38.dll 0x0056b73c;
  GetModuleHandle sites (exe x4, binkw32 x2, atimgpud, libvorbis, libvorbisfile, ...). I resolved one from the exe: 0x00da284a is
  `LoadLibraryA([0x011b76d0])`; that global is read once in code (no store to it in .text) and its initial value points at "dbghelp.dll" (.data 0x010d4c54).
  The other two LoadLibraryA sites I did not resolve.
- Decision by the rule given: not reached by an import entry and by no resolved site -> ssce5432.dll stays declared with no served entries; comdlg32
  is not made a platform DLL; no bridge rows, no tests, nothing changed. Caveat (CLAUDE.md "certificates valid only once every dynamic-name site is
  resolved"): until the 2 remaining dynamic LoadLibraryA sites are resolved this is a measurement-build statement. (ssce5432.dll is the SSCE
  spelling library of the editor tools; no game string names it.)

## 2. galaxy.dll negative test
check_galaxy.py --selftest builds three fact dirs from the real one and requires red on each: a galaxy.dll import for a name without a body; a dynamic
site whose operand names galaxy.dll + a function; a body whose import is removed. All three red; the real facts stay green (11 reached, 11 bodies).

## 3. Tables keyed by module bases
L5 files or tables keyed by the assigned bases of gdffalloutnv / libvorbis / libvorbisfile (or of any module of modules.tsv): 0.
- grep of the tree for the assigned bases of all 7 modules (014 10000, 01440000, 014b0000, 01720000, 01af0000, 01ce0000, 01e20000): 0 hits.
- The L5 manifest facts (imports/dynsites/regsites) use the modules' PREFERRED-base VAs (e.g. libvorbis 0x10006cc0), not assigned bases; they do not change.
- The only consumer of bases is loader/include/fnvnx_modules.h (the descriptor type) over the table that contract/modules.py generates (L1): nothing
  to refresh by hand or by an L5 generator.

## 4. Results
See the head and counts in the message to the lead (s4 / s7 run on the rebased head).
