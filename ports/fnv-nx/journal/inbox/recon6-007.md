### J-XXXX | 2026-11-07 | stream: R6-recon (claude/fnv-recon6) | status: wip | parent: J-0156

* **sweep of the D23 dry-run list** - fnv-nx-build s2d/list/0540aff0d154 (R2, bc323500): 808 recon rows.
  - Filter: no recon_note; not in a shard an active worker holds (1, 2, 7) or that R6 has finished (3, 5, 6, 9); no
    `recon/<unit>` branch for the home unit (266 branches checked). Left: 9 rows.
    - Shards 0, 4 and 8: every unit with open rows already has a recon branch (58 + 62 + 57 rows).
  - None of the 9 is recon-closable:
    - 3 shard-0 extern-text names of spectatorpackage land on `jmp dword ptr [0x011a62xx]` thunks into a table
      filled at runtime (GetProcAddress-style dynamic imports): S4 platform imports.
    - 3 code-defmissing rows are CRT code: 0x00ec330b `_Lockit::~_Lockit`, 0x00ec78e0 and 0x00ec79a1
      `std::exception` members. S4 CRT provider.
    - 3 extern-text data anchors at bogus addresses (0x0, 0x65, 0x14): not functions.
* **state** - R6 has no recon-closable row left on the dry-run list. It re-checks when the authoritative list on
  full1's newest-head link is published.
* **metric** - unchanged: shards 5+3 23, shard 6 18, shard 9 16 open rows, every one routed (recon6-004/005/006).
