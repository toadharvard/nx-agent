#!/usr/bin/env python3
"""translated.nxsp: S6b's install-time translation of the player's shader packages as one precompiled pack for the
native renderer's translated path (n1/translated.h; RENDER_NATIVE.md §4.5). Owner rule / NX1 check 9: no shader is
compiled on the console, so every program the pack lists carries its DKSH.

    sdp2dksh -o <dir> --glsl shaderpackage*.sdp                       # S6b, on the player's side (render/shaders)
    python3 -I render/native/tools/nxsp_pack.py <dir> <translated.nxsp> [--names shaderpackage019.sdp] [--glsl]
        [--check]

Container: NX1's NXSP v1 (toolchain/nro/shaderpack.py, the shaders.nxsp of our own shaders): header "NXSP", u32
version 1, count, index offset, data offset, u8[12] pack id (sha256[:12] of index + data); index rows of 104 bytes
(name[48] family[24] variant[16], u32 stage, offset, size, crc32); blobs at 256-byte boundaries. Rows:
  translated       name = the bytecode hash (fnvnx_shader_hash, 16 lowercase hex), variant = "<vs|ps>_<maj>_<min>/<mask>"
                   (mask = 4 hex digits: the VS input locations, fnvnx_dk_layout.h), blob = DKSH
  translated-glsl  only with --glsl (host / emulator packs; never on the console): the GLSL, NUL-terminated
  translated-name  with --names <package>: name = the program's name in that package, variant = its hash, size 0
The console reads it from fnvshd:/translated.nxsp. --check (the package check): every program in <dir>/index.tsv has
status ok and a DKSH; exit 1 with the list otherwise. The output is derived from the game's shaders: it stays on the
player's machine / the private artifacts, never in this repository."""
import argparse, hashlib, json, os, struct, sys, zlib

LOC_OF = {('POSITION', 0): 0, ('BLENDWEIGHT', 0): 1, ('BLENDINDICES', 0): 2, ('NORMAL', 0): 3, ('COLOR', 0): 4,
          ('COLOR', 1): 5, ('TANGENT', 0): 14, ('BINORMAL', 0): 15}
for _k in range(8):
    LOC_OF[('TEXCOORD', _k)] = 6 + _k
PACK_PATH = 'fnvshd:/translated.nxsp'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    ap.add_argument('out')
    ap.add_argument('--names', help='basename of the shader package whose program names to include')
    ap.add_argument('--glsl', action='store_true', help='also the GLSL (GL reference / emulator packs only)')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(a.dir, 'index.tsv'))][1:]
    progs, names, bad = {}, [], []
    for r in rows:
        if len(r) < 5:   # a truncated index line (an interrupted sdp2dksh run)
            bad.append('%s (truncated index.tsv line)' % '\t'.join(r)[:60])
            continue
        h, pkg, name, model, status = r[0], r[1], r[2], r[3], r[4]
        dk = os.path.join(a.dir, h + '.dksh')
        blob = open(dk, 'rb').read() if os.path.exists(dk) else b''
        if status != 'ok' or len(blob) < 24 or blob[:4] != b'DKSH':
            bad.append('%s %s %s %s' % (h, os.path.basename(pkg), name, status))
            continue
        if a.names and os.path.basename(pkg) == a.names and len(name) < 48:
            names.append((name, h))
        if h in progs:
            continue
        st, maj, mnr = model.split('_')
        mask = 0
        jp = os.path.join(a.dir, h + '.json')
        if st == 'vs' and os.path.exists(jp):
            for i in json.load(open(jp)).get('inputs', []):
                loc = LOC_OF.get((i['usage'], i['index']))
                if loc is not None:
                    mask |= 1 << loc
        gl = os.path.join(a.dir, h + '.glsl')
        progs[h] = ('%s_%s_%s/%04x' % (st, maj, mnr, mask), 0 if st == 'vs' else 1, blob,
                    open(gl, 'rb').read() + b'\0' if a.glsl and os.path.exists(gl) else None)
    if a.check and bad:
        print('package check FAILED: %d programs without a precompiled DKSH (a console compile would be needed):' % len(bad))
        for b in bad[:50]:
            print('  ' + b)
        return 1
    entries = []   # (name, family, variant, stage, blob)
    for h in sorted(progs):
        var, st, dk, gl = progs[h]
        entries.append((h, 'translated', var, st, dk))
        if gl is not None:
            entries.append((h, 'translated-glsl', '', st, gl))
    for name, h in names:
        entries.append((name, 'translated-name', h, 0, b''))
    n, idx = len(entries), 32
    data_off = (idx + n * 104 + 255) & ~255
    index, data = bytearray(), bytearray()
    for name, fam, var, st, blob in entries:
        off = data_off + len(data)
        index += struct.pack('<48s24s16sIIII', name.encode(), fam.encode(), var.encode(), st, off, len(blob),
                             zlib.crc32(blob) if blob else 0)
        data += blob + bytes((-len(blob)) % 256)
    pack_id = hashlib.sha256(bytes(index) + bytes(data)).digest()[:12]
    out = struct.pack('<4sIIII12s', b'NXSP', 1, n, idx, data_off, pack_id) + bytes(index) + \
        bytes(data_off - idx - len(index)) + bytes(data)
    open(a.out, 'wb').write(out)
    nv = sum(1 for v in progs.values() if v[1] == 0)
    print('translated.nxsp: %d programs (%d vertex, %d fragment), all with DKSH; %d names; glsl %s; %d bytes; '
          '%d listed programs without DKSH' % (len(progs), nv, len(progs) - nv, len(names), 'yes' if a.glsl else 'no',
                                               len(out), len(bad)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
