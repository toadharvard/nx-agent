# D9S1: binary D3D9 call stream (RD1)

Written by the generated journaling wrapper (`d9wrap.gen.*` + `d9stream.*`), identically on Windows/Wine and in the guest.
Little-endian. `W` = u32. Every record and every payload piece is padded with zero bytes to a multiple of 4.
A stream is comparable only with the same `d3d9_spec.json` (sha256 in the header). The spec supplies the *types*
(param kinds, struct fields, `when`, `closes`, `lock` pitch fields); the stream itself carries all classes and lengths of out items.

## File header (44 bytes)
`"D9S1"` | W version=1 | 32 bytes sha256(d3d9_spec.json) | W flags=0. Records follow until EOF.

## Numbering
- `iface` = index into the byte-order sorted list of `spec.interfaces` keys (IDirect3D9=0 ... IUnknown=last). `0xFFFF` = no
  interface: an export call (`slot` = index in `spec.exports`) or a foreign object (see ids).
- `slot` = vtable slot (`method.slot`). Exports are recorded with iface 0xFFFF and `this`=0.
- Object ids: 1,2,3,... assigned by first appearance while reading the file in order (inside a record: header `this`, then the
  body in order). 0 = NULL. An id is retired when the wrapper sees the object's final Release (Release returned 0): the id is
  never reused, a level/face surface or volume handed out by a texture/cube/volume texture shares its container's reference count (a Release on it
  returns the container's), so the final Release of any of them retires the container and every level object the stream named, in one record, and the real pointer, if the runtime hands it out again, gets a new id. Foreign pointers (a pointer the wrapper never
  created, e.g. a QueryInterface for an unknown IID) get an id with iface 0xFFFF and are passed through unwrapped.
- Token ids (`TOK`, for ohandle values and ptr fields): 1,2,.. by first appearance of the raw value in the stream; 0 = raw 0.
  Lock pointers/HDCs are *fresh*: every successful Lock/GetDC gets a new token even if the raw value was seen; the matching
  Unlock/ReleaseDC record names that token and then forgets the raw value.

## Record
```
W len                 bytes of the whole record (incl. this word)
W seq                 0,1,2.. = file order (a nested call's record precedes the enclosing call's: written at return)
W thread              thread ordinal, order of first appearance (0 = first)
u16 iface, u16 slot
W this                object id of the receiver (0 for exports)
W depth               number of wrapped calls of the same thread in progress at entry (0 = called by the application)
W n_new, n_new*(W id, W iface)    ids that first appear in this record (id ascending; iface 0xFFFF = foreign)
W n_ret, n_ret*W id               ids retired by this call
body
```
body = INPUTS, RET, OUTS, CLOSE (in this order).

## Items
- `HDR`: W = cls | rec<<8; cls 0 det, 1 env, 2 pix (by:taint and by:query_type are resolved here); rec=1 means the payload follows.
- `OBJ` W object id (the pointer the application sees is the wrapper; the id names the object). `TOK` W token id.
- `RAW(n)`: n bytes + zero padding. `BUF(n)`: W n, RAW(n).
- `STRUCT(s)`: fields of `spec.structs[s]` in order, no gaps: int/enum/flags/float/char: RAW size bytes as in memory
  (arrays of `count` elements are `size` bytes); ptr and ohandle: TOK; pad: `size` zero bytes; struct: recurse `count` (default 1) times.
  The concatenation is padded to 4 once at the end.
- VALUE(param): value/enum/flags/float: RAW(size) (4 for all but 8-byte values); ohandle: TOK; handle: OBJ.

## INPUTS: for each param in spec order
- *Pointer param* (byref, or kind buffer/locked/struct): W present (0 NULL, 1 not). If present and dir is in/inout: [when the param has `in_when`: W flag, the rule's value != 0; the pointee follows only if the flag is 1] the pointee: value/enum/flags/float/ohandle as VALUE, struct as STRUCT, buffer as BUF(len) of `len` bytes starting
  at ptr+`off` (len/off = the spec rule evaluated at entry; `canon`: when its `when` is nonzero every aligned 4-byte word is
  replaced by TOK (as ohandle) or OBJ (as handle); a tail of len%4 bytes stays raw). A riid/guid param is STRUCT(GUID).
- Other param: VALUE.
## RET (unless ret_kind void)
HDR(ret_cls, rec=1), then W (hresult/enum/value/float, raw 4 bytes), TOK (ohandle) or OBJ (handle).
## OUTS: for each dir out/inout param in spec order
- kind other than locked: HDR(cls, rec) with rec = present AND (`when`=always, or SUCCEEDED(hr) for `ok`); if rec: the pointee as in INPUTS,
  read after the call (handle: OBJ of the wrapper handed to the application).
- kind locked (Lock/LockRect/LockBox/GetDC), three groups, rec = present AND SUCCEEDED(hr):
  a) HDR(det) TOK (fresh token of pBits / *ppbData / *phdc);
  b) per pitch field of the locked struct in field order (rect: Pitch; box: RowPitch, SlicePitch): HDR(field_cls) W value;
  c) HDR(taint class of the level at lock time, rec & on_lock) BUF: the locked extent read at Lock return: linear = `bytes` at the returned
     pointer; rect/box = rows(fmt,h) rows of rowbytes(fmt,w) bytes per slice (d slices, 1 for rect), packed, pitch not recorded; dc = same
     extent read from the DC via the port hook `d9port_dc_pixels`.
## CLOSE (only methods with `closes`: Unlock, UnlockRect, UnlockBox, ReleaseDC)
The open lock is found by `lock.match` (lock param value == unlock param value; no pairs = the object's single lock). The lock's identity is the sub-resource
(container, level, face): a lock taken through a level surface/volume (whose key words are those of the GetSurfaceLevel/GetCubeMapSurface/GetVolumeLevel call
that produced it) is found by an unlock through the container and the reverse.
  a) HDR(det, rec=found AND SUCCEEDED(hr)) TOK (token of the lock; forgotten afterwards);
  b) HDR(class as at lock time, rec & on_unlock-at-lock-time) BUF: the extent read just before the real Unlock is forwarded.

## Notes for a comparator
Compare record by record in file order after mapping thread ordinals; ids, tokens and lengths are canonical, so equal streams are byte-equal.
Mask only items whose HDR cls is pix; env items are checked against the reference profile, det items must be equal.
