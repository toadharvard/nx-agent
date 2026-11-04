#!/usr/bin/env python3
"""test_nx_render_bounds.py - S6n: nx_render's input limits (docs/RENDER_NATIVE.md 8.4). Malformed .nxd9 / .nxcap
must end in exit 1 with a message naming the record (or exit 0/3 where the input is merely odd), never in a crash,
a sanitizer report, a hang or an allocation the file did not pay for. Complements tests/v1/test_nx_render_robust.py
(V1's cases, J-0147) with the limits that fix added: sizes, dimensions, counts and id ranges.

    python3 -I test_nx_render_bounds.py --bin <nx_render, preferably the ASan build> [--work <dir>]

Each case: (name, mode, bytes, expected exit codes, substring of stderr or None).
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile


def tag(s):
    return struct.unpack('<I', s.ljust(4).encode())[0]


def rec(t, *words, raw=b''):
    body = struct.pack('<%dI' % len(words), *words) + raw
    return struct.pack('<II', tag(t), len(body)) + body + b'\0' * (-len(body) % 4)


def rec_raw(t, body):
    return struct.pack('<II', tag(t), len(body)) + body + b'\0' * (-len(body) % 4)


D9 = b'NXD9\0\0\0\1'
DEV = rec('DEV', 64, 64, 21, 0)
CAP = b'NXCAP\0\0\1'
IMGDESC = struct.Struct('<HHBBBBHHIQII')     # NxImageDesc (gpu_abi.h), 32 bytes
RB = struct.Struct('<6IQII')                 # NxReadback, 40 bytes


def img(id_, w, h, mips=1, data=b'', data_size=None, fmt=1):
    d = IMGDESC.pack(w, h, fmt, mips, 0, 0, 0, 0, 0, 0, len(data) if data_size is None else data_size, 0)
    return rec_raw('IMGC', struct.pack('<I', id_) + d + data)


CASES = [
    # NXD9: device and resources
    ('B1 DEV 0x0', 'd3d9', D9 + rec('DEV', 0, 0, 21, 0), {1}, 'DEV size'),
    ('B2 DEV 65536x65536', 'd3d9', D9 + rec('DEV', 65536, 65536, 21, 0), {1}, 'DEV size'),
    ('B3 TEX 70000x1 (cast to uint16 before)', 'd3d9', D9 + DEV + rec('TEX', 1, 21, 70000, 1, 1, 0, 0), {1}, 'TEX size'),
    ('B4 TEX 255 levels', 'd3d9', D9 + DEV + rec('TEX', 1, 21, 4, 4, 255, 0, 0), {1}, 'TEX size'),
    ('B5 SURF 0xFFFFFFFF wide', 'd3d9', D9 + DEV + rec('SURF', 1, 1, 21, 0xFFFFFFFF, 4, 0), {1}, 'SURF size'),
    ('B6 VB 1 GiB', 'd3d9', D9 + DEV + rec('VB', 1, 1 << 30), {1}, 'VB/IB size'),
    ('B7 VB 64 MiB x 17 (resident limit)', 'd3d9', D9 + DEV + b''.join(rec('VB', i, 64 << 20) for i in range(1, 18)),
     {1}, 'resident limit'),
    ('B8 VBU into an unknown buffer (ignored)', 'd3d9', D9 + DEV + rec('VBU', 9, 0, 4, raw=b'\0' * 4), {0}, None),
    ('B9 VBU bytes past the record', 'd3d9', D9 + DEV + rec('VB', 1, 16) + rec('VBU', 1, 0, 64, raw=b'\0' * 4),
     {1}, 'VBU/IBU bytes'),
    ('B10 DECL 65 elements', 'd3d9', D9 + DEV + rec('DECL', 1, 65, raw=b'\0' * 520), {1}, 'DECL'),
    ('B11 PSH glsl length past the record', 'd3d9', D9 + DEV + rec('PSH', 1, 0, 0, 0, 0xFFFFFFF0), {1}, 'glsl'),
    ('B12 PSH bytecode padding past the record', 'd3d9', D9 + DEV + rec_raw('PSH', struct.pack('<4I', 1, 0, 0, 3) + b'\0' * 7), {1}, 'glsl'),
    ('B13 CLR 0xFFFFFFFF rects (looped 4 G times before)', 'd3d9', D9 + DEV + rec('CLR', 1, 0, 0, 0, 0xFFFFFFFF),
     {1}, 'CLR rect count'),
    ('B14 VSCF start 0xFFFFFFFF count 2', 'd3d9', D9 + DEV + rec('VSCF', 0xFFFFFFFF, 2, raw=b'\0' * 32), {0}, None),
    ('B15 DRAW 0xFFFFFFFF triangles', 'd3d9', D9 + DEV + rec('DRAW', 4, 0, 0xFFFFFFFF), {3}, None),
    ('B17 TEXC bytes past the record', 'd3d9', D9 + DEV + rec('TEX', 1, 21, 1, 1, 1, 2, 24, raw=b'\0' * 24) + rec('TEXC', 1, 0, 0, 0xFFFFFFF0), {1}, 'TEXC'),
    ('B18 TEXC face 9 (ignored)', 'd3d9', D9 + DEV + rec('TEX', 1, 21, 1, 1, 1, 2, 24, raw=b'\0' * 24) + rec('TEXC', 1, 9, 0, 4, raw=b'\0' * 4), {0}, None),
    ('B19 TEXC face bytes != level / 6', 'd3d9', D9 + DEV + rec('TEX', 1, 21, 1, 1, 1, 2, 24, raw=b'\0' * 24) + rec('TEXC', 1, 0, 0, 8, raw=b'\0' * 8), {1}, 'face bytes'),
    ('B19b cube level bytes not six faces', 'd3d9', D9 + DEV + rec('TEX', 1, 21, 1, 1, 1, 2, 20, raw=b'\0' * 20), {1}, 'six faces'),
    ('B19c cube not square', 'd3d9', D9 + DEV + rec('TEX', 1, 21, 2, 1, 1, 2, 48, raw=b'\0' * 48), {1}, 'square'),
    ('B16 record length 0xFFFFFFFF', 'd3d9', D9 + struct.pack('<II', tag('RS'), 0xFFFFFFFF), {1}, 'truncated'),
    # NXCAP
    ('B20 MEMC 0 bytes', 'nxcap', CAP + rec('MEMC', 1, 0, 0), {1}, 'memory block'),
    ('B21 MEMC 2 GiB', 'nxcap', CAP + rec('MEMC', 1, 0x80000000, 0), {1}, 'memory block'),
    ('B22 MEMC id 300', 'nxcap', CAP + rec('MEMC', 300, 4096, 0), {1}, 'memory id'),
    ('B23 MEMC twice', 'nxcap', CAP + rec('MEMC', 1, 4096, 0) * 2, {1}, 'twice'),
    ('B24 IMGC 0x0', 'nxcap', CAP + img(1, 0, 0), {1}, 'image 1'),
    ('B25 IMGC 65535x65535', 'nxcap', CAP + img(1, 65535, 65535), {1}, 'image 1'),
    ('B26 IMGC data_size past the record', 'nxcap', CAP + img(1, 4, 4, data=b'\0' * 64, data_size=0x10000000),
     {1}, 'data_size'),
    ('B27 IMGC short record', 'nxcap', CAP + rec('IMGC', 1), {1}, 'IMG record'),
    ('B28 SMPL short record', 'nxcap', CAP + rec('SMPC', 1), {1}, 'SMPL record'),
    ('B29 SHDR code sizes past the record', 'nxcap', CAP + rec('SHDC', 1, 1, 0xFFFFFFFD, 8), {1}, 'past its record'),
    ('B30 SUBM range count past the record', 'nxcap', CAP + rec('SUBM', 0, 0, 0, 5), {1}, 'memory range 0'),
    ('B31 SUBM range bytes past the record', 'nxcap', CAP + rec('MEMC', 1, 4096, 0) + rec('SUBM', 0, 0, 0, 1, 1, 0, 0x1000),
     {1}, 'memory range 0'),
    ('B32 SUBM range +4095 4 bytes (block end)', 'nxcap', CAP + rec('MEMC', 1, 4096, 0)
     + rec('SUBM', 0, 0, 0, 1, 1, 4094, 4, raw=b'\0' * 4), {1}, 'outside its block'),
    ('B33 SUBM BindUbo memory id 0xFFFFFFFF', 'nxcap', CAP + rec('SUBM', 0, 0, 6, 7 | 5 << 16, 1, 0, 0xFFFFFFFF, 0, 16, 0),
     {0, 1}, None),
    ('B34 SUBM BindTextures image id past the table', 'nxcap', CAP
     + rec('SUBM', 0, 0, 5, 10 | 4 << 16, 1, 0, 1, 0xFFFFF, 0), {0, 1}, None),
    ('B35 RDBK short record', 'nxcap', CAP + rec('RDBK', 0, 0), {1}, 'RDBK record'),
    ('B36 RDBK 0xFFFFFFFF x 2', 'nxcap', CAP + rec_raw('RDBK', RB.pack(0, 0, 0, 0xFFFFFFFF, 2, 0, 0, 0, 0) + b'\0' * 4),
     {1}, 'RDBK'),
    ('B37 RDBK x + w wraps', 'nxcap', CAP + rec_raw('RDBK', RB.pack(0, 0xFFFFFFF0, 0, 32, 1, 0, 0, 0, 0) + b'\0' * 4),
     {0}, None),
    ('B38 RDBK pixel bytes past the record', 'nxcap', CAP + rec_raw('RDBK', RB.pack(0, 0, 0, 1, 1, 0, 0, 0, 0)
     + struct.pack('<I', 0x1000)), {1}, 'past its record'),
    ('B39 SUBM Draw 0x7fffffff instances (V1 H1 hang)', 'nxcap', CAP
     + rec('SUBM', 0, 0, 7, 11 | 5 << 16, 4, 3, 0x7FFFFFFF, 0, 0, 0, 0), {1}, 'over the limit'),
    ('B40 SUBM DrawIndexed 0xFFFFFFFF indices', 'nxcap', CAP
     + rec('SUBM', 0, 0, 8, 12 | 6 << 16, 4, 0xFFFFFFFF, 1, 0, 0, 0, 0, 0), {1}, 'over the limit'),
    ('B41 SUBM Draw 65536 instances x 65 vertices (product)', 'nxcap', CAP
     + rec('SUBM', 0, 0, 7, 11 | 5 << 16, 4, 65, 65536, 0, 0, 0, 0), {1}, 'over the limit'),
]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--timeout', type=int, default=30)
    a = ap.parse_args(argv)
    work = a.work or tempfile.mkdtemp(prefix='s6n-nxr-')
    os.makedirs(work, exist_ok=True)
    inp, out = os.path.join(work, 'in.bin'), os.path.join(work, 'out')
    env = dict(os.environ, ASAN_OPTIONS='exitcode=99:detect_leaks=0:allocator_may_return_null=0:'
               'max_allocation_size_mb=2048:hard_rss_limit_mb=3072', UBSAN_OPTIONS='halt_on_error=1:exitcode=98')
    bad = 0
    for name, mode, data, rcs, needle in CASES:
        with open(inp, 'wb') as f:
            f.write(data)
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        try:
            p = subprocess.run([a.bin, mode, inp, '-o', out], capture_output=True, timeout=a.timeout, env=env)
            rc, err = p.returncode, p.stderr.decode(errors='replace')
        except subprocess.TimeoutExpired:
            rc, err = 'hang', ''
        ok = rc in rcs and (needle is None or needle in err)
        bad += not ok
        line = err.strip().splitlines()[-1][:120] if err.strip() else ''
        print('%s %s: exit %s%s' % ('ok  ' if ok else 'FAIL', name, rc, ' (' + line + ')' if line else ''), flush=True)
    shutil.rmtree(out, ignore_errors=True)
    os.remove(inp)
    print('nx_render bounds: %d cases, %d failed' % (len(CASES), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
