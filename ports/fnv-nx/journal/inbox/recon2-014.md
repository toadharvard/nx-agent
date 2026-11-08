# recon2-014 | 2026-11-08 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-013

* **trap loop, 97deacd38aec list** (orchestrator decisions of 07:39 applied):
  - Unplaced copies, decision 1: two in-copy edits, each backed by exe bytes.
    - `sbuf_unlock` (97de-1357): inline.
    - `sbstub_alloc` (97de-1372/1373): replace pair.
    - 97de-1364 is closed at head by readbuf2.
  - MSVC STL internals, decision 2: `fill_vec` (97de-1054/1055) inline.
  - Correction to the earlier inline guess: `_Allocate<vector<T>>` is a real out-of-line function. `igeo_alloc_vec`
    (97de-1056/1058) binds both instances to the one 24-byte-element allocate that frondengine.cpp already places. Its
    native GCC signature returns the pointer.
  - parser839 family: `zz_parser839_key` (97de-165) and `parser839_ios_input` (97de-191/193), both inline. 97de-164 is
    closed at head.
  - 97de-6/7/8 (BaseFormComponent) belong to the pure-virtual class note S2-bodies already has (d1ed-216).
  - 97de-4 is with a worker.
* **combined checks**: every recon on one unit is merged in REC1 sorted order and checked with MSVC and GCC together.
  - blk_00ac0000_od_ob1_variant: 11 recons, 168/168 functions, 58022/58022 bytes, 3 replace pairs each apply once, gcc 0.
  - indexedgeometry_ob1_variant: 16/16 functions, 11630/11630 bytes, gcc 0.
* **stale branches**: the remote still has fnv-nx-build `recon/staging_blk_00ac0000_od_ob1_variant_readbuf` and
  `..._parser839`. REC1-DUP skips both whole (count -3), because their functions entries are placed by
  blk_00ac0000_od.cpp now. The readbuf2/parser839b copies are the ones that apply, so the build is unaffected, but each
  prepare reports the two skips.
* **TLS rows: not a defect (tool)**. The list reader (R2's elftraps.py) took the 30 `__fnvnx_tls_*` STT_TLS `.tbss`
  blocks for 4096-byte data stubs.
  - The reader now skips STT_TLS symbols (readelf symbol type TLS).
  - On the 97de guest the trap names go from 252 to 222, and only the 30 TLS names drop out.
  - The next list will show 30 fewer rows.
