# D3D9 journaling wrapper generator (RD1)

One generated wrapper, compiled twice: (A) i686 on Windows/Wine around the real d3d9 (mingw-w64, SDK `d3d9.h`), (B) in the AArch64-ILP32 guest
around our D3D9 (shim `d3d9.h`). It forwards every call unchanged and records it in the `D9S1` stream (STREAM.md), so "guest stream ==
original stream" is an exact check of everything above the D3D9 boundary.

## Files
- `gen_wrap.py OUTDIR [spec.json]` -> `d9wrap.gen.h/.cpp` (+ `d9wrap.manifest.txt`); exit 2 naming the item on any kind / rule function / effect /
  struct field kind without an emitter. Helpers: `gen_meth.py` (per-method emitter), `gen_expr.py` (rule language -> C++), `gen_tab.py` (tables).
  Output is not checked in; `tests/l6/wrap/test_wrap.py` regenerates it.
- `d9stream.h/.cpp`: runtime (object registry / COM identity / ids, record builder, open locks, taint, private data). Sink callback, one spin lock,
  no exceptions/RTTI/iostream; needs malloc/free/memcpy and operator new/delete. `d9wrap_port.h`: the only platform file (lock, TLS, WINAPI, ILP32 assert).
- Entry points: `d9s_open(sink, ctx, d9wrap_spec_sha256)` once; `d9wrap_<Export>(real_fn, args...)` per export (the real function pointer comes
  first; the export thunk is the integrator's); `W_<Iface>` wrappers come out of those calls only (outputs are wrapped, inputs unwrapped).
- Integrator duty: `void d9port_dc_pixels(hdc, fmt, w, h, dst, n)` (GDI read-back of a GetDC surface; no portable default).

## Rules (all derived from the spec; none per item)
- Observational: arguments and results are forwarded unchanged. Only out handle pointers are zeroed before the call (spec README) and replaced by wrappers after.
- COM identity: one object per real pointer, wrappers per interface it was seen as; `$riid` outputs take the interface of the IID.
  A final Release (returns 0) retires the id and frees the wrappers. Pointers the wrapper never made pass through as foreign objects. A level/face surface or volume shares its container's reference count, so its final Release retires the container and all its levels (the whole family dies in one record, no stale registry entry survives pointer reuse); open locks are keyed by (container, level, face), so a level locked through the texture and unlocked through its surface (or the reverse) is one lock.
- `this.<prop>` is read from the real object (GetDesc/GetLevelDesc/GetLevelCount/GetFunction/GetDeclaration/GetDataSize/GetType, `gen_tab.PROPSRC`);
  these queries are not recorded. A rule using a property without a source fails the generator.
- Taint (by:taint): a level is pix iff its usage has RENDERTARGET/DEPTHSTENCIL (or AUTOGENMIPMAP at level >= 1), or an effect of a succeeded call made it pix
  (`pix(x)`, `copy(dst,src)`); a texture's surfaces/volumes are the sub-resources keyed by the lock-match values of the getter's params.
  `pix(rendertargets|depthstencil)` adds nothing: D3D9 binds only such surfaces, which are pix from birth.
- Buffers with `canon` as handle (CheckResourceResidency) are forwarded as a copy with unwrapped pointers.

## Test
`python3 -I tests/l6/wrap/test_wrap.py` (g++ -m32; ~5 s). Mingw: `i686-w64-mingw32-g++ -std=c++17 -I render/d3d9/gen -c d9wrap.gen.cpp d9stream.cpp`.
