#!/usr/bin/env python3
# V6n: independent reader of S6n's .nxcap captures (format: render/native/capture/capture.h header comment), used
# to falsify C66 (capture/replay is byte-identical) by mutation: a replay must FLAG a corrupted capture.
#
#   python3 -I render/verify/capture/nxcap.py stat FILE                 record counts, frames, draws per frame
#   python3 -I render/verify/capture/nxcap.py mutate FILE --replay EXE  one mutant per kind, each replayed;
#                                                                       prints whether replay reported a difference
#   python3 -I render/verify/capture/nxcap.py markers FILE [--passes build/v6n/pass_space.tsv]
#                                       C77 PASS markers: draws per (engine pass id, family) per frame, joined with
#                                       V6n's pass space (true numbering) -> name, class, emit kind
#   python3 -I render/verify/capture/nxcap.py inject FILE OUT          a copy with a PASS marker before every draw
#                                       (synthetic ids cycling over 0..758); replaying it must give the same read-backs
# Mutant kinds: vtx (one byte of a bound vertex range), ubo (one byte of a bound UBO slice), state (one bit of an
#   NxRenderState word in a DefineState), texel (one byte of an image's initial data), readback (one byte of a
#   recorded readback = the expected image).
import os, sys, struct, subprocess, collections
TAGS = {0x4F464E49: 'INFO', 0x434D454D: 'MEMC', 0x43474D49: 'IMGC', 0x43504D53: 'SMPC', 0x43444853: 'SHDC',
        0x4D425553: 'SUBM', 0x4B424452: 'RDBK'}
OP = {1: 'DefineState', 2: 'DefineLayout', 3: 'BeginPass', 6: 'BindState', 7: 'BindUbo', 8: 'BindVtx', 9: 'BindIdx',
      10: 'BindTextures', 11: 'Draw', 12: 'DrawIndexed', 19: 'Marker'}
OP_BEGIN, OP_END, OP_DRAW, OP_DRAWIDX, OP_MARKER = 3, 4, 11, 12, 19
MARKER_PASS = 0x53534150   # NX_FOURCC('P','A','S','S')
PASS_UNKNOWN = 0xFFFFFFFF
FAMILIES = {0: 'none', 1: 'tile', 2: 'nolight', 3: 'lit', 4: 'depth', 5: 'land', 6: 'sky', 7: 'imagespace'}

def records(data):
    assert data[:8] == b'NXCAP\0\0\1', 'not an nxcap file'
    off = 8
    while off + 8 <= len(data):
        tag, n = struct.unpack_from('<II', data, off)
        yield TAGS.get(tag, hex(tag)), off + 8, n
        off += 8 + ((n + 3) & ~3)

def submits(data):
    """yields (payload offset, frame, words offset, nwords, [(range offset in file, block, off, size)])"""
    for t, p, n in records(data):
        if t != 'SUBM': continue
        frame, present, words = struct.unpack_from('<III', data, p)
        wo = p + 12; q = wo + 4 * words
        nr = struct.unpack_from('<I', data, q)[0]; q += 4; rng = []
        for _ in range(nr):
            b, o, s = struct.unpack_from('<III', data, q); q += 12
            rng.append((q, b, o, s)); q += (s + 3) & ~3
        yield p, frame, wo, words, rng

def ops(data, wo, words):
    i = 0
    while i < words:
        w = struct.unpack_from('<I', data, wo + 4 * i)[0]; op, argc = w & 0xFFFF, w >> 16
        yield op, wo + 4 * (i + 1), argc
        i += 1 + argc

def stat(path):
    d = open(path, 'rb').read()
    c = collections.Counter(t for t, _, _ in records(d))
    print('records:', dict(c))
    for p, frame, wo, words, rng in submits(d):
        oc = collections.Counter(OP.get(o, o) for o, _, _ in ops(d, wo, words))
        print('frame %d: %d words, %d ranges, draws %d, states %d' % (frame, words, len(rng), oc['Draw'] + oc['DrawIndexed'], oc['DefineState']))

def mutate(path, replay, work):
    d = bytearray(open(path, 'rb').read())
    targets = {}
    subs = list(submits(d))
    # vertex / ubo bytes: find which ranges a BindVtx / BindUbo of the frame names
    for p, frame, wo, words, rng in subs:
        for op, a, argc in ops(d, wo, words):
            if op == 8 and 'vtx' not in targets:
                blk, off, size = struct.unpack_from('<III', d, a)
                for q, b, o, s in rng:
                    if b == blk and o <= off < o + s: targets['vtx'] = q + (off - o) + 3   # the sign/exponent byte of vertex 0's first float
            if op == 7 and 'ubo' not in targets:
                blk, off, size = struct.unpack_from('<III', d, a + 8)
                for q, b, o, s in rng:
                    if b == blk and o <= off < o + s and size >= 16: targets['ubo'] = q + (off - o) + 3
            if op == 1 and 'state' not in targets:
                targets['state'] = a + 4 * 4   # w[0] low word: blend enable / factors
    for t, p, n in records(d):
        if t == 'IMGC' and n > 40 and 'texel' not in targets: targets['texel'] = p + 4 + 32 + n // 3 // 4 * 4
        if t == 'RDBK' and 'readback' not in targets: targets['readback'] = p + 36 + 1000
    os.makedirs(work, exist_ok=True)
    res = {}
    for k, off in sorted(targets.items()):
        m = bytearray(d)
        m[off] ^= 0x01 if k == 'state' else 0x5A
        mp = os.path.join(work, 'mut_%s.nxcap' % k); open(mp, 'wb').write(m)
        r = subprocess.run([replay, mp, os.path.join(work, 'out_' + k)], capture_output=True, text=True)
        line = [l for l in (r.stdout + r.stderr).split('\n') if 'replay' in l or 'differ' in l]
        res[k] = (r.returncode, line[-1] if line else (r.stdout + r.stderr).strip()[-120:])
        print('%-9s @%-8d rc=%d  %s' % (k, off, r.returncode, res[k][1][:140]))
    return res

def pass_table(path):
    t = {}
    if path and os.path.exists(path):
        rows = open(path).read().split('\n'); hdr = rows[0].split('\t')
        for r in rows[1:]:
            if not r: continue
            c = dict(zip(hdr, r.split('\t'))); t[int(c['id'])] = c
    return t

def markers(path, passes):
    """C77 join: every draw is attributed to the PASS marker in force in its NxOp pass (none / UNKNOWN when untagged)"""
    d = open(path, 'rb').read(); pt = pass_table(passes)
    total = collections.Counter(); bad = 0
    for p, frame, wo, words, rng in submits(d):
        cur = None; npass = -1
        for op, a, argc in ops(d, wo, words):
            if op == OP_BEGIN: npass += 1; cur = None
            elif op == OP_MARKER and argc >= 3:
                tag, pid, fam = struct.unpack_from('<III', d, a)
                if tag == MARKER_PASS: cur = (pid, fam)
            elif op in (OP_DRAW, OP_DRAWIDX):
                total[(frame, npass, cur)] += 1
    by = collections.Counter()
    for (frame, npass, cur), n in total.items(): by[cur] += n
    print('pass_id\tfamily\tdraws\tname\tclass\temit_kind')
    for cur, n in sorted(by.items(), key=lambda x: (x[0] is None, x[0] or (0, 0))):
        if cur is None: print('-\t-\t%d\t(untagged)\t\t' % n); continue
        pid, fam = cur
        if pid == PASS_UNKNOWN: print('UNKNOWN\t-\t%d\t(untagged after tagged)\t\t' % n); continue
        c = pt.get(pid)
        if pid > 758: bad += 1
        name = c['name'] if c else ('(not in pass space)' if pid <= 758 else '(INVALID id > 758)')
        print('%d\t%s\t%d\t%s\t%s\t%s' % (pid, FAMILIES.get(fam, fam), n, name, c['class'] if c else '', c['emit_kind'] if c else ''))
    return bad

def inject(path, out):
    d = open(path, 'rb').read(); o = bytearray(d[:8]); k = 0
    for t, p, n in records(d):
        tag = struct.unpack_from('<I', d, p - 8)[0]
        if t != 'SUBM': o += d[p - 8:p + ((n + 3) & ~3)]; continue
        frame, present, words = struct.unpack_from('<III', d, p)
        wo = p + 12; nw = []
        for op, a, argc in ops(d, wo, words):
            if op in (OP_DRAW, OP_DRAWIDX):
                nw += [OP_MARKER | 3 << 16, MARKER_PASS, (k * 97) % 759, 1 + k % 7]; k += 1
            nw += list(struct.unpack_from('<%dI' % (1 + argc), d, a - 4))
        rest = d[wo + 4 * words:p + n]
        pay = struct.pack('<III', frame, present, len(nw)) + struct.pack('<%dI' % len(nw), *nw) + rest
        o += struct.pack('<II', tag, len(pay)) + pay + b'\0' * ((-len(pay)) % 4)
    open(out, 'wb').write(o)
    print('inject: %d markers' % k)

if __name__ == '__main__':
    if sys.argv[1] == 'stat': stat(sys.argv[2])
    elif sys.argv[1] == 'markers':
        a = sys.argv
        sys.exit(1 if markers(a[2], a[a.index('--passes') + 1] if '--passes' in a else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '../../../build/v6n/pass_space.tsv')) else 0)
    elif sys.argv[1] == 'inject': inject(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'mutate':
        rp = sys.argv[sys.argv.index('--replay') + 1]
        mutate(sys.argv[2], rp, os.path.join(os.path.dirname(os.path.abspath(sys.argv[2])), 'v6n_mut'))
