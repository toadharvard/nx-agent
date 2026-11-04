#!/usr/bin/env python3
"""test_nx_render_robust.py - V1 (D14) against the render machine's nx_render (docs/RENDER_NATIVE.md section 8,
J-0135): malformed .nxd9 / .nxcap input must end in exit 1 or 2 (8.1: "runtime failure (malformed input ...)"), never
in a crash, a sanitizer report or a hang. NXD9 is what S16's converter writes from the exe's D3D9 stream, so its
reader takes outside input.

    python3 -I tests/v1/test_nx_render_robust.py --bin <nx_render> [--seed <file.nxd9|file.nxcap> ...] [--iters N]
        [--work <dir>]

--bin: preferably built with -fsanitize=address,undefined (render/native/capture/{nx_render,d3d9_exec,replay}.cpp);
ASan reports exit 99, UBSan 98. Targeted cases (synthetic bytes, built here) first, then --iters random mutations of
each seed (u32 words set to boundary values, truncation), one input file rewritten in place (no per-iteration
copies). One line per case; exit 1 when any case crashes or hangs. Seeds: tests/s6n/test_nx_render.py's t.nxd9 and
n0.nxcap (make -C tests/s6n nx_render writes them under OUT/nx_render_test).
H1 (per .nxcap seed): the seed's first SUBM Draw op with 0x7fffffff instances must end in exit 1 or 3 within --timeout
(.nxd9 skips draws over 4 M vertices; RENDER_NATIVE.md 8.4 sets no draw bound for .nxcap).
"""
import argparse
import os
import random
import shutil
import struct
import subprocess
import sys

OK_RC = {0, 1, 2, 3}       # nx_render's own exit codes; anything else (signal, sanitizer) is a defect


def tag(s):
    return struct.unpack('<I', s.encode())[0]


def rec(t, *words, raw=b''):
    body = struct.pack('<%dI' % len(words), *words) + raw
    return struct.pack('<II', tag(t), len(body)) + body + b'\0' * (-len(body) % 4)


def rec_len(t, length, body):   # a record whose length field says `length`
    return struct.pack('<II', tag(t), length) + body


NXD9 = b'NXD9\0\0\0\1'
DEV = rec('DEV ', 64, 64, 21, 0)
TEX = rec('TEX ', 7, 21, 4, 4, 1, 0, 64, raw=b'\0' * 64)
D9 = {
    'D1 TEXU bytes 0xFFFFFFF8 (12 + b wraps)': NXD9 + DEV + TEX + rec('TEXU', 7, 0, 0xFFFFFFF8, raw=b'\0' * 16),
    'D2 VBU offset 0xFFFFFF00 + 0x100 bytes (off + b wraps)': NXD9 + DEV + rec('VB  ', 3, 16)
        + rec('VBU ', 3, 0xFFFFFF00, 0x100, raw=b'\xab' * 0x100),
    'D3 VB size 0xFFFFFFFF (allocated from an 8-byte record)': NXD9 + DEV + rec('VB  ', 3, 0xFFFFFFFF),
    'D4 DECL count 0x7FFFFFFF (allocated before the length check)': NXD9 + DEV + rec('DECL', 5, 0x7FFFFFFF),
    'D5 VSH bytecode 0xFFFFFFF0 (16 + bc + 4 wraps)': NXD9 + DEV + rec('VSH ', 9, 1, 2, 0xFFFFFFF0, raw=b'\0' * 32),
    'D6 TEX level bytes 0xFFFFFFF0 (q + 4 + b wraps)': NXD9 + DEV + rec('TEX ', 8, 21, 4, 4, 2, 0, 0xFFFFFFF0,
                                                                        raw=b'\0' * 32),
    'D7 control: truncated record': NXD9 + DEV + rec_len('RS  ', 0x1000, b'\0' * 8),
}
NXCAP = b'NXCAP\0\0\1'
CAP = {
    'C1 MEMC with a 0-byte payload (reads 12)': NXCAP + rec_len('MEMC', 0, b''),
    'C2 SUBM range: offset 0x10000000 into a 4 KiB block': NXCAP + rec('MEMC', 1, 4096, 0)
        + rec('SUBM', 0, 0, 1, 0, 1, 1, 0x10000000, 16, raw=b'\xcd' * 16),
    'C3 SUBM words 0x100000 in a 12-byte record': NXCAP + rec('SUBM', 0, 0, 0x100000),
    'C4 SUBM DefineState shader ids 0xFFFF (shMap has 4097)': NXCAP
        + rec('SUBM', 0, 0, 4, (1 | 3 << 16), 0, 0xFFFF, 0xFFFF, 0),
    'C5 SUBM BeginPass image id 0x00FFFFFF (imgMap has 4098)': NXCAP
        + rec('SUBM', 0, 0, 3, (3 | 2 << 16), 0x00FFFFFF, 0, 0),
    'C6 SUBM argc 0xFFFF past the stream': NXCAP + rec('SUBM', 0, 0, 1, (2 | 0xFFFF << 16), 0),
}


def run(binary, kind, path, out, timeout):
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    env = dict(os.environ, ASAN_OPTIONS='exitcode=99:detect_leaks=0:allocator_may_return_null=0:'
               'max_allocation_size_mb=2048:hard_rss_limit_mb=3072', UBSAN_OPTIONS='halt_on_error=1:exitcode=98')
    try:
        p = subprocess.run([binary, kind, path, '-o', out], capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 'HANG', ''
    err = p.stderr.decode(errors='replace')
    first = next((l for l in err.splitlines() if 'ERROR: AddressSanitizer' in l or 'runtime error' in l
                  or 'Sanitizer' in l), err.strip().splitlines()[-1] if err.strip() else '')
    return p.returncode, first[:160]


def verdict(rc, msg):
    if rc in OK_RC:
        return 'ok (exit %d%s)' % (rc, ', ' + msg if rc == 1 and msg else '')
    return 'CRASH: %s %s' % ('signal %d' % -rc if isinstance(rc, int) and rc < 0 else rc, msg)


def mutate(data, rng, hdr):
    b = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        k = rng.random()
        if k < 0.1 and len(b) > hdr + 4:
            del b[rng.randrange(hdr, len(b)):]
        else:
            i = rng.randrange(hdr, max(hdr + 1, len(b) - 3)) & ~3
            v = rng.choice([0, 1, 0xFFFFFFFF, 0xFFFFFFFC, 0x7FFFFFFF, 0x80000000, 0x10000, 0xFFFF, 4096, 4097,
                            rng.getrandbits(32), rng.getrandbits(8)])
            b[i:i + 4] = struct.pack('<I', v)
    return bytes(b)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', required=True)
    ap.add_argument('--seed', action='append', default=[])
    ap.add_argument('--iters', type=int, default=0)
    ap.add_argument('--work', default=None)
    ap.add_argument('--timeout', type=int, default=30)
    ap.add_argument('--rng', type=int, default=1)
    a = ap.parse_args(argv)
    import tempfile
    work = a.work or tempfile.mkdtemp(prefix='v1-nxr-')
    os.makedirs(work, exist_ok=True)
    inp, out = os.path.join(work, 'in.bin'), os.path.join(work, 'out')
    bad = 0
    for kind, cases in (('d3d9', D9), ('nxcap', CAP)):
        for name, data in cases.items():
            with open(inp, 'wb') as f:
                f.write(data)
            v = verdict(*run(a.bin, kind, inp, out, a.timeout))
            bad |= not v.startswith('ok')
            print('%s: %s' % (name, v), flush=True)
    rng = random.Random(a.rng)
    for seed in a.seed:
        kind = 'nxcap' if seed.endswith('.nxcap') else 'd3d9'
        data = open(seed, 'rb').read()
        if kind == 'nxcap':
            # H1: the first SUBM Draw op (word 0x0005000b: op 0x0b, 5 arguments; first, vertices, instances = 1) of
            # the seed with 0x7fffffff instances. .nxd9 skips draws over 4 M vertices (RENDER_NATIVE.md 8.4); .nxcap
            # has no such bound, so a 2^31-instance draw must end in exit 1 or 3, not run for minutes.
            at = -1
            for k in range(0, len(data) - 24, 4):
                w = struct.unpack_from('<6I', data, k)
                if w[0] == 0x0005000b and 0 < w[2] <= 0x10000 and w[3] == 1:
                    at = k + 12
                    break
            if at < 0:
                print('H1 %s: skip (no 5-argument Draw op with 1 instance)' % os.path.basename(seed))
            else:
                h = bytearray(data)
                struct.pack_into('<I', h, at, 0x7fffffff)
                with open(inp, 'wb') as f:
                    f.write(h)
                v = verdict(*run(a.bin, kind, inp, out, a.timeout))
                bad |= not v.startswith('ok')
                print('H1 %s Draw instances 0x7fffffff at %d: %s' % (os.path.basename(seed), at, v), flush=True)
        crashes = {}
        for it in range(a.iters):
            m = mutate(data, rng, 8)
            with open(inp, 'wb') as f:
                f.write(m)
            rc, msg = run(a.bin, kind, inp, out, a.timeout)
            if rc not in OK_RC:
                key = msg.split(' at pc')[0][:90] or str(rc)
                if key not in crashes:
                    crashes[key] = it
                    with open(os.path.join(work, 'crash-%s-%d.bin' % (kind, len(crashes))), 'wb') as f:
                        f.write(m)    # first input per distinct report only
        bad |= bool(crashes)
        print('M %s x%d: %s' % (os.path.basename(seed), a.iters, 'ok' if not crashes else '%d distinct crashes: %s' % (
            len(crashes), '; '.join('%s (iter %d)' % kv for kv in crashes.items()))), flush=True)
    shutil.rmtree(out, ignore_errors=True)
    os.remove(inp)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
