# l5-platform-w12-001: registry sites, RTTI receiver rule

Branch `claude/fnv-l5-platform-w12`. Private result: fnv-nx-build `l5-platform/manifest` commit 4705473c (manifest/regsites.tsv).

## Numbers (exe + d3dx9_31 + d3dx9_38, measured at the commit above)
- Reg* call sites: 20 total, 18 static, 2 dynamic. The other 16 rows are unchanged against the old table.
- Wrappers (functions passing this/param/field into a registry call): 3, all vtable slots 4, 5, 6 of class RegSettingCollection
  (vtable 0x01082ed8); direct callers 0; entries: 1 vtable slot each, all 3 bound by RTTI (receiver class + offset 0).
- Wrapper call sites: direct 0/0 bound. Virtual call sites reaching the slots: 981 / 785 / 499 call sites of slot 4 / 5 / 6 in the image
  (any class; not all receivers are typed); inside the closed class closure: 1 / 4 / 0 (these are bound, the rest are not).
- Resolved: slot 6 (0xc5d920, RegCreateKeyEx/RegOpenKeyEx): receiver typed, closure of RegSettingCollection closed (55 jobs, 0 leaks):
  subkey = "" (only writer: the constant 0 at +4 in the base constructor 0x404d00), root HKLM -> static (HKLM, "", -).
- NOT resolved (dynamic): 0xc5d73c RegSetValueExA, 0xc5d8b7 RegQueryValueExA. Value name = `[param0+8]` or a local buffer made from it.
  Missing fact: the class of param 0 (the Setting objects in the list at this+0x10c; the list is inserted through generic container code
  0x5ae3d0 with no RTTI nodes) and the values of their name field. Needs a rule for container elements; none is known that closes the class
  (callers of the slot: 981/785 virtual sites in the image, untyped receivers). Reported to the lead.
- Static keys the hive lacks: the 18 static rows add no new key against the old table (the two slot-6 sites read HKLM root itself with key "").

## Assumptions (CLAUDE.md OWNER numbers; ASSUMPTIONS.json ids are not on the trunk yet)
OWNER 1 (compiler: MSVC ABI/RTTI layout, thiscall, ctors return this), OWNER 3 (Win32 as documented: NULL handle, LPCSTR name args are
inputs), OWNER 6 (no wild accesses: a pointer reaches only its object). Scope: pemanifest.py's existing REMOVE items. No new assumption.
Encapsulation ("only member functions write fields") is NOT assumed: the pointer flow is traced from the constructors and any untraced flow
makes the class unaccounted (row dynamic).

## Code
platform/manifest/{rtti.py, regclass.py, regsites.py}; check_regsites.py (synthetic RTTI image: every entry kind, RTTI receiver with
constant writer, unbound writer, unaccounted pointer, multiple-inheritance offset 8, derived class). Selftest ok, 21 rows.

## Update 2026-11-12: key-level rule, 20 of 20 static
- Container-element rule: not implementable without a new assumption (the inserter 0x44f770 is vtable slot 1 of 6 classes, called through untyped receivers). Hooks deleted.
- Lead decision (c): a key-level rule. Static root+key with no declared value under it makes the value name irrelevant: row static, value `*`, reason `key-has-no-declared-values`. Declared hive has no value under HKLM root, so 0xc5d73c and 0xc5d8b7 are static.
- Real exe (FalloutNV.exe + d3dx9_31/38, Galaxy as contract-only): 20 sites, 20 static, 0 dynamic. Private manifest commit: see lead message.
- Synthetic cases in check_regsites.py: dynamic name under an empty static key (static), same under a key with a declared value (dynamic).

## Update 2026-11-12: TEXT_ENTRY (36) Horizon host server
- toolchain/host/host_textentry.inc (host_text_entry, libnx swkbd, mutex-serialised) + host_textconv.h (pure cut / UTF-16<->UTF-8, no libnx) + row `[FNVNX_PLAT_TEXT_ENTRY] = {5, pf_text_entry}` in host_platform.inc, include in host.c.
- tests/s4/test_textconv.cpp runs the real host_textconv.h (27 checks pass in a stand-alone harness; the full s4 build needs the generated SDK header, not available here).
- check_hostapi: TEXT_ENTRY violation gone; ACCESS_FAULT_HANDLER remains (L1/L4).
- Not done: emulator-side (L4 boot machine) server, waiting for L4's answer on file and scenario-text format; the .inc itself is not compiled against libnx here (no libnx headers in this container).
- Correction after checking the pinned libnx (tools/libnx, b156b477): the applet's cancel is MAKERESULT(Module_Libnx, LibnxError_LibAppletBadExit) (libapplet.c:138), not the bare enum; fixed in host_textentry.inc, header lines cited there. The .inc now passes `gcc -fsyntax-only` against the pinned libnx headers (newlib lock.h and arm_acle.h shimmed outside the repo for the check only).

## fontfail + declared fonts (branch claude/fnv-l5-platform-w12-fontfail)
- ASan/UBSan s4 build, fonts absent: the write past the buffer is in the TEST. gpa_font_unicode_ranges kept using an uninitialised FT_Face and
  `want[0]` of an empty vector after a failed FT_New_Face (SEGV at test_gpa_names.cpp:685), and, when the set has no bitmap System font, passed
  a 16-byte buffer to GetFontUnicodeRanges after the NULL query returned a larger size (put16 gdifont.cpp:759 via :1954, stack-buffer-overflow).
  The product writes exactly the size it returned: the Win32 call has no count, so the caller's buffer is the contract. Fixed in the test:
  REQUIRE (s4_test.h) returns on a failed precondition; the buffer is the NULL-query size.
- Fonts are a declared input: `fnvnx_gdi_set_font_dirs` is deleted. The root defines `fnvnx_declared_fonts()` (fnvnx_win32_ext.h): boot.cpp
  (sdmc:/switch/fnv/fonts/{wine,host,reg}: the layout is my choice, owner of the layout please confirm) and host_boundary.cpp (env FNVNX_FONT_REF /
  FNVNX_FONT_HOST, stops loudly if missing). A root without it does not link. No unset state, no `wine ? wine : ""`.

## declared inputs absent = broken build (branch claude/fnv-l5-platform-w12-declin)
- Layout sdmc:/switch/fnv/fonts/{wine,host,reg} is the DEVELOPMENT layout (Atmosphere exefs override, sdmc); decided again at packaging (boot.cpp comment says so).
- gdifont.cpp: declared_input_broken(what, path) prints one line (os_debug_print) and ends the process through os_exit_process(78). Closed: FT_Init_FreeType failure;
  list_dir on a missing/unreadable directory (wine, host and every host subdirectory, reg); a file that a declared directory listed and that cannot be read
  (font file, .reg, and the face file re-read by realize). Empty but present directory = valid machine. Directory entries of the wine dir are skipped (Wine opens regular files only).
- Census of platform/win32, every place that reads an input at run time: file reads = gdifont.cpp (above), registry.cpp:215 (the hive is persistence, not a declared input:
  absent = first boot, the declared rows are compiled in registry_seed.gen.inc and written by fnvnx_registry_first_run), ini.cpp:180 (player INI files: Win32 documents the
  default answer), resource.cpp:568 (player module files), path.cpp:364 / platstat.cpp:170 (player directories, diagnostics). Declared inputs compiled in (absence = build error):
  registry seed, FalloutPrefs (declared_files.gen.inc), NLS (nls_tables.inc), message table (winerr_msgs.inc), env_const.gen.inc: 0 run-time reads. Run-time declared-input reads: 1 file (gdifont.cpp), 0 left silent.
- Open: gdifont.cpp add_font_file(.., listed=false): files NAMED by the declared registry export (vgasys.fon, vgafix.fon, coure.fon, ...) that the wine dir lacks are skipped (Wine 9.0
  load_registry_fonts / load_system_bitmap_fonts skip them). My local reference lacks them. Class-closing proposal: a build-time check that every .fon the exported hive names exists in the
  declared set (w10's reference), instead of a run-time exit.

## fongate2 (option a): face census by rule
- platform/win32/font_faces.{h,cpp}: the only place a face name is spelled; gdifont/display/window request faces through w32_face_name(FACE_x).
- platform/manifest/platform_faces.py: census over the compiled platform objects (relocations to w32_face_name / GetStockObject with immediate arg). At this commit: 37 objects, 18 face requests, 14 distinct faces, 2 stock-font sites, 0 unresolved.
- platform/manifest/wine_fontmap.tsv + fontgate.py: gate declared files == mapped Wine files. Against fonts-wine 9.0~repack-4build3 (63 files): mapped 43, extra 20, missing 0, 10 faces have no file in Wine's own set (Arial, Courier New, Times New Roman, MS Shell Dlg, Liberation x3, Vera x3).
- Open for the lead: the 10 no-file faces; literal-face bypass in a function that also memcpy's a literal is not excluded by relocations; weights are family-wide.

## fongate2 rulings applied
- platform/manifest/fontgate.py derives face -> files from Wine 9.0 data (wine.inf.in FontSubstitutes, font.c default lists + shelldlg row 1252/437, fontconfig 30-metric-aliases accept lists), files = fonts-wine + fonts-liberation (libwine Depends/Recommends closure). Declared == mapped (fnv-nx-build tools/fonts-wine9 099942c, 56 files, SHA256SUMS + inputs + per-face README citations).
- Literal gate: no face-name constant (bytes or UTF-16, case-folded) in any platform object other than font_faces.cpp.o; selftested.
- RED by design: Bitstream Vera Sans/Sans Mono/Serif are named by Wine's default lists but no package of the closure ships them.

## fongate2: one /fonts directory
- fnvnx_font_dirs is `{dir}`; boot.cpp reads sdmc:/switch/fnv/fonts = exactly the fnv-nx-build tools/fonts-wine9 mirror (099942c). No wine/host/reg split; no host font dir.
- gdifont declare_machine(): the registry values (ACP, system bitmap fonts, Fonts, FontSubstitutes, Replacements) are Wine 9.0's, cited per group; no .reg files.
- fontgate.py: default lists evaluated in order, first present reached, later entries recorded unreached.
- s4 at this commit: 485 tests, 404922 checks, 0 failed (full run before the test_gdifont scan fix had 1 failed check: the format scan walked the old reference host tree; it now scans FNVNX_FONTS).
