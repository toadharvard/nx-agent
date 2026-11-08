# recon4-025: vtgate bindings for VTFILL, test_colliding fixture

- test_colliding (S2 reported it failing at integration head) came from my name-addr ABI check, aa531516. The check
  is right: the fixture's free `FUN_00501000(int)` reference against a thiscall `Fmt::FUN_00501000(int)` passes `f`
  where the member expects `this`. The fixture's native is now a static member. a812a4e0, test ok.
- VTFILL bindings are on fnv-nx-build `reports/vtgate-r4` (69d284f), in the format S2 asked for:
  - 1,589 exe addresses: 1,570 function, 14 adjthunk, 5 deldtor.
  - The file covers the republished vtgate's short/null rows and R2's 281 link-bind-pure/vmo trap/pure rows.
  - 11 are addrmap kind C (deleting dtors the decomp named C1). They bind to `__fnvnx_cthis_<addr>`; vtbind.py
    learned this rule in 2565361d.
- Recons left: 2 (00aa77e0, 00aa7840), both being written by R2. That is down from 7.
- vtgate boot=1: 17 rows, 15 shift and 2 short. Both shorts are bound.
- vtgate 010cb798 slot 5 is a false slot: no base relocation. Reported to S2.
- Native tables longer than the exe's (R2's layout rows) are table emission, so S2's under VTFILL. Handed to S2.
