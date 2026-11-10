# method census (L6 render / total / census): D3D9 method universe vs d9cap, d3d9_exec, null device
Files: method_spec.py (parse header -> universe), method_universe.tsv, method_cov.py (statuses; assigned by reading
d9cap.c, d3d9_exec.cpp, gen_null_d3d9.py), method_coverage.tsv, method_state_semantics.md.
Regenerate (from ports/fnv-nx): `python3 -I render/total/census/method_spec.py > .../method_universe.tsv`; same for method_cov.py.
Rows are (concrete interface x vtable slot incl. inherited + IUnknown): 494 rows.
Header check: all 20 interfaces match the SDK slot counts (IDirect3DDevice9 119, IDirect3D9 17, Ex variants 22/13/134). No mismatch.
(Resource9/BaseTexture9 rows are abstract bases. Evidence line numbers in method_coverage.tsv marked ~ are approximate.)

Rows per interface: D3D9 17, Device9 119, SwapChain9 10, Resource9 11, Surface9 17, VB 14, IB 14, BaseTexture9 17,
Texture9 22, CubeTexture9 22, VolumeTexture9 22, Volume9 11, VertexDecl 5, VS 5, PS 5, StateBlock 6, Query 8,
D3D9Ex 22, SwapChain9Ex 13, Device9Ex 134.

status_d9cap (494 rows): journaled_full 35, journaled_partial 19, counted_unjournaled 3, forwarded_unjournaled 221,
not_wrapped 216 (Volume9, VolumeTexture9, StateBlock9, Query9 = 47 rows; Ex interfaces 169 rows).
IDirect3DDevice9 alone (119): full 30, partial 10, counted 3, forwarded 76.
result_recorded: no row records an HRESULT or any read-back value; journaled calls are recorded only if the call succeeded.
status_exec: executed 44, skipped 3 (StretchRect, ColorFill, SetStreamSourceFreq: unknown tags), recorded_unused 1
(SetTextureStageState), absent 446. (Caveats on executed: stream 0 only, RT 0 only, draws Skip() for FVF/fixed-function.)
status_null: real state kept 164, returns constant 330 (Set/Get constants, private data, queries, caps are canned).

Discrepancies found: README says FVF draws are counted unjournaled, d9cap.c does not count them (SetFVF is a plain
forwarder); DrawRectPatch/DrawTriPatch/GetRenderTargetData/GetFrontBufferData change pixels but are not counted.
Conclusion: d9cap is an effect recorder; "every call with all inputs and results" needs a new full-fidelity call journal.
