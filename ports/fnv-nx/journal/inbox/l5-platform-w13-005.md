# l5-platform-w13-005: launcher gate, two open points

Private note (addendum): fnv-nx-build `l5-platform/manifest`, `notes/l5-w13-launcher-gate.md`.

- The setter reached on the equal branch of the gate is memory-only (copies the string into the setting object; no
  file API, no call into the writer). The gate itself never writes FalloutPrefs.ini.
- The generic setting writer is slot 4 of four settings-collection classes, which share one base and are singletons.
  Its only caller inside the collection classes is their SaveAll (slot 8), which writes each setting in turn.
  No caller of either slot is in a function that uses one of the four singletons directly.
- Not closed: receivers passed between functions (99 SaveAll call sites, 207 writer call sites in the exe) are not
  typed by this method; it needs a whole-exe points-to analysis. Nothing was guessed.
- No code changed, so s4/s7 were not rerun; the last counts stand.
