"""LZ4 block format (no frame), as used by NSO segments.

Uses the `lz4` Python package when it imports cleanly; otherwise a small pure-Python greedy compressor
(valid LZ4, a bit weaker than liblz4) and a reference decompressor.
"""
from __future__ import annotations

MINMATCH = 4
LAST_LITERALS = 5      # the last 5 bytes are always literals
MFLIMIT = 12           # a match may not start in the last 12 bytes
MAX_OFFSET = 0xFFFF

try:  # pragma: no cover - depends on the host
    import lz4.block as _lz4

    def compress(data: bytes) -> bytes:
        return _lz4.compress(bytes(data), store_size=False)
    BACKEND = "lz4"
except Exception:  # noqa: BLE001
    _lz4 = None
    BACKEND = "python"

    def compress(data: bytes) -> bytes:
        return _compress_py(bytes(data))


def _emit(out: bytearray, lit: bytes, mlen: int | None, off: int = 0) -> None:
    ll = len(lit)
    tok_l = 15 if ll >= 15 else ll
    if mlen is None:
        tok_m = 0
    else:
        m = mlen - MINMATCH
        tok_m = 15 if m >= 15 else m
    out.append((tok_l << 4) | tok_m)
    if ll >= 15:
        r = ll - 15
        while r >= 255:
            out.append(255)
            r -= 255
        out.append(r)
    out += lit
    if mlen is None:
        return
    out += off.to_bytes(2, "little")
    m = mlen - MINMATCH
    if m >= 15:
        r = m - 15
        while r >= 255:
            out.append(255)
            r -= 255
        out.append(r)


def _compress_py(src: bytes) -> bytes:
    n = len(src)
    out = bytearray()
    if n < MFLIMIT + 1:
        _emit(out, src, None)
        return bytes(out)
    table: dict[bytes, int] = {}
    anchor = 0
    i = 0
    limit = n - MFLIMIT
    while i < limit:
        key = src[i:i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is None or i - cand > MAX_OFFSET:
            i += 1
            continue
        # extend forwards, keeping the last LAST_LITERALS bytes as literals
        mend = n - LAST_LITERALS
        j = i + 4
        k = cand + 4
        while j < mend and src[j] == src[k]:
            j += 1
            k += 1
        # extend backwards into pending literals
        s = i
        c = cand
        while s > anchor and c > 0 and src[s - 1] == src[c - 1]:
            s -= 1
            c -= 1
        _emit(out, src[anchor:s], j - s, s - c)
        anchor = j
        # index a couple of positions inside the match so later data can refer to it
        if j - 2 > i:
            table[src[j - 2:j + 2]] = j - 2
        i = j
    _emit(out, src[anchor:], None)
    return bytes(out)


def decompress(src: bytes, size: int) -> bytes:
    if _lz4 is not None:
        return _lz4.decompress(bytes(src), uncompressed_size=size)
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        tok = src[i]
        i += 1
        ll = tok >> 4
        if ll == 15:
            while True:
                b = src[i]
                i += 1
                ll += b
                if b != 255:
                    break
        out += src[i:i + ll]
        i += ll
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0:
            raise ValueError("lz4: zero offset")
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += MINMATCH
        start = len(out) - off
        if start < 0:
            raise ValueError("lz4: offset before start")
        for k in range(ml):
            out.append(out[start + k])
    if len(out) != size:
        raise ValueError(f"lz4: decoded {len(out)} bytes, expected {size}")
    return bytes(out)
