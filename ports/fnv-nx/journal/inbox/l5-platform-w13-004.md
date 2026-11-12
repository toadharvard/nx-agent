# l5-platform-w13-004: the launcher gate (static facts, no behaviour change)

Private note with all call sites: fnv-nx-build `l5-platform/manifest`, `notes/l5-w13-launcher-gate.md` (07bf593).

- (a) WinMain compares the stored `sD3DDevice` setting (section `Display`, file FalloutPrefs.ini, default empty
  string) with the Description field of D3D9 `GetAdapterIdentifier(adapter 0, flags 0)`, using a case-insensitive
  string compare. Different: the gate is true and WinMain starts the launcher. Equal (or no adapter): the normal
  start. On a machine with no prefs file the stored value is "", so the gate is true by construction.
- (b) ShellExecuteA(NULL, NULL, <module dir>\FalloutNVLauncher.exe, NULL, NULL, SW_SHOWNORMAL). Its result is tested
  only against 0. Zero continues the normal start; any non-zero value (success or any documented error such as
  file not found) makes WinMain return 0. The "> 32" convention is not used.
- (c) FalloutNVLauncher.exe is not in the data this session holds (the owner's exe directory and the ini presets), so
  its writes cannot be derived. The gate implies it is the writer of `sD3DDevice`; that is an inference.
- (d) The exe writes FalloutPrefs.ini itself (the Controls section from the WinMain body and the controls menu;
  typed settings through a generic collection writer), but nothing writes `sD3DDevice` before the gate. The exe
  alone never leaves the launcher branch on a fresh machine.
- Decision for the orchestrator: where the FalloutPrefs.ini with `sD3DDevice` comes from on the declared machine
  (the owner's installed machine, the launcher as a module, or a declared adapter Description). Open: whether the
  setter reached on the equal branch writes the file; the virtual callers of the generic writer.
