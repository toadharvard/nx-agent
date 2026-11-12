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

PIN = {
    'size': 16084808,
    'sha256': '89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe',
    'machine': 0x14c,
    'magic': 0x10b,
    'image_base': 0x400000,
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
