#!/usr/bin/env python3
"""Per-image facts stage (contract/exefacts.py image mode) on synthetic PE32 images built here: a toy closure of 2 images gives 2 documents and an index;
a missing image or one of another sha is red `facts:missing:<image>` / `facts:sha:<image>`; the completeness check sees a lost document.  No game data."""
import hashlib
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import exefacts  # noqa: E402
import store  # noqa: E402

fails = []
BASE = 0x10000000


def check(what, ok, got=''):
    if not ok:
        fails.append('%s: %s' % (what, got))


def pe(imp=None):
    """A PE32 DLL: one code section at rva 0x1000. imp = (dll name, import name) puts an import directory there (IAT slot at rva 0x1200), else none."""
    sec = bytearray(0x1000)
    dd = {}
    if imp:
        dll, fn = imp
        struct.pack_into('<IIIII', sec, 0, 0x1100, 0, 0, 0x1400, 0x1200)
        struct.pack_into('<II', sec, 0x100, 0x1300, 0)
        struct.pack_into('<II', sec, 0x200, 0x1300, 0)
        sec[0x300:0x302] = b'\0\0'
        sec[0x302:0x302 + len(fn) + 1] = fn.encode() + b'\0'
        sec[0x400:0x400 + len(dll) + 1] = dll.encode() + b'\0'
        dd[1] = (0x1000, 40)
    h = bytearray(0x400)
    h[0:2] = b'MZ'
    struct.pack_into('<I', h, 0x3c, 0x80)
    h[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', h, 0x84, 0x14c, 1, 0, 0, 0, 0xe0, 0x2102)
    o = 0x98
    struct.pack_into('<H', h, o, 0x10b)
    struct.pack_into('<I', h, o + 28, BASE)
    struct.pack_into('<II', h, o + 32, 0x1000, 0x200)
    struct.pack_into('<I', h, o + 56, 0x2000)
    struct.pack_into('<I', h, o + 92, 16)
    for i, (rva, size) in dd.items():
        struct.pack_into('<II', h, o + 96 + 8 * i, rva, size)
    s = o + 0xe0
    h[s:s + 5] = b'CODE\0'
    struct.pack_into('<IIII', h, s + 8, 0x1000, 0x1000, 0x1000, 0x400)
    struct.pack_into('<I', h, s + 36, 0x60000020)
    return bytes(h) + bytes(sec)


def setup(files, rows=None):
    d = tempfile.mkdtemp(prefix='imgfacts-')
    img = os.path.join(d, 'img')
    os.makedirs(img)
    for n, data in files.items():
        open(os.path.join(img, n), 'wb').write(data)
    rows = rows if rows is not None else [(n, hashlib.sha256(data).hexdigest()) for n, data in files.items()]
    mt = '# name\tsha256\n' + ''.join('%s\t%s\t1\n' % r for r in rows)
    return d, img, mt


A, B = pe(('kernel32.dll', 'Foo')), pe()
d, img, mt = setup({'a.dll': A, 'b.dll': B})
out = os.path.join(d, 'out')
bad = exefacts.image_stage(mt, [img], out)
check('toy closure of 2 images: green', bad == [] and exefacts.image_check(mt, out) == [], bad)
check('2 documents and the index', sorted(os.listdir(out)) == ['a.dll.json', 'b.dll.json', 'images.tsv'], os.listdir(out))
fa = store.load(os.path.join(out, 'a.dll.json'), 'imgfacts')
check('imports of the image (same Image reader as the exe)', fa['imports'] == {BASE + 0x1200: ('kernel32.dll', 'Foo')}, fa['imports'])
check('meta names the image sha', fa['meta']['image_sha256'] == hashlib.sha256(A).hexdigest() and fa['base'] == BASE and fa['secs']['CODE'][0] == BASE + 0x1000, fa['meta'])
fb = store.load(os.path.join(out, 'b.dll.json'), 'imgfacts')
check('an image with no import and no relocation directory has empty facts', fb['imports'] == {} and fb['relocs'] == [], fb)
idx = open(os.path.join(out, 'images.tsv')).read()
check('the index names every image with its sha256', all(hashlib.sha256(x).hexdigest() in idx for x in (A, B)), idx)
out2 = os.path.join(d, 'out2')
exefacts.image_stage(mt, [img], out2)
check('deterministic', open(os.path.join(out, 'images.tsv')).read() == open(os.path.join(out2, 'images.tsv')).read())

# missing image: red with the id
d, img, mt = setup({'a.dll': A}, rows=[('a.dll', hashlib.sha256(A).hexdigest()), ('c.dll', '0' * 64)])
bad = exefacts.image_stage(mt, [img], os.path.join(d, 'out'))
check('a missing image is red facts:missing:<image>', bad == ['facts:missing:c.dll'], bad)
check('the check agrees', exefacts.image_check(mt, os.path.join(d, 'out')) == ['facts:missing:c.dll'])
# another file under the name
d, img, mt = setup({'a.dll': A}, rows=[('a.dll', '1' * 64)])
check('a file of another sha is red', exefacts.image_stage(mt, [img], os.path.join(d, 'out')) == ['facts:sha:a.dll'])
# a lost document
d, img, mt = setup({'a.dll': A, 'b.dll': B})
out = os.path.join(d, 'out')
exefacts.image_stage(mt, [img], out)
os.remove(os.path.join(out, 'b.dll.json'))
check('a lost document is red', exefacts.image_check(mt, out) == ['facts:missing:b.dll'])
# the CLI: exit 0 green, 1 red
mtp = os.path.join(d, 'modules.tsv')
open(mtp, 'w').write(mt)
cmd = [sys.executable, '-I', os.path.join(HERE, '..', 'exefacts.py'), '--modules', mtp, '--dir', img, '--out-dir', os.path.join(d, 'cli')]
check('CLI green', subprocess.run(cmd, capture_output=True).returncode == 0)
open(mtp, 'w').write(mt + 'z.dll\t%s\t1\n' % ('0' * 64))
p = subprocess.run(cmd, capture_output=True, text=True)
check('CLI red names the image', p.returncode == 1 and 'facts:missing:z.dll' in p.stderr, (p.returncode, p.stderr))
# the bundle: one file for the recipe stage
d, img, mt = setup({'a.dll': A, 'b.dll': B})
text, bad = exefacts.image_bundle(mt, [img])
check('bundle: green and complete', bad == [] and exefacts.bundle_check(mt, text) == [], bad)
import json  # noqa: E402
bj = json.loads(text)
check('bundle holds both documents and the index', sorted(bj['images']) == ['a.dll', 'b.dll'] and bj['index'].count('\n') == 3, bj['index'])
check('bundle document equals the directory document', json.dumps(bj['images']['a.dll']['doc'], sort_keys=True, separators=(',', ':')) + '\n' == open(os.path.join(out, 'a.dll.json')).read()
      if os.path.exists(os.path.join(out, 'a.dll.json')) else True)
check('bundle check: a closure image the bundle lacks is red', exefacts.bundle_check(mt + 'z.dll\t%s\t1\n' % ('0' * 64), text) == ['facts:missing:z.dll'])
check('bundle check: garbage is red for every image', exefacts.bundle_check(mt, 'x') == ['facts:missing:a.dll', 'facts:missing:b.dll'])
mtp2 = os.path.join(d, 'm2.tsv')
open(mtp2, 'w').write(mt)
bp = os.path.join(d, 'set.json')
c2 = [sys.executable, '-I', os.path.join(HERE, '..', 'exefacts.py'), '--modules', mtp2, '--dir', img, '--bundle', bp]
check('CLI bundle green and equal to image_bundle', subprocess.run(c2, capture_output=True).returncode == 0 and open(bp).read() == text)
if fails:
    print('FAIL', *fails, sep='\n  ')
    sys.exit(1)
print('ok: per-image facts stage')
