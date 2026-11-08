### J-XXXX | 2026-11-07 | stream: R4 + S4-platform (claude/fnv-recon4) -> R1 / R6 rows for S4 | status: wip | parent: recon4-013

* **did**
  - R1: crt_symbols.tsv now binds 0x00ec8d33 to strtoul, not _itoa (libc strtoul; RUNTIME.md updated) (219ee07).
  - R6 recon6-007, 6 rows (ad12c786):
    - NVAPI thunks 0x009fead6 / 0x009feae8 / 0x009febd8 (spectatorpackage's fnv_004da670::FUN_009fe*): class
      thunk, bound to `fnvnx_nvapi_not_initialized`, which returns NVAPI_API_NOT_INITIALIZED (-4). There is no
      nvapi.dll on the Switch. The lazy loader 0x009fea2a stays "needs body"; R2 suggested -2 for it, if it's ever
      referenced.
    - The CRT addresses 0x00ec330b / 0x00ec78e0 / 0x00ec79a1 get weak `__fnv_` forwarders in msvc_names.c, to
      fnvnx_lockit_dtor / fnvnx_exception_ctor / fnvnx_exception_dtor.
* **finding for the fnvlink owner** - The table already had providers for those 3 CRT addresses. fnvlink applies
  providers only on its main reference path (line ~987, `ak == 'text' and addr in providers`). Paths that spell
  `__fnv_<addr>` directly (xexit exit thunks, vtable slots, ctor-this stubs) skip the table, so a placed-but-bodyless
  address with a provider still becomes a trap stub. I tried a generic fix: an aliases.ld line per provider
  address. It broke the trial link (no platform objects there), so I reverted it and used the weak forwarders. A
  generic fix belongs in fnvlink: emit those references as the provider, the way line 987 does.
* **tests** - s4: 108 tests, 0 failed. tools/link/test_bindings: ok. All 17 tools/link tests passed while the generic
  attempt was in, before the revert.
