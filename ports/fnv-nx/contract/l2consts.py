#!/usr/bin/env python3
"""l2consts - the non-loaded ELF section `.fnvnx.const`: the declared constant that a function returns (L1 CONTRACT).

    parse_consts(data, relocs, where, ptr) -> [(offset, function, fn_addend, value_bytes, value_target)]

One record, aligned to 4 and padded to 4:
    +0      PTR bytes (4 for ILP32, 8 for LP64): the function. Its bytes are zero; a relocation (ABS32 / ABS64 of the object's ABI) at this
            offset names the function symbol (for a C++ member, the pointer field of `&Class::method`); the addend is kept.
    +PTR    u32 LEN, little endian
    +PTR+4  LEN bytes: the value as an object of the row's type (little endian). If a relocation at the value's offset covers it (LEN == PTR),
            the value is an address: (symbol, addend) goes into value_target and the bytes are zero.
RELOCS = {offset in the section: (symbol name, relocation type is already checked by the caller as ABS, addend)}; the caller (objstub) maps an ELF
symbol index to a name and passes only absolute-data relocations, so a record whose function relocation is of another kind is simply missing it.
Pure: bytes in, tuples out. Every malformed record is a ValueError `<where>: offset 0x..: <what>`; a record whose function relocation is missing is
an error, and so is a relocation inside the record that is neither the function's nor a whole-value address. No record is skipped.
"""
import struct


def parse_consts(data, relocs, where, ptr):
    out, p, n = [], 0, len(data)
    used = set()
    while p < n:
        at = "%s: offset 0x%x" % (where, p)
        if p + ptr + 4 > n:
            raise ValueError("%s: a record is a %d-byte function pointer, a u32 length and the value" % (at, ptr))
        if p not in relocs:
            raise ValueError("%s: the function pointer has no relocation" % at)
        if data[p:p + ptr] != b"\0" * ptr:
            raise ValueError("%s: the function pointer bytes are not zero (the relocation carries the addend)" % at)
        ln = struct.unpack_from("<I", data, p + ptr)[0]
        v0 = p + ptr + 4
        if v0 + ln > n:
            raise ValueError("%s: the length %d leaves the section" % (at, ln))
        sym, _, add = relocs[p]
        used.add(p)
        value, target = bytes(data[v0:v0 + ln]), None
        if ln and v0 in relocs:   # a LEN 0 record has no value: the next record's function relocation sits at v0
            if ln != ptr:
                raise ValueError("%s: a relocation at the value, but the value is %d bytes, not %d" % (at, ln, ptr))
            if value != b"\0" * ptr:
                raise ValueError("%s: the address value bytes are not zero" % at)
            target, _ = (relocs[v0][0], relocs[v0][2]), used.add(v0)
        out.append((p, sym, add, value, target))
        end = (v0 + ln + 3) & ~3
        if end > n:
            raise ValueError("%s: the padding to 4 leaves the section" % at)
        if any(data[v0 + ln:end]):
            raise ValueError("%s: the padding is not zero" % at)
        p = end
    stray = sorted(set(relocs) - used)
    if stray:
        raise ValueError("%s: offset 0x%x: a relocation that is neither a record's function nor a whole-value address" % (where, stray[0]))
    return out
