#!/usr/bin/env python3
"""Census of an NXD9 capture's draws by shader family (render-land / render-lit metric).

    python3 -I nxd9_census.py <capture.nxd9>... [--sdp <dir with shaderpackage*.sdp>] [--native <dir>] [--per-shader] [--json]

Each DRAW/DRWI is attributed to the bound (VS, PS) pair. With --sdp, the bytecode hashes (FNV-1a 64, the same
hash as fnvnx_shader_hash) are matched against the player's shader packages to get the original file names, and
each draw is put in a family by the name of its pixel shader (vertex shader when the PS is unnamed); see FAMILY
below. --native is the nx_render --native directory that tools/native_dir.py lays out from shaders/native.tsv: a draw
is on the native path when both its VS and PS have a replacement (<hash>.glsl) there.

Nothing here reads or prints shader bytecode; the output is counts and file names. Do not commit outputs that
contain hashes of the player's files (they identify game data); the aggregate counts are fine.
"""
import collections, json, os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def fnv1a64(data):
    h = 0xcbf29ce484222325
    for b in data:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def sdp_names(d):
    """hash -> set of names over every shaderpackage*.sdp in d"""
    out = collections.defaultdict(set)
    for fn in sorted(os.listdir(d)):
        if not re.match(r'shaderpackage\d+\.sdp$', fn, re.I):
            continue
        b = open(os.path.join(d, fn), 'rb').read()
        magic, count, _ = struct.unpack_from('<3I', b, 0)
        o = 12
        for _ in range(count):
            name = b[o:o + 256].split(b'\0', 1)[0].decode('latin-1')
            size, = struct.unpack_from('<I', b, o + 256)
            o += 260
            out[fnv1a64(b[o:o + size])].add(name)
            o += size
    return out


# Family by original file name (upper case, without extension). Order matters: the first match wins.
# Families of this stream (render-land): terrain, sky, water, depth, post. Others are listed so the census is
# complete; their owners are in render/native/CLAIMS.md (stream claims).
FAMILY = [
    (r'^SKY', 'sky'),
    (r'^WATER', 'water'),
    (r'^DISTLOD', 'terrain'),
    (r'^IS', 'post'),
    (r'^SM3(ZONLY|DEPTHMAP|SIMPLESHADOW)', 'depth'),
    (r'^TILE', 'tile'),
    (r'^NOLIGHT', 'nolight'),
    (r'^(SLS|SM3)', 'lit'),
    (r'^(GRASS)', 'grass'),
    (r'^(STB|STFROND|STLEAF|DISTTREE)', 'speedtree'),
    (r'^(PAR|PARTICLE|PRECIP)', 'particle'),
    (r'', 'other'),
]
MINE = {'terrain', 'sky', 'water', 'depth', 'post'}


def families(names):
    """the families of every name of one bytecode (identical bytecode can sit under several names)"""
    out = []
    for n in sorted(names):
        base = n.upper().rsplit('.', 1)[0]
        for pat, fam in FAMILY:
            if re.match(pat, base):
                if fam not in out:
                    out.append(fam)
                break
    return out


def family(vnames, pnames):
    """family of a (VS, PS) pair: the first family both stages share (in FAMILY order), else the PS's own"""
    order = [f for _, f in FAMILY]
    fv, fp = families(vnames), families(pnames)
    both = sorted(set(fv) & set(fp), key=order.index)
    if both:
        return both[0]
    for fs in (fp, fv):
        fs = [f for f in fs if f != 'other'] or fs
        if fs:
            return sorted(fs, key=order.index)[0]
    return None


def read_native(d):
    """hashes (int) that have a native replacement in an nx_render --native directory"""
    m = set()
    for fn in os.listdir(d) if d else ():
        if re.match(r'^[0-9a-f]{16}\.glsl$', fn):
            m.add(int(fn[:16], 16))
    return m


def census(path, names=None, native=None):
    names = names or {}
    native = native or {}
    sh = {}                     # (kind, id) -> hash
    vs = ps = 0
    draws = collections.Counter()   # (vs hash, ps hash) -> draws
    frame = 0
    with open(path, 'rb') as f:
        if f.read(8) != b'NXD9\0\0\0\1':
            raise SystemExit('not NXD9: ' + path)
        while True:
            h = f.read(8)
            if len(h) < 8:
                break
            t, n = struct.unpack('<II', h)
            tg = struct.pack('<I', t)
            if tg in (b'VSH ', b'PSH '):
                p = f.read(16)
                sid, lo, hi, _ = struct.unpack('<4I', p)
                sh[(tg[0], sid)] = lo | hi << 32
                f.seek(n - 16 + (-n % 4), 1)
                continue
            if tg in (b'SVS ', b'SPS '):
                sid, = struct.unpack('<I', f.read(4))
                f.seek(n - 4 + (-n % 4), 1)
                h_ = sh.get((ord('V') if tg == b'SVS ' else ord('P'), sid), 0) if sid else 0
                if tg == b'SVS ':
                    vs = h_
                else:
                    ps = h_
                continue
            if tg in (b'DRAW', b'DRWI'):
                draws[(vs, ps)] += 1
            elif tg == b'PRES':
                frame += 1
            f.seek(n + (-n % 4), 1)
    fams = collections.Counter()
    nat = collections.Counter()
    rows = []
    for (v, p), c in draws.items():
        fam = family(names.get(v, ()), names.get(p, ())) or ('fixed' if not (v and p) else 'unnamed')
        on = bool(v and p and v in native and p in native)
        fams[fam] += c
        if on:
            nat[fam] += c
        rows.append((c, fam, on, '/'.join(sorted(names.get(v, {'%016x' % v}))),
                     '/'.join(sorted(names.get(p, {'%016x' % p})))))
    return frame, fams, nat, sorted(rows, reverse=True)


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    names = {}
    if '--sdp' in argv:
        names = sdp_names(argv[argv.index('--sdp') + 1])
        args.remove(argv[argv.index('--sdp') + 1])
    native = set()
    if '--native' in argv:
        native = read_native(argv[argv.index('--native') + 1])
        args.remove(argv[argv.index('--native') + 1])
    out = {}
    for path in args:
        frames, fams, nat, rows = census(path, names, native)
        mine = sum(fams[k] for k in MINE)
        mine_n = sum(nat[k] for k in MINE)
        out[os.path.basename(path)] = {'frames': frames, 'draws': sum(fams.values()), 'families': dict(fams),
                                       'native': dict(nat), 'land_native': mine_n, 'land_total': mine}
        if '--json' not in argv:
            print('%s: %d frames, %d draws; render-land families %d/%d on the native path' % (
                os.path.basename(path), frames, sum(fams.values()), mine_n, mine))
            for k, c in fams.most_common():
                print('  %-10s %5d draws, native %d%s' % (k, c, nat[k], '  (render-land)' if k in MINE else ''))
            if '--per-shader' in argv:
                for c, fam, on, v, p in rows:
                    print('    %5d %-8s %s %s | %s' % (c, fam, 'N' if on else '-', v, p))
    if '--json' in argv:
        print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == '__main__':
    main(sys.argv[1:])
