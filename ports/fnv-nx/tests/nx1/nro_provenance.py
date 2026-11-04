#!/usr/bin/env python3
"""Is the package in a build directory built from this commit? (NX1, used by tools/farm/lib_job.sh publish_nro)

    nro_provenance.py <build dir> <commit or ""> <commit12>

Prints nothing when it is; otherwise one line saying why not (publish_nro then refuses to publish). Proof:
- the package stage record (package/stage.json) names the commit (port_commit, written by the pipeline);
- when sd/switch/fnv/fnv.nro is a real NRO, its NACP display version is the commit12 (toolchain/nro/build.sh).
"""
import json
import os
import struct
import sys


def why_not(bd: str, commit: str, c12: str) -> str:
    try:
        rec = json.load(open(os.path.join(bd, "package", "stage.json")))
    except (OSError, ValueError) as e:
        return f"no package stage record ({e.__class__.__name__})"
    pc = rec.get("port_commit") or ""
    if not pc or not (pc == commit if commit else pc.startswith(c12)):
        return f"the package record names commit {pc[:12] or None}, not {c12}"
    b = open(os.path.join(bd, "package", "sd", "switch", "fnv", "fnv.nro"), "rb").read()
    if b[0x10:0x14] == b"NRO0":
        size = struct.unpack_from("<I", b, 0x18)[0]
        if b[size:size + 4] != b"ASET":
            return "fnv.nro has no ASET/NACP to carry the commit"
        off, n = struct.unpack_from("<QQ", b, size + 8 + 16)
        if n < 0x3070:
            return "fnv.nro's NACP is too short"
        ver = b[size + off + 0x3060:size + off + 0x3070].split(b"\0")[0].decode(errors="replace")
        if ver != c12[:15]:
            return f"fnv.nro is version {ver!r}, not {c12}"
    return ""


if __name__ == "__main__":
    print(why_not(*sys.argv[1:4]))
