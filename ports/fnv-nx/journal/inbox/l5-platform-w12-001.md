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
