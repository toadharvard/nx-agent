#!/usr/bin/env python3
"""capstore.py - the reference capture cache (T1-fastbench): trimmed, normalized NXD9 captures of the cell sweep,
stored once per cache key, deduplicated across scenes, so the sweep replays them without booting the original.

    capstore.py pack   <store> <id> <capture.nxd9> [--meta k=v ...]
    capstore.py unpack <store> <id> <out.nxd9>                     (exit 3: not in the store)
    capstore.py has    <store> <id> [--scene-hash H]               (exit 0 if present and, with H, made from H)
    capstore.py verify <store> [id...]                             unpack each, check the sha256 in its index line
    capstore.py export <store> <out dir> <id>...                   hard links of those scenes and their blobs (a shard's branch)
    capstore.py stat   <store>

Layout of <store> (one directory; the farm job publishes it to fnv-nx-build branches capture/<key>/<k>of<N>, and a
consumer checks every shard branch of the key out into one directory: the files never collide):
    KEY                       the key's inputs (one `name<TAB>value` per line); written by the job
    index/<id>.tsv            sha256 and size of the whole capture, its VS / PS program hashes, scene hash, meta
    index/<id>.blobs          the blob hashes the scene uses (for export)
    scenes/<id>.nxs.xz        the capture with every large payload replaced by a BLOB record (xz -6)
    blobs/<hh>/<hash>.xz      one payload tail per file (sha256[:32] of the bytes after the payload's first 32)
    ref/<id>/...              scene_stats.py --ref-cache: the reference replay (nxd9play under Wine) of that capture
A BLOB record's payload: the original tag (u32), the first 32 bytes of the original payload, the 16-byte hash. Packing
is lossless for any record (the first 32 bytes stay in the skeleton); unpack checks the whole capture's sha256.
Identical payloads (the same texture in many cells, TEX + its TEXU upload) are stored once, and git stores an
identical blob file once across shard branches and across keys. Game-derived: private repository only.
"""
import argparse, hashlib, lzma, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nxd9io

FORMAT = 'fnvnx-capstore/1'
MIN = 4096                      # payloads at least this long go to blobs/ (the state records stay in the skeleton)
HEAD = 32                       # bytes of a blobbed payload kept in the skeleton (the record's own header fields)
BLOB = nxd9io.tag_u32('BLOB')


def blob_path(store, h):
    return os.path.join(store, 'blobs', h[:2], h + '.xz')


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp%d' % os.getpid()
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def pack(store, sid, cap, meta):
    sk = lzma.LZMACompressor(preset=6)
    whole = hashlib.sha256()
    parts, size, nblob, nnew, newbytes, used, progs = [], 8, 0, 0, 0, {}, {'VSH ': set(), 'PSH ': set()}
    with open(cap, 'rb') as f:
        magic = f.read(8)
    whole.update(magic)
    parts.append(sk.compress(magic))
    for t, p in nxd9io.records(cap):
        ln = len(p)
        rec = struct.pack('<II', nxd9io.tag_u32(t), ln)
        pad = b'\0' * ((-ln) % 4)
        whole.update(rec); whole.update(p); whole.update(pad)
        size += 8 + ln + len(pad)
        if t in progs and ln >= 12:     # the program's hash (fnv1a64 of its bytecode, as scene_stats names it)
            _, lo, hi = struct.unpack_from('<3I', p)
            progs[t].add('%016x' % (lo | hi << 32))
        if ln >= MIN:
            tail = p[HEAD:]
            h = hashlib.sha256(tail).hexdigest()[:32]
            bp = blob_path(store, h)
            if not os.path.isfile(bp):
                write_atomic(bp, lzma.compress(bytes(tail), preset=6))
                nnew += 1; newbytes += len(tail)
            nblob += 1; used[h] = 1
            body = struct.pack('<I', nxd9io.tag_u32(t)) + bytes(p[:HEAD]) + bytes.fromhex(h)
            parts.append(sk.compress(struct.pack('<II', BLOB, len(body)) + body))
        else:
            parts.append(sk.compress(rec + bytes(p) + pad))
    parts.append(sk.flush())
    write_atomic(os.path.join(store, 'scenes', sid + '.nxs.xz'), b''.join(parts))
    shutil.rmtree(os.path.join(store, 'ref', sid), ignore_errors=True)    # a new capture: its old reference is void
    write_atomic(os.path.join(store, 'index', sid + '.blobs'), ''.join(h + '\n' for h in used).encode())
    lines = [('format', FORMAT), ('sha256', whole.hexdigest()), ('size', str(size)), ('blobs', str(nblob)),
             ('vs', ','.join(sorted(progs['VSH ']))), ('ps', ','.join(sorted(progs['PSH '])))] + \
        sorted(meta.items())
    write_atomic(os.path.join(store, 'index', sid + '.tsv'), ''.join(f'{k}\t{v}\n' for k, v in lines).encode())
    return {'size': size, 'blobs': nblob, 'new_blobs': nnew, 'new_bytes': newbytes}


def index(store, sid):
    p = os.path.join(store, 'index', sid + '.tsv')
    if not os.path.isfile(p):
        return None
    return dict(l.rstrip('\n').split('\t', 1) for l in open(p) if '\t' in l)


def unpack(store, sid, out):
    ix = index(store, sid)
    if ix is None:
        raise KeyError(sid)
    data = lzma.decompress(open(os.path.join(store, 'scenes', sid + '.nxs.xz'), 'rb').read())
    whole = hashlib.sha256()
    tmp = out + '.tmp%d' % os.getpid()
    with open(tmp, 'wb') as f:
        def put(b):
            whole.update(b); f.write(b)
        put(data[:8])
        at, n = 8, len(data)
        while at + 8 <= n:
            t, ln = struct.unpack_from('<II', data, at)
            body = data[at + 8: at + 8 + ln]
            at += 8 + ((ln + 3) & ~3)
            if t == BLOB:
                ot = struct.unpack_from('<I', body)[0]
                head, h = body[4:4 + HEAD], body[4 + HEAD:].hex()
                tail = lzma.decompress(open(blob_path(store, h), 'rb').read())
                pl = len(head) + len(tail)
                put(struct.pack('<II', ot, pl)); put(head); put(tail); put(b'\0' * ((-pl) % 4))
            else:
                put(struct.pack('<II', t, ln)); put(body); put(b'\0' * ((-ln) % 4))
    if whole.hexdigest() != ix['sha256']:
        os.remove(tmp)
        raise ValueError(f'{sid}: sha256 {whole.hexdigest()} != index {ix["sha256"]}')
    os.replace(tmp, out)
    return ix


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('pack'); p.add_argument('store'); p.add_argument('id'); p.add_argument('capture')
    p.add_argument('--meta', action='append', default=[])
    p = sub.add_parser('unpack'); p.add_argument('store'); p.add_argument('id'); p.add_argument('out')
    p = sub.add_parser('has'); p.add_argument('store'); p.add_argument('id'); p.add_argument('--scene-hash')
    p = sub.add_parser('verify'); p.add_argument('store'); p.add_argument('ids', nargs='*')
    p = sub.add_parser('export'); p.add_argument('store'); p.add_argument('out'); p.add_argument('ids', nargs='+')
    p = sub.add_parser('stat'); p.add_argument('store')
    a = ap.parse_args()
    if a.cmd == 'pack':
        meta = dict(m.split('=', 1) for m in a.meta)
        r = pack(a.store, a.id, a.capture, meta)
        print(f"capstore: {a.id}: {r['size'] >> 20} MB, {r['blobs']} blobs ({r['new_blobs']} new, {r['new_bytes'] >> 20} MB)")
        return 0
    if a.cmd == 'unpack':
        try:
            unpack(a.store, a.id, a.out)
        except KeyError:
            print(f'capstore: {a.id}: not in {a.store}', file=sys.stderr); return 3
        except (OSError, ValueError, lzma.LZMAError) as e:
            print(f'capstore: {a.id}: {e}', file=sys.stderr); return 1
        return 0
    if a.cmd == 'has':
        ix = index(a.store, a.id)
        ok = ix is not None and os.path.isfile(os.path.join(a.store, 'scenes', a.id + '.nxs.xz')) and \
            (not a.scene_hash or ix.get('scene') == a.scene_hash)
        return 0 if ok else 1
    if a.cmd == 'verify':
        ids = a.ids or sorted(f[:-4] for f in os.listdir(os.path.join(a.store, 'index')) if f.endswith('.tsv'))
        bad = 0
        with tempfile.TemporaryDirectory() as t:
            for sid in ids:
                try:
                    unpack(a.store, sid, os.path.join(t, 'x.nxd9'))
                except Exception as e:
                    print(f'capstore: verify {sid}: {e}'); bad += 1
        print(f'capstore: verify: {len(ids) - bad} of {len(ids)} ok')
        return 1 if bad else 0
    if a.cmd == 'export':
        n = 0
        def link(rel):
            src, dst = os.path.join(a.store, rel), os.path.join(a.out, rel)
            if os.path.isfile(src) and not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try: os.link(src, dst)
                except OSError: shutil.copy2(src, dst)
        if os.path.isfile(os.path.join(a.store, 'KEY')):
            link('KEY')
        for sid in a.ids:
            if index(a.store, sid) is None:
                continue
            n += 1
            for rel in ('index/%s.tsv' % sid, 'index/%s.blobs' % sid, 'scenes/%s.nxs.xz' % sid):
                link(rel)
            for h in open(os.path.join(a.store, 'index', sid + '.blobs')).read().split():
                link(os.path.relpath(blob_path(a.store, h), a.store))
            for r, _, fs in os.walk(os.path.join(a.store, 'ref', sid)):
                for f in fs:
                    link(os.path.relpath(os.path.join(r, f), a.store))
        print(f'capstore: exported {n} of {len(a.ids)} scenes to {a.out}')
        return 0
    if a.cmd == 'stat':
        def du(d):
            return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(os.path.join(a.store, d)) for f in fs)
        ids = [f[:-4] for f in os.listdir(os.path.join(a.store, 'index')) if f.endswith('.tsv')] if os.path.isdir(os.path.join(a.store, 'index')) else []
        raw = sum(int(index(a.store, s).get('size', 0)) for s in ids)
        nb = sum(len(fs) for _, _, fs in os.walk(os.path.join(a.store, 'blobs')))
        print(f'scenes {len(ids)}  raw {raw >> 20} MB  stored: skeletons {du("scenes") >> 20} MB, blobs {nb} / '
              f'{du("blobs") >> 20} MB, ref {du("ref") >> 20} MB')
        return 0


if __name__ == '__main__':
    sys.exit(main())
