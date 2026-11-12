#!/usr/bin/env python3
"""exepin - the one place that opens the player's FalloutNV.exe (L1 CONTRACT).

    exepin.py FalloutNV.exe          exit 0 and one line if the file is the supported exe; exit 1 with expected / actual otherwise

The supported exe is exactly one build: GOG 1.4.0.525(a).  Its identity is the PIN below: size, SHA-256 and the PE header fields that
fix the image (machine, optional-header magic, image base).  Rule: every tool entry point that reads the exe gets its bytes from
`load_exe`, and no other code opens the exe path (contract/tests/test_exepin.py proves the second half over the call structure of
every tool).  `load_exe` checks the whole fingerprint before it returns a byte, so no fact is produced from another exe.  Another build
needs its own pin and its own address remap; it is never accepted by loosening this check.

Only the identity is published here (size, hash, standard PE fields); no content of the exe is.  `pin=` exists so that a selftest can
exercise the acceptance path on a synthetic file; no tool passes it.
"""
import hashlib
import struct
import sys

NAME = 'FalloutNV.exe'   # the file name of the supported exe: tools name the exe by this, never by the path they were given

PIN = {
    'size': 16084808,
    'sha256': '89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe',
    'machine': 0x14c,
    'magic': 0x10b,
    'image_base': 0x400000,
}

# The guest image DLLs of the declared machine (contract/images.py: the may-load closure from the exe over the declared file set = the exe's directory
# plus gamedata/redist, which is installed in the declared system32): name -> size and SHA-256.  Identity only (digests), no content.
IMAGES = {
    'atimgpud.dll': {'size': 165304, 'sha256': '15c87bdc99674cee4e4c609b5fb6ac52297b46d228e7a9ea3ff9c7fbd4695912'},
    'binkw32.dll': {'size': 338944, 'sha256': 'c6c06e2d21f2179f0b7fbe829a4b17a7b371440d2c679253ec86b64d421185bd'},
    'd3dx9_31.dll': {'size': 2414360, 'sha256': 'e2065619fe6eb0034833b1dc0369deb4a6edc3110e38a1132eeafcf430c578a5'},
    'd3dx9_38.dll': {'size': 3850760, 'sha256': '743e77a228e7d75442263ad70051e44534f7972c6326fd34b505a9c2c245894b'},
    'gdffalloutnv.dll': {'size': 1963176, 'sha256': '0dfabb6c7d089c4f6b806af6a2888b39212c29504e9974a25bb4d4174f10e1b0'},
    'libvorbis.dll': {'size': 1241528, 'sha256': 'a34f3b4ee26cf093d096c574aba26c6e9fe0ce3dbfa073f92378fe1dffa01f0d'},
    'libvorbisfile.dll': {'size': 120248, 'sha256': '6fc78d8abf8683a91d25166732085d5f934003150087b3d435bbc19e532a7994'},
}


class ExePinError(Exception):
    """The file is not the supported exe; the message lists every difference as `field: expected X, actual Y`."""


def pe_fields(d):
    """The PE header fields of the pin, from the bytes; a field that cannot be read is the string 'unreadable'."""
    r = {'machine': 'unreadable', 'magic': 'unreadable', 'image_base': 'unreadable'}
    if d[:2] != b'MZ' or len(d) < 0x40:
        return r
    pe, = struct.unpack_from('<I', d, 0x3c)
    if pe + 24 + 32 > len(d) or d[pe:pe + 4] != b'PE\0\0':
        return r
    r['machine'], = struct.unpack_from('<H', d, pe + 4)
    r['magic'], = struct.unpack_from('<H', d, pe + 24)
    r['image_base'], = struct.unpack_from('<I', d, pe + 24 + 28)
    return r


def _fmt(k, v):
    if isinstance(v, str):
        return v
    return str(v) if k == 'size' else hex(v)


def check_bytes(d, path, pin=PIN):
    actual = {'size': len(d), 'sha256': hashlib.sha256(d).hexdigest()}
    actual.update(pe_fields(d))
    bad = ['%s: expected %s, actual %s' % (k, _fmt(k, pin[k]), _fmt(k, actual[k])) for k in pin if actual[k] != pin[k]]
    if bad:
        raise ExePinError('%s is not the supported FalloutNV.exe (GOG 1.4.0.525(a)):\n  %s' % (path, '\n  '.join(bad)))


def check_images(found, pin=IMAGES):
    """FOUND: [(lowercase file name, size, SHA-256)] of the guest image DLLs the closure derived (the exe is not among them).  The set must be exactly the
    pinned one: a name that is missing, extra, or with another size or hash is listed; ExePinError if there is any difference."""
    got = {n: {'size': z, 'sha256': h} for n, z, h in found}
    bad = ['%s: not a pinned image' % n for n in sorted(got) if n not in pin]
    bad += ['%s: pinned image not in the derived set' % n for n in sorted(pin) if n not in got]
    bad += ['%s: expected %s, actual %s' % (n, pin[n], got[n]) for n in sorted(got) if n in pin and got[n] != pin[n]]
    if bad:
        raise ExePinError('the guest image set is not the pinned one:\n  ' + '\n  '.join(bad))


def load_exe(path, pin=PIN):
    """The bytes of the exe at `path`, after the fingerprint matched `pin`; ExePinError otherwise."""
    try:
        with open(path, 'rb') as fh:
            d = fh.read()
    except OSError as e:
        raise ExePinError('%s: cannot read: %s' % (path, e))
    check_bytes(d, path, pin)
    return d


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print('usage: exepin.py FalloutNV.exe', file=sys.stderr)
        return 2
    try:
        d = load_exe(argv[0])
    except ExePinError as e:
        print('exepin: %s' % e, file=sys.stderr)
        return 1
    print('exepin: %s is the supported FalloutNV.exe, sha256 %s' % (argv[0], hashlib.sha256(d).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
