#!/usr/bin/env python3
"""shaderpack.py - the native renderer's shaders as one precompiled pack for the console (NX1; owner rule: no shader
is compiled on the console).

    shaderpack.py <render/native/shaders/build.py out dir> --pack <out shaders.nxsp> [--table <dir>]

Input: build.py's out dir (run with --uam): <name>.<stage>.glsl for every shader and variant it generated, and the
uam output <name>.<stage>.dksh next to each. A shader without a DKSH (or one that is not a DKSH) is an error: the
package never falls back to compiling.

Output:
  shaders.nxsp   the pack (goes into fnv.nro's RomFS, toolchain/nro/build.sh):
                 header  "NXSP", u32 version 1, u32 count, u32 index offset, u32 data offset, u8[12] pack id
                 index   count x 104 bytes: name[48] family[24] variant[16] (NUL-padded), u32 stage (0 vert, 1 frag),
                         u32 offset, u32 size, u32 crc32 of the DKSH
                 data    the DKSH blobs, each at a 256-byte boundary
                 family = the GLSL source the shader came from (render/native/shaders/<family>.<stage>.glsl),
                 variant = the rest of the name (nx-variants suffix, __e0/__ea/__ef epilogue permutations)
  --table <dir>  nx_shaders.h / nx_shaders.c for the console guest (replacing build.py's): the same NxShaderBlob table
                 and nx_shader_find(), without any GLSL text; the DKSH blobs are read from the pack at the first
                 nx_shader_find() (FNVNX_SHADER_PACK, the RomFS the host mounts from fnv.nro), and the pack id must be
                 this table's. A missing or different pack leaves every shader absent (logged), never a compile.
                 .glsl holds the blob's DKSH address with glsl_size 0: renderer.cpp identifies a created shader by
                 that pointer (LearnEpilogueVariants), and the device backend ignores it.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "render", "native", "shaders")
MAGIC, VERSION, ENTRY = b"NXSP", 1, 104   # struct.calcsize("<48s24s16sIIII")
PACK_PATH = "fnvshd:/shaders.nxsp"


def families() -> list[str]:
    return sorted({m.group(1) for f in os.listdir(SRC) if (m := re.match(r"^([a-z0-9_]+)\.(vert|frag)\.glsl$", f))},
                  key=len, reverse=True)


def split(name: str, fams: list[str]) -> tuple[str, str]:
    for f in fams:   # longest family first: "tile_vc" before "tile"
        if name == f or name.startswith(f + "_"):
            return f, name[len(f) + 1:] if name != f else ""
    raise ValueError(f"{name}: no source family in {SRC}")


def collect(out_dir: str) -> list[tuple[str, int, bytes]]:
    shaders, errors = [], []
    for f in sorted(os.listdir(out_dir)):
        m = re.match(r"^([a-z0-9_]+)\.(vert|frag)\.glsl$", f)
        if not m:
            continue
        name, stage = m.group(1), 0 if m.group(2) == "vert" else 1
        d = os.path.join(out_dir, f[:-len(".glsl")] + ".dksh")
        blob = open(d, "rb").read() if os.path.isfile(d) else b""
        if len(blob) < 24 or blob[:4] != b"DKSH":
            errors.append(f"{name}.{m.group(2)}: {'no DKSH' if not blob else 'not a DKSH'} (uam output missing)")
            continue
        shaders.append((name, stage, blob))
    if errors:
        raise SystemExit("shaderpack: a shader without precompiled DKSH is a package error (no runtime compile):\n  "
                         + "\n  ".join(errors))
    if not shaders:
        raise SystemExit(f"shaderpack: no shaders in {out_dir}")
    return shaders


def build_pack(shaders) -> tuple[bytes, bytes]:
    fams = families()
    return pack_rows([(name, *split(name, fams), stage, blob) for name, stage, blob in shaders])


def pack_rows(rows) -> tuple[bytes, bytes]:
    """NXSP v1 of [(name, family, variant, stage, blob)] in the given order -> (pack, pack id). Also the format of
    the game's shaders_nx.pak (shaders_nx.py: name = the bytecode hash)."""
    idx_off, n = 32, len(rows)
    data_off = (idx_off + n * ENTRY + 255) & ~255
    index, data = bytearray(), bytearray()
    for name, fam, var, stage, blob in rows:
        off = data_off + len(data)
        index += struct.pack("<48s24s16sIIII", name.encode(), fam.encode(), var.encode(), stage, off, len(blob),
                             zlib.crc32(blob))
        data += blob + bytes((-len(blob)) % 256)
    pack_id = hashlib.sha256(bytes(index) + bytes(data)).digest()[:12]
    head = struct.pack("<4sIIII12s", MAGIC, VERSION, n, idx_off, data_off, pack_id)
    out = head + bytes(index) + bytes(data_off - idx_off - len(index)) + bytes(data)
    return out, pack_id


def parse(pack: bytes):
    """[(name, family, variant, stage, blob)] and the pack id; raises ValueError on a malformed pack."""
    magic, ver, n, idx, data_off, pid = struct.unpack_from("<4sIIII12s", pack, 0)
    if magic != MAGIC or ver != VERSION or idx + n * ENTRY > len(pack):
        raise ValueError("not an NXSP v1 pack")
    rows = []
    for i in range(n):
        nm, fam, var, stage, off, size, crc = struct.unpack_from("<48s24s16sIIII", pack, idx + i * ENTRY)
        blob = pack[off:off + size]
        if off % 256 or off + size > len(pack) or zlib.crc32(blob) != crc:
            raise ValueError(f"entry {i}: bad offset or checksum")
        rows.append((nm.rstrip(b"\0").decode(), fam.rstrip(b"\0").decode(), var.rstrip(b"\0").decode(), stage, blob))
    return rows, pid


def write_table(d: str, shaders, pack_id: bytes):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "nx_shaders.h"), "w") as h:
        h.write("/* generated by toolchain/nro/shaderpack.py (console build: no GLSL, DKSH from the pack) */\n"
                "#ifndef NX_SHADERS_H\n#define NX_SHADERS_H\n#include <stdint.h>\n#ifdef __cplusplus\nextern \"C\" {\n"
                "#endif\ntypedef struct NxShaderBlob { const char* name; uint32_t stage; const uint8_t* glsl; "
                "uint32_t glsl_size;\n    const uint8_t* dksh; uint32_t dksh_size; } NxShaderBlob;\n"
                "extern NxShaderBlob nx_shader_blobs[];\nextern const uint32_t nx_shader_blob_count;\n"
                "/* index of name.stage (stage 0 vert, 1 frag), -1 if absent or the pack is missing */\n"
                "int nx_shader_find(const char* name, uint32_t stage);\n"
                "#ifdef __cplusplus\n}\n#endif\n#endif\n")
    pid = ",".join(str(b) for b in pack_id)
    with open(os.path.join(d, "nx_shaders.c"), "w") as c:
        c.write("/* generated by toolchain/nro/shaderpack.py: the console's shader table. No GLSL; the DKSH blobs come\n"
                " * from the precompiled pack in fnv.nro's RomFS (mounted by the host), read once, checked against this\n"
                " * table's pack id. Nothing is compiled at run time. */\n"
                "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <malloc.h>\n#include \"nx_shaders.h\"\n"
                "#ifndef FNVNX_SHADER_PACK\n#define FNVNX_SHADER_PACK \"%s\"\n#endif\n" % PACK_PATH)
        c.write("NxShaderBlob nx_shader_blobs[] = {\n")
        for name, stage, blob in shaders:
            c.write('    {"%s", %d, 0, 0, 0, %d},\n' % (name, stage, len(blob)))
        c.write("};\nconst uint32_t nx_shader_blob_count = %d;\n" % len(shaders))
        c.write("static const uint8_t k_pack_id[12] = {%s};\n" % pid)
        c.write(r'''static int g_state;   /* 0 not loaded, 1 loaded, -1 missing or not this table's pack */
static int load_pack(void) {
    FILE* f = fopen(FNVNX_SHADER_PACK, "rb");
    unsigned char h[32];
    if (!f || fread(h, 1, 32, f) != 32 || memcmp(h, "NXSP", 4) || memcmp(h + 20, k_pack_id, 12)) {
        printf("fnvnx shaders: %s %s: no shaders (the package is broken; nothing is compiled at run time)\n",
               FNVNX_SHADER_PACK, f ? "is not this build's pack" : "missing");
        if (f) fclose(f);
        return -1;
    }
    uint32_t n, idx, data;
    memcpy(&n, h + 8, 4); memcpy(&idx, h + 12, 4); memcpy(&data, h + 16, 4);
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    uint8_t* p = (uint8_t*)memalign(256, (size_t)size);
    fseek(f, 0, SEEK_SET);
    int ok = p && n == nx_shader_blob_count && fread(p, 1, (size_t)size, f) == (size_t)size;
    fclose(f);
    for (uint32_t i = 0; ok && i < n; i++) {
        const uint8_t* e = p + idx + i * 104;   /* name[48] family[24] variant[16] stage offset size crc32 */
        uint32_t off, sz;
        memcpy(&off, e + 92, 4); memcpy(&sz, e + 96, 4);
        ok = !strncmp((const char*)e, nx_shader_blobs[i].name, 48) && sz == nx_shader_blobs[i].dksh_size &&
             off + sz <= (uint32_t)size;
        if (ok) nx_shader_blobs[i].dksh = nx_shader_blobs[i].glsl = p + off;
    }
    if (!ok) {
        printf("fnvnx shaders: %s does not match the shader table\n", FNVNX_SHADER_PACK);
        free(p);
        return -1;
    }
    printf("fnvnx shaders: %u precompiled DKSH from %s\n", (unsigned)n, FNVNX_SHADER_PACK);
    return 1;
}
int nx_shader_find(const char* name, uint32_t stage) {
    if (!g_state) g_state = load_pack();
    if (g_state < 0) return -1;
    for (uint32_t i = 0; i < nx_shader_blob_count; i++)
        if (nx_shader_blobs[i].stage == stage && !strcmp(nx_shader_blobs[i].name, name)) return (int)i;
    return -1;
}
''')


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("shaders_dir")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--table")
    a = ap.parse_args(argv)
    shaders = collect(a.shaders_dir)
    pack, pid = build_pack(shaders)
    open(a.pack, "wb").write(pack)
    if a.table:
        write_table(a.table, shaders, pid)
    print(f"shaderpack: {len(shaders)} DKSH, {len(pack)} bytes, pack id {pid.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
