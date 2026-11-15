# D3D9 interface spec (RD1)

`d3d9_spec.json` is generated (`python3 -I d3d9_spec.py`, `--stdout` to print) from `shim/include/d3d9*.h` plus the hand
tables `d3d9_rules.py` (per-parameter rules), `d3d9_cls.py` (out classes), `d3d9_tables.py`, `d3d9_exports.py`.
Check: `python3 -I tests/l6/test_spec.py`. Expressions: `d3d9_expr.py` (parser, checker, evaluator).

## Schema
Top level: `interfaces` (base, slots, iid), `methods` (flat, sorted by iface then slot, inherited slots repeated per interface,
IUnknown included), `structs`, `enums`, `flags` (family -> macro prefix), `tables`, `exports`.
Method: `iface slot name ret ret_kind(hresult|void|value|enum|float|ohandle) ret_cls ret_why? params effects? closes?`.
Param: `name ctype dir(in|out|inout) byref kind`, plus by kind
- `value` size signed | `enum` enum | `flags` flags(family) | `float` (4-byte IEEE bits) | `ohandle` os (HWND, HANDLE, HDC, function pointer: opaque, 4 bytes, canonicalised by first appearance)
- `handle` iface (`$riid` = chosen by the riid param), null, out: `own` new (fresh, refcount 1) | addref (existing object, AddRef'd), `when: always`
- `struct` struct, null; fields in `structs` (offset, size, kind int|enum|flags|float|ohandle|ptr|char|struct|pad). `ptr` fields (pBits) and `ohandle` fields (hDeviceWindow) are canonicalised, never compared raw.
- `buffer` elem (bytes), `len` (bytes), `off` (byte offset from the pointer), null, `canon` {when, as: ohandle|handle} (bytes are pointers, replace raw bytes by ids)
- `locked` `lock`: shape linear|rect|box|dc, unlock method, match (lock param, unlock param) pairs, extent (linear: off, bytes; rect/box/dc: fmt, w, h, d in pixels; payload = rows(fmt,h) rows of rowbytes(fmt,w) bytes per slice, packed, pitch not recorded), on_lock/on_unlock (record the bytes at Lock return / at Unlock when true)
- optional `in_when` (record the input value only then: the app need not initialise it otherwise), `cls`, `why`, `field_cls`, `when`.
Out values are recorded when `when`=ok and SUCCEEDED(hr); `always` also on failure (the wrapper pre-zeroes out handles). Any byref param can be NULL at run time (record a flag); `null` marks where the docs allow it.
Every pointer-sized value is 4 bytes on x86 and AArch64-ILP32; struct layout is pack(4) (checked against `g++ -m32`).
Object ids: assigned in order of first appearance in the stream (inputs and outputs), implicit objects (back buffer, auto depth) at first sight.

## Length-rule vocabulary (closed; `tables.functions`, `this.` props in `tables.object_props`)
Integers; parameter names; header constants (`tables.constants`); `*p` (value pointed to by p before the call), `*p@out` (after); `this.P`
(length fmt width height depth levels usage pool shader_bytes decl_bytes data_size); `+ - * /`; functions: verts index_size sm_bytes decl_bytes
u32 ok ok0 nonnull pd_iunknown min max if eq band lvl rows rowbytes rect_w rect_h box_w box_h box_d str_bytes wstr_bytes.
`ok(x)`: x if SUCCEEDED(hr) else 0; `ok0`: x if hr==S_OK. `sm_bytes` follows `tables.sm_walk` (comment tokens skipped, END 0x0000FFFF inclusive).
`rows`/`rowbytes` use `tables.format` (block size; DXTn block rows). `verts` uses `tables.primitive` and is 0 for count 0.

## Classes of out values (and of `ret`)
- `det`: a function of the recorded preceding stream (inputs and recorded outputs); the guest must produce exactly the recorded bytes.
- `env`: an answer about the environment (caps, adapter, modes, format support, free memory, device lost, allocation failure, runtime-resolved present parameters, driver pitch). Same value in both runs only if both run the same reference profile; the comparator checks it against that profile and reports a profile diff, not a stream diff.
- `pix`: depends on rendered pixels or GPU/display timing (query data, raster status, present stats, GetData S_FALSE). Recorded, masked from equality; polling loops on a `pix` return are collapsed by the comparator.
- `by:taint` (contents read through a lock/GetDC) and `by:query_type` (GetData) select det/pix by `tables.cls_rules` / `tables.query_type`; `tables.taint` + `effects[]` say which calls make a surface level pix.
Every non-det class carries a one-line `why`.

## Decided with doubt
Refcounts from AddRef/Release are `det` (the guest must reproduce D3D9's internal references). Ex-only methods (Wine stubs them) follow the SDK docs, not Wine.
