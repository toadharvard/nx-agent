#!/usr/bin/env python3
"""JSON -> NPDM (main.npdm), a port of switch-tools npdmtool (same JSON schema, same binary layout).

    npdmtool.py in.json out.npdm
    npdmtool.py --dump file.npdm

Additions over npdmtool: "address_space_type" may also be a name (see ADDRESS_SPACES); the "syscalls"
capability may be the string "all" (SVC 0x00..0xBF); "--dump" decodes a file; ints are accepted where
npdmtool wants hex strings. Header MmuFlags = is_64_bit | (address_space_type << 1)
| optimize_memory_allocation << 4 | disable_device_address_space_merge << 5 | enable_alias_region_extra_size << 6
| prevent_code_reads << 7.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys

# NPDM ProcessAddressSpace values (Atmosphere ldr Npdm::ProcessAddressSpace_*).
ADDRESS_SPACES = {
    "32bit": 0, "AddressSpace32Bit": 0,
    "64bit_old": 1, "36bit": 1, "AddressSpace64BitOld": 1,
    "32bit_no_reserved": 2, "AddressSpace32BitNoReserved": 2,
    "64bit": 3, "39bit": 3, "AddressSpace64Bit": 3,
}
ADDRESS_SPACE_NAMES = {0: "32Bit", 1: "64Bit36", 2: "32BitNoReserved", 3: "64Bit39"}

HDR = struct.Struct("<IIIBBBBII I I 16s 16s 48s IIII".replace(" ", ""))
assert HDR.size == 0x80
ACID = struct.Struct("<256s256sIIIIQQIIIIIIQ")
assert ACID.size == 0x240
ACI0 = struct.Struct("<I12sQQIIIIIIQ")
assert ACI0.size == 0x40
FAC = struct.Struct("<BBBBQQQQQ")   # packed, 0x2C
FAH = struct.Struct("<IQIIII")      # packed, 0x1C


class NpdmError(ValueError):
    pass


def _hex(v, field):
    if isinstance(v, bool):
        raise NpdmError(f"{field}: expected a hex string or int")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v, 16)
        except ValueError:
            pass
    raise NpdmError(f"{field}: expected a hex string, got {v!r}")


def _req(obj, key, alt=None):
    if key in obj:
        return obj[key]
    if alt and alt in obj:
        return obj[alt]
    raise NpdmError(f"missing field {key}")


def _int(v, field):
    if isinstance(v, bool) or not isinstance(v, int):
        raise NpdmError(f"{field}: expected a number")
    return v


def _bool(v, field):
    if not isinstance(v, bool):
        raise NpdmError(f"{field}: expected true/false")
    return int(v)


def _align(x, a=0x10):
    return (x + a - 1) & ~(a - 1)


def _caps(spec) -> list[int]:
    items = list(spec.items()) if isinstance(spec, dict) else [(_req(c, "type"), c.get("value")) for c in spec]
    caps: list[int] = []
    for typ, value in items:
        if typ == "kernel_flags":
            hp = _int(value["highest_thread_priority"], typ)
            lp = _int(value["lowest_thread_priority"], typ)
            hc = _int(value["highest_cpu_id"], typ)
            lc = _int(value["lowest_cpu_id"], typ)
            real_hi, real_lo = min(hp, lp), max(hp, lp)
            desc = (((((hc << 8) | lc) << 6) | (real_hi & 0x3F)) << 6) | (real_lo & 0x3F)
            caps.append(((desc << 4) | 0x7) & 0xFFFFFFFF)
        elif typ == "syscalls":
            descs = [0] * 8
            if value == "all":  # extension: every SVC id 0x00..0xBF
                value = {f"svc{n:02X}": n for n in range(0xC0)}
            for name, v in value.items():
                n = v if isinstance(v, int) else _hex(v, name)
                if n >= 0xC0:
                    raise NpdmError(f"syscall {name} = {n:#x} out of range")
                descs[n // 0x18] |= 1 << (n % 0x18)
            for i, d in enumerate(descs):
                if d:
                    caps.append((((d | (i << 24)) << 5) | 0xF) & 0xFFFFFFFF)
        elif typ == "map":
            addr, size = _hex(value["address"], "map.address"), _hex(value["size"], "map.size")
            is_ro, is_io = _bool(value["is_ro"], "is_ro"), _bool(value["is_io"], "is_io")
            caps.append(((((addr >> 12) & 0xFFFFFF) | (is_ro << 24)) << 7 | 0x3F) & 0xFFFFFFFF)
            d = ((size >> 12) & 0xFFFFF) | (((addr >> 36) & 0xF) << 20) | ((is_io ^ 1) << 24)
            caps.append(((d << 7) | 0x3F) & 0xFFFFFFFF)
        elif typ == "map_page":
            caps.append(((((_hex(value, typ) >> 12) & 0xFFFFFF) << 8) | 0x7F) & 0xFFFFFFFF)
        elif typ == "map_region":
            if len(value) > 3:
                raise NpdmError("map_region: at most 3 regions")
            c = 0x3FF
            for i, r in enumerate(value):
                c |= ((_int(r["region_type"], typ) & 0x3F) | (_bool(r["is_ro"], typ) << 6)) << (11 + 7 * i)
            caps.append(c & 0xFFFFFFFF)
        elif typ == "irq_pair":
            if len(value) != 2:
                raise NpdmError("irq_pair needs 2 entries")
            d = 0
            for i, irq in enumerate(value):
                d |= (0x3FF if irq is None else (_int(irq, typ) & 0x3FF)) << (10 * i)
            caps.append(((d << 12) | 0x7FF) & 0xFFFFFFFF)
        elif typ == "application_type":
            caps.append((((_int(value, typ) & 7) << 14) | 0x1FFF) & 0xFFFFFFFF)
        elif typ == "min_kernel_version":
            v = value if isinstance(value, int) else _hex(value, typ)
            caps.append((((v & 0xFFFF) << 15) | 0x3FFF) & 0xFFFFFFFF)
        elif typ == "handle_table_size":
            caps.append((((_int(value, typ) & 0xFFFF) << 16) | 0x7FFF) & 0xFFFFFFFF)
        elif typ == "debug_flags":
            a = int(bool(value.get("allow_debug", False)))
            fp = int(bool(value.get("force_debug_prod", False)))
            f = int(bool(value.get("force_debug", False)))
            if a + fp + f > 1:
                raise NpdmError("debug_flags: only one of allow_debug, force_debug_prod, force_debug")
            caps.append((((a | (fp << 1) | (f << 2)) << 17) | 0xFFFF) & 0xFFFFFFFF)
        else:
            raise NpdmError(f"unknown kernel capability {typ!r}")
    if len(caps) > 0x20 * 4:
        raise NpdmError("too many kernel capabilities")
    return caps


def _sac(j) -> bytes:
    out = bytearray()

    def add(name, host):
        if not 0 < len(name) <= 8:
            raise NpdmError(f"bad service name {name!r}")
        out.append((len(name) - 1) | (0x80 if host else 0))
        out.extend(name.encode())

    for s in j.get("service_host", []) or []:
        add(s, True)
    acc = j.get("service_access", []) or []
    if isinstance(acc, dict):
        for name, host in acc.items():
            add(name, _bool(host, name))
    else:
        for name in acc:
            add(name, False)
    return bytes(out)


def build_npdm(j: dict) -> bytes:
    name = _req(j, "name")
    if not isinstance(name, str):
        raise NpdmError("name must be a string")
    stack = _hex(_req(j, "main_thread_stack_size"), "main_thread_stack_size")
    if stack >> 32:
        raise NpdmError("main_thread_stack_size must fit in 32 bits")
    prio = _int(_req(j, "main_thread_priority"), "main_thread_priority")
    cpu = _int(_req(j, "default_cpu_id"), "default_cpu_id")
    srs = _hex(j.get("system_resource_size", 0), "system_resource_size")
    version = _hex(j.get("version", j.get("process_category", 0)), "version")
    ast = _req(j, "address_space_type")
    if isinstance(ast, str):
        if ast not in ADDRESS_SPACES:
            raise NpdmError(f"address_space_type: unknown name {ast!r}")
        ast = ADDRESS_SPACES[ast]
    mmu = ((_int(ast, "address_space_type") & 3) << 1) | _bool(_req(j, "is_64_bit"), "is_64_bit")
    for bit, key in ((4, "optimize_memory_allocation"), (5, "disable_device_address_space_merge"),
                     (6, "enable_alias_region_extra_size"), (7, "prevent_code_reads")):
        if key in j:
            mmu |= _bool(j[key], key) << bit
    keygen = _int(j.get("signature_key_generation", 0), "signature_key_generation")

    acid_flags = _bool(_req(j, "is_retail"), "is_retail") | ((_int(_req(j, "pool_partition"), "pool_partition") & 3) << 2)
    pid_min = _hex(_req(j, "program_id_range_min", "title_id_range_min"), "program_id_range_min")
    pid_max = _hex(_req(j, "program_id_range_max", "title_id_range_max"), "program_id_range_max")
    pid = _hex(_req(j, "program_id", "title_id"), "program_id")

    fs = _req(j, "filesystem_access")
    perms = _hex(_req(fs, "permissions"), "filesystem_access.permissions")
    fac = FAC.pack(1, 0, 0, 0, perms, 0, 0, 0, 0)

    coi = b""
    if fs.get("content_owner_ids"):
        ids = [_hex(x, "content_owner_ids") for x in fs["content_owner_ids"]]
        coi = struct.pack("<I", len(ids)) + b"".join(struct.pack("<Q", x) for x in ids)
    sdoi = b""
    if fs.get("save_data_owner_ids"):
        ents = fs["save_data_owner_ids"]
        n = len(ents)
        acc = bytes(_int(e["accessibility"], "accessibility") for e in ents).ljust((n + 3) & ~3, b"\0")
        sdoi = struct.pack("<I", n) + acc + b"".join(struct.pack("<Q", _hex(e["id"], "id")) for e in ents)
    fah = FAH.pack(1, perms, FAH.size, len(coi), FAH.size + len(coi), len(sdoi)) + coi + sdoi

    sac = _sac(j)
    kac = b"".join(struct.pack("<I", c) for c in _caps(_req(j, "kernel_capabilities")))

    # ACID
    fac_off = ACID.size
    sac_off = _align(fac_off + len(fac))
    kac_off = _align(sac_off + len(sac))
    acid_size_total = kac_off + len(kac)
    acid = bytearray(acid_size_total)
    ACID.pack_into(acid, 0, b"\0" * 256, b"\0" * 256, 0x44494341, acid_size_total - 0x100, 0, acid_flags,
                   pid_min, pid_max, fac_off, len(fac), sac_off, len(sac), kac_off, len(kac), 0)
    acid[fac_off:fac_off + len(fac)] = fac
    acid[sac_off:sac_off + len(sac)] = sac
    acid[kac_off:kac_off + len(kac)] = kac

    # ACI0
    fah_off = ACI0.size
    asac_off = _align(fah_off + len(fah))
    akac_off = _align(asac_off + len(sac))
    aci_size = akac_off + len(kac)
    aci = bytearray(aci_size)
    ACI0.pack_into(aci, 0, 0x30494341, b"\0" * 12, pid, 0, fah_off, len(fah), asac_off, len(sac), akac_off,
                   len(kac), 0)
    aci[fah_off:fah_off + len(fah)] = fah
    aci[asac_off:asac_off + len(sac)] = sac
    aci[akac_off:akac_off + len(kac)] = kac

    acid_off = HDR.size
    aci_off = _align(acid_off + len(acid))
    nm = name.encode()[:15].ljust(16, b"\0")
    hdr = HDR.pack(0x4154454D, keygen, 0, mmu, 0, prio, cpu, 0, srs, version, stack, nm, b"\0" * 16, b"\0" * 48,
                   aci_off, len(aci), acid_off, len(acid))
    out = bytearray(aci_off + len(aci))
    out[0:HDR.size] = hdr
    out[acid_off:acid_off + len(acid)] = acid
    out[aci_off:aci_off + len(aci)] = aci
    return bytes(out)


def dump_npdm(buf: bytes) -> dict:
    (magic, keygen, _u8, mmu, _u0d, prio, cpu, _u10, srs, version, stack, name, _pc, _pad, aci_off, aci_size,
     acid_off, acid_size) = HDR.unpack_from(buf, 0)
    if magic != 0x4154454D:
        raise NpdmError("not an NPDM (META magic)")
    acid = ACID.unpack_from(buf, acid_off)
    aci = ACI0.unpack_from(buf, aci_off)
    kac_off, kac_size = aci[8], aci[9]
    caps = [struct.unpack_from("<I", buf, aci_off + kac_off + 4 * i)[0] for i in range(kac_size // 4)]
    return {
        "name": name.rstrip(b"\0").decode(), "is_64_bit": bool(mmu & 1),
        "address_space": ADDRESS_SPACE_NAMES[(mmu >> 1) & 3], "mmu_flags": mmu,
        "main_thread_priority": prio, "default_cpu_id": cpu, "main_thread_stack_size": stack,
        "system_resource_size": srs, "version": version, "signature_key_generation": keygen,
        "acid_flags": acid[5], "program_id_range": (acid[6], acid[7]), "program_id": aci[2],
        "kernel_caps": caps, "acid_size": acid_size, "aci_size": aci_size,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    a = ap.parse_args(argv)
    if a.dump:
        d = dump_npdm(open(a.input, "rb").read())
        for k, v in d.items():
            if k == "kernel_caps":
                v = " ".join(f"{c:08x}" for c in v)
            elif k == "program_id_range":
                v = f"{v[0]:016x}..{v[1]:016x}"
            elif k == "program_id":
                v = f"{v:016x}"
            elif isinstance(v, int) and not isinstance(v, bool) and k not in ("main_thread_priority", "default_cpu_id"):
                v = hex(v)
            print(f"{k}: {v}")
        return 0
    if not a.output:
        ap.error("output path required")
    with open(a.input) as f:
        npdm = build_npdm(json.load(f))
    with open(a.output, "wb") as f:
        f.write(npdm)
    print(f"{a.output}: {len(npdm)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
