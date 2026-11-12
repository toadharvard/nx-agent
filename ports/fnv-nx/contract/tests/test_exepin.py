#!/usr/bin/env python3
"""contract/exepin.py on synthetic files (no game data): the refusal paths and the acceptance path with a test-only pin."""
import contextlib
import hashlib
import io
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import exepin
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402





def pe(machine=0x14c, magic=0x10b, base=0x400000, pad=0):
    d = bytearray(0x200 + pad)
    d[:2] = b'MZ'
    struct.pack_into('<I', d, 0x3c, 0x80)
    d[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<H', d, 0x84, machine)
    struct.pack_into('<H', d, 0x80 + 24, magic)
    struct.pack_into('<I', d, 0x80 + 24 + 28, base)
    return bytes(d)


def pin_of(d):
    f = exepin.pe_fields(d)
    return dict(size=len(d), sha256=hashlib.sha256(d).hexdigest(), **f)


def refused(path, pin=exepin.PIN):
    try:
        exepin.load_exe(path, pin)
    except exepin.ExePinError as e:
        return str(e)
    return None


with tempfile.TemporaryDirectory() as t:
    def put(name, data):
        p = os.path.join(t, name)
        with open(p, 'wb') as fh:
            fh.write(data)
        return p
    good = pe()
    gp = put('good.exe', good)
    pin = pin_of(good)
    case('accept: bytes equal to the pinned identity are returned', exepin.load_exe(gp, pin) == good)
    case('real pin: the synthetic file is refused', refused(gp) is not None)
    m = refused(gp)
    case('real pin: expected and actual are named', 'expected %d' % exepin.PIN['size'] in m and 'actual %d' % len(good) in m, m)
    other = pe(pad=1)
    m = refused(put('size.exe', other), pin)
    case('size differs: refused with expected and actual', m and 'size: expected %d, actual %d' % (len(good), len(other)) in m, m)
    flipped = bytearray(good)
    flipped[0x100] ^= 1
    m = refused(put('hash.exe', bytes(flipped)), pin)
    case('same size, other bytes: sha256 refused, expected and actual', m and 'sha256: expected %s, actual %s' % (pin['sha256'], hashlib.sha256(bytes(flipped)).hexdigest()) in m, m)
    for k, d in (('machine', pe(machine=0x8664)), ('magic', pe(magic=0x20b)), ('image_base', pe(base=0x10000000))):
        m = refused(put(k + '.exe', d), dict(pin, size=len(d), sha256=hashlib.sha256(d).hexdigest()))
        case('PE field %s differs: refused naming it', m and ('%s: expected' % k) in m, m, key=k)
    notpe = b'MZ' + bytes(0x1fe)
    m = refused(put('notpe.exe', notpe), dict(pin, size=len(notpe), sha256=hashlib.sha256(notpe).hexdigest()))
    case('no PE header: fields unreadable, refused', m and 'unreadable' in m, m)
    case('missing file: refused', refused(os.path.join(t, 'nope.exe'), pin) is not None)
    case('directory: refused', 'cannot read' in (refused(t, pin) or ''))
    os.symlink(gp, os.path.join(t, 'link.exe'))
    case('symlink to the right file: accepted (the bytes are what is pinned)', refused(os.path.join(t, 'link.exe'), pin) is None)
    err, out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        rc_bad = exepin.main([gp])
    case('cli: other exe -> exit 1, message on stderr, nothing on stdout', rc_bad == 1 and 'expected' in err.getvalue() and not out.getvalue())
    with contextlib.redirect_stderr(io.StringIO()):
        case('cli: wrong argument count -> 2', exepin.main([]) == 2)

# the guest image set: exactly the pinned DLLs (names, sizes, hashes)
im = {'a.dll': {'size': 10, 'sha256': 'a' * 64}, 'b.dll': {'size': 20, 'sha256': 'b' * 64}}
row = lambda n, v: (n, v['size'], v['sha256'])


def images_refused(found, pin=im):
    try:
        exepin.check_images(found, pin)
    except exepin.ExePinError as e:
        return str(e)
    return None


case('image set equal to the pin: accepted, in any order', images_refused([row('b.dll', im['b.dll']), row('a.dll', im['a.dll'])]) is None)
m = images_refused([row('a.dll', im['a.dll'])])
case('image missing from the derived set: refused naming it', m and 'b.dll: pinned image not in the derived set' in m, m)
m = images_refused([row('a.dll', im['a.dll']), row('b.dll', im['b.dll']), row('c.dll', im['a.dll'])])
case('extra image: refused naming it', m and 'c.dll: not a pinned image' in m, m)
m = images_refused([row('a.dll', im['a.dll']), ('b.dll', 20, 'c' * 64)])
case('other hash: refused with expected and actual', m and 'b.dll: expected' in m and 'c' * 64 in m, m)
case('the real image pin holds the 7 DLLs incl. d3dx9_31, hashes only', len(exepin.IMAGES) == 7 and 'd3dx9_31.dll' in exepin.IMAGES
      and exepin.IMAGES['d3dx9_31.dll']['sha256'].startswith('e2065619') and exepin.IMAGES['d3dx9_31.dll']['sha256'].endswith('78a5')
      and all(set(v) == {'size', 'sha256'} for v in exepin.IMAGES.values()))

case('the real pin has the published identity', exepin.PIN['size'] == 16084808 and len(exepin.PIN['sha256']) == 64
      and exepin.PIN['image_base'] == 0x400000)
sys.exit(testcase.finish())
