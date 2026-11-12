#!/usr/bin/env python3
"""Synthetic check of imagerec.py: the record, its refusals (malformed relocations, code read as bytes) and the pinned loader."""
import hashlib
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'verify'))
import cas
import imagerec

BASE, TEXT, RD, REL = 0x400000, 0x1000, 0x2000, 0x3000
OK = []


def check(name, got, want):
    if got != want:
        print('FAIL', name, got, want)
        sys.exit(1)
    OK.append(name)


def pe(reloc, text=None):
    """PE32 with .text (push 0x402008 [relocated]; mov eax,[0x402010] [relocated]; ret), .rdata (data) and a .reloc section of `reloc` bytes."""
    if text is None:
        text = bytes([0x68]) + struct.pack('<I', BASE + RD + 8) + bytes([0xa1]) + struct.pack('<I', BASE + RD + 0x10) + bytes([0xc3])
    secs = [(b'.text', TEXT, text.ljust(0x200, b'\0'), 0x60000020), (b'.rdata', RD, bytes(range(256)) * 2, 0x40000040),
            (b'.reloc', REL, bytes(reloc).ljust(0x200, b'\0'), 0x42000040)]
    hdr = bytearray(0x400)
    hdr[0:2] = b'MZ'
    struct.pack_into('<I', hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', hdr, 0x44, 0x14c, len(secs), 0, 0, 0, 224, 0x2102)
    o = 0x58
    struct.pack_into('<H', hdr, o, 0x10b)
    struct.pack_into('<I', hdr, o + 16, TEXT)
    struct.pack_into('<I', hdr, o + 28, BASE)
    struct.pack_into('<II', hdr, o + 32, 0x1000, 0x200)
    struct.pack_into('<II', hdr, o + 56, 0x4000, 0x400)
    struct.pack_into('<I', hdr, o + 92, 16)
    struct.pack_into('<II', hdr, o + 96 + 40, REL, len(reloc))
    sh, fo, body = o + 224, 0x400, b''
    for i, (nm, rva, data, ch) in enumerate(secs):
        struct.pack_into('<8sIIIIIIHHI', hdr, sh + 40 * i, nm, len(data), rva, len(data), fo + len(body), 0, 0, 0, 0, ch)
        body += data
    return bytes(hdr) + body


def block(entries, page=TEXT):
    b = b''.join(struct.pack('<H', e) for e in entries)
    return struct.pack('<II', page, 8 + len(b)) + b


GOOD = block([0x3001, 0x3006, 0])        # HIGHLOW at TEXT+1 and TEXT+6, one padding entry
RUNS = {BASE + TEXT: [(BASE + TEXT, BASE + TEXT + 11)]}


def raises(exc, f, *a):
    try:
        f(*a)
    except exc as e:
        return str(e)
    return None


rec = imagerec.ImageRec(pe(GOOD), 'a.exe', RUNS)
check('relocs', sorted(rec.relocs), [BASE + TEXT + 1, BASE + TEXT + 6])
check('read of data', rec.read(BASE + RD, 4), bytes([0, 1, 2, 3]))
check('word of data', rec.word(BASE + RD + 4), 0x07060504)
check('read in a run refused', raises(imagerec.CodeRead, rec.read, BASE + TEXT, 4) is not None, True)
check('read crossing into a run refused', raises(imagerec.CodeRead, rec.read, BASE + TEXT - 2, 4) is not None, True)
check('word of a relocated immediate in a run: the decoded immediate', rec.word(BASE + TEXT + 1), BASE + RD + 8)
check('word of a relocated displacement in a run: the decoded displacement', rec.word(BASE + TEXT + 6), BASE + RD + 0x10)
check('word of an unrelocated field in a run refused', raises(imagerec.CodeRead, rec.word, BASE + TEXT + 2) is not None, True)
check('decode_at', [(i.mnemonic, i.size) for i in rec.decode_at(BASE + TEXT, 2)], [('push', 5), ('mov', 5)])
check('decode_range', [i.mnemonic for i in rec.decode_range(BASE + TEXT, BASE + TEXT + 11)], ['push', 'mov', 'ret'])
check('no runs: bytes readable', imagerec.ImageRec(pe(GOOD), 'a.exe', {}).word(BASE + TEXT + 1), BASE + RD + 8)

# data in code (data_in_code.tsv): a read in a declared data range is answered as data; code stays refused
T0 = BASE + TEXT
d = imagerec.ImageRec(pe(GOOD), 'a.exe', {T0: [(T0, T0 + 11)]}, None, [(T0 + 1, T0 + 5)], {T0})        # candidate entry: data wins
check('data range over a candidate run: read answered', d.read(T0 + 1, 4), pe(GOOD)[0x400 + 1:0x400 + 5])
check('data range: word of a relocated field answered raw', d.word(T0 + 1), BASE + RD + 8)
check('read straddling data and code refused', raises(imagerec.CodeRead, d.read, T0 + 3, 4) is not None, True)
check('code outside the data range still refused', raises(imagerec.CodeRead, d.read, T0 + 6, 4) is not None, True)
check('in_data', (d.in_data(T0 + 1, 4), d.in_data(T0, 4), d.in_data(T0 + 2, 4)), (True, False, False))
e = imagerec.ImageRec(pe(GOOD), 'a.exe', {T0: [(T0, T0 + 11)]}, None, [(BASE + RD, BASE + RD + 4)], set())
check('data range outside every run: fine, read answered', (e.in_data(BASE + RD, 4), e.read(BASE + RD, 4)), (True, bytes([0, 1, 2, 3])))
check('data range over a run that is not a candidate: contradictory fact', raises(imagerec.ImageError, imagerec.ImageRec, pe(GOOD), 'a.exe',
                                                                                  {T0: [(T0, T0 + 11)]}, None, [(T0 + 1, T0 + 5)], set()) is not None, True)
check('parse: candidate column', imagerec._runs_of('# base=0x10:\n10\t10-20,30-40\tcandidate\n50\t50-60\n'),
      ({0x10: [(0x10, 0x20), (0x30, 0x40)], 0x50: [(0x50, 0x60)]}, 0x10, {0x10}))
check('parse: bad third column', raises(imagerec.ImageError, imagerec._runs_of, '# base=0x10:\n10\t10-20\tcand\n') is not None, True)
check('parse: data ranges', imagerec._data_of('# base=0x10:\n20\t30\tjumptable\tjmp [eax*4+20] at 11\n'), ([(0x20, 0x30)], 0x10))
check('parse: reversed data range', raises(imagerec.ImageError, imagerec._data_of, '20\t10\tx\ty\n') is not None, True)

# malformed relocation directories are errors, never a silent stop
bad = {'block size under 8': struct.pack('<II', TEXT, 4) + b'\0' * 8,
       'block size 0 (zero padding after a block)': GOOD + b'\0' * 8,
       'block past the directory end': struct.pack('<II', TEXT, 64) + struct.pack('<H', 0x3001),
       'odd block size': struct.pack('<II', TEXT, 9) + b'\0',
       'header cut short': GOOD + b'\0' * 4,
       'type 1 (HIGH)': block([0x1001]),
       'type 10 (DIR64)': block([0xa001]),
       'type 5': block([0x5001])}
for k, v in bad.items():
    check('malformed relocations: ' + k, raises(imagerec.ImageError, imagerec.ImageRec, pe(v), 'a.exe', RUNS) is not None, True)
check('padding only', imagerec.ImageRec(pe(block([0, 0])), 'a.exe', RUNS).relocs, set())

# the pinned loader
with tempfile.TemporaryDirectory() as t:
    img = os.path.join(t, 'a.exe')
    data = pe(GOOD)
    open(img, 'wb').write(data)
    facts = os.path.join(t, 'facts')
    os.mkdir(facts)

    def put(sha, base=0x10000):
        with open(os.path.join(facts, 'ranges.tsv'), 'w') as fh:
            fh.write('# base=%x:\n%x\t%x-%x\n' % (base, base + TEXT, base + TEXT, base + TEXT + 11))
        json.dump({'format': 'fnvnx-liftfacts-manifest/1', 'images': [{'image': 'a.exe', 'sha256': sha, 'ranges': 'ranges.tsv'}]},
                  open(os.path.join(facts, 'manifest.json'), 'w'))
        return {'tree_sha256': cas.tree_hash(facts)}

    pin = put(hashlib.sha256(data).hexdigest())
    r = imagerec.load(img, facts, pin)
    check('load: runs rebased to the image base', r.runs, {BASE + TEXT: [(BASE + TEXT, BASE + TEXT + 11)]})
    check('load: facts', (r.facts, r.sha == hashlib.sha256(data).hexdigest()), (None, True))
    check('load: tree not the pin', raises(imagerec.ImageError, imagerec.load, img, facts, {'tree_sha256': '0' * 64}) is not None, True)
    imagerec._trees.clear()
    pin = put('0' * 64)
    check('load: image sha not the manifest\'s', raises(imagerec.ImageError, imagerec.load, img, facts, pin) is not None, True)
    imagerec._trees.clear()
    with open(os.path.join(facts, 'data_in_code.tsv'), 'w') as fh:
        fh.write('# base=%x:\n%x\t%x\tjumptable\tx\n' % (0x10000, 0x10000 + TEXT + 1, 0x10000 + TEXT + 5))
    with open(os.path.join(facts, 'ranges.tsv'), 'w') as fh:
        fh.write('# base=%x:\n%x\t%x-%x\tcandidate\n' % (0x10000, 0x10000 + TEXT, 0x10000 + TEXT, 0x10000 + TEXT + 11))
    json.dump({'format': 'fnvnx-liftfacts-manifest/1', 'images': [{'image': 'a.exe', 'sha256': hashlib.sha256(data).hexdigest(), 'ranges': 'ranges.tsv',
                                                                    'data_in_code': 'data_in_code.tsv'}]}, open(os.path.join(facts, 'manifest.json'), 'w'))
    r = imagerec.load(img, facts, {'tree_sha256': cas.tree_hash(facts)})
    check('load: data range rebased, candidate entry', (r.data, r.candidates), ([(T0 + 1, T0 + 5)], {T0}))
    check('load: word in the data range', r.word(T0 + 1), BASE + RD + 8)
    imagerec._trees.clear()
    imagerec._trees.clear()
    json.dump({'format': 'fnvnx-liftfacts-manifest/1', 'images': []}, open(os.path.join(facts, 'manifest.json'), 'w'))
    pin = {'tree_sha256': cas.tree_hash(facts)}
    r = imagerec.load(img, facts, pin)
    check('load: image the manifest does not list', (r.runs, r.facts), ({}, 'no function facts'))
print('ok test_imagerec (%d checks)' % len(OK))
