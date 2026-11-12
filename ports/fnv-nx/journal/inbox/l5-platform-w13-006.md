# l5-platform-w13-006: the declared FalloutPrefs.ini (launcher gate), settings coverage

Private tables and note: fnv-nx-build `l5-platform/manifest`, `manifest/prefs_settings.tsv`, `manifest/prefs_required.tsv`, `notes/l5-w13-launcher-gate.md` (addendum 2).

- Rule (owner): FalloutPrefs.ini holds exactly the keys the exe's own code requires before its main path, with the declared
  machine's values; every other key is absent and the exe uses its built-in defaults.
- Default coverage (pass `platform/manifest/prefs_settings.py`, over the whole exe): the two settings collections that load
  FalloutPrefs.ini are found by rule (the file name cell appended to a buffer, then the collection's Load), their setting
  constructors by rule (call the collection getter, then its Add). Settings registered: 74; with a default: 74; without: 0. Every
  setting constructor takes the default as a mandatory argument and stores it, so 0 is also a property of the constructor.
- Required before the main path (rule: the callee tested right before the launcher start, and its direct callees): one setting,
  `sD3DDevice` in `[Display]`.
- Generator `platform/manifest/gen_prefs_ini.py` (selftest `check_gen_prefs.py`): reads the required row and the adapter identity images
  of both profiles (1100 bytes each, byte-equal required; Description at offset 512), emits the file text as a generated include. Nothing
  is hand-written and no copy of the value is in this tree.
- Platform: `platform/win32/declared_files.cpp` makes the file under CSIDL_PERSONAL `My Games\FalloutNV\` when it does not exist, called from
  boot before WinMain (same shape as the registry first run).
- Tests (`tests/s4/test_declared_prefs.cpp`, synthetic identity images): the first run makes exactly one key; the gate predicate on what
  GetPrivateProfileStringA returns is equal (the normal-start branch), and is not equal for the default (the launcher branch); an existing file is
  never overwritten.
- Open (not blocking the first frame, per the lead): whether the exe's SaveAll later rewrites the file; receivers of its virtual call are
  not typed.
